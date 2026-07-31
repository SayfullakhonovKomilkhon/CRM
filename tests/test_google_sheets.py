import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from crm.config import GoogleSheetsTabConfig, Settings
from crm.google_sheets import (
    SAFE_IMPORT_FIELDS,
    GoogleSheetsConfigurationError,
    GoogleSheetsSourceError,
    _apply_workflow_values,
    advance_external_id_sequence,
    canonical_checksum,
    parse_date,
    parse_sheet_values,
    resolve_columns,
    submission_requested,
    workflow_is_locked,
)
from crm.models import ApprovalDecision, ApprovalStage, Scenario, ScenarioContent, ScenarioStatus
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
    assert parse_date("21.04.") == date(date.today().year, 4, 21)
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

    by_letters, _ = resolve_columns(
        ["Created", "Body"],
        tab_config(columns={"content.script_text": "B"}),
    )
    assert by_letters["content.script_text"] == 1

    unsafe = tab_config(columns={"unknown.private_field": 1})
    with pytest.raises(GoogleSheetsConfigurationError, match="Unsupported"):
        resolve_columns(["Published"], unsafe)


def test_parse_sheet_values_imports_only_scenarist_owned_fields_and_identity():
    row_id = uuid.uuid4()
    snapshot = parse_sheet_values(
        [
            [
                "Сценарий",
                "Одобрение ответственного",
                "Одобрение сценария клиентом",
                "Одобрение исходника",
                "Проверка монтажа по ТЗ",
                "Одобрение готового клиентом",
                "Исходник и обложка",
                "ТЗ для публициста",
                "Готовый материал",
                "Дата публикации",
                "Опубликовано",
                "Отправка на согласование",
                "",
            ],
            [
                "Полный сценарий",
                "Одобрено",
                "Одобрено",
                "Доработать",
                "Отказ",
                "Ожидает",
                "https://example.com/source",
                "Текст для публициста",
                "https://example.com/video",
                "25.07",
                "TRUE",
                "Отправить",
                str(row_id),
            ],
        ],
        tab_config(),
        "sheet-id",
        100,
        crm_row_id_column="M",
    )

    row = snapshot.rows[0]
    assert row.errors == []
    assert row.crm_row_id == row_id
    assert row.payload["content"]["script_text"] == "Полный сценарий"
    assert (
        row.payload["montage"]["source_material_url"]
        == "https://example.com/source"
    )
    assert row.payload["publication"]["publisher_brief"] == "Текст для публициста"
    assert "approval" not in row.payload
    assert "ready_material_url" not in row.payload["montage"]
    assert str(row.payload["publication"]["publication_date"]) == "2026-07-25"
    assert "is_published" not in row.payload["publication"]
    assert row.source_payload["M"]["value"] == str(row_id)


def test_sheet_inbound_allowlist_excludes_other_role_fields():
    assert "external_id" in SAFE_IMPORT_FIELDS
    assert "content.script_text" in SAFE_IMPORT_FIELDS
    assert "montage.source_material_url" in SAFE_IMPORT_FIELDS
    assert "publication.publisher_brief" in SAFE_IMPORT_FIELDS
    assert "approval.responsible_review.decision" not in SAFE_IMPORT_FIELDS
    assert "montage.ready_material_url" not in SAFE_IMPORT_FIELDS
    assert "montage.price" not in SAFE_IMPORT_FIELDS
    assert "publication.is_published" not in SAFE_IMPORT_FIELDS
    assert "publication.publisher_status" not in SAFE_IMPORT_FIELDS


def test_google_sheet_visible_id_becomes_scenario_external_id():
    snapshot = parse_sheet_values(
        [
            ["ID", "Сценарий", "Отправка на согласование"],
            ["147", "Сценарий с Google ID", "Отправить"],
        ],
        tab_config(),
        "sheet-id",
        100,
    )

    row = snapshot.rows[0]
    assert row.errors == []
    assert row.payload["external_id"] == "147"


def test_submitted_google_row_rejects_an_empty_configured_id():
    snapshot = parse_sheet_values(
        [
            ["ID", "Сценарий", "Отправка на согласование"],
            ["", "Сценарий без ID", "Отправить"],
        ],
        tab_config(),
        "sheet-id",
        100,
    )

    row = snapshot.rows[0]
    assert row.payload is None
    assert "external_id: ID cannot be empty" in row.errors


async def test_numeric_google_id_advances_postgresql_sequence():
    class SequenceSession:
        def __init__(self):
            self.calls = []

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        async def execute(self, statement, params):
            self.calls.append((str(statement), params))

    session = SequenceSession()
    await advance_external_id_sequence(session, "147")
    await advance_external_id_sequence(session, "client-row")

    assert len(session.calls) == 1
    assert "scenario_external_id_seq" in session.calls[0][0]
    assert session.calls[0][1] == {"external_id": 147}


def test_existing_identified_row_is_skipped_without_submission_marker():
    row_id = uuid.uuid4()
    snapshot = parse_sheet_values(
        [
            ["Сценарий", "Отправка на согласование", ""],
            ["Обновлено в Google", "", str(row_id)],
        ],
        tab_config(),
        "sheet-id",
        100,
        crm_row_id_column="C",
    )

    row = snapshot.rows[0]
    assert row.crm_row_id is None
    assert row.submission_requested is False
    assert row.payload is None


def test_workflow_import_derives_published_status_and_keeps_all_decisions():
    scenario = Scenario(project_id=uuid.uuid4(), status=ScenarioStatus.DRAFT)
    scenario.content = ScenarioContent(script_text="Сценарий")
    payload = {
        "approval": {
            "responsible_review": {"decision": ApprovalDecision.APPROVED},
            "pre_generation_client": {"decision": ApprovalDecision.APPROVED},
            "source_material": {"decision": ApprovalDecision.APPROVED},
            "montage_compliance": {"decision": ApprovalDecision.APPROVED},
            "final_client": {"decision": ApprovalDecision.APPROVED},
        },
        "publication": {"is_published": True},
    }

    _apply_workflow_values(scenario, payload)

    assert scenario.status == ScenarioStatus.PUBLISHED
    assert {item.stage for item in scenario.approvals} == set(ApprovalStage)
    assert all(item.decision == ApprovalDecision.APPROVED for item in scenario.approvals)
    assert scenario.publication.is_published is True


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
        [
            "Дата",
            "Сценарий",
            "Текст на обложке",
            "Общий балл",
            "Отправка на согласование",
        ],
        ["24.07.2026", "Тестовый сценарий", "Обложка", "87", "Отправить"],
        ["", "", "", "", ""],
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
        [
            ["Дата", "Общий балл", "Отправка на согласование"],
            ["not-a-date", "101", "Отправить"],
        ],
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
            [
                ["Сценарий", "Отправка на согласование"],
                ["one", "Отправить"],
                ["", ""],
                ["three", "Отправить"],
            ],
            tab_config(),
            "sheet-id",
            1,
        )


def test_unsubmitted_rows_over_limit_do_not_block_submitted_rows():
    snapshot = parse_sheet_values(
        [
            ["Сценарий", "Отправка на согласование"],
            ["one", "Отправить"],
            ["draft", ""],
        ],
        tab_config(),
        "sheet-id",
        1,
    )

    assert len(snapshot.rows) == 1
    assert snapshot.rows[0].submission_requested is True


def test_draft_edits_do_not_invalidate_a_submitted_snapshot():
    headers = ["Сценарий", "Отправка на согласование"]
    config = tab_config()
    first = parse_sheet_values(
        [headers, ["approved row", "Отправить"], ["draft one", ""]],
        config,
        "sheet-id",
        100,
    )
    second = parse_sheet_values(
        [headers, ["approved row", "Отправить"], ["changed draft", ""]],
        config,
        "sheet-id",
        100,
    )

    assert first.checksum == second.checksum


def test_parse_sheet_values_skips_unsubmitted_drafts_without_validating_them():
    snapshot = parse_sheet_values(
        [
            ["Дата", "Общий балл", "Отправка на согласование"],
            ["не дата", "999", ""],
            ["", "", "Отправить"],
        ],
        tab_config(),
        "sheet-id",
        100,
    )

    draft, submitted = snapshot.rows
    assert draft.submission_requested is False
    assert draft.payload is None
    assert draft.errors == []
    assert submitted.submission_requested is True
    assert all(value is None for value in submitted.payload.values())
    assert submitted.errors == []


def test_submission_marker_is_exact_and_header_is_required():
    assert submission_requested("Отправить")
    assert submission_requested("  ОТПРАВИТЬ ")
    assert not submission_requested("Готово")
    assert not submission_requested("")

    with pytest.raises(
        GoogleSheetsConfigurationError,
        match="Отправка на согласование",
    ):
        parse_sheet_values(
            [["Сценарий"], ["Текст"]],
            tab_config(),
            "sheet-id",
            100,
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
