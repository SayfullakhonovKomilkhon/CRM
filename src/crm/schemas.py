import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from crm.models import (
    ApprovalDecision,
    ApprovalStage,
    GateDecision,
    PublicationReviewDecision,
    PublisherStatus,
    Role,
    ScenarioStatus,
)


class EditorStatus(StrEnum):
    IN_PROGRESS = "В работе"
    READY = "Готово"
    NOT_READY = "Не готово"
    REVIEW = "Проверить"
    FIXED = "Исправлено"


class GateManagerDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class PublicationManagerDecision(StrEnum):
    APPROVED = "approved"
    REVISION = "revision"


class PublisherActionStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    PUBLISHED = "published"


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WriteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


MAX_TEXT_LENGTH = 100_000
MAX_COMMENT_LENGTH = 10_000
MAX_URL_LENGTH = 2_048


def normalize_http_url(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("URL must be a string")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_URL_LENGTH:
        raise ValueError("URL is too long")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must use http or https")
    return normalized


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserRead(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    client_id: uuid.UUID | None


class UserOptionRead(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str


class ClientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    external_id: str | None = Field(default=None, max_length=100)


class ClientRead(ORMModel):
    id: uuid.UUID
    name: str
    external_id: str | None
    is_active: bool


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    external_name: str | None = Field(default=None, max_length=255)


class ProjectRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    name: str
    external_name: str | None
    is_active: bool


class ResearchPayload(WriteModel):
    competitor_url: str | None = Field(default=None, max_length=MAX_URL_LENGTH)
    competitor_category: str | None = Field(default=None, max_length=255)
    full_analysis: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    performance_metrics: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    transcription: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    timeline: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    why_viral: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    takeaways: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    improvements: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    replication_template: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    ai_analysis: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)

    _validate_competitor_url = field_validator("competitor_url", mode="before")(
        normalize_http_url
    )


class ContentPayload(WriteModel):
    claude_context: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    cover_text: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    script_text: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    montage_brief: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    scenarist_comment: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    hook: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    retention: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    call_to_action: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    visual_notes: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    score_recommendations: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    ai_review: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)


class ScenarioCreate(WriteModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_id: uuid.UUID
    assigned_scenarist_id: uuid.UUID | None = None
    source_sheet_id: str | None = Field(default=None, max_length=255)
    source_tab: str | None = Field(default=None, max_length=255)
    source_row: int | None = Field(default=None, ge=1)
    scenario_date: date | None = None
    deadline: date | None = None
    score: int | None = Field(default=None, ge=0, le=100)
    scenario_type: str | None = Field(default=None, max_length=100)
    visual_format: str | None = Field(default=None, max_length=255)
    speaker: str | None = Field(default=None, max_length=255)
    research: ResearchPayload | None = None
    content: ContentPayload | None = None


class ScenarioUpdate(WriteModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    assigned_scenarist_id: uuid.UUID | None = None
    scenario_date: date | None = None
    deadline: date | None = None
    score: int | None = Field(default=None, ge=0, le=100)
    scenario_type: str | None = Field(default=None, max_length=100)
    visual_format: str | None = Field(default=None, max_length=255)
    speaker: str | None = Field(default=None, max_length=255)
    research: ResearchPayload | None = None
    content: ContentPayload | None = None


class ResearchRead(ResearchPayload):
    model_config = ConfigDict(from_attributes=True)


class ContentRead(ContentPayload):
    model_config = ConfigDict(from_attributes=True)


class ApprovalRead(ORMModel):
    id: uuid.UUID
    stage: ApprovalStage
    decision: ApprovalDecision
    comment: str | None
    note: str | None
    decided_by_id: uuid.UUID | None
    decided_at: datetime | None


class ApprovalUpdate(WriteModel):
    decision: ApprovalDecision
    comment: str | None = Field(default=None, max_length=MAX_COMMENT_LENGTH)
    note: str | None = Field(default=None, max_length=MAX_COMMENT_LENGTH)


class FinalRevisionGateRead(ORMModel):
    scenario_id: uuid.UUID
    decision: GateDecision
    request_comment: str
    manager_comment: str | None
    decided_by_id: uuid.UUID | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class FinalRevisionGateUpdate(WriteModel):
    decision: GateManagerDecision
    comment: str = Field(min_length=1, max_length=MAX_COMMENT_LENGTH)


class ProjectSummary(ORMModel):
    id: uuid.UUID
    name: str
    client_name: str


class ScenaristSummary(ORMModel):
    id: uuid.UUID | None
    name: str
    initials: str


class CommentCreate(WriteModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=10_000)
    stage: str | None = Field(default=None, max_length=100)


class CommentRead(ORMModel):
    id: uuid.UUID
    scenario_id: uuid.UUID
    author_id: uuid.UUID
    stage: str | None
    text: str
    created_at: datetime


class MontageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_material_url: str | None = Field(default=None, max_length=MAX_URL_LENGTH)
    client_brand_style: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    extra_brief: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    assigned_editor_id: uuid.UUID | None = None
    external_editor_name: str | None = Field(default=None, max_length=255)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    payment_due_date: date | None = None
    material_status: str | None = Field(default=None, max_length=100)
    scenarist_material_comment: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    brief_compliance_status: str | None = Field(default=None, max_length=100)
    ready_at: date | None = None
    bot_visual_analysis: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    compliance_analysis: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    ai_analysis: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    scenarist_revision_status: str | None = Field(default=None, max_length=100)
    scenarist_revision_comment: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)

    _validate_source_material_url = field_validator("source_material_url", mode="before")(
        normalize_http_url
    )


class MontageRead(ORMModel):
    scenario_id: uuid.UUID
    source_material_url: str | None
    client_brand_style: str | None
    extra_brief: str | None
    assigned_editor_id: uuid.UUID | None
    assigned_editor_name: str | None
    external_editor_name: str | None
    price: Decimal | None
    payment_due_date: date | None
    material_status: str | None
    scenarist_material_comment: str | None
    ready_material_url: str | None
    editor_status: EditorStatus | None
    editor_comment: str | None
    brief_compliance_status: str | None
    ready_at: date | None
    bot_visual_analysis: str | None
    compliance_analysis: str | None
    ai_analysis: str | None
    scenarist_revision_status: str | None
    scenarist_revision_comment: str | None
    updated_at: datetime


class EditorMontageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready_material_url: str | None = Field(default=None, max_length=MAX_URL_LENGTH)
    editor_status: EditorStatus | None = None
    editor_comment: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)

    _validate_ready_material_url = field_validator("ready_material_url", mode="before")(
        normalize_http_url
    )


class PublicationUpdate(WriteModel):
    description_dzen: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    description_youtube: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    description_tiktok: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    description_instagram: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    publisher_brief: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    engagement_metrics: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    publication_analysis: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    ai_social_descriptions: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    leia_script: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)



class PublicationManagerReviewUpdate(WriteModel):
    decision: PublicationManagerDecision
    comment: str | None = Field(default=None, max_length=MAX_COMMENT_LENGTH)
    assigned_publisher_id: uuid.UUID | None = None


class PublicationPublisherUpdate(WriteModel):
    status: PublisherActionStatus
    comment: str | None = Field(default=None, max_length=MAX_COMMENT_LENGTH)
    dzen_url: str | None = Field(default=None, max_length=MAX_URL_LENGTH)
    youtube_url: str | None = Field(default=None, max_length=MAX_URL_LENGTH)
    tiktok_url: str | None = Field(default=None, max_length=MAX_URL_LENGTH)
    instagram_url: str | None = Field(default=None, max_length=MAX_URL_LENGTH)

    _validate_urls = field_validator(
        "dzen_url", "youtube_url", "tiktok_url", "instagram_url", mode="before"
    )(normalize_http_url)


class PublicationRead(ORMModel):
    scenario_id: uuid.UUID
    description_dzen: str | None
    description_youtube: str | None
    description_tiktok: str | None
    description_instagram: str | None
    publication_date: date | None
    is_published: bool
    first_published_at: datetime | None
    assigned_publisher_id: uuid.UUID | None
    assigned_publisher_name: str | None
    manager_review_decision: PublicationReviewDecision
    manager_review_comment: str | None
    manager_reviewed_by_id: uuid.UUID | None
    manager_reviewed_at: datetime | None
    publisher_status: PublisherStatus
    publisher_comment: str | None
    dzen_url: str | None
    youtube_url: str | None
    tiktok_url: str | None
    published_at: datetime | None
    publisher_brief: str | None
    instagram_url: str | None
    engagement_metrics: str | None
    publication_analysis: str | None
    ai_social_descriptions: str | None
    leia_script: str | None
    updated_at: datetime


class ScenarioRead(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    assigned_scenarist_id: uuid.UUID | None
    external_id: str
    source_tab: str | None
    source_row: int | None
    scenario_date: date | None
    deadline: date | None
    score: int | None
    scenario_type: str | None
    visual_format: str | None
    speaker: str | None
    status: ScenarioStatus
    research: ResearchRead | None
    content: ContentRead | None
    approvals: list[ApprovalRead]
    title: str
    project: ProjectSummary
    scenarist: ScenaristSummary | None
    comments_count: int
    montage: MontageRead | None
    publication: PublicationRead | None
    final_revision_gate: FinalRevisionGateRead | None = None
    available_sections: list[str] = Field(default_factory=list)
    available_approval_stages: list[ApprovalStage] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ScenarioSortBy(StrEnum):
    UPDATED_AT = "updated_at"
    DEADLINE = "deadline"
    CREATED_AT = "created_at"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class ScenarioListItem(BaseModel):
    id: uuid.UUID
    title: str
    external_id: str
    project: ProjectSummary
    scenarist: ScenaristSummary | None
    speaker: str | None
    visual_format: str | None
    status: ScenarioStatus
    deadline: date | None
    score: int | None
    comments_count: int
    updated_at: datetime


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    pages: int


class ScenarioPage(BaseModel):
    items: list[ScenarioListItem]
    meta: PaginationMeta


class SheetColumnRead(BaseModel):
    field: str
    label: str
    group: str
    editor: Literal["inline", "detail", "readonly"]
    allowed_values: list[str] | None = None


class SheetScenarioRow(BaseModel):
    id: uuid.UUID
    version: datetime
    values: dict[str, Any]
    editable_fields: list[str]


class SheetScenarioPage(BaseModel):
    columns: list[SheetColumnRead]
    items: list[SheetScenarioRow]
    meta: PaginationMeta


class SheetCellChange(WriteModel):
    field: str = Field(min_length=1, max_length=100)
    value: Any = None


class SheetRowPatch(WriteModel):
    expected_version: datetime
    changes: list[SheetCellChange] = Field(min_length=1, max_length=50)


class SheetRowPatchResult(BaseModel):
    id: uuid.UUID
    version: datetime
    changed_fields: list[str]
