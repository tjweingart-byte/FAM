"""How often FAM's categorisers agree about an episode (§209).

An episode is categorised up to three times, by three things that never
consult each other:

* the **keyword map** (`topics.tags_for_text`) reads the question's words;
* the **composer** (`stories.compose`) names a live story's category from
  headlines, before anything is researched (§188);
* the **writer** names it on its `<<CATEGORY:>>` line, after the research
  (§189).

Every written episode logs what each said - `note`, called beside the cache
write on all four write paths (a tap, prefetch, the DailyFAM edition, the
Trending edition) - into the `category_audit` table of the category tree's
own database. `summary` turns that into the numbers: how often the writer's
category could be placed at all, and how often the writer agrees with the
composer and with the keywords on the facet. `python tools/categories_report.py
--audit` prints them with the commonest disagreements.

A log and nothing else. Nothing ranks on it, it costs one local insert per
written episode, and a failure records nothing rather than costing the
episode.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger("fam.category_audit")


def composer_category(query: str, now: Optional[float] = None) -> str:
    """The composer's category for the live story whose question is `query`,
    from the pool or the Trending edition, or "" when no story asks it.
    Local reads only."""
    want = (query or "").strip()
    if not want:
        return ""
    try:
        import stories

        held = list(stories.pool().held(now))
    except Exception:  # noqa: BLE001 - a log line's input, never a failure
        held = []
    try:
        import trending_bank

        held += list(trending_bank.stories_now(now))
    except Exception:  # noqa: BLE001
        pass
    for story in held:
        if (getattr(story, "query", "") or "").strip() == want:
            return getattr(story, "category", "") or ""
    return ""


def note(query: str, words: str, origin: str = "",
         composer: Optional[str] = None, at: float = 0.0) -> dict:
    """Log what each categoriser said about one written episode, and return
    the row. `composer` is looked up when not given. Never raises."""
    row: dict = {}
    try:
        import stories
        import topics

        writer = stories.resolve_category(words) if words else ""
        if composer is None:
            composer = composer_category(query)
        keywords = topics.facets_only(topics.tags_for_text(query or ""))
        row = {
            "origin": origin or "",
            "query": query or "",
            "words": words or "",
            "writer": writer,
            "writer_facet": stories.facet_for(writer) if writer else "",
            "composer": composer or "",
            "composer_facet": (stories.facet_for(composer) if composer else ""),
            "keywords": " ".join(sorted(keywords)),
        }
        topics.category_tree().note_written(row, at=at)
    except Exception:  # noqa: BLE001 - a log, never the episode
        log.exception("could not log a categorisation")
    return row


def _rate(hits: int, of: int) -> Optional[float]:
    # None, not 0, when nothing was measured: "no data" and "never agrees"
    # are different facts (the rule prefetch's report keeps).
    return round(hits / of, 3) if of else None


def summary(days: float = 7.0, now: Optional[float] = None,
            examples: int = 10) -> dict:
    """The agreement numbers over the last `days`.

    * `placed` - the writer named a category and the tree could place it.
    * `writer_vs_composer` - same facet, over episodes both categorised.
    * `writer_vs_keywords` - the writer's facet is one the keyword map found,
      over episodes where both said something.
    * `disagreements` - the commonest (writer facet, other facet) pairs, and
      a few example questions for each kind.
    """
    import topics

    now = time.time() if now is None else now
    rows = topics.category_tree().audit_rows(since=now - days * 86400)
    worded = [r for r in rows if r["words"]]
    placed = [r for r in worded if r["writer"]]
    both = [r for r in placed if r["composer"]]
    with_keys = [r for r in placed if r["keywords"]]
    composer_agrees = [r for r in both
                       if r["writer_facet"] == r["composer_facet"]]
    keyword_agrees = [r for r in with_keys
                      if r["writer_facet"] in r["keywords"].split()]
    pairs: dict = {}
    for r in both:
        if r["writer_facet"] != r["composer_facet"]:
            pair = (r["composer_facet"] or "?", r["writer_facet"] or "?")
            pairs[pair] = pairs.get(pair, 0) + 1
    by_origin: dict = {}
    for r in rows:
        by_origin[r["origin"] or "?"] = by_origin.get(r["origin"] or "?", 0) + 1
    unplaced = [r for r in worded if not r["writer"]]
    return {
        "days": days,
        "episodes": len(rows),
        "by_origin": dict(sorted(by_origin.items())),
        "worded": len(worded),
        "placed": len(placed),
        "placed_rate": _rate(len(placed), len(worded)),
        "with_composer": len(both),
        "writer_vs_composer": _rate(len(composer_agrees), len(both)),
        "with_keywords": len(with_keys),
        "writer_vs_keywords": _rate(len(keyword_agrees), len(with_keys)),
        "composer_to_writer": [
            {"composer": a, "writer": b, "episodes": n}
            for (a, b), n in sorted(pairs.items(), key=lambda kv: -kv[1])],
        "examples": {
            "unplaced": [{"query": r["query"], "words": r["words"]}
                         for r in unplaced[:examples]],
            "composer_disagrees": [
                {"query": r["query"], "composer": r["composer"],
                 "writer": r["writer"]}
                for r in both if r["writer_facet"] != r["composer_facet"]
            ][:examples],
            "keywords_disagree": [
                {"query": r["query"], "keywords": r["keywords"],
                 "writer": r["writer"]}
                for r in with_keys
                if r["writer_facet"] not in r["keywords"].split()
            ][:examples],
        },
    }
