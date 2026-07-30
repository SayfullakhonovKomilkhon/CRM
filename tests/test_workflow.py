from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from crm.models import (
    ApprovalDecision,
    ApprovalStage,
    Role,
    ScenarioStatus,
    SourceMaterialStatus,
)
from crm.workflow import (
    EDITOR_VISIBLE_STATUSES,
    publication_section_available,
    require_stage_prerequisites,
    require_stage_role,
    reset_approvals_from,
    reset_downstream_approvals,
    stage_prerequisites_met,
    status_after_decision,
    status_after_unpublishing,
)


def scenario(
    *,
    approvals=(),
    script_text="Script",
    source_url=None,
    ready_url=None,
    material_status=None,
    editor_id=None,
    publication=None,
):
    return SimpleNamespace(
        status=ScenarioStatus.IN_REVIEW,
        content=SimpleNamespace(script_text=script_text) if script_text is not None else None,
        approvals=list(approvals),
        montage=SimpleNamespace(
            source_material_url=source_url,
            ready_material_url=ready_url,
            assigned_editor_id=editor_id,
            material_status=material_status,
        ),
        publication=publication,
    )


def approval(stage: ApprovalStage, decision: ApprovalDecision = ApprovalDecision.APPROVED):
    return SimpleNamespace(stage=stage, decision=decision)


def test_workflow_decisions_are_role_safe() -> None:
    require_stage_role(Role.MANAGER, ApprovalStage.RESPONSIBLE_REVIEW)
    require_stage_role(Role.EDITOR_MANAGER, ApprovalStage.SOURCE_MATERIAL)
    require_stage_role(Role.EDITOR_MANAGER, ApprovalStage.MONTAGE_COMPLIANCE)
    require_stage_role(Role.CLIENT, ApprovalStage.PRE_GENERATION_CLIENT)
    require_stage_role(Role.CLIENT, ApprovalStage.FINAL_CLIENT)

    for role, stage in (
        (Role.CLIENT, ApprovalStage.SOURCE_MATERIAL),
        (Role.MANAGER, ApprovalStage.SOURCE_MATERIAL),
        (Role.MANAGER, ApprovalStage.FINAL_CLIENT),
        (Role.EDITOR_MANAGER, ApprovalStage.RESPONSIBLE_REVIEW),
        (Role.PUBLISHER_MANAGER, ApprovalStage.MONTAGE_COMPLIANCE),
        (Role.SCENARIST, ApprovalStage.PRE_GENERATION_CLIENT),
        (Role.EDITOR, ApprovalStage.MONTAGE_COMPLIANCE),
        (Role.PUBLISHER, ApprovalStage.FINAL_CLIENT),
    ):
        with pytest.raises(HTTPException) as error:
            require_stage_role(role, stage)
        assert error.value.status_code == 403


def test_workflow_rejects_skipping_required_approvals() -> None:
    value = scenario(source_url="https://example.com/source")

    with pytest.raises(HTTPException) as error:
        require_stage_prerequisites(value, ApprovalStage.SOURCE_MATERIAL)
    assert error.value.status_code == 409


def test_source_review_requires_explicit_row_submission() -> None:
    value = scenario(
        approvals=[approval(ApprovalStage.PRE_GENERATION_CLIENT)],
        source_url="https://example.com/source",
    )
    assert stage_prerequisites_met(value, ApprovalStage.SOURCE_MATERIAL) is False

    value.montage.material_status = SourceMaterialStatus.READY_FOR_REVIEW
    assert stage_prerequisites_met(value, ApprovalStage.SOURCE_MATERIAL) is True


def test_late_client_stage_is_locked_when_pre_generation_is_missing() -> None:
    value = scenario(
        approvals=[
            approval(ApprovalStage.RESPONSIBLE_REVIEW),
            approval(ApprovalStage.SOURCE_MATERIAL),
            approval(ApprovalStage.MONTAGE_COMPLIANCE),
        ],
        source_url="https://example.com/source",
        ready_url="https://example.com/ready",
    )

    assert stage_prerequisites_met(value, ApprovalStage.PRE_GENERATION_CLIENT) is True
    assert stage_prerequisites_met(value, ApprovalStage.FINAL_CLIENT) is False
    with pytest.raises(HTTPException) as error:
        require_stage_prerequisites(value, ApprovalStage.FINAL_CLIENT)
    assert error.value.status_code == 409


def test_final_client_stage_requires_the_complete_approval_chain() -> None:
    value = scenario(
        approvals=[
            approval(ApprovalStage.RESPONSIBLE_REVIEW),
            approval(ApprovalStage.PRE_GENERATION_CLIENT),
            approval(ApprovalStage.SOURCE_MATERIAL),
            approval(ApprovalStage.MONTAGE_COMPLIANCE),
        ],
        source_url="https://example.com/source",
        ready_url="https://example.com/ready",
    )

    assert stage_prerequisites_met(value, ApprovalStage.FINAL_CLIENT) is True


def test_active_workflow_does_not_require_legacy_responsible_review() -> None:
    value = scenario(
        approvals=[
            approval(ApprovalStage.PRE_GENERATION_CLIENT),
            approval(ApprovalStage.SOURCE_MATERIAL),
            approval(ApprovalStage.MONTAGE_COMPLIANCE),
        ],
        source_url="https://example.com/source",
        ready_url="https://example.com/ready",
    )

    assert stage_prerequisites_met(value, ApprovalStage.SOURCE_MATERIAL) is True
    assert stage_prerequisites_met(value, ApprovalStage.MONTAGE_COMPLIANCE) is True
    assert stage_prerequisites_met(value, ApprovalStage.FINAL_CLIENT) is True


def test_client_review_requires_responsible_manager_approval() -> None:
    value = scenario(approvals=[])

    assert stage_prerequisites_met(value, ApprovalStage.PRE_GENERATION_CLIENT) is False
    value.approvals.append(approval(ApprovalStage.RESPONSIBLE_REVIEW))
    assert stage_prerequisites_met(value, ApprovalStage.PRE_GENERATION_CLIENT) is True


def test_editor_queue_excludes_generation_and_includes_active_editing() -> None:
    assert ScenarioStatus.SENT_TO_GENERATION not in EDITOR_VISIBLE_STATUSES
    assert ScenarioStatus.HANDED_TO_EDITOR in EDITOR_VISIBLE_STATUSES
    assert ScenarioStatus.EDITING in EDITOR_VISIBLE_STATUSES
    assert ScenarioStatus.APPROVED in EDITOR_VISIBLE_STATUSES


def test_approved_source_is_handed_to_assigned_editor() -> None:
    value = scenario(editor_id="editor-id")

    next_status = status_after_decision(
        value,
        ApprovalStage.SOURCE_MATERIAL,
        ApprovalDecision.APPROVED,
    )

    assert next_status == ScenarioStatus.HANDED_TO_EDITOR


def test_montage_revision_returns_work_to_editor() -> None:
    value = scenario()

    next_status = status_after_decision(
        value,
        ApprovalStage.MONTAGE_COMPLIANCE,
        ApprovalDecision.REVISION,
    )

    assert next_status == ScenarioStatus.EDITING


def test_montage_rejection_blocks_client_and_returns_work_to_editor() -> None:
    value = scenario()

    next_status = status_after_decision(
        value,
        ApprovalStage.MONTAGE_COMPLIANCE,
        ApprovalDecision.REJECTED,
    )

    assert next_status == ScenarioStatus.EDITING


def test_scenario_manager_rejection_is_terminal() -> None:
    value = scenario()

    next_status = status_after_decision(
        value,
        ApprovalStage.RESPONSIBLE_REVIEW,
        ApprovalDecision.REJECTED,
    )

    assert next_status == ScenarioStatus.REJECTED


def test_previously_published_section_stays_available_after_unpublishing() -> None:
    value = scenario(
        approvals=[approval(ApprovalStage.RESPONSIBLE_REVIEW, ApprovalDecision.REVISION)],
        publication=SimpleNamespace(is_published=False, first_published_at="timestamp"),
    )

    assert publication_section_available(value) is True


def test_unused_publication_does_not_bypass_final_approval_chain() -> None:
    value = scenario(
        publication=SimpleNamespace(is_published=False, first_published_at=None),
    )

    assert publication_section_available(value) is False


def test_unpublishing_restores_status_from_latest_workflow_decision() -> None:
    value = scenario(
        approvals=[approval(ApprovalStage.RESPONSIBLE_REVIEW, ApprovalDecision.REVISION)],
    )
    value.status = ScenarioStatus.PUBLISHED

    assert status_after_unpublishing(value) == ScenarioStatus.REVISION


def test_revision_resets_downstream_approvals_and_unpublishes() -> None:
    responsible = approval(ApprovalStage.RESPONSIBLE_REVIEW, ApprovalDecision.REVISION)
    pre_generation = approval(ApprovalStage.PRE_GENERATION_CLIENT)
    pre_generation.decided_by_id = "user"
    pre_generation.decided_at = "timestamp"
    value = scenario(
        approvals=[responsible, pre_generation],
        publication=SimpleNamespace(is_published=True, first_published_at="timestamp"),
    )

    reset_downstream_approvals(value, ApprovalStage.RESPONSIBLE_REVIEW)

    assert pre_generation.decision == ApprovalDecision.PENDING
    assert pre_generation.decided_by_id is None
    assert pre_generation.decided_at is None
    assert value.publication.is_published is False
    assert value.publication.first_published_at == "timestamp"


def test_script_change_resets_current_and_later_approvals() -> None:
    approvals = [approval(stage) for stage in ApprovalStage]
    for item in approvals:
        item.decided_by_id = "user"
        item.decided_at = "timestamp"
    value = scenario(approvals=approvals)

    reset_approvals_from(value, ApprovalStage.RESPONSIBLE_REVIEW)

    assert all(item.decision == ApprovalDecision.PENDING for item in approvals)
    assert all(item.decided_at is None for item in approvals)
