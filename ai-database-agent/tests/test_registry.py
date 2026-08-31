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


def test_unknown_database_name_raises_with_available_list(tmp_path, pg_test_connection):
    url = pg_test_connection.engine.url.render_as_string(hide_password=False)
    config_path = _write_config(tmp_path, {"testdb": {"database_url": url}})
    registry = DatabaseRegistry(config_path, FakeProvider())

    with pytest.raises(KeyError, match="testdb"):
        registry.get("nonexistent")


def test_list_databases_returns_configured_names(tmp_path, pg_test_connection):
    url = pg_test_connection.engine.url.render_as_string(hide_password=False)
    config_path = _write_config(
        tmp_path, {"testdb": {"database_url": url}, "other": {"database_url": url}}
    )
    registry = DatabaseRegistry(config_path, FakeProvider())

    assert registry.list_databases() == ["other", "testdb"]


def test_bundle_is_cached_across_get_calls(tmp_path, pg_test_connection):
    url = pg_test_connection.engine.url.render_as_string(hide_password=False)
    config_path = _write_config(tmp_path, {"testdb": {"database_url": url}})
    registry = DatabaseRegistry(config_path, FakeProvider())

    first = registry.get("testdb")
    second = registry.get("testdb")

    assert first is second


def test_each_database_gets_its_own_schema_scope(tmp_path, pg_test_connection, synthetic_schema):
    """Two entries pointing at the same physical database but different
    schemas/exclusions must not leak into each other -- this is the crux
    of what makes the agent usable for more than one database."""
    url = pg_test_connection.engine.url.render_as_string(hide_password=False)
    config_path = _write_config(
        tmp_path,
        {
            "public_only": {"database_url": url, "schemas": ["public"]},
            "fixtures_only": {
                "database_url": url,
                "schemas": [synthetic_schema],
                "excluded_tables": [f"{synthetic_schema}.secret_credential"],
            },
        },
    )
    registry = DatabaseRegistry(config_path, FakeProvider())

    public_tables = {t.name for t in registry.get("public_only").schema_service.get_schema().tables}
    fixture_tables = {t.name for t in registry.get("fixtures_only").schema_service.get_schema().tables}

    assert "spatial_ref_sys" in public_tables
    assert "region" not in public_tables

    assert "region" in fixture_tables
    assert "spatial_ref_sys" not in fixture_tables
    assert "secret_credential" not in fixture_tables


def test_context_path_is_loaded_and_appended_to_agent_system_prompt(tmp_path, pg_test_connection):
    context_file = tmp_path / "context.md"
    context_file.write_text("There is no table called widgets -- use widget.")

    url = pg_test_connection.engine.url.render_as_string(hide_password=False)
    config_path = _write_config(
        tmp_path, {"testdb": {"database_url": url, "context_path": "context.md"}}
    )
    registry = DatabaseRegistry(config_path, FakeProvider())

    bundle = registry.get("testdb")

    assert "widgets" in bundle.agent_service.system_prompt
    assert "use widget" in bundle.agent_service.system_prompt


def test_missing_context_path_leaves_prompt_unchanged(tmp_path, pg_test_connection):
    from dbagent.agent.service import SYSTEM_PROMPT

    url = pg_test_connection.engine.url.render_as_string(hide_password=False)
    config_path = _write_config(tmp_path, {"testdb": {"database_url": url}})
    registry = DatabaseRegistry(config_path, FakeProvider())

    bundle = registry.get("testdb")

    assert bundle.agent_service.system_prompt == SYSTEM_PROMPT
