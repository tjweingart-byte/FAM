"""S1: Sonic's recall eval, built from the archive itself (§407).

docs/AGENTS_TRAINING_PLAN.md, S1. Nothing about Sonic was measured; this
measures him on what the archive holds, with no labels and no model call:

* **Facts.** For a stored fact, code writes the question it answers from
  what is stored with it - its subject and its month ("Eagles in February
  2025") - files the request under its node, sets the listener's clock a
  month after it was published (so it lies before a week's search window),
  and runs Sonic's own `retrieve` through his read-only connection.
* **Results and values.** For a stored reading of the live series, the same
  with a result lookup between the day before and the day of the reading.

What is counted, per case and in total:

| Measure | Meaning | Hard rule? |
|---|---|---|
| `found` | the fact or reading the question came from is in the packet | no - the recall rate, to be tuned (S3) |
| `leaks` | an item from inside the search's window - history passed off as now | **yes: must be 0** (sonic.md, never 6) |
| `order_errors` | a packet not oldest first | **yes: must be 0** (always 5) |
| `beyond_family` | items from outside the node, its parent and children | no - by-meaning finds, worth watching |
| `empty`, `fell_back`, `ms` | nothing came back; today's recall stood in; time | no |

**Passing** means the hard rules held. The sample is deterministic - facts
and readings in a fixed order, at most `MAX_FACTS` and `MAX_READINGS` - so
runs compare. Results are kept in `housekeeper.db` (`recall_evals`).
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

MAX_FACTS = 50
MAX_READINGS = 20
#: The search window the eval's questions are asked with, and how long after
#: a fact's publication the listener's clock is set.
WINDOW_DAYS = 7
ASKED_AFTER_DAYS = 30


def _order(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _day(text: str) -> Optional[datetime]:
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _brief(subject: str, domain: str = "", named: str = "") -> SimpleNamespace:
    """A brief for one question. `named`: the lookup C3P0 named, as his call
    would (§375) - else the router and code choose, as on any episode."""
    said = {"archive": {"lookup": named}} if named else None
    return SimpleNamespace(subject=subject, live_domain=domain, live_entity="",
                           outcome_dependent=False, place="", contextualized=said,
                           recency_days=WINDOW_DAYS, degraded=False, query="")


def _family(node: str) -> set:
    from agents import retriever

    return {node, *retriever._family(node)}


def fact_cases(archive) -> list:
    """Stored facts with a node, a subject or a name, and a date."""
    from agents import dedupe

    out = []
    for fact in archive.facts_page("", 2000):
        if fact.get("kind") not in ("sentence", "figure", "quote"):
            continue
        when = _day(fact.get("published", ""))
        subject = fact.get("subject") or next(iter(sorted(dedupe.names(fact["text"]))), "")
        if not (fact.get("node_id") and when and subject):
            continue
        out.append({"id": fact["id"], "node": fact["node_id"], "subject": subject,
                    "when": when, "text": fact["text"]})
    return sorted(out, key=lambda c: _order(c["id"]))[:MAX_FACTS]


def reading_cases(archive) -> list:
    try:
        rows = archive.readings_page(1000)
    except Exception:  # noqa: BLE001 - a backend without readings
        return []
    return sorted(rows, key=lambda r: _order(r.id))[:MAX_READINGS]


def _score(case_id: str, facts: list, now: datetime, node: str) -> dict:
    window_start = (now - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    dates = [f.published for f in facts]
    family = _family(node) if node else set()
    return {"found": any(f.id == case_id for f in facts),
            "leaks": sum(1 for d in dates if d and d >= window_start),
            "order_error": dates != sorted(dates),
            "beyond_family": sum(1 for f in facts if node and f.node_id not in family),
            "items": len(facts)}


def run(record: bool = True) -> dict:
    """Run every case through Sonic. Blocking; never sends anything out."""
    import history_archive as ha
    from agents import housekeeper, retriever
    from agents.handoffs import ArchiveRequest, ResearchPlan

    archive = ha.backend()
    if archive is None:
        return {"ran": False, "why": f"no archive: {ha.diagnose()[1]}"}
    if retriever.reader() is None:
        return {"ran": False, "why": "Sonic has no read-only connection here "
                                     "(MONGODB_READ_URI on Atlas)"}
    details = []
    for case in fact_cases(archive):
        now = case["when"] + timedelta(days=ASKED_AFTER_DAYS, hours=12)
        text = f"{case['subject']} in {case['when']:%B %Y}"
        plan = ResearchPlan(entity=case["subject"], node_id=case["node"],
                            archive=ArchiveRequest(node_id=case["node"], lookup="background"))
        details.append(_run_one("fact", case["id"], case["node"], text, plan,
                                _brief(case["subject"]), now))
    for reading in reading_cases(archive):
        when = reading.as_of.astimezone(timezone.utc)
        now = when + timedelta(days=ASKED_AFTER_DAYS)
        text = f"{reading.subject} result on {when:%B %d, %Y}"
        plan = ResearchPlan(entity=reading.subject, node_id=reading.node_id or "",
                            archive=ArchiveRequest(
                                node_id=reading.node_id or "", lookup="result_between",
                                start=(when - timedelta(days=1)).date().isoformat(),
                                end=when.date().isoformat()))
        # A result question is one C3P0 names the lookup for; the facts'
        # questions go through the router, so both paths are measured.
        details.append(_run_one("reading", reading.id, reading.node_id or "", text, plan,
                                _brief(reading.subject, reading.domain,
                                       named="result_between"), now))
    total = len(details)
    summary = {
        "ran": True, "total": total,
        "found": sum(d["found"] for d in details),
        "leaks": sum(d["leaks"] for d in details),
        "order_errors": sum(int(d["order_error"]) for d in details),
        "beyond_family": sum(d["beyond_family"] for d in details),
        "empty": sum(1 for d in details if not d["items"]),
        "fell_back": sum(int(d["fell_back"]) for d in details),
        "ms": round(sum(d["ms"] for d in details) / total, 1) if total else None,
        "by_kind": {k: {"total": sum(1 for d in details if d["kind"] == k),
                        "found": sum(d["found"] for d in details if d["kind"] == k)}
                    for k in ("fact", "reading")},
        # Routing, measured beside retrieval: which lookups were chosen, by whom.
        "chosen_by": {who: sum(1 for d in details if d["chosen_by"] == who)
                      for who in ("c3p0", "router", "code")},
    }
    summary["found_rate"] = round(summary["found"] / total, 3) if total else None
    summary["passed"] = bool(total) and not summary["leaks"] and not summary["order_errors"]
    summary["details"] = details
    if record:
        housekeeper.store().note_recall_eval(summary)
    return summary


def _run_one(kind: str, case_id: str, node: str, text: str, plan, brief,
             now: datetime) -> dict:
    from agents import retriever

    before = retriever.STATS["fell_back"]
    notes = SimpleNamespace()
    started = time.perf_counter()
    facts = retriever.retrieve(plan, brief, text, notes=notes, now=now)
    ms = (time.perf_counter() - started) * 1000
    chose = getattr(notes, "archive_lookup", None) or {}
    return {"kind": kind, "case": case_id, "question": text, "node": node,
            "lookup": chose.get("lookup", ""), "chosen_by": chose.get("chosen_by", ""),
            "fell_back": retriever.STATS["fell_back"] > before, "ms": round(ms, 1),
            **_score(case_id, facts, now, node)}
