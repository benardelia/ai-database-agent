import json

import pytest

from dbagent.registry import DatabaseRegistry


class FakeProvider:
    def chat(self, messages, tools=None):
        return {"role": "assistant", "content": "unused"}


def _write_config(tmp_path, entries: dict) -> str:
    config_path = tmp_path / "databases.json"
    config_path.write_text(json.dumps(entries))
    return str(config_path)


def test_missing_config_file_raises_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="databases.example.json"):
        DatabaseRegistry(str(tmp_path / "does_not_exist.json"), FakeProvider())


def test_unknown_database_name_raises_with_available_list(tmp_path, ilcms_db_connection):
    url = str(ilcms_db_connection.engine.url)
    config_path = _write_config(tmp_path, {"my_case_db": {"database_url": url}})
    registry = DatabaseRegistry(config_path, FakeProvider())

    with pytest.raises(KeyError, match="my_case_db"):
        registry.get("nonexistent")


def test_list_databases_returns_configured_names(tmp_path, ilcms_db_connection):
    url = str(ilcms_db_connection.engine.url)
    config_path = _write_config(
        tmp_path, {"my_case_db": {"database_url": url}, "other": {"database_url": url}}
    )
    registry = DatabaseRegistry(config_path, FakeProvider())

    assert registry.list_databases() == ["my_case_db", "other"]


def test_bundle_is_cached_across_get_calls(tmp_path, ilcms_db_connection):
    url = str(ilcms_db_connection.engine.url)
    config_path = _write_config(tmp_path, {"my_case_db": {"database_url": url}})
    registry = DatabaseRegistry(config_path, FakeProvider())

    first = registry.get("my_case_db")
    second = registry.get("my_case_db")

    assert first is second


def test_each_database_gets_its_own_schema_scope(tmp_path, ilcms_db_connection):
    """Two entries pointing at the same physical database but different
    schemas/exclusions must not leak into each other -- this is the crux
    of what makes the agent usable for more than one database."""
    url = str(ilcms_db_connection.engine.url)
    config_path = _write_config(
        tmp_path,
        {
            "public_only": {"database_url": url, "schemas": ["public"]},
            "ilcms_only": {
                "database_url": url,
                "schemas": ["my_case_db"],
                "excluded_tables": ["my_case_db.user_credentials"],
            },
        },
    )
    registry = DatabaseRegistry(config_path, FakeProvider())

    public_tables = {t.name for t in registry.get("public_only").schema_service.get_schema().tables}
    ilcms_tables = {t.name for t in registry.get("ilcms_only").schema_service.get_schema().tables}

    assert "spatial_ref_sys" in public_tables
    assert "record" not in public_tables

    assert "record" in ilcms_tables
    assert "spatial_ref_sys" not in ilcms_tables
    assert "user_credentials" not in ilcms_tables
