"""Autocorrect for what a listener types: messages and searchFAM (§142).

The owner asked for it not to rest on the phone's keyboard alone, "because it
doesn't always correct the spelling of something". The keyboard's own
correction is switched back on as well - both boxes had it turned off - and
this is the second pass behind it, run on each word as it is finished.

## Why it is this cautious

A general spell checker is the wrong instrument for this app's text, and
measured on it, it says so immediately: `pyspellchecker`'s best guess turns
"bitcoin" into "bitching", "nvidia" into "vida", "mahomes" into "mahomet" and
"messi" into "mess". Half of what people ask FAM about is a proper noun no
dictionary holds, and an autocorrect that rewrites the subject of a search is
far worse than one that misses a typo - a typo reaches episode intelligence,
which reads intent rather than spelling, while a "correction" arrives as a
confident, different question.

So a word is only changed when every one of these holds:

* it is plain lowercase letters (a capital, a digit or an all-caps word is a
  name, a ticker or a number, and is left alone - except the first word of a
  sentence, which the keyboard capitalises for them);
* it is not a known word, not FAM's own vocabulary and not common chat
  shorthand;
* the fix is **one edit** away - a swapped, missing, extra or wrong letter;
* the fix is a **common** word (`MIN_FREQUENCY`), so a rare dictionary entry
  never replaces a name;
* the fix is not the word with its last letter dropped - "messi" is not a
  misspelling of "mess";
* and it wins clearly: a swapped pair of letters is taken outright, otherwise
  the best candidate must be `DOMINANCE` times as common as the next.

Anything short of that returns the word unchanged. The interface undoes a
correction with one backspace, like a phone does, and does not offer that
word again.

Optional, like PDF attachments: without `pyspellchecker` installed this
returns every word unchanged and `available()` says so.
"""
from __future__ import annotations

import logging
import re
import threading
from functools import lru_cache
from typing import Optional

log = logging.getLogger(__name__)

#: How common a correction must be (occurrences in the checker's English
#: frequency list). 500 keeps "recession" and "tariff"-class words reachable
#: and keeps the 50-count long tail - "mahomet", "webby" - out.
MIN_FREQUENCY = 500

#: The bar for a three-letter word, which is only ever corrected by swapping
#: two letters into one of the commonest words there are ("teh" -> "the").
SHORT_FREQUENCY = 1_000_000

#: How much more common the winner must be than the runner-up, when the typo
#: is not a plain swap of two letters.
DOMINANCE = 2.0

#: Chat shorthand that is not a misspelling of anything.
SHORTHAND = frozenset("""
    lol lmao lmfao omg idk imo imho tbh btw brb thx ty np pls plz ur u r ya yea
    yeah yep nope nah haha hahaha hehe ok okay kk gg rn tho smh fyi irl ngl
    fomo goat vs etc
""".split())

#: Apostrophes a keyboard leaves out. Mapped rather than guessed: the
#: checker's own best answer for "thats" is "that" and for "youre" is "your".
CONTRACTIONS = {
    "im": "i'm", "ive": "i've", "dont": "don't", "doesnt": "doesn't",
    "didnt": "didn't", "cant": "can't", "wont": "won't", "wouldnt": "wouldn't",
    "couldnt": "couldn't", "shouldnt": "shouldn't", "isnt": "isn't",
    "arent": "aren't", "wasnt": "wasn't", "werent": "weren't",
    "havent": "haven't", "hasnt": "hasn't", "thats": "that's",
    "whats": "what's", "youre": "you're", "theyre": "they're",
    "theres": "there's", "lets": "let's", "hes": "he's", "shes": "she's",
    "youve": "you've", "weve": "we've", "theyve": "they've", "id": "i'd",
    "youll": "you'll", "itll": "it'll",
}
#: Contractions that are also ordinary words, so they are only expanded when
#: nothing else could be meant - "id", "lets", "hes", "shes", "wont" and
#: "cant" are all real words or common enough as typed to leave alone.
AMBIGUOUS_CONTRACTIONS = frozenset({"id", "lets", "hes", "shes", "wont", "cant"})

_WORD = re.compile(r"^[a-z]+(?:'[a-z]+)?$")

_lock = threading.Lock()
_checker = None
_failed = False


def _spell():
    """The checker, built once, or None when the package is missing."""
    global _checker, _failed
    if _checker is not None or _failed:
        return _checker
    with _lock:
        if _checker is None and not _failed:
            try:
                from spellchecker import SpellChecker
                _checker = SpellChecker(distance=1)
            except Exception as exc:  # missing package, unreadable data
                _failed = True
                log.warning("autocorrect unavailable (%s); words pass through"
                            " unchanged", exc)
    return _checker


def available() -> bool:
    return _spell() is not None


def warm() -> None:
    """Load the word list and FAM's vocabulary before anybody types.

    Building both costs most of a second, once per worker; paid at startup,
    off the event loop, rather than on the first word somebody finishes.
    """
    try:
        _spell()
        _vocabulary()
    except Exception:
        log.exception("could not warm autocorrect; it will load on first use")


@lru_cache(maxsize=1)
def _vocabulary() -> frozenset:
    """FAM's own words, which are never corrected: the catalogue's subjects,
    the facet labels and the seeded category tree."""
    words: set[str] = set()
    try:
        import topics
        for interest in topics.INTEREST_CATALOGUE:
            words.update(re.findall(r"[a-z]+", interest.label.lower()))
        for label in topics.TAG_LABELS.values():
            words.update(re.findall(r"[a-z]+", label.lower()))
    except Exception:
        log.exception("could not read the catalogue for autocorrect")
    try:
        import category_seed
        for name, parent in category_seed.rows():
            words.update(re.findall(r"[a-z]+", f"{name} {parent}".lower()))
    except Exception:
        log.exception("could not read the category seed for autocorrect")
    return frozenset(words)


def _is_swap(word: str, candidate: str) -> bool:
    """True when `candidate` is `word` with two neighbouring letters swapped."""
    if len(word) != len(candidate) or word == candidate:
        return False
    diff = [i for i, (a, b) in enumerate(zip(word, candidate)) if a != b]
    return (len(diff) == 2 and diff[1] == diff[0] + 1
            and word[diff[0]] == candidate[diff[1]]
            and word[diff[1]] == candidate[diff[0]])


def correct_word(word: str, first: bool = False) -> Optional[str]:
    """The correction for one typed word, or None to leave it as it is.

    `first` says the word starts a sentence, where a capital is the keyboard's
    and not a name - so "Teh" may become "The", and "Messi" mid-sentence is
    never touched.
    """
    if not word:
        return None
    capital = False
    lower = word
    if word != word.lower():
        if not (first and word[0].isupper() and word[1:] == word[1:].lower()):
            return None   # a name, a ticker, an acronym
        capital = True
        lower = word.lower()
    fixed = _correct_lower(lower)
    if fixed is None or fixed == lower:
        return None
    if capital or fixed.startswith("i'") or fixed == "i":
        fixed = fixed[0].upper() + fixed[1:]
    return fixed


@lru_cache(maxsize=4096)
def _correct_lower(word: str) -> Optional[str]:
    if not _WORD.match(word) or word in SHORTHAND:
        return None
    if word in CONTRACTIONS and word not in AMBIGUOUS_CONTRACTIONS:
        return CONTRACTIONS[word]
    spell = _spell()
    if spell is None:
        return None
    if word in spell or word in _vocabulary():
        return None
    try:
        candidates = spell.candidates(word) or set()
    except Exception:
        log.exception("autocorrect failed on %r", word)
        return None
    freq = spell.word_frequency
    scored = []
    for c in candidates:
        if c == word or "'" in c and c.replace("'", "") != word:
            continue
        # "messi" -> "mess", "happend" -> "happen": the word is the fix with
        # a letter on the end, which is how a name looks next to a word far
        # more often than it is how a typo looks.
        if len(c) == len(word) - 1 and word.startswith(c):
            continue
        # "messi" -> "messy", "ohtani" -> "ohtana": a name ending in a vowel
        # sits one final letter from a word constantly. A last letter is
        # only replaced when the typed one was not a vowel.
        if (len(c) == len(word) and c[:-1] == word[:-1]
                and word[-1] in "aeiou"):
            continue
        # A changed letter in a four-letter word is a guess between a name
        # and a word ("siri" -> "sari"); from five letters up the rest of
        # the word says which.
        if len(c) == len(word) and len(word) < 5 and not _is_swap(word, c):
            continue
        scored.append((freq[c], c))
    if not scored:
        return None
    scored.sort(reverse=True)
    # Three letters or fewer has too many neighbours to guess among: "nba"
    # is a swap away from "nab", "lol" one letter from "lot". Only a swap into
    # one of the language's commonest words ("teh" -> "the") is taken.
    if len(word) <= 3:
        swaps = [c for f, c in scored if _is_swap(word, c) and f >= SHORT_FREQUENCY]
        return swaps[0] if len(swaps) == 1 else None
    swaps = [c for f, c in scored if _is_swap(word, c) and f >= MIN_FREQUENCY]
    if len(swaps) == 1:
        return swaps[0]
    # A letter left out is the commonest typo there is, so when the typed
    # letters all survive, in order, inside a candidate one letter longer,
    # those candidates are asked first: "leage" is "league", not the more
    # common "leave".
    missing = [(f, c) for f, c in scored
               if len(c) == len(word) + 1 and _contains(c, word)]
    pick = _clear_winner(missing)
    if pick:
        return pick
    return _clear_winner(scored)


def _clear_winner(scored: list) -> Optional[str]:
    """The top of a (frequency, word) list sorted high to low, if it is
    common enough and clearly ahead of the next; otherwise None."""
    if not scored:
        return None
    best_f, best = scored[0]
    if best_f < MIN_FREQUENCY:
        return None
    if len(scored) > 1 and best_f < DOMINANCE * scored[1][0]:
        return None
    return best


def _contains(longer: str, shorter: str) -> bool:
    """True when `shorter` is `longer` with exactly one letter removed."""
    it = iter(longer)
    return all(ch in it for ch in shorter)
