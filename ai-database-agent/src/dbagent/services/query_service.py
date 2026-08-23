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
    row_count: int
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
    ):
        self._engine = engine
        self._validator = validator
        self._statement_timeout_ms = statement_timeout_ms
        self._max_rows = max_rows

    def execute(self, sql: str) -> QueryResult:
        validated_sql = self._validator.validate(sql)

        start = time.perf_counter()
        try:
            with self._engine.connect() as conn:
                with conn.begin():
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
            row_count=len(rows),
            truncated=truncated,
            execution_time_ms=elapsed_ms,
        )
