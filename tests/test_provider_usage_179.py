"""Requests per outside service per day, beside each one's limit (§179).

The admin page shows how often API-Sports, Exa, GDELT, Polymarket, GNews and
Finnhub are called, today and on average, with the limit in force and - in
parentheses - what the next paid plan would raise it to. Pinned: a request is
counted where it goes out (so every provider behind `live_sources._json` is
counted once, by host), a failed request still counts, counting never costs a
request, and reading the page never creates the store.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import admin_tracker  # noqa: E402
import config  # noqa: E402
import live_sources  # noqa: E402
import provider_usage  # noqa: E402

NOON = 1_790_000_000.0          # a fixed UTC moment
DAY = 86400


def test_every_provider_the_owner_named_is_reported_in_order():
    rows = provider_usage.report(NOON)
    assert [r["label"] for r in rows] == [
        "API-Sports", "Exa", "GDELT", "Polymarket", "GNews", "Finnhub",
        # §194: weather and the local news collector.
        "National Weather Service", "Open-Meteo", "Local news feeds"]
    for row in rows:
        assert row["limit"] and row["next"]


def test_reading_the_report_never_creates_the_store():
    assert not provider_usage.exists()
    assert all(r["today"] == 0 for r in provider_usage.report(NOON))
    assert not provider_usage.exists()


def test_counts_are_per_utc_day_and_failures_are_kept_beside_them(tmp_path):
    usage = provider_usage.UsageStore(str(tmp_path / "u.db"))
    provider_usage.reset(usage)
    for _ in range(3):
        usage.record("gnews", now=NOON)
    usage.record("gnews", ok=False, now=NOON)
    usage.record("gnews", now=NOON - DAY)
    usage.record("exa", now=NOON - 3 * DAY)
    rows = {r["provider"]: r for r in provider_usage.report(NOON)}
    assert rows["gnews"]["today"] == 4 and rows["gnews"]["failed_today"] == 1
    assert rows["gnews"]["yesterday"] == 1
    assert rows["gnews"]["average"] == round(5 / 7, 1)
    assert rows["exa"]["today"] == 0 and rows["exa"]["average"] == round(1 / 7, 1)


def test_the_rationed_limits_are_read_from_the_settings_in_force(monkeypatch):
    patched = dataclasses.replace(config.settings, api_sports_daily_requests=7500,
                                  gnews_daily_requests=950)
    monkeypatch.setattr(config, "settings", patched)
    rows = {r["provider"]: r for r in provider_usage.report(NOON)}
    # API-Sports is per sport since §180: the ceiling setting caps each sport
    # at its plan, and the free plan's 100 is below 7,500.
    sports = {b["sport"]: b for b in rows["api_sports"]["breakdown"]}
    assert sports["american-football"]["limit"].startswith("Free: 100/day")
    assert "Pro $19/mo: 7,500/day" in sports["american-football"]["next"]
    assert rows["gnews"]["limit"].startswith("950/day")
    assert "1,000/day" in rows["gnews"]["next"]


@pytest.mark.parametrize("host, provider", [
    ("v1.american-football.api-sports.io", "api_sports"),
    ("v3.football.api-sports.io", "api_sports"),
    ("finnhub.io", "finnhub"),
    ("gamma-api.polymarket.com", "polymarket"),
    ("api.sportsdata.io", ""),
    ("evil-finnhub.io", ""),
])
def test_a_request_is_counted_against_the_host_it_went_to(host, provider):
    assert provider_usage.provider_for_host(host) == provider


def _serve(monkeypatch, status):
    def handler(request):
        return httpx.Response(status, json={"ok": True})

    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(live_sources.httpx, "AsyncClient", client)


def test_a_live_provider_request_is_counted_once_where_it_goes_out(monkeypatch):
    _serve(monkeypatch, 200)
    asyncio.run(live_sources._json("https://finnhub.io/api/v1/quote", {}, {}, 2.0))
    rows = {r["provider"]: r for r in provider_usage.report()}
    assert rows["finnhub"]["today"] == 1 and rows["finnhub"]["failed_today"] == 0


def test_a_refused_request_still_counts_against_the_allowance(monkeypatch):
    _serve(monkeypatch, 429)
    with pytest.raises(live_sources.ProviderHTTPError):
        asyncio.run(live_sources._json(
            "https://v1.american-football.api-sports.io/games", {}, {}, 2.0))
    rows = {r["provider"]: r for r in provider_usage.report()}
    assert rows["api_sports"]["today"] == 1
    assert rows["api_sports"]["failed_today"] == 1


def test_counting_never_costs_a_request(monkeypatch):
    class Broken:
        def record(self, *a, **k):
            raise OSError("disk full")

    provider_usage.reset(Broken())
    provider_usage.record("exa")          # logs, does not raise


def test_the_admin_snapshot_carries_the_rows():
    snap = admin_tracker.snapshot()
    assert [r["provider"] for r in snap["providers"]] == list(provider_usage.PROVIDERS)


def test_the_admin_page_draws_them():
    page = (admin_tracker.PROJECT_ROOT / "admin_ui" / "tracker.html").read_text()
    assert 'id="providers"' in page and "drawProviders(s.providers)" in page
    assert "Limit (next plan)" in page


def test_counting_never_touches_the_disk_on_the_callers_thread(tmp_path):
    """`live_sources._json` is on the search path and runs on the event loop,
    so a count is kept in memory and written by a background thread."""
    import threading

    caller = threading.current_thread()
    writers = []

    class Watching(provider_usage.UsageStore):
        def add(self, counts):
            writers.append(threading.current_thread())
            super().add(counts)

    provider_usage.reset(Watching(str(tmp_path / "u.db")))
    for _ in range(5):
        provider_usage.record("exa")
    for thread in threading.enumerate():
        if thread.name == "provider-usage-flush":
            thread.join(5)
    assert writers and all(w is not caller for w in writers)
    assert {r["provider"]: r for r in provider_usage.report()}["exa"]["today"] == 5


def test_a_failed_write_keeps_its_counts_for_the_next_one(tmp_path):
    real = provider_usage.UsageStore(str(tmp_path / "u.db"))

    class Flaky:
        fail = True

        def add(self, counts):
            if self.fail:
                raise OSError("database is locked")
            real.add(counts)

        def days(self, *a, **k):
            return real.days(*a, **k)

    flaky = Flaky()
    provider_usage.reset(flaky)
    provider_usage.record("gdelt")
    provider_usage.flush()                  # fails; the count is put back
    flaky.fail = False
    assert {r["provider"]: r for r in provider_usage.report()}["gdelt"]["today"] == 1
