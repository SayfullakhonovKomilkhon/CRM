import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from crm.models import (
    ApprovalDecision,
    ApprovalStage,
    GateDecision,
    GoogleSheetsSyncMode,
    GoogleSheetsSyncStatus,
    PublicationPreparationStatus,
    PublicationReviewDecision,
    PublisherStatus,
    Role,
    ScenarioStatus,
    SheetEventStatus,
    SheetWritebackStatus,
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


class UserAdminRead(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    client_id: uuid.UUID | None
    is_active: bool
    created_at: datetime


class UserAdminCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    role: Role
    client_id: uuid.UUID | None = None
    password: str = Field(min_length=8, max_length=1024)


class UserAdminUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    role: Role | None = None
    client_id: uuid.UUID | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=1024)

    @model_validator(mode="after")
    def reject_null_required_values(self):
        for field_name in ("full_name", "role", "is_active", "password"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class GoogleSheetsRowAction(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    SKIPPED = "skipped"
    ERROR = "error"


class GoogleSheetsTabStatus(BaseModel):
    tab: str
    header_row: int
    project_id: uuid.UUID
    assigned_scenarist_id: uuid.UUID | None


class GoogleSheetsSyncRunRead(ORMModel):
    id: uuid.UUID
    mode: GoogleSheetsSyncMode
    status: GoogleSheetsSyncStatus
    source_tab: str
    header_row: int
    project_id: uuid.UUID
    preview_id: uuid.UUID | None
    total_rows: int
    created_count: int
    updated_count: int
    skipped_count: int
    error_count: int
    warnings: list[str]
    finished_at: datetime | None
    created_at: datetime


class GoogleSheetsStatusRead(BaseModel):
    enabled: bool
    ready: bool
    credential_configured: bool
    spreadsheet_configured: bool
    configured_tabs: list[GoogleSheetsTabStatus]
    safe_import_fields: list[str]
    missing_requirements: list[str]
    last_run: GoogleSheetsSyncRunRead | None


class GoogleSheetsPreviewRequest(WriteModel):
    tab: str = Field(min_length=1, max_length=255)


class GoogleSheetsSyncRequest(WriteModel):
    tab: str = Field(min_length=1, max_length=255)
    preview_id: uuid.UUID
    confirm: Literal[True]


class GoogleSheetsRowResult(BaseModel):
    row_number: int
    action: GoogleSheetsRowAction
    checksum: str | None = None
    scenario_id: uuid.UUID | None = None
    external_id: str | None = None
    title: str | None = None
    errors: list[str] = Field(default_factory=list)


class GoogleSheetsRunSummary(BaseModel):
    total_rows: int
    created: int
    updated: int
    skipped: int
    errors: int


class GoogleSheetsPreviewRead(BaseModel):
    preview_id: uuid.UUID
    status: GoogleSheetsSyncStatus
    source_tab: str
    header_row: int
    project_id: uuid.UUID
    snapshot_checksum: str
    expires_at: datetime
    summary: GoogleSheetsRunSummary
    rows: list[GoogleSheetsRowResult]
    warnings: list[str]


class GoogleSheetsSyncRead(BaseModel):
    run_id: uuid.UUID
    preview_id: uuid.UUID
    status: GoogleSheetsSyncStatus
    source_tab: str
    project_id: uuid.UUID
    snapshot_checksum: str
    written: bool
    summary: GoogleSheetsRunSummary
    rows: list[GoogleSheetsRowResult]
    warnings: list[str]


class SheetSourceCreate(WriteModel):
    spreadsheet_id: str = Field(min_length=1, max_length=255)
    source_tab: str = Field(min_length=1, max_length=255)
    project_id: uuid.UUID
    assigned_scenarist_id: uuid.UUID
    header_row: int = Field(default=1, ge=1, le=10_000)
    inbound_column_map: dict[str, int | str] = Field(default_factory=dict)
    writeback_column_map: dict[str, int | str] = Field(default_factory=dict)
    crm_row_id_column: str = Field(default="A", pattern=r"^[A-Za-z]{1,3}$")
    enabled: bool = True


class SheetSourceUpdate(WriteModel):
    source_tab: str | None = Field(default=None, min_length=1, max_length=255)
    project_id: uuid.UUID | None = None
    assigned_scenarist_id: uuid.UUID | None = None
    header_row: int | None = Field(default=None, ge=1, le=10_000)
    inbound_column_map: dict[str, int | str] | None = None
    writeback_column_map: dict[str, int | str] | None = None
    crm_row_id_column: str | None = Field(default=None, pattern=r"^[A-Za-z]{1,3}$")
    enabled: bool | None = None


class SheetSourceRead(ORMModel):
    id: uuid.UUID
    spreadsheet_id: str
    source_tab: str
    project_id: uuid.UUID
    assigned_scenarist_id: uuid.UUID
    header_row: int
    inbound_column_map: dict[str, int | str]
    writeback_column_map: dict[str, int | str]
    crm_row_id_column: str
    enabled: bool
    last_status: str | None
    last_error: str | None
    last_event_at: datetime | None
    last_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SheetSourceCreated(SheetSourceRead):
    webhook_secret: str


class SheetWebhookEvent(WriteModel):
    event_id: str = Field(min_length=1, max_length=255)
    schema_version: Literal[1]
    row_id: uuid.UUID
    row_number: int = Field(ge=1)
    changed_fields: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)
    checksum: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    origin: Literal["sheets", "crm"]
    correlation_id: str | None = Field(default=None, max_length=255)


class SheetWebhookAccepted(BaseModel):
    event_id: str
    status: SheetEventStatus
    duplicate: bool
    queued: bool
    error: str | None = None


class SheetInboundEventRead(ORMModel):
    id: uuid.UUID
    event_id: str
    schema_version: int
    source_id: uuid.UUID
    crm_row_id: uuid.UUID
    row_number: int
    changed_fields: dict[str, Any]
    checksum: str
    origin: str
    correlation_id: str | None
    status: SheetEventStatus
    attempts: int
    error: str | None
    processed_at: datetime | None
    created_at: datetime


class SheetWritebackEventRead(ORMModel):
    id: uuid.UUID
    source_id: uuid.UUID
    scenario_id: uuid.UUID
    crm_row_id: uuid.UUID
    changed_fields: dict[str, Any]
    checksum: str
    origin: str
    correlation_id: str
    status: SheetWritebackStatus
    attempts: int
    error: str | None
    next_attempt_at: datetime | None
    processed_at: datetime | None
    created_at: datetime


class SheetReconcileRequest(WriteModel):
    apply: bool = False


class SheetReconcileRead(BaseModel):
    source_id: uuid.UUID
    mode: Literal["preview"]
    manual_preview_endpoint: str
    tab: str
    ready: bool
    message: str


class ClientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    external_id: str | None = Field(default=None, max_length=100)


class ClientRead(ORMModel):
    id: uuid.UUID
    name: str
    external_id: str | None
    is_active: bool


class ClientWithAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    external_id: str | None = Field(default=None, max_length=100)
    account_full_name: str = Field(min_length=1, max_length=255)
    account_email: EmailStr
    account_password: str = Field(min_length=8, max_length=1024)


class ClientWithAccountRead(BaseModel):
    client: ClientRead
    user: UserAdminRead


class ClientUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    external_id: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None

    @model_validator(mode="after")
    def reject_null_required_values(self):
        for field_name in ("name", "is_active"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


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


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    external_name: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None

    @model_validator(mode="after")
    def reject_null_required_values(self):
        for field_name in ("name", "is_active"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


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
    publication_date: date | None = None
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
    preparation_status: PublicationPreparationStatus = PublicationPreparationStatus.DRAFT
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


class ScenarioQueue(StrEnum):
    SCENARIO_MANAGER_REVIEW = "scenario_manager_review"
    EDITOR_MANAGER_INTAKE = "editor_manager_intake"
    EDITOR_MANAGER_INWORK = "editor_manager_inwork"
    EDITOR_MANAGER_CHECK = "editor_manager_check"
    EDITOR_MANAGER_REWORK_REVIEW = "editor_manager_rework_review"
    PUBLISHER_MANAGER_QUEUE = "publisher_manager_queue"
    PUBLISHER_MANAGER_INWORK = "publisher_manager_inwork"


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
    status: ScenarioStatus
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
