from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


class DatabaseConnection:
    """Owns the SQLAlchemy engine for one database. Not a singleton -- the
    agent can be pointed at any number of databases at once (see
    dbagent.registry.DatabaseRegistry), each with its own engine/pool."""

    def __init__(self, database_url: str):
        self._engine: Engine = create_engine(database_url, pool_pre_ping=True)

    @property
    def engine(self) -> Engine:
        return self._engine

    @contextmanager
    def connect(self):
        with self._engine.connect() as conn:
            yield conn
