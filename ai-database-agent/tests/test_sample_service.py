import pytest

from dbagent.database import DatabaseConnection
from dbagent.services.query_service import ReadOnlyQueryService
from dbagent.services.sample_service import SampleDataError, SampleDataService
from dbagent.services.schema_service import DatabaseSchemaService
from dbagent.services.sql_validator import SqlValidator


@pytest.fixture(scope="module")
def sample_service(pg_test_connection: DatabaseConnection) -> SampleDataService:
    schema_service = DatabaseSchemaService(pg_test_connection.engine)
    query_service = ReadOnlyQueryService(pg_test_connection.engine, SqlValidator())
    return SampleDataService(schema_service, query_service)


def test_get_sample_rows_returns_bounded_rows(sample_service: SampleDataService):
    result, excluded = sample_service.get_sample_rows("spatial_ref_sys", limit=3)
    assert result.columns == ["srid", "auth_name", "auth_srid", "srtext", "proj4text"]
    assert len(result.rows) == 3
    assert excluded == []


def test_get_sample_rows_unknown_table_raises(sample_service: SampleDataService):
    with pytest.raises(SampleDataError, match="not found"):
        sample_service.get_sample_rows("does_not_exist")


def test_get_sample_rows_excludes_sensitive_columns(
    pg_test_connection: DatabaseConnection, synthetic_schema: str
):
    """`account` has a real 'password' column mixed in with ordinary
    columns (username, ...). Table-level exclusion is a separate defense
    (see DatabaseProfile.excluded_tables) -- this isolates the sample
    service's OWN column-level defense: even on an otherwise-fully-
    accessible table, 'password' must never appear in a sample."""
    schema_service = DatabaseSchemaService(pg_test_connection.engine, schemas=[synthetic_schema])
    query_service = ReadOnlyQueryService(
        pg_test_connection.engine, SqlValidator(), search_path=[synthetic_schema]
    )
    service = SampleDataService(schema_service, query_service)

    table = schema_service.get_table("account")
    assert table is not None
    assert any(c.name == "password" for c in table.columns)

    result, excluded = service.get_sample_rows("account", limit=1)
    assert "password" in excluded
    assert "password" not in result.columns
