import sqlglot
from sqlglot import exp


class SqlValidationError(Exception):
    pass


# Any of these appearing ANYWHERE in the parsed AST is rejected -- not just
# at the top level. Postgres allows data-modifying CTEs (e.g.
# `WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x`), so a top-level
# type check alone is not enough; the whole tree must be walked.
DISALLOWED_EXPRESSION_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.TruncateTable,
    exp.Grant,
    exp.Revoke,
    exp.Command,  # catches CALL, DO, and other DDL/DCL sqlglot doesn't model explicitly
)

ALLOWED_TOP_LEVEL_TYPES = (exp.Select, exp.Union, exp.With, exp.Subquery)


class SqlValidator:
    """Parses generated SQL with sqlglot and only allows a single read-only
    SELECT statement through (Phase 8). This is mandatory -- SQL from an
    LLM is never trusted or executed without going through this first."""

    def __init__(self, dialect: str = "postgres", excluded_tables: set[str] | None = None):
        self._dialect = dialect
        self._excluded_tables = {t.lower() for t in (excluded_tables or set())}

    def validate(self, sql: str) -> str:
        """Return the validated SQL (re-rendered by the parser) or raise
        SqlValidationError."""
        cleaned = sql.strip().rstrip(";").strip()
        if not cleaned:
            raise SqlValidationError("Empty SQL statement.")

        try:
            statements = [s for s in sqlglot.parse(cleaned, dialect=self._dialect) if s]
        except Exception as exc:
            raise SqlValidationError(f"Could not parse SQL: {exc}") from exc

        if len(statements) != 1:
            raise SqlValidationError(
                f"Expected exactly one SQL statement, found {len(statements)}."
            )

        statement = statements[0]

        if not isinstance(statement, ALLOWED_TOP_LEVEL_TYPES):
            raise SqlValidationError(
                f"Only SELECT statements are allowed, got {type(statement).__name__}."
            )

        for node in statement.walk():
            if isinstance(node, DISALLOWED_EXPRESSION_TYPES):
                raise SqlValidationError(
                    f"Disallowed SQL construct: {type(node).__name__}."
                )

        if self._excluded_tables:
            for table_node in statement.find_all(exp.Table):
                qualified = f"{table_node.db}.{table_node.name}".lower()
                if qualified in self._excluded_tables:
                    raise SqlValidationError(
                        f"Access to table '{qualified}' is not permitted."
                    )

        return statement.sql(dialect=self._dialect)
