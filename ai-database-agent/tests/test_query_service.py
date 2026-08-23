import pytest

from dbagent.database import DatabaseConnection
from dbagent.services.query_service import QueryExecutionError, ReadOnlyQueryService
from dbagent.services.sql_validator import SqlValidator


@pytest.fixture(scope="module")
def query_service(ilcms_db_connection: DatabaseConnection) -> ReadOnlyQueryService:
    return ReadOnlyQueryService(ilcms_db_connection.engine, SqlValidator())


def test_execute_returns_structured_result(query_service: ReadOnlyQueryService):
    result = query_service.execute(
        "SELECT srid, auth_name FROM spatial_ref_sys ORDER BY srid LIMIT 3"
    )
    assert result.columns == ["srid", "auth_name"]
    assert result.row_count == 3
    assert not result.truncated
    assert len(result.rows) == 3


def test_execute_caps_rows_and_flags_truncated(ilcms_db_connection: DatabaseConnection):
    service = ReadOnlyQueryService(ilcms_db_connection.engine, SqlValidator(), max_rows=5)
    result = service.execute("SELECT srid FROM spatial_ref_sys")
    assert result.row_count == 5
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
