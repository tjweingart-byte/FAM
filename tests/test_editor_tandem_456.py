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


class _Final:
    stop_reason = "end_turn"
    usage = None


class _Stream:
    def __init__(self, text, log):
        self.text, self.log = text, log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def gen():
            for chunk in self.text.split(" "):
                await asyncio.sleep(0.005)
                yield chunk + " "
        return gen()

    async def get_final_message(self):
        self.log.append(self.text[:12])
        return _Final()


def generator(texts=(FIRST, SECOND)):
    gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    asked, finals = [], []
    queue = list(texts)

    class _Messages:
        def stream(self, **kwargs):
            asked.append(kwargs)
            return _Stream(queue.pop(0), finals)

    gen.client = type("C", (), {"messages": _Messages()})()
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
