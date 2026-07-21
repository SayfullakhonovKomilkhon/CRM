import math
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from crm.creation import (
    require_active_project,
    require_assignable_editor,
    require_assignable_scenarist,
    resolve_scenarist_assignment,
)
from crm.database import get_session
from crm.dependencies import get_current_user, require_roles
from crm.models import (
    ApprovalDecision,
    ApprovalStage,
    Client,
    MontageTask,
    Project,
    Publication,
    Role,
    Scenario,
    ScenarioApproval,
    ScenarioComment,
    ScenarioContent,
    ScenarioResearch,
    ScenarioStatus,
    User,
)
from crm.schemas import (
    MAX_COMMENT_LENGTH,
    MAX_TEXT_LENGTH,
    ApprovalRead,
    ApprovalUpdate,
    CommentCreate,
    CommentRead,
    EditorMontageUpdate,
    EditorStatus,
    MontageRead,
    MontageUpdate,
    PaginationMeta,
    ProjectSummary,
    PublicationRead,
    PublicationUpdate,
    ScenarioCreate,
    ScenarioListItem,
    ScenarioPage,
    ScenarioRead,
    ScenarioSortBy,
    ScenarioUpdate,
    ScenaristSummary,
    SheetRowPatch,
    SheetRowPatchResult,
    SheetScenarioPage,
    SheetScenarioRow,
    SortOrder,
    normalize_http_url,
)
from crm.sheet import columns_for_role, editable_fields_for_role, values_for_role
from crm.workflow import (
    EDITOR_VISIBLE_STATUSES,
    ROLE_APPROVAL_STAGES,
    is_approved,
    publication_section_available,
    require_stage_prerequisites,
    require_stage_role,
    reset_approvals_from,
    reset_downstream_approvals,
    stage_prerequisites_met,
    status_after_decision,
    status_after_unpublishing,
)

router = APIRouter(prefix="/scenarios", tags=["scenarios"])
LOAD_SCENARIO = (
    selectinload(Scenario.project).selectinload(Project.client),
    selectinload(Scenario.assigned_scenarist),
    selectinload(Scenario.research),
    selectinload(Scenario.content),
    selectinload(Scenario.approvals),
    selectinload(Scenario.comments),
    selectinload(Scenario.montage).selectinload(MontageTask.assigned_editor),
    selectinload(Scenario.publication),
)

CLIENT_APPROVAL_STAGES = {
    ApprovalStage.PRE_GENERATION_CLIENT,
    ApprovalStage.FINAL_CLIENT,
}

def apply_visibility(query, user: User):
    if user.role == Role.SCENARIST:
        return query.where(
            or_(Scenario.assigned_scenarist_id == user.id, Scenario.assigned_scenarist_id.is_(None))
        )
    if user.role == Role.EDITOR:
        return query.where(
            Scenario.montage.has(MontageTask.assigned_editor_id == user.id),
            Scenario.status.in_(EDITOR_VISIBLE_STATUSES),
        )
    if user.role == Role.CLIENT:
        return query.where(Scenario.project.has(Project.client_id == user.client_id))
    return query


async def get_visible_scenario(
    session: AsyncSession,
    scenario_id: uuid.UUID,
    user: User,
    *,
    for_update: bool = False,
) -> Scenario:
    query = select(Scenario).where(Scenario.id == scenario_id).options(*LOAD_SCENARIO)
    if for_update:
        query = query.with_for_update()
    scenario = await session.scalar(apply_visibility(query, user))
    if scenario is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found")
    return scenario


def scenario_for_role(scenario: Scenario, user: User) -> ScenarioRead:
    """Apply row workflow metadata; column permissions are shared by all roles."""
    result = ScenarioRead.model_validate(scenario)
    script_approved = is_approved(scenario, ApprovalStage.PRE_GENERATION_CLIENT)

    available_approval_stages = [
        stage
        for stage in ApprovalStage
        if stage in ROLE_APPROVAL_STAGES.get(user.role, set())
        and stage_prerequisites_met(scenario, stage)
    ]

    available_sections = ["content", "approvals"]
    if script_approved or scenario.montage is not None:
        available_sections.append("montage")
    if publication_section_available(scenario):
        available_sections.append("publication")
    if user.role in {Role.MANAGER, Role.SCENARIST, Role.EDITOR}:
        available_sections.append("history")

    updates = {
        "available_sections": available_sections,
        "available_approval_stages": available_approval_stages,
    }
    if user.role == Role.CLIENT:
        updates["assigned_scenarist_id"] = None
        if result.scenarist is not None:
            updates["scenarist"] = result.scenarist.model_copy(update={"id": None})
        if result.montage is not None:
            updates["montage"] = result.montage.model_copy(
                update={"assigned_editor_id": None}
            )

    result = result.model_copy(update=updates)

    return result


@router.get("", response_model=ScenarioPage)
async def list_scenarios(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    project_id: uuid.UUID | None = None,
    scenario_statuses: list[ScenarioStatus] | None = Query(None, alias="status"),
    assigned_scenarist_id: uuid.UUID | None = None,
    deadline_from: date | None = None,
    deadline_to: date | None = None,
    search: str | None = Query(None, max_length=200),
    sort_by: ScenarioSortBy = ScenarioSortBy.UPDATED_AT,
    sort_order: SortOrder = SortOrder.DESC,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ScenarioPage:
    title = func.coalesce(
        func.nullif(ScenarioContent.cover_text, ""),
        func.nullif(ScenarioContent.hook, ""),
        func.nullif(Scenario.external_id, ""),
        "Без названия",
    )
    comments_count = (
        select(func.count(ScenarioComment.id))
        .where(ScenarioComment.scenario_id == Scenario.id)
        .correlate(Scenario)
        .scalar_subquery()
    )
    filters = []
    if project_id:
        filters.append(Scenario.project_id == project_id)
    if scenario_statuses:
        filters.append(Scenario.status.in_(scenario_statuses))
    if assigned_scenarist_id:
        filters.append(Scenario.assigned_scenarist_id == assigned_scenarist_id)
    if deadline_from:
        filters.append(Scenario.deadline >= deadline_from)
    if deadline_to:
        filters.append(Scenario.deadline <= deadline_to)
    if search:
        pattern = f"%{search}%"
        filters.append(
            or_(
                title.ilike(pattern),
                Scenario.external_id.ilike(pattern),
                Scenario.speaker.ilike(pattern),
                Project.name.ilike(pattern),
                Client.name.ilike(pattern),
            )
        )

    count_query = (
        select(func.count(Scenario.id))
        .join(Project, Scenario.project_id == Project.id)
        .join(Client, Project.client_id == Client.id)
        .outerjoin(ScenarioContent, ScenarioContent.scenario_id == Scenario.id)
        .where(*filters)
    )
    total = await session.scalar(apply_visibility(count_query, user)) or 0

    query = select(
        Scenario.id,
        title.label("title"),
        Scenario.external_id,
        Scenario.speaker,
        Scenario.visual_format,
        Scenario.status,
        Scenario.deadline,
        Scenario.score,
        comments_count.label("comments_count"),
        Scenario.updated_at,
        Project.id.label("project_id"),
        Project.name.label("project_name"),
        Client.name.label("client_name"),
        User.id.label("scenarist_id"),
        User.full_name.label("scenarist_name"),
    )
    query = (
        query.join(Project, Scenario.project_id == Project.id)
        .join(Client, Project.client_id == Client.id)
        .outerjoin(User, Scenario.assigned_scenarist_id == User.id)
        .outerjoin(ScenarioContent, ScenarioContent.scenario_id == Scenario.id)
        .where(*filters)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    query = apply_visibility(query, user)
    sort_column = {
        ScenarioSortBy.UPDATED_AT: Scenario.updated_at,
        ScenarioSortBy.DEADLINE: Scenario.deadline,
        ScenarioSortBy.CREATED_AT: Scenario.created_at,
    }[sort_by]
    order_clause = sort_column.asc() if sort_order == SortOrder.ASC else sort_column.desc()
    rows = (await session.execute(query.order_by(order_clause.nulls_last()))).all()
    items = []
    for row in rows:
        scenarist = None
        if row.scenarist_id:
            initials = "".join(
                part[0].upper() for part in (row.scenarist_name or "").split()[:2] if part
            )
            scenarist = ScenaristSummary(
                id=None if user.role == Role.CLIENT else row.scenarist_id,
                name=row.scenarist_name,
                initials=initials,
            )
        items.append(
            ScenarioListItem(
                id=row.id,
                title=row.title,
                external_id=row.external_id,
                project=ProjectSummary(
                    id=row.project_id,
                    name=row.project_name,
                    client_name=row.client_name,
                ),
                scenarist=scenarist,
                speaker=row.speaker,
                visual_format=row.visual_format,
                status=row.status,
                deadline=row.deadline,
                score=row.score,
                comments_count=row.comments_count,
                updated_at=row.updated_at,
            )
        )
    return ScenarioPage(
        items=items,
        meta=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            pages=math.ceil(total / page_size) if total else 0,
        ),
    )


@router.get("/sheet", response_model=SheetScenarioPage)
async def list_scenario_sheet(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    project_id: uuid.UUID | None = None,
    scenario_statuses: list[ScenarioStatus] | None = Query(None, alias="status"),
    assigned_scenarist_id: uuid.UUID | None = None,
    deadline_from: date | None = None,
    deadline_to: date | None = None,
    search: str | None = Query(None, max_length=200),
    sort_by: ScenarioSortBy = ScenarioSortBy.UPDATED_AT,
    sort_order: SortOrder = SortOrder.DESC,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SheetScenarioPage:
    """Paginated spreadsheet projection loaded in a fixed number of queries."""
    title = func.coalesce(
        func.nullif(ScenarioContent.cover_text, ""),
        func.nullif(ScenarioContent.hook, ""),
        func.nullif(Scenario.external_id, ""),
        "Без названия",
    )
    filters = []
    if project_id:
        filters.append(Scenario.project_id == project_id)
    if scenario_statuses:
        filters.append(Scenario.status.in_(scenario_statuses))
    if assigned_scenarist_id:
        filters.append(Scenario.assigned_scenarist_id == assigned_scenarist_id)
    if deadline_from:
        filters.append(Scenario.deadline >= deadline_from)
    if deadline_to:
        filters.append(Scenario.deadline <= deadline_to)
    if search:
        pattern = f"%{search}%"
        filters.append(
            or_(
                title.ilike(pattern),
                Scenario.external_id.ilike(pattern),
                Scenario.speaker.ilike(pattern),
                Project.name.ilike(pattern),
                Client.name.ilike(pattern),
            )
        )

    base = (
        select(Scenario.id)
        .join(Project, Scenario.project_id == Project.id)
        .join(Client, Project.client_id == Client.id)
        .outerjoin(ScenarioContent, ScenarioContent.scenario_id == Scenario.id)
        .where(*filters)
    )
    base = apply_visibility(base, user)
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    sort_column = {
        ScenarioSortBy.UPDATED_AT: Scenario.updated_at,
        ScenarioSortBy.DEADLINE: Scenario.deadline,
        ScenarioSortBy.CREATED_AT: Scenario.created_at,
    }[sort_by]
    order_clause = sort_column.asc() if sort_order == SortOrder.ASC else sort_column.desc()
    page_query = (
        base.order_by(order_clause.nulls_last()).offset((page - 1) * page_size).limit(page_size)
    )
    scenario_ids = list((await session.scalars(page_query)).all())

    scenarios: list[Scenario] = []
    if scenario_ids:
        loaded = list(
            (
                await session.scalars(
                    select(Scenario).where(Scenario.id.in_(scenario_ids)).options(*LOAD_SCENARIO)
                )
            )
            .unique()
            .all()
        )
        by_id = {item.id: item for item in loaded}
        scenarios = [by_id[item_id] for item_id in scenario_ids if item_id in by_id]

    rows = []
    for scenario in scenarios:
        role_view = scenario_for_role(scenario, user)
        rows.append(
            SheetScenarioRow(
                id=scenario.id,
                version=scenario.updated_at,
                values=values_for_role(role_view, user.role),
                editable_fields=editable_fields_for_role(role_view, user.role),
            )
        )
    return SheetScenarioPage(
        columns=columns_for_role(user.role),
        items=rows,
        meta=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            pages=math.ceil(total / page_size) if total else 0,
        ),
    )


@router.post("", response_model=ScenarioRead, status_code=status.HTTP_201_CREATED)
async def create_scenario(
    payload: ScenarioCreate,
    user: User = Depends(require_roles(Role.MANAGER, Role.SCENARIST)),
    session: AsyncSession = Depends(get_session),
) -> ScenarioRead:
    project = await session.scalar(
        select(Project)
        .where(Project.id == payload.project_id)
        .options(selectinload(Project.client))
    )
    require_active_project(project)

    requested_scenarist = None
    if user.role == Role.MANAGER and payload.assigned_scenarist_id is not None:
        requested_scenarist = await session.get(User, payload.assigned_scenarist_id)
    assigned_scenarist_id = resolve_scenarist_assignment(
        user,
        payload.assigned_scenarist_id,
        requested_scenarist,
    )

    if payload.external_id:
        duplicate = await session.scalar(
            select(Scenario.id).where(
                Scenario.project_id == payload.project_id,
                Scenario.external_id == payload.external_id,
            )
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Scenario external_id already exists in this project",
            )

    data = payload.model_dump(exclude={"research", "content"})
    data["assigned_scenarist_id"] = assigned_scenarist_id
    scenario = Scenario(**data)
    if payload.research:
        scenario.research = ScenarioResearch(**payload.research.model_dump())
    if payload.content:
        scenario.content = ScenarioContent(**payload.content.model_dump())
    session.add(scenario)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Scenario conflicts with an existing source row",
        ) from error
    created = await get_visible_scenario(session, scenario.id, user)
    return scenario_for_role(created, user)


SHEET_DATE_FIELDS = {
    "scenario_date",
    "deadline",
    "montage.payment_due_date",
    "montage.ready_at",
    "publication.publication_date",
}
SHEET_UUID_FIELDS = {"assigned_scenarist_id", "montage.assigned_editor_id"}
SHEET_DECIMAL_FIELDS = {"montage.price"}
SHEET_INTEGER_FIELDS = {"score"}
SHEET_BOOLEAN_FIELDS = {"publication.is_published"}
SHEET_URL_FIELDS = {
    "research.competitor_url",
    "montage.source_material_url",
    "montage.ready_material_url",
    "publication.instagram_url",
}
SHEET_STRING_LIMITS = {
    "scenario_type": 100,
    "visual_format": 255,
    "speaker": 255,
    "research.competitor_category": 255,
    "montage.external_editor_name": 255,
    "montage.material_status": 100,
    "montage.brief_compliance_status": 100,
    "montage.scenarist_revision_status": 100,
}


def coerce_sheet_value(field: str, value):
    if field == "montage.editor_status":
        if value is None:
            return None
        try:
            return EditorStatus(value).value
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid montage.editor_status",
            ) from error
    if value == "" and field in (
        SHEET_DATE_FIELDS | SHEET_UUID_FIELDS | SHEET_DECIMAL_FIELDS | SHEET_INTEGER_FIELDS
    ):
        return None
    try:
        if field in SHEET_DATE_FIELDS:
            return (
                value if isinstance(value, date) else date.fromisoformat(value) if value else None
            )
        if field in SHEET_UUID_FIELDS:
            return value if isinstance(value, uuid.UUID) else uuid.UUID(value) if value else None
        if field in SHEET_DECIMAL_FIELDS:
            if value is None:
                return None
            parsed_decimal = Decimal(str(value))
            if not parsed_decimal.is_finite() or parsed_decimal < 0:
                raise ValueError
            _, digits, exponent = parsed_decimal.as_tuple()
            decimal_places = max(-exponent, 0)
            integer_digits = max(len(digits) + exponent, 0)
            if decimal_places > 2 or integer_digits > 10:
                raise ValueError
            return parsed_decimal
        if field in SHEET_INTEGER_FIELDS:
            parsed = int(value) if value is not None else None
            if parsed is not None and not 0 <= parsed <= 100:
                raise ValueError
            return parsed
        if field in SHEET_BOOLEAN_FIELDS:
            if not isinstance(value, bool):
                raise ValueError
            return value
    except (InvalidOperation, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid value for {field}",
        ) from error
    if value is not None and not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid value for {field}",
        )
    if field in SHEET_URL_FIELDS:
        try:
            return normalize_http_url(value)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid value for {field}",
            ) from error
    if isinstance(value, str):
        limit = SHEET_STRING_LIMITS.get(field, MAX_TEXT_LENGTH)
        if len(value) > limit:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Value for {field} exceeds {limit} characters",
            )
    return value


@router.patch("/{scenario_id}/sheet", response_model=SheetRowPatchResult)
async def patch_scenario_sheet_row(
    scenario_id: uuid.UUID,
    payload: SheetRowPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SheetRowPatchResult:
    """Atomically update role-owned inline cells with optimistic locking."""
    scenario = await get_visible_scenario(session, scenario_id, user, for_update=True)
    if scenario.updated_at != payload.expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Scenario was changed by another user; reload the row",
        )

    fields = [change.field for change in payload.changes]
    if len(fields) != len(set(fields)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A field can only be changed once per request",
        )
    role_view = scenario_for_role(scenario, user)
    allowed = set(editable_fields_for_role(role_view, user.role))
    denied = sorted(set(fields) - allowed)
    if denied:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"message": "Fields are not editable at this stage", "fields": denied},
        )

    approval_changes: dict[ApprovalStage, dict[str, object]] = {}
    regular_changes = []
    for change in payload.changes:
        if change.field.startswith("approval."):
            _, stage_value, attribute = change.field.split(".")
            stage = ApprovalStage(stage_value)
            approval_changes.setdefault(stage, {})[attribute] = change.value
        else:
            regular_changes.append(change)

    script_changed = False
    source_material_changed = False
    ready_material_changed = False
    for change in regular_changes:
        value = coerce_sheet_value(change.field, change.value)
        if change.field == "assigned_scenarist_id":
            if value is not None:
                value = require_assignable_scenarist(await session.get(User, value)).id
            scenario.assigned_scenarist_id = value
            continue
        if "." not in change.field:
            setattr(scenario, change.field, value)
            continue
        target_name, attribute = change.field.split(".", 1)
        if target_name == "research":
            if scenario.research is None:
                scenario.research = ScenarioResearch()
            target = scenario.research
        elif target_name == "content":
            if scenario.content is None:
                scenario.content = ScenarioContent()
            target = scenario.content
        elif target_name == "montage":
            if scenario.montage is None:
                scenario.montage = MontageTask()
            target = scenario.montage
        elif target_name == "publication":
            if scenario.publication is None:
                scenario.publication = Publication()
            target = scenario.publication
        else:  # pragma: no cover - guarded by editable field registry
            raise HTTPException(status_code=422, detail=f"Unsupported field {change.field}")
        old_value = getattr(target, attribute)
        if change.field == "montage.assigned_editor_id" and value is not None:
            value = require_assignable_editor(await session.get(User, value)).id
        script_changed |= change.field == "content.script_text" and old_value != value
        source_material_changed |= (
            change.field == "montage.source_material_url" and old_value != value
        )
        ready_material_changed |= (
            change.field == "montage.ready_material_url" and old_value != value
        )
        setattr(target, attribute, value)

    if script_changed:
        reset_approvals_from(scenario, ApprovalStage.RESPONSIBLE_REVIEW)
        scenario.status = (
            ScenarioStatus.IN_REVIEW
            if scenario.content and scenario.content.script_text
            else ScenarioStatus.DRAFT
        )
    if source_material_changed:
        reset_approvals_from(scenario, ApprovalStage.SOURCE_MATERIAL)
        scenario.status = (
            ScenarioStatus.SENT_TO_GENERATION
            if is_approved(scenario, ApprovalStage.PRE_GENERATION_CLIENT)
            else status_after_unpublishing(scenario)
        )
    if ready_material_changed:
        reset_approvals_from(scenario, ApprovalStage.MONTAGE_COMPLIANCE)
        scenario.status = (
            ScenarioStatus.EDITING
            if is_approved(scenario, ApprovalStage.SOURCE_MATERIAL)
            else status_after_unpublishing(scenario)
        )

    stage_order = [
        ApprovalStage.RESPONSIBLE_REVIEW,
        ApprovalStage.PRE_GENERATION_CLIENT,
        ApprovalStage.SOURCE_MATERIAL,
        ApprovalStage.MONTAGE_COMPLIANCE,
        ApprovalStage.FINAL_CLIENT,
    ]
    for stage in stage_order:
        changes = approval_changes.get(stage)
        if changes is None:
            continue
        require_stage_role(user.role, stage)
        approval = next((item for item in scenario.approvals if item.stage == stage), None)
        if approval is None:
            approval = ScenarioApproval(stage=stage)
            scenario.approvals.append(approval)
        if "comment" in changes:
            comment = changes["comment"]
            if comment is not None and (
                not isinstance(comment, str) or len(comment) > MAX_COMMENT_LENGTH
            ):
                raise HTTPException(status_code=422, detail=f"Invalid comment for {stage.value}")
            approval.comment = comment
        if "note" in changes:
            note = changes["note"]
            if note is not None and (
                not isinstance(note, str) or len(note) > MAX_COMMENT_LENGTH
            ):
                raise HTTPException(status_code=422, detail=f"Invalid note for {stage.value}")
            approval.note = note
        if "decision" in changes:
            try:
                decision = ApprovalDecision(changes["decision"])
            except (TypeError, ValueError) as error:
                raise HTTPException(
                    status_code=422, detail=f"Invalid decision for {stage.value}"
                ) from error
            require_stage_prerequisites(scenario, stage)
            approval.decision = decision
            approval.decided_by_id = None if decision == ApprovalDecision.PENDING else user.id
            approval.decided_at = (
                None if decision == ApprovalDecision.PENDING else datetime.now(UTC)
            )
            if decision != ApprovalDecision.APPROVED:
                reset_downstream_approvals(scenario, stage)
            scenario.status = status_after_decision(scenario, stage, decision)

    approval_decision_changed = any(
        field.startswith("approval.") and field.endswith(".decision") for field in fields
    )
    if (
        user.role == Role.SCENARIST
        and scenario.status == ScenarioStatus.REVISION
        and not approval_decision_changed
    ):
        scenario.status = ScenarioStatus.IN_REVIEW
    editor_result_changed = bool(
        {
            "montage.ready_material_url",
            "montage.editor_status",
            "montage.editor_comment",
        }
        & set(fields)
    )
    if user.role == Role.EDITOR and editor_result_changed and not approval_decision_changed:
        scenario.status = ScenarioStatus.EDITING
    assignment_changed = "montage.assigned_editor_id" in fields
    montage_decision_changed = "approval.montage_compliance.decision" in fields
    if (
        assignment_changed
        and not montage_decision_changed
        and scenario.montage
        and scenario.montage.assigned_editor_id
        and is_approved(scenario, ApprovalStage.SOURCE_MATERIAL)
    ):
        scenario.status = ScenarioStatus.HANDED_TO_EDITOR
    if scenario.publication:
        if scenario.publication.is_published:
            scenario.publication.first_published_at = (
                scenario.publication.first_published_at or datetime.now(UTC)
            )
            scenario.status = ScenarioStatus.PUBLISHED
        elif scenario.status == ScenarioStatus.PUBLISHED:
            scenario.status = status_after_unpublishing(scenario)

    scenario.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(scenario)
    return SheetRowPatchResult(
        id=scenario.id,
        version=scenario.updated_at,
        changed_fields=fields,
    )


@router.get("/{scenario_id}", response_model=ScenarioRead)
async def get_scenario(
    scenario_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ScenarioRead:
    scenario = await get_visible_scenario(session, scenario_id, user)
    return scenario_for_role(scenario, user)


@router.patch("/{scenario_id}", response_model=ScenarioRead)
async def update_scenario(
    scenario_id: uuid.UUID,
    payload: ScenarioUpdate,
    user: User = Depends(require_roles(Role.MANAGER, Role.SCENARIST, Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> ScenarioRead:
    scenario = await get_visible_scenario(session, scenario_id, user, for_update=True)

    changes = payload.model_dump(exclude_unset=True, exclude={"research", "content"})
    if "assigned_scenarist_id" in changes:
        requested_scenarist_id = changes.pop("assigned_scenarist_id")
        if requested_scenarist_id is None:
            scenario.assigned_scenarist_id = None
        else:
            scenario.assigned_scenarist_id = require_assignable_scenarist(
                await session.get(User, requested_scenarist_id)
            ).id
    for key, value in changes.items():
        setattr(scenario, key, value)

    script_changed = False
    if payload.research is not None:
        if scenario.research is None:
            scenario.research = ScenarioResearch()
        for key, value in payload.research.model_dump(exclude_unset=True).items():
            setattr(scenario.research, key, value)
    if payload.content is not None:
        if scenario.content is None:
            scenario.content = ScenarioContent()
        if "script_text" in payload.content.model_fields_set:
            script_changed = scenario.content.script_text != payload.content.script_text
        for key, value in payload.content.model_dump(exclude_unset=True).items():
            setattr(scenario.content, key, value)

    if script_changed:
        reset_approvals_from(scenario, ApprovalStage.RESPONSIBLE_REVIEW)
        scenario.status = (
            ScenarioStatus.IN_REVIEW
            if scenario.content and scenario.content.script_text
            else ScenarioStatus.DRAFT
        )

    if user.role == Role.SCENARIST and scenario.status == ScenarioStatus.REVISION:
        scenario.status = ScenarioStatus.IN_REVIEW

    scenario.updated_at = datetime.now(UTC)
    await session.commit()
    updated = await session.scalar(
        select(Scenario).where(Scenario.id == scenario.id).options(*LOAD_SCENARIO)
    )
    if updated is None:  # pragma: no cover - the locked row cannot disappear here
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found")
    return scenario_for_role(updated, user)


@router.put("/{scenario_id}/approvals/{stage}", response_model=ApprovalRead)
async def set_approval(
    scenario_id: uuid.UUID,
    stage: ApprovalStage,
    payload: ApprovalUpdate,
    user: User = Depends(require_roles(Role.MANAGER, Role.SCENARIST, Role.EDITOR, Role.CLIENT)),
    session: AsyncSession = Depends(get_session),
) -> ScenarioApproval:
    scenario = await get_visible_scenario(session, scenario_id, user, for_update=True)
    require_stage_role(user.role, stage)
    require_stage_prerequisites(scenario, stage)
    if (
        "note" in payload.model_fields_set
        and stage != ApprovalStage.PRE_GENERATION_CLIENT
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN
                if user.role == Role.CLIENT
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail="Note is only available for scenario approval",
        )
    approval = await session.scalar(
        select(ScenarioApproval).where(
            ScenarioApproval.scenario_id == scenario_id, ScenarioApproval.stage == stage
        )
    )
    if approval is None:
        approval = ScenarioApproval(scenario_id=scenario_id, stage=stage)
        session.add(approval)
    approval.decision = payload.decision
    approval.comment = payload.comment
    if "note" in payload.model_fields_set:
        approval.note = payload.note
    approval.decided_by_id = (
        None if payload.decision == ApprovalDecision.PENDING else user.id
    )
    approval.decided_at = (
        None if payload.decision == ApprovalDecision.PENDING else datetime.now(UTC)
    )
    if payload.decision != ApprovalDecision.APPROVED:
        reset_downstream_approvals(scenario, stage)
    scenario.status = status_after_decision(scenario, stage, payload.decision)
    scenario.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(approval)
    return approval


@router.get("/{scenario_id}/comments", response_model=list[CommentRead])
async def list_comments(
    scenario_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ScenarioComment]:
    await get_visible_scenario(session, scenario_id, user)
    filters = [ScenarioComment.scenario_id == scenario_id]
    if user.role == Role.CLIENT:
        filters.append(ScenarioComment.stage.in_([stage.value for stage in CLIENT_APPROVAL_STAGES]))
    query = select(ScenarioComment).where(*filters).order_by(ScenarioComment.created_at)
    return list((await session.scalars(query)).all())


@router.post(
    "/{scenario_id}/comments", response_model=CommentRead, status_code=status.HTTP_201_CREATED
)
async def create_comment(
    scenario_id: uuid.UUID,
    payload: CommentCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ScenarioComment:
    scenario = await get_visible_scenario(session, scenario_id, user, for_update=True)
    if user.role == Role.CLIENT and payload.stage not in {
        ApprovalStage.PRE_GENERATION_CLIENT.value,
        ApprovalStage.FINAL_CLIENT.value,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client comment must belong to an allowed approval stage",
        )
    comment = ScenarioComment(
        scenario_id=scenario_id,
        author_id=user.id,
        stage=payload.stage,
        text=payload.text,
    )
    session.add(comment)
    scenario.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(comment)
    return comment


@router.put("/{scenario_id}/montage", response_model=MontageRead)
async def update_montage(
    scenario_id: uuid.UUID,
    payload: MontageUpdate,
    user: User = Depends(require_roles(Role.MANAGER, Role.SCENARIST, Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> MontageTask:
    scenario = await get_visible_scenario(session, scenario_id, user, for_update=True)
    if not is_approved(scenario, ApprovalStage.PRE_GENERATION_CLIENT):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Client must approve the script before montage preparation",
        )
    if scenario.montage is None:
        scenario.montage = MontageTask()
    montage_changes = payload.model_dump(exclude_unset=True)
    if (
        "assigned_editor_id" in montage_changes
        and montage_changes["assigned_editor_id"] is not None
    ):
        montage_changes["assigned_editor_id"] = require_assignable_editor(
            await session.get(User, montage_changes["assigned_editor_id"])
        ).id
    source_material_changed = (
        "source_material_url" in montage_changes
        and scenario.montage.source_material_url != montage_changes["source_material_url"]
    )
    for key, value in montage_changes.items():
        setattr(scenario.montage, key, value)
    if source_material_changed:
        reset_approvals_from(scenario, ApprovalStage.SOURCE_MATERIAL)
        scenario.status = ScenarioStatus.SENT_TO_GENERATION
    if scenario.montage.assigned_editor_id and is_approved(scenario, ApprovalStage.SOURCE_MATERIAL):
        scenario.status = ScenarioStatus.HANDED_TO_EDITOR
    scenario.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(scenario.montage)
    return scenario.montage


@router.put("/{scenario_id}/montage/editor", response_model=MontageRead)
async def update_montage_as_editor(
    scenario_id: uuid.UUID,
    payload: EditorMontageUpdate,
    user: User = Depends(require_roles(Role.MANAGER, Role.SCENARIST, Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> MontageTask:
    scenario = await get_visible_scenario(session, scenario_id, user, for_update=True)
    if scenario.montage is None or not is_approved(scenario, ApprovalStage.SOURCE_MATERIAL):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Source material must be approved before montage result",
        )
    editor_changes = payload.model_dump(exclude_unset=True)
    ready_material_changed = (
        "ready_material_url" in editor_changes
        and scenario.montage.ready_material_url != editor_changes["ready_material_url"]
    )
    for key, value in editor_changes.items():
        setattr(scenario.montage, key, value)
    if ready_material_changed:
        reset_approvals_from(scenario, ApprovalStage.MONTAGE_COMPLIANCE)
    scenario.status = ScenarioStatus.EDITING
    scenario.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(scenario.montage)
    return scenario.montage


@router.put("/{scenario_id}/publication", response_model=PublicationRead)
async def update_publication(
    scenario_id: uuid.UUID,
    payload: PublicationUpdate,
    user: User = Depends(require_roles(Role.MANAGER, Role.SCENARIST, Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> Publication:
    scenario = await get_visible_scenario(session, scenario_id, user, for_update=True)
    if not is_approved(scenario, ApprovalStage.FINAL_CLIENT):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Client must approve the final montage before publication",
        )
    if scenario.publication is None:
        scenario.publication = Publication()
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(scenario.publication, key, value)
    if scenario.publication.is_published:
        scenario.publication.first_published_at = (
            scenario.publication.first_published_at or datetime.now(UTC)
        )
        scenario.status = ScenarioStatus.PUBLISHED
    elif scenario.status == ScenarioStatus.PUBLISHED:
        scenario.status = status_after_unpublishing(scenario)
    scenario.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(scenario.publication)
    return scenario.publication
