"""Sonic, the retriever: one date-ordered history packet from MongoDB (§403).

Phase 2, piece 6 (sonic.md; docs/FAM_AGENTS.md, Retriever). While Netenyahu
lets him work on an episode, Sonic replaces today's recall
(`history_archive.look`) beside the search; the result is merged exactly as
the recall's is - within `ARCHIVE_GRACE_SECONDS` of the packet, or dropped
(`research.finish_history_look`). With `AGENTS=0` or `RETRIEVER=0` nothing
here runs and the recall is today's.

**Read-only, by construction** (never 1, 2): `ReadOnlySQL` and `ReadOnlyAtlas`
have no method that writes, and every statement goes through one gate that
refuses anything but a read. On Atlas he connects with **his own read-only
database user** (`MONGODB_READ_URI`), never the writer's `MONGODB_URI`;
without it he does not run there and the recall is today's. He reads
`facts` and `live_series` only - never `intake`, Consuela's raw material
(never 4).

**The lookup is chosen, never written** (always 2, 4; never 2): C3P0's own
choice when his call named one (§375); else the embedding router - the
installed sentence model and labelled examples, decided by the owner
2026-10-30 - when the question sits close enough to one; else code's
mapping from the brief. Every lookup is one of `handoffs.LOOKUPS`, and code
builds every query: names resolved against what is stored, dates worked out
from the listener's clock and clamped to before the search's recency window
(never 6: history, never now).

**Where he looks** (always 3): the node; then its parent and children when
it holds too little; then by meaning (Atlas Vector Search, or the local
scan); then nothing - no history is better than the wrong history. Results
and values come from the live series first (§396).

**The packet** (always 5, 6): oldest first, each item dated, graded and
sourced, labelled earlier reporting by `history_archive.as_sources`; an
`ArchivePacket` for Shakespeare beside it on the episode's notes. An article
already in the plugin packet is dropped where the packets meet (never 5).

**Late or failing is no history, never a wait** (always 7): any failure falls
back to today's recall, and the grace window applies to both.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import settings

log = logging.getLogger("fam.agents.retriever")

#: Below this many items the node is "holding too little" and its family is read.
ENOUGH = 2
#: The router picks a lookup only this close to one of its examples.
ROUTER_FLOOR = 0.55
#: How far back a result or a series looks when the plan names no start.
DEFAULT_SPAN_DAYS = 365

#: The embedding router's labelled examples (decided 2026-10-30): built in
#: code, a few per lookup, for the questions whose lookup is not background.
ROUTER_EXAMPLES = {
    "result_between": (
        "how did the Eagles do against the Giants last time",
        "who won the last meeting between the two teams",
        "what was the score the last time they played",
        "their record against each other this season",
    ),
    "value_on_day": (
        "what did Apple close at on October 3",
        "the price of bitcoin on Monday",
        "where was the stock on the day of the announcement",
    ),
    "series": (
        "how has Tesla's stock done this year",
        "the team's form over the season",
        "how the price moved over the last few months",
        "their results over the last ten games",
    ),
    "corroboration": (
        "how widely was this reported",
        "did other outlets confirm it",
    ),
    "background": (
        "who is Jalen Hurts",
        "what happened in the strike last year",
        "the history of the dispute",
        "how did this start",
    ),
}

_STATS_LOCK = threading.Lock()
STATS: dict = {"runs": 0, "items": 0, "empty": 0, "fell_back": 0,
               "by_lookup": {}, "chosen_by": {}, "last_ms": None, "last_error": ""}


def _count(key: str, sub: str = "", n: int = 1) -> None:
    with _STATS_LOCK:
        if sub:
            STATS[key][sub] = STATS[key].get(sub, 0) + n
        else:
            STATS[key] = (STATS.get(key) or 0) + n


def reset() -> None:
    with _STATS_LOCK:
        STATS.update({"runs": 0, "items": 0, "empty": 0, "fell_back": 0,
                      "by_lookup": {}, "chosen_by": {}, "last_ms": None,
                      "last_error": ""})
    _READER[0] = None


# ------------------------------------------------------------------ read-only stores
class ReadOnly(Exception):
    """A statement that is not a read reached Sonic's gate."""


def _gate(sql: str) -> str:
    """The only way a statement reaches a store: a read, or refused."""
    if not re.match(r"\s*SELECT\b", sql or "", re.I) or ";" in sql.strip().rstrip(";"):
        raise ReadOnly(f"Sonic only reads: refused {sql[:40]!r}")
    return sql


class ReadOnlySQL:
    """The archive's file (or its Postgres schema), read-only. No writes."""

    kind = "sqlite read-only"

    def __init__(self, path: Optional[str] = None) -> None:
        from paths import data_path

        self.path = data_path("ARCHIVE_DB", "archive.db", path)

    def _connect(self):
        import db as store_db

        if store_db.url():
            # The archive lives in Postgres here (§243); the gate holds it to
            # reads, as `mode=ro` does for the file.
            return store_db.connect(self.path, timeout=2.0)
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=2.0)

    def _read(self, sql: str, args: tuple = ()) -> list:
        with closing(self._connect()) as db:
            return db.execute(_gate(sql), args).fetchall()

    _COLUMNS = ("id, url, title, host, grade, published, published_ts, kind, text, "
                "subject, category, figure, corroborations, node_id, node_path")

    def _fact(self, r) -> "object":
        import json

        import history_archive as ha

        return ha.Fact(id=r[0], url=r[1], title=r[2], host=r[3], grade=r[4],
                       published=r[5], kind=r[7], text=r[8], subject=r[9],
                       category=r[10], figure=json.loads(r[11] or "{}"),
                       corroborations=json.loads(r[12] or "[]"), node_id=r[13] or "",
                       node_path=json.loads(r[14] or "[]"))

    def node_facts(self, nodes: list, before: datetime, limit: int) -> list:
        """The latest facts filed under `nodes`, published before `before`."""
        if not nodes:
            return []
        marks = ",".join("?" for _ in nodes)
        rows = self._read(
            f"SELECT {self._COLUMNS} FROM facts WHERE active = 1 AND kind != 'link' "
            f"AND node_id IN ({marks}) AND published_ts > 0 AND published_ts < ? "
            "ORDER BY published_ts DESC LIMIT ?",
            (*nodes, before.timestamp(), max(1, int(limit))))
        return [self._fact(r) for r in rows]

    def subject_facts(self, subject: str, kind: str, start: datetime, end: datetime,
                      limit: int) -> list:
        rows = self._read(
            f"SELECT {self._COLUMNS} FROM facts WHERE active = 1 AND kind = ? "
            "AND instr(lower(subject), lower(?)) > 0 AND published_ts >= ? "
            "AND published_ts < ? "
            "ORDER BY published_ts DESC LIMIT ?",
            (kind, subject, start.timestamp(), end.timestamp(), max(1, int(limit))))
        return [self._fact(r) for r in rows]

    def by_meaning(self, vector: list, space: str, before: datetime, limit: int,
                   floor: float, nodes: Optional[list] = None) -> list:
        import numpy as np

        where, args = "", ()
        if nodes:
            where = f" AND node_id IN ({','.join('?' for _ in nodes)})"
            args = tuple(nodes)
        rows = self._read(
            f"SELECT {self._COLUMNS}, embedding FROM facts WHERE active = 1 "
            "AND kind != 'link' AND space = ? AND published_ts > 0 AND published_ts < ? "
            f"AND embedding IS NOT NULL{where}", (space, before.timestamp(), *args))
        if not rows:
            return []
        matrix = np.frombuffer(b"".join(r[15] for r in rows), dtype="<f4")
        matrix = matrix.reshape(len(rows), -1)
        query = np.asarray(vector, dtype="<f4")
        if matrix.shape[1] != query.shape[0]:
            return []
        scores = matrix @ query
        out = []
        for i in np.argsort(-scores):
            if float(scores[i]) < floor or len(out) >= limit:
                break
            fact = self._fact(rows[int(i)][:15])
            fact.score = round(float(scores[i]), 4)
            out.append(fact)
        return out

    def readings(self, subject: str, metric: str, start: datetime, end: datetime,
                 limit: int) -> list:
        import history_archive as ha

        rows = self._read(
            "SELECT id, subject, metric, as_of, value, unit, text, status, source, "
            "domain, node_id FROM live_series WHERE instr(lower(subject), lower(?)) > 0 "
            "AND metric = ? AND as_of_ts >= ? AND as_of_ts < ? "
            "ORDER BY as_of_ts DESC LIMIT ?",
            (subject, metric, start.timestamp(), end.timestamp(), max(1, int(limit))))
        return [ha.Reading(id=r[0], subject=r[1], metric=r[2],
                           as_of=datetime.fromisoformat(r[3]), value=r[4], unit=r[5],
                           text=r[6], status=r[7], source=r[8], domain=r[9],
                           node_id=r[10]) for r in rows]


class ReadOnlyAtlas:
    """The archive on Atlas, through Sonic's own read-only database user.

    `database` may be handed in (the tests pass a fake); otherwise a client
    is made from `MONGODB_READ_URI`. Reads only: `find` and `aggregate`."""

    kind = "atlas read-only user"

    def __init__(self, uri: str = "", database=None) -> None:
        if database is None:
            from pymongo import MongoClient

            timeout_ms = int(max(0.1, float(settings.archive_timeout_seconds)) * 1000)
            client = MongoClient(uri, appname="fam-sonic", tz_aware=True,
                                 maxPoolSize=10, serverSelectionTimeoutMS=timeout_ms,
                                 connectTimeoutMS=timeout_ms, socketTimeoutMS=timeout_ms)
            database = client[settings.archive_db_name]
        self.facts_c = database["facts"]
        self.series_c = database["live_series"]

    @staticmethod
    def _fact(doc: dict):
        import history_archive as ha

        published = doc.get("published")
        return ha.Fact(id=str(doc.get("_id", "")), url=doc.get("url", ""),
                       title=doc.get("title", ""), host=doc.get("host", ""),
                       grade=doc.get("grade", ""),
                       published=published.strftime("%Y-%m-%d")
                       if isinstance(published, datetime) else "",
                       kind=doc.get("kind", "sentence"), text=doc.get("text", ""),
                       subject=doc.get("subject", ""), category=doc.get("category", ""),
                       figure=dict(doc.get("figure") or {}),
                       corroborations=list(doc.get("corroborations") or []),
                       node_id=doc.get("node_id", ""),
                       node_path=list(doc.get("node_path") or []),
                       score=float(doc.get("score", 0.0) or 0.0))

    _NO_VECTOR = {"embedding": 0}

    def node_facts(self, nodes: list, before: datetime, limit: int) -> list:
        if not nodes:
            return []
        return [self._fact(d) for d in self.facts_c.find(
            {"active": True, "kind": {"$ne": "link"}, "node_id": {"$in": list(nodes)},
             "published": {"$lt": before}}, self._NO_VECTOR
        ).sort("published", -1).limit(max(1, int(limit)))]

    def subject_facts(self, subject: str, kind: str, start: datetime, end: datetime,
                      limit: int) -> list:
        return [self._fact(d) for d in self.facts_c.find(
            {"active": True, "kind": kind,
             "subject": {"$regex": re.escape(subject), "$options": "i"},
             "published": {"$gte": start, "$lt": end}}, self._NO_VECTOR
        ).sort("published", -1).limit(max(1, int(limit)))]

    def by_meaning(self, vector: list, space: str, before: datetime, limit: int,
                   floor: float, nodes: Optional[list] = None) -> list:
        import history_archive as ha

        where = {"active": True, "space": space, "published": {"$lt": before}}
        if nodes:
            where["node_id"] = {"$in": list(nodes)}
        out = []
        for doc in self.facts_c.aggregate([
                {"$vectorSearch": {"index": ha.VECTOR_INDEX, "path": "embedding",
                                   "queryVector": [float(x) for x in vector],
                                   "numCandidates": max(50, limit * 20),
                                   "limit": max(1, limit * 4), "filter": where}},
                {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
                {"$project": {"embedding": 0}}]):
            similarity = 2.0 * float(doc.get("score", 0.0)) - 1.0
            if similarity < floor or doc.get("kind") == "link":
                continue
            doc["score"] = similarity
            out.append(self._fact(doc))
            if len(out) >= limit:
                break
        return out

    def readings(self, subject: str, metric: str, start: datetime, end: datetime,
                 limit: int) -> list:
        import history_archive as ha

        return [ha.Reading(id=str(d.get("_id", "")), subject=d.get("subject", ""),
                           metric=d.get("metric", ""), as_of=d.get("as_of"),
                           value=d.get("value"), unit=d.get("unit", ""),
                           text=d.get("text", ""), status=d.get("status", ""),
                           source=d.get("source", ""), domain=d.get("domain", ""),
                           node_id=d.get("node_id", ""))
                for d in self.series_c.find(
                    {"subject": {"$regex": re.escape(subject), "$options": "i"},
                     "metric": metric, "as_of": {"$gte": start, "$lt": end}}
                ).sort("as_of", -1).limit(max(1, int(limit)))]


_READER: list = [None]
_READER_LOCK = threading.Lock()


def read_uri() -> str:
    import credentials

    return (credentials.active("MONGODB_READ_URI") or "").strip()


def reader():
    """Sonic's read-only store, or None when he has none here (Atlas without
    his read-only user). Never the writer's connection."""
    import history_archive as ha

    with _READER_LOCK:
        if _READER[0] is None:
            if not ha.enabled():
                return None
            if (settings.archive_backend or "").strip().lower() == "atlas":
                if not read_uri():
                    return None
                try:
                    _READER[0] = ReadOnlyAtlas(read_uri())
                except Exception as exc:  # noqa: BLE001
                    log.warning("sonic could not open his read-only connection: %s", exc)
                    return None
            else:
                _READER[0] = ReadOnlySQL()
        return _READER[0]


def use(store) -> None:
    """Put a read-only store in place (tests)."""
    with _READER_LOCK:
        _READER[0] = store


# ------------------------------------------------------------------ choosing the lookup
_ROUTER: list = [None]


def _router():
    """[(lookup, vector)] for every example, embedded once. [] without the model."""
    if _ROUTER[0] is None:
        import history_archive as ha

        rows = []
        for lookup, phrases in ROUTER_EXAMPLES.items():
            for phrase in phrases:
                vector, _space = ha._embed(phrase)
                if vector is None:
                    _ROUTER[0] = []
                    return []
                rows.append((lookup, list(vector)))
        _ROUTER[0] = rows
    return _ROUTER[0]


def route(text: str) -> tuple:
    """(lookup, similarity) of the nearest example, or ("", 0.0)."""
    import history_archive as ha

    examples = _router()
    if not examples or not (text or "").strip():
        return "", 0.0
    vector, _space = ha._embed(text)
    if vector is None:
        return "", 0.0
    best, score = "", 0.0
    for lookup, example in examples:
        similarity = float(sum(x * y for x, y in zip(vector, example)))
        if similarity > score:
            best, score = lookup, similarity
    return best, score


def code_mapping(brief) -> str:
    """Code's lookup from the brief alone: the fallback under the router."""
    domain = str(getattr(brief, "live_domain", "") or "")
    if domain == "markets":
        return "series"
    if domain == "sports" and getattr(brief, "outcome_dependent", False):
        return "result_between"
    return "background"


def choose(plan, brief, text: str) -> tuple:
    """(lookup, chosen_by): C3P0's named lookup, the router's, or code's."""
    from agents.handoffs import LOOKUPS

    request = getattr(plan, "archive", None)
    named = getattr(request, "lookup", "") if request is not None else ""
    if named in LOOKUPS and getattr(brief, "contextualized", None):
        return named, "c3p0"
    lookup, score = route(text)
    if lookup in LOOKUPS and score >= ROUTER_FLOOR:
        return lookup, "router"
    return code_mapping(brief), "code"


# ------------------------------------------------------------------ fetching
def _family(node: str) -> list:
    """The node's parent and children, from Nando's tree."""
    if not node:
        return []
    try:
        import topics

        tree = topics.category_tree()
        here = tree.get(node)
        out = [here.parent_id] if here is not None and here.parent_id else []
        out += [n for n, item in tree.nodes().items() if item.parent_id == node]
        return out
    except Exception:  # noqa: BLE001
        return []


def _period(request, before: datetime) -> tuple:
    """(start, end) from the plan, clamped to before the search's window;
    (None, None) when the period lies wholly after it - a future period is
    dropped, never looked up."""
    def day(text):
        try:
            return datetime.fromisoformat(str(text)).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
    start = day(getattr(request, "start", "")) if request is not None else None
    end = day(getattr(request, "end", "")) if request is not None else None
    end = min(end + timedelta(days=1), before) if end else before
    start = start or end - timedelta(days=DEFAULT_SPAN_DAYS)
    if start >= end:
        return None, None
    return start, end


_METRIC_OF = {"sports": "score", "markets": "price", "elections": "count"}


def _as_fact(reading):
    """A reading as the history packet's entry: the provider's line, dated."""
    import history_archive as ha

    record = {"domain": reading.domain, "status": reading.status,
              "source": reading.source, "entity": reading.subject,
              "as_of": reading.as_of.isoformat(), "lines": [reading.text],
              "kind": reading.metric, "id": reading.id}
    return ha._live_fact(record, reading.subject, "", reading.node_id)


def fetch(store, lookup: str, plan, brief, text: str, before: datetime) -> list:
    """The facts for one lookup, newest first, before `before`. Blocking."""
    import history_archive as ha

    limit = max(1, int(settings.archive_recall_limit))
    floor = float(settings.archive_min_similarity)
    node = str(getattr(plan, "node_id", "") or "")
    subject = str(getattr(plan, "entity", "") or getattr(brief, "live_entity", "")
                  or getattr(brief, "subject", "") or "").strip()
    request = getattr(plan, "archive", None)
    found: list = []

    if lookup in ("result_between", "value_on_day", "series") and subject:
        start, end = _period(request, before)
        if start is not None:
            metric = _METRIC_OF.get(str(getattr(brief, "live_domain", "") or ""), "score")
            if lookup == "value_on_day" and request is not None and request.start:
                end = min(start + timedelta(days=1), end)
            span = limit * (2 if lookup == "series" else 1)
            found = [_as_fact(r) for r in store.readings(subject, metric, start, end, span)]
            found += store.subject_facts(subject, "live", start, end, limit)
        if found:
            return found[:limit * 2]

    def meaning(nodes=None):
        vector, space = ha._embed(text)
        if vector is None:
            return []
        return store.by_meaning(list(vector), space, before, limit, floor, nodes)

    if lookup == "by_meaning":
        found = meaning([node] + _family(node)) if node else []
        return found or meaning()
    # background, corroboration, and a result or value with none stored:
    # the node, its family, then by meaning, then nothing (always 3).
    if node:
        found = store.node_facts([node], before, limit)
        if len(found) < ENOUGH:
            found += store.node_facts(_family(node), before, limit - len(found))
    if len(found) < ENOUGH:
        seen = {f.id for f in found}
        found += [f for f in meaning() if f.id not in seen][:limit - len(found)]
    if lookup == "corroboration":
        found.sort(key=lambda f: -len(f.corroborations or []))
    return found[:limit]


def packet(facts: list):
    """The `ArchivePacket` for Shakespeare: oldest first, every item dated."""
    from agents.handoffs import ArchiveItem, ArchivePacket, Source

    kinds = {"figure": "figure", "quote": "quote", "live": "result"}
    items = []
    for fact in facts:
        if not fact.published:
            continue
        items.append(ArchiveItem(
            date=fact.published, node_id=fact.node_id or "",
            kind=kinds.get(fact.kind, "fact"),
            source=Source(url=fact.url, published=fact.published, grade=fact.grade,
                          provider=fact.host if fact.url.startswith("live:") else "",
                          sentences=(fact.text,))))
    return ArchivePacket(items=tuple(sorted(items, key=lambda i: i.date)))


def retrieve(plan, brief, text: str, notes=None, now: Optional[datetime] = None) -> list:
    """Sonic's whole job for one episode: the facts, oldest first. Blocking;
    never raises - a failure is today's recall."""
    import history_archive as ha
    import research

    started = time.perf_counter()
    window = research.search_window(brief)
    try:
        store = reader()
        if store is None:
            raise RuntimeError("no read-only connection here")
        now = now or datetime.now(timezone.utc)
        before = now - timedelta(days=max(1, int(window or 0)))
        lookup, chosen_by = choose(plan, brief, text)
        facts = fetch(store, lookup, plan, brief, text, before)
        facts.sort(key=lambda f: (f.published or "", f.url))
        _count("runs")
        _count("items", n=len(facts))
        _count("by_lookup", lookup)
        _count("chosen_by", chosen_by)
        if not facts:
            _count("empty")
        if notes is not None:
            try:
                notes.archive_packet = packet(facts)
                notes.archive_lookup = {"lookup": lookup, "chosen_by": chosen_by,
                                        "items": len(facts)}
            except Exception:  # noqa: BLE001 - the packet never costs the episode
                pass
        with _STATS_LOCK:
            STATS["last_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return facts
    except Exception as exc:  # noqa: BLE001 - today's recall is the fallback
        _count("fell_back")
        with _STATS_LOCK:
            STATS["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
        log.info("sonic fell back to the recall: %s", exc)
        return ha.recall(text, window)


def look(plan, brief, text: str, notes=None):
    """Start Sonic beside the search: a future, or None when this episode is
    not one the archive may add to. Never raises."""
    try:
        import history_archive as ha

        if not ha.enabled() or ha.recall_wanted(brief):
            return None
        import asyncio

        return asyncio.get_running_loop().run_in_executor(
            ha.RECALL_EXECUTOR, retrieve, plan, brief, text, notes)
    except Exception as exc:  # noqa: BLE001
        log.warning("sonic could not start: %s", exc)
        return None


def report() -> dict:
    """For `/api/health`. Opens nothing."""
    backend = (settings.archive_backend or "").strip().lower()
    connection = ("atlas read-only user" if backend == "atlas" and read_uri()
                  else "none: MONGODB_READ_URI is not set" if backend == "atlas"
                  else "sqlite read-only")
    with _STATS_LOCK:
        stats = {k: (dict(v) if isinstance(v, dict) else v) for k, v in STATS.items()}
    return {"on": bool(settings.agents and settings.retriever),
            "connection": connection, **stats}
