"""Preparing the writer for Haiku, and one page for everything that broke (§269).

Pinned here:

* **The figure check** - a markets episode's money, percentages and points are
  held to the material before they are spoken, rounded the way a person
  rounds; history and every other kind of episode are left alone.
* **The editions' own writer** - `EDITION_WRITER_MODEL` / `_EFFORT` reach only
  an episode an edition writes, its billing follows the model actually used,
  an unavailable one falls back to `MODEL`, and nothing changes while unset.
* **`write.py --compare`** - reads "MODEL[:EFFORT],...".
* **Pipeline errors** - a store that records without waiting or raising, a
  report with counts by hour/day/week and by kind, every hook that feeds it,
  and the admin page that draws it.

No key, no network.
"""
from __future__ import annotations

import asyncio
import collections
import dataclasses
import json
import os
import sys
import time
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import figure_guard  # noqa: E402
import live_facts  # noqa: E402
import pipeline_errors  # noqa: E402
import script_generator as sg  # noqa: E402
from script_generator import ScriptNotes, plan_episode  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# the figure check
# --------------------------------------------------------------------------
@pytest.mark.parametrize("said, expected", [
    ("Nvidia is at one hundred eighty-two dollars and forty cents.", [(182.4, 2, "money")]),
    ("Shares rose two point one percent.", [(2.1, 1, "percent")]),
    # Said to the hundred billion: `round` takes -11 (review, §269).
    ("It is worth four point five trillion dollars.", [(4.5e12, -11, "money")]),
    ("The index closed at five thousand eight hundred and twelve points.",
     [(5812.0, 0, "points")]),
    ("It hit $190 this morning.", [(190.0, 0, "money")]),
    ("The Fed moved twenty-five basis points.", [(0.25, 2, "percent")]),
    ("The Bills beat the Rams twenty-four to seventeen.", []),
    ("A hundred dollars buys less than it did.", [(100.0, 0, "money")]),
])
def test_figures_are_read_as_a_voice_script_says_them(said, expected):
    assert figure_guard.figures_in(said) == expected


def _guard(material: str) -> figure_guard.FigureGuard:
    live = types.SimpleNamespace(domain="markets",
                                 as_prompt_block=lambda: material)
    plan = types.SimpleNamespace(brief=ei.Brief(live_domain="markets"),
                                 live=live, evidence="", forecast=None,
                                 readable=[])
    return figure_guard.FigureGuard(plan)


MATERIAL = ("NVIDIA is trading at 182.43. That is up about 2.1 percent on "
            "the day. Its market value is $4.5 trillion.")


@pytest.mark.parametrize("sentence", [
    "Nvidia is trading at one hundred eighty-two dollars, up two point one percent.",
    "Shares sit at about one hundred eighty dollars.",
    "That makes it a four point five trillion dollar company.",
    "In 2021 the shares peaked at three hundred dollars.",
    "Last year they fell forty percent.",
    "The chips are in every data centre.",
])
def test_what_the_material_says_or_history_is_spoken(sentence):
    assert _guard(MATERIAL).why_not(sentence) == ""


@pytest.mark.parametrize("sentence", [
    "Shares are at two hundred dollars right now.",
    "They are up seven percent today.",
])
def test_a_figure_the_material_does_not_state_is_removed(sentence):
    guard = _guard(MATERIAL)
    assert not guard.allow(sentence)
    assert guard.dropped and "not in the material" in guard.dropped[0]


def test_with_no_material_every_current_figure_goes_and_one_line_stays():
    guard = _guard("")
    assert not guard.allow("Nvidia trades at one hundred eighty dollars today.")
    assert guard.rescue().startswith("We can't give you a current figure")


def test_only_a_markets_episode_is_checked():
    plan = types.SimpleNamespace(brief=ei.Brief(live_domain="sports"),
                                 live=None, evidence="", forecast=None, readable=[])
    guard = figure_guard.FigureGuard(plan)
    assert not guard.active
    assert guard.why_not("Tickets cost two hundred dollars.") == ""
    routed = types.SimpleNamespace(brief=ei.Brief(live_route="markets"),
                                   live=None, evidence="", forecast=None, readable=[])
    assert figure_guard.FigureGuard(routed).active, "§265's code route counts"


def test_the_evidence_vouches_as_well_as_the_live_block():
    plan = types.SimpleNamespace(
        brief=ei.Brief(live_domain="markets"), live=None, forecast=None,
        readable=[], evidence="SOURCE 1\nKey evidence:\nShares closed at $182.43.")
    guard = figure_guard.FigureGuard(plan)
    assert guard.why_not("They closed at one hundred eighty-two dollars.") == ""


# --------------------------------------------------------------------------
# a stubbed writer
# --------------------------------------------------------------------------
class _Final:
    def __init__(self, stop_reason="end_turn"):
        self.stop_reason = stop_reason
        self.usage = types.SimpleNamespace(input_tokens=100, output_tokens=50,
                                           cache_read_input_tokens=0,
                                           cache_creation_input_tokens=0)
        self.content = []


def _writer(text: str, sent: list, explode=False, stop_reason="end_turn"):
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

    class _Messages:
        def stream(self, **kwargs):
            sent.append(kwargs)
            return _Stream()

    gen.client = types.SimpleNamespace(messages=_Messages())
    return gen


def _write(gen, plan, notes):
    async def collect():
        return [s async for s in gen.stream_prepared(plan, notes)]
    return asyncio.run(collect())


def test_the_figure_check_runs_on_the_writer_s_stream():
    sent: list = []
    gen = _writer("Nvidia shares sit at two hundred dollars right now. "
                  "The chips sell out every quarter.", sent)
    plan = dataclasses.replace(plan_episode("nvda stock", 2, search=False),
                               brief=ei.Brief(query="nvda stock",
                                              live_domain="markets"))
    notes = ScriptNotes()
    spoken = " ".join(_write(gen, plan, notes))
    assert "two hundred dollars" not in spoken
    assert "chips sell out" in spoken
    assert notes.figure_drops and "not in the material" in notes.figure_drops[0]


# --------------------------------------------------------------------------
# the editions' own writer
# --------------------------------------------------------------------------
def _settings(monkeypatch, **changes):
    monkeypatch.setattr(sg, "settings", dataclasses.replace(sg.settings, **changes))
    # The boot check fills this process-wide; a test that booted the app
    # before this one must not decide which writer it sees.
    monkeypatch.setattr(sg, "UNAVAILABLE_MODELS", set())


def test_unset_every_episode_writes_on_model(monkeypatch):
    _settings(monkeypatch, model="claude-sonnet-5", effort="low",
              edition_writer_model="", edition_writer_effort="")
    edition = ScriptNotes()
    edition.edition = True
    assert sg.writer_for(edition) == ("claude-sonnet-5", "low")
    assert sg.writer_for(ScriptNotes()) == ("claude-sonnet-5", "low")
    assert sg.writer_for(None) == ("claude-sonnet-5", "low")


def test_set_only_an_edition_s_episode_moves(monkeypatch):
    _settings(monkeypatch, model="claude-sonnet-5", effort="low",
              edition_writer_model="claude-haiku-5-5",
              edition_writer_effort="medium")
    edition = ScriptNotes()
    edition.edition = True
    assert sg.writer_for(edition) == ("claude-haiku-5-5", "medium")
    assert sg.writer_for(ScriptNotes()) == ("claude-sonnet-5", "low")
    prefetch = ScriptNotes()
    prefetch.prefetch = True
    assert sg.writer_for(prefetch) == ("claude-sonnet-5", "low"), (
        "a prefetched script is not an edition")


def test_the_request_and_the_bill_follow_the_edition_writer(monkeypatch):
    _settings(monkeypatch, model="claude-sonnet-5", effort="low",
              edition_writer_model="claude-haiku-5-5", edition_writer_effort="")
    sent: list = []
    gen = _writer("A short and complete story.", sent)
    notes = ScriptNotes()
    notes.edition = True
    _write(gen, plan_episode("why the sky is blue", 2, search=False), notes)
    assert sent[0]["model"] == "claude-haiku-5-5"
    assert sent[0]["output_config"]["effort"] == "low"
    assert notes.usage.model == "claude-haiku-5-5"
    params = gen._request_kwargs(plan_episode("x", 2, search=False), notes)
    assert params["model"] == "claude-haiku-5-5", "the batch sends the same"


def test_an_unavailable_edition_writer_falls_back_to_model(monkeypatch):
    _settings(monkeypatch, model="claude-sonnet-5",
              edition_writer_model="claude-haiku-5-5")
    monkeypatch.setattr(sg, "UNAVAILABLE_MODELS", {"claude-haiku-5-5"})
    edition = ScriptNotes()
    edition.edition = True
    assert sg.writer_for(edition)[0] == "claude-sonnet-5"


def test_the_editions_mark_their_episodes():
    for name in ("daily_edition.py", "trending_bank.py"):
        text = open(os.path.join(ROOT, name), encoding="utf-8").read()
        assert "notes.edition = True" in text, name


def test_edition_writer_settings_are_copied():
    fresh = config.Settings()
    assert fresh.edition_writer_model == "" and fresh.edition_writer_effort == ""
    text = open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read()
    assert "# EDITION_WRITER_MODEL=claude-haiku-5-5\n" in text
    assert "# EDITION_WRITER_EFFORT=low\n" in text


def test_write_py_reads_the_writers_to_compare():
    import write
    # Each writer also says whether it reads the evidence cut (§270).
    assert write.writers("claude-sonnet-5, claude-haiku-5-5:medium") == [
        ("claude-sonnet-5", config.settings.effort, False),
        ("claude-haiku-5-5", "medium", False)]
    assert write.writers("claude-haiku-5-5:low+digest") == [
        ("claude-haiku-5-5", "low", True)]
    assert write.writers("") == []


# --------------------------------------------------------------------------
# pipeline errors: the store and its report
# --------------------------------------------------------------------------
def _rows():
    pipeline_errors.flush()
    if not pipeline_errors.exists():
        return []
    return pipeline_errors.store().since(0)


def test_nothing_recorded_means_nothing_created():
    assert not pipeline_errors.exists()
    report = pipeline_errors.report()
    assert report["errors"] == {"hour": 0, "day": 0, "week": 0}
    assert report["recent"] == []
    assert not pipeline_errors.exists(), "reading never creates the store"


def test_a_record_is_kept_and_counted():
    pipeline_errors.record("brief", "timeout (search)", "EI did not answer",
                           "did the eagles win")
    pipeline_errors.record("writer", "score check removed 1 sentence",
                           "a score", "q", pipeline_errors.NOTICE)
    report = pipeline_errors.report()
    assert report["errors"]["hour"] == 1 and report["notices"]["hour"] == 1
    assert report["by_stage"]["brief"] == {"day": 1, "week": 1}
    first = report["kinds"][0]
    assert first["stage"] == "brief" and first["severity"] == "error", (
        "errors are listed before notices")
    assert first["last_query"] == "did the eagles win"
    assert {r["stage"] for r in report["recent"]} == {"brief", "writer"}


def test_old_rows_count_by_their_own_time():
    now = time.time()
    store = pipeline_errors.store()
    store.add([pipeline_errors.row("live", "x: timeout", at=now - 3 * 86400)],
              now=now)
    report = pipeline_errors.report(now)
    assert report["errors"] == {"hour": 0, "day": 0, "week": 1}


def test_recording_never_raises(monkeypatch):
    monkeypatch.setattr(pipeline_errors, "row",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    pipeline_errors.record("brief", "anything")


# --------------------------------------------------------------------------
# pipeline errors: what feeds it
# --------------------------------------------------------------------------
def test_a_timed_out_brief_is_recorded(monkeypatch):
    class _Messages:
        async def create(self, **kwargs):
            raise RuntimeError("down")

    monkeypatch.setattr(ei, "build_async_client",
                        lambda key=None: types.SimpleNamespace(messages=_Messages()))
    monkeypatch.setattr(ei.credentials, "active", lambda name: "sk-test")
    monkeypatch.setattr(ei, "_OUTCOMES", collections.Counter())
    asyncio.run(ei.understand("did the eagles win", 2))
    rows = _rows()
    assert any(r["stage"] == "brief" and r["kind"].startswith("failed")
               and r["query"] == "did the eagles win" for r in rows)


def test_a_provider_that_broke_is_recorded(monkeypatch):
    class Broken(live_facts.LiveSource):
        name = "test scoreboard"
        domain = "sports"

        def diagnose(self):
            return True, "ready"

        async def resolve(self, brief):
            return live_facts.Entity(domain="sports", provider=self.name,
                                     id="g", label="a game")

        async def fetch(self, entity):
            raise RuntimeError("refused: plan does not cover this league")

    monkeypatch.setattr(live_facts, "_SOURCES", [Broken()])
    live_facts.clear_caches()
    asyncio.run(live_facts.lookup(ei.Brief(query="who won",
                                           live_domain="sports")))
    live_facts.clear_caches()
    rows = _rows()
    assert any(r["stage"] == "live" and "provider_failed" in r["kind"]
               and r["severity"] == "error" for r in rows)


def test_a_writer_that_failed_or_refused_is_recorded():
    plan = plan_episode("why the sky is blue", 2, search=False)
    with pytest.raises(RuntimeError):
        _write(_writer("x", [], explode=True), plan, ScriptNotes())
    _write(_writer("Some words.", [], stop_reason="refusal"), plan, ScriptNotes())
    kinds = {r["kind"] for r in _rows() if r["stage"] == "writer"}
    assert any(k.endswith("failed") for k in kinds)
    assert "refused" in kinds


def test_a_check_that_removed_sentences_is_a_notice():
    gen = _writer("Shares sit at two hundred dollars right now. The end.", [])
    plan = dataclasses.replace(plan_episode("nvda stock", 2, search=False),
                               brief=ei.Brief(query="nvda stock",
                                              live_domain="markets"))
    _write(gen, plan, ScriptNotes())
    rows = [r for r in _rows() if r["stage"] == "writer"]
    assert rows and rows[0]["kind"] == "figure check removed 1 sentence"
    assert rows[0]["severity"] == "notice"


def test_every_hook_is_in_place():
    """The ones a unit test cannot reach without the whole server: read off
    the source, so removing one fails here."""
    expect = {
        "script_generator.py": ['"research", "no sources"', '"refused (batch)"'],
        "daily_edition.py": ['"DailyFAM episode failed"'],
        "trending_bank.py": ['"Trending episode failed"'],
        "app.py": ['"episode", "no voice"', '"failed before any audio"',
                   '"episode", "no speech"', '"stream broke mid-flight"'],
        "pipeline.py": ['"voice fell behind"'],
    }
    for name, needles in expect.items():
        text = open(os.path.join(ROOT, name), encoding="utf-8").read()
        for needle in needles:
            assert needle in text, (name, needle)


# --------------------------------------------------------------------------
# pipeline errors: the admin page
# --------------------------------------------------------------------------
def test_the_tracker_snapshot_carries_the_report():
    import admin_tracker
    pipeline_errors.record("research", "no sources", "every rung empty", "q")
    snap = admin_tracker.snapshot()
    assert snap["pipeline_errors"]["errors"]["hour"] == 1
    json.dumps(snap["pipeline_errors"])


def test_the_admin_page_draws_it():
    html = open(os.path.join(ROOT, "admin_ui", "tracker.html"), encoding="utf-8").read()
    assert 'data-page="errors"' in html and 'data-nav="errors"' in html
    assert 'id="pipelineErrors"' in html and 'id="navErrors"' in html
    assert "drawErrors(s.pipeline_errors)" in html


def test_labels_dates_and_clock_times_vouch_for_nothing():
    """`SOURCE 3`, a publication date and "as of 10:32" are the packet's own
    scaffolding: left in, they vouched for "up three percent" (review, §269)."""
    material = ("SOURCE 3\nTitle: Nvidia\nPublished: 2026-10-14 (yesterday)\n"
                "Key evidence:\nShares rose 2.1% to $182.43 on October 14, "
                "2026, as of 10:32.")
    guard = _guard(material)
    assert guard.why_not("Shares are up three percent today.")
    assert guard.why_not("They are up fourteen percent today.")
    assert guard.why_not("They rose two point one percent today.") == ""
    kept = figure_guard.material_values("It traded at $1999; the index hit 2015 points.")
    assert 1999.0 in kept and 2015.0 in kept, "a price is not a year"


# --------------------------------------------------------------------------
# what the pre-merge review found (§269)
# --------------------------------------------------------------------------
def test_a_scaled_figure_rounds_at_the_precision_it_was_said():
    guard = _guard("Apple's market value is 3,456,789,000,000 dollars.")
    assert guard.why_not("Apple is worth three point five trillion dollars.") == ""
    assert guard.why_not("Apple is worth four trillion dollars.")


def test_at_one_point_is_a_phrase_not_a_figure():
    assert figure_guard.figures_in("At one point, Nvidia was up nine percent.") == [
        (9.0, 0, "percent")]
    assert figure_guard.figures_in("The index closed at five thousand points.") == [
        (5000.0, -3, "points")]


def test_the_bill_names_the_model_the_request_was_sent_on(monkeypatch):
    """Read once from the request: a `writer_for` asked again after the call
    could name another model than the one that wrote."""
    _settings(monkeypatch, model="claude-sonnet-5",
              edition_writer_model="claude-haiku-5-5")
    sent: list = []
    gen = _writer("A short and complete story.", sent)
    notes = ScriptNotes()
    notes.edition = True

    async def collect():
        out = []
        async for sentence in gen.stream_prepared(
                plan_episode("why the sky is blue", 2, search=False), notes):
            # The fallback set changes while the writer is writing.
            sg.UNAVAILABLE_MODELS.add("claude-haiku-5-5")
            out.append(sentence)
        return out

    asyncio.run(collect())
    assert sent[0]["model"] == "claude-haiku-5-5"
    assert notes.usage.model == "claude-haiku-5-5"


def test_one_failure_is_one_error(monkeypatch):
    """The writer records its failure; whatever catches it further up records
    a notice, so the badge counts the incident once."""
    plan = plan_episode("why the sky is blue", 2, search=False)
    with pytest.raises(RuntimeError) as caught:
        _write(_writer("x", [], explode=True), plan, ScriptNotes())
    assert pipeline_errors.severity_for(caught.value) == pipeline_errors.NOTICE
    assert pipeline_errors.severity_for(RuntimeError("fresh")) == pipeline_errors.ERROR
    import daily_edition
    daily_edition._failure("why the sky is blue", caught.value)
    rows = _rows()
    assert sum(r["severity"] == "error" for r in rows) == 1
    assert any(r["stage"] == "edition" and r["severity"] == "notice" for r in rows)


def test_only_a_refused_model_is_marked_unavailable(monkeypatch):
    """A 429 or an overload at boot must not move every edition onto MODEL
    until the next deploy."""
    import anthropic
    import httpx2 as httpx

    import app

    def error(kind):
        request = httpx.Request("GET", "https://api.anthropic.com/v1/models/x")
        response = httpx.Response(429 if kind is anthropic.RateLimitError else 404,
                                  request=request)
        return kind("nope", response=response, body=None)

    answers = {"claude-haiku-5-5": error(anthropic.NotFoundError),
               "claude-opus-5": error(anthropic.RateLimitError)}

    class _Models:
        async def retrieve(self, model):
            if model in answers:
                raise answers[model]
            return {"id": model}

    monkeypatch.setattr(app, "build_async_client",
                        lambda *a, **k: types.SimpleNamespace(models=_Models()))
    monkeypatch.setitem(app.CREDENTIALS, "state", "ok")
    monkeypatch.setattr(app, "settings", dataclasses.replace(
        app.settings, model="claude-sonnet-5", ei_model="claude-opus-5",
        edition_writer_model="claude-haiku-5-5"))
    monkeypatch.setattr(sg, "UNAVAILABLE_MODELS", set())
    asyncio.run(app._verify_small_models())
    assert sg.UNAVAILABLE_MODELS == {"claude-haiku-5-5"}
