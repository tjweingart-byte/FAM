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
over time. No credential, no account, generous limits - which is why this can
be a second opinion on every episode without a second bill.

**The honest limitation**, worth knowing before trusting the trending half:
DOC is query-driven. It tells you how much coverage *a query you name* is
getting; it does not hand you a ranked list of everything hot right now. So
`GdeltTrendingSource` sweeps a set of GKG themes and ranks them by measured
volume. That is real measurement over a fixed vocabulary, not open-ended
discovery - open-ended needs the bulk GKG exports, which are a heavier path
and deliberately not taken here.

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

    __slots__ = ("title", "url", "published_date", "highlights", "text")

    def __init__(self, title: str, url: str, published_date: str,
                 highlights: list) -> None:
        self.title = title
        self.url = url
        self.published_date = published_date
        self.highlights = highlights
        self.text = ""


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


async def _get(params: dict, timeout: float) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(DOC_API, params=params)
        response.raise_for_status()
        return response.json()


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
    query = (query or "").strip()
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

    try:
        payload = await asyncio.wait_for(
            _get(params, float(settings.gdelt_timeout_seconds)),
            timeout=float(settings.gdelt_timeout_seconds) + 0.5)
    except Exception as exc:  # noqa: BLE001 - see docstring
        log.warning("gdelt retrieval failed for %r: %s", query, exc)
        return []

    results = parse_articles(payload)
    log.info("gdelt: %d article(s) for %r", len(results), query)
    return results


async def volume_for(theme: str, timeout: float) -> float:
    """How much coverage a GKG theme is getting right now."""
    payload = await _get({"query": f"theme:{theme}", "mode": "timelinevolraw",
                          "format": "json", "timespan": "24h"}, timeout)
    return parse_volume(payload)


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
