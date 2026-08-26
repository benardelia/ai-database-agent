import pytest

from dbagent.database import DatabaseConnection
from dbagent.services.query_service import QueryExecutionError, ReadOnlyQueryService
from dbagent.services.sql_validator import SqlValidator


@pytest.fixture(scope="module")
def query_service(pg_test_connection: DatabaseConnection) -> ReadOnlyQueryService:
    return ReadOnlyQueryService(pg_test_connection.engine, SqlValidator())


def test_execute_returns_structured_result(query_service: ReadOnlyQueryService):
    result = query_service.execute(
        "SELECT srid, auth_name FROM spatial_ref_sys ORDER BY srid LIMIT 3"
    )
    assert result.columns == ["srid", "auth_name"]
    assert result.returned_row_count == 3
    assert not result.truncated
    assert len(result.rows) == 3


def test_execute_caps_rows_and_flags_truncated(pg_test_connection: DatabaseConnection):
    service = ReadOnlyQueryService(pg_test_connection.engine, SqlValidator(), max_rows=5)
    result = service.execute("SELECT srid FROM spatial_ref_sys")
    assert result.returned_row_count == 5
    assert result.truncated is True


def test_execute_rejects_destructive_sql(query_service: ReadOnlyQueryService):
    with pytest.raises(Exception):
        query_service.execute("DELETE FROM spatial_ref_sys")


def test_execute_wraps_db_errors(query_service: ReadOnlyQueryService):
    with pytest.raises(QueryExecutionError):
        query_service.execute("SELECT nonexistent_column FROM spatial_ref_sys")


def test_transaction_is_actually_read_only(query_service: ReadOnlyQueryService):
    """Confirms `SET TRANSACTION READ ONLY` is actually taking effect at
    the database level, not just that the validator rejected DML syntax."""
    result = query_service.execute("SELECT current_setting('transaction_read_only') AS ro")
    assert result.rows == [["on"]]


def test_search_path_resolves_unqualified_table_names(
    pg_test_connection: DatabaseConnection, synthetic_schema: str
):
    """ai_readonly's default search_path is just '$user, public', so an
    unqualified table name from a non-public schema fails to resolve
    unless the query service sets search_path to the database's
    configured schemas -- exactly the failure observed live with the
    agent writing an unqualified query against a non-public table."""
    service = ReadOnlyQueryService(
        pg_test_connection.engine, SqlValidator(), search_path=[synthetic_schema]
    )
    result = service.execute("SELECT COUNT(*) AS n FROM region")
    assert result.rows == [[3]]


def test_without_search_path_unqualified_nonpublic_table_fails(
    pg_test_connection: DatabaseConnection, synthetic_schema: str
):
    service = ReadOnlyQueryService(pg_test_connection.engine, SqlValidator())
    with pytest.raises(QueryExecutionError):
        service.execute("SELECT COUNT(*) FROM region")
