"""§419: FAM's BALLDONTLIE plan is ALL-ACCESS - every sport, every endpoint.

`BALLDONTLIE_TIER=all-access` reaches everything GOAT does (the top of
`ENDPOINT_TIERS`), is paced at the highest published minute, carries the
myFAM sweep, and is priced on the finance workbook.
"""
from __future__ import annotations

import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import balldontlie  # noqa: E402
import balldontlie_scores as bs  # noqa: E402
import config  # noqa: E402


def test_all_access_reaches_every_endpoint():
    kinds = {k if isinstance(k, str) else k[1] for k in balldontlie.ENDPOINT_TIERS}
    for league in balldontlie.LEAGUES:
        for kind in kinds:
            assert balldontlie.plan_reaches(league, kind, "all-access"), (league, kind)


def test_all_access_is_paced_and_sweeps():
    assert balldontlie.PER_MINUTE["all-access"] >= balldontlie.PER_MINUTE["goat"]
    assert bs.sweeps_on_plan("all-access")


def test_the_setting_is_accepted_and_a_typo_is_not(monkeypatch):
    monkeypatch.setenv("BALLDONTLIE_TIER", "ALL-ACCESS")
    assert config.Settings().balldontlie_tier == "all-access"
    monkeypatch.setenv("BALLDONTLIE_TIER", "allaccess")
    with pytest.raises(ValueError, match="all-access"):
        config.Settings()


def test_admin_and_the_workbook_name_the_plan(monkeypatch):
    import financials
    import provider_usage

    patched = dataclasses.replace(config.settings, balldontlie_tier="all-access")
    monkeypatch.setattr(config, "settings", patched)
    monkeypatch.setattr(provider_usage, "settings", patched, raising=False)
    rows = {r["provider"]: r for r in provider_usage.report(1_790_000_000.0)}
    assert rows["balldontlie"]["limit"].startswith("all-access plan: 600 requests/min")
    row = next(r for r in financials.catalogue() if r["vendor"] == "BALLDONTLIE")
    assert row["price"] == 299.99 and row["status"] == "Paying"
