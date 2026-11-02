"""One category tag, everywhere, for an episode's whole life (§404).

Decided item 5: Nando's node travels with the episode - its script row, its
audio's metadata in Cloudflare R2, every MongoDB record from it, and every
surface that reads `app._episode_category` - and **a re-file or a tree change
updates all of them together**. `retag` is that one function; nothing else
moves an episode's node once it is written.

What it moves, in order, for one episode (the cache key's current write):

1. the archive (`history_archive.tag_episode`): the episode's sources record
   takes the new node, and on each of its articles the old node is replaced
   by the new one;
2. the bucket: every audio object of the episode, every voice, gets the new
   node in its metadata by a copy in place (`set_metadata`), keeping its
   type and storage class;
3. the script row (`set_category_node`) - last, so a surface never shows a
   node the rest does not hold yet.

**There is no app cache to invalidate**: `_episode_category` reads the
script store on every call (§376), so the row is the invalidation.

A step that fails does not stop the others; it is recorded (`failed`, and
`pipeline_errors` where /admin shows it), and the nightly sync check (§405)
finds the drift; `push` - run by the owner, `tools/tag_sync.py --repair` -
repairs it from the script row. Never raises. An episode with no node written
(Nando never filed it) is left as it is: the writer's words still place it.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

log = logging.getLogger("fam.category_tag")

_LOCK = threading.Lock()
STATS: dict = {"retagged": 0, "unchanged": 0, "failed": 0, "objects": 0}


def reset() -> None:
    with _LOCK:
        STATS.update({"retagged": 0, "unchanged": 0, "failed": 0, "objects": 0})


def _count(name: str, n: int = 1) -> None:
    with _LOCK:
        STATS[name] = STATS.get(name, 0) + n


def metadata(node: str, episode: str) -> dict:
    """The R2 metadata an episode's audio carries."""
    out = {"episode": episode} if episode else {}
    if node:
        out["category-node"] = node
    return out


def retag(cache, key: str, node: str, *, reason: str = "",
          dry_run: bool = False) -> dict:
    """Move the episode under `key` to `node` everywhere. Returns what moved."""
    import cache as cache_mod

    out = {"key": key, "old": "", "new": node, "reason": reason, "script": False,
           "objects": 0, "archive": {}, "failed": [], "dry_run": dry_run}
    try:
        old = cache.category_node(key)
        out["old"] = old
        if not old:
            out["skipped"] = "Nando never filed it: the writer's words place it"
            return out
        if old == node:
            _count("unchanged")
            out["skipped"] = "already filed there"
            return out
        objects = list(getattr(cache, "audio_objects", lambda _k: [])(key))
        episode = cache_mod.episode_id(key, cache.sourced_at(key))
        if dry_run:
            out.update(objects=len(objects), would=True)
            return out
        # 1. The archive.
        try:
            import history_archive as ha

            store = ha.backend()
            if store is not None and episode:
                out["archive"] = store.tag_episode(episode, old, node)
        except Exception as exc:  # noqa: BLE001
            out["failed"].append(f"archive: {type(exc).__name__}: {exc}")
        # 2. The bucket.
        if objects:
            import audio_store

            bucket = audio_store.get_store()
            for name in objects:
                try:
                    if bucket is None:
                        raise audio_store.AudioStoreError("no bucket here")
                    bucket.set_metadata(name, metadata(node, episode))
                    out["objects"] += 1
                except Exception as exc:  # noqa: BLE001
                    out["failed"].append(f"r2 {name}: {exc}")
        # 3. The script row, last.
        out["script"] = bool(cache.set_category_node(key, node))
        if not out["script"]:
            out["failed"].append("script row: not updated")
        _count("retagged")
        _count("objects", out["objects"])
        if out["failed"]:
            _count("failed")
            try:
                import pipeline_errors

                # "episode", a notice: nobody heard less (§405).
                pipeline_errors.record("episode", "re-file incomplete",
                                       f"{key[:60]}: {'; '.join(out['failed'])[:240]}"
                                       " - the nightly sync check finds it",
                                       severity=pipeline_errors.NOTICE)
            except Exception:  # noqa: BLE001
                pass
        log.info("re-filed %r from %r to %r (%s): %s", key[:60], old, node, reason,
                 {k: out[k] for k in ("script", "objects", "archive", "failed")})
        return out
    except Exception as exc:  # noqa: BLE001 - a re-file never raises
        _count("failed")
        out["failed"].append(f"{type(exc).__name__}: {exc}")
        log.exception("could not re-file %r", key[:60])
        return out


def push(cache, key: str, dry_run: bool = False) -> dict:
    """Make the bucket and the archive agree with the script row, which is
    the record (§405): the repair for drift `tag_sync` found. Never raises."""
    import cache as cache_mod

    out = {"key": key, "node": "", "objects": 0, "archive": {}, "failed": []}
    try:
        node = cache.category_node(key)
        out["node"] = node
        if not node:
            return out
        episode = cache_mod.episode_id(key, cache.sourced_at(key))
        if dry_run:
            return {**out, "would": True}
        try:
            import history_archive as ha

            store = ha.backend()
            if store is not None and episode:
                held = store.episode_node(episode)
                if held is not None and held != node:
                    out["archive"] = store.tag_episode(episode, held, node)
        except Exception as exc:  # noqa: BLE001
            out["failed"].append(f"archive: {type(exc).__name__}: {exc}")
        objects = list(getattr(cache, "audio_objects", lambda _k: [])(key))
        if objects:
            import audio_store

            bucket = audio_store.get_store()
            for name in objects:
                try:
                    if bucket is None:
                        raise audio_store.AudioStoreError("no bucket here")
                    bucket.set_metadata(name, metadata(node, episode))
                    out["objects"] += 1
                except Exception as exc:  # noqa: BLE001
                    out["failed"].append(f"r2 {name}: {exc}")
        return out
    except Exception as exc:  # noqa: BLE001
        out["failed"].append(f"{type(exc).__name__}: {exc}")
        return out


def report() -> dict:
    with _LOCK:
        return dict(STATS)
