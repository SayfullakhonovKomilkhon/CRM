import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from crm.models import ApprovalDecision, ApprovalStage, Role
from crm.routers.scenarios import coerce_sheet_value, scenario_for_role
from crm.schemas import (
    ApprovalUpdate,
    EditorMontageUpdate,
    EditorStatus,
    MontageUpdate,
    ResearchPayload,
)
from crm.sheet import columns_for_role, editable_fields_for_role, values_for_role


def test_all_roles_receive_the_same_sheet_columns() -> None:
    columns_by_role = {
        role: [column.field for column in columns_for_role(role)] for role in Role
    }

    assert all(columns == columns_by_role[Role.MANAGER] for columns in columns_by_role.values())
    assert "montage.price" in columns_by_role[Role.CLIENT]
    assert "research.ai_analysis" in columns_by_role[Role.CLIENT]
    assert len(columns_by_role[Role.MANAGER]) == 78


def test_sheet_registry_contains_every_persisted_workflow_field() -> None:
    columns = {column.field for column in columns_for_role(Role.MANAGER)}
    expected = {
        "content.claude_context",
        "content.ai_review",
        "montage.scenarist_material_comment",
        "montage.brief_compliance_status",
        "montage.bot_visual_analysis",
        "montage.compliance_analysis",
        "montage.ai_analysis",
        "montage.scenarist_revision_status",
        "montage.scenarist_revision_comment",
        "publication.ai_social_descriptions",
        "publication.leia_script",
        "assigned_scenarist_id",
        "montage.assigned_editor_name",
    }

    assert expected <= columns


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


def test_all_internal_roles_can_reassign_scenarist_from_sheet() -> None:
    scenario = SimpleNamespace(
        available_sections=["content", "approvals"],
        available_approval_stages=[],
    )

    assert "assigned_scenarist_id" in editable_fields_for_role(scenario, Role.MANAGER)
    assert "assigned_scenarist_id" in editable_fields_for_role(scenario, Role.SCENARIST)
    assert "assigned_scenarist_id" in editable_fields_for_role(scenario, Role.EDITOR)
    assert "assigned_scenarist_id" not in editable_fields_for_role(scenario, Role.CLIENT)


def test_client_projection_hides_assignment_ids_but_keeps_names() -> None:
    scenario = SimpleNamespace(
        approvals=[],
        assigned_scenarist_id="scenarist-uuid",
        montage=SimpleNamespace(
            assigned_editor_id="editor-uuid",
            assigned_editor_name="Монтажёр",
        ),
    )

    values = values_for_role(scenario, Role.CLIENT)

    assert values["assigned_scenarist_id"] is None
    assert values["montage.assigned_editor_id"] is None
    assert values["montage.assigned_editor_name"] == "Монтажёр"


def test_client_detail_projection_hides_assignment_ids_but_keeps_names() -> None:
    scenario_id = uuid.uuid4()
    project_id = uuid.uuid4()
    scenarist_id = uuid.uuid4()
    editor_id = uuid.uuid4()
    now = datetime.now(UTC)
    scenario = SimpleNamespace(
        id=scenario_id,
        project_id=project_id,
        assigned_scenarist_id=scenarist_id,
        external_id="7",
        source_tab=None,
        source_row=None,
        scenario_date=None,
        deadline=None,
        score=None,
        scenario_type=None,
        visual_format=None,
        speaker=None,
        status="draft",
        research=None,
        content=None,
        approvals=[],
        title="Test",
        project=SimpleNamespace(id=project_id, name="Project", client_name="Client"),
        scenarist=SimpleNamespace(id=scenarist_id, name="Сценарист", initials="С"),
        comments_count=0,
        comments=[],
        montage=SimpleNamespace(
            scenario_id=scenario_id,
            source_material_url=None,
            client_brand_style=None,
            extra_brief=None,
            assigned_editor_id=editor_id,
            assigned_editor_name="Монтажёр",
            external_editor_name=None,
            price=None,
            payment_due_date=None,
            material_status=None,
            scenarist_material_comment=None,
            ready_material_url=None,
            editor_status=None,
            editor_comment=None,
            brief_compliance_status=None,
            ready_at=None,
            bot_visual_analysis=None,
            compliance_analysis=None,
            ai_analysis=None,
            scenarist_revision_status=None,
            scenarist_revision_comment=None,
            updated_at=now,
        ),
        publication=None,
        created_at=now,
        updated_at=now,
    )
    user = SimpleNamespace(role=Role.CLIENT)

    result = scenario_for_role(scenario, user)

    assert result.assigned_scenarist_id is None
    assert result.scenarist.id is None
    assert result.scenarist.name == "Сценарист"
    assert result.montage.assigned_editor_id is None
    assert result.montage.assigned_editor_name == "Монтажёр"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("research.competitor_url", "javascript:alert(1)"),
        ("montage.price", "10000000000.00"),
        ("montage.price", "1.001"),
        ("score", 101),
    ],
)
def test_sheet_coercion_rejects_invalid_values(field: str, value) -> None:
    with pytest.raises(HTTPException) as error:
        coerce_sheet_value(field, value)
    assert error.value.status_code == 422


def test_direct_payloads_reject_invalid_urls_prices_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ResearchPayload(competitor_url="ftp://example.com/file")
    with pytest.raises(ValidationError):
        MontageUpdate(price="1.001")
    with pytest.raises(ValidationError):
        ResearchPayload(unknown_field="value")
