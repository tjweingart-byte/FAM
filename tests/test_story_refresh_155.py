"""§155: sports and markets every fifteen minutes, the news every two hours.

The owner asked for the story refresh to move to two hours, then narrowed it:
scores must not be that outdated. So the pool keeps its fifteen-minute tick
and each source keeps its own floor. What has to hold:

* the fast sources are asked every tick and the slow ones every two hours;
* a slow source's stories stay in the pool between its sweeps;
* a game the press was covering keeps that lift on a tick with no news
  sweep, rather than dropping for most of every two hours.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import stories  # noqa: E402
import story_sources  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TICK = 900.0


class _Source(stories.StorySource):
    def __init__(self, name, domain, floor, signals):
        self.name, self.domain = name, domain
        self.min_interval_seconds = floor
        self.signals, self.asked = signals, 0

    def diagnose(self):
        return True, "ready"

    async def collect(self, limit):
        self.asked += 1
        return list(self.signals)


@pytest.fixture()
def pool(monkeypatch):
    monkeypatch.setattr(stories, "settings", dataclasses.replace(
        config.settings, stories=True, stories_compose=False))
    stories.reset()
    yield
    stories.reset()


def test_the_floors():
    s = config.settings
    assert s.stories_background_seconds == TICK == s.stories_ttl_seconds
    assert story_sources.FinnhubSignals().min_interval_seconds == TICK
    for slow in (story_sources.GdeltSignals, story_sources.TrendingRegistrySignals,
                 story_sources.PolymarketSignals):
        assert slow().min_interval_seconds == 7200.0, slow.name


def _game_and_news():
    game = stories.Signal(subject="Kansas City Chiefs vs Buffalo Bills",
                          observation="under way", domain=stories.SPORTS,
                          strength=0.5, live_line="Live · Chiefs 21–14 Bills",
                          live_status="in_progress")
    news = stories.Signal(subject="Chiefs and Bills trade blows in overtime",
                          observation="12 outlets", domain=stories.ATTENTION,
                          strength=0.9, coverage=12,
                          keywords=("chief", "bill", "overtime"))
    other = stories.Signal(subject="Tsunami warning off Japan", observation="8",
                           domain=stories.ATTENTION, strength=0.8, coverage=8,
                           keywords=("tsunami", "japan", "warning"))
    return game, news, other


def test_sports_every_tick_news_every_two_hours(pool):
    game, news, other = _game_and_news()
    sports = _Source("sports", stories.SPORTS, 0.0, [game])
    press = _Source("press", stories.ATTENTION, 7200.0, [news, other])
    stories.register(sports)
    stories.register(press)

    t = 10_000.0
    for tick in range(9):                       # 0, 15 min ... 2 h
        asyncio.run(stories.refresh(now=t + tick * TICK))
    assert sports.asked == 9
    assert press.asked == 2, "the news sweep ran more than every two hours"


def test_between_news_sweeps_nothing_is_lost(pool):
    game, news, other = _game_and_news()
    sports = _Source("sports", stories.SPORTS, 0.0, [game])
    press = _Source("press", stories.ATTENTION, 7200.0, [news, other])
    stories.register(sports)
    stories.register(press)

    first = asyncio.run(stories.refresh(now=10_000.0))
    held = {s.subject: s for s in first.stories}
    assert "Tsunami Warning Off Japan".lower() in {k.lower() for k in held}

    sports.signals = [dataclasses.replace(
        game, live_line="Live · Chiefs 28–14 Bills")]
    later = asyncio.run(stories.refresh(now=10_000.0 + TICK))
    now_held = {s.subject: s for s in later.stories}
    assert set(now_held) == set(held), "a news story vanished between sweeps"

    before = next(s for s in first.stories if s.domain == stories.SPORTS)
    after = next(s for s in later.stories if s.domain == stories.SPORTS)
    assert after.live_line.endswith("28–14 Bills"), "the score did not move"
    assert after.coverage == 12
    assert after.strength == pytest.approx(before.strength), (
        "a tick with no news sweep read as the press losing interest")


def test_a_news_sweep_that_drops_the_game_drops_its_lift(pool):
    game, news, other = _game_and_news()
    sports = _Source("sports", stories.SPORTS, 0.0, [game])
    press = _Source("press", stories.ATTENTION, 7200.0, [news, other])
    stories.register(sports)
    stories.register(press)
    asyncio.run(stories.refresh(now=10_000.0))

    press.signals = [other]                     # the press moved on
    later = asyncio.run(stories.refresh(now=10_000.0 + 7200.0))
    after = next(s for s in later.stories if s.domain == stories.SPORTS)
    assert after.strength == pytest.approx(game.strength)


def test_a_stale_score_does_not_claim_to_be_live():
    """Scores refresh on the API-Sports budget; when it runs low a score can
    sit for a while. It keeps its age and loses the badge that says it is
    happening now."""
    html = open(os.path.join(ROOT, "static", "index.html")).read()
    body = html[html.index("function seedLiveHtml"):]
    body = body[:body.index("\n  }\n")]
    assert '"in_progress" && fresh' in body
