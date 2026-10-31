"""C3P0, the contextualizer - the `ResearchPlan`, built in code first (§355).

docs/agent_rules/c3p0.md: work out what a request is about, which plugins
to ask and what to ask them, what Sonic should fetch, and the title. Phase 1
starts where the guide's "First" line says to (docs/FAM_AGENTS.md, C3P0,
Plugin options): write the `ResearchPlan` and fill it from today's brief and
code routes with **no model**, so the plan C3P0's call will later improve is
one written decision per episode from the start, and so how often a search
came back empty while the archive held something is measured before the
archive is allowed to answer alone (responsibility 8).

`from_brief` reads the same predicates the pipeline already acts on - the
ladder always ([always-researched]), `live_route.domain_of` for a provider,
`live_facts.wants_forecast`, `history_archive.recall_wanted`, the brief's
town - so the plan describes what the episode will ask, never a second
opinion that could drift from it. It is pure: no file, store, socket or
model, microseconds after the brief. Nothing reads the plan yet; it is kept
on the episode's notes and counted.
"""
from __future__ import annotations

import collections
import logging
import threading
from typing import Optional

from agents.handoffs import PLUGINS, ArchiveRequest, PluginCall, ResearchPlan

log = logging.getLogger("fam.agents")

#: How plans have been built since boot, and what empty searches found in
#: the archive (`note_empty_search`). `/api/health`'s agents block reads it.
SINCE_BOOT: "collections.Counter[str]" = collections.Counter()
_LOCK = threading.Lock()


def _count(kind: str) -> None:
    with _LOCK:
        SINCE_BOOT[kind] += 1


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def from_brief(brief, query: str = "") -> Optional[ResearchPlan]:
    """The plan today's brief and code routes already make, as a
    `ResearchPlan`. None for no brief. Never raises."""
    if brief is None:
        return None
    try:
        import history_archive
        import live_facts
        import live_route
        from config import settings

        calls = [PluginCall(plugin="search", query=_text(
            getattr(brief, "retrieval", "") or query, 300))]
        place = _text(getattr(brief, "place", ""), 120)
        domain = live_route.domain_of(brief)
        entity = _text(live_route.entity_of(brief), 300)
        if domain == "weather":
            calls.append(PluginCall(plugin="weather", query=place or entity))
        elif domain:
            calls.append(PluginCall(plugin="live_facts", query=entity))
            if domain == "sports":
                calls.append(PluginCall(plugin="players", query=entity))
        if place and settings.local_news:
            calls.append(PluginCall(plugin="local_news", query=place))
        if live_facts.wants_forecast(brief):
            calls.append(PluginCall(plugin="forecast", query=_text(
                getattr(brief, "forecast_query", "") or entity, 300)))
        path = tuple(str(p) for p in (getattr(brief, "category_path", None) or ()))
        node = path[-1] if path else ""
        archive = None
        if not history_archive.recall_wanted(brief):
            calls.append(PluginCall(plugin="archive", query=_text(
                getattr(brief, "subject", "") or query, 300)))
            archive = ArchiveRequest(node_id=node, lookup="background",
                                     what=_text(getattr(brief, "subject", ""), 200))
        plan = ResearchPlan(
            entity=_text(getattr(brief, "live_entity", "") or getattr(brief, "subject", ""), 200),
            entity_kind=domain,
            plugins=tuple(calls),
            node_id=node,
            node_path=path,
            archive=archive,
            title=_text(getattr(brief, "title", ""), 80),
        )
        _count("from_brief")
        return plan
    except Exception as exc:  # noqa: BLE001 - a plan never costs an episode
        log.warning("the code research plan could not be built: %s", exc)
        _count("from_brief_failed")
        return None


def note_empty_search(look) -> None:
    """A search came back empty with the archive recall started beside it:
    count whether the archive held something - without waiting for it and
    without handing it to the writer (an empty search still gets no
    archive, §316, until `archive_alone` is built). Never raises."""
    try:
        _count("empty_search")
        if look is None:
            _count("empty_search_no_recall")
            return

        def done(fut) -> None:
            try:
                found = [] if fut.cancelled() else list(fut.result() or [])
            except Exception:  # noqa: BLE001
                found = []
            _count("empty_search_archive_had" if found else "empty_search_archive_nothing")

        look.add_done_callback(done)
    except Exception:  # noqa: BLE001
        log.warning("could not note an empty search", exc_info=True)


def report() -> dict:
    with _LOCK:
        counts = dict(SINCE_BOOT)
    empty = counts.get("empty_search", 0)
    had = counts.get("empty_search_archive_had", 0)
    return {"since_boot": counts,
            # None, not 0, before any empty search ([metering]'s rule).
            "empty_search_archive_had_rate": round(had / empty, 3) if empty else None}


def reset() -> None:
    with _LOCK:
        SINCE_BOOT.clear()
