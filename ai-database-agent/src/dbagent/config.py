from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor default paths to the project root (not the process's CWD) so the
# app behaves the same whether it's launched via `python scripts/...` from
# the project directory, `uvicorn ... --app-dir src` from elsewhere, or
# packaged/deployed. config.py lives at <root>/src/dbagent/config.py.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"), extra="ignore"
    )

    # Which databases the agent can talk to, and how, is described by
    # databases.json (per-database URL/schemas/exclusions/glossary) -- not
    # by top-level settings here, so the agent isn't bound to one database.
    databases_config_path: str = str(PROJECT_ROOT / "databases.json")

    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_timeout_seconds: float = 300.0
    # How long Ollama keeps the model resident in memory between requests.
    # Idling past this triggers a full reload on the next request, which
    # can itself take long enough to blow past a per-call timeout.
    ollama_keep_alive: str = "30m"


settings = Settings()
