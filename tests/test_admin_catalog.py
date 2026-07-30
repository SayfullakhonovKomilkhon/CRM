import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from crm.models import Role
from crm.routers.catalog import (
    ensure_active_admin_remains,
    ensure_user_creation_allowed,
    require_catalog_reader,
    resolve_user_client_id,
)
from crm.schemas import ClientUpdate, ProjectUpdate, UserAdminCreate, UserAdminUpdate


class FakeSession:
    def __init__(self, value=None):
        self.value = value

    async def get(self, *_):
        return self.value


def user(role: Role, *, active: bool = True):
    return SimpleNamespace(
        id=uuid.uuid4(),
        role=role,
        is_active=active,
    )


def test_admin_user_create_normalizes_email_and_validates_password() -> None:
    payload = UserAdminCreate(
        email="  MANAGER@Example.COM ",
        full_name="Новый менеджер",
        role=Role.MANAGER,
        password="password8",
    )

    assert str(payload.email) == "MANAGER@example.com"

    with pytest.raises(ValidationError):
        UserAdminCreate(
            email="invalid",
            full_name="Пользователь",
            role=Role.EDITOR,
            password="password8",
        )
    with pytest.raises(ValidationError):
        UserAdminCreate(
            email="editor@example.com",
            full_name="Пользователь",
            role=Role.EDITOR,
            password="short",
        )


@pytest.mark.parametrize("field", ["full_name", "role", "is_active", "password"])
def test_admin_user_patch_rejects_explicit_null_for_required_values(field: str) -> None:
    with pytest.raises(ValidationError):
        UserAdminUpdate.model_validate({field: None})


def test_catalog_patch_schemas_reject_null_required_values() -> None:
    with pytest.raises(ValidationError):
        ClientUpdate(name=None)
    with pytest.raises(ValidationError):
        ClientUpdate(is_active=None)
    with pytest.raises(ValidationError):
        ProjectUpdate(name=None)
    with pytest.raises(ValidationError):
        ProjectUpdate(is_active=None)

    assert ClientUpdate(external_id=None).external_id is None
    assert ProjectUpdate(external_name=None).external_name is None


@pytest.mark.asyncio
async def test_client_role_requires_an_active_client() -> None:
    client_id = uuid.uuid4()
    active_client = SimpleNamespace(id=client_id, is_active=True)

    assert (
        await resolve_user_client_id(FakeSession(active_client), Role.CLIENT, client_id)
        == client_id
    )

    with pytest.raises(HTTPException) as missing_id:
        await resolve_user_client_id(FakeSession(), Role.CLIENT, None)
    assert missing_id.value.status_code == 422

    with pytest.raises(HTTPException) as missing_client:
        await resolve_user_client_id(FakeSession(), Role.CLIENT, client_id)
    assert missing_client.value.status_code == 404

    with pytest.raises(HTTPException) as inactive_client:
        await resolve_user_client_id(
            FakeSession(SimpleNamespace(id=client_id, is_active=False)),
            Role.CLIENT,
            client_id,
        )
    assert inactive_client.value.status_code == 409


@pytest.mark.asyncio
async def test_internal_role_cannot_have_client_id() -> None:
    with pytest.raises(HTTPException) as error:
        await resolve_user_client_id(FakeSession(), Role.EDITOR, uuid.uuid4())
    assert error.value.status_code == 422

    assert await resolve_user_client_id(FakeSession(), Role.EDITOR, None) is None


def test_last_active_admin_cannot_be_deactivated_or_demoted() -> None:
    administrator = user(Role.ADMIN)

    for role, active in (
        (Role.ADMIN, False),
        (Role.SCENARIST, True),
    ):
        with pytest.raises(HTTPException) as error:
            ensure_active_admin_remains(
                administrator, role, active, active_admin_count=1
            )
        assert error.value.status_code == 409

    ensure_active_admin_remains(
        administrator,
        Role.ADMIN,
        False,
        active_admin_count=2,
    )
    ensure_active_admin_remains(
        user(Role.EDITOR),
        Role.EDITOR,
        False,
        active_admin_count=1,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        Role.ADMIN,
        Role.MANAGER,
        Role.EDITOR_MANAGER,
        Role.PUBLISHER_MANAGER,
        Role.SCENARIST,
        Role.CLIENT,
    ],
)
async def test_catalog_reader_allows_manager_scenarist_and_client(role: Role) -> None:
    actor = user(role)
    assert await require_catalog_reader(actor) is actor


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.EDITOR, Role.PUBLISHER])
async def test_catalog_reader_rejects_editor_and_publisher(role: Role) -> None:
    with pytest.raises(HTTPException) as error:
        await require_catalog_reader(user(role))
    assert error.value.status_code == 403


def test_admin_payload_accepts_both_specialized_manager_roles() -> None:
    for role in (Role.EDITOR_MANAGER, Role.PUBLISHER_MANAGER):
        payload = UserAdminCreate(
            email=f"{role.value}@example.com",
            full_name=role.value,
            role=role,
            password="password8",
        )
        assert payload.role == role


def test_admin_can_create_any_team_role() -> None:
    for role in Role:
        ensure_user_creation_allowed(Role.ADMIN, role, admin_exists=True)


def test_scenario_manager_can_create_scenarist() -> None:
    ensure_user_creation_allowed(
        Role.MANAGER,
        Role.SCENARIST,
        admin_exists=True,
    )


@pytest.mark.parametrize(
    "role",
    [
        Role.MANAGER,
        Role.EDITOR_MANAGER,
        Role.PUBLISHER_MANAGER,
        Role.EDITOR,
        Role.CLIENT,
        Role.PUBLISHER,
        Role.ADMIN,
    ],
)
def test_scenario_manager_cannot_create_other_roles_when_admin_exists(
    role: Role,
) -> None:
    with pytest.raises(HTTPException) as error:
        ensure_user_creation_allowed(
            Role.MANAGER,
            role,
            admin_exists=True,
        )
    assert error.value.status_code == 403


def test_first_admin_bootstrap_remains_available_to_manager() -> None:
    ensure_user_creation_allowed(
        Role.MANAGER,
        Role.ADMIN,
        admin_exists=False,
    )
