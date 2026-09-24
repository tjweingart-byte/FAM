"""§143: every episode is kept a week and stamped with when it was sourced,
and DailyFAM is an edition written in the background before anybody taps.

Two halves, pinned separately:

* **Kept is not current.** A row lives `CACHE_LIFE_SECONDS` (a week) whatever
  it is about; `ttl_for` decides only how long a *new request* may be served
  it. Replay surfaces play anything kept, with its sourced time; a request
  that would write an episode is never handed one past its current window.
* **The edition.** One episode per distinct subject across every mix, under
  the key a tap computes, with episode intelligence run on every one - and the
  interface is served the exact words and length the edition wrote.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cache as cache_mod  # noqa: E402
import config  # noqa: E402
import daily_edition  # noqa: E402
import mixes as M  # noqa: E402
from cache import MemoryScriptCache, SqliteScriptCache  # noqa: E402
from pipeline import GenerationStats, PodcastPipeline, key_for  # noqa: E402
from script_generator import plan_episode  # noqa: E402
from tts import DebugEngine  # noqa: E402

WEEK = 7 * 86400


# --------------------------------------------------------------------------
# The cache: kept a week, current for its window, stamped with its sourcing
# --------------------------------------------------------------------------
def test_the_defaults_keep_every_episode_a_week():
    assert config.settings.cache_life_seconds == WEEK
    assert config.settings.cache_ttl_seconds == WEEK, (
        "an evergreen episode is current for as long as it is kept")


@pytest.mark.parametrize("make", [
    lambda tmp: SqliteScriptCache(str(tmp / "c.db")),
    lambda tmp: MemoryScriptCache(),
])
def test_a_volatile_episode_is_kept_a_week_but_not_served_as_current(tmp_path, make):
    store = make(tmp_path)
    sourced = time.time() - 3600          # an hour ago
    store.put("k", ["One."], 900, "latest on the fed", "", 2, "b",
              sourced_at=sourced)
    # Past its fifteen-minute window: a new request is not handed it...
    assert store.get("k") is None
    assert store.written_at("k") is None, "a tap would write a new one"
    # ...and a replay still plays it, with the time it was sourced.
    assert store.get("k", current=False) == ["One."]
    assert store.sourced_at("k") == pytest.approx(sourced)
    [row] = store.recent()
    assert row["current"] is False
    assert row["sourced_at"] == pytest.approx(sourced)


def test_a_row_goes_after_a_week_from_when_it_was_sourced(tmp_path):
    store = SqliteScriptCache(str(tmp_path / "c.db"))
    store.put("old", ["One."], 900, "q", "", 2, sourced_at=time.time() - WEEK - 60)
    store.put("new", ["Two."], 900, "r", "", 2, sourced_at=time.time() - 60)
    assert store.get("old", current=False) is None
    assert store.get("new", current=False) == ["Two."]
    assert store.purge_expired() == 1


def test_a_score_mid_game_is_kept_and_never_current(tmp_path):
    """`ttl_for` returns 0 for `in_progress`. That used to mean "do not write
    it"; it now means no new request is ever handed it."""
    assert cache_mod.ttl_for("chiefs game", live_status="in_progress") == 0
    store = SqliteScriptCache(str(tmp_path / "c.db"))
    store.put("k", ["They lead."], 0, "chiefs game", "", 2)
    assert store.get("k") is None
    assert store.get("k", current=False) == ["They lead."]


def test_a_near_match_is_never_a_stale_episode(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "settings", dataclasses.replace(
        cache_mod.settings, cache_vector=True))
    store = SqliteScriptCache(str(tmp_path / "c.db"))
    store.put("k", ["One."], 0, "what is the latest on the fed", "", 2, "bucket")
    assert store.nearest("bucket", "whats the latest on the fed") is None


def test_a_life_of_zero_restores_the_old_cache(kept_only_while_current, tmp_path):
    store = SqliteScriptCache(str(tmp_path / "c.db"))
    store.put("k", ["One."], 0, "q", "", 2)
    assert store.get("k", current=False) is None


class Writer:
    """A generator that counts writes and says when it sourced."""

    client = None

    def __init__(self):
        self.calls = 0

    async def stream_sentences(self, plan, notes=None):
        self.calls += 1
        if notes is not None:
            notes.recency_days = 1          # "the last 24 hours"
            notes.sourced_at = time.time() - 30
        yield f"Written {self.calls}."

    async def top_up(self, plan, spoken_so_far, words_needed, notes=None):
        return
        yield ""  # pragma: no cover


def _drain(pipeline, plan):
    async def run():
        async for _ in pipeline.stream_pcm(plan, GenerationStats()):
            pass
    asyncio.run(run())


def test_the_pipeline_stamps_what_it_writes_and_writes_again_once_stale():
    store = MemoryScriptCache()
    gen = Writer()
    pipe = PodcastPipeline(generator=gen, engine=DebugEngine(), cache=store)
    plan = plan_episode("what happened with the fed today", 1)
    _drain(pipe, plan)
    key = asyncio.run(key_for(plan))
    stamped = store.sourced_at(key)
    assert stamped and stamped < time.time() - 20, "the notes' sourced time"

    # Age it past its current window: a replay plays it, a request rewrites.
    sourced, _until = store._clocks[key]
    store._clocks[key] = (sourced, time.time() - 1)
    _drain(pipe, plan_episode("what happened with the fed today", 1,
                              cached_only=True))
    assert gen.calls == 1, "a replay plays what is kept"
    _drain(pipe, plan)
    assert gen.calls == 2, "a new request is never handed a stale episode"
    assert store.get(key) == ["Written 2."]


# --------------------------------------------------------------------------
# The edition's clock and day
# --------------------------------------------------------------------------
def _at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=daily_edition.zone()).timestamp()


def test_the_edition_day_is_the_slots_not_the_servers():
    # 3am Eastern on the 24th is still the 23rd's edition; 6am is the 24th's.
    assert daily_edition.edition_day(_at(2026, 9, 24, 3)).isoformat() == "2026-09-23"
    assert daily_edition.edition_day(_at(2026, 9, 24, 6)).isoformat() == "2026-09-24"
    # 11pm Eastern is 03:00 UTC the next day - the UTC date prefetch used.
    assert daily_edition.edition_day(_at(2026, 9, 24, 23)).isoformat() == "2026-09-24"


def test_a_mix_item_serves_the_words_and_length_the_edition_writes():
    item = M.followed_item("f:nfl~Eagles")
    served = item.as_dict()
    assert served["prompt"] == daily_edition.prompt_for(item)
    assert "{date}" not in served["prompt"]
    assert served["minutes"] == daily_edition.minutes() == 3


def test_subjects_are_shared_across_mixes_and_most_followed_first(tmp_path):
    store = M.MixStore(str(tmp_path / "m.db"))
    store.create("a", "Morning", ["f:nfl~Eagles", "fed-next-move"])
    store.create("b", "Gym", ["f:nfl~Eagles"])
    got = daily_edition.subjects(store)
    assert got[0]["query"] == daily_edition.prompt_for(M.followed_item("f:nfl~Eagles"))
    assert got[0]["mixes"] == 2
    assert len(got) == 2, "one episode per subject, not per mix"


# --------------------------------------------------------------------------
# Building an edition
# --------------------------------------------------------------------------
class EditionWriter:
    """Records the order EI and writing happen in, per query."""

    client = None

    def __init__(self, degraded=False, live=()):
        self.log = []
        self.degraded = degraded
        self.live = set(live)

    async def understand(self, plan, notes):
        self.log.append(("understand", plan.query))
        return dataclasses.replace(plan, brief=SimpleNamespace(
            degraded=self.degraded, outcome_dependent=True, recency_days=1))

    async def stream_sentences(self, plan, notes):
        assert plan.brief is not None, "written without a brief"
        self.log.append(("write", plan.query))
        notes.recency_days = 1
        notes.outcome_dependent = True
        notes.sourced_at = time.time()
        if any(w in plan.query for w in self.live):
            notes.live_status = "in_progress"
        notes.title = "A title"
        yield "One."
        yield "Two."


@pytest.fixture
def edition(tmp_path, monkeypatch):
    on = dataclasses.replace(config.settings, daily_edition=True)
    monkeypatch.setattr(config, "settings", on)
    daily_edition.reset(daily_edition.EditionStore(str(tmp_path / "ed.db")))
    yield M.MixStore(str(tmp_path / "m.db"))
    daily_edition.reset()


def test_an_edition_writes_every_subject_with_ei_under_the_taps_key(edition):
    edition.create("a", "Morning", ["f:nfl~Eagles", "f:stocks~Nvidia"])
    edition.create("b", "Gym", ["f:nfl~Eagles", "fed-next-move"])
    store, gen = MemoryScriptCache(), EditionWriter()
    report = asyncio.run(daily_edition.build(edition, gen, store))

    wanted = [s["query"] for s in daily_edition.subjects(edition)]
    assert report["subjects"] == len(wanted) == 3
    assert report["written"] == 3 and report["ei_ok"] == 3
    for query in wanted:
        # EI before the writing, for every episode.
        assert gen.log.index(("understand", query)) < gen.log.index(("write", query))
        # Under the key a tap on the served prompt, at the served length,
        # computes - and current, although "the last 24 hours" alone would
        # have given it fifteen minutes.
        key = asyncio.run(key_for(plan_episode(query, daily_edition.minutes())))
        assert store.get(key) == ["One.", "Two."]
        assert store.sourced_at(key)


def test_an_edition_says_when_ei_fell_back(edition):
    edition.create("a", "Morning", ["f:nfl~Eagles"])
    report = asyncio.run(daily_edition.build(
        edition, EditionWriter(degraded=True), MemoryScriptCache()))
    assert report["ei_degraded"] == 1 and report["ei_ok"] == 0


def test_an_edition_episode_stays_current_until_the_next_edition(edition, monkeypatch):
    edition.create("a", "Morning", ["f:nfl~Eagles"])
    store = MemoryScriptCache()
    asyncio.run(daily_edition.build(edition, EditionWriter(), store))
    [key] = list(store._clocks)
    _sourced, until = store._clocks[key]
    assert until >= daily_edition.next_slot().timestamp()


def test_a_game_in_progress_is_kept_but_left_for_the_tap(edition):
    edition.create("a", "Morning", ["f:nfl~Eagles"])
    store = MemoryScriptCache()
    report = asyncio.run(daily_edition.build(
        edition, EditionWriter(live=("Eagles",)), store))
    assert report["volatile"] == 1
    [key] = list(store._clocks)
    assert store.get(key) is None and store.get(key, current=False)


def test_one_builder_per_slot(edition):
    edition.create("a", "Morning", ["f:nfl~Eagles"])
    assert asyncio.run(daily_edition.build(edition, EditionWriter(),
                                           MemoryScriptCache()))
    assert asyncio.run(daily_edition.build(edition, EditionWriter(),
                                           MemoryScriptCache())) is None
    assert not daily_edition.due()


def test_the_ceiling_is_a_ceiling(edition, monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, daily_edition_max_episodes=1))
    edition.create("a", "Morning", ["f:nfl~Eagles", "f:stocks~Nvidia", "fed-next-move"])
    report = asyncio.run(daily_edition.build(edition, EditionWriter(),
                                             MemoryScriptCache()))
    assert report["written"] == 1 and report["skipped_for_ceiling"] == 2


class SlowWriter(EditionWriter):
    """Suspends mid-write, as a real one does on the network."""

    async def stream_sentences(self, plan, notes):
        await asyncio.sleep(0.01)
        async for sentence in super().stream_sentences(plan, notes):
            yield sentence


def test_the_ceiling_holds_with_several_writing_at_once(edition, monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, daily_edition_max_episodes=1))
    edition.create("a", "Morning", ["f:nfl~Eagles", "f:stocks~Nvidia", "fed-next-move"])
    report = asyncio.run(daily_edition.build(edition, SlowWriter(),
                                             MemoryScriptCache()))
    assert report["written"] == 1 and report["skipped_for_ceiling"] == 2


def test_no_writer_is_reported_not_papered_over(edition):
    edition.create("a", "Morning", ["f:nfl~Eagles"])
    report = asyncio.run(daily_edition.build(edition, None, None))
    assert "written on the tap" in report["detail"]
    # Nothing claimed: once a key arrives, today's edition is still due.
    assert daily_edition.due()


def test_a_mix_saved_between_editions_is_written_in_the_background(edition):
    mix = edition.create("a", "Morning", ["f:nfl~Eagles"])
    store, gen = MemoryScriptCache(), EditionWriter()

    async def run():
        assert daily_edition.schedule_mix(mix, gen, store)
        await asyncio.gather(*list(daily_edition._TASKS))

    asyncio.run(run())
    query = daily_edition.prompt_for(M.followed_item("f:nfl~Eagles"))
    assert ("understand", query) in gen.log and ("write", query) in gen.log
    key = asyncio.run(key_for(plan_episode(query, daily_edition.minutes())))
    assert store.get(key)


def test_the_health_page_reports_the_edition(edition):
    report = daily_edition.report()
    assert report["enabled"] and report["minutes"] == 3
    assert report["schedule"]["hours"] == [5]


# --------------------------------------------------------------------------
# The second pass (PROBLEMS.md §143, "checked twice")
# --------------------------------------------------------------------------
def test_a_tap_that_beat_the_edition_is_given_its_window_not_rewritten(edition):
    edition.create("a", "Morning", ["f:nfl~Eagles"])
    query = daily_edition.prompt_for(M.followed_item("f:nfl~Eagles"))
    key = asyncio.run(key_for(plan_episode(query, daily_edition.minutes())))
    store = MemoryScriptCache()
    # The tap path's own write: fifteen minutes current, sourced just now.
    store.put(key, ["Tapped."], 900, query, "", daily_edition.minutes())
    gen = EditionWriter()
    report = asyncio.run(daily_edition.build(edition, gen, store))
    assert report["cached"] == 1 and gen.log == [], "paid for twice"
    _sourced, until = store._clocks[key]
    assert until >= daily_edition.next_slot().timestamp()


def test_extending_never_makes_a_stale_entry_current(tmp_path):
    for store in (MemoryScriptCache(), SqliteScriptCache(str(tmp_path / "c.db"))):
        store.put("k", ["Mid-game."], 0, "chiefs game", "", 2)
        assert store.extend_current("k", time.time() + 3600) is False
        assert store.get("k") is None
        store.put("f", ["Fresh."], 900, "fed", "", 2)
        assert store.extend_current("f", time.time() + 3600) is True
        store.put("g", ["Gone."], 900, "q", "", 2,
                  sourced_at=time.time() - 3600)
        assert store.extend_current("g", time.time() + 3600) is False


def test_the_player_is_not_told_a_superseded_episodes_name():
    store = MemoryScriptCache()
    pipe = PodcastPipeline(generator=Writer(), engine=DebugEngine(), cache=store)
    plan = plan_episode("what happened with the fed today", 1)
    key = asyncio.run(key_for(plan))
    store.put(key, ["Old."], 900, plan.query, "old thread", 1, title="Old name",
              sourced_at=time.time() - 3600)
    live = asyncio.run(pipe.episode_meta(plan, current_only=True))
    assert live["title"] != "Old name" and live["thread"] == ""
    assert live["sourced_at"] == 0.0
    # A replay, and a card naming the episode, still read the kept row.
    kept = asyncio.run(pipe.episode_meta(plan))
    assert kept["title"] == "Old name" and kept["title_final"]


def test_a_save_writes_only_what_the_mix_gained_and_only_once(edition):
    before = edition.create("a", "Morning", ["f:nfl~Eagles"])
    after = edition.update("a", before.id, topic_ids=["f:nfl~Eagles",
                                                      "f:stocks~Nvidia"])
    store, gen = MemoryScriptCache(), EditionWriter()

    async def run(mix, prior):
        started = daily_edition.schedule_mix(mix, gen, store, before=prior)
        await asyncio.gather(*list(daily_edition._TASKS))
        return started

    assert asyncio.run(run(after, before))
    written = {q for kind, q in gen.log if kind == "write"}
    assert written == {daily_edition.prompt_for(M.followed_item("f:stocks~Nvidia"))}
    # Reordering, removing, or saving the same again spends nothing.
    assert not asyncio.run(run(after, after))
    assert not asyncio.run(run(after, before)), "tried once per edition"


def test_a_life_of_zero_writes_nothing_never_current(kept_only_while_current):
    class Live(Writer):
        async def stream_sentences(self, plan, notes=None):
            if notes is not None:
                notes.live_status = "in_progress"
            yield "Under way."

    store, gen = MemoryScriptCache(), Live()
    plan = plan_episode("chiefs game", 1)
    key = asyncio.run(key_for(plan))
    _drain(PodcastPipeline(generator=gen, engine=DebugEngine(), cache=store),
           plan)
    assert key not in store._data, "the old cache never wrote it"
    assert store.plays(key) == 0
