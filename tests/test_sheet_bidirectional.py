import inspect
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from crm.config import Settings
from crm.google_sheets import (
    SAFE_IMPORT_FIELDS,
    GoogleSheetsClient,
    GoogleSheetsSourceError,
    canonical_checksum,
    unique_sheet_title,
)
from crm.main import app
from crm.models import (
    ApprovalDecision,
    ApprovalStage,
    Publication,
    PublicationPreparationStatus,
    PublicationReviewDecision,
    PublisherStatus,
    Role,
    Scenario,
    ScenarioApproval,
    ScenarioStatus,
    SheetEventStatus,
    SheetSource,
    SheetWritebackStatus,
)
from crm.routers import scenarios as scenario_routes
from crm.schemas import ScenarioCreate, SheetWebhookEvent
from crm.sheet import SHEET_FIELDS
from crm.sheet_mapping import (
    CANONICAL_WRITEBACK_COLUMN_MAP,
    MANAGED_EXTENSION_HEADERS,
    effective_writeback_column_map,
)
from crm.sheet_sync import (
    CRM_OWNED_WRITEBACK_FIELDS,
    REALTIME_SERVER_CONTROLLED_FIELDS,
    WRITEBACK_FIELDS,
    WRITEBACK_ID_FIELDS,
    _set_values,
    active_scenarist_revision_stage,
    append_row_values,
    column_letters,
    column_number,
    crm_owned_writeback_snapshot,
    enqueue_sheet_writeback,
    inbound_update_allowed,
    process_inbound_event,
    process_writeback_event,
    sheet_cell_value,
    source_metadata_matches,
    source_webhook_secret,
    submission_requested,
    validate_column_map,
    verify_webhook,
    webhook_signature,
)


def approval(stage, decision):
    return SimpleNamespace(
        stage=stage,
        decision=decision,
        decided_by_id=uuid.uuid4(),
        decided_at=datetime.now(UTC),
    )


def scenario_in_revision(stage):
    approvals = []
    if stage != ApprovalStage.RESPONSIBLE_REVIEW:
        approvals.append(
            approval(ApprovalStage.RESPONSIBLE_REVIEW, ApprovalDecision.APPROVED)
        )
    approvals.extend(
        [
            approval(stage, ApprovalDecision.REVISION),
            approval(ApprovalStage.SOURCE_MATERIAL, ApprovalDecision.APPROVED),
        ]
    )
    return SimpleNamespace(
        status=ScenarioStatus.REVISION,
        approvals=approvals,
        content=SimpleNamespace(script_text="new script"),
        montage=None,
        publication=None,
        final_revision_gate=None,
    )


@pytest.mark.parametrize(
    "stage",
    [ApprovalStage.RESPONSIBLE_REVIEW, ApprovalStage.PRE_GENERATION_CLIENT],
)
def test_scenarist_revision_accepts_sheet_edit_without_automatic_resubmit(stage):
    value = scenario_in_revision(stage)

    assert active_scenarist_revision_stage(value) == stage
    assert inbound_update_allowed(value) is True
    assert value.status == ScenarioStatus.REVISION
    assert next(item for item in value.approvals if item.stage == stage).decision == (
        ApprovalDecision.REVISION
    )


def test_realtime_sheet_edits_cannot_submit_source_material() -> None:
    assert "montage.material_status" in REALTIME_SERVER_CONTROLLED_FIELDS


@pytest.mark.parametrize(
    "status",
    [
        ScenarioStatus.CLIENT_REVIEW,
        ScenarioStatus.SENT_TO_GENERATION,
        ScenarioStatus.EDITING,
        ScenarioStatus.APPROVED,
        ScenarioStatus.PUBLISHED,
    ],
)
def test_approved_or_production_stage_blocks_sheet_source_update(status):
    value = scenario_in_revision(ApprovalStage.PRE_GENERATION_CLIENT)
    value.status = status
    assert inbound_update_allowed(value) is False


def test_inbound_values_use_scenario_validation_before_mutation():
    value = Scenario(
        project_id=uuid.uuid4(),
        assigned_scenarist_id=uuid.uuid4(),
        external_id="test",
    )
    with pytest.raises(ValidationError, match="less than or equal to 100"):
        _set_values(value, {"score": 101})
    assert value.score is None


def test_inbound_coercion_error_identifies_the_source_field():
    value = Scenario(
        project_id=uuid.uuid4(),
        assigned_scenarist_id=uuid.uuid4(),
    )

    with pytest.raises(ValueError, match="scenario_date: expected date"):
        _set_values(value, {"scenario_date": "not-a-date"})


def test_partial_sheet_row_can_create_and_later_clear_one_field():
    value = Scenario(
        project_id=uuid.uuid4(),
        assigned_scenarist_id=uuid.uuid4(),
    )

    _set_values(value, {"speaker": "Только спикер заполнен"})
    assert value.speaker == "Только спикер заполнен"
    assert value.content is None
    assert value.research is None

    _set_values(value, {"speaker": None})
    assert value.speaker is None


def test_apps_script_syncs_full_partial_rows_and_has_recovery_trigger():
    script = (
        Path(__file__).parents[1] / "integrations/google_apps_script/Code.gs"
    ).read_text()

    assert "fullRowFields_" in script
    assert "includeEmptySourceFields: hasRecoverableIdentity" in script
    assert (
        'const hasRecoverableIdentity = hasExistingIdentity || visibleExternalId !== ""'
        in script
    )
    assert "publicationSubmitRequested = !submissionRequested && hasRecoverableIdentity" in script
    assert "rowIdAppearsEarlier_" in script
    assert "One malformed or workflow-locked row must not block rows below it." in script
    assert "const CRM_SCENARIST_INBOUND_FIELDS = new Set([" in script
    assert '"scenarist.name",' in script
    assert "const CRM_CANONICAL_INBOUND_COLUMN_MAP = {" in script
    assert '"57": "publication.publication_date"' in script
    assert '"62": "publication.description_instagram"' in script
    assert "CRM_PUBLICATION_AUTO_SUBMIT_FIELDS" in script
    assert "rowHasPublicationReadyContent_" in script
    assert "ensurePublishedCheckboxes_" in script
    assert "requireCheckbox()" in script
    assert "canonicalLayout ? CRM_CANONICAL_INBOUND_COLUMN_MAP : {}" in script
    assert '"external_id"' in script
    assert '"content.script_text"' in script
    assert '"montage.source_material_url"' in script
    assert '"approval.responsible_review.decision"' not in script
    assert "!CRM_SCENARIST_INBOUND_FIELDS.has(field)" in script
    assert 'value === "" && !options.includeEmptySourceFields' in script
    assert "normalizedSheetDate_(" in script
    assert 'Object.prototype.toString.call(rawValue) === "[object Date]"' in script
    assert "numericValue - 25569" in script
    assert "ensureRowDateFormats_(sheet, rowNumber, map)" in script
    assert 'cell.setNumberFormat("yyyy-mm-dd")' in script
    assert '"yyyy-MM-dd"' in script
    assert 'newTrigger(CRM_RECONCILE_HANDLER).timeBased().everyMinutes(5)' in script
    assert 'response.status === "failed"' in script
    assert 'const CRM_SUBMISSION_HEADER = "Отправка на согласование"' in script
    assert 'const CRM_LIVE_SOURCE_FIELDS = new Set([' in script
    assert 'const CRM_LIVE_PUBLICATION_FIELDS = new Set([' in script
    assert '"source_material_submit"' in script
    assert '"publication_submit"' in script
    assert "sync_mode: syncMode" in script
    assert 'sourceSubmissionCell.setValue("ready_for_review")' in script
    assert 'publicationSubmissionCell.setValue("ready_for_review")' in script
    assert "if (submissionRequested) submissionCell.clearContent()" in script
    assert "isSubmissionRequested_" in script
    assert "submissionCell.clearContent()" in script
    assert "if (!hasExistingIdentity && !hasMeaningfulFields_(fields))" not in script


def test_realtime_inbound_requires_explicit_marker_for_every_row():
    assert submission_requested("Отправить") is True
    assert submission_requested("Черновик") is False
    source = inspect.getsource(process_inbound_event)
    assert 'event.raw.get("submission_status")' in source
    assert "Sheet row is not marked 'Отправить'" in source
    assert "if not submit_requested" in source


def test_explicit_submission_accepts_a_row_with_no_other_values():
    event = SheetWebhookEvent(
        event_id="marker-only",
        schema_version=1,
        row_id=uuid.uuid4(),
        row_number=5,
        changed_fields={},
        raw={
            "spreadsheet_id": "sheet-1",
            "tab": "сценарий",
            "submission_status": "Отправить",
        },
        checksum=canonical_checksum({}),
        origin="sheets",
    )

    assert event.changed_fields == {}


async def test_submitted_partial_row_enters_manager_queue_and_draft_is_skipped():
    source_id = uuid.uuid4()
    project_id = uuid.uuid4()
    scenarist_id = uuid.uuid4()
    source = SimpleNamespace(
        id=source_id,
        enabled=True,
        spreadsheet_id="sheet-1",
        source_tab="сценарий",
        project_id=project_id,
        assigned_scenarist_id=scenarist_id,
        inbound_column_map={"external_id": "B", "speaker": "C"},
        last_status=None,
        last_error=None,
        last_event_at=None,
    )

    class InboundSession:
        def __init__(
            self,
            event,
            existing=None,
            legacy_existing=None,
            recovered_existing=None,
        ):
            self.event = event
            self.existing = existing
            self.legacy_existing = legacy_existing
            self.recovered_existing = recovered_existing
            self.scalar_calls = 0
            self.added = []

        async def scalar(self, _query):
            self.scalar_calls += 1
            if self.scalar_calls == 1:
                return self.event
            if self.scalar_calls == 2:
                return self.existing
            if self.scalar_calls == 3:
                return self.legacy_existing
            if self.scalar_calls == 4:
                return self.recovered_existing
            return None

        async def get(self, model, _identifier):
            assert model is SheetSource
            return source

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

        def add(self, value):
            self.added.append(value)

        async def flush(self):
            return None

    def inbound_event(
        submission_status,
        changed_fields=None,
        row_id=None,
        sync_mode=None,
    ):
        changed_fields = changed_fields or {}
        return SimpleNamespace(
            id=uuid.uuid4(),
            status=SheetEventStatus.RECEIVED,
            attempts=0,
            source_id=source_id,
            crm_row_id=row_id or uuid.uuid4(),
            row_number=5,
            changed_fields=changed_fields,
            raw={
                "spreadsheet_id": "sheet-1",
                "tab": "сценарий",
                "submission_status": submission_status,
                "sync_mode": sync_mode,
            },
            checksum=canonical_checksum(changed_fields),
            origin="sheets",
            error=None,
            processed_at=None,
        )

    submitted_session = InboundSession(
        inbound_event("Отправить", {"external_id": "147"})
    )
    submitted = await process_inbound_event(
        submitted_session,
        submitted_session.event.id,
    )
    created = next(
        item for item in submitted_session.added if isinstance(item, Scenario)
    )
    assert submitted.status == SheetEventStatus.COMPLETED
    assert created.status == ScenarioStatus.IN_REVIEW
    assert created.external_id == "147"
    assert created.project_id == project_id
    assert created.assigned_scenarist_id == scenarist_id

    repeated_display_id_session = InboundSession(
        inbound_event("Отправить", {"external_id": "147"}),
    )
    repeated_display_id = await process_inbound_event(
        repeated_display_id_session,
        repeated_display_id_session.event.id,
    )
    repeated_scenario = next(
        item
        for item in repeated_display_id_session.added
        if isinstance(item, Scenario)
    )
    assert repeated_display_id.status == SheetEventStatus.COMPLETED
    assert repeated_scenario.external_id == "147"

    draft_session = InboundSession(inbound_event(""))
    skipped = await process_inbound_event(draft_session, draft_session.event.id)
    assert skipped.status == SheetEventStatus.SKIPPED
    assert draft_session.added == []

    existing_row_id = uuid.uuid4()
    existing = Scenario(
        project_id=project_id,
        assigned_scenarist_id=scenarist_id,
        sheet_source_id=source_id,
        crm_row_id=existing_row_id,
        source_checksum="old",
        status=ScenarioStatus.DRAFT,
    )
    edit_session = InboundSession(
        inbound_event(
            "",
            {"speaker": "Обновлено в Google"},
            existing_row_id,
        ),
        existing,
    )
    skipped_existing = await process_inbound_event(
        edit_session,
        edit_session.event.id,
    )
    assert skipped_existing.status == SheetEventStatus.SKIPPED
    assert existing.speaker is None
    assert existing.status == ScenarioStatus.DRAFT

    source.inbound_column_map["montage.source_material_url"] = "AK"
    premature_source_session = InboundSession(
        inbound_event(
            "",
            {"montage.source_material_url": "https://example.com/too-early"},
            existing_row_id,
            "scenarist_live_update",
        ),
        existing,
    )
    premature_source_result = await process_inbound_event(
        premature_source_session,
        premature_source_session.event.id,
    )
    assert premature_source_result.status == SheetEventStatus.FAILED
    assert "client must approve" in premature_source_result.error
    assert existing.montage is None

    live_row_id = uuid.uuid4()
    live_existing = Scenario(
        project_id=project_id,
        assigned_scenarist_id=scenarist_id,
        sheet_source_id=source_id,
        crm_row_id=live_row_id,
        source_checksum="live-old",
        status=ScenarioStatus.SENT_TO_GENERATION,
    )
    live_existing.approvals.append(
        ScenarioApproval(
            stage=ApprovalStage.PRE_GENERATION_CLIENT,
            decision=ApprovalDecision.APPROVED,
        )
    )
    live_session = InboundSession(
        inbound_event(
            "",
            {"montage.source_material_url": "https://example.com/source"},
            live_row_id,
            "scenarist_live_update",
        ),
        live_existing,
    )
    live_result = await process_inbound_event(
        live_session,
        live_session.event.id,
    )
    assert live_result.status == SheetEventStatus.COMPLETED
    assert live_existing.montage.source_material_url == "https://example.com/source"
    assert live_existing.montage.material_status.value == "draft"
    assert live_existing.status == ScenarioStatus.SENT_TO_GENERATION

    regenerated_row_id = uuid.uuid4()
    live_existing.external_id = "20260802901"
    live_existing.sheet_source_id = uuid.uuid4()
    live_existing.source_tab = "сценарий"
    recovered_session = InboundSession(
        inbound_event(
            "",
            {
                "external_id": "20260802901",
                "montage.source_material_url": "https://example.com/recovered-source",
            },
            regenerated_row_id,
            "scenarist_live_update",
        ),
        recovered_existing=live_existing,
    )
    recovered_result = await process_inbound_event(
        recovered_session,
        recovered_session.event.id,
    )
    assert recovered_result.status == SheetEventStatus.COMPLETED
    assert live_existing.crm_row_id == regenerated_row_id
    assert live_existing.sheet_source_id == source_id
    assert live_existing.source_sheet_id == "sheet-1"
    assert live_existing.source_tab == "сценарий"
    assert live_existing.source_row == 5
    assert (
        live_existing.montage.source_material_url
        == "https://example.com/recovered-source"
    )

    source_submit_session = InboundSession(
        inbound_event(
            "",
            {},
            live_row_id,
            "source_material_submit",
        ),
        live_existing,
    )
    source_submit_result = await process_inbound_event(
        source_submit_session,
        source_submit_session.event.id,
    )
    assert source_submit_result.status == SheetEventStatus.COMPLETED
    assert live_existing.montage.material_status.value == "ready_for_review"

    source.inbound_column_map["publication.description_youtube"] = "BH"
    publication_row_id = uuid.uuid4()
    publication_existing = Scenario(
        project_id=project_id,
        assigned_scenarist_id=scenarist_id,
        sheet_source_id=source_id,
        crm_row_id=publication_row_id,
        source_checksum="publication-old",
        status=ScenarioStatus.APPROVED,
        publication=Publication(
            preparation_status=PublicationPreparationStatus.REVISION.value,
            manager_review_decision=PublicationReviewDecision.REVISION,
        ),
    )
    publication_existing.approvals.append(
        ScenarioApproval(
            stage=ApprovalStage.FINAL_CLIENT,
            decision=ApprovalDecision.APPROVED,
        )
    )
    publication_live_session = InboundSession(
        inbound_event(
            "",
            {"publication.description_youtube": "Описание из Google"},
            publication_row_id,
            "scenarist_live_update",
        ),
        publication_existing,
    )
    publication_live_result = await process_inbound_event(
        publication_live_session,
        publication_live_session.event.id,
    )
    assert publication_live_result.status == SheetEventStatus.COMPLETED
    assert publication_existing.publication.description_youtube == "Описание из Google"
    assert (
        publication_existing.publication.preparation_status
        == PublicationPreparationStatus.DRAFT.value
    )
    assert (
        publication_existing.publication.manager_review_decision
        == PublicationReviewDecision.PENDING
    )

    publication_submit_session = InboundSession(
        inbound_event(
            "",
            {},
            publication_row_id,
            "publication_submit",
        ),
        publication_existing,
    )
    publication_submit_result = await process_inbound_event(
        publication_submit_session,
        publication_submit_session.event.id,
    )
    assert publication_submit_result.status == SheetEventStatus.COMPLETED
    assert (
        publication_existing.publication.preparation_status
        == PublicationPreparationStatus.READY_FOR_REVIEW.value
    )

    # Backward compatibility: project tabs can still run an older Apps Script
    # that submits a complete row snapshot for a publication action.  Locked
    # scenario fields in that snapshot must be ignored rather than rejecting
    # the publication fields that belong to the current stage.
    source.inbound_column_map["speaker"] = "R"
    legacy_full_snapshot_session = InboundSession(
        inbound_event(
            "",
            {
                "external_id": "20260802901",
                "speaker": "Не должно перезаписаться",
                "publication.description_youtube": "Полный снимок старого скрипта",
            },
            publication_row_id,
            "publication_submit",
        ),
        publication_existing,
    )
    legacy_full_snapshot_result = await process_inbound_event(
        legacy_full_snapshot_session,
        legacy_full_snapshot_session.event.id,
    )
    assert legacy_full_snapshot_result.status == SheetEventStatus.COMPLETED
    assert publication_existing.speaker is None
    assert (
        publication_existing.publication.description_youtube
        == "Полный снимок старого скрипта"
    )
    assert (
        publication_existing.publication.preparation_status
        == PublicationPreparationStatus.READY_FOR_REVIEW.value
    )

    source.inbound_column_map["publication.publication_date"] = "BE"
    legacy_without_mode_session = InboundSession(
        inbound_event(
            "",
            {
                "external_id": "20260802901",
                "speaker": "Сценарное поле остаётся заблокированным",
                "publication.publication_date": "2026-08-16",
                "publication.description_youtube": "Снимок без sync_mode",
            },
            publication_row_id,
        ),
        publication_existing,
    )
    legacy_without_mode_result = await process_inbound_event(
        legacy_without_mode_session,
        legacy_without_mode_session.event.id,
    )
    assert legacy_without_mode_result.status == SheetEventStatus.COMPLETED
    assert publication_existing.speaker is None
    assert str(publication_existing.publication.publication_date) == "2026-08-16"
    assert (
        publication_existing.publication.description_youtube
        == "Снимок без sync_mode"
    )

    legacy_invalid_mode_session = InboundSession(
        inbound_event(
            "Отправить",
            {
                "external_id": "20260802901",
                "speaker": "Также не должно перезаписаться",
                "publication.publication_date": "2026-08-17",
                "publication.description_youtube": "Полный снимок со старой меткой",
            },
            publication_row_id,
            "submission",
        ),
        publication_existing,
    )
    legacy_invalid_mode_result = await process_inbound_event(
        legacy_invalid_mode_session,
        legacy_invalid_mode_session.event.id,
    )
    assert legacy_invalid_mode_result.status == SheetEventStatus.COMPLETED
    assert publication_existing.speaker is None
    assert str(publication_existing.publication.publication_date) == "2026-08-17"
    assert (
        publication_existing.publication.description_youtube
        == "Полный снимок со старой меткой"
    )

    source.inbound_column_map["publication.publication_date"] = "BE"
    date_only_row_id = uuid.uuid4()
    date_only_existing = Scenario(
        project_id=project_id,
        assigned_scenarist_id=scenarist_id,
        sheet_source_id=source_id,
        crm_row_id=date_only_row_id,
        source_checksum="date-only-old",
        status=ScenarioStatus.APPROVED,
        publication=Publication(),
    )
    date_only_existing.approvals.append(
        ScenarioApproval(
            stage=ApprovalStage.FINAL_CLIENT,
            decision=ApprovalDecision.APPROVED,
        )
    )
    date_only_session = InboundSession(
        inbound_event(
            "",
            {"publication.publication_date": "2026-08-15"},
            date_only_row_id,
            "publication_submit",
        ),
        date_only_existing,
    )
    date_only_result = await process_inbound_event(
        date_only_session,
        date_only_session.event.id,
    )
    assert date_only_result.status == SheetEventStatus.COMPLETED
    assert str(date_only_existing.publication.publication_date) == "2026-08-15"
    assert (
        date_only_existing.publication.preparation_status
        == PublicationPreparationStatus.READY_FOR_REVIEW.value
    )

    legacy_row_id = uuid.uuid4()
    legacy_existing = Scenario(
        project_id=project_id,
        assigned_scenarist_id=scenarist_id,
        sheet_source_id=source_id,
        crm_row_id=None,
        source_row=5,
        source_checksum="legacy-old",
        status=ScenarioStatus.SENT_TO_GENERATION,
    )
    legacy_existing.approvals.append(
        ScenarioApproval(
            stage=ApprovalStage.PRE_GENERATION_CLIENT,
            decision=ApprovalDecision.APPROVED,
        )
    )
    legacy_session = InboundSession(
        inbound_event(
            "",
            {"montage.source_material_url": "https://example.com/legacy-source"},
            legacy_row_id,
            "scenarist_live_update",
        ),
        legacy_existing=legacy_existing,
    )
    legacy_result = await process_inbound_event(
        legacy_session,
        legacy_session.event.id,
    )
    assert legacy_result.status == SheetEventStatus.COMPLETED
    assert legacy_existing.crm_row_id == legacy_row_id
    assert (
        legacy_existing.montage.source_material_url
        == "https://example.com/legacy-source"
    )

    source.inbound_column_map = {
        "external_id": "B",
        "speaker": "C",
        "approval.responsible_review.decision": "D",
    }
    legacy_row_id = uuid.uuid4()
    legacy_existing = Scenario(
        project_id=project_id,
        assigned_scenarist_id=scenarist_id,
        sheet_source_id=source_id,
        crm_row_id=legacy_row_id,
        source_checksum="older",
        status=ScenarioStatus.DRAFT,
    )
    legacy_session = InboundSession(
        inbound_event(
            "Отправить",
            {
                "speaker": "Разрешённое изменение",
                "approval.responsible_review.decision": "approved",
            },
            legacy_row_id,
        ),
        legacy_existing,
    )
    legacy_result = await process_inbound_event(
        legacy_session,
        legacy_session.event.id,
    )
    assert legacy_result.status == SheetEventStatus.COMPLETED
    assert legacy_existing.speaker == "Разрешённое изменение"
    assert legacy_existing.approvals == []
    assert legacy_existing.source_payload["last_ignored_sheet_fields"] == [
        "approval.responsible_review.decision"
    ]


def test_inbound_allowlist_accepts_scenarist_fields_and_rejects_role_workflow():
    validate_column_map(
        {
            "external_id": "B",
            "content.script_text": "C",
            "montage.source_material_url": "D",
            "publication.publisher_brief": "E",
        },
        allowed_fields=SAFE_IMPORT_FIELDS,
    )
    for role_owned_field in (
        "approval.responsible_review.decision",
        "montage.ready_material_url",
        "montage.price",
        "publication.is_published",
        "publication.publisher_status",
    ):
        with pytest.raises(HTTPException, match="Unsupported mapped fields"):
            validate_column_map(
                {role_owned_field: "B"},
                allowed_fields=SAFE_IMPORT_FIELDS,
            )


def test_inbound_update_eager_loads_mutable_scenario_relationships():
    source = inspect.getsource(process_inbound_event)
    for relationship in (
        "research",
        "content",
        "approvals",
        "montage",
        "publication",
        "final_revision_gate",
    ):
        assert f"selectinload(Scenario.{relationship})" in source


def test_webhook_hmac_timestamp_and_wrong_signature():
    now = datetime.now(UTC)
    body = b'{"event_id":"one"}'
    timestamp = str(int(now.timestamp()))
    signature = webhook_signature("secret", timestamp, body)
    verify_webhook(
        secret="secret",
        timestamp=timestamp,
        signature=signature,
        body=body,
        now=now,
        max_age_seconds=300,
    )
    with pytest.raises(HTTPException) as wrong:
        verify_webhook(
            secret="secret",
            timestamp=timestamp,
            signature="sha256=bad",
            body=body,
            now=now,
            max_age_seconds=300,
        )
    assert wrong.value.status_code == 401
    with pytest.raises(HTTPException, match="expired"):
        verify_webhook(
            secret="secret",
            timestamp=timestamp,
            signature=signature,
            body=body,
            now=now + timedelta(minutes=10),
            max_age_seconds=300,
        )


def test_source_secret_is_per_source_and_rotatable():
    settings = Settings(_env_file=None, app_secret_key="x" * 32)
    first = SimpleNamespace(id=uuid.uuid4(), webhook_secret_version=1)
    second = SimpleNamespace(id=uuid.uuid4(), webhook_secret_version=1)
    assert source_webhook_secret(settings, first) != source_webhook_secret(settings, second)
    original = source_webhook_secret(settings, first)
    first.webhook_secret_version = 2
    assert source_webhook_secret(settings, first) != original


def test_source_metadata_and_multifield_contract():
    source = SimpleNamespace(spreadsheet_id="sheet-1", source_tab="Сценарист")
    assert source_metadata_matches(
        source,
        {"spreadsheet_id": "sheet-1", "tab": "Сценарист", "a1": "B3:D4"},
    )
    assert not source_metadata_matches(
        source,
        {"spreadsheet_id": "other", "tab": "Сценарист"},
    )
    changed_fields = {
        "content.script_text": "Script",
        "content.cover_text": "Cover",
        "score": 90,
    }
    assert len(changed_fields) == 3


def test_identity_column_is_separate_from_workflow_allowlist():
    assert "crm_row_id" not in WRITEBACK_FIELDS
    assert column_letters(1) == "A"
    assert column_letters(27) == "AA"
    assert column_number("CA") == 79
    with pytest.raises(HTTPException):
        validate_column_map(
            {"workflow.not_allowed": "B"},
            allowed_fields=WRITEBACK_FIELDS,
        )


def test_canonical_role_writeback_map_is_complete_and_keeps_bz_for_identity():
    assert CANONICAL_WRITEBACK_COLUMN_MAP["external_id"] == "B"
    assert CANONICAL_WRITEBACK_COLUMN_MAP["content.script_text"] == "T"
    assert CANONICAL_WRITEBACK_COLUMN_MAP["approval.final_client.decision"] == "BC"
    assert CANONICAL_WRITEBACK_COLUMN_MAP["publication.publisher_status"] == "CL"
    assert "BZ" not in CANONICAL_WRITEBACK_COLUMN_MAP.values()
    assert len(set(CANONICAL_WRITEBACK_COLUMN_MAP.values())) == len(
        CANONICAL_WRITEBACK_COLUMN_MAP
    )
    assert set(MANAGED_EXTENSION_HEADERS) <= set(CANONICAL_WRITEBACK_COLUMN_MAP)
    assert WRITEBACK_ID_FIELDS.isdisjoint(WRITEBACK_FIELDS)
    expected_fields = {
        item.field for item in SHEET_FIELDS
        if item.field not in WRITEBACK_ID_FIELDS
    } | {"comments.latest"}
    assert set(CANONICAL_WRITEBACK_COLUMN_MAP) == expected_fields


def test_canonical_mapping_fills_missing_roles_but_preserves_explicit_overrides():
    source = SimpleNamespace(
        writeback_column_map={
            "external_id": 2,
            "content.script_text": 20,
            "montage.external_editor_name": "AO",
            "publication.assigned_publisher_id": "CE",
        }
    )

    mapping = effective_writeback_column_map(source)

    assert mapping["approval.responsible_review.decision"] == "AE"
    assert mapping["publication.publisher_status"] == "CL"
    assert mapping["montage.assigned_editor_name"] == "AO"
    assert mapping["montage.external_editor_name"] == "BQ"
    assert "publication.assigned_publisher_id" not in mapping
    assert mapping["publication.assigned_publisher_name"] == "CE"

    protected_layout = SimpleNamespace(
        header_row=4,
        crm_row_id_column="BZ",
        writeback_column_map={},
    )
    protected_mapping = effective_writeback_column_map(protected_layout)
    assert protected_mapping["external_id"] == "B"
    assert protected_mapping["publication.publisher_status"] == "CL"


def test_legacy_identity_column_is_never_reused_by_canonical_workflow_field():
    source = SimpleNamespace(
        header_row=4,
        crm_row_id_column="CA",
        writeback_column_map={
            "external_id": "B",
            "content.script_text": "T",
        },
    )

    mapping = effective_writeback_column_map(source)

    assert "CA" not in {
        column_letters(reference) for reference in mapping.values()
    }
    assert mapping["final_revision_gate.decided_at"] == "CT"


def test_legacy_publication_extensions_do_not_hide_client_workflow_columns():
    source = SimpleNamespace(
        header_row=4,
        crm_row_id_column="CA",
        writeback_column_map={
            "external_id": "B",
            "content.script_text": "T",
            "publication.ai_social_descriptions": "BO",
            "publication.leia_script": "BP",
        },
    )

    mapping = effective_writeback_column_map(source)

    assert mapping["approval.pre_generation_client.note"] == "BO"
    assert mapping["approval.pre_generation_client.decided_at"] == "BP"
    assert mapping["publication.ai_social_descriptions"] == "CB"
    assert mapping["publication.leia_script"] == "CC"


async def test_canonical_source_writes_crm_owned_role_field_not_in_stored_subset():
    source_id = uuid.uuid4()
    scenario_id = uuid.uuid4()
    row_id = uuid.uuid4()
    source = SimpleNamespace(
        id=source_id,
        enabled=True,
        writeback_column_map={
            "external_id": "B",
            "content.script_text": "T",
        },
    )
    session = FakeSession(source)
    scenario = SimpleNamespace(
        id=scenario_id,
        sheet_source_id=source_id,
        crm_row_id=row_id,
    )

    event = await enqueue_sheet_writeback(
        session,
        scenario,
        {"publication.publisher_status": "published"},
    )

    assert event is not None
    assert event.changed_fields == {"publication.publisher_status": "published"}


def test_new_sheet_row_places_fields_and_identity_in_sparse_columns():
    row_id = uuid.uuid4()
    source = SimpleNamespace(
        crm_row_id_column="CA",
        writeback_column_map={
            "external_id": "B",
            "content.script_text": "T",
        },
    )

    last_column, values = append_row_values(
        source,
        row_id,
        {"external_id": "124", "content.script_text": "Новый сценарий"},
    )

    assert last_column == "CA"
    assert len(values) == 79
    assert values[1] == "124"
    assert values[19] == "Новый сценарий"
    assert values[78] == str(row_id)


def test_sheet_status_values_are_localized_without_touching_content_or_checkbox():
    assert (
        sheet_cell_value(
            "approved",
            field_name="approval.responsible_review.decision",
        )
        == "Одобрено"
    )
    assert (
        sheet_cell_value("rejected", field_name="approval.final_client.decision")
        == "Отказ"
    )
    assert (
        sheet_cell_value(
            "published",
            field_name="publication.publisher_status",
        )
        == "Опубликовано"
    )
    assert (
        sheet_cell_value(
            "ready_for_review",
            field_name="montage.material_status",
        )
        == "На проверке"
    )
    assert sheet_cell_value("approved", field_name="content.script_text") == "approved"
    assert sheet_cell_value(True, field_name="publication.is_published") is True


@pytest.mark.parametrize(
    ("field_name", "value", "expected"),
    [
        ("approval.pre_generation_client.decision", "pending", "Ожидает"),
        ("approval.source_material.decision", "revision", "Доработать"),
        ("approval.montage_compliance.decision", "rejected", "Отказ"),
        ("montage.material_status", "draft", "В работе"),
        ("montage.material_status", "ready_for_review", "На проверке"),
        ("montage.editor_status", "in_progress", "В работе"),
        ("montage.editor_status", "fixed", "Исправлено"),
        ("final_revision_gate.decision", "approved", "Одобрено"),
        ("publication.manager_review_decision", "revision", "Доработать"),
        ("publication.preparation_status", "ready_for_review", "На проверке"),
        ("publication.publisher_status", "assigned", "Назначено"),
        ("publication.publisher_status", "in_progress", "В работе"),
        ("publication.publisher_status", "published", "Опубликовано"),
        ("montage.brief_compliance_status", "APPROVED", "Одобрено"),
        ("montage.scenarist_revision_status", "completed", "Завершено"),
    ],
)
def test_every_sheet_workflow_status_family_is_localized(field_name, value, expected):
    assert sheet_cell_value(value, field_name=field_name) == expected


def test_unknown_or_already_russian_status_is_preserved():
    assert (
        sheet_cell_value("Особый статус", field_name="montage.brief_compliance_status")
        == "Особый статус"
    )
    assert (
        sheet_cell_value("Готово", field_name="montage.editor_status")
        == "Готово"
    )


def test_new_sheet_row_localizes_only_workflow_fields():
    row_id = uuid.uuid4()
    source = SimpleNamespace(
        crm_row_id_column="BZ",
        writeback_column_map={
            "content.script_text": "T",
            "approval.responsible_review.decision": "AE",
            "publication.publisher_status": "CL",
            "publication.is_published": "BK",
        },
    )

    _, values = append_row_values(
        source,
        row_id,
        {
            "content.script_text": "approved",
            "approval.responsible_review.decision": "approved",
            "publication.publisher_status": "published",
            "publication.is_published": True,
        },
    )

    assert values[column_number("T") - 1] == "approved"
    assert values[column_number("AE") - 1] == "Одобрено"
    assert values[column_number("CL") - 1] == "Опубликовано"
    assert values[column_number("BK") - 1] is True


def test_create_writeback_is_built_from_payload_without_lazy_relationship_reads():
    payload = ScenarioCreate(
        project_id=uuid.uuid4(),
        scenario_type="Экспертный",
        content={
            "cover_text": "Новая строка",
            "script_text": "Полный текст",
        },
    )

    values = scenario_routes.scenario_create_writeback(
        payload,
        "124",
        "Тестовый сценарист",
        project_name="Тестовый проект",
        client_name="Тестовый клиент",
    )

    assert values["external_id"] == "124"
    assert values["scenarist.name"] == "Тестовый сценарист"
    assert "assigned_scenarist_id" not in values
    assert values["scenario_type"] == "Экспертный"
    assert values["content.cover_text"] == "Новая строка"
    assert values["content.script_text"] == "Полный текст"
    assert values["project.name"] == "Тестовый проект"
    assert values["project.client_name"] == "Тестовый клиент"
    assert "deadline" not in values


def test_loaded_writeback_uses_scenarist_name_instead_of_uuid():
    scenario = SimpleNamespace(
        approvals=[],
        external_id="125",
        assigned_scenarist_id=uuid.uuid4(),
    )

    values = scenario_routes.loaded_scenario_writeback(
        scenario,
        scenarist_name="Сценарист для таблицы",
    )

    assert values["external_id"] == "125"
    assert values["scenarist.name"] == "Сценарист для таблицы"
    assert "assigned_scenarist_id" not in values


def test_full_writeback_serializes_each_role_and_cleared_cells():
    decided_at = datetime.now(UTC)
    scenario = SimpleNamespace(
        approvals=[
            SimpleNamespace(
                stage=ApprovalStage.RESPONSIBLE_REVIEW,
                decision=ApprovalDecision.APPROVED,
                comment="Менеджер сценаристов одобрил",
                note=None,
                decided_at=decided_at,
            ),
            SimpleNamespace(
                stage=ApprovalStage.FINAL_CLIENT,
                decision=ApprovalDecision.REVISION,
                comment="Клиент просит правку",
                note=None,
                decided_at=decided_at,
            ),
        ],
        external_id="148",
        project=SimpleNamespace(name="Проект", client_name="Клиент"),
        scenarist=SimpleNamespace(name="Сценарист"),
        content=SimpleNamespace(script_text="Сценарий"),
        montage=SimpleNamespace(
            assigned_editor_id=uuid.uuid4(),
            assigned_editor_name="Монтажёр",
            editor_status="готово",
            editor_comment=None,
        ),
        publication=SimpleNamespace(
            assigned_publisher_id=uuid.uuid4(),
            assigned_publisher_name="Публицист",
            publisher_status=PublisherStatus.PUBLISHED,
            publisher_comment="Готово",
        ),
        final_revision_gate=SimpleNamespace(
            request_comment="Причина",
            decision="pending",
            manager_comment=None,
            decided_at=None,
        ),
    )

    values = scenario_routes.loaded_scenario_writeback(
        scenario,
        include_empty=True,
    )

    assert values["content.script_text"] == "Сценарий"
    assert values["approval.responsible_review.decision"] == "approved"
    assert values["approval.final_client.comment"] == "Клиент просит правку"
    assert values["montage.assigned_editor_name"] == "Монтажёр"
    assert values["montage.editor_comment"] is None
    assert values["publication.assigned_publisher_name"] == "Публицист"
    assert values["publication.publisher_status"] == "published"
    assert values["final_revision_gate.manager_comment"] is None
    assert "montage.assigned_editor_id" not in values
    assert "publication.assigned_publisher_id" not in values

    crm_owned = crm_owned_writeback_snapshot(scenario)
    assert "content.script_text" not in crm_owned
    assert "montage.scenarist_revision_comment" not in crm_owned
    assert crm_owned["approval.final_client.comment"] == "Клиент просит правку"
    assert crm_owned["montage.editor_status"] == "готово"
    assert crm_owned["publication.publisher_status"] == "published"
    assert "publication.publisher_status" in CRM_OWNED_WRITEBACK_FIELDS


@pytest.mark.parametrize(
    ("sheet_source_id", "source_row", "event_status", "expected"),
    [
        (None, None, None, "not_configured"),
        (uuid.uuid4(), None, None, "waiting"),
        (uuid.uuid4(), None, SheetWritebackStatus.PENDING, "syncing"),
        (uuid.uuid4(), None, SheetWritebackStatus.PROCESSING, "syncing"),
        (uuid.uuid4(), None, SheetWritebackStatus.FAILED, "error"),
        (uuid.uuid4(), None, SheetWritebackStatus.COMPLETED, "synced"),
        (uuid.uuid4(), 128, None, "synced"),
    ],
)
def test_sheet_sync_status_is_visible_per_scenario(
    sheet_source_id,
    source_row,
    event_status,
    expected,
):
    scenario = SimpleNamespace(
        sheet_source_id=sheet_source_id,
        source_row=source_row,
    )
    assert (
        scenario_routes.sheet_sync_status_for_scenario(scenario, event_status)
        == expected
    )


async def test_sheet_bound_scenario_cannot_be_reassigned_to_another_sheet():
    source_id = uuid.uuid4()
    current_scenarist_id = uuid.uuid4()
    requested_scenarist_id = uuid.uuid4()
    scenario = SimpleNamespace(
        sheet_source_id=source_id,
        project_id=uuid.uuid4(),
        assigned_scenarist_id=current_scenarist_id,
    )
    requested_scenarist = SimpleNamespace(
        id=requested_scenarist_id,
        role=Role.SCENARIST,
        is_active=True,
    )
    source = SimpleNamespace(
        id=source_id,
        enabled=True,
        assigned_scenarist_id=current_scenarist_id,
    )

    class AssignmentSession:
        async def get(self, model, _identifier):
            if model is SheetSource:
                return source
            return requested_scenarist

    with pytest.raises(HTTPException) as error:
        await scenario_routes.assign_scenarist_without_moving_sheet_row(
            AssignmentSession(),
            scenario,
            requested_scenarist_id,
        )

    assert error.value.status_code == 409
    assert "another sheet" in error.value.detail
    assert scenario.assigned_scenarist_id == current_scenarist_id


async def test_project_shared_sheet_allows_reassignment_without_moving_row():
    source_id = uuid.uuid4()
    requested_scenarist_id = uuid.uuid4()
    scenario = SimpleNamespace(
        sheet_source_id=source_id,
        project_id=uuid.uuid4(),
        assigned_scenarist_id=uuid.uuid4(),
    )
    requested_scenarist = SimpleNamespace(
        id=requested_scenarist_id,
        full_name="Новый сценарист",
        role=Role.SCENARIST,
        is_active=True,
    )
    source = SimpleNamespace(
        id=source_id,
        enabled=True,
        assigned_scenarist_id=None,
    )

    class AssignmentSession:
        async def get(self, model, _identifier):
            return source if model is SheetSource else requested_scenarist

    moved, name = await scenario_routes.assign_scenarist_without_moving_sheet_row(
        AssignmentSession(),
        scenario,
        requested_scenarist_id,
    )

    assert moved is False
    assert name == "Новый сценарист"
    assert scenario.assigned_scenarist_id == requested_scenarist_id
    assert scenario.sheet_source_id == source_id


def test_project_tab_title_is_safe_unique_and_does_not_replace_main_tab():
    existing = {"сценарий", "Новый проект"}

    assert unique_sheet_title("Новый проект", existing) == "Новый проект (2)"
    assert unique_sheet_title("Клиент / Проект: 1", existing) == "Клиент Проект 1"
    assert "сценарий" in existing


class FakeSession:
    def __init__(self, source):
        self.source = source
        self.added = []

    async def get(self, model, _identifier):
        assert model is SheetSource
        return self.source

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


@pytest.mark.parametrize(
    "changed",
    [
        {"content.script_text": "script"},
        {"approval.pre_generation_client.decision": "approved"},
        {"montage.ready_material_url": "https://example.com/video"},
        {"publication.manager_review_decision": "approved"},
        {"publication.publisher_status": "published"},
        {"comments.latest": "message"},
    ],
)
async def test_outbox_filters_every_representative_role_mutation(changed):
    source_id = uuid.uuid4()
    scenario_id = uuid.uuid4()
    row_id = uuid.uuid4()
    source = SimpleNamespace(
        id=source_id,
        enabled=True,
        writeback_column_map={next(iter(changed)): "B"},
    )
    session = FakeSession(source)
    scenario = SimpleNamespace(
        id=scenario_id,
        sheet_source_id=source_id,
        crm_row_id=row_id,
    )
    event = await enqueue_sheet_writeback(session, scenario, changed)
    assert event is not None
    assert event.changed_fields == changed
    assert session.added == [event]


async def test_google_write_retries_429(monkeypatch):
    responses = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={}),
    ]

    class FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return responses.pop(0)

    async def token(_self):
        return "token"

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(GoogleSheetsClient, "_access_token", token)
    monkeypatch.setattr("crm.google_sheets.httpx.AsyncClient", lambda **_kwargs: FakeHttpClient())
    monkeypatch.setattr("crm.google_sheets.asyncio.sleep", no_sleep)
    client = object.__new__(GoogleSheetsClient)
    await client.batch_update_values(
        "sheet",
        [{"range": "'Tab'!B2", "values": [["value"]]}],
        max_retries=2,
    )
    assert responses == []


async def test_google_write_reports_429_after_retry_budget(monkeypatch):
    class AlwaysLimited:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return httpx.Response(429, headers={"Retry-After": "0"})

    async def token(_self):
        return "token"

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(GoogleSheetsClient, "_access_token", token)
    monkeypatch.setattr("crm.google_sheets.httpx.AsyncClient", lambda **_kwargs: AlwaysLimited())
    monkeypatch.setattr("crm.google_sheets.asyncio.sleep", no_sleep)
    client = object.__new__(GoogleSheetsClient)
    with pytest.raises(GoogleSheetsSourceError, match="HTTP 429"):
        await client.batch_update_values(
            "sheet",
            [{"range": "'Tab'!B2", "values": [["value"]]}],
            max_retries=2,
        )


async def test_google_write_expands_tab_before_new_role_columns(monkeypatch):
    requests = []

    class FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return httpx.Response(
                200,
                json={
                    "sheets": [
                        {
                            "properties": {
                                "sheetId": 42,
                                "title": "сценарий",
                                "gridProperties": {"columnCount": 81},
                            }
                        }
                    ]
                },
            )

        async def post(self, url, **kwargs):
            requests.append((url, kwargs["json"]))
            return httpx.Response(200, json={})

    async def token(_self):
        return "token"

    monkeypatch.setattr(GoogleSheetsClient, "_access_token", token)
    monkeypatch.setattr(
        "crm.google_sheets.httpx.AsyncClient",
        lambda **_kwargs: FakeHttpClient(),
    )
    client = object.__new__(GoogleSheetsClient)

    await client.ensure_tab_column_capacity("sheet", "сценарий", 97)
    await client.ensure_tab_column_capacity("sheet", "сценарий", 97)

    assert requests == [
        (
            "https://sheets.googleapis.com/v4/spreadsheets/sheet:batchUpdate",
            {
                "requests": [
                    {
                        "appendDimension": {
                            "sheetId": 42,
                            "dimension": "COLUMNS",
                            "length": 16,
                        }
                    }
                ]
            },
        )
    ]


async def test_google_append_returns_created_row_and_identity_lookup(monkeypatch):
    responses = [
        httpx.Response(
            200,
            json={"values": [["other"], ["target-row-id"]]},
        ),
        httpx.Response(
            200,
            json={"updates": {"updatedRange": "'сценарий'!A128:CA128"}},
        ),
    ]

    class FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return responses.pop(0)

        async def post(self, *_args, **_kwargs):
            return responses.pop(0)

    async def token(_self):
        return "token"

    monkeypatch.setattr(GoogleSheetsClient, "_access_token", token)
    monkeypatch.setattr(
        "crm.google_sheets.httpx.AsyncClient",
        lambda **_kwargs: FakeHttpClient(),
    )
    client = object.__new__(GoogleSheetsClient)

    assert (
        await client.find_value_row(
            "sheet",
            "сценарий",
            "CA",
            "target-row-id",
            first_row=5,
        )
        == 6
    )
    assert await client.append_row(
        "sheet",
        "сценарий",
        "CA",
        ["", "124"],
    ) == 128


@pytest.mark.parametrize("existing_row", [None, 128])
async def test_writeback_creates_or_recovers_new_sheet_row_idempotently(existing_row):
    source_id = uuid.uuid4()
    scenario_id = uuid.uuid4()
    row_id = uuid.uuid4()
    event = SimpleNamespace(
        id=uuid.uuid4(),
        status=SheetWritebackStatus.PENDING,
        attempts=0,
        source_id=source_id,
        scenario_id=scenario_id,
        crm_row_id=row_id,
        changed_fields={"external_id": "124"},
        error=None,
        next_attempt_at=None,
        processed_at=None,
    )
    source = SimpleNamespace(
        id=source_id,
        enabled=True,
        spreadsheet_id="sheet",
        source_tab="сценарий",
        header_row=4,
        crm_row_id_column="CA",
        writeback_column_map={"external_id": "B"},
        last_status=None,
        last_error=None,
        last_sync_at=None,
    )
    scenario = SimpleNamespace(
        id=scenario_id,
        crm_row_id=row_id,
        source_row=None,
        source_sheet_id="sheet",
        source_tab="сценарий",
    )

    class FakeSession:
        async def scalar(self, _query):
            return event

        async def get(self, model, _identifier):
            return source if model is SheetSource else scenario

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    class FakeClient:
        def __init__(self):
            self.append_calls = 0
            self.updates = []
            self.capacity = None

        async def ensure_tab_column_capacity(
            self, _spreadsheet_id, _tab, required_column_count
        ):
            self.capacity = required_column_count

        async def find_value_row(self, *_args, **_kwargs):
            return existing_row

        async def append_row(self, *_args, **_kwargs):
            self.append_calls += 1
            return 128

        async def batch_update_values(self, _spreadsheet_id, updates, **_kwargs):
            self.updates = updates

    client = FakeClient()
    result = await process_writeback_event(FakeSession(), event.id, client)

    assert result.status == SheetWritebackStatus.COMPLETED
    assert scenario.source_row == 128
    assert client.append_calls == (0 if existing_row else 1)
    assert client.capacity == 79
    assert client.updates[0]["range"] == "'сценарий'!CA128"


async def test_role_writeback_clears_stale_value_and_ensures_extension_header():
    source_id = uuid.uuid4()
    scenario_id = uuid.uuid4()
    row_id = uuid.uuid4()
    event = SimpleNamespace(
        id=uuid.uuid4(),
        status=SheetWritebackStatus.PENDING,
        attempts=0,
        source_id=source_id,
        scenario_id=scenario_id,
        crm_row_id=row_id,
        changed_fields={"publication.publisher_status": None},
        error=None,
        next_attempt_at=None,
        processed_at=None,
    )
    source = SimpleNamespace(
        id=source_id,
        enabled=True,
        spreadsheet_id="sheet",
        source_tab="сценарий",
        header_row=4,
        crm_row_id_column="BZ",
        writeback_column_map={
            "external_id": "B",
            "content.script_text": "T",
        },
        last_status=None,
        last_error=None,
        last_sync_at=None,
    )
    scenario = SimpleNamespace(
        id=scenario_id,
        crm_row_id=row_id,
        source_row=148,
        source_sheet_id="sheet",
        source_tab="сценарий",
    )

    class RoleSession:
        async def scalar(self, _query):
            return event

        async def get(self, model, _identifier):
            return source if model is SheetSource else scenario

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    class RoleClient:
        def __init__(self):
            self.updates = []
            self.capacity = None

        async def ensure_tab_column_capacity(
            self, _spreadsheet_id, _tab, required_column_count
        ):
            self.capacity = required_column_count

        async def batch_update_values(self, _spreadsheet_id, updates, **_kwargs):
            self.updates = updates

    client = RoleClient()
    result = await process_writeback_event(RoleSession(), event.id, client)

    assert result.status == SheetWritebackStatus.COMPLETED
    assert client.capacity == column_number("CL")
    assert {
        item["range"]: item["values"]
        for item in client.updates
    } == {
        "'сценарий'!BZ148": [[str(row_id)]],
        "'сценарий'!CL148": [[""]],
        "'сценарий'!CL4": [["Статус публикации"]],
    }


def test_models_define_idempotency_and_stable_row_constraints():
    inbound_constraints = {
        constraint.name
        for constraint in __import__(
            "crm.models", fromlist=["SheetInboundEvent"]
        ).SheetInboundEvent.__table__.constraints
    }
    scenario_constraints = {
        constraint.name for constraint in Scenario.__table__.constraints
    }
    assert any(name for name in inbound_constraints if "event_id" in (name or ""))
    assert "uq_scenario_sheet_source_crm_row" in scenario_constraints


def test_openapi_documents_management_webhook_and_event_contracts():
    schema = app.openapi()
    paths = schema["paths"]
    assert "post" in paths["/api/v1/google-sheets/sources"]
    assert {"get", "patch", "delete"} <= set(
        paths["/api/v1/google-sheets/sources/{source_id}"]
    )
    webhook = paths["/api/v1/google-sheets/webhook/{source_id}"]["post"]
    body_schema = webhook["requestBody"]["content"]["application/json"]["schema"]
    assert body_schema["$ref"].endswith("/SheetWebhookEvent")
    assert "202" in webhook["responses"]
    accepted_schema = app.openapi()["components"]["schemas"]["SheetWebhookAccepted"]
    assert "error" in accepted_schema["properties"]
    assert "/api/v1/google-sheets/inbound-events" in paths
    assert "post" in paths[
        "/api/v1/google-sheets/inbound-events/{inbound_event_id}/retry"
    ]
    assert "/api/v1/google-sheets/writeback-events" in paths
    assert "post" in paths["/api/v1/scenarios/{scenario_id}/sheet-sync/retry"]


@pytest.mark.parametrize(
    "handler",
    [
        scenario_routes.create_scenario,
        scenario_routes.patch_scenario_sheet_row,
        scenario_routes.retry_scenario_sheet_sync,
        scenario_routes.update_scenario,
        scenario_routes.set_approval,
        scenario_routes.decide_final_revision_gate,
        scenario_routes.create_comment,
        scenario_routes.update_montage,
        scenario_routes.update_montage_as_editor,
        scenario_routes.update_publication,
        scenario_routes.review_publication,
        scenario_routes.update_publication_as_publisher,
    ],
)
def test_every_sheet_visible_mutation_route_writes_outbox_before_commit(handler):
    source = inspect.getsource(handler)
    assert source.index("enqueue_sheet_writeback") < source.index("session.commit")
