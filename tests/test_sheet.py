import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from crm.models import (
    ApprovalDecision,
    ApprovalStage,
    GateDecision,
    PublicationReviewDecision,
    PublisherStatus,
    Role,
    ScenarioStatus,
)
from crm.routers.scenarios import (
    EDITOR_MANAGER_MONTAGE_FIELDS as API_EDITOR_MANAGER_MONTAGE_FIELDS,
)
from crm.routers.scenarios import (
    SCENARIST_MONTAGE_FIELDS as API_SCENARIST_MONTAGE_FIELDS,
)
from crm.routers.scenarios import coerce_sheet_value, scenario_for_role
from crm.schemas import (
    ApprovalUpdate,
    EditorMontageUpdate,
    EditorStatus,
    MontageUpdate,
    ResearchPayload,
)
from crm.sheet import (
    EDITOR_MANAGER_MONTAGE_FIELDS,
    SCENARIST_SOURCE_FIELDS,
    SHEET_FIELDS,
    columns_for_role,
    editable_fields_for_role,
    values_for_role,
)


def test_scenario_manager_receives_only_scenario_review_columns() -> None:
    columns_by_role = {
        role: [column.field for column in columns_for_role(role)]
        for role in (
            Role.MANAGER,
            Role.EDITOR_MANAGER,
            Role.PUBLISHER_MANAGER,
            Role.SCENARIST,
            Role.EDITOR,
            Role.PUBLISHER,
        )
    }

    manager_columns = set(columns_by_role[Role.MANAGER])
    assert "content.script_text" in manager_columns
    assert "research.full_analysis" in manager_columns
    assert "approval.responsible_review.decision" in manager_columns
    assert not any(field.startswith("montage.") for field in manager_columns)
    assert not any(field.startswith("publication.") for field in manager_columns)
    assert "approval.pre_generation_client.decision" not in manager_columns
    assert len(columns_by_role[Role.SCENARIST]) > len(columns_by_role[Role.MANAGER])


def test_client_receives_only_the_safe_workflow_projection_in_order() -> None:
    assert [column.field for column in columns_for_role(Role.CLIENT)] == [
        "scenario_date",
        "external_id",
        "speaker",
        "content.script_text",
        "approval.pre_generation_client.decision",
        "approval.pre_generation_client.comment",
        "approval.pre_generation_client.note",
        "montage.ready_material_url",
        "approval.final_client.decision",
        "approval.final_client.comment",
        "final_revision_gate.decision",
        "final_revision_gate.manager_comment",
        "publication.description_dzen",
        "publication.description_youtube",
        "publication.description_tiktok",
        "publication.description_instagram",
        "publication.publication_date",
        "publication.is_published",
        "publication.publisher_status",
        "publication.dzen_url",
        "publication.youtube_url",
        "publication.tiktok_url",
        "publication.instagram_url",
        "publication.published_at",
    ]


def test_sheet_registry_contains_every_persisted_workflow_field() -> None:
    columns = {column.field for column in SHEET_FIELDS}
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


def test_each_internal_role_receives_only_its_workflow_columns() -> None:
    scenarist = {column.field for column in columns_for_role(Role.SCENARIST)}
    editor_manager = {
        column.field for column in columns_for_role(Role.EDITOR_MANAGER)
    }
    editor = {column.field for column in columns_for_role(Role.EDITOR)}
    publisher_manager = {
        column.field for column in columns_for_role(Role.PUBLISHER_MANAGER)
    }
    publisher = {column.field for column in columns_for_role(Role.PUBLISHER)}

    assert "research.full_analysis" in scenarist
    assert "montage.source_material_url" in scenarist
    assert "publication.preparation_status" in scenarist
    assert "montage.assigned_editor_id" not in scenarist
    assert "montage.price" not in scenarist
    assert "montage.editor_status" not in scenarist
    assert "publication.assigned_publisher_id" not in scenarist
    assert "publication.publisher_status" not in scenarist

    assert "montage.assigned_editor_id" in editor_manager
    assert "approval.montage_compliance.decision" in editor_manager
    assert "research.full_analysis" not in editor_manager
    assert "publication.description_youtube" not in editor_manager

    assert "content.montage_brief" in editor
    assert "montage.editor_status" in editor
    assert "montage.payment_due_date" in editor
    assert "research.full_analysis" not in editor
    assert "montage.assigned_editor_id" not in editor
    assert "publication.description_youtube" not in editor

    assert "publication.assigned_publisher_id" in publisher_manager
    assert "publication.manager_review_decision" in publisher_manager
    assert "montage.ready_material_url" in publisher_manager
    assert "content.script_text" not in publisher_manager
    assert "montage.price" not in publisher_manager

    assert "publication.publisher_brief" in publisher
    assert "publication.publisher_status" in publisher
    assert "publication.youtube_url" in publisher
    assert "publication.assigned_publisher_id" not in publisher
    assert "publication.engagement_metrics" not in publisher
    assert "content.script_text" not in publisher


@pytest.mark.parametrize(
    "role",
    [
        Role.MANAGER,
        Role.SCENARIST,
        Role.EDITOR_MANAGER,
        Role.EDITOR,
        Role.PUBLISHER_MANAGER,
        Role.PUBLISHER,
    ],
)
def test_every_editable_field_is_present_in_the_roles_projection(role: Role) -> None:
    scenario = SimpleNamespace(
        status=ScenarioStatus.EDITING,
        available_sections=["content", "approvals", "montage", "publication"],
        available_approval_stages=list(ApprovalStage),
        final_revision_gate=SimpleNamespace(decision=GateDecision.PENDING),
        publication=SimpleNamespace(
            manager_review_decision=PublicationReviewDecision.PENDING
        ),
    )

    visible = {column.field for column in columns_for_role(role)}
    editable = set(editable_fields_for_role(scenario, role))

    assert editable <= visible


def test_source_material_status_is_server_controlled_for_scenarist() -> None:
    assert "montage.material_status" not in SCENARIST_SOURCE_FIELDS
    assert "material_status" not in API_SCENARIST_MONTAGE_FIELDS


def test_editor_can_edit_only_result_fields_during_active_stage() -> None:
    scenario = SimpleNamespace(
        status=ScenarioStatus.EDITING,
        available_sections=["content", "montage"],
        available_approval_stages=[
            ApprovalStage.SOURCE_MATERIAL,
            ApprovalStage.MONTAGE_COMPLIANCE,
        ],
    )

    editable = set(editable_fields_for_role(scenario, Role.EDITOR))

    assert editable == {
        "montage.ready_material_url",
        "montage.editor_status",
        "montage.editor_comment",
    }


def test_editor_result_is_locked_outside_an_active_editor_stage() -> None:
    scenario = SimpleNamespace(
        status=ScenarioStatus.CLIENT_REVIEW,
        available_sections=["content", "montage"],
        available_approval_stages=[],
    )

    editable = set(editable_fields_for_role(scenario, Role.EDITOR))

    assert editable == set()


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


def test_client_sheet_has_separate_note_and_no_internal_decision_dates() -> None:
    columns = {column.field for column in columns_for_role(Role.CLIENT)}
    scenario = SimpleNamespace(
        available_sections=["content", "approvals"],
        available_approval_stages=[ApprovalStage.PRE_GENERATION_CLIENT],
    )
    editable = set(editable_fields_for_role(scenario, Role.CLIENT))

    assert "approval.pre_generation_client.note" in columns
    assert "approval.pre_generation_client.decided_at" not in columns
    assert "approval.final_client.decided_at" not in columns
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


def test_scenarist_edits_scenario_source_and_publication_preparation() -> None:
    scenario = SimpleNamespace(
        available_sections=["content", "approvals", "publication"],
        available_approval_stages=list(ApprovalStage),
    )

    editable = set(editable_fields_for_role(scenario, Role.SCENARIST))

    assert "research.full_analysis" in editable
    assert "content.script_text" in editable
    assert "montage.source_material_url" not in editable
    assert "approval.final_client.decision" not in editable
    assert "publication.description_instagram" in editable
    assert "publication.is_published" not in editable
    assert "external_id" not in editable
    assert "project.name" not in editable
    assert "project.client_name" not in editable
    assert "scenarist.name" not in editable
    assert "approval.pre_generation_client.decided_at" not in editable


def test_scenarist_source_fields_open_with_montage_section() -> None:
    scenario = SimpleNamespace(
        available_sections=["content", "approvals", "montage", "publication"],
        available_approval_stages=list(ApprovalStage),
    )

    editable = set(editable_fields_for_role(scenario, Role.SCENARIST))

    assert "research.full_analysis" in editable
    assert "content.script_text" in editable
    assert "montage.source_material_url" in editable
    assert "montage.editor_comment" not in editable
    assert "publication.description_instagram" in editable
    assert "approval.responsible_review.decision" not in editable
    assert "approval.final_client.comment" not in editable
    assert "external_id" not in editable
    assert "project.name" not in editable
    assert "scenarist.name" not in editable
    assert "approval.final_client.decided_at" not in editable


def test_three_manager_levels_have_disjoint_sheet_actions() -> None:
    scenario = SimpleNamespace(
        available_sections=["content", "approvals", "montage", "publication"],
        available_approval_stages=list(ApprovalStage),
        final_revision_gate=SimpleNamespace(decision=GateDecision.PENDING),
        publication=SimpleNamespace(
            manager_review_decision=PublicationReviewDecision.PENDING
        ),
    )

    scenario_manager = set(editable_fields_for_role(scenario, Role.MANAGER))
    editor_manager = set(
        editable_fields_for_role(scenario, Role.EDITOR_MANAGER)
    )
    publisher_manager = set(
        editable_fields_for_role(scenario, Role.PUBLISHER_MANAGER)
    )

    assert {
        "assigned_scenarist_id",
        "approval.responsible_review.decision",
        "approval.responsible_review.comment",
    } <= scenario_manager
    assert "montage.assigned_editor_id" not in scenario_manager
    assert "publication.assigned_publisher_id" not in scenario_manager

    assert "montage.assigned_editor_id" in editor_manager
    assert "approval.source_material.decision" in editor_manager
    assert "approval.montage_compliance.decision" in editor_manager
    assert "final_revision_gate.decision" in editor_manager
    assert "assigned_scenarist_id" not in editor_manager
    assert "publication.assigned_publisher_id" not in editor_manager

    assert "publication.assigned_publisher_id" in publisher_manager
    assert "publication.manager_review_decision" in publisher_manager
    assert "montage.assigned_editor_id" not in publisher_manager
    assert "approval.responsible_review.decision" not in publisher_manager


def test_sheet_and_direct_montage_ownership_contracts_match() -> None:
    assert {
        f"montage.{field}" for field in API_SCENARIST_MONTAGE_FIELDS
    } == SCENARIST_SOURCE_FIELDS
    assert {
        f"montage.{field}" for field in API_EDITOR_MANAGER_MONTAGE_FIELDS
    } == EDITOR_MANAGER_MONTAGE_FIELDS


def test_client_edits_only_owned_approvals() -> None:
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


def test_only_manager_can_reassign_scenarist_from_sheet() -> None:
    scenario = SimpleNamespace(
        available_sections=["content", "approvals"],
        available_approval_stages=[],
    )

    assert "assigned_scenarist_id" in editable_fields_for_role(scenario, Role.MANAGER)
    assert "assigned_scenarist_id" not in editable_fields_for_role(scenario, Role.SCENARIST)
    assert "assigned_scenarist_id" not in editable_fields_for_role(
        scenario, Role.EDITOR_MANAGER
    )
    assert "assigned_scenarist_id" not in editable_fields_for_role(
        scenario, Role.PUBLISHER_MANAGER
    )
    assert "assigned_scenarist_id" not in editable_fields_for_role(scenario, Role.EDITOR)
    assert "assigned_scenarist_id" not in editable_fields_for_role(scenario, Role.CLIENT)


def test_client_sheet_values_contain_only_safe_projection() -> None:
    scenario = SimpleNamespace(
        approvals=[],
        assigned_scenarist_id="scenarist-uuid",
        montage=SimpleNamespace(
            assigned_editor_id="editor-uuid",
            assigned_editor_name="Монтажёр",
        ),
    )

    values = values_for_role(scenario, Role.CLIENT)

    assert list(values) == [column.field for column in columns_for_role(Role.CLIENT)]
    assert "assigned_scenarist_id" not in values
    assert "montage.assigned_editor_id" not in values
    assert "montage.assigned_editor_name" not in values
    assert "research.full_analysis" not in values
    assert "montage.price" not in values


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
        publication=SimpleNamespace(
            scenario_id=scenario_id,
            description_dzen=None,
            description_youtube=None,
            description_tiktok=None,
            description_instagram=None,
            publication_date=None,
            is_published=False,
            first_published_at=None,
            assigned_publisher_id=uuid.uuid4(),
            assigned_publisher_name="Публицист",
            manager_review_decision=PublicationReviewDecision.PENDING,
            manager_review_comment=None,
            manager_reviewed_by_id=uuid.uuid4(),
            manager_reviewed_at=None,
            publisher_status=PublisherStatus.PENDING,
            publisher_comment=None,
            dzen_url=None,
            youtube_url=None,
            tiktok_url=None,
            instagram_url=None,
            published_at=None,
            publisher_brief=None,
            engagement_metrics=None,
            publication_analysis=None,
            ai_social_descriptions=None,
            leia_script=None,
            updated_at=now,
        ),
        final_revision_gate=SimpleNamespace(
            scenario_id=scenario_id,
            decision=GateDecision.PENDING,
            request_comment="Доработать",
            manager_comment=None,
            decided_by_id=uuid.uuid4(),
            decided_at=None,
            created_at=now,
            updated_at=now,
        ),
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
    assert result.publication.assigned_publisher_id is None
    assert result.publication.assigned_publisher_name == "Публицист"
    assert result.publication.manager_reviewed_by_id is None
    assert result.final_revision_gate.decided_by_id is None


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
