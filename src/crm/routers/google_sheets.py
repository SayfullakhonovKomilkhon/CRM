import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from crm.config import GoogleSheetsTabConfig, Settings, get_settings
from crm.creation import (
    require_active_project,
    require_assignable_scenarist,
)
from crm.database import get_session
from crm.dependencies import require_roles
from crm.google_sheets import (
    SAFE_IMPORT_FIELDS,
    GoogleSheetsConfigurationError,
    GoogleSheetsSourceError,
    apply_planned_rows,
    canonical_checksum,
    google_sheets_client,
    lock_sync_transaction,
    parse_sheet_values,
    plan_rows,
    serialized_row_report,
    summarize_results,
)
from crm.models import (
    GoogleSheetsSyncMode,
    GoogleSheetsSyncRun,
    GoogleSheetsSyncStatus,
    Project,
    Role,
    SheetInboundEvent,
    SheetSource,
    SheetWritebackEvent,
    User,
)
from crm.schemas import (
    GoogleSheetsPreviewRead,
    GoogleSheetsPreviewRequest,
    GoogleSheetsRowResult,
    GoogleSheetsRunSummary,
    GoogleSheetsStatusRead,
    GoogleSheetsSyncRead,
    GoogleSheetsSyncRequest,
    GoogleSheetsTabStatus,
    SheetInboundEventRead,
    SheetReconcileRead,
    SheetReconcileRequest,
    SheetSourceCreate,
    SheetSourceCreated,
    SheetSourceRead,
    SheetSourceUpdate,
    SheetWebhookAccepted,
    SheetWebhookEvent,
    SheetWritebackEventRead,
)
from crm.sheet_sync import (
    WRITEBACK_FIELDS,
    column_letters,
    enqueue_redis,
    source_metadata_matches,
    source_webhook_secret,
    validate_column_map,
    verify_webhook,
)

router = APIRouter(prefix="/google-sheets", tags=["google-sheets"])


async def _source_targets(
    session: AsyncSession,
    project_id: uuid.UUID,
    scenarist_id: uuid.UUID,
) -> None:
    project = await session.scalar(
        select(Project).where(Project.id == project_id).options(selectinload(Project.client))
    )
    require_active_project(project)
    require_assignable_scenarist(await session.get(User, scenarist_id))


def _protect_identity_column(values: dict) -> None:
    identity = values.get("crm_row_id_column", "A").strip().upper()
    mapped = {
        column_letters(reference)
        for mapping_name in ("inbound_column_map", "writeback_column_map")
        for reference in values.get(mapping_name, {}).values()
    }
    if identity in mapped:
        raise HTTPException(
            status_code=422,
            detail="crm_row_id_column is protected and cannot map a workflow field",
        )
    values["crm_row_id_column"] = identity


@router.get("/sources", response_model=list[SheetSourceRead])
async def list_sheet_sources(
    _: User = Depends(require_roles(Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> list[SheetSource]:
    return list(
        (
            await session.scalars(
                select(SheetSource).order_by(
                    SheetSource.spreadsheet_id, SheetSource.source_tab
                )
            )
        ).all()
    )


@router.post(
    "/sources",
    response_model=SheetSourceCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_sheet_source(
    payload: SheetSourceCreate,
    _: User = Depends(require_roles(Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SheetSourceCreated:
    await _source_targets(session, payload.project_id, payload.assigned_scenarist_id)
    values = payload.model_dump()
    values["source_tab"] = values.pop("source_tab").strip()
    values["spreadsheet_id"] = values.pop("spreadsheet_id").strip()
    values["inbound_column_map"] = validate_column_map(
        values["inbound_column_map"], allowed_fields=SAFE_IMPORT_FIELDS
    )
    values["writeback_column_map"] = validate_column_map(
        values["writeback_column_map"], allowed_fields=WRITEBACK_FIELDS
    )
    _protect_identity_column(values)
    source = SheetSource(**values)
    session.add(source)
    try:
        await session.flush()
        secret = source_webhook_secret(settings, source)
        await session.commit()
        await session.refresh(source)
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This spreadsheet/tab source already exists",
        ) from error
    return SheetSourceCreated(
        **SheetSourceRead.model_validate(source).model_dump(),
        webhook_secret=secret,
    )


@router.get("/sources/{source_id}", response_model=SheetSourceRead)
async def get_sheet_source(
    source_id: uuid.UUID,
    _: User = Depends(require_roles(Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> SheetSource:
    source = await session.get(SheetSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Sheet source not found")
    return source


@router.patch("/sources/{source_id}", response_model=SheetSourceRead)
async def update_sheet_source(
    source_id: uuid.UUID,
    payload: SheetSourceUpdate,
    _: User = Depends(require_roles(Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> SheetSource:
    source = await session.get(SheetSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Sheet source not found")
    changes = payload.model_dump(exclude_unset=True)
    project_id = changes.get("project_id", source.project_id)
    scenarist_id = changes.get("assigned_scenarist_id", source.assigned_scenarist_id)
    await _source_targets(session, project_id, scenarist_id)
    if "inbound_column_map" in changes:
        changes["inbound_column_map"] = validate_column_map(
            changes["inbound_column_map"], allowed_fields=SAFE_IMPORT_FIELDS
        )
    if "writeback_column_map" in changes:
        changes["writeback_column_map"] = validate_column_map(
            changes["writeback_column_map"], allowed_fields=WRITEBACK_FIELDS
        )
    merged = {
        "crm_row_id_column": changes.get(
            "crm_row_id_column", source.crm_row_id_column
        ),
        "inbound_column_map": changes.get(
            "inbound_column_map", source.inbound_column_map
        ),
        "writeback_column_map": changes.get(
            "writeback_column_map", source.writeback_column_map
        ),
    }
    _protect_identity_column(merged)
    changes["crm_row_id_column"] = merged["crm_row_id_column"]
    for key, value in changes.items():
        setattr(source, key, value.strip() if key == "source_tab" else value)
    try:
        await session.commit()
        await session.refresh(source)
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Spreadsheet/tab already exists") from error
    return source


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable_sheet_source(
    source_id: uuid.UUID,
    _: User = Depends(require_roles(Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Soft-delete a source so event history and scenario identity remain auditable."""
    source = await session.get(SheetSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Sheet source not found")
    source.enabled = False
    source.last_status = "disabled"
    await session.commit()


@router.post("/sources/{source_id}/rotate-secret", response_model=SheetSourceCreated)
async def rotate_sheet_source_secret(
    source_id: uuid.UUID,
    _: User = Depends(require_roles(Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SheetSourceCreated:
    source = await session.get(SheetSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Sheet source not found")
    source.webhook_secret_version += 1
    await session.commit()
    await session.refresh(source)
    return SheetSourceCreated(
        **SheetSourceRead.model_validate(source).model_dump(),
        webhook_secret=source_webhook_secret(settings, source),
    )


@router.post(
    "/webhook/{source_id}",
    response_model=SheetWebhookAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_sheet_webhook(
    source_id: uuid.UUID,
    request: Request,
    payload: SheetWebhookEvent,
    x_crm_timestamp: str | None = Header(default=None),
    x_crm_signature: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SheetWebhookAccepted:
    source = await session.get(SheetSource, source_id)
    if source is None or not source.enabled:
        raise HTTPException(status_code=404, detail="Sheet source not found")
    body = await request.body()
    verify_webhook(
        secret=source_webhook_secret(settings, source),
        timestamp=x_crm_timestamp,
        signature=x_crm_signature,
        body=body,
        now=datetime.now(UTC),
        max_age_seconds=settings.sheet_webhook_max_age_seconds,
    )
    if not source_metadata_matches(source, payload.raw):
        raise HTTPException(
            status_code=403,
            detail="Webhook source metadata does not match the registered source",
        )
    if canonical_checksum(payload.changed_fields) != payload.checksum.lower():
        raise HTTPException(status_code=422, detail="Webhook checksum mismatch")
    existing = await session.scalar(
        select(SheetInboundEvent).where(
            SheetInboundEvent.event_id == payload.event_id
        )
    )
    if existing is not None:
        return SheetWebhookAccepted(
            event_id=existing.event_id,
            status=existing.status,
            duplicate=True,
            queued=False,
        )
    event = SheetInboundEvent(
        event_id=payload.event_id,
        schema_version=payload.schema_version,
        source_id=source.id,
        crm_row_id=payload.row_id,
        row_number=payload.row_number,
        changed_fields=payload.changed_fields,
        raw=payload.raw,
        checksum=payload.checksum.lower(),
        origin=payload.origin,
        correlation_id=payload.correlation_id,
    )
    session.add(event)
    source.last_status = "inbound_received"
    source.last_event_at = datetime.now(UTC)
    try:
        await session.commit()
        await session.refresh(event)
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(
            select(SheetInboundEvent).where(
                SheetInboundEvent.event_id == payload.event_id
            )
        )
        if existing is None:
            raise
        return SheetWebhookAccepted(
            event_id=existing.event_id,
            status=existing.status,
            duplicate=True,
            queued=False,
        )
    queued = await enqueue_redis(settings, "crm:sheet:inbound", event.id)
    return SheetWebhookAccepted(
        event_id=event.event_id,
        status=event.status,
        duplicate=False,
        queued=queued,
    )


@router.get("/inbound-events", response_model=list[SheetInboundEventRead])
async def list_inbound_events(
    source_id: uuid.UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
    _: User = Depends(require_roles(Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> list[SheetInboundEvent]:
    query = select(SheetInboundEvent)
    if source_id is not None:
        query = query.where(SheetInboundEvent.source_id == source_id)
    return list(
        (
            await session.scalars(
                query.order_by(SheetInboundEvent.created_at.desc()).limit(limit)
            )
        ).all()
    )


@router.get("/writeback-events", response_model=list[SheetWritebackEventRead])
async def list_writeback_events(
    source_id: uuid.UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
    _: User = Depends(require_roles(Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> list[SheetWritebackEvent]:
    query = select(SheetWritebackEvent)
    if source_id is not None:
        query = query.where(SheetWritebackEvent.source_id == source_id)
    return list(
        (
            await session.scalars(
                query.order_by(SheetWritebackEvent.created_at.desc()).limit(limit)
            )
        ).all()
    )


@router.post("/sources/{source_id}/reconcile", response_model=SheetReconcileRead)
async def reconcile_sheet_source(
    source_id: uuid.UUID,
    payload: SheetReconcileRequest,
    _: User = Depends(require_roles(Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SheetReconcileRead:
    source = await session.get(SheetSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Sheet source not found")
    if payload.apply:
        raise HTTPException(
            status_code=409,
            detail="Reconcile is preview-only; confirm through the existing preview/sync flow",
        )
    return SheetReconcileRead(
        source_id=source.id,
        mode="preview",
        manual_preview_endpoint="/api/v1/google-sheets/preview",
        tab=source.source_tab,
        ready=bool(settings.google_service_account_json),
        message="Use the existing preview endpoint, review rows, then confirm sync",
    )


def _tab_config(settings: Settings, requested_tab: str) -> GoogleSheetsTabConfig:
    tab = requested_tab.strip().casefold()
    for config in settings.google_sheets_tab_configs:
        if config.tab.casefold() == tab:
            return config
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Google Sheets tab is not configured",
    )


def _missing_requirements(settings: Settings) -> list[str]:
    missing: list[str] = []
    if not settings.google_sheets_enabled:
        missing.append("GOOGLE_SHEETS_ENABLED")
    if not settings.google_service_account_json:
        missing.append("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not settings.google_sheets_spreadsheet_id:
        missing.append("GOOGLE_SHEETS_SPREADSHEET_ID")
    if not settings.google_sheets_tab_configs:
        missing.append("GOOGLE_SHEETS_TAB_CONFIGS")
    return missing


def _require_ready(settings: Settings) -> str:
    missing = _missing_requirements(settings)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "Google Sheets import is not ready",
                "missing_requirements": missing,
            },
        )
    return settings.google_sheets_spreadsheet_id or ""


async def _validate_targets(
    session: AsyncSession,
    config: GoogleSheetsTabConfig,
) -> None:
    project = await session.scalar(
        select(Project)
        .where(Project.id == config.project_id)
        .options(selectinload(Project.client))
    )
    require_active_project(project)
    if config.assigned_scenarist_id:
        require_assignable_scenarist(
            await session.get(User, config.assigned_scenarist_id)
        )


def _summary(results: list[GoogleSheetsRowResult]) -> GoogleSheetsRunSummary:
    return GoogleSheetsRunSummary(**summarize_results(results))


def _new_run(
    *,
    spreadsheet_id: str,
    config: GoogleSheetsTabConfig,
    actor: User,
    mode: GoogleSheetsSyncMode,
    run_status: GoogleSheetsSyncStatus,
    snapshot_checksum: str,
    results: list[GoogleSheetsRowResult],
    warnings: list[str],
    preview_id=None,
) -> GoogleSheetsSyncRun:
    summary = summarize_results(results)
    return GoogleSheetsSyncRun(
        spreadsheet_id=spreadsheet_id,
        source_tab=config.tab,
        header_row=config.header_row,
        project_id=config.project_id,
        requested_by_id=actor.id,
        preview_id=preview_id,
        mode=mode,
        status=run_status,
        snapshot_checksum=snapshot_checksum,
        total_rows=summary["total_rows"],
        created_count=summary["created"],
        updated_count=summary["updated"],
        skipped_count=summary["skipped"],
        error_count=summary["errors"],
        row_report=serialized_row_report(results),
        warnings=warnings,
        finished_at=datetime.now(UTC),
    )


async def _snapshot(
    settings: Settings,
    spreadsheet_id: str,
    config: GoogleSheetsTabConfig,
    source: SheetSource | None = None,
):
    try:
        effective_config = config
        if source:
            registered_columns = {
                **source.inbound_column_map,
                **{
                    field_name: reference
                    for field_name, reference in source.writeback_column_map.items()
                    if field_name in SAFE_IMPORT_FIELDS
                },
            }
            effective_config = config.model_copy(
                update={"columns": {**config.columns, **registered_columns}}
            )
        values = await google_sheets_client(settings).fetch_values(
            spreadsheet_id,
            config.tab,
            config.header_row,
            settings.google_sheets_max_rows,
        )
        return parse_sheet_values(
            values,
            effective_config,
            spreadsheet_id,
            settings.google_sheets_max_rows,
            crm_row_id_column=source.crm_row_id_column if source else None,
        )
    except GoogleSheetsConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    except GoogleSheetsSourceError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error


async def _registered_source(
    session: AsyncSession,
    spreadsheet_id: str,
    tab: str,
) -> SheetSource | None:
    return await session.scalar(
        select(SheetSource).where(
            SheetSource.spreadsheet_id == spreadsheet_id,
            SheetSource.source_tab == tab,
            SheetSource.enabled.is_(True),
        )
    )


@router.get("/status", response_model=GoogleSheetsStatusRead)
async def google_sheets_status(
    _: User = Depends(require_roles(Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> GoogleSheetsStatusRead:
    missing = _missing_requirements(settings)
    last_run = await session.scalar(
        select(GoogleSheetsSyncRun)
        .where(GoogleSheetsSyncRun.mode == GoogleSheetsSyncMode.SYNC)
        .order_by(GoogleSheetsSyncRun.created_at.desc())
        .limit(1)
    )
    return GoogleSheetsStatusRead(
        enabled=settings.google_sheets_enabled,
        ready=not missing,
        credential_configured=bool(settings.google_service_account_json),
        spreadsheet_configured=bool(settings.google_sheets_spreadsheet_id),
        configured_tabs=[
            GoogleSheetsTabStatus(
                tab=config.tab,
                header_row=config.header_row,
                project_id=config.project_id,
                assigned_scenarist_id=config.assigned_scenarist_id,
            )
            for config in settings.google_sheets_tab_configs
        ],
        safe_import_fields=list(SAFE_IMPORT_FIELDS),
        missing_requirements=missing,
        last_run=last_run,
    )


@router.post("/preview", response_model=GoogleSheetsPreviewRead)
async def preview_google_sheets(
    payload: GoogleSheetsPreviewRequest,
    actor: User = Depends(require_roles(Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> GoogleSheetsPreviewRead:
    spreadsheet_id = _require_ready(settings)
    config = _tab_config(settings, payload.tab)
    await _validate_targets(session, config)
    source = await _registered_source(session, spreadsheet_id, config.tab)
    snapshot = await _snapshot(settings, spreadsheet_id, config, source)
    planned = await plan_rows(
        session, snapshot, spreadsheet_id, config.tab, source=source
    )
    results = [item.result() for item in planned]
    run_status = (
        GoogleSheetsSyncStatus.VALIDATION_FAILED
        if any(item.errors for item in results)
        else GoogleSheetsSyncStatus.PREVIEW_READY
    )
    run = _new_run(
        spreadsheet_id=spreadsheet_id,
        config=config,
        actor=actor,
        mode=GoogleSheetsSyncMode.PREVIEW,
        run_status=run_status,
        snapshot_checksum=snapshot.checksum,
        results=results,
        warnings=snapshot.warnings,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return GoogleSheetsPreviewRead(
        preview_id=run.id,
        status=run.status,
        source_tab=config.tab,
        header_row=config.header_row,
        project_id=config.project_id,
        snapshot_checksum=snapshot.checksum,
        expires_at=run.created_at
        + timedelta(minutes=settings.google_sheets_preview_ttl_minutes),
        summary=_summary(results),
        rows=results,
        warnings=snapshot.warnings,
    )


@router.post("/sync", response_model=GoogleSheetsSyncRead)
async def sync_google_sheets(
    payload: GoogleSheetsSyncRequest,
    actor: User = Depends(require_roles(Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> GoogleSheetsSyncRead:
    spreadsheet_id = _require_ready(settings)
    config = _tab_config(settings, payload.tab)
    await _validate_targets(session, config)
    preview = await session.get(GoogleSheetsSyncRun, payload.preview_id)
    if (
        preview is None
        or preview.mode != GoogleSheetsSyncMode.PREVIEW
        or preview.status != GoogleSheetsSyncStatus.PREVIEW_READY
        or preview.spreadsheet_id != spreadsheet_id
        or preview.source_tab != config.tab
        or preview.project_id != config.project_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Preview is invalid, failed, or does not match this tab configuration",
        )
    expires_at = preview.created_at + timedelta(
        minutes=settings.google_sheets_preview_ttl_minutes
    )
    if datetime.now(UTC) > expires_at:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Preview expired; run preview again",
        )

    source = await _registered_source(session, spreadsheet_id, config.tab)
    snapshot = await _snapshot(settings, spreadsheet_id, config, source)
    if snapshot.checksum != preview.snapshot_checksum:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Google Sheets changed after preview; run preview again",
        )
    await lock_sync_transaction(session, spreadsheet_id, config.tab)
    planned = await plan_rows(
        session,
        snapshot,
        spreadsheet_id,
        config.tab,
        for_update=True,
        source=source,
    )
    current_results = [item.result() for item in planned]
    if canonical_checksum(serialized_row_report(current_results)) != canonical_checksum(
        preview.row_report
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="CRM data changed after preview; run preview again",
        )
    if any(item.errors for item in current_results):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Preview contains validation errors and cannot be synchronized",
        )

    results = await apply_planned_rows(
        session, planned, spreadsheet_id, config, source=source
    )
    if source:
        escaped_source_tab = source.source_tab.replace("'", "''")
        identity_updates = [
            {
                "range": (
                    f"'{escaped_source_tab}'!"
                    f"{source.crm_row_id_column}{item.parsed.row_number}"
                ),
                "majorDimension": "ROWS",
                "values": [[str(item.existing.crm_row_id)]],
            }
            for item in planned
            if item.parsed.crm_row_id is None
            and item.existing is not None
            and item.existing.crm_row_id is not None
        ]
        if identity_updates:
            await google_sheets_client(settings).batch_update_values(
                spreadsheet_id,
                identity_updates,
                max_retries=settings.sheet_google_max_retries,
            )
    run = _new_run(
        spreadsheet_id=spreadsheet_id,
        config=config,
        actor=actor,
        mode=GoogleSheetsSyncMode.SYNC,
        run_status=GoogleSheetsSyncStatus.COMPLETED,
        snapshot_checksum=snapshot.checksum,
        results=results,
        warnings=snapshot.warnings,
        preview_id=preview.id,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return GoogleSheetsSyncRead(
        run_id=run.id,
        preview_id=preview.id,
        status=run.status,
        source_tab=config.tab,
        project_id=config.project_id,
        snapshot_checksum=snapshot.checksum,
        written=True,
        summary=_summary(results),
        rows=results,
        warnings=snapshot.warnings,
    )
