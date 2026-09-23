"""The four live sources behind myFAM's story pool.

    GDELT        what the world's press is writing about   (keyless)
    Finnhub      what moved in the market today            (free key)
    Polymarket   what people are betting on                (keyless)
    API-Sports   what is being played today                (free/cheap key)

Each one answers "what should FAM offer today" from a different direction, and
the four together are what stop the browse page being four rows of the same
corner of the world.

What a source may return, and what it may not
---------------------------------------------
A `Signal` is a **measurement**, never a report. Coverage volume, a traded
price, a betting line, a fixture on today's card: all things that are true
whatever happens next. **No source puts a result in an `observation` for the
composer to write into a title** - because a title is written once and read
for hours, and a result in it becomes a result on a tile long after it
stopped being true. PROBLEMS.md §88 is what that costs.

**One exception, and it is not in the title** (§135, at the owner's
direction). API-Sports passes the score on now, as `Signal.live_line`: a line
written in code from the scoreboard on every sweep, drawn beside the tile and
never composed into it. A score that is re-read every fourteen minutes and
says how old it is is a different thing from a score frozen into a sentence.

GDELT is no longer a list of themes. It finds the actual stories - headlines
grouped by `news_clusters` - worldwide and in each region's press, and counts
the outlets running each. That count is what Trending ranks on, and
`stories.corroborate` lends it to a game, a price or a market the press is
also running, so the row is ranked on everything FAM knows rather than on
each feed alone.

Why these four and not a news API
---------------------------------
Between them they are keyless, free-tier or flat-rate, so a refresh that
serves every listener costs approximately nothing, which is what makes the
whole shared-pool design work (`stories.py` has the arithmetic). A
per-article news API priced per request would put the browse page's inventory
on a meter, and an inventory on a meter is one that gets refreshed less often
than it should.

Not verified against the live services
--------------------------------------
The build container's egress proxy blocks these hosts, so every shape below is
written from the documented APIs and exercised against recorded payloads in
`tests/test_story_sources.py`. **Nothing here has made a real request.** Run
`python tools/verify_live.py` somewhere with network before believing it
works - "a key is set" is not "the key works" (PROBLEMS.md §52).
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

import httpx

import live_facts
import stories
from config import settings

log = logging.getLogger(__name__)


async def _json(url: str, headers: dict, params: dict, timeout: float):
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()


def _tags(text: str, *extra: str) -> tuple:
    """Facets for a signal, in the vocabulary the rest of the feed reasons in.

    Imported here rather than at module scope because `topics` imports
    `stories`, which imports this - the cycle only bites at import time, and a
    function-level import is the cheapest way to keep the dependency pointing
    one way. The keyword map is deliberately the same dumb one the bank uses:
    a wrong tag costs one mediocre recommendation, and a model call to
    classify each signal would cost more than the tile is worth.
    """
    import topics

    found = set(topics.tags_for_text(text))
    for tag in extra:
        if tag:
            found.add(tag)
            found.add(topics.facet_of(tag))
    return tuple(sorted(found))


# --------------------------------------------------------------------------
# GDELT - what the press is writing about
# --------------------------------------------------------------------------
class GdeltSignals(stories.StorySource):
    """The stories the world's press is running, worldwide and region by region.

    **Stories, not themes** (§135). This used to emit one signal per GKG
    theme - "inflation", "sport", "the stock market" - with the leading
    headlines pasted under it, so the Trending row was fifteen subjects that
    are always being written about and read the same every day. Now it reads
    the articles and finds the stories in them:

    1. **Which themes are hot** - `gdelt.volume_for` over `gdelt.THEMES`, the
       sweep it always did. Cheap, and it decides where the worldwide sample
       is drawn from.
    2. **What is being run** - `gdelt.discover`: the recent articles under
       the hottest themes (the worldwide sample) and under each region's own
       press (`geography.GDELT_SOURCES`), a few hundred headlines in all.
    3. **Which of them are one story** - `news_clusters.cluster` groups
       headlines that share their people, places and organisations, and
       counts the **distinct outlets** running each group. That count is the
       story's popularity, and it is what Trending ranks on.

    Geography comes with it for free: every article carries its publisher's
    country, so a story knows whether the whole world is running it or one
    region's press is - `geography.scope_for`. A story found only in one
    region's sweep keeps that region when its rows carry no country.

    **Every region gets a voice.** The worldwide sample is dominated by the
    largest newsrooms, so a region's top stories are guaranteed places before
    the rest are filled by popularity - otherwise "trending regionally" would
    be whichever region publishes most in English.

    Identity is by fingerprint, not by headline: a story's leading headline
    changes between sweeps, and `stories._adopt_identity` matches it to the
    tile it already has.
    """

    name = "GDELT"
    domain = stories.ATTENTION
    cost_per_refresh = 0.0
    #: Keyless and generous, so this may run on the pool's own clock.
    min_interval_seconds = 0.0
    #: Room for the world and every region, rather than one provider's eight.
    max_signals = 32
    #: Twenty-odd requests, six at a time. Nobody waits on a sweep, and one
    #: cut short by the shared ceiling is a sweep that found nothing.
    timeout_seconds = 45.0

    #: How many of the hottest themes the worldwide sample is drawn from.
    HOT_THEMES = 8
    #: How far back a trending story may have been published.
    WINDOW_HOURS = 12
    #: Articles read per query. The extra rows cost nothing - it is the same
    #: request - and they are what clustering and the country split measure.
    ARTICLES_PER_QUERY = 75
    #: Places guaranteed to each region's own top stories.
    PER_REGION = 2
    #: Headlines quoted to the composer per story.
    HEADLINES_PER_STORY = 4

    def diagnose(self) -> tuple[bool, str]:
        import gdelt

        ok, why = gdelt.available()
        if not ok:
            return False, f"GDELT is switched off ({why})"
        return True, "GDELT DOC 2.0, keyless; not verified from this machine"

    async def verify(self) -> tuple[bool, str]:
        import gdelt

        return await gdelt.GdeltTrendingSource().verify()

    async def hot_themes(self, timeout: float) -> list:
        import gdelt

        async def measure(theme: str):
            try:
                return theme, await gdelt.volume_for(theme, timeout)
            except Exception as exc:  # noqa: BLE001 - one theme must not sink the sweep
                log.debug("stories/gdelt: theme %s failed: %s", theme, exc)
                return theme, -1.0

        measured = [row for row in await asyncio.gather(
            *(measure(theme) for theme, _subject in gdelt.THEMES)) if row[1] > 0]
        measured.sort(key=lambda row: (-row[1], row[0]))
        return [theme for theme, _v in measured[:self.HOT_THEMES]]

    async def collect(self, limit: int) -> list:
        import gdelt
        import geography
        import news_clusters

        timeout = float(settings.gdelt_timeout_seconds)
        themes = await self.hot_themes(timeout)
        articles, scope_of, (failed, asked) = await gdelt.discover(
            themes, timeout, hours=self.WINDOW_HOURS,
            per_query=self.ARTICLES_PER_QUERY)
        if asked and failed == asked:
            # Every request failed: an outage, which `collect` must raise so
            # the report says so, rather than a quiet day with no news.
            raise RuntimeError(f"all {asked} GDELT requests failed")
        groups = news_clusters.cluster(articles, scope_of=scope_of)
        if not groups:
            return []

        now = datetime.now(timezone.utc)
        signals = [self._signal(g, now) for g in groups]
        # Popularity, log-scaled so the one story every outlet is running
        # does not flatten the rest of the row to nothing.
        peak = math.log1p(max(s.coverage for s in signals) or 1)
        signals = [replace(s, strength=round(min(1.0, math.log1p(s.coverage) / peak), 3))
                   for s in signals]

        def home(signal) -> str:
            """The region a story belongs to, or "world"."""
            scope, key, _label = geography.scope_for(signal.countries,
                                                      signal.region_hint)
            if scope == geography.WORLD:
                return geography.WORLD
            return key if scope == "region" else geography.REGION_OF.get(key, "")

        chosen: list = []
        seen: set = set()
        for region in geography.REGIONS:
            for signal in [s for s in signals if home(s) == region][:self.PER_REGION]:
                seen.add(signal.subject)
                chosen.append(signal)
        for signal in signals:
            if len(chosen) >= limit:
                break
            if signal.subject not in seen:
                seen.add(signal.subject)
                chosen.append(signal)
        chosen.sort(key=lambda s: (-s.coverage, s.subject))
        return chosen[:limit]

    def _signal(self, group, now: datetime) -> "stories.Signal":
        headline = group.headline()
        titles = [headline] + [t for t in group.titles if t != headline]
        region = group.main_scope()
        countries = stories.country_shares(group.countries)
        said = (f"{group.outlets} different outlets"
                + (f" in {len({c for c, _s in countries})} countries" if countries else "")
                + f" have run this in the last {self.WINDOW_HOURS} hours. "
                + "What they are saying: "
                + "; ".join(t[:120] for t in titles[:self.HEADLINES_PER_STORY]))
        subject = headline[:140]
        return stories.Signal(
            subject=subject,
            observation=said,
            domain=self.domain,
            source=self.name,
            tags=_tags(" ".join(titles[:3])),
            strength=0.5,
            as_of=now,
            url=group.urls[0] if group.urls else "",
            countries=countries,
            keywords=group.keywords(),
            coverage=group.outlets,
            region_hint="" if region in ("", "world") else region,
            suggested_query=f"{subject}: what is happening and why it matters",
            suggested_angle=f"Running across {group.outlets} outlets right now",
        )


class TrendingRegistrySignals(stories.StorySource):
    """Whatever `TRENDING_SOURCE` installed, as attention signals.

    The Trending row shipped with its own small registry (`trending.py`),
    which is where `TRENDING_SOURCE=fake` lives and where a future feed would
    be added. Rather than deprecate it - and lose the fake the demo and the
    preview both use - the pool reads it as one more source.

    One refresh of the pool is one refresh of that registry, so nothing is
    fetched twice, and `trending.cached()` stays exactly as true as it was.
    """

    name = "trending registry"
    domain = stories.ATTENTION
    cost_per_refresh = 0.0
    min_interval_seconds = 0.0

    def diagnose(self) -> tuple[bool, str]:
        import trending

        if not settings.trending:
            return False, "TRENDING=0"
        ready = [s for s in trending.sources() if s.available()]
        if not ready:
            return False, ("no world-trending source is configured "
                           "(TRENDING_SOURCE is empty)")
        return True, f"trending registry serving from {ready[0].name}"

    async def verify(self) -> tuple[bool, str]:
        import trending

        for source in trending.sources():
            if source.available():
                return await source.verify()
        return False, self.diagnose()[1]

    async def collect(self, limit: int) -> list:
        import trending

        feed = await trending.refresh(limit)
        if feed.outcome == trending.TIMEOUT:
            raise TimeoutError(feed.detail)
        if feed.outcome == trending.SOURCE_FAILED:
            raise RuntimeError(feed.detail)
        out = []
        for item in feed.items[:limit]:
            # `why_now` is the source's own one-line reason, which is exactly
            # what a signal's observation is. The item's `query` rides along
            # so a templated tile is word for word the row that shipped
            # before the pool existed - a deployment with no model key sees no
            # change at all.
            out.append(stories.Signal(
                subject=item.subject,
                observation=(item.why_now
                             or f"{item.subject} is being widely written about"),
                domain=(stories.PREDICTION
                        if item.kind == trending.PREDICTION_MARKET
                        else stories.ATTENTION),
                source=item.source or self.name,
                tags=tuple(item.tags) or _tags(f"{item.subject} {item.query}"),
                strength=max(0.2, 1.0 - 0.1 * max(0, item.rank - 1)),
                as_of=item.as_of,
                url=item.url,
                suggested_query=item.query,
                suggested_angle=item.why_now,
            ))
        return out


# --------------------------------------------------------------------------
# Finnhub - what moved today
# --------------------------------------------------------------------------
#: The default watchlist, and why it is a list rather than a screen.
#:
#: Finnhub's free tier has no "biggest movers" endpoint - that is a paid
#: screener - so the honest cheap version is to quote a fixed, named set and
#: report the ones that actually moved. A fixed list is also the thing a
#: deployment most obviously wants to change, which is what
#: `FINNHUB_WATCHLIST` is for.
#:
#: The names are here so a tile can say "Nvidia" rather than "NVDA". Looking
#: each one up through `/stock/profile2` would be one more request per symbol
#: per sweep to learn something that does not change.
WATCHLIST = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia",
    "GOOGL": "Alphabet", "AMZN": "Amazon", "META": "Meta",
    "TSLA": "Tesla", "JPM": "JPMorgan", "XOM": "ExxonMobil",
    "SPY": "the S&P 500", "QQQ": "the Nasdaq 100", "GLD": "the gold price",
}


class FinnhubSignals(stories.StorySource):
    """One quote per watchlist symbol; a signal for whatever actually moved.

    **A price is a measurement and never a verdict**, which is the whole
    reason this is safe to put on a tile: "Nvidia is down about six percent
    today" is true regardless of why, and the episode is what finds out why.
    The threshold is what keeps it interesting - a market where nothing moved
    more than a percent has no story in it, and offering one anyway is how a
    browse page fills up with tiles nobody wants.

    Delayed by about twenty minutes on the free tier. That is fine here and is
    not fine in `live_sources.FinnhubSource`, which says so out loud: a browse
    tile about a move is not a claim about the price this second.
    """

    name = "Finnhub"
    domain = stories.MARKETS
    #: Free tier; $0 and 60 requests a minute. The watchlist is a dozen.
    cost_per_refresh = 0.0
    #: Half an hour. The market does not move enough in fifteen minutes to
    #: change what is worth offering, and this is the source with the most
    #: requests per sweep.
    min_interval_seconds = 1800.0

    BASE = "https://finnhub.io/api/v1"

    def diagnose(self) -> tuple[bool, str]:
        if not settings.finnhub_key:
            return False, "FINNHUB_KEY is not set, so market stories are off"
        return True, "FINNHUB_KEY present (not verified from this machine)"

    async def verify(self) -> tuple[bool, str]:
        import live_sources

        return await live_sources.FinnhubSource().verify()

    def watchlist(self) -> list:
        raw = (settings.finnhub_watchlist or "").strip()
        if not raw:
            return list(WATCHLIST)
        return [s.strip().upper() for s in raw.split(",") if s.strip()][:24]

    async def collect(self, limit: int) -> list:
        timeout = float(settings.live_timeout_seconds)
        symbols = self.watchlist()

        async def quote(symbol: str):
            try:
                return symbol, await _json(
                    f"{self.BASE}/quote", {},
                    {"symbol": symbol, "token": settings.finnhub_key}, timeout)
            except Exception as exc:  # noqa: BLE001 - one symbol is not the sweep
                log.debug("stories/finnhub: %s failed: %s", symbol, exc)
                return symbol, None

        rows = await asyncio.gather(*(quote(s) for s in symbols))
        threshold = float(settings.stories_market_move_percent)
        moved = []
        for symbol, data in rows:
            change = (data or {}).get("dp")
            price = (data or {}).get("c")
            if change is None or price in (None, 0):
                continue
            if abs(float(change)) < threshold:
                continue
            moved.append((symbol, float(change), float(price)))
        if not moved:
            return []

        moved.sort(key=lambda row: -abs(row[1]))
        peak = abs(moved[0][1]) or 1.0
        now = datetime.now(timezone.utc)
        out = []
        for symbol, change, price in moved[:limit]:
            label = WATCHLIST.get(symbol, symbol)
            way = "up" if change >= 0 else "down"
            out.append(stories.Signal(
                subject=label,
                observation=(f"{label} is {way} about {abs(change):.1f} percent "
                             f"today, at around {price:.2f}. That is a price "
                             f"move on a roughly twenty-minute delay, and "
                             f"nothing here says why."),
                domain=self.domain,
                source=self.name,
                tags=_tags(label, "money"),
                strength=min(1.0, abs(change) / peak),
                as_of=now,
            ))
        return out


# --------------------------------------------------------------------------
# Polymarket - what people are betting on
# --------------------------------------------------------------------------
class PolymarketSignals(stories.StorySource):
    """The most-traded open markets. Forecasts, and never results.

    The broadest single keyless source there is - elections, sport, economics
    and geopolitics through one endpoint - and the one that needs the loudest
    warning attached, for the reason `live_sources.PolymarketSource` states:
    a price that moves with an outcome *reads* like the outcome. Every signal
    here is `outcome_pending`, the domain note tells the composer it is a
    forecast and often a wrong one, and `DOMAIN_WEIGHT` ranks it last so a
    quiet news day does not turn the browse page into a betting slip.

    What it is genuinely good at: surfacing the question everybody has already
    decided is the question. Volume is a crowd saying "this is the thing", and
    the tile asks what would actually settle it - which is an episode, where
    the price is not.
    """

    name = "Polymarket"
    domain = stories.PREDICTION
    cost_per_refresh = 0.0
    min_interval_seconds = 1800.0

    def diagnose(self) -> tuple[bool, str]:
        if not settings.stories_polymarket:
            return False, "STORIES_POLYMARKET=0"
        if not settings.polymarket_base:
            return False, "POLYMARKET_BASE is empty"
        return True, "Polymarket public API, keyless (not verified from this machine)"

    async def verify(self) -> tuple[bool, str]:
        import live_sources

        return await live_sources.PolymarketSource().verify()

    async def collect(self, limit: int) -> list:
        data = await _json(
            f"{settings.polymarket_base}/markets", {},
            {"limit": max(limit * 3, 20), "closed": "false", "active": "true",
             "order": "volume24hr", "ascending": "false"},
            float(settings.live_timeout_seconds))
        rows = data if isinstance(data, list) else []
        picked = []
        for row in rows:
            question = str(row.get("question") or "").strip()
            if not question:
                continue
            price = _first_price(row)
            if price is None:
                continue
            volume = _as_float(row.get("volume24hr")) or _as_float(row.get("volume")) or 0.0
            picked.append((question, price, volume, row))
        if not picked:
            return []

        peak = max(v for _q, _p, v, _r in picked) or 1.0
        now = datetime.now(timezone.utc)
        out = []
        for question, price, volume, row in picked[:limit]:
            percent = max(0, min(100, round(price * 100)))
            said = (f"on prediction markets this is trading around {percent} "
                    f"percent, on about {volume:,.0f} dollars of turnover in a "
                    f"day. That is what people are betting, not a reported "
                    f"result, and it is frequently wrong.")
            out.append(stories.Signal(
                subject=question[:140],
                observation=said,
                domain=self.domain,
                source=self.name,
                tags=_tags(question, "world"),
                strength=min(1.0, volume / peak),
                as_of=now,
                # A market that is open has, by definition, not resolved.
                outcome_pending=True,
            ))
        return out


def _as_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_price(row: dict) -> Optional[float]:
    for name in ("bestBid", "lastTradePrice", "outcomePrices"):
        value = (row or {}).get(name)
        if isinstance(value, str) and value.startswith("["):
            # Gamma returns outcomePrices as a JSON-encoded string on some
            # rows and as a list on others. Tolerated rather than trusted.
            value = value.strip("[]").split(",")
        if isinstance(value, list) and value:
            value = value[0]
        price = _as_float(str(value).strip('" ') if value is not None else None)
        if price is not None:
            return price
    return None


# --------------------------------------------------------------------------
# API-Sports - what is being played today
# --------------------------------------------------------------------------
class ApiSportsSignals(stories.StorySource):
    """Today's card for the sports this deployment serves - **with the score**.

    **The score is passed on now** (§135, at the owner's direction: "it
    should include score so people can have updates on current sports events
    happening"). It used to be withheld, on §88's reasoning - a tile is
    written once, before anything is researched, so a score in its title is
    true for one sweep and wrong for the rest of the game. That reasoning
    still holds for the *title*, and it is kept: the composer is told the
    score and told not to write it. What carries it is `Signal.live_line`,
    a line written **in code from the provider's own numbers on every
    sweep** - "Live · Chiefs 21–14 Bills · Third Quarter", "Final · ...",
    "Starts 20:15 UTC" - so it is never older than one sweep and the card
    says how old it is. The episode still researches the game on the tap,
    and the live lookup reads the same feed.

    **Swept on the whole daily allowance, paced** (§135). It used to sweep at
    most every two hours to keep the free tier's hundred requests a day in
    reserve. `min_interval_seconds` now comes from
    `live_sources.API_SPORTS_BUDGET`: whatever is left of the day's
    allowance, spread over what is left of the day - about every fourteen
    minutes with one sport on the free tier, and slower on a day episode
    lookups have spent a share of it. One request per sport per sweep.

    **Major leagues first.** A date request returns every fixture in the
    world; `live_sources.MAJOR_LEAGUES` puts the NFL, the NBA and the Premier
    League ahead of the third division, and a game the press is also running
    rises further when `stories.corroborate` finds it in the news sweep. The
    league's country is the tile's geography.
    """

    name = "API-Sports"
    domain = stories.SPORTS
    #: Flat-rate plan; the bill is not per call.
    cost_per_refresh = 0.0
    max_signals = 12

    @property
    def min_interval_seconds(self) -> float:
        import live_sources

        return live_sources.API_SPORTS_BUDGET.sweep_interval(len(self.sports()))

    def diagnose(self) -> tuple[bool, str]:
        import live_sources

        if not settings.api_sports_key:
            return False, "API_SPORTS_KEY is not set, so sports stories are off"
        for key in self.sports():
            if key not in live_sources.SPORTS:
                return False, (f"STORIES_SPORTS lists {key!r}, which is not one of "
                               f"{', '.join(sorted(live_sources.SPORTS))}")
        budget = live_sources.API_SPORTS_BUDGET
        return True, (f"API_SPORTS_KEY present, sweeping "
                      f"{', '.join(self.sports())} about every "
                      f"{budget.sweep_interval(len(self.sports())) / 60:.0f} min "
                      f"({budget.remaining()} of {budget.daily} requests left "
                      f"today; not verified from this machine)")

    async def verify(self) -> tuple[bool, str]:
        import live_sources

        return await live_sources.ApiSportsSource().verify()

    def sports(self) -> list:
        raw = (settings.stories_sports or "").strip()
        if raw:
            return [s.strip() for s in raw.split(",") if s.strip()][:4]
        return [settings.api_sports_sport]

    async def collect(self, limit: int) -> list:
        import live_sources

        timeout = float(settings.live_timeout_seconds) * 4
        today = datetime.now(timezone.utc).date().isoformat()

        async def card(key: str):
            sport = live_sources.SPORTS[key]
            data = await live_sources.api_sports_json(
                f"{sport.host}/{sport.path}", {"date": today}, timeout)
            rows = (data or {}).get("response", []) or []
            live_sources.remember_card(key, rows)
            return sport, rows

        out = []
        failures: list = []
        for result in await asyncio.gather(
                *(card(k) for k in self.sports() if k in live_sources.SPORTS),
                return_exceptions=True):
            if isinstance(result, BaseException):
                failures.append(result)
                continue
            sport, rows = result
            now = datetime.now(timezone.utc)
            for row in rows:
                signal = self._signal(sport, row, now)
                if signal is not None:
                    out.append(signal)
        if failures and not out:
            # Every sport failed - the allowance, the key or the host. An
            # outage, raised so the report names it, never a day with no games.
            raise failures[0]
        if not out:
            return []
        # Deterministic, because the provider's own order is not a ranking.
        out.sort(key=lambda s: (-s.strength, s.subject))
        return out[:limit]

    #: How interesting each state of a game is to somebody browsing. Under way
    #: beats about to start beats finished: the first is the only one where an
    #: episode arrives while it still matters, and the last is the one most
    #: likely to have been seen already.
    STRENGTH = {
        live_facts.IN_PROGRESS: 1.0,
        live_facts.SCHEDULED: 0.8,
        live_facts.FINAL: 0.55,
    }
    #: A game outside `MAJOR_LEAGUES` is worth this share of one inside it.
    MINOR_LEAGUE = 0.45

    def _signal(self, sport, row: dict, now: datetime):
        import geography
        import live_sources

        home, away = live_sources.ApiSportsSource._team_names(row)  # noqa: SLF001
        if not (home and away):
            return None
        status, line = live_sources.ApiSportsSource.live_line(row, sport)
        if status == live_facts.UNKNOWN or not line:
            # `unknown` is not "probably fine" - it is the state in which
            # nothing may be said. A game FAM cannot describe is a game it
            # does not offer.
            return None
        league, country = live_sources.league_of(row)
        major = live_sources.is_major(league, country)
        where = stories.normalise_country(country)
        hint = ""
        if where in ("world", "international", ""):
            where = ""
        elif where == "europe":
            where, hint = "", "europe"
        elif where not in geography.REGION_OF:
            where = ""
        stamp = now.strftime("%H:%M UTC")
        said = {
            live_facts.IN_PROGRESS: f"is under way right now: {line} (as of {stamp})",
            live_facts.SCHEDULED: f"is on today's card and has not started ({line})",
            live_facts.FINAL: f"has finished: {line}",
        }[status]
        return stories.Signal(
            subject=f"{home} vs {away}",
            observation=(f"this {sport.key.replace('-', ' ')} game"
                         + (f" in the {league}" if league else "")
                         + f" {said}. The score is shown to the listener beside "
                         "the tile and updates on its own; keep it out of the "
                         "title, the angle and the query."),
            domain=self.domain,
            source=self.name,
            tags=_tags(f"{home} {away} {sport.key} {league}", "sports"),
            strength=round(self.STRENGTH.get(status, 0.5)
                           * (1.0 if major else self.MINOR_LEAGUE), 3),
            as_of=now,
            outcome_pending=status != live_facts.FINAL,
            countries=((where, 1.0),) if where else (),
            region_hint=hint,
            live_line=line,
            live_status=status,
        )


def _status_code(row: dict) -> str:
    """The provider's short status code, wherever this sport keeps it."""
    for path in (("status", "short"), ("fixture", "status", "short"),
                 ("game", "status", "short"), ("games", "status", "short")):
        value = row
        for step in path:
            value = (value or {}).get(step) if isinstance(value, dict) else None
        if isinstance(value, str) and value:
            return value
    return ""


# --------------------------------------------------------------------------
# Installation
# --------------------------------------------------------------------------
#: Every source this build knows, in the order they are swept. Order is not a
#: ranking - `Story.push` decides that - it is only what a report reads like.
BUILDERS = {
    "gdelt": GdeltSignals,
    "trending": TrendingRegistrySignals,
    "finnhub": FinnhubSignals,
    "polymarket": PolymarketSignals,
    "api-sports": ApiSportsSignals,
}

#: What a deployment gets when `STORIES_SOURCES` is empty: all of them. Each
#: one already reports itself as not configured when its credential is absent,
#: so "all" means "whatever this machine can actually do" rather than a
#: promise that all four are live. That is the difference between a gap that
#: is visible on `/api/health` and one nobody notices.
DEFAULT_SOURCES = tuple(BUILDERS)


def configured() -> list:
    raw = (settings.stories_sources or "").strip()
    if not raw:
        return list(DEFAULT_SOURCES)
    return [name.strip().lower() for name in raw.split(",") if name.strip()]


def install() -> dict:
    """Register the configured sources. Re-runnable, and never raises.

    An unknown name is reported rather than raised: it costs one source, and
    stopping the server over a typo in a browse-page setting would be the
    wrong trade by a distance.
    """
    installed: list = []
    problems: list = []
    for name in configured():
        builder = BUILDERS.get(name)
        if builder is None:
            problems.append(
                f"STORIES_SOURCES names {name!r}, which is not a source this "
                f"build knows. Known: {', '.join(sorted(BUILDERS))}.")
            continue
        try:
            source = builder()
            source._fam_installed = True  # noqa: SLF001 - our own marker
            stories.register(source)
            installed.append(source.name)
        except Exception as exc:  # noqa: BLE001 - must not stop boot
            problems.append(f"story source {name!r} failed to build: {exc}")
    for problem in problems:
        log.error("stories: %s", problem)
    return {"installed": installed, "problems": problems,
            "configured": configured()}
