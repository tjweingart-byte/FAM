"""The four live sources, against recorded payloads.

**None of these has ever made a real request from this machine** - the build
container's egress proxy blocks all four hosts - so every payload below is
written from the documented API and every assertion is about the parsing, not
about the service. `python tools/verify_live.py` is what says a given machine
can actually reach them; §52's rule is that "a key is set" is not "the key
works", and neither is a green test file.

What is genuinely worth pinning here is the *contract*, which is the same for
all four and is the thing a fifth source would be most likely to break:

* a signal is a **measurement**, never a result - with one exception, the
  score a sports card carries in `live_line`, written in code every sweep and
  never composed into a title (§135);
* a source that is not configured says which credential is missing;
* a source that breaks raises, and one that saw nothing returns `[]`.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import live_facts  # noqa: E402
import stories  # noqa: E402
import story_sources  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def with_settings(monkeypatch, **kw):
    patched = dataclasses.replace(config.settings, **kw)
    monkeypatch.setattr(story_sources, "settings", patched)
    return patched


# --------------------------------------------------------------------------
# the contract every source keeps
# --------------------------------------------------------------------------
def test_every_source_says_which_credential_it_is_missing():
    """A gap that names its own fix. The alternative is a browse page that is
    thinner than it should be for a reason nobody can find."""
    for name, builder in story_sources.BUILDERS.items():
        ok, why = builder().diagnose()
        assert why, f"{name} gave no reason"
        if not ok:
            assert any(word in why for word in
                       ("KEY", "=0", "not configured", "switched off",
                        "is empty")), f"{name}: {why!r} does not name the fix"


def test_no_source_can_be_installed_by_a_typo():
    ready = story_sources.install()
    assert ready["configured"]
    assert not ready["problems"]
    stories.reset()
    for source in list(stories._SOURCES):
        stories.unregister(source.name)


def test_an_unknown_source_name_costs_one_source_and_not_the_server(monkeypatch):
    with_settings(monkeypatch, stories_sources="gdelt,not-a-source")
    try:
        ready = story_sources.install()
        assert ready["problems"], "a typo should be reported"
        assert "not a source this build knows" in ready["problems"][0]
        assert "GDELT" in ready["installed"], "the real one still installed"
    finally:
        for source in list(stories._SOURCES):
            stories.unregister(source.name)


# --------------------------------------------------------------------------
# Finnhub: a price is a measurement, never a verdict
# --------------------------------------------------------------------------
QUOTES = {
    "NVDA": {"c": 119.4, "dp": -6.2, "t": 1789000000},
    "AAPL": {"c": 232.1, "dp": 0.4, "t": 1789000000},   # did not move enough
    "SPY":  {"c": 581.0, "dp": 3.4, "t": 1789000000},
    "XOM":  {"c": 0, "dp": None, "t": 0},               # unreadable
}


def fake_quotes(monkeypatch):
    async def _json(url, headers, params, timeout):
        return QUOTES.get(params.get("symbol"), {})

    monkeypatch.setattr(story_sources, "_json", _json)


def test_finnhub_reports_what_moved_and_ignores_what_did_not(monkeypatch):
    fake_quotes(monkeypatch)
    with_settings(monkeypatch, finnhub_key="k",
                  finnhub_watchlist="NVDA,AAPL,SPY,XOM",
                  stories_market_move_percent=3.0)
    rows = run(story_sources.FinnhubSignals().collect(8))
    assert [r.subject for r in rows] == ["Nvidia", "the S&P 500"]
    assert rows[0].strength == 1.0, "the biggest mover leads"
    assert rows[0].domain == stories.MARKETS


def test_a_finnhub_observation_says_what_moved_and_not_why(monkeypatch):
    """The whole reason a price is safe to put on a tile: "down six percent"
    is true whatever the reason, and the reason is what the episode is for."""
    fake_quotes(monkeypatch)
    with_settings(monkeypatch, finnhub_key="k", finnhub_watchlist="NVDA")
    said = run(story_sources.FinnhubSignals().collect(8))[0].observation
    assert "down about 6.2 percent" in said
    assert "nothing here says why" in said
    assert "delay" in said, "a delayed quote must say it is delayed"


def test_a_quiet_market_produces_no_tiles_rather_than_dull_ones(monkeypatch):
    fake_quotes(monkeypatch)
    with_settings(monkeypatch, finnhub_key="k", finnhub_watchlist="AAPL")
    assert run(story_sources.FinnhubSignals().collect(8)) == []


def test_the_sweep_keeps_every_watchlist_quote_for_a_tap(monkeypatch):
    """§267: the sweep remembers each watchlist quote, moved or not, with the
    session it was taken in - and only the watchlist, never the market."""
    import live_sources

    async def _json(url, headers, params, timeout):
        if url.endswith("/stock/market-status"):
            return {"isOpen": False}
        return QUOTES.get(params.get("symbol"), {})

    monkeypatch.setattr(story_sources, "_json", _json)
    monkeypatch.setattr(live_sources, "QUOTES", {})
    with_settings(monkeypatch, finnhub_key="k",
                  finnhub_watchlist="NVDA,AAPL,XOM",
                  stories_market_move_percent=3.0)
    run(story_sources.FinnhubSignals().collect(8))
    assert set(live_sources.QUOTES) == {"NVDA", "AAPL"}, (
        "both readable quotes kept, the unreadable one not")
    assert live_sources.swept_quote("aapl")["is_open"] is False


def test_a_tap_uses_the_swept_quote_only_in_the_same_session(monkeypatch):
    import asyncio
    import time as _time
    from datetime import datetime, timezone

    import live_facts
    import live_sources

    asked = []

    async def _json(url, headers, params, timeout):
        asked.append(url.rsplit("/", 1)[-1])
        return {"c": 300.0, "dp": 1.0,
                "t": datetime.now(timezone.utc).timestamp()}

    monkeypatch.setattr(live_sources, "_json", _json)
    monkeypatch.setattr(live_sources, "QUOTES", {})
    source = live_sources.FinnhubSource()
    entity = live_facts.Entity(domain="markets", provider="Finnhub", id="AAPL",
                               label="Apple")
    live_sources.remember_quote(
        "AAPL", {"c": 231.5, "dp": -1.0, "t": _time.time() - 17 * 3600}, False)

    async def closed():
        return False

    monkeypatch.setattr(source, "market_open", closed)
    facts = asyncio.run(source.fetch(entity))
    assert asked == [], "a closed market's swept close needs no quote request"
    assert facts.market_closed and "231.50" in " ".join(facts.facts)

    async def opened():
        return True

    monkeypatch.setattr(source, "market_open", opened)
    facts = asyncio.run(source.fetch(entity))
    assert asked == ["quote"], "the session changed, so the quote is asked live"
    assert "300.00" in " ".join(facts.facts)

    # A swept quote too old to use even in its own session costs no round
    # trip: the quote and the session are asked together, as before.
    asked.clear()
    live_sources.remember_quote(
        "AAPL", {"c": 231.5, "dp": -1.0, "t": _time.time() - 3 * 3600}, True)
    sessions = []

    async def counted():
        sessions.append(1)
        return True

    monkeypatch.setattr(source, "market_open", counted)
    facts = asyncio.run(source.fetch(entity))
    assert asked == ["quote"] and sessions == [1]
    assert "300.00" in " ".join(facts.facts)


def test_finnhub_without_a_key_is_off_and_says_which_key(monkeypatch):
    with_settings(monkeypatch, finnhub_key="")
    ok, why = story_sources.FinnhubSignals().diagnose()
    assert not ok and "FINNHUB_KEY" in why


# --------------------------------------------------------------------------
# Polymarket: a forecast, and never a result
# --------------------------------------------------------------------------
MARKETS = [
    {"question": "Will the Fed cut rates in March?",
     "outcomePrices": "[\"0.62\", \"0.38\"]", "volume24hr": 412000},
    {"question": "Will the bill pass before the recess?",
     "bestBid": 0.19, "volume24hr": 88000},
    {"question": "No price on this one", "volume24hr": 5},
]


def test_polymarket_turns_the_most_traded_markets_into_questions(monkeypatch):
    async def _json(url, headers, params, timeout):
        return MARKETS

    monkeypatch.setattr(story_sources, "_json", _json)
    with_settings(monkeypatch, stories_polymarket=True,
                  polymarket_base="https://example.invalid")
    rows = run(story_sources.PolymarketSignals().collect(8))
    assert len(rows) == 2, "a market with no readable price is not a signal"
    assert rows[0].subject.startswith("Will the Fed")
    assert "62 percent" in rows[0].observation


def test_every_polymarket_signal_is_marked_unresolved_and_says_so(monkeypatch):
    """A price that moves with an outcome reads like the outcome. This is what
    forbids that inference structurally rather than by asking nicely."""
    async def _json(url, headers, params, timeout):
        return MARKETS

    monkeypatch.setattr(story_sources, "_json", _json)
    with_settings(monkeypatch, stories_polymarket=True,
                  polymarket_base="https://example.invalid")
    for row in run(story_sources.PolymarketSignals().collect(8)):
        assert row.outcome_pending
        assert "not a reported result" in row.observation
        assert row.domain == stories.PREDICTION


def test_polymarket_ships_off(monkeypatch):
    """Keyless, so without a switch it would turn itself on - and would then
    be the only live source on a fresh deployment, which would make the browse
    page a betting slip."""
    monkeypatch.setattr(story_sources, "settings", config.settings)
    ok, why = story_sources.PolymarketSignals().diagnose()
    assert not ok and "STORIES_POLYMARKET" in why


# --------------------------------------------------------------------------
# BALLDONTLIE: the score rides beside the tile, never inside it (§135, §276)
# --------------------------------------------------------------------------
def _team(i, name):
    return {"id": i, "full_name": name, "name": name}


def _game(gid, home, away, state, hs=None, as_=None, hours=-1.0):
    from datetime import datetime, timedelta, timezone

    row = {"id": gid, "home_team": home, "visitor_team": away,
           "status_state": state,
           "date": (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()}
    if hs is not None:
        row.update(home_team_score=hs, visitor_team_score=as_)
    return row


CHIEFS, BRONCOS = _team(1, "Chiefs"), _team(2, "Broncos")
EAGLES, GIANTS = _team(3, "Eagles"), _team(4, "Giants")
JETS, BILLS = _team(5, "Jets"), _team(6, "Bills")
RAMS, NINERS = _team(7, "Rams"), _team(8, "Niners")
CARD = {"data": [
    _game(1, CHIEFS, BRONCOS, "in_progress", 21, 7),
    _game(2, EAGLES, GIANTS, "scheduled", hours=2.0),
    _game(3, JETS, BILLS, "final", 3, 30, hours=-4.0),
    _game(4, RAMS, NINERS, "who-knows"),
]}


def balldontlie_answering(monkeypatch, card=None, swept=("nfl",), **settings_kw):
    """BALLDONTLIE answering every games request with `card`; every request
    recorded. A fresh day: every followed league due."""
    import balldontlie
    import balldontlie_scores
    import live_sources

    asked = []

    async def _json(url, headers, params, timeout):
        asked.append(url.split("balldontlie.io", 1)[1])
        answer = CARD if card is None else card
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(live_sources, "_json", _json)
    monkeypatch.setattr(balldontlie_scores, "SWEPT", tuple(swept))
    monkeypatch.setattr(stories, "recent_demand", lambda now: True)
    settings_kw.setdefault("balldontlie_api_key", "k")
    settings_kw.setdefault("balldontlie_tier", "goat")
    patched = with_settings(monkeypatch, **settings_kw)
    for module in (balldontlie, balldontlie_scores, live_sources):
        monkeypatch.setattr(module, "settings", patched)
    return asked


def signals():
    import balldontlie_scores

    return balldontlie_scores.make_signals()(client=__import__("balldontlie").Client())


def sports(monkeypatch, card=None):
    balldontlie_answering(monkeypatch, card)
    return run(signals().collect(8))


def test_the_score_is_on_the_card_and_kept_out_of_the_title(monkeypatch):
    """§135, at the owner's direction: "it should include score so people can
    have updates on current sports events happening". The score reaches the
    tile as a line written in code from the scoreboard - never as words the
    composer wrote once and nobody updates, which is how §88's final score
    for a game in its third quarter happened."""
    rows = {r.subject: r for r in sports(monkeypatch)}
    assert rows["Chiefs vs Broncos"].live_line == "Live \u00b7 Chiefs 21\u20137 Broncos"
    assert rows["Chiefs vs Broncos"].live_status == live_facts.IN_PROGRESS
    assert rows["Jets vs Bills"].live_line == "Final \u00b7 Jets 3\u201330 Bills"
    assert rows["Eagles vs Giants"].live_line.startswith("Starts ")
    for row in rows.values():
        # The composer is told the score and told where it goes.
        assert "keep it out of the title" in row.observation
        # A tile written without the composer never carries it either.
        tile = stories.template(row)
        assert not any(ch.isdigit() for ch in tile.title + tile.angle)
        assert tile.live_line == row.live_line


def test_the_score_on_a_held_tile_moves_with_the_game(monkeypatch):
    """A tile keeps its title and its clock across sweeps; its score line and
    its question move with the game, or the card is §88 on a timer."""
    first = {r.subject: r for r in sports(monkeypatch)}["Chiefs vs Broncos"]
    held = stories.template(first, now=1000.0)
    later = dataclasses.replace(first, live_line="Final \u00b7 Chiefs 31\u201317 Broncos",
                                live_status=live_facts.FINAL, outcome_pending=False)
    moved = stories._seen_again(held, later, 2000.0)
    assert moved.title == held.title and moved.first_seen == held.first_seen
    assert moved.live_line.startswith("Final")
    assert moved.live_as_of == 2000.0
    assert moved.query == stories.sports_query(first.subject, live_facts.FINAL)
    assert not moved.outcome_pending


def test_a_game_in_progress_is_pushed_hardest_and_a_finished_one_least(monkeypatch):
    rows = {r.subject: r for r in sports(monkeypatch)}
    assert rows["Chiefs vs Broncos"].strength > rows["Eagles vs Giants"].strength
    assert rows["Eagles vs Giants"].strength > rows["Jets vs Bills"].strength


def test_an_unfinished_game_is_marked_unfinished(monkeypatch):
    rows = {r.subject: r for r in sports(monkeypatch)}
    assert rows["Chiefs vs Broncos"].outcome_pending
    assert rows["Eagles vs Giants"].outcome_pending
    assert not rows["Jets vs Bills"].outcome_pending


def test_a_game_whose_state_cannot_be_read_is_not_offered(monkeypatch):
    """`unknown` is not "probably fine" - it is the state in which nothing may
    be said. A game FAM cannot describe is a game it does not offer."""
    assert "Rams vs Niners" not in {r.subject for r in sports(monkeypatch)}


def test_a_spent_minute_is_said_not_sent(monkeypatch):
    """BALLDONTLIE's plan is a per-minute limit (§271): a request past it
    raises before anything is sent, rather than being refused by the
    provider in a reply that reads like a quiet day."""
    import balldontlie

    asked = balldontlie_answering(monkeypatch, balldontlie_tier="free")
    client = balldontlie.Client()
    for _ in range(balldontlie.PER_MINUTE["free"]):
        client.pace()
    with pytest.raises(balldontlie.RateLimited):
        run(client.get(balldontlie.LEAGUES["nfl"], "games", "games", {}))
    assert asked == []


def test_the_provider_s_own_limit_reply_is_an_outage_not_an_empty_card(monkeypatch):
    """A 429 read as a card would be a day with no games - §89's failure
    exactly. A sweep that lost every league raises."""
    import balldontlie
    import live_sources

    balldontlie_answering(monkeypatch, card=live_sources.ProviderHTTPError(
        429, "api.balldontlie.io/nfl/v1/games"))
    with pytest.raises(balldontlie.RateLimited):
        run(signals().collect(8))


def test_every_sweep_request_is_counted_and_a_league_not_due_is_not_asked(monkeypatch):
    import balldontlie_scores

    asked = balldontlie_answering(monkeypatch, swept=("nfl", "nba"))
    source = signals()
    run(source.collect(8))
    assert sorted(asked) == ["/nba/v1/games", "/nfl/v1/games"]
    run(source.collect(8))
    assert len(asked) == 2, "a league that is not due yet was swept again"
    # And the card is kept for the episode lookup to read before it spends.
    assert set(balldontlie_scores.CARDS) == {"nfl", "nba"}


def test_balldontlie_without_a_key_is_off_and_says_which_key(monkeypatch):
    balldontlie_answering(monkeypatch, balldontlie_api_key="")
    ok, why = signals().diagnose()
    assert not ok and "BALLDONTLIE_API_KEY" in why


# --------------------------------------------------------------------------
# GDELT and the trending registry
# --------------------------------------------------------------------------
def test_gdelt_is_the_one_source_that_needs_no_credential(monkeypatch):
    with_settings(monkeypatch, gdelt=True)
    import gdelt

    monkeypatch.setattr(gdelt, "settings", dataclasses.replace(
        config.settings, gdelt=True))
    ok, why = story_sources.GdeltSignals().diagnose()
    assert ok and "keyless" in why


def _article(title, url, country=""):
    import gdelt

    return gdelt._Result(title=title, url=url, published_date="2026-09-23",
                         highlights=[title], country=country)


#: What each GDELT query answers with. The worldwide sample carries one story
#: five outlets are running; Europe's press carries a story only it is
#: running; nobody else is running anything two outlets agree on.
GDELT_ANSWERS = {
    "theme:ECON_INFLATION": [
        _article("Fed cuts interest rates as Powell signals more to come",
                 "https://reuters.com/1", "United States"),
        _article("Powell: Fed cuts rates for the second time this year",
                 "https://cnbc.com/2", "United States"),
        _article("Federal Reserve cuts rates; Powell hints at December",
                 "https://bbc.co.uk/3", "United Kingdom"),
        _article("Markets rally as Powell and the Fed cut rates",
                 "https://nikkei.com/4", "Japan"),
        _article("Fed rate cut: what Powell said", "https://dw.com/5", "Germany"),
        _article("A local bakery wins an award", "https://local.com/6",
                 "United States"),
    ],
    "(sourcecountry:unitedkingdom OR sourcecountry:france OR "
    "sourcecountry:germany OR sourcecountry:spain OR sourcecountry:italy OR "
    "sourcecountry:netherlands OR sourcecountry:poland OR "
    "sourcecountry:ireland)": [
        _article("Rail strike shuts Paris and Lyon stations", "https://lemonde.fr/7",
                 "France"),
        _article("French rail strike: Paris stations closed", "https://france24.com/8",
                 "France"),
        _article("Paris rail strike strands commuters across France",
                 "https://theguardian.com/9", "United Kingdom"),
    ],
}


@pytest.fixture
def gdelt_answering(monkeypatch):
    import gdelt

    monkeypatch.setattr(gdelt, "settings", dataclasses.replace(
        config.settings, gdelt=True))
    volumes = {"ECON_INFLATION": 90.0, "SPORTS": 30.0}

    async def volume_for(theme, timeout):
        return volumes.get(theme, 0.0)

    asked: list = []

    async def artlist(query, limit, hours, timeout):
        asked.append(query)
        return list(GDELT_ANSWERS.get(query, []))

    monkeypatch.setattr(gdelt, "volume_for", volume_for)
    monkeypatch.setattr(gdelt, "artlist", artlist)
    return asked


def test_gdelt_finds_stories_not_themes(gdelt_answering):
    """§135. The row used to be fifteen fixed themes - "inflation", "sport" -
    ranked by volume, so it read the same every day. It is the stories now:
    headlines grouped by what they share, ranked by how many outlets run
    each, with a singleton scoop dropped."""
    rows = run(story_sources.GdeltSignals().collect(32))
    subjects = [r.subject for r in rows]
    assert len(rows) == 2, subjects
    fed, strike = rows
    assert "Fed" in fed.subject and "Powell" in fed.subject or "rates" in fed.subject
    assert fed.coverage == 5 and strike.coverage == 3
    assert fed.strength == 1.0 > strike.strength > 0
    assert "5 different outlets" in fed.observation
    assert "bakery" not in " ".join(subjects)
    assert {"fed", "powell"} <= set(fed.keywords)
    assert all(stories._safe(r.observation) for r in rows)


def test_a_story_knows_where_it_is_trending(gdelt_answering):
    """Worldwide when the press of several regions runs it; a region's when
    only that region's press does."""
    import geography

    fed, strike = run(story_sources.GdeltSignals().collect(32))
    assert geography.scope_for(fed.countries, fed.region_hint)[0] == geography.WORLD
    scope, key, label = geography.scope_for(strike.countries, strike.region_hint)
    assert (scope, label) in (("region", "Europe"), ("country", "France"))


def test_every_region_s_press_is_asked(gdelt_answering):
    import gdelt
    import geography

    run(story_sources.GdeltSignals().collect(32))
    asked = " ".join(gdelt_answering)
    for region in geography.REGIONS:
        assert gdelt.region_query(region) in asked, region
    assert "theme:ECON_INFLATION" in gdelt_answering


def test_a_gdelt_sweep_that_lost_every_request_is_an_outage(monkeypatch):
    import gdelt

    monkeypatch.setattr(gdelt, "settings", dataclasses.replace(
        config.settings, gdelt=True))

    async def volume_for(theme, timeout):
        return 10.0

    async def artlist(query, limit, hours, timeout):
        raise RuntimeError("429")

    monkeypatch.setattr(gdelt, "volume_for", volume_for)
    monkeypatch.setattr(gdelt, "artlist", artlist)
    with pytest.raises(RuntimeError, match="GDELT requests failed"):
        run(story_sources.GdeltSignals().collect(32))


@pytest.fixture
def trending_registry():
    """Registered sources survive `trending.reset()` - that clears the cached
    feed, not the registry - so anything registered here has to be taken out
    by name. Left behind, it leaks into `trending.report()` in another file
    and fails a test about a *different* deployment's configuration."""
    import trending

    before = list(trending._SOURCES)
    trending.reset()
    yield trending
    trending.reset()
    trending._SOURCES[:] = before


def test_the_trending_registry_reaches_the_pool_as_attention_signals(trending_registry):
    """`TRENDING_SOURCE=fake` still works, and still produces the row it
    always did - the registry became one source among four rather than being
    deprecated, so nothing that was configured stops being configured."""
    trending = trending_registry
    trending.register(trending.FakeTrendingSource())
    if True:
        rows = run(story_sources.TrendingRegistrySignals().collect(6))
        assert rows
        assert rows[0].domain == stories.ATTENTION
        assert rows[0].strength > rows[-1].strength, "the feed's own order is kept"
        # And the row it produces is the row that shipped before the pool
        # existed: the registry already wrote a question and a one-line
        # reason, and a templated tile uses both rather than overwriting them
        # with something more generic. Putting a layer in front of a
        # configured source must not make that source's output worse.
        tile = stories.template(rows[0])
        assert tile.query == "why cutting one undersea cable can slow a whole " \
                             "country's internet"
        assert tile.angle == rows[0].observation


def test_a_broken_trending_source_raises_rather_than_looking_empty(trending_registry):
    """"Broke" and "had nothing" are different sentences. Collapsing them is
    how an outage gets shipped as a quiet miss."""
    trending = trending_registry

    class Boom(trending.TrendingSource):
        name = "boom"

        def diagnose(self):
            return True, "ready"

        async def fetch(self, limit):
            raise RuntimeError("upstream 500")

    trending.register(Boom())
    with pytest.raises(RuntimeError):
        run(story_sources.TrendingRegistrySignals().collect(6))


# --------------------------------------------------------------------------
# the rule the whole file exists for
# --------------------------------------------------------------------------
def test_the_status_vocabulary_is_the_one_live_facts_already_closed():
    """A free string misses every comparison silently. The sports source maps
    at the provider boundary into the same four words everything else in FAM
    switches on."""
    source = signals()
    assert set(source.STRENGTH) <= {
        live_facts.SCHEDULED, live_facts.IN_PROGRESS, live_facts.FINAL}
    assert live_facts.UNKNOWN not in source.STRENGTH


def test_the_sports_card_is_ordered_deterministically(monkeypatch):
    """Three strength values over a whole day's fixtures means most of them
    tie, and a stable sort hands the tie to whatever the API happened to list
    first - so the tiles changed between sweeps for no reason a listener could
    see. The same card must produce the same tiles twice."""
    first = sports(monkeypatch)
    import balldontlie_scores

    monkeypatch.setattr(balldontlie_scores, "SWEPT_AT", {})
    shuffled = {"data": list(reversed(CARD["data"]))}
    second = sports(monkeypatch, card=shuffled)
    assert [s.subject for s in first] == [s.subject for s in second]


def test_only_a_followed_league_is_swept(monkeypatch):
    """Since §191 only the leagues the owner named (`balldontlie_scores.SWEPT`)
    are swept; every other league is looked up on demand by the episode that
    asks about it, never swept."""
    import balldontlie_scores

    assert "mls" not in balldontlie_scores.SWEPT
    asked = balldontlie_answering(monkeypatch,
                                  swept=balldontlie_scores.SWEPT)
    run(signals().collect(8))
    assert asked and not any(path.startswith("/mls/") for path in asked)
    assert {p.split("/")[1] for p in asked} <= {
        balldontlie_scores.LEAGUES[k].prefix.split("/")[1]
        for k in balldontlie_scores.SWEPT}


def test_a_swept_quote_needs_its_own_time_and_a_recent_sweep():
    """§267 review: without a trade time `to_facts` would date it now and it
    would never age; and a close kept through a weekend of failed sweeps is
    not one a matching session vouches for."""
    import time as _time

    import live_sources

    now = _time.time()
    good = {"data": {"c": 1.0, "t": now - 60}, "is_open": False, "at": now - 60}
    assert live_sources.usable_sweep(good, now)
    assert not live_sources.usable_sweep({**good, "data": {"c": 1.0}}, now)
    assert not live_sources.usable_sweep({**good, "is_open": None}, now)
    assert not live_sources.usable_sweep({**good, "at": now - 3 * 86400}, now)
    assert not live_sources.usable_sweep(None, now)
