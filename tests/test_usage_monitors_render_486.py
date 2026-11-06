"""Keith's usage monitors, piece 2d: Render (§486).

`fam`'s memory against its plan's, `fam-db`'s storage against its disk and
its memory against its plan's, and `fam`'s outbound bandwidth this month
against the workspace's 25 GB. Pinned: the requests asked, the figures in GB
whatever unit Render answers in, the levels with the next plan, what Render
does not answer listed and never a zero, nothing asked without the key, the
key never in a reading and never held by staging.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import json

import httpx
import pytest

import spend_guard
import usage_monitors as UM

NOW = datetime.datetime(2026, 11, 10, 15, 0, tzinfo=datetime.timezone.utc)
KEY = "rnd_SECRET_KEY_VALUE"
SRV, DPG = "srv-fam", "dpg-famdb"


def use(monkeypatch, **changes):
    monkeypatch.setattr(UM, "settings", dataclasses.replace(UM.settings, **changes))


@pytest.fixture
def render(monkeypatch):
    use(monkeypatch, render_service_id=SRV, render_postgres_name="fam-db",
        render_bandwidth_included_gb=25.0)
    monkeypatch.setattr(UM, "_secret", lambda name: KEY if name == "RENDER_API_KEY" else "")


def series(*values, unit="GB"):
    return [{"labels": [{"field": "resource", "value": "x"}], "unit": unit,
             "values": [{"timestamp": f"2026-11-10T14:{i:02d}:00Z", "value": v}
                        for i, v in enumerate(values)]}]


def api(answers: dict, fail: dict = ()):
    """A Render that answers each (path, resource) from `answers`."""
    seen = []

    def answer(request):
        seen.append(request)
        path = request.url.path.replace("/v1", "", 1)
        resource = request.url.params.get("resource", "")
        if (path, resource) in dict(fail):
            return httpx.Response(dict(fail)[(path, resource)], json={"msg": KEY})
        body = answers.get((path, resource), answers.get(path))
        if body is None:
            return httpx.Response(404, json={"msg": "nope"})
        return httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(answer)), seen


def normal(over=None):
    answers = {
        f"/services/{SRV}": {"id": SRV, "serviceDetails": {"plan": "standard"}},
        ("/metrics/memory", SRV): series(0.9, 1.1),
        ("/metrics/memory-limit", SRV): series(2.0),
        ("/metrics/bandwidth", SRV): series(400, 300, unit="MB"),
        "/postgres": [{"cursor": "c", "postgres": {"id": DPG, "name": "fam-db",
                                                    "plan": "basic_256mb", "diskSizeGB": 1,
                                                    "diskAutoscalingEnabled": False}}],
        ("/metrics/disk-usage", DPG): series(0.5675),
        ("/metrics/memory", DPG): series(150, unit="MB"),
        ("/metrics/memory-limit", DPG): series(256, unit="MiB"),
    }
    answers.update(over or {})
    return answers


def read(client):
    return asyncio.run(UM.read_render(NOW, client))


def by_name(got):
    return {f["name"]: f for f in got["figures"]}


def test_the_service_and_its_database_are_read(render):
    client, seen = api(normal())
    got = read(client)
    assert got["ok"] and got["level"] == "ok", got
    f = by_name(got)
    assert f["fam memory"]["value"] == 1.1 and f["fam memory"]["limit"] == 2.0
    assert "Pro (2 CPU, 4 GB), $85/month" == f["fam memory"]["next"]
    assert f["fam outbound bandwidth this month"]["value"] == 0.7
    assert f["fam outbound bandwidth this month"]["limit"] == 25.0
    assert f["fam-db storage"]["value"] == 0.5675 and f["fam-db storage"]["limit"] == 1.0
    assert f["fam-db memory"]["value"] == 0.15
    assert f["fam-db memory"]["limit"] == pytest.approx(256 * 1024 ** 2 / 1e9, abs=1e-4)
    assert "$19/month" in f["fam-db memory"]["next"]
    assert got["detail"]["postgres_plan"] == "basic_256mb"
    assert got["detail"]["disk_autoscaling"] is False
    for request in seen:
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        assert request.method == "GET"                              # reads only
    month = next(r for r in seen if r.url.path.endswith("/metrics/bandwidth"))
    assert month.url.params["startTime"] == "2026-11-01T00:00:00Z"
    listed = next(r for r in seen if r.url.path.endswith("/postgres"))
    assert listed.url.params["name"] == "fam-db"


def test_the_database_filling_up_warns_then_is_urgent(render):
    got = read(api(normal({("/metrics/disk-usage", DPG): series(0.85)}))[0])
    assert by_name(got)["fam-db storage"]["level"] == "warn"
    got = read(api(normal({("/metrics/disk-usage", DPG): series(0.97)}))[0])
    assert got["level"] == "urgent"
    assert "5 GB" in by_name(got)["fam-db storage"]["next"]


def test_bandwidth_on_pace_past_the_workspace_s_allowance_is_urgent(render):
    # 12 GB by the 10th is on pace for ~37 GB in November.
    got = read(api(normal({("/metrics/bandwidth", SRV): series(12, unit="GB")}))[0])
    bw = by_name(got)["fam outbound bandwidth this month"]
    assert bw["projected"] > 25 and bw["level"] == "urgent"
    assert "$0.15 per GB" in bw["next"]


def test_what_render_does_not_answer_is_listed_never_zero(render):
    client, _ = api(normal(), fail={("/metrics/memory", DPG): 500,
                                    ("/metrics/disk-usage", DPG): 403})
    got = read(client)
    assert got["ok"]
    assert "fam-db storage" not in by_name(got) and "fam-db memory" not in by_name(got)
    said = " ".join(got["detail"]["not_read"])
    assert "HTTP 500" in said and "HTTP 403" in said
    assert KEY not in json.dumps(got)


def test_a_unit_not_understood_is_said(render):
    got = read(api(normal({("/metrics/memory", SRV): series(5, unit="furlongs")}))[0])
    assert "fam memory" not in by_name(got)
    assert any("furlongs" in w for w in got["detail"]["not_read"])


def test_a_database_not_found_is_said(render):
    got = read(api(normal({"/postgres": []}))[0])
    assert any("not found" in w for w in got["detail"]["not_read"])
    assert "fam memory" in by_name(got)


def test_nothing_answering_is_a_failed_reading(render):
    client, _ = api({})
    got = read(client)
    assert not got["ok"] and got["figures"] == [] and "HTTP 404" in got["why"]


def test_without_the_key_or_the_service_nothing_is_asked(monkeypatch):
    use(monkeypatch, render_service_id=SRV)
    monkeypatch.setattr(UM, "_secret", lambda name: "")
    client, seen = api(normal())
    got = read(client)
    assert not got["ok"] and "not configured" in got["why"] and seen == []


def test_staging_never_holds_the_key_and_render_is_a_monitor():
    assert "RENDER_API_KEY" in spend_guard.PAID_CREDENTIALS
    assert UM.MONITORS["render"] is UM.read_render
