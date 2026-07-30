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
from sqlalchemy import select, text
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
    Scenario,
    ScenarioContent,
    ScenarioResearch,
    ScenarioStatus,
    SheetEventStatus,
    SheetInboundEvent,
    SheetSource,
    SheetWritebackEvent,
    SheetWritebackStatus,
)
from crm.schemas import ScenarioCreate
from crm.sheet import SHEET_FIELDS
from crm.workflow import approval_for, submit_for_responsible_review

WEBHOOK_SCHEMA_VERSION = 1
SHEETS_ORIGIN = "sheets"
CRM_ORIGIN = "crm"
COLUMN_RE = re.compile(r"^[A-Z]{1,3}$")
WRITEBACK_FIELDS = frozenset({item.field for item in SHEET_FIELDS} | {"comments.latest"})
REALTIME_SERVER_CONTROLLED_FIELDS = frozenset({"montage.material_status"})


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
        value = coerce_value(field_name, raw_value)
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
    if not submit_requested:
        event.status = SheetEventStatus.SKIPPED
        event.error = "Sheet row is not marked 'Отправить' for approval"
        event.processed_at = datetime.now(UTC)
        return event
    configured_inbound_fields = set(source.inbound_column_map)
    if "external_id" in (getattr(source, "writeback_column_map", None) or {}):
        configured_inbound_fields.add("external_id")
    allowed = (
        configured_inbound_fields
        & set(SAFE_IMPORT_FIELDS)
        - set(REALTIME_SERVER_CONTROLLED_FIELDS)
    )
    inbound_fields = {
        field_name: value
        for field_name, value in event.changed_fields.items()
        if field_name in allowed
    }
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
    incoming_external_id = inbound_fields.get("external_id")
    if scenario is None and incoming_external_id is None:
        event.status = SheetEventStatus.FAILED
        event.error = "Google Sheet ID is required for a new scenario"
        return event
    if incoming_external_id is not None:
        duplicate_query = select(Scenario.id).where(
            Scenario.external_id == incoming_external_id
        )
        if scenario is not None:
            duplicate_query = duplicate_query.where(Scenario.id != scenario.id)
        duplicate_id = await session.scalar(duplicate_query)
        if duplicate_id is not None:
            event.status = SheetEventStatus.FAILED
            event.error = (
                f"Google Sheet ID '{incoming_external_id}' already exists in CRM"
            )
            return event
    if scenario is not None and scenario.source_checksum == event.checksum:
        event.status = SheetEventStatus.SKIPPED
        event.error = "Checksum already applied"
        event.processed_at = datetime.now(UTC)
        return event
    if (
        scenario is not None
        and not inbound_update_allowed(scenario)
        and not workflow_fields_only(inbound_fields)
    ):
        event.status = SheetEventStatus.FAILED
        event.error = "CRM workflow has started; inbound source updates are locked"
        return event
    scenario_was_new = scenario is None
    if scenario_was_new:
        scenario = Scenario(
            project_id=source.project_id,
            assigned_scenarist_id=source.assigned_scenarist_id,
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
        _set_values(scenario, inbound_fields)
        source_payload = dict(scenario.source_payload or {})
        mapped_fields = dict(source_payload.get("mapped_fields") or {})
        mapped_fields.update(inbound_fields)
        source_payload["mapped_fields"] = mapped_fields
        source_payload["last_event_raw"] = event.raw
        if ignored_fields:
            source_payload["last_ignored_sheet_fields"] = ignored_fields
        scenario.source_payload = source_payload
        if scenario.status in {ScenarioStatus.DRAFT, ScenarioStatus.REVISION}:
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
    approved = {
        key: value
        for key, value in changed_fields.items()
        if key in source.writeback_column_map and key in WRITEBACK_FIELDS
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
    mapped = {
        column_number(source.writeback_column_map[field_name]): value
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
    approved = {
        key: value
        for key, value in event.changed_fields.items()
        if key in source.writeback_column_map and key in WRITEBACK_FIELDS
    }
    if set(approved) != set(event.changed_fields):
        event.status = SheetWritebackStatus.FAILED
        event.error = "Writeback contains fields outside the source allowlist"
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
                f"{column_letters(source.writeback_column_map[field_name])}"
                f"{scenario.source_row}"
            ),
            "majorDimension": "ROWS",
            "values": [[value]],
        }
        for field_name, value in approved.items()
    ]
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
