"""Retrieval that runs *before* Claude writes, so Claude reads rather than searches.

Two ways to research an episode, and the difference is only who does the
looking - **both finish before the first word is written**:

* **exa** - Exa retrieves, this module builds an evidence packet, and the
  packet goes into the prompt as context.
* **claude** - the model is given Anthropic's server-side `web_search` tool in
  a call of its own whose entire job is to come back with evidence. What it
  reports is shaped into the same packet and goes into the writing prompt the
  same way. Used where there is no Exa key, and as the second attempt when Exa
  comes back with nothing usable.

**`claude` used to mean something else, and that is the change here**
(PROBLEMS.md §108). The tool was attached to the *writing* call, so the model
searched while it wrote - which meant the first sentence, the one a listener
uses to decide whether there will be a second, was produced before anything
had been looked up. The prompt spent a paragraph asking it not to write until
it had searched ("a tool is not an instruction", §77), a guard held back the
disclaimers it wrote anyway (§94), and the episode still opened on the one
thing it could say with nothing in hand. None of that is needed once the
looking happens in its own call: the writer is handed evidence, exactly as on
the Exa path, and the tool is never attached to the call that speaks.

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
answers in about half a second, and the `claude` pre-pass costs the 10-25
seconds the model's own search has always cost, now spent before the episode
starts instead of underneath it.

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
from types import SimpleNamespace
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
    #: The `usage` block of the model call that did the searching, on the
    #: `claude` backend. `None` on Exa, which bills per search rather than per
    #: token - the two costs are added to `metering.Usage` by different
    #: methods and must not be conflated.
    usage: object = None

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
    import provenance as provenance_mod

    return Packet(
        context=build_packet(results, packet_sources, highlights_per_source),
        sources=domains(results),
        # Only the sources that actually made it into the packet are credited.
        # Listing everything the search returned would claim corroboration
        # from passages the writer never saw.
        provenance=provenance_mod.from_results(
            list(results)[:packet_sources], retriever="exa"),
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

    **The `claude` backend retrieves now**, in a call of its own that finishes
    before the writing call starts - see `retrieve_with_claude`. It used to
    return an empty packet and let the writing call carry the search tool,
    which is how the first sentence of an episode came to be written before
    anything had been looked up (PROBLEMS.md §108).

    Raises `ResearchUnavailable` when the *configured* backend cannot run. It
    does not fall back to the other one here: a deployment that asked for Exa
    and silently got the model's own search would be measuring one thing while
    believing another, and this project has paid for that shape more than once.
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
        return await retrieve_with_gdelt(
            query, brief=brief,
            recency_days=int(overrides.get(
                "recency_days", getattr(brief, "recency_days", 0) or 0)))

    if chosen == "claude":
        packet = await retrieve_with_claude(
            query, brief=brief,
            recency_days=int(overrides.get(
                "recency_days", getattr(brief, "recency_days", 0) or 0)),
            must_establish=list(overrides.get(
                "must_establish", getattr(brief, "must_establish", []) or [])))
        # The model's own "NOT FOUND" line and this check answer the same
        # question from opposite sides - what it noticed it was missing, and
        # what the brief asked for and the text does not appear to contain.
        # Both are kept: the writer is told a part is thin either way.
        _, missing = packet_covers(packet.context, list(overrides.get(
            "must_establish", getattr(brief, "must_establish", []) or [])))
        for item in missing:
            if item not in packet.missing:
                packet.missing.append(item)
        return packet

    num_results = int(overrides.get("num_results", settings.exa_num_results))
    packet_sources = int(overrides.get("packet_sources", settings.exa_packet_sources))
    highlights_per_source = int(
        overrides.get("highlights_per_source", settings.exa_highlights_per_source))
    search_type = str(overrides.get("search_type", DEFAULT_SEARCH_TYPE))
    recency_days = int(overrides.get(
        "recency_days", getattr(brief, "recency_days", 0) or 0))
    must_establish = list(overrides.get(
        "must_establish", getattr(brief, "must_establish", []) or []))

    # Off the event loop: other episodes are being served while this runs,
    # and a synchronous HTTP call on the loop would stop all of them.
    packet = await asyncio.to_thread(
        _retrieve_blocking, query, num_results, packet_sources,
        highlights_per_source, search_type, recency_days)

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
        subject = (getattr(brief, "subject", "") or "").strip() or query
        log.info("exa packet missed %s for %r; one more search on %r with no "
                 "window", missing, query, subject)
        second = await _second_look(subject, num_results, packet_sources,
                                    highlights_per_source, search_type)
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
            packet.retried = True

    # **The second opinion.** One vendor's index is one vendor's blind spots,
    # and a question Exa covers poorly currently produces a thin episode with
    # nothing to show that another index would have done better. GDELT is
    # keyless and free, so a cross-check costs nothing per episode - and it
    # adds corroboration a listener can *see*, because both retrievers reach
    # the provenance panel.
    #
    # Additive only: it never replaces the primary packet and never fails an
    # episode. `gdelt.retrieve` returns [] on any error by contract.
    if settings.gdelt_cross_check:
        import gdelt
        import provenance as provenance_mod

        second = await gdelt.retrieve(query, recency_days=recency_days)
        if second:
            ranked = rank_results(second)[:packet_sources]
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
        log.warning("exa returned %d result(s) but no usable evidence for %r",
                    packet.results_returned, query)
    else:
        log.info("exa: %d chars from %d source(s) in %.2fs (~$%.4f) for %r "
                 "[window=%sd, searches=%d%s]",
                 len(packet.context), len(packet.sources), packet.seconds,
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
    at 10-25 seconds, or writing from memory. This costs one keyless HTTP call
    and sometimes ends the ladder there.

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

    ranked = rank_results(list(results or []))[:settings.exa_packet_sources]
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


# --------------------------------------------------------------------------
# Claude's own search, as a retrieval
# --------------------------------------------------------------------------
#
# One call whose only job is to come back with evidence. It is not the call
# that writes the episode, and that separation is the whole of it: the writer
# starts with the packet in front of it instead of with a tool it has been
# asked to remember to use.

#: What the searching call is for. Deliberately not the house voice - nothing
#: this call writes is ever spoken, and asking one model call to both research
#: and write is what produced an opening written before the research landed.
CLAUDE_RESEARCH_SYSTEM = """You are a researcher. You search the web and report \
what you found. You never write the piece that uses it - something else does \
that, from your report, and it can only be as good as what you hand over.

Search before you answer, and search more than once if the first query misses. \
Report only what a source actually says. Never fill a gap from memory, never \
soften a source, and never report something you did not read.

Output nothing but the evidence blocks in the format asked for. No preamble, no \
summary, no advice about how to write it."""


def _claude_research_prompt(query: str, brief=None, recency_days: int = 0,
                            must_establish=()) -> str:
    """What to look for, in the words the brief already worked out."""
    lines = [f"Research this so an episode can be written from it:\n\n{query}\n"]
    subject = (getattr(brief, "subject", "") or "").strip()
    if subject and subject.lower() != query.strip().lower():
        lines.append(f"The subject, as far as it has been resolved: {subject}.")
    why_now = (getattr(brief, "why_now", "") or "").strip()
    if why_now:
        lines.append(
            f"The reason this is being asked now may be: {why_now}. That is a "
            "hypothesis, not a fact - confirm it or drop it.")
    wanted = [str(item).strip() for item in (must_establish or []) if str(item).strip()]
    if wanted:
        lines.append("The episode needs these established:\n- "
                     + "\n- ".join(wanted))
    if recency_days:
        lines.append(
            f"Only the last {recency_days} day(s) count as current here. Older "
            "material is background and must be labelled with its own date.")
    lines.append(
        "It is currently " + _now_phrase() + ".\n\n"
        "Report what you found as blocks in exactly this format, at most "
        f"{settings.exa_packet_sources} of them, best source first:\n\n"
        "SOURCE 1\n"
        "Title: the headline or page title\n"
        "URL: the full address you read it at\n"
        "Published: YYYY-MM-DD, or unknown if the page does not say\n"
        "Key evidence:\n"
        "the specific sentences that matter - figures, names, what happened, "
        "quoted or closely paraphrased, not your summary of them\n\n"
        "Then, if and only if something the episode needs is genuinely not in "
        "anything you found, one final line:\n\n"
        "NOT FOUND: what is missing\n\n"
        "A preview, odds, a projected line-up or a 'how to watch' page is "
        "evidence that a thing has not happened yet. Report it as what it is; "
        "do not report a result that no source states.")
    return "\n\n".join(lines)


def _now_phrase() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%A %d %B %Y at %H:%M UTC").replace(" 0", " ")


#: A `URL:` line in what the model reported. It is the one thing in the block
#: the writer must never see - a domain in the packet is a domain the voice can
#: read out - and the one thing that lets this module grade the source itself
#: rather than taking the model's word for how reliable its own find was.
_URL_LINE = re.compile(r"^\s*URL:\s*(\S+)\s*$", re.I | re.M)
_PUBLISHED_LINE = re.compile(r"^\s*Published:\s*(.+?)\s*$", re.I | re.M)
_NOT_FOUND_LINE = re.compile(r"^\s*NOT FOUND:\s*(.+?)\s*$", re.I | re.M)


def shape_claude_packet(text: str, now: Optional[datetime] = None
                        ) -> tuple[str, list, list]:
    """Turn what the searching call reported into a packet the writer can read.

    Three things happen here, and each is a rule from elsewhere in the project
    applied to a source of evidence that did not exist when they were written:

    * **The hostname comes out.** `build_packet` has never put one in the
      packet, because a domain in the packet is a domain the voice can read
      aloud. The model is asked for the URL precisely so that this can take it
      back out again - and it is returned separately, which is what the
      sources panel and the log are built from.
    * **The grade is computed, never taken.** `credibility()` reads a closed
      list of publishers; a model asked to rate its own find would be putting
      a confidence on the panel that nothing measured.
    * **The relative phrase is computed.** "Yesterday" is subtraction from a
      date, which is code's job - the failure that rule exists for is an
      episode asked to date events from evidence carrying no dates.

    Returns the packet text, the hostnames behind it, and whatever the call
    reported it could not find.
    """
    now = now or datetime.now(timezone.utc)
    hosts: list = []

    def _source_type(match) -> str:
        url = match.group(1).strip().strip("<>,;")
        probe = SimpleNamespace(url=url)
        host = host_of(probe)
        if host and host not in hosts:
            hosts.append(host)
        return f"Source type: {TIER_LABELS[credibility(probe)]}"

    shaped = _URL_LINE.sub(_source_type, text or "")

    def _dated(match) -> str:
        stamp = match.group(1).strip()
        found = _DATE.search(stamp)
        if not found:
            return "Published: unknown"
        try:
            when = datetime(int(found.group(1)), int(found.group(2)),
                            int(found.group(3)), tzinfo=timezone.utc)
        except ValueError:
            return "Published: unknown"
        return f"Published: {when:%Y-%m-%d} ({age_phrase(when, now)})"

    shaped = _PUBLISHED_LINE.sub(_dated, shaped)

    missing = [m.strip() for m in _NOT_FOUND_LINE.findall(shaped) if m.strip()]
    shaped = _NOT_FOUND_LINE.sub("", shaped)
    return shaped.strip(), hosts, missing


def research_client():
    """The client the searching call uses.

    Its own function so a test can replace it without reaching inside the
    coroutine, and so the credential is read at call time rather than at
    import - a key rotated in the secrets manager must reach a call made
    later in the life of the process.
    """
    import credentials
    from anthropic_client import build_async_client

    return build_async_client(credentials.active("ANTHROPIC_API_KEY"))


async def retrieve_with_claude(query: str, brief=None, recency_days: int = 0,
                               must_establish=()) -> Packet:
    """Search with the model's own tool, in a call of its own, before writing.

    Never raises. Every failure - no credential, a refusal, a timeout, a
    provider error - comes back as an empty packet, because this is a layer
    that adds quality and one of those must not be able to subtract
    availability: the episode is still answerable, and an empty packet is
    exactly what an unresearched one already looks like.
    """
    started = time.perf_counter()
    packet = Packet(backend="claude", window_days=int(recency_days or 0))
    try:
        import provenance as provenance_mod

        client = research_client()
        message = await client.messages.create(
            model=settings.model,
            max_tokens=settings.research_max_tokens,
            system=CLAUDE_RESEARCH_SYSTEM,
            tools=[{
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": settings.max_web_searches,
            }],
            messages=[{
                "role": "user",
                "content": _claude_research_prompt(
                    query, brief, recency_days, must_establish),
            }],
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        packet.seconds = time.perf_counter() - started
        log.warning("the model's own search could not run for %r: %s; the "
                    "episode will be written without evidence", query, exc)
        return packet

    text = "\n".join(
        str(getattr(block, "text", "") or "")
        for block in getattr(message, "content", []) or []
        if getattr(block, "type", "") == "text"
    )
    shaped, hosts, missing = shape_claude_packet(text)
    packet.context = shaped
    packet.sources = hosts
    packet.missing = missing
    packet.results_returned = len(hosts)
    packet.seconds = time.perf_counter() - started
    packet.usage = getattr(message, "usage", None)
    try:
        found = provenance_mod.from_web_search(message)
        packet.provenance = found if found.items else None
        packet.searches = len(found.items)
    except Exception:  # noqa: BLE001 - provenance never fails an episode
        log.warning("could not read provenance off the research call",
                    exc_info=True)

    if not packet:
        log.warning("the model searched and reported no usable evidence for %r",
                    query)
    else:
        log.info("claude search: %d chars from %d source(s) in %.2fs for %r%s",
                 len(packet.context), len(packet.sources), packet.seconds,
                 query, ", still thin" if packet.missing else "")
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
        # Whether anything searches while the episode is being spoken. It is
        # always False now and is reported rather than assumed: from outside,
        # an episode written from a packet and an episode written by a model
        # searching mid-sentence look identical until you hear the first ten
        # seconds of one. PROBLEMS.md §108.
        "search_during_writing": False,
        # What happens when the configured backend comes back with nothing:
        # the model's own search runs as a second retrieval, still before the
        # first word, and the episode's record says it did.
        "fallback_backend": "claude",
    }
