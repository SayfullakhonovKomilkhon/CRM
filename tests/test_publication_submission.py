import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from crm.models import (
    ApprovalDecision,
    ApprovalStage,
    PublicationPreparationStatus,
    PublicationReviewDecision,
    PublisherStatus,
    ScenarioStatus,
)
from crm.routers import scenarios as routes


class FakeSession:
    def __init__(self, scenario):
        self.scenario = scenario
        self.commits = 0

    async def commit(self):
        self.commits += 1

    async def scalar(self, _query):
        return self.scenario


def publication_scenario(
    user_id,
    *,
    assigned=True,
    final_approved=True,
    preparation_status=PublicationPreparationStatus.DRAFT,
    description="Готовое описание",
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        assigned_scenarist_id=user_id if assigned else uuid.uuid4(),
        status=ScenarioStatus.APPROVED,
        approvals=[
            SimpleNamespace(
                stage=ApprovalStage.FINAL_CLIENT,
                decision=(
                    ApprovalDecision.APPROVED
                    if final_approved
                    else ApprovalDecision.PENDING
                ),
            )
        ],
        publication=SimpleNamespace(
            description_dzen=description,
            description_youtube=None,
            description_tiktok=None,
            description_instagram=None,
            preparation_status=preparation_status,
            manager_review_decision=PublicationReviewDecision.REVISION,
            manager_review_comment="Переделать",
            manager_reviewed_by_id=uuid.uuid4(),
            manager_reviewed_at="timestamp",
            assigned_publisher_id=None,
            publisher_status=PublisherStatus.PENDING,
        ),
        updated_at=None,
    )


def test_submit_publication_moves_exact_row_to_publisher_manager(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())
    scenario = publication_scenario(
        user.id,
        preparation_status=PublicationPreparationStatus.REVISION,
    )
    session = FakeSession(scenario)

    async def visible(_session, _scenario_id, _user, *, for_update=False):
        assert for_update is True
        return scenario

    monkeypatch.setattr(routes, "get_visible_scenario", visible)
    monkeypatch.setattr(routes, "scenario_for_role", lambda value, _user: value)

    result = asyncio.run(
        routes.submit_publication_for_review(
            scenario.id,
            user=user,
            session=session,
        )
    )

    assert result is scenario
    assert (
        scenario.publication.preparation_status
        == PublicationPreparationStatus.READY_FOR_REVIEW
    )
    assert scenario.publication.manager_review_decision == PublicationReviewDecision.PENDING
    assert scenario.publication.manager_review_comment is None
    assert session.commits == 1


@pytest.mark.parametrize(
    ("assigned", "final_approved", "preparation_status", "description", "expected"),
    [
        (False, True, PublicationPreparationStatus.DRAFT, "Текст", 403),
        (True, False, PublicationPreparationStatus.DRAFT, "Текст", 409),
        (True, True, PublicationPreparationStatus.READY_FOR_REVIEW, "Текст", 409),
        (True, True, PublicationPreparationStatus.APPROVED, "Текст", 409),
        (True, True, PublicationPreparationStatus.DRAFT, None, 422),
    ],
)
def test_submit_publication_rejects_invalid_transition(
    monkeypatch,
    assigned,
    final_approved,
    preparation_status,
    description,
    expected,
):
    user = SimpleNamespace(id=uuid.uuid4())
    scenario = publication_scenario(
        user.id,
        assigned=assigned,
        final_approved=final_approved,
        preparation_status=preparation_status,
        description=description,
    )
    session = FakeSession(scenario)

    async def visible(_session, _scenario_id, _user, *, for_update=False):
        return scenario

    monkeypatch.setattr(routes, "get_visible_scenario", visible)

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            routes.submit_publication_for_review(
                scenario.id,
                user=user,
                session=session,
            )
        )
    assert error.value.status_code == expected
    assert session.commits == 0
