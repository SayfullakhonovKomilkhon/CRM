import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
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
    Scenario,
    ScenarioContent,
    ScenarioResearch,
    ScenarioStatus,
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
}

DATE_FIELDS = {"scenario_date", "deadline"}
INTEGER_FIELDS = {"score"}
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
    for date_format in ("%d.%m", "%d/%m"):
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


def coerce_value(field_name: str, value: Any) -> Any:
    if field_name in DATE_FIELDS:
        return parse_date(value)
    if field_name in INTEGER_FIELDS:
        return parse_integer(value)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _nested_payload(mapped: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    research: dict[str, Any] = {}
    content: dict[str, Any] = {}
    for field_name, value in mapped.items():
        if field_name.startswith("research."):
            research[field_name.split(".", 1)[1]] = value
        elif field_name.startswith("content."):
            content[field_name.split(".", 1)[1]] = value
        else:
            payload[field_name] = value
    if research:
        payload["research"] = research
    if content:
        payload["content"] = content
    return payload


def parse_sheet_values(
    values: list[list[Any]],
    config: GoogleSheetsTabConfig,
    spreadsheet_id: str,
    max_rows: int,
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
        mapped: dict[str, Any] = {}
        errors: list[str] = []
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
    duplicate_rows: set[int] = set()
    for scenario in existing_rows:
        if scenario.source_row in existing_by_row:
            duplicate_rows.add(scenario.source_row)
        else:
            existing_by_row[scenario.source_row] = scenario

    planned: list[PlannedRow] = []
    for parsed in snapshot.rows:
        existing = existing_by_row.get(parsed.row_number)
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
        elif workflow_is_locked(existing):
            parsed.errors.append(
                "CRM workflow has started; source updates are locked for this row"
            )
            planned.append(
                PlannedRow(
                    parsed=parsed,
                    action=GoogleSheetsRowAction.ERROR,
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


async def apply_planned_rows(
    session: AsyncSession,
    planned: list[PlannedRow],
    spreadsheet_id: str,
    config: GoogleSheetsTabConfig,
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
                status=(
                    ScenarioStatus.IN_REVIEW
                    if content_values.get("script_text")
                    else ScenarioStatus.DRAFT
                ),
                **scenario_values,
            )
            if research_values:
                scenario.research = ScenarioResearch(**research_values)
            if content_values:
                scenario.content = ScenarioContent(**content_values)
            session.add(scenario)
            item.existing = scenario
        else:
            scenario = item.existing
            scenario.project_id = config.project_id
            scenario.assigned_scenarist_id = config.assigned_scenarist_id
            scenario.source_checksum = item.parsed.checksum
            _set_nested_values(scenario, scenario_values)
            if research_values:
                if scenario.research is None:
                    scenario.research = ScenarioResearch()
                _set_nested_values(scenario.research, research_values)
            if content_values:
                if scenario.content is None:
                    scenario.content = ScenarioContent()
                _set_nested_values(scenario.content, content_values)
                if "script_text" in content_values:
                    scenario.status = (
                        ScenarioStatus.IN_REVIEW
                        if content_values["script_text"]
                        else ScenarioStatus.DRAFT
                    )
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
