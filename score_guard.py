"""The last check on a sports episode's scores before they are spoken (§259).

The live block tells the writer that every score and every period comes from
the scoreboard. A prompt is a request, and the night it was not met a listener
heard "first quarter" about a game that had finished: API-Sports had refused
the account, and the writer took the state from a live blog written during the
game. **A wrong score is the one error a sports listener never forgives**, so
the rule is enforced here, in code, on every sentence before the voice sees it.

What is checked, per sentence, against what the scoreboard said (`plan.live`):

* **A score** - two numbers joined by "-", "–" or "to", as digits or as words -
  must be one the scoreboard's own sentences contain: tonight's score, a
  team's record, its last result. Anything else is removed, unless the
  sentence is plainly about another season or an earlier game and says nothing
  about now.
* **Where the game stands** - a quarter, half, period or inning said as now
  ("it's the first quarter", "still in the third"), or "under way" - is kept
  only while the scoreboard says the game is under way, and only the period it
  names. A finished game has no current quarter; a game with no scoreboard has
  no state we know.
* **A result** - who won, said about this game - is kept only when the
  scoreboard says final, and only the right way round.
* **Who is ahead** in a game under way - only the side the scoreboard says.

With no scoreboard at all (no provider, a refused account, a timeout, a stuck
feed) every one of those is removed, because nothing could vouch for it.

Removed means not spoken: dropped from the stream like `OpeningGuard`'s
openings, logged at warning and kept on `ScriptNotes.score_drops` so a prompt
rule that fails is seen. Deliberately narrow - numbers, periods, results - so a
sports episode's story survives it; only the claims a scoreboard owns are
held to the scoreboard.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

import live_facts

log = logging.getLogger("fam.score_guard")

# --------------------------------------------------------------------------
# Numbers, as a script writes them for a voice
# --------------------------------------------------------------------------
_UNITS = {
    "zero": 0, "nothing": 0, "nil": 0, "one": 1, "two": 2, "three": 3,
    "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_ONES = "one|two|three|four|five|six|seven|eight|nine"
_WORD_NUM = (rf"(?:(?:{'|'.join(_TENS)})(?:[- ](?:{_ONES}))?"
             rf"|{'|'.join(sorted(_UNITS, key=len, reverse=True))})")
_NUM = rf"(?:\d{{1,3}}|{_WORD_NUM})"

#: What follows a pair of numbers that are not a score: a range of yards or
#: minutes, a defensive front, a share.
_NOT_A_SCORE_AFTER = re.compile(
    r"^\s*(?:-\s*)?(?:yards?|yd|minutes?|seconds?|hours?|days?|weeks?|months?|"
    r"years?|percent|per ?cent|%|million|billion|thousand|miles?|inch(?:es)?|"
    r"feet|foot|degrees?|defen[cs]e|front|scheme|formation|base|split|ratio|"
    r"times|o'clock|am\b|pm\b|p\.m\.|a\.m\.|underdogs?|favou?rites?|odds|"
    r"shots?|chance)", re.I)
_MONTHS = ("january|february|march|april|may|june|july|august|september|"
           "october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|"
           "oct|nov|dec")
_SCORE = re.compile(
    rf"(?<![\w.:])({_NUM})(\s*(?:-|–|—|\bto\b)\s*)({_NUM})(?![\w%]|[.:]\d)", re.I)


def _value(token: str) -> Optional[int]:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    if token in _UNITS:
        return _UNITS[token]
    parts = re.split(r"[- ]", token)
    if parts and parts[0] in _TENS:
        return _TENS[parts[0]] + (_UNITS.get(parts[1], 0) if len(parts) > 1 else 0)
    return None


def scores_in(text: str) -> list:
    """Every `(a, b)` pair in `text` that reads as a score, in the order said.

    Not a year span ("2025-26"), a date ("October 12-13"), a clock, or a
    range with a unit ("five to ten yards", "a 4-3 defense")."""
    out = []
    for match in _SCORE.finditer(text):
        # "twenty-four" is one number, not twenty to four: a tens word
        # joined to a ones word by a hyphen is how a voice script spells it.
        if (match.group(1).lower() in _TENS and match.group(2).strip() in ("-", "")
                and match.group(3).lower() in _ONES.split("|")):
            continue
        a, b = _value(match.group(1)), _value(match.group(3))
        if a is None or b is None or a > 200 or b > 200:
            continue
        if _NOT_A_SCORE_AFTER.match(text[match.end():]):
            continue
        before = text[:match.start()].rstrip().lower()
        if re.search(rf"\b(?:{_MONTHS})\.?$", before):
            continue
        # "one to watch" never matches (the second side must be a number);
        # "from one to three" is a range, not a score.
        if re.search(r"\b(?:from|between)$", before):
            continue
        out.append((a, b))
    return out


def _pairs_in_facts(sentences) -> set:
    """Every unordered pair of numbers a scoreboard sentence contains.

    Generous on purpose: "won 3 and lost 2" vouches for a 3-2 record and
    "beat the Jets 24 to 17" for 24-17, in either order. What it never does
    is vouch for a number the scoreboard did not say."""
    pairs = set()
    for sentence in sentences:
        numbers = [_value(n) for n in re.findall(rf"\b{_NUM}\b", sentence, re.I)]
        numbers = [n for n in numbers if n is not None]
        for i, a in enumerate(numbers):
            for b in numbers[i + 1:]:
                pairs.add(frozenset((a, b)))
    return pairs


# --------------------------------------------------------------------------
# Where a game stands, and who won
# --------------------------------------------------------------------------
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
             "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "sixth": 6, "6th": 6,
             "seventh": 7, "7th": 7, "eighth": 8, "8th": 8, "ninth": 9,
             "9th": 9, "tenth": 10, "10th": 10}
_PERIOD = re.compile(
    rf"\b(?P<ord>{'|'.join(_ORDINALS)})\s+(?P<unit>quarter|half|period|inning|"
    rf"innings|set|round)\b"
    r"|\b(?P<q>Q[1-4])\b"
    r"|\b(?P<unit2>quarter|period|inning|half|set|round) (?P<num>[1-9])\b"
    r"|\b(?P<named>half-?time|overtime|two-minute warning|stoppage time|"
    r"extra time|end of regulation|(?:top|bottom) of the \w+)\b", re.I)

#: Words that put a sentence in the present of the game.
_NOW = re.compile(
    r"\b(?:it's|it is|we're|we are|they're|they are|is in|are in|are into|"
    r"is into|currently|right now|"
    r"at the moment|as we speak|as of now|so far tonight|still in|still|"
    r"under ?way|in progress|ongoing|now)\b", re.I)
_UNDER_WAY = re.compile(
    r"\b(?:under ?way|in progress|still (?:going|playing|on)|being played|"
    r"is live|are live|live right now)\b", re.I)

#: Words that put a sentence in another season or an earlier game, where a
#: score belongs to history rather than to this game's scoreboard.
_PAST = re.compile(
    r"\b(?:last (?:season|year|postseason|playoffs?|week|month|meeting|time|"
    r"game|matchup|january|february|december|super bowl)|years? ago|seasons? ago|"
    r"all-time|in history|history|historically|previous(?:ly)?|earlier this "
    r"(?:season|year|month)|back in|since (?:19|20)\d\d|week \d+|"
    r"in (?:19|20)\d\d|(?:19|20)\d\d (?:season|playoffs?|super bowl))\b", re.I)
_TONIGHT = re.compile(
    r"\b(?:tonight|today|this evening|this afternoon|right now|currently|"
    r"just (?:now|moments)|moments ago|minutes ago|this game|tonight's|today's)\b",
    re.I)
_RESULT = re.compile(
    r"\b(?:won|wins|beat|beats|defeated|defeats|topped|edged|held off|"
    r"outlasted|lost|loses|fell to|fall to|victory|final score|final whistle|"
    r"finished|ended|went on to win)\b", re.I)
#: "have won four straight" is a streak, not tonight's result.
_PERFECT = re.compile(r"\b(?:have|has|had)\s+(?:won|lost|beaten)\b", re.I)
_LEAD = re.compile(
    r"\b(?:lead|leads|leading|are ahead|is ahead|are up|is up|in front)\b", re.I)
#: A claim about where a game stands *now*: who is ahead, or level. Not
#: "leads the league in sacks", which is a season and not this game.
_STANDING = re.compile(
    r"\b(?:lead|leads|trail|trails|are ahead|is ahead|are behind|is behind|"
    r"are leading|is leading|leading by|are trailing|is trailing|trailing by|"
    r"are up|is up|are down|is down|are tied|is tied|tied at|are level|is level|"
    r"level at|in front)\b(?!\s+(?:the|all|every)\s+(?:league|nfl|nba|nhl|mlb|"
    r"afc|nfc|division|conference|nation|country|series|majors|wnba|team|way)\b)",
    re.I)
_LEVEL = re.compile(r"\b(?:tied|level)\b", re.I)


def _periods_in(text: str) -> list:
    """`(unit, number)` for each period named; named states have number 0."""
    out = []
    for m in _PERIOD.finditer(text):
        if m.group("ord"):
            unit = m.group("unit").lower().rstrip("s") if m.group("unit").lower() \
                != "innings" else "inning"
            out.append((unit, _ORDINALS[m.group("ord").lower()]))
        elif m.group("q"):
            out.append(("quarter", int(m.group("q")[1])))
        elif m.group("unit2"):
            out.append((m.group("unit2").lower(), int(m.group("num"))))
        else:
            named = m.group("named").lower().replace("-", "")
            out.append((named, 0))
    return out


def _years_other_than_now(text: str) -> bool:
    """Whether the sentence names a year before this one."""
    this_year = datetime.now(timezone.utc).year
    return any(int(y) < this_year
               for y in re.findall(r"\b((?:19|20)\d\d)\b", text))


def _team_words(team: str, other: str) -> str:
    """A regex alternation of what a script may call one side: its city and
    its nickname ("los|angeles|rams"), less any word the other side shares
    ("New York" names neither the Jets nor the Giants)."""
    mine = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", team or "")}
    theirs = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", other or "")}
    words = sorted(w for w in mine - theirs - {"the", "fc", "sc", "cf"} if len(w) > 1)
    return "|".join(re.escape(w) for w in words)


class ScoreGuard:
    """One per episode, beside `OpeningGuard` in `_ScriptReader`.

    Active only on a sports question (`brief.live_domain == "sports"`, or a
    sports scoreboard on the plan): everywhere else a pair of numbers is not
    a scoreboard's business."""

    def __init__(self, plan) -> None:
        brief = getattr(plan, "brief", None)
        live = getattr(plan, "live", None)
        domain = str(getattr(brief, "live_domain", "") or "")
        live_domain = str(getattr(live, "domain", "") or "")
        self.active = domain == "sports" or live_domain == "sports"
        self.dropped: list = []
        self.spoken = 0

        facts = None
        if (live is not None and getattr(live, "outcome", "") == live_facts.FACTS
                and live_domain == "sports"):
            facts = getattr(live, "facts", None)
        #: What the scoreboard established. `unknown` covers every way of not
        #: having one: no provider, a refusal, a timeout, a stuck feed.
        self.status = (facts.status if facts is not None else live_facts.UNKNOWN)
        sentences = list(getattr(facts, "facts", []) or []) if facts else []
        self.allowed = _pairs_in_facts(sentences)
        self.periods = [p for s in sentences for p in _periods_in(s)]
        self.leader = self.trailer = ""
        self.winner = self.loser = ""
        #: Whether the scoreboard says the sides are level.
        self.level = any(" are level at " in s for s in sentences[:1])
        for sentence in sentences[:1]:
            m = re.match(r"(.+?) (beat|lead) (.+?) \S+ to \S+\.$", sentence)
            if m:
                first = _team_words(m.group(1), m.group(3))
                second = _team_words(m.group(3), m.group(1))
                if m.group(2) == "beat":
                    self.winner, self.loser = first, second
                else:
                    self.leader, self.trailer = first, second

    # ------------------------------------------------------------------
    def why_not(self, sentence: str) -> str:
        """Why this sentence may not be spoken, or "" when it may."""
        if not self.active or not sentence.strip():
            return ""
        text = " ".join(sentence.split())
        standing = bool(_STANDING.search(text))
        now = bool(_NOW.search(text) or _TONIGHT.search(text) or standing)
        past = bool(_PAST.search(text) or _years_other_than_now(text))
        history = past and not now
        status = self.status

        for pair in scores_in(text):
            if frozenset(pair) in self.allowed:
                continue
            if history:
                continue
            return (f"score {pair[0]}-{pair[1]} is not on the scoreboard"
                    + ("" if self.allowed else " (there is no scoreboard)"))

        periods = _periods_in(text)
        if periods and not history:
            if status != live_facts.IN_PROGRESS and (now or status in (
                    live_facts.UNKNOWN, live_facts.SCHEDULED)):
                return (f"names a period ({periods[0][0]}) of a game the "
                        f"scoreboard says is {status}")
            if status == live_facts.IN_PROGRESS:
                current = self.periods
                for unit, number in periods:
                    same = [n for u, n in current if u == unit]
                    if now and current and (unit, number) not in current:
                        return (f"says it is the {unit} {number or ''} but the "
                                "scoreboard says otherwise").replace("  ", " ")
                    if now and not current:
                        return "names the current period; the scoreboard does not"
                    if same and number > max(same):
                        return (f"names a {unit} the game has not reached")
                    if unit == "overtime" and ("overtime", 0) not in current:
                        return "names overtime the scoreboard does not"

        if _UNDER_WAY.search(text) and status != live_facts.IN_PROGRESS \
                and not history:
            return f"says the game is under way; the scoreboard says {status}"

        lower = text.lower()
        if _RESULT.search(text) and not history and not (
                _PERFECT.search(text) and not _TONIGHT.search(text)):
            if status != live_facts.FINAL and _TONIGHT.search(text):
                return f"states tonight's result; the scoreboard says {status}"
            if status == live_facts.FINAL and self.loser and re.search(
                    rf"\b(?:{self.loser})\b[^.?!]{{0,40}}\b(?:won|beat|"
                    rf"defeated|topped|edged|held off|outlasted)\b", lower):
                return "says the losing side won; the scoreboard says they lost"
            if status == live_facts.FINAL and self.winner and re.search(
                    rf"\b(?:{self.winner})\b[^.?!]{{0,30}}\b(?:lost|fell)\b",
                    lower):
                return "says the winning side lost; the scoreboard says they won"

        if standing and not history:
            # Who is ahead is a live state: only a game under way has one,
            # and only the one its scoreboard says.
            if status != live_facts.IN_PROGRESS:
                return f"says who is ahead; the scoreboard says {status}"
            if _LEVEL.search(text) and not self.level:
                return "says the game is level; the scoreboard says it is not"
            if self.trailer and re.search(
                    rf"\b(?:{self.trailer})\b[^.?!]{{0,25}}{_STANDING.pattern}",
                    lower) and not re.search(r"\b(?:trail|trails|behind|down)\b", lower):
                return "says the trailing side leads; the scoreboard says they trail"
            if self.leader and re.search(
                    rf"\b(?:{self.leader})\b[^.?!]{{0,25}}\b(?:trail|trails|are behind|"
                    rf"is behind|are down|is down)\b", lower):
                return "says the leading side trails; the scoreboard says they lead"
        return ""

    def allow(self, sentence: str) -> bool:
        why = self.why_not(sentence)
        if not why:
            self.spoken += 1
            return True
        self.dropped.append(f"{why}: {sentence.strip()}")
        log.warning("score guard: removed before it was spoken (%s): %r",
                    why, sentence.strip()[:160])
        return False

    def rescue(self) -> str:
        """The one line said when the guard removed everything (§259):
        silence is never the answer, and a score nothing vouches for is not
        either."""
        if self.spoken or not self.dropped:
            return ""
        self.spoken += 1
        return ("We can't give you a score or the state of this game that we "
                "can stand behind right now.")
