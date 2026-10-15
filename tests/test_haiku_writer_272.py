"""The writer on Haiku 5.5 at low effort (§272).

Its own setting, `WRITER_MODEL`, never `MODEL`: `MODEL` is in every episode's
cache key, so moving it would orphan every kept episode, and the composer,
placer and thumbnails stay on it. Pinned here: the default, that the cache
key does not move, that the request and the bill follow the writer, that an
account without Haiku writes on `MODEL`, and that health says which is in
force.
"""
from __future__ import annotations

import dataclasses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cache  # noqa: E402
import config  # noqa: E402
import script_generator as sg  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_writer_is_haiku_at_low_by_default():
    fresh = config.Settings()
    assert fresh.writer_model == "claude-haiku-5-5" == config.SMALL_MODEL
    assert fresh.effort == "low"
    assert fresh.model == "claude-sonnet-5", "MODEL is not the writer's any more"
    text = open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read()
    assert "\nWRITER_MODEL=claude-haiku-5-5\n" in text and "\nEFFORT=low\n" in text


def test_every_episode_writes_on_it_and_editions_follow_unless_set(monkeypatch):
    monkeypatch.setattr(sg, "UNAVAILABLE_MODELS", set())
    monkeypatch.setattr(sg, "settings", dataclasses.replace(
        config.Settings(), edition_writer_model="", edition_writer_effort=""))
    edition = sg.ScriptNotes()
    edition.edition = True
    for notes in (None, sg.ScriptNotes(), edition):
        assert sg.writer_for(notes) == ("claude-haiku-5-5", "low")


def test_an_account_without_it_writes_on_model(monkeypatch):
    monkeypatch.setattr(sg, "settings", config.Settings())
    monkeypatch.setattr(sg, "UNAVAILABLE_MODELS", {"claude-haiku-5-5"})
    assert sg.writer_for(None) == ("claude-sonnet-5", "low")


def test_the_request_names_it():
    gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    params = gen._request_kwargs(sg.plan_episode("why is the sky blue", 2,
                                                 search=False))
    assert params["model"] == sg.writer_for(None)[0]
    assert params["output_config"] == {"effort": "low"}


def test_the_cache_key_does_not_move(monkeypatch):
    """A tap still finds the episode the Sonnet writer kept."""
    before = cache.cache_key("why is the sky blue", 2)
    monkeypatch.setattr(cache, "settings", dataclasses.replace(
        cache.settings, writer_model="claude-sonnet-5"))
    assert cache.cache_key("why is the sky blue", 2) == before


def test_health_and_the_boot_check_know_about_it():
    source = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert '"writer_model": writer_for(ScriptNotes())[0],' in source
    assert "settings.image_check_model, settings.writer_model," in source


def test_an_unavailable_edition_writer_falls_back_to_the_everyday_writer(monkeypatch):
    """Review, §273: not to `MODEL`, which would move the editions to Sonnet
    while every tap is on Haiku."""
    monkeypatch.setattr(sg, "settings", dataclasses.replace(
        config.Settings(), edition_writer_model="claude-opus-5-5"))
    monkeypatch.setattr(sg, "UNAVAILABLE_MODELS", {"claude-opus-5-5"})
    edition = sg.ScriptNotes()
    edition.edition = True
    assert sg.writer_for(edition)[0] == "claude-haiku-5-5"
