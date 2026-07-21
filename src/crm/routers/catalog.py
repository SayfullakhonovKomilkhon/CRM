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
    ProjectCreate,
    ProjectRead,
    UserOptionRead,
)

router = APIRouter(tags=["catalog"])


@router.get("/clients", response_model=list[ClientRead])
async def list_clients(
    active_only: bool = True,
    user: User = Depends(get_current_user),
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
    _: User = Depends(require_roles(Role.MANAGER)),
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


@router.get("/projects", response_model=list[ProjectRead])
async def list_projects(
    client_id: uuid.UUID | None = None,
    active_only: bool = True,
    user: User = Depends(get_current_user),
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
    _: User = Depends(require_roles(Role.MANAGER)),
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


@router.get("/users/scenarists", response_model=list[UserOptionRead])
async def list_scenarists(
    _: User = Depends(require_roles(Role.MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> list[User]:
    query = (
        select(User)
        .where(User.role == Role.SCENARIST, User.is_active.is_(True))
        .order_by(User.full_name)
    )
    return list((await session.scalars(query)).all())
