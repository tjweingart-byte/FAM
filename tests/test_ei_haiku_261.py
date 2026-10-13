"""The brief runs on Haiku 5.5 at medium effort (§261, the owner's direction).

§249 put the brief back on `MODEL` (Sonnet 5 at low); §261 moves the brief
alone back to Haiku, at medium rather than low effort. The writer, composer,
placer and thumbnails are unchanged. Pinned here: the defaults, that the
brief does not follow `MODEL` (render.yaml sets it), that both settings
still restore §249's brief without code, and that the request carries them.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def fresh_config(monkeypatch):
    for name in ("EI_MODEL", "EI_EFFORT", "MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FAM_IGNORE_DOTENV", "1")
    import config

    def load():
        importlib.reload(config)
        return config

    yield load
    for name in ("EI_MODEL", "EI_EFFORT", "MODEL"):
        monkeypatch.delenv(name, raising=False)
    importlib.reload(config)


def test_the_brief_defaults_to_haiku_at_medium(fresh_config):
    config = fresh_config()
    assert config.settings.ei_model == config.SMALL_MODEL == "claude-haiku-5-5"
    assert config.settings.ei_effort == "medium"


def test_the_writer_stays_where_it_was(fresh_config):
    config = fresh_config()
    assert config.settings.model == "claude-sonnet-5"
    assert config.settings.effort == "low"


def test_the_brief_does_not_follow_model(fresh_config, monkeypatch):
    """`render.yaml` sets MODEL; following it would keep production on Sonnet."""
    monkeypatch.setenv("MODEL", "claude-sonnet-5")
    config = fresh_config()
    assert config.settings.ei_model == "claude-haiku-5-5"


def test_both_settings_restore_the_sonnet_brief(fresh_config, monkeypatch):
    monkeypatch.setenv("EI_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("EI_EFFORT", "low")
    config = fresh_config()
    assert (config.settings.ei_model, config.settings.ei_effort) == \
        ("claude-sonnet-5", "low")


def test_env_example_agrees_with_config():
    """A setting is settled only where it is copied (CLAUDE.md)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, ".env.example"), encoding="utf-8").read()
    assert "\nEI_EFFORT=medium\n" in text
    assert "# EI_MODEL=claude-haiku-5-5\n" in text


def test_the_request_carries_the_model_and_effort(fresh_config, monkeypatch):
    config = fresh_config()
    import episode_intelligence as ei
    monkeypatch.setattr(ei, "settings", config.settings)
    sent = {}

    class _Messages:
        async def create(self, **kwargs):
            sent.update(kwargs)
            raise RuntimeError("stop here")

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(ei, "build_async_client", lambda *a, **k: _Client())
    brief = asyncio.run(ei.understand("who won the game last night"))
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["output_config"]["effort"] == "medium"
    assert brief.degraded  # the failure fell back, as every failure does
