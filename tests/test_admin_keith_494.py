"""Keith on /admin, item 6 (§494): the five proposals the owner said yes to.

(1) cost per episode on the Overview, (2) the usage monitors on "Outside
service requests" (a share on each provider row, and what each service
says), (3) Claude's spend by what spent it, (4) the Agents page's trial,
tandem, Mclovin's unchecked share and rule-book checks, (5) a Keith page with
incidents, their history and the fix report. Pinned in a browser, from the
tracker's real data (the snapshot built by `admin_tracker` from the stores),
on the real page: each section draws, says what it does when there is
nothing yet, and nothing on the page throws.
"""
from __future__ import annotations

import json
import pathlib
import time

import pytest
from fastapi.testclient import TestClient

import incidents as INC
import pipeline_errors as PE
import provider_usage
import usage_monitors as UM

ROOT = pathlib.Path(__file__).resolve().parent.parent
ADMIN = {"X-Admin-Token": "sekrit"}


@pytest.fixture
def data(monkeypatch, tmp_path):
    import app as appmod

    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "sekrit")
    PE.reset(PE.ErrorStore(str(tmp_path / "e.db")))
    INC.reset(INC.IncidentStore(str(tmp_path / "e.db")))
    provider_usage.reset(provider_usage.UsageStore(str(tmp_path / "u.db")))
    # Something in each section: a provider's quota, Claude's spend by source,
    # a monitor's reading, an incident with its plan, a fix "would".
    import config
    import dataclasses

    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, gnews_daily_requests=100))
    now = time.time()
    for _ in range(85):
        provider_usage.store().record("gnews", now=now)
    provider_usage.spend("episodes", "claude-haiku-5-5", usd=0.42)
    provider_usage.spend("edition:trending", "claude-haiku-5-5", usd=0.18)
    provider_usage.spend("episodes", "web searches", usd=0.05)
    reading = UM.reading("render", [UM.figure("fam-db storage", 0.97, "GB", "now",
                                              limit=1.0, limit_source="its disk",
                                              next_plan="grow the disk")], at=now)
    provider_usage.store().add_reading("render", reading["level"], json.dumps(reading), now)
    INC.watch(now)
    import keith_fixes
    import asyncio

    asyncio.run(keith_fixes.act(now))
    client = TestClient(appmod.app)
    snapshot = client.get("/api/admin/tracker", headers=ADMIN).json()
    agents = client.get("/api/admin/agents", headers=ADMIN).json()
    fixes = client.get("/api/admin/fixes", headers=ADMIN).json()
    alerts = client.get("/api/admin/incidents", headers=ADMIN).json()
    yield {"tracker": snapshot, "agents": agents, "fixes": fixes, "incidents": alerts}
    INC.reset()


def test_the_snapshot_carries_keiths_figures(data):
    s = data["tracker"]
    for key in ("cost_per_episode", "usage_monitors", "claude_spend", "agents_watch",
                "incidents"):
        assert key in s, key
    assert s["claude_spend"]["claude"]["today"] == pytest.approx(0.60)
    assert any(r["cause"] == "quota:gnews:" for r in s["incidents"]["open"])


def test_the_five_proposals_draw_on_the_real_page(data, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    html = (ROOT / "admin_ui" / "tracker.html").read_text()
    script = (ROOT / "admin_ui" / "keith_alerts.js").read_text()
    answers = {"/api/admin/tracker": data["tracker"], "/api/admin/agents": data["agents"],
               "/api/admin/fixes": data["fixes"], "/api/admin/incidents": data["incidents"],
               "/api/admin/schema": {"stores": []}}
    errors = []
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"no browser: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))

        def route(r):
            path = "/" + r.request.url.split("/", 3)[3].split("?")[0].split("#")[0]
            if path == "/admin":
                r.fulfill(content_type="text/html; charset=utf-8", body=html)
            elif path == "/admin/keith-alerts.js":
                r.fulfill(content_type="application/javascript; charset=utf-8", body=script)
            elif path == "/api/admin/login":
                r.fulfill(content_type="application/json", body="{}")
            elif path in answers:
                r.fulfill(content_type="application/json", body=json.dumps(answers[path]))
            else:
                r.fulfill(content_type="application/json", body="{}")

        page.route("**/*", route)
        page.goto("http://fam.test/admin#overview")
        # Here the alerts answer before sign-in (a real page gets a 404 until
        # then): close the pop-up for this page view, as the owner can.
        page.wait_for_selector("#keith-alerts")
        page.click(".keith-close")
        # The page reads its data once the owner signs in through its form.
        page.fill("#adminEmail", "owner@example.com")
        page.fill("#adminPassword", "x")
        page.click("#loginBtn")
        page.wait_for_selector("#kpis .kpi.cost")
        # (1) Cost per episode: three tiles on the Overview.
        tiles = page.inner_text("#kpis").lower()          # labels are styled in capitals
        assert "agents per episode" in tiles and "claude per episode" in tiles
        assert "nightly agent jobs" in tiles and "plan 0.45¢" in tiles
        # (2) The usage monitors: a share on the provider row, the monitors' table.
        page.evaluate("location.hash = 'services'")
        page.wait_for_selector("#usageMonitors table")
        assert "85% of this day's 100" in page.inner_text("#providers")
        usage = page.inner_text("#usageMonitors")
        assert "fam-db storage" in usage and "grow the disk" in usage
        # (3) Claude's spend by what spent it, searches apart.
        spend = page.inner_text("#claudeSpend")
        assert "edition:trending" in spend and "$0.60" in spend
        # (4) The agents page's watch.
        page.evaluate("location.hash = 'agents'")
        watch = page.inner_text("#agentsWatch")
        assert "Shakespeare's two weeks" in watch and "Rule-book checks" in watch
        assert "No tandem outcomes yet" in watch
        # (5) The Keith page: open incidents with plans, the fix report, the badge.
        page.evaluate("location.hash = 'keith'")
        page.wait_for_selector("#keithOpen table")
        assert "GNews" in page.inner_text("#keithOpen")
        assert "Recommended" in page.inner_text("#keithOpen")
        assert "none - Keith observes only" in page.inner_text("#keithFixes")
        assert page.inner_text("#navKeith").strip().isdigit()
        page.click("#keithFilter .chip[data-src='quota']")
        assert "GNews" in page.inner_text("#keithOpen")
        browser.close()
    assert errors == []


def test_keith_is_in_the_menu():
    html = (ROOT / "admin_ui" / "tracker.html").read_text()
    assert '<a href="#keith" data-nav="keith">' in html
    assert 'data-page="keith"' in html
