"""The 9.29 packet (PROBLEMS.md §174): six changes from the owner.

1. "Find new friends" is a pill with a plus.
2. Your own YourFAM page shows no interests; a friend's still does.
3. Audio keeps playing smoothly with the app in the background.
4. "Start here" cards name what they are about before anybody taps.
5. "Pick up where you left off": nothing more than 60% heard, nothing older
   than a day.
6. No country pill on Trending; its View more is grouped by continent.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import config  # noqa: E402
import geography  # noqa: E402
import saved as saved_mod  # noqa: E402
import startup  # noqa: E402
import stories  # noqa: E402
import topics as T  # noqa: E402
import trending_bank as TB  # noqa: E402
from cache import MemoryScriptCache  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()
AUDIO = (ROOT / "static" / "fam-audio.js").read_text()


def _function(name: str) -> str:
    start = INDEX.index(f"function {name}(")
    end = INDEX.index("\n  }\n", start)
    return INDEX[start:end]


# --- 1 and 2: YourFAM -------------------------------------------------------

def test_find_new_friends_is_a_pill_with_a_plus():
    body = _function("renderProfile")
    assert 'class="yf-find" onclick="openFriends()">\' + yfIcon("plus"' in body
    assert "Find new friends" in body
    rule = re.search(r"\.yf-find\{[^}]*\}", INDEX).group(0)
    assert "border-radius" in rule and "background" in rule


def test_your_own_page_draws_no_interests_but_a_friends_does():
    own = _function("renderProfile")
    assert "yf-chip" not in own and "interests_shown" not in own
    # A friend's profile keeps its chips.
    assert "interestChipsHTML(personInterests" in INDEX


# --- 3: the background -------------------------------------------------------

def test_a_hidden_page_queues_far_ahead_in_longer_slices():
    assert re.search(r"var LOOKAHEAD_HIDDEN = (\d+)", AUDIO)
    hidden = float(re.search(r"var LOOKAHEAD_HIDDEN = ([\d.]+)", AUDIO).group(1))
    visible = float(re.search(r"var LOOKAHEAD = ([\d.]+)", AUDIO).group(1))
    # Well past the slowest a background timer is allowed to run.
    assert hidden >= 10 and hidden > 20 * visible
    tick = AUDIO[AUDIO.index("function tick()"):AUDIO.index("function rescheduleFrom")]
    assert "lookahead()" in tick and "LOOKAHEAD" not in tick.replace("lookahead()", "")
    # Every block reads the slice through the function, never the constant.
    assert "SLICE *" not in AUDIO
    # Topped up on the event, not only on the throttled timer.
    assert "if (hidden()) tick();" in AUDIO
    assert 'addEventListener("visibilitychange", function () { tick(); })' in AUDIO


# --- 4: "Start here" written ahead ------------------------------------------

class FakeGenerator:
    client = None

    def __init__(self, outcome_dependent=()):
        self.outcome_dependent = set(outcome_dependent)
        self.written = []

    async def understand(self, plan, notes):
        from types import SimpleNamespace

        brief = SimpleNamespace(
            outcome_dependent=any(w in plan.query for w in self.outcome_dependent),
            degraded=False)
        return dataclasses.replace(plan, brief=brief)

    async def stream_sentences(self, plan, notes):
        self.written.append(plan.query)
        notes.title = f"Named: {plan.query[:24]}"
        notes.summary = "What it turned out to be."
        for i in range(3):
            yield f"Sentence {i}."


def et(*args) -> float:
    from zoneinfo import ZoneInfo

    return datetime(*args, tzinfo=ZoneInfo("America/New_York")).timestamp()


@pytest.fixture
def bank(tmp_path, monkeypatch):
    new = dataclasses.replace(TB.settings, startup_write_ahead=True)
    for module in (config, TB):
        monkeypatch.setattr(module, "settings", new)
    store = TB.BankStore(str(tmp_path / "trending_bank.db"))
    TB.reset(store)
    yield store
    TB.reset()


def test_every_start_here_question_is_written_under_the_key_a_tap_computes(bank):
    from pipeline import key_for
    from script_generator import plan_episode

    cache, generator = MemoryScriptCache(), FakeGenerator()
    now = time.time()
    results = asyncio.run(TB.write_startup(now, generator, cache))
    assert set(results) == {s[0] for s in startup.STARTUP_TOPICS}
    for spec in startup.STARTUP_TOPICS:
        key = asyncio.run(key_for(plan_episode(spec[3], config.BROWSE_MINUTES)))
        assert results[spec[0]]["key"] == key and cache.get(key)
        assert cache.title(key).startswith("Named:")
        assert cache.origin_of(key) == "startup"
        # Current until the next slot has had its chance to rewrite it.
        assert cache._data[key][0] >= TB.next_slot(now).timestamp() - 5


def test_the_questions_are_written_once_per_slot_and_again_at_the_next(bank):
    cache, generator = MemoryScriptCache(), FakeGenerator()
    at_five = et(2026, 9, 29, 5, 0, 5)
    assert TB.startup_due(at_five)
    asyncio.run(TB.write_startup(at_five, generator, cache))
    assert not TB.startup_due(at_five + 60)
    assert asyncio.run(TB.write_startup(at_five + 60, generator, cache)) is None
    first = len(generator.written)
    assert first == len(startup.STARTUP_TOPICS)
    # This week's question is asked again at the next slot, not kept.
    at_five_pm = et(2026, 9, 29, 17, 0, 5)
    assert TB.startup_due(at_five_pm)
    asyncio.run(TB.write_startup(at_five_pm, generator, cache))
    assert len(generator.written) == 2 * first


def test_a_result_is_still_never_written_ahead(bank):
    cache = MemoryScriptCache()
    generator = FakeGenerator(outcome_dependent={"sport"})
    results = asyncio.run(TB.write_startup(time.time(), generator, cache))
    assert results["su-sports"]["status"] == "volatile"
    assert results["su-tech"]["status"] == "written"


def test_off_or_without_a_writer_nothing_is_written(bank, monkeypatch):
    cache = MemoryScriptCache()
    assert asyncio.run(TB.write_startup(time.time(), None, cache)) is None
    new = dataclasses.replace(TB.settings, startup_write_ahead=False)
    monkeypatch.setattr(TB, "settings", new)
    assert not TB.startup_due()
    assert asyncio.run(TB.write_startup(time.time(), FakeGenerator(), cache)) is None


def test_the_startup_slot_is_never_read_as_an_edition(bank):
    asyncio.run(TB.write_startup(time.time(), FakeGenerator(), MemoryScriptCache()))
    assert bank.latest() is None and TB.current() is None


def test_a_written_start_here_card_takes_the_episodes_name(monkeypatch):
    cache, generator = MemoryScriptCache(), FakeGenerator()
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", cache)
    spec = next(s for s in startup.STARTUP_TOPICS if s[0] == "su-business")
    from pipeline import key_for
    from script_generator import plan_episode

    plan = plan_episode(spec[3], config.BROWSE_MINUTES)
    key = asyncio.run(key_for(plan))
    cache.put(key, ["One."], 3600, spec[3], "", config.BROWSE_MINUTES, "", "", "",
              "Nvidia's $5 Billion Bet on Intel", summary="Why a rival bought in.")
    tiles = [{"id": spec[0], "title": spec[1], "query": spec[3], "cached": True}]
    appmod._name_written_tiles(tiles, config.BROWSE_MINUTES)
    assert tiles[0]["title"] == "Nvidia's $5 Billion Bet on Intel"


# --- 5: Pick up where you left off --------------------------------------------

def test_more_than_sixty_percent_heard_is_not_offered_back(tmp_path):
    store = saved_mod.SavedStore(str(tmp_path / "saved.db"))
    # Two minutes asked for: 72s is 60%, 73s is past it.
    assert store.note_progress("u", "at sixty", 2, 72)
    assert not store.note_progress("u", "past sixty", 2, 73)
    assert [r["query"] for r in store.progress("u")] == ["at sixty"]


def test_the_real_length_decides_when_the_player_knows_it(tmp_path):
    store = saved_mod.SavedStore(str(tmp_path / "saved.db"))
    # Three minutes asked for, but the episode ran 100s: 70s is 70% of it.
    assert not store.note_progress("u", "ran short", 3, 70, duration=100)
    assert store.note_progress("u", "ran short", 3, 50, duration=100)
    row = store.progress("u")[0]
    assert row["duration"] == 100.0
    # A later write that does not know the length keeps the known one.
    assert store.note_progress("u", "ran short", 3, 55)
    assert store.progress("u")[0]["duration"] == 100.0


def test_a_position_kept_before_the_rule_is_not_offered(tmp_path):
    store = saved_mod.SavedStore(str(tmp_path / "saved.db"))
    store._conn().execute(
        "INSERT INTO progress (user_id, query, minutes, seconds, title, updated,"
        " context, duration) VALUES ('u', 'old row', 2, 100, '', ?, '', 0)",
        (time.time(),))
    assert store.progress("u") == []


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    appmod._read_hits.clear()
    with TestClient(appmod.app) as c:
        c.get("/api/auth/me")
        yield c


def signed_in(client, email):
    r = client.post("/api/auth/signup", json={"email": email, "password": "password12"})
    assert r.status_code == 200, r.text
    return client.get("/api/auth/me").json()["user_id"]


def test_pick_up_holds_a_tile_for_a_day_at_most(client):
    user = signed_in(client, "aday@fam.test")
    yesterday = time.time() - 25 * 3600
    appmod.SAVED.note_progress(user, "yesterday's half listen", 3, 60, at=yesterday)
    appmod.EVENTS.record(T.Event(user, "complete", "sleep-science", "", ("health",),
                                 at=yesterday, thread="yesterday's follow-up"))
    client.post("/api/progress", json={"query": "this morning's", "minutes": 3,
                                       "seconds": 60, "duration": 170})
    appmod.EVENTS.record(T.Event(user, "complete", "ai-agents", "", ("tech",),
                                 thread="today's follow-up"))
    body = client.get("/api/godeeper").json()
    assert [r["query"] for r in body["resume"]] == ["this morning's"]
    assert body["resume"][0]["duration"] == 170.0
    assert [t["thread"] for t in body["threads"]] == ["today's follow-up"]


def test_the_player_sends_the_real_length_and_the_card_draws_against_it():
    note = _function("noteResume")
    assert "duration: duration" in note and "FamAudio.isComplete()" in note
    assert "(r.duration || r.minutes * 60)" in INDEX


# --- 6: Trending by continent -------------------------------------------------

@pytest.mark.parametrize("country, continent", [
    ("canada", "north-america"), ("mexico", "north-america"),
    ("jamaica", "north-america"), ("brazil", "south-america"),
    ("egypt", "africa"), ("turkey", "asia"), ("japan", "asia"),
    ("india", "asia"), ("russia", "europe"), ("australia", "oceania"),
    ("atlantis", ""),
])
def test_every_country_is_on_a_continent(country, continent):
    assert geography.continent_of_country(country) == continent


def test_every_known_country_is_on_one_of_six_continents_and_never_antarctica():
    assert "antarctica" not in geography.CONTINENTS
    assert len(geography.CONTINENTS) == 6
    for country in geography.REGION_OF:
        assert geography.continent_of_country(country) in geography.CONTINENTS, country


def test_a_story_belongs_to_the_continent_whose_press_owns_it():
    assert geography.continent_for("country", "canada") == "north-america"
    assert geography.continent_for("region", "latin-america") == "south-america"
    assert geography.continent_for("world", "world") == geography.WORLD
    # Europe and the Middle East's press together: neither region owns it,
    # but most of it is in Asia.
    assert geography.continent_for(
        "world", "world", (("israel", 0.4), ("iran", 0.3), ("germany", 0.3))) == "asia"
    assert geography.continent_for(
        "world", "world", (("united states", 0.4), ("france", 0.3),
                           ("japan", 0.3))) == geography.WORLD


def _story(subject, coverage, countries):
    return stories.Story(
        subject=subject, title=subject.title(), angle="an angle",
        query=f"what is happening with {subject}", tags=("world",),
        source="test", first_seen=time.time(), last_seen=time.time(),
        coverage=coverage, countries=countries)


def test_view_more_on_trending_is_grouped_by_continent():
    tiles = T.topics_from_stories([
        TB._placed(_story("the big one", 40, (("united states", 0.4),
                                              ("united kingdom", 0.3),
                                              ("japan", 0.3)))),
        TB._placed(_story("the delhi heatwave", 9, (("india", 1.0),))),
        TB._placed(_story("the rail strike", 4, (("france", 1.0),))),
        TB._placed(_story("the ottawa vote", 3, (("canada", 1.0),))),
        TB._placed(_story("the andes quake", 2, (("chile", 1.0),))),
    ])
    groups = T.trending_groups(tiles, "GB")
    assert [g["label"] for g in groups][:2] == ["Worldwide", "Europe"]
    assert groups[1]["yours"]
    assert {g["label"] for g in groups} == {
        "Worldwide", "Europe", "Asia", "North America", "South America"}
    by = {g["label"]: [t.title for t in g["topics"]] for g in groups}
    assert by["North America"] == ["The Ottawa Vote"]
    assert by["South America"] == ["The Andes Quake"]
