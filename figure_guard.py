"""A market figure is checked before it is spoken (§269).

§259 holds a sports episode's scores to the scoreboard. A markets episode has
the same failure with money: a writer that has no price, or a price it reads
past, says one anyway in the same confident voice as the researched half -
and a listener cannot tell a recalled figure from a reported one. The owner
saw it ("confusion on simple ideas like ... stock prices"), and a smaller
writer is likelier to do it than a larger one, so the rule is enforced here,
in code, on every sentence before the voice sees it, whichever model wrote it.

What is checked, per sentence, on an episode about a market (the brief's
`live_domain`, the live lookup's domain, or §265's code route is `markets`):

* **A money amount, a percentage or a number of points**, said about now,
  must be one the material contains - the live block, the evidence packet
  (with §267's measurements), the forecast and anything the listener
  attached. Rounded the way a person rounds: "one hundred eighty-two dollars"
  is vouched for by 182.43, "about one hundred eighty" by it too; "two
  hundred" is not.
* **History is left alone**: a sentence plainly about another year or an
  earlier period ("in 2021", "last year", "all-time") says nothing about now.

Removed means not spoken: dropped from the stream like `ScoreGuard`'s, logged
at warning, kept on `ScriptNotes.figure_drops` and reported to `/admin`'s
pipeline errors. Deliberately narrow - money, percentages, points - so the
story survives it; only the figures a market owns are held to the market.

Not checked, on purpose: a figure the writer worked out from two it was given
(a percentage between two prices). That is arithmetic on the material, not
recall, but telling the two apart is not something a pattern can do, so it is
removed with the rest and counted; if the count says it happens, the cure is
a line of arithmetic in the live block, not a looser check here.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import score_guard

log = logging.getLogger("fam.figure_guard")

# --------------------------------------------------------------------------
# Numbers as a voice script says them, and as a source prints them
# --------------------------------------------------------------------------
_UNITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_DIGIT = re.compile(r"^\$?\d[\d,]*(?:\.\d+)?$")
_TOKEN = re.compile(r"\$?\d[\d,]*(?:\.\d+)?|%|[a-z]+", re.I)

#: Words that say a figure is rounded: "about", "nearly", "more than".
_APPROX = re.compile(
    r"\b(?:about|around|roughly|nearly|almost|close to|some|more than|over|"
    r"under|less than|just over|just under|upwards of|approximately|north of|"
    r"south of)\b", re.I)


def _is_word_number(token: str) -> bool:
    return token in _UNITS or token in _TENS or token == "hundred" \
        or token in _SCALES


def _read_number(tokens: list, i: int) -> tuple:
    """`(value, decimals, next index)` for the number starting at `i`, or
    `(None, 0, i)`. Reads "one hundred eighty-two", "three point two",
    "4.5 billion", "a hundred", "$182.40"."""
    total = 0.0
    current = 0.0
    seen = False
    decimals = 0
    j = i
    while j < len(tokens):
        tok = tokens[j]
        low = tok.lower()
        if _DIGIT.match(tok):
            if seen and current:
                break
            text = tok.lstrip("$").replace(",", "")
            current += float(text)
            decimals = len(text.split(".")[1]) if "." in text else 0
            seen = True
        elif low == "a" and j + 1 < len(tokens) and \
                tokens[j + 1].lower() in ("hundred", *_SCALES) and not seen:
            current = 1.0
            seen = True
        elif low in _UNITS:
            current += _UNITS[low]
            seen = True
        elif low in _TENS:
            current += _TENS[low]
            seen = True
        elif low == "hundred" and seen:
            current = (current or 1) * 100
        elif low in _SCALES and seen:
            total += (current or 1) * _SCALES[low]
            current = 0.0
        elif low == "and" and seen and j + 1 < len(tokens) and \
                _is_word_number(tokens[j + 1].lower()) and \
                tokens[j - 1].lower() in ("hundred", *_SCALES):
            pass
        elif low == "point" and seen and j + 1 < len(tokens) and \
                tokens[j + 1].lower() in _UNITS:
            digits = ""
            k = j + 1
            while k < len(tokens) and tokens[k].lower() in _UNITS \
                    and _UNITS[tokens[k].lower()] < 10:
                digits += str(_UNITS[tokens[k].lower()])
                k += 1
            current += float("0." + digits)
            decimals = len(digits)
            j = k
            continue
        else:
            break
        j += 1
    if not seen:
        return None, 0, i
    return total + current, decimals, j


#: A figure's kind, from the word or sign after (or the "$" before) it.
def _unit_after(tokens: list, j: int) -> str:
    nxt = tokens[j].lower() if j < len(tokens) else ""
    two = " ".join(t.lower() for t in tokens[j:j + 2])
    if nxt in ("dollars", "dollar", "bucks"):
        return "money"
    if nxt == "cents":
        return "cents"
    if nxt in ("percent", "%") or two in ("per cent", "percentage points"):
        return "percent"
    if two == "basis points":
        return "bps"
    if nxt in ("points", "point"):
        return "points"
    return ""


def figures_in(sentence: str) -> list:
    """`[(value, decimals, unit)]` for each money amount, percentage or
    number of points the sentence says, in order. "A hundred eighty-two
    dollars and forty cents" is one figure, 182.4."""
    tokens = _TOKEN.findall(sentence.replace("-", " "))
    out: list = []
    i = 0
    while i < len(tokens):
        value, decimals, j = _read_number(tokens, i)
        if value is None:
            i += 1
            continue
        unit = "money" if tokens[i].startswith("$") else _unit_after(tokens, j)
        if unit == "money" and j < len(tokens) and tokens[j].lower() in (
                "dollars", "dollar"):
            j += 1
        if unit == "money" and j < len(tokens) and tokens[j].lower() == "and":
            cents, _d, k = _read_number(tokens, j + 1)
            if cents is not None and _unit_after(tokens, k) == "cents":
                value += cents / 100
                decimals = 2
                j = k + 1
        if unit == "cents":
            value, decimals, unit = value / 100, 2, "money"
        if unit == "bps":
            value, decimals, unit = value / 100, 2, "percent"
        if unit:
            out.append((value, decimals, unit))
        i = max(j, i + 1)
    return out


_PRINTED = re.compile(
    r"(?<![\w.])\$?(\d[\d,]*(?:\.\d+)?)\s*(trillion|billion|million|thousand|"
    r"tn|bn|mn|t|b|m|k)?\b", re.I)
_PRINTED_SCALE = {"trillion": 1e12, "tn": 1e12, "t": 1e12, "billion": 1e9,
                  "bn": 1e9, "b": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6,
                  "thousand": 1e3, "k": 1e3}


def material_values(text: str) -> list:
    """Every number the material states, digits and words, scales applied.
    Generous on purpose: anything printed may vouch for what is said."""
    values: set = set()
    for m in _PRINTED.finditer(text or ""):
        try:
            number = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        values.add(number)
        scale = (m.group(2) or "").lower()
        if scale:
            values.add(number * _PRINTED_SCALE[scale])
    for value, _d, _u in figures_in(text or ""):
        values.add(value)
    return sorted(values)


def vouched(value: float, decimals: int, approx: bool, material: list) -> bool:
    """Whether a spoken figure is one the material states, rounded the way a
    person rounds it."""
    for known in material:
        if round(known, decimals) == round(value, decimals):
            return True
        if known and abs(value - known) / abs(known) <= (0.03 if approx else 0.01):
            return True
    return False


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------
def _material(plan) -> str:
    """Everything the writer was given that may state a figure."""
    parts = [str(getattr(plan, "evidence", "") or "")]
    for name in ("live", "forecast"):
        block = getattr(plan, name, None)
        if block is not None:
            try:
                parts.append(block.as_prompt_block())
            except Exception:  # noqa: BLE001 - a block that cannot render vouches for nothing
                log.warning("figure guard: the %s block could not be read", name)
    for attached in getattr(plan, "readable", None) or []:
        parts.append(str(getattr(attached, "text", "") or ""))
    return "\n".join(parts)


def _is_markets(plan) -> bool:
    brief = getattr(plan, "brief", None)
    live = getattr(plan, "live", None)
    return "markets" in (str(getattr(brief, "live_domain", "") or ""),
                         str(getattr(brief, "live_route", "") or ""),
                         str(getattr(live, "domain", "") or ""))


class FigureGuard:
    """One per episode, beside `ScoreGuard` in `_ScriptReader`."""

    def __init__(self, plan) -> None:
        self.active = _is_markets(plan)
        self.material = material_values(_material(plan)) if self.active else []
        self.dropped: list = []
        self.spoken = 0

    def why_not(self, sentence: str) -> str:
        """Why this sentence may not be spoken, or "" when it may."""
        if not self.active or not sentence.strip():
            return ""
        text = " ".join(sentence.split())
        figures = figures_in(text)
        if not figures:
            return ""
        if (score_guard._PAST.search(text)
                or score_guard._years_other_than_now(text)):
            return ""
        approx = bool(_APPROX.search(text))
        for value, decimals, unit in figures:
            if not vouched(value, decimals, approx, self.material):
                said = f"{value:g}" + ("%" if unit == "percent" else "")
                return (f"{unit} figure {said} is not in the material"
                        + ("" if self.material else " (there is none)"))
        return ""

    def allow(self, sentence: str) -> bool:
        why = self.why_not(sentence)
        if not why:
            self.spoken += 1
            return True
        self.dropped.append(f"{why}: {sentence.strip()}")
        log.warning("figure guard: removed before it was spoken (%s): %r",
                    why, sentence.strip()[:160])
        return False

    def rescue(self) -> Optional[str]:
        """The one line said when the guard removed everything: silence is
        never the answer, and a figure nothing vouches for is not either."""
        if self.spoken or not self.dropped:
            return ""
        self.spoken += 1
        return ("We can't give you a current figure for this that we can "
                "stand behind right now.")
