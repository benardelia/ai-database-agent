from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from dbagent.agent.models import AgentResponse
from dbagent.ai.provider import build_ollama_provider
from dbagent.config import settings
from dbagent.metadata.models import DatabaseSchema, RelationshipMetadata, TableSearchResult
from dbagent.registry import DatabaseBundle, DatabaseRegistry

app = FastAPI(title="AI Database Reasoning Agent")

registry = DatabaseRegistry(settings.databases_config_path, build_ollama_provider())


def _bundle(database: str) -> DatabaseBundle:
    try:
        return registry.get(database)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class AgentQueryRequest(BaseModel):
    database: str
    question: str


@app.get("/api/databases")
def list_databases() -> list[str]:
    return registry.list_databases()


@app.get("/api/schema", response_model=DatabaseSchema)
def get_schema(database: str, refresh: bool = False) -> DatabaseSchema:
    return _bundle(database).schema_service.get_schema(refresh=refresh)


@app.get("/api/schema/search", response_model=list[TableSearchResult])
def search_tables(database: str, query: str, limit: int = 20) -> list[TableSearchResult]:
    return _bundle(database).search_service.search_tables(query, limit=limit)


@app.get(
    "/api/schema/relationships/{table_name}", response_model=list[RelationshipMetadata]
)
def get_relationships(database: str, table_name: str) -> list[RelationshipMetadata]:
    return _bundle(database).schema_service.find_relationships(table_name)


@app.get("/api/glossary")
def get_glossary(database: str) -> dict[str, list[str]]:
    return _bundle(database).glossary_service.all_terms()


@app.post("/api/ai/query", response_model=AgentResponse)
def ai_query(request: AgentQueryRequest) -> AgentResponse:
    return _bundle(request.database).agent_service.ask(request.question)
