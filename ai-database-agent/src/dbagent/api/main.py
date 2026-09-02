import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from dbagent.agent.models import AgentResponse
from dbagent.ai.provider import build_ollama_provider
from dbagent.config import settings
from dbagent.metadata.models import DatabaseSchema, RelationshipMetadata, TableSearchResult
from dbagent.registry import DatabaseBundle, DatabaseRegistry

# Nothing else in the process configures a handler -- without this, every
# logger.info() call across the app (including AgentService's per-tool-call
# trace) is silently dropped, and `docker compose logs` shows nothing but
# uvicorn's own final "200 OK" access line with no visibility into what the
# agent actually did to get there.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Database Reasoning Agent")

registry = DatabaseRegistry(settings.databases_config_path, build_ollama_provider())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Phase 40 (Error Recovery), applied broadly: LLM failures already come
    # back as a normal AgentResponse (see AgentService.ask), but a failure
    # in schema/metadata operations -- e.g. the database itself being
    # unreachable, observed live during Docker smoke-testing -- wasn't
    # covered by that and would otherwise surface as a raw 500 with a
    # stack trace leaking internals to the caller. Log the real exception
    # server-side, return something a caller can actually act on.
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal error: {exc}"},
    )


def _bundle(database: str) -> DatabaseBundle:
    try:
        return registry.get(database)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class AgentQueryRequest(BaseModel):
    database: str
    question: str
    # e.g. {"app.current_shop_id": "<uuid>"} -- forwarded to every SQL call
    # made while answering this question, consumed by Postgres RLS policies
    # (see the "app." namespace restriction in ReadOnlyQueryService). This
    # is how row-level tenant isolation gets enforced at the database
    # level instead of relying on prompt text.
    session_variables: dict[str, str] | None = None


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
    return _bundle(request.database).agent_service.ask(
        request.question, session_variables=request.session_variables
    )
