import pytest

from dbagent.database import DatabaseConnection
from dbagent.services.query_service import ReadOnlyQueryService
from dbagent.services.sample_service import SampleDataError, SampleDataService
from dbagent.services.schema_service import DatabaseSchemaService
from dbagent.services.sql_validator import SqlValidator


@pytest.fixture(scope="module")
def sample_service(ilcms_db_connection: DatabaseConnection) -> SampleDataService:
    schema_service = DatabaseSchemaService(ilcms_db_connection.engine)
    query_service = ReadOnlyQueryService(ilcms_db_connection.engine, SqlValidator())
    return SampleDataService(schema_service, query_service)


def test_get_sample_rows_returns_bounded_rows(sample_service: SampleDataService):
    result, excluded = sample_service.get_sample_rows("spatial_ref_sys", limit=3)
    assert result.columns == ["srid", "auth_name", "auth_srid", "srtext", "proj4text"]
    assert len(result.rows) == 3
    assert excluded == []


def test_get_sample_rows_unknown_table_raises(sample_service: SampleDataService):
    with pytest.raises(SampleDataError, match="not found"):
        sample_service.get_sample_rows("does_not_exist")


def test_get_sample_rows_excludes_sensitive_columns():
    """user_account_table has a real 'password' column mixed in with ordinary
    columns (username, email, ...). Table-level exclusion already keeps
    the agent from reaching user_account_table in production (see databases.json,
    which REVOKEs SELECT for ai_readonly on this exact table) -- this test
    uses a superuser connection specifically to bypass that DB-level grant
    and isolate the sample service's OWN column-level defense: even with
    unrestricted DB access, 'password' must never appear in a sample."""
    superuser_connection = DatabaseConnection(
        "postgresql+psycopg://mac@localhost:5432/my_store_db"
    )
    schema_service = DatabaseSchemaService(superuser_connection.engine, schemas=["public"])
    query_service = ReadOnlyQueryService(
        superuser_connection.engine, SqlValidator(), search_path=["public"]
    )
    service = SampleDataService(schema_service, query_service)

    table = schema_service.get_table("user_account_table")
    assert table is not None
    assert any(c.name == "password" for c in table.columns)

    result, excluded = service.get_sample_rows("user_account_table", limit=1)
    assert "password" in excluded
    assert "password" not in result.columns
