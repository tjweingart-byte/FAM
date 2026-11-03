"""C1: Consuela's dedupe eval grows from real material (§407).

docs/AGENTS_TRAINING_PLAN.md, C1. The hand-written core of the eval
(`dedupe_eval.CASES`, §401) is the guide's never-duplicates table; this adds
cases made from what Consuela has actually kept - sentences from her report
(`housekeeper.db`, the dry run's choices included) and the archive's facts -
each changed **in code** in one known way, so its right answer is known
without anyone labelling it (decided item 30):

| Change | The pair is | What it tests |
|---|---|---|
| a number changed | different | code's number rule |
| a hedge added ("officials expect that ...") | different | forecast against outcome |
| a day or month moved ("Monday" → "Tuesday") | different | a moment the models must notice |
| a speaker swapped ("..., Smith said" → "..., Jones said") | different | one quote, two speakers |
| a named subject swapped for another kept name | different | two subjects, one claim |
| an attribution added (", according to reports.") | same | a real repeat in other words |

**And the pairs her two models disagreed on** in a night's dedupe (the first
look said "same", the second opinion did not; recorded as `disputed` in
`merges`): kept apart, so counted as "different" until a grade (C3) says
otherwise - the safe direction.

The sample is deterministic - sources in a fixed order by their text's hash,
at most `MAX_SOURCES` - so two runs over the same archive ask the same
questions and their scores compare. With no material (a fresh deployment)
nothing is added and the core still runs.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Optional

log = logging.getLogger("fam.agents.dedupe_cases")

#: Source sentences read, and grown cases made, at most.
MAX_SOURCES = 40
MAX_CASES = 80
#: A sentence shorter than this many words is too thin to change meaningfully.
MIN_WORDS = 6

_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_SAID = re.compile(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)?) (said|says|told)\b|"
                   r"\b(said|says) ([A-Z][a-z]+(?: [A-Z][a-z]+)?)\b")
_QUOTE = re.compile(r"[\"“”]")


def _order(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def material(store=None, archive=None) -> list:
    """Real sentences Consuela kept, from her report and the archive -
    deduplicated, in a fixed order, at most `MAX_SOURCES`."""
    texts: set = set()
    try:
        if store is None:
            from agents import housekeeper

            store = housekeeper.store()
        for row in store.recent(500):
            for item in row.get("kept") or []:
                if item.get("kind") in ("sentence", "figure", "quote"):
                    texts.add(str(item.get("text", "")).strip())
    except Exception as exc:  # noqa: BLE001 - no report is no material
        log.info("no report to grow the eval from: %s", exc)
    try:
        if archive is None:
            import history_archive as ha

            archive = ha.backend()
        if archive is not None:
            for fact in archive.facts_page("", 500):
                if fact.get("kind") in ("sentence", "figure", "quote"):
                    texts.add(str(fact.get("text", "")).strip())
    except Exception as exc:  # noqa: BLE001
        log.info("no archive to grow the eval from: %s", exc)
    usable = [t for t in texts if len(t.split()) >= MIN_WORDS]
    return sorted(usable, key=_order)[:MAX_SOURCES]


def _change_number(text: str) -> Optional[str]:
    match = _NUMBER.search(text)
    if not match:
        return None
    raw = match.group(0)
    if "." in raw:
        whole, part = raw.split(".", 1)
        new = f"{whole}.{(int(part) + 1) % (10 ** len(part)):0{len(part)}d}"
    else:
        new = str(int(raw.replace(",", "")) + 1)
    return text[:match.start()] + new + text[match.end():]


def _add_hedge(text: str) -> Optional[str]:
    from agents import dedupe

    if dedupe.hedged(text) or _QUOTE.search(text):
        return None
    return "Officials expect that " + text[0].lower() + text[1:]


def _move_moment(text: str) -> Optional[str]:
    for names in (_DAYS, _MONTHS):
        for i, name in enumerate(names):
            if re.search(rf"\b{name}\b", text):
                return re.sub(rf"\b{name}\b", names[(i + 1) % len(names)], text, count=1)
    return None


def _swap_speaker(text: str, pool: list) -> Optional[str]:
    match = _SAID.search(text)
    if not match:
        return None
    speaker = match.group(1) or match.group(4)
    other = next((p for p in pool if p not in text and p != speaker), None)
    return text.replace(speaker, other, 1) if other else None


def _swap_subject(text: str, pool: list) -> Optional[str]:
    from agents import dedupe

    named = [n for n in dedupe.names(text) if n not in _DAYS and n not in _MONTHS]
    if not named:
        return None
    first = min(named, key=text.find)
    other = next((p for p in pool if p not in text and p != first), None)
    return re.sub(rf"\b{re.escape(first)}\b", other, text, count=1) if other else None


def _attribute(text: str) -> Optional[str]:
    if _QUOTE.search(text) or re.search(r"\b(said|says|according to)\b", text):
        return None
    return text.rstrip(" .") + ", according to reports."


def grow(sources: Optional[list] = None, disputed: Optional[list] = None) -> list:
    """The grown cases, in `dedupe_eval.CASES`'s shape. Pure: material in,
    cases out."""
    from agents import dedupe

    sources = material() if sources is None else list(sources)
    pool = sorted({n for t in sources for n in dedupe.names(t)
                   if n not in _DAYS and n not in _MONTHS}, key=_order)
    cases = []
    for text in sources:
        made = (("number changed", "different", _change_number(text)),
                ("hedge added", "different", _add_hedge(text)),
                ("moment moved", "different", _move_moment(text)),
                ("speaker swapped", "different", _swap_speaker(text, pool)),
                ("subject swapped", "different", _swap_subject(text, pool)),
                ("attribution added", "same", _attribute(text)))
        for change, expected, other in made:
            if other and other != text:
                label = f"grown: {change}: {text[:40]}"
                cases.append((label, expected, (text, 0, "sentence"),
                              (other, 0, "sentence")))
    for pair in (disputed if disputed is not None else _disputed()):
        cases.append((f"disputed: {pair[0][:40]}", "different",
                      (pair[0], 0, "sentence"), (pair[1], 0, "sentence")))
    return cases[:MAX_CASES]


def _disputed() -> list:
    """(keeper text, repeat text) of every pair her two models disagreed on."""
    try:
        from agents import dedupe, housekeeper

        return [(m["keeper_text"], m["repeat_text"])
                for m in housekeeper.store().recent_merges(500)
                if m["verdict"] == dedupe.DISPUTED]
    except Exception:  # noqa: BLE001
        return []
