import asyncio
import json

import pytest

import dbagent.mcp_server as mcp_server_module
from dbagent.mcp_server import mcp
from dbagent.registry import DatabaseRegistry


class FakeProvider:
    def chat(self, messages, tools=None):
        return {"role": "assistant", "content": "unused"}


@pytest.fixture(autouse=True)
def synthetic_registry(monkeypatch, tmp_path, pg_test_connection, synthetic_schema):
    """Points the MCP server's module-level registry at a synthetic,
    fully-fake config for the duration of each test -- the tool functions
    in mcp_server.py look up `_registry` by name at call time, so
    monkeypatching this module attribute is enough; no production code
    needs to change for testability."""
    url = pg_test_connection.engine.url.render_as_string(hide_password=False)
    metrics_file = tmp_path / "metrics.json"
    metrics_file.write_text(
        json.dumps(
            [
                {
                    "name": "completed_widgets",
                    "description": "Count of widgets whose status is Completed.",
                    "sql": "SELECT COUNT(*) AS completed_widgets FROM widget WHERE status = 'Completed'",
                }
            ]
        )
    )

    config_path = tmp_path / "databases.json"
    config_path.write_text(
        json.dumps(
            {
                "alpha": {
                    "database_url": url,
                    "schemas": [synthetic_schema],
                    "excluded_tables": [f"{synthetic_schema}.secret_credential"],
                    "metrics_path": str(metrics_file),
                },
                "beta": {"database_url": url, "schemas": ["public"]},
            }
        )
    )

    registry = DatabaseRegistry(str(config_path), FakeProvider())
    monkeypatch.setattr(mcp_server_module, "_registry", registry)
    return registry


def _call(tool_name: str, arguments: dict) -> dict:
    """Call an MCP tool through the real protocol path (mcp.call_tool) and
    return its structured_content -- exercises the same code path a real
    MCP client (Claude Desktop, Claude Code, ...) would use, not just the
    underlying Python function directly."""
    result = asyncio.run(mcp.call_tool(tool_name, arguments))
    assert not result.is_error, result
    return result.structured_content


def test_list_databases_returns_configured_names():
    result = asyncio.run(mcp.call_tool("list_databases", {}))
    assert not result.is_error
    assert "alpha" in result.structured_content["result"]
    assert "beta" in result.structured_content["result"]


def test_execute_readonly_sql_returns_real_data():
    result = _call(
        "execute_readonly_sql",
        {"database": "alpha", "sql": "SELECT COUNT(*) FROM region"},
    )
    assert result["rows"] == [[3]]


def test_execute_readonly_sql_rejects_destructive_sql():
    result = _call(
        "execute_readonly_sql",
        {"database": "alpha", "sql": "DELETE FROM region"},
    )
    assert "error" in result


def test_validate_sql_reports_invalid_for_write_statement():
    result = _call(
        "validate_sql", {"database": "alpha", "sql": "UPDATE region SET name = 'x'"}
    )
    assert result["valid"] is False


def test_get_table_schema_hides_excluded_table():
    """secret_credential is excluded for the 'alpha' database. It must be
    unreachable through the MCP layer exactly like it is through the HTTP
    API and the Ollama agent -- same underlying services."""
    result = _call("get_table_schema", {"database": "alpha", "table": "secret_credential"})
    assert "error" in result
    assert "not found" in result["error"]


def test_get_table_schema_works_for_real_table():
    result = _call("get_table_schema", {"database": "alpha", "table": "widget"})
    column_names = {c["name"] for c in result["columns"]}
    assert "name" in column_names
    assert "status" in column_names


def test_unknown_database_returns_clear_error():
    result = _call("get_database_schema", {"database": "does_not_exist"})
    assert "error" in result
    assert "does_not_exist" in result["error"]
    assert "alpha" in result["error"]


def test_search_tables_via_mcp():
    result = asyncio.run(mcp.call_tool("search_tables", {"database": "alpha", "query": "widget"}))
    assert not result.is_error
    table_names = [r["table"] for r in result.structured_content["result"]]
    assert "widget" in table_names


def test_get_sample_rows_excludes_sensitive_columns_via_mcp():
    result = _call("get_sample_rows", {"database": "alpha", "table": "account", "limit": 2})
    assert "password" in result["excluded_sensitive_columns"]


def test_list_business_metrics_via_mcp():
    result = _call("list_business_metrics", {"database": "alpha"})
    names = {m["name"] for m in result["metrics"]}
    assert "completed_widgets" in names


def test_compute_metric_via_mcp_returns_expected_value():
    result = _call("compute_metric", {"database": "alpha", "name": "completed_widgets"})
    assert result["rows"] == [[2]]


def test_compute_metric_via_mcp_unavailable_for_database_without_metrics():
    """'beta' has no metrics_path configured -- must degrade gracefully."""
    result = _call("list_business_metrics", {"database": "beta"})
    assert result["metrics"] == []
