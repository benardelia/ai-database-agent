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
