import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from dbagent.database import DatabaseConnection


@pytest.fixture(scope="session")
def ilcms_db_connection() -> DatabaseConnection:
    """Engine for the 'my_case_db' entry in databases.json -- the same config
    path the app itself uses, so tests exercise real, current settings
    rather than a hardcoded duplicate connection string."""
    config = json.loads((ROOT / "databases.json").read_text())
    return DatabaseConnection(config["my_case_db"]["database_url"])
