"""What FAM will not say, and what it marks as explicit (PROBLEMS.md §171).

At the owner's direction: "we don't want any censorship from any media sources
or speech options. The only thing we want to filter out are any words that
could be considered slurs, or were made to be hateful to a specific group of
people ... Cuss words are ok to be in the episodes, but if they are in an
episode, there should be an explicit symbol."

So two lists, with opposite consequences, and neither is a profanity filter:

- **`SLURS` are taken out** of everything an episode says or shows - the
  sentences the voice speaks and captions show, the title, the summary and
  the Go Deeper line. A slur becomes the words "a slur", which is how a
  careful reporter says one was used: "he called them a slur" still carries
  the fact the story is about.
- **`PROFANITY` is left in** and only *detected*: an episode with a cuss word
  in it is `explicit`, and every surface that names it draws the E.

**Overfiltering is the failure this module is built against**, so matching is
whole words only - never substrings, which is how "Scunthorpe", "Dickens",
"assessment" and "cocktail" get bleeped by filters that look for letters. A
word is on `SLURS` only when its dominant use is as contempt for a group of
people. Words that are slurs in one sense and ordinary in another are left off
on purpose, listed in `DELIBERATELY_ABSENT` with why, so nobody "fixes" the
gap: a surname (Carrie Coon, Dick Van Dyke), a food, a martial art, a place,
a team's former name a sports episode has to be able to say. Where one idiom
is the whole problem ("a chink in the armour", "honky-tonk") the word is kept
and the idiom excepted.

The writer is told the same thing in its system prompt; this is the backstop,
applied in code, because a quote in a source is exactly where a slur arrives.
"""

from __future__ import annotations

import re
from typing import Iterable

#: Stems whose dominant use is contempt for a group of people. Each is matched
#: as a whole word, with the plural and the "-ed"/"-ing" forms the stem takes
#: listed out rather than guessed at by a suffix rule.
SLURS: tuple[str, ...] = (
    # Race and ethnicity
    "nigger", "niggers", "nigga", "niggas", "niggaz", "sandnigger", "sandniggers",
    "chink", "chinks", "gook", "gooks", "spic", "spics", "spick", "spicks",
    "wetback", "wetbacks", "beaner", "beaners", "kike", "kikes", "kyke",
    "heeb", "heebs", "hymie", "hymies", "raghead", "ragheads", "towelhead",
    "towelheads", "jigaboo", "jigaboos", "pickaninny", "pickaninnies",
    "porchmonkey", "porchmonkeys", "zipperhead", "zipperheads", "wop", "wops",
    "dago", "dagos", "dagoes", "paki", "pakis", "chinaman", "chinamen",
    "injun", "injuns", "honky", "honkies", "honkeys", "wigger", "wiggers",
    "slanteye", "slanteyes", "jap", "japs",
    # Sexuality and gender
    "faggot", "faggots", "fag", "fags", "faggy", "tranny", "trannies",
    "shemale", "shemales",
    # Disability
    "retard", "retards", "retarded", "spaz", "spazz",
)

#: Two-word slurs, matched as phrases (any whitespace or a hyphen between).
SLUR_PHRASES: tuple[tuple[str, str], ...] = (
    ("sand", "nigger"), ("sand", "niggers"),
    ("porch", "monkey"), ("porch", "monkeys"),
    ("slant", "eye"), ("slant", "eyes"), ("slant", "eyed"),
    ("camel", "jockey"), ("camel", "jockeys"),
)

#: Idioms and compounds that contain a listed word and are not a slur. Matched
#: first, and left exactly as written.
EXCEPTIONS: tuple[str, ...] = (
    # "a chink in the armour", "the only chink in the Chiefs' armor".
    r"chinks?\s+in\s+(?:[\w'\u2019]+\s+){0,3}armou?r",
    r"chinks?\s+of\s+light",
    r"honky[\s-]*tonks?",
    # "Retard" as a verb in engineering and pharmacology: "to retard the
    # spread", "retards corrosion". Only after "to"/"will"/"may"/"can" or
    # before a noun phrase the verb takes; the insult is never in these.
    r"(?:to|will|may|can|could|would|might|should)\s+retard\b",
    r"retard(?:s|ed)?\s+(?:the|its|their|growth|corrosion|spread|combustion|"
    r"ignition|fire|flames?|progress|development|germination)\b",
    r"retarded\s+(?:growth|development|progress|combustion|ignition)",
    r"flame[\s-]*retard\w*",
    r"fire[\s-]*retard\w*",
)

#: Words that are slurs in some mouths and ordinary in most sentences, left
#: off `SLURS` on purpose. Here so the next person to see one on a list online
#: finds the reason before they add it.
DELIBERATELY_ABSENT: dict[str, str] = {
    "coon": "a surname (Carrie Coon) and a hunting dog breed's name",
    "dyke": "a surname (Dick Van Dyke), a Dutch word, and reclaimed",
    "sambo": "a Russian martial art on every combat-sports card",
    "cracker": "a food",
    "gypsy": "the name some Romani and Traveller people use for themselves",
    "redskins": "a team's former name, which history episodes have to say",
    "squaw": "in place names being renamed, which the news reports",
    "negro": "in institution names (the United Negro College Fund)",
    "queer": "reclaimed; an academic field and a common self-description",
    "yid": "a contested football-terrace usage the sports news reports",
    "coolie": "a historical labour term in history episodes",
    "shylock": "a Shakespeare character",
    "gringo": "mild, and the title of films and songs",
    "spastic": "a clinical term (spastic cerebral palsy, spastic paralysis)",
}

#: Cuss words. Kept in the episode; their presence makes it `explicit`.
#: Whole words only - "ass", "hell", "damn" and "crap" are not here, because an
#: E on every episode about Hell's Kitchen or a donkey sanctuary would make the
#: mark mean nothing.
PROFANITY: tuple[str, ...] = (
    "fuck", "fucks", "fucked", "fucker", "fuckers", "fucking", "fuckin",
    "motherfucker", "motherfuckers", "motherfucking", "fuckup", "fuckups",
    "shit", "shits", "shitty", "shitting", "shitted", "bullshit", "horseshit",
    "shithead", "shitheads", "shitshow",
    "bitch", "bitches", "bitching", "bitchy",
    "asshole", "assholes", "arsehole", "arseholes", "dumbass", "jackass",
    "bastard", "bastards", "cunt", "cunts", "twat", "twats",
    "dickhead", "dickheads", "cocksucker", "cocksuckers",
    "pussy", "wanker", "wankers", "bollocks", "goddamn", "goddamned",
    "douchebag", "douchebags",
)


def _alternation(words: Iterable[str]) -> str:
    # Longest first, so "niggers" is tried before "nigger".
    return "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))


_WORD = re.compile(r"(?<![A-Za-z])(" + _alternation(SLURS) + r")(?![A-Za-z])", re.I)
_PHRASE = re.compile(
    r"(?<![A-Za-z])(?:"
    + "|".join(re.escape(a) + r"[\s-]+" + re.escape(b) for a, b in SLUR_PHRASES)
    + r")(?![A-Za-z])", re.I)
_EXCEPT = re.compile("|".join("(?:%s)" % e for e in EXCEPTIONS), re.I)
_PROFANE = re.compile(r"(?<![A-Za-z])(" + _alternation(PROFANITY) + r")(?![A-Za-z])",
                      re.I)
#: What a slur is replaced with, and how the words around it are tidied so the
#: sentence still reads: "called him a X" and "used the word X" both become
#: "... a slur", never "a a slur" or "the word a slur".
_LEAD = re.compile(r"\b(?:the\s+(?:word|term|slur)|an?|the)\s+$", re.I)
_SENTENCE_START = re.compile(r"(?:^|[.!?])\s*[\"'“‘]?\s*$")
_SINGULAR_ENDING_S = frozenset(("spaz", "chinaman"))


def _plural(word: str) -> bool:
    w = re.sub(r"[\s-]+", "", word.lower())
    return (w.endswith(("s", "z", "men")) and w not in _SINGULAR_ENDING_S)


def _masked(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in _EXCEPT.finditer(text)]


def _inside(span: tuple[int, int], masks: list[tuple[int, int]]) -> bool:
    return any(a <= span[0] and span[1] <= b for a, b in masks)


def slurs_in(text: str) -> list[str]:
    """Every slur in `text`, as written. Empty for clean text."""
    if not text:
        return []
    masks = _masked(text)
    found = [m.group(0) for m in _PHRASE.finditer(text) if not _inside(m.span(), masks)]
    found += [m.group(0) for m in _WORD.finditer(text) if not _inside(m.span(), masks)]
    return found


def scrub(text: str) -> str:
    """`text` with every slur replaced by "a slur" (or "slurs").

    Nothing else changes: no profanity is touched, no word that merely
    contains a slur's letters, no idiom in `EXCEPTIONS`. Clean text comes back
    as the same string.
    """
    if not text or not slurs_in(text):
        return text
    # Phrases first, so "sand X" goes as one slur rather than leaving "sand".
    for pattern in (_PHRASE, _WORD):
        masks = _masked(text)
        out, last = [], 0
        for m in pattern.finditer(text):
            if _inside(m.span(), masks):
                continue
            before = text[last:m.start()]
            # "a X", "the X", "the word X" all become "a slur"; a plural
            # takes no article at all.
            lead = _LEAD.search(before)
            if lead:
                before = before[:lead.start()]
            replacement = "slurs" if _plural(m.group(0)) else "a slur"
            if _SENTENCE_START.search("".join(out) + before):
                replacement = replacement[0].upper() + replacement[1:]
            out.append(before + replacement)
            last = m.end()
        out.append(text[last:])
        text = "".join(out)
    return text


def scrub_all(sentences: Iterable[str]) -> list[str]:
    return [scrub(s) for s in sentences]


def has_slur(sentences: Iterable[str]) -> bool:
    return any(slurs_in(s) for s in sentences)


def is_explicit(sentences) -> bool:
    """Does this episode swear? A string or a list of sentences."""
    if isinstance(sentences, str):
        sentences = (sentences,)
    return any(_PROFANE.search(s or "") for s in sentences)
