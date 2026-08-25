"""Shared fixtures: every test runs against an isolated SQLite file and
covers directory, never the real manga.db / covers/ this app is actually
tracking your collection in.
"""
import pytest

from app.backend import covers as covers_module
from app.backend import db as db_module


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point db.py at a throwaway SQLite file and create its schema.

    db.py's functions all read the module-level DB_PATH at call time (via
    `db.get_connection()`), so patching the attribute here is enough to
    redirect every db.* call for the rest of the test — no need to touch
    each function individually.
    """
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module.init_db()


@pytest.fixture
def temp_covers_dir(tmp_path, monkeypatch):
    """Point covers.py's save/fetch helpers at a throwaway directory.

    Note: this does NOT affect the '/covers' StaticFiles mount in main.py —
    that mount captures COVERS_DIR by value at import time, before any
    fixture runs, so it always serves the real project covers/ directory.
    Tests here only exercise the write path (save_cover), never rely on
    reading a cover back through the mounted static route.
    """
    covers_dir = tmp_path / "covers"
    covers_dir.mkdir()
    monkeypatch.setattr(covers_module, "COVERS_DIR", covers_dir)
    return covers_dir


@pytest.fixture
def client(temp_db, temp_covers_dir, tmp_path, monkeypatch):
    """A TestClient for the FastAPI app, fully isolated from real data:
    throwaway db, throwaway covers dir, throwaway scan log."""
    from fastapi.testclient import TestClient

    from app.backend import main as main_module

    monkeypatch.setattr(main_module, "SCAN_LOG_PATH", tmp_path / "scan_log.jsonl")

    with TestClient(main_module.app) as test_client:
        yield test_client
