"""The server refuses to start on an empty Postgres beside a full disk
(PROBLEMS.md §253).

Production was pointed at its new database before the copy had run. The
server found empty schemas and served an app with no accounts and an empty
waitlist, the files on the disk untouched beside it. What must hold:

* **Accounts on the disk and none in Postgres stop the boot**, with a message
  that says nothing was lost and what to do.
* **Nothing else stops it**: no disk, a copied database, a store other than
  the accounts, or `DATABASE_ALLOW_EMPTY=1` - each starts, and every store
  found uncopied is still logged and listed in `/api/health`.
* **The disk is read, never written.**
"""
from __future__ import annotations

import os
import sqlite3
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as db_mod  # noqa: E402


def _file(tmp_path, name, rows=0):
    path = str(tmp_path / name)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    conn.commit()
    conn.close()
    return path


def test_a_file_says_whether_it_holds_rows(tmp_path):
    assert db_mod.file_has_rows(str(tmp_path / "missing.db")) is None
    assert db_mod.file_has_rows("") is None
    assert db_mod.file_has_rows(_file(tmp_path, "empty.db")) is False
    full = _file(tmp_path, "full.db", rows=3)
    before = os.path.getmtime(full)
    assert db_mod.file_has_rows(full) is True
    assert os.path.getmtime(full) == before, "the disk must only be read"


def test_uncopied_names_full_files_beside_empty_schemas(tmp_path, monkeypatch):
    full, empty = _file(tmp_path, "a.db", rows=1), _file(tmp_path, "b.db")
    monkeypatch.setattr(db_mod, "schema_has_rows", lambda schema: schema == "copied")
    found = db_mod.uncopied([
        ("accounts", full, "fresh"),            # rows on the disk, none in Postgres
        ("mixes", full, "copied"),              # copied
        ("social", empty, "fresh"),             # nothing on the disk to copy
        ("saved", str(tmp_path / "none.db"), "fresh"),   # no file at all
    ])
    assert found == ["accounts"]


# --- the boot ----------------------------------------------------------------


@pytest.fixture
def appmod(monkeypatch):
    import app as appmod

    monkeypatch.setattr(appmod.db_mod, "enabled", lambda: True)
    monkeypatch.setattr(appmod, "_database_report", lambda: [
        {"name": "accounts", "path": "/data/accounts.db", "schema": "accounts_x"},
        {"name": "GDELT export copy", "path": "/data/gdelt.db", "schema": "gdelt_x"}])
    monkeypatch.setattr(appmod, "_UNCOPIED", [])
    monkeypatch.delenv("DATABASE_ALLOW_EMPTY", raising=False)
    return appmod


def test_accounts_that_were_never_copied_stop_the_boot(appmod, monkeypatch):
    monkeypatch.setattr(appmod.db_mod, "uncopied", lambda stores: ["accounts"])
    with pytest.raises(appmod.UncopiedDatabase) as refused:
        appmod._refuse_an_uncopied_database()
    message = str(refused.value)
    assert "Nothing has been lost" in message
    assert "sqlite_to_postgres.py" in message and "--yes" in message
    assert "DATABASE_ALLOW_EMPTY=1" in message
    assert appmod._UNCOPIED == ["accounts"]


def test_the_whole_startup_refuses(appmod, monkeypatch):
    """Through the lifespan, as uvicorn runs it: the deploy fails."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(appmod.db_mod, "uncopied", lambda stores: ["accounts"])
    with pytest.raises(appmod.UncopiedDatabase):
        with TestClient(appmod.app):
            pass


def test_starting_empty_on_purpose_is_allowed_and_still_reported(appmod, monkeypatch):
    monkeypatch.setattr(appmod.db_mod, "uncopied", lambda stores: ["accounts"])
    monkeypatch.setenv("DATABASE_ALLOW_EMPTY", "1")
    appmod._refuse_an_uncopied_database()
    assert appmod._UNCOPIED == ["accounts"]


def test_another_store_is_reported_but_never_stops_the_boot(appmod, monkeypatch, caplog):
    monkeypatch.setattr(appmod.db_mod, "uncopied", lambda stores: ["GDELT export copy"])
    appmod._refuse_an_uncopied_database()
    assert appmod._UNCOPIED == ["GDELT export copy"]
    assert "GDELT export copy" in caplog.text


def test_a_copied_database_and_sqlite_start_quietly(appmod, monkeypatch):
    monkeypatch.setattr(appmod.db_mod, "uncopied", lambda stores: [])
    appmod._refuse_an_uncopied_database()
    assert appmod._UNCOPIED == []
    monkeypatch.setattr(appmod.db_mod, "enabled", lambda: False)
    monkeypatch.setattr(appmod.db_mod, "uncopied",
                        lambda stores: pytest.fail("SQLite has nothing to compare"))
    appmod._refuse_an_uncopied_database()


def test_health_lists_what_was_not_copied(appmod, monkeypatch):
    monkeypatch.setattr(appmod, "_UNCOPIED", ["GDELT export copy"])
    monkeypatch.setattr(appmod.db_mod, "report", lambda: {"backend": "postgres"})
    monkeypatch.setattr(appmod.db_mod, "sizes",
                        lambda: {"total_bytes": 0, "schemas": {}})
    summary = appmod._database_summary([])
    assert summary["uncopied"] == ["GDELT export copy"]


# --- against a real Postgres ---------------------------------------------------


@pytest.mark.skipif(not db_mod.enabled(), reason="needs DATABASE_URL (Postgres)")
def test_a_real_schema_is_compared(tmp_path):
    schema = "uncopied_" + uuid.uuid4().hex[:8]
    full = _file(tmp_path, "accounts.db", rows=2)
    conn = db_mod.raw_connect(autocommit=True)
    try:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f'CREATE TABLE "{schema}".t (x INTEGER)')
        assert db_mod.uncopied([("accounts", full, schema)]) == ["accounts"]
        conn.execute(f'INSERT INTO "{schema}".t VALUES (1)')
        assert db_mod.uncopied([("accounts", full, schema)]) == []
    finally:
        conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        conn.close()
