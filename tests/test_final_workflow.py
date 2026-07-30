import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

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
    apply_final_revision_gate_decision,
    apply_publication_manager_review,
    apply_publisher_action,
    require_approval_comment,
)
from crm.schemas import (
    GateManagerDecision,
    PublicationManagerDecision,
    PublisherActionStatus,
)
from crm.sheet import editable_fields_for_role


def approval(stage, decision):
    return SimpleNamespace(
        stage=stage,
        decision=decision,
        decided_by_id=uuid.uuid4(),
        decided_at="timestamp",
    )


def manager():
    return SimpleNamespace(id=uuid.uuid4(), role=Role.MANAGER)


def editor_manager():
    return SimpleNamespace(id=uuid.uuid4(), role=Role.EDITOR_MANAGER)


def publisher_manager():
    return SimpleNamespace(id=uuid.uuid4(), role=Role.PUBLISHER_MANAGER)


def publisher(*, active=True):
    return SimpleNamespace(id=uuid.uuid4(), role=Role.PUBLISHER, is_active=active)


def publication(**updates):
    values = {
        "description_dzen": "Dzen description",
        "description_youtube": None,
        "description_tiktok": None,
        "description_instagram": None,
        "manager_review_decision": PublicationReviewDecision.PENDING,
        "manager_review_comment": None,
        "manager_reviewed_by_id": None,
        "manager_reviewed_at": None,
        "assigned_publisher_id": None,
        "publisher_status": PublisherStatus.PENDING,
        "publisher_comment": None,
        "dzen_url": None,
        "youtube_url": None,
        "tiktok_url": None,
        "instagram_url": None,
        "is_published": False,
        "first_published_at": None,
        "published_at": None,
        "publication_date": None,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def test_approved_final_revision_gate_routes_work_to_editor() -> None:
    montage_review = approval(ApprovalStage.MONTAGE_COMPLIANCE, ApprovalDecision.APPROVED)
    final_review = approval(ApprovalStage.FINAL_CLIENT, ApprovalDecision.REVISION)
    gate = SimpleNamespace(
        decision=GateDecision.PENDING,
        manager_comment=None,
        decided_by_id=None,
        decided_at=None,
    )
    scenario = SimpleNamespace(
        approvals=[montage_review, final_review],
        final_revision_gate=gate,
        publication=None,
        status=ScenarioStatus.MANAGER_REVISION_REVIEW,
    )

    apply_final_revision_gate_decision(
        scenario,
        GateManagerDecision.APPROVED,
        "Причина весомая",
        editor_manager(),
    )

    assert gate.decision == GateDecision.APPROVED
    assert montage_review.decision == ApprovalDecision.PENDING
    assert final_review.decision == ApprovalDecision.PENDING
    assert scenario.status == ScenarioStatus.EDITING


def test_rejected_final_revision_gate_returns_to_client_review() -> None:
    final_review = approval(ApprovalStage.FINAL_CLIENT, ApprovalDecision.REVISION)
    gate = SimpleNamespace(
        decision=GateDecision.PENDING,
        manager_comment=None,
        decided_by_id=None,
        decided_at=None,
    )
    scenario = SimpleNamespace(
        approvals=[final_review],
        final_revision_gate=gate,
        publication=None,
        status=ScenarioStatus.MANAGER_REVISION_REVIEW,
    )

    apply_final_revision_gate_decision(
        scenario,
        GateManagerDecision.REJECTED,
        "Доработка не требуется",
        editor_manager(),
    )

    assert gate.decision == GateDecision.REJECTED
    assert final_review.decision == ApprovalDecision.PENDING
    assert scenario.status == ScenarioStatus.CLIENT_REVIEW


@pytest.mark.asyncio
async def test_manager_approves_publication_and_assigns_active_publisher() -> None:
    assigned_publisher = publisher()
    final_review = approval(ApprovalStage.FINAL_CLIENT, ApprovalDecision.APPROVED)
    scenario = SimpleNamespace(
        approvals=[final_review],
        publication=publication(),
        status=ScenarioStatus.APPROVED,
    )
    session = SimpleNamespace(get=lambda *_: None)

    async def get_user(*_):
        return assigned_publisher

    session.get = get_user
    await apply_publication_manager_review(
        session,
        scenario,
        PublicationManagerDecision.APPROVED,
        "Можно публиковать",
        assigned_publisher.id,
        publisher_manager(),
    )

    assert scenario.publication.assigned_publisher_id == assigned_publisher.id
    assert scenario.publication.publisher_status == PublisherStatus.ASSIGNED
    assert scenario.status == ScenarioStatus.READY_TO_PUBLISH


@pytest.mark.asyncio
async def test_approved_publication_review_cannot_be_reassigned_without_reopening() -> None:
    scenario = SimpleNamespace(
        approvals=[approval(ApprovalStage.FINAL_CLIENT, ApprovalDecision.APPROVED)],
        publication=publication(
            manager_review_decision=PublicationReviewDecision.APPROVED
        ),
        status=ScenarioStatus.READY_TO_PUBLISH,
    )

    with pytest.raises(HTTPException) as error:
        await apply_publication_manager_review(
            SimpleNamespace(),
            scenario,
            PublicationManagerDecision.APPROVED,
            "Повторное назначение",
            uuid.uuid4(),
            publisher_manager(),
        )
    assert error.value.status_code == 409


def test_publisher_marks_material_published_with_automatic_dates() -> None:
    scenario = SimpleNamespace(
        publication=publication(
            manager_review_decision=PublicationReviewDecision.APPROVED,
            publisher_status=PublisherStatus.ASSIGNED,
        ),
        status=ScenarioStatus.READY_TO_PUBLISH,
    )

    apply_publisher_action(
        scenario,
        PublisherActionStatus.PUBLISHED,
        "Опубликовано",
        {"youtube_url": "https://youtube.com/watch?v=test"},
    )

    assert scenario.publication.publisher_status == PublisherStatus.PUBLISHED
    assert scenario.publication.is_published is True
    assert scenario.publication.publication_date is not None
    assert scenario.publication.published_at is not None
    assert scenario.status == ScenarioStatus.PUBLISHED


def test_management_roles_and_publisher_receive_only_their_workflow_actions() -> None:
    gate = SimpleNamespace(decision=GateDecision.PENDING)
    pending_publication = publication()
    approved_publication = publication(manager_review_decision=PublicationReviewDecision.APPROVED)
    editor_manager_view = SimpleNamespace(
        available_sections=["content", "approvals", "publication"],
        available_approval_stages=[],
        final_revision_gate=gate,
        publication=pending_publication,
    )
    publisher_manager_view = SimpleNamespace(
        available_sections=["publication"],
        available_approval_stages=[],
        final_revision_gate=None,
        publication=pending_publication,
    )
    publisher_view = SimpleNamespace(
        available_sections=["publication"],
        publication=approved_publication,
    )

    editor_manager_fields = set(
        editable_fields_for_role(editor_manager_view, Role.EDITOR_MANAGER)
    )
    publisher_manager_fields = set(
        editable_fields_for_role(
            publisher_manager_view,
            Role.PUBLISHER_MANAGER,
        )
    )
    publisher_fields = set(editable_fields_for_role(publisher_view, Role.PUBLISHER))

    assert "final_revision_gate.decision" in editor_manager_fields
    assert "publication.assigned_publisher_id" not in editor_manager_fields
    assert "publication.assigned_publisher_id" in publisher_manager_fields
    assert "publication.manager_review_decision" in publisher_manager_fields
    assert "publication.publisher_status" not in publisher_manager_fields
    assert publisher_fields == {
        "publication.publisher_status",
        "publication.publisher_comment",
        "publication.dzen_url",
        "publication.youtube_url",
        "publication.tiktok_url",
        "publication.instagram_url",
    }


def test_approved_publication_review_is_locked_for_publisher_manager() -> None:
    manager_view = SimpleNamespace(
        available_sections=["publication"],
        available_approval_stages=[],
        final_revision_gate=None,
        publication=publication(
            manager_review_decision=PublicationReviewDecision.APPROVED
        ),
    )

    manager_fields = set(
        editable_fields_for_role(manager_view, Role.PUBLISHER_MANAGER)
    )

    assert "publication.assigned_publisher_id" not in manager_fields
    assert "publication.manager_review_decision" not in manager_fields
    assert "publication.manager_review_comment" not in manager_fields


def test_gate_comment_is_required() -> None:
    final_review = approval(ApprovalStage.FINAL_CLIENT, ApprovalDecision.REVISION)
    scenario = SimpleNamespace(
        approvals=[final_review],
        final_revision_gate=SimpleNamespace(decision=GateDecision.PENDING),
        publication=None,
    )

    with pytest.raises(HTTPException) as error:
        apply_final_revision_gate_decision(
            scenario, GateManagerDecision.APPROVED, "", editor_manager()
        )
    assert error.value.status_code == 422


def test_wrong_manager_level_cannot_decide_gate() -> None:
    scenario = SimpleNamespace(approvals=[], final_revision_gate=None)
    with pytest.raises(HTTPException) as error:
        apply_final_revision_gate_decision(
            scenario,
            GateManagerDecision.APPROVED,
            "comment",
            manager(),
        )
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_wrong_manager_level_cannot_review_publication() -> None:
    with pytest.raises(HTTPException) as error:
        await apply_publication_manager_review(
            SimpleNamespace(),
            SimpleNamespace(),
            PublicationManagerDecision.APPROVED,
            "comment",
            uuid.uuid4(),
            manager(),
        )
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_publication_revision_comment_is_optional() -> None:
    scenario = SimpleNamespace(
        approvals=[
            approval(ApprovalStage.FINAL_CLIENT, ApprovalDecision.APPROVED),
        ],
        publication=publication(),
        status=ScenarioStatus.APPROVED,
    )

    result = await apply_publication_manager_review(
        SimpleNamespace(),
        scenario,
        PublicationManagerDecision.REVISION,
        None,
        None,
        publisher_manager(),
    )

    assert result.manager_review_decision == PublicationReviewDecision.REVISION
    assert result.manager_review_comment is None
    assert scenario.status == ScenarioStatus.APPROVED


@pytest.mark.parametrize(
    "stage",
    [
        ApprovalStage.RESPONSIBLE_REVIEW,
        ApprovalStage.PRE_GENERATION_CLIENT,
        ApprovalStage.SOURCE_MATERIAL,
        ApprovalStage.MONTAGE_COMPLIANCE,
        ApprovalStage.FINAL_CLIENT,
    ],
)
def test_revision_comment_is_optional_for_every_active_review_stage(stage) -> None:
    require_approval_comment(stage, ApprovalDecision.REVISION, "")


@pytest.mark.parametrize(
    "stage",
    [
        ApprovalStage.RESPONSIBLE_REVIEW,
        ApprovalStage.PRE_GENERATION_CLIENT,
        ApprovalStage.SOURCE_MATERIAL,
        ApprovalStage.MONTAGE_COMPLIANCE,
        ApprovalStage.FINAL_CLIENT,
    ],
)
def test_rejected_comment_remains_required_for_every_active_review_stage(
    stage,
) -> None:
    with pytest.raises(HTTPException) as error:
        require_approval_comment(stage, ApprovalDecision.REJECTED, "")
    assert error.value.status_code == 422
