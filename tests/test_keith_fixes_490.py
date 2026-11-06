"""Keith's fixes, piece 3b: a closed list, observe only until switched on (§490).

Pinned: while a fix is off it is written to the report as "would", once per
incident; switched on it runs once per opening and never twice; each is
verified by a real check (the voice answering, a re-run loop still running a
minute later, Netenyahu's store holding the agent); a fix that fails is
written into the incident's plan as the owner's; a provider near its quota
is an incident; the report is answerable at any moment, to admins only.
"""
from __future__ import annotations

import asyncio
import dataclasses

import pytest
from fastapi.testclient import TestClient

import incidents as INC
import keith_fixes as KF
import pipeline_errors as PE
import provider_usage
import voice_control
from config import settings

T0 = 1_790_000_000.0


@pytest.fixture
def stores(tmp_path, monkeypatch):
    PE.reset(PE.ErrorStore(str(tmp_path / "e.db")))
    INC.reset(INC.IncidentStore(str(tmp_path / "e.db")))
    provider_usage.reset(provider_usage.UsageStore(str(tmp_path / "u.db")))
    KF.RERUNS.clear()
    yield
    INC.reset()
    KF.RERUNS.clear()


def fixes_on(monkeypatch, value):
    import config

    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, it_fixes=value))


def voice_errors(at=T0):
    PE.store().add([PE.row("voice", "no worker", "none answered", "", PE.ERROR, at=at - i)
                    for i in range(4)], now=at)
    INC.watch(at)


def report_rows():
    return INC.store().fixes_since(0)


def test_off_a_fix_is_written_as_would_once(stores, monkeypatch):
    fixes_on(monkeypatch, "")
    voice_errors()
    called = []
    monkeypatch.setattr(voice_control, "current", lambda force=False: called.append(1))
    asyncio.run(KF.act(T0))
    asyncio.run(KF.act(T0 + 300))
    rows = report_rows()
    assert [(r["fix"], r["mode"], r["outcome"]) for r in rows] == [
        ("reverify_voice", "observe", "would")]
    assert called == []                                   # nothing was done
    assert KF.report(now=T0)["enabled"] == []


def test_on_the_voice_is_re_verified_once_by_a_real_call(stores, monkeypatch):
    fixes_on(monkeypatch, "reverify_voice")
    voice_errors()
    calls = []

    async def current(force=False):
        calls.append(force)
        return voice_control.Endpoint("http", "https://pod.example", "registered", "fresh")

    monkeypatch.setattr(voice_control, "current", current)
    asyncio.run(KF.act(T0))
    asyncio.run(KF.act(T0 + 300))                         # never twice
    assert calls == [True]
    [row] = report_rows()
    assert row["mode"] == "act" and row["outcome"] == "verified"
    assert "worker answered" in row["how"]
    plan = INC.store().get("errors:voice:no worker")["plan"]
    assert plan["tried"].startswith("Keith tried reverify_voice") and "(verified)" in plan["tried"]
    INC.watch(T0 + 300)                                   # the round keeps it
    assert INC.store().get("errors:voice:no worker")["plan"]["tried"].startswith("Keith tried")


def test_a_fix_that_fails_is_the_owners_and_not_tried_again(stores, monkeypatch):
    fixes_on(monkeypatch, "reverify_voice")
    voice_errors()

    async def current(force=False):
        raise voice_control.VoiceControlError("nobody answers")

    monkeypatch.setattr(voice_control, "current", current)
    asyncio.run(KF.act(T0))
    asyncio.run(KF.act(T0 + 300))
    [row] = report_rows()
    assert row["outcome"] == "failed"
    tried = INC.store().get("errors:voice:no worker")["plan"]["tried"]
    assert "did not work" in tried and "this is yours" in tried


def test_a_stopped_loop_is_started_again_once_and_checked_a_minute_later(stores, monkeypatch):
    fixes_on(monkeypatch, "rerun_job")
    runs = []

    async def sweep():
        runs.append(1)
        await asyncio.sleep(3600)

    INC.restartable(sweep)
    name = f"{sweep.__module__}.{sweep.__qualname__}"

    async def scenario():
        INC.LOOPS[name] = {"at": T0, "error": "RuntimeError"}
        INC.watch(T0)
        await KF.act(T0)
        await asyncio.sleep(0)
        assert runs == [1]
        assert report_rows()[0]["outcome"] == "pending"
        await KF.act(T0 + 30)                             # too soon to say
        await KF.act(T0 + KF.RERUN_VERIFY_SECONDS + 1)
        for task in [t for t, _ in KF.RERUNS.values()]:
            task.cancel()

    asyncio.run(scenario())
    outcomes = [r["outcome"] for r in report_rows()]
    assert outcomes == ["verified", "pending"]
    assert "still running" in report_rows()[0]["how"]


def test_a_loop_not_registered_has_no_fix(stores, monkeypatch):
    fixes_on(monkeypatch, "rerun_job")
    INC.LOOPS["somewhere.unknown_loop"] = {"at": T0, "error": "RuntimeError"}
    INC.watch(T0)
    asyncio.run(KF.act(T0))
    assert report_rows() == []


def test_an_agent_is_held_through_netenyahu_and_read_back(stores, monkeypatch):
    from agents import netenyahu

    fixes_on(monkeypatch, "hold_agent")
    INC.store().seen(INC.finding("breach:retriever:wrote", "agents", INC.URGENT,
                                 "Sonic wrote to the archive",
                                 detail={"role": "retriever"}), T0)
    held = {}
    monkeypatch.setattr(netenyahu, "hold", lambda role, why: held.update({role: why}))
    monkeypatch.setattr(netenyahu, "_read_held", lambda: dict(held))
    asyncio.run(KF.act(T0))
    [row] = report_rows()
    assert row["outcome"] == "verified" and "releasing it is yours" in row["how"]
    assert held["retriever"].startswith("Keith: Sonic wrote")


def test_a_provider_near_its_quota_is_an_incident(stores, monkeypatch):
    import config

    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, gnews_daily_requests=100))
    usage = provider_usage.store()
    for _ in range(96):
        usage.record("gnews", now=T0)
    INC.watch(T0)
    [row] = [r for r in INC.report(T0)["open"] if r["source"] == "quota"]
    assert row["cause"] == "quota:gnews:" and row["severity"] == "urgent"
    assert "96% of its day's 100 requests" in row["title"]


def test_the_report_is_admins_only_and_beside_the_alerts(stores, monkeypatch):
    import app as appmod

    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "sekrit")
    client = TestClient(appmod.app)
    assert client.get("/api/admin/fixes").status_code == 404
    body = client.get("/api/admin/fixes", headers={"X-Admin-Token": "sekrit"}).json()
    assert body["available"] == ["hold_agent", "rerun_job", "reverify_voice"]
    assert body["enabled"] == [] and body["fixes"] == []
    alerts = client.get("/api/admin/incidents", headers={"X-Admin-Token": "sekrit"}).json()
    assert alerts["fix_report"]["available"] == body["available"]


def test_an_unknown_fix_name_in_it_fixes_switches_nothing_on(monkeypatch):
    fixes_on(monkeypatch, "reverify_voice, restart_the_world")
    assert KF.enabled() == {"reverify_voice"}
