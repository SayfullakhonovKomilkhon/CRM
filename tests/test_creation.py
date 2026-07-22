import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from crm.creation import (
    require_active_client,
    require_active_project,
    require_assignable_editor,
    require_assignable_publisher,
    resolve_scenarist_assignment,
)
from crm.models import Role


def actor(role: Role, *, active: bool = True):
    return SimpleNamespace(
        id=uuid.uuid4(),
        role=role,
        is_active=active,
    )


def test_missing_and_inactive_client_have_stable_errors() -> None:
    with pytest.raises(HTTPException) as missing:
        require_active_client(None)
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as inactive:
        require_active_client(SimpleNamespace(is_active=False))
    assert inactive.value.status_code == 409


def test_project_and_its_client_must_both_be_active() -> None:
    with pytest.raises(HTTPException) as missing:
        require_active_project(None)
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as inactive_project:
        require_active_project(
            SimpleNamespace(is_active=False, client=SimpleNamespace(is_active=True))
        )
    assert inactive_project.value.status_code == 409

    with pytest.raises(HTTPException) as inactive_client:
        require_active_project(
            SimpleNamespace(is_active=True, client=SimpleNamespace(is_active=False))
        )
    assert inactive_client.value.status_code == 409


def test_scenarist_is_always_assigned_to_own_scenario() -> None:
    scenarist = actor(Role.SCENARIST)

    assert resolve_scenarist_assignment(scenarist, None, None) == scenarist.id
    assert (
        resolve_scenarist_assignment(scenarist, scenarist.id, scenarist) == scenarist.id
    )

    with pytest.raises(HTTPException) as another_user:
        resolve_scenarist_assignment(scenarist, uuid.uuid4(), None)
    assert another_user.value.status_code == 403


def test_manager_can_assign_only_an_active_scenarist() -> None:
    manager = actor(Role.MANAGER)
    scenarist = actor(Role.SCENARIST)

    assert resolve_scenarist_assignment(manager, None, None) is None
    assert (
        resolve_scenarist_assignment(manager, scenarist.id, scenarist) == scenarist.id
    )

    with pytest.raises(HTTPException) as missing:
        resolve_scenarist_assignment(manager, uuid.uuid4(), None)
    assert missing.value.status_code == 404

    inactive = actor(Role.SCENARIST, active=False)
    with pytest.raises(HTTPException) as inactive_error:
        resolve_scenarist_assignment(manager, inactive.id, inactive)
    assert inactive_error.value.status_code == 409

    editor = actor(Role.EDITOR)
    with pytest.raises(HTTPException) as wrong_role:
        resolve_scenarist_assignment(manager, editor.id, editor)
    assert wrong_role.value.status_code == 409


def test_montage_assignment_requires_an_active_editor() -> None:
    editor = actor(Role.EDITOR)
    assert require_assignable_editor(editor) is editor

    with pytest.raises(HTTPException) as missing:
        require_assignable_editor(None)
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as inactive:
        require_assignable_editor(actor(Role.EDITOR, active=False))
    assert inactive.value.status_code == 409

    with pytest.raises(HTTPException) as wrong_role:
        require_assignable_editor(actor(Role.SCENARIST))
    assert wrong_role.value.status_code == 409


def test_publication_assignment_requires_an_active_publisher() -> None:
    publisher = actor(Role.PUBLISHER)
    assert require_assignable_publisher(publisher) is publisher

    with pytest.raises(HTTPException) as missing:
        require_assignable_publisher(None)
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as inactive:
        require_assignable_publisher(actor(Role.PUBLISHER, active=False))
    assert inactive.value.status_code == 409

    with pytest.raises(HTTPException) as wrong_role:
        require_assignable_publisher(actor(Role.EDITOR))
    assert wrong_role.value.status_code == 409
