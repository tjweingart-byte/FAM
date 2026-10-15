"""Every declined model call, on the errors page with its context (§273).

Haiku 5.5 runs safety classifiers and has no server-side fallback, and no
retry on another model is built yet. So each refusal is recorded with what
it was declined for, on which call and model, who was waiting and how far
the episode got - and the errors page reports how often and where.

No key and no network: every model is a stub.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evidence_digest as ed  # noqa: E402
import pipeline_errors  # noqa: E402
import script_generator as sg  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DETAILS = types.SimpleNamespace(type="refusal", category="general_harms",
                                explanation="This request touches on harm.")


class _Final:
    def __init__(self, stop_reason="refusal"):
        self.stop_reason = stop_reason
        self.stop_details = DETAILS if stop_reason == "refusal" else None
        self.usage = None
        self.content = []
        self.model = "claude-haiku-5-5"


def _writer(text: str, stop_reason="refusal"):
    gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)

    class _Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        @property
        def text_stream(self):
            async def chunks():
                for word in text.split(" "):
                    if word:
                        yield word + " "
            return chunks()

        async def get_final_message(self):
            return _Final(stop_reason)

    gen.client = types.SimpleNamespace(messages=types.SimpleNamespace(
        stream=lambda **kw: _Stream()))
    return gen


def _write(gen, plan, notes):
    async def collect():
        return [s async for s in gen.stream_prepared(plan, notes)]
    return asyncio.run(collect())


def _refusals():
    return pipeline_errors.report()["refusals"]


def test_a_declined_writer_is_recorded_with_its_context():
    plan = sg.plan_episode("what happened in the trial", 2)
    said = _write(_writer("The jury came back this morning. Then"), plan,
                  sg.ScriptNotes())
    assert said[-1].startswith("I can't put together a briefing on that.")
    report = _refusals()
    assert report["week"] == 1 and report["mid_episode"] == 1
    row = report["recent"][0]
    assert row["call"] == "writer" and row["model"] == sg.writer_for(None)[0]
    assert row["path"] == "search" and row["category"] == "general_harms"
    assert row["explanation"] == "This request touches on harm."
    assert row["query"] == "what happened in the trial"
    assert row["spoken"] == 1, "one sentence was heard before it stopped"
    assert report["by_category"][0]["name"] == "general_harms"


def test_a_refusal_before_any_word_says_nothing_was_heard():
    _write(_writer(""), sg.plan_episode("q", 2), sg.ScriptNotes())
    assert _refusals()["recent"][0]["spoken"] == 0
    assert _refusals()["mid_episode"] == 0


def test_the_rate_is_against_every_writer_call():
    for _ in range(3):
        _write(_writer("A whole sentence.", stop_reason="end_turn"),
               sg.plan_episode("q", 2), sg.ScriptNotes())
    _write(_writer(""), sg.plan_episode("q", 2), sg.ScriptNotes())
    report = _refusals()
    assert report["writer_calls"] == 4 and report["writer_refused"] == 1
    assert report["writer_rate"] == 0.25


def test_a_declined_batched_writer_is_recorded_as_ahead():
    gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    notes = sg.ScriptNotes()
    notes.edition = True
    gen.sentences_from_message(sg.plan_episode("q", 2), notes, _Final())
    row = _refusals()["recent"][0]
    assert row["call"] == "writer (batch)" and row["path"] == "ahead"


def test_a_declined_evidence_cut_is_recorded():
    async def create(**kwargs):
        block = types.SimpleNamespace(type="text", text=json.dumps({"keep": [1]}))
        reply = _Final()
        reply.content = [block]
        return reply

    packet = ("SOURCE 1\nTitle: t\nKey evidence:\nThe first sentence of the "
              "passage is here. The second sentence of the passage follows.\n")
    plan = types.SimpleNamespace(query="q", evidence=packet, live=None,
                                 brief=types.SimpleNamespace(must_establish=[],
                                                             live_domain="",
                                                             live_route=""))
    client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    result = asyncio.run(ed.digest(plan, client=client))
    assert result.outcome == "refused" and result.text == ""
    assert _refusals()["recent"][0]["call"] == "evidence cut"


def test_a_declined_brief_part_is_recorded(monkeypatch):
    import episode_intelligence as ei

    async def create(**kwargs):
        return _Final()

    client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    kind, _, _ = asyncio.run(ei._ask(client, "understand", "q", 2, "", "now", (),
                                     5.0, None, "search"))
    assert kind == "refused"
    row = _refusals()["recent"][0]
    assert row["call"] == "brief: understand" and row["path"] == "search"


def test_nothing_declined_is_an_empty_report():
    report = _refusals()
    assert report["week"] == 0 and report["recent"] == []


def test_the_errors_page_has_a_refusals_subsection():
    html = open(os.path.join(ROOT, "admin_ui", "tracker.html"), encoding="utf-8").read()
    page = html.split('data-page="errors"', 1)[1].split('data-page="services"', 1)[0]
    assert 'id="refusalsBox"' in page and 'id="refusals"' in page
    assert "drawRefusals(s.pipeline_errors)" in html
    for header in ("Call", "Category", "Reason given", "Spoken first", "Question"):
        assert f"<th>{header}</th>" in html
