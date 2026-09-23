"""The writing call thinks at `low` effort by default (PROBLEMS.md §129).

Pinned because it is a decision, reversing §108's `high`: the hidden thinking
was the largest wait in front of the first word on search. The test checks the
value that reaches the request, not only the setting, and that a running
server says which value is in force - an EFFORT left in a deploy's dashboard
would otherwise undo this silently.
"""
from __future__ import annotations

import config
from script_generator import ScriptGenerator, plan_episode


def _kwargs():
    generator = ScriptGenerator.__new__(ScriptGenerator)
    plan = plan_episode("how does a heat pump work", 2, search=False)
    return generator._request_kwargs(plan)


def test_the_default_is_low(monkeypatch):
    monkeypatch.delenv("EFFORT", raising=False)
    assert config.Settings().effort == "low"


def test_the_writing_request_carries_it():
    assert _kwargs()["output_config"]["effort"] == config.settings.effort


def test_an_environment_value_still_wins(monkeypatch):
    monkeypatch.setenv("EFFORT", "high")
    assert config.Settings().effort == "high"


def test_the_order_is_unchanged_only_the_budget_is_smaller():
    """§108's ordering rules stay: no tools on the call that speaks, and the
    prompt still asks for the whole piece to be decided before it opens."""
    kwargs = _kwargs()
    assert "tools" not in kwargs
    from script_generator import system_prompt, build_prompt
    text = system_prompt() + build_prompt(plan_episode("x", 2, search=False))
    assert "Decide the whole piece before you write the first word" in text


def test_health_says_which_effort_is_in_force_and_why(monkeypatch):
    from fastapi.testclient import TestClient
    import app as appmod

    monkeypatch.delenv("EFFORT", raising=False)
    body = TestClient(appmod.app).get("/api/health").json()
    assert body["writer_effort"] == config.settings.effort
    assert body["writer_effort_source"] == "config.py default"
    monkeypatch.setenv("EFFORT", "high")
    body = TestClient(appmod.app).get("/api/health").json()
    assert body["writer_effort_source"] == "EFFORT env var"
