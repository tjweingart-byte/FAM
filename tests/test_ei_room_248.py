"""The brief has room to finish, and says when it does not (§248).

`EI_MAX_TOKENS` bounds the brief's thinking and its JSON together. It was
1200, sized on Sonnet 5; on Haiku 5.5 a brief that thinks longer is cut off
mid-object, searched as the raw query, and logged as "nothing readable". These
pin the larger ceiling, a reason that names the setting, and a count of every
degradation in /api/health so the rate can be read rather than guessed.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import episode_intelligence as ei  # noqa: E402
from tests.test_episode_intelligence import GOOD, fake_reply  # noqa: E402


@pytest.fixture
def model(monkeypatch):
    state = {"reply": fake_reply(GOOD), "raise": None}

    class FakeMessages:
        async def create(self, **kwargs):
            state["kwargs"] = kwargs
            if state["raise"] is not None:
                raise state["raise"]
            return state["reply"]

    monkeypatch.setattr(ei, "build_async_client",
                        lambda key=None: types.SimpleNamespace(messages=FakeMessages()))
    monkeypatch.setattr(ei.credentials, "active", lambda name: "sk-test")
    monkeypatch.setattr(ei, "_OUTCOMES", __import__("collections").Counter())
    return state


def test_the_ceiling_has_room_for_haikus_thinking():
    """1200 was Sonnet's, and Sonnet's own briefs sometimes reached it."""
    assert config.Settings().ei_max_tokens == 3000


def test_the_call_asks_for_the_ceiling(model):
    asyncio.run(ei.understand("who won the 49ers game", 3))
    assert model["kwargs"]["max_tokens"] == config.settings.ei_max_tokens


def test_a_brief_cut_off_by_the_ceiling_says_so(model):
    """Truncated JSON used to read as 'nothing readable', which hides that
    the cure is a setting."""
    reply = fake_reply(GOOD, stop_reason="max_tokens")
    reply.content[0].text = reply.content[0].text[:40]
    model["reply"] = reply
    brief = asyncio.run(ei.understand("who won the 49ers game", 3))
    assert brief.degraded
    assert "EI_MAX_TOKENS" in brief.notes[0]
    assert ei.report()["since_boot"] == {"out_of_room": 1}


def test_every_ending_is_counted_by_its_reason(model):
    asyncio.run(ei.understand("who won the 49ers game", 3))
    model["reply"] = fake_reply(GOOD, stop_reason="refusal")
    asyncio.run(ei.understand("who won the 49ers game", 3))
    model["reply"] = types.SimpleNamespace(content=[], usage=None,
                                           stop_reason="end_turn")
    asyncio.run(ei.understand("who won the 49ers game", 3))
    model["raise"] = RuntimeError("boom")
    asyncio.run(ei.understand("who won the 49ers game", 3))
    assert ei.report()["since_boot"] == {
        "ok": 1, "refused": 1, "unreadable": 1, "failed": 1}


def test_a_brief_that_was_never_asked_for_is_not_counted(model, monkeypatch):
    """EPISODE_INTELLIGENCE=0 and an empty query are choices, not failures."""
    asyncio.run(ei.understand("", 3))
    monkeypatch.setattr(ei, "settings",
                        __import__("dataclasses").replace(
                            ei.settings, episode_intelligence=False))
    asyncio.run(ei.understand("who won", 3))
    assert ei.report()["since_boot"] == {}


def test_health_reports_the_ceiling(model):
    assert ei.report()["max_tokens"] == config.settings.ei_max_tokens
