"""The evidence cut to what the episode can use (§270).

A Haiku call with thinking off chooses which of the packet's sentences the
writer reads. Pinned here, because each is a way the cut could cost an
episode something:

* the model answers with numbers and its words never reach the writer;
* a source it chose nothing from keeps its passages whole;
* every failure - timeout, error, refusal, out of room, nonsense - leaves the
  packet as it was, and a cut that loses what the brief asked to establish,
  or saves too little, is not made;
* every figure stays on a scores or markets episode;
* the score and figure checks still read the whole packet;
* off by default, and small packets are never cut.

No key and no network: the client is a stub.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import evidence_digest as ed  # noqa: E402
import figure_guard  # noqa: E402
import script_generator as sg  # noqa: E402

PACKET = """SOURCE 1
Title: Fed holds rates
Published: 2026-10-14 (yesterday)
Source type: wire service
Key evidence:
The Federal Reserve held its benchmark rate at 4.25 percent on Wednesday. Subscribe to our newsletter for more updates every day.
Officials said inflation is expected to cool next year. Read more stories like this one on our site.

SOURCE 2
Title: Markets react
Published: 2026-10-14 (yesterday)
Source type: national newspaper
Key evidence:
Click here to see all the related coverage from this week.
Stocks rose 1.2 percent after the decision was announced.

WHAT FAM MEASURED
Measured: today (today)
Key evidence:
Twelve outlets ran this story.
"""


def _plan(evidence=PACKET, must=(), domain=""):
    brief = types.SimpleNamespace(must_establish=list(must), live_domain=domain,
                                  live_route="")
    return types.SimpleNamespace(query="what did the fed do", evidence=evidence,
                                 brief=brief, live=None)


def _client(answer=None, stop="end_turn", raises=None, delay=0.0):
    async def create(**kwargs):
        _client.kwargs = kwargs
        if delay:
            await asyncio.sleep(delay)
        if raises is not None:
            raise raises
        block = types.SimpleNamespace(type="text", text=json.dumps(answer))
        return types.SimpleNamespace(content=[block], stop_reason=stop, usage=None)
    return types.SimpleNamespace(messages=types.SimpleNamespace(create=create))


def _run(plan, client, **kw):
    return asyncio.run(ed.digest(plan, client=client, **kw))


def _numbers(*wanted):
    parsed = ed.parse(PACKET)
    return [n for n, (_, _, t) in enumerate(parsed.pieces, 1)
            if any(w in t for w in wanted)]


def test_off_by_default_and_small_packets_are_never_cut():
    assert config.settings.evidence_digest is False
    on = dataclasses.replace(config.settings, evidence_digest=True)
    original = ed.settings
    try:
        ed.settings = on
        assert ed.wanted(_plan()) is False          # under the minimum
        ed.settings = dataclasses.replace(on, evidence_digest_min_chars=10)
        assert ed.wanted(_plan()) is True
        assert ed.wanted(_plan(evidence="")) is False
    finally:
        ed.settings = original


def test_the_cut_keeps_only_the_sources_own_sentences_under_their_headers():
    keep = _numbers("4.25 percent", "inflation", "Stocks rose")
    result = _run(_plan(), _client({"keep": keep}))
    assert result.outcome == "cut", result.detail
    text = result.text
    assert "Subscribe" not in text and "Click here" not in text
    for line in ("Title: Fed holds rates", "Published: 2026-10-14 (yesterday)",
                 "Source type: wire service", "WHAT FAM MEASURED",
                 "Twelve outlets ran this story."):
        assert line in text
    # Every sentence kept is one the packet holds, word for word.
    body = [ln for ln in text.splitlines() if ln and ":" not in ln[:12]
            and not ln.startswith("SOURCE")]
    for ln in body:
        assert ln in " ".join(PACKET.split()) or ln in PACKET
    assert result.kept_chars < result.raw_chars


def test_thinking_is_off_and_the_answer_is_numbers():
    _run(_plan(), _client({"keep": _numbers("4.25")}))
    assert _client.kwargs["thinking"] == {"type": "disabled"}
    assert _client.kwargs["output_config"]["format"]["schema"]["required"] == ["keep"]


def test_a_source_with_nothing_chosen_keeps_its_passages_whole():
    result = _run(_plan(), _client({"keep": _numbers("4.25", "inflation")}))
    assert "Click here" in result.text and "Stocks rose" in result.text


def test_every_failure_leaves_the_packet_as_it_was():
    cases = [
        (_client(raises=RuntimeError("boom")), "failed"),
        (_client({"keep": [1]}, stop="refusal"), "refused"),
        (_client({"keep": [1]}, stop="max_tokens"), "out_of_room"),
        (_client("not an object"), "unreadable"),
        (_client({"keep": []}), "nothing_chosen"),
        (_client({"keep": [999]}), "nothing_chosen"),
    ]
    for client, outcome in cases:
        result = _run(_plan(), client)
        assert result.outcome == outcome
        assert result.text == ""


def test_a_slow_call_is_abandoned_at_its_ceiling():
    original = ed.settings
    try:
        ed.settings = dataclasses.replace(config.settings,
                                          evidence_digest_timeout_seconds=0.05)
        result = _run(_plan(), _client({"keep": [1]}, delay=1.0))
    finally:
        ed.settings = original
    assert result.outcome == "timeout" and result.text == ""


def test_a_cut_that_saves_little_is_not_made():
    everything = list(range(1, len(ed.parse(PACKET).pieces) + 1))
    result = _run(_plan(), _client({"keep": everything}))
    assert result.outcome == "no_gain" and result.text == ""


def test_a_cut_that_loses_what_the_brief_asked_for_is_not_made():
    result = _run(_plan(must=["inflation expected next year"]),
                  _client({"keep": _numbers("4.25", "Stocks rose")}))
    assert result.outcome == "lost_coverage" and result.text == ""


def test_every_figure_stays_on_a_markets_episode():
    result = _run(_plan(domain="markets"),
                  _client({"keep": _numbers("inflation")}))
    assert result.outcome == "cut"
    assert "4.25 percent" in result.text and "1.2 percent" in result.text


def test_the_writer_reads_the_cut_and_the_checks_read_the_whole_packet():
    plan = sg.plan_episode("what did the fed do", 2)
    cut = "SOURCE 1\nTitle: Fed holds rates\nKey evidence:\nThe Fed held.\n"
    plan = dataclasses.replace(plan, evidence=PACKET, evidence_digest=cut)
    prompt = sg.build_prompt(plan)
    assert "The Fed held." in prompt and "Subscribe to our newsletter" not in prompt
    assert "Stocks rose 1.2 percent" in figure_guard._material(plan)
    # Off, the prompt reads the packet whole, as it always did.
    whole = sg.build_prompt(dataclasses.replace(plan, evidence_digest=""))
    assert "Subscribe to our newsletter" in whole


def test_failures_are_counted_by_path_and_noticed():
    before = dict(ed._BY_PATH["ahead"])
    _run(_plan(), _client(raises=RuntimeError("boom")), ahead=True)
    assert ed._BY_PATH["ahead"]["failed"] == before.get("failed", 0) + 1
    report = ed.report()
    assert report["thinking"] == "off" and "seconds_by_path" in report


def test_a_packet_with_no_sources_is_left_alone():
    result = _run(_plan(evidence="Some local notes with no source blocks."),
                  _client({"keep": [1]}))
    assert result.outcome == "no_gain" and result.text == ""
