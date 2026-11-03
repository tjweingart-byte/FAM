"""Mclovin's sentence checks, while the writer writes (Phase 3, piece 2, §434).

docs/agent_rules/mclovin.md "always" 1, 3, 4 and 5, "never" 1, 3, 4 and 7:
every sentence is checked as it is written, against the two packets only -
the plugin packet (the evidence, the live block, the forecast) and Sonic's
archive packet, both of which are in `plan.evidence` / `plan.live` - never
memory, never a new search. A failing sentence goes back to the writer once
(`script_generator`'s one-sentence rewrite); a second failure drops it. No
model reads the whole script, and no model judges here at all: every check
is code (decided item 26). The grounding checker for facts and claims is
the next piece, asked of the owner first (item 34).

**Each check has a mode** (`SUPERVISOR_ENFORCE`): *enforced* checks send a
failing sentence back; the others only *count* - the verdict is recorded and
the sentence plays as written. The owner's rule for figures (§269, "measure
on audits first") is the reason for the mode, and the same caution applies
to the heuristic ones:

* `quote` (enforced) - a quoted span of three or more words appears in the
  packets word for word (case and punctuation aside), and, when the
  sentence names who said it, that name is beside the span in the source
  that holds it. Long quotes are counted (`quote_long`), never failed: §429
  left the length limit to the owner.
* `website` (enforced) - a hostname or "dot com" in speech.
* `advice` (enforced) - a recommendation in FAM's own voice about health,
  the law or money (C3P0's `RECOMMENDS`, §425), never a quoted or
  attributed one ("doctors say you should" is a fact about doctors).
* `superlative` (enforced) - "the first", "a record", "the biggest", "the
  only", "all-time": the same kind of word in a packet sentence that shares
  a name or two words with this one.
* `figures` (counted) - every number, `figure_guard`'s reader widened from
  markets to every episode, matched against every number in the packets;
  the archive's only for a sentence that says when.
* `dates` (counted) - "last night", "yesterday", "today", "this week": the
  packet source most like the sentence must be young enough for the phrase,
  by the listener's clock.
* `mixed` (counted) - a sentence whose names are held by two different
  stories' sources and by no one source together.

Never raises: a check that fails to run is `unchecked`, recorded, and the
sentence plays (decided item 32).
"""
from __future__ import annotations

import collections
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

log = logging.getLogger("fam.agents")

CHECKS = ("quote", "website", "advice", "superlative", "figures", "dates", "mixed")
#: Which checks send a failing sentence back by default. The rest count.
DEFAULT_ENFORCED = ("quote", "website", "advice", "superlative")
#: The named failure (agents.handoffs.FAILURES) each check reports.
FAILURE_OF = {
    "website": "website_named", "advice": "advice",
    "superlative": "unsupported_superlative", "figures": "number_not_in_packets",
    "dates": "wrong_date", "mixed": "stories_joined",
}
#: Quotes this many words or more are counted as long (§429 left the limit
#: to the owner; nothing fails on length).
QUOTE_LONG_WORDS = 40
#: A quoted span shorter than this is a term, not a quote ("a 'historic' deal").
QUOTE_MIN_WORDS = 3

SINCE_BOOT: "collections.Counter[str]" = collections.Counter()
_LOCK = threading.Lock()


def _count(kind: str, n: int = 1) -> None:
    with _LOCK:
        SINCE_BOOT[kind] += n


def enforced() -> frozenset:
    from config import settings

    raw = str(getattr(settings, "supervisor_enforce", "") or "")
    return frozenset(c.strip() for c in raw.split(",") if c.strip() in CHECKS)


# ------------------------------------------------------------------ the packets
@dataclass
class Block:
    """One packet source: its text, its date, and whether it is the archive's."""

    text: str
    published: Optional[datetime] = None
    archive: bool = False
    live: bool = False
    folded: str = ""
    words: frozenset = frozenset()
    names: frozenset = frozenset()


def _fold(text: str) -> str:
    text = (text or "").replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def _blocks(plan) -> list:
    import news_clusters
    import research

    out = []
    evidence = str(getattr(plan, "evidence", "") or "")
    archive_label = "Source type: " + research.TIER_LABELS["archive"]
    for chunk in re.split(r"(?m)^(?=SOURCE \d+\s*$)", evidence):
        if not chunk.strip():
            continue
        day = re.search(r"^Published:\s*(\d{4})-(\d{2})-(\d{2})", chunk, re.M)
        when = None
        if day:
            try:
                from datetime import timezone

                when = datetime(int(day[1]), int(day[2]), int(day[3]), tzinfo=timezone.utc)
            except ValueError:
                when = None
        out.append(Block(chunk, when, archive=archive_label in chunk))
    for name in ("live", "forecast"):
        block = getattr(plan, name, None)
        if block is None:
            continue
        try:
            out.append(Block(block.as_prompt_block(), None, live=True))
        except Exception:  # noqa: BLE001 - a block that cannot render vouches for nothing
            pass
    for block in out:
        block.folded = _fold(block.text)
        block.words = news_clusters.tokens(block.text)
        block.names = news_clusters.names(block.text)
    return out


# ------------------------------------------------------------------ verdicts
@dataclass(frozen=True)
class Check:
    """One check's verdict on one sentence."""

    check: str
    result: str                 # pass | fail | unchecked
    why: str = ""
    enforced: bool = False

    @property
    def failure(self) -> Optional[str]:
        if self.result != "fail":
            return None
        return FAILURE_OF.get(self.check) or (
            "quote_wrong_speaker" if "speaker" in self.why else "quote_not_said")


@dataclass
class SentenceVerdict:
    sentence: str
    checks: list = field(default_factory=list)
    seconds: float = 0.0

    @property
    def failing(self) -> list:
        """The enforced failures - what sends the sentence back."""
        return [c for c in self.checks if c.result == "fail" and c.enforced]

    @property
    def counted(self) -> list:
        return [c for c in self.checks if c.result == "fail" and not c.enforced]

    def reasons(self) -> str:
        return "; ".join(c.why for c in self.failing)


# ------------------------------------------------------------------ the checks
_QUOTED = re.compile(r'["“]([^"“”]{3,600})["”]')
_SAID = r"(?:said|says|told|tells|wrote|writes|added|adds|asked|argued|argues|declared|warned|insisted)"
_NAME = r"((?:[A-Z][\w'’.-]+)(?:\s+[A-Z][\w'’.-]+){0,3})"
_SPEAKER_AFTER = re.compile(r'["”]\s*,?\s*' + _NAME + r"\s+" + _SAID + r"\b")
_SPEAKER_AFTER2 = re.compile(r'["”]\s*,?\s*' + _SAID + r"\s+" + _NAME)
_SPEAKER_BEFORE = re.compile(_NAME + r"\s+" + _SAID + r"\s*,?\s*:?\s*[\"“]")
_PRONOUNS = frozenset({"He", "She", "They", "It", "We", "I", "The", "A", "An", "This"})


def _speaker(sentence: str) -> str:
    for pattern in (_SPEAKER_AFTER, _SPEAKER_AFTER2, _SPEAKER_BEFORE):
        m = pattern.search(sentence)
        if m:
            name = m.group(1).strip(" ,.")
            first = name.split()[0]
            if first in _PRONOUNS:
                return ""
            return name
    return ""


def check_quote(sentence: str, blocks: list) -> tuple:
    """(result, why). Quote spans of `QUOTE_MIN_WORDS`+ words only."""
    spans = [s for s in _QUOTED.findall(sentence) if len(_fold(s).split()) >= QUOTE_MIN_WORDS]
    if not spans:
        return "pass", ""
    speaker = _speaker(sentence)
    for span in spans:
        folded = _fold(span)
        if len(folded.split()) >= QUOTE_LONG_WORDS:
            _count("quote_long")
        holders = [b for b in blocks if folded and folded in b.folded]
        if not holders:
            return "fail", f"the quote \"{span[:80]}\" is not in the packets"
        if speaker:
            last = _fold(speaker).split()[-1]
            near = False
            for block in holders:
                at = block.folded.find(folded)
                window = block.folded[max(0, at - 400): at + len(folded) + 400]
                if re.search(r"\b" + re.escape(last) + r"\b", window):
                    near = True
                    break
            if not near:
                return "fail", (f"the packets do not give \"{span[:60]}\" to "
                                f"{speaker} (wrong speaker)")
    return "pass", ""


_WEBSITE = re.compile(
    r"\b[a-z0-9][a-z0-9-]*\.(?:com|org|net|gov|edu|io|co\.uk|co|ai|tv|news|info)\b"
    r"|\bdot (?:com|org|net|gov)\b|https?://|\bwww\b", re.I)


def check_website(sentence: str, _blocks: list) -> tuple:
    m = _WEBSITE.search(sentence)
    return ("fail", f"names a website ({m.group(0)})") if m else ("pass", "")


#: The fields where a recommendation is advice FAM never gives.
_ADVICE_FIELD = re.compile(
    r"\b(?:health|doctor|medic\w*|medicine|drug|dose|dosage|symptom\w*|treat\w*|"
    r"diet|vaccin\w*|pregnan\w*|therap\w*|surgery|pill\w*|"
    r"law|legal|lawyer|attorney|court|sue|lawsuit|contract|lease|tenant|landlord|"
    r"visa|custody|divorce|tax\w*|irs|"
    r"money|invest\w*|stock\w*|shares?|fund\w*|portfolio|retire\w*|401|ira|"
    r"mortgage|refinanc\w*|loan|debt|credit|savings?|crypto\w*|bitcoin|insurance|"
    r"budget|pension|bond\w*|etf\w*)\b", re.I)
_ATTRIBUTED = re.compile(
    r"\b(?:say|says|said|recommend\w*|advise\w*|urge\w*|guidance|guidelines|"
    r"according to|tell\w*|told)\b[^.;]{0,40}$", re.I)


def check_advice(sentence: str, _blocks: list, advice_request: bool = False) -> tuple:
    from agents.contextualizer import RECOMMENDS

    bare = _QUOTED.sub(" ", sentence)
    m = RECOMMENDS.search(bare)
    if not m:
        return "pass", ""
    if _ATTRIBUTED.search(bare[: m.start()]):
        return "pass", ""
    if not advice_request and not _ADVICE_FIELD.search(bare):
        return "pass", ""
    return "fail", f"advises the listener ({m.group(0)})"


_SUPERLATIVE = re.compile(
    r"\b(?:the|its|their|his|her|a|an)\s+(?:(?:first|only|biggest|largest|highest|"
    r"lowest|fastest|youngest|oldest|longest|most|fewest|worst|best)\b)"
    r"|\b(?:record|all-time|unprecedented|never before|ever)\b", re.I)
#: The family each superlative's support may use.
_FAMILIES = (("first",), ("only",), ("record", "all-time", "all time", "ever", "unprecedented",
                                     "never before"),
             ("biggest", "largest", "most", "highest", "best", "longest", "fastest",
              "oldest", "youngest"),
             ("lowest", "fewest", "worst", "smallest"))


def _family(word: str) -> tuple:
    word = word.lower()
    for family in _FAMILIES:
        if any(word.startswith(w) for w in family):
            return family
    return (word,)


def _sentences(text: str) -> list:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def check_superlative(sentence: str, blocks: list) -> tuple:
    import news_clusters

    m = _SUPERLATIVE.search(_QUOTED.sub(" ", sentence))
    if not m:
        return "pass", ""
    word = m.group(0).split()[-1].lower()
    family = _family(word)
    names = news_clusters.names(sentence)
    words = news_clusters.tokens(sentence)
    for block in blocks:
        for line in _sentences(block.text):
            low = line.lower()
            if not any(re.search(r"\b" + re.escape(w), low) for w in family):
                continue
            if (names & news_clusters.names(line)) or \
                    len(words & news_clusters.tokens(line)) >= 2:
                return "pass", ""
    return "fail", f"\"{m.group(0)}\" is not stated in the packets"


def check_figures(sentence: str, blocks: list, values=None) -> tuple:
    """Every number, widened from markets (§269): counted, never enforced by
    default. Years, ordinals, clock times and counts up to twelve said in
    words are left alone - a count of things is not a figure."""
    import figure_guard
    import score_guard

    text = " ".join(sentence.split())
    if score_guard._PAST.search(text) or score_guard._years_other_than_now(text):
        return "pass", ""
    fresh, archived = values
    approx = bool(figure_guard._APPROX.search(text))
    dated = bool(figure_guard._DATED.search(text))
    said = _numbers(text)
    for value, decimals in said:
        if figure_guard.vouched(value, decimals, approx, fresh):
            continue
        if dated and figure_guard.vouched(value, decimals, approx, archived):
            continue
        return "fail", f"the number {value:g} is not in the packets"
    return "pass", ""


_DIGITS = re.compile(r"(?<![\w.])\$?(\d[\d,]*(?:\.\d+)?)(?![\w])")


def _numbers(text: str) -> list:
    """`(value, decimals)` for each number said - word numbers through
    `figure_guard`'s reader, digits directly; never a year, a time, an
    ordinal, or a small count in words."""
    import figure_guard

    out = []
    scrubbed = figure_guard._without_scaffolding(text)
    for value, decimals, _unit in figure_guard.figures_in(scrubbed):
        out.append((value, decimals))
    if out:
        return out
    tokens = re.findall(r"\$?\d[\d,]*(?:\.\d+)?|[a-z]+(?:-[a-z]+)?", scrubbed.lower())
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if re.match(r"^\$?\d", token):
            try:
                value = float(token.lstrip("$").replace(",", ""))
            except ValueError:
                i += 1
                continue
            decimals = len(token.split(".")[1]) if "." in token else 0
            out.append((value, decimals))
            i += 1
            continue
        parts = token.split("-")
        if figure_guard._is_word_number(parts[0]):
            value, decimals, j = figure_guard._read_number(tokens, i)
            if value is not None and j > i:
                if not (value <= 12 and j == i + 1):
                    out.append((value, decimals))
                i = j
                continue
        i += 1
    return out


#: Relative times, and the oldest a source may be (days) for each.
_RELATIVE = (
    (re.compile(r"\b(?:this morning|this afternoon|this evening|today|tonight)\b", re.I), 1),
    (re.compile(r"\b(?:last night|yesterday)\b", re.I), 2),
    (re.compile(r"\b(?:this week|this weekend|earlier this week)\b", re.I), 7),
    (re.compile(r"\b(?:last week|last weekend)\b", re.I), 14),
)


def check_dates(sentence: str, blocks: list, now: Optional[datetime] = None) -> tuple:
    import news_clusters

    phrase = limit = None
    for pattern, days in _RELATIVE:
        m = pattern.search(sentence)
        if m:
            phrase, limit = m.group(0), days
            break
    if phrase is None:
        return "pass", ""
    names = news_clusters.names(sentence)
    words = news_clusters.tokens(sentence)
    scored = []
    for block in blocks:
        if block.live:
            return "pass", ""      # a live block is now by definition
        score = 2 * len(names & block.names) + len(words & block.words)
        if score >= 3 and block.published is not None:
            scored.append((score, block))
    if not scored:
        return "pass", ""          # nothing to judge it by
    if now is None:
        import listener_clock

        now = listener_clock.now()
    best = max(s for s, _b in scored)
    young = [b for s, b in scored if s >= best - 1 and not b.archive and
             (now.date() - b.published.date()).days <= limit]
    if young:
        return "pass", ""
    oldest_needed = min((now.date() - b.published.date()).days for _s, b in scored)
    return "fail", (f"\"{phrase}\" but the source most like it is "
                    f"{oldest_needed} days old")


def check_mixed(sentence: str, blocks: list) -> tuple:
    """A sentence whose names come from two different stories and are held
    together by no one source."""
    import news_clusters

    names = {n for n in news_clusters.names(sentence)
             if any(n in b.names for b in blocks)}
    if len(names) < 2:
        return "pass", ""
    if any(names <= b.names for b in blocks):
        return "pass", ""
    holders = [b for b in blocks if names & b.names]
    stories: list = []
    for block in holders:
        title = re.search(r"^Title:\s*(.*)$", block.text, re.M)
        fingerprint = news_clusters.tokens(title.group(1) if title else block.text[:200])
        for story in stories:
            if news_clusters.same_story(story[0], fingerprint):
                story[1].append(block)
                break
        else:
            stories.append((fingerprint, [block]))
    if len(stories) < 2:
        return "pass", ""
    # Two stories each hold part of the sentence's names, and none holds all.
    covered = [set().union(*(b.names for b in story[1])) & names for story in stories]
    if any(names <= c for c in covered):
        return "pass", ""
    return "fail", ("joins " + " and ".join(sorted(names)[:4])
                    + " from two different stories, linked by no one source")


# ------------------------------------------------------------------ one episode
class Checker:
    """One per episode, in `_ScriptReader`, only while Mclovin works on it."""

    def __init__(self, plan, notes=None, now: Optional[datetime] = None) -> None:
        import figure_guard

        self.plan = plan
        self.notes = notes
        self.enforce = enforced()
        self.now = now
        self.verdicts: list = []
        self.repairs = 0
        #: What `ScriptNotes.supervisor` shows, filled in place (§434).
        self.summary: dict = {"rows": [], "repaired": [], "dropped": [],
                              "enforced": sorted(self.enforce)}
        try:
            self.blocks = _blocks(plan)
            fresh = "\n".join(b.text for b in self.blocks if not b.archive)
            old = "\n".join(b.text for b in self.blocks if b.archive)
            self.values = (figure_guard.material_values(fresh),
                           figure_guard.material_values(old))
        except Exception:  # noqa: BLE001
            log.warning("Mclovin could not read the packets; sentences play unchecked",
                        exc_info=True)
            self.blocks, self.values = None, ([], [])
        self.research_plan = getattr(notes, "research_plan", None)

    def check(self, sentence: str) -> SentenceVerdict:
        from config import settings

        verdict = SentenceVerdict(sentence)
        started = time.perf_counter()
        limit = float(getattr(settings, "supervisor_timeout_seconds", 1.0) or 1.0)
        for name in CHECKS:
            if self.blocks is None:
                verdict.checks.append(Check(name, "unchecked", "the packets could not be read"))
                continue
            if time.perf_counter() - started > limit:
                # Past the ceiling: the rest plays unchecked (decided item 32).
                verdict.checks.append(Check(name, "unchecked", "out of time"))
                continue
            try:
                if name == "advice":
                    result, why = check_advice(sentence, self.blocks, bool(
                        getattr(self.research_plan, "advice_request", False)))
                elif name == "figures":
                    result, why = check_figures(sentence, self.blocks, self.values)
                elif name == "dates":
                    result, why = check_dates(sentence, self.blocks, self.now)
                else:
                    result, why = _BY_NAME[name](sentence, self.blocks)
            except Exception as exc:  # noqa: BLE001
                result, why = "unchecked", f"the check failed: {type(exc).__name__}"
            verdict.checks.append(Check(name, result, why, name in self.enforce))
        verdict.seconds = time.perf_counter() - started
        for c in verdict.checks:
            _count(f"{c.check}_{c.result}")
        if any(c.result == "unchecked" for c in verdict.checks):
            _count("sentences_unchecked")
        _count("sentences")
        index = len(self.verdicts)
        self.verdicts.append(verdict)
        for c in verdict.checks:
            if c.result != "pass":
                self.summary["rows"].append({
                    "sentence": index, "check": c.check, "result": c.result,
                    "enforced": c.enforced, "failure": c.failure, "why": c.why,
                    "text": sentence[:240]})
        return verdict

    def nearest(self, sentence: str, n: int = 3) -> list:
        """The packet sources most like a sentence, for the writer's rewrite."""
        import news_clusters

        names = news_clusters.names(sentence)
        words = news_clusters.tokens(sentence)
        scored = sorted(((2 * len(names & b.names) + len(words & b.words), i, b)
                         for i, b in enumerate(self.blocks or [])),
                        key=lambda t: (-t[0], t[1]))
        return [b for score, _i, b in scored[:n] if score > 0]

def repair_prompt(checker: "Checker", sentence: str, why: str) -> str:
    """The writer's rewrite request: the sentence, what failed, and the
    sources most like it, in the packet's own words (never Mclovin's)."""
    sources = "\n\n".join(b.text.strip()[:2500] for b in checker.nearest(sentence))
    return (f"The sentence:\n{sentence}\n\nWhat the check found: {why}\n\n"
            f"The sources:\n{sources or '(none of the sources is about this)'}")


_BY_NAME = {"quote": check_quote, "website": check_website,
            "superlative": check_superlative, "mixed": check_mixed}


def report() -> dict:
    with _LOCK:
        counts = dict(SINCE_BOOT)
    sentences = counts.get("sentences", 0)
    return {"since_boot": counts, "enforced": sorted(enforced()),
            "unchecked_rate": (round(counts.get("sentences_unchecked", 0) / sentences, 3)
                               if sentences else None)}


def reset() -> None:
    with _LOCK:
        SINCE_BOOT.clear()
