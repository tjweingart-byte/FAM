"""A seam for facts an article index cannot be fresh enough to hold.

Exa retrieves *writing about* the world. For most questions that is the right
shape - an explainer, a cause, a trend. For two kinds it is structurally wrong,
and no amount of better querying fixes it:

* **Scores, fixtures and standings.** A game ends and the scoreboard knows
  instantly; the recap that says so is written, published and indexed later.
  Between those two moments a search returns the *preview*, and an episode
  built on it describes a match that has already been played as though it is
  still coming. That is the failure in the blueprint's temporal table, and it
  is not a writing bug - the evidence really did say that.
* **Prices, indexes and rates.** Same shape, shorter fuse. "Where is the stock
  now" is answered by a quote, not by an article about a quote.

So this is the route around the index: a small registry of sources that answer
a *fact* rather than return a document, consulted when the brief says the
question turns on one.

The shape, and why it is this shape
-----------------------------------
`LiveSource` is deliberately tiny - a domain, a diagnosis, and a fetch. That is
enough for the pipeline to route to it and enough for `/api/health` to say
whether it can serve, and it commits FAM to no particular vendor. Adding a real
provider is `register(MySource())` and nothing else changes: `research` already
asks this module, and `script_generator` already knows how to put the answer in
front of the writer.

**Nothing is configured by default, and that is not the same as nothing being
here.** Both domain sources below are declared and both report, precisely, what
they would need in order to serve. That is the difference this project keeps
paying for: a capability that is absent and says so can be fixed, and one that
is absent and quiet gets shipped. `report()` is on `/api/health` for exactly
that reason.

What a live fact outranks
-------------------------
Everything. A source here reports a state with a timestamp attached, which is
strictly better evidence than a sentence in an article about that state. So
`as_prompt_block` says so to the writer, and says the timestamp out loud, so a
result that arrived four minutes ago is not described as last night's.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

#: The question shapes an article index is structurally too slow for. Matches
#: `Brief.live_domain`, and is the whole routing vocabulary - a source declares
#: one of these and the brief names one of these.
LIVE_DOMAINS = ("sports", "markets")


@dataclass
class LiveFacts:
    """A state of the world, with the time it was true attached.

    `as_of` is not decoration. The entire reason this module exists is that the
    listener is told "last night" or "two days ago" and the difference has to
    come from a timestamp rather than a guess.
    """

    domain: str
    source: str
    #: When this was true. Timezone-aware, always.
    as_of: datetime
    #: Plain statements, already in the words a person would use. One per line
    #: in the prompt, so keep them short and each one self-contained.
    facts: list = field(default_factory=list)
    #: Whether the thing being asked about has finished, is running, or has not
    #: started. The single most valuable field here: it is what stops a future
    #: event being narrated in the past tense.
    status: str = ""
    url: str = ""

    def __bool__(self) -> bool:
        return bool(self.facts)

    def age_phrase(self, now: Optional[datetime] = None) -> str:
        """How old this is, in the words the episode would use."""
        now = now or datetime.now(timezone.utc)
        seconds = max(0.0, (now - self.as_of).total_seconds())
        if seconds < 120:
            return "just now"
        if seconds < 7200:
            return f"{int(seconds // 60)} minutes ago"
        if seconds < 172800:
            return f"{int(seconds // 3600)} hours ago"
        return f"{int(seconds // 86400)} days ago"

    def as_prompt_block(self, now: Optional[datetime] = None) -> str:
        """What the writer sees. Outranks the article packet, and says so."""
        if not self:
            return ""
        lines = "\n".join(f"- {fact}" for fact in self.facts)
        status = f"\nStatus: {self.status}." if self.status else ""
        return f"""
This came from {self.source}, which reports the current state directly rather
than an article about it. It was true as of {self.as_of:%A %d %B %Y at %H:%M %Z}
- {self.age_phrase(now)}.

This outranks everything else you have been given, including anything below
that contradicts it and anything you remember. Where the two disagree, this is
what is true now.{status}

<live_facts>
{lines}
</live_facts>
"""

    def as_dict(self) -> dict:
        return {"domain": self.domain, "source": self.source,
                "as_of": self.as_of.isoformat(), "facts": list(self.facts),
                "status": self.status, "url": self.url}


class LiveSource:
    """One provider of live facts for one domain.

    Subclass, implement `diagnose` and `fetch`, and `register` it. Three rules,
    each of which this codebase has paid to learn:

    * `diagnose` says *why* it cannot serve, never just that it cannot. A check
      that answers no without a reason cannot be fixed from a log.
    * `fetch` returns `None` when it has nothing, and raises when it is broken.
      Those are different states and collapsing them hides an outage as a quiet
      miss.
    * `fetch` never falls back to another source. A deployment that asked for
      one provider and silently got another is measuring one thing and
      believing another - see `research.retrieve` for the same rule.
    """

    name = "unnamed"
    domain = ""

    def diagnose(self) -> tuple[bool, str]:
        raise NotImplementedError

    def available(self) -> bool:
        return self.diagnose()[0]

    async def fetch(self, brief) -> Optional[LiveFacts]:
        raise NotImplementedError


class _UnconfiguredSource(LiveSource):
    """A domain FAM knows it needs and has no provider for yet.

    It exists so the gap is *named* on the health report rather than being an
    absence nobody can see. It never returns facts and never pretends to.
    """

    def __init__(self, domain: str, needs: str):
        self.domain = domain
        self.name = f"{domain} (not configured)"
        self._needs = needs

    def diagnose(self) -> tuple[bool, str]:
        return False, (
            f"no {self.domain} source is configured, so {self.domain} questions "
            f"are answered from indexed articles and can lag the real state. "
            f"Needs: {self._needs}")

    async def fetch(self, brief) -> Optional[LiveFacts]:
        return None


#: The registry. Ordered, first match wins within a domain - so a real source
#: registered at startup takes precedence over the placeholder without either
#: of them needing to know about the other.
_SOURCES: list = [
    _UnconfiguredSource(
        "sports",
        "a scores/fixtures API and its credential, plus a `fetch` that maps "
        "the brief's subject to a team or competition"),
    _UnconfiguredSource(
        "markets",
        "a quotes API and its credential, plus a `fetch` that maps the brief's "
        "subject to a ticker or index"),
]


def register(source: LiveSource) -> None:
    """Add a source. Registered first is tried first within its domain."""
    if source.domain not in LIVE_DOMAINS:
        raise ValueError(
            f"{source.name} declares domain {source.domain!r}, which is not one "
            f"of {', '.join(LIVE_DOMAINS)}. Refusing rather than registering it "
            "somewhere it will never be consulted.")
    _SOURCES.insert(0, source)
    log.info("live facts: registered %s for %s", source.name, source.domain)


def sources_for(domain: str) -> list:
    return [s for s in _SOURCES if s.domain == domain]


async def lookup(brief) -> Optional[LiveFacts]:
    """Ask the registered sources for this brief's domain, in order.

    Returns None when the brief names no live domain, when nothing is
    configured for it, or when every source had nothing. A source that *raises*
    is logged and skipped rather than allowed to end the episode - a live-fact
    provider is an enhancement to an episode that is already answerable, and
    must never be able to prevent one.
    """
    domain = getattr(brief, "live_domain", "") or ""
    if domain not in LIVE_DOMAINS:
        return None

    for source in sources_for(domain):
        ok, why = source.diagnose()
        if not ok:
            log.info("live facts: %s cannot serve - %s", source.name, why)
            continue
        try:
            facts = await source.fetch(brief)
        except Exception as exc:  # noqa: BLE001 - see docstring
            log.warning("live facts: %s failed for %r: %s",
                        source.name, getattr(brief, "query", ""), exc)
            continue
        if facts:
            log.info("live facts: %s answered %r with %d fact(s) as of %s",
                     source.name, getattr(brief, "query", ""),
                     len(facts.facts), facts.as_of.isoformat())
            return facts
    return None


def report() -> dict:
    """What live-fact coverage this server has - for /api/health."""
    per_domain: dict = {}
    for domain in LIVE_DOMAINS:
        entries = []
        for source in sources_for(domain):
            ok, why = source.diagnose()
            entries.append({"name": source.name, "ready": ok, "detail": why})
        per_domain[domain] = entries
    return {
        "domains": list(LIVE_DOMAINS),
        "sources": per_domain,
        "ready": [d for d, entries in per_domain.items()
                  if any(e["ready"] for e in entries)],
    }
