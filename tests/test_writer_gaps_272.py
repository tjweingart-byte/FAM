"""§272: the writer's evidence gaps closed in one pass.

A bigger packet for every two-minute episode, more expert sources trusted,
the league's standings beside a team's record, Trending stories' own outlets
searched, and a way to seed the local-outlet file from Wikidata.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import live_sources  # noqa: E402
import research  # noqa: E402
import stories  # noqa: E402
import trending_bank  # noqa: E402
from config import BROWSE_MINUTES  # noqa: E402


# --------------------------------------------------------------------------
# The packet
# --------------------------------------------------------------------------
def test_a_two_minute_episode_gets_five_sources_of_three_passages(monkeypatch):
    defaults = config.Settings()
    assert defaults.search_num_results == 10
    assert defaults.search_packet_sources == 5
    assert defaults.search_highlights_per_source == 3
    assert defaults.parallel_chars_per_result == 3000
    assert defaults.perplexity_tokens_per_page == 1024
    monkeypatch.setattr(research, "settings", defaults)
    # Browse is the base; it overrides nothing.
    assert research.packet_size(BROWSE_MINUTES) == {}


def test_a_longer_episode_never_gets_less_than_a_browse_one(monkeypatch):
    monkeypatch.setattr(research, "settings", config.Settings())
    base = (config.Settings().search_packet_sources,
            config.Settings().search_highlights_per_source)
    previous = base
    for minutes in range(BROWSE_MINUTES + 1, 11):
        size = research.packet_size(minutes)
        now = (size["packet_sources"], size["highlights_per_source"])
        assert now >= previous, f"{minutes} minutes got less than {previous}"
        previous = now
    assert previous == (10, 4)


# --------------------------------------------------------------------------
# Who is trusted
# --------------------------------------------------------------------------
@pytest.mark.parametrize("url,tier", [
    ("https://www.nejm.org/doi/x", "primary"),
    ("https://www.thelancet.com/x", "primary"),
    ("https://ecb.europa.eu/press/x", "primary"),
    ("https://www.abs.gov.au/statistics", "primary"),
    ("https://www.cdc.gov/flu", "primary"),
    ("https://variety.com/2026/film/x", "established"),
    ("https://www.billboard.com/charts/hot-100", "established"),
    ("https://www.boxofficemojo.com/weekend/x", "established"),
    ("https://www.statnews.com/x", "established"),
    ("https://someblog.example/x", "unverified"),
])
def test_expert_sources_are_graded(url, tier):
    assert research.credibility(SimpleNamespace(url=url)) == tier


# --------------------------------------------------------------------------
# Standings
# --------------------------------------------------------------------------
def test_a_team_s_place_is_the_table_s_own():
    """§276: BALLDONTLIE reads the table now (`balldontlie_scores`)."""
    import balldontlie
    import balldontlie_scores as bs

    nfl = balldontlie.LEAGUES["nfl"]
    row = {"team": {"id": 9, "full_name": "Kansas City Chiefs"}, "wins": 6,
           "losses": 1, "division_rank": 2, "division_name": "AFC West"}
    assert bs.standing_facts(nfl, {"id": 9, "full_name": "Kansas City Chiefs"},
                             row) == [
        "The Kansas City Chiefs have won 6 and lost 1 this season, per the "
        "league's standings.",
        "The Kansas City Chiefs are 2nd in the AFC West."]
    assert bs.standing_facts(nfl, {"id": 1, "full_name": "Denver Broncos"},
                             None) == []


def test_standings_are_one_cached_request_per_league(monkeypatch):
    import asyncio

    import balldontlie
    import balldontlie_scores as bs

    calls = []

    class Client:
        async def get(self, league, kind, path, params):
            calls.append((league.key, kind, path, dict(params)))
            return {"data": [{"team": {"id": 9}, "wins": 1, "losses": 0}]}

    nfl = balldontlie.LEAGUES["nfl"]
    first = asyncio.run(bs.league_standings(Client(), nfl))
    second = asyncio.run(bs.league_standings(Client(), nfl))
    assert len(calls) == 1 and first == second and 9 in first
    assert calls[0][:3] == ("nfl", "standings", "standings")


# --------------------------------------------------------------------------
# Trending's outlets
# --------------------------------------------------------------------------
def test_a_trending_story_s_outlets_are_searched_and_its_headlines_are_not_evidence(
        monkeypatch):
    story = SimpleNamespace(
        query="The Fed's surprise cut: what is happening and why it matters",
        subject="The Fed's surprise cut", domain=stories.ATTENTION,
        url="https://www.reuters.com/a",
        urls=("https://www.reuters.com/a", "https://apnews.com/b",
              "https://www.reuters.com/c"))
    monkeypatch.setattr(stories, "_POOL", SimpleNamespace(held=lambda now: []))
    monkeypatch.setattr(trending_bank, "stories_now", lambda now=None: [story])
    context = stories.context_for(story.query)
    assert context is not None
    assert context.hosts == ("reuters.com", "apnews.com")
    assert context.measured is False and context.as_evidence() == ""


def test_the_edition_keeps_every_article_address():
    source = (ROOT / "trending_bank.py").read_text()
    assert "urls=tuple(group.urls)," in source


# --------------------------------------------------------------------------
# Seeding local outlets
# --------------------------------------------------------------------------
def _tool():
    spec = importlib.util.spec_from_file_location(
        "local_outlets_tool", ROOT / "tools" / "local_outlets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_seed_merges_by_homepage_and_keeps_what_was_filed(tmp_path):
    tool = _tool()
    seed = tmp_path / "local_outlets.json"
    seed.write_text((ROOT / "local_outlets.json").read_text())
    rows = tool.seed_rows([
        {"name": "The Sacramento Bee", "homepage": "https://www.sacbee.com/",
         "town": "Sacramento", "scope": "town"},
        {"name": "Marin Independent Journal", "homepage": "https://www.marinij.com/",
         "town": "", "scope": "county"}], "Sacramento County", "California")
    assert tool.merge_seed(rows, seed) == 1
    assert tool.merge_seed(rows, seed) == 0
    data = json.loads(seed.read_text())
    homepages = [o["homepage"] for o in data["outlets"]]
    assert homepages.count("https://www.marinij.com/") == 1
    added = [o for o in data["outlets"] if o["homepage"] == "https://www.sacbee.com/"][0]
    assert added == {"name": "The Sacramento Bee", "homepage": "https://www.sacbee.com/",
                     "town": "Sacramento", "county": "Sacramento",
                     "region": "California", "country": "US", "scope": "town"}


def test_the_top_counties_are_fifty_distinct_ones():
    tool = _tool()
    assert len(tool.TOP_COUNTIES) == 50 == len(set(tool.TOP_COUNTIES))
    assert tool.TOP_COUNTIES[0] == ("Los Angeles County", "California")
