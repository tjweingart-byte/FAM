"""Retrieval that runs *before* Claude writes, so Claude reads rather than searches.

The retrievers, and **all of them finish before the first word is written**:

* **parallel** - the default since §258: Parallel's Search API
  retrieves, this module builds an evidence packet, and the packet goes into
  the prompt as context. About a seventh of Exa's price.
* **perplexity** - Perplexity's Search API, a second, independent index. It is
  the second look on a thin packet and the first rung below the configured
  backend, so one vendor's blind spot is not the episode's.
* **exa** - the backend every packet was measured on until the swap. Kept
  selectable (`RESEARCH_BACKEND=exa`) so the two can be compared on the same
  questions before it is deleted.
* **gdelt** - a keyless article index. Thinner than the others - titles,
  dates and grades, no passages - and available to a deployment with no
  retrieval credential at all.

All three web backends hand back results with the same four attributes -
`url`, `title`, `published_date`, `highlights` - which is the whole of what
ranking, screening, the packet and provenance read. A backend is a function
that produces those, and nothing downstream knows which one ran.

Beside them, `live_facts` asks API-Sports, Finnhub and Polymarket for a
current state when the brief says the question turns on one. Those five are
the whole of where an episode's information comes from.

**There is no third retriever, and the model does not search** (§135, at the
owner's direction). A `claude` backend used to give a separate research call
Anthropic's `web_search` tool, and it was the last rung of the ladder below.
It is deleted rather than switched off - the Piper reasoning: a backend left
behind is a knob somebody turns back on - and a deployment still setting
`RESEARCH_BACKEND=claude` runs on Exa and is told so at startup and on
`/api/health`. Before that it was attached to the
*writing* call (PROBLEMS.md §108), which is how the first sentence of an
episode came to be written before anything had been looked up.

**Which one runs is a ladder, not a setting** (§109). The configured backend
goes first; if it comes back with nothing GDELT is tried (`ladder()` is the
one definition of that), asking `Brief.broader` rather than the precise query
that just failed. No rung may raise. If every rung is empty the episode is
still written (§256, which deleted the §109 refusal); the writer's rules keep
anything volatile from being supplied from memory.

The packet shape is unchanged - `SOURCE n / Title: / Published: / Source type:
/ Key evidence:` - and is byte-for-byte the one the manual benchmark measured
on 2026-09-05 for the Exa path. Keeping it identical is the point: the numbers
already measured stay comparable, a change of packet shape is a deliberate act
rather than a drift, and the writing prompt has exactly one thing to read
however the evidence was found.

**Where this is allowed to cost time.** A retrieval in front of the first word
used to be the one cost this product refused, and for a while it was covered by
a second model call speaking from knowledge while this ran. That cover is gone
(§108): it wrote the opening of every researched episode without a brief,
without evidence, and without knowing what the episode was going to be about.
So the wait is real and it is in front of the first word, deliberately - Exa
answers in about half a second and GDELT from its export copy on disk (§211).

**It does not block the event loop.** The experiment ran `exa_py` synchronously
because trials were pinned to one at a time and a thread hand-off would have
added its own time to a number meant to be Exa's. Production is the opposite
case: other episodes are being served on this loop, and a synchronous HTTP call
on it would stop all of them. So the call goes to a worker thread, exactly as
speech synthesis does.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from config import (BROWSE_MINUTES, RESEARCH_BACKENDS, WEB_RESEARCH_BACKENDS,
                    settings)

log = logging.getLogger("research")

#: The manual benchmark's settings, as defaults. Changing one here silently
#: breaks comparability with the hand-measured run; override per request or by
#: configuration instead.
DEFAULT_SEARCH_TYPE = "fast"

#: Exa's published rate, used only when a response reports no cost of its own:
#: $7 per 1,000 searches plus contents for the 8 results FAM asks highlights
#: of, about $0.015 (docs/FINANCIAL.md §2.2). Was 0.005, a third of that, so
#: every row priced from the fallback under-reported (§207).
COST_PER_SEARCH = 0.015

#: The web-search backends - every retriever that sends a query to a paid
#: search vendor. GDELT is a retriever too, but reads a copy on disk.
WEB_BACKENDS = WEB_RESEARCH_BACKENDS

#: What one search costs on each paid backend when its reply carries no
#: dollar figure of its own (Parallel and Perplexity never do). Parallel's
#: `fast` mode and Perplexity's `fast` search are $1 per 1,000 requests with
#: up to 10 results and their excerpts included (docs/FINANCIAL.md §7.5).
SEARCH_COSTS = {"exa": COST_PER_SEARCH, "parallel": 0.001, "perplexity": 0.001}

#: Where each vendor's search lives, and the credential it reads.
PARALLEL_URL = "https://api.parallel.ai/v1/search"
PERPLEXITY_URL = "https://api.perplexity.ai/search"
KEYS = {"parallel": "PARALLEL_API_KEY", "perplexity": "PERPLEXITY_API_KEY",
        "exa": "EXA_API_KEY"}
#: Perplexity accepts at most 20 results per request (its SDK's own note).
#: The allow-list ceiling is not in the SDK; 20 is the figure reported for its
#: docs, unverified - a longer list is cut, best-known outlets first.
PERPLEXITY_MAX_RESULTS = 20
PERPLEXITY_MAX_DOMAINS = 20
#: Parallel's allow-list ceiling (include plus exclude, combined).
PARALLEL_MAX_DOMAINS = 200
#: How long one Parallel search query may be. Its SDK asks for 3-6 words;
#: a hard ceiling is reported at about 200 characters (unverified).
PARALLEL_MAX_QUERY_CHARS = 200
#: How many queries one Parallel request carries: the precise search and
#: EI's angles (§260). Its guidance is two or three for best results.
PARALLEL_MAX_QUERIES = 3


# --------------------------------------------------------------------------
# Source credibility
# --------------------------------------------------------------------------
# Recency and credibility pull against each other, and the resolution here is
# to make them do different jobs rather than trade off against each other in a
# weighted sum nobody can reason about:
#
#   **Recency is a filter. Credibility is the sort.**
#
# The window (`start_published_date`, from the brief's `recency_days`) decides
# what is even eligible, so nothing stale is considered however well ranked it
# is. Among what survives, the best-sourced result goes first. So a question
# about last night is answered from last night, by a wire service rather than by
# whoever published fastest - which is what "prioritise recency without giving
# up source quality" actually has to mean.
#
# The tiers are a short, explicit list plus two suffix rules rather than an
# attempt at the whole web. A domain nobody listed is `unverified`, which is a
# statement about FAM's knowledge of it, not an accusation - and the writer is
# told exactly that, because a packet that silently ranked sources would be a
# machine judging credibility with nobody able to see the workings.

#: Wire services, papers of record, exchanges and primary official sources.
TIER_PRIMARY = frozenset({
    "reuters.com", "apnews.com", "ap.org", "bloomberg.com", "ft.com",
    "wsj.com", "nytimes.com", "washingtonpost.com", "economist.com",
    "bbc.co.uk", "bbc.com", "npr.org", "theguardian.com", "cnbc.com",
    "espn.com", "espn.co.uk", "theathletic.com", "skysports.com",
    "nature.com", "science.org", "nasa.gov", "who.int",
    # The leagues' own sites: primary for their own schedules and results.
    "nfl.com", "nba.com", "wnba.com", "mlb.com", "nhl.com", "ncaa.com",
    "premierleague.com", "uefa.com", "fifa.com",
})

#: Established outlets and trade press: reliable, not primary.
TIER_ESTABLISHED = frozenset({
    "cnn.com", "nbcnews.com", "cbsnews.com", "abcnews.go.com", "axios.com",
    "politico.com", "thetimes.co.uk", "telegraph.co.uk", "independent.co.uk",
    "forbes.com", "fortune.com", "businessinsider.com", "techcrunch.com",
    "theverge.com", "arstechnica.com", "wired.com", "engadget.com",
    "marketwatch.com", "barrons.com", "investopedia.com", "sportingnews.com",
    "cbssports.com", "nbcsports.com", "si.com", "yahoo.com", "usatoday.com",
    "foxsports.com", "bleacherreport.com", "theringer.com",
    "sports-reference.com", "pro-football-reference.com",
    "basketball-reference.com", "baseball-reference.com", "espncricinfo.com",
})


# --------------------------------------------------------------------------
# Screening: what never reaches the packet (9.30 #5)
# --------------------------------------------------------------------------
#
# The packet's worst episode read a Madden sim league's "Commanders top Colts
# 45-31" as a real result for a game not yet played, beside a gametracker
# and an odds piece that both said it had not kicked off. A grade told the
# writer to weigh it; the writer weighed it out loud for a paragraph. Two
# screens now keep that kind of page out before anybody weighs anything.

#: Phrases that mark a page as a video game's or a simulated league's, not
#: the sport's: regular expressions over the title and the passages (never
#: the URL, where `.cfm` is a file type). "Madden" alone is John Madden, the
#: All-Madden team and a Thanksgiving tradition, so only the game's own
#: phrasings count.
SIMULATION_MARKERS = (
    r"madden (nfl|\d\d|ultimate team|franchise|league|sim|online|cfm)",
    r"sim(ulation)? league", r"simulated season", r"franchise mode",
    r"online franchise", r"video game league", r"nba 2k\d*", r"mlb the show",
    r"ea sports fc", r"esports league",
)
#: Hosts known to publish simulated results written up like real ones.
SIMULATION_HOSTS = frozenset({"2kolf.com"})
#: Words in the listener's own question that make a simulated league the
#: subject, so the screen stands aside.
_GAME_WORDS = re.compile(
    r"\b(madden|video ?games?|esports?|sim league|simulation|franchise mode|"
    r"2k\d*|gaming)\b", re.I)


def is_simulation(result) -> bool:
    """Whether a result reports a video game or a simulated league."""
    host = host_of(result)
    if host in SIMULATION_HOSTS or any(host.endswith("." + h)
                                       for h in SIMULATION_HOSTS):
        return True
    text = " ".join([getattr(result, "title", "") or ""]
                    + [str(h) for h in (getattr(result, "highlights", None) or [])])
    text = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
    return any(re.search(r"\b(" + marker + r")\b", text)
               for marker in SIMULATION_MARKERS)


#: On a sports question, an outlet FAM does not know is dropped once this
#: many known ones are in hand: a score or a record has a known outlet
#: reporting it, and the unknown ones are where the stray results live.
SPORTS_KNOWN_ENOUGH = 2


def screen_results(results, *, query: str = "", live_domain: str = "") -> list:
    """What may reach the packet, in the order given. Never raises.

    * A simulated or video-game league's page, unless the question is about
      one - its "results" are for games that were never played.
    * On a question that turns on a sports state (`live_domain == "sports"`),
      an unknown outlet whenever `SPORTS_KNOWN_ENOUGH` known ones answered.

    Dropped pages are logged with their hosts, so a screen that drops too
    much is visible rather than silent.
    """
    results = list(results or [])
    kept, dropped = [], []
    games_asked = bool(_GAME_WORDS.search(query or ""))
    for result in results:
        if not games_asked and is_simulation(result):
            dropped.append((host_of(result), "simulation"))
            continue
        kept.append(result)
    if live_domain == "sports":
        known = [r for r in kept if credibility(r) != "unverified"]
        if len(known) >= SPORTS_KNOWN_ENOUGH:
            dropped.extend((host_of(r), "unknown outlet on a sports question")
                           for r in kept if credibility(r) == "unverified")
            kept = [r for r in kept if credibility(r) != "unverified"]
    if dropped:
        log.info("research: screened out %s for %r",
                 ", ".join(f"{h or '?'} ({why})" for h, why in dropped), query)
    return kept

#: How much a tier is worth when ordering. Only the order matters, not the gaps.
TIER_SCORES = {"primary": 3, "established": 2, "unverified": 1}

#: What the writer is told about each tier, in words rather than a number. A
#: score in a prompt is a thing to be argued with; a description is a thing to
#: weigh.
TIER_LABELS = {
    "primary": "wire service, paper of record or primary source",
    "established": "established outlet",
    "unverified": "not on FAM's list of known outlets - weigh it accordingly",
}


def host_of(result) -> str:
    """The bare hostname of a result, lowercased and without `www.`."""
    url = (getattr(result, "url", "") or "").strip()
    if "//" not in url:
        return ""
    host = url.split("//", 1)[1].split("/", 1)[0].lower()
    return host[4:] if host.startswith("www.") else host


def credibility(result) -> str:
    """Which tier a result's host belongs to. Never raises, never guesses high."""
    host = host_of(result)
    if not host:
        return "unverified"
    if host in TIER_PRIMARY:
        return "primary"
    if host in TIER_ESTABLISHED:
        return "established"
    # A government or academic domain is primary for the thing it is about -
    # a central bank's own statement outranks any report of that statement.
    if host.endswith((".gov", ".gov.uk", ".edu", ".ac.uk", ".int", ".mil")):
        return "primary"
    # Subdomains of a listed host inherit it: `finance.yahoo.com` is `yahoo.com`.
    for known, tier in ((TIER_PRIMARY, "primary"), (TIER_ESTABLISHED, "established")):
        if any(host.endswith("." + domain) for domain in known):
            return tier
    return "unverified"


_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def published_at(result) -> Optional[datetime]:
    """When a result was published, as a timezone-aware datetime, or None.

    Exa reports this as an ISO string and sometimes not at all. `None` is a
    real answer and is passed through to the packet as "not stated" rather than
    being filled with today - an undated source that looks dated is how an old
    article becomes "last night".
    """
    raw = (getattr(result, "published_date", None)
           or getattr(result, "publishedDate", None) or "")
    match = _DATE.search(str(raw))
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)),
                        int(match.group(3)), tzinfo=timezone.utc)
    except ValueError:
        return None


def age_phrase(when: Optional[datetime], now: Optional[datetime] = None) -> str:
    """How old a source is, in the words the episode would use.

    This is the field the temporal failures in the blueprint came down to. The
    packet used to carry a title and some highlights and no date at all, so the
    model was asked to choose between "last night" and "two days ago" with
    nothing to choose on. It is computed here, from the dates, rather than left
    to the model to infer from prose.
    """
    if when is None:
        return "date not stated"
    # The listener's calendar day (`listener_clock`, 10.1): at 9pm in
    # California the UTC date is already tomorrow, and an article from
    # this evening read as "yesterday".
    import listener_clock

    now = now or listener_clock.now()
    days = (now.date() - when.date()).days
    if days < 0:
        return "dated in the future - treat with suspicion"
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 14:
        return "last week"
    if days < 60:
        return f"{days // 7} weeks ago"
    if days < 730:
        return f"{max(1, days // 30)} months ago"
    return f"{days // 365} years ago"


def rank_results(results, now: Optional[datetime] = None) -> list:
    """Order what came back: best-sourced first, newest first within a tier.

    Stable, so Exa's own relevance ordering survives as the final tie-break -
    this re-sorts by source quality and date without discarding what the
    retrieval thought was most relevant.
    """
    now = now or datetime.now(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def key(result):
        when = published_at(result)
        return (
            -TIER_SCORES.get(credibility(result), 1),
            # Undated sorts last within its tier: it may be anything.
            -(when or epoch).timestamp(),
        )

    return sorted(results, key=key)


def names_place(results, place: str) -> list:
    """Only the results that name `place` (§194, the fix that stops invention).

    A local question's evidence must be about that place. A Marin County or
    San Francisco story that never says "San Anselmo" is not evidence about
    San Anselmo, however high a national index ranked it - and handed to the
    writer it was the gap the writer filled from memory.
    """
    import local_news

    kept = []
    for result in results or []:
        text = " ".join([str(getattr(result, "title", "") or "")]
                        + [str(h) for h in (getattr(result, "highlights", None) or [])])
        if local_news.names(text, place):
            kept.append(result)
    return kept


class ResearchUnavailable(RuntimeError):
    """The configured backend cannot run, and says which part is missing."""


class NoEvidence(RuntimeError):
    """Every retriever came back empty on a question that needs today's facts.

    Not the same as `ResearchUnavailable`, which is about configuration. This
    is the end of the ladder: the retrievers ran, none of them found anything,
    and the question is one whose answer *turns on* something current.

    Writing it anyway is the failure this project has spent the most rules on
    - "never infer a current-world fact from the absence of current-world
    evidence" (§89) - because what the model would write from is training
    data of unknown age, delivered in exactly the same confident voice as a
    researched episode, with nothing a listener could use to tell them apart.
    An episode that does not exist is a worse product than one that does; an
    episode that is confidently wrong about today is a worse product than
    both. §109.

    It carries the sentence the listener sees, composed here so the web app
    and the iOS client cannot word it differently.
    """


@dataclass
class Packet:
    """What retrieval produced, and what it cost to produce it.

    `context` is the only part Claude sees. The rest exists so a person can
    judge the sources and so a run can be costed - neither is ever scored by
    machine, and neither reaches the prompt.
    """

    context: str = ""
    sources: list = field(default_factory=list)
    searches: int = 0
    seconds: float = 0.0
    cost: float = 0.0
    results_returned: int = 0
    backend: str = ""
    #: The recency window this was retrieved under, in days. 0 means none.
    window_days: int = 0
    #: What the brief asked for and this packet does not appear to contain.
    #: Carried to the writer so a thin patch is named as thin rather than
    #: filled in from memory and presented as research.
    missing: list = field(default_factory=list)
    #: Whether the one permitted second search happened.
    retried: bool = False
    #: Who published what this packet is made of - see `provenance.py`.
    #: Carried out of band from `context`, because the packet the model reads
    #: deliberately contains grades and never hostnames, and this deliberately
    #: contains hostnames and never reaches a prompt.
    provenance: object = None
    #: The backend that was asked for and could not serve, when this packet
    #: came from the other one. Never empty silently: a deployment measuring
    #: Exa and actually being served by the model's own search has to be able
    #: to see that from the episode's own record.
    fell_back_from: str = ""
    #: The `usage` block of a model call that did the searching. Always
    #: `None` since the `claude` backend was deleted (§135) - Exa bills per
    #: search and GDELT not at all - and kept because the metering path reads
    #: it on every rung and a missing attribute would be a crash on each one.
    usage: object = None
    #: Every web backend this packet's searches went to, in order - the
    #: configured one and, when the second look went elsewhere, that one.
    #: The ladder reads it so a rung that already ran as the second look is
    #: not asked the same thing again.
    tried: list = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.context.strip())

    def as_dict(self) -> dict:
        return {
            "backend": self.backend,
            "sources": list(self.sources),
            "searches": self.searches,
            "seconds": round(self.seconds, 3),
            "cost": round(self.cost, 4),
            "results_returned": self.results_returned,
            "packet_chars": len(self.context),
            "window_days": self.window_days,
            "missing": list(self.missing),
            "retried": self.retried,
            "fell_back_from": self.fell_back_from,
            "tried": list(self.tried),
        }


# --------------------------------------------------------------------------
# Exa
# --------------------------------------------------------------------------
def _client():
    """Build the Exa client, or say precisely what is missing.

    Imported here rather than at module scope so that `exa_py` is an optional
    dependency: a deployment on the `gdelt` backend must not need it, and the
    test suite must run without it.
    """
    try:
        from exa_py import Exa
    except ImportError as exc:
        raise ResearchUnavailable(
            "exa_py is not installed. `pip install -r requirements-exa.txt`"
        ) from exc
    key = (os.environ.get("EXA_API_KEY") or "").strip()
    if not key:
        raise ResearchUnavailable(
            "EXA_API_KEY is not set, so Exa cannot retrieve anything. Set it, "
            "or set RESEARCH_BACKEND=gdelt (with GDELT=1) to use the keyless "
            "index instead.")
    return Exa(key)


def diagnose() -> tuple[bool, str]:
    """Why Exa can or cannot serve, in one sentence.

    The same shape as `ChatterboxEngine.diagnose`, and for the same reason:
    something that reports readiness must name what is wrong, not just answer
    no. Does not perform a retrieval - that would cost money on every health
    check - so it reports *configured*, and `retrieve` is what proves it works.
    """
    try:
        import exa_py  # noqa: F401
    except ImportError:
        return False, "exa_py is not installed"
    if not (os.environ.get("EXA_API_KEY") or "").strip():
        return False, "EXA_API_KEY is not set"
    return True, "exa_py installed and EXA_API_KEY present"


def available() -> bool:
    return diagnose()[0]


# --------------------------------------------------------------------------
# Parallel and Perplexity
# --------------------------------------------------------------------------
@dataclass
class SearchResult:
    """One result from any web backend, in the shape Exa's results have.

    Exa's result objects are what every reader in this module was written
    against (`getattr(result, "highlights")` and so on), so the other two
    backends are translated into the same four attributes rather than the
    readers learning three shapes.
    """

    url: str = ""
    title: str = ""
    #: The page's own publication date, `YYYY-MM-DD`, or "" when the vendor
    #: does not know it. Never a "last updated" date: an old article edited
    #: yesterday is not news from yesterday, and `published_at` turns this
    #: into the words the episode says.
    published_date: str = ""
    highlights: list = field(default_factory=list)


def _key(backend: str) -> str:
    import credentials

    return (credentials.active(KEYS[backend]) or "").strip()


def backend_diagnose(backend: str) -> tuple[bool, str]:
    """Why a web backend can or cannot serve, in one sentence. No request."""
    if backend == "exa":
        return diagnose()
    if backend not in KEYS:
        return False, f"{backend!r} is not a web search backend"
    if not _key(backend):
        return False, f"{KEYS[backend]} is not set"
    return True, f"{KEYS[backend]} present"


def backend_available(backend: str) -> bool:
    return backend_diagnose(backend)[0]


def _clip(text: str, limit: int) -> str:
    """`text` cut to `limit` characters at a sentence end where one is near."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return cut[:end + 1] if end > limit // 2 else cut.rstrip() + "..."


_MARKDOWN_LEAD = re.compile(r"^(#{1,6}\s+|[-*+]\s+|>\s*|\|\s*)+")


def passages(text, limit: int = 0) -> list:
    """A page's excerpt text as passages the size of Exa's highlights.

    Perplexity returns one snippet per page and Parallel a few markdown
    excerpts; the packet reads `highlights_per_source` passages from each
    source, so both are cut into paragraphs and then into runs of sentences
    of about `limit` characters (`SEARCH_PASSAGE_CHARS`).
    """
    limit = int(limit or settings.search_passage_chars)
    chunks = text if isinstance(text, (list, tuple)) else [text]
    out: list = []
    for chunk in chunks:
        for paragraph in re.split(r"\n\s*\n", str(chunk or "")):
            # Markdown's headings, bullets, quotes and table bars are not
            # words to read out; a leading minus on a number ("-3.5%") is,
            # so a bullet is only a mark followed by a space.
            paragraph = " ".join(
                _MARKDOWN_LEAD.sub("", line.strip())
                for line in paragraph.splitlines()).strip()
            if not paragraph:
                continue
            if len(paragraph) <= limit:
                out.append(paragraph)
                continue
            current = ""
            for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
                if current and len(current) + 1 + len(sentence) > limit:
                    out.append(_clip(current, limit))
                    current = sentence
                else:
                    current = f"{current} {sentence}".strip()
            if current:
                out.append(_clip(current, limit))
    return [p for p in out if len(p) > 20]


def _post(url: str, headers: dict, body: dict) -> dict:
    """One JSON request to a search vendor. Raises on any failure.

    `httpx` is already a dependency (remote voice), so neither vendor's SDK
    is needed; the request shapes were read from their published SDKs
    (`parallel-web` 1.3.5, `perplexityai` 0.43.8).
    """
    import httpx

    with httpx.Client(timeout=float(settings.search_timeout_seconds)) as client:
        reply = client.post(url, headers=headers, json=body)
    reply.raise_for_status()
    return reply.json()


def _cutoff(recency_days: int) -> Optional[datetime]:
    if recency_days <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(days=recency_days)


def _search_parallel(query: str, num_results: int, recency_days: int = 0,
                     include_domains: tuple = (), objective: str = "",
                     extra_queries: tuple = ()) -> tuple[list, float]:
    """One Parallel search: (results, dollars). Raises on any failure."""
    key = _key("parallel")
    if not key:
        raise ResearchUnavailable(
            "PARALLEL_API_KEY is not set, so Parallel cannot retrieve "
            "anything. Set it, or set RESEARCH_BACKEND to another backend.")
    advanced: dict = {
        "max_results": int(num_results),
        "excerpt_settings": {
            "max_chars_per_result": int(settings.parallel_chars_per_result)},
    }
    policy: dict = {}
    cutoff = _cutoff(recency_days)
    if cutoff is not None:
        policy["after_date"] = cutoff.strftime("%Y-%m-%d")
    if include_domains:
        policy["include_domains"] = list(include_domains)[:PARALLEL_MAX_DOMAINS]
    if policy:
        advanced["source_policy"] = policy
    body: dict = {
        # Parallel wants short keyword queries; a raw question (EI off, or no
        # `retrieval` in the brief) is cut rather than sent whole.
        # EI's other wordings ride along (§260): Parallel asks for two or
        # three, and one request with three is still one request's price.
        "search_queries": list(dict.fromkeys(
            _clip(q, PARALLEL_MAX_QUERY_CHARS)
            for q in (query, *extra_queries) if str(q or "").strip()
        ))[:PARALLEL_MAX_QUERIES],
        # Always named: Parallel defaults to `advanced` ($5/1k, seconds)
        # when it is omitted.
        "mode": settings.parallel_mode,
        "advanced_settings": advanced,
    }
    if objective:
        body["objective"] = objective
    data = _post(PARALLEL_URL, {"x-api-key": key}, body)
    results = [
        SearchResult(url=str(item.get("url") or ""),
                     title=str(item.get("title") or ""),
                     published_date=str(item.get("publish_date") or ""),
                     highlights=passages(item.get("excerpts") or []))
        for item in (data.get("results") or []) if isinstance(item, dict)]
    return results, SEARCH_COSTS["parallel"]


def _search_perplexity(query: str, num_results: int, recency_days: int = 0,
                       include_domains: tuple = (), objective: str = ""
                       ) -> tuple[list, float]:
    """One Perplexity search: (results, dollars). Raises on any failure."""
    key = _key("perplexity")
    if not key:
        raise ResearchUnavailable(
            "PERPLEXITY_API_KEY is not set, so Perplexity cannot retrieve "
            "anything. Set it, or set RESEARCH_SECOND_LOOK=same.")
    body: dict = {
        "query": query,
        "max_results": min(int(num_results), PERPLEXITY_MAX_RESULTS),
        "search_type": settings.perplexity_search_type,
        "max_tokens_per_page": int(settings.perplexity_tokens_per_page),
    }
    cutoff = _cutoff(recency_days)
    if cutoff is not None:
        # Perplexity's date filters are written month/day/year.
        body["search_after_date_filter"] = cutoff.strftime("%m/%d/%Y")
    if include_domains:
        body["search_domain_filter"] = list(include_domains)[:PERPLEXITY_MAX_DOMAINS]
    data = _post(PERPLEXITY_URL, {"Authorization": f"Bearer {key}"}, body)
    results = [
        SearchResult(url=str(item.get("url") or ""),
                     title=str(item.get("title") or ""),
                     # `date` is publication; `last_updated` is not, and is
                     # deliberately not read (see `SearchResult`).
                     published_date=str(item.get("date") or ""),
                     highlights=passages(item.get("snippet") or ""))
        for item in (data.get("results") or []) if isinstance(item, dict)]
    return results, SEARCH_COSTS["perplexity"]


def _search_exa(query: str, num_results: int, recency_days: int = 0,
                include_domains: tuple = (), objective: str = "",
                search_type: str = DEFAULT_SEARCH_TYPE) -> tuple[list, float]:
    """One Exa search: (results, dollars). Raises on any failure."""
    client = _client()
    kwargs: dict = {
        "type": search_type,
        "num_results": num_results,
        "highlights": True,
    }
    cutoff = _cutoff(recency_days)
    if cutoff is not None:
        kwargs["start_published_date"] = cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    # The local ladder's third rung (§194): only the town's and county's
    # known outlets.
    if include_domains:
        kwargs["include_domains"] = list(include_domains)
    reply = client.search_and_contents(query, **kwargs)
    cost = getattr(getattr(reply, "cost_dollars", None), "total", None)
    return (list(getattr(reply, "results", []) or []),
            float(cost) if cost is not None else COST_PER_SEARCH)


#: **A floor on the search's window from the words of the request** (§266).
#: "What happened today" searched with no window is how a well-ranked article
#: from last year answers a question about this morning: credibility sorts
#: inside the window, so with no window the best-sourced old report wins. A
#: brief that degraded has no window at all, and a brief that called a "latest"
#: question evergreen has none either. This puts a floor under the *search*
#: only: `Brief.recency_days` is untouched, so how long the episode stays
#: current is still decided by what it was built on and never by these words
#: ([ttl-from-evidence]). A floor too narrow for the subject costs one more
#: search - the second look drops the window - and never the episode.
_DAY_WORDS = re.compile(
    r"\b(today|today's|tonight|tonight's|yesterday|yesterday's|last night|"
    r"this morning|this afternoon|this evening|right now)\b", re.I)
_WEEK_WORDS = re.compile(
    r"\b(this week|this weekend|latest|lately|these days|currently|so far)\b",
    re.I)
SEARCH_FLOOR_DAY_DAYS = 3
SEARCH_FLOOR_WEEK_DAYS = 7


def search_window(brief) -> int:
    """The window the search uses: the brief's, or - when it has none - the
    floor its request's words call for. Never read by the cache.

    Not for a `teaching` brief: "today's stoic quote" asks for one good
    example of a kind of thing, and a window would only make the search
    for its substance come back thin (§149)."""
    if brief is None:
        return 0
    days = int(getattr(brief, "recency_days", 0) or 0)
    if days > 0:
        return days
    if getattr(brief, "intent", "") == "teaching" and \
            not getattr(brief, "degraded", False):
        return 0
    words = str(getattr(brief, "query", "") or "")
    if _DAY_WORDS.search(words):
        return SEARCH_FLOOR_DAY_DAYS
    if _WEEK_WORDS.search(words):
        return SEARCH_FLOOR_WEEK_DAYS
    return 0


def objective_for(brief, recency_days: Optional[int] = None) -> str:
    """What Parallel is told the search is for, from the brief - never the
    listener's own words, which the consent notice does not send to a
    search vendor. "" without a brief.

    **EI's own objective first** (§260): Parallel reads it as the question
    behind the search and ranks and cuts its excerpts against it, so it is
    written by the call that resolved the subject, with the moment in dates
    and the kind of source that answers it. The date the window opens and
    what must be established are added here, in code, because they are
    facts the request already enforces - and the window's line is left off
    when there is none, so an evergreen question is never told to want
    "recent, dated reporting". `recency_days` defaults to the brief's;
    the second look, which drops the window, passes 0.
    """
    if brief is None:
        return ""
    days = int(getattr(brief, "recency_days", 0) or 0) \
        if recency_days is None else int(recency_days or 0)
    subject = str(getattr(brief, "subject", "") or "").strip()
    written = " ".join(str(getattr(brief, "search_objective", "") or "").split())
    wanted = [str(w).strip() for w in (getattr(brief, "must_establish", None) or [])
              if str(w).strip()]
    if not written and not subject and not wanted:
        return ""
    if written:
        text = written if written[-1] in ".!?" else written + "."
        if wanted:
            text += " A useful source establishes: " + "; ".join(wanted[:4]) + "."
    else:
        kind = "Recent, dated reporting" if days else "Reliable reporting or explanation"
        text = f"{kind} about {subject or 'this subject'}"
        if wanted:
            text += " that establishes: " + "; ".join(wanted[:4])
        text += "."
    cutoff = _cutoff(days)
    if cutoff is not None:
        text += (f" Only sources published on or after "
                 f"{cutoff.strftime('%Y-%m-%d')} answer this.")
    return text


def queries_for(query: str, brief) -> tuple:
    """The other wordings sent beside `query` (§260) - EI's `search_angles`,
    and only when `query` is the precise search they were written beside.
    A broader retry, a wider rung or a town's own query asks something else,
    and gets no angles meant for a different string."""
    if brief is None:
        return ()
    retrieval = str(getattr(brief, "retrieval", "") or "").strip().lower()
    if not retrieval or (query or "").strip().lower() != retrieval:
        return ()
    return tuple(a for a in (getattr(brief, "search_angles", None) or [])
                 if str(a).strip() and str(a).strip().lower() != retrieval)


def build_packet(results, packet_sources: int, highlights_per_source: int,
                 now: Optional[datetime] = None) -> str:
    """The evidence packet: what was found, who published it, and when.

    **The shape changed here, deliberately, and this is the reasoning.** The
    packet used to be `SOURCE n / Title: / Key evidence:` - byte-for-byte the
    one the manual benchmark measured on 2026-09-05, kept identical so those
    numbers stayed comparable. It carried no date and no publisher.

    That is the direct cause of the temporal failures FAM EI was built to fix.
    An episode was asked to say whether a game finished last night or two days
    ago, from evidence with no dates in it, and the only other time it had was
    `now_line()` - the current moment. There was nothing to subtract from it.
    The two new lines are the fix, and they cost nothing: Exa already returns
    the date and the URL, and this function was throwing both away.

    `Published` is computed here rather than left as an ISO string on purpose.
    The relative phrase is the thing the episode actually says, so deriving it
    from the date in code means the model reads "yesterday" instead of
    calculating it - and the blueprint's rule that relative labels come from
    normalized time rather than being guessed from prose is enforced rather
    than requested.

    The old shape is still reachable with `EXA_DATED_PACKET=0`, for comparing
    against the hand-measured numbers.
    """
    if not settings.exa_dated_packet:
        parts: list[str] = []
        for index, result in enumerate(list(results)[:packet_sources], 1):
            parts.append(f"SOURCE {index}")
            parts.append(f"Title: {getattr(result, 'title', '') or ''}")
            highlights = getattr(result, "highlights", None)
            if highlights:
                parts.append("Key evidence:")
                for highlight in list(highlights)[:highlights_per_source]:
                    parts.append(highlight)
            parts.append("")
        return "\n".join(parts)

    import listener_clock

    now = now or listener_clock.now()
    parts = []
    for index, result in enumerate(list(results)[:packet_sources], 1):
        when = published_at(result)
        stamp = when.strftime("%Y-%m-%d") if when else "unknown"
        parts.append(f"SOURCE {index}")
        parts.append(f"Title: {getattr(result, 'title', '') or ''}")
        parts.append(f"Published: {stamp} ({age_phrase(when, now)})")
        # The tier, described - never the hostname. A domain in the packet is a
        # domain the voice can read out, and the packet has been kept free of
        # them since it was written; the credibility signal is what the model
        # needs to weigh a source, and "wire service" carries that without
        # putting "reuters dot com" within reach of the script. `domains()`
        # still reports the hosts, out of band, for a person to judge.
        parts.append(f"Source type: {TIER_LABELS[credibility(result)]}")
        highlights = getattr(result, "highlights", None)
        if highlights:
            parts.append("Key evidence:")
            for highlight in list(highlights)[:highlights_per_source]:
                parts.append(highlight)
        parts.append("")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Sufficiency
# --------------------------------------------------------------------------
_WORD = re.compile(r"[a-z0-9']+")
#: Words too common to prove a packet covered anything.
_THIN = frozenset({
    "the", "and", "for", "with", "that", "this", "what", "when", "who", "how",
    "was", "were", "has", "have", "had", "its", "his", "her", "their", "from",
    "about", "into", "over", "after", "before", "any", "all", "new", "latest",
    "current", "recent", "result", "results", "score", "news", "update",
})


def _terms(text: str) -> set:
    return {w for w in _WORD.findall((text or "").lower())
            if len(w) > 2 and w not in _THIN}


def packet_covers(packet: str, must_establish) -> tuple[bool, list]:
    """Does this packet contain what the brief said the episode needs?

    A token test, not a model call, and for the same reason `research_reason`
    is one: a round trip here sits in front of the first word. It is a coarse
    instrument and is used only to decide whether one more search is worth
    half a second - never to judge the episode, and never to reject a packet
    that will still be used.

    An item counts as covered when a majority of its distinctive words appear
    somewhere in the packet. That is loose on purpose: the cost of a false
    "covered" is the episode FAM would have made anyway, and the cost of a
    false "missing" is one extra search.
    """
    wanted = [str(item).strip() for item in (must_establish or []) if str(item).strip()]
    if not wanted:
        return True, []
    have = _terms(packet)
    missing = []
    for item in wanted:
        terms = _terms(item)
        if not terms:
            continue
        hits = len(terms & have)
        if hits * 2 < len(terms):
            missing.append(item)
    # Sufficient when most of what was asked for is there. Requiring all of it
    # would retry on nearly every episode, because a brief naming four things
    # rarely gets all four from three sources.
    return len(missing) * 2 <= len(wanted), missing


def domains(results) -> list:
    """Distinct hosts, for a person to judge. Never scored by machine."""
    seen: list = []
    for result in results:
        url = getattr(result, "url", "") or ""
        if "//" in url:
            host = url.split("//", 1)[1].split("/", 1)[0]
            if host and host not in seen:
                seen.append(host)
    return seen


def _retrieve_blocking(query: str, num_results: int, packet_sources: int,
                       highlights_per_source: int, search_type: str,
                       recency_days: int = 0, live_domain: str = "",
                       include_domains: tuple = (), must_name: str = "",
                       backend: str = "exa", objective: str = "",
                       extra_queries: tuple = ()) -> Packet:
    """One web search, ranked, packed and costed.

    `recency_days` becomes the backend's after-date filter, which is the
    *filter* half of "recency filters, credibility sorts". Zero means an
    evergreen question and no window at all - a piece on how something works
    is not improved by refusing to look at anything written last year.
    """
    started = time.perf_counter()
    import provider_usage

    try:
        if backend == "parallel":
            returned, cost = _search_parallel(query, num_results, recency_days,
                                              include_domains, objective,
                                              extra_queries)
        elif backend == "perplexity":
            returned, cost = _search_perplexity(query, num_results, recency_days,
                                                include_domains, objective)
        else:
            returned, cost = _search_exa(query, num_results, recency_days,
                                         include_domains, objective, search_type)
    except ResearchUnavailable:
        # Configuration, not an outage: nothing was asked of the vendor.
        raise
    except Exception:
        provider_usage.record(backend, ok=False)
        raise
    provider_usage.record(backend)
    elapsed = time.perf_counter() - started

    results = screen_results(rank_results(returned), query=query,
                             live_domain=live_domain)
    if must_name:
        results = names_place(results, must_name)
    import provenance as provenance_mod

    return Packet(
        context=build_packet(results, packet_sources, highlights_per_source),
        sources=domains(results),
        # Only the sources that actually made it into the packet are credited.
        # Listing everything the search returned would claim corroboration
        # from passages the writer never saw.
        provenance=provenance_mod.from_results(
            list(results)[:packet_sources], retriever=backend),
        searches=1,
        seconds=elapsed,
        cost=float(cost),
        results_returned=len(returned),
        backend=backend,
        window_days=recency_days,
        tried=[backend],
    )


async def _second_look(query: str, num_results: int, packet_sources: int,
                       highlights_per_source: int,
                       search_type: str, live_domain: str = "",
                       include_domains: tuple = (), must_name: str = "",
                       backend: str = "exa", objective: str = ""
                       ) -> Optional[Packet]:
    """The one extra search, which is allowed to fail quietly. `retrieve` is not.

    The distinction is the whole reason this is a separate function, and it is
    worth stating because `retrieve` is pinned by a test that forbids it to
    catch anything:

    * A **first** retrieval that fails means the episode was never researched.
      That has to reach the caller, or a deployment believes it is researching
      when it is not - the failure `test_exa_failure_does_not_fall_back_inside_retrieve`
      exists to prevent, and the reason `retrieve` handles nothing.
    * A **second** retrieval that fails means research happened and an optional
      improvement to it did not. The first packet is still real evidence, and
      throwing it away because a bonus attempt broke would make the episode
      worse for no one's benefit.

    So this returns `None` on failure and never re-raises, and it is the only
    place in this module permitted to do that.
    """
    try:
        return await asyncio.to_thread(
            _retrieve_blocking, query, num_results, packet_sources,
            highlights_per_source, search_type, 0, live_domain,
            include_domains, must_name, backend, objective)
    except Exception as exc:  # noqa: BLE001 - see docstring
        log.warning("the second look failed for %r: %s; keeping the first "
                    "packet", query, exc)
        return None


def _note_gaps(packet: "Packet", must_establish: list) -> "Packet":
    """Name what the brief asked for and this packet does not appear to hold.

    Every rung runs this. A packet that answers half the brief is still worth
    writing from, and the writer is told which half is missing rather than
    filling it from memory in the same confident voice - §88. Additive: a
    retriever that already named its own gaps keeps them.
    """
    _, missing = packet_covers(packet.context, must_establish)
    for item in missing:
        if item not in packet.missing:
            packet.missing.append(item)
    return packet


def packet_size(minutes: int) -> dict:
    """How much evidence an episode of `minutes` is handed (the 10.7 packet).

    A five-minute search used to get the same three sources and two
    highlights each as a two-minute one, and the writer drops a beat with
    nothing behind it - so it ended near two minutes whatever was asked.
    Duration buys depth, and depth needs material: past the browse length
    each extra minute brings one more source, and four minutes or more a
    third highlight each. Two minutes and under overrides nothing, so every
    browse episode is unchanged. The sources come out of the results the one
    Exa call already returns, so a longer episode costs prompt tokens, never
    another search.
    """
    extra = max(0, int(minutes) - BROWSE_MINUTES)
    if not extra:
        return {}
    highlights = settings.exa_highlights_per_source + (1 if int(minutes) >= 4 else 0)
    return {"packet_sources": min(settings.exa_num_results,
                                  settings.exa_packet_sources + extra),
            "highlights_per_source": highlights}


async def retrieve(query: str, backend: Optional[str] = None,
                   brief=None, **overrides: Any) -> Packet:
    """Research `query` with the configured backend.

    Raises `ResearchUnavailable` when the *configured* backend cannot run. It
    does not fall back to the other one here: a deployment that asked for Exa
    and silently got GDELT would be measuring one thing while believing
    another, and this project has paid for that shape more than once.
    The caller may fall back, and when it does the packet says so out loud in
    `fell_back_from`.

    **The brief, when there is one**, supplies what to search for
    (`Brief.retrieval`), how fresh it has to be (`recency_days`) and what the
    packet must contain (`must_establish`). Without one this behaves exactly as
    it did before FAM EI existed: the raw query, no window, no second look.

    **The one retry**, and its rule. A packet that misses what the brief said
    the episode needs buys one more search - never more. The second search is
    not a rephrasing by another model call, which would put a second round trip
    in front of the first word; it drops the recency window and searches the
    resolved subject, which are the two things most likely to have caused an
    empty result. A retry that also misses still returns its packet: half an
    answer beats none, and the writer is told which parts are thin.
    """
    chosen = (backend or settings.research_backend or "").strip().lower()
    if chosen not in RESEARCH_BACKENDS:
        raise ResearchUnavailable(
            f"RESEARCH_BACKEND={chosen!r} is not a backend. Use one of: "
            f"{', '.join(RESEARCH_BACKENDS)}. Refusing rather than falling "
            "back - an unrecognised value must never quietly pick one.")
    query = (query or "").strip()
    if not query:
        raise ResearchUnavailable("nothing to research: the query is empty")

    if chosen == "gdelt":
        packet = await retrieve_with_gdelt(
            query, brief=brief,
            recency_days=int(overrides.get("recency_days", search_window(brief))))
        # The same sufficiency check every other rung runs. Skipping it left
        # `thin_on` empty on this backend, so a packet of headlines with no
        # passages under them reached the writer as though it answered the
        # brief - which is the §88 shape `thin_on` exists to prevent, and
        # GDELT is the rung most likely to produce it.
        return _note_gaps(packet, list(overrides.get(
            "must_establish", getattr(brief, "must_establish", []) or [])))

    num_results = int(overrides.get("num_results", settings.exa_num_results))
    packet_sources = int(overrides.get("packet_sources", settings.exa_packet_sources))
    highlights_per_source = int(
        overrides.get("highlights_per_source", settings.exa_highlights_per_source))
    search_type = str(overrides.get("search_type", DEFAULT_SEARCH_TYPE))
    recency_days = int(overrides.get("recency_days", search_window(brief)))
    written = str(overrides.get("objective", "") or "")
    objective = written or objective_for(brief, recency_days)
    must_establish = list(overrides.get(
        "must_establish", getattr(brief, "must_establish", []) or []))

    # Off the event loop: other episodes are being served while this runs,
    # and a synchronous HTTP call on the loop would stop all of them.
    live_domain = str(getattr(brief, "live_domain", "") or "")
    include_domains = tuple(overrides.get("include_domains", ()) or ())
    must_name = str(overrides.get("must_name", "") or "")
    packet = await asyncio.to_thread(
        _retrieve_blocking, query, num_results, packet_sources,
        highlights_per_source, search_type, recency_days, live_domain,
        include_domains, must_name, chosen, objective,
        queries_for(query, brief))

    covered, missing = packet_covers(packet.context, must_establish)
    packet.missing = list(missing)
    # **An empty packet always buys the second look.** This used to be gated
    # on `covered`, and `packet_covers("", [])` is `True` - so a brief that
    # named nothing specific to establish turned a search that found
    # *absolutely nothing* into a satisfied one, and the retry that exists for
    # exactly this case never ran. The commonest way to get here is the
    # recency window: a narrow one on a subject nothing was published about
    # in that window returns zero results, and the second look is the one that
    # drops the window.
    if (not packet or not covered) and settings.research_retry:
        # **EI's own broader phrasing first**, then the resolved subject, then
        # the raw query - `Brief.broader` picks whichever exists and differs
        # from what was just searched. The fallback query is written in the
        # same call as the precise one, so this stays what §82 required: one
        # more *search*, never a model call to rephrase. §109.
        broader = (getattr(brief, "broader", "") or "").strip()
        if not broader:
            broader = (getattr(brief, "subject", "") or "").strip() or query
        # **On a second index where there is one** (§258): the same index
        # asked again tends to miss the same way, so the second look goes to
        # `RESEARCH_SECOND_LOOK` (Perplexity) when that can serve, and to the
        # configured backend otherwise.
        other = second_look_backend(chosen)
        log.info("%s packet missed %s for %r; one more search on %s for %r "
                 "with no window", chosen, missing, query, other, broader)
        second = await _second_look(broader, num_results, packet_sources,
                                    highlights_per_source, search_type,
                                    live_domain, include_domains, must_name,
                                    other, written or objective_for(brief, 0))
        if second is not None:
            _, second_missing = packet_covers(second.context, must_establish)
            # Keep whichever packet answers more of the brief; on a tie keep
            # the first, which was searched with the window and is fresher.
            #
            # **`not packet` is the other half of that, and it was missing.**
            # With no `must_establish` both packets "miss" nothing, so the tie
            # rule kept the first - and the first is the one with nothing in
            # it. A retry that finds sources and then discards them for an
            # empty packet is worse than no retry: it pays for the search and
            # reports it as thin.
            # Both backends, when the second look went to another index.
            tried = list(dict.fromkeys(packet.tried + second.tried))
            if second and (not packet or len(second_missing) < len(missing)):
                second.searches += packet.searches
                second.cost += packet.cost
                second.seconds += packet.seconds
                second.missing = list(second_missing)
                packet = second
            else:
                packet.searches += second.searches
                packet.cost += second.cost
                packet.seconds += second.seconds
            packet.tried = tried
            packet.retried = True

    # **The second opinion.** One vendor's index is one vendor's blind spots,
    # and a question Exa covers poorly currently produces a thin episode with
    # nothing to show that another index would have done better. GDELT is
    # read from its export copy on disk (§211), so a cross-check sends GDELT
    # nothing and costs the episode a local read - and it
    # adds corroboration a listener can *see*, because both retrievers reach
    # the provenance panel.
    #
    # Additive only: it never replaces the primary packet and never fails an
    # episode. `gdelt.retrieve` returns [] on any error by contract.
    if settings.gdelt_cross_check and not must_name:
        # Never on a local question (§194): GDELT is off that path entirely.
        import gdelt
        import provenance as provenance_mod

        second = await gdelt.retrieve(query, recency_days=recency_days)
        if second:
            ranked = screen_results(rank_results(second), query=query,
                                    live_domain=live_domain)[:packet_sources]
            extra = build_packet(ranked, packet_sources, highlights_per_source)
            if extra.strip():
                packet.context = (packet.context or "") + "\n" + extra
                packet.searches += 1
                for host in domains(ranked):
                    if host not in packet.sources:
                        packet.sources.append(host)
                found = provenance_mod.from_results(ranked, retriever="gdelt")
                if packet.provenance is None:
                    packet.provenance = found
                else:
                    for item in found.items:
                        packet.provenance.add(item)
                    if "gdelt" not in packet.provenance.retrievers:
                        packet.provenance.retrievers.append("gdelt")
                log.info("gdelt cross-check added %d source(s) for %r",
                         len(ranked), query)

    if not packet:
        # Retrieval succeeded and found nothing usable. Not an exception - the
        # episode is still answerable from knowledge - but it must not pass as
        # research, so it says so and the prompt gets no evidence block.
        log.warning("%s returned %d result(s) but no usable evidence for %r",
                    chosen, packet.results_returned, query)
    else:
        log.info("%s: %d chars from %d source(s) in %.2fs (~$%.4f) for %r "
                 "[window=%sd, searches=%d%s]",
                 chosen, len(packet.context), len(packet.sources), packet.seconds,
                 packet.cost, query, packet.window_days or "none",
                 packet.searches, ", still thin" if packet.missing else "")
    return packet


# --------------------------------------------------------------------------
# GDELT, as a retriever rather than only as a second opinion
# --------------------------------------------------------------------------
async def retrieve_with_gdelt(query: str, brief=None, recency_days: int = 0
                              ) -> Packet:
    """An article index that needs no credential. Never raises.

    It was already here as the additive cross-check beside an Exa packet. The
    reason it is also a rung of its own (§109): when the configured retriever
    comes back with nothing, the alternative rungs are the model's own search
    at 10-25 seconds, or writing from memory. Since §211 it is a read of
    GDELT's export copy on disk - no request to GDELT at all, so it costs
    GDELT nothing however many episodes fall to it.

    What it cannot do is carry highlights: GDELT returns articles, so the
    packet is titles, dates and grades, with no passages under them. That is
    thinner evidence than Exa's and it is *evidence*, which is the distinction
    the whole ladder turns on - the writer is reading what was published
    rather than recalling what it read in training.
    """
    started = time.perf_counter()
    packet = Packet(backend="gdelt", window_days=int(recency_days or 0))
    try:
        import gdelt
        import provenance as provenance_mod

        results = await gdelt.retrieve(query, recency_days=recency_days)
    except Exception:  # noqa: BLE001 - a rung that raises is a rung that fails
        packet.seconds = time.perf_counter() - started
        log.warning("gdelt failed while retrieving %r", query, exc_info=True)
        return packet

    ranked = screen_results(
        rank_results(list(results or [])), query=query,
        live_domain=str(getattr(brief, "live_domain", "") or "")
    )[:settings.exa_packet_sources]
    packet.context = build_packet(ranked, settings.exa_packet_sources,
                                  settings.exa_highlights_per_source)
    packet.sources = domains(ranked)
    packet.results_returned = len(results or [])
    packet.searches = 1 if results else 0
    packet.seconds = time.perf_counter() - started
    if ranked:
        packet.provenance = provenance_mod.from_results(ranked, retriever="gdelt")
    if packet:
        log.info("gdelt: %d chars from %d source(s) in %.2fs for %r",
                 len(packet.context), len(packet.sources), packet.seconds, query)
    else:
        log.warning("gdelt returned nothing usable for %r", query)
    return packet


#: The rungs below the configured backend, in the order they are tried.
#:
#: **Perplexity, then GDELT** (§258). Perplexity is a second, independent
#: index with passages, so it is the better backup; GDELT stays below it as
#: the floor - free, keyless and read from disk, so it still answers when both
#: paid accounts fail together. A rung that cannot serve (no key, `GDELT=0`)
#: is left out by `ladder()`.
#:
#: The model's own web search used to be the last rung (§135, at the owner's
#: direction) - a separate research call at 10-25 seconds - and it is deleted
#: rather than switched off: everything an episode is written from comes from
#: the search backends, GDELT and the live providers (API-Sports, Finnhub,
#: Polymarket), and nowhere else. A question that finds nothing on any index
#: is still written (§256 deleted the §109 refusal).
#:
#: **Exa sits between them while it is kept for comparison**: a deploy that
#: arrives before the new keys are set still researches on the old one, and
#: `ladder()` drops it the day `EXA_API_KEY` is removed.
FALLBACK_RUNGS = ("perplexity", "exa", "gdelt")


def second_look_backend(primary: str) -> str:
    """Where the one retry goes: `RESEARCH_SECOND_LOOK` when it names another
    web backend that can serve, the configured backend otherwise (§258)."""
    other = (settings.research_second_look or "").strip().lower()
    if other in WEB_BACKENDS and other != primary and backend_available(other):
        return other
    return primary


def ladder(backend: Optional[str] = None) -> list:
    """The rungs a researched episode will actually try, in order.

    One definition, walked by `ScriptGenerator.research` and reported by the
    health report and the startup warning. A second hand-written copy of it
    is the shape CLAUDE.md calls a decorative guard.

    **Only rungs that can serve are listed**, which is the difference between
    a ladder and a wish: GDELT returns `[]` whenever `GDELT=0`, which is the
    shipped default, so listing it unconditionally would have had
    `/api/health` promising a fallback that cannot fetch anything. A rung that
    is configured off is not a rung.
    """
    chosen = (backend or settings.research_backend or "").strip().lower()
    rungs = [chosen] if chosen else []
    for rung in FALLBACK_RUNGS:
        if rung == chosen or not _rung_available(rung):
            continue
        rungs.append(rung)
    return rungs


def _rung_available(rung: str) -> bool:
    """Whether a fallback rung could return anything if it were asked."""
    if rung == "gdelt":
        import gdelt

        return gdelt.available()[0]
    if rung in WEB_BACKENDS:
        return backend_available(rung)
    # Nothing else is a retriever (§135). An unknown name is not a rung.
    return False


def report() -> dict:
    """What the server can actually do for research right now - for /api/health."""
    ok, detail = diagnose()
    return {
        "backend": settings.research_backend,
        # A retired value this deployment still sets, and was run as the
        # default instead (§135). "" when nothing was replaced.
        "backend_replaced": getattr(settings, "research_backend_replaced", ""),
        "backends": list(RESEARCH_BACKENDS),
        "exa_configured": ok,
        "exa_detail": detail,
        # Why the *configured* backend can or cannot serve, whichever it is
        # - what the startup warning and the preflight print (§258).
        "backend_detail": (backend_diagnose(settings.research_backend)[1]
                           if settings.research_backend in WEB_BACKENDS
                           else ("GDELT=1" if _rung_available("gdelt")
                                 else "GDELT=0 or no export copy yet")),
        # Every web backend, configured or not, and why (§258).
        "web_backends": {name: dict(zip(("configured", "detail"),
                                        backend_diagnose(name)))
                         for name in WEB_BACKENDS},
        "second_look": second_look_backend(settings.research_backend),
        # True when the *configured* backend cannot run - whichever it is.
        # It used to ask only about Exa, so `RESEARCH_BACKEND=gdelt` with
        # `GDELT=0` reported healthy and retrieved nothing, for ever.
        # A researched episode no longer fails when this is true (it falls
        # down the ladder), but it is not being researched the way this
        # deployment asked, which is worth seeing on a tab.
        "unavailable": not _rung_available(settings.research_backend),
        "gdelt_configured": _rung_available("gdelt"),
        # Whether evidence reaches the writer dated and attributed. Reported
        # because an undated packet does not look broken from outside - it
        # produces an episode that is confidently wrong about *when*, which is
        # the one failure mode that reads as a writing problem and is not.
        "dated_packet": bool(settings.exa_dated_packet),
        "retry_on_thin_packet": bool(settings.research_retry),
        # Whether anything searches while the episode is being spoken. It is
        # always False now and is reported rather than assumed: from outside,
        # an episode written from a packet and an episode written by a model
        # searching mid-sentence look identical until you hear the first ten
        # seconds of one. PROBLEMS.md §108.
        "search_during_writing": False,
        # The rungs an episode will actually try, in the order it tries them,
        # starting with whatever this deployment configured. Reported rather
        # than described in prose anywhere, because "what happens when the
        # search finds nothing" is the question a health tab is being asked,
        # and the answer depends on what is configured here. §109.
        "ladder": ladder(),
        # And what happens at the bottom of it: nothing is refused for want
        # of evidence since §256 - the episode is written regardless.
        "refuses_without_evidence": False,
    }
