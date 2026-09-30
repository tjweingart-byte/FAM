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
        "API-Sports", "Exa", "GDELT", "Polymarket", "GNews", "Finnhub"]
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
    assert rows["api_sports"]["limit"].startswith("7,500/day")
    assert rows["gnews"]["limit"].startswith("950/day")
    assert "7,500/day" in rows["api_sports"]["next"]
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
