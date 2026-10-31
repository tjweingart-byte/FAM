"""§359: the whole script written ahead for both browse surfaces.

* The mixes' edition skips a **dormant** mix (not played, made or edited in
  48 hours); a subject an active mix also follows is written all the same,
  and opening a dormant mix writes it.
* The **rails edition** writes every story the pool holds at 05:00 and 17:00
  under the tap's key; a sports story it wrote stays on the shelf until the
  next one.
* No ceiling on either by default; a game in progress is current for
  `EDITION_IN_PROGRESS_SECONDS`.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import daily_edition  # noqa: E402
import mixes as M  # noqa: E402
import rails_edition  # noqa: E402
import stories as S  # noqa: E402
from cache import MemoryScriptCache  # noqa: E402
from pipeline import key_for  # noqa: E402
from script_generator import plan_episode  # noqa: E402

HOUR = 3600.0


class Writer:
    """A generator that briefs and writes, recording each query."""

    client = None

    def __init__(self, live=()):
        self.written = []
        self.live = set(live)

    async def understand(self, plan, notes):
        return dataclasses.replace(plan, brief=SimpleNamespace(
            degraded=False, outcome_dependent=False, recency_days=1))

    async def stream_sentences(self, plan, notes):
        self.written.append(plan.query)
        notes.sourced_at = time.time()
        if any(w in plan.query for w in self.live):
            notes.live_status = "in_progress"
        notes.title = "Written title"
        yield "One."


@pytest.fixture
def on(tmp_path, monkeypatch):
    settings = dataclasses.replace(config.settings, daily_edition=True,
                                   rails_edition=True)
    monkeypatch.setattr(config, "settings", settings)
    daily_edition.reset(daily_edition.EditionStore(str(tmp_path / "ed.db")))
    rails_edition.reset()
    S.reset()
    yield M.MixStore(str(tmp_path / "m.db"))
    daily_edition.reset()
    rails_edition.reset()
    S.reset()


def _age(store, mix_id, hours):
    """Make a mix look made, edited and last played `hours` ago."""
    then = time.time() - hours * HOUR
    store._conn().execute(
        "UPDATE mixes SET created_at = ?, updated_at = ?, played_at = 0 "
        "WHERE id = ?", (then, then, mix_id))


# --------------------------------------------------------------------------
# The ceilings are lifted
# --------------------------------------------------------------------------
def test_no_ceiling_on_either_edition_by_default():
    s = config.Settings()
    assert s.daily_edition_max_episodes == 0
    assert s.daily_edition_max_dollars == 0.0
    assert s.rails_edition_max_episodes == 0
    assert s.rails_edition_max_dollars == 0.0
    assert s.daily_edition_active_hours == 48.0
    assert s.edition_in_progress_seconds == 1800.0
    assert s.rails_edition_hours == "5,17"


# --------------------------------------------------------------------------
# Dormant mixes
# --------------------------------------------------------------------------
def test_a_dormant_mix_is_not_written_but_a_shared_subject_is(on):
    active = on.create("a", "Morning", ["f:nfl~Eagles"])
    dormant = on.create("b", "Old", ["f:nfl~Eagles", "fed-next-move"])
    _age(on, dormant.id, 72)
    queries = [s["query"] for s in daily_edition.subjects(on)]
    eagles = daily_edition.prompt_for(M.followed_item("f:nfl~Eagles"))
    assert queries == [eagles], (
        "the subject the active mix follows is written; the dormant mix's "
        "other subject is not")
    assert active.is_active(time.time(), 48)
    assert daily_edition.dormant_count(on) == 1
    every = daily_edition.subjects(on, include_dormant=True)
    assert len(every) == 2 and every[0]["mixes"] == 2, (
        "a follower count still counts the dormant mix")


def test_a_play_keeps_a_mix_active_and_an_old_one_lapses(on):
    mix = on.create("a", "Morning", ["fed-next-move"])
    _age(on, mix.id, 72)
    assert not daily_edition.is_active(on.get("a", mix.id))
    assert on.note_played("a", mix.id)
    assert daily_edition.is_active(on.get("a", mix.id))
    assert not on.note_played("someone-else", mix.id), "only the owner's play"


def test_a_new_mix_is_active_before_anybody_could_play_it(on):
    mix = on.create("a", "Morning", ["fed-next-move"])
    assert daily_edition.is_active(mix)


def test_active_hours_zero_writes_every_mix(on, monkeypatch):
    mix = on.create("a", "Morning", ["fed-next-move"])
    _age(on, mix.id, 500)
    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, daily_edition_active_hours=0))
    assert len(daily_edition.subjects(on)) == 1


def test_the_edition_reports_how_many_mixes_it_left_dormant(on):
    on.create("a", "Morning", ["f:nfl~Eagles"])
    old = on.create("b", "Old", ["fed-next-move"])
    _age(on, old.id, 72)
    report = asyncio.run(daily_edition.build(on, Writer(), MemoryScriptCache()))
    assert report["dormant_mixes"] == 1
    assert report["subjects"] == 1


def test_opening_a_dormant_mix_writes_all_of_it(on):
    mix = on.create("a", "Old", ["fed-next-move", "f:nfl~Eagles"])
    _age(on, mix.id, 72)
    writer, cache = Writer(), MemoryScriptCache()

    async def run():
        assert daily_edition.schedule_open(on.get("a", mix.id), writer, cache)
        await asyncio.gather(*list(daily_edition._TASKS))

    asyncio.run(run())
    assert len(writer.written) == 2


def test_the_open_endpoint_writes_only_a_dormant_mix(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app as app_mod

    calls = []
    monkeypatch.setattr(daily_edition, "schedule_open",
                        lambda mix, **kw: calls.append(mix.id) or True)
    with TestClient(app_mod.app) as client:
        r = client.post("/api/auth/signup", json={
            "email": "open359@example.com", "password": "correct horse 359",
            "terms": True})
        assert r.status_code in (200, 201), r.text
        made = client.post("/api/mixes", json={
            "name": "Old", "topic_ids": ["fed-next-move"]}).json()
        fresh = client.post(f"/api/mixes/{made['id']}/open", json={}).json()
        assert fresh == {"ok": True, "dormant": False, "writing": False}
        _age(app_mod.MIXES, made["id"], 72)
        old = client.post(f"/api/mixes/{made['id']}/open", json={}).json()
        assert old["dormant"] and old["writing"] and calls == [made["id"]]
        client.post(f"/api/mixes/{made['id']}/open", json={"played": True})
        again = client.post(f"/api/mixes/{made['id']}/open", json={}).json()
        assert again["dormant"] is False, "a play makes it active again"
        assert client.post("/api/mixes/nope/open", json={}).status_code == 404


# --------------------------------------------------------------------------
# The rails edition
# --------------------------------------------------------------------------
def _story(sid, domain, query, age_hours=1.0, **kw):
    now = time.time()
    return S.Story(subject=sid, title=f"{sid} title", angle="an angle",
                   query=query, domain=domain,
                   first_seen=now - age_hours * HOUR, last_seen=now,
                   shelf_life=S.DOMAIN_SHELF_LIFE[domain], **kw)


def _pool(*items):
    S._POOL = S.Pool(stories=list(items), fetched_at=time.time())


def test_the_rails_edition_runs_at_five_and_five():
    assert rails_edition.hours() == [5, 17]
    assert rails_edition.slot_id(rails_edition.last_slot()).startswith("rails@")


def test_every_held_story_is_written_under_the_taps_key(on):
    _pool(_story("eagles", S.SPORTS, "how did the eagles do last night"),
          _story("nvda", S.MARKETS, "why did nvidia stock move today"))
    writer, cache = Writer(), MemoryScriptCache()
    report = asyncio.run(rails_edition.build(writer, cache))
    assert report["written"] == 2
    for query in ("how did the eagles do last night",
                  "why did nvidia stock move today"):
        key = asyncio.run(key_for(plan_episode(query, config.BROWSE_MINUTES)))
        assert cache.get(key), "a tap at BROWSE_MINUTES is a cache hit"
    assert not rails_edition.due()
    assert asyncio.run(rails_edition.build(writer, cache)) is None


def test_it_writes_what_the_variety_cap_hides(on):
    many = [_story(f"s{i}", S.SPORTS, f"how did team {i} play", age_hours=0.5)
            for i in range(S.MAX_PER_FACET + 3)]
    _pool(*many)
    assert len(S.pool().live()) < len(many)
    assert len(rails_edition.subjects()) == len(many)


def test_a_written_sports_story_is_held_until_the_next_edition(on):
    _pool(_story("eagles", S.SPORTS, "how did the eagles do last night"),
          _story("nvda", S.MARKETS, "why did nvidia stock move today"))
    asyncio.run(rails_edition.build(Writer(), MemoryScriptCache()))
    until = rails_edition.current_until(time.time())
    eagles = [s for s in S.pool().stories if s.subject == "eagles"][0]
    nvda = [s for s in S.pool().stories if s.subject == "nvda"][0]
    assert eagles.first_seen + eagles.shelf_life == pytest.approx(until, abs=5)
    assert nvda.shelf_life == S.DOMAIN_SHELF_LIFE[S.MARKETS], "sports only"


def test_hold_until_only_lengthens():
    S.reset()
    story = _story("eagles", S.SPORTS, "q")
    _pool(story)
    assert not S.hold_until(story.id, story.first_seen + HOUR)
    assert S.hold_until(story.id, story.first_seen + 20 * HOUR)
    assert S.pool().stories[0].shelf_life == 20 * HOUR
    assert not S.hold_until("missing", time.time() + 99 * HOUR)
    S.reset()


def test_a_game_in_progress_keeps_its_title_and_a_short_window(on):
    _pool(_story("eagles", S.SPORTS, "how are the eagles doing right now"))
    cache = MemoryScriptCache()
    report = asyncio.run(rails_edition.build(Writer(live=("eagles",)), cache))
    assert report["volatile"] == 1
    key = asyncio.run(key_for(plan_episode("how are the eagles doing right now",
                                           config.BROWSE_MINUTES)))
    sourced, until = cache._clocks[key]
    assert until - sourced == pytest.approx(1800, abs=1)
    assert cache.title(key) == "Written title"
    # Past its window, a tap is handed nothing and writes a new episode.
    cache._clocks[key] = (sourced, time.time() - 1)
    assert cache.get(key) is None
    assert cache.get(key, current=False)


def test_off_writes_nothing(on, monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, rails_edition=False))
    _pool(_story("eagles", S.SPORTS, "q"))
    assert asyncio.run(rails_edition.build(Writer(), MemoryScriptCache())) is None
    assert not rails_edition.due()


def test_health_reports_the_rails_edition():
    import app as app_mod

    report = app_mod._rails_edition_report()
    assert report["schedule"]["hours"] == [5, 17]
    assert "ceilings" in report


# --------------------------------------------------------------------------
# What the review of §359 found
# --------------------------------------------------------------------------
def test_a_live_game_is_never_stretched_to_the_editions_window(on):
    """A second edition writer in the same slot (an opened mix, a forced
    re-run, the other edition) finds the in-progress episode current and
    must leave its thirty minutes alone."""
    on.create("a", "Morning", ["f:nfl~Eagles"])
    cache = MemoryScriptCache()
    asyncio.run(daily_edition.build(on, Writer(live=("Eagles",)), cache))
    [key] = list(cache._clocks)
    sourced, until = cache._clocks[key]
    query = daily_edition.prompt_for(M.followed_item("f:nfl~Eagles"))
    result = asyncio.run(daily_edition.write_episode(
        query, Writer(), cache, daily_edition.minutes(),
        daily_edition._current_until(time.time()),
        since=daily_edition.last_slot().timestamp()))
    assert result["status"] == "cached"
    assert cache._clocks[key][1] == pytest.approx(until), "not extended"


def test_a_mix_with_a_listen_time_is_never_dormant(on):
    mix = on.create("a", "Morning", ["fed-next-move"])
    on.update("a", mix.id, listen_at="07:30", listen_tz="America/New_York")
    _age(on, mix.id, 72)
    assert daily_edition.is_active(on.get("a", mix.id)), (
        "its owner is told every day it is ready, so it is written every day")


def test_a_hold_survives_a_refresh_that_was_composing(on):
    story = _story("eagles", S.SPORTS, "q")
    _pool(story)
    before = list(S.pool().stories)        # what a refresh copied
    assert S.hold_until(story.id, story.first_seen + 20 * HOUR)
    again = S._apply_holds(before, time.time())
    assert again[0].shelf_life == 20 * HOUR


def test_a_held_subject_is_not_retired_on_the_domains_clock():
    S.reset()
    story = _story("eagles", S.SPORTS, "q", age_hours=10)
    _pool(story)
    S._FIRST_SEEN[story.id] = story.first_seen
    assert S.hold_until(story.id, time.time() + 5 * HOUR)
    assert S._admissible(story.id, S.SPORTS, time.time())
    assert story.id not in S._RETIRED
    S.reset()


def test_a_hold_does_not_make_a_story_louder():
    story = _story("eagles", S.SPORTS, "q", age_hours=4)
    held = dataclasses.replace(story, shelf_life=20 * HOUR)
    assert held.push() == pytest.approx(story.push())


def test_building_is_not_reported_by_a_caller_that_lost_the_claim(on):
    on.create("a", "Morning", ["fed-next-move"])
    asyncio.run(daily_edition.build(on, Writer(), MemoryScriptCache()))
    seen = []
    real = daily_edition.run_slot

    async def spy(*a, **kw):
        seen.append(daily_edition._BUILDING)
        return await real(*a, **kw)

    daily_edition.run_slot = spy
    try:
        assert asyncio.run(daily_edition.build(
            on, Writer(), MemoryScriptCache())) is None
    finally:
        daily_edition.run_slot = real
    assert seen == [False] and daily_edition._BUILDING is False
