import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from crm.models import (
    ApprovalDecision,
    ApprovalStage,
    ScenarioStatus,
    SourceMaterialStatus,
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


def approval(stage, decision):
    return SimpleNamespace(
        stage=stage,
        decision=decision,
        decided_by_id=None,
        decided_at=None,
    )


def source_scenario(user_id, *, pregen=True, material_status=None):
    approvals = (
        [approval(ApprovalStage.PRE_GENERATION_CLIENT, ApprovalDecision.APPROVED)]
        if pregen
        else []
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        assigned_scenarist_id=user_id,
        status=ScenarioStatus.SENT_TO_GENERATION,
        approvals=approvals,
        montage=SimpleNamespace(material_status=material_status),
        publication=None,
        updated_at=None,
    )


def test_submit_source_material_moves_exact_row_to_review(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())
    scenario = source_scenario(user.id, material_status=SourceMaterialStatus.DRAFT)
    session = FakeSession(scenario)
    writebacks = []

    async def visible(_session, _scenario_id, _user, *, for_update=False):
        assert for_update is True
        return scenario

    async def enqueue(_session, queued_scenario, changes):
        writebacks.append((queued_scenario.id, changes))

    monkeypatch.setattr(routes, "get_visible_scenario", visible)
    monkeypatch.setattr(routes, "enqueue_sheet_writeback", enqueue)
    monkeypatch.setattr(routes, "scenario_for_role", lambda value, _user: value)

    result = asyncio.run(
        routes.submit_source_material_for_review(
            scenario.id,
            user=user,
            session=session,
        )
    )

    assert result is scenario
    assert scenario.montage.material_status == SourceMaterialStatus.READY_FOR_REVIEW
    assert session.commits == 1
    assert writebacks == [
        (
            scenario.id,
            {
                "montage.material_status": "ready_for_review",
                "approval.source_material.decision": "pending",
            },
        )
    ]


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (ApprovalDecision.PENDING, SourceMaterialStatus.READY_FOR_REVIEW),
        (ApprovalDecision.APPROVED, SourceMaterialStatus.APPROVED),
        (ApprovalDecision.REVISION, SourceMaterialStatus.REVISION),
        (ApprovalDecision.REJECTED, SourceMaterialStatus.REJECTED),
    ],
)
def test_source_manager_decision_updates_material_status(decision, expected):
    assert routes.source_material_status_after_decision(decision) == expected


@pytest.mark.parametrize(
    ("assigned", "pregen", "material_status", "expected_status"),
    [
        (False, True, SourceMaterialStatus.DRAFT, 403),
        (True, False, SourceMaterialStatus.DRAFT, 409),
        (True, True, SourceMaterialStatus.READY_FOR_REVIEW, 409),
        (True, True, SourceMaterialStatus.REJECTED, 409),
    ],
)
def test_submit_source_material_rejects_invalid_transition(
    monkeypatch,
    assigned,
    pregen,
    material_status,
    expected_status,
):
    user = SimpleNamespace(id=uuid.uuid4())
    assigned_id = user.id if assigned else uuid.uuid4()
    scenario = source_scenario(
        assigned_id,
        pregen=pregen,
        material_status=material_status,
    )
    session = FakeSession(scenario)

    async def visible(_session, _scenario_id, _user, *, for_update=False):
        return scenario

    monkeypatch.setattr(routes, "get_visible_scenario", visible)

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            routes.submit_source_material_for_review(
                scenario.id,
                user=user,
                session=session,
            )
        )
    assert error.value.status_code == expected_status
    assert session.commits == 0
