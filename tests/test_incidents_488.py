"""Keith's incidents, piece 3a: one per cause, from what FAM records (§488).

Pinned: a kind of error past the hour's floor is one incident whose count
grows, never sixty; it closes when quiet and reopens (said) if it comes
back; the usage warnings, the records falling behind, a background loop that
died and old listener reports are each an incident with a solution plan; a
source that cannot be read closes nothing; nothing is fixed or changed; the
round runs every few minutes only while Keith is on.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import json
import time

import pytest

import incidents as INC
import pipeline_errors as PE
import provider_usage
import usage_monitors as UM
from agents import netenyahu, tasks
from config import settings

T0 = 1_790_000_000.0


@pytest.fixture
def stores(tmp_path):
    PE.reset(PE.ErrorStore(str(tmp_path / "e.db")))
    INC.reset(INC.IncidentStore(str(tmp_path / "e.db")))
    provider_usage.reset(provider_usage.UsageStore(str(tmp_path / "u.db")))
    yield
    INC.reset()


def errors(n, stage="brief", kind="timeout", at=T0, severity=PE.ERROR):
    rows = [PE.row(stage, kind, "it took too long", "q", severity, at=at - i)
            for i in range(n)]
    PE.store().add(rows, now=at)


def test_a_kind_of_error_past_the_floor_is_one_incident_with_a_plan(stores):
    errors(2)
    assert INC.watch(T0)["opened"] == []                      # under the floor
    errors(3, at=T0 + 60)
    done = INC.watch(T0 + 60)
    assert done["opened"] == ["errors:brief:timeout"]
    errors(5, at=T0 + 120)
    assert INC.watch(T0 + 120)["seen"] == 1                   # the same incident
    [row] = INC.report(T0 + 120)["open"]
    assert row["seen"] == 2 and row["severity"] == "warn"
    assert "in the last hour" in row["title"]
    plan = row["plan"]
    assert plan["problem"] and plan["listener_notices"] and plan["recommended"]
    assert "observes only" in plan["tried"]


def test_notices_do_not_open_incidents_and_a_flood_is_urgent(stores):
    errors(10, kind="score check removed 1 sentence", stage="writer", severity=PE.NOTICE)
    assert INC.watch(T0)["opened"] == []
    errors(INC.ERRORS_URGENT, stage="voice", kind="no worker")
    INC.watch(T0)
    assert INC.report(T0)["counts"] == {"urgent": 1}


def test_quiet_for_two_hours_closes_and_coming_back_reopens(stores):
    errors(4)
    INC.watch(T0)
    assert INC.watch(T0 + 3600)["closed"] == []               # an hour: not yet
    assert INC.watch(T0 + 2 * 3600 + 1)["closed"] == ["errors:brief:timeout"]
    closed = INC.report(T0 + 2 * 3600 + 1)["closed"][0]
    assert closed["closed_why"] == "quiet for two hours"
    errors(3, at=T0 + 3 * 3600)
    assert INC.watch(T0 + 3 * 3600)["reopened"] == ["errors:brief:timeout"]
    assert INC.report(T0 + 3 * 3600)["open"][0]["reopened"] == 1


def test_a_usage_warning_is_an_incident_until_the_level_falls(stores, monkeypatch):
    state = {"level": "urgent"}

    def latest(*a, **k):
        return {"services": [{"service": "render", "ok": True, "figures": [
            {"name": "fam-db storage", "value": 0.97, "unit": "GB", "limit": 1.0,
             "share": 0.97, "projected": None, "level": state["level"],
             "limit_source": "its disk (Render)",
             "next": "grow the disk: $0.30/GB-month, in steps of 5 GB"}]}]}

    monkeypatch.setattr(UM, "report", latest)
    INC.watch(T0)
    [row] = INC.report(T0)["open"]
    assert row["cause"] == "usage:render:fam-db storage" and row["severity"] == "urgent"
    assert "97%" in row["title"] and "5 GB" in row["plan"]["recommended"]
    assert "never changes one" in row["plan"]["needs_from_owner"]
    state["level"] = "ok"
    assert INC.watch(T0 + 300)["closed"] == ["usage:render:fam-db storage"]


def test_a_monitor_that_cannot_read_is_a_notice_but_not_configured_is_not(stores, monkeypatch):
    monkeypatch.setattr(UM, "report", lambda *a, **k: {"services": [
        {"service": "atlas", "ok": False, "why": "HTTP 401 from the Admin API"},
        {"service": "render", "ok": False, "why": "not configured: RENDER_API_KEY"},
        {"service": "r2", "never_read": True}]})
    INC.watch(T0)
    [row] = INC.report(T0)["open"]
    assert row["cause"] == "usage:atlas:unread" and row["severity"] == "notice"


def test_records_falling_behind_are_urgent(stores, monkeypatch):
    monkeypatch.setattr(provider_usage, "status", lambda: {
        "behind": True, "waiting": 12, "failures": 2,
        "last_failure": "could not write the store: OSError"})
    INC.watch(T0)
    [row] = INC.report(T0)["open"]
    assert row["cause"] == "tracking:provider_usage" and row["severity"] == "urgent"
    assert "OSError" in row["plan"]["problem"]


def test_a_background_loop_that_dies_is_an_incident(stores):
    async def sweep_forever():
        raise RuntimeError("boom")

    async def off_at_boot():
        return None

    async def run():
        died = asyncio.get_running_loop().create_task(sweep_forever())
        fine = asyncio.get_running_loop().create_task(off_at_boot())
        INC.watch_task(died)
        INC.watch_task(fine)
        await asyncio.sleep(0)
        await asyncio.gather(died, fine, return_exceptions=True)
        await asyncio.sleep(0)

    asyncio.run(run())
    assert list(INC.LOOPS) == ["test_a_background_loop_that_dies_is_an_incident"
                               ".<locals>.sweep_forever"]
    INC.watch(T0)
    [row] = INC.report(T0)["open"]
    assert row["severity"] == "urgent" and row["detail"]["error"] == "RuntimeError"
    assert "restart" in row["plan"]["recommended"]
    assert INC.health()["loops_stopped"] == list(INC.LOOPS)


def test_old_listener_reports_are_an_incident(stores):
    class Feedback:
        def list(self, state, limit):
            return [{"id": "a", "created": T0 - 30 * 3600},
                    {"id": "b", "created": T0 - 3600}]

    INC.FEEDBACK[0] = Feedback()
    INC.watch(T0)
    [row] = INC.report(T0)["open"]
    assert row["cause"] == "reports:open" and "1 listener report" in row["title"]


def test_a_source_that_cannot_be_read_closes_nothing(stores, monkeypatch):
    errors(4)
    INC.watch(T0)

    def broken(now):
        raise OSError("database unreachable")

    monkeypatch.setattr(INC, "from_errors", broken)
    assert INC.watch(T0 + 3 * 3600)["closed"] == []
    assert len(INC.report(T0)["open"]) == 1


def test_the_round_only_reads(stores, monkeypatch):
    """Observe only: a round writes nothing but its own incidents."""
    errors(4)
    before = len(PE.store().since(0))
    INC.watch(T0)
    assert len(PE.store().since(0)) == before


def test_the_round_runs_every_few_minutes_only_while_keith_is_on(monkeypatch):
    handlers = {("it", "watch"): lambda _t: None}
    at = datetime.datetime(2026, 11, 10, 14, 2, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(netenyahu, "settings", dataclasses.replace(
        settings, it_agent=False, it_watch_seconds=300))
    assert netenyahu.schedule(at, handlers) == 0
    monkeypatch.setattr(netenyahu, "settings", dataclasses.replace(
        settings, it_agent=True, it_watch_seconds=300))
    assert netenyahu.schedule(at, handlers) == 1
    assert netenyahu.schedule(at.replace(minute=4), handlers) == 0     # same 5 min
    assert netenyahu.schedule(at.replace(minute=6), handlers) == 1
    assert sorted(tasks.store().due(10)) == ["it:watch:2026-11-10T1400",
                                             "it:watch:2026-11-10T1405"]


def test_the_handler_does_nothing_unless_keith_may_run(monkeypatch):
    from agents import roster

    ran = []
    monkeypatch.setattr(roster, "status", lambda a: {"may_run": False, "why_not": "IT_AGENT=0"})
    monkeypatch.setattr(INC, "watch", lambda *a, **k: ran.append(1))
    asyncio.run(INC.handler("it:watch:x"))
    assert ran == []


def test_health_and_admin_say_the_incidents():
    from fastapi.testclient import TestClient

    import app

    body = TestClient(app.app).get("/api/health").json()["incidents"]
    assert body == {"open": {}, "loops_stopped": []}
