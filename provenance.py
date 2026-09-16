"""Where an episode's facts came from, kept so a listener can see them.

FAM already knew all of this and threw it away. `research.domains()` extracted
every publisher, `Packet.sources` carried them, `script_generator` stored them
on `notes.research` - and nothing ever read them back. `ScriptNotes` said so in
its own docstring: *"Written to and never read back by the writing path."*

So this module is mostly plumbing for data that already existed. What it adds
is a shape the interface can render and the cache can keep.

The distinction that makes this safe
------------------------------------
CLAUDE.md is emphatic that **the evidence packet carries the grade and never
the hostname** - "a domain in the packet is a domain the voice can read out",
and the model needs to know it is reading a wire service in order to weigh it,
not a way to say "reuters dot com" aloud.

**That rule is about the prompt. This is a different channel.** What the app
*displays* and what the writer *reads* are not the same thing, and `domains()`
has always existed "for a person to judge". Nothing here goes into a prompt,
and nothing here changes what is spoken.

Write that down wherever this is touched, because the obvious next step - "we
show sources now, so let the model cite them" - would put hostnames back in
front of the voice, which is the drift CLAUDE.md warns about by name.

What is private, and never shared
---------------------------------
An attachment's title is the listener's own document. It is marked `private`
and is **never written to the shared cache**, because the script cache is
shared and Explore replays from it - so a cached attachment title would show
one listener the name of another listener's file. An attached episode is
already uncacheable (`pipeline.key_for` returns "" for it), which makes this
belt and braces rather than the only guard.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

#: What kind of thing contributed. Rendered differently, and weighted
#: differently by the listener: "a wire service said this an hour ago" and
#: "your own PDF said this" are not the same claim.
ARTICLE = "article"
LIVE = "live"
ATTACHMENT = "attachment"
KINDS = (ARTICLE, LIVE, ATTACHMENT)


@dataclass
class Attribution:
    """One source that contributed to one episode."""

    #: What to show. A cleaned hostname for an article, a provider name for a
    #: live fact, a filename for an attachment. Never a URL.
    label: str
    kind: str = ARTICLE
    #: `research.TIER_LABELS`' plain-English grade, where there is one.
    tier: str = ""
    #: ISO date for an article, ISO timestamp for a live fact. "" when the
    #: source did not state one - which is shown as "date not stated" rather
    #: than filled in, for the same reason `research.published_at` returns
    #: None instead of today.
    at: str = ""
    #: An article headline where retrieval gave one. Shown under the label.
    title: str = ""
    #: True for anything belonging to this listener alone. Never cached.
    private: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Provenance:
    """Everything one episode drew on."""

    items: list = field(default_factory=list)
    #: Which retrieval backends actually contributed, by name. Carried
    #: separately from the publishers because "two indexes agreed" is a
    #: different claim from "two newspapers agreed" - see PROBLEMS.md §91.
    retrievers: list = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.items)

    @property
    def shareable(self):
        """The half that may be written to the shared cache."""
        return Provenance([i for i in self.items if not i.private],
                          list(self.retrievers))

    def add(self, item: Attribution) -> None:
        # Deduplicated on what the listener sees, so one outlet that produced
        # three of the packet's passages is one line rather than three.
        if not any(i.label == item.label and i.kind == item.kind
                   for i in self.items):
            self.items.append(item)

    def as_dict(self) -> dict:
        return {"items": [i.as_dict() for i in self.items],
                "retrievers": list(self.retrievers),
                "count": len(self.items)}

    def to_json(self) -> str:
        try:
            return json.dumps(self.shareable.as_dict())
        except (TypeError, ValueError):
            log.warning("could not serialise provenance; storing none")
            return ""

    @classmethod
    def from_json(cls, raw: str):
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return cls()
        items = [Attribution(**{k: v for k, v in row.items()
                                if k in Attribution.__dataclass_fields__})
                 for row in data.get("items", []) if isinstance(row, dict)]
        return cls(items=items, retrievers=list(data.get("retrievers") or []))


def clean_host(url_or_host: str) -> str:
    """A hostname a person would recognise, from a URL or a host.

    Deliberately not a publisher-name lookup table. A map of hosts to display
    names ("reuters.com" -> "Reuters") is a map somebody has to maintain, and a
    stale one shows the wrong masthead - which on a provenance panel is worse
    than showing the domain. The domain is always true.
    """
    text = (url_or_host or "").strip()
    if "//" in text:
        text = text.split("//", 1)[1]
    text = text.split("/", 1)[0].split("?", 1)[0]
    if text.startswith("www."):
        text = text[4:]
    return text.lower()


def from_results(results, retriever: str = "") -> Provenance:
    """Build provenance from what a retrieval backend returned.

    Imported lazily inside the function so this module stays importable
    without `research`'s optional dependencies.
    """
    import research

    out = Provenance(retrievers=[retriever] if retriever else [])
    for result in results or []:
        host = clean_host(getattr(result, "url", "") or "")
        if not host:
            continue
        when = research.published_at(result)
        out.add(Attribution(
            label=host,
            kind=ARTICLE,
            tier=research.TIER_LABELS.get(research.credibility(result), ""),
            at=when.date().isoformat() if when else "",
            title=str(getattr(result, "title", "") or "")[:200],
        ))
    return out


def from_live(lookup) -> Optional[Attribution]:
    """The live provider, when one actually answered.

    Only on `facts`. A provider that failed, timed out or returned something
    too stale to use did not contribute to the episode, and listing it would
    be claiming corroboration that did not happen.
    """
    import live_facts

    if lookup is None or getattr(lookup, "outcome", "") != live_facts.FACTS:
        return None
    facts = getattr(lookup, "facts", None)
    if facts is None:
        return None
    at = ""
    try:
        at = facts.as_of.isoformat()
    except (AttributeError, ValueError):
        pass
    tier = "live feed"
    if getattr(facts, "delayed_seconds", 0):
        minutes = max(1, int(round(facts.delayed_seconds / 60)))
        tier = f"live feed, delayed about {minutes} minute(s)"
    return Attribution(label=facts.source, kind=LIVE, tier=tier, at=at)


def from_attachments(attachments) -> list:
    """The listener's own documents. Private, and never cached."""
    out = []
    for attachment in attachments or []:
        label = (getattr(attachment, "title", "")
                 or getattr(attachment, "name", "")
                 or getattr(attachment, "filename", ""))
        if not label:
            continue
        out.append(Attribution(label=str(label)[:200],
                               kind=ATTACHMENT,
                               tier="attached by you",
                               private=True))
    return out
