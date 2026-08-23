import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine

from dbagent.services.sql_validator import SqlValidator


class QueryExecutionError(Exception):
    pass


class QueryResult(BaseModel):
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    # Named returned_row_count (not row_count) deliberately: a small model
    # reading this JSON confused a sibling "row_count" field with the actual
    # answer -- e.g. it read row_count=1 (one row came back) and reported
    # "there is 1 ward" even though that one row's actual value, in `rows`,
    # was 0. This field describes the shape of the result, not its content.
    returned_row_count: int
    truncated: bool
    execution_time_ms: float


def _serialize_value(value: Any) -> Any:
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


class ReadOnlyQueryService:
    """Executes only SQL that has passed SqlValidator, inside an explicit
    read-only transaction with a statement timeout, and caps the number of
    rows pulled back (Phase 9-10). This is the only place SQL from the
    agent is ever actually run against the database."""

    def __init__(
        self,
        engine: Engine,
        validator: SqlValidator,
        statement_timeout_ms: int = 10_000,
        max_rows: int = 1_000,
        search_path: list[str] | None = None,
    ):
        self._engine = engine
        self._validator = validator
        self._statement_timeout_ms = statement_timeout_ms
        self._max_rows = max_rows
        # Configured schemas, so unqualified table names (which small models
        # frequently write even when the tool results show a schema_name)
        # resolve correctly instead of failing with "relation does not
        # exist" against the connection's bare default search_path.
        self._search_path_sql = (
            "SET LOCAL search_path TO "
            + ", ".join(f'"{s}"' for s in [*search_path, "public"])
            if search_path
            else None
        )

    def execute(self, sql: str) -> QueryResult:
        validated_sql = self._validator.validate(sql)

        start = time.perf_counter()
        try:
            with self._engine.connect() as conn:
                with conn.begin():
                    if self._search_path_sql:
                        conn.execute(text(self._search_path_sql))
                    conn.execute(
                        text(f"SET LOCAL statement_timeout = {self._statement_timeout_ms}")
                    )
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    result = conn.execute(text(validated_sql))

                    columns = list(result.keys())
                    rows: list[list[Any]] = []
                    truncated = False
                    for i, row in enumerate(result):
                        if i >= self._max_rows:
                            truncated = True
                            break
                        rows.append([_serialize_value(v) for v in row])
        except Exception as exc:
            raise QueryExecutionError(str(exc)) from exc

        elapsed_ms = (time.perf_counter() - start) * 1000

        return QueryResult(
            sql=validated_sql,
            columns=columns,
            rows=rows,
            returned_row_count=len(rows),
            truncated=truncated,
            execution_time_ms=elapsed_ms,
        )
