import re

from dbagent.services.query_service import QueryResult, ReadOnlyQueryService
from dbagent.services.schema_service import DatabaseSchemaService

# Column names matching this are never included in a sample, regardless of
# the table's own SELECT grants -- sample rows are meant to help the agent
# understand what a column's values look like (Phase 14), not to leak
# credentials/PII through the back door of "just show me some rows".
SENSITIVE_COLUMN_PATTERN = re.compile(
    r"(password|passwd|token|secret|api[_-]?key|credit[_-]?card|card[_-]?number|"
    r"cvv|bank[_-]?account|ssn|\bnin\b|passport|session[_-]?key)",
    re.IGNORECASE,
)


class SampleDataError(Exception):
    pass


class SampleDataService:
    """Phase 14: bounded, column-filtered sample rows so the agent can see
    what a table's values actually look like (e.g. status is one of
    ACTIVE/PENDING/COMPLETED) without pulling sensitive columns or full
    unbounded row dumps. Goes through the same ReadOnlyQueryService as
    everything else, so validation/read-only/timeout/row-cap all apply."""

    def __init__(self, schema_service: DatabaseSchemaService, query_service: ReadOnlyQueryService):
        self._schema_service = schema_service
        self._query_service = query_service

    def get_sample_rows(self, table_name: str, limit: int = 10) -> tuple[QueryResult, list[str]]:
        table = self._schema_service.get_table(table_name)
        if table is None:
            raise SampleDataError(f"Table '{table_name}' was not found in the schema.")

        safe_columns = [c.name for c in table.columns if not SENSITIVE_COLUMN_PATTERN.search(c.name)]
        excluded_columns = [c.name for c in table.columns if c.name not in safe_columns]

        if not safe_columns:
            raise SampleDataError(
                f"Table '{table_name}' has no columns considered safe to sample."
            )

        column_list = ", ".join(f'"{c}"' for c in safe_columns)
        sql = f'SELECT {column_list} FROM "{table.schema_name}"."{table.name}" LIMIT {int(limit)}'

        result = self._query_service.execute(sql)
        return result, excluded_columns
