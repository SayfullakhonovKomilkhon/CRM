import uuid
from types import SimpleNamespace

from sqlalchemy import select

from crm.main import app
from crm.models import Project, Role, Scenario
from crm.routers.scenarios import apply_visibility, client_filter


def test_client_id_is_documented_for_list_and_sheet() -> None:
    schema = app.openapi()

    for path in ("/api/v1/scenarios", "/api/v1/scenarios/sheet"):
        parameters = {
            parameter["name"]: parameter
            for parameter in schema["paths"][path]["get"]["parameters"]
        }
        assert parameters["client_id"]["in"] == "query"
        assert parameters["client_id"]["required"] is False
        assert parameters["client_id"]["schema"]["anyOf"][0]["format"] == "uuid"


def test_client_review_stage_is_documented_in_lightweight_list() -> None:
    schema = app.openapi()
    property_schema = schema["components"]["schemas"]["ScenarioListItem"]["properties"]

    assert "review_stage" in property_schema
    review_stage = property_schema["review_stage"]
    assert any(
        option.get("$ref", "").endswith("/ApprovalStage")
        for option in review_stage["anyOf"]
    )


def test_client_id_filter_targets_the_project_owner() -> None:
    requested_client_id = uuid.uuid4()
    statement = (
        select(Scenario.id)
        .join(Project, Scenario.project_id == Project.id)
        .where(client_filter(requested_client_id))
    )
    compiled = statement.compile()

    assert "projects.client_id" in str(compiled)
    assert requested_client_id in compiled.params.values()


def test_client_id_filter_is_intersected_with_client_role_visibility() -> None:
    requested_client_id = uuid.uuid4()
    authenticated_client_id = uuid.uuid4()
    user = SimpleNamespace(
        id=uuid.uuid4(),
        role=Role.CLIENT,
        client_id=authenticated_client_id,
    )
    statement = (
        select(Scenario.id)
        .join(Project, Scenario.project_id == Project.id)
        .where(client_filter(requested_client_id))
    )
    compiled = apply_visibility(statement, user).compile()

    assert requested_client_id in compiled.params.values()
    assert authenticated_client_id in compiled.params.values()


def test_client_id_filter_keeps_editor_assignment_visibility() -> None:
    requested_client_id = uuid.uuid4()
    editor_id = uuid.uuid4()
    user = SimpleNamespace(id=editor_id, role=Role.EDITOR, client_id=None)
    statement = (
        select(Scenario.id)
        .join(Project, Scenario.project_id == Project.id)
        .where(client_filter(requested_client_id))
    )
    compiled = apply_visibility(statement, user).compile()

    assert requested_client_id in compiled.params.values()
    assert editor_id in compiled.params.values()
