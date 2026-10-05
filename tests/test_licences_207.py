"""§207: the deploy says whether it may use each licensed provider commercially.

GNews' free plan is for development, Finnhub's for personal use, and
Open-Meteo's keyless endpoint is non-commercial. None of their answers says
which plan a key is on, so the deploy says it (`GNEWS_PLAN`, `FINNHUB_PLAN`,
or Open-Meteo's own key) and `/admin` and `/api/health` flag a free plan that
is in use - the question to answer before charging anybody.
"""
from __future__ import annotations

import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import credentials  # noqa: E402
import provider_usage  # noqa: E402


@pytest.fixture
def deploy(monkeypatch):
    def configure(**changes):
        meteo_key = changes.pop("meteo_key", "")
        monkeypatch.setattr(config, "settings",
                            dataclasses.replace(config.settings, **changes))
        monkeypatch.setattr(credentials, "active",
                            lambda name: meteo_key if name == "OPEN_METEO_API_KEY" else "")
        return provider_usage.licences()
    return configure


def test_unused_free_plans_are_not_a_problem(deploy):
    report = deploy(gnews_key="", finnhub_key="", open_meteo_keyless=False)
    assert report["commercial_ready"] is True
    assert report["non_commercial_in_use"] == []


def test_free_plans_in_use_are_flagged(deploy):
    report = deploy(gnews_key="g", finnhub_key="f", open_meteo_keyless=True,
                    gnews_plan="free", finnhub_plan="free")
    assert report["commercial_ready"] is False
    assert set(report["non_commercial_in_use"]) == {"gnews", "finnhub", "open_meteo"}
    assert "GNEWS_PLAN" in report["providers"]["gnews"]["buy"]


def test_paid_plans_clear_it(deploy):
    report = deploy(gnews_key="g", finnhub_key="f", gnews_plan="essential",
                    finnhub_plan="commercial", gnews_daily_requests=950,
                    open_meteo_keyless=False, meteo_key="k")
    assert report["providers"]["open_meteo"]["commercial"] is True
    assert report["commercial_ready"] is True
    assert "warning" not in report["providers"]["gnews"]


def test_a_paid_gnews_plan_still_capped_at_the_free_guard_says_so(deploy):
    report = deploy(gnews_key="g", gnews_plan="essential", gnews_daily_requests=90)
    assert "950" in report["providers"]["gnews"]["warning"]


def test_the_admin_rows_carry_the_licence(deploy, monkeypatch, tmp_path):
    deploy(gnews_key="g", gnews_plan="free")
    monkeypatch.setattr(provider_usage, "exists", lambda: False)
    rows = {r["provider"]: r for r in provider_usage.report()}
    assert rows["gnews"]["licence"]["commercial"] is False
    assert "licence" not in rows["exa"]


def test_health_reports_it():
    from fastapi.testclient import TestClient
    import app as appmod

    with TestClient(appmod.app) as c:
        body = c.get("/api/health").json()
    assert "commercial_ready" in body["licences"]
