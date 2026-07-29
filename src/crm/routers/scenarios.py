import math
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from crm.creation import (
    require_active_project,
    require_assignable_editor,
    require_assignable_publisher,
    require_assignable_scenarist,
    resolve_scenarist_assignment,
)
from crm.database import get_session
from crm.dependencies import get_current_user, require_roles
from crm.models import (
    ApprovalDecision,
    ApprovalStage,
    Client,
    FinalClientRevisionGate,
    GateDecision,
    MontageTask,
    Project,
    Publication,
    PublicationReviewDecision,
    PublisherStatus,
    Role,
    Scenario,
    ScenarioApproval,
    ScenarioComment,
    ScenarioContent,
    ScenarioResearch,
    ScenarioStatus,
    SheetSource,
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
    FinalRevisionGateRead,
    FinalRevisionGateUpdate,
    GateManagerDecision,
    MontageRead,
    MontageUpdate,
    PaginationMeta,
    ProjectSummary,
    PublicationManagerDecision,
    PublicationManagerReviewUpdate,
    PublicationPublisherUpdate,
    PublicationRead,
    PublicationUpdate,
    PublisherActionStatus,
    ScenarioCreate,
    ScenarioListItem,
    ScenarioPage,
    ScenarioQueue,
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
from crm.sheet_sync import enqueue_sheet_writeback
from crm.workflow import (
    EDITOR_ACTION_STATUSES,
    EDITOR_MANAGER_VISIBLE_STATUSES,
    EDITOR_VISIBLE_STATUSES,
    PUBLISHER_MANAGER_VISIBLE_STATUSES,
    PUBLISHER_VISIBLE_STATUSES,
    ROLE_APPROVAL_STAGES,
    approval_for,
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
    selectinload(Scenario.publication).selectinload(Publication.assigned_publisher),
    selectinload(Scenario.final_revision_gate),
)

CLIENT_APPROVAL_STAGES = {
    ApprovalStage.PRE_GENERATION_CLIENT,
    ApprovalStage.FINAL_CLIENT,
}

REVISION_COMMENT_STAGES = {
    *CLIENT_APPROVAL_STAGES,
    ApprovalStage.SOURCE_MATERIAL,
    ApprovalStage.MONTAGE_COMPLIANCE,
}

SCENARIST_MONTAGE_FIELDS = {
    "source_material_url",
    "client_brand_style",
    "extra_brief",
    "material_status",
    "scenarist_material_comment",
    "scenarist_revision_status",
    "scenarist_revision_comment",
}
EDITOR_MANAGER_MONTAGE_FIELDS = {
    "assigned_editor_id",
    "external_editor_name",
    "price",
    "payment_due_date",
    "brief_compliance_status",
    "ready_at",
    "bot_visual_analysis",
    "compliance_analysis",
    "ai_analysis",
}


def require_approval_comment(
    stage: ApprovalStage, decision: ApprovalDecision, comment: str | None
) -> None:
    if (
        stage in REVISION_COMMENT_STAGES
        and decision in {ApprovalDecision.REVISION, ApprovalDecision.REJECTED}
        and not (comment or "").strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A revision decision requires a comment",
        )


def require_source_editor_assignment(
    scenario: Scenario, stage: ApprovalStage, decision: ApprovalDecision
) -> None:
    if (
        stage == ApprovalStage.SOURCE_MATERIAL
        and decision == ApprovalDecision.APPROVED
        and (scenario.montage is None or scenario.montage.assigned_editor_id is None)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assign an editor before approving source material",
        )


def open_final_revision_gate(scenario: Scenario, request_comment: str) -> None:
    gate = scenario.final_revision_gate
    if gate is None:
        gate = FinalClientRevisionGate()
        scenario.final_revision_gate = gate
    gate.decision = GateDecision.PENDING
    gate.request_comment = request_comment
    gate.manager_comment = None
    gate.decided_by_id = None
    gate.decided_at = None


def publication_content_ready(publication: Publication | None) -> bool:
    return bool(
        publication
        and any(
            (
                publication.description_dzen,
                publication.description_youtube,
                publication.description_tiktok,
                publication.description_instagram,
            )
        )
    )


def reset_publication_review(publication: Publication) -> None:
    publication.manager_review_decision = PublicationReviewDecision.PENDING
    publication.manager_review_comment = None
    publication.manager_reviewed_by_id = None
    publication.manager_reviewed_at = None
    publication.assigned_publisher_id = None
    publication.publisher_status = PublisherStatus.PENDING
    publication.publisher_comment = None
    publication.is_published = False
    publication.published_at = None


def apply_final_revision_gate_decision(
    scenario: Scenario,
    decision: GateManagerDecision,
    comment: str,
    manager: User,
) -> FinalClientRevisionGate:
    if manager.role != Role.EDITOR_MANAGER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only editor manager can decide the final revision gate",
        )
    gate = scenario.final_revision_gate
    final_approval = approval_for(scenario, ApprovalStage.FINAL_CLIENT)
    if gate is None or final_approval is None or final_approval.decision not in {
        ApprovalDecision.REVISION,
        ApprovalDecision.REJECTED,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="There is no pending final client revision request",
        )
    if gate.decision != GateDecision.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Final revision gate is already decided",
        )
    if not comment.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Manager gate comment is required",
        )

    gate.decision = GateDecision(decision.value)
    gate.manager_comment = comment
    gate.decided_by_id = manager.id
    gate.decided_at = datetime.now(UTC)
    if gate.decision == GateDecision.APPROVED:
        reset_approvals_from(scenario, ApprovalStage.MONTAGE_COMPLIANCE)
        scenario.status = ScenarioStatus.EDITING
    else:
        final_approval.decision = ApprovalDecision.PENDING
        final_approval.decided_by_id = None
        final_approval.decided_at = None
        scenario.status = ScenarioStatus.CLIENT_REVIEW
    return gate


async def apply_publication_manager_review(
    session: AsyncSession,
    scenario: Scenario,
    decision: PublicationManagerDecision,
    comment: str | None,
    assigned_publisher_id: uuid.UUID | None,
    manager: User,
) -> Publication:
    if manager.role != Role.PUBLISHER_MANAGER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only publisher manager can review publication",
        )
    if (
        scenario.publication is not None
        and scenario.publication.manager_review_decision
        == PublicationReviewDecision.APPROVED
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Publication review is already approved; edit publication content to reopen it",
        )
    if decision == PublicationManagerDecision.REVISION and not (comment or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Publication revision requires a manager comment",
        )
    if not is_approved(scenario, ApprovalStage.FINAL_CLIENT):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Final client approval is required before publication review",
        )
    if not publication_content_ready(scenario.publication):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="At least one publication description is required",
        )
    publication = scenario.publication
    publication.manager_review_decision = PublicationReviewDecision(decision.value)
    publication.manager_review_comment = comment
    publication.manager_reviewed_by_id = manager.id
    publication.manager_reviewed_at = datetime.now(UTC)
    publication.is_published = False
    publication.published_at = None
    if publication.manager_review_decision == PublicationReviewDecision.APPROVED:
        if assigned_publisher_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="assigned_publisher_id is required for approval",
            )
        publisher = require_assignable_publisher(
            await session.get(User, assigned_publisher_id)
        )
        publication.assigned_publisher_id = publisher.id
        publication.publisher_status = PublisherStatus.ASSIGNED
        scenario.status = ScenarioStatus.READY_TO_PUBLISH
    else:
        publication.assigned_publisher_id = None
        publication.publisher_status = PublisherStatus.PENDING
        scenario.status = ScenarioStatus.APPROVED
    return publication


def apply_publisher_action(
    scenario: Scenario,
    status_value: PublisherActionStatus,
    comment: str | None,
    urls: dict[str, str | None],
) -> Publication:
    publication = scenario.publication
    if publication is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Publication is missing")
    for key, value in urls.items():
        setattr(publication, key, value)
    publication.publisher_comment = comment
    publication.publisher_status = PublisherStatus(status_value.value)
    if publication.publisher_status == PublisherStatus.PUBLISHED:
        if not any(
            (
                publication.dzen_url,
                publication.youtube_url,
                publication.tiktok_url,
                publication.instagram_url,
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="At least one published URL is required",
            )
        now = datetime.now(UTC)
        publication.is_published = True
        publication.published_at = publication.published_at or now
        publication.first_published_at = publication.first_published_at or now
        publication.publication_date = publication.publication_date or now.date()
        scenario.status = ScenarioStatus.PUBLISHED
    else:
        publication.is_published = False
        scenario.status = ScenarioStatus.READY_TO_PUBLISH
    return publication

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
    if user.role == Role.EDITOR_MANAGER:
        return query.where(Scenario.status.in_(EDITOR_MANAGER_VISIBLE_STATUSES))
    if user.role == Role.CLIENT:
        return query.where(Scenario.project.has(Project.client_id == user.client_id))
    if user.role == Role.PUBLISHER:
        return query.where(
            Scenario.publication.has(Publication.assigned_publisher_id == user.id),
            Scenario.status.in_(PUBLISHER_VISIBLE_STATUSES),
        )
    if user.role == Role.PUBLISHER_MANAGER:
        return query.where(Scenario.status.in_(PUBLISHER_MANAGER_VISIBLE_STATUSES))
    return query


def client_filter(client_id: uuid.UUID):
    """Filter by owning client without changing the caller's role visibility."""
    return Project.client_id == client_id


QUEUE_ROLES = {
    ScenarioQueue.SCENARIO_MANAGER_REVIEW: Role.MANAGER,
    ScenarioQueue.EDITOR_MANAGER_INTAKE: Role.EDITOR_MANAGER,
    ScenarioQueue.EDITOR_MANAGER_INWORK: Role.EDITOR_MANAGER,
    ScenarioQueue.EDITOR_MANAGER_CHECK: Role.EDITOR_MANAGER,
    ScenarioQueue.EDITOR_MANAGER_REWORK_REVIEW: Role.EDITOR_MANAGER,
    ScenarioQueue.PUBLISHER_MANAGER_QUEUE: Role.PUBLISHER_MANAGER,
    ScenarioQueue.PUBLISHER_MANAGER_INWORK: Role.PUBLISHER_MANAGER,
}


def _approval_pending(stage: ApprovalStage):
    stage_exists = Scenario.approvals.any(ScenarioApproval.stage == stage)
    pending = Scenario.approvals.any(
        and_(
            ScenarioApproval.stage == stage,
            ScenarioApproval.decision == ApprovalDecision.PENDING,
        )
    )
    return or_(~stage_exists, pending)


def _approval_approved(stage: ApprovalStage):
    return Scenario.approvals.any(
        and_(
            ScenarioApproval.stage == stage,
            ScenarioApproval.decision == ApprovalDecision.APPROVED,
        )
    )


def queue_filter(queue: ScenarioQueue, user: User):
    required_role = QUEUE_ROLES[queue]
    if user.role != required_role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Queue {queue.value} is available only to {required_role.value}",
        )

    nonempty_script = Scenario.content.has(
        func.nullif(ScenarioContent.script_text, "").is_not(None)
    )
    nonempty_source = Scenario.montage.has(
        func.nullif(MontageTask.source_material_url, "").is_not(None)
    )
    nonempty_ready = Scenario.montage.has(
        func.nullif(MontageTask.ready_material_url, "").is_not(None)
    )
    if queue == ScenarioQueue.SCENARIO_MANAGER_REVIEW:
        return and_(
            Scenario.status == ScenarioStatus.IN_REVIEW,
            nonempty_script,
            _approval_pending(ApprovalStage.RESPONSIBLE_REVIEW),
        )
    if queue == ScenarioQueue.EDITOR_MANAGER_INTAKE:
        return and_(
            Scenario.status == ScenarioStatus.SENT_TO_GENERATION,
            _approval_approved(ApprovalStage.PRE_GENERATION_CLIENT),
            nonempty_source,
            _approval_pending(ApprovalStage.SOURCE_MATERIAL),
        )
    if queue == ScenarioQueue.EDITOR_MANAGER_INWORK:
        return and_(
            Scenario.status.in_(
                {ScenarioStatus.HANDED_TO_EDITOR, ScenarioStatus.EDITING}
            ),
            Scenario.montage.has(MontageTask.assigned_editor_id.is_not(None)),
            ~nonempty_ready,
        )
    if queue == ScenarioQueue.EDITOR_MANAGER_CHECK:
        return and_(
            Scenario.status == ScenarioStatus.EDITING,
            nonempty_ready,
            _approval_pending(ApprovalStage.MONTAGE_COMPLIANCE),
        )
    if queue == ScenarioQueue.EDITOR_MANAGER_REWORK_REVIEW:
        pending_gate = Scenario.final_revision_gate.has(
            FinalClientRevisionGate.decision == GateDecision.PENDING
        )
        resubmitted_montage = and_(
            Scenario.status == ScenarioStatus.EDITING,
            nonempty_ready,
            Scenario.approvals.any(
                and_(
                    ScenarioApproval.stage == ApprovalStage.MONTAGE_COMPLIANCE,
                    ScenarioApproval.decision == ApprovalDecision.PENDING,
                    func.nullif(ScenarioApproval.comment, "").is_not(None),
                )
            ),
        )
        return or_(pending_gate, resubmitted_montage)
    if queue == ScenarioQueue.PUBLISHER_MANAGER_QUEUE:
        prepared_publication = Scenario.publication.has(
            and_(
                Publication.manager_review_decision
                == PublicationReviewDecision.PENDING,
                or_(
                    func.nullif(Publication.description_dzen, "").is_not(None),
                    func.nullif(Publication.description_youtube, "").is_not(None),
                    func.nullif(Publication.description_tiktok, "").is_not(None),
                    func.nullif(Publication.description_instagram, "").is_not(None),
                ),
            )
        )
        return and_(
            Scenario.status == ScenarioStatus.APPROVED,
            _approval_approved(ApprovalStage.FINAL_CLIENT),
            prepared_publication,
        )
    return and_(
        Scenario.status == ScenarioStatus.READY_TO_PUBLISH,
        Scenario.publication.has(
            and_(
                Publication.manager_review_decision
                == PublicationReviewDecision.APPROVED,
                Publication.assigned_publisher_id.is_not(None),
                Publication.publisher_status.in_(
                    {PublisherStatus.ASSIGNED, PublisherStatus.IN_PROGRESS}
                ),
            )
        ),
    )


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
    if user.role in {
        Role.MANAGER,
        Role.EDITOR_MANAGER,
        Role.PUBLISHER_MANAGER,
        Role.SCENARIST,
        Role.EDITOR,
        Role.PUBLISHER,
    }:
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
        if result.publication is not None:
            updates["publication"] = result.publication.model_copy(
                update={
                    "assigned_publisher_id": None,
                    "manager_reviewed_by_id": None,
                }
            )
        if result.final_revision_gate is not None:
            updates["final_revision_gate"] = result.final_revision_gate.model_copy(
                update={"decided_by_id": None}
            )

    result = result.model_copy(update=updates)

    return result


@router.get("", response_model=ScenarioPage)
async def list_scenarios(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    client_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    scenario_statuses: list[ScenarioStatus] | None = Query(None, alias="status"),
    assigned_scenarist_id: uuid.UUID | None = None,
    queue: ScenarioQueue | None = None,
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
    if queue is not None:
        filters.append(queue_filter(queue, user))
    if client_id:
        filters.append(client_filter(client_id))
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
    client_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    scenario_statuses: list[ScenarioStatus] | None = Query(None, alias="status"),
    assigned_scenarist_id: uuid.UUID | None = None,
    queue: ScenarioQueue | None = None,
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
    if queue is not None:
        filters.append(queue_filter(queue, user))
    if client_id:
        filters.append(client_filter(client_id))
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


def scenario_create_writeback(
    payload: ScenarioCreate,
    external_id: str,
) -> dict[str, object]:
    values: dict[str, object | None] = {
        "external_id": external_id,
        "scenario_date": payload.scenario_date,
        "deadline": payload.deadline,
        "score": payload.score,
        "scenario_type": payload.scenario_type,
        "visual_format": payload.visual_format,
        "speaker": payload.speaker,
    }
    if payload.research:
        values.update(
            {
                f"research.{field_name}": value
                for field_name, value in payload.research.model_dump().items()
            }
        )
    if payload.content:
        values.update(
            {
                f"content.{field_name}": value
                for field_name, value in payload.content.model_dump().items()
            }
        )
    return {
        field_name: value
        for field_name, value in values.items()
        if value is not None
    }


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
    sheet_sources = list(
        (
            await session.scalars(
                select(SheetSource)
                .where(
                    SheetSource.project_id == payload.project_id,
                    SheetSource.enabled.is_(True),
                )
                .order_by(SheetSource.created_at)
            )
        ).all()
    )
    exact_sources = [
        source
        for source in sheet_sources
        if source.assigned_scenarist_id == assigned_scenarist_id
    ]
    sheet_source = (
        exact_sources[0]
        if len(exact_sources) == 1
        else sheet_sources[0]
        if len(sheet_sources) == 1
        else None
    )

    data = payload.model_dump(exclude={"research", "content"})
    data["assigned_scenarist_id"] = assigned_scenarist_id
    if sheet_source is not None:
        data.update(
            sheet_source_id=sheet_source.id,
            crm_row_id=uuid.uuid4(),
            source_sheet_id=sheet_source.spreadsheet_id,
            source_tab=sheet_source.source_tab,
        )
    if session.get_bind().dialect.name != "postgresql":
        existing_ids = await session.scalars(select(Scenario.external_id))
        numeric_ids = [
            int(value) for value in existing_ids if value is not None and value.isdecimal()
        ]
        data["external_id"] = str(max(numeric_ids, default=0) + 1)
    scenario = Scenario(**data)
    if payload.research:
        scenario.research = ScenarioResearch(**payload.research.model_dump())
    if payload.content:
        scenario.content = ScenarioContent(**payload.content.model_dump())
    session.add(scenario)
    try:
        await session.flush()
        await enqueue_sheet_writeback(
            session,
            scenario,
            scenario_create_writeback(payload, scenario.external_id),
        )
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
SHEET_UUID_FIELDS = {
    "assigned_scenarist_id",
    "montage.assigned_editor_id",
    "publication.assigned_publisher_id",
}
SHEET_DECIMAL_FIELDS = {"montage.price"}
SHEET_INTEGER_FIELDS = {"score"}
SHEET_BOOLEAN_FIELDS = {"publication.is_published"}
SHEET_URL_FIELDS = {
    "research.competitor_url",
    "montage.source_material_url",
    "montage.ready_material_url",
    "publication.instagram_url",
    "publication.dzen_url",
    "publication.youtube_url",
    "publication.tiktok_url",
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
SHEET_GATE_FIELDS = {
    "final_revision_gate.decision",
    "final_revision_gate.manager_comment",
}
SHEET_PUBLICATION_REVIEW_FIELDS = {
    "publication.assigned_publisher_id",
    "publication.manager_review_decision",
    "publication.manager_review_comment",
}
SHEET_PUBLISHER_FIELDS = {
    "publication.publisher_status",
    "publication.publisher_comment",
    "publication.dzen_url",
    "publication.youtube_url",
    "publication.tiktok_url",
    "publication.instagram_url",
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
    if field == "publication.publisher_status":
        if value is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="publication.publisher_status cannot be null",
            )
        try:
            return PublisherStatus(PublisherActionStatus(value).value)
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid publication.publisher_status",
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
    gate_changes = {}
    publication_review_changes = {}
    publisher_changes = {}
    regular_changes = []
    for change in payload.changes:
        if change.field.startswith("approval."):
            _, stage_value, attribute = change.field.split(".")
            stage = ApprovalStage(stage_value)
            approval_changes.setdefault(stage, {})[attribute] = change.value
        elif change.field in SHEET_GATE_FIELDS:
            gate_changes[change.field.rsplit(".", 1)[1]] = change.value
        elif change.field in SHEET_PUBLICATION_REVIEW_FIELDS:
            publication_review_changes[change.field.rsplit(".", 1)[1]] = change.value
        elif change.field in SHEET_PUBLISHER_FIELDS:
            publisher_changes[change.field.rsplit(".", 1)[1]] = change.value
        else:
            regular_changes.append(change)

    script_changed = False
    source_material_changed = False
    ready_material_changed = False
    publisher_status_changed = False
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
        publisher_status_changed |= (
            change.field == "publication.publisher_status" and old_value != value
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
            require_approval_comment(stage, decision, approval.comment)
            require_source_editor_assignment(scenario, stage, decision)
            approval.decision = decision
            approval.decided_by_id = None if decision == ApprovalDecision.PENDING else user.id
            approval.decided_at = (
                None if decision == ApprovalDecision.PENDING else datetime.now(UTC)
            )
            if decision != ApprovalDecision.APPROVED:
                reset_downstream_approvals(scenario, stage)
            if stage == ApprovalStage.FINAL_CLIENT and decision in {
                ApprovalDecision.REVISION,
                ApprovalDecision.REJECTED,
            }:
                open_final_revision_gate(scenario, approval.comment or "")
            scenario.status = status_after_decision(scenario, stage, decision)

    if gate_changes:
        gate = scenario.final_revision_gate
        if gate is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Final revision gate not found",
            )
        if "manager_comment" in gate_changes:
            comment = gate_changes["manager_comment"]
            if not isinstance(comment, str) or len(comment) > MAX_COMMENT_LENGTH:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Invalid manager gate comment",
                )
            gate.manager_comment = comment
        if "decision" in gate_changes:
            try:
                gate_decision = GateManagerDecision(gate_changes["decision"])
            except (TypeError, ValueError) as error:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Invalid final revision gate decision",
                ) from error
            apply_final_revision_gate_decision(
                scenario,
                gate_decision,
                gate.manager_comment or "",
                user,
            )

    if publication_review_changes:
        if scenario.publication is None:
            scenario.publication = Publication()
        publication = scenario.publication
        if "manager_review_comment" in publication_review_changes:
            comment = publication_review_changes["manager_review_comment"]
            if comment is not None and (
                not isinstance(comment, str) or len(comment) > MAX_COMMENT_LENGTH
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Invalid publication manager comment",
                )
            publication.manager_review_comment = comment
        if "assigned_publisher_id" in publication_review_changes:
            publisher_id = coerce_sheet_value(
                "publication.assigned_publisher_id",
                publication_review_changes["assigned_publisher_id"],
            )
            if publisher_id is not None:
                publisher_id = require_assignable_publisher(
                    await session.get(User, publisher_id)
                ).id
            publication.assigned_publisher_id = publisher_id
        if "manager_review_decision" in publication_review_changes:
            try:
                review_decision = PublicationManagerDecision(
                    publication_review_changes["manager_review_decision"]
                )
            except (TypeError, ValueError) as error:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Invalid publication manager decision",
                ) from error
            await apply_publication_manager_review(
                session,
                scenario,
                review_decision,
                publication.manager_review_comment,
                publication.assigned_publisher_id,
                user,
            )

    if publisher_changes:
        publication = scenario.publication
        if publication is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Publication is missing",
            )
        urls = {}
        for key in ("dzen_url", "youtube_url", "tiktok_url", "instagram_url"):
            if key in publisher_changes:
                urls[key] = coerce_sheet_value(
                    f"publication.{key}", publisher_changes[key]
                )
        if "publisher_comment" in publisher_changes:
            publisher_comment = publisher_changes["publisher_comment"]
            if publisher_comment is not None and (
                not isinstance(publisher_comment, str)
                or len(publisher_comment) > MAX_COMMENT_LENGTH
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Invalid publisher comment",
                )
            publication.publisher_comment = publisher_comment
        for key, value in urls.items():
            setattr(publication, key, value)
        if "publisher_status" in publisher_changes:
            try:
                publisher_action = PublisherActionStatus(
                    publisher_changes["publisher_status"]
                )
            except (TypeError, ValueError) as error:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Invalid publisher status",
                ) from error
            apply_publisher_action(
                scenario,
                publisher_action,
                publication.publisher_comment,
                {},
            )

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
        if user.role == Role.PUBLISHER and publisher_status_changed:
            if scenario.publication.publisher_status == PublisherStatus.PUBLISHED:
                if not any(
                    (
                        scenario.publication.dzen_url,
                        scenario.publication.youtube_url,
                        scenario.publication.tiktok_url,
                        scenario.publication.instagram_url,
                    )
                ):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="At least one published URL is required",
                    )
                now = datetime.now(UTC)
                scenario.publication.is_published = True
                scenario.publication.published_at = scenario.publication.published_at or now
                scenario.publication.first_published_at = (
                    scenario.publication.first_published_at or now
                )
                scenario.publication.publication_date = (
                    scenario.publication.publication_date or now.date()
                )
                scenario.status = ScenarioStatus.PUBLISHED
            else:
                scenario.publication.is_published = False
                scenario.status = ScenarioStatus.READY_TO_PUBLISH
        elif scenario.publication.is_published:
            scenario.publication.first_published_at = (
                scenario.publication.first_published_at or datetime.now(UTC)
            )
            scenario.status = ScenarioStatus.PUBLISHED
        elif scenario.status == ScenarioStatus.PUBLISHED:
            scenario.status = status_after_unpublishing(scenario)

    scenario.updated_at = datetime.now(UTC)
    await enqueue_sheet_writeback(
        session,
        scenario,
        {change.field: change.value for change in payload.changes},
    )
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
    user: User = Depends(require_roles(Role.MANAGER, Role.SCENARIST)),
    session: AsyncSession = Depends(get_session),
) -> ScenarioRead:
    scenario = await get_visible_scenario(session, scenario_id, user, for_update=True)

    if user.role == Role.MANAGER:
        denied_fields = sorted(payload.model_fields_set - {"assigned_scenarist_id"})
        if denied_fields:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": "Scenario manager can only reassign the scenarist",
                    "fields": denied_fields,
                },
            )

    changes = payload.model_dump(exclude_unset=True, exclude={"research", "content"})
    writeback_changes = dict(changes)
    if "assigned_scenarist_id" in changes:
        if user.role != Role.MANAGER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only manager can reassign a scenarist",
            )
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
            writeback_changes[f"research.{key}"] = value
    if payload.content is not None:
        if scenario.content is None:
            scenario.content = ScenarioContent()
        if "script_text" in payload.content.model_fields_set:
            script_changed = scenario.content.script_text != payload.content.script_text
        for key, value in payload.content.model_dump(exclude_unset=True).items():
            setattr(scenario.content, key, value)
            writeback_changes[f"content.{key}"] = value

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
    await enqueue_sheet_writeback(session, scenario, writeback_changes)
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
    user: User = Depends(
        require_roles(Role.MANAGER, Role.EDITOR_MANAGER, Role.CLIENT)
    ),
    session: AsyncSession = Depends(get_session),
) -> ScenarioApproval:
    scenario = await get_visible_scenario(session, scenario_id, user, for_update=True)
    require_stage_role(user.role, stage)
    require_stage_prerequisites(scenario, stage)
    require_approval_comment(stage, payload.decision, payload.comment)
    require_source_editor_assignment(scenario, stage, payload.decision)
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
    if stage == ApprovalStage.FINAL_CLIENT and payload.decision in {
        ApprovalDecision.REVISION,
        ApprovalDecision.REJECTED,
    }:
        open_final_revision_gate(scenario, payload.comment or "")
    scenario.status = status_after_decision(scenario, stage, payload.decision)
    scenario.updated_at = datetime.now(UTC)
    approval_writeback = {
        f"approval.{stage.value}.decision": payload.decision.value,
        f"approval.{stage.value}.comment": payload.comment,
    }
    if "note" in payload.model_fields_set:
        approval_writeback[f"approval.{stage.value}.note"] = payload.note
    if stage == ApprovalStage.FINAL_CLIENT and scenario.final_revision_gate is not None:
        approval_writeback.update(
            {
                "final_revision_gate.request_comment": (
                    scenario.final_revision_gate.request_comment
                ),
                "final_revision_gate.decision": (
                    scenario.final_revision_gate.decision.value
                ),
            }
        )
    await enqueue_sheet_writeback(session, scenario, approval_writeback)
    await session.commit()
    await session.refresh(approval)
    return approval


@router.get(
    "/{scenario_id}/final-revision-gate",
    response_model=FinalRevisionGateRead,
)
async def get_final_revision_gate(
    scenario_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FinalRevisionGateRead:
    scenario = await get_visible_scenario(session, scenario_id, user)
    if scenario.final_revision_gate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Final revision gate not found",
        )
    result = FinalRevisionGateRead.model_validate(scenario.final_revision_gate)
    if user.role == Role.CLIENT:
        result = result.model_copy(update={"decided_by_id": None})
    return result


@router.put(
    "/{scenario_id}/final-revision-gate",
    response_model=FinalRevisionGateRead,
)
async def decide_final_revision_gate(
    scenario_id: uuid.UUID,
    payload: FinalRevisionGateUpdate,
    user: User = Depends(require_roles(Role.EDITOR_MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> FinalClientRevisionGate:
    scenario = await get_visible_scenario(session, scenario_id, user, for_update=True)
    gate = apply_final_revision_gate_decision(
        scenario, payload.decision, payload.comment, user
    )
    scenario.updated_at = datetime.now(UTC)
    await enqueue_sheet_writeback(
        session,
        scenario,
        {
            "final_revision_gate.decision": gate.decision.value,
            "final_revision_gate.manager_comment": gate.manager_comment,
        },
    )
    await session.commit()
    await session.refresh(gate)
    return gate


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
    await enqueue_sheet_writeback(
        session,
        scenario,
        {"comments.latest": payload.text},
    )
    await session.commit()
    await session.refresh(comment)
    return comment


@router.put("/{scenario_id}/montage", response_model=MontageRead)
async def update_montage(
    scenario_id: uuid.UUID,
    payload: MontageUpdate,
    user: User = Depends(require_roles(Role.SCENARIST, Role.EDITOR_MANAGER)),
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
    allowed_fields = (
        SCENARIST_MONTAGE_FIELDS
        if user.role == Role.SCENARIST
        else EDITOR_MANAGER_MONTAGE_FIELDS
    )
    denied_fields = sorted(set(montage_changes) - allowed_fields)
    if denied_fields:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "Montage fields are not owned by this role",
                "fields": denied_fields,
            },
        )
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
    await enqueue_sheet_writeback(
        session,
        scenario,
        {f"montage.{key}": value for key, value in montage_changes.items()},
    )
    await session.commit()
    updated = await get_visible_scenario(session, scenario.id, user)
    return updated.montage


@router.put("/{scenario_id}/montage/editor", response_model=MontageRead)
async def update_montage_as_editor(
    scenario_id: uuid.UUID,
    payload: EditorMontageUpdate,
    user: User = Depends(require_roles(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> MontageTask:
    scenario = await get_visible_scenario(session, scenario_id, user, for_update=True)
    if (
        scenario.montage is None
        or scenario.montage.assigned_editor_id != user.id
        or not is_approved(scenario, ApprovalStage.SOURCE_MATERIAL)
        or scenario.status not in EDITOR_ACTION_STATUSES
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Montage result is not available at the current assigned editor stage",
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
    await enqueue_sheet_writeback(
        session,
        scenario,
        {f"montage.{key}": value for key, value in editor_changes.items()},
    )
    await session.commit()
    await session.refresh(scenario.montage)
    return scenario.montage


@router.put("/{scenario_id}/publication", response_model=PublicationRead)
async def update_publication(
    scenario_id: uuid.UUID,
    payload: PublicationUpdate,
    user: User = Depends(require_roles(Role.SCENARIST)),
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
    publication_changes = payload.model_dump(exclude_unset=True)
    changed = any(
        getattr(scenario.publication, key) != value
        for key, value in publication_changes.items()
    )
    for key, value in publication_changes.items():
        setattr(scenario.publication, key, value)
    if changed:
        reset_publication_review(scenario.publication)
        scenario.status = ScenarioStatus.APPROVED
    scenario.updated_at = datetime.now(UTC)
    await enqueue_sheet_writeback(
        session,
        scenario,
        {f"publication.{key}": value for key, value in publication_changes.items()},
    )
    await session.commit()
    await session.refresh(scenario.publication)
    return scenario.publication


@router.put(
    "/{scenario_id}/publication/manager-review",
    response_model=PublicationRead,
)
async def review_publication(
    scenario_id: uuid.UUID,
    payload: PublicationManagerReviewUpdate,
    user: User = Depends(require_roles(Role.PUBLISHER_MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> Publication:
    scenario = await get_visible_scenario(session, scenario_id, user, for_update=True)
    await apply_publication_manager_review(
        session,
        scenario,
        payload.decision,
        payload.comment,
        payload.assigned_publisher_id,
        user,
    )
    scenario.updated_at = datetime.now(UTC)
    publication = scenario.publication
    await enqueue_sheet_writeback(
        session,
        scenario,
        {
            "publication.manager_review_decision": (
                publication.manager_review_decision.value
            ),
            "publication.manager_review_comment": publication.manager_review_comment,
            "publication.assigned_publisher_id": publication.assigned_publisher_id,
        },
    )
    await session.commit()
    updated = await get_visible_scenario(session, scenario.id, user)
    return updated.publication


@router.put(
    "/{scenario_id}/publication/publisher",
    response_model=PublicationRead,
)
async def update_publication_as_publisher(
    scenario_id: uuid.UUID,
    payload: PublicationPublisherUpdate,
    user: User = Depends(require_roles(Role.PUBLISHER)),
    session: AsyncSession = Depends(get_session),
) -> Publication:
    scenario = await get_visible_scenario(session, scenario_id, user, for_update=True)
    publication = scenario.publication
    if (
        publication is None
        or publication.assigned_publisher_id != user.id
        or publication.manager_review_decision != PublicationReviewDecision.APPROVED
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Publication is not assigned and approved for this publisher",
        )
    urls = payload.model_dump(exclude_unset=True, exclude={"status", "comment"})
    publication = apply_publisher_action(
        scenario, payload.status, payload.comment, urls
    )
    scenario.updated_at = datetime.now(UTC)
    await enqueue_sheet_writeback(
        session,
        scenario,
        {
            "publication.publisher_status": publication.publisher_status.value,
            "publication.publisher_comment": publication.publisher_comment,
            **{f"publication.{key}": value for key, value in urls.items()},
        },
    )
    await session.commit()
    await session.refresh(publication)
    return publication
