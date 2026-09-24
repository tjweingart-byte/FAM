"""GDELT: a second retrieval index, and the world-trending feed.

Two jobs from one upstream, because GDELT answers two different questions and
FAM has two different clocks for them:

* **Evidence** (`retrieve`) - a second index beside Exa, on the per-episode
  clock. One vendor's index is one vendor's blind spots; a question Exa covers
  poorly currently produces a thin episode with no sign that another index
  would have done better.
* **Attention** (`GdeltTrendingSource`) - what the world is writing about, on
  the shared 15-minute clock, for the myFAM Trending row.

Why GDELT for the second one specifically
-----------------------------------------
Exa is *retrieval*: find me documents about X, ranked by relevance. GDELT is
*measurement*: it counts coverage across hundreds of thousands of outlets in
100+ languages. Attention is a volume question, and only one of those two
measures volume. That count is also the thing a provenance panel can show -
"covered by 47 outlets" is corroboration a listener can read.

The API
-------
DOC 2.0, a single keyless endpoint over a rolling three-month window:

    https://api.gdeltproject.org/api/v2/doc/doc

`mode=artlist` returns articles; `mode=timelinevolraw` returns coverage volume
over time. No credential and no account - but **not** unlimited: GDELT asks
for one request every five seconds per address, and Render's outbound address
is shared with other tenants.

**Every request goes through one pacer** (§144). The boot of 24/09 fired the
story sweep's fifteen theme requests at once, the regional sweep six at a
time, the old trending source's own fifteen and a cross-check per episode, all
from one address in the same minute - and every one of them timed out. So
`_get` waits its turn: background work queues one at a time, and an episode
takes the next slot or, if that is further off than
`GDELT_EPISODE_WAIT_SECONDS`, does without GDELT rather than wait on a sweep.
Theme volumes are cached (`VOLUME_TTL_SECONDS`) because the story pool and the
trending source measure the same fifteen themes.

**The limitation, and how the story pool gets round it** (§135). DOC is
query-driven: it tells you how much coverage *a query you name* is getting;
it does not hand you a ranked list of everything hot right now. So
`GdeltTrendingSource` sweeps a set of GKG themes and ranks them by measured
volume - real measurement over a fixed vocabulary. The story pool no longer
stops there: `discover` reads the recent articles under the hottest themes
and under each region's own press, and `news_clusters` groups them into the
actual stories, ranked by how many outlets are running each one. Open-ended
discovery without the bulk GKG exports.

Not verified against the live service
-------------------------------------
The build container's egress proxy blocks `api.gdeltproject.org`, so every
shape below is written from the documented API and exercised against recorded
payloads in `tests/test_gdelt.py`. **Nothing here has made a real request.**
Run `python tools/verify_live.py` and `tools/gdelt_probe.py` somewhere with
network before believing it works.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

import trending
from config import settings

log = logging.getLogger(__name__)

DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

#: GKG themes swept for the Trending row, with the plain-English subject each
#: one stands for. A fixed vocabulary rather than open discovery - see the
#: module docstring for why, and `TRENDING.md` for what open discovery would
#: cost. Chosen to span domains rather than to be exhaustive; GDELT publishes
#: thousands and sweeping all of them would be thousands of requests.
THEMES = (
    ("ECON_STOCKMARKET", "the stock market"),
    ("ECON_INFLATION", "inflation"),
    ("ENV_CLIMATECHANGE", "climate change"),
    ("WB_2670_JOBS", "jobs and employment"),
    ("EPU_POLICY_GOVERNMENT", "government policy"),
    ("TAX_DISEASE", "public health"),
    ("SCIENCE", "science"),
    ("MEDIA_SOCIAL", "social media"),
    ("CRISISLEX_CRISISLEXREC", "disasters and emergencies"),
    ("ELECTION", "elections"),
    ("TAX_FNCACT_SOLDIER", "armed conflict"),
    ("WB_635_PUBLIC_HEALTH", "healthcare"),
    ("ENERGY", "energy"),
    ("TECH", "technology"),
    ("SPORTS", "sport"),
)


class _Result:
    """One article, shaped like the Exa result the rest of FAM already reads.

    Duck-typed rather than adapted at the call site, so `research.rank_results`,
    `research.credibility`, `research.published_at` and `provenance.from_results`
    all work on it unchanged. A second retriever that needed its own branch in
    each of those would be four places to forget.
    """

    __slots__ = ("title", "url", "published_date", "highlights", "text",
                 "country")

    def __init__(self, title: str, url: str, published_date: str,
                 highlights: list, country: str = "") -> None:
        self.title = title
        self.url = url
        self.published_date = published_date
        self.highlights = highlights
        self.text = ""
        #: Where the *publisher* is, as GDELT names it ("United States"). It
        #: is what lets Trending say how a story is running in one listener's
        #: country without a second request - see `stories.country_shares`.
        self.country = country


def _iso(stamp: str) -> str:
    """GDELT's `20260916T143000Z` -> an ISO date `research.published_at` reads."""
    text = (stamp or "").strip()
    if len(text) >= 8 and text[:8].isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return ""


def parse_articles(payload: dict) -> list:
    """Turn a DOC `mode=artlist` response into Exa-shaped results.

    Tolerant on purpose: GDELT is a research project and fields come and go.
    A row missing a URL is skipped rather than raising, because one malformed
    article must not cost the whole second opinion.
    """
    out: list = []
    for row in (payload or {}).get("articles", []) or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        title = str(row.get("title") or "").strip()
        out.append(_Result(
            title=title,
            url=url,
            published_date=_iso(str(row.get("seendate") or "")),
            # DOC does not return article body text. The title is the only
            # evidence it carries, and it is passed through as the single
            # highlight rather than being padded out into something that looks
            # like more than it is.
            highlights=[title] if title else [],
            country=str(row.get("sourcecountry") or "").strip(),
        ))
    return out


def parse_volume(payload: dict) -> float:
    """The most recent coverage-volume point from `mode=timelinevolraw`."""
    for series in (payload or {}).get("timeline", []) or []:
        points = series.get("data") or []
        if points:
            try:
                return float(points[-1].get("value", 0.0))
            except (TypeError, ValueError, AttributeError):
                continue
    return 0.0


class GdeltBusy(RuntimeError):
    """The next free slot is further off than this caller may wait."""


class _Pacer:
    """One request every `GDELT_REQUEST_GAP_SECONDS`, across the process.

    A reservation clock rather than a lock around the request: a slot is taken
    the moment it is asked for, so a slow response never holds anybody else
    up, and the gap is between request *starts*, which is what GDELT counts.

    **Episodes go first.** Background work queues on a lock and takes a slot
    only once it is free *now*, so a sweep of thirty requests never books
    thirty slots up front - which would put every episode's request two and a
    half minutes into the future. An episode books the next slot directly, so
    it waits at most one gap behind whatever started last, and a background
    request that was about to go simply waits one more.
    """

    def __init__(self) -> None:
        self._next = 0.0
        self._background: Optional[asyncio.Lock] = None
        self._loop = None

    def _lock(self) -> asyncio.Lock:
        # One lock per event loop: tests run each case in a fresh loop, and a
        # lock bound to a closed loop raises on first use.
        loop = asyncio.get_running_loop()
        if self._background is None or self._loop is not loop:
            self._background = asyncio.Lock()
            self._loop = loop
        return self._background

    async def slot(self, max_wait: Optional[float] = None) -> None:
        gap = float(settings.gdelt_request_gap_seconds)
        if gap <= 0:
            return
        if max_wait is None:
            async with self._lock():
                while True:
                    now = time.monotonic()
                    if self._next <= now:
                        self._next = now + gap
                        return
                    await asyncio.sleep(self._next - now)
        else:
            await self._take(gap, max_wait)

    async def _take(self, gap: float, max_wait: float) -> None:
        now = time.monotonic()
        at = max(now, self._next)
        wait = at - now
        if wait > max_wait:
            raise GdeltBusy(
                f"GDELT's next free slot is {wait:.1f}s away, past the "
                f"{max_wait:.1f}s an episode may wait")
        self._next = at + gap
        if wait > 0:
            await asyncio.sleep(wait)

    def reset(self) -> None:
        self._next = 0.0


PACER = _Pacer()


async def _get(params: dict, timeout: float,
               max_wait: Optional[float] = None) -> dict:
    """One DOC request, in its turn. `max_wait=None` is background work."""
    await PACER.slot(max_wait)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(DOC_API, params=params)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            # DOC answers a query it cannot parse with a 200 and a sentence of
            # plain text. Say what it said, rather than "Expecting value".
            raise ValueError(
                f"GDELT answered with text, not JSON: {response.text[:160]!r}"
            ) from exc


def _describe(exc: BaseException) -> str:
    """A failure in words. A timeout's own message is empty, which is how the
    24/09 logs came to read `gdelt retrieval failed for '...': ` and stop."""
    text = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {text}" if text else name


#: Most words a free-text query keeps. DOC is a keyword index, and a whole
#: DailyFAM prompt ("The latest on Anthropic (Startups) as of Thursday...")
#: sent when EI degrades is a query it either rejects or matches badly.
MAX_QUERY_WORDS = 10


def clean_query(query: str) -> str:
    """Free text reduced to what DOC can search on.

    Only for `retrieve`, whose query is somebody's words: `artlist` carries
    DOC operators (`theme:`, `sourcecountry:`, parentheses) and is left alone.
    Punctuation goes, because DOC treats brackets and quotes as syntax.
    """
    words = re.sub(r"[^\w\s'-]", " ", query or "").split()
    return " ".join(words[:MAX_QUERY_WORDS])


def available() -> tuple[bool, str]:
    """Whether GDELT is switched on. No credential exists to check."""
    if not settings.gdelt:
        return False, "GDELT=0"
    return True, "GDELT DOC 2.0 needs no credential"


async def retrieve(query: str, limit: int = 0,
                   recency_days: int = 0) -> list:
    """Articles for `query`, as Exa-shaped results. Never raises.

    The second opinion beside Exa. Returns `[]` on any failure, because a
    cross-check that could break an episode would be worse than no
    cross-check - the first retriever's packet is still real evidence.
    """
    query = clean_query(query)
    if not query:
        return []
    ok, _why = available()
    if not ok:
        return []

    limit = limit or int(settings.gdelt_max_records)
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": str(max(1, min(250, limit))),
        "format": "json",
        "sort": "hybridrel",
    }
    if recency_days > 0:
        # DOC expresses windows in hours, capped at its three-month window.
        params["timespan"] = f"{min(2160, max(1, recency_days * 24))}h"

    timeout = float(settings.gdelt_timeout_seconds)
    wait = float(settings.gdelt_episode_wait_seconds)
    try:
        payload = await asyncio.wait_for(
            _get(params, timeout, max_wait=wait),
            timeout=timeout + wait + 0.5)
    except GdeltBusy as exc:
        # Not a failure of GDELT: a sweep has the next few slots. Info, not a
        # warning - it is the pacer doing its job.
        log.info("gdelt: skipped for %r - %s", query, exc)
        return []
    except Exception as exc:  # noqa: BLE001 - see docstring
        log.warning("gdelt retrieval failed for %r: %s", query, _describe(exc))
        return []

    results = parse_articles(payload)
    log.info("gdelt: %d article(s) for %r", len(results), query)
    return results


async def artlist(query: str, limit: int, hours: int, timeout: float) -> list:
    """Recent articles for `query`. **Raises** on failure, unlike `retrieve`.

    The trending sweep needs to tell "GDELT answered with nothing" from
    "GDELT did not answer" - the second is an outage `/api/health` has to be
    able to say - so this is the half of `retrieve` without its safety net.
    `retrieve` is the one to call on an episode's path.
    """
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": str(max(1, min(250, limit))),
        "format": "json",
        # Hybrid relevance leans towards the outlets GDELT weights most, which
        # is what a trending row wants from a sample: the stories the big
        # newsrooms are running, rather than the most recent two minutes.
        "sort": "hybridrel",
        "timespan": f"{max(1, min(2160, hours))}h",
    }
    return parse_articles(await _get(params, timeout))


def region_query(region: str) -> str:
    """How to ask for one region's press: its main source countries, OR'd.

    GDELT wants OR'd terms in parentheses, and `sourcecountry:` takes the
    country name with its spaces removed - the FIPS codes it also accepts are
    not ISO codes (`UK`, `GM`), which is a mistake waiting in a lookup table.
    """
    import geography

    names = geography.GDELT_SOURCES.get(region, ())
    if not names:
        return ""
    if len(names) == 1:
        return f"sourcecountry:{names[0]}"
    return "(" + " OR ".join(f"sourcecountry:{n}" for n in names) + ")"


async def discover(hot_themes: list, timeout: float, hours: int = 12,
                   per_query: int = 75, regions=None,
                   concurrency: int = 6) -> tuple:
    """Read what the world's press, and each region's, is running right now.

    One `artlist` per hot theme for the worldwide sample, and one per region
    over that region's own press. Returns `(articles, scope_of, failures)`:
    every article read, a function saying which sweep found each one ("world"
    or a region key), and how many requests failed - so a sweep that lost
    every request is reported as an outage rather than as a quiet news day.

    Bounded concurrency, because GDELT is a research project's free service
    and asks to be treated like one; a sweep of fifteen at once is how a
    keyless API becomes a rate-limited one.
    """
    import geography

    regions = tuple(geography.REGIONS if regions is None else regions)
    jobs = [("world", f"theme:{theme}") for theme in hot_themes]
    jobs += [(region, region_query(region)) for region in regions
             if region_query(region)]
    gate = asyncio.Semaphore(max(1, concurrency))
    found_in: dict = {}
    failures = 0

    async def run(scope: str, query: str):
        async with gate:
            try:
                return scope, await artlist(query, per_query, hours, timeout), None
            except Exception as exc:  # noqa: BLE001 - one query is not the sweep
                return scope, [], exc

    articles: list = []
    for scope, rows, error in await asyncio.gather(
            *(run(scope, query) for scope, query in jobs)):
        if error is not None:
            failures += 1
            log.info("gdelt: %s sweep failed: %s", scope, _describe(error))
            continue
        for row in rows:
            # First finder wins, and a worldwide finding beats a regional
            # one: the world sweep runs first in `jobs`.
            found_in.setdefault(row.url, scope)
            articles.append(row)

    return articles, (lambda article: found_in.get(article.url, "")), \
        (failures, len(jobs))


#: How long a theme's measured volume is reused. The window it measures is 24
#: hours, so twenty minutes of staleness moves nothing, and it lets the story
#: pool and the trending source share one set of fifteen requests per sweep.
VOLUME_TTL_SECONDS = 1200.0
_VOLUMES: dict = {}


async def _measure_volume(theme: str, timeout: float) -> float:
    payload = await _get({"query": f"theme:{theme}", "mode": "timelinevolraw",
                          "format": "json", "timespan": "24h"}, timeout)
    return parse_volume(payload)


async def volume_for(theme: str, timeout: float) -> float:
    """How much coverage a GKG theme is getting right now. Cached, shared.

    Two callers asking at once share one request: the cache holds the task
    itself, shielded, so a caller that gives up does not cancel the request
    for the other.
    """
    now = time.monotonic()
    held = _VOLUMES.get(theme)
    if held is not None:
        expires, task = held
        loop_ok = task.get_loop() is asyncio.get_running_loop()
        if loop_ok and (not task.done() or (
                expires > now and not task.cancelled()
                and task.exception() is None)):
            return await asyncio.shield(task)
    task = asyncio.ensure_future(_measure_volume(theme, timeout))
    # Every caller may have given up (a sweep's ceiling) before it fails;
    # reading the outcome here keeps that from logging "exception was never
    # retrieved" on top of the failure the sweep already reported.
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    _VOLUMES[theme] = (now + VOLUME_TTL_SECONDS, task)
    return await asyncio.shield(task)


class GdeltTrendingSource(trending.TrendingSource):
    """The Trending row, ranked by measured coverage volume.

    Sweeps `THEMES`, ranks by volume, and turns the top few into questions.

    **The question is templated, not generated**, and that is a deliberate
    stopping point rather than the finished article. `TRENDING.md` says a tile
    must carry a question worth an episode rather than a headline, and a
    template is only just on the right side of that line. The better version is
    one model call per refresh window turning the top themes and their leading
    headlines into real questions - one call for everybody, which the shared
    clock makes affordable. That is left as the next step rather than guessed
    at here, because it is a writing-quality decision and this file cannot
    test writing quality.
    """

    name = "GDELT"
    cost_per_refresh = 0.0
    #: Fifteen paced requests are over a minute; `TRENDING_TIMEOUT_SECONDS`
    #: (8s) is sized for one request, and nobody waits on this sweep.
    timeout_seconds = 150.0

    def diagnose(self) -> tuple[bool, str]:
        ok, why = available()
        if not ok:
            return False, f"GDELT is switched off ({why})"
        return True, "GDELT DOC 2.0, keyless; not verified from this machine"

    async def verify(self) -> tuple[bool, str]:
        try:
            results = await retrieve("climate", limit=1)
        except Exception as exc:  # noqa: BLE001
            return False, f"GDELT did not answer: {type(exc).__name__}: {exc}"
        if not results:
            return False, "GDELT answered but returned nothing parseable"
        return True, f"GDELT answered with {len(results)} article(s)"

    async def fetch(self, limit: int) -> list:
        timeout = float(settings.gdelt_timeout_seconds)
        measured: list = []

        async def measure(theme: str, subject: str):
            try:
                return subject, theme, await volume_for(theme, timeout)
            except Exception as exc:  # noqa: BLE001 - one theme must not sink the sweep
                log.debug("gdelt: theme %s failed: %s", theme, exc)
                return subject, theme, -1.0

        # Concurrent, because this is fifteen small requests and doing them in
        # series would put the whole sweep past any sensible ceiling. It runs
        # on the shared clock, so nobody is waiting on it.
        results = await asyncio.gather(
            *(measure(theme, subject) for theme, subject in THEMES))
        measured = [row for row in results if row[2] > 0]
        if not measured:
            return []

        measured.sort(key=lambda row: -row[2])
        now = datetime.now(timezone.utc)
        items = []
        for rank, (subject, theme, volume) in enumerate(measured[:limit or 6]):
            items.append(trending.TrendingItem(
                subject=subject,
                query=f"what is actually driving the news about {subject} right now",
                why_now=f"coverage of {subject} is running high across global media",
                source=self.name,
                as_of=now,
                rank=rank + 1,
                kind=trending.ATTENTION,
            ))
        return items


def report() -> dict:
    ok, why = available()
    return {"enabled": bool(settings.gdelt), "ready": ok, "detail": why,
            "endpoint": DOC_API, "themes_swept": len(THEMES),
            "verified_from_this_machine": False}
