"""§566: one budget a day for everything written ahead, and seeing it.

* `AHEAD_DAILY_DOLLARS` stops each of the three writers - the mixes' edition
  (myFAM on the listener's screen), the rails edition and prefetch (DailyFAM)
  - and each says it was the shared budget that stopped it.
* 0 is no shared ceiling; each writer's own ceilings still work alone.
* `AHEAD_DAILYFAM_SHARE` holds part of it for DailyFAM's writers.
* `/api/health` and the admin snapshot carry the day per writer; the
  "Written ahead today" card draws it on the real page.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import pathlib
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ahead_budget  # noqa: E402
import cache as cache_mod  # noqa: E402
import config  # noqa: E402
import daily_edition  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import mixes as M  # noqa: E402
import prefetch  # noqa: E402
import rails_edition  # noqa: E402
import stories as S  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
COST = 0.10


class Writer:
    """Briefs and writes without a model; every episode costs `COST`."""

    client = None

    def __init__(self):
        self.written = []

    async def understand(self, plan, notes=None):
        return dataclasses.replace(plan, brief=ei.Brief(
            query=plan.query, subject=plan.query, search_query=plan.query))

    async def live_lookup(self, plan, notes=None):
        return plan

    async def stream_sentences(self, plan, notes=None):
        self.written.append(plan.query)
        if notes is not None:
            notes.sourced_at = time.time()
            notes.title = "A title"
        yield "One."


@pytest.fixture
def priced(monkeypatch):
    """Every episode, brief or warm costs `COST` at metering's prices."""
    monkeypatch.setattr(prefetch, "_dollars", lambda notes: COST)


def _settings(monkeypatch, **kw):
    s = dataclasses.replace(config.settings, daily_edition=True,
                            rails_edition=True, edition_concurrency=1,
                            edition_batch=False, **kw)
    monkeypatch.setattr(config, "settings", s)
    return s


@pytest.fixture
def stores(tmp_path):
    daily_edition.reset(daily_edition.EditionStore(str(tmp_path / "ed.db")))
    rails_edition.reset()
    S.reset()
    yield M.MixStore(str(tmp_path / "m.db"))
    daily_edition.reset()
    rails_edition.reset()
    S.reset()


def _mix(store, n=5):
    store.create("a", "Morning", [{"query": f"what happened to topic {i} today",
                                   "title": f"Topic {i}"} for i in range(n)])


def _today(writer):
    rows = ahead_budget.store().rows_since(ahead_budget.day())
    return next((r for r in rows if r["writer"] == writer), {})


def _pool(n=5):
    now = time.time()
    S._POOL = S.Pool(stories=[
        S.Story(subject=f"s{i}", title=f"Story {i}", angle="an angle",
                query=f"what happened in story {i}", domain=S.MARKETS,
                first_seen=now - 600, last_seen=now,
                shelf_life=S.DOMAIN_SHELF_LIFE[S.MARKETS])
        for i in range(n)], fetched_at=now)


# --------------------------------------------------------------------------
# The settings
# --------------------------------------------------------------------------
def test_off_by_default_so_nothing_changes():
    assert config.settings.ahead_daily_dollars == 0.0
    assert config.settings.ahead_dailyfam_share == 0.0
    assert ahead_budget.allows("dailyfam") == (True, "")


# --------------------------------------------------------------------------
# The mixes' edition (myFAM on the screen)
# --------------------------------------------------------------------------
def test_the_shared_budget_stops_the_mixes_edition(monkeypatch, stores, priced):
    _settings(monkeypatch, ahead_daily_dollars=0.25)
    _mix(stores)
    report = asyncio.run(daily_edition.build(stores, Writer(),
                                             cache_mod.MemoryScriptCache()))
    # $0.00, $0.10, $0.20 are under $0.25; at $0.30 it stops.
    assert report["written"] == 3
    assert report["skipped_for_shared_budget"] == 2
    assert report["skipped_for_ceiling"] == 2
    assert any("shared daily budget" in s for s in report["stopped"])
    row = _today("dailyfam")
    assert row["episodes"] == 3 and row["usd"] == pytest.approx(0.30)
    assert row["stopped"] == "shared"


def test_zero_is_no_shared_ceiling(monkeypatch, stores, priced):
    _settings(monkeypatch, ahead_daily_dollars=0.0)
    _mix(stores)
    report = asyncio.run(daily_edition.build(stores, Writer(),
                                             cache_mod.MemoryScriptCache()))
    assert report["written"] == 5 and report["skipped_for_ceiling"] == 0
    # Still counted, for the admin page.
    assert _today("dailyfam")["usd"] == pytest.approx(0.50)
    assert not _today("dailyfam")["stopped"]


def test_its_own_episode_ceiling_still_works_alone(monkeypatch, stores, priced):
    _settings(monkeypatch, daily_edition_max_episodes=2)
    _mix(stores)
    report = asyncio.run(daily_edition.build(stores, Writer(),
                                             cache_mod.MemoryScriptCache()))
    assert report["written"] == 2 and report["skipped_for_ceiling"] == 3
    assert report["skipped_for_shared_budget"] == 0
    assert _today("dailyfam")["stopped"] == "own_episodes"


def test_its_own_dollar_ceiling_still_works_alone(monkeypatch, stores, priced):
    _settings(monkeypatch, daily_edition_max_dollars=0.15)
    _mix(stores)
    report = asyncio.run(daily_edition.build(stores, Writer(),
                                             cache_mod.MemoryScriptCache()))
    assert report["written"] == 2
    assert report["skipped_for_shared_budget"] == 0
    assert _today("dailyfam")["stopped"] == "own_dollars"


def test_the_lower_of_the_two_binds(monkeypatch, stores, priced):
    _settings(monkeypatch, daily_edition_max_episodes=4, ahead_daily_dollars=0.15)
    _mix(stores)
    report = asyncio.run(daily_edition.build(stores, Writer(),
                                             cache_mod.MemoryScriptCache()))
    assert report["written"] == 2 and report["skipped_for_shared_budget"] == 3


# --------------------------------------------------------------------------
# The rails edition (DailyFAM news)
# --------------------------------------------------------------------------
def test_the_shared_budget_stops_the_rails_edition(monkeypatch, stores, priced):
    _settings(monkeypatch, ahead_daily_dollars=0.15)
    _pool(4)
    report = asyncio.run(rails_edition.build(Writer(),
                                             cache_mod.MemoryScriptCache()))
    assert report["written"] == 2 and report["skipped_for_shared_budget"] == 2
    assert _today("rails")["stopped"] == "shared"


def test_one_writers_spend_counts_against_the_others(monkeypatch, stores, priced):
    """Shared means shared: what the mixes spent is gone for the rails."""
    _settings(monkeypatch, ahead_daily_dollars=0.25)
    ahead_budget.record("dailyfam", episodes=3, usd=0.30)
    _pool(2)
    report = asyncio.run(rails_edition.build(Writer(),
                                             cache_mod.MemoryScriptCache()))
    assert report["written"] == 0 and report["skipped_for_shared_budget"] == 2


# --------------------------------------------------------------------------
# Prefetch (DailyFAM cards)
# --------------------------------------------------------------------------
@pytest.fixture
def warmer(monkeypatch, priced):
    prefetch.reset()
    monkeypatch.setattr(prefetch, "settings", dataclasses.replace(
        config.settings, prefetch=True, prefetch_level="script"))
    pf = prefetch.Prefetcher(generator=Writer(),
                             cache=cache_mod.MemoryScriptCache(),
                             budget=prefetch.Budget(max_episodes=10,
                                                    max_dollars=5.0))
    yield pf
    prefetch.reset()


def _candidate(i):
    return prefetch.Candidate(query=f"what did team {i} do", minutes=2,
                              source="test", reason="because")


def test_the_shared_budget_stops_prefetch(monkeypatch, warmer):
    _settings(monkeypatch, ahead_daily_dollars=0.15)
    results = [asyncio.run(warmer.warm(_candidate(i))) for i in range(3)]
    assert results == ["script", "script", "budget"]
    row = _today("prefetch")
    assert row["episodes"] == 2 and row["stopped"] == "shared"


def test_prefetchs_own_ceiling_still_works_alone(monkeypatch, warmer):
    _settings(monkeypatch)
    warmer.budget = prefetch.Budget(max_episodes=1, max_dollars=5.0)
    results = [asyncio.run(warmer.warm(_candidate(i))) for i in range(2)]
    assert results == ["script", "budget"]
    assert _today("prefetch")["stopped"] == "own_episodes"


# --------------------------------------------------------------------------
# The reserve for DailyFAM
# --------------------------------------------------------------------------
def test_the_reserve_is_held_from_the_mixes_edition_only(monkeypatch):
    _settings(monkeypatch, ahead_daily_dollars=1.0, ahead_dailyfam_share=0.4)
    ahead_budget.record("dailyfam", episodes=6, usd=0.6)
    # The mixes have had their $0.60; the $0.40 left is DailyFAM's.
    assert ahead_budget.allows("dailyfam") == (False, "reserve")
    assert ahead_budget.allows("rails") == (True, "")
    assert ahead_budget.allows("prefetch") == (True, "")
    ahead_budget.record("rails", episodes=4, usd=0.4)
    assert ahead_budget.allows("rails") == (False, "shared")


def test_dailyfam_spending_past_its_reserve_is_counted_once(monkeypatch):
    _settings(monkeypatch, ahead_daily_dollars=1.0, ahead_dailyfam_share=0.2)
    ahead_budget.record("rails", usd=0.5)
    ahead_budget.record("dailyfam", usd=0.4)
    assert ahead_budget.allows("dailyfam") == (True, "")
    ahead_budget.record("dailyfam", usd=0.1)
    assert ahead_budget.allows("dailyfam") == (False, "shared")


# --------------------------------------------------------------------------
# What the owner sees
# --------------------------------------------------------------------------
def test_the_report_shape(monkeypatch):
    _settings(monkeypatch, ahead_daily_dollars=2.0, ahead_dailyfam_share=0.25,
              rails_edition_max_episodes=40)
    ahead_budget.record("rails", episodes=3, usd=0.3)
    ahead_budget.note_stopped("prefetch", "own_dollars")
    r = ahead_budget.report()
    assert [w["writer"] for w in r["writers"]] == ["dailyfam", "rails", "prefetch"]
    labels = [w["label"] for w in r["writers"]]
    assert labels == ["myFAM mixes (05:00 edition)",
                      "DailyFAM news (05:00 and 17:00 editions)",
                      "DailyFAM cards (written after a page is drawn)"]
    rails = r["writers"][1]
    assert rails["episodes"] == 3 and rails["usd"] == pytest.approx(0.3)
    assert rails["own"]["env"]["episodes"] == "RAILS_EDITION_MAX_EPISODES"
    assert rails["max_episodes_per_day"] == 80        # 40 an edition, twice
    assert r["writers"][0]["max_dollars_per_day"] is None   # no ceiling
    assert r["writers"][2]["stopped"]["why"] == "own_dollars"
    assert r["shared"]["ceiling"] == 2.0 and r["shared"]["used"] == pytest.approx(0.3)
    assert r["shared"]["reserve_usd"] == pytest.approx(0.5)
    assert r["shared"]["env"] == {"ceiling": "AHEAD_DAILY_DOLLARS",
                                  "reserve": "AHEAD_DAILYFAM_SHARE"}
    assert r["prefetch_level"]["env"] == "PREFETCH_LEVEL"
    cpe = r["cost_per_episode"]
    assert cpe["by_writer"]["rails"]["per_episode"] == pytest.approx(0.1)
    assert "search" in cpe and "hit_rate" in r


def test_health_and_the_tracker_carry_it(monkeypatch):
    import app as appmod
    from fastapi.testclient import TestClient

    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "sekrit")
    ahead_budget.record("dailyfam", episodes=2, usd=0.2)
    client = TestClient(appmod.app)
    health = client.get("/api/health").json()["written_ahead"]
    assert health["writers"][0]["episodes"] == 2
    assert "cost_per_episode" not in health, "the history is the admin page's"
    snap = client.get("/api/admin/tracker",
                      headers={"X-Admin-Token": "sekrit"}).json()
    assert snap["written_ahead"]["writers"][0]["usd"] == pytest.approx(0.2)
    assert "cost_per_episode" in snap["written_ahead"]


def test_the_card_draws_on_the_real_page(monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    _settings(monkeypatch, ahead_daily_dollars=1.0, ahead_dailyfam_share=0.3)
    ahead_budget.record("dailyfam", episodes=7, usd=0.7)
    ahead_budget.note_stopped("dailyfam", "reserve")
    snapshot = {"at": time.time(), "metrics": {}, "signups": [],
                "written_ahead": json.loads(json.dumps(ahead_budget.report()))}
    html = (ROOT / "admin_ui" / "tracker.html").read_text()
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
            elif path == "/api/admin/tracker":
                r.fulfill(content_type="application/json", body=json.dumps(snapshot))
            elif path.endswith(".js"):
                r.fulfill(content_type="application/javascript", body="")
            else:
                r.fulfill(content_type="application/json", body="{}")

        page.route("**/*", route)
        page.goto("http://fam.test/admin#services")
        page.fill("#adminEmail", "owner@example.com")
        page.fill("#adminPassword", "x")
        page.click("#loginBtn")
        page.wait_for_selector("#aheadSpend table")
        text = page.inner_text("#aheadSpend")
        browser.close()
    assert "myFAM mixes (05:00 edition)" in text
    assert "DailyFAM news (05:00 and 17:00 editions)" in text
    assert "DailyFAM cards (written after a page is drawn)" in text
    assert "AHEAD_DAILY_DOLLARS" in text and "PREFETCH_LEVEL" in text
    assert "held for DailyFAM" in text
    assert "$0.70 / $1.00" in text
    assert "Cost per episode, last 7 days" in text
    assert "a searchfam episode" in text.lower()      # labels are in capitals
    assert "Taps that found a ready script" in text
    assert errors == []
