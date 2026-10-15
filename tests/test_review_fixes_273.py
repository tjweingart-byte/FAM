"""Tests for the five review fixes (§273) that had none of their own.

* the live lookup is timed by its own finish, not the search's beside it;
* a listener who leaves during the forecast wait takes the evidence cut with
  them;
* `/api/health` says when the configured writer was refused and the writer
  fell back to `MODEL`;
* the errors page says when its timings are over the newest steps only;
* `write.py --compare` prints the writer's cache reads and writes.

No key and no network: every model and lookup is a stub.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import live_facts  # noqa: E402
import pipeline_errors  # noqa: E402
import script_generator as sg  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKET = ("SOURCE 1\nTitle: t\nKey evidence:\nThe first sentence of the passage "
          "is here. The second sentence of the passage follows.\n")


def _brief():
    return types.SimpleNamespace(live_domain="sports", live_route="",
                                 live_entity="Kansas City Chiefs",
                                 outcome_dependent=False, recency_days=0,
                                 intent="recap", must_establish=[], place="",
                                 pronounce=[], subject="the Chiefs")


class _Gen(sg.ScriptGenerator):
    """`prepare` with every outside call stubbed, each at its own pace."""

    def __init__(self, research=0.0, live=0.0, forecast=0.0):
        self.delays = {"research": research, "live": live, "forecast": forecast}

    async def understand(self, plan, notes=None):
        return dataclasses.replace(plan, brief=_brief())

    async def live_lookup(self, plan, notes=None):
        await asyncio.sleep(self.delays["live"])
        return dataclasses.replace(plan, live=live_facts.LiveLookup(
            "sports", live_facts.NOT_CONFIGURED))

    async def forecast_lookup(self, plan, notes=None):
        await asyncio.sleep(self.delays["forecast"])
        return types.SimpleNamespace(forecast=None)

    async def research(self, plan, notes=None):
        await asyncio.sleep(self.delays["research"])
        return dataclasses.replace(plan, evidence=PACKET)


def _steps(name):
    return [s for s in pipeline_errors.report()["steps"] if s["step"] == name]


# -- 1. the live lookup's own time ------------------------------------------
def test_the_live_lookup_is_timed_by_its_own_finish():
    """A lookup that answers in 0.05 s beside a 0.4 s search is a 0.05 s
    lookup - it used to be recorded as the search's 0.4 s."""
    asyncio.run(_Gen(research=0.4, live=0.05).prepare(
        sg.plan_episode("did the chiefs win", 2), sg.ScriptNotes()))
    rows = _steps("live lookup")
    assert len(rows) == 1 and rows[0]["outcomes"] == {live_facts.NOT_CONFIGURED: 1}
    assert rows[0]["max"] < 0.25, rows[0]
    # The search beside it keeps its own, longer time.
    assert _steps("search")[0]["max"] >= 0.35


def test_a_lookup_slower_than_the_search_keeps_its_own_time_too():
    asyncio.run(_Gen(research=0.05, live=0.3).prepare(
        sg.plan_episode("did the chiefs win", 2), sg.ScriptNotes()))
    assert _steps("live lookup")[0]["max"] >= 0.25


# -- 2. the evidence cut cancelled with the listener ------------------------
def test_a_listener_who_leaves_during_the_forecast_wait_takes_the_cut(monkeypatch):
    seen: dict = {}

    async def slow_digest(plan, notes=None, ahead=False, client=None):
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            seen["cancelled"] = True
            raise
        seen["finished"] = True

    monkeypatch.setattr(sg.evidence_digest, "digest", slow_digest)
    monkeypatch.setattr(sg.evidence_digest, "wanted", lambda plan: True)
    # Long enough that the listener leaves while it is being waited for.
    monkeypatch.setattr(sg, "FORECAST_GRACE_SECONDS", 5.0)

    async def leave():
        task = asyncio.ensure_future(_Gen(forecast=5.0).prepare(
            sg.plan_episode("did the chiefs win", 2), sg.ScriptNotes()))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.05)
        # Read before `asyncio.run` ends: on its way out it cancels every
        # task still running, which would hide a cut left behind.
        return dict(seen)

    assert asyncio.run(leave()) == {"cancelled": True}, (
        "the cut ran on after the listener left")


# -- 3. health says the writer fell back ------------------------------------
def test_health_says_when_the_writer_fell_back_to_model(monkeypatch):
    from fastapi.testclient import TestClient

    import app as appmod

    monkeypatch.setattr(appmod, "SCRIPT_CACHE", None)
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    client = TestClient(appmod.app)

    monkeypatch.setattr(sg, "UNAVAILABLE_MODELS", set())
    body = client.get("/api/health").json()
    assert body["writer_model"] == config.settings.writer_model
    assert body["writer_model_source"] == "config.py default"

    monkeypatch.setattr(sg, "UNAVAILABLE_MODELS", {config.settings.writer_model})
    body = client.get("/api/health").json()
    assert body["writer_model"] == config.settings.model
    assert body["writer_model_source"] == (
        "config.py default (unavailable to this account; fell back to MODEL)")
    assert body["writer"]["model"] == config.settings.model


# -- 4. the errors page says its timings are capped -------------------------
def _draw_steps(report: dict) -> str:
    """Run the admin page's own `drawSteps` on a report and return the HTML
    it writes - the function as shipped, read out of the page."""
    html = open(os.path.join(ROOT, "admin_ui", "tracker.html"), encoding="utf-8").read()
    paths = re.search(r"var STEP_PATHS = \{.*?\};", html).group(0)
    start = html.index("  function drawSteps(r) {")
    end = html.index("\n  }\n", start) + len("\n  }\n")
    script = (
        "var out = {};\n"
        "function $(id) { return out[id] = out[id] || {}; }\n"
        "function esc(s) { return String(s == null ? '' : s).replace(/[&<>\"']/g, "
        "function (c) { return '&#' + c.charCodeAt(0) + ';'; }); }\n"
        + paths + "\n" + html[start:end] + "\n"
        f"drawSteps({json.dumps(report)});\n"
        "process.stdout.write(out.pipelineSteps.innerHTML);\n")
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                          timeout=30)
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_errors_page_says_when_its_timings_are_the_newest_only(monkeypatch):
    monkeypatch.setattr(pipeline_errors, "STEPS_READ", 2)
    for _ in range(3):
        pipeline_errors.step("brief", "search", "ok", 1.0)
    capped = pipeline_errors.report()
    assert capped["steps_capped"] is True
    assert "newest 50,000 measured steps" in _draw_steps(capped)

    monkeypatch.setattr(pipeline_errors, "STEPS_READ", 50_000)
    whole = pipeline_errors.report()
    assert whole["steps_capped"] is False
    drawn = _draw_steps(whole)
    assert "Brief (episode intelligence)" in drawn
    assert "newest 50,000" not in drawn


# -- 5. --compare prints cache reads and writes -----------------------------
def test_compare_prints_the_writer_s_cache_reads_and_writes(capsys):
    import write

    class _Writer:
        async def stream_prepared(self, plan, notes):
            notes.usage.add_model_call("claude-haiku-5-5", types.SimpleNamespace(
                input_tokens=1200, output_tokens=900,
                cache_read_input_tokens=3500, cache_creation_input_tokens=0))
            yield "A whole sentence."

    plan = sg.plan_episode("why is the sky blue", 2, search=False)
    asyncio.run(write.compare(_Writer(), plan, "claude-haiku-5-5:low"))
    out = capsys.readouterr().out
    assert ("writer read 4700 tokens (3500 from cache, 0 written to it), "
            "wrote 900 (thinking + script)") in out
