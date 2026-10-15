"""§259: a sports score is never spoken unless the scoreboard said it.

The night this was written, the sports provider had suspended the account,
the live lookup failed, and an episode about a finished Bills-Rams game said it was in
the first quarter - lifted from a live blog in the packet. These pin the four
answers: the writer is told, the sentence is removed if it is written anyway,
the feed's own status is sanity-checked, and a refused account is loud.

§276: the scoreboard is BALLDONTLIE's (`balldontlie_scores`); the checks on
the feed are pinned against its rows.
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

import balldontlie  # noqa: E402
import balldontlie_scores as bs  # noqa: E402
import config  # noqa: E402
import live_facts  # noqa: E402
import live_sources as LS  # noqa: E402
import score_guard  # noqa: E402
import script_generator as sg  # noqa: E402

NFL = balldontlie.LEAGUES["nfl"]


def settings(monkeypatch, **values):
    values.setdefault("balldontlie_api_key", "k")
    values.setdefault("balldontlie_tier", "goat")
    patched = dataclasses.replace(config.settings, **values)
    for module in (LS, config, balldontlie, bs):
        monkeypatch.setattr(module, "settings", patched)
    return patched


def failed() -> live_facts.LiveLookup:
    return live_facts.LiveLookup(
        "sports", live_facts.PROVIDER_FAILED,
        detail="BALLDONTLIE scores failed: AccountRefused: suspended")


def scoreboard(status: str, *sentences: str) -> live_facts.LiveLookup:
    return live_facts.LiveLookup(
        "sports", live_facts.FACTS, facts=live_facts.LiveFacts(
            domain="sports", source="BALLDONTLIE scores",
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


# Found on the branch's own review: claims that slipped past the first cut.
@pytest.mark.parametrize("which, sentence, kept", [
    ("none", "Buffalo leads 24-17 after beating Miami last week.", False),
    ("none", "The Rams lead at SoFi.", False),
    ("none", "The Rams are ahead by a touchdown.", False),
    ("none", "The game is tied.", False),
    ("none", "Allen threw twenty-four passes before the break.", True),
    ("none", "Allen has thirty-one touchdowns over two seasons.", True),
    ("none", "The Rams came in as two-to-one underdogs.", True),
    ("none", "Allen leads the league in rushing touchdowns.", True),
    ("final", "The Bills lead 24-17.", False),
    ("final", "Los Angeles won 24-17.", False),     # the city, not the nickname
    ("live", "The Bills are tied with the Rams.", False),
    ("live", "Los Angeles leads.", False),
    ("live", "The score is 14 to 7.", True),
])
def test_standing_claims_and_compound_numbers(which, sentence, kept):
    live = {"none": failed(), "final": FINAL, "live": LIVE_Q2}[which]
    assert (guard(live).why_not(sentence) == "") is kept


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

BILLS = {"id": 1, "full_name": "Buffalo Bills", "name": "Bills"}
RAMS = {"id": 2, "full_name": "Los Angeles Rams", "name": "Rams"}


def nfl_row(state, kickoff, home=24, away=17, gid=9, home_team=BILLS,
            away_team=RAMS):
    return {"id": gid, "status_state": state, "date": kickoff.isoformat(),
            "home_team": home_team, "visitor_team": away_team,
            "home_team_score": home, "visitor_team_score": away,
            "period_detail": "1st Quarter"}


def read(row):
    """What the scoreboard says of a row: its believed status and sentences."""
    status = bs.believable(bs.status_of(row), row, bs.SHAPES["nfl"])
    return status, bs.game_facts(NFL, row, status)


def test_a_game_still_live_nine_hours_after_kickoff_is_not_believed():
    kick = datetime.now(timezone.utc) - timedelta(hours=9)
    status, facts = read(nfl_row("in_progress", kick))
    assert status == live_facts.UNKNOWN
    assert not any("24" in f or "quarter" in f.lower() for f in facts)
    assert bs.live_line(NFL, nfl_row("in_progress", kick)) == (live_facts.UNKNOWN, "")


def test_a_game_live_an_hour_after_kickoff_is():
    kick = datetime.now(timezone.utc) - timedelta(hours=1)
    status, facts = read(nfl_row("in_progress", kick))
    assert status == live_facts.IN_PROGRESS
    assert "Buffalo Bills lead Los Angeles Rams 24 to 17." in facts


def test_not_started_four_hours_after_kickoff_is_not_believed():
    kick = datetime.now(timezone.utc) - timedelta(hours=5)
    assert read(nfl_row("scheduled", kick))[0] == live_facts.UNKNOWN


def test_an_unknown_status_never_carries_a_score():
    kick = datetime.now(timezone.utc) - timedelta(hours=1)
    status, facts = read(nfl_row("WEIRD", kick))
    assert status == live_facts.UNKNOWN
    assert not any("24" in f for f in facts)


# --- a game that just finished is found -----------------------------------------

class Wire:
    """BALLDONTLIE answering by path; every request recorded."""

    def __init__(self, routes):
        self.routes = routes
        self.asked = []

    async def __call__(self, url, headers, params, timeout):
        path = url.split("balldontlie.io", 1)[1]
        self.asked.append((path, dict(params)))
        answer = self.routes.get(path)
        if isinstance(answer, Exception):
            raise answer
        return answer if answer is not None else {"data": []}


def brief(text):
    return NS(subject=text, query=text, live_entity="", named_slot="",
              category="", live_domain="sports", live_route="")


def test_a_finished_game_is_found_by_date(monkeypatch):
    settings(monkeypatch)
    kick = datetime.now(timezone.utc) - timedelta(hours=3)
    wire = Wire({"/nfl/v1/teams": {"data": [BILLS, RAMS]},
                 "/nfl/v1/games": {"data": [nfl_row("final", kick)]}})
    monkeypatch.setattr(LS, "_json", wire)
    found = asyncio.run(bs.ScoreBoard().resolve(
        brief("Buffalo Bills vs Los Angeles Rams nfl")))
    assert found is not None and found.id == "nfl:game:9"
    games = [p for path, p in wire.asked if path == "/nfl/v1/games"]
    assert games and all("dates[]" in p for p in games)


def test_the_game_naming_both_sides_wins_over_one_sharing_a_city(monkeypatch):
    """"Los Angeles" is the Rams and the Chargers. The first row containing
    "angeles" used to win - somebody else's game, with a real score."""
    settings(monkeypatch)
    kick = datetime.now(timezone.utc) - timedelta(hours=3)
    chargers = {"id": 3, "full_name": "Los Angeles Chargers", "name": "Chargers"}
    broncos = {"id": 4, "full_name": "Denver Broncos", "name": "Broncos"}
    other = nfl_row("final", kick, gid=5, home_team=chargers, away_team=broncos)
    wire = Wire({"/nfl/v1/teams": {"data": [BILLS, RAMS, chargers, broncos]},
                 "/nfl/v1/games": {"data": [other, nfl_row("final", kick)]}})
    monkeypatch.setattr(LS, "_json", wire)
    found = asyncio.run(bs.ScoreBoard().resolve(
        brief("Buffalo Bills vs Los Angeles Rams nfl")))
    assert found.id == "nfl:game:9"


def test_the_sports_source_has_time_for_two_requests():
    assert bs.ScoreBoard.timeout_seconds >= 2 * config.settings.live_timeout_seconds


# --- a refused account is loud ---------------------------------------------------

def test_a_refused_key_is_a_failure_never_an_answer(monkeypatch):
    """A key BALLDONTLIE refuses outright is `AccountRefused`, raised - so the
    lookup is recorded as a failed provider (/admin's pipeline errors, §269),
    never read as "no such game"."""
    settings(monkeypatch)
    refused = LS.ProviderHTTPError(401, "api.balldontlie.io/nfl/v1/teams")
    monkeypatch.setattr(LS, "_json", Wire({"/nfl/v1/teams": refused}))
    with pytest.raises(balldontlie.AccountRefused):
        asyncio.run(bs.ScoreBoard().resolve(brief("Buffalo Bills nfl")))


def test_a_plan_refusal_is_not_an_account_refusal_and_is_not_hammered(monkeypatch):
    settings(monkeypatch)
    refused = LS.ProviderHTTPError(403, "api.balldontlie.io/nfl/v1/standings")
    wire = Wire({"/nfl/v1/standings": refused})
    monkeypatch.setattr(LS, "_json", wire)
    client = balldontlie.Client()
    with pytest.raises(balldontlie.TierRefused):
        asyncio.run(bs.league_standings(client, NFL))
    with pytest.raises(balldontlie.TierRefused):
        asyncio.run(bs.league_standings(client, NFL))
    assert len(wire.asked) == 1, "a refused plan is not asked again that day"
