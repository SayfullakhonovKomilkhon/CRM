import inspect
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from crm.config import Settings
from crm.google_sheets import GoogleSheetsClient, GoogleSheetsSourceError
from crm.main import app
from crm.models import (
    ApprovalDecision,
    ApprovalStage,
    Scenario,
    ScenarioStatus,
    SheetSource,
    SheetWritebackStatus,
)
from crm.routers import scenarios as scenario_routes
from crm.schemas import ScenarioCreate
from crm.sheet_sync import (
    WRITEBACK_FIELDS,
    _set_values,
    active_scenarist_revision_stage,
    append_row_values,
    column_letters,
    column_number,
    enqueue_sheet_writeback,
    inbound_update_allowed,
    process_inbound_event,
    process_writeback_event,
    source_metadata_matches,
    source_webhook_secret,
    validate_column_map,
    verify_webhook,
    webhook_signature,
    workflow_fields_only,
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


def test_inbound_workflow_statuses_are_coerced_and_applied():
    value = Scenario(
        project_id=uuid.uuid4(),
        assigned_scenarist_id=uuid.uuid4(),
        external_id="workflow-test",
    )
    value.content = __import__(
        "crm.models", fromlist=["ScenarioContent"]
    ).ScenarioContent(script_text="Сценарий")

    _set_values(
        value,
        {
            "approval.responsible_review.decision": "Одобрено",
            "approval.pre_generation_client.decision": "Доработать",
            "approval.pre_generation_client.comment": "Исправьте вступление",
        },
    )

    assert value.status == ScenarioStatus.REVISION
    client = next(
        item
        for item in value.approvals
        if item.stage == ApprovalStage.PRE_GENERATION_CLIENT
    )
    assert client.decision == ApprovalDecision.REVISION
    assert client.comment == "Исправьте вступление"
    assert workflow_fields_only(
        {"publication.is_published": "TRUE"}
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


def test_create_writeback_is_built_from_payload_without_lazy_relationship_reads():
    payload = ScenarioCreate(
        project_id=uuid.uuid4(),
        scenario_type="Экспертный",
        content={
            "cover_text": "Новая строка",
            "script_text": "Полный текст",
        },
    )

    values = scenario_routes.scenario_create_writeback(payload, "124")

    assert values["external_id"] == "124"
    assert values["scenario_type"] == "Экспертный"
    assert values["content.cover_text"] == "Новая строка"
    assert values["content.script_text"] == "Полный текст"
    assert "deadline" not in values


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
    assert client.updates[0]["range"] == "'сценарий'!CA128"


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
    assert "/api/v1/google-sheets/inbound-events" in paths
    assert "/api/v1/google-sheets/writeback-events" in paths


@pytest.mark.parametrize(
    "handler",
    [
        scenario_routes.create_scenario,
        scenario_routes.patch_scenario_sheet_row,
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
