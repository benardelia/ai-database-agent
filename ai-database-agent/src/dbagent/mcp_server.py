"""MCP server exposing the database tool layer (guide Phase 12/20).

This lets any MCP-compatible client (Claude Desktop, Claude Code, other
agent frameworks) use the exact same search/inspect/validate/execute
primitives our own Ollama-driven agent uses -- without going through that
agent's reasoning loop. The calling client's own model does the reasoning;
this process only ever exposes what the underlying services already allow
(same schema exclusions, same SQL validation, same read-only execution).
"""

from typing import Any

from mcp.server import MCPServer

from dbagent.ai.provider import build_ollama_provider
from dbagent.business.metric_service import MetricError
from dbagent.config import settings
from dbagent.registry import DatabaseBundle, DatabaseRegistry
from dbagent.services.query_service import QueryExecutionError
from dbagent.services.sample_service import SampleDataError
from dbagent.services.sql_validator import SqlValidationError

mcp = MCPServer("ai-database-agent")

# The LLM provider is only needed here because DatabaseBundle builds a full
# AgentService per database; the MCP tools below never call it -- reasoning
# is the calling client's job, not this server's.
_registry = DatabaseRegistry(settings.databases_config_path, build_ollama_provider())


def _bundle(database: str) -> DatabaseBundle | None:
    try:
        return _registry.get(database)
    except KeyError:
        return None


def _unknown_database_error(database: str) -> dict[str, Any]:
    return {
        "error": f"Unknown database '{database}'. Configured databases: {_registry.list_databases()}"
    }


@mcp.tool()
def list_databases() -> list[str]:
    """List the names of all configured databases this server can query."""
    return _registry.list_databases()


@mcp.tool()
def get_database_schema(database: str) -> dict[str, Any]:
    """Return the full normalized schema (tables, columns, keys, relationships) for a database."""
    bundle = _bundle(database)
    if bundle is None:
        return _unknown_database_error(database)
    return bundle.schema_service.get_schema().model_dump()


@mcp.tool()
def search_tables(database: str, query: str, limit: int = 20) -> list[dict[str, Any]] | dict[str, Any]:
    """Search database tables/columns relevant to a business concept or keyword."""
    bundle = _bundle(database)
    if bundle is None:
        return _unknown_database_error(database)
    results = bundle.search_service.search_tables(query, limit=limit)
    return [r.model_dump() for r in results]


@mcp.tool()
def get_table_schema(database: str, table: str) -> dict[str, Any]:
    """Return columns, keys and relationships for one specific table."""
    bundle = _bundle(database)
    if bundle is None:
        return _unknown_database_error(database)
    result = bundle.schema_service.get_table(table)
    if result is None:
        return {"error": f"Table '{table}' was not found in the schema."}
    return result.model_dump()


@mcp.tool()
def find_relationships(database: str, table: str) -> list[dict[str, Any]] | dict[str, Any]:
    """Return foreign key relationships involving a table, in either direction."""
    bundle = _bundle(database)
    if bundle is None:
        return _unknown_database_error(database)
    relationships = bundle.schema_service.find_relationships(table)
    return [r.model_dump() for r in relationships]


@mcp.tool()
def get_sample_rows(
    database: str,
    table: str,
    limit: int = 10,
    session_variables: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a few example rows from a table so you can see what its values look like.
    Sensitive-looking columns (passwords, tokens, card numbers, etc.) are never included.
    session_variables (e.g. {"app.current_shop_id": "<uuid>"}) are applied for Postgres
    RLS policies, if the target database has any configured."""
    bundle = _bundle(database)
    if bundle is None:
        return _unknown_database_error(database)
    try:
        result, excluded_columns = bundle.sample_service.get_sample_rows(
            table, limit=limit, session_variables=session_variables
        )
    except SampleDataError as exc:
        return {"error": str(exc)}
    return {
        "columns": result.columns,
        "rows": result.rows,
        "excluded_sensitive_columns": excluded_columns,
    }


@mcp.tool()
def list_business_metrics(database: str) -> dict[str, Any]:
    """List trusted, pre-defined business metrics for this database (e.g.
    'completed_widgets', 'total_revenue'). Prefer these over writing raw SQL
    for a concept that matches one, since they guarantee consistent business
    logic instead of letting the model re-derive it per question."""
    bundle = _bundle(database)
    if bundle is None:
        return _unknown_database_error(database)
    return {"metrics": [m.model_dump() for m in bundle.metric_service.list_metrics()]}


@mcp.tool()
def compute_metric(
    database: str,
    name: str,
    start_date: str | None = None,
    end_date: str | None = None,
    session_variables: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compute a trusted business metric by name (from list_business_metrics)
    and return the real result. Some metrics need start_date/end_date
    (YYYY-MM-DD); list_business_metrics tells you which. session_variables
    (e.g. {"app.current_shop_id": "<uuid>"}) are applied for Postgres RLS
    policies, if the target database has any configured."""
    bundle = _bundle(database)
    if bundle is None:
        return _unknown_database_error(database)
    try:
        sql = bundle.metric_service.render_sql(name, start_date, end_date)
    except MetricError as exc:
        return {"error": str(exc)}
    try:
        result = bundle.query_service.execute(sql, session_variables=session_variables)
    except (SqlValidationError, QueryExecutionError) as exc:
        return {"error": str(exc)}
    metric = bundle.metric_service.get_metric(name)
    return {**result.model_dump(), "metric": metric.name, "metric_description": metric.description}


@mcp.tool()
def validate_sql(database: str, sql: str) -> dict[str, Any]:
    """Check that a SQL statement is a single safe read-only SELECT before executing it."""
    bundle = _bundle(database)
    if bundle is None:
        return _unknown_database_error(database)
    try:
        validated_sql = bundle.sql_validator.validate(sql)
        return {"valid": True, "sql": validated_sql}
    except SqlValidationError as exc:
        return {"valid": False, "error": str(exc)}


@mcp.tool()
def execute_readonly_sql(
    database: str, sql: str, session_variables: dict[str, str] | None = None
) -> dict[str, Any]:
    """Execute a validated read-only SELECT statement and return the result rows.
    session_variables (e.g. {"app.current_shop_id": "<uuid>"}) are applied for
    Postgres RLS policies, if the target database has any configured."""
    bundle = _bundle(database)
    if bundle is None:
        return _unknown_database_error(database)
    try:
        result = bundle.query_service.execute(sql, session_variables=session_variables)
        return result.model_dump()
    except (SqlValidationError, QueryExecutionError) as exc:
        return {"error": str(exc)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
