from sqlalchemy.engine import Engine

from dbagent.metadata.extractor import MetadataExtractor
from dbagent.metadata.models import DatabaseSchema, RelationshipMetadata, TableMetadata


class DatabaseSchemaService:
    """Normalized, cached view of the database schema.

    Raw information_schema/pg_catalog metadata is never handed to callers
    directly -- everything is exposed through the DatabaseSchema model so
    the schema only needs to be extracted once per refresh.
    """

    def __init__(
        self,
        engine: Engine,
        schemas: str | list[str] = "public",
        excluded_tables: set[str] | None = None,
    ):
        self._engine = engine
        self._schemas = schemas
        self._excluded_tables = excluded_tables or set()
        self._extractor = MetadataExtractor(engine)
        self._cache: DatabaseSchema | None = None

    def get_schema(self, refresh: bool = False) -> DatabaseSchema:
        if refresh or self._cache is None:
            full = self._extractor.extract(
                database_name=self._engine.url.database, schemas=self._schemas
            )
            full.tables = [
                t for t in full.tables if t.qualified_name not in self._excluded_tables
            ]
            self._cache = full
        return self._cache

    def get_table(self, table_name: str) -> TableMetadata | None:
        return self.get_schema().find_table(table_name)

    def find_relationships(self, table_name: str) -> list[RelationshipMetadata]:
        """Relationships involving a table in either direction: foreign
        keys the table owns (outgoing) and foreign keys other tables hold
        that point back to it (incoming)."""
        schema = self.get_schema()
        return [
            rel
            for rel in schema.all_relationships()
            if rel.source_table == table_name or rel.target_table == table_name
        ]
