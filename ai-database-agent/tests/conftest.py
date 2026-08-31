import getpass
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL

from dbagent.database import DatabaseConnection

# Schema/table names below are entirely synthetic (created and torn down by
# this fixture) -- never real business schema. Tests must never hardcode
# real table/column names or real data values; use this fixture instead so
# the suite stays runnable by anyone who clones the repo, not just someone
# with access to a specific private database.
FIXTURE_SCHEMA = "agent_test_fixtures"


@pytest.fixture(scope="session")
def pg_test_connection() -> DatabaseConnection:
    """Connection to whatever database is first configured in the local,
    gitignored databases.json -- used only as *a* real PostgreSQL instance
    to run structural tests against (via the synthetic schema below), never
    to read that database's own real tables."""
    config = json.loads((ROOT / "databases.json").read_text())
    first_entry = next(iter(config.values()))
    return DatabaseConnection(first_entry["database_url"])


@pytest.fixture(scope="session")
def synthetic_schema(pg_test_connection: DatabaseConnection) -> str:
    """Creates a throwaway schema with fabricated tables/data (regions,
    records with a self-referencing FK, a fake credential table, a fake
    widget/payment pair for metric-style aggregate tests) so tests can
    exercise real Postgres behavior -- multi-schema discovery, relationship
    discovery, exclusion, search_path resolution, aggregate queries --
    without depending on any real business schema or data. Session-scoped:
    built once, reused, dropped at the end of the run.
    """
    base_url = pg_test_connection.engine.url
    # Locally, the OS user that initialized Postgres is typically a
    # superuser reachable via peer/trust auth (no password) -- that's the
    # default. CI (e.g. GitHub Actions' postgres service container) has no
    # such OS-user mapping, so TEST_SUPERUSER/TEST_SUPERUSER_PASSWORD let
    # the workflow point this at the service's actual superuser instead.
    superuser_url = URL.create(
        drivername=base_url.drivername,
        username=os.environ.get("TEST_SUPERUSER", getpass.getuser()),
        password=os.environ.get("TEST_SUPERUSER_PASSWORD"),
        host=base_url.host,
        port=base_url.port,
        database=base_url.database,
    )
    # NOT str(superuser_url) -- SQLAlchemy's URL.__str__ redacts the
    # password (renders literal "***"), which silently "worked" locally
    # because local Postgres uses trust/peer auth that ignores the
    # password entirely, but fails hard anywhere password auth is actually
    # enforced (e.g. CI's postgres service container: "password
    # authentication failed"). render_as_string(hide_password=False) keeps
    # the real password.
    superuser = DatabaseConnection(superuser_url.render_as_string(hide_password=False))

    with superuser.engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {FIXTURE_SCHEMA}"))
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {FIXTURE_SCHEMA}.region (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {FIXTURE_SCHEMA}.record (
                    id SERIAL PRIMARY KEY,
                    region_id INTEGER REFERENCES {FIXTURE_SCHEMA}.region(id),
                    parent_record_id INTEGER REFERENCES {FIXTURE_SCHEMA}.record(id),
                    title TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {FIXTURE_SCHEMA}.secret_credential (
                    id SERIAL PRIMARY KEY,
                    token TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {FIXTURE_SCHEMA}.account (
                    id SERIAL PRIMARY KEY,
                    username TEXT NOT NULL,
                    password TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {FIXTURE_SCHEMA}.widget (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    price NUMERIC(10,2) NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {FIXTURE_SCHEMA}.payment (
                    id SERIAL PRIMARY KEY,
                    widget_id INTEGER REFERENCES {FIXTURE_SCHEMA}.widget(id),
                    amount NUMERIC(10,2) NOT NULL,
                    status TEXT NOT NULL,
                    paid_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        # Reset to known, deterministic data every session.
        conn.execute(
            text(
                f"TRUNCATE {FIXTURE_SCHEMA}.payment, {FIXTURE_SCHEMA}.widget, "
                f"{FIXTURE_SCHEMA}.account, {FIXTURE_SCHEMA}.secret_credential, "
                f"{FIXTURE_SCHEMA}.record, {FIXTURE_SCHEMA}.region RESTART IDENTITY CASCADE"
            )
        )
        conn.execute(
            text(f"INSERT INTO {FIXTURE_SCHEMA}.region (name) VALUES ('North'), ('South'), ('East')")
        )
        conn.execute(
            text(
                f"""
                INSERT INTO {FIXTURE_SCHEMA}.record (region_id, parent_record_id, title) VALUES
                    (1, NULL, 'Root Record'),
                    (1, 1, 'Child Record')
                """
            )
        )
        conn.execute(text(f"INSERT INTO {FIXTURE_SCHEMA}.secret_credential (token) VALUES ('shh')"))
        conn.execute(
            text(f"INSERT INTO {FIXTURE_SCHEMA}.account (username, password) VALUES ('alice', 'hunter2')")
        )
        conn.execute(
            text(
                f"""
                INSERT INTO {FIXTURE_SCHEMA}.widget (name, status, price) VALUES
                    ('Widget A', 'Completed', 10.00),
                    ('Widget B', 'Completed', 25.50),
                    ('Widget C', 'Pending', 5.00)
                """
            )
        )
        conn.execute(
            text(
                f"""
                INSERT INTO {FIXTURE_SCHEMA}.payment (widget_id, amount, status, paid_at) VALUES
                    (1, 10.00, 'Success', '2025-01-15T00:00:00Z'),
                    (2, 25.50, 'Success', '2025-02-15T00:00:00Z'),
                    (3, 5.00, 'Failed', '2025-01-20T00:00:00Z')
                """
            )
        )

        conn.execute(text(f"GRANT USAGE ON SCHEMA {FIXTURE_SCHEMA} TO ai_readonly"))
        conn.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {FIXTURE_SCHEMA} TO ai_readonly"))

    yield FIXTURE_SCHEMA

    with superuser.engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {FIXTURE_SCHEMA} CASCADE"))
