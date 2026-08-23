import pytest

from dbagent.database import DatabaseConnection
from dbagent.services.schema_service import DatabaseSchemaService

EXCLUDED = {
    "my_case_db.user_credentials",
    "my_case_db.oauth_authorization",
    "my_case_db.oauth_consent",
    "my_case_db.oauth_refresh_token",
    "my_case_db.oauth_registered_client",
}


@pytest.fixture(scope="module")
def schema_service(ilcms_db_connection: DatabaseConnection) -> DatabaseSchemaService:
    return DatabaseSchemaService(
        ilcms_db_connection.engine, schemas=["my_case_db", "app_schema_dict"], excluded_tables=EXCLUDED
    )


def test_real_business_tables_are_discovered(schema_service: DatabaseSchemaService):
    table_names = {t.name for t in schema_service.get_schema().tables}
    assert "record" in table_names
    assert "person" in table_names
    assert "region" in table_names
    assert "subregion" in table_names


def test_excluded_credential_tables_are_not_discoverable(
    schema_service: DatabaseSchemaService,
):
    table_names = {t.name for t in schema_service.get_schema().tables}
    assert "user_credentials" not in table_names
    assert "oauth_authorization" not in table_names
    assert "oauth_refresh_token" not in table_names


def test_dm_case_relationships_are_discovered(schema_service: DatabaseSchemaService):
    relationships = schema_service.find_relationships("record")
    targets = {(r.source_column, r.target_table) for r in relationships}
    assert ("transaction_id", "transaction") in targets
    assert ("parent_record_id", "record") in targets


def test_get_table_returns_columns_for_real_table(schema_service: DatabaseSchemaService):
    table = schema_service.get_table("record")
    assert table is not None
    column_names = {c.name for c in table.columns}
    assert "id" in column_names
    assert any(c.primary_key for c in table.columns)
