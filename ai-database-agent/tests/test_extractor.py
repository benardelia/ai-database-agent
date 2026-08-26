import pytest

from dbagent.database import DatabaseConnection
from dbagent.metadata.extractor import MetadataExtractor
from dbagent.metadata.models import DatabaseSchema


@pytest.fixture(scope="module")
def schema(pg_test_connection: DatabaseConnection) -> DatabaseSchema:
    extractor = MetadataExtractor(pg_test_connection.engine)
    return extractor.extract(database_name="test_db", schemas="public")


def test_extract_returns_database_schema(schema: DatabaseSchema):
    assert schema.database_name == "test_db"
    assert isinstance(schema.tables, list)


def test_known_table_is_discovered(schema: DatabaseSchema):
    table = schema.find_table("spatial_ref_sys")
    assert table is not None
    assert table.schema_name == "public"
    assert any(col.primary_key for col in table.columns)


def test_columns_have_data_types(schema: DatabaseSchema):
    table = schema.find_table("spatial_ref_sys")
    assert table is not None
    assert all(col.data_type for col in table.columns)
