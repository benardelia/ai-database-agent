from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Which databases the agent can talk to, and how, is described by
    # databases.json (per-database URL/schemas/exclusions/glossary) -- not
    # by top-level settings here, so the agent isn't bound to one database.
    databases_config_path: str = "databases.json"

    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"


settings = Settings()
