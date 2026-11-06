"""Keith's usage monitors, piece 2a: the common part and RunPod's spend (§473).

keith.md "always" 6: watch each service's usage against its limits and warn
at 80%, urgently at 95% or when a limit will run out before it resets. The
owner (2026-11-06): RunPod tops itself up, so what matters there is total
spend. Pinned: the figures read from RunPod's own billing, a budget only
when the owner sets one (no share of nothing), the levels, a failure said
and never a zero, the key never in a reading, and the hourly task only while
Keith is on.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import json

import httpx
import pytest

import provider_usage
import usage_monitors as UM
from agents import netenyahu, tasks
from config import settings

NOV_10 = datetime.datetime(2026, 11, 10, 15, 0, tzinfo=datetime.timezone.utc)
KEY = "rpa_SECRET_KEY_VALUE"


def use(monkeypatch, **changes):
    monkeypatch.setattr(UM, "settings", dataclasses.replace(UM.settings, **changes))


def billing(days: dict, status: int = 200, body=None):
    """A RunPod that answers `GET /v2/billing` with one record per day."""
    seen = []

    def answer(request):
        seen.append(request)
        if body is not None:
            return httpx.Response(status, json=body)
        records = [{"startTime": f"{day}T00:00:00Z", "endTime": f"{day}T23:59:59Z",
                    "totalAmount": total, "serverlessGpuAmount": gpu,
                    "serverlessDiskAmount": 0.0, "podGpuAmount": total - gpu}
                   for day, (total, gpu) in days.items()]
        return httpx.Response(status, json={"records": records, "metadata": {}})

    return httpx.AsyncClient(transport=httpx.MockTransport(answer)), seen


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(UM, "_runpod_key", lambda: KEY)


def read(client, now=NOV_10):
    return asyncio.run(UM.read_runpod(now, client))


def by_name(got):
    return {f["name"]: f for f in got["figures"]}


def test_runpod_spend_is_read_from_its_own_billing(key):
    client, seen = billing({"2026-10-31": (9.0, 9.0), "2026-11-01": (3.0, 3.0),
                            "2026-11-09": (4.5, 4.0), "2026-11-10": (2.25, 2.25)})
    got = read(client)
    assert got["ok"] and got["service"] == "runpod"
    f = by_name(got)
    assert f["spend this month"]["value"] == 9.75            # October not counted
    assert f["spend today"]["value"] == 2.25
    assert f["spend yesterday"]["value"] == 4.5
    assert f["serverless this month"]["value"] == 9.25
    request = seen[0]
    assert request.url.path.endswith("/v2/billing")
    assert request.url.params["bucketSize"] == "day"
    assert int(request.url.params["lastN"]) >= 10            # the whole month
    assert request.headers["Authorization"] == f"Bearer {KEY}"


def test_without_a_budget_the_spend_is_shown_and_measured_against_nothing(key):
    got = read(billing({"2026-11-10": (500.0, 500.0)})[0])
    month = by_name(got)["spend this month"]
    assert month["limit"] is None and month["share"] is None
    assert month["level"] == "ok" and got["level"] == "ok"


def test_a_budget_gives_a_share_a_projection_and_a_level(key, monkeypatch):
    use(monkeypatch, it_budgets="runpod=300, anthropic=200")
    assert UM.budgets() == {"runpod": 300.0, "anthropic": 200.0}
    # 9.625 days into November, $80 spent: 27%, on pace for ~$249.
    month = by_name(read(billing({"2026-11-05": (80.0, 80.0)})[0]))["spend this month"]
    assert month["limit"] == 300.0 and month["limit_source"] == "IT_BUDGETS"
    assert month["share"] == round(80 / 300, 4) and month["level"] == "ok"
    assert month["projected"] == pytest.approx(80 / 9.625 * 30, abs=0.01)
    # $245 on the 28th: 82%, on pace for $266 - a warning. (The same $245 on
    # the 10th is on pace to pass the budget, so it is urgent.)
    late = datetime.datetime(2026, 11, 28, 15, 0, tzinfo=datetime.timezone.utc)
    warned = by_name(read(billing({"2026-11-05": (245.0, 0.0)})[0], late))[
        "spend this month"]
    assert warned["projected"] < 300 and warned["level"] == "warn"
    assert by_name(read(billing({"2026-11-05": (245.0, 0.0)})[0]))[
        "spend this month"]["level"] == "urgent"
    # $290: 97% - urgent.
    assert read(billing({"2026-11-05": (290.0, 0.0)})[0])["level"] == "urgent"


def test_a_pace_that_passes_the_budget_is_urgent_before_it_does(key, monkeypatch):
    use(monkeypatch, it_budgets="runpod=300")
    # $150 by the 10th: half the budget, but $468 by the month's end.
    month = by_name(read(billing({"2026-11-08": (150.0, 150.0)})[0]))["spend this month"]
    assert month["share"] == 0.5 and month["projected"] > 300
    assert month["level"] == "urgent"


def test_the_first_days_are_not_projected(key, monkeypatch):
    use(monkeypatch, it_budgets="runpod=300")
    early = datetime.datetime(2026, 11, 1, 9, 0, tzinfo=datetime.timezone.utc)
    month = by_name(read(billing({"2026-11-01": (40.0, 40.0)})[0], early))[
        "spend this month"]
    assert month["projected"] is None and month["level"] == "ok"


@pytest.mark.parametrize("budget", ["runpod", "runpod=", "runpod=abc", "runpod=-5", "=300"])
def test_a_budget_that_does_not_read_is_ignored_not_guessed(monkeypatch, budget):
    use(monkeypatch, it_budgets=budget)
    assert UM.budgets() == {}


def test_a_refusal_is_said_without_the_body_and_never_as_zero(key):
    got = read(billing({}, status=401, body={"detail": f"bad key {KEY}"})[0])
    assert not got["ok"] and got["figures"] == []
    assert "HTTP 401" in got["why"]
    assert KEY not in json.dumps(got)


def test_an_answer_not_understood_is_said(key):
    got = read(billing({}, body={"unexpected": True})[0])
    assert not got["ok"] and "not understood" in got["why"]


def test_no_key_asks_nothing(monkeypatch):
    monkeypatch.setattr(UM, "_runpod_key", lambda: "")
    client, seen = billing({"2026-11-10": (1.0, 1.0)})
    got = read(client)
    assert not got["ok"] and "not configured" in got["why"] and seen == []


def test_a_poll_keeps_each_reading_and_the_report_reads_the_latest(key, tmp_path,
                                                                    monkeypatch):
    monkeypatch.setattr(UM, "MONITORS", {"runpod": UM.read_runpod})  # this one alone
    usage = provider_usage.UsageStore(str(tmp_path / "u.db"))
    provider_usage.reset(usage)
    assert UM.report()["services"] == [{"service": "runpod", "never_read": True}]
    client, _ = billing({"2026-11-10": (2.0, 2.0)})
    asyncio.run(UM.poll(NOV_10, client))
    client, _ = billing({"2026-11-10": (3.0, 3.0)})
    asyncio.run(UM.poll(NOV_10 + datetime.timedelta(hours=1), client))
    report = UM.report(NOV_10.timestamp() + 7200)
    assert report["readings"] == 2
    latest = report["services"][0]
    assert by_name(latest)["spend today"]["value"] == 3.0
    assert KEY not in json.dumps(report)
    assert UM.health()["last"]["runpod"]["ok"] is True


def test_a_monitor_that_raises_is_a_reading_not_a_stopped_poll(monkeypatch, tmp_path):
    provider_usage.reset(provider_usage.UsageStore(str(tmp_path / "u.db")))

    async def broken(now, client):
        raise RuntimeError("surprise")

    monkeypatch.setitem(UM.MONITORS, "runpod", broken)
    got = asyncio.run(UM.poll(NOV_10))
    assert got[0]["ok"] is False and "RuntimeError" in got[0]["why"]


def test_keith_reads_hourly_only_while_he_is_on(monkeypatch):
    handlers = {("it", "usage"): lambda _t: None}
    at = datetime.datetime(2026, 11, 10, 14, 20, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(netenyahu, "settings", dataclasses.replace(settings, it_agent=False))
    assert netenyahu.schedule(at, handlers) == 0
    monkeypatch.setattr(netenyahu, "settings", dataclasses.replace(settings, it_agent=True))
    assert netenyahu.schedule(at, handlers) == 1
    assert netenyahu.schedule(at.replace(minute=50), handlers) == 0   # same hour
    assert netenyahu.schedule(at.replace(hour=15), handlers) == 1     # the next
    assert sorted(tasks.store().due(10)) == ["it:usage:2026-11-10T14",
                                             "it:usage:2026-11-10T15"]
    assert netenyahu.schedule(at.replace(hour=16), {}) == 0           # no handler


def test_the_handler_does_nothing_unless_keith_may_run(monkeypatch):
    from agents import roster

    called = []
    monkeypatch.setattr(roster, "status", lambda agent: {"may_run": False,
                                                          "why_not": "IT_AGENT=0"})
    monkeypatch.setattr(UM, "poll", lambda *a, **k: called.append(1))
    asyncio.run(UM.handler("it:usage:2026-11-10T14"))
    assert called == []


def test_health_says_the_budgets_and_the_shares():
    from fastapi.testclient import TestClient

    import app

    body = TestClient(app.app).get("/api/health").json()["usage_monitors"]
    assert body["budgets"] == {} and body["warn_share"] == 0.8
    assert body["urgent_share"] == 0.95
