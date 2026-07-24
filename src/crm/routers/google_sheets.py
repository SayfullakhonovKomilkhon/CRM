from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
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
)

router = APIRouter(prefix="/google-sheets", tags=["google-sheets"])


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
):
    try:
        values = await google_sheets_client(settings).fetch_values(
            spreadsheet_id,
            config.tab,
            config.header_row,
            settings.google_sheets_max_rows,
        )
        return parse_sheet_values(
            values,
            config,
            spreadsheet_id,
            settings.google_sheets_max_rows,
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
    snapshot = await _snapshot(settings, spreadsheet_id, config)
    planned = await plan_rows(session, snapshot, spreadsheet_id, config.tab)
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

    snapshot = await _snapshot(settings, spreadsheet_id, config)
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

    results = await apply_planned_rows(session, planned, spreadsheet_id, config)
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
