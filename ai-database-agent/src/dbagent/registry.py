import json
from pathlib import Path

from pydantic import BaseModel

from dbagent.agent.service import AgentService
from dbagent.ai.provider import LLMProvider
from dbagent.ai.tools import ToolExecutor
from dbagent.business.glossary_service import BusinessTermService
from dbagent.business.metric_service import MetricService
from dbagent.database import DatabaseConnection
from dbagent.services.query_service import ReadOnlyQueryService
from dbagent.services.sample_service import SampleDataService
from dbagent.services.schema_service import DatabaseSchemaService
from dbagent.services.search_service import SchemaSearchService
from dbagent.services.sql_validator import SqlValidator


class DatabaseProfile(BaseModel):
    """On-disk shape of one entry in databases.json."""

    database_url: str
    schemas: list[str] = ["public"]
    excluded_tables: list[str] = []
    glossary_path: str | None = None
    metrics_path: str | None = None
    context_path: str | None = None


class DatabaseBundle:
    """Everything needed to serve one configured database: its own engine,
    schema/search/validation/execution services, and an agent wired to
    them. Built lazily and cached per database name so each database gets
    exactly one connection pool, reused across requests."""

    def __init__(self, name: str, profile: DatabaseProfile, llm_provider: LLMProvider):
        self.name = name
        excluded = set(profile.excluded_tables)

        self.connection = DatabaseConnection(profile.database_url)
        self.schema_service = DatabaseSchemaService(
            self.connection.engine, schemas=profile.schemas, excluded_tables=excluded
        )
        self.glossary_service = (
            BusinessTermService(profile.glossary_path)
            if profile.glossary_path
            else BusinessTermService()
        )
        self.search_service = SchemaSearchService(self.schema_service, self.glossary_service)
        self.sql_validator = SqlValidator(excluded_tables=excluded)
        self.query_service = ReadOnlyQueryService(
            self.connection.engine, self.sql_validator, search_path=profile.schemas
        )
        self.sample_service = SampleDataService(self.schema_service, self.query_service)
        self.metric_service = MetricService(profile.metrics_path)
        self.tool_executor = ToolExecutor(
            self.schema_service,
            self.search_service,
            self.sql_validator,
            self.query_service,
            self.sample_service,
            self.metric_service,
        )
        database_context = (
            Path(profile.context_path).read_text() if profile.context_path else None
        )
        self.agent_service = AgentService(
            llm_provider, self.tool_executor, database_context=database_context
        )


class DatabaseRegistry:
    """Loads databases.json and hands out a DatabaseBundle per configured
    database name. This is what makes the agent database-agnostic: adding
    a new database is a config file entry, not a code change."""

    def __init__(self, config_path: str | Path, llm_provider: LLMProvider):
        self._config_path = Path(config_path)
        self._llm_provider = llm_provider
        self._profiles = self._load_profiles()
        self._bundles: dict[str, DatabaseBundle] = {}

    def _load_profiles(self) -> dict[str, DatabaseProfile]:
        if not self._config_path.exists():
            raise FileNotFoundError(
                f"Database config file not found: {self._config_path}. "
                "Copy databases.example.json to databases.json and fill in "
                "your connection details."
            )

        raw = json.loads(self._config_path.read_text())
        base_dir = self._config_path.parent
        profiles: dict[str, DatabaseProfile] = {}

        for name, entry in raw.items():
            profile = DatabaseProfile.model_validate(entry)
            if profile.glossary_path and not Path(profile.glossary_path).is_absolute():
                profile.glossary_path = str(base_dir / profile.glossary_path)
            if profile.metrics_path and not Path(profile.metrics_path).is_absolute():
                profile.metrics_path = str(base_dir / profile.metrics_path)
            if profile.context_path and not Path(profile.context_path).is_absolute():
                profile.context_path = str(base_dir / profile.context_path)
            profiles[name] = profile

        return profiles

    def list_databases(self) -> list[str]:
        return sorted(self._profiles.keys())

    def get(self, name: str) -> DatabaseBundle:
        if name not in self._profiles:
            raise KeyError(
                f"Unknown database '{name}'. Configured databases: {self.list_databases()}"
            )
        if name not in self._bundles:
            self._bundles[name] = DatabaseBundle(name, self._profiles[name], self._llm_provider)
        return self._bundles[name]
