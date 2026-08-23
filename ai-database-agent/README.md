# AI Database Reasoning Agent

Incremental implementation following `AI_Database_Reasoning_Agent_Implementation_Guide.md`,
built against the real local `my_case_db` database (a land case management system: `record`,
`person`, `transaction`, `region`, `subregion`, etc., in the `my_case_db` / `app_schema_dict`
schemas -- not `public`, which only holds PostGIS system tables).

## Status

**Milestones 1-5 of the guide are implemented:**

- **Phase 1** -- connects through a dedicated read-only role (`ai_readonly`). Grants are scoped
  to `my_case_db` + `app_schema_dict` only, with `user_credentials` and the `oauth2_*` tables
  explicitly excluded (see "Security notes" below).
- **Phase 2** -- `MetadataExtractor` reads `information_schema` / `pg_catalog`, across multiple
  schemas.
- **Phase 3** -- normalized into `DatabaseSchema` / `TableMetadata` / `ColumnMetadata` /
  `RelationshipMetadata` Pydantic models. `DatabaseSchemaService` caches the extracted schema.
- **Phase 4** -- `SchemaSearchService.search_tables()`: keyword scoring over table/column
  names, comments, and business-glossary aliases.
- **Phase 5** -- `BusinessTermService` / `glossary.json`: maps user terminology (e.g. "owner")
  to database terminology (e.g. `right_holder`).
- Relationship discovery: `DatabaseSchemaService.find_relationships()` (both directions),
  verified against real FKs (`record.transaction_id -> transaction.id`, etc.).
- **Phase 6 + 11 (tool calling)** -- `LLMProvider` abstraction with `OllamaProvider` as the
  primary backend (local, `llama3.2`); `AgentService` runs a bounded tool-calling loop with
  `max_steps` / `max_tool_calls` guards (Phase 30), temperature=0 for determinism, and bounded
  self-correction nudges (Phase 40) for stray tool-calls-as-text and premature stalls.
- **Phase 7** -- NL -> SQL: the model itself writes the SELECT statement after inspecting real
  schema via tools (no separate SQL-generation service; matches Strategy B in Section 32).
- **Phase 8** -- `SqlValidator` (sqlglot): only a single read-only SELECT/CTE/UNION statement is
  allowed. Walks the *entire* AST (not just the top level), so a data-modifying CTE like
  `WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x` is caught even though it "looks like"
  a SELECT. Also enforces a table-level denylist as a second layer on top of DB grants.
- **Phase 9-10** -- `ReadOnlyQueryService`: explicit `SET TRANSACTION READ ONLY` +
  `statement_timeout`, row cap with a `truncated` flag, structured `QueryResult`, verified
  against real data (aggregate query returns the correct value).

Endpoints: `GET /api/schema`, `GET /api/schema/search`, `GET /api/schema/relationships/{table}`,
`GET /api/glossary`, `POST /api/ai/query`.

`scripts/print_schema.py` prints the schema to the console (the Section 81 success condition).
`scripts/ask_agent.py "<question>"` runs a question through the live agent + Ollama.

### Known limitation: small local model reliability

`llama3.2:3b` reliably handles 1-2 hop questions (schema lookups, simple searches) but can
stall or send malformed arguments on longer chains (search -> inspect -> validate -> execute).
The agent handles this gracefully (bounded nudges, tool-argument errors are fed back to the
model instead of crashing -- see `test_tool_executor.py`), but doesn't force a correct outcome
every time. The validation/execution layers themselves are correct and safe regardless of what
the model does or how it fails (verified independent of the LLM in `test_sql_validator.py` /
`test_query_service.py`). A larger tool-calling model (e.g. `llama3.1:8b`, `qwen2.5:7b`) would
likely be more reliable end-to-end; not pulled yet due to limited local disk space (~5GB free).

Not yet implemented: result interpretation as a distinct layer (currently folded into the
agent's final answer), MCP server, semantic layer / business metrics registry, embeddings/RAG,
multi-step analytics, and everything past Milestone 5 in the guide.

## Security notes

- `ai_readonly` has `SELECT` only, scoped to `my_case_db` + `app_schema_dict`. `user_credentials`
  and `oauth2_*` are explicitly `REVOKE`d.
- Postgres exposes table/column *metadata* to any role with schema `USAGE`, regardless of
  `SELECT` grants -- so those excluded tables are *also* filtered out at the app layer
  (`Settings.excluded_table_set`, applied in `DatabaseSchemaService` and `SqlValidator`) so the
  agent never discovers they exist, not just that it can't read them.
- No `ALTER DEFAULT PRIVILEGES` was set on `my_case_db`/`app_schema_dict` -- new tables added later need
  an explicit grant + exclusion-list review before the agent can see them.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in DATABASE_URL with your ai_readonly credentials
```

Requires Ollama running locally with the configured model pulled:

```bash
brew install ollama
brew services start ollama
ollama pull llama3.2
```

## Run

Print the schema:

```bash
python scripts/print_schema.py
```

Ask the agent a question:

```bash
python scripts/ask_agent.py "How many districts are there in total?"
```

Run the API:

```bash
uvicorn dbagent.api.main:app --reload --app-dir src
```

Then `curl http://localhost:8000/api/schema`.

## Test

```bash
pytest
```
