"""fam-db's memory was its connections (§574).

With two people on the app, the database sat at 83% of its 256 MB: each
server connection is a Postgres process holding memory, busy or idle, and
FAM kept up to eight idle ones open for ever, filled by background work.

Pinned here, with no database (the pool's dialling is faked):

* an idle connection is closed after `DATABASE_IDLE_SECONDS`, by the pool and
  by its reaper, so a quiet server hangs up too;
* background work holds at most `DATABASE_BACKGROUND_CONNECTIONS` at once and
  waits for its own share; a request somebody sent never does; a thread
  already holding one is never made to wait on itself; the event loop never
  waits on the share;
* every HTTP request is marked as one (`app.carry_the_session`);
* Keith reads who holds connections beside fam-db's memory, and his plan
  says idle connections before it says a bigger plan; sizes read "256 MB".
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import sqlite3
import threading
import time

import pytest

import db
import incidents as INC
import usage_monitors as UM


class FakePg:
    closed = False

    def __init__(self):
        from psycopg.pq import TransactionStatus

        self.closed_by_us = False
        self.info = type("Info", (), {"transaction_status": TransactionStatus.IDLE})()

    def close(self):
        self.closed_by_us = True
        self.closed = True


@pytest.fixture
def pool(monkeypatch):
    p = db._Pool()
    monkeypatch.setattr(p, "_start_reaper", lambda: None)

    def fake_open():
        slot = db._Slot(FakePg())
        with p._cond:
            p._all.add(slot)
        return slot

    monkeypatch.setattr(p, "_open", fake_open)
    monkeypatch.setattr(db, "WAIT_SECONDS", 0.3)
    monkeypatch.setenv("DATABASE_MAX_CONNECTIONS", "6")
    monkeypatch.setenv("DATABASE_BACKGROUND_CONNECTIONS", "2")
    monkeypatch.setenv("DATABASE_IDLE_SECONDS", "300")
    return p


def hold(pool, n):
    """`n` background threads each holding a connection until `done` is set:
    alive, as a thread mid-statement is (an ended thread's id is reused)."""
    done, held, ready = threading.Event(), [], threading.Semaphore(0)

    def work():
        held.append(pool._take(None))
        ready.release()
        done.wait(5)

    threads = [threading.Thread(target=work, daemon=True) for _ in range(n)]
    for t in threads:
        t.start()
        ready.acquire()
    return done, held


def test_settings_have_defaults_and_bounds(monkeypatch):
    for name in ("DATABASE_IDLE_SECONDS", "DATABASE_BACKGROUND_CONNECTIONS",
                 "DATABASE_MAX_CONNECTIONS"):
        monkeypatch.delenv(name, raising=False)
    assert db.idle_seconds() == 300
    assert db.background_connections() == 4
    monkeypatch.setenv("DATABASE_MAX_CONNECTIONS", "3")
    monkeypatch.setenv("DATABASE_BACKGROUND_CONNECTIONS", "10")
    assert db.background_connections() == 2          # always room for a request
    monkeypatch.setenv("DATABASE_IDLE_SECONDS", "nonsense")
    assert db.idle_seconds() == 300


def test_idle_connections_close_after_the_limit(pool):
    slots = [pool._take(None) for _ in range(3)]
    for s in slots:
        pool.release(s)
    assert pool.stats()["idle"] == 3
    # Nothing has sat long enough yet.
    assert pool.close_idle() == 0
    later = time.monotonic() + 301
    assert pool.close_idle(now=later) == 3
    assert all(s.pg.closed_by_us for s in slots)
    stats = pool.stats()
    assert stats["open"] == 0 and stats["idle"] == 0 and stats["closed_idle"] == 3


def test_zero_keeps_idle_connections(pool, monkeypatch):
    monkeypatch.setenv("DATABASE_IDLE_SECONDS", "0")
    pool.release(pool._take(None))
    assert pool.close_idle(now=time.monotonic() + 10 ** 6) == 0


def test_releasing_closes_what_went_stale_meanwhile(pool):
    old, new = pool._take(None), pool._take(None)
    pool.release(old)
    old.last_used -= 400
    pool.release(new)
    assert old.pg.closed_by_us and not new.pg.closed_by_us
    assert pool.stats()["idle"] == 1


def test_background_waits_for_its_share_and_then_says_so(pool):
    done, _ = hold(pool, 2)
    assert pool.stats()["background_busy"] == 2

    errors = []

    def third():
        try:
            pool._take(None)
        except sqlite3.OperationalError as exc:
            errors.append(str(exc))

    t = threading.Thread(target=third)
    t.start()
    t.join()
    assert errors and "DATABASE_BACKGROUND_CONNECTIONS" in errors[0]
    done.set()


def test_background_gets_one_as_soon_as_its_share_frees(pool):
    done, held = hold(pool, 2)
    waited = []

    def third():
        waited.append(pool._take(None))

    t = threading.Thread(target=third)
    t.start()
    time.sleep(0.05)
    assert not waited
    pool.release(held[0])
    t.join()
    assert waited
    done.set()


def test_a_request_is_never_held_to_the_background_share(pool):
    done, _ = hold(pool, 2)
    out = []

    def request():
        db.for_request()
        out.append(pool._take(None))

    t = threading.Thread(target=request)
    t.start()
    t.join()
    assert out and not out[0].background
    assert pool.stats()["background_busy"] == 2
    done.set()


def test_a_thread_holding_one_is_not_made_to_wait_on_itself(pool):
    out = []

    def work():
        first = pool._take(None)
        second = pool._take(None)
        third = pool._take(None)        # past its share of 2: still lent
        out.extend([first, second, third])

    t = threading.Thread(target=work)
    t.start()
    t.join()
    assert len(out) == 3


def test_the_event_loop_never_waits_on_the_share(pool):
    done, _ = hold(pool, 2)

    async def on_the_loop():
        return pool._take(None)

    started = time.monotonic()
    slot = asyncio.run(on_the_loop())
    assert slot is not None and time.monotonic() - started < 0.2
    done.set()


def test_every_http_request_is_marked_as_one(monkeypatch):
    from fastapi.testclient import TestClient

    import app as appmod

    seen = []
    real = db.report

    def report():
        seen.append(db._FOR_REQUEST.get())
        return real()

    monkeypatch.setattr(db, "report", report)
    assert TestClient(appmod.app).get("/api/health").status_code == 200
    assert seen and all(seen)
    assert db._FOR_REQUEST.get() is False          # not leaked to the caller


# --------------------------------------------------------------- Keith
HELD = {"total": 12, "by_state": {"idle": 10, "active": 2}, "fam": 11,
        "fam_idle": 9, "others": 1, "max_connections": 97, "pool": {}}


def _render_reading(monkeypatch, held):
    import test_usage_monitors_render_486 as R

    monkeypatch.setattr(UM, "settings", dataclasses.replace(
        UM.settings, render_service_id=R.SRV, render_postgres_name="fam-db",
        render_bandwidth_included_gb=25.0))
    monkeypatch.setattr(UM, "_secret", lambda n: R.KEY if n == "RENDER_API_KEY" else "")
    monkeypatch.setattr(UM, "_server_connections", lambda: held)
    client, _ = R.api(R.normal())
    return R.read(client)


def test_keith_reads_who_holds_connections_beside_the_memory(monkeypatch):
    got = _render_reading(monkeypatch, HELD)
    figures = {f["name"]: f for f in got["figures"]}
    conns = figures["fam-db connections"]
    assert conns["value"] == 12 and conns["limit"] == 97 and conns["level"] == "ok"
    assert figures["fam-db memory"]["connections"] == HELD
    assert got["detail"]["connections"] == HELD


def test_without_postgres_there_is_no_connections_figure(monkeypatch):
    got = _render_reading(monkeypatch, None)
    names = {f["name"] for f in got["figures"]}
    assert "fam-db connections" not in names and "fam-db memory" in names
    assert "connections" not in next(f for f in got["figures"]
                                     if f["name"] == "fam-db memory")


def test_the_memory_incident_names_idle_connections_before_a_plan(monkeypatch):
    memory = UM.figure("fam-db memory", 0.2225, "GB", "now",
                       limit=256 * 1024 ** 2 / 1e9, limit_source="its plan (Render)",
                       next_plan="Basic 1 GB (0.5 CPU), $19/month")
    memory["connections"] = HELD
    assert memory["level"] == "warn"
    monkeypatch.setattr(UM, "report", lambda: {"services": [
        {"service": "render", "ok": True, "figures": [memory]}]})
    (found,) = INC.from_usage()
    assert found["title"] == "render: fam-db memory at 83% of 256 MB"
    plan = found["plan"]
    assert "212 MB against 256 MB" in plan["problem"]
    assert "12 connection(s) open: 2 active, 10 idle" in plan["problem"]
    assert "9 of them idle" in plan["problem"]
    assert plan["recommended"].startswith("9 of FAM's connections are idle")
    assert "$19/month" in plan["recommended"]
    assert found["detail"]["connections"] == HELD


def test_without_idle_connections_the_plan_is_the_plan(monkeypatch):
    memory = UM.figure("fam-db memory", 0.2225, "GB", "now",
                       limit=256 * 1024 ** 2 / 1e9, limit_source="its plan (Render)",
                       next_plan="Basic 1 GB (0.5 CPU), $19/month")
    memory["connections"] = dict(HELD, fam_idle=1)
    monkeypatch.setattr(UM, "report", lambda: {"services": [
        {"service": "render", "ok": True, "figures": [memory]}]})
    (found,) = INC.from_usage()
    assert found["plan"]["recommended"] == \
        "Move up before the limit: Basic 1 GB (0.5 CPU), $19/month."


def test_quantities_read_as_a_person_reads_them():
    assert INC._quantity(256 * 1024 ** 2 / 1e9, "GB") == "256 MB"
    assert INC._quantity(1.0, "GB") == "1 GB"
    assert INC._quantity(0.97, "USD") == "0.97 USD"
    assert INC._quantity(None, "GB") == "?"


def test_the_settings_are_copied_into_env_example():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text()
    assert "# DATABASE_IDLE_SECONDS=300" in text
    assert "# DATABASE_BACKGROUND_CONNECTIONS=4" in text


# ----------------------------------------------------- against a real Postgres
needs_postgres = pytest.mark.skipif(not db.enabled(),
                                    reason="no DATABASE_URL (CI's Postgres job sets one)")


@needs_postgres
def test_the_server_says_who_holds_connections(tmp_path):
    conn = db.connect(str(tmp_path / "x.db"), isolation_level=None)
    conn.execute("CREATE TABLE IF NOT EXISTS t (n INTEGER)")
    held = db.server_connections()
    assert held["max_connections"] > 0
    assert held["fam"] >= 1 and held["total"] >= held["fam"]
    assert held["fam_idle"] <= held["fam"]
    assert sum(held["by_state"].values()) == held["total"]
    assert held["pool"]["background_max"] == db.background_connections()


@needs_postgres
def test_idle_connections_really_close_on_the_server(tmp_path, monkeypatch):
    path = str(tmp_path / "y.db")
    db.connect(path, isolation_level=None).execute(
        "CREATE TABLE IF NOT EXISTS t (n INTEGER)")
    before = db.server_connections()["fam"]
    assert before >= 1
    closed = db._POOL.close_idle(now=time.monotonic() + db.idle_seconds() + 1)
    assert closed >= 1
    # A fresh statement dials again and works.
    assert db.connect(path).execute("SELECT count(*) FROM t").fetchone()[0] == 0
