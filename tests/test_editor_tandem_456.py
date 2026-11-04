"""Shakespeare on searchFAM, in tandem with the writer (§456).

His plan starts beside the writer, never in front of the first word. The
writer opens from the packets as today; once `EDITOR_TANDEM_OPENING_WORDS`
are spoken it waits for his plan until `EDITOR_TANDEM_WAIT_SECONDS`, and a
second writer call writes the rest from it. Late, empty or off: the writer
is exactly what it was.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import time

import pytest

import script_generator as sg
from agents import editor, netenyahu
from script_generator import ScriptNotes, plan_episode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE = """SOURCE 1
Title: Fed holds
Published: 2026-11-03 (yesterday)
Key evidence:
The Federal Reserve held its benchmark rate at 4.25 percent.
"""
FIRST = ("The Federal Reserve held its rate at four and a quarter percent on Wednesday. "
         "Powell said the committee would wait for more data before moving. "
         "Two members wanted a cut and said so in their dissents. "
         "Markets had expected exactly this outcome all week.")
SECOND = ("The two dissents are the story, because they show the committee is split. "
          "The next meeting is in December.")


class _Usage:
    input_tokens, output_tokens = 100, 10
    cache_read_input_tokens = cache_creation_input_tokens = 0


class _Final:
    stop_reason = "end_turn"
    usage = _Usage()


class _Stream:
    """As the SDK's: one text generator, resumable after a break, and
    `get_final_message` reads whatever is left of it first."""

    def __init__(self, text, log, fail_at=None, final_fails=False, chunk=1):
        self.text, self.log, self.read = text, log, []
        self.fail_at, self.final_fails, self.chunk = fail_at, final_fails, chunk
        self.text_stream = self._gen()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def _gen(self):
        words = self.text.split(" ")
        # `chunk` words at a time, as a real stream straddles sentence ends.
        for i in range(0, len(words), self.chunk):
            if self.fail_at is not None and i >= self.fail_at:
                raise RuntimeError("overloaded")
            await asyncio.sleep(0.005)
            part = words[i:i + self.chunk]
            self.read.extend(part)
            yield " ".join(part) + " "

    async def get_final_message(self):
        if self.final_fails:
            raise RuntimeError("connection reset")
        async for _ in self.text_stream:
            pass
        self.log.append(self.text[:12])
        return _Final()


def generator(texts=(FIRST, SECOND), second_fails_at=None, second_open_fails=False,
              first_final_fails=False, chunk=1):
    gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    asked, finals, streams = [], [], []

    class _Messages:
        def stream(self, **kwargs):
            asked.append(kwargs)
            i = len(streams)
            if i == 1 and second_open_fails:
                raise RuntimeError("rate limited")
            if i == 1 and first_final_fails:
                streams[0].final_fails = True
            streams.append(_Stream(texts[i], finals, chunk=chunk,
                                   fail_at=second_fails_at if i == 1 else None))
            return streams[-1]

    gen.client = type("C", (), {"messages": _Messages()})()
    gen.streams = streams
    return gen, asked, finals


def settings_for(monkeypatch, on=True, words=8, wait=1.0):
    import config

    changed = dataclasses.replace(config.settings, editor_tandem=on,
                                  editor_tandem_opening_words=words,
                                  editor_tandem_wait_seconds=wait)
    monkeypatch.setattr(config, "settings", changed)
    monkeypatch.setattr(sg, "settings", changed)


def shakespeare(monkeypatch, block="\n<plan>open on the dissents</plan>\n", delay=0.0):
    called = []

    async def outline(p, notes=None, **kw):
        called.append((time.monotonic(), kw))
        await asyncio.sleep(delay)
        return block

    monkeypatch.setattr(editor, "outline", outline)
    return called


def run(gen, roles=("editor",), ahead=False):
    notes = ScriptNotes()
    notes.ahead = ahead
    notes.agents = netenyahu.EpisodeRun(tuple(roles), {})
    plan = dataclasses.replace(plan_episode("what did the fed decide", 1.5),
                               search=False, evidence=EVIDENCE)
    times = []

    async def go():
        start = time.monotonic()
        out = []
        async for s in gen.stream_prepared(plan, notes):
            times.append(time.monotonic() - start)
            out.append(s)
        return out

    return asyncio.run(go()), notes, times


@pytest.fixture(autouse=True)
def fresh():
    editor.reset()
    yield
    editor.reset()


def test_off_search_is_written_as_it_always_was(monkeypatch):
    settings_for(monkeypatch, on=False)
    called = shakespeare(monkeypatch)
    gen, asked, _ = generator()
    spoken, notes, _ = run(gen)
    assert called == [] and len(asked) == 1 and notes.editor_tandem == ""
    assert " ".join(spoken) == FIRST


def test_a_plan_in_time_writes_the_rest_from_it(monkeypatch):
    settings_for(monkeypatch, words=8)
    called = shakespeare(monkeypatch, delay=0.05)
    gen, asked, finals = generator()
    spoken, notes, _ = run(gen)
    assert len(called) == 1 and called[0][1] == {"ahead": False}   # search: his low effort
    assert len(asked) == 2 and notes.editor_tandem == "handed_over"
    # The opening is the first call's, whole sentences only; then the second's.
    assert spoken[0] == "The Federal Reserve held its rate at four and a quarter percent on Wednesday."
    assert spoken[-2:] == SECOND.replace(". ", ".\n").split("\n")
    assert "Markets had expected" not in " ".join(spoken)
    # The second call reads his plan, what was spoken, and the words left.
    prompt = asked[1]["messages"][0]["content"][-1]["text"]
    assert "<plan>open on the dissents</plan>" in prompt
    assert "<already_spoken>\n" + " ".join(spoken[:-2]) + "\n</already_spoken>" in prompt
    assert "more words" in prompt and asked[1]["system"] == asked[0]["system"]
    # The first call's prompt is what it always was: no plan, nothing spoken.
    assert "<plan>" not in asked[0]["messages"][0]["content"][-1]["text"]
    # Both calls are metered from their own final messages.
    assert sorted(finals) == sorted([FIRST[:12], SECOND[:12]])
    assert editor.report()["since_boot"]["tandem_handed_over"] == 1


def test_he_is_never_in_front_of_the_first_word(monkeypatch):
    settings_for(monkeypatch, words=8, wait=2.0)
    called = shakespeare(monkeypatch, delay=0.5)
    gen, _, _ = generator()
    spoken, notes, times = run(gen)
    assert times[0] < 0.4                      # the first sentence before his plan
    assert notes.editor_tandem == "handed_over"


def test_a_late_plan_leaves_the_writer_as_it_was(monkeypatch):
    settings_for(monkeypatch, words=8, wait=0.2)
    shakespeare(monkeypatch, delay=1.0)
    gen, asked, _ = generator()
    spoken, notes, _ = run(gen)
    assert len(asked) == 1 and notes.editor_tandem == "late"
    assert " ".join(spoken) == FIRST


def test_an_empty_plan_leaves_the_writer_as_it_was(monkeypatch):
    settings_for(monkeypatch, words=8)
    shakespeare(monkeypatch, block="")
    gen, asked, _ = generator()
    spoken, notes, _ = run(gen)
    assert len(asked) == 1 and notes.editor_tandem == "no_plan"
    assert " ".join(spoken) == FIRST


def test_an_episode_shorter_than_the_opening_is_never_handed_over(monkeypatch):
    settings_for(monkeypatch, words=500)
    shakespeare(monkeypatch)
    gen, asked, _ = generator()
    spoken, notes, _ = run(gen)
    assert len(asked) == 1 and notes.editor_tandem == "short"
    assert " ".join(spoken) == FIRST


def test_not_ahead_of_the_tap_and_not_when_he_does_not_work(monkeypatch):
    settings_for(monkeypatch)
    for roles, ahead in ((("editor",), True), ((), False)):
        called = shakespeare(monkeypatch)
        gen, asked, _ = generator()
        spoken, notes, _ = run(gen, roles=roles, ahead=ahead)
        assert called == [] and len(asked) == 1 and notes.editor_tandem == ""


def test_the_settings_are_copied_and_off():
    text = open(os.path.join(ROOT, ".env.example")).read()
    for line in ("EDITOR_TANDEM=0", "EDITOR_TANDEM_OPENING_WORDS=30",
                 "EDITOR_TANDEM_WAIT_SECONDS=7"):
        assert f"\n{line}\n" in text
    assert editor.report()["tandem"] is False


def test_both_calls_are_metered_from_their_own_answers(monkeypatch):
    settings_for(monkeypatch, words=8)
    shakespeare(monkeypatch)
    gen, _, _ = generator()
    _spoken, notes, _ = run(gen)
    assert notes.editor_tandem == "handed_over"
    assert notes.usage.input_tokens == 200 and notes.usage.output_tokens == 20


def test_a_second_call_that_fails_before_speaking_leaves_the_first_to_finish(monkeypatch):
    """The review (§459): the episode must never die for the editor's sake."""
    for kw in ({"second_fails_at": 0}, {"second_open_fails": True}):
        settings_for(monkeypatch, words=8)
        shakespeare(monkeypatch)
        gen, asked, _ = generator(**kw)
        spoken, notes, _ = run(gen)
        assert len(asked) == 2 and notes.editor_tandem == "second_failed"
        assert " ".join(spoken) == FIRST          # nothing lost, nothing repeated


def test_a_second_call_that_fails_after_speaking_fails_as_a_writer_does(monkeypatch):
    settings_for(monkeypatch, words=8)
    shakespeare(monkeypatch)
    gen, _, _ = generator(second_fails_at=14)
    with pytest.raises(RuntimeError):
        run(gen)


def test_reading_the_first_call_for_its_bill_never_costs_the_episode(monkeypatch):
    settings_for(monkeypatch, words=8)
    shakespeare(monkeypatch)
    gen, _, _ = generator(first_final_fails=True)
    spoken, notes, _ = run(gen)
    assert notes.editor_tandem == "handed_over"
    assert spoken[-1] == "The next meeting is in December."
    assert notes.usage.input_tokens == 100        # the second call's, still billed


def test_past_the_budget_the_tail_is_spoken_before_the_rest_is_read(monkeypatch):
    """As `stream_prepared` does (§459): a runaway writer's last sentence
    does not wait for everything the model goes on to write."""
    settings_for(monkeypatch, words=500)
    shakespeare(monkeypatch)
    long = " ".join(f"Sentence number {i} says the Fed held its rate today." for i in range(40))
    gen, _, _ = generator(texts=(long,), chunk=7)
    notes = ScriptNotes()
    notes.agents = netenyahu.EpisodeRun(("editor",), {})
    p = dataclasses.replace(plan_episode("what did the fed decide", 1.5), search=False,
                            evidence=EVIDENCE, word_budget=20)
    read_when_spoken = []

    async def go():
        async for _s in gen.stream_prepared(p, notes):
            read_when_spoken.append(len(gen.streams[0].read))

    asyncio.run(go())
    assert read_when_spoken and read_when_spoken[-1] < len(long.split(" "))


def test_a_plan_that_did_not_shape_the_episode_is_not_on_its_record(monkeypatch):
    settings_for(monkeypatch, words=8)

    async def outline(p, notes=None, **kw):
        notes.editor = {"beats": 0}
        return ""

    monkeypatch.setattr(editor, "outline", outline)
    gen, _, _ = generator()
    _spoken, notes, _ = run(gen)
    assert notes.editor_tandem == "no_plan" and notes.editor is None


def test_the_local_gap_line_is_part_of_what_was_spoken(monkeypatch):
    settings_for(monkeypatch, words=8)
    shakespeare(monkeypatch)
    monkeypatch.setattr(sg._ScriptReader, "opening", lambda self: ["Nothing came from the town."])
    gen, asked, _ = generator()
    spoken, notes, _ = run(gen)
    assert spoken[0] == "Nothing came from the town." and notes.editor_tandem == "handed_over"
    prompt = asked[1]["messages"][0]["content"][-1]["text"]
    assert "<already_spoken>\nNothing came from the town. The Federal Reserve" in prompt
