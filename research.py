"""Retrieval that runs *before* Claude writes, so Claude reads rather than searches.

Two ways to research an episode, and the difference is who does the looking:

* **claude** - the model gets Anthropic's server-side `web_search` tool and
  searches while it writes. One call, no second credential, and the searching
  is inside the model's own turn.
* **exa** - Exa retrieves first, this module builds an evidence packet, and the
  packet goes into the prompt as context. Claude reads it. No tool, no
  searching inside the turn.

The second is what this module is. The call and the packet are byte-for-byte
the ones the manual benchmark measured on 2026-09-05 and the experiment layer
then repeated - `search_and_contents(type="fast", num_results=8,
highlights=True)`, top 3 results, 2 highlights each, formatted
`SOURCE n / Title: / Key evidence:`. Keeping them identical is the point: the
numbers already measured stay comparable, and a change of packet shape is a
deliberate act rather than a drift.

**Where this is allowed to cost time.** A retrieval in front of the first word
is the one cost this product refuses (the one-sentence spec, and PROBLEMS.md
§55 on the cold open). It is affordable here for one reason only: a researched
episode already runs two calls at once. `_answer_first` speaks the
from-knowledge half immediately while the researched half works underneath, and
this retrieval happens on the researched half - so it is covered by an answer
rather than by filler or by silence. With `ANSWER_FIRST=0` there is nothing
covering it, and the wait is real; that is a deliberate trade of that setting,
not of this module.

**It does not block the event loop.** The experiment ran `exa_py` synchronously
because trials were pinned to one at a time and a thread hand-off would have
added its own time to a number meant to be Exa's. Production is the opposite
case: the cover is speaking, the assembler is batching, and a synchronous HTTP
call on the loop would stop both. So the call goes to a worker thread, exactly
as speech synthesis does.
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

from config import RESEARCH_BACKENDS, settings

log = logging.getLogger("research")

#: The manual benchmark's settings, as defaults. Changing one here silently
#: breaks comparability with the hand-measured run; override per request or by
#: configuration instead.
DEFAULT_SEARCH_TYPE = "fast"

#: Exa's published rate, used only when a response reports no cost of its own.
COST_PER_SEARCH = 0.005


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
})

#: Established outlets and trade press: reliable, not primary.
TIER_ESTABLISHED = frozenset({
    "cnn.com", "nbcnews.com", "cbsnews.com", "abcnews.go.com", "axios.com",
    "politico.com", "thetimes.co.uk", "telegraph.co.uk", "independent.co.uk",
    "forbes.com", "fortune.com", "businessinsider.com", "techcrunch.com",
    "theverge.com", "arstechnica.com", "wired.com", "engadget.com",
    "marketwatch.com", "barrons.com", "investopedia.com", "sportingnews.com",
    "cbssports.com", "nbcsports.com", "si.com", "yahoo.com", "usatoday.com",
})

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
    now = now or datetime.now(timezone.utc)
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


class ResearchUnavailable(RuntimeError):
    """The configured backend cannot run, and says which part is missing."""


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
        }


# --------------------------------------------------------------------------
# Exa
# --------------------------------------------------------------------------
def _client():
    """Build the Exa client, or say precisely what is missing.

    Imported here rather than at module scope so that `exa_py` is an optional
    dependency: a deployment on the `claude` backend must not need it, and the
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
            "or set RESEARCH_BACKEND=claude to let the model search instead.")
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

    now = now or datetime.now(timezone.utc)
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
                       recency_days: int = 0) -> Packet:
    """One Exa call, ranked, packed and costed.

    `recency_days` becomes `start_published_date`, which is the *filter* half
    of "recency filters, credibility sorts". Zero means an evergreen question
    and no window at all - a piece on how something works is not improved by
    refusing to look at anything written last year.
    """
    client = _client()
    kwargs: dict = {
        "type": search_type,
        "num_results": num_results,
        "highlights": True,
    }
    if recency_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=recency_days)
        kwargs["start_published_date"] = cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    started = time.perf_counter()
    reply = client.search_and_contents(query, **kwargs)
    elapsed = time.perf_counter() - started

    results = rank_results(list(getattr(reply, "results", []) or []))
    cost = getattr(getattr(reply, "cost_dollars", None), "total", None)
    return Packet(
        context=build_packet(results, packet_sources, highlights_per_source),
        sources=domains(results),
        searches=1,
        seconds=elapsed,
        cost=float(cost) if cost is not None else COST_PER_SEARCH,
        results_returned=len(results),
        backend="exa",
        window_days=recency_days,
    )


async def _second_look(query: str, num_results: int, packet_sources: int,
                       highlights_per_source: int,
                       search_type: str) -> Optional[Packet]:
    """The one extra search, which is allowed to fail quietly. `retrieve` is not.

    The distinction is the whole reason this is a separate function, and it is
    worth stating because `retrieve` is pinned by a test that forbids it to
    catch anything:

    * A **first** retrieval that fails means the episode was never researched.
      That has to reach the caller, or a deployment believes it is researching
      when it is not - the failure `test_exa_failure_does_not_become_a_claude_search`
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
            highlights_per_source, search_type, 0)
    except Exception as exc:  # noqa: BLE001 - see docstring
        log.warning("the second look failed for %r: %s; keeping the first "
                    "packet", query, exc)
        return None


async def retrieve(query: str, backend: Optional[str] = None,
                   brief=None, **overrides: Any) -> Packet:
    """Research `query` with the configured backend.

    Returns an empty packet for the `claude` backend - not an error. There is
    nothing to retrieve there because the model does its own searching inside
    the turn, and an empty packet is exactly what tells `build_prompt` to leave
    the tool attached and add no evidence block.

    Raises `ResearchUnavailable` when the *configured* backend cannot run. It
    does not fall back to the other one: a deployment that asked for Exa and
    silently got the model's own search would be measuring one thing while
    believing another, and this project has paid for that shape more than once.

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
    if chosen == "claude":
        return Packet(backend="claude")

    query = (query or "").strip()
    if not query:
        raise ResearchUnavailable("nothing to research: the query is empty")

    num_results = int(overrides.get("num_results", settings.exa_num_results))
    packet_sources = int(overrides.get("packet_sources", settings.exa_packet_sources))
    highlights_per_source = int(
        overrides.get("highlights_per_source", settings.exa_highlights_per_source))
    search_type = str(overrides.get("search_type", DEFAULT_SEARCH_TYPE))
    recency_days = int(overrides.get(
        "recency_days", getattr(brief, "recency_days", 0) or 0))
    must_establish = list(overrides.get(
        "must_establish", getattr(brief, "must_establish", []) or []))

    # Off the event loop: the cover is speaking and the assembler is batching
    # while this runs, and a synchronous HTTP call on the loop would stop both.
    packet = await asyncio.to_thread(
        _retrieve_blocking, query, num_results, packet_sources,
        highlights_per_source, search_type, recency_days)

    covered, missing = packet_covers(packet.context, must_establish)
    packet.missing = list(missing)
    if not covered and settings.research_retry:
        subject = (getattr(brief, "subject", "") or "").strip() or query
        log.info("exa packet missed %s for %r; one more search on %r with no "
                 "window", missing, query, subject)
        second = await _second_look(subject, num_results, packet_sources,
                                    highlights_per_source, search_type)
        if second is not None:
            _, second_missing = packet_covers(second.context, must_establish)
            # Keep whichever packet answers more of the brief; on a tie keep
            # the first, which was searched with the window and is fresher.
            if second and len(second_missing) < len(missing):
                second.searches += packet.searches
                second.cost += packet.cost
                second.seconds += packet.seconds
                second.missing = list(second_missing)
                packet = second
            else:
                packet.searches += second.searches
                packet.cost += second.cost
                packet.seconds += second.seconds
            packet.retried = True

    if not packet:
        # Retrieval succeeded and found nothing usable. Not an exception - the
        # episode is still answerable from knowledge - but it must not pass as
        # research, so it says so and the prompt gets no evidence block.
        log.warning("exa returned %d result(s) but no usable evidence for %r",
                    packet.results_returned, query)
    else:
        log.info("exa: %d chars from %d source(s) in %.2fs (~$%.4f) for %r "
                 "[window=%sd, searches=%d%s]",
                 len(packet.context), len(packet.sources), packet.seconds,
                 packet.cost, query, packet.window_days or "none",
                 packet.searches, ", still thin" if packet.missing else "")
    return packet


def report() -> dict:
    """What the server can actually do for research right now - for /api/health."""
    ok, detail = diagnose()
    return {
        "backend": settings.research_backend,
        "backends": list(RESEARCH_BACKENDS),
        "exa_configured": ok,
        "exa_detail": detail,
        # True when the configured backend cannot run. A researched episode
        # will fail rather than quietly search another way, so this is worth
        # seeing on a tab rather than discovering in a log.
        "unavailable": settings.research_backend == "exa" and not ok,
        # Whether evidence reaches the writer dated and attributed. Reported
        # because an undated packet does not look broken from outside - it
        # produces an episode that is confidently wrong about *when*, which is
        # the one failure mode that reads as a writing problem and is not.
        "dated_packet": bool(settings.exa_dated_packet),
        "retry_on_thin_packet": bool(settings.research_retry),
    }
