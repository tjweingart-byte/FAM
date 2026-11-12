"""The nightly check that an episode's tag agrees everywhere (§405).

Decided item 5: "a reconciliation job that checks every episode id and node
id agree between MongoDB, the script cache and R2's objects; Keith reports
drift". The script row is the record (§371, §404); for every episode Nando
filed, this reads the row, then each of its audio objects' metadata
(`head`, a read) and the archive's record of it, and names every way they
disagree:

* `dead node` - the row names a node the tree no longer holds;
* `object missing` - a row names an audio object the bucket does not have;
* `r2 untagged` - an object without the node (uploaded before §404);
* `r2 node drift` / `r2 episode drift` - an object naming another node or
  another episode;
* `archive untagged` - the archive's record of the episode has no node
  (written before §404);
* `archive node drift` - it names another node.

An episode with no archive record (one written from live data alone, or
before the archive) is not drift.

**It reports; it repairs nothing on its own.** Keith's fixes are
observe-only until the owner switches each on (docs/AGENTS_BUILD_PROMPT.md,
Phase 4), so drift goes to `/admin` (`pipeline_errors`, the "episode" stage,
a notice) and `/api/health` → `archive` → `tag_sync`, and the owner repairs
it with `python tools/tag_sync.py --repair`, which pushes the row's node to
the bucket and the archive (`category_tag.push`).

Runs as Keith's nightly task (`it:tag_sync`, only while `IT_AGENT` is on and
his rule book is signed), a page of episodes a night by a cursor; and at any
time from the tool. Never raises; staging has no bucket and checks the rest.
"""
from __future__ import annotations

import categories
import logging
import threading
import time
from typing import Optional

log = logging.getLogger("fam.tag_sync")

#: Episodes checked per night, at most.
PAGE = 2000

KINDS = ("dead node", "object missing", "r2 untagged", "r2 node drift",
         "r2 episode drift", "archive untagged", "archive node drift")

_LOCK = threading.Lock()
LAST: dict = {}
_CURSOR: list = [""]
#: The script cache, handed in at startup (`register`).
SCRIPT_CACHE: list = [None]


def check_episode(key: str, node: str, sourced, objects: list, *, bucket, archive,
                  tree) -> list:
    """[(kind, detail)] for one episode: every way its tag disagrees."""
    import cache as cache_mod
    from urllib.parse import unquote

    out = []
    episode = cache_mod.episode_id(key, sourced)
    # A facet is a root, never a row: Nando files there, and it is never
    # dead (§552, §555).
    if tree is not None and not categories.holds(tree, node):
        out.append(("dead node", node))
    if bucket is not None:
        for name in objects:
            try:
                meta = bucket.head(name)
            except Exception as exc:  # noqa: BLE001 - one object, one finding
                out.append(("object missing", f"{name}: {exc}"))
                continue
            if meta is None:
                out.append(("object missing", name))
                continue
            held = unquote(str(meta.get("category-node", "") or ""))
            if not held:
                out.append(("r2 untagged", name))
            elif held != node:
                out.append(("r2 node drift", f"{name}: {held!r} not {node!r}"))
            said = unquote(str(meta.get("episode", "") or ""))
            if said and said != episode:
                out.append(("r2 episode drift", f"{name}: {said!r} not {episode!r}"))
    if archive is not None and episode:
        held = archive.episode_node(episode)
        if held is not None:
            if not held:
                out.append(("archive untagged", episode))
            elif held != node:
                out.append(("archive node drift", f"{held!r} not {node!r}"))
    return out


def run(cache=None, *, page: int = PAGE, after: Optional[str] = None,
        record: bool = True) -> dict:
    """Check the next page of filed episodes. Returns the counts, by kind,
    and every finding. Blocking; never raises."""
    import audio_store
    import history_archive as ha

    cache = cache or SCRIPT_CACHE[0]
    summary = {"checked": 0, "drift": {k: 0 for k in KINDS}, "findings": [],
               "finished": False, "at": time.time()}
    try:
        if cache is None or not hasattr(cache, "tagged"):
            return {**summary, "why": "no script cache"}
        try:
            import topics

            tree = topics.category_tree()
        except Exception:  # noqa: BLE001
            tree = None
        bucket = audio_store.get_store()
        archive = ha.backend()
        start = _CURSOR[0] if after is None else after
        rows = cache.tagged(start, page)
        for key, node, sourced, objects in rows:
            summary["checked"] += 1
            for kind, detail in check_episode(key, node, sourced, objects,
                                              bucket=bucket, archive=archive, tree=tree):
                summary["drift"][kind] += 1
                summary["findings"].append({"key": key, "kind": kind, "detail": detail})
        summary["finished"] = len(rows) < page
        if after is None:
            _CURSOR[0] = "" if summary["finished"] else (rows[-1][0] if rows else "")
        if record:
            _report(summary)
        return summary
    except Exception as exc:  # noqa: BLE001
        log.exception("the tag sync check failed")
        return {**summary, "why": f"{type(exc).__name__}: {exc}"}


def _report(summary: dict) -> None:
    with _LOCK:
        LAST.clear()
        LAST.update({k: v for k, v in summary.items() if k != "findings"})
    total = sum(summary["drift"].values())
    if not total:
        return
    try:
        import pipeline_errors

        counts = ", ".join(f"{n} {k}" for k, n in summary["drift"].items() if n)
        pipeline_errors.record(
            "episode", "tag drift",
            f"{counts} in {summary['checked']} episodes - "
            "python tools/tag_sync.py to list, --repair to fix",
            severity=pipeline_errors.NOTICE)
    except Exception:  # noqa: BLE001
        pass


def repair(cache, findings: list, dry_run: bool = False) -> list:
    """Push the row's node to the bucket and the archive for each episode a
    finding names (`category_tag.push`). A dead node is not repaired here:
    which node replaces it is a re-file, Consuela's (§402)."""
    import category_tag

    keys = sorted({f["key"] for f in findings if f["kind"] != "dead node"})
    return [category_tag.push(cache, key, dry_run=dry_run) for key in keys]


async def handler(_task: str) -> None:
    """Keith's nightly task `it:tag_sync`: observe and report only."""
    import asyncio

    from agents import roster

    if not roster.status("it")["may_run"]:
        log.info("tag sync not run: %s", roster.status("it")["why_not"])
        return
    summary = await asyncio.to_thread(run)
    log.info("tag sync: %s episodes, drift %s", summary["checked"],
             {k: n for k, n in summary["drift"].items() if n})


def register(script_cache) -> None:
    """At startup, with `AGENTS=1`: the cache, and Keith's task."""
    from agents import netenyahu

    SCRIPT_CACHE[0] = script_cache
    netenyahu.HANDLERS[("it", "tag_sync")] = handler


def report() -> dict:
    with _LOCK:
        return dict(LAST)
