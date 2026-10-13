"""§259: a sports score is never spoken unless the scoreboard said it.

The night this was written, API-Sports had suspended the account, the live
lookup failed, and an episode about a finished Bills-Rams game said it was in
the first quarter - lifted from a live blog in the packet. These pin the four
answers: the writer is told, the sentence is removed if it is written anyway,
the feed's own status is sanity-checked, and a refused account is loud.
"""

from __future__ import annotations

import asyncio
import dataclasses
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import live_facts  # noqa: E402
import live_sources as LS  # noqa: E402
import provider_usage  # noqa: E402
import score_guard  # noqa: E402
import script_generator as sg  # noqa: E402

NFL = LS.SPORTS["american-football"]


def settings(monkeypatch, **values):
    patched = dataclasses.replace(config.settings, **values)
    monkeypatch.setattr(LS, "settings", patched)
    monkeypatch.setattr(config, "settings", patched)
    return patched


def failed() -> live_facts.LiveLookup:
    return live_facts.LiveLookup(
        "sports", live_facts.PROVIDER_FAILED,
        detail="API-Sports failed: AccountRefused: suspended")


def scoreboard(status: str, *sentences: str) -> live_facts.LiveLookup:
    return live_facts.LiveLookup(
        "sports", live_facts.FACTS, facts=live_facts.LiveFacts(
            domain="sports", source="API-Sports",
            as_of=datetime.now(timezone.utc), facts=list(sentences),
            status=status))


FINAL = scoreboard(live_facts.FINAL,
                   "Buffalo Bills beat Los Angeles Rams 24 to 17.",
                   "The Buffalo Bills have won 4 and lost 1 in the regular "
                   "season so far.")
LIVE_Q2 = scoreboard(live_facts.IN_PROGRESS,
                     "Buffalo Bills lead Los Angeles Rams 14 to 7.",
                     "They are in Second Quarter.")


def guard(live) -> score_guard.ScoreGuard:
    return score_guard.ScoreGuard(NS(brief=NS(live_domain="sports"), live=live))


# --- the guard -------------------------------------------------------------

@pytest.mark.parametrize("live", [failed(), None], ids=["failed", "no-lookup"])
@pytest.mark.parametrize("sentence", [
    # The sentence that was spoken that night, and its relatives.
    "The Bills and Rams are in the first quarter at SoFi.",
    "It's still the first quarter, and Josh Allen has already run for a score.",
    "Buffalo leads 7-0 early.",
    "The Bills beat the Rams twenty-four to seventeen tonight.",
    "The Rams won tonight.",
    "The game is under way at SoFi Stadium.",
])
def test_with_no_scoreboard_no_score_period_or_result_is_spoken(live, sentence):
    assert guard(live).why_not(sentence)


@pytest.mark.parametrize("sentence", [
    "The Rams beat the Bills 31 to 10 in 2022.",
    "The Bills have won four straight coming into this one.",
    "Allen threw for 280 yards and two touchdowns.",
    "The Bills run a 4-3 defense.",
    "They gained five to ten yards a carry.",
    "Kick-off was set for 8:15 on October 12-13 weekend coverage.",
])
def test_history_yardage_and_formations_are_not_scores(sentence):
    assert guard(failed()).why_not(sentence) == ""


@pytest.mark.parametrize("sentence, kept", [
    ("The Bills beat the Rams twenty-four to seventeen.", True),
    ("Buffalo won 24-17.", True),
    ("Buffalo is 4-1 on the season.", True),          # the record it counted
    ("The Bills won 27-17.", False),                   # a wrong number
    ("The Rams won tonight, 24-17.", False),           # the wrong way round
    ("The Bills lost 24-17.", False),
    ("It's the first quarter at SoFi.", False),        # a finished game
    ("The game is under way.", False),
    ("The Bills scored twice in the fourth quarter.", True),  # a recap
])
def test_a_final_score_must_be_the_scoreboards(sentence, kept):
    assert (guard(FINAL).why_not(sentence) == "") is kept


@pytest.mark.parametrize("sentence, kept", [
    ("Buffalo leads 14-7 in the second quarter.", True),
    ("They are in the second quarter.", True),
    ("It's still the first quarter.", False),          # the wrong period
    ("They are in the fourth quarter.", False),
    ("The Bills scored in the fourth quarter.", False),  # not reached yet
    ("The Rams are leading at SoFi.", False),          # the wrong side
    ("Buffalo leads 21-7.", False),
    ("The Bills won tonight.", False),                 # no result yet
    ("The game is under way.", True),
])
def test_a_game_under_way_is_said_as_the_scoreboard_has_it(sentence, kept):
    assert (guard(LIVE_Q2).why_not(sentence) == "") is kept


def test_the_guard_is_off_outside_sports():
    off = score_guard.ScoreGuard(NS(brief=NS(live_domain="markets"), live=None))
    assert off.why_not("The vote was 52 to 48.") == ""
    assert score_guard.ScoreGuard(NS(brief=None, live=None)).active is False


def test_scores_are_read_as_words_and_digits():
    assert score_guard.scores_in("a twenty-four to seventeen win") == [(24, 17)]
    assert score_guard.scores_in("won 31-10") == [(31, 10)]
    assert score_guard.scores_in("seven to nothing") == [(7, 0)]
    assert score_guard.scores_in("the 2025-26 season") == []
    assert score_guard.scores_in("one to watch") == []


# --- in the writer's reader --------------------------------------------------

def sports_plan(live):
    plan = sg.plan_episode("bills rams score", 2, search=True)
    return dataclasses.replace(plan, brief=NS(live_domain="sports"), live=live)


def test_the_reader_drops_the_sentence_and_keeps_the_story():
    notes = sg.ScriptNotes()
    reader = sg._ScriptReader(sports_plan(failed()), notes)
    said = reader.feed("Buffalo came to Los Angeles with the AFC's best record. "
                       "It's still the first quarter, and the Bills lead 7-0. "
                       "Josh Allen is the reason they are favoured. ")
    said += reader.finish()
    assert said == ["Buffalo came to Los Angeles with the AFC's best record.",
                    "Josh Allen is the reason they are favoured."]
    assert len(notes.score_drops) == 1 and "first quarter" in notes.score_drops[0]


def test_an_episode_of_nothing_but_unvouched_scores_says_one_fixed_line():
    reader = sg._ScriptReader(sports_plan(failed()), sg.ScriptNotes())
    said = reader.feed("The Bills lead 7-0 in the first quarter. ") + reader.finish()
    assert len(said) == 1
    assert said[0].startswith("We can't give you a score")


# --- the writer is told --------------------------------------------------------

def test_a_failed_feed_forbids_a_period_or_score_from_an_article():
    block = failed().as_prompt_block()
    assert "No current state from an article" in block
    assert "first quarter" in block and "removed before it is spoken" in block


def test_a_scoreboard_owns_the_period_too():
    block = LIVE_Q2.as_prompt_block()
    assert "quarter, half" in block and "removed before it is spoken" in block


# --- the feed is sanity-checked ------------------------------------------------

def nfl_row(short, kickoff, home=24, away=17):
    return {"game": {"id": 9, "status": {"short": short, "long": "First Quarter"},
                     "date": {"timestamp": int(kickoff.timestamp())}},
            "teams": {"home": {"id": 1, "name": "Buffalo Bills"},
                      "away": {"id": 2, "name": "Los Angeles Rams"}},
            "scores": {"home": {"total": home}, "away": {"total": away}}}


def entity():
    return live_facts.Entity(domain="sports", provider="API-Sports",
                             id="american-football:9", label="Bills v Rams")


def test_a_game_still_live_nine_hours_after_kickoff_is_not_believed():
    kick = datetime.now(timezone.utc) - timedelta(hours=9)
    facts = LS.ApiSportsSource().to_facts(nfl_row("Q1", kick), entity(), NFL)
    assert facts.status == live_facts.UNKNOWN
    assert not any("24" in f or "quarter" in f.lower() for f in facts.facts)
    assert LS.ApiSportsSource.live_line(nfl_row("Q1", kick), NFL) == (
        live_facts.UNKNOWN, "")


def test_a_game_live_an_hour_after_kickoff_is():
    kick = datetime.now(timezone.utc) - timedelta(hours=1)
    facts = LS.ApiSportsSource().to_facts(nfl_row("Q1", kick), entity(), NFL)
    assert facts.status == live_facts.IN_PROGRESS
    assert "Buffalo Bills lead Los Angeles Rams 24 to 17." in facts.facts


def test_not_started_four_hours_after_kickoff_is_not_believed():
    kick = datetime.now(timezone.utc) - timedelta(hours=5)
    facts = LS.ApiSportsSource().to_facts(nfl_row("NS", kick), entity(), NFL)
    assert facts.status == live_facts.UNKNOWN


def test_an_unknown_status_never_carries_a_score():
    kick = datetime.now(timezone.utc) - timedelta(hours=1)
    facts = LS.ApiSportsSource().to_facts(nfl_row("WEIRD", kick), entity(), NFL)
    assert facts.status == live_facts.UNKNOWN
    assert not any("24" in f for f in facts.facts)


# --- a game that just finished is found -----------------------------------------

def test_a_finished_game_is_found_by_date_not_live_all(monkeypatch):
    settings(monkeypatch, api_sports_key="k")
    asked = []
    kick = datetime.now(timezone.utc) - timedelta(hours=3)

    async def fake(url, headers, params, timeout):
        asked.append(dict(params))
        if "date" in params:
            return {"response": [nfl_row("FT", kick)]}
        return {"response": []}
    monkeypatch.setattr(LS, "_json", fake)

    async def no_team_path(*a, **k):
        return None
    monkeypatch.setattr(LS.ApiSportsSource, "_resolve_by_team", no_team_path)
    found = asyncio.run(LS.ApiSportsSource().resolve(
        NS(subject="Buffalo Bills vs Los Angeles Rams", query="", named_slot="")))
    assert found is not None and found.id == "american-football:9"
    assert not any(p.get("live") == "all" for p in asked)


def test_the_sports_source_has_time_for_two_requests():
    assert LS.ApiSportsSource.timeout_seconds >= 2 * config.settings.live_timeout_seconds


# --- a refused account is loud ---------------------------------------------------

SUSPENDED = {"access": "Your account is suspended, check on "
                       "https://dashboard.api-football.com."}


def test_a_suspended_account_is_held_reported_and_not_hammered(monkeypatch):
    settings(monkeypatch, api_sports_key="k")
    calls = []

    async def fake(url, headers, params, timeout):
        calls.append(url)
        return {"errors": SUSPENDED, "response": []}
    monkeypatch.setattr(LS, "_json", fake)
    url = f"{NFL.host}/games"
    with pytest.raises(LS.AccountRefused):
        asyncio.run(LS.api_sports_json(url, {}, 1))
    report = LS.account_report()
    assert report["refused"] and "suspended" in report["message"]
    assert LS.report()["api_sports_account"]["refused"]
    # Every sport shares the key: the next ask, any sport, is not sent.
    with pytest.raises(LS.AccountRefused):
        asyncio.run(LS.api_sports_json(f"{LS.SPORTS['hockey'].host}/games", {}, 1))
    assert len(calls) == 1


def test_the_account_comes_back_by_itself(monkeypatch):
    settings(monkeypatch, api_sports_key="k")

    async def fake(url, headers, params, timeout):
        return {"response": []}
    monkeypatch.setattr(LS, "_json", fake)
    LS.note_account_refusal(SUSPENDED, "american-football",
                            now=0.0)  # long ago: past the retry wait
    asyncio.run(LS.api_sports_json(f"{NFL.host}/games", {}, 1))
    assert LS.account_report() == {}


def test_a_plan_refusal_is_not_an_account_refusal(monkeypatch):
    settings(monkeypatch, api_sports_key="k")

    async def fake(url, headers, params, timeout):
        return {"errors": {"plan": "Free plans do not have access to this "
                                   "season, try from 2022 to 2024."},
                "response": []}
    monkeypatch.setattr(LS, "_json", fake)
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(LS.api_sports_json(f"{NFL.host}/games", {}, 1))
    assert not isinstance(caught.value, LS.AccountRefused)
    assert LS.account_report() == {}


def test_a_refused_account_is_not_remembered_as_a_refused_catalogue(monkeypatch):
    settings(monkeypatch, api_sports_key="k")

    async def fake(url, headers, params, timeout):
        return {"errors": SUSPENDED, "response": []}
    monkeypatch.setattr(LS, "_json", fake)
    with pytest.raises(LS.AccountRefused):
        asyncio.run(LS.league_teams(NFL))
    assert NFL.key not in LS.TEAMS_REFUSED


def test_admin_shows_the_refusal_in_red(monkeypatch):
    LS.note_account_refusal(SUSPENDED, "american-football")
    alert = provider_usage._api_sports_alert()
    assert "refused this account" in alert and "dashboard.api-football.com" in alert
    html = (pathlib.Path(__file__).resolve().parent.parent
            / "admin_ui" / "tracker.html").read_text()
    assert "r.alert" in html
