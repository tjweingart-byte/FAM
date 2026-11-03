"""Re-file the episodes written before Nando, once, after the owner's review
(decided item 20; nando.md always 10, §426).

An episode written before Nando has no node of its own: `app._episode_category`
places it from the writer's `<<CATEGORY:>>` words against the tree as it is
now, the nearest node by meaning when no phrase matches. This works out the
node Nando would file each one under, from what the row keeps (its question,
title and the writer's words), and lists every episode whose node would
change.

Two steps, never one:

1. `propose` + `write_report`: the list, as a JSON file the owner reads (and a
   Markdown copy beside it). Nothing is changed. The report's SHA-256 is
   printed: it names exactly this list.
2. `apply(cache, path, approve)`: only with that SHA-256, so the list applied
   is the list reviewed, byte for byte. Each change goes through
   `category_tag.retag` (§404) - the archive, the audio's metadata and the
   script row together - as a first filing. An episode whose row changed
   since the review (filed meanwhile, or its words now place it elsewhere) is
   skipped and said so, never re-filed on a stale reading.

Nando's placement here is what he does on a new episode (the owner's
answer, §426): his code steps (`categorizer.place`), his own small model
where its numbers trust it, **his own call** choosing from code's shortlist
where code filed nothing solid (`categorizer.ask_tie`, one small call per
such episode, through the agent layer - so only where he is switched on and
his rule book signed, and never on zero-spend staging), then his check
against the writer's words (`categorizer.confirm`). C3P0 is not asked: there
is no request left to ask about. `--no-model` makes the list in code alone,
for nothing.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import pathlib
from typing import Optional

log = logging.getLogger("fam.refile_old")

#: Why every change was made, on the retag's log line and the report.
REASON = "re-file of an episode written before Nando, reviewed by the owner (§426)"


def current_node(words: str) -> str:
    """Where the app files the episode today: `app._episode_category`'s
    reading of the writer's words for a row with no node."""
    if not words:
        return ""
    import stories

    return stories.resolve_category(words, near=True)


def nando_node(query: str, title: str, words: str, pick: str = "") -> tuple:
    """`(node, how)`: what Nando would file the episode under, given his own
    call's `pick` when it was asked. Never raises."""
    try:
        import episode_intelligence as ei
        from agents import categorizer

        brief = ei.Brief(query=query, subject=title or query)
        field = categorizer.provider_field(query)
        placed = categorizer.place(brief, query, field, categorizer.NO_PRIOR, pick)
        locked = (placed.how or "").split("~")[0] == "provider"
        node, how = categorizer.confirm(placed.node, words, field, locked)
        return node or "", how or ""
    except Exception as exc:  # noqa: BLE001 - one row never stops the list
        log.warning("could not place %r: %s", query[:80], exc)
        return "", "error"


async def _his_pick(query: str, title: str, gate, counts: dict) -> str:
    """Nando's own call (§379), as at the request: only when code's steps
    filed nothing solid, never over a name that means two things (§424), on
    code's shortlist. "" when not asked or not answered. Never raises."""
    try:
        import episode_intelligence as ei
        from agents import categorizer

        brief = ei.Brief(query=query, subject=title or query)
        field = categorizer.provider_field(query)
        placed = categorizer.place(brief, query, field, categorizer.NO_PRIOR, "")
        if placed.node or placed.ambiguous:
            return ""
        nodes, _tie = categorizer.candidates_for(query, "", None)
        if not nodes:
            return ""
        async with gate:
            counts["asked"] += 1
            pick = await categorizer.ask_tie(query, nodes, None, ahead=True)
        counts["answered" if pick else "no_answer"] += 1
        return pick
    except Exception as exc:  # noqa: BLE001
        log.warning("nando's call failed for %r: %s", query[:80], exc)
        counts["no_answer"] += 1
        return ""


def propose(cache, page: int = 500, model: bool = True, at_once: int = 4) -> dict:
    """Every episode with no node, and what would change. Changes nothing
    but, with `model`, spends one small call of Nando's per episode code
    could not file - all on one event loop (§388)."""
    import asyncio

    rows, after = [], ""
    while True:
        batch = cache.unfiled(after, page)
        if not batch:
            break
        rows.extend(batch)
        after = batch[-1]["key"]
    counts = {"asked": 0, "answered": 0, "no_answer": 0}
    picks = [""] * len(rows)
    if model and rows:
        async def ask_all():
            gate = asyncio.Semaphore(max(1, at_once))
            return await asyncio.gather(*(
                _his_pick(r.get("query", ""), r.get("title", ""), gate, counts)
                for r in rows))
        picks = asyncio.run(ask_all())
    changes, unchanged, unplaced = [], 0, 0
    import topics
    from agents import categorizer

    for row, pick in zip(rows, picks):
        words = row.get("category", "")
        now = current_node(words)
        node, how = nando_node(row.get("query", ""), row.get("title", ""), words, pick)
        if not node:
            unplaced += 1
            continue
        if node == now:
            unchanged += 1
            continue
        changes.append({
            "key": row["key"], "query": row.get("query", "")[:300],
            "title": row.get("title", "")[:120], "words": words[:120],
            "current": now, "current_path": " > ".join(categorizer.path_for(now)),
            "proposed": node, "proposed_path": " > ".join(categorizer.path_for(node)),
            "how": how, "his_call": pick,
            "tree_has_it": node in topics.FACETS
            or topics.category_tree().get(node) is not None,
        })
    return {"episodes": len(rows), "changes": changes, "unchanged": unchanged,
            "unplaced": unplaced, "model": bool(model), "calls": counts}


def write_report(proposal: dict, path) -> str:
    """The list as JSON at `path` (and Markdown beside it). Returns its SHA-256."""
    path = pathlib.Path(path)
    body = dict(proposal, made=datetime.datetime.now(datetime.timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"), reason=REASON)
    data = json.dumps(body, indent=1, sort_keys=True).encode("utf-8")
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    lines = [f"# Re-file of episodes written before Nando - for review ({body['made']})", "",
             f"{proposal['episodes']} episodes with no node; "
             f"**{len(proposal['changes'])} would change**, {proposal['unchanged']} "
             f"stay where they are, {proposal['unplaced']} Nando would not place.", "",
             f"Approve with: `python tools/refile_old.py --apply {path.name} "
             f"--approve {digest}`", "",
             "| Question | Title | Writer's words | Today | Nando | How |",
             "|---|---|---|---|---|---|"]
    for c in proposal["changes"]:
        cells = [c["query"], c["title"], c["words"], c["current_path"] or "(nowhere)",
                 c["proposed_path"], c["how"]]
        lines.append("| " + " | ".join(str(x).replace("|", "/") for x in cells) + " |")
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return digest


def apply(cache, path, approve: str, dry_run: bool = False) -> dict:
    """Apply the reviewed list - only when `approve` is its SHA-256."""
    data = pathlib.Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if not approve or approve.strip().lower() != digest:
        return {"applied": 0, "refused": "the approval does not name this list "
                f"(its SHA-256 is {digest}); nothing was changed"}
    report = json.loads(data)
    out = {"applied": 0, "skipped": [], "failed": [], "results": []}
    import category_tag

    for change in report.get("changes", []):
        key = change["key"]
        why = _stale(cache, change)
        if why:
            out["skipped"].append({"key": key, "why": why})
            continue
        result = category_tag.retag(cache, key, change["proposed"], reason=REASON,
                                    dry_run=dry_run, first_filing=True,
                                    was=change["current"])
        out["results"].append(result)
        if result.get("failed"):
            out["failed"].append({"key": key, "why": "; ".join(result["failed"])})
        elif not result.get("skipped"):
            out["applied"] += 1
    return out


def _stale(cache, change: dict) -> Optional[str]:
    """Why the row no longer matches what was reviewed, or None."""
    words, node = cache.category_pair(change["key"])
    if node:
        return f"filed meanwhile, under {node}"
    if not words and not change.get("words"):
        return None
    if current_node(words) != change["current"]:
        return "its words now place it elsewhere"
    return None
