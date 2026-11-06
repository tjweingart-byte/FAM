"""What an episode costs, against the owner's plan (§492, decided items 21, 27).

The owner (2026-11-06): (a) the agents per episode against the Cost table's
0.45¢, (b) all of Claude per episode against the pricing estimate's 1¢.
Pinned: the figures from the ledger and the day's spend over the episodes
written; the nightly jobs kept apart as dollars a month; too few episodes is
no figure; 1.5x the plan is an urgent incident (so emailed); nothing is ever
held or capped for it.
"""
from __future__ import annotations

import dataclasses
import time

import pytest

import cost_watch as CW
import incidents as INC
import metering
import pipeline_errors as PE
import provider_usage

NOW = time.time()


class Quality:
    def __init__(self, n):
        self.n = n

    def episodes_since(self, at):
        return [{"key": str(i)} for i in range(self.n)]


@pytest.fixture
def world(tmp_path, monkeypatch):
    import episode_quality

    PE.reset(PE.ErrorStore(str(tmp_path / "e.db")))
    INC.reset(INC.IncidentStore(str(tmp_path / "e.db")))
    provider_usage.reset(provider_usage.UsageStore(str(tmp_path / "u.db")))
    ledger = metering.MeterStore(str(tmp_path / "m.db"))
    CW.LEDGER[0] = ledger
    episodes = {"n": 100}
    monkeypatch.setattr(episode_quality, "exists", lambda: True)
    monkeypatch.setattr(episode_quality, "store", lambda: Quality(episodes["n"]))
    yield ledger, episodes
    CW.LEDGER[0] = None
    INC.reset()


def agent_call(ledger, usd, surface="search", agent="editor"):
    ledger.record_agent_call(agent=agent, task="t", surface=surface, status="ok",
                             vendor="anthropic", model="claude-haiku-5-5",
                             served_model="claude-haiku-5-5", usage={}, cost_usd=usd,
                             priced=True, sent=True, seconds=1.0, reason="")


def claude(usd):
    provider_usage.spend("episodes", "claude-haiku-5-5", usd=usd)


def test_both_figures_per_episode_with_the_nightly_jobs_apart(world):
    ledger, _ = world
    agent_call(ledger, 0.30)                                # 100 episodes: 0.3¢ each
    agent_call(ledger, 0.10, surface="ahead")
    agent_call(ledger, 7.00, surface="background", agent="supervisor")
    claude(7.50)                                            # the nightly 7.00 included
    r = CW.report(NOW)
    assert r["episodes"] == 100
    assert r["agents"]["cents_per_episode"] == pytest.approx(0.4)
    assert r["agents"]["plan_cents"] == 0.45 and r["agents"]["well_above"] is False
    assert r["claude"]["cents_per_episode"] == pytest.approx(0.5)
    assert r["claude"]["plan_cents"] == 1.0
    # Over whole UTC days (§495), as Claude's spend is kept.
    assert r["since"] % 86400 == 0 and NOW - r["since"] >= 7 * 86400
    span = (NOW - r["since"]) / 86400
    assert r["nightly_usd_per_month"] == pytest.approx(7.00 / span * 30, abs=0.01)


def test_too_few_episodes_is_no_figure(world):
    ledger, episodes = world
    episodes["n"] = 10
    agent_call(ledger, 5.0)
    r = CW.report(NOW)
    assert r["agents"]["cents_per_episode"] is None and r["agents"]["well_above"] is False
    assert CW.findings(NOW) == []


def test_well_above_the_plan_is_an_urgent_incident_and_emailed(world, monkeypatch):
    ledger, _ = world
    agent_call(ledger, 0.80)                                # 0.8¢: 1.8x of 0.45
    claude(1.20)                                            # 1.2¢: 1.2x of 1.0
    INC.watch(NOW)
    [row] = INC.report(NOW)["open"]
    assert row["cause"] == "cost:agents" and row["severity"] == "urgent"
    assert "1.8x the plan's 0.45¢" in row["title"]
    assert "never capped" in row["plan"]["listener_notices"]


def test_a_plan_figure_and_the_threshold_are_settings(world, monkeypatch):
    import config

    ledger, _ = world
    claude(1.20)
    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, it_cost_plan_claude_cents=0.5, it_cost_well_above=2.0))
    r = CW.report(NOW)
    assert r["claude"]["ratio"] == pytest.approx(2.4) and r["claude"]["well_above"]
    assert r["well_above_at"] == 2.0


def test_cost_never_holds_or_slows_anything(world, monkeypatch):
    """Decided item 21: no cap decides whether an agent runs."""
    from agents import netenyahu

    held = []
    monkeypatch.setattr(netenyahu, "hold", lambda *a: held.append(a))
    ledger, _ = world
    agent_call(ledger, 5.0)
    INC.watch(NOW)
    import asyncio
    import keith_fixes

    asyncio.run(keith_fixes.act(NOW))
    assert held == []
    assert all(f["fix"] != "hold_agent" for f in INC.store().fixes_since(0))
