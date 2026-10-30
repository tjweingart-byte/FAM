"""The history archive: what FAM has read, kept for the episodes after (§308).

Every packet used to be read once by the writer and thrown away, so an
episode knew only what was published inside its recency window. This module
keeps what the research found, so a later episode can be handed a second,
clearly labelled block of **earlier reporting** beside the fresh search.

Three collections, in MongoDB Atlas in production (`ARCHIVE_BACKEND=atlas`)
and in one SQLite file everywhere else (tests, staging, a fresh clone),
behind the same interface:

* **intake** - one record per article URL that entered a packet: its title,
  outlet, grade, publish date and the passages the search returned, plus the
  subjects and categories of the episodes that used it. This is the raw
  material **Consuela, the housekeeper** agent (`docs/FAM_AGENTS.md`) will
  digest. That agent is not built yet, so intake is kept until it is:
  nothing is digested, and nothing is thrown away except past
  `ARCHIVE_INTAKE_MAX` records, oldest first.
* **episode_sources** - each written episode's articles (URL, title, outlet,
  grade, date), keyed by `cache.episode_id`, for a sources view on the
  episode screen. Provenance, never identity: no listener is stored.
* **facts** - the key sentences and figures the agent will keep, one record
  each, with the vector the recall searches. Empty until the agent exists.
  `add_fact` is how it (and the tests) will write one.

**The recall** (`look` / `add_to_packet`) runs beside the web search, like
the reference look (§278): it is started first, given `ARCHIVE_GRACE_SECONDS`
once the packet is in, and dropped if it is late, so it never holds the first
word longer than that. It adds facts only:

* published **before** the packet's recency window (what is inside it is the
  search's job), and never without a publish date;
* **only beside web sources, for now** (the owner, §316): an empty search
  gets no archive. That is today's default, not a rule - whether the
  archive may answer an empty search is the contextualizer's decision,
  made when the agents are built (`docs/FAM_AGENTS.md`, agent 4), and
  nothing here forbids it;
* on every kind of question but weather and a town's news - sports,
  markets and outcome-dependent questions included (§313, the owner's
  direction: scores and prices are the archive's most valuable material).
  An old figure cannot pass for today's: `ScoreGuard` lets a score through
  only from the scoreboard or in a sentence about the past, and
  `FigureGuard` lets an archived figure vouch only for a sentence that
  says when it was true;
* labelled as earlier reporting, dated, graded, never a hostname, and never a
  current state ([recency-credibility], [started-not-finished]).

**Nothing here is on a listener's path except the recall's bounded wait.**
Intake and links are submitted to this module's own two threads and never
awaited. Every failure is counted, reported on `/api/health` > `archive` and
`/admin`'s errors, and backs the backend off for `FAILURE_BACKOFF_SECONDS`
so an unreachable Atlas costs each episode nothing.

**Private searches and attached documents never reach the archive**
(`after_episode` is called only for an episode that was cached and
shareable, and refuses the private origin).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import db as store_db
from config import settings
from paths import data_path

log = logging.getLogger("fam.archive")

#: The two backends. `sqlite` is the default: it needs nothing installed and
#: nothing configured, which is what tests, staging and a fresh clone have.
BACKENDS = ("sqlite", "atlas")
#: The width of the vectors the recall compares. all-MiniLM-L6-v2, the
#: model the Dockerfile installs (`embeddings.semantic_encoder`). The Atlas
#: vector index is created with exactly this many dimensions.
DIMS = 384
#: Atlas's vector index, by name (`tools/archive_setup.py` creates it).
VECTOR_INDEX = "facts_vector"
#: The Atlas vector index. `active`, `published` and `space` are filters so
#: superseded facts, recent ones and vectors from another model are excluded
#: inside the index rather than after it. Scalar quantisation keeps a quarter
#: of each vector in memory - what lets years of facts sit on a small tier.
VECTOR_INDEX_DEFINITION = {
    "fields": [
        {"type": "vector", "path": "embedding", "numDimensions": DIMS,
         "similarity": "cosine", "quantization": "scalar"},
        {"type": "filter", "path": "active"},
        {"type": "filter", "path": "published"},
        {"type": "filter", "path": "space"},
    ],
}
#: After a failed call, nothing more is sent to the backend for this long.
FAILURE_BACKOFF_SECONDS = 60.0
#: How many intake writes between two checks of the intake cap.
PRUNE_EVERY = 500
#: Most passages kept per article; more only repeats the search's choice.
MAX_PASSAGES = 8
#: Background writes: intake and links, never awaited by anybody.
EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="archive")
#: The recall runs in its own pool, so a queue of writes never delays it.
RECALL_EXECUTOR = ThreadPoolExecutor(max_workers=2,
                                     thread_name_prefix="archive-recall")
#: The private search's origin (`cache.PRIVATE_ORIGIN`), never archived.
PRIVATE_ORIGIN = "private"


# --------------------------------------------------------------------------
# What is kept
# --------------------------------------------------------------------------
@dataclass
class Finding:
    """One article that entered a packet, as the archive keeps it."""

    url: str
    title: str = ""
    host: str = ""
    #: `research.credibility`: primary / established / unverified.
    grade: str = ""
    #: `YYYY-MM-DD`, or "" when the search did not know it.
    published: str = ""
    passages: tuple = ()
    #: Which search supplied it - "perplexity", "parallel" or "gdelt" (§358).
    #: Their terms differ on keeping what they return (docs/PROVIDER_TERMS.md),
    #: so the archive keeps each provider's passages apart and can drop one
    #: provider's words without the other's. "" for a caller that does not say.
    provider: str = ""

    def as_link(self) -> dict:
        return {"url": self.url, "title": self.title, "host": self.host,
                "grade": self.grade, "published": self.published}


#: The searches whose passages the archive keeps apart (§358).
PROVIDERS = ("perplexity", "parallel", "gdelt")


@dataclass
class Fact:
    """One kept sentence or figure (the agent's output; §307)."""

    url: str
    text: str
    title: str = ""
    host: str = ""
    grade: str = ""
    published: str = ""
    #: "sentence" or "figure".
    kind: str = "sentence"
    subject: str = ""
    category: str = ""
    #: For a figure: metric, value, unit, period, status (outcome/forecast).
    figure: dict = field(default_factory=dict)
    #: Articles that repeated it: url, host, grade, published (§307).
    corroborations: list = field(default_factory=list)
    id: str = ""
    #: Cosine similarity to the recall's question, when it came from one.
    score: float = 0.0


def url_id(url: str) -> str:
    return hashlib.sha256((url or "").strip().encode("utf-8")).hexdigest()[:32]


def findings_from(results, packet_sources: int, highlights_per_source: int,
                  provider: str = "") -> list:
    """The articles a packet was built from, in the packet's own order.

    Called wherever `research.build_packet` is, with the same arguments, so
    only what the writer actually read is archived. `provider` is the search
    that returned `results` (§358)."""
    import research

    found = []
    for result in list(results)[:max(0, int(packet_sources))]:
        url = str(getattr(result, "url", "") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        when = research.published_at(result)
        highlights = [str(h).strip() for h in
                      list(getattr(result, "highlights", None) or [])
                      [:max(0, int(highlights_per_source))] if str(h).strip()]
        found.append(Finding(
            url=url, title=str(getattr(result, "title", "") or "").strip(),
            host=research.host_of(result), grade=research.credibility(result),
            published=when.strftime("%Y-%m-%d") if when else "",
            passages=tuple(highlights), provider=str(provider or "")))
    return found


def _day(stamp: str) -> Optional[datetime]:
    try:
        return datetime.strptime(stamp, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# The SQLite backend: tests, staging, a fresh clone
# --------------------------------------------------------------------------
class SQLiteBackend:
    """The archive in one file (`ARCHIVE_DB`). The recall is an exact scan."""

    name = "sqlite"

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("ARCHIVE_DB", "archive.db", path)
        self._lock = threading.Lock()
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS intake ("
                       " id TEXT PRIMARY KEY, url TEXT NOT NULL,"
                       " title TEXT NOT NULL, host TEXT NOT NULL,"
                       " grade TEXT NOT NULL, published TEXT NOT NULL,"
                       " passages TEXT NOT NULL, subjects TEXT NOT NULL,"
                       " categories TEXT NOT NULL, first_seen REAL NOT NULL,"
                       " last_seen REAL NOT NULL, seen INTEGER NOT NULL,"
                       " digested INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE INDEX IF NOT EXISTS intake_first_seen"
                       " ON intake(first_seen)")
            # Each provider's passages apart (§358): {"perplexity": [...]}.
            # `passages` stays the union every reader already reads. Rows
            # written before this have `{}` - their provider was never known.
            try:
                with closing(self._connect()) as alter, alter:
                    alter.execute("ALTER TABLE intake ADD COLUMN by_provider "
                                  "TEXT NOT NULL DEFAULT '{}'")
            except Exception:  # noqa: BLE001 - already there
                pass
            db.execute("CREATE TABLE IF NOT EXISTS episode_sources ("
                       " episode TEXT PRIMARY KEY, key TEXT NOT NULL,"
                       " sourced_at REAL NOT NULL, links TEXT NOT NULL,"
                       " created REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS facts ("
                       " id TEXT PRIMARY KEY, url TEXT NOT NULL,"
                       " title TEXT NOT NULL, host TEXT NOT NULL,"
                       " grade TEXT NOT NULL, published TEXT NOT NULL,"
                       " published_ts REAL NOT NULL, kind TEXT NOT NULL,"
                       " text TEXT NOT NULL, subject TEXT NOT NULL,"
                       " category TEXT NOT NULL, figure TEXT NOT NULL,"
                       " corroborations TEXT NOT NULL,"
                       " active INTEGER NOT NULL DEFAULT 1,"
                       " superseded_by TEXT NOT NULL DEFAULT '',"
                       " space TEXT NOT NULL, embedding BLOB,"
                       " created REAL NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS facts_active"
                       " ON facts(active, published_ts)")

    def _connect(self) -> sqlite3.Connection:
        return store_db.connect(self.path, timeout=10)

    def intake(self, findings: list, subject: str, category: str,
               now: float) -> int:
        written = 0
        with self._lock, closing(self._connect()) as db, db:
            for f in findings:
                row = db.execute("SELECT passages, subjects, categories, "
                                 "by_provider FROM intake WHERE id = ?",
                                 (url_id(f.url),)).fetchone()
                if row is None:
                    db.execute(
                        "INSERT INTO intake (id, url, title, host, grade, "
                        "published, passages, subjects, categories, first_seen, "
                        "last_seen, seen, digested, by_provider) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)",
                        (url_id(f.url), f.url, f.title, f.host, f.grade,
                         f.published, json.dumps(list(f.passages)[:MAX_PASSAGES]),
                         json.dumps([subject] if subject else []),
                         json.dumps([category] if category else []), now, now,
                         json.dumps(_by_provider({}, f))))
                else:
                    passages = _union(json.loads(row[0]), f.passages, MAX_PASSAGES)
                    subjects = _union(json.loads(row[1]), [subject] if subject else [])
                    categories = _union(json.loads(row[2]),
                                        [category] if category else [])
                    by_provider = _by_provider(json.loads(row[3] or "{}"), f)
                    db.execute(
                        "UPDATE intake SET passages = ?, subjects = ?, "
                        "categories = ?, by_provider = ?, last_seen = ?, "
                        "seen = seen + 1 WHERE id = ?",
                        (json.dumps(passages), json.dumps(subjects),
                         json.dumps(categories), json.dumps(by_provider), now,
                         url_id(f.url)))
                written += 1
        return written

    def prune_intake(self, keep: int) -> int:
        with self._lock, closing(self._connect()) as db, db:
            total = db.execute("SELECT COUNT(*) FROM intake").fetchone()[0]
            over = int(total) - max(0, int(keep))
            if over <= 0:
                return 0
            db.execute("DELETE FROM intake WHERE id IN (SELECT id FROM intake "
                       "ORDER BY first_seen LIMIT ?)", (over,))
            return over

    def link_episode(self, episode: str, key: str, sourced_at: float,
                     links: list, now: float) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT INTO episode_sources (episode, key, sourced_at, "
                       "links, created) VALUES (?, ?, ?, ?, ?) "
                       "ON CONFLICT(episode) DO UPDATE SET links = excluded.links",
                       (episode, key, float(sourced_at or 0), json.dumps(links), now))

    def episode_links(self, episode: str) -> list:
        with closing(self._connect()) as db:
            row = db.execute("SELECT links FROM episode_sources WHERE episode = ?",
                             (episode,)).fetchone()
        return json.loads(row[0]) if row else []

    def add_fact(self, fact: Fact, vector: Optional[list], space: str,
                 now: float) -> str:
        import embeddings

        when = _day(fact.published)
        fid = fact.id or url_id(fact.url + "\n" + fact.text)
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO facts (id, url, title, host, grade, published, "
                "published_ts, kind, text, subject, category, figure, "
                "corroborations, active, superseded_by, space, embedding, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '', ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET text = excluded.text, "
                "corroborations = excluded.corroborations",
                (fid, fact.url, fact.title, fact.host, fact.grade, fact.published,
                 when.timestamp() if when else 0.0, fact.kind, fact.text,
                 fact.subject, fact.category, json.dumps(fact.figure),
                 json.dumps(fact.corroborations), space,
                 embeddings.pack(vector) if vector else None, now))
        return fid

    def recall(self, vector: list, space: str, before: datetime, limit: int,
               min_similarity: float, exclude_urls: set) -> list:
        import numpy as np

        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT id, url, title, host, grade, published, kind, text, "
                "subject, category, figure, corroborations, embedding FROM facts "
                "WHERE active = 1 AND space = ? AND published_ts > 0 "
                "AND published_ts < ? AND embedding IS NOT NULL",
                (space, before.timestamp())).fetchall()
        if not rows:
            return []
        matrix = np.frombuffer(b"".join(r[12] for r in rows), dtype="<f4")
        matrix = matrix.reshape(len(rows), -1)
        query = np.asarray(vector, dtype="<f4")
        if matrix.shape[1] != query.shape[0]:
            return []
        scores = matrix @ query
        out = []
        for i in np.argsort(-scores):
            if float(scores[i]) < min_similarity or len(out) >= limit:
                break
            r = rows[int(i)]
            if r[1] in exclude_urls:
                continue
            out.append(Fact(id=r[0], url=r[1], title=r[2], host=r[3], grade=r[4],
                            published=r[5], kind=r[6], text=r[7], subject=r[8],
                            category=r[9], figure=json.loads(r[10] or "{}"),
                            corroborations=json.loads(r[11] or "[]"),
                            score=round(float(scores[i]), 4)))
        return out

    def counts(self) -> dict:
        with closing(self._connect()) as db:
            return {
                "intake": db.execute("SELECT COUNT(*) FROM intake").fetchone()[0],
                "undigested": db.execute("SELECT COUNT(*) FROM intake "
                                         "WHERE digested = 0").fetchone()[0],
                "episodes": db.execute("SELECT COUNT(*) FROM "
                                       "episode_sources").fetchone()[0],
                "facts": db.execute("SELECT COUNT(*) FROM facts "
                                    "WHERE active = 1").fetchone()[0],
                # Articles holding each provider's passages (§358); "unknown"
                # is what was written before the provider was recorded.
                "intake_by_provider": {
                    **{p: db.execute("SELECT COUNT(*) FROM intake WHERE "
                                     "by_provider LIKE ?", (f'%"{p}"%',)).fetchone()[0]
                       for p in PROVIDERS},
                    "unknown": db.execute("SELECT COUNT(*) FROM intake WHERE "
                                          "by_provider = '{}'").fetchone()[0]},
            }


def _by_provider(held: dict, finding: "Finding") -> dict:
    """`held` with `finding`'s passages added under its provider (§358)."""
    held = dict(held or {})
    if finding.provider and finding.passages:
        held[finding.provider] = _union(held.get(finding.provider, []),
                                        finding.passages, MAX_PASSAGES)
    return held


def _union(existing, more, limit: int = 0) -> list:
    out = list(existing or [])
    for item in more or ():
        if item and item not in out:
            out.append(item)
    return out[:limit] if limit else out


# --------------------------------------------------------------------------
# The Atlas backend: production
# --------------------------------------------------------------------------
class AtlasBackend:
    """The archive in MongoDB Atlas. The recall is Atlas Vector Search.

    `database` may be handed in (the tests pass a fake with the three
    collections); otherwise a client is made from `MONGODB_URI`."""

    name = "atlas"

    def __init__(self, uri: str = "", database: Any = None) -> None:
        if database is None:
            from pymongo import MongoClient

            timeout_ms = int(max(0.1, float(settings.archive_timeout_seconds)) * 1000)
            self.client = MongoClient(
                uri, appname="fam", tz_aware=True, retryWrites=True,
                maxPoolSize=10, serverSelectionTimeoutMS=timeout_ms,
                connectTimeoutMS=timeout_ms, socketTimeoutMS=timeout_ms * 2)
            database = self.client[settings.archive_db_name]
        else:
            self.client = None
        self.db = database
        self.intake_c = database["intake"]
        self.episodes_c = database["episode_sources"]
        self.facts_c = database["facts"]
        self._indexed = False

    def ensure_indexes(self) -> None:
        """The ordinary indexes. Idempotent; the vector index is
        `ensure_vector_index`, which only `tools/archive_setup.py` calls."""
        if self._indexed:
            return
        self.intake_c.create_index("first_seen")
        self.intake_c.create_index("digested")
        self.facts_c.create_index("url")
        self.facts_c.create_index([("active", 1), ("published", 1)])
        self._indexed = True

    def vector_index_status(self) -> str:
        """"ready", "building", "missing", or the index's own status word."""
        for index in self.facts_c.list_search_indexes(VECTOR_INDEX):
            if index.get("queryable"):
                return "ready"
            return str(index.get("status", "building")).lower()
        return "missing"

    def ensure_vector_index(self) -> str:
        status = self.vector_index_status()
        if status == "missing":
            from pymongo.operations import SearchIndexModel

            self.facts_c.create_search_index(SearchIndexModel(
                definition=VECTOR_INDEX_DEFINITION, name=VECTOR_INDEX,
                type="vectorSearch"))
            return "building"
        return status

    def intake(self, findings: list, subject: str, category: str,
               now: float) -> int:
        self.ensure_indexes()
        seen = datetime.fromtimestamp(now, timezone.utc)
        written = 0
        for f in findings:
            update: dict = {
                "$setOnInsert": {"url": f.url, "title": f.title, "host": f.host,
                                 "grade": f.grade, "published": f.published,
                                 "first_seen": seen, "digested": False},
                "$set": {"last_seen": seen},
                "$inc": {"seen": 1},
            }
            add: dict = {}
            if f.passages:
                add["passages"] = {"$each": list(f.passages)[:MAX_PASSAGES]}
                if f.provider:
                    # Each provider's passages apart (§358); see SQLite's.
                    add[f"by_provider.{f.provider}"] = {
                        "$each": list(f.passages)[:MAX_PASSAGES]}
            if subject:
                add["subjects"] = subject
            if category:
                add["categories"] = category
            if add:
                update["$addToSet"] = add
            self.intake_c.update_one({"_id": url_id(f.url)}, update, upsert=True)
            written += 1
        return written

    def prune_intake(self, keep: int) -> int:
        total = self.intake_c.estimated_document_count()
        over = int(total) - max(0, int(keep))
        if over <= 0:
            return 0
        oldest = [d["_id"] for d in self.intake_c.find(
            {}, {"_id": 1}).sort("first_seen", 1).limit(over)]
        if oldest:
            self.intake_c.delete_many({"_id": {"$in": oldest}})
        return len(oldest)

    def link_episode(self, episode: str, key: str, sourced_at: float,
                     links: list, now: float) -> None:
        self.episodes_c.replace_one(
            {"_id": episode},
            {"_id": episode, "key": key, "sourced_at": float(sourced_at or 0),
             "links": links, "created": datetime.fromtimestamp(now, timezone.utc)},
            upsert=True)

    def episode_links(self, episode: str) -> list:
        doc = self.episodes_c.find_one({"_id": episode})
        return list(doc.get("links", [])) if doc else []

    def add_fact(self, fact: Fact, vector: Optional[list], space: str,
                 now: float) -> str:
        self.ensure_indexes()
        fid = fact.id or url_id(fact.url + "\n" + fact.text)
        doc = {"url": fact.url, "title": fact.title, "host": fact.host,
               "grade": fact.grade, "published": _day(fact.published),
               "kind": fact.kind, "text": fact.text, "subject": fact.subject,
               "category": fact.category, "figure": fact.figure,
               "corroborations": fact.corroborations, "active": True,
               "superseded_by": "", "space": space,
               "created": datetime.fromtimestamp(now, timezone.utc)}
        if vector:
            doc["embedding"] = [float(x) for x in vector]
        self.facts_c.replace_one({"_id": fid}, {"_id": fid, **doc}, upsert=True)
        return fid

    def recall(self, vector: list, space: str, before: datetime, limit: int,
               min_similarity: float, exclude_urls: set) -> list:
        pipeline = [
            {"$vectorSearch": {
                "index": VECTOR_INDEX, "path": "embedding",
                "queryVector": [float(x) for x in vector],
                "numCandidates": max(50, limit * 20),
                "limit": max(1, limit * 4),
                "filter": {"active": True, "space": space,
                           "published": {"$lt": before}},
            }},
            {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
            {"$project": {"embedding": 0}},
        ]
        out = []
        for doc in self.facts_c.aggregate(pipeline):
            # Atlas scores cosine as (1 + cosine) / 2.
            similarity = 2.0 * float(doc.get("score", 0.0)) - 1.0
            if similarity < min_similarity:
                continue
            if doc.get("url") in exclude_urls:
                continue
            published = doc.get("published")
            out.append(Fact(
                id=str(doc.get("_id", "")), url=doc.get("url", ""),
                title=doc.get("title", ""), host=doc.get("host", ""),
                grade=doc.get("grade", ""),
                published=published.strftime("%Y-%m-%d")
                if isinstance(published, datetime) else "",
                kind=doc.get("kind", "sentence"), text=doc.get("text", ""),
                subject=doc.get("subject", ""), category=doc.get("category", ""),
                figure=dict(doc.get("figure") or {}),
                corroborations=list(doc.get("corroborations") or []),
                score=round(similarity, 4)))
            if len(out) >= limit:
                break
        return out

    def counts(self) -> dict:
        return {
            "intake": self.intake_c.estimated_document_count(),
            "undigested": self.intake_c.count_documents({"digested": False}),
            "episodes": self.episodes_c.estimated_document_count(),
            "facts": self.facts_c.count_documents({"active": True}),
            # As SQLite's (§358).
            "intake_by_provider": {
                **{p: self.intake_c.count_documents(
                    {f"by_provider.{p}": {"$exists": True}}) for p in PROVIDERS},
                "unknown": self.intake_c.count_documents(
                    {"by_provider": {"$exists": False}})},
        }

    def ping(self) -> None:
        self.db.command("ping")


# --------------------------------------------------------------------------
# Which backend, and whether it may be used
# --------------------------------------------------------------------------
_BACKEND: list = [None]
_BACKEND_LOCK = threading.Lock()
#: What has happened in this process, for `/api/health`.
STATS: dict = {"intake_articles": 0, "episodes_linked": 0, "recalls": 0,
               "recalled_facts": 0, "recall_late": 0, "errors": 0,
               "last_error": "", "last_recall_ms": None, "live_facts": 0,
               "live_skipped": 0, "swept_facts": 0, "swept_skipped": 0}
_STATS_LOCK = threading.Lock()
_BACKOFF_UNTIL = [0.0]
_SINCE_PRUNE = [0]


def _uri() -> str:
    import credentials

    return (credentials.active("MONGODB_URI") or "").strip()


def diagnose() -> tuple[bool, str]:
    """Whether the archive can be used here, and why, in one sentence.
    Sends nothing."""
    if not settings.archive:
        return False, "ARCHIVE=0"
    chosen = (settings.archive_backend or "").strip().lower()
    if chosen not in BACKENDS:
        return False, (f"ARCHIVE_BACKEND={chosen!r} is not a backend; use one "
                       f"of: {', '.join(BACKENDS)}")
    if chosen == "atlas":
        if not _uri():
            # Never a quiet fall back to the local file: a production that
            # meant Atlas and wrote to its own disk would hold history that
            # vanishes on the next deploy, while believing it was kept.
            return False, "ARCHIVE_BACKEND=atlas but MONGODB_URI is not set"
        try:
            import pymongo  # noqa: F401
        except ImportError:
            return False, ("ARCHIVE_BACKEND=atlas but pymongo is not installed "
                           "(requirements-archive.txt)")
    return True, f"{chosen}"


def enabled() -> bool:
    return diagnose()[0]


def backend():
    """The open backend, or None when the archive is off. Never raises."""
    if not enabled():
        return None
    with _BACKEND_LOCK:
        if _BACKEND[0] is None:
            try:
                if settings.archive_backend.strip().lower() == "atlas":
                    _BACKEND[0] = AtlasBackend(_uri())
                else:
                    _BACKEND[0] = SQLiteBackend()
            except Exception as exc:  # noqa: BLE001 - see `_failed`
                _failed("open", exc)
                return None
        return _BACKEND[0]


def use(instance) -> None:
    """Put a backend in place (tests, and `tools/archive_setup.py`)."""
    with _BACKEND_LOCK:
        _BACKEND[0] = instance


def reset() -> None:
    """Forget the open backend, the counters and any backoff (tests)."""
    with _BACKEND_LOCK:
        _BACKEND[0] = None
    with _STATS_LOCK:
        STATS.update({"intake_articles": 0, "episodes_linked": 0, "recalls": 0,
                      "recalled_facts": 0, "recall_late": 0, "errors": 0,
                      "last_error": "", "last_recall_ms": None,
                      "live_facts": 0, "live_skipped": 0, "swept_facts": 0,
                      "swept_skipped": 0})
    _SWEPT_SEEN.clear()
    _BACKOFF_UNTIL[0] = 0.0
    _SINCE_PRUNE[0] = 0


def exists() -> bool:
    """Whether the local file has been created (the health report's rule:
    a store is reported once it exists, never created by being asked)."""
    return store_db.has_tables(data_path("ARCHIVE_DB", "archive.db"))


def _backing_off() -> bool:
    return time.monotonic() < _BACKOFF_UNTIL[0]


def _failed(what: str, exc: BaseException) -> None:
    _BACKOFF_UNTIL[0] = time.monotonic() + FAILURE_BACKOFF_SECONDS
    detail = f"{what}: {type(exc).__name__}: {exc}"[:300]
    with _STATS_LOCK:
        STATS["errors"] += 1
        STATS["last_error"] = detail
    log.warning("the archive could not %s (%s); not asked again for %ds",
                what, detail, FAILURE_BACKOFF_SECONDS)
    try:
        import pipeline_errors

        pipeline_errors.record("research", "archive", detail)
    except Exception:  # noqa: BLE001 - reporting never fails anything
        pass


def _count(name: str, n: int = 1) -> None:
    with _STATS_LOCK:
        STATS[name] = (STATS.get(name) or 0) + n


# --------------------------------------------------------------------------
# Writing: after an episode is kept
# --------------------------------------------------------------------------
#: The live domains whose provider data is kept (§313). Weather is a
#: forecast, never an outcome ([weather]); a prediction market is never
#: evidence ([forecast-beside]).
LIVE_DOMAINS = ("sports", "markets", "elections")


def live_records(*lookups) -> list:
    """What the episode's live providers said, as records the archive keeps
    (§313, the owner's direction: scores and prices are the archive's most
    valuable material). Code only - the provider's own lines, its time and
    its status, never a model's words.

    Kept: a market's state at its `as_of`; a game, race or count only once
    it is **final** (a score mid-game is not history, it is a moment that
    will be wrong in a minute); a player's or team's report. Never raises."""
    import live_facts

    out = []
    for lookup in lookups:
        try:
            if lookup is None or getattr(lookup, "outcome", "") != live_facts.FACTS:
                continue
            facts = getattr(lookup, "facts", None)
            if facts is None or not list(getattr(facts, "facts", None) or []):
                continue
            domain = str(getattr(facts, "domain", "") or "")
            kind = str(getattr(facts, "kind", "") or "")
            status = str(getattr(facts, "status", "") or "")
            if kind == live_facts.PREDICTION_MARKET or domain not in LIVE_DOMAINS:
                continue
            if (domain != "markets" and kind != live_facts.PLAYER_REPORT
                    and status != live_facts.FINAL):
                continue
            as_of = getattr(facts, "as_of", None)
            if as_of is None:
                continue
            entity = getattr(facts, "entity", None)
            name = str(getattr(entity, "label", "") or getattr(entity, "id", "")
                       or "")
            out.append({
                "domain": domain, "kind": kind or "state", "status": status,
                "source": str(getattr(facts, "source", "") or ""),
                "entity": name, "as_of": as_of.isoformat(),
                "lines": [str(x) for x in facts.facts if str(x).strip()]})
        except Exception as exc:  # noqa: BLE001 - never costs the episode
            log.warning("a live record could not be kept: %s", exc)
    return out


def _live_fact(record: dict, subject: str, category: str) -> Fact:
    """One live record as an archive fact: dated by the provider's own
    `as_of`, graded primary, its lines verbatim."""
    day = str(record.get("as_of", ""))[:10]
    entity = record.get("entity") or subject
    title = " ".join(filter(None, (entity, {"markets": "market data",
                                            "sports": "result",
                                            "elections": "count"}.get(
                                                record.get("domain"), "record"),
                                   day)))
    return Fact(
        id=str(record.get("id", "") or ""),
        url=f"live:{record.get('source', '')}:{entity}:{record.get('as_of', '')}",
        text=" ".join(record.get("lines") or []), title=title,
        host=str(record.get("source", "")), grade="primary", published=day,
        kind="live", subject=subject, category=category,
        figure={"domain": record.get("domain"), "status": record.get("status"),
                "as_of": record.get("as_of"), "kind": record.get("kind")})


# --------------------------------------------------------------------------
# The sweeps' scores and prices (§315)
# --------------------------------------------------------------------------
#: What a sweep already kept, by fact id and what it said, so a final seen
#: on every sweep of the day, or a price that has not moved, is embedded
#: once. In memory: after a restart the first sweep writes each again, and
#: the store's upsert keeps it one fact.
_SWEPT_SEEN: dict = {}
SWEPT_SEEN_MAX = 5000


def keep_swept(records: list) -> None:
    """Keep what a sweep fetched anyway: a game once it is **final**, a
    symbol's latest price for its trading day (§315, the owner's direction:
    scores and prices are the archive's most valuable material). Records
    are `live_records`' shape plus a stable `id`, `subject` and `category`.
    No request of its own; queued on the archive's thread; never raises,
    never waits."""
    try:
        if not records or not enabled():
            return
        fresh = []
        for record in records:
            mark = (record.get("id"), tuple(record.get("lines") or ()))
            if _SWEPT_SEEN.get(mark[0]) == mark[1]:
                continue
            fresh.append(record)
        if fresh:
            EXECUTOR.submit(_write_swept, fresh)
    except Exception as exc:  # noqa: BLE001 - never costs the sweep
        log.warning("could not queue a sweep for the archive: %s", exc)


def _write_swept(records: list) -> None:
    if _backing_off() or backend() is None:
        return
    for index, record in enumerate(records):
        try:
            add_fact(_live_fact(record, str(record.get("subject", "") or ""),
                                str(record.get("category", "") or "")))
        except RuntimeError:
            # No sentence model: nothing kept can be recalled. Not a store
            # failure, so no back-off.
            _count("swept_skipped", len(records) - index)
            return
        except Exception as exc:  # noqa: BLE001 - see `_failed`
            _failed("swept fact", exc)
            return
        _count("swept_facts")
        if len(_SWEPT_SEEN) >= SWEPT_SEEN_MAX:
            _SWEPT_SEEN.clear()
        _SWEPT_SEEN[record.get("id")] = tuple(record.get("lines") or ())


def swept_game(league_key: str, league_label: str, game_id, day: str,
               lines: list, source: str, as_of: datetime, home: str,
               away: str) -> dict:
    """A final game from the sports sweep, as a record `keep_swept` takes."""
    return {"id": url_id(f"sweep:sports:{league_key}:{game_id}"),
            "domain": "sports", "kind": "final", "status": "final",
            "source": source, "entity": f"{home} vs {away}",
            "subject": f"{home} vs {away}", "category": league_label,
            "as_of": as_of.isoformat(),
            "lines": [f"{league_label}, {day}: " + " ".join(lines)]}


def swept_price(symbol: str, label: str, price: float, change, as_of: datetime,
                is_open, source: str) -> dict:
    """A symbol's latest price from the markets sweep, one record per symbol
    per trading day - the last sweep of the day leaves the day's last price.
    Worded as what the provider said, never "closed at" unless the market
    is known to be shut."""
    day = as_of.astimezone(timezone.utc).date().isoformat()
    moved = ""
    if change is not None:
        way = "up" if float(change) >= 0 else "down"
        moved = f", {way} {abs(float(change)):.1f} percent on the day"
    state = (" with the US market closed" if is_open is False else "")
    return {"id": url_id(f"sweep:markets:{symbol}:{day}"),
            "domain": "markets", "kind": "price", "status": "",
            "source": source, "entity": f"{label} ({symbol})",
            "subject": label, "category": "stock market",
            "as_of": as_of.isoformat(),
            "lines": [f"{label} ({symbol}) last traded at {float(price):.2f}"
                      f"{moved}, as of {as_of.astimezone(timezone.utc):%H:%M} "
                      f"UTC on {day}{state}."]}


def after_episode(notes, key: str, sourced_at: float, origin: str = "") -> None:
    """Queue the episode's articles for the archive. Never raises, never
    waits: the work is submitted to `EXECUTOR`.

    Called beside `category_audit.note`, after the episode was cached - so
    only a shareable episode reaches it - and refused here for a private
    search."""
    try:
        if (origin or "") == PRIVATE_ORIGIN or not enabled():
            return
        carried = getattr(notes, "archive", None) or {}
        findings = list(carried.get("findings") or [])
        live = list(carried.get("live") or [])
        if not (findings or live) or not key:
            return
        import cache as cache_mod

        episode = cache_mod.episode_id(key, sourced_at)
        subject = str(carried.get("subject", "") or "")
        category = str(getattr(notes, "category", "") or "")
        EXECUTOR.submit(_write, findings, subject, category, episode, key,
                        float(sourced_at or 0), live)
    except Exception as exc:  # noqa: BLE001 - never costs the episode
        log.warning("could not queue an episode for the archive: %s", exc)


def _write(findings: list, subject: str, category: str, episode: str,
           key: str, sourced_at: float, live: list = ()) -> None:
    if _backing_off():
        return
    store = backend()
    if store is None:
        return
    now = time.time()
    # The live providers' data (§313): kept as facts at once, in code - a
    # score or a price needs no agent to decide what it says.
    for record in live or ():
        try:
            add_fact(_live_fact(record, subject, category))
            _count("live_facts")
        except RuntimeError:
            # No embedder installed: a fact that cannot be recalled is not
            # kept, and the store itself is fine, so no back-off.
            _count("live_skipped", len(live) - live.index(record))
            break
        except Exception as exc:  # noqa: BLE001 - see `_failed`
            _failed("live fact", exc)
            break
    if not findings:
        return
    try:
        _count("intake_articles", store.intake(findings, subject, category, now))
        if episode:
            store.link_episode(episode, key, sourced_at,
                               [f.as_link() for f in findings], now)
            _count("episodes_linked")
        _SINCE_PRUNE[0] += len(findings)
        if _SINCE_PRUNE[0] >= PRUNE_EVERY:
            _SINCE_PRUNE[0] = 0
            dropped = store.prune_intake(int(settings.archive_intake_max))
            if dropped:
                log.warning("the archive's intake passed ARCHIVE_INTAKE_MAX=%d; "
                            "dropped the %d oldest undigested articles",
                            settings.archive_intake_max, dropped)
    except Exception as exc:  # noqa: BLE001 - see `_failed`
        _failed("write", exc)


def add_fact(fact: Fact) -> str:
    """Keep one fact, embedded here. For Consuela (the housekeeper agent) and the tests.
    Raises when there is no archive or no embedder - it is never on a
    listener's path, and a fact without a vector cannot be recalled."""
    store = backend()
    if store is None:
        raise RuntimeError(f"no archive: {diagnose()[1]}")
    vector, space = _embed(" ".join(filter(None, (fact.subject, fact.text))))
    if vector is None:
        raise RuntimeError("no sentence embedder is installed "
                           "(python tools/install_embed_model.py)")
    return store.add_fact(fact, vector, space, time.time())


def episode_links(episode: str) -> list:
    """An episode's articles, for the episode screen. [] on any failure."""
    store = backend()
    if store is None or not episode:
        return []
    try:
        return store.episode_links(episode)
    except Exception as exc:  # noqa: BLE001
        _failed("read an episode's sources", exc)
        return []


# --------------------------------------------------------------------------
# Reading: the recall, beside the search
# --------------------------------------------------------------------------
def _embed(text: str) -> tuple:
    """(vector, space) from the sentence model, or (None, "") without one.
    The hashing vector is never used here: it is lexical, and a recall that
    matched on spelling would hand the writer the wrong history."""
    import embeddings

    encoder = embeddings.semantic_encoder()
    if encoder is None:
        return None, ""
    vector = encoder.encode(text)
    return vector, f"onnx:{len(vector)}"


def recall_wanted(brief) -> str:
    """"" when this episode may be handed earlier reporting, else why not."""
    if not settings.archive_recall:
        return "ARCHIVE_RECALL=0"
    if brief is None:
        return "no brief"
    # Sports, markets and outcome-dependent questions are recalled too
    # (§313, the owner's direction): their history is the archive's most
    # valuable material. What keeps an old score or price from passing for
    # today's is downstream - dated sources, `ScoreGuard`, `FigureGuard`'s
    # archive rule and the writer's outcome note - not a refusal here.
    domain = str(getattr(brief, "live_domain", "") or "")
    if domain == "weather" or str(getattr(brief, "place", "") or "").strip():
        return "weather or a town's news"
    return ""


def recall(text: str, window_days: int, exclude_urls: set = frozenset(),
           now: Optional[datetime] = None) -> list:
    """Facts relevant to `text`, published before the recency window.
    Blocking; [] on any failure or with nothing to search."""
    store = backend()
    if store is None or _backing_off() or not (text or "").strip():
        return []
    started = time.perf_counter()
    try:
        vector, space = _embed(text)
        if vector is None:
            return []
        now = now or datetime.now(timezone.utc)
        before = now - _days(max(1, int(window_days or 0)))
        found = store.recall(vector, space, before,
                             int(settings.archive_recall_limit),
                             float(settings.archive_min_similarity),
                             set(exclude_urls or ()))
    except Exception as exc:  # noqa: BLE001 - see `_failed`
        _failed("recall", exc)
        return []
    with _STATS_LOCK:
        STATS["recalls"] += 1
        STATS["recalled_facts"] += len(found)
        STATS["last_recall_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return found


def _days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


def look(text: str, brief):
    """Start the recall beside the search: a future, or None when this
    episode is not one the archive may add to. Never raises."""
    try:
        if not enabled() or recall_wanted(brief):
            return None
        import asyncio
        import research

        window = research.search_window(brief)
        return asyncio.get_running_loop().run_in_executor(
            RECALL_EXECUTOR, recall, text, window)
    except Exception as exc:  # noqa: BLE001
        log.warning("the archive recall could not start: %s", exc)
        return None


def as_sources(facts: list, first_number: int, now: Optional[datetime] = None) -> str:
    """The packet's entries for recalled facts: one per article, dated and
    graded, labelled as earlier reporting, never a hostname."""
    import listener_clock
    import research

    now = now or listener_clock.now()
    by_url: dict = {}
    for fact in facts:
        by_url.setdefault(fact.url, []).append(fact)
    parts = []
    number = first_number
    for url, items in by_url.items():
        head = items[0]
        when = _day(head.published)
        grade = research.TIER_LABELS.get(head.grade, "")
        kind = research.TIER_LABELS["archive"]
        parts += [f"SOURCE {number}",
                  f"Title: {head.title}",
                  f"Published: {head.published} ({research.age_phrase(when, now)})",
                  f"Source type: {kind}" + (f"; originally {grade}" if grade else ""),
                  "Key evidence:"]
        parts += [f.text for f in items]
        parts.append("")
        number += 1
    return "\n".join(parts)


def report() -> dict:
    """For `/api/health`. Sends nothing and never opens a store."""
    ok, why = diagnose()
    with _STATS_LOCK:
        stats = dict(STATS)
    try:
        import embeddings

        embedder = embeddings.model_installed()
    except Exception:  # noqa: BLE001
        embedder = False
    return {"enabled": ok, "backend": (settings.archive_backend or "").lower(),
            "reason": why, "recall": bool(settings.archive_recall),
            "embedder": embedder,
            "backing_off": _backing_off(),
            "grace_seconds": float(settings.archive_grace_seconds), **stats}
