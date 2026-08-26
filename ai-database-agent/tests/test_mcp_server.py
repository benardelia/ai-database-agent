import asyncio
import json

import pytest

from dbagent.mcp_server import mcp


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
    assert "my_case_db" in result.structured_content["result"]
    assert "my_store_db" in result.structured_content["result"]


def test_execute_readonly_sql_returns_real_data():
    result = _call(
        "execute_readonly_sql",
        {"database": "my_case_db", "sql": "SELECT COUNT(*) FROM region"},
    )
    assert result["rows"] == [[3]]


def test_execute_readonly_sql_rejects_destructive_sql():
    result = _call(
        "execute_readonly_sql",
        {"database": "my_case_db", "sql": "DELETE FROM region"},
    )
    assert "error" in result


def test_validate_sql_reports_invalid_for_write_statement():
    result = _call(
        "validate_sql", {"database": "my_case_db", "sql": "UPDATE region SET name = 'x'"}
    )
    assert result["valid"] is False


def test_get_table_schema_hides_excluded_table():
    """user_account_table is excluded for the my_store_db database (databases.json). It
    must be unreachable through the MCP layer exactly like it is through
    the HTTP API and the Ollama agent -- same underlying services."""
    result = _call("get_table_schema", {"database": "my_store_db", "table": "user_account_table"})
    assert "error" in result
    assert "not found" in result["error"]


def test_get_table_schema_works_for_real_table():
    result = _call("get_table_schema", {"database": "my_store_db", "table": "product_table"})
    column_names = {c["name"] for c in result["columns"]}
    assert "name" in column_names
    assert "selling_price" in column_names


def test_unknown_database_returns_clear_error():
    result = _call("get_database_schema", {"database": "does_not_exist"})
    assert "error" in result
    assert "does_not_exist" in result["error"]
    assert "my_case_db" in result["error"]


def test_search_tables_via_mcp():
    result = asyncio.run(
        mcp.call_tool("search_tables", {"database": "my_store_db", "query": "product"})
    )
    assert not result.is_error
    table_names = [r["table"] for r in result.structured_content["result"]]
    assert "product_table" in table_names


def test_get_sample_rows_excludes_sensitive_columns_via_mcp():
    result = _call("get_sample_rows", {"database": "my_store_db", "table": "product_table", "limit": 2})
    assert len(result["rows"]) <= 2
    assert "excluded_sensitive_columns" in result
