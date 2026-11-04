"""Training Mclovin, step 4: real episodes as a second, ongoing check (§440).

The planted-mistake eval (`tools/mclovin_eval.py`, steps 1-2) measures each
check on mistakes put there in code. This is the other half: what Mclovin
did on real episodes, reviewed nightly by a stronger model, so a check that
is too strict - or one that lets a real mistake through - is found on the
scripts listeners hear, not only on planted ones (decided item 30: no step
waits on the owner scoring anything).

* **Kept** (`note`): when an episode Mclovin checked is cached, every
  sentence he did not simply pass - check, result, whether it was enforced,
  why, the sentence - in the agents' own store (`AGENTS_DB`), with the
  episode's key and how many sentences he checked. Never a listener id,
  never a private search's or an attached file's episode (those are never
  cached and never measured).
* **Reviewed** (`review_batch`, the nightly task `supervisor:review`): up to
  `SUPERVISOR_REVIEW_PER_RUN` sentences - his flags, and an equal sample of
  sentences he passed - each sent alone with the packet sources most like
  it (from `episode_quality`'s record of the evidence the writer read),
  never the whole script (mclovin.md "always" 6), to
  `SUPERVISOR_JUDGE_MODEL` (Opus 5.5) under his own signed rule book. It
  answers which of his named failures, if any, the sentence has. Each
  sentence is reviewed once.
* **Used** (`report`, `disagreements`): per check, how often the reviewer
  agreed with a flag, and the sentences he passed that the reviewer failed.
  Each disagreement is a case to add to `tests/test_mclovin_training_440.py`
  (step 3) - tighten the rule, never the model - printed by
  `python tools/mclovin_eval.py --reviewed`.

A wipe forgets it all, as everything learnt from the log
(`demo_data._forget_what_the_log_taught`). Nothing here runs on a
listener's path.
"""
from __future__ import annotations

import logging
import random
import sqlite3
import threading
import time
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

import db as store_db
from paths import data_path

log = logging.getLogger("fam.agents")

#: Days a kept flag waits to be reviewed.
REVIEW_DAYS = 7.0


class Review(BaseModel):
    """The reviewer's answer about one sentence."""

    model_config = ConfigDict(extra="forbid")
    #: One of Mclovin's named failures, or "none" when the sentence keeps
    #: every rule, or "unsure".
    failure: str
    #: The packet sentence that decided it, quoted, or "" for none.
    evidence: str
    reason: str


REVIEW_INSTRUCTIONS = """You review one sentence of an audio episode script \
against the packet sources the writer was given - nothing else is evidence: \
not your memory, not anything outside the sources. Say which of these named \
failures the sentence has, or "none" if it keeps every rule, or "unsure":

- number_not_in_packets: a number or figure the sources do not state for the \
same subject (rounding the way a person rounds is fine)
- quote_not_said: a quotation the sources do not contain word for word
- quote_wrong_speaker: a quotation given to someone the sources do not give it to
- unsupported_superlative: "first", "record", "biggest", "only", "ever" the \
sources do not state
- wrong_date: "today", "yesterday", "last night", "this week" wrong for the \
source's own date
- stories_joined: facts from two different stories joined, with no source \
for the link
- website_named: a website or web address said aloud
- advice: telling the listener what to do about their health, the law or \
their money

Quote the source sentence that decided it in `evidence` ("" if none), and \
give the reason in a few words. A sentence that is merely awkward, or that \
you would have written differently, has no failure."""

#: The named failures the reviewer may give, and the check each belongs to.
CHECK_OF = {"number_not_in_packets": "figures", "quote_not_said": "quote",
            "quote_wrong_speaker": "quote", "unsupported_superlative": "superlative",
            "wrong_date": "dates", "stories_joined": "mixed", "website_named": "website",
            "advice": "advice", "quote_too_long": "quote_length"}


class SupervisorTrainingStore:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("AGENTS_DB", "agents.db", path)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS mclovin_episodes (
                key TEXT NOT NULL, sourced INTEGER NOT NULL, sentences INTEGER NOT NULL,
                at REAL NOT NULL, PRIMARY KEY (key, sourced))""")
            db.execute("""CREATE TABLE IF NOT EXISTS mclovin_flags (
                key TEXT NOT NULL, sourced INTEGER NOT NULL, sentence INTEGER NOT NULL,
                check_name TEXT NOT NULL, result TEXT NOT NULL, enforced INTEGER NOT NULL,
                why TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, at REAL NOT NULL,
                PRIMARY KEY (key, sourced, sentence, check_name))""")
            db.execute("""CREATE TABLE IF NOT EXISTS mclovin_reviews (
                key TEXT NOT NULL, sourced INTEGER NOT NULL, sentence INTEGER NOT NULL,
                flagged TEXT NOT NULL DEFAULT '', failure TEXT NOT NULL,
                evidence TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL, judge_model TEXT NOT NULL DEFAULT '', at REAL NOT NULL,
                PRIMARY KEY (key, sourced, sentence))""")
            db.execute("CREATE INDEX IF NOT EXISTS mclovin_flags_at ON mclovin_flags(at)")

    def _connect(self) -> sqlite3.Connection:
        conn = store_db.connect(self.path, timeout=10, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _rows(self, sql: str, args: tuple = ()) -> list:
        with self._connect() as db:
            cur = db.execute(sql, args)
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]

    def put_episode(self, key: str, sourced: int, sentences: int, rows: list,
                    now: float = 0.0) -> None:
        now = now or time.time()
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO mclovin_episodes (key, sourced, sentences, at)"
                       " VALUES (?, ?, ?, ?) ON CONFLICT(key, sourced) DO UPDATE SET"
                       " sentences = excluded.sentences, at = excluded.at",
                       (key, int(sourced), int(sentences), now))
            for row in rows:
                db.execute("INSERT INTO mclovin_flags (key, sourced, sentence, check_name,"
                           " result, enforced, why, text, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                           " ON CONFLICT DO NOTHING",
                           (key, int(sourced), int(row["sentence"]), str(row["check"])[:20],
                            str(row["result"])[:12], 1 if row.get("enforced") else 0,
                            str(row.get("why") or "")[:240], str(row.get("text") or "")[:400],
                            now))

    def episodes_since(self, at: float) -> list:
        return self._rows("SELECT * FROM mclovin_episodes WHERE at >= ? ORDER BY at DESC",
                          (at,))

    def flags_for(self, key: str, sourced: int) -> list:
        return self._rows("SELECT * FROM mclovin_flags WHERE key = ? AND sourced = ?",
                          (key, int(sourced)))

    def reviewed(self, key: str, sourced: int) -> set:
        return {r["sentence"] for r in self._rows(
            "SELECT sentence FROM mclovin_reviews WHERE key = ? AND sourced = ?",
            (key, int(sourced)))}

    def put_review(self, key: str, sourced: int, sentence: int, flagged: str, failure: str,
                   evidence: str, reason: str, text: str, judge_model: str,
                   now: float = 0.0) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO mclovin_reviews (key, sourced, sentence, flagged, failure,"
                       " evidence, reason, text, judge_model, at)"
                       " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                       (key, int(sourced), int(sentence), flagged[:60], failure[:40],
                        evidence[:400], reason[:240], text[:400], judge_model[:80],
                        now or time.time()))

    def reviews_since(self, at: float) -> list:
        return self._rows("SELECT * FROM mclovin_reviews WHERE at >= ?", (at,))

    def forget(self) -> int:
        with self._lock, self._connect() as db:
            n = db.execute("SELECT COUNT(*) FROM mclovin_flags").fetchone()[0]
            for table in ("mclovin_episodes", "mclovin_flags", "mclovin_reviews"):
                db.execute(f"DELETE FROM {table}")
        return int(n)


_STORE: list = [None]


def store() -> SupervisorTrainingStore:
    if _STORE[0] is None:
        _STORE[0] = SupervisorTrainingStore()
    return _STORE[0]


def reset(training_store: Optional[SupervisorTrainingStore] = None) -> None:
    _STORE[0] = training_store


def exists() -> bool:
    import os

    return _STORE[0] is not None or os.path.exists(data_path("AGENTS_DB", "agents.db"))


# ------------------------------------------------------------------ kept
def note(key: str, sourced, sentences: list, notes) -> None:
    """Keep what Mclovin found on an episode as it is cached, beside its
    quality record. Only when he checked it. Never raises."""
    try:
        summary = getattr(notes, "supervisor", None)
        if not key or not isinstance(summary, dict):
            return
        rows = [r for r in summary.get("rows", []) if r.get("result") != "pass"]
        store().put_episode(key, int(sourced or time.time()), len(sentences or []), rows)
    except Exception:  # noqa: BLE001 - a record never costs an episode
        log.warning("could not keep Mclovin's verdicts for %r", key, exc_info=True)


# ------------------------------------------------------------------ reviewed
def _nearest(sentence: str, evidence: str, n: int = 3) -> str:
    """The packet sources most like the sentence, in their own words."""
    from agents import supervisor_checks

    plan = type("Plan", (), {"evidence": evidence, "live": None, "forecast": None})()
    checker = supervisor_checks.Checker(plan, None)
    return "\n\n".join(b.text.strip()[:2500] for b in checker.nearest(sentence, n))


async def review(sentence: str, sources: str) -> Optional[dict]:
    """One sentence through the reviewer, or None. Never raises."""
    try:
        from agents import models
        from config import settings

        request = models.AgentRequest(
            agent="supervisor", task="review", switch_setting="supervisor",
            model_setting="supervisor_judge_model", rule_book="mclovin.md",
            schema=Review, instructions=REVIEW_INSTRUCTIONS,
            material=(("script", sentence), ("evidence", sources or "(no sources)")),
            timeout=float(settings.supervisor_judge_timeout_seconds),
            max_tokens=int(settings.supervisor_judge_max_tokens), effort="medium",
            surface="background")
        result = await models.ask(request)
        if not result.ok:
            return None
        failure = result.value.failure.strip()
        if failure not in CHECK_OF and failure not in ("none", "unsure"):
            return None
        return {"failure": failure, "evidence": result.value.evidence.strip(),
                "reason": result.value.reason.strip(), "judge_model": result.model}
    except Exception as exc:  # noqa: BLE001
        log.warning("Mclovin's review failed: %s", exc)
        return None


async def review_batch(limit: int = 0, days: float = REVIEW_DAYS, seed: int = 0) -> dict:
    """Review up to `limit` sentences not yet reviewed: half his flags, half
    a sample of sentences he passed. Never raises."""
    import asyncio

    out = {"looked": 0, "reviewed": 0, "failed": 0, "no_evidence": 0}
    try:
        import episode_quality
        from config import settings

        limit = int(limit or settings.supervisor_review_per_run)

        def plan() -> list:
            rng = random.Random(seed or int(time.time() // 86400))
            flagged, passed = [], []
            for ep in store().episodes_since(time.time() - days * 86400):
                try:
                    quality = episode_quality.store().get(ep["key"], ep["sourced"])
                except Exception:  # noqa: BLE001 - no record, nothing to review it by
                    quality = None
                if not quality or not quality.get("evidence"):
                    out["no_evidence"] += 1
                    continue
                import json

                sentences = json.loads(quality.get("sentences") or "[]")
                done = store().reviewed(ep["key"], ep["sourced"])
                flags = {}
                for row in store().flags_for(ep["key"], ep["sourced"]):
                    if row["result"] == "fail":
                        flags.setdefault(row["sentence"], []).append(row["check_name"])
                for index, sentence in enumerate(sentences):
                    if index in done:
                        continue
                    item = (ep, index, sentence, ",".join(sorted(flags.get(index, []))),
                            quality["evidence"])
                    (flagged if index in flags else passed).append(item)
            rng.shuffle(passed)
            half = max(1, limit // 2)
            chosen = flagged[:half]
            return chosen + passed[: max(0, limit - len(chosen))]

        for ep, index, sentence, flagged, evidence in await asyncio.to_thread(plan):
            out["looked"] += 1
            sources = await asyncio.to_thread(_nearest, sentence, evidence)
            verdict = await review(sentence, sources)
            if verdict is None:
                out["failed"] += 1
                continue
            await asyncio.to_thread(store().put_review, ep["key"], ep["sourced"], index,
                                    flagged, verdict["failure"], verdict["evidence"],
                                    verdict["reason"], sentence, verdict["judge_model"])
            out["reviewed"] += 1
    except Exception as exc:  # noqa: BLE001
        log.exception("Mclovin's review run failed")
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def handler():
    async def run(_task: str) -> None:
        log.info("Mclovin's nightly review: %s", await review_batch())
    return run


def register() -> None:
    from agents import netenyahu

    netenyahu.HANDLERS[("supervisor", "review")] = handler()


# ------------------------------------------------------------------ used
def report(days: float = 30.0) -> dict:
    """Per check: flags the reviewer agreed with, flags it did not, and
    sentences he passed that it failed. None, not 0, where nothing was
    reviewed ([metering])."""
    try:
        reviews = store().reviews_since(time.time() - days * 86400) if exists() else []
    except Exception:  # noqa: BLE001
        reviews = []
    per: dict = {}
    for r in reviews:
        named = CHECK_OF.get(r["failure"], "")
        for check in [c for c in r["flagged"].split(",") if c]:
            entry = per.setdefault(check, {"flags_reviewed": 0, "agreed": 0, "missed": 0})
            entry["flags_reviewed"] += 1
            entry["agreed"] += int(named == check)
        if not r["flagged"] and named:
            per.setdefault(named, {"flags_reviewed": 0, "agreed": 0, "missed": 0})["missed"] += 1
    for entry in per.values():
        n = entry["flags_reviewed"]
        entry["agreement"] = round(entry["agreed"] / n, 3) if n else None
    return {"reviewed": len(reviews), "checks": per}


def disagreements(days: float = 30.0, limit: int = 50) -> list:
    """The cases to turn into tests (step 3): a flag the reviewer did not
    agree with, and a passed sentence the reviewer failed."""
    out = []
    for r in (store().reviews_since(time.time() - days * 86400) if exists() else []):
        named = CHECK_OF.get(r["failure"], "")
        flagged = [c for c in r["flagged"].split(",") if c]
        if r["failure"] == "unsure":
            continue
        if flagged and named not in flagged:
            out.append({"kind": "too strict", "checks": flagged, "reviewer": r["failure"],
                        "text": r["text"], "reason": r["reason"], "evidence": r["evidence"]})
        elif not flagged and named:
            out.append({"kind": "missed", "checks": [named], "reviewer": r["failure"],
                        "text": r["text"], "reason": r["reason"], "evidence": r["evidence"]})
    return out[:limit]
