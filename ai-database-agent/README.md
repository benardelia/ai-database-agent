# AI Database Reasoning Agent

Incremental implementation following `AI_Database_Reasoning_Agent_Implementation_Guide.md`.
Not bound to one database -- any number of PostgreSQL databases can be configured via
`databases.json` and selected at request time (Phase 50: multi-database support). Currently
configured with two real databases:

- `my_case_db` -- a land case management system (`record`, `person`, `transaction`,
  `region`, `subregion`, etc., in the `my_case_db` / `app_schema_dict` schemas -- not `public`, which
  only holds PostGIS system tables).
- `my_store_db` -- a Django-based store management system (`product_table`, `order_table`,
  `customer_table`, `payment_table`, etc., in `public`).

## Status

**Milestones 1-7 of the guide are implemented, plus multi-database support:**

- **Multi-database registry** (`dbagent/registry.py`) -- `databases.json` describes any number
  of named database connections, each with its own URL, schema list, excluded-tables denylist,
  and business glossary. `DatabaseRegistry` lazily builds and caches one full set of
  services/engine per configured name. Every API endpoint, script, and MCP tool takes a
  `database` selector; adding a database is a config file entry, not a code change (proven live
  in `test_registry.py::test_each_database_gets_its_own_schema_scope`).
- **Phase 1** -- connects through a dedicated read-only role (`ai_readonly`) per database, scoped
  to specific schemas, with credential/session/token tables explicitly excluded at both the DB
  grant level and the app metadata level (see "Security notes").
- **Phase 2-3** -- `MetadataExtractor` reads `information_schema` / `pg_catalog` across multiple
  schemas, normalized into `DatabaseSchema` / `TableMetadata` / `ColumnMetadata` /
  `RelationshipMetadata` Pydantic models, cached by `DatabaseSchemaService`.
- **Phase 4-5** -- `SchemaSearchService.search_tables()` (keyword scoring over table/column
  names, comments, business-glossary aliases) + `BusinessTermService` / per-database glossary
  files mapping user terminology to real table/column names.
- Relationship discovery: `DatabaseSchemaService.find_relationships()` (both directions),
  verified against real FKs.
- **Phase 6 + 11 (tool calling)** -- `LLMProvider` abstraction with `OllamaProvider` as the
  primary backend (local, `llama3.2`, shared across all configured databases); `AgentService`
  runs a bounded tool-calling loop with `max_steps` / `max_tool_calls` guards (Phase 30),
  temperature=0 for determinism, argument-name-alias tolerance (small models often guess
  `from_table` instead of `table`, etc.), and bounded self-correction nudges (Phase 40) for
  stray tool-calls-as-text and premature stalls.
- **Phase 7** -- NL -> SQL: the model itself writes the SELECT statement after inspecting real
  schema via tools (matches Strategy B in Section 32). The query service sets `search_path` to
  each database's configured schemas so unqualified table names resolve correctly even though
  `ai_readonly`'s own default search_path is just `"$user", public`.
- **Phase 8** -- `SqlValidator` (sqlglot): only a single read-only SELECT/CTE/UNION statement is
  allowed. Walks the *entire* AST, so a data-modifying CTE like
  `WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x` is caught even though it "looks like"
  a SELECT. Also enforces a table-level denylist as a second layer on top of DB grants.
- **Phase 9-10** -- `ReadOnlyQueryService`: explicit `SET TRANSACTION READ ONLY` +
  `statement_timeout`, row cap with a `truncated` flag, structured `QueryResult` (the row-count
  field is deliberately named `returned_row_count`, not `row_count`, after a small model once
  misread it as the answer itself instead of the actual value inside `rows`).
- **Phase 14** -- `SampleDataService.get_sample_rows()`: bounded example rows with
  sensitive-looking columns (password, token, card number, etc.) filtered out by name, on top of
  all the same read-only/validation/exclusion protections.
- **Phase 12/20 -- MCP server** (`dbagent/mcp_server.py`) -- exposes `list_databases`,
  `get_database_schema`, `search_tables`, `get_table_schema`, `find_relationships`,
  `get_sample_rows`, `validate_sql`, `execute_readonly_sql` as standard MCP tools, so any
  MCP-compatible client (Claude Desktop, Claude Code, etc.) can use the same tool layer our own
  agent uses -- with the calling client's own model doing the reasoning instead of going through
  our `AgentService`. Verified against a real stdio subprocess with the official MCP client SDK,
  not just called as plain Python functions.

Endpoints (all take `?database=<name>` or a `database` body field): `GET /api/databases` (lists
configured names, no selector needed), `GET /api/schema`, `GET /api/schema/search`,
`GET /api/schema/relationships/{table}`, `GET /api/glossary`, `POST /api/ai/query`.

`scripts/print_schema.py <database>` prints the schema to the console (the Section 81 success
condition). `scripts/ask_agent.py <database> "<question>"` runs a question through the live
agent + Ollama. `scripts/run_mcp_server.py` runs the MCP server over stdio. All list configured
databases if you omit required arguments.

### Known limitation: small local model reliability

`llama3.2:3b` reliably handles most 1-3 hop questions now (several real failure modes were
found and fixed by testing against it live: unqualified-table search_path resolution, a
field-name collision that caused a hallucinated answer, and tool-argument-name guessing) but can
still occasionally stall or send unusual argument names on longer chains. The agent handles this
gracefully (bounded nudges, tool-argument errors are fed back to the model instead of crashing --
see `test_tool_executor.py`), but doesn't force a correct outcome every time. The
validation/execution layers themselves are correct and safe regardless of what the model does or
how it fails (verified independent of the LLM in `test_sql_validator.py` / `test_query_service.py`
/ `test_sample_service.py`). A larger tool-calling model (e.g. `llama3.1:8b`, `qwen2.5:7b`) would
likely be more reliable end-to-end; not pulled yet due to limited local disk space (~5GB free).
The MCP server sidesteps this entirely for MCP clients, since reasoning happens in the *calling*
client's model, not `llama3.2`.

Not yet implemented: semantic layer / business metrics registry, embeddings/RAG, multi-step
analytics, charts/reports, audit trail, and everything past Milestone 7 in the guide.

## Security notes

- Each database's `ai_readonly` role has `SELECT` only, scoped to that database's configured
  schemas. Credential/session/token tables are explicitly `REVOKE`d (`my_case_db`:
  `user_credentials`, `oauth2_*`; `my_store_db`: `user_account_table` [has a `password` column],
  `session_table`, `email_verification_token`).
- Postgres exposes table/column *metadata* to any role with schema `USAGE`, regardless of
  `SELECT` grants -- so those excluded tables are *also* filtered out at the app layer
  (`DatabaseProfile.excluded_tables` in `databases.json`, applied in `DatabaseSchemaService` and
  `SqlValidator`) so the agent never discovers they exist, not just that it can't read them. This
  is per-database config, so each database's exclusion list is independent, and it applies
  identically whether accessed via the HTTP API, the Ollama agent, or the MCP server -- they all
  share the same `DatabaseBundle` services.
- `get_sample_rows` adds a third layer specifically for sample-value previews: even on a table
  that *is* otherwise accessible, columns matching a sensitive-name pattern (password, token,
  secret, card number, ssn, etc.) are stripped before the query runs.
- No `ALTER DEFAULT PRIVILEGES` was set on any schema -- new tables added later need an explicit
  grant + exclusion-list review before the agent can see them.
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
guide), the same way `ai_readonly` was set up for `my_case_db` and `my_store_db`. Check what's actually in the
schema before granting broadly -- credential/session/token tables should be excluded the same way
(`REVOKE` at the DB level + `excluded_tables` in the config). No restart-time code change is
needed; the new name shows up in `GET /api/databases` and can be used immediately.

## Run

Print the schema:

```bash
python scripts/print_schema.py my_case_db
```

Ask the agent a question:

```bash
python scripts/ask_agent.py my_case_db "How many districts are there in total?"
python scripts/ask_agent.py my_store_db "give me the first 5 records of product_table"
```

Run the API:

```bash
uvicorn dbagent.api.main:app --reload --app-dir src
```

Then `curl "http://localhost:8000/api/schema?database=my_case_db"`. Interactive docs at
`http://localhost:8000/docs`.

Run the MCP server (stdio transport):

```bash
python scripts/run_mcp_server.py
```

To use it from Claude Desktop or Claude Code, add it as an MCP server pointing at that script,
e.g. in Claude Code:

```bash
claude mcp add ai-database-agent -- python /absolute/path/to/ai-database-agent/scripts/run_mcp_server.py
```

or in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ai-database-agent": {
      "command": "python",
      "args": ["/absolute/path/to/ai-database-agent/scripts/run_mcp_server.py"]
    }
  }
}
```

Once connected, the client's own model can call `list_databases`, `search_tables`,
`get_table_schema`, `find_relationships`, `get_sample_rows`, `validate_sql`, and
`execute_readonly_sql` directly -- no Ollama dependency for that path, since the calling client
supplies its own reasoning.

## Test

```bash
pytest
```
