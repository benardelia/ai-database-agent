import json
from pathlib import Path

import pytest

from dbagent.ai.tools import ToolExecutor
from dbagent.business.metric_service import MetricService
from dbagent.database import DatabaseConnection
from dbagent.services.query_service import ReadOnlyQueryService
from dbagent.services.schema_service import DatabaseSchemaService
from dbagent.services.search_service import SchemaSearchService
from dbagent.services.sql_validator import SqlValidator

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def executor(ilcms_db_connection: DatabaseConnection) -> ToolExecutor:
    schema_service = DatabaseSchemaService(ilcms_db_connection.engine)
    search_service = SchemaSearchService(schema_service)
    return ToolExecutor(schema_service, search_service)


@pytest.fixture
def sms_executor_with_metrics() -> ToolExecutor:
    config = json.loads((ROOT / "databases.json").read_text())
    sms_url = config["my_store_db"]["database_url"]
    connection = DatabaseConnection(sms_url)

    schema_service = DatabaseSchemaService(connection.engine, schemas=["public"])
    search_service = SchemaSearchService(schema_service)
    sql_validator = SqlValidator()
    query_service = ReadOnlyQueryService(connection.engine, sql_validator, search_path=["public"])
    metric_service = MetricService(ROOT / "src/dbagent/business/metrics.json")

    return ToolExecutor(
        schema_service,
        search_service,
        sql_validator,
        query_service,
        metric_service=metric_service,
    )


def test_missing_required_argument_returns_error_not_exception(executor: ToolExecutor):
    """A model can call a tool with the wrong/missing arguments (Phase 40:
    invalid tool arguments). That must come back as a tool error the model
    can react to, not crash the agent loop."""
    result = executor.execute("find_relationships", {})
    assert "error" in result
    assert "table" in result["error"]


def test_missing_argument_for_get_table_schema(executor: ToolExecutor):
    result = executor.execute("get_table_schema", {})
    assert "error" in result


def test_unknown_tool_returns_error(executor: ToolExecutor):
    result = executor.execute("delete_everything", {"table": "x"})
    assert "error" in result


def test_find_relationships_accepts_from_table_alias(executor: ToolExecutor):
    """Small models frequently guess 'from_table' instead of the tool
    schema's actual 'table' parameter (observed live with llama3.2). The
    executor should tolerate that rather than bounce the model's guess
    back as an error every time."""
    result = executor.execute("find_relationships", {"from_table": "spatial_ref_sys"})
    assert "error" not in result
    assert result == {"relationships": []}


def test_get_table_schema_accepts_table_name_alias(executor: ToolExecutor):
    result = executor.execute("get_table_schema", {"table_name": "spatial_ref_sys"})
    assert "error" not in result
    assert result["name"] == "spatial_ref_sys"


def test_search_tables_accepts_search_query_alias(executor: ToolExecutor):
    result = executor.execute("search_tables", {"search_query": "geometry"})
    assert "error" not in result
    assert any(r["table"] == "geometry_columns" for r in result["results"])


def test_blank_argument_value_is_treated_as_missing(executor: ToolExecutor):
    result = executor.execute("find_relationships", {"table": ""})
    assert "error" in result


def test_list_business_metrics_returns_definitions(sms_executor_with_metrics: ToolExecutor):
    result = sms_executor_with_metrics.execute("list_business_metrics", {})
    names = {m["name"] for m in result["metrics"]}
    assert "completed_widgets" in names


def test_compute_metric_returns_real_verified_value(sms_executor_with_metrics: ToolExecutor):
    """Ground truth confirmed independently via psql: 7 widgets with
    status='Completed' in the live my_store_db database."""
    result = sms_executor_with_metrics.execute("compute_metric", {"name": "completed_widgets"})
    assert result["rows"] == [[7]]
    assert result["metric"] == "completed_widgets"


def test_compute_metric_with_period_returns_real_verified_value(
    sms_executor_with_metrics: ToolExecutor,
):
    """Ground truth confirmed independently via psql: 1042.50 in
    successful payments during April 2026."""
    result = sms_executor_with_metrics.execute(
        "compute_metric",
        {
            "name": "payments_in_period",
            "start_date": "2026-04-01",
            "end_date": "2026-05-01",
        },
    )
    assert result["rows"] == [["1042.50"]]


def test_compute_metric_unknown_name_returns_error(sms_executor_with_metrics: ToolExecutor):
    result = sms_executor_with_metrics.execute("compute_metric", {"name": "not_a_real_metric"})
    assert "error" in result


def test_compute_metric_missing_dates_returns_error(sms_executor_with_metrics: ToolExecutor):
    result = sms_executor_with_metrics.execute(
        "compute_metric", {"name": "payments_in_period"}
    )
    assert "error" in result


def test_metrics_unavailable_returns_empty_list(executor: ToolExecutor):
    """The my_case_db-based `executor` fixture has no metric_service wired --
    should degrade gracefully, not crash."""
    result = executor.execute("list_business_metrics", {})
    assert result == {"metrics": []}
