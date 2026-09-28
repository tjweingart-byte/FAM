"""How the voice says names it would otherwise get wrong (§165).

The 28/09 packet: "Jaron-Keawe Sagapolutele", Cal's quarterback, and names
like it - people, places, companies - came out of the voice as a guess.
Chatterbox reads English spelling and has no phoneme input and no lexicon of
its own, so the letters it is handed are the only instruction it gets. A name
it has never seen is read as though it were an English word, which for
Samoan, Hawaiian, Polish or Vietnamese names is usually wrong.

So a hard name is **respelled** in the text handed to the voice - the same
move `spoken_text` makes for "612" and "NFL", and on the same terms: the
script, the captions, the cache and the title keep the real spelling, and
only what the voice is given changes. "Sagapolutele" is spoken from
"Sah-gah-poh-loo-teh-leh".

Where the respellings come from, cheapest first, and none of them a new
model call:

* **Episode intelligence** already resolves the subject before anything is
  looked up, so the brief carries a `pronounce` list for the names in the
  request and its resolved subject - a handful of output tokens on a call
  that is already being made.
* **The writer** holds the evidence, so it names the hard names the evidence
  brought in, on `<<SAY: Name = respelling>>` lines written *before* the
  script - before, because the first sentence is spoken before the rest of
  the episode exists, and a pronunciation that arrived with the last token
  would be too late for the only mention that matters.
* **An admin** can fix any name on /admin, and an admin's respelling is never
  replaced by a model's.

Every respelling is kept in one shared lexicon (a table in the voice bank's
database - it is a fact about how the voice speaks, and it needs no store of
its own), so a name learned once is said the same way in every later
episode, including a cached script voiced again.

What keeps a model's guess from doing damage: an entry must look like a name
(a capital letter, no digits, a sane length), a respelling must be plain
letters and hyphens, and a word every English speaker already says right is
refused. It never raises: on any surprise the text goes to the voice as it
was.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Iterable, Optional

log = logging.getLogger("fam.pronunciation")

#: The writer's line. Stripped before anything is spoken or stored, like
#: `<<TITLE:>>` and `<<NEXT:>>`.
SAY_MARKER = re.compile(r"<<\s*SAY\s*:\s*([^<>=]{1,80}?)\s*=\s*([^<>]{1,160}?)\s*>>", re.I)

#: Where an entry came from. The order is precedence: an admin's respelling
#: is never replaced by a model's.
SOURCES = ("admin", "writer", "brief")

#: How often a process re-reads the table, so a fix made on /admin (served
#: by one worker) reaches every other worker without a restart.
RELOAD_SECONDS = 60.0

#: Most entries a single episode may add. A writer listing forty names is not
#: telling us about hard names, it is listing every name.
MAX_PER_EPISODE = 12

_NAME = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿĀ-ſ][A-Za-zÀ-ÖØ-öø-ÿĀ-ſ'’.\- ]{1,59}$")
_SAY = re.compile(r"^[A-Za-z][A-Za-z'\- ]{1,119}$")

#: Words a model might list and that the voice already says right. Refusing
#: them is cheaper than hearing "The" respelled in every episode after one
#: bad guess.
_COMMON = frozenset("""
the a an and or of in on at to for with from by is it he she they we you i
will may june april august mark grace bill rich hope rose may jack art
""".split())


def clean(name: str, say: str) -> Optional[tuple[str, str]]:
    """(name, respelling) when both look like what they claim to be, else None."""
    name = re.sub(r"\s+", " ", (name or "").strip().strip("\"'“”"))
    say = re.sub(r"\s+", " ", (say or "").strip().strip("\"'“”.,;"))
    if not name or not say or not _NAME.match(name) or not _SAY.match(say):
        return None
    if not any(c.isupper() for c in name):
        return None
    if name.lower() in _COMMON or len(name.replace(" ", "")) < 3:
        return None
    if say.replace("-", "").replace(" ", "").lower() == \
            re.sub(r"[^a-z]", "", name.lower()):
        return None     # respelled as itself: nothing to do
    # A respelling many times the length of the name is a sentence, not a
    # pronunciation.
    if len(say) > 4 * len(name) + 12:
        return None
    return name, say


def voiced(say: str) -> str:
    """A respelling as the voice is handed it.

    Lower case, hyphens between syllables and a capital only on each word's
    first letter. A stressed syllable written in capitals ("loo-TEH") is kept
    in the lexicon for anybody reading it, but not handed to the voice as
    capitals: `spoken_text` would read "TEH" as the letters T, E, H.
    """
    words = [w.lower() for w in say.split()]
    return " ".join(w[:1].upper() + w[1:] for w in words if w)


def parse_markers(text: str) -> list[tuple[str, str]]:
    """Every `<<SAY:>>` pair in `text`, cleaned, in order."""
    out = []
    for m in SAY_MARKER.finditer(text or ""):
        pair = clean(m.group(1), m.group(2))
        if pair:
            out.append(pair)
    return out


def strip_markers(text: str) -> str:
    return SAY_MARKER.sub("", text or "")


def _split(name: str, say: str) -> list[tuple[str, str]]:
    """The whole name, then each word of it when the respelling lines up.

    A script names somebody in full once and by surname after that, so
    "Jaron Sagapolutele" alone would miss every later "Sagapolutele". When
    the respelling has as many words as the name, each word gets its own
    entry too - but only a word long enough to be a name rather than a
    particle ("de", "van").
    """
    pairs = [(name, say)]
    names, says = name.split(), say.split()
    if len(names) > 1 and len(names) == len(says):
        for n, s in zip(names, says):
            pair = clean(n, s)
            if pair and len(n) >= 4:
                pairs.append(pair)
    return pairs


class Lexicon:
    """The shared respellings, held in memory and in the voice bank's table.

    `apply` is on the synthesis path for every chunk, so it reads a compiled
    pattern and never the database; the table is re-read at most every
    `RELOAD_SECONDS`, and at once after a write from this process.
    """

    def __init__(self, store=None) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[str, str]] = {}
        self._pattern: Optional[re.Pattern] = None
        self._loaded = 0.0

    def _bank(self):
        if self._store is not None:
            return self._store
        import voice_bank
        return voice_bank.bank()

    def _compile(self) -> None:
        names = sorted(self._entries, key=len, reverse=True)
        self._pattern = (re.compile(
            r"(?<![A-Za-zÀ-ÿĀ-ſ'’-])(" + "|".join(re.escape(n) for n in names)
            + r")(?![A-Za-zÀ-ÿĀ-ſ-])") if names else None)

    def reload(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._loaded < RELOAD_SECONDS:
            return
        try:
            rows = self._bank().pronunciations()
        except Exception:  # noqa: BLE001 - a lexicon outage is not a voice outage
            log.exception("pronunciation lexicon could not be read")
            self._loaded = now
            return
        # Lowest precedence first, so a later row overwrites: a brief's
        # guess, then a writer's, then an admin's.
        rank = {s: i for i, s in enumerate(SOURCES)}
        rows = sorted(rows, key=lambda r: -rank.get(r.get("source"), len(SOURCES)))
        with self._lock:
            self._entries = {}
            for row in rows:
                for n, s in _split(row["name"], row["say"]):
                    self._entries[n] = (s, row["source"])
            self._compile()
            self._loaded = now

    def entries(self) -> dict[str, tuple[str, str]]:
        self.reload()
        with self._lock:
            return dict(self._entries)

    def apply(self, text: str) -> str:
        """`text` with every known hard name respelled. Never raises."""
        if not text:
            return text
        try:
            self.reload()
            with self._lock:
                pattern, entries = self._pattern, self._entries
            if pattern is None:
                return text
            return pattern.sub(lambda m: voiced(entries[m.group(1)][0]), text)
        except Exception:  # noqa: BLE001 - the voice still gets the words
            log.exception("pronunciation lexicon failed to apply")
            return text

    def learn(self, pairs: Iterable, source: str) -> int:
        """Keep what a brief or a writer said about names. Returns how many
        were new or changed. Never replaces an admin's respelling."""
        if source not in SOURCES:
            raise ValueError(source)
        kept = 0
        for raw in list(pairs or ())[:MAX_PER_EPISODE]:
            try:
                name, say = (raw["name"], raw["say"]) if isinstance(raw, dict) else raw
            except (KeyError, TypeError, ValueError):
                continue
            pair = clean(name, say)
            if not pair:
                continue
            try:
                if self._bank().learn_pronunciation(pair[0], pair[1], source):
                    kept += 1
            except Exception:  # noqa: BLE001
                log.exception("pronunciation for %r could not be kept", pair[0])
        if kept:
            log.info("pronunciation: %d name(s) learned from the %s", kept, source)
            self.reload(force=True)
        return kept

    def set(self, name: str, say: str) -> tuple[str, str]:
        """An admin's respelling. Raises ValueError with a readable reason."""
        pair = clean(name, say)
        if not pair:
            raise ValueError(
                "A name needs a capital letter and no digits, and a "
                "respelling is letters, spaces and hyphens - "
                "e.g. Sagapolutele = sah-gah-poh-loo-TEH-leh")
        self._bank().learn_pronunciation(pair[0], pair[1], "admin")
        self.reload(force=True)
        return pair

    def remove(self, name: str) -> bool:
        gone = self._bank().forget_pronunciation(name)
        self.reload(force=True)
        return gone


_LEXICON: Optional[Lexicon] = None
_LEXICON_LOCK = threading.Lock()


def lexicon() -> Lexicon:
    global _LEXICON
    with _LEXICON_LOCK:
        if _LEXICON is None:
            _LEXICON = Lexicon()
        return _LEXICON


def reset(lex: Optional[Lexicon] = None) -> None:
    """For tests: use `lex`, or build a fresh one on next use."""
    global _LEXICON
    with _LEXICON_LOCK:
        _LEXICON = lex


def respell(text: str) -> str:
    """The text handed to the voice, with every known hard name respelled."""
    return lexicon().apply(text)


def learn(pairs: Iterable, source: str) -> int:
    """Keep respellings from a brief or a writer. Never raises."""
    try:
        return lexicon().learn(pairs, source)
    except Exception:  # noqa: BLE001
        log.exception("pronunciation learning failed")
        return 0


def known_in(text: str, limit: int = 20) -> list[str]:
    """Names the lexicon already holds that appear in `text`, so a writer
    handed that text need not list them again. Never raises."""
    try:
        return sorted(n for n in lexicon().entries()
                      if re.search(r"(?<![A-Za-z])" + re.escape(n) + r"(?![A-Za-z])",
                                   text or ""))[:limit]
    except Exception:  # noqa: BLE001
        return []
