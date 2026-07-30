import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    FetchedValue,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from crm.database import Base


class Role(StrEnum):
    ADMIN = "admin"
    SCENARIST = "scenarist"
    MANAGER = "manager"
    EDITOR_MANAGER = "editor_manager"
    PUBLISHER_MANAGER = "publisher_manager"
    EDITOR = "editor"
    CLIENT = "client"
    PUBLISHER = "publisher"


class ScenarioStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    REVISION = "revision"
    REJECTED = "rejected"
    APPROVED = "approved"
    SENT_TO_GENERATION = "sent_to_generation"
    HANDED_TO_EDITOR = "handed_to_editor"
    EDITING = "editing"
    CLIENT_REVIEW = "client_review"
    MANAGER_REVISION_REVIEW = "manager_revision_review"
    READY_TO_PUBLISH = "ready_to_publish"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class ApprovalStage(StrEnum):
    RESPONSIBLE_REVIEW = "responsible_review"
    PRE_GENERATION_CLIENT = "pre_generation_client"
    SOURCE_MATERIAL = "source_material"
    MONTAGE_COMPLIANCE = "montage_compliance"
    FINAL_CLIENT = "final_client"


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REVISION = "revision"
    REJECTED = "rejected"


class GateDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PublicationReviewDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REVISION = "revision"


class PublisherStatus(StrEnum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    PUBLISHED = "published"


class GoogleSheetsSyncMode(StrEnum):
    PREVIEW = "preview"
    SYNC = "sync"


class GoogleSheetsSyncStatus(StrEnum):
    PREVIEW_READY = "preview_ready"
    COMPLETED = "completed"
    VALIDATION_FAILED = "validation_failed"
    FAILED = "failed"


class SheetEventStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class SheetWritebackStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role, name="role"), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id"), nullable=True)

    @property
    def name(self) -> str:
        return self.full_name

    @property
    def initials(self) -> str:
        return "".join(part[0].upper() for part in self.full_name.split()[:2] if part)


class Client(TimestampMixin, Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("external_id", name="uq_clients_external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    projects: Mapped[list["Project"]] = relationship(back_populates="client")


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("client_id", "name", name="uq_project_client_name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    external_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    client: Mapped[Client] = relationship(back_populates="projects")
    scenarios: Mapped[list["Scenario"]] = relationship(back_populates="project")

    @property
    def client_name(self) -> str:
        return self.client.name


class Scenario(TimestampMixin, Base):
    __tablename__ = "scenarios"
    __table_args__ = (
        UniqueConstraint("source_sheet_id", "source_tab", "external_id", name="uq_scenario_source"),
        UniqueConstraint(
            "source_sheet_id",
            "source_tab",
            "source_row",
            name="uq_scenario_source_row",
        ),
        UniqueConstraint("external_id", name="uq_scenarios_external_id"),
        UniqueConstraint(
            "sheet_source_id",
            "crm_row_id",
            name="uq_scenario_sheet_source_crm_row",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    assigned_scenarist_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    external_id: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True, server_default=FetchedValue()
    )
    source_sheet_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_tab: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sheet_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sheet_sources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    crm_row_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=uuid.uuid4)
    scenario_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scenario_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    visual_format: Mapped[str | None] = mapped_column(String(255), nullable=True)
    speaker: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[ScenarioStatus] = mapped_column(
        Enum(ScenarioStatus, name="scenario_status"),
        default=ScenarioStatus.DRAFT,
        index=True,
    )

    project: Mapped[Project] = relationship(back_populates="scenarios")
    assigned_scenarist: Mapped[User | None] = relationship()
    research: Mapped["ScenarioResearch | None"] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", uselist=False
    )
    content: Mapped["ScenarioContent | None"] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", uselist=False
    )
    approvals: Mapped[list["ScenarioApproval"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )
    comments: Mapped[list["ScenarioComment"]] = relationship(
        back_populates="scenario",
        cascade="all, delete-orphan",
        order_by="ScenarioComment.created_at",
    )
    montage: Mapped["MontageTask | None"] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", uselist=False
    )
    publication: Mapped["Publication | None"] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", uselist=False
    )
    final_revision_gate: Mapped["FinalClientRevisionGate | None"] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", uselist=False
    )

    @property
    def title(self) -> str:
        if self.content:
            return (
                self.content.cover_text or self.content.hook or self.external_id or "Без названия"
            )
        return self.external_id or "Без названия"

    @property
    def scenarist(self) -> User | None:
        return self.assigned_scenarist

    @property
    def comments_count(self) -> int:
        return len(self.comments)


class GoogleSheetsSyncRun(TimestampMixin, Base):
    __tablename__ = "google_sheets_sync_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    spreadsheet_id: Mapped[str] = mapped_column(String(255), index=True)
    source_tab: Mapped[str] = mapped_column(String(255), index=True)
    header_row: Mapped[int] = mapped_column(Integer)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    requested_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    preview_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("google_sheets_sync_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    mode: Mapped[GoogleSheetsSyncMode] = mapped_column(
        Enum(GoogleSheetsSyncMode, name="google_sheets_sync_mode"),
        index=True,
    )
    status: Mapped[GoogleSheetsSyncStatus] = mapped_column(
        Enum(GoogleSheetsSyncStatus, name="google_sheets_sync_status"),
        index=True,
    )
    snapshot_checksum: Mapped[str] = mapped_column(String(64))
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    row_report: Mapped[list[dict]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SheetSource(TimestampMixin, Base):
    __tablename__ = "sheet_sources"
    __table_args__ = (
        UniqueConstraint("spreadsheet_id", "source_tab", name="uq_sheet_source_location"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    spreadsheet_id: Mapped[str] = mapped_column(String(255), index=True)
    source_tab: Mapped[str] = mapped_column(String(255))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    assigned_scenarist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    header_row: Mapped[int] = mapped_column(Integer, default=1)
    inbound_column_map: Mapped[dict] = mapped_column(JSON, default=dict)
    writeback_column_map: Mapped[dict] = mapped_column(JSON, default=dict)
    crm_row_id_column: Mapped[str] = mapped_column(String(3), default="A")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    webhook_secret_version: Mapped[int] = mapped_column(Integer, default=1)
    last_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SheetInboundEvent(TimestampMixin, Base):
    __tablename__ = "sheet_inbound_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_sheet_inbound_event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(255), index=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sheet_sources.id", ondelete="CASCADE"), index=True
    )
    crm_row_id: Mapped[uuid.UUID] = mapped_column(index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    changed_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    origin: Mapped[str] = mapped_column(String(50))
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[SheetEventStatus] = mapped_column(
        Enum(SheetEventStatus, name="sheet_event_status"),
        default=SheetEventStatus.RECEIVED,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SheetWritebackEvent(TimestampMixin, Base):
    __tablename__ = "sheet_writeback_events"
    __table_args__ = (
        UniqueConstraint(
            "correlation_id",
            name="uq_sheet_writeback_correlation_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sheet_sources.id", ondelete="CASCADE"), index=True
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), index=True
    )
    crm_row_id: Mapped[uuid.UUID] = mapped_column(index=True)
    changed_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    origin: Mapped[str] = mapped_column(String(50), default="crm")
    correlation_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[SheetWritebackStatus] = mapped_column(
        Enum(SheetWritebackStatus, name="sheet_writeback_status"),
        default=SheetWritebackStatus.PENDING,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScenarioResearch(TimestampMixin, Base):
    __tablename__ = "scenario_research"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True
    )
    competitor_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    competitor_category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    performance_metrics: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcription: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeline: Mapped[str | None] = mapped_column(Text, nullable=True)
    why_viral: Mapped[str | None] = mapped_column(Text, nullable=True)
    takeaways: Mapped[str | None] = mapped_column(Text, nullable=True)
    improvements: Mapped[str | None] = mapped_column(Text, nullable=True)
    replication_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)

    scenario: Mapped[Scenario] = relationship(back_populates="research")


class ScenarioContent(TimestampMixin, Base):
    __tablename__ = "scenario_content"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True
    )
    claude_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    script_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    montage_brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    scenarist_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    retention: Mapped[str | None] = mapped_column(Text, nullable=True)
    call_to_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    visual_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_recommendations: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_review: Mapped[str | None] = mapped_column(Text, nullable=True)

    scenario: Mapped[Scenario] = relationship(back_populates="content")


class ScenarioApproval(TimestampMixin, Base):
    __tablename__ = "scenario_approvals"
    __table_args__ = (UniqueConstraint("scenario_id", "stage", name="uq_scenario_approval_stage"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[ApprovalStage] = mapped_column(Enum(ApprovalStage, name="approval_stage"))
    decision: Mapped[ApprovalDecision] = mapped_column(
        Enum(ApprovalDecision, name="approval_decision"), default=ApprovalDecision.PENDING
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    scenario: Mapped[Scenario] = relationship(back_populates="approvals")
    decided_by: Mapped[User | None] = relationship()


class ScenarioComment(Base):
    __tablename__ = "scenario_comments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    stage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    scenario: Mapped[Scenario] = relationship(back_populates="comments")
    author: Mapped[User] = relationship()


class FinalClientRevisionGate(TimestampMixin, Base):
    __tablename__ = "final_client_revision_gates"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True
    )
    decision: Mapped[GateDecision] = mapped_column(
        Enum(GateDecision, name="gate_decision"), default=GateDecision.PENDING, index=True
    )
    request_comment: Mapped[str] = mapped_column(Text)
    manager_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    scenario: Mapped[Scenario] = relationship(back_populates="final_revision_gate")
    decided_by: Mapped[User | None] = relationship()


class MontageTask(TimestampMixin, Base):
    __tablename__ = "montage_tasks"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True
    )
    source_material_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_brand_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_editor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    external_editor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    payment_due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    material_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scenarist_material_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    ready_material_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    editor_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    editor_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    brief_compliance_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ready_at: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    bot_visual_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    compliance_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    scenarist_revision_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scenarist_revision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    scenario: Mapped[Scenario] = relationship(back_populates="montage")
    assigned_editor: Mapped[User | None] = relationship()

    @property
    def assigned_editor_name(self) -> str | None:
        return self.assigned_editor.full_name if self.assigned_editor else None


class Publication(TimestampMixin, Base):
    __tablename__ = "publications"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True
    )
    description_dzen: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_youtube: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_tiktok: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_instagram: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    first_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assigned_publisher_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    manager_review_decision: Mapped[PublicationReviewDecision] = mapped_column(
        Enum(PublicationReviewDecision, name="publication_review_decision"),
        default=PublicationReviewDecision.PENDING,
        index=True,
    )
    manager_review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    manager_reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    manager_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    publisher_status: Mapped[PublisherStatus] = mapped_column(
        Enum(PublisherStatus, name="publisher_status"),
        default=PublisherStatus.PENDING,
        index=True,
    )
    publisher_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    dzen_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    youtube_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    tiktok_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publisher_brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    instagram_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    engagement_metrics: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_social_descriptions: Mapped[str | None] = mapped_column(Text, nullable=True)
    leia_script: Mapped[str | None] = mapped_column(Text, nullable=True)

    scenario: Mapped[Scenario] = relationship(back_populates="publication")
    assigned_publisher: Mapped[User | None] = relationship(
        foreign_keys=[assigned_publisher_id]
    )
    manager_reviewed_by: Mapped[User | None] = relationship(
        foreign_keys=[manager_reviewed_by_id]
    )

    @property
    def assigned_publisher_name(self) -> str | None:
        return self.assigned_publisher.full_name if self.assigned_publisher else None
