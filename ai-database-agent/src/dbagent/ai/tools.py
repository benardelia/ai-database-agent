from typing import Any

from dbagent.business.metric_service import MetricError, MetricService
from dbagent.services.query_service import QueryExecutionError, ReadOnlyQueryService
from dbagent.services.sample_service import SampleDataError, SampleDataService
from dbagent.services.schema_service import DatabaseSchemaService
from dbagent.services.search_service import SchemaSearchService
from dbagent.services.sql_validator import SqlValidationError, SqlValidator

SEARCH_TABLES_TOOL = {
    "type": "function",
    "function": {
        "name": "search_tables",
        "description": (
            "Search database tables and columns relevant to a business "
            "concept or question. Use this before assuming a table exists."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword or business term, e.g. 'land rent' or 'transactions'",
                }
            },
            "required": ["query"],
        },
    },
}

GET_TABLE_SCHEMA_TOOL = {
    "type": "function",
    "function": {
        "name": "get_table_schema",
        "description": "Return columns, keys and relationships for a specific database table.",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Exact table name"}
            },
            "required": ["table"],
        },
    },
}

FIND_RELATIONSHIPS_TOOL = {
    "type": "function",
    "function": {
        "name": "find_relationships",
        "description": (
            "Return foreign key relationships involving a table, in either "
            "direction (tables it references and tables that reference it)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Exact table name"}
            },
            "required": ["table"],
        },
    },
}

GET_SAMPLE_ROWS_TOOL = {
    "type": "function",
    "function": {
        "name": "get_sample_rows",
        "description": (
            "Return a small number of example rows from a table so you can "
            "see what its values actually look like (e.g. that 'status' "
            "contains ACTIVE/PENDING/COMPLETED). Sensitive-looking columns "
            "(passwords, tokens, card numbers, etc.) are never included."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Exact table name"},
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (default 10)",
                },
            },
            "required": ["table"],
        },
    },
}

LIST_BUSINESS_METRICS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_business_metrics",
        "description": (
            "List trusted, pre-defined business metrics for this database "
            "(e.g. 'completed_widgets', 'total_revenue'). Check this before "
            "writing your own SQL for a concept that sounds like a standard "
            "business metric -- using a trusted definition avoids "
            "reinventing business logic inconsistently."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

COMPUTE_METRIC_TOOL = {
    "type": "function",
    "function": {
        "name": "compute_metric",
        "description": (
            "Compute a trusted business metric by name (from "
            "list_business_metrics) and return the real result. Some "
            "metrics need start_date/end_date (YYYY-MM-DD); "
            "list_business_metrics tells you which."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Exact metric name"},
                "start_date": {"type": "string", "description": "YYYY-MM-DD, if the metric needs a period"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD, if the metric needs a period"},
            },
            "required": ["name"],
        },
    },
}

VALIDATE_SQL_TOOL = {
    "type": "function",
    "function": {
        "name": "validate_sql",
        "description": (
            "Check that a generated SQL statement is a single safe read-only "
            "SELECT before it can be executed. Always call this before "
            "execute_readonly_sql."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "The SQL statement to validate"}
            },
            "required": ["sql"],
        },
    },
}

EXECUTE_READONLY_SQL_TOOL = {
    "type": "function",
    "function": {
        "name": "execute_readonly_sql",
        "description": (
            "Execute a validated read-only SELECT statement and return the "
            "result rows. Only call this after validate_sql has approved the "
            "same statement."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "The validated SQL statement to run"}
            },
            "required": ["sql"],
        },
    },
}

AGENT_TOOLS = [
    SEARCH_TABLES_TOOL,
    GET_TABLE_SCHEMA_TOOL,
    FIND_RELATIONSHIPS_TOOL,
    GET_SAMPLE_ROWS_TOOL,
    LIST_BUSINESS_METRICS_TOOL,
    COMPUTE_METRIC_TOOL,
    VALIDATE_SQL_TOOL,
    EXECUTE_READONLY_SQL_TOOL,
]
TOOL_NAMES = [t["function"]["name"] for t in AGENT_TOOLS]


class ToolExecutor:
    """Dispatches a tool call by name to the underlying schema services.
    This is the boundary the LLM cannot cross on its own -- it can only
    ever ask for what these methods choose to expose."""

    def __init__(
        self,
        schema_service: DatabaseSchemaService,
        search_service: SchemaSearchService,
        sql_validator: SqlValidator | None = None,
        query_service: ReadOnlyQueryService | None = None,
        sample_service: SampleDataService | None = None,
        metric_service: MetricService | None = None,
    ):
        self._schema_service = schema_service
        self._search_service = search_service
        self._sql_validator = sql_validator
        self._query_service = query_service
        self._sample_service = sample_service
        self._metric_service = metric_service

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._dispatch(tool_name, arguments)
        except KeyError as exc:
            return {
                "error": (
                    f"Missing required argument {exc} for tool '{tool_name}'. "
                    "Call it again with all required arguments."
                )
            }

    def _dispatch(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "search_tables":
            query = _get_arg(arguments, "query", "search_query", "term", "keyword")
            results = self._search_service.search_tables(query)
            return {"results": [r.model_dump() for r in results]}

        if tool_name == "get_table_schema":
            table_name = _get_arg(arguments, "table", "table_name", "from_table")
            table = self._schema_service.get_table(table_name)
            if table is None:
                return {"error": f"Table '{table_name}' was not found in the schema."}
            return table.model_dump()

        if tool_name == "find_relationships":
            table_name = _get_arg(arguments, "table", "table_name", "from_table", "source_table")
            relationships = self._schema_service.find_relationships(table_name)
            return {"relationships": [r.model_dump() for r in relationships]}

        if tool_name == "get_sample_rows":
            if self._sample_service is None:
                return {"error": "Sample data access is not available."}
            table_name = _get_arg(arguments, "table", "table_name", "from_table")
            limit = arguments.get("limit") or 10
            try:
                result, excluded_columns = self._sample_service.get_sample_rows(
                    table_name, limit=limit
                )
                return {
                    "columns": result.columns,
                    "rows": result.rows,
                    "excluded_sensitive_columns": excluded_columns,
                }
            except SampleDataError as exc:
                return {"error": str(exc)}

        if tool_name == "list_business_metrics":
            if self._metric_service is None:
                return {"metrics": []}
            return {"metrics": [m.model_dump() for m in self._metric_service.list_metrics()]}

        if tool_name == "compute_metric":
            if self._metric_service is None or self._query_service is None:
                return {"error": "Business metrics are not available."}
            metric_name = _get_arg(arguments, "name", "metric", "metric_name")
            start_date = arguments.get("start_date")
            end_date = arguments.get("end_date")
            try:
                sql = self._metric_service.render_sql(metric_name, start_date, end_date)
            except MetricError as exc:
                return {"error": str(exc)}
            try:
                result = self._query_service.execute(sql)
            except (SqlValidationError, QueryExecutionError) as exc:
                return {"error": str(exc)}
            metric = self._metric_service.get_metric(metric_name)
            return {
                **result.model_dump(),
                "metric": metric.name,
                "metric_description": metric.description,
            }

        if tool_name == "validate_sql":
            if self._sql_validator is None:
                return {"error": "SQL validation is not available."}
            sql = _get_arg(arguments, "sql", "query", "statement")
            try:
                validated_sql = self._sql_validator.validate(sql)
                return {"valid": True, "sql": validated_sql}
            except SqlValidationError as exc:
                return {"valid": False, "error": str(exc)}

        if tool_name == "execute_readonly_sql":
            if self._query_service is None:
                return {"error": "SQL execution is not available."}
            sql = _get_arg(arguments, "sql", "query", "statement")
            try:
                result = self._query_service.execute(sql)
                return result.model_dump()
            except (SqlValidationError, QueryExecutionError) as exc:
                return {"error": str(exc)}

        return {"error": f"Unknown tool '{tool_name}'"}


def _get_arg(arguments: dict[str, Any], canonical: str, *aliases: str) -> Any:
    """Small local models don't always name arguments exactly as the tool
    schema declares (e.g. 'from_table' instead of 'table'). Accept the
    common near-misses instead of relying on the model to read an error
    message and self-correct, which is unreliable for smaller models."""
    for key in (canonical, *aliases):
        if key in arguments and arguments[key] not in (None, "", []):
            return arguments[key]
    raise KeyError(f"'{canonical}'")
