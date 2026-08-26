import pytest

from dbagent.database import DatabaseConnection
from dbagent.services.schema_service import DatabaseSchemaService
from dbagent.services.search_service import SchemaSearchService


@pytest.fixture(scope="module")
def search_service(pg_test_connection: DatabaseConnection) -> SchemaSearchService:
    schema_service = DatabaseSchemaService(pg_test_connection.engine)
    return SchemaSearchService(schema_service)


def test_search_matches_table_name_substring(search_service: SchemaSearchService):
    results = search_service.search_tables("geometry")
    table_names = [r.table for r in results]
    assert "geometry_columns" in table_names


def test_search_matches_column_name(search_service: SchemaSearchService):
    results = search_service.search_tables("srid")
    table_names = [r.table for r in results]
    assert "spatial_ref_sys" in table_names
    assert all(r.score > 0 for r in results)


def test_search_results_are_ranked_descending(search_service: SchemaSearchService):
    results = search_service.search_tables("geometry")
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_unrelated_query_returns_no_results(search_service: SchemaSearchService):
    results = search_service.search_tables("xyzzy_nonexistent_term")
    assert results == []
