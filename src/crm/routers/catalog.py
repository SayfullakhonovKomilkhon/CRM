import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from crm.creation import require_active_client
from crm.database import get_session
from crm.dependencies import get_current_user, require_roles
from crm.models import Client, Project, Role, User
from crm.schemas import (
    ClientCreate,
    ClientRead,
    ClientUpdate,
    ClientWithAccountCreate,
    ClientWithAccountRead,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
    UserAdminCreate,
    UserAdminRead,
    UserAdminUpdate,
    UserOptionRead,
)
from crm.security import hash_password

router = APIRouter(tags=["catalog"])
require_catalog_reader = require_roles(
    Role.ADMIN,
    Role.MANAGER,
    Role.EDITOR_MANAGER,
    Role.PUBLISHER_MANAGER,
    Role.SCENARIST,
    Role.CLIENT,
)


@router.get("/clients", response_model=list[ClientRead])
async def list_clients(
    active_only: bool = True,
    user: User = Depends(require_catalog_reader),
    session: AsyncSession = Depends(get_session),
) -> list[Client]:
    query = select(Client).order_by(Client.name)
    if active_only:
        query = query.where(Client.is_active.is_(True))
    if user.role == Role.CLIENT:
        query = query.where(Client.id == user.client_id)
    return list((await session.scalars(query)).all())


@router.post("/clients", response_model=ClientRead, status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: ClientCreate,
    _: User = Depends(require_roles(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> Client:
    duplicate_conditions = [func.lower(Client.name) == payload.name.lower()]
    if payload.external_id:
        duplicate_conditions.append(Client.external_id == payload.external_id)
    duplicate = await session.scalar(select(Client.id).where(or_(*duplicate_conditions)))
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Client with this name or external_id already exists",
        )
    client = Client(**payload.model_dump())
    session.add(client)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Client already exists",
        ) from error
    await session.refresh(client)
    return client


@router.post(
    "/clients/with-account",
    response_model=ClientWithAccountRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_client_with_account(
    payload: ClientWithAccountCreate,
    _: User = Depends(require_roles(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> ClientWithAccountRead:
    duplicate_conditions = [func.lower(Client.name) == payload.name.lower()]
    if payload.external_id:
        duplicate_conditions.append(Client.external_id == payload.external_id)
    duplicate_client = await session.scalar(
        select(Client.id).where(or_(*duplicate_conditions))
    )
    if duplicate_client is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Client with this name or external_id already exists",
        )

    email = str(payload.account_email).lower()
    duplicate_user = await session.scalar(
        select(User.id).where(func.lower(User.email) == email)
    )
    if duplicate_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email already exists",
        )

    client = Client(name=payload.name, external_id=payload.external_id)
    session.add(client)
    try:
        await session.flush()
        account = User(
            email=email,
            full_name=payload.account_full_name,
            role=Role.CLIENT,
            client_id=client.id,
            password_hash=hash_password(payload.account_password),
        )
        session.add(account)
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Client name, external_id, or account email already exists",
        ) from error

    await session.refresh(client)
    await session.refresh(account)
    return ClientWithAccountRead(client=client, user=account)


@router.patch("/clients/{client_id}", response_model=ClientRead)
async def update_client(
    client_id: uuid.UUID,
    payload: ClientUpdate,
    _: User = Depends(require_roles(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> Client:
    client = await session.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    changes = payload.model_dump(exclude_unset=True)
    duplicate_conditions = []
    if "name" in changes:
        duplicate_conditions.append(func.lower(Client.name) == changes["name"].lower())
    if changes.get("external_id"):
        duplicate_conditions.append(Client.external_id == changes["external_id"])
    if duplicate_conditions:
        duplicate = await session.scalar(
            select(Client.id).where(
                Client.id != client.id,
                or_(*duplicate_conditions),
            )
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Client with this name or external_id already exists",
            )

    for key, value in changes.items():
        setattr(client, key, value)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Client with this name or external_id already exists",
        ) from error
    await session.refresh(client)
    return client


@router.get("/projects", response_model=list[ProjectRead])
async def list_projects(
    client_id: uuid.UUID | None = None,
    active_only: bool = True,
    user: User = Depends(require_catalog_reader),
    session: AsyncSession = Depends(get_session),
) -> list[Project]:
    query = select(Project).order_by(Project.name)
    if active_only:
        query = query.where(
            Project.is_active.is_(True),
            Project.client.has(Client.is_active.is_(True)),
        )
    if user.role == Role.CLIENT:
        query = query.where(Project.client_id == user.client_id)
    if client_id:
        query = query.where(Project.client_id == client_id)
    return list((await session.scalars(query)).all())


@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    _: User = Depends(require_roles(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> Project:
    client = require_active_client(await session.get(Client, payload.client_id))
    duplicate = await session.scalar(
        select(Project.id).where(
            Project.client_id == client.id,
            func.lower(Project.name) == payload.name.lower(),
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Project with this name already exists for the client",
        )
    project = Project(**payload.model_dump())
    session.add(project)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Project already exists for the client",
        ) from error
    await session.refresh(project)
    return project


@router.patch("/projects/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    _: User = Depends(require_roles(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        duplicate = await session.scalar(
            select(Project.id).where(
                Project.id != project.id,
                Project.client_id == project.client_id,
                func.lower(Project.name) == changes["name"].lower(),
            )
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Project with this name already exists for the client",
            )

    for key, value in changes.items():
        setattr(project, key, value)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Project with this name already exists for the client",
        ) from error
    await session.refresh(project)
    return project


async def resolve_user_client_id(
    session: AsyncSession,
    role: Role,
    client_id: uuid.UUID | None,
) -> uuid.UUID | None:
    if role == Role.CLIENT:
        if client_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="client_id is required for the client role",
            )
        return require_active_client(await session.get(Client, client_id)).id
    if client_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="client_id must be null for internal roles",
        )
    return None


def ensure_active_admin_remains(
    target: User,
    next_role: Role,
    next_is_active: bool,
    active_admin_count: int,
) -> None:
    removes_active_admin = (
        target.role == Role.ADMIN
        and target.is_active
        and (next_role != Role.ADMIN or not next_is_active)
    )
    if removes_active_admin and active_admin_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The last active administrator cannot be deactivated or demoted",
        )


def ensure_user_creation_allowed(
    actor_role: Role,
    requested_role: Role,
    *,
    admin_exists: bool,
) -> None:
    if actor_role == Role.ADMIN:
        return
    managed_role = {
        Role.MANAGER: Role.SCENARIST,
        Role.EDITOR_MANAGER: Role.EDITOR,
        Role.PUBLISHER_MANAGER: Role.PUBLISHER,
    }.get(actor_role)
    if managed_role == requested_role:
        return
    if (
        actor_role == Role.MANAGER
        and requested_role == Role.ADMIN
        and not admin_exists
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"A {actor_role.value} can create {managed_role.value} users only"
            if managed_role is not None
            else "Only an administrator can create users"
        ),
    )


@router.get("/users", response_model=list[UserAdminRead])
async def list_users(
    role: Role | None = None,
    active_only: bool = True,
    actor: User = Depends(
        require_roles(
            Role.ADMIN,
            Role.MANAGER,
            Role.EDITOR_MANAGER,
            Role.PUBLISHER_MANAGER,
        )
    ),
    session: AsyncSession = Depends(get_session),
) -> list[User]:
    managed_role = {
        Role.MANAGER: Role.SCENARIST,
        Role.EDITOR_MANAGER: Role.EDITOR,
        Role.PUBLISHER_MANAGER: Role.PUBLISHER,
    }.get(actor.role)
    if managed_role is not None:
        if role not in (None, managed_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"A {actor.role.value} can list {managed_role.value} users only",
            )
        role = managed_role
    query = select(User).order_by(User.full_name, User.email)
    if role is not None:
        query = query.where(User.role == role)
    if active_only:
        query = query.where(User.is_active.is_(True))
    return list((await session.scalars(query)).all())


@router.post("/users", response_model=UserAdminRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserAdminCreate,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> User:
    existing_admin = None
    if actor.role != Role.ADMIN:
        existing_admin = await session.scalar(
            select(User.id).where(User.role == Role.ADMIN).limit(1)
        )
    ensure_user_creation_allowed(
        actor.role,
        payload.role,
        admin_exists=existing_admin is not None,
    )
    email = str(payload.email).lower()
    duplicate = await session.scalar(
        select(User.id).where(func.lower(User.email) == email)
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email already exists",
        )
    client_id = await resolve_user_client_id(session, payload.role, payload.client_id)
    user = User(
        email=email,
        full_name=payload.full_name,
        role=payload.role,
        client_id=client_id,
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email already exists",
        ) from error
    await session.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserAdminRead)
async def update_user(
    user_id: uuid.UUID,
    payload: UserAdminUpdate,
    _: User = Depends(require_roles(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> User:
    active_admins = list(
        (
            await session.scalars(
                select(User)
                .where(User.role == Role.ADMIN, User.is_active.is_(True))
                .order_by(User.id)
                .with_for_update()
            )
        ).all()
    )
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    next_role = payload.role if "role" in payload.model_fields_set else target.role
    next_is_active = (
        payload.is_active
        if "is_active" in payload.model_fields_set
        else target.is_active
    )
    ensure_active_admin_remains(
        target,
        next_role,
        next_is_active,
        len(active_admins),
    )

    client_id_was_set = "client_id" in payload.model_fields_set
    if next_role != Role.CLIENT and "role" in payload.model_fields_set:
        if client_id_was_set and payload.client_id is not None:
            await resolve_user_client_id(session, next_role, payload.client_id)
        next_client_id = None
    else:
        next_client_id = payload.client_id if client_id_was_set else target.client_id
        next_client_id = await resolve_user_client_id(
            session,
            next_role,
            next_client_id,
        )

    changes = payload.model_dump(
        exclude_unset=True,
        exclude={"client_id", "password"},
    )
    for key, value in changes.items():
        setattr(target, key, value)
    target.client_id = next_client_id
    if "password" in payload.model_fields_set:
        target.password_hash = hash_password(payload.password)

    await session.commit()
    await session.refresh(target)
    return target


@router.get("/users/scenarists", response_model=list[UserOptionRead])
async def list_scenarists(
    _: User = Depends(require_roles(Role.ADMIN, Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> list[User]:
    query = (
        select(User)
        .where(User.role == Role.SCENARIST, User.is_active.is_(True))
        .order_by(User.full_name)
    )
    return list((await session.scalars(query)).all())


@router.get("/users/editors", response_model=list[UserOptionRead])
async def list_editors(
    _: User = Depends(require_roles(Role.ADMIN, Role.MANAGER, Role.EDITOR_MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> list[User]:
    query = (
        select(User)
        .where(User.role == Role.EDITOR, User.is_active.is_(True))
        .order_by(User.full_name)
    )
    return list((await session.scalars(query)).all())


@router.get("/users/publishers", response_model=list[UserOptionRead])
async def list_publishers(
    _: User = Depends(require_roles(Role.ADMIN, Role.MANAGER, Role.PUBLISHER_MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> list[User]:
    query = (
        select(User)
        .where(User.role == Role.PUBLISHER, User.is_active.is_(True))
        .order_by(User.full_name)
    )
    return list((await session.scalars(query)).all())


@router.get("/users/editor-managers", response_model=list[UserOptionRead])
async def list_editor_managers(
    _: User = Depends(require_roles(Role.ADMIN, Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> list[User]:
    query = (
        select(User)
        .where(User.role == Role.EDITOR_MANAGER, User.is_active.is_(True))
        .order_by(User.full_name)
    )
    return list((await session.scalars(query)).all())


@router.get("/users/publisher-managers", response_model=list[UserOptionRead])
async def list_publisher_managers(
    _: User = Depends(require_roles(Role.ADMIN, Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> list[User]:
    query = (
        select(User)
        .where(User.role == Role.PUBLISHER_MANAGER, User.is_active.is_(True))
        .order_by(User.full_name)
    )
    return list((await session.scalars(query)).all())
