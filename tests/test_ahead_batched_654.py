"""Claude per episode, by day; the writer written ahead batched, at medium
(§654, the owner, 2026-11-18).

"The per episode costs of claude have been going up significantly" - so:

* **A breakdown that says why.** `cost_watch.by_day` gives each UTC day's
  episodes (by the surface that wrote them) and Claude's dollars by spend
  source, the nightly agents taken out as the 7-day figure takes them out.
* **Medium where nobody waits** (`AHEAD_WRITER_EFFORT`, high since §522).
* **Every writer written ahead is batched**: the editions were (§179); the
  "Start here" write-ahead and prefetch's script warms are now, by the same
  rules - one batch, the same reader, a writer the batch does not answer is
  written live from what was already prepared, a tap's episode is never
  written over.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time

import pytest

import claude_batch
import config
import cost_watch as CW
import metering
import prefetch
import provider_usage
import script_generator as sg
import trending_bank
from cache import MemoryScriptCache
from script_generator import ScriptNotes, plan_episode
from test_edition_batch_179 import BatchWriter, _no_sleep

DAY = 86400.0


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------- 1. by day
class Quality:
    def __init__(self, rows):
        self.rows = rows

    def episodes_since(self, at):
        return [r for r in self.rows if r["at"] >= at]


@pytest.fixture
def ledgers(tmp_path, monkeypatch):
    import episode_quality

    provider_usage.reset(provider_usage.UsageStore(str(tmp_path / "u.db")))
    ledger = metering.MeterStore(str(tmp_path / "m.db"))
    CW.LEDGER[0] = ledger
    rows: list = []
    monkeypatch.setattr(episode_quality, "exists", lambda: True)
    monkeypatch.setattr(episode_quality, "store", lambda: Quality(rows))
    yield ledger, rows
    CW.LEDGER[0] = None
    provider_usage.reset()


def _spend_on(day_at, source, usd, model="claude-haiku-5-5"):
    provider_usage.spend(source, model, usd=usd)
    provider_usage.flush()
    store = provider_usage.store()
    with store._lock, contextlib.closing(store._connect()) as db:
        # Move today's row to the day under test.
        db.execute("UPDATE spend SET day = ? WHERE day = ? AND source = ? AND model = ?",
                   (time.strftime("%Y-%m-%d", time.gmtime(day_at)),
                    time.strftime("%Y-%m-%d", time.gmtime(time.time())), source, model))
        db.commit()


def test_each_day_says_its_episodes_its_sources_and_its_cents(ledgers):
    ledger, rows = ledgers
    now = time.time()
    yesterday = now - DAY
    rows += [{"at": yesterday, "origin": "search"}] * 4 + \
            [{"at": yesterday, "origin": "dailyfam"}] * 6
    _spend_on(yesterday, "episodes", 0.06)
    _spend_on(yesterday, "edition:dailyfam", 0.10)
    _spend_on(yesterday, "gemini-ish", 5.0, model="google:imagen")   # not Claude
    b = CW.by_day(now)
    day = next(d for d in b["days"]
               if d["day"] == time.strftime("%Y-%m-%d", time.gmtime(yesterday)))
    assert day["episodes"] == 10
    assert day["origins"] == {"search": 4, "dailyfam": 6}
    assert day["sources"] == {"episodes": 0.06, "edition:dailyfam": 0.1}
    assert day["claude_usd"] == pytest.approx(0.16)
    assert b["sources"][0] == "edition:dailyfam"
    assert day["cents_per_episode"] == pytest.approx(1.6)


def test_the_nightly_agents_are_taken_out_of_their_own_day(ledgers):
    ledger, rows = ledgers
    now = time.time()
    rows += [{"at": now, "origin": "search"}] * 10
    _spend_on(now, "episodes", 0.10)
    _spend_on(now, "nightly", 0.04)
    ledger.record_agent_call(agent="supervisor", task="t", surface="background",
                             status="ok", vendor="anthropic", model="claude-haiku-5-5",
                             served_model="claude-haiku-5-5", usage={}, cost_usd=0.04,
                             priced=True, sent=True, seconds=1.0, reason="")
    today = CW.by_day(now)["days"][-1]
    assert today["nightly_usd"] == pytest.approx(0.04)
    assert today["cents_per_episode"] == pytest.approx(1.0)


def test_the_report_carries_the_days_and_a_day_with_no_episodes_has_no_cents(ledgers):
    r = CW.report(time.time())
    assert len(r["by_day"]["days"]) in (8, 9)
    assert all(d["cents_per_episode"] is None for d in r["by_day"]["days"])


def test_the_admin_page_draws_the_table():
    import os
    page = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             "admin_ui", "tracker.html")).read()
    assert "costByDayHtml(c.by_day)" in page
    assert "Claude per episode, by day" in page


# ------------------------------------------------------------- 2. medium
def test_the_writer_written_ahead_is_medium_by_default():
    assert config.Settings().ahead_writer_effort == "medium"
    for flags in ({"ahead": True}, {"edition": True}, {"prefetch": True}):
        assert sg.writer_for(ScriptNotes(**flags))[1] == "medium"
    assert sg.writer_for(ScriptNotes())[1] == "low"          # searchFAM unmoved


# ------------------------------------------------------- 3. the write-ahead
@pytest.fixture
def batching(monkeypatch, tmp_path):
    on = dataclasses.replace(config.settings, edition_batch=True,
                             edition_batch_poll_seconds=1.0,
                             startup_write_ahead=True, prefetch=True,
                             prefetch_level="script", prefetch_batch=True)
    monkeypatch.setattr(config, "settings", on)
    monkeypatch.setattr(trending_bank, "settings", on)
    monkeypatch.setattr(prefetch, "settings", on)
    monkeypatch.setattr(claude_batch.asyncio, "sleep", _no_sleep)
    trending_bank.reset(trending_bank.BankStore(str(tmp_path / "t.db")))
    yield on
    trending_bank.reset()


def test_the_start_here_questions_go_out_as_one_batch(batching):
    import startup

    gen, cache = BatchWriter(), MemoryScriptCache()
    results = run(trending_bank.write_startup(time.time(), gen, cache, force=True))
    assert results is not None
    assert set(results) == {spec[0] for spec in startup.STARTUP_TOPICS}
    assert len(gen.batches.created) == 1
    assert len(gen.batches.created[0]) == len(startup.STARTUP_TOPICS)
    assert not [e for e in gen.log if e[0] == "live"]


def test_a_rewritten_question_is_not_set_aside_for_last_slots_episode(batching):
    """The write-ahead rewrites a current key on purpose: the key already in
    the cache is last slot's episode, not a tap that raced the batch."""
    from types import SimpleNamespace

    gen, cache = BatchWriter(), MemoryScriptCache()
    stories = [SimpleNamespace(id="q", query="what happened this week in film")]
    first = run(trending_bank.write_batched(stories, gen, cache, 2,
                                            time.time() + 3600, rewrite=True))
    again = run(trending_bank.write_batched(stories, gen, cache, 2,
                                            time.time() + 3600, rewrite=True))
    assert first["q"]["status"] == again["q"]["status"] == "written"


# --------------------------------------------------------------- 4. prefetch
def _candidate(query):
    return prefetch.Candidate(query=query, minutes=2, source="test", reason="because")


class Source:
    name = "test"

    def __init__(self, queries):
        self.queries = queries

    def candidates(self, listener="", limit=6):
        return [_candidate(q) for q in self.queries][:limit]


async def _drain():
    while prefetch._CYCLES:
        await asyncio.gather(*list(prefetch._CYCLES))


def _cycle(pf):
    async def go():
        result = await pf.run_once()
        await _drain()
        return result
    return run(go())


@pytest.fixture
def warmer(batching):
    prefetch.reset()
    prefetch.reset_sources()
    yield
    prefetch.reset()
    prefetch.reset_sources()


def _prefetcher(gen, cache):
    return prefetch.Prefetcher(generator=gen, cache=cache,
                               budget=prefetch.Budget(max_episodes=10, max_dollars=5.0))


def test_a_cycles_script_warms_go_out_as_one_batch(warmer):
    import pipeline

    prefetch.register(Source(["why the sky is blue", "how tides work"]))
    gen, cache = BatchWriter(), MemoryScriptCache()
    pf = _prefetcher(gen, cache)
    result = _cycle(pf)
    assert result["outcomes"] == {"batched": 2}
    assert len(gen.batches.created) == 1 and len(gen.batches.created[0]) == 2
    for q in ("why the sky is blue", "how tides work"):
        key = run(pipeline.key_for(plan_episode(q, 2)))
        assert cache.get(key) == ["One.", "Two."]
    assert not pf._batched_keys
    assert pf.ledger.as_dict()  # counted as warmed by `_store`


def test_a_failed_prefetch_batch_is_written_live_from_what_was_prepared(warmer):
    prefetch.register(Source(["why the sky is blue"]))
    gen = BatchWriter(fail_create=True)
    _cycle(_prefetcher(gen, MemoryScriptCache()))
    assert [e[0] for e in gen.log].count("prepare") == 1
    assert [e[0] for e in gen.log].count("live") == 1


def test_while_a_batch_is_out_a_cycle_warms_briefs_only(warmer):
    prefetch.register(Source(["how tides work"]))
    gen, cache = BatchWriter(), MemoryScriptCache()
    pf = _prefetcher(gen, cache)
    pf._batched_keys.add("some key still out")
    result = run(pf.run_once())
    assert result["outcomes"] == {"brief": 1}
    assert gen.batches.created == []


def test_prefetch_batch_off_writes_live_as_before(warmer, monkeypatch):
    monkeypatch.setattr(prefetch, "settings", dataclasses.replace(
        prefetch.settings, prefetch_batch=False))
    prefetch.register(Source(["why the sky is blue"]))

    class Live(BatchWriter):
        async def stream_sentences(self, plan, notes):
            self.log.append(("stream", plan.query))
            yield "One."

    gen = Live()
    result = _cycle(_prefetcher(gen, MemoryScriptCache()))
    assert result["outcomes"] == {"script": 1}
    assert gen.batches.created == []


def test_the_setting_is_copied_and_reported():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert "\nPREFETCH_BATCH=1\n" in open(os.path.join(root, ".env.example")).read()
    assert '"prefetch_batch": settings.prefetch_batch' in open(
        os.path.join(root, "app.py")).read()


def test_the_write_ahead_batch_is_waited_for_well_inside_the_grace(batching, monkeypatch):
    """Last slot's "Start here" episodes expire `EPISODE_GRACE_SECONDS` after
    the slot; a batch waited for the editions' full hour would leave the live
    fallback no time before the cards lose their names."""
    seen = {}
    real = claude_batch.run

    async def spy(client, requests, **kwargs):
        seen["wait"] = kwargs["wait_seconds"]
        return await real(client, requests, **kwargs)

    monkeypatch.setattr(claude_batch, "run", spy)
    run(trending_bank.write_startup(time.time(), BatchWriter(), MemoryScriptCache(),
                                    force=True))
    assert seen["wait"] == trending_bank.STARTUP_BATCH_WAIT_SECONDS
    assert seen["wait"] <= trending_bank.EPISODE_GRACE_SECONDS / 2


def test_the_write_ahead_and_the_edition_do_not_wait_for_each_other(monkeypatch):
    """Both are batched, so the scheduler runs them side by side."""
    on = dataclasses.replace(config.settings, trending_bank=True,
                             startup_write_ahead=True)
    monkeypatch.setattr(trending_bank, "settings", on)
    started = []
    both = asyncio.Event()

    alone = []

    async def job(name):
        started.append(name)
        if len(started) == 2:
            both.set()
        try:
            await asyncio.wait_for(both.wait(), 0.5)
        except asyncio.TimeoutError:
            alone.append(name)          # ran while the other had not started

    async def startup_job(**_kw):
        await job("startup")

    async def edition_job(**_kw):
        await job("edition")

    monkeypatch.setattr(trending_bank, "startup_due", lambda: True)
    monkeypatch.setattr(trending_bank, "due", lambda: True)
    monkeypatch.setattr(trending_bank, "write_startup", startup_job)
    monkeypatch.setattr(trending_bank, "build", edition_job)
    monkeypatch.setattr(trending_bank, "store", lambda: type(
        "S", (), {"status": staticmethod(lambda _sid: None)})())

    async def go():
        task = asyncio.ensure_future(trending_bank.run_forever(generator=object()))
        await asyncio.wait_for(both.wait(), 2)
        task.cancel()
    run(go())
    assert sorted(started) == ["edition", "startup"]
    assert alone == [], "one job waited for the other to finish"


# ------------------------------------------------- the branch double-checked
def test_a_tap_written_while_the_write_ahead_batch_was_out_is_kept(batching, monkeypatch):
    """`rewrite` sets aside last slot's episode, never a tap's: an episode
    sourced after this one was prepared is a listener's, and stays."""
    from types import SimpleNamespace
    from pipeline import key_for

    cache = MemoryScriptCache()
    story = SimpleNamespace(id="q", query="what happened this week in film")
    real = claude_batch.run

    async def run_then_tap(client, requests, **kwargs):
        answers = await real(client, requests, **kwargs)
        key = await key_for(plan_episode(story.query, 2))
        cache.put(key, ["Tapped."], 3600, story.query, "", 2, "", "", "",
                  "Tap's title", sourced_at=time.time() + 60)
        return answers

    monkeypatch.setattr(claude_batch, "run", run_then_tap)
    result = run(trending_bank.write_batched([story], BatchWriter(), cache, 2,
                                             time.time() + 3600, rewrite=True))
    assert result["q"]["status"] == "cached"
    assert cache.get(result["q"]["key"]) == ["Tapped."]


def test_an_unanswered_prefetch_warm_waits_for_quiet_before_it_is_written_live(warmer):
    prefetch.register(Source(["why the sky is blue"]))
    gen = BatchWriter(fail_create=True)
    pf = _prefetcher(gen, MemoryScriptCache())

    async def go():
        await pf.run_once()
        pf.note_live_generation()           # a listener starts while it is out
        await _drain()
    run(go())
    assert [e[0] for e in gen.log].count("live") == 0
    assert pf.budget.briefs == 1 and pf.budget.episodes == 0


def test_a_batched_warm_a_tap_beat_is_charged_as_a_brief(warmer, monkeypatch):
    import pipeline

    prefetch.register(Source(["why the sky is blue"]))
    cache = MemoryScriptCache()
    real = claude_batch.run

    async def run_then_tap(client, requests, **kwargs):
        answers = await real(client, requests, **kwargs)
        key = await pipeline.key_for(plan_episode("why the sky is blue", 2))
        cache.put(key, ["Tapped."], 3600, "why the sky is blue", "", 2, "", "", "", "t")
        return answers

    monkeypatch.setattr(claude_batch, "run", run_then_tap)
    pf = _prefetcher(BatchWriter(), cache)
    _cycle(pf)
    assert pf.budget.episodes == 0 and pf.budget.briefs == 1


def test_an_empty_prefetch_batch_setting_is_the_default_as_edition_batch_is(monkeypatch):
    monkeypatch.setenv("PREFETCH_BATCH", "")
    monkeypatch.setenv("EDITION_BATCH", "")
    s = config.Settings()
    assert s.prefetch_batch is True and s.edition_batch is True
