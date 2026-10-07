"""The finance workbook: every service on one sheet, every day's spend on another.

What is pinned: the recorded spend reaches the ledger on the day it happened;
a usage-billed service reads the ledger rather than a typed number; the plan
the deployment says it bought decides a provider's row; a store that does not
exist is never created by reading it; and only an operator can download it.
"""
from __future__ import annotations

import io
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import financials  # noqa: E402
import metering  # noqa: E402

NOW = time.mktime((2026, 10, 7, 12, 0, 0, 0, 0, 0)) - time.timezone


@pytest.fixture
def stores(tmp_path, monkeypatch):
    for var, name in (("METERING_DB", "metering.db"),
                      ("THUMBNAILS_DB", "thumbnails.db"),
                      ("PROVIDER_USAGE_DB", "provider_usage.db")):
        monkeypatch.setenv(var, str(tmp_path / name))
    return tmp_path


def _episode(store, at, *, hit=False):
    u = metering.Usage()
    u.model, u.model_calls = "claude-sonnet-5", 2
    u.input_tokens, u.output_tokens = 6000, 2500
    u.exa_searches, u.exa_cost = 1, 0.015
    u.audio_seconds = 120
    u.cache_hit = hit
    store.record("listener", u, surface="search", minutes=2, at=at)


def _book(data):
    return load_workbook(io.BytesIO(data))


def test_spend_lands_on_the_day_it_happened(stores):
    store = metering.MeterStore()
    _episode(store, NOW)
    _episode(store, NOW - 86400)
    _episode(store, NOW - 86400, hit=True)
    days = financials.daily_ledger(NOW)
    assert days["2026-10-07"]["episodes"] == 1
    assert days["2026-10-06"]["episodes"] == 1
    assert days["2026-10-06"]["hits"] == 1
    claude = metering.price_of(_usage()).claude
    assert days["2026-10-07"]["claude"] == pytest.approx(claude, abs=1e-6)


def _usage():
    u = metering.Usage()
    u.model, u.model_calls = "claude-sonnet-5", 2
    u.input_tokens, u.output_tokens = 6000, 2500
    return u


def test_the_ledger_runs_to_today_with_a_row_for_every_day(stores):
    book = _book(financials.build(NOW))
    led = book["Daily Ledger"]
    dates = [led.cell(row=r, column=1).value for r in range(5, led.max_row)]
    assert len(dates) == financials.LEDGER_MIN_DAYS
    assert dates[-1].date().isoformat() == "2026-10-07"
    assert led.cell(row=led.max_row, column=1).value == "Total"


def test_usage_billed_rows_read_the_ledger_never_a_typed_price(stores):
    """A typed number for Claude would be a guess that never moves; the row has
    to follow what was actually spent."""
    cs = _book(financials.build(NOW))["Cost Structure"]
    for r in range(6, cs.max_row + 1):
        if cs.cell(row=r, column=8).value == "usage":
            assert str(cs.cell(row=r, column=10).value).startswith("=SUMIFS('Daily Ledger'")


def test_every_service_the_code_calls_has_a_row(stores):
    vendors = {r["vendor"] for r in financials.catalogue()}
    for needed in ("Render", "RunPod", "Anthropic", "Exa", "GNews", "API-Sports",
                   "Finnhub", "Open-Meteo", "Polymarket", "GDELT",
                   "National Weather Service", "Google AI Studio", "Viral Loops",
                   "Apple"):
        assert needed in vendors, needed


def _set(monkeypatch, **changes):
    import dataclasses

    import config

    monkeypatch.setattr(config, "settings",
                        dataclasses.replace(config.settings, **changes))


def test_a_bought_plan_changes_its_row_and_leaves_the_launch_list(stores, monkeypatch):
    _set(monkeypatch, gnews_plan="free")
    free = {r["vendor"]: r for r in financials.catalogue()}["GNews"]
    assert free["price"] == 0 and "licence" in free["status"]
    assert any("GNews" in p["item"] for p in financials.launch_purchases(
        financials.catalogue()))
    _set(monkeypatch, gnews_plan="essential")
    paid = {r["vendor"]: r for r in financials.catalogue()}["GNews"]
    assert paid["status"] == "Paying" and paid["unit"] == "EUR/month"
    assert not any("GNews" in p["item"] for p in financials.launch_purchases(
        financials.catalogue()))


def test_a_pod_is_a_fixed_rental_and_serverless_is_usage(stores, monkeypatch):
    _set(monkeypatch, remote_voice_transport="runpod")
    gpu = [r for r in financials.catalogue() if r["category"] == financials.VOICE][0]
    assert gpu["unit"] == "usage" and gpu["ledger"] == "gpu"
    _set(monkeypatch, remote_voice_transport="http")
    gpu = [r for r in financials.catalogue() if r["category"] == financials.VOICE][0]
    assert gpu["unit"] == "/month" and gpu["price"] > 100


def test_reading_never_creates_a_store(stores):
    financials.build(NOW)
    for name in ("metering.db", "thumbnails.db", "provider_usage.db"):
        assert not (stores / name).exists(), name


def test_only_an_operator_can_download_it(stores, monkeypatch):
    client = TestClient(appmod.app)
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "")
    assert client.get("/api/admin/financials.xlsx").status_code == 404
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "the-real-token")
    assert client.get("/api/admin/financials.xlsx",
                      headers={"X-Admin-Token": "guess"}).status_code == 404
    res = client.get("/api/admin/financials.xlsx",
                     headers={"X-Admin-Token": "the-real-token"})
    assert res.status_code == 200
    assert "FAM_Financials_" in res.headers["content-disposition"]
    assert _book(res.content).sheetnames == [
        "Dashboard", "Cost Structure", "Daily Ledger", "12-Month Plan",
        "Provider Calls", "Notes"]


def test_the_plan_is_driven_by_listeners_with_sports_apart(stores):
    """Cost follows new episodes, and sports misses the cache more and spends
    API-Sports requests - so it has its own rows, not a blended growth rate."""
    plan = _book(financials.build(NOW))["12-Month Plan"]
    labels = {plan.cell(row=r, column=1).value: r for r in range(1, plan.max_row + 1)}
    for needed in ("Listeners (monthly active)", "  of which sports",
                   "Cache miss rate - sports searches", "API-Sports plans",
                   "Sports share of costs", "Total costs"):
        assert needed in labels, needed
    episode = plan.cell(row=labels["Cost of one new episode ($)"], column=2).value
    assert "'Daily Ledger'" in episode, "the forecast must learn from recorded spend"
    api = plan.cell(row=labels["API-Sports plans"], column=4).value
    assert "7500" in api and "75000" in api
