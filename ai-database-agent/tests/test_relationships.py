import pytest

from dbagent.database import DatabaseConnection
from dbagent.services.schema_service import DatabaseSchemaService


@pytest.fixture(scope="module")
def schema_service(pg_test_connection: DatabaseConnection) -> DatabaseSchemaService:
    return DatabaseSchemaService(pg_test_connection.engine)


def test_find_relationships_for_table_with_no_foreign_keys(
    schema_service: DatabaseSchemaService,
):
    assert schema_service.find_relationships("spatial_ref_sys") == []


def test_find_relationships_for_unknown_table_is_empty(
    schema_service: DatabaseSchemaService,
):
    assert schema_service.find_relationships("does_not_exist") == []
