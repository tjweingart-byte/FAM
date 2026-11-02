"""Phase 2, piece 2: whose material Consuela may store (§397).

Decided items 8, 25 and 41; consuela.md, never 8: Consuela keeps a
provider's material only once the owner has approved it - GDELT, Tiingo,
BALLDONTLIE and (2026-11-02) Perplexity - and never Parallel's, Finnhub's
or API-Sports'. With her off, the archive keeps what it kept before her.
The sweeps' scores and prices reach her series only through this switch.
"""
from __future__ import annotations

import dataclasses
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import history_archive as ha  # noqa: E402

NOW = datetime(2026, 11, 2, 20, tzinfo=timezone.utc)


@pytest.fixture
def archive(monkeypatch, tmp_path):
    ha.reset()
    changed = dataclasses.replace(ha.settings, archive=True, archive_backend="sqlite")
    monkeypatch.setattr(ha, "settings", changed)
    monkeypatch.setattr(ha, "_embed", lambda text: ([1.0] + [0.0] * (ha.DIMS - 1),
                                                    f"onnx:{ha.DIMS}"))
    monkeypatch.setattr(ha.EXECUTOR, "submit", lambda fn, *a: fn(*a))
    store = ha.SQLiteBackend(str(tmp_path / "archive.db"))
    ha.use(store)
    yield store
    ha.reset()


@pytest.fixture
def consuela(monkeypatch):
    monkeypatch.setattr(ha, "consuela_runs", lambda: True)


def _series(subject, metric="score"):
    return ha.readings(subject, metric, NOW - timedelta(days=2), NOW + timedelta(days=2))


# --- the approval list -------------------------------------------------------

def test_the_owner_s_approvals_are_the_default():
    assert set(ha.settings.housekeeper_approved_providers) == {
        "fam", "gdelt", "tiingo", "balldontlie", "perplexity"}
    for name in ("GDELT", "Tiingo", "BALLDONTLIE scores", "BALLDONTLIE", "perplexity",
                 "fam"):
        assert ha.approved(name), name


@pytest.mark.parametrize("name", ["parallel", "Finnhub", "API-Sports", "Polymarket",
                                  "gnews", "", "fake scoreboard (NOT REAL DATA)"])
def test_everyone_else_is_not_approved(name):
    assert not ha.approved(name)


def test_parallel_finnhub_and_api_sports_cannot_be_approved_by_a_setting(monkeypatch):
    listed = dataclasses.replace(ha.settings, housekeeper_approved_providers=(
        "parallel", "finnhub", "api-sports", "tiingo"))
    monkeypatch.setattr(ha, "settings", listed)
    assert [p for p in ("parallel", "finnhub", "api-sports") if ha.approved(p)] == []
    assert ha.approved("Tiingo")
    assert ha.report()["approved_providers"] == ["tiingo"]


def test_consuela_does_not_run_with_the_agents_off():
    assert ha.consuela_runs() is False


def test_the_env_example_matches_the_default():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, ".env.example")).read()
    assert ("HOUSEKEEPER_APPROVED_PROVIDERS="
            + ",".join(ha.settings.housekeeper_approved_providers)) in text


# --- intake --------------------------------------------------------------------

def _finding(url, provider):
    return ha.Finding(url=url, title="T", host="x.com", grade="primary",
                      published="2026-10-20", passages=("One.",), provider=provider)


FOUND = [_finding("https://a.com/1", "perplexity"), _finding("https://a.com/2", "gdelt"),
         _finding("https://a.com/3", ""), _finding("https://a.com/4", "parallel")]


def test_with_consuela_off_the_intake_keeps_what_it_kept_before_her(archive):
    assert [f.provider for f in ha._kept(FOUND)] == ["perplexity", "gdelt", ""]


def test_with_consuela_on_only_approved_providers_reach_the_intake(archive, consuela):
    assert [f.provider for f in ha._kept(FOUND)] == ["perplexity", "gdelt"]
    assert ha.STATS["not_approved"] == 1 and ha.STATS["never_kept"] == 1


# --- readings ------------------------------------------------------------------

def _reading(source, subject="Eagles vs Giants", minute=0):
    return ha.Reading(subject=subject, metric="score", as_of=NOW + timedelta(minutes=minute),
                      text="Eagles 7, Giants 0.", source=source, domain="sports")


def test_with_consuela_off_no_reading_is_kept(archive):
    assert ha.keep_readings([_reading("BALLDONTLIE")]) == 0
    assert _series("Eagles vs Giants") == []


def test_with_consuela_on_only_approved_readings_are_kept(archive, consuela):
    kept = ha.keep_readings([_reading("BALLDONTLIE"), _reading("API-Sports", "Arsenal vs Spurs"),
                             _reading("Finnhub", "Apple")])
    assert kept == 1
    assert len(_series("Eagles vs Giants")) == 1
    assert _series("Arsenal vs Spurs") == [] and _series("Apple") == []
    assert ha.STATS["readings_not_approved"] == 2 and ha.STATS["readings_kept"] == 1


def test_a_record_becomes_a_reading_only_with_a_moment_a_line_and_a_kept_domain():
    good = {"domain": "markets", "subject": "Apple", "as_of": NOW.isoformat(),
            "lines": ["Apple last traded at 200.00."], "source": "Tiingo", "value": 200}
    reading = ha.reading_from(good)
    assert (reading.metric, reading.value, reading.source) == ("price", 200.0, "Tiingo")
    for bad in ({"as_of": ""}, {"as_of": "2026-11-02T20:00:00"}, {"lines": []},
                {"domain": "weather"}, {"subject": "", "entity": ""}):
        assert ha.reading_from({**good, **bad}) is None, bad


def test_an_episode_s_live_records_become_readings_while_consuela_runs(archive, consuela):
    record = {"domain": "sports", "kind": "final", "status": "final",
              "source": "BALLDONTLIE scores", "entity": "Eagles vs Giants",
              "as_of": NOW.isoformat(), "lines": ["Eagles 24, Giants 17."]}
    notes = SimpleNamespace(archive={"findings": [], "subject": "Eagles",
                                     "live": [record]}, category="nfl",
                            category_settled={"node": "eagles"})
    ha.after_episode(notes, "k", 1.0)
    series = _series("Eagles vs Giants")
    assert [(r.text, r.status, r.node_id) for r in series] == [
        ("Eagles 24, Giants 17.", "final", "eagles")]


# --- the sweeps ------------------------------------------------------------------

def test_the_sports_sweep_keeps_every_score_and_one_final(archive, consuela, monkeypatch):
    import balldontlie_scores as bs
    import live_facts

    league = SimpleNamespace(key="nfl", label="NFL")
    state = {"status": live_facts.IN_PROGRESS, "line": "Eagles 7, Giants 0."}
    monkeypatch.setattr(bs, "status_of", lambda row: state["status"])
    monkeypatch.setattr(bs, "game_facts", lambda lg, row, st, cat: [state["line"]])
    monkeypatch.setattr(bs, "sides", lambda row, cat: ("Eagles", "Giants"))
    monkeypatch.setattr(bs, "team_said", lambda team: team)
    rows = [{"id": 42}]
    bs._keep_readings(league, rows, {}, "BALLDONTLIE", NOW)
    state["line"] = "Eagles 14, Giants 3."
    bs._keep_readings(league, rows, {}, "BALLDONTLIE", NOW + timedelta(minutes=10))
    state.update(status=live_facts.FINAL, line="Eagles 24, Giants 17.")
    bs._keep_readings(league, rows, {}, "BALLDONTLIE", NOW + timedelta(minutes=60))
    bs._keep_readings(league, rows, {}, "BALLDONTLIE", NOW + timedelta(minutes=70))
    series = _series("Eagles vs Giants")
    assert [r.text for r in series] == ["NFL: Eagles 7, Giants 0.",
                                        "NFL: Eagles 14, Giants 3.",
                                        "NFL: Eagles 24, Giants 17."]
    assert series[-1].status == live_facts.FINAL
    # A league API-Sports answered for is never kept.
    monkeypatch.setattr(bs, "sides", lambda row, cat: ("Arsenal", "Spurs"))
    bs._keep_readings(league, rows, {}, "API-Sports", NOW)
    assert _series("Arsenal vs Spurs") == []


def test_the_markets_sweep_keeps_tiingo_s_prices_and_never_finnhub_s(archive, consuela):
    import story_sources

    stamp = NOW.timestamp()
    story_sources._keep_prices([("AAPL", {"c": 201.5, "dp": 1.2, "t": stamp})], True, "Tiingo")
    story_sources._keep_prices([("MSFT", {"c": 400.0, "dp": 0.1, "t": stamp})], True, "Finnhub")
    apple = _series("Apple", "price")
    assert [(r.value, r.unit, r.source) for r in apple] == [(201.5, "USD", "Tiingo")]
    assert _series("Microsoft", "price") == []
