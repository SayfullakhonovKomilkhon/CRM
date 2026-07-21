from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from crm.models import ApprovalDecision, ApprovalStage, Role, ScenarioStatus
from crm.workflow import (
    EDITOR_VISIBLE_STATUSES,
    publication_section_available,
    require_stage_prerequisites,
    require_stage_role,
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
        ),
        publication=publication,
    )


def approval(stage: ApprovalStage, decision: ApprovalDecision = ApprovalDecision.APPROVED):
    return SimpleNamespace(stage=stage, decision=decision)


def test_internal_roles_can_decide_all_visible_workflow_stages() -> None:
    require_stage_role(Role.MANAGER, ApprovalStage.SOURCE_MATERIAL)
    require_stage_role(Role.MANAGER, ApprovalStage.FINAL_CLIENT)
    require_stage_role(Role.SCENARIST, ApprovalStage.PRE_GENERATION_CLIENT)
    require_stage_role(Role.EDITOR, ApprovalStage.MONTAGE_COMPLIANCE)
    require_stage_role(Role.CLIENT, ApprovalStage.FINAL_CLIENT)
    require_stage_role(Role.EDITOR, ApprovalStage.PRE_GENERATION_CLIENT)

    with pytest.raises(HTTPException) as error:
        require_stage_role(Role.CLIENT, ApprovalStage.SOURCE_MATERIAL)
    assert error.value.status_code == 403


def test_workflow_rejects_skipping_required_approvals() -> None:
    value = scenario(source_url="https://example.com/source")

    with pytest.raises(HTTPException) as error:
        require_stage_prerequisites(value, ApprovalStage.SOURCE_MATERIAL)
    assert error.value.status_code == 409


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


def test_editor_queue_excludes_generation_and_includes_active_editing() -> None:
    assert ScenarioStatus.SENT_TO_GENERATION not in EDITOR_VISIBLE_STATUSES
    assert ScenarioStatus.HANDED_TO_EDITOR in EDITOR_VISIBLE_STATUSES
    assert ScenarioStatus.EDITING in EDITOR_VISIBLE_STATUSES


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
