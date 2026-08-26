import pytest

from dbagent.database import DatabaseConnection
from dbagent.services.schema_service import DatabaseSchemaService


@pytest.fixture(scope="module")
def schema_service(
    pg_test_connection: DatabaseConnection, synthetic_schema: str
) -> DatabaseSchemaService:
    return DatabaseSchemaService(
        pg_test_connection.engine,
        schemas=[synthetic_schema],
        excluded_tables={f"{synthetic_schema}.secret_credential"},
    )


def test_tables_are_discovered(schema_service: DatabaseSchemaService):
    table_names = {t.name for t in schema_service.get_schema().tables}
    assert "region" in table_names
    assert "record" in table_names
    assert "widget" in table_names


def test_excluded_table_is_not_discoverable(schema_service: DatabaseSchemaService):
    table_names = {t.name for t in schema_service.get_schema().tables}
    assert "secret_credential" not in table_names


def test_relationships_are_discovered(schema_service: DatabaseSchemaService):
    """record has both a FK to region and a self-referencing FK to record
    itself (parent_record_id) -- mirrors the real-world shape this was
    written to cover without depending on any real business schema."""
    relationships = schema_service.find_relationships("record")
    targets = {(r.source_column, r.target_table) for r in relationships}
    assert ("region_id", "region") in targets
    assert ("parent_record_id", "record") in targets


def test_get_table_returns_columns_for_real_table(schema_service: DatabaseSchemaService):
    table = schema_service.get_table("record")
    assert table is not None
    column_names = {c.name for c in table.columns}
    assert "id" in column_names
    assert any(c.primary_key for c in table.columns)
