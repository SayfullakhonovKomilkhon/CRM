import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

from crm.database import Base
from crm.models import Role, Scenario
from crm.schemas import ScenarioCreate
from crm.sheet import editable_fields_for_role


def test_scenario_create_does_not_accept_external_id() -> None:
    with pytest.raises(ValidationError):
        ScenarioCreate(
            project_id="00000000-0000-0000-0000-000000000001",
            external_id="999",
        )
    assert "external_id" not in ScenarioCreate.model_json_schema()["properties"]


@pytest.mark.parametrize("role", list(Role))
def test_scenario_external_id_is_readonly_for_every_role(role: Role) -> None:
    scenario = SimpleNamespace(
        available_sections=["content", "approvals", "montage", "publication"],
        available_approval_stages=[],
    )

    assert "external_id" not in editable_fields_for_role(scenario, role)


def test_scenario_model_has_non_unique_required_generated_display_id() -> None:
    external_id = Scenario.__table__.c.external_id
    constraint_names = {constraint.name for constraint in Scenario.__table__.constraints}

    assert external_id.nullable is False
    assert external_id.server_default is not None
    assert "uq_scenarios_external_id" not in constraint_names
    assert "uq_scenario_source" not in constraint_names
    assert "uq_scenario_project_external_id" not in constraint_names


def test_models_still_compile_for_sqlite_unit_tests() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    assert "scenarios" in Base.metadata.tables


def test_postgresql_migration_renumbers_deterministically_before_sequence_default() -> None:
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0012_scenario_external_id_sequence.py"
    )
    namespace = runpy.run_path(migration_path)
    calls = []

    class Recorder:
        def get_bind(self):
            return object()

        def drop_constraint(self, *args, **kwargs):
            calls.append(("drop_constraint", args, kwargs))

        def execute(self, statement):
            calls.append(("execute", str(statement), {}))

        def alter_column(self, *args, **kwargs):
            calls.append(("alter_column", args, kwargs))

        def create_unique_constraint(self, *args, **kwargs):
            calls.append(("create_unique_constraint", args, kwargs))

    class Inspector:
        def get_unique_constraints(self, _table):
            return [{"name": "uq_scenario_project_external_id"}]

    namespace["upgrade"].__globals__["op"] = Recorder()
    namespace["upgrade"].__globals__["sa"].inspect = lambda _bind: Inspector()
    namespace["upgrade"]()

    executed_sql = "\n".join(call[1] for call in calls if call[0] == "execute")
    assert "row_number() OVER (ORDER BY created_at, id)" in executed_sql
    assert "setval" in executed_sql
    assert "OWNED BY scenarios.external_id" in executed_sql
    unique_call = next(call for call in calls if call[0] == "create_unique_constraint")
    assert unique_call[1] == ("uq_scenarios_external_id", "scenarios", ["external_id"])
    alter_call = next(call for call in calls if call[0] == "alter_column")
    assert alter_call[2]["nullable"] is False
    assert "nextval" in str(alter_call[2]["server_default"])
