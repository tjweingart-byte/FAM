"""Consuela, the housekeeper: each article consolidated as it enters (§399).

Phase 2, piece 3 (`docs/AGENTS_BUILD_PROMPT.md`; consuela.md, always 1-3;
decided items 3 and 35). An article that entered an episode's packet waits
in the archive's intake; Consuela's task, on Netenyahu's background loop,
takes the oldest few and keeps only what is worth keeping:

* **The model answers with sentence numbers only** - which sentences are
  key, which are quotes, which state a figure and whether it reports
  something that *happened* or something *expected*. FAM keeps the
  publisher's own sentence, never a model's words (every-agent.md, never 2;
  consuela.md, never 1).
* **Code does everything else**: numbers the sentences
  (`evidence_digest.split_sentences`), checks every answer (in range, an
  article once, a sentence once per list - a bad answer is retried once,
  then the batch is left for the next run and reported), reads each figure
  (`figure_guard.figures_in` - a sentence whose figure code cannot read is
  kept as a sentence, never as a figure), and decides a figure's status: an
  *outcome* only when the model says it happened **and** the sentence has
  no hedge ("expected", "will", "could"...); otherwise a forecast or unsure
  (consuela.md, never 3).
* **Filed under Nando's nodes** (always 1): every node an episode that used
  the article was filed under, and the tree still knows - one node, or
  several (§413, the owner: a Bengals-Giants game is filed under both
  teams, never collapsed to their parent). An article no node vouches for,
  or none of whose nodes the tree knows, is held - left in intake,
  reported - never filed by guess.
* **Only an approved provider's passages** (§397; never 8): an article's
  passages are read from `by_provider`, and only the approved providers'.
  One kept before providers were recorded (§358) has none, so it is held.

**A dry run until the owner says otherwise** (`HOUSEKEEPER_DRY_RUN=1`): she
chooses, and what she would keep is written to her report
(`housekeeper.db`, `tools/consuela_report.py`) - nothing reaches the
archive, and an article reported once is not asked about again. With the
dry run off, the chosen sentences become facts (`history_archive.add_fact`)
and the intake record is marked consolidated **and its passages cleared at
once** (§400, the owner: clearing is the consolidation itself - always 3 -
not a removal kept 30 days). The record keeps its link, title, outlet,
grade, date and nodes; new passages for it from a later episode make it
wait again, and are cleared the same way.

Nothing here is on a listener's path: it runs only as Netenyahu's task,
only while Consuela may run (`history_archive.consuela_runs`), and with
`AGENTS=0` nothing in this module is ever called.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import threading
import time
from contextlib import closing
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

import db as store_db
from config import settings
from paths import data_path

log = logging.getLogger("fam.agents.housekeeper")

AGENT = "housekeeper"
TASK = "consolidate"

#: Most sentences one article offers the model; the rest are not read.
MAX_SENTENCES = 40
#: A held article is looked at again after this long (its node may be
#: settled by then).
HELD_RETRY_SECONDS = 24 * 3600.0
#: How far past the oldest intake one run looks for work.
LOOK_AHEAD = 4


# ------------------------------------------------------------------ the answer's shape
class FigureMark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sentence: int
    status: Literal["happened", "expected", "unsure"]


class ArticlePick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    article: int
    key: list[int]
    quotes: list[int]
    figures: list[FigureMark]


class Picks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    articles: list[ArticlePick]


INSTRUCTIONS = (
    "You choose which sentences of each article are worth keeping in FAM's "
    "history archive. Answer with sentence numbers only; FAM keeps the "
    "publisher's own words, never a summary. Keep a sentence when it says who "
    "did what, when, why, or states a figure: a score, a price, a vote, a "
    "count, a date. Leave out what only describes the article or the site.\n\n"
    "For each article, by its number: `key` lists the sentences worth "
    "keeping; `quotes` the sentences that quote someone directly; `figures` "
    "the sentences that state a figure, each with whether it reports "
    "something that **happened** or something **expected**. \"Is expected to "
    "win 4-2\" is expected; \"won 4-2\" happened. If you cannot tell, say "
    "unsure. A sentence may appear in more than one list, but never twice in "
    "one. An article with nothing worth keeping gets empty lists. Answer for "
    "every article."
)


# ------------------------------------------------------------------ code's half
#: Wording that makes a figure a forecast, whatever the model said
#: (docs/FAM_AGENTS.md, Housekeeper, guardrails: "hedged wording ... stores
#: forecast").
HEDGE = re.compile(
    r"\b(expect(?:s|ed)?|set to|could|would|will|may|might|forecast(?:s|ed)?|"
    r"project(?:s|ed|ion)|predict(?:s|ed|ion)?|likely|poised|on track|"
    r"aims? to|plans? to|preview|odds|estimate[sd]?)\b", re.I)
_QUOTE_MARK = re.compile(r"[\"“”]|(?:^|\s)[‘'][A-Z]")


def passages_of(record: dict) -> list:
    """An intake record's passages from approved providers only (§397)."""
    import history_archive as ha

    out: list = []
    for provider, passages in (record.get("by_provider") or {}).items():
        if ha.approved(provider):
            out += [p for p in passages or () if p and p not in out]
    return out


def sentences_of(record: dict) -> list:
    """The record's approved passages, numbered as the model sees them."""
    import evidence_digest

    out: list = []
    for passage in passages_of(record):
        for sentence in evidence_digest.split_sentences(passage):
            if sentence not in out:
                out.append(sentence)
    return out[:MAX_SENTENCES]


def material(batch: list) -> str:
    """The batch as numbered articles: title, date, grade, sentences. Never
    the hostname (§308's packet rule)."""
    parts = []
    for i, (record, sentences) in enumerate(batch, 1):
        parts += [f"ARTICLE {i}", f"Title: {record.get('title', '')}",
                  f"Published: {record.get('published') or 'unknown'}",
                  f"Grade: {record.get('grade') or 'unknown'}"]
        parts += [f"[{n}] {s}" for n, s in enumerate(sentences, 1)]
        parts.append("")
    return "\n".join(parts)


def check(picks: Picks, batch: list) -> str:
    """"" when the answer can be used, else why not: every article once, every
    sentence number in range, none twice in one list."""
    seen = set()
    for pick in picks.articles:
        if not 1 <= pick.article <= len(batch) or pick.article in seen:
            return f"article {pick.article} is out of range or repeated"
        seen.add(pick.article)
        count = len(batch[pick.article - 1][1])
        for name, numbers in (("key", pick.key), ("quotes", pick.quotes),
                              ("figures", [f.sentence for f in pick.figures])):
            if len(set(numbers)) != len(numbers):
                return f"article {pick.article}: a sentence twice in {name}"
            if any(not 1 <= n <= count for n in numbers):
                return f"article {pick.article}: a sentence out of range in {name}"
    if len(seen) != len(batch):
        return "not every article was answered"
    return ""


def status_of(sentence: str, said: str) -> str:
    """A figure's status: an outcome only when the model says it happened and
    the sentence has no hedge; a hedge or "expected" is a forecast."""
    if HEDGE.search(sentence) or said == "expected":
        return "forecast"
    return "outcome" if said == "happened" else "unsure"


def facts_from(record: dict, sentences: list, pick: ArticlePick, node: str,
               path: list, also: Optional[list] = None) -> list:
    """The facts one article's pick keeps, in the article's order."""
    import figure_guard
    import history_archive as ha

    figures = {f.sentence: f.status for f in pick.figures}
    chosen = sorted(set(pick.key) | set(pick.quotes) | set(figures))
    subjects = list(record.get("subjects") or [])
    categories = list(record.get("categories") or [])
    out = []
    for n in chosen:
        text = sentences[n - 1]
        kind, figure = "sentence", {}
        values = figure_guard.figures_in(text) if n in figures else []
        if values:
            kind = "figure"
            figure = {"values": [{"value": v, "decimals": d, "unit": u}
                                 for v, d, u in values],
                      "status": status_of(text, figures[n])}
        elif n in pick.quotes and _QUOTE_MARK.search(text):
            kind = "quote"
        out.append(ha.Fact(
            url=record.get("url", ""), text=text, title=record.get("title", ""),
            host=record.get("host", ""), grade=record.get("grade", ""),
            published=record.get("published", ""), kind=kind,
            subject=subjects[0] if subjects else "",
            category=categories[0] if categories else "", figure=figure,
            node_id=node, node_path=list(path), also_nodes=list(also or [])))
    return out


def filing_nodes(record: dict) -> list:
    """The nodes an article is filed under (§413): every node an episode that
    used it was filed under, in the order they came, that the tree knows.
    The first is the facts' `node_id`, the rest their `also_nodes`.
    [] when the tree cannot be read."""
    nodes = []
    for node in record.get("nodes") or ():
        if node and node not in nodes:
            nodes.append(node)
    try:
        import topics

        tree = topics.category_tree()
        return [n for n in nodes if tree.get(n) is not None]
    except Exception:  # noqa: BLE001
        return []


def held_why(record: dict, sentences: list) -> str:
    """"" when the record may be consolidated, else why it is held."""
    nodes = [n for n in record.get("nodes") or () if n]
    if not nodes:
        return "no node: Nando did not file an episode that used it"
    if not filing_nodes(record):
        return (f"none of its nodes is in the tree ({', '.join(sorted(set(nodes)))})"
                " or the tree could not be read")
    if not sentences:
        return "no passages from an approved provider"
    return ""


# ------------------------------------------------------------------ the report
class ReportStore:
    """What each run chose, per intake record - the dry run's whole output,
    and the record of every live run. No listener, no passage: the chosen
    sentences' numbers and texts, the record's id and link."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("HOUSEKEEPER_DB", "housekeeper.db", path)
        self._lock = threading.Lock()
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS consolidations ("
                       " intake_id TEXT PRIMARY KEY, url TEXT NOT NULL,"
                       " at REAL NOT NULL, mode TEXT NOT NULL,"
                       " outcome TEXT NOT NULL, why TEXT NOT NULL,"
                       " kept TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS consolidations_at"
                       " ON consolidations(at)")
            # Her dedupe (§401): every merge she made or would make, every
            # conflict she flagged, and every run of her eval.
            db.execute("CREATE TABLE IF NOT EXISTS merges ("
                       " pair TEXT PRIMARY KEY, at REAL NOT NULL, mode TEXT NOT NULL,"
                       " verdict TEXT NOT NULL, why TEXT NOT NULL,"
                       " keeper TEXT NOT NULL, repeat TEXT NOT NULL,"
                       " keeper_text TEXT NOT NULL, repeat_text TEXT NOT NULL,"
                       " first TEXT NOT NULL, review TEXT NOT NULL)")
            # Her longer jobs (§402): each run's summary, each re-file with
            # where it came from (so it can be undone), and the comb's place.
            db.execute("CREATE TABLE IF NOT EXISTS jobs ("
                       " id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,"
                       " job TEXT NOT NULL, mode TEXT NOT NULL, summary TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS refiles ("
                       " id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,"
                       " mode TEXT NOT NULL, fact TEXT NOT NULL,"
                       " from_node TEXT NOT NULL, to_node TEXT NOT NULL,"
                       " from_path TEXT NOT NULL, to_path TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS state ("
                       " key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            # Sonic's recall eval (§407): the agents' measures live together.
            db.execute("CREATE TABLE IF NOT EXISTS recall_evals ("
                       " id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,"
                       " total INTEGER NOT NULL, found INTEGER NOT NULL,"
                       " leaks INTEGER NOT NULL, order_errors INTEGER NOT NULL,"
                       " summary TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS evals ("
                       " id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,"
                       " models TEXT NOT NULL, total INTEGER NOT NULL,"
                       " wrong_merges INTEGER NOT NULL, missed INTEGER NOT NULL,"
                       " fell_back INTEGER NOT NULL, details TEXT NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        return store_db.connect(self.path, timeout=10)

    def note(self, intake_id: str, url: str, mode: str, outcome: str,
             why: str = "", kept: Optional[list] = None, now: float = 0.0) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO consolidations (intake_id, url, at, mode, outcome, why, kept)"
                " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(intake_id) DO UPDATE SET"
                " at = excluded.at, mode = excluded.mode, outcome = excluded.outcome,"
                " why = excluded.why, kept = excluded.kept",
                (intake_id, url, now or time.time(), mode, outcome, why,
                 json.dumps(kept or [])))

    def skipped(self, ids: list, mode: str, now: float = 0.0) -> set:
        """Which of `ids` this run passes over: in a dry run, any already
        chosen for (asked once); in either mode, any held in the last
        `HELD_RETRY_SECONDS` - so held articles never block the queue
        behind them, and are looked at again a day later."""
        if not ids:
            return set()
        marks = ",".join("?" for _ in ids)
        since = (now or time.time()) - HELD_RETRY_SECONDS
        with closing(self._connect()) as db:
            return {r[0] for r in db.execute(
                f"SELECT intake_id FROM consolidations WHERE intake_id IN ({marks})"
                f" AND ((outcome = 'held' AND at > ?)"
                f" OR (mode = 'dry_run' AND outcome = 'chosen' AND ? = 'dry_run'))",
                [*ids, since, mode])}

    def recent(self, limit: int = 50) -> list:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT intake_id, url, at, mode, outcome, why, kept "
                              "FROM consolidations ORDER BY at DESC LIMIT ?",
                              (max(1, int(limit)),)).fetchall()
        return [{"intake_id": r[0], "url": r[1], "at": r[2], "mode": r[3],
                 "outcome": r[4], "why": r[5], "kept": json.loads(r[6] or "[]")}
                for r in rows]

    def counts(self) -> dict:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT mode, outcome, COUNT(*) FROM consolidations "
                              "GROUP BY mode, outcome").fetchall()
            merges = db.execute("SELECT mode, verdict, COUNT(*) FROM merges "
                                "GROUP BY mode, verdict").fetchall()
        return {**{f"{m}:{o}": n for m, o, n in rows},
                **{f"dedupe:{m}:{v}": n for m, v, n in merges}}

    # -------------------------------------------------- her dedupe (§401)
    def note_merge(self, keeper: dict, repeat: dict, verdict: str, why: str,
                   mode: str, first: str = "", review: str = "",
                   now: float = 0.0) -> None:
        pair = "|".join(sorted((keeper["id"], repeat["id"])))
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO merges (pair, at, mode, verdict, why, keeper, repeat,"
                " keeper_text, repeat_text, first, review)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(pair) DO UPDATE"
                " SET at = excluded.at, mode = excluded.mode, verdict = excluded.verdict,"
                " why = excluded.why, first = excluded.first, review = excluded.review",
                (pair, now or time.time(), mode, verdict, why, keeper["id"],
                 repeat["id"], keeper.get("text", ""), repeat.get("text", ""),
                 first, review))

    def recent_merges(self, limit: int = 50) -> list:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT at, mode, verdict, why, keeper, repeat,"
                              " keeper_text, repeat_text, first, review FROM merges"
                              " ORDER BY at DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        keys = ("at", "mode", "verdict", "why", "keeper", "repeat", "keeper_text",
                "repeat_text", "first", "review")
        return [dict(zip(keys, r)) for r in rows]

    def note_eval(self, models: str, total: int, wrong_merges: int, missed: int,
                  fell_back: bool, details: list, now: float = 0.0) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT INTO evals (at, models, total, wrong_merges, missed,"
                       " fell_back, details) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (now or time.time(), models, int(total), int(wrong_merges),
                        int(missed), 1 if fell_back else 0, json.dumps(details)))

    # -------------------------------------------------- her longer jobs (§402)
    def note_job(self, job: str, mode: str, summary: dict, now: float = 0.0) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT INTO jobs (at, job, mode, summary) VALUES (?, ?, ?, ?)",
                       (now or time.time(), job, mode, json.dumps(summary)))

    def last_job(self, job: str) -> Optional[dict]:
        with closing(self._connect()) as db:
            row = db.execute("SELECT at, mode, summary FROM jobs WHERE job = ? "
                             "ORDER BY id DESC LIMIT 1", (job,)).fetchone()
        return None if row is None else {"at": row[0], "mode": row[1],
                                         "summary": json.loads(row[2] or "{}")}

    def note_refile(self, fact: str, mode: str, from_node: str, to_node: str,
                    from_path: list, to_path: list, now: float = 0.0) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT INTO refiles (at, mode, fact, from_node, to_node,"
                       " from_path, to_path) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (now or time.time(), mode, fact, from_node, to_node,
                        json.dumps(from_path), json.dumps(to_path)))

    def refiles(self, limit: int = 50) -> list:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT at, mode, fact, from_node, to_node, from_path,"
                              " to_path FROM refiles ORDER BY id DESC LIMIT ?",
                              (max(1, int(limit)),)).fetchall()
        return [{"at": r[0], "mode": r[1], "fact": r[2], "from_node": r[3],
                 "to_node": r[4], "from_path": json.loads(r[5]),
                 "to_path": json.loads(r[6])} for r in rows]

    def get_state(self, key: str, default: str = "") -> str:
        with closing(self._connect()) as db:
            row = db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key)"
                       " DO UPDATE SET value = excluded.value", (key, value))

    def note_recall_eval(self, summary: dict, now: float = 0.0) -> None:
        """One run of Sonic's recall eval (§407), its details included."""
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT INTO recall_evals (at, total, found, leaks, order_errors,"
                       " summary) VALUES (?, ?, ?, ?, ?, ?)",
                       (now or time.time(), int(summary.get("total") or 0),
                        int(summary.get("found") or 0), int(summary.get("leaks") or 0),
                        int(summary.get("order_errors") or 0), json.dumps(summary)))

    def latest_recall_eval(self) -> Optional[dict]:
        with closing(self._connect()) as db:
            row = db.execute("SELECT at, summary FROM recall_evals ORDER BY id DESC"
                             " LIMIT 1").fetchone()
        return None if row is None else {"at": row[0], **json.loads(row[1] or "{}")}

    def latest_eval(self, models: str) -> Optional[dict]:
        with closing(self._connect()) as db:
            row = db.execute("SELECT at, total, wrong_merges, missed, fell_back, details"
                             " FROM evals WHERE models = ? ORDER BY id DESC LIMIT 1",
                             (models,)).fetchone()
        if row is None:
            return None
        return {"at": row[0], "total": row[1], "wrong_merges": row[2], "missed": row[3],
                "fell_back": bool(row[4]), "details": json.loads(row[5] or "[]")}


_STORE: list = [None]
_STORE_LOCK = threading.Lock()


def store() -> ReportStore:
    with _STORE_LOCK:
        if _STORE[0] is None:
            _STORE[0] = ReportStore()
        return _STORE[0]


def reset(report_store: Optional[ReportStore] = None) -> None:
    with _STORE_LOCK:
        _STORE[0] = report_store


# ------------------------------------------------------------------ the run
async def _choose(batch: list) -> tuple:
    """(Picks or None, why). One call, one retry on an answer code refuses."""
    from agents import models

    request = models.AgentRequest(
        agent=AGENT, task=TASK, switch_setting="housekeeper",
        model_setting="housekeeper_model", rule_book="consuela.md", schema=Picks,
        instructions=INSTRUCTIONS, material=(("evidence", material(batch)),),
        timeout=float(settings.housekeeper_timeout_seconds),
        max_tokens=int(settings.housekeeper_max_tokens), effort="low",
        surface="background")
    why = ""
    for _attempt in range(2):
        result = await models.ask(request)
        if not result.ok:
            return None, f"the model call fell back: {result.status}"
        why = check(result.value, batch)
        if not why:
            return result.value, ""
    return None, f"unusable answer twice: {why}"


async def run() -> dict:
    """One run: the oldest intake, in batches, chosen and reported (dry run)
    or kept (live). Returns what it did. Never raises."""
    import history_archive as ha

    summary = {"chosen": 0, "held": 0, "kept_facts": 0, "failed": 0,
               "dry_run": bool(settings.housekeeper_dry_run)}
    try:
        if not ha.consuela_runs():
            return {**summary, "why": "Consuela may not run"}
        archive = ha.backend()
        if archive is None:
            return {**summary, "why": "no archive"}
        size = max(1, int(settings.housekeeper_batch_articles))
        want = size * max(1, int(settings.housekeeper_batches_per_run))
        records = await asyncio.to_thread(archive.undigested, want * LOOK_AHEAD)
        mode = "dry_run" if settings.housekeeper_dry_run else "live"
        done = await asyncio.to_thread(store().skipped, [r["id"] for r in records], mode)
        records = [r for r in records if r["id"] not in done]
        ready = []
        for record in records[:want]:
            sentences = sentences_of(record)
            why = held_why(record, sentences)
            if why:
                summary["held"] += 1
                await asyncio.to_thread(store().note, record["id"], record["url"],
                                        mode, "held", why)
                continue
            ready.append((record, sentences))
        for start in range(0, len(ready), size):
            batch = ready[start:start + size]
            picks, why = await _choose(batch)
            if picks is None:
                summary["failed"] += len(batch)
                log.warning("consuela left %d articles for the next run: %s",
                            len(batch), why)
                continue
            by_article = {p.article: p for p in picks.articles}
            for i, (record, sentences) in enumerate(batch, 1):
                nodes = filing_nodes(record)
                node = nodes[0]
                facts = facts_from(record, sentences, by_article[i], node,
                                   ha.node_path(node), also=nodes[1:])
                kept = [{"kind": f.kind, "text": f.text, "figure": f.figure}
                        for f in facts]
                if mode == "live":
                    for fact in facts:
                        await asyncio.to_thread(ha.add_fact, fact)
                    await asyncio.to_thread(archive.mark_digested, [record["id"]])
                    summary["kept_facts"] += len(facts)
                summary["chosen"] += 1
                await asyncio.to_thread(store().note, record["id"], record["url"],
                                        mode, "chosen",
                                        "filed under " + ", ".join(nodes), kept)
        return summary
    except Exception as exc:  # noqa: BLE001 - a run never takes the loop down
        log.exception("consuela's run failed")
        return {**summary, "why": f"{type(exc).__name__}: {exc}"}


# ------------------------------------------------------------------ Netenyahu's task
def queue() -> str:
    """Ask for a run soon: one queued task at a time, a fresh one each minute
    something enters (the task table's loop rule refuses the rest)."""
    from agents import tasks

    return tasks.store().add(AGENT, TASK, "intake",
                             {"minute": int(time.time() // 60)})


async def handler(_task: str) -> None:
    summary = await run()
    if summary.get("why"):
        log.info("consuela's run: %s", summary)
    if summary.get("failed") and not summary.get("chosen"):
        # Every batch fell back: a failure for Netenyahu's hold count.
        raise RuntimeError(f"no batch could be consolidated: {summary}")


def register(script_cache=None) -> None:
    """Give Netenyahu Consuela's task (app startup, only with `AGENTS=1`),
    and her re-files the script cache (§404)."""
    from agents import dedupe, housekeeping, netenyahu

    if script_cache is not None:
        housekeeping.SCRIPT_CACHE[0] = script_cache

    netenyahu.HANDLERS[(AGENT, TASK)] = handler
    # Her nightly dedupe (§401).
    netenyahu.HANDLERS[(AGENT, dedupe.TASK)] = dedupe.handler
    # Her longer jobs (§402): re-filing on tree changes, the 90-day pass and
    # the full comb.

    netenyahu.HANDLERS[(AGENT, "refile")] = housekeeping.refile_handler
    netenyahu.HANDLERS[(AGENT, "second")] = housekeeping.second_handler
    netenyahu.HANDLERS[(AGENT, "comb")] = housekeeping.comb_handler


def exists() -> bool:
    """Whether her report has been created (never created by being asked)."""
    return store_db.has_tables(data_path("HOUSEKEEPER_DB", "housekeeper.db"))


def report() -> dict:
    """For `/api/health`. Never opens a store that does not exist."""
    out = {"dry_run": bool(settings.housekeeper_dry_run),
           "dedupe_dry_run": bool(settings.housekeeper_dedupe_dry_run),
           "batch_articles": int(settings.housekeeper_batch_articles),
           "batches_per_run": int(settings.housekeeper_batches_per_run)}
    try:
        if exists():
            out["consolidations"] = store().counts()
    except Exception:  # noqa: BLE001
        pass
    return out
