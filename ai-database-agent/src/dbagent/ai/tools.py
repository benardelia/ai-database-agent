from typing import Any

from dbagent.services.query_service import QueryExecutionError, ReadOnlyQueryService
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
    ):
        self._schema_service = schema_service
        self._search_service = search_service
        self._sql_validator = sql_validator
        self._query_service = query_service

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
            results = self._search_service.search_tables(arguments["query"])
            return {"results": [r.model_dump() for r in results]}

        if tool_name == "get_table_schema":
            table = self._schema_service.get_table(arguments["table"])
            if table is None:
                return {"error": f"Table '{arguments['table']}' was not found in the schema."}
            return table.model_dump()

        if tool_name == "find_relationships":
            relationships = self._schema_service.find_relationships(arguments["table"])
            return {"relationships": [r.model_dump() for r in relationships]}

        if tool_name == "validate_sql":
            if self._sql_validator is None:
                return {"error": "SQL validation is not available."}
            try:
                validated_sql = self._sql_validator.validate(arguments["sql"])
                return {"valid": True, "sql": validated_sql}
            except SqlValidationError as exc:
                return {"valid": False, "error": str(exc)}

        if tool_name == "execute_readonly_sql":
            if self._query_service is None:
                return {"error": "SQL execution is not available."}
            try:
                result = self._query_service.execute(arguments["sql"])
                return result.model_dump()
            except (SqlValidationError, QueryExecutionError) as exc:
                return {"error": str(exc)}

        return {"error": f"Unknown tool '{tool_name}'"}
