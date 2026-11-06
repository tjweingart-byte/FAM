"""The second review of Phase 4's branch (§495): Keith's alerts open, stay
and close as they say they do.

Pinned, each failing before its fix:

1. a dropped record keeps the tracking alert for an hour, not for the
   server's life;
2. a stopped loop is started again once in the server's life - the incident
   stays open until the check a minute on, and a reopening never re-runs or
   re-emails it in a loop; the restarted task is held and cancelled at
   shutdown;
3. (in test_agent_checks_491) Nando's breach stands until a whole pass is
   clean; C3P0's check reads his own titles, never the words;
4. (in test_cost_watch_492) Claude's figure leaves the nightly jobs out, over
   whole days;
5. `/api/health` reads incident counts from memory and never creates the
   table;
6. the note "still happening after you said it was fixed" outlives the next
   round;
7. an error kind the owner said was fixed, once below the floor, is closed
   only after the quiet half hour, and kept open by any of it since;
8. "I fixed it" re-runs the agents' checks rather than standing on the
   hour's;
9. and the small ones: the fixed endpoint checks who is asking first,
   health carries no provider's error text, tandem reads its own step,
   Netenyahu's queued keys do not grow.
"""
from __future__ import annotations

import asyncio
import datetime
import time

import pytest
from fastapi.testclient import TestClient

import agent_checks as AC
import incidents as INC
import keith_fixes as KF
import pipeline_errors as PE
import provider_usage

T0 = 1_790_000_000.0


@pytest.fixture
def stores(tmp_path):
    PE.reset(PE.ErrorStore(str(tmp_path / "e.db")))
    INC.reset(INC.IncidentStore(str(tmp_path / "e.db")))
    provider_usage.reset(provider_usage.UsageStore(str(tmp_path / "u.db")))
    yield
    INC.reset()


def errors(n, at, stage="brief", kind="timeout"):
    PE.store().add([PE.row(stage, kind, "it took too long", "q", PE.ERROR, at=at - i)
                    for i in range(n)], now=at)


def fixes_on(monkeypatch, value):
    import config
    import dataclasses

    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings,
                                                                it_fixes=value))


# ------------------------------------------------------------------- 1
def test_a_drop_keeps_the_tracking_alert_for_an_hour_not_forever(stores, monkeypatch):
    with PE._PENDING_LOCK:
        PE._WRITES["dropped"]["errors"] = 3
        PE._WRITES["last_dropped_at"] = time.time() - 60
    try:
        assert PE.status()["behind"]
        assert any(f["cause"] == "tracking:pipeline_errors" for f in INC.from_tracking())
        with PE._PENDING_LOCK:
            PE._WRITES["last_dropped_at"] = time.time() - PE.DROPPED_RECENT_SECONDS - 1
        state = PE.status()
        assert not state["behind"] and state["dropped_total"] == 3   # still counted
        assert INC.from_tracking() == []
    finally:
        PE.reset()


# ------------------------------------------------------------------- 2
def test_a_loop_is_started_again_once_in_the_server_s_life(stores, monkeypatch):
    fixes_on(monkeypatch, "rerun_job")
    runs = []

    async def sweep():
        runs.append(1)
        await asyncio.sleep(3600)

    INC.restartable(sweep)
    name = f"{sweep.__module__}.{sweep.__qualname__}"
    cause = f"loop:{name}"

    async def scenario():
        INC.LOOPS[name] = {"at": T0, "error": "RuntimeError"}
        INC.watch(T0)
        await KF.act(T0)
        await asyncio.sleep(0)
        assert runs == [1] and len(KF.RUNNING) == 1        # held, not collected
        # Pending: still open, not closed into a reopening and a second run.
        INC.watch(T0 + 30)
        assert INC.store().get(cause)["closed_at"] is None
        await KF.act(T0 + 30)
        await KF.act(T0 + KF.RERUN_VERIFY_SECONDS + 1)     # verified
        INC.watch(T0 + 120)
        assert INC.store().get(cause)["closed_at"] is not None
        # It dies again: the incident reopens, and is the owner's - no re-run.
        for task in list(KF.RUNNING):
            task.cancel()
        INC.LOOPS[name] = {"at": T0 + 200, "error": "RuntimeError"}
        INC.watch(T0 + 300)
        assert INC.store().get(cause)["reopened"] == 1
        await KF.act(T0 + 300)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert runs == [1]
    outcomes = [r["outcome"] for r in INC.store().fixes_since(0)]
    assert outcomes == ["verified", "pending"]


def test_restarted_loops_are_cancelled_at_shutdown():
    import inspect

    import app

    source = inspect.getsource(app.lifespan)
    assert "keith_fixes.RUNNING" in source


# ------------------------------------------------------------------- 5
def test_health_reads_counts_from_memory_and_creates_nothing(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "pe.db"
    sqlite3.connect(path).execute("CREATE TABLE errors (at REAL)").connection.close()
    monkeypatch.setenv("PIPELINE_ERRORS_DB", str(path))
    INC.reset()
    body = TestClient(__import__("app").app).get("/api/health").json()["incidents"]
    assert body["open"] == {} and body["counted_at"] is None
    tables = {r[0] for r in sqlite3.connect(path).execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "incidents" not in tables


def test_a_round_leaves_its_counts_for_health(stores):
    errors(40, T0)
    INC.watch(T0)
    assert INC.health()["open"] == {"urgent": 1} and INC.health()["counted_at"] == T0


# ------------------------------------------------------------------- 6, 7
def test_the_after_your_fix_note_outlives_the_next_round(stores):
    errors(5, T0)
    INC.watch(T0)
    INC.owner_fixed("errors:brief:timeout", T0 + 10)
    errors(5, T0 + 60)
    INC.watch(T0 + 60)                                       # after the claim
    assert "still happening" in INC.store().get("errors:brief:timeout")["detail"]["after_your_fix"]
    errors(5, T0 + 120)
    INC.watch(T0 + 120)                                      # the next round
    assert "still happening" in INC.store().get("errors:brief:timeout")["detail"]["after_your_fix"]


def test_a_claim_below_the_floor_waits_the_quiet_half_hour(stores):
    errors(5, T0)
    INC.watch(T0)
    claim = T0 + 3500
    INC.owner_fixed("errors:brief:timeout", claim)
    # The five leave the hour: below the floor, with nothing since the claim -
    # not closed yet, and closed only once the half hour has passed.
    INC.watch(T0 + 3600 + 20)
    assert INC.store().get("errors:brief:timeout")["closed_at"] is None
    INC.watch(claim + INC.CLAIM_QUIET_SECONDS)
    assert INC.store().get("errors:brief:timeout")["closed_at"] is not None


def test_a_claim_below_the_floor_is_kept_open_by_any_since(stores):
    errors(5, T0)
    INC.watch(T0)
    INC.owner_fixed("errors:brief:timeout", T0 + 10)
    later = T0 + 3600 + 20                   # the first five are out of the hour
    errors(1, later)                         # one, below the floor, after the claim
    INC.watch(later)
    row = INC.store().get("errors:brief:timeout")
    assert row["closed_at"] is None and row["fixed_claim_at"] is None
    assert "still happening" in row["detail"]["after_your_fix"]


# ------------------------------------------------------------------- 8
def test_i_fixed_it_runs_the_agents_checks_now(stores, monkeypatch):
    INC.store().seen(INC.finding("breach:retriever:writer-user", "agents", INC.URGENT,
                                 "Sonic uses the writer", detail={"role": "retriever"}), T0)
    AC._LAST.update(at=T0, findings=[INC.finding(
        "breach:retriever:writer-user", "agents", INC.URGENT, "stale")])
    ran = []
    monkeypatch.setattr(AC, "sonic_never_writes", lambda: ran.append(1) or [])
    got = INC.owner_fixed("breach:retriever:writer-user", T0 + 60)
    assert ran == [1] and got["state"] == "closed"


# ------------------------------------------------------------------- 9
def test_the_fixed_endpoint_asks_who_first(monkeypatch):
    import app as appmod

    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "sekrit")
    client = TestClient(appmod.app)
    assert client.post("/api/admin/incidents/fixed", json={}).status_code == 404
    got = client.post("/api/admin/incidents/fixed", json={},
                      headers={"X-Admin-Token": "sekrit"})
    assert got.status_code == 422


def test_health_carries_no_provider_s_error_text(monkeypatch):
    import usage_monitors as UM

    monkeypatch.setitem(UM.LAST, "r2", UM.reading("r2", [], ok=False,
                                                  why="HTTP 401: Cloudflare says no"))
    assert "why" not in UM.health()["last"]["r2"]


def test_tandem_reads_its_own_step(stores):
    now = time.time()
    PE.store().add([], steps=[{"at": now - 60, "step": "writer", "path": "search",
                               "outcome": "ok", "seconds": 1.0}] * 3
                   + [{"at": now - 60, "step": "editor tandem", "path": "search",
                       "outcome": "late", "seconds": None}])
    rows = PE.store().steps_since(now - 3600, step="editor tandem")
    assert [r["step"] for r in rows] == ["editor tandem"]


def test_netenyahu_keeps_only_the_current_keys(monkeypatch):
    from agents import netenyahu

    netenyahu._QUEUED_DAYS.update({("it", "watch", f"old-{i}") for i in range(500)})
    netenyahu.schedule(datetime.datetime(2026, 11, 6, 12, tzinfo=datetime.timezone.utc),
                       handlers={})
    assert not any(str(k[2]).startswith("old-") for k in netenyahu._QUEUED_DAYS)


# ------------------------------------------------- the third review (§496)
def test_a_claimed_kind_closed_after_the_half_hour_does_not_reopen(stores):
    """The hour still counts the errors from before the claim: no reason to
    reopen (or re-email) what Keith just confirmed fixed."""
    errors(40, T0)                                           # urgent
    INC.watch(T0)
    INC.owner_fixed("errors:brief:timeout", T0 + 60)
    INC.watch(T0 + 60 + INC.CLAIM_QUIET_SECONDS)
    row = INC.store().get("errors:brief:timeout")
    assert row["closed_at"] is not None
    done = INC.watch(T0 + 60 + INC.CLAIM_QUIET_SECONDS + 300)   # still in the hour
    assert done["reopened"] == []
    assert INC.store().get("errors:brief:timeout")["closed_at"] is not None
    errors(5, T0 + 3000)                                     # it comes back
    assert INC.watch(T0 + 3000)["reopened"] == ["errors:brief:timeout"]


def test_still_happening_is_not_followed_by_quiet_for_two_hours(stores):
    errors(5, T0)
    INC.watch(T0)
    claim = T0 + 5400                                        # 90 minutes on
    INC.owner_fixed("errors:brief:timeout", claim)
    errors(1, claim + 600)                                   # one, below the floor
    INC.watch(claim + 900)
    row = INC.store().get("errors:brief:timeout")
    assert "still happening" in row["detail"]["after_your_fix"]
    assert row["last_seen"] == claim + 600
    INC.watch(T0 + 7300)                     # two hours from the first, not the last
    assert INC.store().get("errors:brief:timeout")["closed_at"] is None


def test_a_claim_is_checked_against_errors_older_than_the_hour(stores):
    """A round late by over an hour still sees an error since the claim."""
    errors(5, T0)
    INC.watch(T0)
    INC.owner_fixed("errors:brief:timeout", T0 + 60)
    errors(1, T0 + 600)
    INC.watch(T0 + 600 + 3700)                               # the error is past the hour
    row = INC.store().get("errors:brief:timeout")
    assert row["closed_at"] is None and "still happening" in row["detail"]["after_your_fix"]


def test_a_check_that_cannot_read_keeps_its_last_findings(stores, monkeypatch):
    breach = INC.finding("breach:categorizer:dead-node", "agents", INC.URGENT, "dead")
    monkeypatch.setattr(AC, "nando_nodes_exist", lambda cache: [breach])
    assert [f["cause"] for f in AC.findings(T0, force=True)] == [breach["cause"]]

    def broken(cache):
        raise LookupError("no tree")

    monkeypatch.setattr(AC, "nando_nodes_exist", broken)
    assert [f["cause"] for f in AC.findings(T0 + 3600, force=True)] == [breach["cause"]]


def test_nando_with_no_tree_raises_rather_than_reading_clean(monkeypatch):
    import config
    import dataclasses

    import topics

    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, agents=True, categorizer=True))
    monkeypatch.setattr(topics, "category_tree", lambda: None)

    class Cache:
        def tagged(self, after="", limit=500):
            return [("a", "gone", 1, [])]

    with pytest.raises(LookupError):
        AC.nando_nodes_exist(Cache())


def test_i_fixed_it_does_not_poll_a_service_while_keith_is_off(stores, monkeypatch):
    import usage_monitors as UM

    INC.store().seen(INC.finding("usage:r2:storage", "usage", INC.WARN, "R2 storage"), T0)
    polled = []

    async def poll(**k):
        polled.append(k)
        return []

    monkeypatch.setattr(UM, "poll", poll)
    INC.owner_fixed("usage:r2:storage", T0 + 60)             # AGENTS off by default
    assert polled == []
