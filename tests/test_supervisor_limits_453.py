"""Mclovin's per-sentence limits, the owner's (§453): 2 s and 500 tokens.

At 1 s and 300 tokens most of his second looks timed out or ran out of room
before answering (§452). Support stays off; these are the limits it will
have when the owner revisits it.
"""
import os
import re

from config import Settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_defaults_are_the_owners(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("SUPERVISOR_MAX_TOKENS", raising=False)
    s = Settings()
    assert s.supervisor_timeout_seconds == 2.0
    assert s.supervisor_max_tokens == 500


def test_env_example_agrees():
    text = open(os.path.join(ROOT, ".env.example")).read()
    assert re.search(r"^SUPERVISOR_TIMEOUT_SECONDS=2$", text, re.M)
    assert re.search(r"^SUPERVISOR_MAX_TOKENS=500$", text, re.M)


def test_support_stays_off():
    from agents import grounding

    assert not str(Settings().supervisor_grounding or "").strip()
    assert grounding.scorer() is None
