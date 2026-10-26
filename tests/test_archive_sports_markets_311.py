"""Sports and market data in the history archive (§311, the owner's direction).

Scores and prices are the archive's most valuable material, so:

* the live providers' data an episode used is kept as archive facts, in
  code - a market state at its time, a game only once it is final, a
  player's report - and never a prediction market or the weather;
* the recall reaches sports, markets and outcome-dependent questions;
* an archived figure never passes for today's: `FigureGuard` lets it vouch
  only for a sentence that says when it was true.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import figure_guard  # noqa: E402
import history_archive as ha  # noqa: E402
import live_facts  # noqa: E402
import research  # noqa: E402
from tests.test_history_archive_308 import archive, _stub_embed  # noqa: E402,F401

AS_OF = datetime(2026, 10, 3, 20, tzinfo=timezone.utc)


def _lookup(domain, status=live_facts.FINAL, kind="", lines=("Eagles 24, Giants 17.",),
            outcome=live_facts.FACTS):
    facts = SimpleNamespace(domain=domain, source="BALLDONTLIE", as_of=AS_OF,
                            facts=list(lines), status=status, kind=kind,
                            entity=SimpleNamespace(label="Philadelphia Eagles", id="1"))
    return SimpleNamespace(outcome=outcome, facts=facts)


# -- what is kept -------------------------------------------------------------
def test_a_final_game_a_market_state_and_a_player_report_are_kept():
    records = ha.live_records(
        _lookup("sports"),
        _lookup("markets", status=live_facts.UNKNOWN, lines=("NVDA closed at 182.43.",)),
        _lookup("sports", status=live_facts.IN_PROGRESS, kind=live_facts.PLAYER_REPORT,
                lines=("Jalen Hurts: 24 touchdowns this season.",)))
    assert [r["domain"] for r in records] == ["sports", "markets", "sports"]
    assert records[0]["as_of"] == AS_OF.isoformat()
    assert records[1]["lines"] == ["NVDA closed at 182.43."]


@pytest.mark.parametrize("lookup", [
    _lookup("sports", status=live_facts.IN_PROGRESS),     # a moment, not history
    _lookup("sports", status=live_facts.SCHEDULED),
    _lookup("markets", kind=live_facts.PREDICTION_MARKET),  # never evidence
    _lookup("weather"),                                    # a forecast
    _lookup("sports", outcome="provider_failed"),          # nothing established
    None,
])
def test_what_is_not_history_is_not_kept(lookup):
    assert ha.live_records(lookup) == []


def test_live_data_is_written_as_dated_primary_facts(archive, monkeypatch):
    monkeypatch.setattr(ha.EXECUTOR, "submit", lambda fn, *a: fn(*a))
    notes = SimpleNamespace(category="nfl", archive={
        "subject": "Eagles", "findings": [], "live": ha.live_records(_lookup("sports"))})
    ha.after_episode(notes, "key", 100.0, "tap")
    assert archive.counts()["facts"] == 1 and archive.counts()["intake"] == 0
    found = ha.recall("eagles", window_days=1,
                      now=datetime(2026, 10, 25, tzinfo=timezone.utc))
    assert found and found[0].text == "Eagles 24, Giants 17."
    assert found[0].published == "2026-10-03" and found[0].grade == "primary"
    # The same snapshot kept twice is one fact.
    ha.after_episode(notes, "key", 101.0, "tap")
    assert archive.counts()["facts"] == 1


def test_a_private_search_keeps_no_live_data(archive, monkeypatch):
    monkeypatch.setattr(ha.EXECUTOR, "submit",
                        lambda *a: pytest.fail("a private search was queued"))
    notes = SimpleNamespace(category="", archive={
        "subject": "x", "findings": [], "live": ha.live_records(_lookup("sports"))})
    ha.after_episode(notes, "key", 1.0, ha.PRIVATE_ORIGIN)


# -- an archived figure never passes for today's ------------------------------
def _packet():
    archived = ha.as_sources([ha.Fact(
        url="live:Tiingo:NVDA:2026-10-03", text="NVIDIA closed at 150.10.",
        title="NVDA market data 2026-10-03", grade="primary",
        published="2026-10-03")], 2, now=datetime(2026, 10, 25, tzinfo=timezone.utc))
    fresh = ("SOURCE 1\nTitle: Today\nPublished: 2026-10-25 (today)\n"
             "Source type: wire service\nKey evidence:\nNVIDIA trades at 182.43.\n\n")
    return fresh + archived


def _guard():
    plan = SimpleNamespace(evidence=_packet(), live=None, forecast=None,
                           readable=[], brief=SimpleNamespace(
                               live_domain="markets", live_route=""))
    return figure_guard.FigureGuard(plan)


def test_the_packet_splits_into_the_search_and_the_archive():
    fresh, archived = figure_guard.split_archive(_packet())
    assert "182.43" in fresh and "150.10" not in fresh
    assert "150.10" in archived and research.TIER_LABELS["archive"] in archived


def test_an_archived_price_vouches_only_for_a_dated_sentence():
    guard = _guard()
    assert guard.why_not("NVIDIA trades at $182.43.") == ""
    assert guard.why_not("On October 3 it closed at $150.10.") == ""
    assert guard.why_not("Three weeks ago it closed at $150.10.") == ""
    assert "not in the material" in guard.why_not("NVIDIA trades at $150.10.")
    assert "not in the material" in guard.why_not(
        "On October 3 it closed at $150.10, and it is still there now.")


# -- the recall reaches them ---------------------------------------------------
@pytest.mark.parametrize("brief", [
    SimpleNamespace(live_domain="sports"), SimpleNamespace(live_domain="markets"),
    SimpleNamespace(outcome_dependent=True)])
def test_the_recall_reaches_sports_markets_and_results(archive, brief):
    assert ha.recall_wanted(brief) == ""


def test_the_writer_is_told_an_archived_result_is_not_the_one_asked_for():
    import episode_intelligence as ei

    block = ei.build_brief_block(ei.Brief(query="who won", subject="the Eagles",
                                          outcome_dependent=True), 1)
    assert "Earlier reporting from FAM's archive is never that result" in block
