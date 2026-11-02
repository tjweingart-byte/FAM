"""Consuela's dedupe: repeats marked, never lost (§401).

Phase 2, piece 4 (consuela.md, always 5, 7, 8, 9, 11; never 2, 7; decided
item 33; docs/FAM_AGENTS.md, Housekeeper, "Dedupe order"). Each night her
task looks at the facts kept in the last two days and asks, for each, whether
an earlier fact under the same node already says it.

**Code decides almost everything** (`code_verdict`), in this order, and a
model is never asked about a number:

1. a live record is never deduplicated (scores and prices are a series);
2. two sentences of one article are not repeats of each other;
3. an undated fact, or two more than `WINDOW_DAYS` apart, are different
   moments;
4. the same words (case, spacing and punctuation aside) are a repeat -
   with a number in them, only on the same day ("Apple closed at $201.50"
   on two days is a price on two days);
5. a forecast and an outcome are different (a hedge in one and not the
   other, or two figure statuses that differ);
6. different numbers are different facts - and when the two sentences name
   the same things, sit very close in meaning and were published the same
   day, they are a **conflict**: both kept, flagged (always 9);
7. a quote is a repeat only word for word ("one quote, two speakers" is
   flagged, never merged);
8. the same numbers are a repeat only when the names match and the meaning
   is very close; otherwise different - a figure is never merged on a
   model's say (never 2);
9. what is not alike is different.

**What is left - plain sentences, alike, the same moment - is asked**: the
first look on `HOUSEKEEPER_MODEL` (Haiku 5.5), and every pair it calls the
same goes to `HOUSEKEEPER_REVIEW_MODEL` (Sonnet 5.5). **A merge needs both
to say "same"**; "unsure" or a disagreement keeps both, the safe outcome.

**A merge is a reversible mark** (always 8 and 11): the better-sourced fact
stays (grade, then the earlier publication), the repeat becomes inactive
with `superseded_by`, when and why, and the keeper gains its link and count,
never its text. `history_archive.restore` undoes it; it is deleted for good
only `HOUSEKEEPER_SUPERSEDED_DAYS` (30) after the mark.

**A dry run until the owner says otherwise** (`HOUSEKEEPER_DEDUPE_DRY_RUN=1`):
the merges she would make are reported (`housekeeper.db`, `merges`), nothing
is marked. With it off, she still marks nothing unless her automated eval
(`agents/dedupe_eval.py`, `tools/consuela_dedupe_eval.py`) has passed - no
wrong merge, every call answered - for the models now set (decided item 33).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from config import settings

log = logging.getLogger("fam.agents.dedupe")

AGENT = "housekeeper"
TASK = "dedupe"

SAME = "same"
DIFFERENT = "different"
CONFLICT = "conflict"
ASK = "ask"

#: Sources, best first (`research.credibility`).
GRADE_RANK = {"primary": 0, "established": 1, "unverified": 2}
#: Cosine similarity a pair needs to be worth a look at all.
CANDIDATE_SIMILARITY = 0.80
#: Cosine similarity code needs before it calls two figures one fact, or two
#: different figures a conflict.
FIGURE_SIMILARITY = 0.90
#: Two facts further apart than this are different moments.
WINDOW_DAYS = 3
#: The night's look covers facts kept in the last this-many days.
NEW_FACTS_DAYS = 2
#: Facts read per run, and neighbours per fact, at most.
MAX_NEW_FACTS = 500
MAX_NEIGHBOURS = 50
#: Pairs per model call.
PAIRS_PER_CALL = 20

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_QUOTE = re.compile(r"[\"“”]")
_PUNCT = re.compile(r"[^\w\s]")


# ------------------------------------------------------------------ code's half
def normalise(text: str) -> str:
    return " ".join(_PUNCT.sub(" ", (text or "").lower()).split())


def numbers(text: str) -> tuple:
    """Every number written in digits, in order, without thousands commas."""
    return tuple(n.replace(",", "") for n in _NUMBER.findall(text or ""))


#: Capitalised words that open sentences rather than name anything.
_OPENERS = frozenset({"The", "A", "An", "On", "In", "At", "According", "It", "Its",
                      "This", "That", "These", "Those", "He", "She", "They", "We",
                      "But", "And", "After", "Before", "For", "By", "As", "With",
                      "Earlier", "Later", "Meanwhile", "Also", "Then", "There"})


def names(text: str) -> frozenset:
    """The capitalised words a sentence names, wherever they stand - the
    first word too ("Apple closed..." names Apple), but not a word that only
    opens the sentence ("The", "On"). Erring toward more names keeps two
    facts apart, which is the safe direction."""
    words = [w.strip("'’").removesuffix("'s").removesuffix("’s")
             for w in _WORD.findall(text or "")]
    return frozenset(w for w in words if w and w[0].isupper() and w not in _OPENERS)


def hedged(text: str) -> bool:
    from agents import housekeeper

    return bool(housekeeper.HEDGE.search(text or ""))


def _day(fact: dict) -> Optional[float]:
    stamp = float(fact.get("published_ts") or 0.0)
    return stamp / 86400.0 if stamp > 0 else None


def code_verdict(a: dict, b: dict, similarity: float) -> tuple:
    """(verdict, why) for a pair: SAME, DIFFERENT, CONFLICT, or ASK."""
    if "live" in (a.get("kind"), b.get("kind")):
        return DIFFERENT, "a live record is never deduplicated"
    if a.get("url") and a.get("url") == b.get("url"):
        return DIFFERENT, "two sentences of one article"
    day_a, day_b = _day(a), _day(b)
    if day_a is None or day_b is None:
        return DIFFERENT, "undated"
    if abs(day_a - day_b) > WINDOW_DAYS:
        return DIFFERENT, "different moments"
    text_a, text_b = a.get("text", ""), b.get("text", "")
    if normalise(text_a) == normalise(text_b):
        # The same words are a repeat - but "Apple closed at $201.50" on two
        # days is a price on two days, so a sentence with a number repeats
        # only on the same day.
        if numbers(text_a) and int(day_a) != int(day_b):
            return DIFFERENT, "the same words on different days"
        return SAME, "the same words"
    status_a = (a.get("figure") or {}).get("status", "")
    status_b = (b.get("figure") or {}).get("status", "")
    if hedged(text_a) != hedged(text_b) or (status_a and status_b and status_a != status_b):
        return DIFFERENT, "a forecast and an outcome"
    num_a, num_b = numbers(text_a), numbers(text_b)
    same_names = names(text_a) == names(text_b)
    if sorted(num_a) != sorted(num_b):
        if (num_a and num_b and same_names and similarity >= FIGURE_SIMILARITY
                and int(day_a) == int(day_b)):
            return CONFLICT, "two sources, two values, one moment"
        return DIFFERENT, "different numbers"
    if _QUOTE.search(text_a) or _QUOTE.search(text_b):
        return DIFFERENT, "a quote is merged only word for word"
    if num_a:
        if same_names and similarity >= FIGURE_SIMILARITY:
            return SAME, "the same figures, names and moment"
        return DIFFERENT, "a figure is never merged on a model's say"
    if similarity < CANDIDATE_SIMILARITY:
        return DIFFERENT, "not alike"
    return ASK, ""


def keeper_of(a: dict, b: dict) -> tuple:
    """(keeper, repeat): the better source, then the earlier publication."""
    def rank(f):
        return (GRADE_RANK.get(f.get("grade", ""), 3),
                float(f.get("published_ts") or 0.0) or float("inf"),
                float(f.get("created") or 0.0), f.get("id", ""))
    return (a, b) if rank(a) <= rank(b) else (b, a)


def similarity_of(a: dict, b: dict) -> float:
    va, vb = a.get("vector"), b.get("vector")
    if not va or not vb or len(va) != len(vb):
        return 0.0
    return float(sum(x * y for x, y in zip(va, vb)))


# ------------------------------------------------------------------ the models' half
class PairVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pair: int
    verdict: Literal["same", "different", "unsure"]


class Verdicts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pairs: list[PairVerdict]


INSTRUCTIONS = (
    "For each numbered pair, say whether the two sentences state the same "
    "fact. Answer `same` only if they make the same claim about the same "
    "subject at the same moment. Two days' prices, two quarters' revenue, a "
    "first count and a revised one, a prediction and a result, or two sources "
    "that disagree are `different`. The same words attributed to two "
    "different speakers are `different`. When in doubt, answer `unsure`; FAM "
    "then keeps both, which is the safe outcome. Answer for every pair."
)


def material(pairs: list) -> str:
    parts = []
    for i, (a, b) in enumerate(pairs, 1):
        parts += [f"PAIR {i}",
                  f"A (published {a.get('published') or 'unknown'}): {a.get('text', '')}",
                  f"B (published {b.get('published') or 'unknown'}): {b.get('text', '')}",
                  ""]
    return "\n".join(parts)


async def _ask(pairs: list, model_setting: str, task: str) -> Optional[dict]:
    """{index: verdict} for every pair, or None when the call fell back or
    answered badly twice."""
    from agents import models

    request = models.AgentRequest(
        agent=AGENT, task=task, switch_setting="housekeeper",
        model_setting=model_setting, rule_book="consuela.md", schema=Verdicts,
        instructions=INSTRUCTIONS, material=(("evidence", material(pairs)),),
        timeout=float(settings.housekeeper_timeout_seconds),
        max_tokens=int(settings.housekeeper_max_tokens), effort="low",
        surface="background")
    for _attempt in range(2):
        result = await models.ask(request)
        if not result.ok:
            return None
        got = {p.pair: p.verdict for p in result.value.pairs}
        if (sorted(got) == list(range(1, len(pairs) + 1))
                and len(got) == len(result.value.pairs)):
            return got
    return None


@dataclass
class Decision:
    a: dict
    b: dict
    verdict: str
    why: str
    #: What each model said, when asked: "" when not asked.
    first: str = ""
    review: str = ""


async def decide(pairs: list) -> tuple:
    """(decisions, fell_back): every pair's verdict, code first, then the two
    models on what code left. A pair whose model call fell back is kept -
    never merged - and `fell_back` says so."""
    decisions, asks = [], []
    for a, b, similarity in pairs:
        verdict, why = code_verdict(a, b, similarity)
        decision = Decision(a, b, verdict, why)
        decisions.append(decision)
        if verdict == ASK:
            asks.append(decision)
    asks = asks[:max(0, int(settings.housekeeper_dedupe_pairs))]
    fell_back = False
    for start in range(0, len(asks), PAIRS_PER_CALL):
        chunk = asks[start:start + PAIRS_PER_CALL]
        first = await _ask([(d.a, d.b) for d in chunk], "housekeeper_model", TASK)
        if first is None:
            fell_back = True
            for d in chunk:
                d.verdict, d.why = DIFFERENT, "the first look fell back: both kept"
            continue
        for i, d in enumerate(chunk, 1):
            d.first = first[i]
        sames = [d for d in chunk if d.first == "same"]
        for d in chunk:
            if d.first != "same":
                d.verdict, d.why = DIFFERENT, f"first look: {d.first}"
        if not sames:
            continue
        review = await _ask([(d.a, d.b) for d in sames], "housekeeper_review_model",
                            "dedupe_review")
        if review is None:
            fell_back = True
            for d in sames:
                d.verdict, d.why = DIFFERENT, "the second opinion fell back: both kept"
            continue
        for i, d in enumerate(sames, 1):
            d.review = review[i]
            d.verdict, d.why = ((SAME, "both models: same") if d.review == "same"
                                else (DIFFERENT, f"second opinion: {d.review}"))
    for d in decisions:
        if d.verdict == ASK:   # past this run's pair limit
            d.verdict, d.why = DIFFERENT, "not asked this run: kept"
    return decisions, fell_back


# ------------------------------------------------------------------ the run
def models_key() -> str:
    """The two model settings an eval result belongs to."""
    return f"{settings.housekeeper_model}|{settings.housekeeper_review_model}"


def eval_passed() -> tuple:
    """(passed, why): the latest eval for the models now set had no wrong
    merge and every call answered (decided item 33)."""
    from agents import housekeeper

    try:
        latest = housekeeper.store().latest_eval(models_key())
    except Exception as exc:  # noqa: BLE001
        return False, f"the eval could not be read: {exc}"
    if latest is None:
        return False, "no eval has run for these models"
    if latest["fell_back"]:
        return False, "the last eval had calls that fell back"
    if latest["wrong_merges"]:
        return False, f"the last eval made {latest['wrong_merges']} wrong merge(s)"
    return True, ""


def _pairs(archive, facts: list) -> list:
    """(a, b, similarity) for each of `facts` and each fact near it."""
    out, seen = [], set()
    window = WINDOW_DAYS * 86400.0
    for fact in facts:
        if not fact.get("vector") or not fact.get("published_ts"):
            continue
        ts = float(fact["published_ts"])
        for other in archive.facts_near(fact.get("node_id", ""), fact.get("subject", ""),
                                        ts - window, ts + window, MAX_NEIGHBOURS):
            key = tuple(sorted((fact["id"], other["id"])))
            if other["id"] == fact["id"] or key in seen or not other.get("vector"):
                continue
            seen.add(key)
            similarity = similarity_of(fact, other)
            if (similarity < CANDIDATE_SIMILARITY
                    and normalise(fact["text"]) != normalise(other["text"])):
                continue
            out.append((fact, other, similarity))
    return out


async def run(now: Optional[float] = None, facts: Optional[list] = None) -> dict:
    """One night's dedupe over the facts kept in the last two days - or, for
    the full comb (§402), over `facts`. Returns what it did. Never raises."""
    import history_archive as ha
    from agents import housekeeper

    now = now or time.time()
    live = not settings.housekeeper_dedupe_dry_run
    summary = {"pairs": 0, "same": 0, "conflicts": 0, "asked": 0, "marked": 0,
               "purged": 0, "mode": "live" if live else "dry_run"}
    try:
        if not ha.consuela_runs():
            return {**summary, "why": "Consuela may not run"}
        archive = ha.backend()
        if archive is None:
            return {**summary, "why": "no archive"}
        if live:
            passed, why = eval_passed()
            if not passed:
                live = False
                summary.update(mode="dry_run", why=f"kept to the dry run: {why}")
        if facts is None:
            facts = await asyncio.to_thread(
                archive.dedupe_facts, now - NEW_FACTS_DAYS * 86400.0, MAX_NEW_FACTS)
        pairs = await asyncio.to_thread(_pairs, archive, facts)
        decisions, fell_back = await decide(pairs)
        summary.update(pairs=len(pairs), fell_back=fell_back,
                       asked=sum(1 for d in decisions if d.first))
        gone: set = set()
        for d in decisions:
            if d.verdict == CONFLICT:
                summary["conflicts"] += 1
            keeper, repeat = keeper_of(d.a, d.b)
            if d.verdict == SAME:
                summary["same"] += 1
                if repeat["id"] in gone or keeper["id"] in gone:
                    continue   # a chain: settled on a later night
                if live and await asyncio.to_thread(
                        archive.supersede, repeat["id"], keeper["id"], d.why, now):
                    summary["marked"] += 1
                gone.add(repeat["id"])
            if d.verdict in (SAME, CONFLICT):
                await asyncio.to_thread(
                    housekeeper.store().note_merge, keeper, repeat, d.verdict, d.why,
                    summary["mode"], d.first, d.review, now)
        if live:
            before = now - float(settings.housekeeper_superseded_days) * 86400.0
            summary["purged"] = await asyncio.to_thread(archive.purge_superseded, before)
        return summary
    except Exception as exc:  # noqa: BLE001 - a run never takes the loop down
        log.exception("consuela's dedupe failed")
        return {**summary, "why": f"{type(exc).__name__}: {exc}"}


async def handler(_task: str) -> None:
    summary = await run()
    log.info("consuela's dedupe: %s", summary)
