import pytest

from dbagent.ai.tools import ToolExecutor
from dbagent.database import DatabaseConnection
from dbagent.services.schema_service import DatabaseSchemaService
from dbagent.services.search_service import SchemaSearchService


@pytest.fixture
def executor(ilcms_db_connection: DatabaseConnection) -> ToolExecutor:
    schema_service = DatabaseSchemaService(ilcms_db_connection.engine)
    search_service = SchemaSearchService(schema_service)
    return ToolExecutor(schema_service, search_service)


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
