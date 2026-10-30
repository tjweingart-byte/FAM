"""The Agents panel on /admin (§346): read-only, admins only, true to the ledger."""
from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import metering
from agents import names

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def admin(monkeypatch, tmp_path):
    import app as appmod

    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "sekrit")
    store = metering.MeterStore(str(tmp_path / "metering.db"))
    monkeypatch.setattr(appmod, "METER", store)
    return appmod, TestClient(appmod.app), store


def test_only_an_admin_sees_it(admin):
    _appmod, client, _store = admin
    assert client.get("/api/admin/agents").status_code == 404
    assert client.get("/api/admin/agents", headers={"X-Admin-Token": "wrong"}).status_code == 404
    assert client.get("/api/admin/agents", headers={"X-Admin-Token": "sekrit"}).status_code == 200


def test_every_agent_is_shown_off_and_held_with_nothing_spent(admin):
    _appmod, client, _store = admin
    body = client.get("/api/admin/agents", headers={"X-Admin-Token": "sekrit"}).json()
    assert body["enabled"] is False and body["lock"]["signed"] is False
    assert [r["role"] for r in body["agents"]] == list(names.AGENTS)
    for row in body["agents"]:
        assert row["on"] is False and row["may_run"] is False and row["why_not"] == "AGENTS=0"
        assert row["week"]["requests"] == 0 and row["week"]["cost_usd"] is None
    assert body["recent"] == []


def test_spend_and_fallbacks_come_from_the_ledger(admin):
    appmod, client, store = admin
    store.record_agent_call(agent="editor", task="outline", status="ok", vendor="anthropic",
                            model="claude-sonnet-5-5", served_model="claude-sonnet-5-5",
                            rulebook_version="3", cost_usd=0.02)
    store.record_agent_call(agent="editor", task="outline", status="timeout",
                            vendor="anthropic", model="claude-sonnet-5-5", cost_usd=None,
                            reason="no answer within 60s")
    store.record_agent_call(agent="supervisor", task="check a sentence", status="held",
                            reason="no signed manifest")
    body = client.get("/api/admin/agents", headers={"X-Admin-Token": "sekrit"}).json()
    rows = {r["role"]: r for r in body["agents"]}
    assert rows["editor"]["week"]["requests"] == 2
    assert rows["editor"]["week"]["sent"] == 1
    assert rows["editor"]["week"]["cost_usd"] == pytest.approx(0.02)
    assert rows["editor"]["week"]["fallbacks"] == 1
    assert rows["supervisor"]["week"]["cost_usd"] is None   # nothing sent is not $0
    assert body["recent"][0]["agent"] == "supervisor"       # newest first
    assert rows["contextualizer"]["week"]["last_at"] is None


def test_the_panel_changes_nothing():
    """Read-only (the owner's choice, §341): the page posts nothing about
    agents and the server has no route that changes one."""
    page = (ROOT / "admin_ui" / "tracker.html").read_text(encoding="utf-8")
    assert 'data-page="agents"' in page and 'data-nav="agents"' in page
    assert not re.search(r'api\("/api/admin/agents[^"]*",\s*\{', page)
    import app as appmod

    routes = [(r.path, sorted(getattr(r, "methods", []) or [])) for r in appmod.app.routes]
    agent_routes = [(p, m) for p, m in routes if "agents" in p]
    assert agent_routes == [("/api/admin/agents", ["GET"])]
