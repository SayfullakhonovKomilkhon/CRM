import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from crm.config import GoogleSheetsTabConfig, Settings
from crm.google_sheets import (
    GoogleSheetsConfigurationError,
    GoogleSheetsSourceError,
    canonical_checksum,
    parse_date,
    parse_sheet_values,
    resolve_columns,
    workflow_is_locked,
)
from crm.models import ScenarioStatus
from crm.schemas import ScenarioCreate


def tab_config(**overrides):
    values = {
        "tab": "Сценарист",
        "header_row": 2,
        "project_id": uuid.uuid4(),
    }
    values.update(overrides)
    return GoogleSheetsTabConfig(**values)


def test_settings_parse_google_sheet_tabs_and_keep_import_disabled_by_default():
    settings = Settings(
        _env_file=None,
        google_sheets_tab_configs=(
            '[{"tab":"Сценарист","header_row":2,'
            f'"project_id":"{uuid.uuid4()}"}}]'
        ),
    )

    assert settings.google_sheets_enabled is False
    assert settings.google_sheets_tab_configs[0].header_row == 2


def test_settings_reject_duplicate_tabs_case_insensitively():
    project_id = uuid.uuid4()
    with pytest.raises(ValidationError, match="duplicate tab names"):
        Settings(
            _env_file=None,
            google_sheets_tab_configs=[
                {"tab": "Content", "project_id": project_id},
                {"tab": "content", "project_id": project_id},
            ],
        )


def test_parse_date_accepts_day_and_month_using_current_year():
    assert parse_date("25.05") == date(date.today().year, 5, 25)
    assert parse_date("27/05") == date(date.today().year, 5, 27)


def test_resolve_columns_supports_explicit_header_and_rejects_unsafe_fields():
    config = tab_config(
        columns={
            "scenario_date": "Created",
            "content.script_text": 2,
        }
    )
    resolved, _ = resolve_columns(["Created", "Body"], config)
    assert resolved["scenario_date"] == 0
    assert resolved["content.script_text"] == 1

    unsafe = tab_config(columns={"publication.is_published": 1})
    with pytest.raises(GoogleSheetsConfigurationError, match="Unsupported"):
        resolve_columns(["Published"], unsafe)


def test_resolve_columns_rejects_one_column_mapped_to_two_fields():
    config = tab_config(
        columns={
            "content.script_text": 1,
            "content.cover_text": 1,
        }
    )
    with pytest.raises(GoogleSheetsConfigurationError, match="multiple CRM fields"):
        resolve_columns(["Text"], config)


def test_parse_sheet_values_returns_stable_row_identity_and_validated_payload():
    config = tab_config()
    values = [
        ["Дата", "Сценарий", "Текст на обложке", "Общий балл"],
        ["24.07.2026", "Тестовый сценарий", "Обложка", "87"],
        ["", "", "", ""],
    ]
    first = parse_sheet_values(values, config, "sheet-id", 100)
    second = parse_sheet_values(values, config, "sheet-id", 100)

    assert first.checksum == second.checksum
    assert len(first.rows) == 1
    row = first.rows[0]
    assert row.row_number == 3
    assert row.payload["scenario_date"] == date(2026, 7, 24)
    assert row.payload["score"] == 87
    assert row.payload["content"]["script_text"] == "Тестовый сценарий"
    assert row.title == "Обложка"
    assert row.checksum == second.rows[0].checksum


def test_parse_sheet_values_reports_cell_validation_with_row_number():
    snapshot = parse_sheet_values(
        [["Дата", "Общий балл"], ["not-a-date", "101"]],
        tab_config(),
        "sheet-id",
        100,
    )
    assert snapshot.rows[0].row_number == 3
    assert snapshot.rows[0].payload is None
    assert any("scenario_date" in error for error in snapshot.rows[0].errors)
    assert any("score" in error for error in snapshot.rows[0].errors)


def test_parse_sheet_values_rejects_nonempty_rows_over_limit():
    with pytest.raises(GoogleSheetsSourceError, match="MAX_ROWS"):
        parse_sheet_values(
            [["Сценарий"], ["one"], [""], ["three"]],
            tab_config(),
            "sheet-id",
            1,
        )


def test_workflow_lock_starts_at_first_approval_record():
    base = {
        "montage": None,
        "publication": None,
        "final_revision_gate": None,
        "status": ScenarioStatus.DRAFT,
    }
    assert workflow_is_locked(SimpleNamespace(approvals=[], **base)) is False
    assert workflow_is_locked(
        SimpleNamespace(approvals=[SimpleNamespace()], **base)
    ) is True


def test_public_scenario_create_rejects_source_metadata():
    with pytest.raises(ValidationError, match="source_sheet_id"):
        ScenarioCreate(
            project_id=uuid.uuid4(),
            source_sheet_id="must-be-server-owned",
        )


def test_checksum_is_order_independent_for_mapping_keys():
    assert canonical_checksum({"b": 2, "a": date(2026, 7, 24)}) == canonical_checksum(
        {"a": date(2026, 7, 24), "b": 2}
    )
