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
whatever happens next. **No source here ever puts a result in an
`observation`** - not a score, not a winner, not a settled market - because
that text is the only thing the composer is given and a result in it becomes a
result on a tile, and from there an episode written around a claim nobody
checked. PROBLEMS.md §88 is what that costs.

That rule has one place it looks odd and is still right: a game that has
finished. API-Sports knows the score, and this file deliberately does not pass
it on. The tile says "what decided it"; the *episode* finds out, from dated
evidence, on the research path built for exactly that.

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
    """Coverage volume across a fixed set of GKG themes, plus the headlines.

    `gdelt.py` already sweeps these themes for the Trending row and turns each
    into a templated question. Its own docstring names the better version and
    leaves it undone: *one model call per refresh window turning the top
    themes and their leading headlines into real questions*. This is the half
    that makes that possible - it measures the theme and then goes and reads
    what is actually being written under it, so the composer has something
    specific to write about rather than the word "sport".

    **The identity trade, stated because it is the one thing here that is not
    obviously right.** A story's id is hashed from its subject, and the
    subject is the *theme* - so the tile that appears under "energy" keeps its
    id while the leading headlines underneath it move on. That is what keeps
    fatigue, impressions and the cooldown working on a stable thing, and it is
    why the attention shelf life is the shortest of the broad domains: after
    it, the theme is retired, cools off, and comes back with a fresh angle
    rather than silently acquiring one.
    """

    name = "GDELT"
    domain = stories.ATTENTION
    cost_per_refresh = 0.0
    #: Keyless and generous, so this may run on the pool's own clock.
    min_interval_seconds = 0.0

    #: How many themes get a headline read after the volume sweep. Each one is
    #: a second request, so this is the knob between "specific" and "cheap".
    HEADLINE_THEMES = 6
    HEADLINES_PER_THEME = 4

    def diagnose(self) -> tuple[bool, str]:
        import gdelt

        ok, why = gdelt.available()
        if not ok:
            return False, f"GDELT is switched off ({why})"
        return True, "GDELT DOC 2.0, keyless; not verified from this machine"

    async def verify(self) -> tuple[bool, str]:
        import gdelt

        return await gdelt.GdeltTrendingSource().verify()

    async def collect(self, limit: int) -> list:
        import gdelt

        timeout = float(settings.gdelt_timeout_seconds)

        async def measure(theme: str, subject: str):
            try:
                return subject, theme, await gdelt.volume_for(theme, timeout)
            except Exception as exc:  # noqa: BLE001 - one theme must not sink the sweep
                log.debug("stories/gdelt: theme %s failed: %s", theme, exc)
                return subject, theme, -1.0

        measured = [row for row in await asyncio.gather(
            *(measure(theme, subject) for theme, subject in gdelt.THEMES))
            if row[2] > 0]
        if not measured:
            return []

        measured.sort(key=lambda row: -row[2])
        top = measured[:min(limit, self.HEADLINE_THEMES)]
        peak = top[0][2] or 1.0

        async def headlines(subject: str):
            try:
                results = await gdelt.retrieve(
                    f'"{subject}"', limit=self.HEADLINES_PER_THEME, recency_days=1)
            except Exception as exc:  # noqa: BLE001
                log.debug("stories/gdelt: headlines for %r failed: %s", subject, exc)
                return []
            return [r.title for r in results if getattr(r, "title", "")]

        heads = await asyncio.gather(*(headlines(subject) for subject, _t, _v in top))

        now = datetime.now(timezone.utc)
        out = []
        for (subject, _theme, volume), titles in zip(top, heads):
            said = (f"coverage of {subject} is running at about "
                    f"{volume / peak:.0%} of today's busiest subject")
            if titles:
                said += (". What is being written under it right now: "
                         + "; ".join(t[:120] for t in titles[:self.HEADLINES_PER_THEME]))
            out.append(stories.Signal(
                subject=subject,
                observation=said,
                domain=self.domain,
                source=self.name,
                tags=_tags(f"{subject} {' '.join(titles[:3])}"),
                strength=min(1.0, volume / peak),
                as_of=now,
            ))
        return out


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
    """Today's card for the sports this deployment serves.

    **The score is deliberately not passed on.** API-Sports has it, and
    putting it in an `observation` would put it on a tile - a claim about an
    outcome made by the one layer that must never make one, on the one subject
    where FAM has already got it wrong (PROBLEMS.md §88: a final score written
    for a game in its third quarter). What the signal carries is the *status*,
    from the provider's own closed vocabulary, and the tile is written to be
    true either way. The episode finds out what happened, from dated evidence,
    on the path built for it.

    Free tier is 100 requests a day, which is why this sweeps at most once
    every two hours and asks for one date per sport rather than one per
    league: at fifteen-minute windows it would spend its whole daily quota on
    a browse page nobody had opened.
    """

    name = "API-Sports"
    domain = stories.SPORTS
    #: Flat-rate plan; the bill is not per call.
    cost_per_refresh = 0.0
    min_interval_seconds = 7200.0

    def diagnose(self) -> tuple[bool, str]:
        import live_sources

        if not settings.api_sports_key:
            return False, "API_SPORTS_KEY is not set, so sports stories are off"
        for key in self.sports():
            if key not in live_sources.SPORTS:
                return False, (f"STORIES_SPORTS lists {key!r}, which is not one of "
                               f"{', '.join(sorted(live_sources.SPORTS))}")
        return True, (f"API_SPORTS_KEY present, sweeping "
                      f"{', '.join(self.sports())} (not verified from this machine)")

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

        headers = {"x-apisports-key": settings.api_sports_key}
        timeout = float(settings.live_timeout_seconds)
        today = datetime.now(timezone.utc).date().isoformat()

        async def card(key: str):
            sport = live_sources.SPORTS[key]
            try:
                data = await _json(f"{sport.host}/{sport.path}", headers,
                                   {"date": today}, timeout)
            except Exception as exc:  # noqa: BLE001 - one sport is not the sweep
                log.debug("stories/api-sports: %s failed: %s", key, exc)
                return sport, []
            return sport, (data or {}).get("response", []) or []

        now = datetime.now(timezone.utc)
        out = []
        for sport, rows in await asyncio.gather(*(card(k) for k in self.sports()
                                                 if k in live_sources.SPORTS)):
            for row in rows:
                signal = self._signal(sport, row, now)
                if signal is not None:
                    out.append(signal)
        if not out:
            return []
        out.sort(key=lambda s: -s.strength)
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

    def _signal(self, sport, row: dict, now: datetime):
        import live_sources

        # A staticmethod on the live-facts adapter: the row shape is that
        # provider's, so the one place that already knows how to read it is
        # the right place to keep knowing.
        home, away = live_sources.ApiSportsSource._team_names(row)  # noqa: SLF001
        if not (home and away):
            return None
        status = sport.statuses.get(_status_code(row), live_facts.UNKNOWN)
        if status == live_facts.UNKNOWN:
            # `unknown` is not "probably fine" - it is the state in which
            # nothing may be said. A game FAM cannot describe is a game it
            # does not offer.
            return None
        said = {
            live_facts.IN_PROGRESS: "is under way right now",
            live_facts.SCHEDULED: "is on today's card and has not started",
            live_facts.FINAL: "was played earlier today and has finished",
        }[status]
        return stories.Signal(
            subject=f"{home} vs {away}",
            observation=(f"this {sport.key.replace('-', ' ')} fixture {said}. "
                         f"Nothing here says what the score is or who is "
                         f"ahead, and you must not imply either."),
            domain=self.domain,
            source=self.name,
            tags=_tags(f"{home} {away} {sport.key}", "sports"),
            strength=self.STRENGTH.get(status, 0.5),
            as_of=now,
            outcome_pending=status != live_facts.FINAL,
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
