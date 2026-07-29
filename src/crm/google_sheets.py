import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from crm.config import GoogleSheetsTabConfig, Settings
from crm.models import (
    ApprovalDecision,
    ApprovalStage,
    MontageTask,
    Publication,
    PublisherStatus,
    Scenario,
    ScenarioApproval,
    ScenarioContent,
    ScenarioResearch,
    ScenarioStatus,
    SheetSource,
)
from crm.schemas import (
    GoogleSheetsRowAction,
    GoogleSheetsRowResult,
    ScenarioCreate,
)

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

SAFE_IMPORT_FIELDS = (
    "scenario_date",
    "deadline",
    "score",
    "scenario_type",
    "visual_format",
    "speaker",
    "research.competitor_url",
    "research.competitor_category",
    "research.full_analysis",
    "research.performance_metrics",
    "research.transcription",
    "research.timeline",
    "research.why_viral",
    "research.takeaways",
    "research.improvements",
    "research.replication_template",
    "research.ai_analysis",
    "content.claude_context",
    "content.cover_text",
    "content.script_text",
    "content.montage_brief",
    "content.scenarist_comment",
    "content.hook",
    "content.retention",
    "content.call_to_action",
    "content.visual_notes",
    "content.score_recommendations",
    "content.ai_review",
    "approval.responsible_review.decision",
    "approval.responsible_review.comment",
    "approval.pre_generation_client.decision",
    "approval.pre_generation_client.comment",
    "approval.source_material.decision",
    "approval.source_material.comment",
    "approval.montage_compliance.decision",
    "approval.montage_compliance.comment",
    "approval.final_client.decision",
    "approval.final_client.comment",
    "montage.source_material_url",
    "montage.client_brand_style",
    "montage.extra_brief",
    "montage.external_editor_name",
    "montage.price",
    "montage.material_status",
    "montage.scenarist_material_comment",
    "montage.ready_material_url",
    "montage.ready_at",
    "montage.bot_visual_analysis",
    "montage.compliance_analysis",
    "montage.ai_analysis",
    "montage.scenarist_revision_status",
    "montage.scenarist_revision_comment",
    "publication.publication_date",
    "publication.publisher_brief",
    "publication.description_dzen",
    "publication.description_youtube",
    "publication.description_tiktok",
    "publication.description_instagram",
    "publication.is_published",
    "publication.instagram_url",
    "publication.engagement_metrics",
    "publication.publication_analysis",
    "publication.ai_social_descriptions",
    "publication.leia_script",
)

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "scenario_date": ("дата", "дата создания"),
    "deadline": ("дата дедлайна", "дедлайн"),
    "score": ("общий балл", "общий бал"),
    "scenario_type": ("тип сценария", "тип сценария 33 33 33"),
    "visual_format": ("формат визуала",),
    "speaker": ("спикер",),
    "research.competitor_url": (
        "ссылка на конкурента",
        "ссылка на конкурента перераспаковку",
        "ссылка на конкурента ссылка на перераспаковку",
    ),
    "research.competitor_category": ("категория конкурента",),
    "research.full_analysis": ("анализ из бота", "полный анализ для клода"),
    "research.performance_metrics": (
        "просмотры лайки комментарии хештеги вирусность",
        "просмотры реакции вирусность",
    ),
    "research.transcription": ("транскрибация", "трансгибация"),
    "research.timeline": ("таймлайн",),
    "research.why_viral": ("почему залетело",),
    "research.takeaways": ("забрать себе",),
    "research.improvements": ("улучшить",),
    "research.replication_template": ("шаблон для репликации", "шаблон репликации"),
    "research.ai_analysis": ("ии на этапе анализа", "ии анализ исследования"),
    "content.claude_context": (
        "место для вставки информации с клода",
        "место для информации с claude",
        "контекст claude",
    ),
    "content.cover_text": ("текст на обложке",),
    "content.script_text": ("сценарий",),
    "content.montage_brief": ("тз для монтажа",),
    "content.scenarist_comment": ("комментарий сценариста",),
    "content.hook": ("хук",),
    "content.retention": ("удержание",),
    "content.call_to_action": ("призыв к действию",),
    "content.visual_notes": ("визуальные заметки", "визуальный формат"),
    "content.score_recommendations": (
        "общий бал рекомендации",
        "общий балл рекомендации",
    ),
    "content.ai_review": ("ии на этапе проверки", "ии проверка сценария"),
    "approval.responsible_review.decision": ("одобрение ответственного",),
    "approval.responsible_review.comment": ("комментарий ответственного",),
    "approval.pre_generation_client.decision": ("одобрение сценария клиентом",),
    "approval.pre_generation_client.comment": ("комментарий клиента",),
    "approval.source_material.decision": ("одобрение исходника",),
    "approval.source_material.comment": ("комментарий к исходнику",),
    "approval.montage_compliance.decision": ("проверка монтажа по тз",),
    "approval.montage_compliance.comment": ("комментарий менеджера",),
    "approval.final_client.decision": ("одобрение готового клиентом",),
    "approval.final_client.comment": ("комментарий клиента к монтажу",),
    "montage.source_material_url": ("исходник и обложка",),
    "montage.client_brand_style": ("фирменный стиль",),
    "montage.extra_brief": ("дополнительное тз",),
    "montage.external_editor_name": ("монтажёр", "монтажер"),
    "montage.price": ("цена монтажа",),
    "montage.material_status": ("статус материала", "статус материалов"),
    "montage.scenarist_material_comment": ("комментарий сценариста к материалам",),
    "montage.ready_material_url": ("готовый материал",),
    "montage.ready_at": ("дата готового монтажа",),
    "montage.bot_visual_analysis": ("раскладка бота анализатора",),
    "montage.compliance_analysis": ("анализ соответствия",),
    "montage.ai_analysis": ("ии анализ монтажа",),
    "montage.scenarist_revision_status": ("исправление сценариста",),
    "montage.scenarist_revision_comment": ("комментарий сценариста к исправлению",),
    "publication.publication_date": ("дата публикации",),
    "publication.publisher_brief": ("тз для публициста",),
    "publication.description_dzen": ("описание dzen", "описание дзен"),
    "publication.description_youtube": ("описание youtube",),
    "publication.description_tiktok": ("описание tiktok",),
    "publication.description_instagram": ("описание instagram",),
    "publication.is_published": ("опубликовано",),
    "publication.instagram_url": ("ссылка instagram",),
    "publication.engagement_metrics": ("лайки просмотры",),
    "publication.publication_analysis": ("анализ публикации",),
    "publication.ai_social_descriptions": ("ии описания сетей",),
    "publication.leia_script": ("сценарий от леи",),
}

DATE_FIELDS = {"scenario_date", "deadline", "montage.ready_at", "publication.publication_date"}
INTEGER_FIELDS = {"score"}
DECIMAL_FIELDS = {"montage.price"}
BOOLEAN_FIELDS = {"publication.is_published"}
APPROVAL_DECISION_FIELDS = {
    field_name
    for field_name in SAFE_IMPORT_FIELDS
    if field_name.startswith("approval.") and field_name.endswith(".decision")
}
SCENARIO_FIELDS = {
    "scenario_date",
    "deadline",
    "score",
    "scenario_type",
    "visual_format",
    "speaker",
}
UNLOCKED_IMPORT_STATUSES = {ScenarioStatus.DRAFT, ScenarioStatus.IN_REVIEW}


class GoogleSheetsConfigurationError(ValueError):
    pass


class GoogleSheetsSourceError(RuntimeError):
    pass


@dataclass
class ParsedSheetRow:
    row_number: int
    payload: dict[str, Any] | None
    checksum: str | None
    title: str | None
    crm_row_id: uuid.UUID | None = None
    source_payload: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class SheetSnapshot:
    checksum: str
    rows: list[ParsedSheetRow]
    warnings: list[str]


@dataclass
class PlannedRow:
    parsed: ParsedSheetRow
    action: GoogleSheetsRowAction
    existing: Scenario | None = None

    def result(self) -> GoogleSheetsRowResult:
        return GoogleSheetsRowResult(
            row_number=self.parsed.row_number,
            action=self.action,
            checksum=self.parsed.checksum,
            scenario_id=self.existing.id if self.existing else None,
            external_id=self.existing.external_id if self.existing else None,
            title=self.parsed.title,
            errors=self.parsed.errors,
        )


def normalize_header(value: Any) -> str:
    return re.sub(r"[^\w]+", " ", str(value or "").casefold()).strip()


def canonical_checksum(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _column_index(
    field_name: str,
    config: GoogleSheetsTabConfig,
    normalized_headers: list[str],
) -> tuple[int | None, str | None]:
    override = config.columns.get(field_name)
    if isinstance(override, int):
        if override > len(normalized_headers):
            return None, f"{field_name}: configured column {override} is outside the header"
        return override - 1, None
    if isinstance(override, str):
        column_reference = override.strip().upper()
        if re.fullmatch(r"[A-Z]{1,3}", column_reference):
            column_index = _column_number(column_reference) - 1
            if column_index >= len(normalized_headers):
                return (
                    None,
                    f"{field_name}: configured column {column_reference} is outside the header",
                )
            return column_index, None
        requested = normalize_header(override)
        matches = [index for index, header in enumerate(normalized_headers) if header == requested]
        if len(matches) == 1:
            return matches[0], None
        if not matches:
            return None, f"{field_name}: configured header '{override}' was not found"
        return None, f"{field_name}: configured header '{override}' is duplicated"

    aliases = {normalize_header(alias) for alias in FIELD_ALIASES[field_name]}
    matches = [index for index, header in enumerate(normalized_headers) if header in aliases]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, f"{field_name}: matching header is ambiguous; configure a column index"
    return None, None


def resolve_columns(
    headers: list[Any],
    config: GoogleSheetsTabConfig,
) -> tuple[dict[str, int], list[str]]:
    unsupported = sorted(set(config.columns) - set(SAFE_IMPORT_FIELDS))
    if unsupported:
        raise GoogleSheetsConfigurationError(
            f"Unsupported import fields in tab config: {', '.join(unsupported)}"
        )

    normalized_headers = [normalize_header(value) for value in headers]
    resolved: dict[str, int] = {}
    warnings: list[str] = []
    for field_name in SAFE_IMPORT_FIELDS:
        index, warning = _column_index(field_name, config, normalized_headers)
        if warning:
            warnings.append(warning)
        if index is not None:
            resolved[field_name] = index
    if not resolved:
        raise GoogleSheetsConfigurationError("No safe import columns matched the header row")
    duplicate_indexes = {
        index for index in resolved.values() if list(resolved.values()).count(index) > 1
    }
    if duplicate_indexes:
        fields = [
            field_name
            for field_name, index in resolved.items()
            if index in duplicate_indexes
        ]
        raise GoogleSheetsConfigurationError(
            "One source column cannot map to multiple CRM fields: " + ", ".join(fields)
        )

    missing_recommended = [
        field_name
        for field_name in ("scenario_date", "content.script_text")
        if field_name not in resolved
    ]
    if missing_recommended:
        warnings.append(
            "Recommended columns are not mapped: " + ", ".join(missing_recommended)
        )
    unmapped = [field_name for field_name in SAFE_IMPORT_FIELDS if field_name not in resolved]
    if unmapped:
        warnings.append("Unmapped safe fields: " + ", ".join(unmapped))
    return resolved, warnings


def parse_date(value: Any) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    normalized = str(value).strip()
    for date_format in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(normalized, date_format).date()
        except ValueError:
            continue
    for date_format in ("%d.%m", "%d.%m.", "%d/%m"):
        try:
            return datetime.strptime(normalized, date_format).date().replace(
                year=date.today().year
            )
        except ValueError:
            continue
    raise ValueError(
        "expected date as YYYY-MM-DD, DD.MM.YYYY, DD/MM/YYYY, MM/DD/YYYY, "
        "DD.MM or DD/MM"
    )


def parse_integer(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        raise ValueError("expected an integer")
    if isinstance(value, int):
        return value
    normalized = str(value).strip()
    if not re.fullmatch(r"[+-]?\d+", normalized):
        raise ValueError("expected an integer")
    return int(normalized)


def parse_decimal(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).strip().replace(" ", "").replace(",", "."))
    except InvalidOperation as error:
        raise ValueError("expected a decimal number") from error


def parse_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().casefold()
    if normalized in {"true", "1", "да", "yes", "опубликовано"}:
        return True
    if normalized in {"", "false", "0", "нет", "no", "не опубликовано"}:
        return False
    raise ValueError("expected TRUE/FALSE")


def parse_approval_decision(value: Any) -> ApprovalDecision:
    normalized = re.sub(r"\s+", " ", str(value or "").strip().casefold())
    if not normalized or normalized in {"ожидает", "ожидание", "pending"}:
        return ApprovalDecision.PENDING
    if normalized in {"одобрено", "одобрен", "approved", "готово"}:
        return ApprovalDecision.APPROVED
    if normalized in {
        "доработать",
        "доработка",
        "на доработку",
        "revision",
        "исправить",
    }:
        return ApprovalDecision.REVISION
    if normalized in {"отказ", "отказано", "отклонено", "rejected"}:
        return ApprovalDecision.REJECTED
    raise ValueError("expected Ожидает, Одобрено, Доработать or Отказ")


def coerce_value(field_name: str, value: Any) -> Any:
    if field_name in DATE_FIELDS:
        return parse_date(value)
    if field_name in INTEGER_FIELDS:
        return parse_integer(value)
    if field_name in DECIMAL_FIELDS:
        return parse_decimal(value)
    if field_name in BOOLEAN_FIELDS:
        return parse_boolean(value)
    if field_name in APPROVAL_DECISION_FIELDS:
        return parse_approval_decision(value)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _nested_payload(mapped: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field_name, value in mapped.items():
        parts = field_name.split(".")
        if len(parts) == 1:
            payload[field_name] = value
            continue
        target = payload
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return payload


def _column_letters(index: int) -> str:
    result = ""
    value = index
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _source_payload(headers: list[Any], row: list[Any]) -> dict[str, Any]:
    return {
        _column_letters(index + 1): {
            "header": str(headers[index]).strip() if index < len(headers) else "",
            "value": value,
        }
        for index, value in enumerate(row)
        if str(value).strip()
    }


def _column_number(reference: str) -> int:
    value = 0
    for character in reference.strip().upper():
        if not "A" <= character <= "Z":
            raise ValueError("invalid column letters")
        value = value * 26 + ord(character) - 64
    return value


def parse_sheet_values(
    values: list[list[Any]],
    config: GoogleSheetsTabConfig,
    spreadsheet_id: str,
    max_rows: int,
    crm_row_id_column: str | None = None,
) -> SheetSnapshot:
    if not values:
        raise GoogleSheetsSourceError("The configured header row is empty")
    headers = values[0]
    resolved, warnings = resolve_columns(headers, config)
    data_rows = values[1:]
    if len(data_rows) > max_rows:
        if any(
            str(value).strip()
            for overflow in data_rows[max_rows:]
            for value in overflow
        ):
            raise GoogleSheetsSourceError(
                f"Tab exceeds GOOGLE_SHEETS_MAX_ROWS={max_rows}"
            )
        data_rows = data_rows[:max_rows]

    parsed_rows: list[ParsedSheetRow] = []
    for offset, row in enumerate(data_rows, start=1):
        row_number = config.header_row + offset
        if not any(str(value).strip() for value in row):
            continue
        source_payload = _source_payload(headers, row)
        mapped: dict[str, Any] = {}
        errors: list[str] = []
        crm_row_id = None
        if crm_row_id_column:
            identity_index = _column_number(crm_row_id_column) - 1
            identity_value = row[identity_index] if identity_index < len(row) else None
            if str(identity_value or "").strip():
                try:
                    crm_row_id = uuid.UUID(str(identity_value).strip())
                except ValueError:
                    errors.append("crm_row_id: expected UUID in protected identity column")
        for field_name, column_index in resolved.items():
            raw_value = row[column_index] if column_index < len(row) else None
            try:
                mapped[field_name] = coerce_value(field_name, raw_value)
            except ValueError as error:
                errors.append(f"{field_name}: {error}")
        if not any(value is not None for value in mapped.values()):
            errors.append("No importable values found in configured columns")

        nested = _nested_payload(mapped)
        validated_payload: dict[str, Any] | None = None
        title = None
        try:
            workflow_payload = {
                key: nested.pop(key)
                for key in ("approval", "montage", "publication")
                if key in nested
            }
            validated = ScenarioCreate(
                project_id=config.project_id,
                assigned_scenarist_id=config.assigned_scenarist_id,
                **nested,
            )
            candidate_payload = validated.model_dump(
                mode="python",
                exclude_unset=True,
                exclude={"project_id", "assigned_scenarist_id"},
            )
            candidate_payload.update(workflow_payload)
            if not errors:
                validated_payload = candidate_payload
                content = candidate_payload.get("content") or {}
                title = (
                    content.get("cover_text")
                    or content.get("hook")
                    or f"Google Sheets row {row_number}"
                )
        except ValidationError as error:
            errors.extend(
                f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                for item in error.errors()
            )
        checksum = (
            canonical_checksum(
                {
                    "project_id": str(config.project_id),
                    "assigned_scenarist_id": (
                        str(config.assigned_scenarist_id)
                        if config.assigned_scenarist_id
                        else None
                    ),
                    "payload": validated_payload,
                    "crm_row_id": str(crm_row_id) if crm_row_id else None,
                    "source_payload": source_payload,
                }
            )
            if validated_payload is not None
            else None
        )
        parsed_rows.append(
            ParsedSheetRow(
                row_number=row_number,
                payload=validated_payload,
                checksum=checksum,
                title=title,
                crm_row_id=crm_row_id,
                source_payload=source_payload,
                errors=errors,
            )
        )

    snapshot_checksum = canonical_checksum(
        {
            "spreadsheet_id": spreadsheet_id,
            "tab": config.tab,
            "header_row": config.header_row,
            "project_id": str(config.project_id),
            "assigned_scenarist_id": (
                str(config.assigned_scenarist_id)
                if config.assigned_scenarist_id
                else None
            ),
            "columns": config.columns,
            "values": values,
        }
    )
    return SheetSnapshot(
        checksum=snapshot_checksum,
        rows=parsed_rows,
        warnings=warnings,
    )


def workflow_is_locked(scenario: Scenario) -> bool:
    return bool(
        scenario.approvals
        or scenario.montage is not None
        or scenario.publication is not None
        or scenario.final_revision_gate is not None
        or scenario.status not in UNLOCKED_IMPORT_STATUSES
    )


async def plan_rows(
    session: AsyncSession,
    snapshot: SheetSnapshot,
    spreadsheet_id: str,
    tab: str,
    *,
    for_update: bool = False,
    source: SheetSource | None = None,
) -> list[PlannedRow]:
    query = (
        select(Scenario)
        .where(
            Scenario.source_sheet_id == spreadsheet_id,
            Scenario.source_tab == tab,
            Scenario.source_row.is_not(None),
        )
        .options(
            selectinload(Scenario.approvals),
            selectinload(Scenario.montage),
            selectinload(Scenario.publication),
            selectinload(Scenario.final_revision_gate),
            selectinload(Scenario.research),
            selectinload(Scenario.content),
        )
    )
    if for_update:
        query = query.with_for_update()
    existing_rows = list((await session.scalars(query)).all())
    existing_by_row: dict[int, Scenario] = {}
    existing_by_identity: dict[uuid.UUID, Scenario] = {}
    duplicate_rows: set[int] = set()
    for scenario in existing_rows:
        if scenario.source_row in existing_by_row:
            duplicate_rows.add(scenario.source_row)
        else:
            existing_by_row[scenario.source_row] = scenario
        if scenario.crm_row_id is not None:
            existing_by_identity[scenario.crm_row_id] = scenario

    planned: list[PlannedRow] = []
    for parsed in snapshot.rows:
        existing = (
            existing_by_identity.get(parsed.crm_row_id)
            if parsed.crm_row_id is not None
            else None
        ) or existing_by_row.get(parsed.row_number)
        if parsed.row_number in duplicate_rows:
            parsed.errors.append("Duplicate source identity already exists in CRM")
        if parsed.errors:
            planned.append(
                PlannedRow(parsed=parsed, action=GoogleSheetsRowAction.ERROR, existing=existing)
            )
        elif existing is None:
            planned.append(
                PlannedRow(parsed=parsed, action=GoogleSheetsRowAction.CREATED)
            )
        elif existing.source_checksum == parsed.checksum:
            planned.append(
                PlannedRow(
                    parsed=parsed,
                    action=GoogleSheetsRowAction.SKIPPED,
                    existing=existing,
                )
            )
        else:
            planned.append(
                PlannedRow(
                    parsed=parsed,
                    action=GoogleSheetsRowAction.UPDATED,
                    existing=existing,
                )
            )
    return planned


async def lock_sync_transaction(
    session: AsyncSession,
    spreadsheet_id: str,
    tab: str,
) -> None:
    """Serialize syncs for one source while preserving SQLite test compatibility."""
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:source_key, 0))"),
            {"source_key": f"{spreadsheet_id}\x1f{tab}"},
        )


def _set_nested_values(target: Any, values: dict[str, Any]) -> None:
    for key, value in values.items():
        setattr(target, key, value)


def _approval_values(payload: dict[str, Any]) -> dict[ApprovalStage, dict[str, Any]]:
    raw = payload.get("approval") or {}
    return {
        stage: raw.get(stage.value) or {}
        for stage in ApprovalStage
        if raw.get(stage.value)
    }


def _apply_approvals(
    scenario: Scenario,
    values: dict[ApprovalStage, dict[str, Any]],
) -> None:
    existing = {approval.stage: approval for approval in scenario.approvals}
    for stage, stage_values in values.items():
        approval = existing.get(stage)
        if approval is None:
            approval = ScenarioApproval(stage=stage)
            scenario.approvals.append(approval)
        if "decision" in stage_values:
            approval.decision = stage_values["decision"]
        if "comment" in stage_values:
            approval.comment = stage_values["comment"]
        approval.decided_by_id = None
        approval.decided_at = None


def _decision(
    scenario: Scenario,
    stage: ApprovalStage,
) -> ApprovalDecision:
    approval = next(
        (item for item in scenario.approvals if item.stage == stage),
        None,
    )
    return approval.decision if approval else ApprovalDecision.PENDING


def _derive_status(
    scenario: Scenario,
) -> ScenarioStatus:
    if scenario.publication and scenario.publication.is_published:
        return ScenarioStatus.PUBLISHED
    final = _decision(scenario, ApprovalStage.FINAL_CLIENT)
    if final == ApprovalDecision.APPROVED:
        return ScenarioStatus.APPROVED
    if final in {ApprovalDecision.REVISION, ApprovalDecision.REJECTED}:
        return ScenarioStatus.MANAGER_REVISION_REVIEW
    compliance = _decision(scenario, ApprovalStage.MONTAGE_COMPLIANCE)
    if compliance == ApprovalDecision.APPROVED:
        return ScenarioStatus.CLIENT_REVIEW
    if compliance in {ApprovalDecision.REVISION, ApprovalDecision.REJECTED}:
        return ScenarioStatus.EDITING
    if scenario.montage and scenario.montage.ready_material_url:
        return ScenarioStatus.EDITING
    source = _decision(scenario, ApprovalStage.SOURCE_MATERIAL)
    if source == ApprovalDecision.APPROVED:
        if scenario.montage and (
            scenario.montage.assigned_editor_id or scenario.montage.external_editor_name
        ):
            return ScenarioStatus.HANDED_TO_EDITOR
        return ScenarioStatus.SENT_TO_GENERATION
    if source in {ApprovalDecision.REVISION, ApprovalDecision.REJECTED}:
        return ScenarioStatus.SENT_TO_GENERATION
    client = _decision(scenario, ApprovalStage.PRE_GENERATION_CLIENT)
    if client == ApprovalDecision.APPROVED:
        return ScenarioStatus.SENT_TO_GENERATION
    if client in {ApprovalDecision.REVISION, ApprovalDecision.REJECTED}:
        return ScenarioStatus.REVISION
    responsible = _decision(scenario, ApprovalStage.RESPONSIBLE_REVIEW)
    if responsible == ApprovalDecision.APPROVED:
        return ScenarioStatus.CLIENT_REVIEW
    if responsible in {ApprovalDecision.REVISION, ApprovalDecision.REJECTED}:
        return ScenarioStatus.REVISION
    if scenario.content and scenario.content.script_text:
        return ScenarioStatus.IN_REVIEW
    return ScenarioStatus.DRAFT


def _apply_workflow_values(scenario: Scenario, payload: dict[str, Any]) -> None:
    approvals = _approval_values(payload)
    montage_values = payload.get("montage") or {}
    publication_values = payload.get("publication") or {}
    _apply_approvals(scenario, approvals)
    if montage_values:
        if scenario.montage is None:
            scenario.montage = MontageTask()
        _set_nested_values(scenario.montage, montage_values)
    if publication_values:
        if scenario.publication is None:
            scenario.publication = Publication()
        _set_nested_values(scenario.publication, publication_values)
        scenario.publication.publisher_status = (
            PublisherStatus.PUBLISHED
            if scenario.publication.is_published
            else PublisherStatus.PENDING
        )
    scenario.status = _derive_status(scenario)


async def apply_planned_rows(
    session: AsyncSession,
    planned: list[PlannedRow],
    spreadsheet_id: str,
    config: GoogleSheetsTabConfig,
    source: SheetSource | None = None,
) -> list[GoogleSheetsRowResult]:
    for item in planned:
        if item.action in {GoogleSheetsRowAction.ERROR, GoogleSheetsRowAction.SKIPPED}:
            continue
        payload = item.parsed.payload or {}
        scenario_values = {
            key: value for key, value in payload.items() if key in SCENARIO_FIELDS
        }
        research_values = payload.get("research") or {}
        content_values = payload.get("content") or {}
        if item.existing is None:
            scenario = Scenario(
                project_id=config.project_id,
                assigned_scenarist_id=config.assigned_scenarist_id,
                source_sheet_id=spreadsheet_id,
                source_tab=config.tab,
                source_row=item.parsed.row_number,
                source_checksum=item.parsed.checksum,
                source_payload=item.parsed.source_payload,
                sheet_source_id=source.id if source else None,
                crm_row_id=item.parsed.crm_row_id or uuid.uuid4(),
                status=ScenarioStatus.DRAFT,
                **scenario_values,
            )
            if research_values:
                scenario.research = ScenarioResearch(**research_values)
            if content_values:
                scenario.content = ScenarioContent(**content_values)
            _apply_workflow_values(scenario, payload)
            session.add(scenario)
            item.existing = scenario
        else:
            scenario = item.existing
            scenario.project_id = config.project_id
            scenario.assigned_scenarist_id = config.assigned_scenarist_id
            scenario.source_checksum = item.parsed.checksum
            scenario.source_payload = item.parsed.source_payload
            if source:
                scenario.sheet_source_id = source.id
            if item.parsed.crm_row_id:
                scenario.crm_row_id = item.parsed.crm_row_id
            _set_nested_values(scenario, scenario_values)
            if research_values:
                if scenario.research is None:
                    scenario.research = ScenarioResearch()
                _set_nested_values(scenario.research, research_values)
            if content_values:
                if scenario.content is None:
                    scenario.content = ScenarioContent()
                _set_nested_values(scenario.content, content_values)
            _apply_workflow_values(scenario, payload)
    await session.flush()
    return [item.result() for item in planned]


def summarize_results(results: list[GoogleSheetsRowResult]) -> dict[str, int]:
    return {
        "total_rows": len(results),
        "created": sum(item.action == GoogleSheetsRowAction.CREATED for item in results),
        "updated": sum(item.action == GoogleSheetsRowAction.UPDATED for item in results),
        "skipped": sum(item.action == GoogleSheetsRowAction.SKIPPED for item in results),
        "errors": sum(item.action == GoogleSheetsRowAction.ERROR for item in results),
    }


def serialized_row_report(results: list[GoogleSheetsRowResult]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in results]


def comparable_row_report(results: list[GoogleSheetsRowResult]) -> list[tuple[Any, ...]]:
    return [
        (
            item.row_number,
            item.action.value,
            item.checksum,
            item.scenario_id and str(item.scenario_id),
            item.external_id,
            tuple(item.errors),
        )
        for item in results
    ]


class GoogleSheetsClient:
    def __init__(self, service_account_json: str):
        try:
            info = json.loads(service_account_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise GoogleSheetsConfigurationError(
                "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON"
            ) from error
        if not isinstance(info, dict):
            raise GoogleSheetsConfigurationError(
                "GOOGLE_SERVICE_ACCOUNT_JSON must contain a JSON object"
            )
        try:
            self._credentials = Credentials.from_service_account_info(
                info,
                scopes=[SHEETS_SCOPE],
            )
        except (TypeError, ValueError, KeyError) as error:
            raise GoogleSheetsConfigurationError(
                "GOOGLE_SERVICE_ACCOUNT_JSON is not a valid service-account key"
            ) from error
        self._refresh_lock = asyncio.Lock()

    async def _access_token(self) -> str:
        async with self._refresh_lock:
            if not self._credentials.valid:
                try:
                    await asyncio.to_thread(self._credentials.refresh, Request())
                except Exception as error:
                    raise GoogleSheetsSourceError(
                        "Google service-account authentication failed"
                    ) from error
            if not self._credentials.token:
                raise GoogleSheetsSourceError("Google did not return an access token")
            return self._credentials.token

    async def fetch_values(
        self,
        spreadsheet_id: str,
        tab: str,
        header_row: int,
        max_rows: int,
    ) -> list[list[Any]]:
        token = await self._access_token()
        escaped_tab = tab.replace("'", "''")
        last_row = header_row + max_rows + 1
        range_name = f"'{escaped_tab}'!A{header_row}:ZZ{last_row}"
        url = (
            "https://sheets.googleapis.com/v4/spreadsheets/"
            f"{quote(spreadsheet_id, safe='')}/values/{quote(range_name, safe='')}"
        )
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "majorDimension": "ROWS",
                        "valueRenderOption": "FORMATTED_VALUE",
                        "dateTimeRenderOption": "FORMATTED_STRING",
                    },
                )
        except httpx.HTTPError as error:
            raise GoogleSheetsSourceError("Google Sheets API request failed") from error
        if response.status_code == 404:
            raise GoogleSheetsSourceError(
                "Spreadsheet or configured tab was not found"
            )
        if response.status_code in {401, 403}:
            raise GoogleSheetsSourceError(
                "Service account cannot read the configured spreadsheet"
            )
        if response.status_code >= 400:
            raise GoogleSheetsSourceError(
                f"Google Sheets API returned HTTP {response.status_code}"
            )
        payload = response.json()
        values = payload.get("values", [])
        if not isinstance(values, list):
            raise GoogleSheetsSourceError("Google Sheets API returned invalid values")
        return values

    async def batch_update_values(
        self,
        spreadsheet_id: str,
        updates: list[dict[str, Any]],
        *,
        max_retries: int = 4,
    ) -> None:
        """Write explicitly prepared A1 ranges; callers enforce the column allowlist."""
        if not updates:
            return
        token = await self._access_token()
        url = (
            "https://sheets.googleapis.com/v4/spreadsheets/"
            f"{quote(spreadsheet_id, safe='')}/values:batchUpdate"
        )
        payload = {
            "valueInputOption": "RAW",
            "includeValuesInResponse": False,
            "data": updates,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            for attempt in range(max_retries):
                try:
                    response = await client.post(
                        url,
                        headers={"Authorization": f"Bearer {token}"},
                        json=payload,
                    )
                except httpx.HTTPError as error:
                    if attempt + 1 >= max_retries:
                        raise GoogleSheetsSourceError(
                            "Google Sheets write request failed"
                        ) from error
                    await asyncio.sleep(2**attempt)
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 >= max_retries:
                        raise GoogleSheetsSourceError(
                            f"Google Sheets API returned HTTP {response.status_code}"
                        )
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else 2**attempt
                    await asyncio.sleep(min(delay, 30))
                    continue
                if response.status_code in {401, 403}:
                    raise GoogleSheetsSourceError(
                        "Service account cannot write to the configured spreadsheet"
                    )
                if response.status_code >= 400:
                    raise GoogleSheetsSourceError(
                        f"Google Sheets API returned HTTP {response.status_code}"
                    )
                return


_google_sheets_clients: dict[str, GoogleSheetsClient] = {}


def google_sheets_client(settings: Settings) -> GoogleSheetsClient:
    configured_secret = settings.google_service_account_json
    if not configured_secret:
        raise GoogleSheetsConfigurationError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not configured"
        )
    secret = configured_secret.get_secret_value()
    secret_fingerprint = canonical_checksum(secret)
    client = _google_sheets_clients.get(secret_fingerprint)
    if client is None:
        client = GoogleSheetsClient(secret)
        _google_sheets_clients.clear()
        _google_sheets_clients[secret_fingerprint] = client
    return client
