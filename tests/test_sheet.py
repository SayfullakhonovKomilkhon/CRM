from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from crm.models import ApprovalDecision, ApprovalStage, Role
from crm.schemas import ApprovalUpdate, EditorMontageUpdate, EditorStatus
from crm.sheet import columns_for_role, editable_fields_for_role


def test_all_roles_receive_the_same_sheet_columns() -> None:
    columns_by_role = {
        role: [column.field for column in columns_for_role(role)] for role in Role
    }

    assert all(columns == columns_by_role[Role.MANAGER] for columns in columns_by_role.values())
    assert "montage.price" in columns_by_role[Role.CLIENT]
    assert "research.ai_analysis" in columns_by_role[Role.CLIENT]


def test_editor_can_edit_all_visible_work_fields() -> None:
    scenario = SimpleNamespace(
        available_sections=["content", "montage"],
        available_approval_stages=[
            ApprovalStage.SOURCE_MATERIAL,
            ApprovalStage.MONTAGE_COMPLIANCE,
        ],
    )

    editable = set(editable_fields_for_role(scenario, Role.EDITOR))

    assert "scenario_date" in editable
    assert "content.script_text" in editable
    assert "content.montage_brief" in editable
    assert "montage.client_brand_style" in editable
    assert "montage.price" in editable
    assert "approval.source_material.decision" in editable
    assert "approval.montage_compliance.comment" in editable
    assert "external_id" not in editable
    assert "project.name" not in editable
    assert "scenarist.name" not in editable


@pytest.mark.parametrize("status", list(EditorStatus))
def test_editor_update_accepts_fixed_status_values(status: EditorStatus) -> None:
    payload = EditorMontageUpdate(editor_status=status.value)

    assert payload.editor_status == status


def test_editor_update_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        EditorMontageUpdate(editor_status="Произвольный статус")


def test_sheet_column_publishes_editor_status_options() -> None:
    column = next(
        item for item in columns_for_role(Role.EDITOR) if item.field == "montage.editor_status"
    )

    assert column.allowed_values == [status.value for status in EditorStatus]


def test_client_sheet_has_separate_note_and_readonly_decision_dates() -> None:
    columns = {column.field for column in columns_for_role(Role.CLIENT)}
    scenario = SimpleNamespace(
        available_sections=["content", "approvals"],
        available_approval_stages=[ApprovalStage.PRE_GENERATION_CLIENT],
    )
    editable = set(editable_fields_for_role(scenario, Role.CLIENT))

    assert "approval.pre_generation_client.note" in columns
    assert "approval.pre_generation_client.decided_at" in columns
    assert "approval.final_client.decided_at" in columns
    assert "approval.pre_generation_client.note" in editable
    assert "approval.pre_generation_client.decided_at" not in editable
    assert "approval.final_client.decided_at" not in editable


def test_approval_payload_keeps_note_separate_from_comment() -> None:
    payload = ApprovalUpdate(
        decision=ApprovalDecision.APPROVED,
        comment="Замечание к решению",
        note="Отдельное примечание",
    )

    assert payload.comment == "Замечание к решению"
    assert payload.note == "Отдельное примечание"


@pytest.mark.parametrize("role", [Role.MANAGER, Role.SCENARIST])
def test_manager_and_scenarist_can_edit_all_visible_work_fields(role: Role) -> None:
    scenario = SimpleNamespace(
        available_sections=["content", "approvals", "publication"],
        available_approval_stages=list(ApprovalStage),
    )

    editable = set(editable_fields_for_role(scenario, role))

    assert "research.full_analysis" in editable
    assert "content.script_text" in editable
    assert "approval.pre_generation_client.comment" in editable
    assert "approval.final_client.decision" in editable
    assert "publication.description_instagram" in editable
    assert "publication.is_published" in editable
    assert "external_id" not in editable
    assert "project.name" not in editable
    assert "project.client_name" not in editable
    assert "scenarist.name" not in editable
    assert "approval.pre_generation_client.decided_at" not in editable


@pytest.mark.parametrize("role", [Role.MANAGER, Role.SCENARIST, Role.EDITOR])
def test_internal_roles_can_edit_all_available_work_fields(role: Role) -> None:
    scenario = SimpleNamespace(
        available_sections=["content", "approvals", "montage", "publication"],
        available_approval_stages=list(ApprovalStage),
    )

    editable = set(editable_fields_for_role(scenario, role))

    assert "research.full_analysis" in editable
    assert "content.script_text" in editable
    assert "montage.editor_comment" in editable
    assert "publication.description_instagram" in editable
    assert "approval.responsible_review.decision" in editable
    assert "approval.final_client.comment" in editable
    assert "external_id" not in editable
    assert "project.name" not in editable
    assert "scenarist.name" not in editable
    assert "approval.final_client.decided_at" not in editable


def test_client_sees_all_columns_but_edits_only_owned_approvals() -> None:
    scenario = SimpleNamespace(
        available_sections=["content", "approvals", "montage", "publication"],
        available_approval_stages=[
            ApprovalStage.PRE_GENERATION_CLIENT,
            ApprovalStage.FINAL_CLIENT,
        ],
    )

    editable = set(editable_fields_for_role(scenario, Role.CLIENT))

    assert editable == {
        "approval.pre_generation_client.decision",
        "approval.pre_generation_client.comment",
        "approval.pre_generation_client.note",
        "approval.final_client.decision",
        "approval.final_client.comment",
    }
    assert "content.script_text" not in editable
    assert "montage.editor_comment" not in editable
    assert "publication.description_instagram" not in editable
    assert "approval.responsible_review.comment" not in editable
