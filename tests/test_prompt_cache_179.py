"""The writer's instructions are marked cacheable (PROBLEMS.md §179).

The ~3,500 tokens of house rules and style example are the same on every
writer call. Marked with `cache_control`, a call inside the TTL of the last
one reads them at a tenth of the input price and starts sooner. What these pin
is the part that makes that true and the part that makes it safe: the cached
text is exactly `system_prompt()`, nothing that varies per episode is in it,
and `PROMPT_CACHE=0` sends the plain string as before.
"""
from __future__ import annotations

import dataclasses

import config
import script_generator
from script_generator import ScriptGenerator, plan_episode, system_prompt


def _kwargs(query="how does a heat pump work"):
    generator = ScriptGenerator.__new__(ScriptGenerator)
    return generator._request_kwargs(plan_episode(query, 2, search=False))


def _with(monkeypatch, **overrides):
    patched = dataclasses.replace(config.settings, **overrides)
    monkeypatch.setattr(config, "settings", patched)
    monkeypatch.setattr(script_generator, "settings", patched)


def test_on_by_default(monkeypatch):
    monkeypatch.delenv("PROMPT_CACHE", raising=False)
    monkeypatch.delenv("PROMPT_CACHE_TTL", raising=False)
    fresh = config.Settings()
    assert fresh.prompt_cache is True
    assert fresh.prompt_cache_ttl == "5m"


def test_the_cached_block_is_the_whole_system_prompt_and_nothing_else(monkeypatch):
    _with(monkeypatch, prompt_cache=True, prompt_cache_ttl="5m")
    system = _kwargs()["system"]
    assert system == [{"type": "text", "text": system_prompt(),
                       "cache_control": {"type": "ephemeral"}}]


def test_the_prefix_is_identical_across_episodes(monkeypatch):
    """A cache read needs a byte-identical prefix. Two different questions
    must send the same system block; what differs goes in the user turn."""
    _with(monkeypatch, prompt_cache=True)
    first, second = _kwargs("heat pumps"), _kwargs("the Fed's rate decision")
    assert first["system"] == second["system"]
    assert first["messages"] != second["messages"]


def test_an_hour_when_asked(monkeypatch):
    _with(monkeypatch, prompt_cache=True, prompt_cache_ttl="1h")
    assert _kwargs()["system"][0]["cache_control"] == {"type": "ephemeral",
                                                        "ttl": "1h"}


def test_off_sends_the_plain_string(monkeypatch):
    _with(monkeypatch, prompt_cache=False)
    assert _kwargs()["system"] == system_prompt()


def test_above_the_models_cacheable_minimum():
    """Sonnet 5 caches nothing shorter than 1,024 tokens, silently. The prompt
    is ~3,500; this fails if it is ever cut to a size where the marker would
    do nothing. Four characters a token is the conservative estimate."""
    assert len(system_prompt()) / 4 > 1024
