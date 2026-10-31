"""Training Nando: the answer key and what it teaches (§377, piece N3).

The owner wants Nando to decide (§375) and to keep getting better as the
tree grows, without anyone hand-scoring episodes (decided item 30). The
answer key is how: a stronger model - `CATEGORIZER_JUDGE_MODEL`, Opus 5.5
(the owner's choice) - reads finished episodes in the background and says
which node each is truly about. Its labels are

* the **measurement**: how often Nando's filing agrees with the key, by
  facet, and the commonest confusions (`report`, printed by
  `tools/categories_report.py --audit`);
* the **material** the later pieces learn from: Nando's corrected mistakes
  become worked examples (N5), labelled questions fill his memory (N4) and
  train his small model (N6).

Kept in the agents' own store (`AGENTS_DB`), in tables of their own: the
episode's cache key, its question, the key's node, how sure it was and why
- never a listener id, never a private search's or an attached file's
episode (those are never cached and never audited). A wipe forgets them,
as everything learnt from the log.

The judge runs as one of Nando's background tasks (`categorizer:judge`),
scheduled by Netenyahu (`netenyahu.SCHEDULE`), a batch of at most
`NANDO_JUDGE_PER_RUN` episodes a run, each one request through
`agents.models.ask` under Nando's signed rule book. Nothing here runs on a
listener's path.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

import db as store_db
from paths import data_path

log = logging.getLogger("fam.agents")

#: How much of a finished script the judge reads: enough to know what the
#: episode is about, bounded so a call has a size.
SCRIPT_SENTENCES = 40


class JudgeLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: A node id from the tree as listed, or "none".
    node: str = Field(max_length=120)
    confidence: Literal["high", "medium", "low"]
    reason: str = Field(max_length=240)


JUDGE_INSTRUCTIONS = """You are the answer key for FAM's categorizer. You \
are given a listener's question and the finished episode written for it, \
and FAM's whole category tree. Say which single node the episode is truly \
about - the most specific node that is true, using its id exactly as \
listed - or "none" when no node fits. Judge by what the episode is about, \
not by a word it contains: "Detroit lions at the zoo" is about animals, not \
the football team. A broader true node is better than a specific wrong \
one. Say how sure you are (high, medium, low) and give a one-sentence \
reason. Your answer grades another model, so be strict and honest; "low" \
is a correct answer when the episode could belong to more than one node."""


class TrainingStore:
    """Nando's training material, in the agents' store."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("AGENTS_DB", "agents.db", path)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS nando_labels (
                key TEXT PRIMARY KEY, query TEXT NOT NULL, node TEXT NOT NULL,
                confidence TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                judge_model TEXT NOT NULL DEFAULT '', at REAL NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS nando_labels_at ON nando_labels(at)")

    def _connect(self) -> sqlite3.Connection:
        conn = store_db.connect(self.path, timeout=10, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def put_label(self, key: str, query: str, node: str, confidence: str, reason: str,
                  judge_model: str, now: float = 0.0) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO nando_labels (key, query, node, confidence, reason,"
                       " judge_model, at) VALUES (?, ?, ?, ?, ?, ?, ?)"
                       " ON CONFLICT(key) DO UPDATE SET query = excluded.query,"
                       " node = excluded.node, confidence = excluded.confidence,"
                       " reason = excluded.reason, judge_model = excluded.judge_model,"
                       " at = excluded.at",
                       (key, query[:500], node[:120], confidence[:8], reason[:240],
                        judge_model[:80], now or time.time()))

    def labelled(self, keys) -> set:
        keys, found = list(keys), set()
        with self._connect() as db:
            for i in range(0, len(keys), 500):     # under any bound-variable limit
                chunk = keys[i:i + 500]
                found.update(r[0] for r in db.execute(
                    f"SELECT key FROM nando_labels WHERE key IN"
                    f" ({', '.join('?' for _ in chunk)})", chunk).fetchall())
        return found

    def labels(self, since: float = 0.0, limit: int = 20000) -> list:
        with self._connect() as db:
            rows = db.execute("SELECT key, query, node, confidence, reason, judge_model, at"
                              " FROM nando_labels WHERE at >= ? ORDER BY at DESC LIMIT ?",
                              (since, int(limit))).fetchall()
        names = ("key", "query", "node", "confidence", "reason", "judge_model", "at")
        return [dict(zip(names, r)) for r in rows]

    def forget(self) -> int:
        """A wipe: every label was learnt from episodes the wipe empties."""
        with self._lock, self._connect() as db:
            n = db.execute("SELECT COUNT(*) FROM nando_labels").fetchone()[0]
            db.execute("DELETE FROM nando_labels")
        return int(n)


_STORE: list = [None]


def store() -> TrainingStore:
    if _STORE[0] is None:
        _STORE[0] = TrainingStore()
    return _STORE[0]


def reset(training_store: Optional[TrainingStore] = None) -> None:
    _STORE[0] = training_store


def exists() -> bool:
    return _STORE[0] is not None or store_db.has_tables(data_path("AGENTS_DB", "agents.db"))


# ------------------------------------------------------------------ the judge
def tree_listing() -> str:
    """The whole tree as the judge reads it: id, path, and the drafted
    definition where there is one."""
    import category_vocabulary
    import topics

    from agents import categorizer

    tree = topics.category_tree()
    lines = []
    for node in sorted(set(tree.nodes()) | set(topics.FACETS)):
        about = category_vocabulary.definition(node)
        extra = f" - {about['definition']}" if about.get("definition") else ""
        lines.append(f"{node} | {' > '.join(categorizer.path_for(node))}{extra}")
    return "\n".join(lines)


async def judge(key: str, query: str, sentences: list, listing: str) -> Optional[dict]:
    """One episode through the answer key, or None when it did not answer
    usably. Never raises."""
    try:
        import topics
        from agents import models, netenyahu
        from config import settings

        script = " ".join(str(s) for s in (sentences or ())[:SCRIPT_SENTENCES])
        request = models.AgentRequest(
            agent="categorizer", task="judge", switch_setting="categorizer",
            model_setting="categorizer_judge_model", rule_book="nando.md",
            schema=JudgeLabel,
            instructions=JUDGE_INSTRUCTIONS + "\n\nFAM's category tree (id | path):\n" + listing,
            material=(("request", query), ("script", script)),
            timeout=float(settings.categorizer_judge_timeout_seconds),
            max_tokens=int(settings.categorizer_judge_max_tokens), effort="medium",
            surface="background")
        result = await models.ask(request)
        netenyahu.report("categorizer", result.status)
        if not result.ok:
            return None
        node = result.value.node.strip()
        if node != "none" and node not in topics.FACETS \
                and topics.category_tree().get(node) is None:
            return None                       # never a node the tree lacks
        return {"node": "" if node == "none" else node,
                "confidence": result.value.confidence,
                "reason": result.value.reason.strip(),
                "judge_model": result.model}
    except Exception as exc:  # noqa: BLE001 - a background job never raises
        log.warning("nando's answer key failed for %r: %s", query, exc)
        return None


def _newest_per_key(rows) -> list:
    """One audit row per episode - the newest (rows come newest first): a
    key written twice is one episode to grade, under its latest filing."""
    out, keys = [], set()
    for r in rows:
        if r["key"] not in keys:
            keys.add(r["key"])
            out.append(r)
    return out


#: The words of the owner's rule (nando.md, always 12, §380) that lets the
#: answer key read a finished episode. Until the signed rule book Nando
#: runs under carries them, the key grades nothing: one call per episode
#: plus a retry is every agent's bound (every-agent.md, always 3), and only
#: the owner's rule makes this one more.
ANSWER_KEY_RULE = "Let your answer key"


def allowed() -> tuple:
    """`(ok, why)`: whether Nando's signed rule book lets the answer key
    run. Never raises."""
    try:
        from agents import rulebooks

        lock = rulebooks.check("nando.md")
        if not lock.ok:
            return False, f"nando's rule books are not signed: {lock.reason}"
        if ANSWER_KEY_RULE not in lock.text:
            return False, ("nando's signed rule book has no answer-key rule yet "
                           "(nando.md, always 12, §380): nothing is graded")
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


#: Origins never labelled: nothing private reaches an agent.
NOT_LABELLED = frozenset({"private", "archive"})


async def judge_batch(script_for: Callable[[str], list], limit: int = 0,
                      days: float = 7.0) -> dict:
    """Label up to `limit` written episodes the key has not seen, newest
    first. `script_for(key)` returns the cached sentences (or []). Returns
    what it did. Never raises."""
    out = {"looked": 0, "labelled": 0, "skipped": 0, "failed": 0}
    ok, why = allowed()
    if not ok:
        log.warning("nando's answer key did not run: %s", why)
        out["refused"] = why
        return out
    try:
        import category_audit  # noqa: F401 - the audit's own store
        import topics
        from config import settings

        limit = int(limit or settings.nando_judge_per_run)
        rows = [r for r in topics.category_tree().audit_rows(since=time.time() - days * 86400)
                if r.get("key") and r.get("origin", "") not in NOT_LABELLED]
        rows = _newest_per_key(rows)
        seen = store().labelled(r["key"] for r in rows)
        fresh = [r for r in rows if r["key"] not in seen]
        listing = tree_listing()
        for row in fresh[:limit]:
            out["looked"] += 1
            sentences = list(script_for(row["key"]) or [])
            if not sentences:
                out["skipped"] += 1
                continue
            label = await judge(row["key"], row["query"], sentences, listing)
            if label is None:
                out["failed"] += 1
                continue
            store().put_label(row["key"], row["query"], label["node"], label["confidence"],
                              label["reason"], label["judge_model"])
            out["labelled"] += 1
    except Exception as exc:  # noqa: BLE001
        log.exception("nando's answer-key run failed")
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def handler(script_for: Callable[[str], list]):
    """The background task `categorizer:judge`, bound to the script cache.
    (`register` also gives Netenyahu his model's nightly training, §381.)"""
    async def run(_task: str) -> None:
        result = await judge_batch(script_for)
        log.info("nando's answer key: %s", result)
    return run


def register(script_for: Callable[[str], list]) -> None:
    """Give Netenyahu the task's handler (the app, at boot, with AGENTS=1)."""
    from agents import netenyahu

    from agents import nando_model

    netenyahu.HANDLERS[("categorizer", "judge")] = handler(script_for)
    netenyahu.HANDLERS[("categorizer", "train")] = nando_model.handler()


# ------------------------------------------------------------------ the measurement
def _rate(hits: int, of: int) -> Optional[float]:
    return round(hits / of, 3) if of else None


def verdict(filed: str, truth: str) -> str:
    """How a filing compares with the key: `exact`, `branch` (one is the
    other's ancestor: true, more or less specific), `wrong`, or `nothing`
    (nothing filed)."""
    from agents import categorizer

    if not filed:
        return "nothing"
    if filed == truth:
        return "exact"
    if truth and (truth in categorizer._branch(filed) or filed in categorizer._branch(truth)):
        return "branch"
    return "wrong"


def compared(days: float = 30.0) -> list:
    """Every graded episode beside Nando's filing, newest first: `[{"key",
    "query", "filed", "truth", "confidence", "verdict", "at"}]`. Reads the
    stores - background and reports only."""
    import topics

    since = time.time() - days * 86400
    labels = {l["key"]: l for l in store().labels(since=since)}
    out = []
    for r in _newest_per_key(row for row in topics.category_tree().audit_rows(since=since)
                             if row.get("key") in labels):
        label = labels[r["key"]]
        filed = r.get("nando") or r.get("nando_request") or ""
        out.append({"key": r["key"], "query": r["query"], "filed": filed,
                    "truth": label["node"], "confidence": label["confidence"],
                    "verdict": verdict(filed, label["node"]), "at": label["at"]})
    return out


#: How many corrected mistakes become worked examples at most (§379), and
#: from how far back.
MISTAKES_KEPT = 200
MISTAKE_DAYS = 60.0


def mistakes(limit: int = MISTAKES_KEPT, days: float = MISTAKE_DAYS) -> list:
    """Nando's corrected mistakes as worked examples (§379, piece N5): each
    graded episode he filed under a wrong node - or under nothing - that the
    key was sure about, as `{"question", "node", "source": "mistake"}`, the
    key's node the answer. Newest first, one per question. A filing on the
    right branch is not a mistake. Never raises; read in the background by
    `category_examples`."""
    try:
        out, seen = [], set()
        for g in compared(days):
            if g["verdict"] not in ("wrong", "nothing") or g["confidence"] != "high":
                continue
            q = " ".join((g["query"] or "").split())
            if not q or q.lower() in seen:
                continue
            seen.add(q.lower())
            out.append({"question": q, "node": g["truth"], "source": "mistake"})
            if len(out) >= limit:
                break
        return out
    except Exception:  # noqa: BLE001
        log.exception("nando's mistakes could not be read")
        return []


def report(days: float = 30.0, examples: int = 10) -> dict:
    """How Nando's filings agree with the answer key (§377). Agreement is
    "same node" (exact), "on one branch" (one is the other's ancestor: true
    but more or less specific), else a disagreement. A filing of nothing is
    counted apart: it is never wrong, only unhelpful. Never raises."""
    try:
        from agents import categorizer

        graded = compared(days)
        exact = branch = wrong = nothing = 0
        by_facet: dict = {}
        confusions: dict = {}
        for g in graded:
            truth, filed, verdict = g["truth"], g["filed"], g["verdict"]
            facet = categorizer._facet(truth) if truth else "none"
            slot = by_facet.setdefault(facet, {"labelled": 0, "exact": 0, "wrong": 0})
            slot["labelled"] += 1
            if verdict == "nothing":
                nothing += 1
            elif verdict == "exact":
                exact += 1
                slot["exact"] += 1
            elif verdict == "branch":
                branch += 1
            else:
                wrong += 1
                slot["wrong"] += 1
                pair = (filed, truth or "none")
                confusions[pair] = confusions.get(pair, 0) + 1
        rows = graded
        filed_count = exact + branch + wrong
        return {
            "days": days, "labels": len(store().labels(since=time.time() - days * 86400)),
            "compared": len(rows),
            "exact": exact, "on_branch": branch, "wrong": wrong, "filed_nothing": nothing,
            "exact_rate": _rate(exact, filed_count), "wrong_rate": _rate(wrong, filed_count),
            "by_facet": dict(sorted(by_facet.items())),
            "confusions": [{"filed": a, "truth": b, "episodes": n}
                           for (a, b), n in sorted(confusions.items(),
                                                   key=lambda kv: -kv[1])[:examples]],
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
