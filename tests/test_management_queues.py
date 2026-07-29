from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from crm.models import Role
from crm.routers.scenarios import QUEUE_ROLES, queue_filter
from crm.schemas import ScenarioQueue


@pytest.mark.parametrize("queue,role", list(QUEUE_ROLES.items()))
def test_each_management_queue_accepts_only_its_owner(
    queue: ScenarioQueue,
    role: Role,
) -> None:
    clause = queue_filter(queue, SimpleNamespace(role=role))
    assert clause is not None

    wrong_role = (
        Role.EDITOR_MANAGER
        if role != Role.EDITOR_MANAGER
        else Role.PUBLISHER_MANAGER
    )
    with pytest.raises(HTTPException) as error:
        queue_filter(queue, SimpleNamespace(role=wrong_role))
    assert error.value.status_code == 403


def test_queue_enum_is_complete_and_stable_for_frontend() -> None:
    assert [item.value for item in ScenarioQueue] == [
        "scenario_manager_review",
        "editor_manager_intake",
        "editor_manager_inwork",
        "editor_manager_check",
        "editor_manager_rework_review",
        "publisher_manager_queue",
        "publisher_manager_inwork",
    ]
