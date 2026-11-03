"""Consuela's longer jobs: re-filing, the 90-day pass, the full comb (§402).

Phase 2, piece 5 (consuela.md, always 4, 5, 10, 11; decided items 3, 5 and
18). Each runs as one of Netenyahu's tasks, only while Consuela may run, and
with `AGENTS=0` nothing here is called. No model is asked by anything in
this module but the comb, which uses the dedupe's own two (§401).

**Re-filing when the tree changes** (always 10; `refile`). Nando's tree
follower queues `housekeeper:refile:<node>` for every node minted, moved or
pruned (§387). The task carries only the node; the change is read from the
tree as it is now:

* a node still in the tree (minted, or moved under a new parent): every
  fact filed under it or beneath it gets its path recomputed;
* a node gone from the tree (pruned or merged away): every fact filed under
  it moves to its nearest ancestor still in the tree - read from the fact's
  own stored path - or to no node when none survives; intake records and
  readings that name it follow. MongoDB never points at a dead node.

Each re-file is recorded with where it came from (`refiles`), so it can be
undone. The full comb repeats the same check on every fact it reads, so a
re-file a dry run only reported is made later, not lost.

**The 90-day pass** (always 4; `second`). A plain sentence kept more than 90
days ago is folded into its article's **link**: one `link` fact per article
(its title as the publisher wrote it, its link, date, grade and node; no
vector, so the recall never offers a headline as evidence), and the
sentence is marked superseded by it - reversible, deleted for good 30 days
later. Figures, quotes and live records are never touched: "important
figures, quotes, trends and article links" are what stays, and a number is
never dropped on anyone's judgment, a model's least of all.

**The full comb** (always 5, decided item 18; `comb`). Every
`HOUSEKEEPER_COMB_DAYS` (7) the whole archive is combed for duplicates, a
page of `COMB_PAGE` facts a night until it is through - the dedupe's own
code, models, dry run and eval lock (§401), on every fact rather than the
last two days'.

**Dry runs**: re-filing and the 90-day pass follow `HOUSEKEEPER_DRY_RUN`
(what she would change is reported, nothing is changed); the comb follows
the dedupe's own `HOUSEKEEPER_DEDUPE_DRY_RUN` and eval.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from config import settings

log = logging.getLogger("fam.agents.housekeeping")

#: The owner's number (decided item 3): a plain sentence is folded into its
#: article's link this long after it was kept.
SECOND_PASS_DAYS = 90
#: The script cache, handed in at startup (`housekeeper.register`), so a
#: re-file moves the episodes as well as the facts (§404).
SCRIPT_CACHE: list = [None]

#: Sentences folded per run, facts re-filed per node, facts combed per night.
SECOND_PASS_LIMIT = 500
REFILE_LIMIT = 5000
COMB_PAGE = 1000


def _tree():
    import topics

    return topics.category_tree()


def _mode() -> str:
    return "dry_run" if settings.housekeeper_dry_run else "live"


# ------------------------------------------------------------------ re-filing
def surviving(path: list, tree) -> str:
    """The nearest node of `path` (root first) still in the tree, or ""."""
    for node in reversed(list(path or [])):
        if node and tree.get(node) is not None:
            return node
    return ""


def where_now(fact: dict, tree) -> tuple:
    """(node, path) a fact should be filed under now. Its own node while it
    is in the tree, with the tree's path; otherwise its nearest surviving
    ancestor from the path it was filed with."""
    import history_archive as ha

    node = fact.get("node_id", "")
    if node and tree.get(node) is None:
        node = surviving(fact.get("node_path") or [], tree)
    return node, (ha.node_path(node) if node else [])


def refile_facts(archive, facts: list, mode: str, now: float) -> dict:
    """Re-file every fact in `facts` whose node or path is out of date."""
    from agents import housekeeper

    tree = _tree()
    out = {"looked": 0, "refiled": 0, "unfiled": 0, "moved_nodes": {}}
    for fact in facts:
        out["looked"] += 1
        if not fact.get("node_id"):
            continue
        node, path = where_now(fact, tree)
        if node == fact.get("node_id") and path == list(fact.get("node_path") or []):
            continue
        out["refiled"] += 1
        if not node:
            out["unfiled"] += 1
        if node != fact.get("node_id"):
            out["moved_nodes"][fact["node_id"]] = node
        housekeeper.store().note_refile(fact["id"], mode, fact.get("node_id", ""), node,
                                        list(fact.get("node_path") or []), path, now)
        if mode == "live":
            archive.set_node(fact["id"], node, path)
    return out


def refile(node: str, now: Optional[float] = None) -> dict:
    """The `housekeeper:refile:<node>` task's work. Blocking; never raises."""
    import history_archive as ha
    from agents import housekeeper

    now = now or time.time()
    mode = _mode()
    try:
        if not ha.consuela_runs():
            return {"why": "Consuela may not run"}
        archive = ha.backend()
        if archive is None:
            return {"why": "no archive"}
        facts = [f for f in archive.facts_under(node, REFILE_LIMIT)
                 if f.get("node_id") == node or node in (f.get("node_path") or [])]
        out = refile_facts(archive, facts, mode, now)
        if mode == "live":
            for old, new in out["moved_nodes"].items():
                archive.replace_node(old, new)
        episodes = 0
        if _tree().get(node) is None:
            new = out["moved_nodes"].get(node, "")
            if mode == "live":
                # Intake and readings that named a node no fact named.
                archive.replace_node(node, new)
            # The episodes filed under it move with it, everywhere at once
            # (§404): script row, bucket, archive. To "" when no fact told
            # where - the writer's words then place them, as for any episode
            # whose node has gone (§371).
            cache = SCRIPT_CACHE[0]
            if cache is not None:
                import category_tag

                for key in cache.keys_with_node(node):
                    category_tag.retag(cache, key, new, reason=f"tree: {node!r} gone",
                                       dry_run=mode != "live")
                    episodes += 1
        summary = {"node": node, "mode": mode,
                   **{k: v for k, v in out.items() if k != "moved_nodes"},
                   "moved": out["moved_nodes"], "episodes": episodes}
        housekeeper.store().note_job("refile", mode, summary, now)
        return summary
    except Exception as exc:  # noqa: BLE001
        log.exception("consuela could not re-file %r", node)
        return {"why": f"{type(exc).__name__}: {exc}"}


async def refile_handler(task: str) -> None:
    node = task.split(":", 2)[2] if task.count(":") >= 2 else ""
    summary = await asyncio.to_thread(refile, node)
    log.info("consuela re-filed %r: %s", node, summary)
    # A re-file that did not happen is a failed task, so the queue shows it
    # and tries again (§417) - never a quiet "done".
    if summary.get("why") and summary["why"] not in ("Consuela may not run", "no archive"):
        raise RuntimeError(f"re-file of {node!r} failed: {summary['why']}")


# ------------------------------------------------------------------ the 90-day pass
def link_fact(sentence: dict):
    """The one `link` fact for a sentence's article: the publisher's title,
    the link, date, grade and node - no text of Consuela's own."""
    import history_archive as ha

    return ha.Fact(url=sentence["url"], text=sentence.get("title") or sentence["url"],
                   title=sentence.get("title", ""), host=sentence.get("host", ""),
                   grade=sentence.get("grade", ""), published=sentence.get("published", ""),
                   kind="link", subject=sentence.get("subject", ""),
                   category=sentence.get("category", ""),
                   id=ha.url_id(sentence["url"] + "\nlink"),
                   node_id=sentence.get("node_id", ""),
                   node_path=list(sentence.get("node_path") or []))


def second_pass(now: Optional[float] = None) -> dict:
    """Fold plain sentences older than 90 days into their articles' links.
    Blocking; never raises."""
    import history_archive as ha
    from agents import housekeeper

    now = now or time.time()
    mode = _mode()
    summary = {"mode": mode, "sentences": 0, "articles": 0, "folded": 0, "purged": 0}
    try:
        if not ha.consuela_runs():
            return {**summary, "why": "Consuela may not run"}
        archive = ha.backend()
        if archive is None:
            return {**summary, "why": "no archive"}
        aging = archive.aging_sentences(now - SECOND_PASS_DAYS * 86400.0,
                                        SECOND_PASS_LIMIT)
        summary["sentences"] = len(aging)
        summary["articles"] = len({s["url"] for s in aging})
        if mode == "live":
            links: dict = {}
            for sentence in aging:
                link = links.get(sentence["url"])
                if link is None:
                    link = link_fact(sentence)
                    archive.add_fact(link, None, "", now)
                    links[sentence["url"]] = link
                if archive.supersede(sentence["id"], link.id,
                                     "the 90-day consolidation: folded into its link", now):
                    summary["folded"] += 1
            before = now - float(settings.housekeeper_superseded_days) * 86400.0
            summary["purged"] = archive.purge_superseded(before)
        housekeeper.store().note_job("second", mode, summary, now)
        return summary
    except Exception as exc:  # noqa: BLE001
        log.exception("consuela's 90-day pass failed")
        return {**summary, "why": f"{type(exc).__name__}: {exc}"}


async def second_handler(_task: str) -> None:
    summary = await asyncio.to_thread(second_pass)
    log.info("consuela's 90-day pass: %s", summary)


# ------------------------------------------------------------------ the full comb
def comb_due(now: float) -> bool:
    """Whether tonight continues or starts a comb: one is under way (its
    cursor is set), or the last finished `HOUSEKEEPER_COMB_DAYS` ago."""
    from agents import housekeeper

    store = housekeeper.store()
    if store.get_state("comb_cursor"):
        return True
    finished = float(store.get_state("comb_finished_at", "0") or 0)
    return now - finished >= float(settings.housekeeper_comb_days) * 86400.0


async def comb(now: Optional[float] = None) -> dict:
    """One night of the full comb: the next page of facts, deduplicated and
    checked for out-of-date filing. Never raises."""
    import history_archive as ha
    from agents import dedupe, housekeeper

    now = now or time.time()
    summary = {"due": False}
    try:
        if not ha.consuela_runs():
            return {**summary, "why": "Consuela may not run"}
        archive = ha.backend()
        if archive is None:
            return {**summary, "why": "no archive"}
        if not await asyncio.to_thread(comb_due, now):
            return summary
        store = housekeeper.store()
        cursor = await asyncio.to_thread(store.get_state, "comb_cursor", "")
        page = await asyncio.to_thread(archive.facts_page, cursor, COMB_PAGE)
        refiled = await asyncio.to_thread(refile_facts, archive, page, _mode(), now)
        result = await dedupe.run(now=now, facts=page)
        done = len(page) < COMB_PAGE
        if done:
            await asyncio.to_thread(store.set_state, "comb_cursor", "")
            await asyncio.to_thread(store.set_state, "comb_finished_at", str(now))
        else:
            await asyncio.to_thread(store.set_state, "comb_cursor", page[-1]["id"])
        summary = {"due": True, "facts": len(page), "finished": done,
                   "refiled": refiled["refiled"], "dedupe": result}
        await asyncio.to_thread(store.note_job, "comb", result.get("mode", ""), summary, now)
        return summary
    except Exception as exc:  # noqa: BLE001
        log.exception("consuela's comb failed")
        return {**summary, "why": f"{type(exc).__name__}: {exc}"}


async def comb_handler(_task: str) -> None:
    summary = await comb()
    log.info("consuela's comb: %s", summary)
