"""Training Nando: the answer key and what it teaches (§381, piece N3).

The owner wants Nando to decide (§379) and to keep getting better as the
tree grows, without anyone hand-scoring episodes (decided item 30). The
answer key is how: a stronger model - `CATEGORIZER_JUDGE_MODEL`, Sonnet 5.5
since §503 (Opus before; the owner's choice) - reads finished episodes in the background and says
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

import asyncio
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
            # §387: following the tree. The node's path when it was graded
            # (so a pruned node's grade can point at the nearest ancestor
            # still there), when it was retired and what it points at now,
            # and whether a new node under it asks for a fresh grade.
            for column, kind in (("path", "TEXT NOT NULL DEFAULT ''"),
                                 ("retired_at", "REAL NOT NULL DEFAULT 0"),
                                 ("node_now", "TEXT NOT NULL DEFAULT ''"),
                                 ("regrade", "INTEGER NOT NULL DEFAULT 0")):
                try:
                    db.execute(f"ALTER TABLE nando_labels ADD COLUMN {column} {kind}")
                except Exception:  # noqa: BLE001 - already there
                    pass
            # Every episode the answer key has been asked about, answered or
            # not (§391): nando.md rule 12 lets it read each finished
            # episode once, so a failed or skipped answer is never asked
            # again.
            db.execute("""CREATE TABLE IF NOT EXISTS nando_asked (
                key TEXT PRIMARY KEY, at REAL NOT NULL)""")
            # The answer key's batches sent and not yet read (§501): the
            # batch id, each request's id with the episode it grades, and
            # when it was sent. Kept until collected, so a restart loses
            # nothing.
            db.execute("""CREATE TABLE IF NOT EXISTS nando_batches (
                batch_id TEXT PRIMARY KEY, waiting TEXT NOT NULL,
                sent_at REAL NOT NULL, done_at REAL NOT NULL DEFAULT 0)""")
            # The model the batch was sent to, so a deploy that changes the
            # setting meanwhile cannot fail every answer as the wrong model.
            try:
                db.execute("ALTER TABLE nando_batches ADD COLUMN model"
                           " TEXT NOT NULL DEFAULT ''")
            except Exception:  # noqa: BLE001 - already there
                pass
            # The one-time comparison's start (§503): once it has begun it has
            # run, whatever it managed to compare.
            db.execute("""CREATE TABLE IF NOT EXISTS nando_calibration_run (
                id INTEGER PRIMARY KEY, started_at REAL NOT NULL,
                finished_at REAL NOT NULL DEFAULT 0, outcome TEXT NOT NULL DEFAULT '')""")
            # The owner's own reviews (§503): the node the owner said an
            # episode is about ("" for none), and the answer key's grade at
            # that moment, so how often the key agrees with the owner is a
            # number. The review also becomes the episode's grade.
            db.execute("""CREATE TABLE IF NOT EXISTS nando_owner (
                key TEXT PRIMARY KEY, query TEXT NOT NULL, node TEXT NOT NULL,
                key_node TEXT NOT NULL DEFAULT '', key_model TEXT NOT NULL DEFAULT '',
                at REAL NOT NULL)""")
            # The owner's one-time comparison of two answer keys (§503): each
            # episode's grade from the nightly key and from the second.
            db.execute("""CREATE TABLE IF NOT EXISTS nando_calibration (
                key TEXT PRIMARY KEY, query TEXT NOT NULL, first TEXT NOT NULL,
                first_model TEXT NOT NULL, second TEXT NOT NULL,
                second_model TEXT NOT NULL, at REAL NOT NULL)""")
            # The tree as the last nightly look saw it (§387).
            db.execute("""CREATE TABLE IF NOT EXISTS nando_tree (
                node TEXT PRIMARY KEY, parent TEXT NOT NULL DEFAULT '',
                seen_at REAL NOT NULL)""")

    def _connect(self) -> sqlite3.Connection:
        conn = store_db.connect(self.path, timeout=10, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def put_owner(self, key: str, query: str, node: str, key_node: str,
                  key_model: str, now: float = 0.0) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO nando_owner (key, query, node, key_node, key_model, at)"
                       " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
                       " node = excluded.node, at = excluded.at",
                       (key, query, node, key_node, key_model, now or time.time()))

    def owner_reviews(self, since: float = 0.0) -> list:
        with self._connect() as db:
            rows = db.execute("SELECT key, query, node, key_node, key_model, at"
                              " FROM nando_owner WHERE at >= ? ORDER BY at DESC",
                              (since,)).fetchall()
        return [dict(zip(("key", "query", "node", "key_node", "key_model", "at"), r))
                for r in rows]

    def put_calibration(self, key: str, query: str, first: str, first_model: str,
                        second: str, second_model: str, now: float = 0.0) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO nando_calibration (key, query, first, first_model,"
                       " second, second_model, at) VALUES (?, ?, ?, ?, ?, ?, ?)"
                       " ON CONFLICT(key) DO NOTHING",
                       (key, query, first, first_model, second, second_model,
                        now or time.time()))

    def calibration(self) -> list:
        with self._connect() as db:
            rows = db.execute("SELECT key, query, first, first_model, second, second_model,"
                              " at FROM nando_calibration ORDER BY at").fetchall()
        return [dict(zip(("key", "query", "first", "first_model", "second",
                          "second_model", "at"), r)) for r in rows]

    def put_batch(self, batch_id: str, waiting: dict, now: float = 0.0,
                  model: str = "") -> None:
        import json

        with self._lock, self._connect() as db:
            db.execute("INSERT INTO nando_batches (batch_id, waiting, sent_at, done_at, model)"
                       " VALUES (?, ?, ?, 0, ?) ON CONFLICT(batch_id) DO NOTHING",
                       (batch_id, json.dumps(waiting), now or time.time(), model))

    def pending_batches(self) -> list:
        """`[(batch_id, waiting, sent_at, model)]` not yet collected, oldest
        first."""
        import json

        with self._connect() as db:
            rows = db.execute("SELECT batch_id, waiting, sent_at, model FROM nando_batches"
                              " WHERE done_at = 0 ORDER BY sent_at").fetchall()
        return [(r[0], json.loads(r[1] or "{}"), float(r[2]), r[3] or "") for r in rows]

    def start_calibration(self, now: float = 0.0) -> bool:
        """Record the one-time comparison's start; False when it has started
        before (§503). Once started it has run."""
        with self._lock, self._connect() as db:
            if db.execute("SELECT 1 FROM nando_calibration_run WHERE id = 1").fetchone():
                return False
            db.execute("INSERT INTO nando_calibration_run (id, started_at) VALUES (1, ?)",
                       (now or time.time(),))
            return True

    def finish_calibration(self, outcome: str, now: float = 0.0) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE nando_calibration_run SET finished_at = ?, outcome = ?"
                       " WHERE id = 1", (now or time.time(), outcome[:300]))

    def calibration_run(self) -> Optional[dict]:
        with self._connect() as db:
            row = db.execute("SELECT started_at, finished_at, outcome FROM"
                             " nando_calibration_run WHERE id = 1").fetchone()
        return None if row is None else {"started_at": float(row[0]),
                                         "finished_at": float(row[1]), "outcome": row[2]}

    def finish_batch(self, batch_id: str, now: float = 0.0) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE nando_batches SET done_at = ? WHERE batch_id = ?",
                       (now or time.time(), batch_id))

    def put_label(self, key: str, query: str, node: str, confidence: str, reason: str,
                  judge_model: str, now: float = 0.0, path: str = "") -> None:
        """A grade, or a fresh one for the same episode: a new grade is
        never retired and asks for no regrade."""
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO nando_labels (key, query, node, confidence, reason,"
                       " judge_model, at, path, retired_at, node_now, regrade)"
                       " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, '', 0)"
                       " ON CONFLICT(key) DO UPDATE SET query = excluded.query,"
                       " node = excluded.node, confidence = excluded.confidence,"
                       " reason = excluded.reason, judge_model = excluded.judge_model,"
                       " at = excluded.at, path = excluded.path, retired_at = 0,"
                       " node_now = '', regrade = 0"
                       # The owner's review is never replaced by a model's
                       # grade - a batch collected after it, say (§503).
                       f" WHERE nando_labels.judge_model <> '{OWNER}'"
                       f" OR excluded.judge_model = '{OWNER}'",
                       (key, query[:500], node[:120], confidence[:8], reason[:240],
                        judge_model[:80], now or time.time(), path[:400]))

    def mark_asked(self, key: str, now: float = 0.0) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO nando_asked (key, at) VALUES (?, ?)"
                       " ON CONFLICT(key) DO NOTHING", (key, now or time.time()))

    def asked(self, keys) -> set:
        """The keys the answer key has been asked about, graded or not."""
        keys, found = list(keys), set()
        with self._connect() as db:
            for i in range(0, len(keys), 500):     # under any bound-variable limit
                chunk = keys[i:i + 500]
                found.update(r[0] for r in db.execute(
                    f"SELECT key FROM nando_asked WHERE key IN"
                    f" ({', '.join('?' for _ in chunk)})", chunk).fetchall())
        return found

    def labelled(self, keys) -> set:
        keys, found = list(keys), set()
        with self._connect() as db:
            for i in range(0, len(keys), 500):     # under any bound-variable limit
                chunk = keys[i:i + 500]
                found.update(r[0] for r in db.execute(
                    f"SELECT key FROM nando_labels WHERE key IN"
                    f" ({', '.join('?' for _ in chunk)})", chunk).fetchall())
        return found

    _COLUMNS = ("key", "query", "node", "confidence", "reason", "judge_model", "at",
                "path", "retired_at", "node_now", "regrade")

    def labels(self, since: float = 0.0, limit: int = 20000,
               retired: bool = True) -> list:
        """Grades since `since`, newest first. `retired=False` leaves out
        grades whose node the tree has lost (§387)."""
        where = "at >= ?" + ("" if retired else " AND retired_at = 0")
        with self._connect() as db:
            rows = db.execute(f"SELECT {', '.join(self._COLUMNS)} FROM nando_labels"
                              f" WHERE {where} ORDER BY at DESC LIMIT ?",
                              (since, int(limit))).fetchall()
        return [dict(zip(self._COLUMNS, r)) for r in rows]

    def to_regrade(self) -> set:
        with self._connect() as db:
            return {r[0] for r in db.execute(
                "SELECT key FROM nando_labels WHERE regrade = 1").fetchall()}

    def mark(self, key: str, **fields) -> None:
        """Change a grade's tree fields only (`retired_at`, `node_now`,
        `regrade`, `path`): its node and reason stay as the key gave them,
        so every mark can be undone."""
        allowed = {k: v for k, v in fields.items()
                   if k in ("retired_at", "node_now", "regrade", "path")}
        if not allowed:
            return
        with self._lock, self._connect() as db:
            db.execute(f"UPDATE nando_labels SET {', '.join(f'{k} = ?' for k in allowed)}"
                       " WHERE key = ?", (*allowed.values(), key))

    def tree_seen(self) -> dict:
        with self._connect() as db:
            return dict(db.execute("SELECT node, parent FROM nando_tree").fetchall())

    def see_tree(self, nodes: dict, now: float = 0.0) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM nando_tree")
            db.executemany("INSERT INTO nando_tree (node, parent, seen_at) VALUES (?, ?, ?)",
                           [(n, p, now or time.time()) for n, p in nodes.items()])

    def forget(self) -> int:
        """A wipe: every label was learnt from episodes the wipe empties, and
        the tree is re-seeded, so the last look at it goes too."""
        with self._lock, self._connect() as db:
            n = db.execute("SELECT COUNT(*) FROM nando_labels").fetchone()[0]
            db.execute("DELETE FROM nando_labels")
            db.execute("DELETE FROM nando_asked")
            db.execute("DELETE FROM nando_batches")
            db.execute("DELETE FROM nando_owner")
            db.execute("DELETE FROM nando_calibration")
            db.execute("DELETE FROM nando_calibration_run")
            db.execute("DELETE FROM nando_tree")
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


def _judge_request(query: str, sentences: list, listing: str,
                   model_setting: str = "categorizer_judge_model"):
    """The answer key's one request for one episode, live or batched.
    `model_setting` is the second key's only in the owner's one-time
    comparison (§503)."""
    from agents import models
    from config import settings

    script = " ".join(str(s) for s in (sentences or ())[:SCRIPT_SENTENCES])
    return models.AgentRequest(
        agent="categorizer",
        task="judge" if model_setting == "categorizer_judge_model" else "calibrate",
        switch_setting="categorizer", model_setting=model_setting, rule_book="nando.md",
        schema=JudgeLabel,
        instructions=JUDGE_INSTRUCTIONS + "\n\nFAM's category tree (id | path):\n" + listing,
        material=(("request", query), ("script", script)),
        timeout=float(settings.categorizer_judge_timeout_seconds),
        max_tokens=int(settings.categorizer_judge_max_tokens), effort="medium",
        surface="background")


def _label_from(result) -> Optional[dict]:
    """A usable grade from the key's answer, or None. Never a node the tree
    lacks."""
    import topics

    # Not counted toward Nando's hold (§391): three failed nightly grades
    # must never stop his filing on listeners' episodes. A failure is still
    # in `agent_calls` and on /admin.
    if result is None or not result.ok:
        return None
    node = result.value.node.strip()
    if node != "none" and node not in topics.FACETS \
            and topics.category_tree().get(node) is None:
        return None
    return {"node": "" if node == "none" else node,
            "confidence": result.value.confidence,
            "reason": result.value.reason.strip(),
            "judge_model": result.model}


async def judge(key: str, query: str, sentences: list, listing: str,
                model_setting: str = "categorizer_judge_model") -> Optional[dict]:
    """One episode through the answer key, live, or None when it did not
    answer usably. Never raises."""
    try:
        from agents import models

        return _label_from(await models.ask(
            _judge_request(query, sentences, listing, model_setting)))
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


#: The words of the owner's rule (nando.md, always 12, §384) that lets the
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
                           "(nando.md, always 12, §384): nothing is graded")
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _path_text(node: str) -> str:
    from agents import categorizer

    return " > ".join(categorizer.path_for(node)) if node else ""


#: Origins never labelled: nothing private reaches an agent.
NOT_LABELLED = frozenset({"private", "archive"})


#: What a filing came from when it was not Nando's own reading (§501):
#: the tap's own filing, which the words may only narrow, or a provider's.
SOURCES = ("tile", "bank", "dailyfam", "edition", "provider")


def filed_by(row: dict) -> str:
    """Who decided an audited episode's filing (§501): a solid source it
    started from (`SOURCES`) when the filing is that source's node or a
    narrowing of it on its branch; otherwise how Nando's request found it -
    `picked` (a model's pick from his list), `category` or `facet` (the
    brief's own words), `model` (his small model), `parent` (a Go Deeper's
    episode) - or `nothing`."""
    from agents import categorizer

    filed = row.get("nando") or row.get("nando_request") or ""
    how = (row.get("nando_request_how") or "").split("~")[0]
    if not filed:
        return "nothing"
    if how in SOURCES:
        return how
    prior, source = row.get("nando_prior") or "", row.get("nando_prior_source") or ""
    if prior and source in SOURCES and (
            filed == prior or prior in categorizer._branch(filed)):
        return source
    return how or "unknown"


def _tier(row: dict) -> int:
    """How much grading this episode teaches (§501), most first: 0 Nando
    filed nothing; 1 the writer made him re-file; 2 his node and the
    writer's words disagree; 3 his small model filed it, a guess its numbers
    trusted; 4 a solid source filed it, so only the key checks that source;
    5 everything agreed."""
    if not (row.get("nando") or row.get("nando_request")):
        return 0
    if row.get("nando_how") == "refiled":
        return 1
    if row.get("nando") and row.get("writer") and row["nando"] != row["writer"]:
        return 2
    by = filed_by(row)
    if by == "model":
        return 3
    if by in SOURCES:
        return 4
    return 5


def agreed(row: dict) -> bool:
    """Nando, the writer and the composer all filed the episode on one
    branch (§503): the same node, or one more specific than another - taken
    as right without asking the key. False when Nando filed nothing or the
    writer named nothing, since then nothing was checked; when a solid source
    filed it (a tile's, an edition's, a bank topic's, a DailyFAM subject's,
    a provider's filing), since Nando may only narrow that one and the
    composer's is often the same filing, so they agree by construction and
    the source itself is what needs grading (§502); and when a filing is a
    bare facet beside something more specific, which agrees with anything
    under it."""
    import topics

    filed = row.get("nando") or row.get("nando_request") or ""
    writer, composer = row.get("writer") or "", row.get("composer") or ""
    if not filed or not writer or row.get("nando_how") == "refiled":
        return False
    if filed_by(row) in SOURCES:
        return False
    for other in (writer, composer):
        if not other:
            continue
        v = verdict(filed, other)
        if v == "exact":
            continue
        if v != "branch" or filed in topics.FACETS or other in topics.FACETS:
            return False
    return True


def _facet_of(row: dict) -> str:
    from agents import categorizer

    node = row.get("nando") or row.get("nando_request") or row.get("writer") or ""
    try:
        return categorizer._facet(node) if node else (row.get("writer_facet") or "none")
    except Exception:  # noqa: BLE001
        return "none"


def choose(rows: list) -> list:
    """The order the answer key grades in (§501), not newest first: every
    write path takes its turn - a search, prefetch, each edition - so a
    burst on one path never crowds out another; within a path the episodes
    that teach most come first (`_tier`), and within a tier the facets take
    turns, newest first. Pure: rows in, the same rows reordered."""
    buckets: dict = {}
    for row in rows:
        buckets.setdefault(row.get("origin") or "?", []).append(row)
    for origin, items in buckets.items():
        items.sort(key=lambda r: -(r.get("at") or 0))
        turn: dict = {}
        ranked = []
        for r in items:
            group = (_tier(r), _facet_of(r))
            turn[group] = turn.get(group, 0) + 1
            ranked.append(((_tier(r), turn[group], -(r.get("at") or 0)), r))
        buckets[origin] = [r for _k, r in sorted(ranked, key=lambda kr: kr[0])]
    out: list = []
    queues = [buckets[o] for o in sorted(buckets)]
    i = 0
    while any(queues):
        q = queues[i % len(queues)]
        if q:
            out.append(q.pop(0))
        i += 1
    return out


async def judge_batch(script_for: Callable[[str], list], limit: int = 0,
                      days: float = 7.0) -> dict:
    """Grade up to `limit` written episodes the key has not seen, in
    `choose`'s order. Through the Message Batches API when
    `NANDO_JUDGE_BATCH` is on (§501): the batch is sent here and collected
    by `collect_batches`; otherwise each grade is asked live, as before.
    `script_for(key)` returns the cached sentences (or []). Returns what it
    did. Never raises."""
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

        def plan() -> tuple:
            # Every database read in a thread, never on the event loop the
            # listeners share (§391).
            rows = [r for r in topics.category_tree().audit_rows(
                        since=time.time() - days * 86400)
                    if r.get("key") and r.get("origin", "") not in NOT_LABELLED]
            rows = _newest_per_key(rows)
            done = store().labelled(r["key"] for r in rows) | store().asked(
                r["key"] for r in rows)
            fresh = []
            todo = [r for r in rows if r["key"] not in done]
            if settings.nando_judge_disagreements_only:
                # Three filers on one branch are taken as right (§503): the
                # key is spent only where they disagree.
                todo = [r for r in todo if not agreed(r)]
            for row in choose(todo):
                sentences = list(script_for(row["key"]) or [])
                if sentences:                # no script yet: asked another night
                    fresh.append((row, sentences))
                if len(fresh) >= limit:
                    break
            return fresh, tree_listing()

        # Read once each (nando.md rule 12): an episode is never asked again,
        # answered or not; a new node under its grade only flags it (§387).
        fresh, listing = await asyncio.to_thread(plan)
        out["by_origin"] = {}
        out["by_tier"] = {}
        for row, _s in fresh:
            o = row.get("origin") or "?"
            out["by_origin"][o] = out["by_origin"].get(o, 0) + 1
            t = str(_tier(row))
            out["by_tier"][t] = out["by_tier"].get(t, 0) + 1
        out["looked"] = len(fresh)
        for row, _s in fresh:
            await asyncio.to_thread(store().mark_asked, row["key"])
        if settings.nando_judge_batch and fresh:
            from agents import models

            ids = {f"n{i}": (row, sentences) for i, (row, sentences) in enumerate(fresh)}
            requests = {cid: _judge_request(row["query"], sentences, listing)
                        for cid, (row, sentences) in ids.items()}
            batch_id, refused, attempted = await models.submit_batch(requests)
            out["failed"] += len(refused)
            if batch_id:
                waiting = {cid: {"key": row["key"], "query": row["query"]}
                           for cid, (row, _s) in ids.items() if cid not in refused}
                await asyncio.to_thread(store().put_batch, batch_id, waiting, 0.0,
                                        models.model_setting_value("categorizer_judge_model"))
                out["batch_id"] = batch_id
                out["batched"] = len(waiting)
                return out
            if attempted:
                # The create failed or timed out, and the batch may have been
                # made all the same: its episodes are not read again live
                # (rule 12: each finished episode once).
                out["failed"] += len(ids) - len(refused)
                return out
            # Never sent (a vendor that cannot batch): asked live instead.
            fresh = [pair for cid, pair in ids.items() if cid not in refused]
        for row, sentences in fresh:
            label = await judge(row["key"], row["query"], sentences, listing)
            if label is None:
                out["failed"] += 1
                continue
            await asyncio.to_thread(_keep, row["key"], row["query"], label)
            out["labelled"] += 1
    except Exception as exc:  # noqa: BLE001
        log.exception("nando's answer-key run failed")
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _keep(key: str, query: str, label: dict) -> None:
    store().put_label(key, query, label["node"], label["confidence"], label["reason"],
                      label["judge_model"], 0.0, _path_text(label["node"]))


#: A sent batch nobody could read for this long is given up (the API
#: answers every batch within a day; two days covers a deployment down).
BATCH_GIVE_UP_SECONDS = 48 * 3600


async def collect_batches(script_for: Callable[[str], list]) -> dict:
    """Read every sent batch that has ended (§501) and keep its grades. A
    request the batch did not answer is not asked again: nando.md rule 12
    lets the key read each finished episode once, and §391 settled that an
    episode asked about is never asked again, answered or not - the same
    single attempt a live grade gets. A batch still running is left for the
    next pass. Never raises."""
    # What the batches cost, and how much of their input the prompt cache
    # served (§502): the batch halves every token, but a live run already
    # reads the tree from the cache, so the saving is only as good as the
    # batch's cache hits - measured here, never assumed.
    out = {"batches": 0, "labelled": 0, "failed": 0, "waiting": 0,
           "cost_usd": 0.0, "input_tokens": 0, "cache_read_tokens": 0}
    try:
        from agents import models

        pending = await asyncio.to_thread(store().pending_batches)
        for batch_id, waiting, sent_at, model in pending:
            requests = {cid: _judge_request(item["query"], [], "")
                        for cid, item in waiting.items()}
            # Read with the model it was sent to, whatever the setting says now.
            results = await models.collect_batch(batch_id, requests, model=model)
            if results is None:
                if time.time() - sent_at > BATCH_GIVE_UP_SECONDS:
                    log.warning("nando's answer-key batch %s given up unread", batch_id)
                    await asyncio.to_thread(store().finish_batch, batch_id)
                    out["failed"] += len(waiting)
                else:
                    out["waiting"] += 1
                continue
            out["batches"] += 1
            # Finished before its grades are kept: its spend is now in the
            # ledger, and a batch read twice would record it twice. A grade
            # that cannot be kept below is lost, never paid for again.
            await asyncio.to_thread(store().finish_batch, batch_id)
            for cid, item in waiting.items():
                result = results.get(cid)
                if result is not None:
                    out["cost_usd"] += float(result.cost_usd or 0.0)
                    out["input_tokens"] += int((result.usage or {}).get("input_tokens", 0))
                    out["cache_read_tokens"] += int(
                        (result.usage or {}).get("cache_read_tokens", 0))
                label = _label_from(result)
                if label is None:
                    out["failed"] += 1
                    continue
                try:
                    await asyncio.to_thread(_keep, item["key"], item["query"], label)
                    out["labelled"] += 1
                except Exception:  # noqa: BLE001 - one grade, not the batch
                    log.exception("nando's grade for %r could not be kept", item["query"])
                    out["failed"] += 1
    except Exception as exc:  # noqa: BLE001
        log.exception("nando's answer-key batches could not be collected")
        out["error"] = f"{type(exc).__name__}: {exc}"
    out["cost_usd"] = round(out["cost_usd"], 6)
    read = out["cache_read_tokens"]
    out["cache_hit_share"] = _rate(read, read + out["input_tokens"])
    return out


def collect_handler(script_for: Callable[[str], list]):
    """The background task `categorizer:collect` (§501)."""
    async def run(_task: str) -> None:
        result = await collect_batches(script_for)
        if result.get("batches") or result.get("error"):
            log.info("nando's answer key, collected: %s", result)
    return run


def handler(script_for: Callable[[str], list]):
    """The background task `categorizer:judge`, bound to the script cache.
    (`register` also gives Netenyahu his model's nightly training, §385.)"""
    async def run(_task: str) -> None:
        result = await judge_batch(script_for)
        log.info("nando's answer key: %s", result)
    return run


def register(script_for: Callable[[str], list]) -> None:
    """Give Netenyahu the task's handler (the app, at boot, with AGENTS=1)."""
    from agents import netenyahu

    from agents import nando_model

    netenyahu.HANDLERS[("categorizer", "follow")] = follow_handler()
    netenyahu.HANDLERS[("categorizer", "judge")] = handler(script_for)
    netenyahu.HANDLERS[("categorizer", "collect")] = collect_handler(script_for)
    netenyahu.HANDLERS[("categorizer", "calibrate")] = calibrate_handler(script_for)
    netenyahu.HANDLERS[("categorizer", "train")] = nando_model.handler()


# ------------------------------------------------------------------ the measurement
def _rate(hits: int, of: int) -> Optional[float]:
    return round(hits / of, 3) if of else None


def verdict(filed: str, truth: str) -> str:
    """How a filing compares with the key: `exact`, `branch` (one is the
    other's ancestor: true, more or less specific), `wrong`, or `nothing`
    (nothing filed)."""
    from agents import categorizer

    if not filed and not truth:
        return "exact"           # both said no node fits: agreement, never a mistake
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
    # A retired grade describes a tree that has gone (§387): not compared.
    labels = {l["key"]: l for l in store().labels(since=since, retired=False)}
    out = []
    for r in _newest_per_key(row for row in topics.category_tree().audit_rows(since=since)
                             if row.get("key") in labels):
        label = labels[r["key"]]
        filed = r.get("nando") or r.get("nando_request") or ""
        composer = r.get("composer") or ""
        out.append({"key": r["key"], "query": r["query"], "filed": filed,
                    "truth": label["node"], "confidence": label["confidence"],
                    "verdict": verdict(filed, label["node"]), "at": label["at"],
                    # §501: who filed it, and the composer's own category
                    # graded apart from Nando's filing.
                    "by": filed_by(r), "origin": r.get("origin") or "",
                    "composer": composer,
                    "composer_verdict": verdict(composer, label["node"]) if composer else ""})
    return out


#: How many corrected mistakes become worked examples at most (§383), and
#: from how far back.
MISTAKES_KEPT = 200
MISTAKE_DAYS = 30.0          # the audit keeps 30 days (categories.AUDIT_DAYS)


def mistakes(limit: int = MISTAKES_KEPT, days: float = MISTAKE_DAYS) -> list:
    """Nando's corrected mistakes as worked examples (§383, piece N5): each
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


#: How many of the composer's corrected categories it is shown (§501).
COMPOSER_CORRECTIONS = 12


#: The words of the amendment to nando.md rule 12 that lets the key's
#: grades teach the story composer (§502). Rule 12 as signed says the
#: grades "only teach you and measure you" - Nando - so until the owner
#: signs a book carrying these words, the composer is shown nothing.
COMPOSER_RULE = "teach the story composer"


def composer_allowed() -> tuple:
    """`(ok, why)`: whether Nando's signed rule book lets his answer key's
    grades teach the story composer. Never raises."""
    try:
        from agents import rulebooks

        lock = rulebooks.check("nando.md")
        if not lock.ok:
            return False, f"nando's rule books are not signed: {lock.reason}"
        if COMPOSER_RULE not in lock.text:
            return False, ("nando's signed rule 12 lets the answer key teach only him "
                           "(§502): the composer is shown no corrections until the "
                           "owner signs the amendment")
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def composer_corrections(limit: int = COMPOSER_CORRECTIONS,
                         days: float = MISTAKE_DAYS) -> list:
    """The story composer's categories the answer key was sure were wrong
    (§501), newest first, one per question: `{"question", "said", "node"}`,
    the key's node the answer ("" when no node fits). Shown to the composer
    as worked corrections, since a tile's category is a solid filing that
    Nando may narrow and never move - so its mistakes are corrected where
    they are made. [] until the owner signs the amendment to rule 12 that
    allows it (`composer_allowed`). Never raises; read in the background."""
    try:
        if not composer_allowed()[0]:
            return []
        out, seen = [], set()
        for g in compared(days):
            if g.get("composer_verdict") != "wrong" or g["confidence"] != "high":
                continue
            q = " ".join((g["query"] or "").split())
            if not q or q.lower() in seen:
                continue
            seen.add(q.lower())
            out.append({"question": q, "said": g["composer"], "node": g["truth"]})
            if len(out) >= limit:
                break
        return out
    except Exception:  # noqa: BLE001
        log.exception("the composer's corrections could not be read")
        return []


def _source_counts(graded: list, field: str, verdict_field: str, examples: int) -> dict:
    """Per filer: graded, exact, on its branch, wrong, the wrong rate (of
    those filed), and its commonest wrong filings."""
    out: dict = {}
    for g in graded:
        name = g.get(field) or ""
        v = g.get(verdict_field) or ""
        if not name or not v or v == "nothing":
            continue
        slot = out.setdefault(name, {"graded": 0, "exact": 0, "on_branch": 0, "wrong": 0,
                                     "wrong_examples": []})
        slot["graded"] += 1
        key = {"exact": "exact", "branch": "on_branch", "wrong": "wrong"}[v]
        slot[key] += 1
        if v == "wrong" and len(slot["wrong_examples"]) < examples:
            slot["wrong_examples"].append({"query": g["query"], "filed": g["filed"]
                                           if field == "by" else g["composer"],
                                           "truth": g["truth"] or "none",
                                           "confidence": g["confidence"]})
    for slot in out.values():
        slot["wrong_rate"] = _rate(slot["wrong"], slot["graded"])
    return dict(sorted(out.items()))


def report(days: float = 30.0, examples: int = 10) -> dict:
    """How Nando's filings agree with the answer key (§381). Agreement is
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
            # §501: every filer graded apart - each solid source (tile, bank
            # topic, DailyFAM subject, edition, provider) and each way
            # Nando decides - and the story composer's own category; and
            # every write path apart.
            "by_source": _source_counts(graded, "by", "verdict", examples),
            "composer": _source_counts(
                [dict(g, composer_name="composer") for g in graded],
                "composer_name", "composer_verdict", examples).get("composer"),
            "by_origin": _source_counts(graded, "origin", "verdict", 0),
            "confusions": [{"filed": a, "truth": b, "episodes": n}
                           for (a, b), n in sorted(confusions.items(),
                                                   key=lambda kv: -kv[1])[:examples]],
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


# ------------------------------------------------------------------ the owner's review (§503)
#: How far back the review queue looks: an episode's script is kept a week.
REVIEW_DAYS = 7.0


def review_queue(script_for: Callable[[str], list], limit: int = 20,
                 days: float = REVIEW_DAYS) -> list:
    """Episodes for the owner to file by hand, in the answer key's own order
    (`choose`: every write path in turn, disagreements first), skipping
    any the owner has reviewed, a private search's and any whose script is
    gone. Each with what every filer said, the key's grade if it has one,
    and the script's opening, to read. Reads the stores - run in a thread.
    Never raises."""
    try:
        import topics

        since = time.time() - days * 86400
        rows = _newest_per_key(r for r in topics.category_tree().audit_rows(since=since)
                               if r.get("key") and r.get("origin", "") not in NOT_LABELLED)
        reviewed = {r["key"] for r in store().owner_reviews()}
        labels = {l["key"]: l for l in store().labels(since=0, retired=False)}
        out = []
        for row in choose([r for r in rows if r["key"] not in reviewed]):
            sentences = list(script_for(row["key"]) or [])
            if not sentences:
                continue
            label = labels.get(row["key"])
            out.append({
                "key": row["key"], "query": row["query"], "origin": row.get("origin") or "",
                "nando": row.get("nando") or row.get("nando_request") or "",
                "filed_by": filed_by(row), "writer": row.get("writer") or "",
                "writer_words": row.get("words") or "", "composer": row.get("composer") or "",
                "agreed": agreed(row),
                "key_grade": (label or {}).get("node") if label else None,
                "key_model": (label or {}).get("judge_model", "") if label else "",
                "opening": " ".join(str(x) for x in sentences[:6]),
                "at": row.get("at")})
            if len(out) >= limit:
                break
        return out
    except Exception:  # noqa: BLE001
        log.exception("the owner's review queue could not be read")
        return []


def owner_label(key: str, node: str) -> dict:
    """The owner's filing of one episode (§503): `node` a tree node or "" for
    none. Kept as the owner's review - beside the key's grade at that
    moment, for the agreement number - and as the episode's grade, so it
    teaches Nando ahead of any model's (his memory, his mistakes, his small
    model) and the key never spends a call on it. Raises ValueError for a
    node the tree lacks or an episode the audit has no record of."""
    import topics

    node = (node or "").strip()
    if node and node not in topics.FACETS and topics.category_tree().get(node) is None:
        raise ValueError(f"{node!r} is not a node of the category tree")
    rows = [r for r in topics.category_tree().audit_rows(since=0) if r.get("key") == key]
    if not rows:
        raise ValueError("no written episode with that key")
    query = rows[0]["query"]
    before = {l["key"]: l for l in store().labels(since=0, retired=False)}.get(key) or {}
    key_node = before.get("node", "") if before.get("judge_model") != OWNER else ""
    key_model = before.get("judge_model", "") if before.get("judge_model") != OWNER else ""
    store().put_owner(key, query, node, key_node if before else "", key_model)
    store().put_label(key, query, node, "high", "the owner's review", OWNER, 0.0,
                      _path_text(node))
    store().mark_asked(key)
    return {"key": key, "node": node, "key_node": key_node, "key_model": key_model}


#: The `judge_model` an owner's review is kept under.
OWNER = "owner"


def owner_agreement(days: float = 90.0) -> dict:
    """How often the answer key's grade matched the owner's review (§503),
    over reviews of episodes the key had graded first: the number that says
    whether the key can be trusted ([episode-quality]: a grader is trusted
    once it matches the owner). None, not 0, before any."""
    exact = branch = wrong = 0
    examples = []
    for r in store().owner_reviews(since=time.time() - days * 86400):
        if not r["key_model"]:
            continue
        v = verdict(r["key_node"], r["node"])
        if v == "exact":
            exact += 1
        elif v == "branch":
            branch += 1
        else:
            wrong += 1
            if len(examples) < 10:
                examples.append({"query": r["query"], "key": r["key_node"] or "none",
                                 "owner": r["node"] or "none", "model": r["key_model"]})
    n = exact + branch + wrong
    return {"compared": n, "exact": exact, "on_branch": branch, "wrong": wrong,
            "agree_rate": _rate(exact + branch, n), "exact_rate": _rate(exact, n),
            "disagreements": examples}


# ------------------------------------------------------------------ the one-time comparison (§503)
#: The words of the amendment to nando.md rule 12 that lets a second answer
#: key read episodes again, once, for the owner's comparison (§503). Rule
#: 12 as signed lets the key read each finished episode once.
CALIBRATION_RULE = "once only and at the owner's request"


def calibration_allowed() -> tuple:
    """`(ok, why)`: whether the owner's one-time comparison may run - the
    signed book carries the amendment, and it has never run. Never raises."""
    try:
        from agents import rulebooks

        lock = rulebooks.check("nando.md")
        if not lock.ok:
            return False, f"nando's rule books are not signed: {lock.reason}"
        if CALIBRATION_RULE not in lock.text:
            return False, ("nando's signed rule 12 lets the answer key read each episode "
                           "once: the comparison waits for the owner to sign the "
                           "amendment (§503)")
        if store().calibration_run() is not None:
            return False, "the comparison has already run; it runs once only"
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


#: The most episodes the comparison may read, as signed rule 12 says ("at
#: most 100"), whatever `NANDO_CALIBRATION_EPISODES` is set to.
CALIBRATION_MAX = 100


async def calibrate(script_for: Callable[[str], list], limit: int = 0,
                    days: float = REVIEW_DAYS, at_once: int = 4) -> dict:
    """The owner's one-time comparison (§503): up to `limit`
    (`NANDO_CALIBRATION_EPISODES`, never more than `CALIBRATION_MAX`)
    recent episodes, each graded by the nightly key and once by the second
    (`CATEGORIZER_CALIBRATION_MODEL`), four at a time. The nightly key's
    existing grade is reused; an episode it never read is read by it once
    (and that grade kept as the night's would be); an episode it was asked
    about with no usable grade is passed over, so no episode is read by the
    nightly key twice. Only the comparison is kept. Its start is recorded
    before anything is read, so it runs once only, however it ends. Never
    raises."""
    out = {"compared": 0, "failed": 0}
    ok, why = calibration_allowed()
    if not ok:
        out["refused"] = why
        log.warning("nando's comparison did not run: %s", why)
        return out
    try:
        import topics
        from config import settings

        if not (settings.agents and settings.categorizer):
            out["refused"] = "Nando is off (AGENTS, CATEGORIZER)"
            return out
        if not await asyncio.to_thread(store().start_calibration):
            out["refused"] = "the comparison has already run; it runs once only"
            return out
        limit = max(1, min(int(limit or settings.nando_calibration_episodes),
                           CALIBRATION_MAX))

        def plan() -> tuple:
            rows = _newest_per_key(
                r for r in topics.category_tree().audit_rows(since=time.time() - days * 86400)
                if r.get("key") and r.get("origin", "") not in NOT_LABELLED)
            labels = {l["key"]: l for l in store().labels(since=0, retired=False)}
            asked = store().asked(r["key"] for r in rows)
            picked = []
            for row in choose(rows):
                label = labels.get(row["key"])
                if label and label["judge_model"] == OWNER:
                    continue                  # the owner's filing is not a key's
                if label is None and row["key"] in asked:
                    continue                  # read once already, with no grade
                sentences = list(script_for(row["key"]) or [])
                if sentences:
                    picked.append((row, sentences, label))
                if len(picked) >= limit:
                    break
            return picked, tree_listing()

        picked, listing = await asyncio.to_thread(plan)
        gate = asyncio.Semaphore(max(1, int(at_once)))

        async def one(row, sentences, label):
            async with gate:
                if label is None:
                    await asyncio.to_thread(store().mark_asked, row["key"])
                    first = await judge(row["key"], row["query"], sentences, listing)
                    if first is not None:
                        await asyncio.to_thread(_keep, row["key"], row["query"], first)
                else:
                    first = {"node": label["node"], "judge_model": label["judge_model"]}
                second = await judge(row["key"], row["query"], sentences, listing,
                                     "categorizer_calibration_model")
                if first is None or second is None:
                    out["failed"] += 1
                    return
                await asyncio.to_thread(
                    store().put_calibration, row["key"], row["query"], first["node"],
                    first["judge_model"], second["node"], second["judge_model"])
                out["compared"] += 1

        await asyncio.gather(*(one(*p) for p in picked))
        await asyncio.to_thread(store().finish_calibration,
                                f"{out['compared']} compared, {out['failed']} failed")
    except Exception as exc:  # noqa: BLE001
        log.exception("nando's comparison failed")
        out["error"] = f"{type(exc).__name__}: {exc}"
        try:
            await asyncio.to_thread(store().finish_calibration, out["error"])
        except Exception:  # noqa: BLE001
            pass
    return out


def calibration_report() -> dict:
    """The one-time comparison's result (§503): how often the nightly key
    agreed with the second, and where it did not. `ran` False before."""
    rows = store().calibration()
    exact = branch = wrong = 0
    examples = []
    for r in rows:
        v = verdict(r["first"], r["second"])
        if v == "exact":
            exact += 1
        elif v == "branch":
            branch += 1
        else:
            wrong += 1
            if len(examples) < 15:
                examples.append({"query": r["query"], "first": r["first"] or "none",
                                 "second": r["second"] or "none"})
    n = exact + branch + wrong
    run = store().calibration_run()
    return {"ran": run is not None, "outcome": (run or {}).get("outcome", ""),
            "finished": bool(run and run["finished_at"]), "compared": n, "exact": exact, "on_branch": branch,
            "wrong": wrong, "agree_rate": _rate(exact + branch, n),
            "exact_rate": _rate(exact, n),
            "first_model": rows[0]["first_model"] if rows else "",
            "second_model": rows[0]["second_model"] if rows else "",
            "disagreements": examples}


def queue_calibration() -> bool:
    """Queue the one-time comparison on Netenyahu's loop (§503). True when
    this call queued it. It runs once only however often it is queued."""
    from agents import tasks
    from config import settings

    # Keyed by the click: the run-once guarantee is the comparison's own
    # start record (`start_calibration`), so a task that found it refused
    # (Nando switched off) never leaves the button stuck.
    stamp = str(int(time.time()))
    added = tasks.store().add("categorizer", "calibrate", f"once-{stamp}",
                              {"episodes": min(int(settings.nando_calibration_episodes),
                                               CALIBRATION_MAX), "at": stamp})
    return added == tasks.ADDED


def calibrate_handler(script_for: Callable[[str], list]):
    """The background task `categorizer:calibrate` (§503), queued from
    /admin by the owner, keyed so it can be queued only once."""
    async def run(_task: str) -> None:
        log.info("nando's comparison: %s", await calibrate(script_for))
    return run


# ------------------------------------------------------------------ following the tree
def _tree_now() -> dict:
    import topics

    return {node_id: (node.parent_id or "") for node_id, node in
            topics.category_tree().nodes().items()}


def _nearest_surviving(path: str, now: dict) -> str:
    """The deepest node of a grade's old path the tree still has, or ""."""
    import topics

    for node in reversed([p.strip() for p in (path or "").split(" > ") if p.strip()]):
        if node in now or node in topics.FACETS:
            return node
    return ""


def follow_tree(now_nodes: Optional[dict] = None, at: float = 0.0) -> dict:
    """The nightly look at the tree (§387, piece N7): what was minted,
    pruned or moved since the last look, and what that changes for Nando's
    training. Never deletes a grade (every-agent.md, always 8):

    * a **pruned** node's grades are retired and point at the nearest
      ancestor still there (or nothing); they come back if the node does;
    * a **minted** node asks for a fresh grade of the episodes graded under
      its parent, which the answer key does first while their scripts are
      kept;
    * every change is a re-file task for Consuela (nando.md, always 9),
      queued until she is built;
    * a minted node with no drafted definition is listed for a person.

    The first look only remembers the tree. Never raises."""
    out = {"minted": [], "pruned": [], "moved": [], "retired": 0, "restored": 0,
           "regrade": 0, "consuela": 0, "undefined": []}
    try:
        import category_vocabulary

        from agents import tasks

        now = _tree_now() if now_nodes is None else dict(now_nodes)
        if not now:
            # A tree that could not be read is not a tree with no nodes:
            # retiring every grade on it would be a creation (§391).
            out["skipped"] = "the tree read empty"
            return out
        seen = store().tree_seen()
        if not seen:
            store().see_tree(now, at)
            out["first_look"] = True
            return out
        minted = sorted(set(now) - set(seen))
        pruned = sorted(set(seen) - set(now))
        moved = sorted(n for n in set(now) & set(seen) if now[n] != seen[n])
        out.update(minted=minted, pruned=pruned, moved=moved)
        parents_of_new = {now[n] for n in minted if now[n]}
        for label in store().labels(limit=100000):
            node, key = label["node"], label["key"]
            if label["retired_at"] and node in now:
                store().mark(key, retired_at=0, node_now="",         # it came back
                             path=_path_text(node))
                out["restored"] += 1
            elif not label["retired_at"] and node and node in pruned:
                store().mark(key, retired_at=at or time.time(),
                             node_now=_nearest_surviving(label["path"], now))
                out["retired"] += 1
            elif not label["retired_at"] and node in parents_of_new and not label["regrade"]:
                store().mark(key, regrade=1)
                out["regrade"] += 1
            if moved and node in now and not label["retired_at"]:
                path = _path_text(node)          # under a moved branch, anywhere
                if path != label["path"]:
                    store().mark(key, path=path)
        for change, nodes in (("minted", minted), ("pruned", pruned), ("moved", moved)):
            for node in nodes:
                if tasks.store().add("housekeeper", "refile", node,
                                     {"change": change, "node": node,
                                      "parent": now.get(node, seen.get(node, ""))}
                                     ) == tasks.ADDED:
                    out["consuela"] += 1
        out["undefined"] = [n for n in minted
                            if not category_vocabulary.definition(n).get("definition")]
        store().see_tree(now, at)
    except Exception as exc:  # noqa: BLE001
        log.exception("nando could not follow the tree")
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def follow_handler():
    """The background task `categorizer:follow`."""
    import asyncio

    async def run(_task: str) -> None:
        result = await asyncio.to_thread(follow_tree)
        log.info("nando followed the tree: %s", result)
    return run
