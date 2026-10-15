"""The writer's ceiling, its timing, and every step's on /admin (§271).

* A writer that reaches `MAX_OUTPUT_TOKENS` is an error on the errors page,
  and the half-sentence it stopped in is never spoken.
* `/api/health` → `writer` says how each writer call ended and how long it
  took to its first word and its last, by who was waiting.
* Every measured step is stored beside the errors, and the errors page has
  a subsection that reads it: counts, averages, percentiles, failure rate.

No key and no network: the writer is a stub stream.
"""
from __future__ import annotations

import asyncio
import collections
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline_errors  # noqa: E402
import script_generator as sg  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Final:
    def __init__(self, stop_reason="end_turn"):
        self.stop_reason = stop_reason
        self.usage = types.SimpleNamespace(input_tokens=100, output_tokens=50,
                                           cache_read_input_tokens=0,
                                           cache_creation_input_tokens=0)
        self.content = [types.SimpleNamespace(type="text", text="")]
        self.model = "claude-haiku-5-5"


def _writer(text: str, stop_reason="end_turn", explode=False):
    gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)

    class _Stream:
        async def __aenter__(self):
            if explode:
                raise RuntimeError("the writer fell over")
            return self

        async def __aexit__(self, *exc):
            return False

        @property
        def text_stream(self):
            async def chunks():
                for word in text.split(" "):
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


def _fresh(monkeypatch):
    monkeypatch.setattr(sg, "_WRITER_BY_PATH", {"search": collections.Counter(),
                                                "ahead": collections.Counter()})
    monkeypatch.setattr(sg, "_WRITER_FIRST_WORD", {
        "search": collections.deque(maxlen=10), "ahead": collections.deque(maxlen=10)})
    monkeypatch.setattr(sg, "_WRITER_TOTAL", {
        "search": collections.deque(maxlen=10), "ahead": collections.deque(maxlen=10)})


def test_a_writer_out_of_room_is_an_error_and_never_speaks_half_a_sentence(monkeypatch):
    _fresh(monkeypatch)
    plan = sg.plan_episode("what did the fed do", 2)
    notes = sg.ScriptNotes()
    said = _write(_writer("The Fed held rates on Wednesday. It said inflation "
                          "was cooling and that the next", "max_tokens"), plan, notes)
    assert said == ["The Fed held rates on Wednesday."]
    assert sg.writer_report()["since_boot_by_path"]["search"] == {"out_of_room": 1}
    report = pipeline_errors.report()
    kinds = {(k["stage"], k["kind"]): k for k in report["kinds"]}
    row = kinds[("writer", "ran out of room")]
    assert row["severity"] == "error" and "MAX_OUTPUT_TOKENS" in row["last_detail"]


def test_an_ordinary_ending_is_spoken_whole_and_counted_ok(monkeypatch):
    _fresh(monkeypatch)
    plan = sg.plan_episode("what did the fed do", 2)
    said = _write(_writer("The Fed held rates. It meets again in December"),
                  plan, sg.ScriptNotes())
    assert said[-1] == "It meets again in December"
    health = sg.writer_report()
    assert health["since_boot_by_path"]["search"] == {"ok": 1}
    assert health["first_word_seconds_by_path"]["search"]["n"] == 1
    assert health["total_seconds_by_path"]["search"]["n"] == 1
    assert health["max_output_tokens"] > 0


def test_a_writer_nobody_waits_on_is_counted_ahead(monkeypatch):
    _fresh(monkeypatch)
    notes = sg.ScriptNotes()
    notes.edition = True
    _write(_writer("A whole sentence."), sg.plan_episode("q", 2), notes)
    assert sg.writer_report()["since_boot_by_path"]["ahead"] == {"ok": 1}


def test_a_failed_writer_is_counted_failed(monkeypatch):
    _fresh(monkeypatch)
    try:
        _write(_writer("", explode=True), sg.plan_episode("q", 2), sg.ScriptNotes())
    except RuntimeError:
        pass
    assert sg.writer_report()["since_boot_by_path"]["search"] == {"failed": 1}


def test_a_batched_writer_out_of_room_is_caught_too(monkeypatch):
    _fresh(monkeypatch)
    gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    message = _Final("max_tokens")
    message.content = [types.SimpleNamespace(type="text",
                                             text="One whole sentence. And half")]
    notes = sg.ScriptNotes()
    notes.edition = True
    out = gen.sentences_from_message(sg.plan_episode("q", 2), notes, message)
    assert out == ["One whole sentence."]
    assert sg.writer_report()["since_boot_by_path"]["ahead"] == {"out_of_room": 1}


def test_the_writer_is_in_health():
    source = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert '"writer": writer_report(),' in source
    report = sg.writer_report()
    for key in ("model", "effort", "since_boot_by_path",
                "first_word_seconds_by_path", "total_seconds_by_path"):
        assert key in report


def test_every_step_is_stored_and_reported_by_path():
    for seconds, outcome in ((1.0, "ok"), (2.0, "ok"), (9.0, "timeout")):
        pipeline_errors.step("brief", "search", outcome, seconds)
    pipeline_errors.step("brief", "ahead", "ok", 12.0)
    pipeline_errors.step("writer", "ahead", "ok", None)
    steps = {(x["step"], x["path"]): x for x in pipeline_errors.report()["steps"]}
    brief = steps[("brief", "search")]
    assert brief["week"] == 3 and brief["day"] == 3 and brief["hour"] == 3
    assert brief["mean"] == 4.0 and brief["median"] == 2.0 and brief["max"] == 9.0
    assert brief["failed"] == 1 and brief["failure_rate"] == round(1 / 3, 4)
    assert brief["outcomes"] == {"ok": 2, "timeout": 1}
    assert brief["label"] == "Brief (episode intelligence)"
    assert steps[("brief", "ahead")]["week"] == 1
    # A step with no time (a batched writer) is counted, never averaged.
    assert steps[("writer", "ahead")]["mean"] is None
    # In the order a listener waits through them.
    order = [x["step"] for x in pipeline_errors.report()["steps"]]
    assert order.index("brief") < order.index("writer")


def test_steps_alone_make_a_report_and_a_wipe_clears_them():
    pipeline_errors.step("first audio", "listener", "ok", 7.5)
    assert pipeline_errors.report()["steps"][0]["step"] == "first audio"
    pipeline_errors.flush()
    pipeline_errors.store().clear()
    assert pipeline_errors.report()["steps"] == []


def test_the_errors_page_has_its_own_timing_subsection():
    html = open(os.path.join(ROOT, "admin_ui", "tracker.html"), encoding="utf-8").read()
    errors_page = html.split('data-page="errors"', 1)[1].split('data-page="services"', 1)[0]
    assert 'id="pipelineStepsBox"' in errors_page and 'id="pipelineSteps"' in errors_page
    assert "Timing and health, by step" in errors_page
    assert "drawSteps(s.pipeline_errors)" in html
    for header in ("Average", "Median", "p90", "Slowest", "Failure rate", "How it ended"):
        assert f"<th>{header}</th>" in html


def test_a_script_fam_stopped_at_its_budget_is_not_out_of_room(monkeypatch):
    """Review, §273: the budget valve stopping a writer is FAM's choice; a
    `max_tokens` the model reached after it lost nothing FAM meant to say."""
    _fresh(monkeypatch)
    plan = sg.plan_episode("q", 1)
    words = " ".join(["This is one more full sentence."] * 120)
    _write(_writer(words, "max_tokens"), plan, sg.ScriptNotes())
    assert sg.writer_report()["since_boot_by_path"]["search"] == {"over_budget": 1}
    kinds = {(k["stage"], k["kind"]) for k in pipeline_errors.report()["kinds"]}
    assert ("writer", "ran out of room") not in kinds


def test_the_report_reads_a_bounded_number_of_steps(monkeypatch):
    monkeypatch.setattr(pipeline_errors, "STEPS_READ", 3)
    for _ in range(5):
        pipeline_errors.step("brief", "search", "ok", 1.0)
    report = pipeline_errors.report()
    assert report["steps"][0]["week"] == 3 and report["steps_capped"] is True
