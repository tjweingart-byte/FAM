"""The editions write through the Message Batches API at half price (§179).

What is pinned:

* **A saving never costs an episode.** A batch that cannot be created, that
  errors a request, or that has not ended by its deadline gives back `None`
  for what it did not answer, and the edition writes those live - from the
  brief and evidence it already paid for, never asking for them twice.
* **A batched episode is read exactly as a streamed one.** The same reader
  parses the answer: the same sentences, title, `<<NEXT:>>` and guard.
* **Metering says what was billed.** A batched call's Claude cost is half
  its list price, cache reads and writes included.
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

import claude_batch  # noqa: E402
import config  # noqa: E402
import daily_edition  # noqa: E402
import metering  # noqa: E402
import mixes as M  # noqa: E402
import script_generator  # noqa: E402
import trending_bank  # noqa: E402
from cache import MemoryScriptCache  # noqa: E402
from script_generator import ScriptGenerator, ScriptNotes, plan_episode  # noqa: E402


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# A fake Batches API
# --------------------------------------------------------------------------
def message(text="One. Two.", stop="end_turn", tokens=(1000, 500)):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1],
                              cache_read_input_tokens=0,
                              cache_creation_input_tokens=0),
        stop_reason=stop, stop_details=None)


class Results:
    def __init__(self, entries):
        self.entries = entries

    def __aiter__(self):
        async def gen():
            for entry in self.entries:
                yield entry
        return gen()


class FakeBatches:
    def __init__(self, *, fail_create=False, polls_to_end=1, errored=(),
                 end_on_cancel=True):
        self.fail_create = fail_create
        self.polls_to_end = polls_to_end
        self.errored = set(errored)
        self.end_on_cancel = end_on_cancel
        self.created: list = []
        self.cancelled: list = []
        self.polls = 0

    async def create(self, requests):
        if self.fail_create:
            raise RuntimeError("503 overloaded")
        self.created.append(requests)
        return SimpleNamespace(id="batch_1", processing_status="in_progress")

    async def retrieve(self, batch_id):
        self.polls += 1
        ended = (self.polls >= self.polls_to_end
                 or (self.cancelled and self.end_on_cancel))
        return SimpleNamespace(id=batch_id,
                               processing_status="ended" if ended else "in_progress")

    async def cancel(self, batch_id):
        self.cancelled.append(batch_id)
        return SimpleNamespace(id=batch_id, processing_status="canceling")

    async def results(self, batch_id):
        entries = []
        for request in self.created[-1]:
            cid = request["custom_id"]
            if self.cancelled:
                result = SimpleNamespace(type="canceled")
            elif cid in self.errored:
                result = SimpleNamespace(type="errored", error=SimpleNamespace(
                    error=SimpleNamespace(type="api_error", message="boom")))
            else:
                result = SimpleNamespace(type="succeeded", message=message())
            entries.append(SimpleNamespace(custom_id=cid, result=result))
        return Results(entries)


def client(**kwargs):
    batches = FakeBatches(**kwargs)
    return SimpleNamespace(messages=SimpleNamespace(batches=batches)), batches


async def _no_sleep(_seconds):
    return None


class Clock:
    def __init__(self, step=30.0):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


# --------------------------------------------------------------------------
# claude_batch.run
# --------------------------------------------------------------------------
def test_one_batch_answers_every_request():
    fake, batches = client(polls_to_end=2)
    beats = []
    answers = run(claude_batch.run(fake, {"a": {"x": 1}, "b": {"x": 2}},
                                   wait_seconds=3600, alive=lambda: beats.append(1),
                                   sleep=_no_sleep))
    assert len(batches.created) == 1 and len(batches.created[0]) == 2
    assert set(answers) == {"a", "b"} and all(answers.values())
    assert beats, "the claim is kept alive while the batch is waited on"


def test_a_batch_that_cannot_be_created_answers_nothing_and_raises_nothing():
    fake, _ = client(fail_create=True)
    assert run(claude_batch.run(fake, {"a": {}}, wait_seconds=60,
                                sleep=_no_sleep)) == {"a": None}


def test_an_errored_request_is_left_for_the_live_writer():
    fake, _ = client(errored={"b"})
    answers = run(claude_batch.run(fake, {"a": {}, "b": {}}, wait_seconds=60,
                                   sleep=_no_sleep))
    assert answers["a"] is not None and answers["b"] is None


def test_past_the_deadline_the_batch_is_cancelled_and_nothing_is_waited_for():
    fake, batches = client(polls_to_end=10_000)
    answers = run(claude_batch.run(fake, {"a": {}}, wait_seconds=60,
                                   sleep=_no_sleep, clock=Clock()))
    assert batches.cancelled == ["batch_1"]
    assert answers == {"a": None}


def test_a_batch_that_never_ends_even_cancelled_is_given_up_on():
    fake, batches = client(polls_to_end=10_000, end_on_cancel=False)
    answers = run(claude_batch.run(fake, {"a": {}}, wait_seconds=60,
                                   sleep=_no_sleep, clock=Clock()))
    assert batches.cancelled and answers == {"a": None}


def test_an_invalid_custom_id_is_never_sent():
    fake, batches = client()
    assert run(claude_batch.run(fake, {"has space": {}}, wait_seconds=60,
                                sleep=_no_sleep)) == {"has space": None}
    assert batches.created == []


# --------------------------------------------------------------------------
# Metering: half price, said as a discount beside the tokens
# --------------------------------------------------------------------------
def test_a_batched_call_costs_half_its_list_price():
    live, batched = metering.Usage(), metering.Usage()
    usage = {"input_tokens": 5_500, "output_tokens": 2_000,
             "cache_read_input_tokens": 3_500, "cache_creation_input_tokens": 0}
    live.add_model_call("claude-sonnet-5", usage)
    batched.add_model_call("claude-sonnet-5", usage, batched=True)
    full, half = metering.price_of(live), metering.price_of(batched)
    assert batched.input_tokens == live.input_tokens   # tokens are still counted
    assert half.claude == pytest.approx(full.claude / 2)
    assert half.total == pytest.approx(full.total - full.claude / 2)


def test_the_ledger_records_what_was_billed(tmp_path):
    ledger = metering.MeterStore(str(tmp_path / "m.db"))
    usage = metering.Usage()
    usage.add_model_call("claude-sonnet-5", {"input_tokens": 1_000_000,
                                             "output_tokens": 0}, batched=True)
    ledger.record("u", usage)
    row = ledger.rows()[0]
    assert row["claude_usd"] == pytest.approx(1.00)   # $2/M in, halved


# --------------------------------------------------------------------------
# The reader: a batched answer is read exactly as a stream is
# --------------------------------------------------------------------------
SCRIPT = ("The Fed held rates at four percent on Wednesday. Two governors "
          "dissented, the first split since 2019. Markets had priced a hold. "
          "<<TITLE: The Fed Holds, Two Dissent>>\n<<NEXT: what the dissenters "
          "wanted>>")


def _streamed(plan, notes, text, step=7):
    reader = script_generator._ScriptReader(plan, notes)
    out = []
    for i in range(0, len(text), step):
        out.extend(reader.feed(text[i:i + step]))
    out.extend(reader.finish())
    return out


def test_a_batched_answer_gives_the_sentences_a_stream_gives():
    plan = plan_episode("did the fed hold rates", 2, search=False)
    gen = ScriptGenerator.__new__(ScriptGenerator)
    streamed_notes, batched_notes = ScriptNotes(), ScriptNotes()
    streamed = _streamed(plan, streamed_notes, SCRIPT)
    batched = gen.sentences_from_message(plan, batched_notes, message(SCRIPT))
    assert batched == streamed and len(batched) == 3
    assert batched_notes.title == streamed_notes.title == "The Fed Holds, Two Dissent"
    assert batched_notes.thread == streamed_notes.thread
    assert batched_notes.usage.batch_discount > 0


def test_a_batched_answer_is_held_to_the_word_budget():
    plan = plan_episode("anything", 1, search=False)
    gen = ScriptGenerator.__new__(ScriptGenerator)
    long_text = " ".join(f"Sentence number {i} is here." for i in range(2000))
    words = sum(len(s.split()) for s in gen.sentences_from_message(
        plan, ScriptNotes(), message(long_text)))
    assert words <= plan.max_words * 1.35 + 60


def test_a_refused_batched_request_says_so():
    plan = plan_episode("anything", 1, search=False)
    gen = ScriptGenerator.__new__(ScriptGenerator)
    out = gen.sentences_from_message(plan, ScriptNotes(),
                                     message("", stop="refusal"))
    assert out and out[-1].startswith("I can't put together a briefing")


# --------------------------------------------------------------------------
# The editions
# --------------------------------------------------------------------------
class BatchWriter:
    """An edition writer with the batch half. Records what it was asked."""

    def __init__(self, **batch_kwargs):
        self.client, self.batches = client(**batch_kwargs)
        self.log = []

    async def understand(self, plan, notes):
        self.log.append(("understand", plan.query))
        return dataclasses.replace(plan, brief=SimpleNamespace(
            degraded=False, outcome_dependent=False, recency_days=1))

    async def batch_request(self, plan, notes):
        self.log.append(("prepare", plan.query))
        notes.sourced_at = time.time()
        return dataclasses.replace(plan, evidence="packet"), {"q": plan.query}

    def sentences_from_message(self, plan, notes, msg):
        assert plan.evidence == "packet"
        self.log.append(("batched", plan.query))
        notes.title = "A title"
        return ["One.", "Two."]

    async def stream_prepared(self, plan, notes):
        assert plan.evidence == "packet", "a fallback re-prepared the episode"
        self.log.append(("live", plan.query))
        notes.title = "A title"
        yield "One."
        yield "Two."

    async def stream_sentences(self, plan, notes):  # pragma: no cover
        raise AssertionError("a batching edition never prepares twice")


@pytest.fixture
def edition(tmp_path, monkeypatch):
    on = dataclasses.replace(config.settings, daily_edition=True,
                             edition_batch=True, edition_batch_poll_seconds=1.0)
    monkeypatch.setattr(config, "settings", on)
    monkeypatch.setattr(trending_bank, "settings", on)
    monkeypatch.setattr(claude_batch.asyncio, "sleep", _no_sleep)
    daily_edition.reset(daily_edition.EditionStore(str(tmp_path / "ed.db")))
    yield M.MixStore(str(tmp_path / "m.db"))
    daily_edition.reset()


def _mixes(store):
    store.create("a", "Morning", ["f:nfl~Eagles", "f:stocks~Nvidia"])
    store.create("b", "Gym", ["fed-next-move"])


def test_the_daily_edition_sends_its_writers_as_one_batch(edition):
    _mixes(edition)
    gen, cache = BatchWriter(), MemoryScriptCache()
    report = run(daily_edition.build(edition, gen, cache))
    assert report["written"] == report["batched"] == 3
    assert len(gen.batches.created) == 1 and len(gen.batches.created[0]) == 3
    assert not [e for e in gen.log if e[0] == "live"]
    for query, result in report["episodes"].items():
        assert cache.get(result["key"]) == ["One.", "Two."]
    assert not daily_edition._IN_FLIGHT


def test_a_failed_batch_is_written_live_without_asking_twice(edition):
    _mixes(edition)
    gen, cache = BatchWriter(fail_create=True), MemoryScriptCache()
    report = run(daily_edition.build(edition, gen, cache))
    assert report["written"] == 3 and report["batched"] == 0
    prepared = [e for e in gen.log if e[0] == "prepare"]
    assert len(prepared) == 3, "each episode is prepared once, batch or not"
    assert len([e for e in gen.log if e[0] == "live"]) == 3
    assert not daily_edition._IN_FLIGHT


def test_one_errored_request_is_written_live_and_the_rest_from_the_batch(edition):
    _mixes(edition)
    gen, cache = BatchWriter(errored={"e0"}), MemoryScriptCache()
    report = run(daily_edition.build(edition, gen, cache))
    assert report["written"] == 3 and report["batched"] == 2


def test_edition_batch_off_writes_live_as_before(edition, monkeypatch):
    off = dataclasses.replace(config.settings, edition_batch=False)
    monkeypatch.setattr(config, "settings", off)
    _mixes(edition)

    class Live(BatchWriter):
        async def stream_sentences(self, plan, notes):
            self.log.append(("stream", plan.query))
            yield "One."

    gen = Live()
    report = run(daily_edition.build(edition, gen, MemoryScriptCache()))
    assert report["written"] == 3 and report["batched"] == 0
    assert gen.batches.created == []


def _stories(n=3):
    return [SimpleNamespace(id=f"s{i}", query=f"story number {i} this week")
            for i in range(n)]


def test_the_trending_edition_sends_its_writers_as_one_batch(edition):
    gen, cache = BatchWriter(), MemoryScriptCache()
    beats = []
    results = run(trending_bank.write_batched(
        _stories(), gen, cache, 2, time.time() + 3600,
        alive=lambda: beats.append(1)))
    assert [r["status"] for r in results.values()] == ["written"] * 3
    assert all(r["batched"] for r in results.values())
    assert len(gen.batches.created) == 1 and len(gen.batches.created[0]) == 3
    assert beats


def test_a_trending_story_that_is_a_result_is_left_for_the_tap(edition):
    class Results(BatchWriter):
        async def understand(self, plan, notes):
            return dataclasses.replace(plan, brief=SimpleNamespace(
                degraded=False, outcome_dependent=True, recency_days=1))

    gen = Results()
    results = run(trending_bank.write_batched(
        _stories(2), gen, MemoryScriptCache(), 2, time.time() + 3600))
    assert [r["status"] for r in results.values()] == ["volatile"] * 2
    assert gen.batches.created == []


def test_a_failed_trending_batch_is_written_live(edition):
    gen = BatchWriter(fail_create=True)
    results = run(trending_bank.write_batched(
        _stories(), gen, MemoryScriptCache(), 2, time.time() + 3600))
    assert [r["status"] for r in results.values()] == ["written"] * 3
    assert not any(r["batched"] for r in results.values())
    assert len([e for e in gen.log if e[0] == "prepare"]) == 3


# --------------------------------------------------------------------------
# Review fixes: a tap in the window, the dollar ceiling, the 1h TTL
# --------------------------------------------------------------------------
class TapsMeanwhile(BatchWriter):
    """While the batch is out, a listener's tap writes every episode."""

    def __init__(self, cache, keys, **kw):
        super().__init__(**kw)
        self.cache, self.keys = cache, keys

    async def batch_request(self, plan, notes):
        prepared, params = await super().batch_request(plan, notes)
        self.keys.append(None)
        return prepared, params

    def sentences_from_message(self, plan, notes, msg):  # pragma: no cover
        raise AssertionError("a tap's episode was written over")


def _tap_everything_before_the_answers(monkeypatch, cache, words=("Tapped.",)):
    real = claude_batch.run

    async def run_then_tap(client, requests, **kwargs):
        answers = await real(client, requests, **kwargs)
        for request_params in requests.values():
            from pipeline import key_for
            key = await key_for(plan_episode(request_params["q"],
                                             daily_edition.minutes()))
            cache.put(key, list(words), 3600, request_params["q"], "", 2, "",
                      "", "", "Tap's title", sourced_at=time.time() + 1)
        return answers

    monkeypatch.setattr(claude_batch, "run", run_then_tap)


def test_a_tap_during_the_batch_keeps_its_episode_daily(edition, monkeypatch):
    _mixes(edition)
    cache = MemoryScriptCache()
    _tap_everything_before_the_answers(monkeypatch, cache)
    report = run(daily_edition.build(edition, TapsMeanwhile(cache, []), cache))
    assert report["cached"] == 3 and report["written"] == 0
    for result in report["episodes"].values():
        assert cache.get(result["key"]) == ["Tapped."]
    assert not daily_edition._IN_FLIGHT


def test_a_tap_during_the_batch_keeps_its_episode_trending(edition, monkeypatch):
    cache = MemoryScriptCache()
    real = claude_batch.run

    async def run_then_tap(client, requests, **kwargs):
        answers = await real(client, requests, **kwargs)
        from pipeline import key_for
        for params in requests.values():
            key = await key_for(plan_episode(params["q"], 2))
            cache.put(key, ["Tapped."], 3600, params["q"], "", 2, "", "", "",
                      "Tap's title")
        return answers

    monkeypatch.setattr(claude_batch, "run", run_then_tap)
    results = run(trending_bank.write_batched(
        _stories(), TapsMeanwhile(cache, []), cache, 2, time.time() + 3600))
    assert [r["status"] for r in results.values()] == ["cached"] * 3
    assert all(cache.get(r["key"]) == ["Tapped."] for r in results.values())


def test_the_dollar_ceiling_still_bounds_a_batched_edition(edition, monkeypatch):
    """Batched, the writer is paid when the batch returns - after every
    episode has passed the ceiling check - so each one reserves its writer
    up front. Without that, only the episode ceiling bound the batch."""
    tight = dataclasses.replace(config.settings, daily_edition_max_dollars=0.03)
    monkeypatch.setattr(config, "settings", tight)
    _mixes(edition)
    report = run(daily_edition.build(edition, BatchWriter(), MemoryScriptCache()))
    assert report["skipped_for_ceiling"] >= 1
    assert report["written"] <= 2


def test_an_hour_long_cache_write_is_priced_at_twice_the_input_rate(monkeypatch):
    usage = metering.Usage(model="claude-sonnet-5", cache_write_tokens=1_000_000)
    monkeypatch.setattr(config, "settings",
                        dataclasses.replace(config.settings, prompt_cache_ttl="5m"))
    assert metering.price_of(usage).cache_write == pytest.approx(2.50)
    monkeypatch.setattr(config, "settings",
                        dataclasses.replace(config.settings, prompt_cache_ttl="1h"))
    assert metering.price_of(usage).cache_write == pytest.approx(4.00)
