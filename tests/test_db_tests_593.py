"""The Postgres test run kept its pool's books and its catalogue (§593).

Pinned here, with no database (the pool's dialling is faked):

* `close_all` closes the idle connections now and a lent one only when its
  thread gives it back - never under a statement - and the counts stay true;
* the test thread is a request: never held to the background share (§574),
  while a thread it starts still is;
* the schemas a test made are dropped by directory, and what the process
  remembered about them is forgotten - except one a live connection still
  names (a module reloaded in a test keeps its stores for the run).

And against a real Postgres: a transaction that is open while `close_all`
runs still commits; a dropped store can be made again in the same place.
"""
from __future__ import annotations

import gc
import threading
import time

import pytest

import db
from test_db_connections_574 import FakePg, hold, pool  # noqa: F401 - fixture


def test_close_all_closes_idle_now_and_lent_on_return(pool):
    idle, lent = pool._take(None), pool._take(None)
    pool.release(idle)
    assert pool.stats()["open"] == 2

    assert pool.close_all() == 2
    assert idle.pg.closed_by_us
    assert not lent.pg.closed_by_us                # still in use: left open
    stats = pool.stats()
    assert stats["open"] == 1 and stats["idle"] == 0

    pool.release(lent)                             # its thread is done with it
    assert lent.pg.closed_by_us
    assert pool.stats()["open"] == 0 and not pool._all


def test_a_lent_background_connection_keeps_its_share_until_returned(pool):
    done, held = hold(pool, 2)
    pool.close_all()
    # Still out, so still counted: the share is not handed out twice.
    assert pool.stats()["background_busy"] == 2
    for slot in held:
        pool.release(slot)
    assert pool.stats()["background_busy"] == 0
    assert pool.stats()["open"] == 0
    done.set()


def test_a_connection_retired_while_out_is_never_lent_again(pool):
    slot = pool._take(None)
    pool.close_all()
    pool.release(slot)
    fresh = pool._take(None)
    assert fresh is not slot
    pool.release(fresh)
    assert pool.stats()["open"] == 1 and pool.stats()["idle"] == 1


def test_the_test_thread_is_a_request():
    assert db._FOR_REQUEST.get() is True


def test_the_test_thread_never_waits_on_the_background_share(pool):
    done, _ = hold(pool, 2)                        # the share is full
    started = time.monotonic()
    slot = pool._take(None)
    assert time.monotonic() - started < 0.2 and not slot.background
    done.set()


def test_a_thread_the_test_starts_is_still_background(pool):
    out = []
    t = threading.Thread(target=lambda: out.append(pool._take(None)))
    t.start()
    t.join()
    assert out and out[0].background


def test_dropping_needs_postgres(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db.drop_schemas_under(str(tmp_path)) == 0


def test_a_store_remembers_the_directory_it_was_opened_in(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "url", lambda: "postgresql://fam@db.internal/fam")
    monkeypatch.setattr(db, "check_staging_address", lambda *a: None)
    monkeypatch.setattr(db, "_DIRS", {})
    path = tmp_path / "deep" / "x.db"
    db.connect(str(path))
    assert db._DIRS == {db.schema_for(str(path)): str(tmp_path / "deep")}


def test_forgetting_a_schema_forgets_everything_remembered_about_it(monkeypatch):
    for name, value in (("_DIRS", {"a_1": "/t", "b_2": "/u"}),
                        ("_SCHEMAS", {"a_1", "b_2"}), ("_CREATED", {"a_1"}),
                        ("_KEYS", {("a_1", "t"): ("id",), ("b_2", "t"): ("id",)}),
                        ("_IDENTITY", {("a_1", "t"): "id"}),
                        ("_FLOAT_MINUTES", {("a_1", "t"), ("b_2", "t")})):
        monkeypatch.setattr(db, name, value)
    db._forget_schema("a_1")
    assert db._DIRS == {"b_2": "/u"} and db._SCHEMAS == {"b_2"}
    assert not db._CREATED and not db._IDENTITY
    assert list(db._KEYS) == [("b_2", "t")] and db._FLOAT_MINUTES == {("b_2", "t")}


# ----------------------------------------------------- against a real Postgres
needs_postgres = pytest.mark.skipif(not db.enabled(),
                                    reason="no DATABASE_URL (CI's Postgres job sets one)")


@needs_postgres
def test_a_transaction_open_across_close_all_still_commits(tmp_path):
    path = str(tmp_path / "held.db")
    setup = db.connect(path, isolation_level=None)
    setup.execute("CREATE TABLE IF NOT EXISTS t (n INTEGER)")
    began, closed, out = threading.Event(), threading.Event(), []

    def work():
        conn = db.connect(path)
        conn.execute("INSERT INTO t (n) VALUES (1)")  # opens the transaction
        began.set()
        closed.wait(5)
        conn.commit()
        out.append("committed")

    t = threading.Thread(target=work)
    t.start()
    began.wait(5)
    db.close_all()
    closed.set()
    t.join(10)
    assert out == ["committed"]
    assert setup.execute("SELECT count(*) FROM t").fetchone()[0] == 1
    assert db._POOL.stats()["open"] == len(db._POOL._all)


@needs_postgres
def test_a_dropped_store_can_be_made_again(tmp_path):
    path = str(tmp_path / "again.db")
    conn = db.connect(path, isolation_level=None)
    conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, n INTEGER)")
    conn.execute("INSERT OR REPLACE INTO t (id, n) VALUES (1, 1)")
    schema = db.schema_for(path)
    db.close_all()
    db.drop_schemas_under(str(tmp_path))
    assert schema in db._DIRS                      # still held: kept
    del conn
    gc.collect()
    assert db.drop_schemas_under(str(tmp_path)) >= 1
    assert not db.has_tables(path) and schema not in db._DIRS

    conn = db.connect(path, isolation_level=None)
    conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, n INTEGER)")
    conn.execute("INSERT OR REPLACE INTO t (id, n) VALUES (1, 2)")
    assert conn.execute("SELECT n FROM t").fetchall() == [(2,)]


@needs_postgres
def test_only_the_tests_own_directory_is_dropped(tmp_path, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("kept")
    mine = db.connect(str(tmp_path / "mine.db"), isolation_level=None)
    mine.execute("CREATE TABLE IF NOT EXISTS t (n INTEGER)")
    kept_path = str(elsewhere / "kept.db")
    kept = db.connect(kept_path, isolation_level=None)
    kept.execute("CREATE TABLE IF NOT EXISTS t (n INTEGER)")
    del mine, kept
    gc.collect()
    db.close_all()
    # Mine, and the ones conftest's own stores made in the same directory.
    assert db.drop_schemas_under(str(tmp_path)) >= 1
    assert not db.has_tables(str(tmp_path / "mine.db"))
    assert db.has_tables(kept_path)
    assert db.drop_schemas_under(str(elsewhere)) == 1


@needs_postgres
def test_a_store_something_still_holds_keeps_its_schema(tmp_path):
    held = db.connect(str(tmp_path / "held.db"), isolation_level=None)
    held.execute("CREATE TABLE IF NOT EXISTS t (n INTEGER)")
    db.close_all()
    db.drop_schemas_under(str(tmp_path))
    assert held.execute("SELECT count(*) FROM t").fetchone()[0] == 0
