import asyncio
import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from crm.config import Settings
from crm.google_sheets import (
    SAFE_IMPORT_FIELDS,
    SCENARIO_FIELDS,
    GoogleSheetsClient,
    GoogleSheetsSourceError,
    _apply_workflow_values,
    _nested_payload,
    advance_external_id_sequence,
    canonical_checksum,
    coerce_value,
    submission_requested,
    workflow_is_locked,
)
from crm.models import (
    ApprovalDecision,
    ApprovalStage,
    MontageTask,
    Publication,
    PublicationPreparationStatus,
    PublicationReviewDecision,
    PublisherStatus,
    Role,
    Scenario,
    ScenarioApproval,
    ScenarioContent,
    ScenarioResearch,
    ScenarioStatus,
    SheetEventStatus,
    SheetInboundEvent,
    SheetSource,
    SheetWritebackEvent,
    SheetWritebackStatus,
    SourceMaterialStatus,
    User,
)
from crm.schemas import ScenarioCreate
from crm.sheet import SCENARIST_OWNED_FIELD_NAMES, SHEET_FIELDS
from crm.sheet_mapping import (
    CANONICAL_WRITEBACK_COLUMN_MAP,
    MANAGED_EXTENSION_HEADERS,
    canonical_layout_enabled,
    effective_writeback_column_map,
)
from crm.workflow import (
    approval_for,
    is_approved,
    reset_approvals_from,
    reset_downstream_approvals,
    submit_for_responsible_review,
)

WEBHOOK_SCHEMA_VERSION = 1
SHEETS_ORIGIN = "sheets"
CRM_ORIGIN = "crm"
COLUMN_RE = re.compile(r"^[A-Z]{1,3}$")
WRITEBACK_ID_FIELDS = frozenset(
    {
        "assigned_scenarist_id",
        "montage.assigned_editor_id",
        "publication.assigned_publisher_id",
    }
)
WRITEBACK_FIELDS = frozenset(
    ({item.field for item in SHEET_FIELDS} | {"comments.latest"})
    - WRITEBACK_ID_FIELDS
)
CRM_OWNED_WRITEBACK_FIELDS = frozenset(
    WRITEBACK_FIELDS - SCENARIST_OWNED_FIELD_NAMES
)
REALTIME_SERVER_CONTROLLED_FIELDS = frozenset({"montage.material_status"})
SHARED_PROJECT_ASSIGNMENT_FIELDS = frozenset({"scenarist.name"})
LIVE_SCENARIST_SOURCE_FIELDS = frozenset(
    {
        "montage.source_material_url",
        "montage.client_brand_style",
        "montage.extra_brief",
        "montage.scenarist_material_comment",
        "montage.scenarist_revision_status",
        "montage.scenarist_revision_comment",
    }
)
SOURCE_MATERIAL_CONTENT_FIELDS = frozenset(
    {
        "montage.source_material_url",
        "montage.client_brand_style",
        "montage.extra_brief",
        "montage.scenarist_material_comment",
    }
)
LIVE_SCENARIST_PUBLICATION_FIELDS = frozenset(
    {
        "publication.publication_date",
        "publication.publisher_brief",
        "publication.description_dzen",
        "publication.description_youtube",
        "publication.description_tiktok",
        "publication.description_instagram",
        "publication.ai_social_descriptions",
        "publication.leia_script",
    }
)
LIVE_SCENARIST_FIELDS = (
    LIVE_SCENARIST_SOURCE_FIELDS | LIVE_SCENARIST_PUBLICATION_FIELDS
)

# CRM keeps stable English enum values in the API/database. Google Sheets is a
# user-facing surface, so workflow cells receive Russian labels instead. Keep
# this field-aware: arbitrary scenario text containing e.g. "approved" must not
# be translated.
SHEET_APPROVAL_STATUS_FIELDS = frozenset(
    item.field
    for item in SHEET_FIELDS
    if item.field.startswith("approval.") and item.field.endswith(".decision")
)
SHEET_GENERIC_STATUS_LABELS = {
    "pending": "Ожидает",
    "waiting": "Ожидает",
    "draft": "Черновик",
    "in_review": "На проверке",
    "ready_for_review": "Готово к проверке",
    "revision": "Доработка",
    "rejected": "Отказ",
    "approved": "Одобрено",
    "sent_to_generation": "Передано в производство",
    "handed_to_editor": "Передано монтажёру",
    "editing": "Монтаж",
    "client_review": "На проверке у клиента",
    "manager_revision_review": "Проверка доработки",
    "ready_to_publish": "Готово к публикации",
    "published": "Опубликовано",
    "archived": "Архив",
    "assigned": "Назначено",
    "in_progress": "В работе",
    "ready": "Готово",
    "not_ready": "Не готово",
    "review": "Проверить",
    "fixed": "Исправлено",
    "completed": "Завершено",
    "failed": "Ошибка",
}
SHEET_STATUS_LABELS: dict[str, dict[str, str]] = {
    **{
        field: {
            "pending": "Ожидает",
            "approved": "Одобрено",
            "revision": "Доработать",
            "rejected": "Отказ",
        }
        for field in SHEET_APPROVAL_STATUS_FIELDS
    },
    "montage.material_status": {
        "draft": "В работе",
        "ready_for_review": "На проверке",
        "revision": "На доработке",
        "approved": "Одобрено",
        "rejected": "Отказ",
    },
    "montage.editor_status": {
        "pending": "Ожидает",
        "in_progress": "В работе",
        "ready": "Готово",
        "not_ready": "Не готово",
        "review": "Проверить",
        "fixed": "Исправлено",
    },
    "final_revision_gate.decision": {
        "pending": "Ожидает",
        "approved": "Одобрено",
        "rejected": "Отказ",
    },
    "publication.manager_review_decision": {
        "pending": "Ожидает",
        "approved": "Одобрено",
        "revision": "Доработать",
    },
    "publication.publisher_status": {
        "pending": "Ожидает назначения",
        "assigned": "Назначено",
        "in_progress": "В работе",
        "published": "Опубликовано",
    },
    "publication.preparation_status": {
        "draft": "В работе",
        "ready_for_review": "На проверке",
        "revision": "На доработке",
        "approved": "Одобрено",
    },
}


def source_webhook_secret(settings: Settings, source: SheetSource) -> str:
    material = f"sheet-source:{source.id}:{source.webhook_secret_version}".encode()
    return hmac.new(
        settings.app_secret_key.encode(),
        material,
        hashlib.sha256,
    ).hexdigest()


def webhook_signature(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(
        secret.encode(),
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def verify_webhook(
    *,
    secret: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: datetime,
    max_age_seconds: int,
) -> None:
    try:
        sent_at = int(timestamp or "")
    except ValueError as error:
        raise HTTPException(status_code=401, detail="Invalid webhook timestamp") from error
    if abs(int(now.timestamp()) - sent_at) > max_age_seconds:
        raise HTTPException(status_code=401, detail="Webhook timestamp expired")
    expected = webhook_signature(secret, str(sent_at), body)
    if not signature or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def source_metadata_matches(source: SheetSource, raw: dict[str, Any]) -> bool:
    return (
        raw.get("spreadsheet_id") == source.spreadsheet_id
        and raw.get("tab") == source.source_tab
    )


def validate_column_map(
    mapping: dict[str, int | str],
    *,
    allowed_fields: frozenset[str] | tuple[str, ...],
) -> dict[str, int | str]:
    unsupported = sorted(set(mapping) - set(allowed_fields))
    if unsupported:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported mapped fields: {', '.join(unsupported)}",
        )
    normalized: dict[str, int | str] = {}
    used: set[str] = set()
    for field_name, reference in mapping.items():
        if isinstance(reference, bool):
            raise HTTPException(status_code=422, detail=f"{field_name}: invalid column")
        if isinstance(reference, int):
            if not 1 <= reference <= 18_278:
                raise HTTPException(status_code=422, detail=f"{field_name}: invalid column")
            key = str(reference)
        else:
            value = reference.strip().upper()
            if not COLUMN_RE.fullmatch(value):
                raise HTTPException(status_code=422, detail=f"{field_name}: invalid A1 column")
            reference = value
            key = value
        if key in used:
            raise HTTPException(status_code=422, detail="A column can map to only one field")
        used.add(key)
        normalized[field_name] = reference
    return normalized


async def _redis_command(redis_url: str, *parts: str) -> None:
    parsed = urlsplit(redis_url)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise ValueError("REDIS_URL must use redis:// or rediss://")
    reader, writer = await asyncio.open_connection(
        parsed.hostname,
        parsed.port or 6379,
        ssl=parsed.scheme == "rediss",
    )
    try:
        commands: list[tuple[str, ...]] = []
        if parsed.password:
            commands.append(("AUTH", parsed.password))
        database = parsed.path.strip("/") or "0"
        if database != "0":
            commands.append(("SELECT", database))
        commands.append(tuple(parts))
        for command in commands:
            encoded = [item.encode() for item in command]
            payload = f"*{len(encoded)}\r\n".encode() + b"".join(
                f"${len(item)}\r\n".encode() + item + b"\r\n" for item in encoded
            )
            writer.write(payload)
            await writer.drain()
            response = await reader.readline()
            if response.startswith(b"-"):
                raise RuntimeError(response.decode(errors="replace").strip())
    finally:
        writer.close()
        await writer.wait_closed()


async def enqueue_redis(settings: Settings, queue: str, event_id: uuid.UUID) -> bool:
    if not settings.redis_url:
        return False
    try:
        await _redis_command(settings.redis_url, "LPUSH", queue, str(event_id))
    except (TimeoutError, OSError, ValueError, RuntimeError):
        return False
    return True


async def dequeue_redis(
    settings: Settings,
    queues: tuple[str, ...],
    *,
    timeout_seconds: int = 2,
) -> tuple[str, uuid.UUID] | None:
    if not settings.redis_url:
        return None
    parsed = urlsplit(settings.redis_url)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        return None
    try:
        reader, writer = await asyncio.open_connection(
            parsed.hostname,
            parsed.port or 6379,
            ssl=parsed.scheme == "rediss",
        )
        try:
            commands: list[tuple[str, ...]] = []
            if parsed.password:
                commands.append(("AUTH", parsed.password))
            database = parsed.path.strip("/") or "0"
            if database != "0":
                commands.append(("SELECT", database))
            commands.append(("BRPOP", *queues, str(timeout_seconds)))
            response_parts: list[str] | None = None
            for command in commands:
                encoded = [item.encode() for item in command]
                writer.write(
                    f"*{len(encoded)}\r\n".encode()
                    + b"".join(
                        f"${len(item)}\r\n".encode() + item + b"\r\n"
                        for item in encoded
                    )
                )
                await writer.drain()
                first = await reader.readline()
                if first.startswith(b"-"):
                    return None
                if command[0] != "BRPOP":
                    continue
                if first == b"$-1\r\n" or first == b"*-1\r\n":
                    return None
                if not first.startswith(b"*"):
                    return None
                count = int(first[1:].strip())
                response_parts = []
                for _ in range(count):
                    length_line = await reader.readline()
                    length = int(length_line[1:].strip())
                    value = await reader.readexactly(length)
                    await reader.readexactly(2)
                    response_parts.append(value.decode())
            if not response_parts or len(response_parts) != 2:
                return None
            return response_parts[0], uuid.UUID(response_parts[1])
        finally:
            writer.close()
            await writer.wait_closed()
    except (OSError, ValueError, RuntimeError, TimeoutError):
        return None


async def lock_source_row(
    session: AsyncSession,
    source_id: uuid.UUID,
    crm_row_id: uuid.UUID,
) -> None:
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{source_id}:{crm_row_id}"},
        )


async def lock_source_append(
    session: AsyncSession,
    source_id: uuid.UUID,
) -> None:
    """Serialize append position allocation for one Sheet source."""
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{source_id}:append"},
        )


def _set_values(scenario: Scenario, changed_fields: dict[str, Any]) -> None:
    scenario_values: dict[str, Any] = {}
    research_values: dict[str, Any] = {}
    content_values: dict[str, Any] = {}
    workflow_values: dict[str, Any] = {}
    for field_name, raw_value in changed_fields.items():
        try:
            value = coerce_value(field_name, raw_value)
        except ValueError as error:
            raise ValueError(f"{field_name}: {error}") from error
        if field_name in SCENARIO_FIELDS:
            scenario_values[field_name] = value
        elif field_name.startswith("research."):
            research_values[field_name.split(".", 1)[1]] = value
        elif field_name.startswith("content."):
            content_values[field_name.split(".", 1)[1]] = value
        else:
            workflow_values[field_name] = value
    external_id_supplied = "external_id" in scenario_values
    external_id = scenario_values.pop("external_id", None)
    if external_id_supplied and external_id is None:
        raise ValueError("external_id cannot be empty")
    validated = ScenarioCreate(
        project_id=scenario.project_id,
        assigned_scenarist_id=scenario.assigned_scenarist_id,
        **scenario_values,
        research=research_values or None,
        content=content_values or None,
    )
    validated_data = validated.model_dump(exclude_unset=True)
    scenario_values = {
        key: validated_data[key] for key in scenario_values if key in validated_data
    }
    research_values = (
        validated_data.get("research") or {} if research_values else {}
    )
    content_values = validated_data.get("content") or {} if content_values else {}
    for field_name, value in scenario_values.items():
        setattr(scenario, field_name, value)
    if external_id_supplied:
        scenario.external_id = external_id
    if research_values:
        scenario.research = scenario.research or ScenarioResearch()
        for field_name, value in research_values.items():
            setattr(scenario.research, field_name, value)
    if content_values:
        scenario.content = scenario.content or ScenarioContent()
        for field_name, value in content_values.items():
            setattr(scenario.content, field_name, value)
    if workflow_values:
        _apply_workflow_values(scenario, _nested_payload(workflow_values))


def active_scenarist_revision_stage(scenario: Scenario) -> ApprovalStage | None:
    if scenario.status != ScenarioStatus.REVISION:
        return None
    for stage in (
        ApprovalStage.PRE_GENERATION_CLIENT,
        ApprovalStage.RESPONSIBLE_REVIEW,
    ):
        approval = approval_for(scenario, stage)
        if approval is not None and approval.decision in {
            ApprovalDecision.REVISION,
            ApprovalDecision.REJECTED,
        }:
            return stage
    return None


def inbound_update_allowed(scenario: Scenario) -> bool:
    return not workflow_is_locked(scenario) or active_scenarist_revision_stage(scenario) is not None


def workflow_fields_only(changed_fields: dict[str, Any]) -> bool:
    return bool(changed_fields) and all(
        field_name.startswith(("approval.", "montage.", "publication."))
        for field_name in changed_fields
    )


def _publication_content_ready(publication: Publication | None) -> bool:
    return bool(
        publication
        and any(
            (
                getattr(publication, "publication_date", None),
                publication.description_dzen,
                publication.description_youtube,
                publication.description_tiktok,
                publication.description_instagram,
            )
        )
    )


def _reset_publication_review(publication: Publication) -> None:
    publication.preparation_status = PublicationPreparationStatus.DRAFT.value
    publication.manager_review_decision = PublicationReviewDecision.PENDING
    publication.manager_review_comment = None
    publication.manager_reviewed_by_id = None
    publication.manager_reviewed_at = None
    publication.assigned_publisher_id = None
    publication.publisher_status = PublisherStatus.PENDING
    publication.publisher_comment = None
    publication.is_published = False
    publication.published_at = None


def _submit_source_material_from_sheet(scenario: Scenario) -> None:
    if not is_approved(scenario, ApprovalStage.PRE_GENERATION_CLIENT):
        raise ValueError("The client must approve the scenario first")
    if scenario.status != ScenarioStatus.SENT_TO_GENERATION:
        raise ValueError("Source material is not waiting for scenarist submission")
    scenario.montage = scenario.montage or MontageTask()
    if scenario.montage.material_status == SourceMaterialStatus.READY_FOR_REVIEW:
        raise ValueError("Source material has already been submitted")
    if scenario.montage.material_status == SourceMaterialStatus.REJECTED:
        raise ValueError("Rejected source material cannot be resubmitted")
    approval = approval_for(scenario, ApprovalStage.SOURCE_MATERIAL)
    if approval is not None and approval.decision == ApprovalDecision.APPROVED:
        raise ValueError("Source material has already been approved")
    reset_downstream_approvals(scenario, ApprovalStage.SOURCE_MATERIAL)
    if approval is None:
        approval = ScenarioApproval(stage=ApprovalStage.SOURCE_MATERIAL)
        scenario.approvals.append(approval)
    approval.decision = ApprovalDecision.PENDING
    approval.decided_by_id = None
    approval.decided_at = None
    scenario.montage.material_status = SourceMaterialStatus.READY_FOR_REVIEW


def _submit_publication_from_sheet(scenario: Scenario) -> None:
    if not is_approved(scenario, ApprovalStage.FINAL_CLIENT):
        raise ValueError("The client must approve the final montage first")
    scenario.publication = scenario.publication or Publication()
    if (
        scenario.publication.preparation_status
        == PublicationPreparationStatus.READY_FOR_REVIEW
    ):
        return
    if (
        scenario.publication.preparation_status
        == PublicationPreparationStatus.APPROVED
    ):
        return
    if scenario.status != ScenarioStatus.APPROVED:
        raise ValueError("Publication content is not waiting for scenarist submission")
    if not _publication_content_ready(scenario.publication):
        raise ValueError("Publication date or at least one description is required")
    scenario.publication.preparation_status = (
        PublicationPreparationStatus.READY_FOR_REVIEW.value
    )
    scenario.publication.manager_review_decision = PublicationReviewDecision.PENDING
    scenario.publication.manager_review_comment = None
    scenario.publication.manager_reviewed_by_id = None
    scenario.publication.manager_reviewed_at = None
    scenario.publication.assigned_publisher_id = None
    scenario.publication.publisher_status = PublisherStatus.PENDING


async def process_inbound_event(
    session: AsyncSession,
    event_id: uuid.UUID,
) -> SheetInboundEvent:
    event = await session.scalar(
        select(SheetInboundEvent)
        .where(SheetInboundEvent.id == event_id)
        .with_for_update()
    )
    if event is None:
        raise LookupError("Inbound event not found")
    if event.status in {SheetEventStatus.COMPLETED, SheetEventStatus.SKIPPED}:
        return event
    event.status = SheetEventStatus.PROCESSING
    event.attempts += 1
    source = await session.get(SheetSource, event.source_id)
    if source is None or not source.enabled:
        event.status = SheetEventStatus.FAILED
        event.error = "Sheet source is missing or disabled"
        return event
    if not source_metadata_matches(source, event.raw):
        event.status = SheetEventStatus.FAILED
        event.error = "Inbound source metadata does not match the registry"
        return event
    await lock_source_row(session, source.id, event.crm_row_id)
    if event.origin == CRM_ORIGIN:
        event.status = SheetEventStatus.SKIPPED
        event.error = "CRM-origin event suppressed"
        event.processed_at = datetime.now(UTC)
        return event
    submit_requested = submission_requested(event.raw.get("submission_status"))
    sync_mode = event.raw.get("sync_mode")
    live_update_requested = sync_mode == "scenarist_live_update"
    source_submit_requested = sync_mode == "source_material_submit"
    publication_submit_requested = sync_mode == "publication_submit"
    configured_inbound_fields = set(source.inbound_column_map)
    if canonical_layout_enabled(source):
        configured_inbound_fields.update(CANONICAL_WRITEBACK_COLUMN_MAP)
    if "external_id" in (getattr(source, "writeback_column_map", None) or {}):
        configured_inbound_fields.add("external_id")
    allowed = (
        configured_inbound_fields
        & (set(SAFE_IMPORT_FIELDS) | set(SHARED_PROJECT_ASSIGNMENT_FIELDS))
        - set(REALTIME_SERVER_CONTROLLED_FIELDS)
    )
    inbound_fields = {
        field_name: value
        for field_name, value in event.changed_fields.items()
        if field_name in allowed
    }
    # Older Apps Script installations send the whole visible row even for an
    # explicit late-stage action.  Do not let unchanged scenario/research
    # cells turn a source-material or publication submission into a forbidden
    # attempt to rewrite the already-started workflow.  The stage marker is
    # server-controlled, so it is safe to narrow that snapshot here as well as
    # in the current Apps Script client.
    if source_submit_requested:
        inbound_fields = {
            field_name: value
            for field_name, value in inbound_fields.items()
            if field_name in LIVE_SCENARIST_SOURCE_FIELDS
            or field_name == "external_id"
        }
    elif publication_submit_requested:
        inbound_fields = {
            field_name: value
            for field_name, value in inbound_fields.items()
            if field_name in LIVE_SCENARIST_PUBLICATION_FIELDS
            or field_name == "external_id"
        }
    scenarist_name = str(inbound_fields.pop("scenarist.name", "") or "").strip()
    ignored_fields = sorted(set(event.changed_fields) - allowed)
    if ignored_fields and not inbound_fields:
        event.status = SheetEventStatus.SKIPPED
        event.error = (
            "Sheet edit contains only CRM-owned fields: "
            + ", ".join(ignored_fields)
        )
        event.processed_at = datetime.now(UTC)
        return event
    scenario = await session.scalar(
        select(Scenario)
        .where(
            Scenario.sheet_source_id == source.id,
            Scenario.crm_row_id == event.crm_row_id,
        )
        .options(
            selectinload(Scenario.research),
            selectinload(Scenario.content),
            selectinload(Scenario.approvals),
            selectinload(Scenario.montage),
            selectinload(Scenario.publication),
            selectinload(Scenario.final_revision_gate),
        )
        .with_for_update()
    )
    stage_fields = set(inbound_fields) - {"external_id"}
    live_update_payload = bool(
        not submit_requested
        and live_update_requested
        and stage_fields
        and stage_fields <= LIVE_SCENARIST_FIELDS
    )
    source_submit_payload = bool(
        not submit_requested
        and source_submit_requested
        and stage_fields <= LIVE_SCENARIST_SOURCE_FIELDS
    )
    publication_submit_payload = bool(
        not submit_requested
        and publication_submit_requested
        and stage_fields <= LIVE_SCENARIST_PUBLICATION_FIELDS
    )
    scenarist_stage_action = bool(
        live_update_payload or source_submit_payload or publication_submit_payload
    )
    if scenario is None and scenarist_stage_action:
        legacy_scenario = await session.scalar(
            select(Scenario)
            .where(
                Scenario.sheet_source_id == source.id,
                Scenario.source_row == event.row_number,
                Scenario.crm_row_id.is_(None),
            )
            .options(
                selectinload(Scenario.research),
                selectinload(Scenario.content),
                selectinload(Scenario.approvals),
                selectinload(Scenario.montage),
                selectinload(Scenario.publication),
                selectinload(Scenario.final_revision_gate),
            )
            .with_for_update()
        )
        if legacy_scenario is not None:
            legacy_scenario.crm_row_id = event.crm_row_id
            scenario = legacy_scenario
    if scenario is None and scenarist_stage_action:
        external_id = str(inbound_fields.get("external_id") or "").strip()
        if external_id:
            recovered_scenario = await session.scalar(
                select(Scenario)
                .where(
                    Scenario.project_id == source.project_id,
                    Scenario.external_id == external_id,
                    # A project tab can be recreated or registered after its
                    # scenarios were imported. In that case the business row
                    # still points at the same tab/external ID, while the
                    # internal SheetSource UUID is stale. Recover that binding
                    # from the stable project + tab + visible ID tuple.
                    Scenario.source_tab == source.source_tab,
                )
                .options(
                    selectinload(Scenario.research),
                    selectinload(Scenario.content),
                    selectinload(Scenario.approvals),
                    selectinload(Scenario.montage),
                    selectinload(Scenario.publication),
                    selectinload(Scenario.final_revision_gate),
                )
                .with_for_update()
            )
            if recovered_scenario is not None:
                recovered_scenario.sheet_source_id = source.id
                recovered_scenario.crm_row_id = event.crm_row_id
                recovered_scenario.source_sheet_id = source.spreadsheet_id
                recovered_scenario.source_tab = source.source_tab
                recovered_scenario.source_row = event.row_number
                scenario = recovered_scenario
    # Very old project-tab scripts have no sync_mode at all and post a full
    # row snapshot.  Infer only the currently open scenarist stage from CRM
    # state; downstream approvals remain the source of truth, so an early row
    # cannot jump directly to publication or source-material review.
    legacy_full_snapshot = bool(
        not sync_mode or (live_update_requested and not live_update_payload)
    )
    if scenario is not None and legacy_full_snapshot and not submit_requested:
        publication_values = {
            field_name: value
            for field_name, value in inbound_fields.items()
            if field_name in LIVE_SCENARIST_PUBLICATION_FIELDS
            and value not in (None, "")
        }
        source_values = {
            field_name: value
            for field_name, value in inbound_fields.items()
            if field_name in SOURCE_MATERIAL_CONTENT_FIELDS
            and value not in (None, "")
        }
        if (
            scenario.status == ScenarioStatus.APPROVED
            and is_approved(scenario, ApprovalStage.FINAL_CLIENT)
            and publication_values
        ):
            publication_submit_requested = True
            inbound_fields = {
                field_name: value
                for field_name, value in inbound_fields.items()
                if field_name in LIVE_SCENARIST_PUBLICATION_FIELDS
                or field_name == "external_id"
            }
        elif (
            is_approved(scenario, ApprovalStage.PRE_GENERATION_CLIENT)
            and source_values
        ):
            source_submit_requested = True
            inbound_fields = {
                field_name: value
                for field_name, value in inbound_fields.items()
                if field_name in LIVE_SCENARIST_SOURCE_FIELDS
                or field_name == "external_id"
            }
    stage_fields = set(inbound_fields) - {"external_id"}
    live_update_payload = bool(
        not submit_requested
        and live_update_requested
        and stage_fields
        and stage_fields <= LIVE_SCENARIST_FIELDS
    )
    source_submit_payload = bool(
        not submit_requested
        and source_submit_requested
        and stage_fields <= LIVE_SCENARIST_SOURCE_FIELDS
    )
    publication_submit_payload = bool(
        not submit_requested
        and publication_submit_requested
        and stage_fields <= LIVE_SCENARIST_PUBLICATION_FIELDS
    )
    live_update = bool(live_update_payload and scenario is not None)
    source_submit = bool(source_submit_payload and scenario is not None)
    publication_submit = bool(publication_submit_payload and scenario is not None)
    if not submit_requested and not (live_update or source_submit or publication_submit):
        event.status = SheetEventStatus.SKIPPED
        event.error = "Sheet row is not marked 'Отправить' for approval"
        event.processed_at = datetime.now(UTC)
        return event
    incoming_external_id = inbound_fields.get("external_id")
    if scenario is None and incoming_external_id is None:
        event.status = SheetEventStatus.FAILED
        event.error = "Google Sheet ID is required for a new scenario"
        return event
    if (
        scenario is not None
        and scenario.source_checksum == event.checksum
        and not (source_submit or publication_submit)
    ):
        event.status = SheetEventStatus.SKIPPED
        event.error = "Checksum already applied"
        event.processed_at = datetime.now(UTC)
        return event
    if (
        scenario is not None
        and not inbound_update_allowed(scenario)
        and not (live_update or source_submit or publication_submit)
        and not workflow_fields_only(inbound_fields)
    ):
        event.status = SheetEventStatus.FAILED
        event.error = "CRM workflow has started; inbound source updates are locked"
        return event
    scenario_was_new = scenario is None
    assigned_scenarist_id = source.assigned_scenarist_id
    if source.assigned_scenarist_id is None and (
        scenario is None or scenario.assigned_scenarist_id is None
    ):
        if not scenarist_name:
            event.status = SheetEventStatus.FAILED
            event.error = "Scenarist name is required for a shared project tab"
            return event
        scenarists = list(
            (
                await session.scalars(
                    select(User)
                    .where(
                        User.role == Role.SCENARIST,
                        User.is_active.is_(True),
                        func.lower(User.full_name) == scenarist_name.lower(),
                    )
                    .limit(2)
                )
            ).all()
        )
        if len(scenarists) != 1:
            event.status = SheetEventStatus.FAILED
            event.error = (
                "Shared project tab requires exactly one active CRM scenarist "
                f"named '{scenarist_name}'"
            )
            return event
        assigned_scenarist_id = scenarists[0].id
    if scenario_was_new:
        scenario = Scenario(
            project_id=source.project_id,
            assigned_scenarist_id=assigned_scenarist_id,
            sheet_source_id=source.id,
            crm_row_id=event.crm_row_id,
            source_sheet_id=source.spreadsheet_id,
            source_tab=source.source_tab,
            source_row=event.row_number,
            source_checksum=event.checksum,
            status=ScenarioStatus.DRAFT,
        )
        session.add(scenario)
    else:
        scenario.source_row = event.row_number
        scenario.source_checksum = event.checksum
    try:
        source_fields = set(inbound_fields) & LIVE_SCENARIST_SOURCE_FIELDS
        publication_fields = (
            set(inbound_fields) & LIVE_SCENARIST_PUBLICATION_FIELDS
        )
        if not submit_requested and source_fields and not is_approved(
            scenario, ApprovalStage.PRE_GENERATION_CLIENT
        ):
            raise ValueError("The client must approve the scenario first")
        if not submit_requested and publication_fields and not is_approved(
            scenario, ApprovalStage.FINAL_CLIENT
        ):
            raise ValueError("The client must approve the final montage first")
        previous_source_values = {
            field_name: (
                getattr(scenario.montage, field_name.split(".", 1)[1])
                if scenario.montage is not None
                else None
            )
            for field_name in source_fields
        }
        previous_publication_values = {
            field_name: (
                getattr(scenario.publication, field_name.split(".", 1)[1])
                if scenario.publication is not None
                else None
            )
            for field_name in publication_fields
        }
        _set_values(scenario, inbound_fields)
        publication_changed = any(
            getattr(scenario.publication, field_name.split(".", 1)[1])
            != previous_value
            for field_name, previous_value in previous_publication_values.items()
        )
        if live_update:
            source_changed = any(
                getattr(scenario.montage, field_name.split(".", 1)[1])
                != previous_value
                for field_name, previous_value in previous_source_values.items()
                if field_name in SOURCE_MATERIAL_CONTENT_FIELDS
            )
            if source_changed:
                reset_approvals_from(scenario, ApprovalStage.SOURCE_MATERIAL)
                scenario.montage = scenario.montage or MontageTask()
                scenario.montage.material_status = SourceMaterialStatus.DRAFT
                if is_approved(scenario, ApprovalStage.PRE_GENERATION_CLIENT):
                    scenario.status = ScenarioStatus.SENT_TO_GENERATION
            if publication_changed:
                scenario.publication = scenario.publication or Publication()
                _reset_publication_review(scenario.publication)
                scenario.status = ScenarioStatus.APPROVED
        if source_submit:
            _submit_source_material_from_sheet(scenario)
        if publication_submit:
            if publication_changed:
                scenario.publication = scenario.publication or Publication()
                _reset_publication_review(scenario.publication)
                scenario.status = ScenarioStatus.APPROVED
            _submit_publication_from_sheet(scenario)
        source_payload = dict(scenario.source_payload or {})
        mapped_fields = dict(source_payload.get("mapped_fields") or {})
        mapped_fields.update(inbound_fields)
        if scenarist_name:
            mapped_fields["scenarist.name"] = scenarist_name
        source_payload["mapped_fields"] = mapped_fields
        source_payload["last_event_raw"] = event.raw
        if ignored_fields:
            source_payload["last_ignored_sheet_fields"] = ignored_fields
        scenario.source_payload = source_payload
        if submit_requested and scenario.status in {
            ScenarioStatus.DRAFT,
            ScenarioStatus.REVISION,
        }:
            submit_for_responsible_review(scenario)
        await advance_external_id_sequence(session, scenario.external_id)
    except (ValueError, ValidationError) as error:
        if scenario_was_new:
            session.expunge(scenario)
        event.status = SheetEventStatus.FAILED
        event.error = f"Inbound validation failed: {error}"
        return event
    event.status = SheetEventStatus.COMPLETED
    event.error = None
    event.processed_at = datetime.now(UTC)
    source.last_status = "inbound_completed"
    source.last_error = None
    source.last_event_at = event.processed_at
    await session.flush()
    return event


async def enqueue_sheet_writeback(
    session: AsyncSession,
    scenario: Scenario,
    changed_fields: dict[str, Any],
    *,
    correlation_id: str | None = None,
) -> SheetWritebackEvent | None:
    if scenario.sheet_source_id is None or scenario.crm_row_id is None:
        return None
    source = await session.get(SheetSource, scenario.sheet_source_id)
    if source is None or not source.enabled:
        return None
    writeback_map = effective_writeback_column_map(source)
    approved = {
        key: value
        for key, value in changed_fields.items()
        if key in writeback_map and key in WRITEBACK_FIELDS
    }
    if not approved:
        return None
    event = SheetWritebackEvent(
        source_id=source.id,
        scenario_id=scenario.id,
        crm_row_id=scenario.crm_row_id,
        changed_fields=json.loads(json.dumps(approved, default=str)),
        checksum=canonical_checksum(approved),
        origin=CRM_ORIGIN,
        correlation_id=correlation_id or str(uuid.uuid4()),
        status=SheetWritebackStatus.PENDING,
    )
    session.add(event)
    await session.flush()
    return event


async def bind_unbound_project_scenarios(
    session: AsyncSession,
    source: SheetSource,
) -> int:
    """Populate a project tab without stealing rows from an existing source."""
    scenarios = list(
        (
            await session.scalars(
                select(Scenario)
                .where(
                    Scenario.project_id == source.project_id,
                    Scenario.sheet_source_id.is_(None),
                    Scenario.assigned_scenarist_id.is_not(None),
                )
                .options(
                    selectinload(Scenario.assigned_scenarist),
                    selectinload(Scenario.research),
                    selectinload(Scenario.content),
                    selectinload(Scenario.approvals),
                    selectinload(Scenario.montage),
                    selectinload(Scenario.publication),
                    selectinload(Scenario.final_revision_gate),
                )
                .order_by(Scenario.created_at, Scenario.id)
            )
        ).all()
    )
    for scenario in scenarios:
        scenario.sheet_source_id = source.id
        scenario.crm_row_id = scenario.crm_row_id or uuid.uuid4()
        scenario.source_sheet_id = source.spreadsheet_id
        scenario.source_tab = source.source_tab
        scenario.source_row = None
        await enqueue_sheet_writeback(
            session,
            scenario,
            scenario_writeback_snapshot(
                scenario,
                include_empty=True,
                scenarist_name=scenario.assigned_scenarist.full_name,
            ),
        )
    return len(scenarios)


def retry_at(attempts: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=min(300, 2 ** max(1, attempts)))


def column_letters(reference: int | str) -> str:
    if isinstance(reference, str):
        value = reference.strip().upper()
        if not COLUMN_RE.fullmatch(value):
            raise ValueError("Invalid A1 column")
        return value
    if not 1 <= reference <= 18_278:
        raise ValueError("Invalid column number")
    result = ""
    value = reference
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def column_number(reference: int | str) -> int:
    if isinstance(reference, int):
        if not 1 <= reference <= 18_278:
            raise ValueError("Invalid column number")
        return reference
    value = 0
    for character in column_letters(reference):
        value = value * 26 + ord(character) - 64
    return value


def append_row_values(
    source: SheetSource,
    crm_row_id: uuid.UUID,
    changed_fields: dict[str, Any],
) -> tuple[str, list[Any]]:
    writeback_map = effective_writeback_column_map(source)
    mapped = {
        column_number(writeback_map[field_name]): sheet_cell_value(
            value,
            field_name=field_name,
        )
        for field_name, value in changed_fields.items()
    }
    identity_column = column_number(source.crm_row_id_column)
    mapped[identity_column] = str(crm_row_id)
    last_column = max(mapped)
    values = [""] * last_column
    for column, value in mapped.items():
        values[column - 1] = value
    return column_letters(last_column), values


async def process_writeback_event(
    session: AsyncSession,
    event_id: uuid.UUID,
    client: GoogleSheetsClient,
    *,
    max_retries: int = 4,
) -> SheetWritebackEvent:
    event = await session.scalar(
        select(SheetWritebackEvent)
        .where(SheetWritebackEvent.id == event_id)
        .with_for_update()
    )
    if event is None:
        raise LookupError("Writeback event not found")
    if event.status == SheetWritebackStatus.COMPLETED:
        return event
    source = await session.get(SheetSource, event.source_id)
    scenario = await session.get(Scenario, event.scenario_id)
    event.status = SheetWritebackStatus.PROCESSING
    event.attempts += 1
    if source is None or scenario is None or not source.enabled:
        event.status = SheetWritebackStatus.FAILED
        event.error = "Sheet source/scenario is missing or source is disabled"
        return event
    if scenario.crm_row_id != event.crm_row_id:
        event.status = SheetWritebackStatus.FAILED
        event.error = "Scenario source identity is inconsistent"
        return event
    writeback_map = effective_writeback_column_map(source)
    approved = {
        key: value
        for key, value in event.changed_fields.items()
        if key in writeback_map and key in WRITEBACK_FIELDS
    }
    if set(approved) != set(event.changed_fields):
        event.status = SheetWritebackStatus.FAILED
        event.error = "Writeback contains fields outside the source allowlist"
        return event
    required_column_count = max(
        column_number(source.crm_row_id_column),
        *(column_number(writeback_map[field_name]) for field_name in approved),
    )
    try:
        await client.ensure_tab_column_capacity(
            source.spreadsheet_id,
            source.source_tab,
            required_column_count,
        )
    except GoogleSheetsSourceError as error:
        event.status = SheetWritebackStatus.FAILED
        event.error = str(error)
        event.next_attempt_at = retry_at(event.attempts)
        source.last_status = "writeback_failed"
        source.last_error = str(error)
        return event
    if scenario.source_row is None:
        await lock_source_append(session, source.id)
        identity_value = str(event.crm_row_id)
        try:
            source_row = await client.find_value_row(
                source.spreadsheet_id,
                source.source_tab,
                source.crm_row_id_column,
                identity_value,
                first_row=source.header_row + 1,
            )
            if source_row is None:
                last_column, row_values = append_row_values(
                    source,
                    event.crm_row_id,
                    approved,
                )
                try:
                    source_row = await client.append_row(
                        source.spreadsheet_id,
                        source.source_tab,
                        last_column,
                        row_values,
                    )
                except GoogleSheetsSourceError:
                    # The request may have reached Google even if its response was lost.
                    source_row = await client.find_value_row(
                        source.spreadsheet_id,
                        source.source_tab,
                        source.crm_row_id_column,
                        identity_value,
                        first_row=source.header_row + 1,
                    )
                    if source_row is None:
                        raise
            scenario.source_row = source_row
            scenario.source_sheet_id = source.spreadsheet_id
            scenario.source_tab = source.source_tab
        except GoogleSheetsSourceError as error:
            event.status = SheetWritebackStatus.FAILED
            event.error = str(error)
            event.next_attempt_at = retry_at(event.attempts)
            source.last_status = "writeback_failed"
            source.last_error = str(error)
            return event
    escaped_tab = source.source_tab.replace("'", "''")
    updates = [
        {
            "range": (
                f"'{escaped_tab}'!"
                f"{column_letters(writeback_map[field_name])}"
                f"{scenario.source_row}"
            ),
            "majorDimension": "ROWS",
            "values": [[sheet_cell_value(value, field_name=field_name)]],
        }
        for field_name, value in approved.items()
    ]
    updates.extend(
        {
            "range": (
                f"'{escaped_tab}'!"
                f"{column_letters(writeback_map[field_name])}"
                f"{source.header_row}"
            ),
            "majorDimension": "ROWS",
            "values": [[header]],
        }
        for field_name, header in MANAGED_EXTENSION_HEADERS.items()
        if field_name in approved
    )
    updates.insert(
        0,
        {
            "range": (
                f"'{escaped_tab}'!"
                f"{column_letters(source.crm_row_id_column)}"
                f"{scenario.source_row}"
            ),
            "majorDimension": "ROWS",
            "values": [[str(event.crm_row_id)]],
        },
    )
    try:
        await client.batch_update_values(
            source.spreadsheet_id,
            updates,
            max_retries=max_retries,
        )
    except GoogleSheetsSourceError as error:
        event.status = SheetWritebackStatus.FAILED
        event.error = str(error)
        event.next_attempt_at = retry_at(event.attempts)
        source.last_status = "writeback_failed"
        source.last_error = str(error)
        return event
    event.status = SheetWritebackStatus.COMPLETED
    event.error = None
    event.next_attempt_at = None
    event.processed_at = datetime.now(UTC)
    source.last_status = "writeback_completed"
    source.last_error = None
    source.last_sync_at = event.processed_at
    return event


def sheet_cell_value(value: Any, *, field_name: str | None = None) -> Any:
    """Return a localized, Google-compatible cell value.

    Values stored in the CRM and the transactional outbox remain canonical.
    Only known workflow fields are localized at the final Sheets boundary.
    """
    if value is None:
        return ""
    if hasattr(value, "value"):
        value = value.value
    if field_name is None or not isinstance(value, str):
        return value
    canonical_value = value.strip().lower()
    field_labels = SHEET_STATUS_LABELS.get(field_name)
    if field_labels is not None:
        return field_labels.get(canonical_value, value)
    if (
        field_name == "status"
        or field_name.endswith("_status")
        or field_name.endswith(".status")
        or field_name.endswith(".decision")
    ):
        return SHEET_GENERIC_STATUS_LABELS.get(canonical_value, value)
    return value


def scenario_writeback_snapshot(
    scenario: Scenario,
    *,
    include_empty: bool = True,
    scenarist_name: str | None = None,
) -> dict[str, Any]:
    """Serialize every Google-visible value without exposing assignment UUIDs."""
    approvals = {
        item.stage.value: item
        for item in getattr(scenario, "approvals", ())
    }
    values: dict[str, Any] = {}
    for item in SHEET_FIELDS:
        if item.field in WRITEBACK_ID_FIELDS:
            continue
        if item.field.startswith("approval."):
            _, stage, attribute = item.field.split(".")
            value = getattr(approvals.get(stage), attribute, None)
        else:
            value: Any = scenario
            for part in item.field.split("."):
                value = getattr(value, part, None)
                if value is None:
                    break
        if hasattr(value, "value"):
            value = value.value
        if value is not None or include_empty:
            values[item.field] = value
    if scenarist_name is not None:
        values["scenarist.name"] = scenarist_name
    return values


def crm_owned_writeback_snapshot(scenario: Scenario) -> dict[str, Any]:
    """Backfill CRM-owned cells without overwriting scenarist Sheet drafts."""
    return {
        field: value
        for field, value in scenario_writeback_snapshot(scenario).items()
        if field in CRM_OWNED_WRITEBACK_FIELDS
    }
