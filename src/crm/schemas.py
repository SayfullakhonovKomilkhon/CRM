import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from crm.models import ApprovalDecision, ApprovalStage, Role, ScenarioStatus


class EditorStatus(StrEnum):
    IN_PROGRESS = "В работе"
    READY = "Готово"
    NOT_READY = "Не готово"
    REVIEW = "Проверить"
    FIXED = "Исправлено"


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


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
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    external_id: str | None = Field(default=None, max_length=100)


class ClientRead(ORMModel):
    id: uuid.UUID
    name: str
    external_id: str | None
    is_active: bool


class ProjectCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    client_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    external_name: str | None = Field(default=None, max_length=255)


class ProjectRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    name: str
    external_name: str | None
    is_active: bool


class ResearchPayload(BaseModel):
    competitor_url: str | None = None
    competitor_category: str | None = None
    full_analysis: str | None = None
    performance_metrics: str | None = None
    transcription: str | None = None
    timeline: str | None = None
    why_viral: str | None = None
    takeaways: str | None = None
    improvements: str | None = None
    replication_template: str | None = None
    ai_analysis: str | None = None


class ContentPayload(BaseModel):
    claude_context: str | None = None
    cover_text: str | None = None
    script_text: str | None = None
    montage_brief: str | None = None
    scenarist_comment: str | None = None
    hook: str | None = None
    retention: str | None = None
    call_to_action: str | None = None
    visual_notes: str | None = None
    score_recommendations: str | None = None
    ai_review: str | None = None


class ScenarioCreate(BaseModel):
    project_id: uuid.UUID
    assigned_scenarist_id: uuid.UUID | None = None
    external_id: str | None = Field(default=None, max_length=100)
    source_sheet_id: str | None = None
    source_tab: str | None = None
    source_row: int | None = Field(default=None, ge=1)
    scenario_date: date | None = None
    deadline: date | None = None
    score: int | None = Field(default=None, ge=0, le=100)
    scenario_type: str | None = Field(default=None, max_length=100)
    visual_format: str | None = Field(default=None, max_length=255)
    speaker: str | None = Field(default=None, max_length=255)
    research: ResearchPayload | None = None
    content: ContentPayload | None = None


class ScenarioUpdate(BaseModel):
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


class ApprovalUpdate(BaseModel):
    decision: ApprovalDecision
    comment: str | None = None
    note: str | None = None


class ProjectSummary(ORMModel):
    id: uuid.UUID
    name: str
    client_name: str


class ScenaristSummary(ORMModel):
    id: uuid.UUID
    name: str
    initials: str


class CommentCreate(BaseModel):
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

    source_material_url: str | None = None
    client_brand_style: str | None = None
    extra_brief: str | None = None
    assigned_editor_id: uuid.UUID | None = None
    external_editor_name: str | None = None
    price: Decimal | None = Field(default=None, ge=0)
    payment_due_date: date | None = None
    material_status: str | None = None
    scenarist_material_comment: str | None = None
    brief_compliance_status: str | None = None
    ready_at: date | None = None
    bot_visual_analysis: str | None = None
    compliance_analysis: str | None = None
    ai_analysis: str | None = None
    scenarist_revision_status: str | None = None
    scenarist_revision_comment: str | None = None


class MontageRead(ORMModel):
    scenario_id: uuid.UUID
    source_material_url: str | None
    client_brand_style: str | None
    extra_brief: str | None
    assigned_editor_id: uuid.UUID | None
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

    ready_material_url: str | None = None
    editor_status: EditorStatus | None = None
    editor_comment: str | None = None


class PublicationUpdate(BaseModel):
    description_dzen: str | None = None
    description_youtube: str | None = None
    description_tiktok: str | None = None
    description_instagram: str | None = None
    publication_date: date | None = None
    is_published: bool | None = None
    publisher_brief: str | None = None
    instagram_url: str | None = None
    engagement_metrics: str | None = None
    publication_analysis: str | None = None
    ai_social_descriptions: str | None = None
    leia_script: str | None = None


class PublicationRead(ORMModel):
    scenario_id: uuid.UUID
    description_dzen: str | None
    description_youtube: str | None
    description_tiktok: str | None
    description_instagram: str | None
    publication_date: date | None
    is_published: bool
    first_published_at: datetime | None
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
    external_id: str | None
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
    external_id: str | None
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


class SheetCellChange(BaseModel):
    field: str = Field(min_length=1, max_length=100)
    value: Any = None


class SheetRowPatch(BaseModel):
    expected_version: datetime
    changes: list[SheetCellChange] = Field(min_length=1, max_length=50)


class SheetRowPatchResult(BaseModel):
    id: uuid.UUID
    version: datetime
    changed_fields: list[str]
