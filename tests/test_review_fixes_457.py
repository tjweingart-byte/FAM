"""The review's two small fixes (§457).

1. A prefetch warm's second look is labelled "ahead": nobody is waiting on
   it, whether or not `notes.ahead` is set (`ScriptNotes.ahead`).
2. A guard that drops the rewrite of an episode's final sentence is on the
   notes too: that rewrite is rechecked after `finish` copied the drops.
"""
from __future__ import annotations

import asyncio
import dataclasses

import script_generator as sg
from agents import grounding, netenyahu
from agents.handoffs import ResearchPlan
from script_generator import ScriptNotes, plan_episode

EVIDENCE = """SOURCE 1
Title: Fed holds
Published: 2026-11-03 (yesterday)
Key evidence:
The Federal Reserve held its benchmark rate at 4.25 percent on Wednesday.
"""


def plan():
    return dataclasses.replace(plan_episode("what did the fed decide", 1.5),
                               search=False, evidence=EVIDENCE)


class _Answer:
    def __init__(self, text):
        self.content = [type("B", (), {"type": "text", "text": text})()]
        self.usage = None


def _generator(rewrites):
    gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)

    class _Messages:
        async def create(self, **kwargs):
            return _Answer(rewrites.pop(0) if rewrites else "NONE")

    gen.client = type("C", (), {"messages": _Messages()})()
    return gen


def _reader(notes):
    notes.agents = netenyahu.EpisodeRun(("supervisor",), {})
    notes.research_plan = ResearchPlan()
    return sg._ScriptReader(plan(), notes)


def test_a_prefetch_warms_second_look_is_ahead(monkeypatch):
    asked = []

    async def second_look(sentence, sources, ahead=False):
        asked.append(ahead)
        return "yes"

    monkeypatch.setattr(grounding, "second_look", second_look)
    for prefetch, ahead, expected in ((True, False, True), (False, True, True),
                                      (False, False, False)):
        notes = ScriptNotes()
        notes.prefetch, notes.ahead = prefetch, ahead
        reader = _reader(notes)
        item = sg.Repair("The Fed held at 4.25 percent.", "unsure", reader, second_look=True)
        asyncio.run(_generator([])._repair(plan(), notes, item))
        assert asked[-1] is expected


def test_a_dropped_rewrite_of_the_final_sentence_is_on_the_notes():
    notes = ScriptNotes()
    reader = _reader(notes)
    # The last sentence names a website (enforced): `finish` hands it back
    # for its one rewrite, after it has copied the guards' drops.
    reader.feed("The Federal Reserve held its rate at 4.25 percent on Wednesday. ")
    tail = reader.finish()
    assert tail == [] or not isinstance(tail[-1], sg.Repair)
    reader.feed("Read the statement at federalreserve dot gov.")
    out = reader.finish()
    item = out[-1]
    assert isinstance(item, sg.Repair)
    assert notes.meta_openings == ()
    # The rewrite talks about its own material: the material guard drops it.
    rewrite = "The information I received says the Fed held its rate."
    spoken = asyncio.run(_generator([rewrite])._repair(plan(), notes, item))
    assert spoken == ""
    assert rewrite in notes.meta_openings
