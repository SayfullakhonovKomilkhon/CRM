import uuid

from fastapi import HTTPException, status

from crm.models import Client, Project, Role, User


def require_active_client(client: Client | None) -> Client:
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    if not client.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Client is inactive",
        )
    return client


def require_active_project(project: Project | None) -> Project:
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not project.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Project is inactive",
        )
    require_active_client(project.client)
    return project


def require_assignable_scenarist(user: User | None) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenarist not found")
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Scenarist is inactive",
        )
    if user.role != Role.SCENARIST:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assigned user is not a scenarist",
        )
    return user


def require_assignable_editor(user: User | None) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Editor not found")
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Editor is inactive",
        )
    if user.role != Role.EDITOR:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assigned user is not an editor",
        )
    return user


def resolve_scenarist_assignment(
    actor: User,
    requested_id: uuid.UUID | None,
    requested_user: User | None,
) -> uuid.UUID | None:
    if actor.role == Role.SCENARIST:
        if requested_id is not None and requested_id != actor.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Scenarist cannot assign a scenario to another user",
            )
        return actor.id
    if requested_id is None:
        return None
    return require_assignable_scenarist(requested_user).id
