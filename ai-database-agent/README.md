# AI Database Reasoning Agent

Incremental implementation following `AI_Database_Reasoning_Agent_Implementation_Guide.md`.
Not bound to one database -- any number of PostgreSQL databases can be configured via
`databases.json` and selected at request time (Phase 50: multi-database support).
`databases.json` is gitignored (it holds real connection strings); this repo ships only
`databases.example.json`, a generic template. Development and testing were done against two
private local databases (a multi-schema case-management-style system and a single-schema
e-commerce-style system) -- no real schema, table names, or data from either appears in this
repo or its history; the test suite builds and tears down its own fully synthetic schema
instead (see `tests/conftest.py`).

## Status

**Milestones 1-7 of the guide are implemented, plus the business-metrics piece of Milestone 8
(semantic layer), plus multi-database support:**

- **Multi-database registry** (`dbagent/registry.py`) -- `databases.json` describes any number
  of named database connections, each with its own URL, schema list, excluded-tables denylist,
  and business glossary. `DatabaseRegistry` lazily builds and caches one full set of
  services/engine per configured name. Every API endpoint, script, and MCP tool takes a
  `database` selector; adding a database is a config file entry, not a code change (proven in
  `test_registry.py::test_each_database_gets_its_own_schema_scope`, against a synthetic schema).
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
  verified against real FKs (including self-referencing ones).
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
  `get_sample_rows`, `list_business_metrics`, `compute_metric`, `validate_sql`,
  `execute_readonly_sql` as standard MCP tools, so any MCP-compatible client (Claude Desktop,
  Claude Code, etc.) can use the same tool layer our own agent uses -- with the calling client's
  own model doing the reasoning instead of going through our `AgentService`. Verified against a
  real stdio subprocess with the official MCP client SDK, not just called as plain Python
  functions.
- **Phase 15/26 -- business metrics registry** (`dbagent/business/metric_service.py`) -- trusted,
  versioned SQL definitions per database, optionally date-ranged (with strict `YYYY-MM-DD`
  validation before substitution into the template). `compute_metric` renders and runs a
  metric's template through the exact same `SqlValidator`/`ReadOnlyQueryService` pipeline as any
  other query. The agent checks `list_business_metrics` before writing raw SQL for a concept
  that matches one, so the same question always uses the same trusted definition instead of the
  model re-deriving the filter per question. See `metrics.example.json` for the file shape.
- **Per-database context notes** (`DatabaseProfile.context_path`, optional) -- a plain-text/
  markdown file of curated, *verified* schema facts appended to the agent's system prompt for
  that database (real table/column names, FK targets, observed enum values, explicit "there is
  no table called X" callouts for names a model might guess). This is additive, not a
  replacement for tool-based discovery -- it just gives the model a head start so it doesn't
  need search_tables/get_table_schema for tables it already "knows" about, which matters a lot
  for a 3B model prone to guessing plausible-sounding table names (observed live: a "give me the
  last 3 orders" question hallucinated a nonexistent table before this was added; went straight
  to the correct table in one tool call after). See `context.example.md` for the file shape --
  every fact in a real one should be checked against the live schema via `psql` before being
  written, since the agent is told to trust it.

Endpoints (all take `?database=<name>` or a `database` body field): `GET /api/databases` (lists
configured names, no selector needed), `GET /api/schema`, `GET /api/schema/search`,
`GET /api/schema/relationships/{table}`, `GET /api/glossary`, `POST /api/ai/query`.

`scripts/print_schema.py <database>` prints the schema to the console (the Section 81 success
condition). `scripts/ask_agent.py <database> "<question>"` runs a question through the live
agent + Ollama. `scripts/run_mcp_server.py` runs the MCP server over stdio. All list configured
databases if you omit required arguments.

### Known limitation: small local model reliability

`llama3.2:3b` reliably handles most 1-3 hop questions now -- several real failure modes were
found and fixed by testing against it live, not just against unit tests:

- Unqualified-table `search_path` resolution (a query against a non-`public` schema failed until
  the query service started setting `search_path` explicitly).
- A field-name collision (`row_count` next to `rows` in the same JSON) that caused a hallucinated
  answer -- fixed by renaming to `returned_row_count` and tightening the prompt.
- Tool-argument-name guessing (`from_table` instead of `table`, etc.) -- fixed with alias
  tolerance in `ToolExecutor`.
- **Runaway exploration after already getting the right answer**: a prompt instruction alone
  ("stop once you have the answer") was not reliable -- the model kept calling unrelated
  metrics/tables until it ran out of steps, even after a tool call had already returned the
  correct value. Fixed structurally, not by prompting harder: once a `compute_metric` /
  `execute_readonly_sql` call succeeds, the *next* turn omits tool definitions entirely, so the
  model has no choice but to answer in text (see `test_tools_are_withheld_after_successful_data_result`).
- **That fix's own failure mode**: force-stopping on *any* success meant a wrong-but-valid metric
  guess (e.g. computing a count when the question asked for a revenue total, after guessing a
  nonexistent metric name first) got force-stopped into a confidently wrong answer -- worse than
  the original bug, since a garbled loop became a plausible-sounding lie. Fixed with a one-time
  "grace round": the success immediately after a wrong-metric-name guess is *not* trusted, but
  the one after that is -- matching the guide's own "the agent may correct itself once, avoid
  unlimited retries" principle (Phase 40). Verified live: a revenue question that previously
  answered with the wrong (but real) count now correctly self-corrects to the right metric and
  the exact ground-truth dollar figure.
- **Stray-tool-call-as-text reaching the user as a fake "answer"**: when self-correction nudges
  ran out and the model was still emitting a tool call as JSON-shaped plain text, that raw text
  was returned as if it were the real answer. Fixed to return an honest failure
  (`stopped_reason="stray_tool_call_unresolved"`) instead of presenting garbage as data.
- **A raw Ollama timeout crashed the API with a 500**: `POST /api/ai/query` returned an unhandled
  500 with a full httpx stack trace when a single Ollama call took longer than the (120s at the
  time) client timeout -- plausibly a model reload after Ollama's default ~5-minute idle unload,
  compounded by a long tool-call chain growing the prompt each turn. Fixed on three levels:
  `OllamaProvider` now catches `httpx` timeout/connection/status errors and raises a domain
  `LLMProviderError` with an actionable message; `AgentService.ask()` catches that and returns a
  normal `AgentResponse` (`stopped_reason="llm_provider_error"`) instead of letting it propagate,
  consistent with how every other stop condition is surfaced; and the default timeout was raised
  to 180s with `keep_alive=30m` sent on every request so Ollama holds the model in memory instead
  of reloading it between requests. Both are configurable via `OLLAMA_TIMEOUT_SECONDS` /
  `OLLAMA_KEEP_ALIVE` in `.env`.

All of the above are locked in with unit tests using a scripted fake provider (deterministic,
fast, no Ollama dependency) in `test_agent_service.py`, in addition to having been reproduced and
re-verified live against the real model. The agent still doesn't force a correct outcome every
time (e.g. a metric needing `start_date`/`end_date` can still stall on a multi-hop question, and
the model can still mangle a field while writing its prose summary even when the underlying data
it pulled was correct), but it now fails honestly rather than confidently wrong or presenting
garbled text. The validation/execution layers themselves are correct and safe regardless of what
the model does or how it fails (verified independent of the LLM in `test_sql_validator.py` /
`test_query_service.py` / `test_sample_service.py` / `test_metric_service.py`). A larger
tool-calling model (e.g. `llama3.1:8b`, `qwen2.5:7b`) would likely be more reliable end-to-end;
not pulled yet due to limited local disk space. The MCP server sidesteps this entirely for MCP
clients, since reasoning happens in the *calling* client's model, not `llama3.2`.

Not yet implemented: embeddings/RAG (guide explicitly says these only become useful once a schema
gets large -- both databases used in development are still small enough for keyword search),
multi-step analytics, charts/reports, audit trail, role-based access control, and everything past
Milestone 8 in the guide.

## Security notes

- Each database's `ai_readonly` role has `SELECT` only, scoped to that database's configured
  schemas. Credential/session/token tables (e.g. an auth-credentials table, an OAuth token
  table, a Django-style user table with a password column) should be explicitly `REVOKE`d --
  see `databases.example.json` for the `excluded_tables` shape.
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
  `.env`. `databases.example.json` is the checked-in template. Per-database business content
  (`context_*.md`, `glossary_*.json`, `metrics_*.json` -- real schema/business facts about a
  specific private database) is also gitignored by pattern; only the generic `*.example.*`
  templates are committed. See `.gitignore` for the exact patterns.

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
    "glossary_path": "src/dbagent/business/glossary_my_other_db.json",
    "metrics_path": "src/dbagent/business/metrics_my_other_db.json",
    "context_path": "src/dbagent/business/context_my_other_db.md"
  }
}
```

`schemas`, `excluded_tables`, `glossary_path`, `metrics_path`, and `context_path` are all optional
(default to `["public"]`, `[]`, no glossary, no metrics, and no extra context respectively).
Create a dedicated read-only role on that database first (Phase 1 -- see the
`CREATE USER ... SELECT only` pattern in the implementation guide). Check what's actually in the
schema before granting broadly -- credential/session/token tables should be excluded the same way
(`REVOKE` at the DB level + `excluded_tables` in the config). No restart-time code change is
needed; the new name shows up in `GET /api/databases` and can be used immediately.

`context_path`, `glossary_path`, and `metrics_path` files named `context_*.md` / `glossary_*.json`
/ `metrics_*.json` are gitignored by pattern (see `.gitignore`) so real per-database business
content never gets committed -- keep them locally, and only commit the generic `.example.`
variants if you want to document the shape for others. `context_path` points at a plain-text/
markdown file of schema notes appended to that database's agent system prompt --
**only write facts you've actually verified against the live schema** (`psql`/`get_table_schema`),
never guessed or copied from a similar-looking app. A wrong fact in here is worse than no context
at all, since the agent is told to trust it. Keep it concise: table names, real column names, FK
targets, and any observed enum-like values are far more useful than long prose or example queries
-- a smaller model's attention degrades with prompt length, so the goal is a dense cheat-sheet,
not documentation.

## Run

Print the schema:

```bash
python scripts/print_schema.py my_other_db
```

Ask the agent a question:

```bash
python scripts/ask_agent.py my_other_db "How many records are there in total?"
```

Run the API:

```bash
uvicorn dbagent.api.main:app --reload --app-dir src
```

Then `curl "http://localhost:8000/api/schema?database=my_other_db"`. Interactive docs at
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

## Docker

```bash
docker compose build
docker compose up -d
```

Serves on container-internal port 8005 (`docker-compose.yml`'s `expose`, not published to the
host -- only reachable by other containers on the same Docker network). Joins the external
`proxy-network` (`global-proxy-network`), so any other service on that network can reach it at
`http://ai-database-agent:8005`. `databases.json` and any per-database business content
(`context_*.md` / `glossary_*.json` / `metrics_*.json`) are bind-mounted from the host, never
baked into the image -- put them on the server the same way you do locally. If Ollama runs on
the host machine rather than in a container, set `OLLAMA_HOST=http://host.docker.internal:11434`
in `.env` (works on Linux too here, via the `extra_hosts` entry in `docker-compose.yml`).

## Test

```bash
pytest
```

The suite needs a local PostgreSQL instance reachable via the first entry in your (gitignored)
`databases.json`, plus a superuser-capable local role (the OS user that initialized the Postgres
install typically has this via peer/trust auth) -- it creates and tears down its own throwaway
`agent_test_fixtures` schema with fabricated tables/data for every test, and never touches your
real database's own tables.
