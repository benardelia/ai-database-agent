# AI Database Reasoning Agent

Incremental implementation following `AI_Database_Reasoning_Agent_Implementation_Guide.md`.
Not bound to one database -- any number of PostgreSQL databases can be configured via
`databases.json` and selected at request time (Phase 50: multi-database support). Currently
configured with one real database, `my_case_db` (a land case management system: `record`,
`person`, `transaction`, `region`, `subregion`, etc., in the `my_case_db` / `app_schema_dict`
schemas -- not `public`, which only holds PostGIS system tables).

## Status

**Milestones 1-5 of the guide are implemented, plus multi-database support:**

- **Multi-database registry** (`dbagent/registry.py`) -- `databases.json` describes any number
  of named database connections, each with its own URL, schema list, excluded-tables denylist,
  and business glossary. `DatabaseRegistry` lazily builds and caches one full set of
  services/engine per configured name. Every API endpoint and script takes a `database`
  selector; adding a database is a config file entry, not a code change (proven live in
  `test_registry.py::test_each_database_gets_its_own_schema_scope` -- two registry entries
  against the same physical Postgres server stay fully isolated by schema/exclusion scope).
- **Phase 1** -- connects through a dedicated read-only role (`ai_readonly`). The `my_case_db` entry's
  grants are scoped to `my_case_db` + `app_schema_dict` only, with `user_credentials` and the
  `oauth2_*` tables explicitly excluded (see "Security notes" below).
- **Phase 2** -- `MetadataExtractor` reads `information_schema` / `pg_catalog`, across multiple
  schemas.
- **Phase 3** -- normalized into `DatabaseSchema` / `TableMetadata` / `ColumnMetadata` /
  `RelationshipMetadata` Pydantic models. `DatabaseSchemaService` caches the extracted schema.
- **Phase 4** -- `SchemaSearchService.search_tables()`: keyword scoring over table/column
  names, comments, and business-glossary aliases.
- **Phase 5** -- `BusinessTermService` / `glossary.json`: maps user terminology (e.g. "owner")
  to database terminology (e.g. `right_holder`). Configurable per database via `glossary_path`.
- Relationship discovery: `DatabaseSchemaService.find_relationships()` (both directions),
  verified against real FKs (`record.transaction_id -> transaction.id`, etc.).
- **Phase 6 + 11 (tool calling)** -- `LLMProvider` abstraction with `OllamaProvider` as the
  primary backend (local, `llama3.2`, shared across all configured databases); `AgentService`
  runs a bounded tool-calling loop with `max_steps` / `max_tool_calls` guards (Phase 30),
  temperature=0 for determinism, and bounded self-correction nudges (Phase 40) for stray
  tool-calls-as-text and premature stalls.
- **Phase 7** -- NL -> SQL: the model itself writes the SELECT statement after inspecting real
  schema via tools (no separate SQL-generation service; matches Strategy B in Section 32).
- **Phase 8** -- `SqlValidator` (sqlglot): only a single read-only SELECT/CTE/UNION statement is
  allowed. Walks the *entire* AST (not just the top level), so a data-modifying CTE like
  `WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x` is caught even though it "looks like"
  a SELECT. Also enforces a table-level denylist as a second layer on top of DB grants.
- **Phase 9-10** -- `ReadOnlyQueryService`: explicit `SET TRANSACTION READ ONLY` +
  `statement_timeout`, row cap with a `truncated` flag, structured `QueryResult`, verified
  against real data (aggregate query returns the correct value).

Endpoints (all take `?database=<name>` or a `database` body field): `GET /api/databases` (lists
configured names, no selector needed), `GET /api/schema`, `GET /api/schema/search`,
`GET /api/schema/relationships/{table}`, `GET /api/glossary`, `POST /api/ai/query`.

`scripts/print_schema.py <database>` prints the schema to the console (the Section 81 success
condition). `scripts/ask_agent.py <database> "<question>"` runs a question through the live
agent + Ollama. Both list configured databases if you omit the name.

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
  (`DatabaseProfile.excluded_tables` in `databases.json`, applied in both `DatabaseSchemaService`
  and `SqlValidator`) so the agent never discovers they exist, not just that it can't read them.
  This is per-database config, so each database's exclusion list is independent.
- No `ALTER DEFAULT PRIVILEGES` was set on `my_case_db`/`app_schema_dict` -- new tables added later need
  an explicit grant + exclusion-list review before the agent can see them.
- `databases.json` holds real connection strings (with passwords) and is gitignored, same as
  `.env`. `databases.example.json` is the checked-in template.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp databases.example.json databases.json   # then fill in your database(s)
```

Requires Ollama running locally with the configured model pulled:

```bash
brew install ollama
brew services start ollama
ollama pull llama3.2
```

## Adding a database

Add an entry to `databases.json`:

```json
{
  "my_other_db": {
    "database_url": "postgresql+psycopg://ai_readonly:password@localhost:5432/my_other_db",
    "schemas": ["public"],
    "excluded_tables": [],
    "glossary_path": "src/dbagent/business/glossary_my_other_db.json"
  }
}
```

`schemas`, `excluded_tables`, and `glossary_path` are all optional (default to `["public"]`,
`[]`, and the shared default glossary respectively). Create a dedicated read-only role on that
database first (Phase 1 -- see the `CREATE USER ... SELECT only` pattern in the implementation
guide), the same way `ai_readonly` was set up for `my_case_db`. No restart-time code change is
needed; the new name shows up in `GET /api/databases` and can be used immediately.

## Run

Print the schema:

```bash
python scripts/print_schema.py my_case_db
```

Ask the agent a question:

```bash
python scripts/ask_agent.py my_case_db "How many districts are there in total?"
```

Run the API:

```bash
uvicorn dbagent.api.main:app --reload --app-dir src
```

Then `curl "http://localhost:8000/api/schema?database=my_case_db"`.

## Test

```bash
pytest
```
