"""A mix's rows are called by today's episode, not the topic (§570).

The owner, 11.12: the episodes in somebody's mix should show episode titles,
new with each story every day, not the category names. Today's episode, once
the edition has written it, rides on each row as `episode_title`; the topic
keeps `title`, because a reorder sends it back as the item.
"""
from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

INDEX = (pathlib.Path(__file__).resolve().parent.parent / "static" / "index.html").read_text()


def test_a_written_episode_names_its_row_and_the_topic_stays(monkeypatch):
    titles = {"eagles prompt": "Hurts throws for four as the Eagles take the East"}
    monkeypatch.setattr(appmod, "_today_title",
                        lambda prompt, minutes: titles.get(prompt, ""))
    body = {"items": [
        {"id": "f:nfl~Eagles", "title": "Eagles", "prompt": "eagles prompt", "minutes": 2},
        {"id": "f:stocks", "title": "Stocks & Economy", "prompt": "stocks prompt", "minutes": 2},
        {"id": "bank", "title": "A bank topic", "prompt": "", "minutes": 2},
    ]}
    appmod._mix_episode_titles(body)
    eagles, stocks, bank = body["items"]
    assert eagles["episode_title"] == titles["eagles prompt"]
    assert eagles["title"] == "Eagles"                 # the topic is never renamed
    assert "episode_title" not in stocks               # not written yet: the topic
    assert "episode_title" not in bank


def test_a_failing_read_never_fails_the_mix(monkeypatch):
    def boom(prompt, minutes):
        raise RuntimeError("cache down")
    monkeypatch.setattr(appmod, "_today_title", boom)
    body = {"items": [{"id": "x", "title": "X", "prompt": "p", "minutes": 2}]}
    appmod._mix_episode_titles(body)
    assert "episode_title" not in body["items"][0]


def test_every_mix_response_carries_it():
    import inspect
    assert "_mix_episode_titles(body)" in inspect.getsource(appmod._mix_out)
    assert "_mix_episode_titles(body)" in inspect.getsource(appmod.public_mix)


def test_the_row_and_the_player_show_the_episode_title():
    assert "escapeHtml(t.episode_title || t.title)" in INDEX
    assert "function mixTopicSub(t, by)" in INDEX
    assert "title: item.episode_title || (daily ?" in INDEX
