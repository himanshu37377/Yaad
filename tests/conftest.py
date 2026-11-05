"""Test isolation: never let the suite touch a real user database.

yaad.config calls load_dotenv() at import time, and the repo-root .env sets
YAAD_DB_PATH to ~/.yaad/yaad.sqlite3. Per-file os.environ.setdefault(...)
calls lose that race whenever another test module imports yaad first, so
the global `main.db` points at the developer's real database. conftest.py is
imported before any test module, so a hard override here wins.
load_dotenv() does not override variables that are already set.
"""
import os
import tempfile
from pathlib import Path

_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="yaad-tests-"))
os.environ["YAAD_DB_PATH"] = str(_TEST_DB_DIR / "yaad-tests.sqlite3")
