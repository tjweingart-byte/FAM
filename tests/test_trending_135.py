"""PROBLEMS.md §135: where an episode's information comes from, and Trending.

Four instructions from the owner, one file:

1. **The model never searches.** Everything an episode is written from comes
   from Exa, GDELT, API-Sports, Finnhub and Polymarket - pinned in
   `test_research.py` and `test_no_evidence.py`; here only the one rule that
   crosses modules: a live provider with a key is switched on by the key.
2. **API-Sports sweeps on its whole allowance, with the score** - the budget
   is in `test_story_sources.py`; here, that an episode's lookup reads the
   sweep's card before it spends.
3. **Trending is the stories the world's press is running** - clustered from
   headlines, ranked by how many outlets carry them, across every source.
4. **...worldwide and regionally, organised by geography.**

Nothing here touches the network: every provider is a recorded shape.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import geography  # noqa: E402
import live_facts  # noqa: E402
import live_sources  # noqa: E402
import news_clusters  # noqa: E402
import stories  # noqa: E402
import topics as T  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


def art(title, url, country=""):
    return types.SimpleNamespace(title=title, url=url, country=country,
                                 published_date="2026-09-23")


def story(subject, *, coverage=0, countries=(), strength=0.6, tags=("world",),
          domain=stories.ATTENTION, region_hint="", live_line="", keywords=()):
    scope, key, label = geography.scope_for(countries, region_hint)
    return stories.Story(
        subject=subject, title=subject.title(), angle="an angle",
        query=f"what is behind {subject}", domain=domain, source="test",
        tags=tuple(tags), strength=strength, first_seen=time.time(),
        shelf_life=stories.DOMAIN_SHELF_LIFE[domain], countries=tuple(countries),
        coverage=coverage, region_hint=region_hint, geo_scope=scope,
        geo_key=key, geo=label, live_line=live_line, keywords=tuple(keywords))


@pytest.fixture()
def store(tmp_path):
    stories.seed([])
    yield T.EventStore(str(tmp_path / "events.db"))
    stories.seed([])


# --------------------------------------------------------------------------
# geography
# --------------------------------------------------------------------------
def test_every_country_the_app_can_spell_is_somewhere():
    """A country missing from the region table silently counts as nowhere -
    the §134 finding about the ISO table, one layer up."""
    missing = set(stories.ISO_COUNTRIES.values()) - set(geography.REGION_OF)
    assert not missing, sorted(missing)
    for region in geography.REGIONS:
        assert region in geography.REGION_LABELS
        assert region in geography.GDELT_SOURCES, f"nobody asks {region}'s press"


@pytest.mark.parametrize("countries, expected", [
    # Three regions' press running it: the world noticed.
    ((("united states", 0.4), ("united kingdom", 0.3), ("japan", 0.3)),
     ("world", "world", "Worldwide")),
    # One country's press, overwhelmingly.
    ((("france", 0.8), ("united kingdom", 0.2)), ("country", "france", "France")),
    # One region, no one country owning it.
    ((("france", 0.4), ("germany", 0.35), ("spain", 0.25)),
     ("region", "europe", "Europe")),
    # Nothing known, nothing claimed.
    ((), ("world", "world", "Worldwide")),
])
def test_where_a_story_is_trending(countries, expected):
    assert geography.scope_for(countries) == expected


def test_a_story_with_no_countries_keeps_the_region_that_found_it():
    assert geography.scope_for((), "south-asia") == (
        "region", "south-asia", "South Asia")


def test_the_listener_s_part_of_the_world():
    assert geography.matches("country", "united states", "US")
    assert geography.matches("region", "north-america", "USA")
    assert not geography.matches("region", "europe", "US")
    assert not geography.matches("world", "world", "US"), "worldwide is not local"
    assert not geography.matches("region", "europe", ""), "unknown is nowhere"


# --------------------------------------------------------------------------
# headlines into stories
# --------------------------------------------------------------------------
def test_headlines_that_share_their_people_and_places_are_one_story():
    groups = news_clusters.cluster([
        art("Fed cuts interest rates as Powell signals more", "https://reuters.com/a"),
        art("Powell: Fed cuts rates for second time", "https://cnbc.com/b"),
        art("Tsunami warning after earthquake off Japan coast", "https://nhk.or.jp/c"),
        art("Japan earthquake triggers tsunami warning on coast", "https://bbc.co.uk/d"),
        art("Local bakery wins an award", "https://local.com/e"),
    ])
    assert [g.outlets for g in groups] == [2, 2]
    assert {"fed", "powell"} <= set(groups[0].keywords()) or \
        {"fed", "powell"} <= set(groups[1].keywords())
    assert not any("bakery" in g.headline().lower() for g in groups), \
        "one outlet's story is not a trend"


def test_popularity_is_outlets_not_articles():
    """One outlet syndicating a story across its sites is one outlet deciding
    it matters, not five."""
    groups = news_clusters.cluster(
        [art(f"Fed cuts rates, Powell says ({n})", f"https://wire.com/{n}")
         for n in range(5)]
        + [art("Powell says Fed cuts rates", "https://other.com/x")])
    assert groups[0].size == 6 and groups[0].outlets == 2


def test_a_different_rate_decision_is_a_different_story():
    """What an embedding would merge and a trending row must not."""
    groups = news_clusters.cluster([
        art("Fed holds rates steady, Powell says", "https://a.com/1"),
        art("Powell: Fed holds rates steady again", "https://b.com/2"),
        art("ECB holds rates steady, Lagarde says", "https://c.com/3"),
        art("Lagarde: ECB holds rates steady again", "https://d.com/4"),
    ])
    assert len(groups) == 2


def test_the_same_story_is_recognised_under_a_new_headline():
    assert news_clusters.same_story(("fed", "powell", "cuts", "rate"),
                                    ("powell", "fed", "rate", "december"))
    assert not news_clusters.same_story(("fed", "powell", "rate"),
                                        ("ecb", "lagarde", "rate"))


# --------------------------------------------------------------------------
# every source's view of one story, as one tile
# --------------------------------------------------------------------------
def test_a_game_the_press_is_running_takes_the_press_s_popularity():
    """"Collect all known information and create trending activity based off
    of that": the scoreboard says a game is on; the press says how much the
    world cares. One tile, carrying both."""
    game = stories.Signal(subject="Kansas City Chiefs vs Buffalo Bills",
                          observation="under way", domain=stories.SPORTS,
                          strength=0.5, live_line="Live · Chiefs 21–14 Bills")
    news = stories.Signal(subject="Chiefs and Bills trade blows in overtime",
                          observation="12 outlets", domain=stories.ATTENTION,
                          strength=0.9, coverage=12,
                          keywords=("chief", "bill", "overtime"),
                          countries=(("united states", 1.0),))
    other = stories.Signal(subject="Tsunami warning off Japan", observation="8",
                           domain=stories.ATTENTION, coverage=8,
                           keywords=("tsunami", "japan", "warning"))
    out = stories.corroborate([game, news, other])
    subjects = [s.subject for s in out]
    assert "Chiefs and Bills trade blows in overtime" not in subjects, \
        "the news story was offered beside the game it is about"
    merged = next(s for s in out if s.domain == stories.SPORTS)
    assert merged.coverage == 12 and merged.strength == pytest.approx(0.9)
    assert merged.countries == (("united states", 1.0),)
    assert merged.live_line == game.live_line
    assert "Tsunami warning off Japan" in subjects, "an unrelated story went"


def test_a_structured_signal_nobody_is_writing_about_is_left_alone():
    quote = stories.Signal(subject="Nvidia", observation="up 4%",
                           domain=stories.MARKETS, strength=0.7)
    news = stories.Signal(subject="Rail strike in France", observation="5",
                          domain=stories.ATTENTION, coverage=5,
                          keywords=("rail", "strike", "france"))
    assert stories.corroborate([quote, news]) == [quote, news]


class _Source(stories.StorySource):
    name = "news"
    domain = stories.ATTENTION

    def __init__(self, signals):
        self.signals = signals

    def diagnose(self):
        return True, "ready"

    async def collect(self, limit):
        return list(self.signals)


def test_a_story_keeps_its_tile_when_its_headline_changes(monkeypatch):
    """A cluster's subject is its best headline, and that moves between
    sweeps. Without identity by fingerprint, every sweep would mint a new tile
    for the same event - which never ages, never expires and is never damped
    by fatigue (§103 through a new door)."""
    monkeypatch.setattr(stories, "settings", dataclasses.replace(
        config.settings, stories=True, stories_compose=False))
    stories.reset()
    first = stories.Signal(subject="Fed cuts rates as Powell signals more",
                           observation="9 outlets", coverage=9, strength=1.0,
                           keywords=("fed", "powell", "cuts", "rate"),
                           countries=(("united states", 0.5),
                                      ("united kingdom", 0.3), ("japan", 0.2)))
    source = _Source([first])
    stories.register(source)
    try:
        pool = asyncio.run(stories.refresh(now=1000.0))
        (held,) = pool.stories
        assert held.coverage == 9 and held.geo == "Worldwide"
        source.signals = [dataclasses.replace(
            first, subject="Powell: the Fed cuts rates again, eyes December",
            coverage=14, keywords=("powell", "fed", "rate", "december"))]
        pool = asyncio.run(stories.refresh(now=2000.0))
        (again,) = pool.stories
        assert again.id == held.id, "a new headline minted a new tile"
        assert again.first_seen == held.first_seen
        assert again.coverage == 14, "popularity did not move with the story"
    finally:
        stories.reset()


def test_the_variety_cap_is_per_place():
    """Five world-news stories used to fill the pool's whole allowance for
    world news, so a story trending in Europe competed with one trending in
    India. Two places' news is not the same subject twice."""
    keys = stories._variety_keys(("world",), stories.ATTENTION, "europe")
    assert keys == {"world@europe"}
    assert stories._variety_keys(("world",), stories.ATTENTION, "") == {"world@world"}


# --------------------------------------------------------------------------
# the Trending rail
# --------------------------------------------------------------------------
def test_the_story_the_press_is_running_most_leads_trending():
    """"The trending section should carry stories based off of their
    popularity on trending news." Coverage outranks a signal nobody is
    writing about, whatever its own strength."""
    tiles = T.topics_from_stories([
        story("a quiet game", domain=stories.SPORTS, strength=1.0, tags=("sports",)),
        story("the big one", coverage=40, strength=0.8, tags=("money",)),
        story("a middling one", coverage=6, strength=0.5, tags=("science",)),
    ])
    row = T.rank_world(tiles)
    assert [t.title for t in row][:2] == ["The Big One", "A Middling One"]


def test_trending_saves_places_for_where_the_listener_is():
    """"Trending stories from over the world and regionally." A listener in
    France gets what France's press is running even when the world's biggest
    stories would otherwise fill the row - and still gets the world."""
    world = [story(f"world story {n}", coverage=50 - n, tags=(facet,),
                   countries=(("united states", 0.4), ("united kingdom", 0.3),
                              ("japan", 0.3)))
             for n, facet in enumerate(("money", "tech", "science", "culture"))]
    local = [story("the rail strike", coverage=4, tags=("world",),
                   countries=(("france", 1.0),)),
             story("the paris vote", coverage=3, tags=("health",),
                   countries=(("france", 0.9), ("belgium", 0.1)))]
    tiles = T.topics_from_stories(world + local)
    row = T.rank_world(tiles, "FR")
    titles = [t.title for t in row]
    assert "The Rail Strike" in titles and "The Paris Vote" in titles
    assert sum(t.geo_scope == "world" for t in row) == 2
    # Shown in popularity order - the local places are a reservation, not a
    # position at the front (and the country boost is §134's, unchanged).
    scores = [T.trending_score(t, "FR", 50) for t in row]
    assert scores == sorted(scores, reverse=True)
    # With no country, it is the world's row exactly.
    assert all(t.geo_scope == "world" for t in T.rank_world(tiles))


def test_a_trending_card_says_where_it_is_trending_and_carries_the_score():
    tile = T.topics_from_stories([story(
        "chiefs vs bills", domain=stories.SPORTS, tags=("sports",),
        countries=(("united states", 1.0),),
        live_line="Live · Chiefs 21–14 Bills · Q3")])[0].as_dict()
    assert tile["geo"] == "United States" and tile["geo_scope"] == "country"
    assert tile["live_line"] == "Live · Chiefs 21–14 Bills · Q3"


def test_view_more_is_trending_by_place(store):
    """Worldwide first, then where the listener is, then everywhere else -
    the same tiles as the flat list, in the same popularity order inside
    each place."""
    stories.seed([
        story("the big one", coverage=40,
              countries=(("united states", 0.4), ("united kingdom", 0.3),
                         ("japan", 0.3))),
        story("the delhi heatwave", coverage=9, countries=(("india", 1.0),)),
        story("the rail strike", coverage=4, countries=(("france", 1.0),)),
        story("the ohio vote", coverage=3, countries=(("united states", 1.0),)),
    ])
    section = T.build_section(store, "me", "world_trending", country="US")
    groups = section["groups"]
    assert [g["key"] for g in groups][:2] == ["world", "north-america"]
    assert groups[1]["yours"] and groups[1]["label"] == "North America"
    assert [t["id"] for g in groups for t in g["topics"]] == \
        [t["id"] for t in section["topics"]]
    assert {g["label"] for g in groups} >= {"South Asia", "Europe"}


def test_the_trending_row_never_takes_the_bank_even_by_geography(store):
    """§134's rule survives §135: an empty pool is an empty row."""
    stories.seed([])
    feed = T.build_feed(store, "me", country="US")
    row = [s for s in feed["sections"] if s["key"] == "world_trending"][0]
    assert row["topics"] == [] and row["empty_reason"]


# --------------------------------------------------------------------------
# live providers: a key is the statement of intent
# --------------------------------------------------------------------------
def test_a_live_provider_with_a_key_is_switched_on_by_the_key(monkeypatch):
    monkeypatch.setattr(live_sources, "settings", dataclasses.replace(
        config.settings, api_sports_key="k", finnhub_key="f",
        live_sports_provider="", live_markets_provider="",
        live_elections_provider="polymarket"))
    assert live_sources.configured() == {
        "sports": "api-sports", "markets": "finnhub", "elections": "polymarket"}
    assert live_sources.report()["derived_from_key"] == ["markets", "sports"]


def test_none_still_switches_a_domain_off(monkeypatch):
    monkeypatch.setattr(live_sources, "settings", dataclasses.replace(
        config.settings, api_sports_key="k", live_sports_provider="none"))
    assert live_sources.configured()["sports"] == ""


def test_no_key_no_provider(monkeypatch):
    monkeypatch.setattr(live_sources, "settings", dataclasses.replace(
        config.settings, api_sports_key="", finnhub_key="",
        live_sports_provider="", live_markets_provider=""))
    assert live_sources.configured()["sports"] == ""
    assert live_sources.configured()["markets"] == ""


def test_an_episode_about_a_swept_game_spends_nothing_finding_it(monkeypatch):
    """On a plan of a hundred requests a day, a request saved is fourteen
    minutes of fresher scores on myFAM. The sweep already listed today's
    games; the lookup reads that before asking, and a card fresh enough to
    be current is used as the state too."""
    row = {"game": {"id": 42, "status": {"short": "Q3", "long": "Third Quarter"}},
           "teams": {"home": {"name": "Kansas City Chiefs"},
                     "away": {"name": "Buffalo Bills"}},
           "scores": {"home": {"total": 21}, "away": {"total": 14}}}
    spent: list = []

    async def _json(url, headers, params, timeout):
        spent.append(params)
        return {"response": [row]}

    monkeypatch.setattr(live_sources, "_json", _json)
    monkeypatch.setattr(live_sources, "API_SPORTS_BUDGET", live_sources.RequestBudget())
    monkeypatch.setattr(live_sources, "CARD", {})
    monkeypatch.setattr(live_sources, "settings", dataclasses.replace(
        config.settings, api_sports_key="k", api_sports_sport="american-football"))
    live_sources.remember_card("american-football", [row])

    source = live_sources.ApiSportsSource()
    brief = types.SimpleNamespace(subject="Chiefs game", query="chiefs game")
    entity = asyncio.run(source.resolve(brief))
    facts = asyncio.run(source.fetch(entity))
    assert entity.id == "american-football:42"
    assert facts.status == live_facts.IN_PROGRESS
    assert spent == [], "the lookup spent requests the sweep had already paid for"

    # A card too old to be current costs one request, for the state only.
    live_sources.CARD["american-football"] = (time.time() - 600, [row])
    asyncio.run(source.fetch(asyncio.run(source.resolve(brief))))
    assert spent == [{"id": "42"}]


# --------------------------------------------------------------------------
# the pool refreshes itself
# --------------------------------------------------------------------------
def test_the_pool_refreshes_with_nobody_looking(monkeypatch):
    """A quiet hour used to leave Trending an hour old and the API-Sports
    allowance unspent, because only a page load refreshed the pool."""
    import app

    ticks: list = []
    warmed: list = []

    async def sleep(seconds):
        ticks.append(seconds)
        if len(ticks) > 2:
            raise asyncio.CancelledError

    async def warm():
        warmed.append(True)

    monkeypatch.setattr(app.asyncio, "sleep", sleep)
    monkeypatch.setattr(app, "_warm_stories", warm)
    monkeypatch.setattr(app, "settings", dataclasses.replace(
        config.settings, stories_background_seconds=900.0))
    stories.seed([])
    stories.pool().fetched_at = 0.0
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(app._refresh_stories_forever())
    assert warmed, "a stale pool was not refreshed"
    assert all(s <= 60 for s in ticks)


# --------------------------------------------------------------------------
# the interface
# --------------------------------------------------------------------------
def test_the_card_draws_the_score_beside_the_title_and_the_place_on_trending():
    assert "function seedLiveHtml(topic)" in INDEX
    assert "function seedTagText(sectionKey, topic)" in INDEX
    assert 'sectionKey === "world_trending" && topic && topic.geo' in INDEX
    assert "if(d.groups && d.groups.length)" in INDEX
    # Both surfaces that draw a card draw the score the same way.
    assert INDEX.count("+ seedLiveHtml(t)") == 2


# --------------------------------------------------------------------------
# found on review
# --------------------------------------------------------------------------
def test_a_game_is_not_matched_to_news_about_its_city():
    """"Kansas City Chiefs vs Buffalo Bills" shares two words with a story
    about a Kansas City tornado - and only one side of the fixture."""
    game = stories.Signal(subject="Kansas City Chiefs vs Buffalo Bills",
                          observation="under way", domain=stories.SPORTS)
    storm = stories.Signal(subject="Tornado tears through Kansas City",
                           observation="9", domain=stories.ATTENTION,
                           coverage=9, keywords=("kansa", "city", "tornado"))
    out = stories.corroborate([game, storm])
    assert [s.coverage for s in out if s.domain == stories.SPORTS] == [0]
    assert storm in out, "the storm story was folded into a football game"


def test_a_score_read_off_the_sweep_is_as_old_as_the_sweep(monkeypatch):
    """Stamping it with this moment would let the freshness check that
    withholds a stale score pass one it should have questioned."""
    row = {"game": {"id": 7, "status": {"short": "Q2"}},
           "teams": {"home": {"name": "Chiefs"}, "away": {"name": "Bills"}},
           "scores": {"home": {"total": 7}, "away": {"total": 3}}}
    monkeypatch.setattr(live_sources, "CARD", {})
    swept = time.time() - 40
    live_sources.remember_card("american-football", [row], now=swept)
    entity = live_facts.Entity(domain="sports", provider="API-Sports",
                               id="american-football:7", label="Chiefs v Bills")
    facts = asyncio.run(live_sources.ApiSportsSource().fetch(entity))
    assert abs(facts.as_of.timestamp() - swept) < 1


def test_an_api_sports_refusal_is_never_an_empty_card(monkeypatch):
    """A bad key is answered 200 with the reason in `errors` and an empty
    `response`. Read as data, that is a day with no games."""
    async def _json(url, headers, params, timeout):
        return {"errors": {"token": "Error/Missing application key."},
                "response": []}

    monkeypatch.setattr(live_sources, "_json", _json)
    monkeypatch.setattr(live_sources, "API_SPORTS_BUDGET", live_sources.RequestBudget())
    with pytest.raises(RuntimeError, match="application key"):
        asyncio.run(live_sources.api_sports_json("https://x", {}, 1.0))
    assert live_sources.API_SPORTS_BUDGET.remaining() > 0, \
        "a bad key is not a spent allowance"


def test_nothing_but_exa_and_gdelt_is_a_rung():
    import research

    assert research._rung_available("claude") is False
    assert research._rung_available("anything") is False


def test_health_reports_the_api_sports_allowance():
    report = live_sources.report()["api_sports_budget"]
    assert set(report) == {"daily", "used_today", "remaining"}
