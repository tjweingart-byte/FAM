"""Mclovin's labelled cases, built in code (Phase 3 training, step 1, §440).

Decided item 30: where an eval needs labelled examples, build them in code -
inject a wrong figure, a swapped quote, a preview told as a result into a
good script - rather than asking the owner to label. This module plants one
known mistake into a copy of a real sentence, against that episode's own
packets, so the right answer is known without anyone reading it:

| Planted | The check that must catch it |
|---|---|
| a number changed | `figures` |
| a packet quote given to someone else | `quote` (wrong speaker) |
| a packet quote with its words changed | `quote` (not said) |
| two packet sentences quoted as one | `quote_length` |
| "Yesterday" on a story the packets date two weeks old | `dates` |
| "for the first time" the packets never say | `superlative` |
| a name from another story joined on | `mixed` |
| "you should" about health, law or money | `advice` |
| a website named | `website` |

Beside each planted case, **controls** the same check must pass: the packet
quote given to its own speaker, "Yesterday" on a story the packets date
today or yesterday. The unaltered sentences of every script are the third
set: any flag on one is either a real mistake the writer made or a check
too strict, and the rate is the upper bound on wrongly flagging a clean
sentence.

Pure code: no model, no network, deterministic for a given seed.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

#: The check each kind of planted mistake must trip.
EXPECTS = {
    "number": "figures", "quote_speaker": "quote", "quote_words": "quote",
    "quote_two": "quote_length", "date_old": "dates", "first": "superlative",
    "mixed": "mixed", "advice": "advice", "website": "website",
}
#: Controls: the check named must *pass* them.
CONTROLS = {"quote_own": "quote", "date_fresh": "dates"}


@dataclass(frozen=True)
class Case:
    kind: str                 # a key of EXPECTS or CONTROLS
    check: str                # the check it is about
    sentence: str
    should_fail: bool
    source: str = ""          # the unaltered sentence it was made from, if any


_UNIT_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_SWAP = dict(zip(_UNIT_WORDS, ("seven", "eight", "six", "nine", "two", "three", "four",
                               "five", "one")))
_BIG = r"(?:hundred|thousand|million|billion|trillion|point|percent)"


def change_number(sentence: str) -> Optional[str]:
    """The sentence with one number changed so no packet could vouch for it,
    or None when it has none worth changing (counts up to twelve are left
    alone by the check on purpose)."""
    months = (r"(?:january|february|march|april|may|june|july|august|september|october|"
              r"november|december|section|title|article|chapter|no\.|number)")
    m = None
    for found in re.finditer(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?![\w-])", sentence):
        before = sentence[max(0, found.start() - 12):found.start()].lower()
        if re.search(months + r"\s*$", before):
            continue                          # a date or a label, never a figure
        m = found
        break
    if m:
        raw = m.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            return None
        if 1900 <= value <= 2100 and "." not in raw:
            return None                      # a year
        if value <= 12 and "." not in raw:
            return None
        new = f"{value * 1.37 + 3:.2f}" if "." in raw else str(int(value * 1.37) + 3)
        return sentence[:m.start(1)] + new + sentence[m.end(1):]
    m = re.search(r"\b(" + "|".join(_UNIT_WORDS) + r")(?=[\s-]+(?:\w+[\s-]+){0,2}" + _BIG + r"\b)",
                  sentence, re.I)
    if m:
        word = m.group(1)
        swap = _SWAP[word.lower()]
        swap = swap.capitalize() if word[0].isupper() else swap
        return sentence[:m.start(1)] + swap + sentence[m.end(1):]
    return None


_PACKET_QUOTE = re.compile(r'["“]([^"“”]{15,240})["”]')
#: A run of capitalised words, as the mixed-story planting reads names.
_CAPS = re.compile(r"\b([A-Z][^\W\d_A-Z]+(?:\s+[A-Z][^\W\d_A-Z]+)+)\b")
#: Words a sentence or a headline capitalises that never begin or join a
#: name ("The Central Bank Chair spoke" names nobody).
_FUNCTION_WORDS = frozenset("""
the a an this that these those his her its their our my your and but or nor
for of to in on at by from with as after before when while since until than
""".split())
#: One word of a name: letters of any alphabet, an apostrophe inside it.
#: A possessive's "'s" is never part of the name ("Jerome Powell's" reads
#: "Jerome Powell", §460).
_WORD = re.compile(r"[^\W\d_]+(?:['’](?!s\b)[^\W\d_]+)?")


def _names_in(text: str, lo: int, hi: int, not_names) -> list:
    """`(start, end, name)` for each run of two or more capitalised words in
    `text[lo:hi]`, accented letters included ("Émile Zola", "Michel Bréal").
    A role word ends a run rather than spoiling it, and the run straight
    before it is the role's organisation, not a person ("Federal Reserve
    Chair Jerome Powell" gives "Jerome Powell"); a word cut off at the
    window's edge ends one too ("Summer Gam", §450). Function words ("The")
    never begin or join a name."""
    runs, current, prev_end = [], [], None
    for w in _WORD.finditer(text, lo, hi):
        word = w.group(0)
        cut = (w.start() == lo and lo > 0 and text[lo - 1].isalpha()) or \
              (w.end() == hi and hi < len(text) and text[hi].isalpha())
        role = word.lower() in not_names
        good = (word[0].isupper() and len(word) >= 3 and not cut and not role
                and word.lower() not in _FUNCTION_WORDS)
        joined = prev_end is not None and text[prev_end:w.start()].isspace()
        if good and current and joined:
            current.append(w)
        else:
            # A run straight before a role word is the role's organisation
            # ("Central Bank Chair", "Federal Reserve Chair"), never a speaker.
            if len(current) >= 2 and not (role and joined):
                runs.append(current)
            current = [w] if good else []
        prev_end = w.end()
    if len(current) >= 2:
        runs.append(current)
    return [(r[0].start(), r[-1].end(), text[r[0].start():r[-1].end()]) for r in runs]


def packet_quotes(blocks) -> list:
    """`(span, speaker)` for quotes in the packets with a two-word name
    within 120 characters - the speaker as the packet places it: the name
    nearest the quote, never a role ("Federal Reserve Chair") and never a
    word cut off at the window's edge ("Summer Gam", §450)."""
    from agents.supervisor_checks import NOT_ENTITIES

    out = []
    for block in blocks:
        text = block.text
        for m in _PACKET_QUOTE.finditer(text):
            span = m.group(1).strip().rstrip(",.")
            if len(span.split()) < 4:
                continue
            lo, hi = max(0, m.start() - 120), min(len(text), m.end() + 120)
            names = []
            for start, end, name in _names_in(text, lo, hi, NOT_ENTITIES):
                if name in span or m.start() <= start < m.end():
                    continue
                gap = m.start() - end if end <= m.start() else start - m.end()
                names.append((gap, name))
            if names:
                out.append((span, min(names)[1]))
    return out


def _sentences(text: str) -> list:
    """Clean sentences only: 6-40 words, ending on a word and a full stop."""
    out = []
    for line in (text or "").splitlines():
        for s in re.split(r"(?<=[a-z][.!?])\s+(?=[A-Z])", line.strip()):
            s = s.strip()
            if 6 <= len(s.split()) <= 40 and re.search(r"[a-z]{2}[.!?]$", s) \
                    and not re.search(r"[\"“”‘’]", s):
                out.append(s)
    return out


def _body(block) -> str:
    return block.text.split("Key evidence:", 1)[1] if "Key evidence:" in block.text else ""


def _age(block, now: datetime) -> Optional[int]:
    if block.published is None:
        return None
    return (now.date() - block.published.date()).days


def _best(sentence: str, blocks):
    import news_clusters

    names = news_clusters.names(sentence)
    words = news_clusters.tokens(sentence)
    scored = [(2 * len(names & b.names) + len(words & b.words), i, b)
              for i, b in enumerate(blocks) if not b.live]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored[0][2] if scored and scored[0][0] >= 3 else None


def _close(sentence: str, blocks) -> list:
    """The sources nearly as like the sentence as the best one (the dates
    check's own reading), best first."""
    import news_clusters

    names = news_clusters.names(sentence)
    words = news_clusters.tokens(sentence)
    scored = sorted(((2 * len(names & b.names) + len(words & b.words), i, b)
                     for i, b in enumerate(blocks) if not b.live and b.published is not None),
                    key=lambda t: (-t[0], t[1]))
    if not scored or scored[0][0] < 3:
        return []
    best = scored[0][0]
    return [b for s, _i, b in scored if s >= best - 1]


ADVICE = ("You should refinance your mortgage now while you still can.",
          "Your best bet is to move your savings into these shares today.",
          "You should stop taking the medication until you hear more.",
          "You should sue your landlord over it.")
SITES = ("reuters.com", "federalreserve.gov", "espn.com", "theverge.com")


def plant(blocks, sentences: list, now: datetime, seed: int = 0) -> list:
    """The planted cases and controls for one episode: its packets as
    `supervisor_checks.Block`s, its script's unaltered sentences."""
    import news_clusters

    rng = random.Random(seed)
    cases: list = []
    real = [s for s in sentences if len(s.split()) >= 6]
    if not real:
        return cases
    # A changed number.
    for sentence in rng.sample(real, len(real)):
        changed = change_number(sentence)
        if changed and changed != sentence:
            cases.append(Case("number", "figures", changed, True, sentence))
            break
    # Quotes from the packets: their own speaker, someone else, words changed,
    # and two sentences quoted as one.
    quotes = packet_quotes(blocks)
    if quotes:
        span, speaker = quotes[rng.randrange(len(quotes))]
        last = speaker.split()[-1]
        cases.append(Case("quote_own", "quote", f'"{span}," {speaker} said.', False))
        # Someone the packets never put beside this quote, anywhere.
        def beside(name: str) -> bool:
            low = name.split()[-1].lower()
            for block in blocks:
                for found in re.finditer(re.escape(span[:40]), block.text):
                    if low in block.text[max(0, found.start() - 600):found.end() + 600].lower():
                        return True
            return False
        others = [n for _s, n in quotes if n.split()[-1] != last and not beside(n)] \
            or ["Margaret Okafor"]
        other = others[rng.randrange(len(others))]
        if other.split()[-1] != last:
            cases.append(Case("quote_speaker", "quote", f'"{span}," {other} said.', True))
        words = span.split()
        if len(words) >= 5:
            at = len(words) // 2
            words[at] = "never" if words[at].lower() != "never" else "always"
            cases.append(Case("quote_words", "quote", f'"{" ".join(words)}," {speaker} said.',
                              True))
    for block in blocks:
        lines = _sentences(_body(block))
        if len(lines) >= 2:
            first, second = lines[0].rstrip(), lines[1].rstrip()
            cases.append(Case("quote_two", "quote_length",
                              f'Officials put it plainly: "{first} {second}"', True))
            break
    # Dates: "Yesterday" on an old story, and on a fresh one.
    old = fresh = None
    for sentence in real:
        if re.search(r"\b(today|tonight|yesterday|last night|this week|last week)\b",
                     sentence, re.I):
            continue
        # Every source close to it the same age, so the right answer is not
        # in doubt: all old, or the best one fresh.
        close = _close(sentence, blocks)
        ages = [_age(b, now) for b in close if not b.archive]
        if not close or any(a is None for a in ages) or not ages:
            continue
        if min(ages) >= 15 and old is None:
            old = sentence
        if _age(close[0], now) is not None and _age(close[0], now) <= 1 and fresh is None:
            fresh = sentence
    for sentence, kind, fail in ((old, "date_old", True), (fresh, "date_fresh", False)):
        if sentence:
            cases.append(Case(kind, "dates", "Yesterday, " + sentence[0].lower() + sentence[1:],
                              fail, sentence))
    # "For the first time", where no packet line says first.
    if not any(re.search(r"\bfirst\b", b.text, re.I) for b in blocks):
        sentence = real[rng.randrange(len(real))]
        if not re.search(r"\b(first|record|only|ever)\b", sentence, re.I):
            cases.append(Case("first", "superlative",
                              re.sub(r"[.!?]\s*$", "", sentence) + " for the first time.",
                              True, sentence))
    # "The biggest ... ever", where no packet line claims anything of the kind.
    if not any(re.search(r"\b(biggest|largest|ever|record|unprecedented)\b", b.text, re.I)
               for b in blocks):
        sentence = real[rng.randrange(len(real))]
        if not re.search(r"\b(biggest|largest|ever|record)\b", sentence, re.I):
            cases.append(Case("biggest", "superlative",
                              re.sub(r"[.!?]\s*$", "", sentence)
                              + ", the biggest move of its kind ever recorded.", True, sentence))
    cases.extend(plant_mixed(blocks, real, rng))
    cases.append(Case("advice", "advice", ADVICE[rng.randrange(len(ADVICE))], True))
    cases.append(Case("website", "website",
                      f"The full details are on {SITES[rng.randrange(len(SITES))]}.", True))
    return cases


def plant_mixed(blocks, sentences: list, rng: random.Random, other_blocks=()) -> list:
    """Two stories joined with no source for the link: a sentence one story's
    sources hold, and a person or place only another story's sources name.
    `other_blocks` is a second episode's packets, for an evaluation that
    joins two episodes' packets into one two-story packet - a single
    edition's packet is nearly always one story."""
    import news_clusters
    from agents import supervisor_checks

    texts = [b for b in list(blocks) + list(other_blocks) if not b.live]
    if len(texts) < 2:
        return []
    story_of = supervisor_checks._stories(texts)
    mine_blocks = [b for b in blocks if not b.live]
    def owned(name_stems, story) -> bool:
        holders = {story_of[j] for j, b in enumerate(texts) if name_stems & b.names}
        return holders == {story}

    for sentence in rng.sample(sentences, len(sentences)):
        block = _best(sentence, mine_blocks)
        if block is None:
            continue
        mine = story_of[texts.index(block)]
        # The sentence must name someone of its own story: a join needs two sides.
        words = sentence.split()
        opener = news_clusters.names(words[0]) if words else frozenset()
        own = {n for n in news_clusters.names(sentence)
               if n not in supervisor_checks.NOT_ENTITIES and n not in opener}
        if not any(owned({n}, mine) for n in own):
            continue
        for i, other in enumerate(texts):
            if story_of[i] == mine:
                continue
            body = _body(other)
            # A person or place named mid-sentence (never a headline's
            # capitalised fragment), only this other story's sources name.
            only = [n for n in _CAPS.findall(body)
                    if all(len(w) >= 3 for w in n.split())
                    and re.search(r"[a-z,]\s+" + re.escape(n) + r"\b", body)
                    and not (news_clusters.names(n) & supervisor_checks.NOT_ENTITIES)
                    and owned(news_clusters.names(n), story_of[i])]
            if only:
                return [Case("mixed", "mixed",
                             re.sub(r"[.!?]\s*$", "", sentence) + f", just as {only[0]} did.",
                             True, sentence)]
    return []
