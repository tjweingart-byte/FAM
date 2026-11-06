"""Mclovin's sentence checks, while the writer writes (Phase 3, piece 2, §435).

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
  that holds it.
* `quote_length` (enforced since mclovin.md v2 was signed, §441) - a quote
  is at most one sentence (the owner, 2026-11-03, §436: copyright).
* `website` (enforced) - a hostname or "dot com" in speech.
* `advice` (enforced) - a recommendation in FAM's own voice about health,
  the law or money (C3P0's `RECOMMENDS`, §425), never a quoted or
  attributed one ("doctors say you should" is a fact about doctors).
* `superlative` (counted, the owner's choice, §436) - "the first", "a record", "the biggest", "the
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

CHECKS = ("quote", "quote_length", "website", "advice", "superlative", "figures",
          "dates", "mixed")
#: Which checks send a failing sentence back by default. The rest count.
#: Set by the planted-mistake eval (§440, step 2: `tools/mclovin_eval.py`):
#: a check enforces once it catches 90% of its planted mistakes and flags
#: at most 1% of clean sentences. Figures (85%, 1.5%) still count. The
#: one-sentence quote limit (100%, 0%) enforces since the owner signed
#: mclovin.md v2 (§441).
DEFAULT_ENFORCED = ("quote", "quote_length", "website", "advice", "superlative", "dates",
                    "mixed")
#: The named failure (agents.handoffs.FAILURES) each check reports.
#: The grounding checker's check (§440, step 5), run only when one is
#: plugged in (`agents.grounding`); none is yet.
SUPPORT = "support"
FAILURE_OF = {
    "support": "unsupported_changing_fact",
    "quote_length": "quote_too_long", "website": "website_named", "advice": "advice",
    "superlative": "unsupported_superlative", "figures": "number_not_in_packets",
    "dates": "wrong_date", "mixed": "stories_joined",
}
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
    return frozenset(c.strip() for c in raw.split(",") if c.strip() in CHECKS + (SUPPORT,))


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
    #: Every number the source states, digits and words, read once (§440).
    values: tuple = ()


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
        # Names from the source's own text, never the packet's labels
        # ("Title", "Published", "Source type", "Key evidence") or a
        # title-case headline, where every word is capitalised: with them
        # every source shared names and two stories read as one (§440).
        body = block.text.split("Key evidence:", 1)[1] if "Key evidence:" in block.text \
            else block.text
        block.names = news_clusters.names(body)
        block.values = _material_values(block.text)
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
    def unsure(self) -> list:
        """Enforced checks that could not decide - the grounding checker's
        middle band, which takes the small model's second look."""
        return [c for c in self.checks if c.result == "unsure" and c.enforced]

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
        holders = [b for b in blocks if folded and folded in b.folded]
        if not holders:
            return "fail", f"the quote \"{span[:80]}\" is not in the packets"
        if speaker:
            last = _fold(speaker).split()[-1]
            near = False
            for block in holders:
                # Beside any place the quote appears, never only the first
                # (§440, step 3: a quote repeated in a headline lost its speaker).
                for at in [m.start() for m in re.finditer(re.escape(folded), block.folded)]:
                    window = block.folded[max(0, at - 400): at + len(folded) + 400]
                    if re.search(r"\b" + re.escape(last) + r"\b", window):
                        near = True
                        break
                if near:
                    break
            if not near:
                return "fail", (f"the packets do not give \"{span[:60]}\" to "
                                f"{speaker} (wrong speaker)")
    return "pass", ""


#: Where a quote's sentence ends inside it - never at an abbreviation.
_ABBREVIATIONS = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|Gen|Sen|Rep|Gov|Lt|Col|Capt|Prof|Inc|Co|Corp|Ltd|"
    r"No|vs|U\.S|U\.K|U\.N|E\.U|a\.m|p\.m|e\.g|i\.e)\.$")


def sentences_in(span: str) -> int:
    """How many sentences a quoted span holds."""
    count, start = 1, 0
    for m in re.finditer(r"[.!?…]+[\"'”’)]*\s+(?=[\"“‘(]?[A-Z0-9])", span.strip()):
        if _ABBREVIATIONS.search(span.strip()[start:m.start() + 1]):
            continue
        count += 1
        start = m.end()
    return count


def check_quote_length(sentence: str, _blocks: list) -> tuple:
    """The owner's limit (2026-11-03, §436): a quote is at most one sentence,
    so an episode never copies a long stretch of anyone's words."""
    for span in _QUOTED.findall(sentence):
        if sentences_in(span) > 1:
            return "fail", f"the quote \"{span[:60]}...\" is longer than one sentence"
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


#: A claim that something is unprecedented - claim-shaped, never ordinary
#: English (§440, step 3: "the most recent", "October the first", "the
#: first week" and "the record matters" were flagged on clean scripts):
#: "for the first time", "first-ever", "the first ... to / since / ever",
#: "the only ... to", "a / new / all-time record", "record high", "the
#: biggest ... since / ever / on record / in history", "unprecedented",
#: "never before".
_CLAIM_TAIL = r"(?:[\w'-]+\s+){0,5}?(?:to|since|ever|in history|on record|of all time|anywhere)\b"
_SUPERLATIVE = re.compile(
    r"\bfor the first time\b|\bfirst-ever\b|\bnever before\b|\bunprecedented\b"
    r"|\b(?:a|new|an all-time|all-time)\s+record\b|\brecord[- ](?:high|low|number|level|levels)\b"
    r"|\b(?:the|its|their|his|her)\s+(?:first|only)\s+" + _CLAIM_TAIL +
    r"|\b(?:the|its|their|his|her)\s+(?:biggest|largest|highest|lowest|fastest|youngest|"
    r"oldest|longest|shortest|most|fewest|worst|best|smallest|deadliest)\s+" + _CLAIM_TAIL,
    re.I)
#: The family each superlative's support may use.
#: The words a packet may use for the same claim: "biggest" is "largest",
#: never any "most" or "highest" (§440, step 3: a packet's "highest since
#: May" vouched for a planted "biggest ever").
_FAMILIES = (("first",), ("only",), ("record", "all-time", "all time", "ever", "unprecedented",
                                     "never before"),
             ("biggest", "largest"), ("most",), ("highest", "high"), ("lowest", "low"),
             ("best",), ("worst",), ("longest",), ("shortest",), ("fastest",),
             ("oldest",), ("youngest",), ("fewest",), ("smallest",), ("deadliest",))


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
    said = m.group(0).lower()
    word = next((w for w in ("first", "only", "record", "never before", "unprecedented",
                             "ever") if w in said), said.split()[1] if len(said.split()) > 1
                else said)
    for w in ("biggest", "largest", "highest", "lowest", "fastest", "youngest", "oldest",
              "longest", "shortest", "most", "fewest", "worst", "best", "smallest",
              "deadliest"):
        if re.search(r"\b" + w + r"\b", said):
            word = w
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
    fresh, archived = values
    approx = bool(figure_guard._APPROX.search(text))
    # History is checked too (§440, step 3): a sentence about another year
    # or an earlier period may be vouched for by the archive as well.
    dated = bool(figure_guard._DATED.search(text) or score_guard._PAST.search(text)
                 or score_guard._years_other_than_now(text))
    said = _numbers(text)
    if not said:
        return "pass", ""
    # The same subject (mclovin.md "always" 3): a number is vouched for by
    # the sources about what the sentence is about, not by any number
    # anywhere in the packets. With no source like it, all of them.
    near = _near_values(sentence, blocks)
    if near is not None:
        fresh, archived = near
    for value, decimals in said:
        if figure_guard.vouched(value, decimals, approx, fresh):
            continue
        if dated and figure_guard.vouched(value, decimals, approx, archived):
            continue
        return "fail", f"the number {value:g} is not in the packets about this"
    return "pass", ""


#: A source is about the sentence's subject when it shares this much with it
#: (a name counts twice), as the dates check reads it.
SUBJECT_SCORE = 3


def _near_values(sentence: str, blocks: list):
    """`(fresh, archived)` numbers of the sources about this sentence's
    subject - and the live block always - or None when no source is."""
    import figure_guard
    import news_clusters

    names = news_clusters.names(sentence)
    words = news_clusters.tokens(sentence)
    near = [b for b in blocks
            if b.live or 2 * len(names & b.names) + len(words & b.words) >= SUBJECT_SCORE]
    if not any(not b.live for b in near):
        return None
    fresh = sorted({v for b in near if not b.archive for v in b.values})
    old = sorted({v for b in near if b.archive for v in b.values})
    return fresh, old


def _material_values(text: str) -> tuple:
    """What a source states: `figure_guard`'s reading of printed figures,
    and the same word-number reader the script is read with - "one hundred
    and seventy-eight" in a source vouches for 178 (§440, step 3)."""
    import figure_guard

    values = set(figure_guard.material_values(text))
    values |= {v for v, _d in _numbers(text)}
    return tuple(sorted(values))


_DIGITS = re.compile(r"(?<![\w.])\$?(\d[\d,]*(?:\.\d+)?)(?![\w])")


_ORDINAL_WORDS = re.compile(
    r"\b(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)[\s-]+)?"
    r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|"
    r"twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|"
    r"nineteenth|twentieth|thirtieth|fortieth|fiftieth|hundredth|thousandth)\b", re.I)


def _numbers(text: str) -> list:
    """`(value, decimals)` for each number said, read by `figure_guard`'s own
    reader on its own tokens - "four hundred and eighty-five thousand" is
    one number, "a hundred and forty" another - and never a year, a date, a
    clock time, cents beside dollars, a word with digits in it ("COP28"), or
    a count of things up to twelve said as one word."""
    import figure_guard

    out = []
    scrubbed = figure_guard._without_scaffolding(text)
    # A word with digits in it is a name, never a figure.
    scrubbed = re.sub(r"\b[A-Za-z]+[\w-]*\d[\w-]*\b", " ", scrubbed)
    # Ordinals said as words are dates and places in a list - "the
    # twenty-eighth of October" - never figures.
    scrubbed = _ORDINAL_WORDS.sub(" ", scrubbed)
    # "The hundred and seventy-eight exclusions" is a hundred and seventy-eight.
    scrubbed = re.sub(r"\bthe hundred\b", "one hundred", scrubbed, flags=re.I)
    tokens = figure_guard._TOKEN.findall(scrubbed)
    i = 0
    while i < len(tokens):
        low = tokens[i].lower()
        if figure_guard._DIGIT.match(tokens[i]) or low in figure_guard._UNITS \
                or low in figure_guard._TENS or (low == "a" and i + 1 < len(tokens)
                                                 and tokens[i + 1].lower() == "hundred"):
            value, decimals, j = figure_guard._read_number(tokens, i)
            if value is None or j <= i:
                i += 1
                continue
            words = [t.lower() for t in tokens[i:j]]
            nxt = ""
            # "Eighteen and a half", "three and three-quarters".
            tail = [t.lower() for t in tokens[j:j + 3]]
            for said, part in ((["and", "a", "half"], 0.5), (["and", "a", "quarter"], 0.25),
                               (["and", "three", "quarter"], 0.75),
                               (["and", "three", "quarters"], 0.75)):
                if tail == said:
                    value, decimals, j = value + part, 2, j + 3
                    break
            nxt = tokens[j].lower() if j < len(tokens) else ""
            # A count of things up to twelve is not a figure - "two teams" -
            # but "eight percent" and "five dollars" are (§440, step 3).
            small_count = (value <= 12 and len(words) == 1
                           and not figure_guard._DIGIT.match(tokens[i])
                           and nxt not in ("percent", "%", "points", "point", "dollars",
                                           "pounds", "euros", "cents", "per", "basis",
                                           "million", "billion", "trillion", "thousand"))
            if nxt != "cents" and not small_count:
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


#: "Today" as an event's day needs an event: a past-tense verb. "Today is
#: the third", "today's tariffs" and "trains toward today" mean now, and
#: were flagged on clean scripts (§440, step 3).
_NOW_ONLY = re.compile(r"\btoday(?:'s|’s)|\btoday\s+(?:is|are|was the)\b|\b(?:toward|until|"
                       r"to|as of|from|than|through)\s+today\b", re.I)
_EVENT = re.compile(r"\b(?:said|says|told|gave|won|lost|beat|held|met|named|voted|signed|"
                    r"announced|approved|reported|rose|fell|closed|opened|launched|"
                    r"\w{3,}ed)\b", re.I)


def check_dates(sentence: str, blocks: list, now: Optional[datetime] = None) -> tuple:
    import news_clusters

    phrase = limit = None
    for pattern, days in _RELATIVE:
        m = pattern.search(sentence)
        if m:
            phrase, limit = m.group(0), days
            break
    if phrase is not None and limit == 1 and phrase.lower() in ("today", "tonight") and (
            _NOW_ONLY.search(sentence) or not _EVENT.search(sentence)):
        return "pass", ""
    if phrase is None:
        return "pass", ""
    names = news_clusters.names(sentence)
    words = news_clusters.tokens(sentence)
    scored = []
    for block in blocks:
        score = 2 * len(names & block.names) + len(words & block.words)
        if block.live:
            # The live block is now by definition - but only vouches for a
            # sentence about it (§440, step 3: any live block passed all).
            if score >= 3:
                return "pass", ""
            continue
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


#: Capitalised words that name no one: months, days, titles and roles,
#: parties and the words a sentence starts with. Left in, "Representative
#: Nancy Mace, a Republican" read as four names and "on November the
#: ninth" joined any two stories (§440, step 3).
NOT_ENTITIES = frozenset("""
january february march april may june july august september october november
december monday tuesday wednesday thursday friday saturday sunday representative
senator president minister prime secretary governor mayor chief chair chairman
judge justice general director officer spokesman spokeswoman spokesperson leader
republican democrat democratic party house senate congress committee court
government state states department office council union analyst lawmaker official
officials people reports report
""".split())

#: Two sources are one story when they share at least this many names.
SAME_STORY_NAMES = 3


def _stories(blocks: list) -> list:
    """Which story each source is: sources sharing `SAME_STORY_NAMES` names
    are one story (grouped transitively), so two outlets' different
    headlines on one event are never two stories."""
    group = list(range(len(blocks)))

    def find(i):
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            shared = (blocks[i].names & blocks[j].names) - NOT_ENTITIES
            if len(shared) >= SAME_STORY_NAMES:
                group[find(i)] = find(j)
    return [find(i) for i in range(len(blocks))]


def check_mixed(sentence: str, blocks: list) -> tuple:
    """A sentence joining two stories with no source for the link: a name
    only one story's sources hold, and a name only another story's sources
    hold, that no single source holds together. A name many stories share
    ("Israel") is context, never one side of a join."""
    import news_clusters

    words = sentence.split()
    opener = news_clusters.names(words[0]) if words else frozenset()
    names = {n for n in news_clusters.names(sentence)
             if n not in NOT_ENTITIES and n not in opener}
    if len(names) < 2:
        return "pass", ""
    texts = [b for b in blocks if not b.live]
    story_of = _stories(texts)
    owner: dict = {}
    for n in names:
        stories = {story_of[i] for i, b in enumerate(texts) if n in b.names}
        if len(stories) == 1:
            owner[n] = stories.pop()
    for a in owner:
        for b in owner:
            if owner[a] == owner[b] or a >= b:
                continue
            if any(a in blk.names and b in blk.names for blk in texts):
                continue
            return "fail", (f"joins {a} and {b} from two different stories, "
                            "linked by no one source")
    return "pass", ""


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
        #: What `ScriptNotes.supervisor` shows, filled in place (§435).
        self.summary: dict = {"rows": [], "repaired": [], "dropped": [],
                              "enforced": sorted(self.enforce)}
        try:
            self.blocks = _blocks(plan)
            self.values = (sorted({v for b in self.blocks if not b.archive for v in b.values}),
                           sorted({v for b in self.blocks if b.archive for v in b.values}))
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
        support = self._support(sentence)
        if support is not None:
            verdict.checks.append(support)
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

    def _support(self, sentence: str) -> Optional["Check"]:
        """The grounding checker's verdict, or None when none is plugged in."""
        from agents import grounding
        from config import settings

        if grounding.scorer() is None or self.blocks is None:
            return None
        enforced = SUPPORT in self.enforce
        sources = "\n\n".join(b.text for b in self.nearest(sentence))
        import episode_quality

        if not sources.strip():
            # No source is about it: a settled fact with no name, number or
            # date may stay (decided item 12); anything else is unsure, and
            # takes the second look while `support` is enforced.
            if not episode_quality._has_particular(sentence):
                return Check(SUPPORT, "pass", "settled, no particular, no source near",
                             enforced)
            return Check(SUPPORT, "unsure", "no source is about it", enforced)
        score = grounding.score(sources, sentence)
        if score is None:
            return Check(SUPPORT, "unchecked", "the grounding checker did not answer", enforced)
        if score >= float(settings.supervisor_grounding_high):
            return Check(SUPPORT, "pass", f"supported ({score:.2f})", enforced)
        if score <= float(settings.supervisor_grounding_low):
            # Decided item 12: a settled fact with no name, number or date may stay.
            if not episode_quality._has_particular(sentence):
                return Check(SUPPORT, "pass", f"settled, no particular ({score:.2f})", enforced)
            return Check(SUPPORT, "fail", f"the sources do not support it ({score:.2f})",
                         enforced)
        return Check(SUPPORT, "unsure", f"the grounding checker is unsure ({score:.2f})",
                     enforced)

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


_BY_NAME = {"quote": check_quote, "quote_length": check_quote_length,
            "website": check_website,
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
