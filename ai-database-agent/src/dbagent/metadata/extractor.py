from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from dbagent.metadata.models import (
    ColumnMetadata,
    DatabaseSchema,
    IndexMetadata,
    RelationshipMetadata,
    TableMetadata,
)

ROW_ESTIMATE_SQL = text(
    """
    SELECT relname AS table_name, reltuples::bigint AS estimate
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema AND c.relkind IN ('r', 'p')
    """
)

TABLE_COMMENT_SQL = text(
    """
    SELECT c.relname AS table_name, obj_description(c.oid) AS comment
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema AND c.relkind IN ('r', 'p', 'v')
    """
)


class MetadataExtractor:
    """Inspects PostgreSQL metadata (information_schema / pg_catalog) and
    normalizes it into the application's own metadata model."""

    def __init__(self, engine: Engine):
        self._engine = engine

    def extract(
        self, database_name: str, schemas: str | list[str] = "public"
    ) -> DatabaseSchema:
        schema_list = [schemas] if isinstance(schemas, str) else list(schemas)
        inspector = inspect(self._engine)

        tables: list[TableMetadata] = []

        for schema in schema_list:
            row_estimates = self._row_estimates(schema)
            comments = self._table_comments(schema)

            for table_name in inspector.get_table_names(schema=schema):
                tables.append(
                    self._build_table(
                        inspector, schema, table_name, "table", row_estimates, comments
                    )
                )

            for view_name in inspector.get_view_names(schema=schema):
                tables.append(
                    self._build_table(
                        inspector, schema, view_name, "view", row_estimates, comments
                    )
                )

        return DatabaseSchema(database_name=database_name, tables=tables)

    def _build_table(
        self,
        inspector,
        schema: str,
        table_name: str,
        table_type: str,
        row_estimates: dict[str, int],
        comments: dict[str, str],
    ) -> TableMetadata:
        pk_columns = set(
            inspector.get_pk_constraint(table_name, schema=schema).get(
                "constrained_columns"
            )
            or []
        )

        fk_columns: dict[str, RelationshipMetadata] = {}
        for fk in inspector.get_foreign_keys(table_name, schema=schema):
            constrained = fk.get("constrained_columns") or []
            referred_columns = fk.get("referred_columns") or []
            for source_col, target_col in zip(constrained, referred_columns):
                fk_columns[source_col] = RelationshipMetadata(
                    source_table=table_name,
                    source_column=source_col,
                    target_table=fk.get("referred_table"),
                    target_column=target_col,
                    constraint_name=fk.get("name"),
                )

        columns: list[ColumnMetadata] = []
        for col in inspector.get_columns(table_name, schema=schema):
            columns.append(
                ColumnMetadata(
                    name=col["name"],
                    data_type=str(col["type"]),
                    nullable=col.get("nullable", True),
                    primary_key=col["name"] in pk_columns,
                    foreign_key=col["name"] in fk_columns,
                    default=(
                        str(col["default"]) if col.get("default") is not None else None
                    ),
                    comment=col.get("comment"),
                )
            )

        indexes: list[IndexMetadata] = []
        for idx in inspector.get_indexes(table_name, schema=schema):
            indexes.append(
                IndexMetadata(
                    name=idx["name"],
                    columns=list(idx.get("column_names") or []),
                    unique=idx.get("unique", False),
                )
            )
        if pk_columns:
            indexes.append(
                IndexMetadata(
                    name=f"{table_name}_pkey",
                    columns=sorted(pk_columns),
                    unique=True,
                    primary=True,
                )
            )

        return TableMetadata(
            schema_name=schema,
            name=table_name,
            table_type=table_type,
            description=comments.get(table_name),
            columns=columns,
            indexes=indexes,
            relationships=list(fk_columns.values()),
            estimated_row_count=row_estimates.get(table_name),
        )

    def _row_estimates(self, schema: str) -> dict[str, int]:
        with self._engine.connect() as conn:
            rows = conn.execute(ROW_ESTIMATE_SQL, {"schema": schema})
            return {row.table_name: int(row.estimate) for row in rows}

    def _table_comments(self, schema: str) -> dict[str, str]:
        with self._engine.connect() as conn:
            rows = conn.execute(TABLE_COMMENT_SQL, {"schema": schema})
            return {row.table_name: row.comment for row in rows if row.comment}
