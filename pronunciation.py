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
* **A seed** (§656, `SEED` below): well-known names an English speaker
  says one way and the voice reads another - "Iran" as "eye-ran" - which no
  model lists, because to a model they are not hard, and which the CMU scan
  (`hard_words.py`) never flags, because the dictionary has them. Written to
  the table once per process, under an admin and over everything else.
* **Wikipedia** (10.16 #6, `wiki_pronounce.py`): every name a model adds is
  looked up in the background, and an article's own respelling or IPA
  replaces a model's guess it disagrees with. Below an admin, above both
  models.

Every respelling is kept in one shared lexicon (a table in the voice bank's
database - it is a fact about how the voice speaks, and it needs no store of
its own), so a name learned once is said the same way in every later
episode, including a cached script voiced again.

**A personal episode's names are never kept** - an attachment, or a
question the cache already refuses to share. They are held in memory for
that episode (`Lexicon.hold`) and never reach the shared table or /admin.

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
SOURCES = ("admin", "seed", "wikipedia", "writer", "brief")

#: Names the voice says wrong although every listener knows them (§656).
#: The owner: "We are still having pronunciation issues within the app, even
#: with names as similar as the country name 'Iran'". The lexicon only ever
#: learned names a model chose to list, and no model lists "Iran". These are
#: the common American broadcast readings, chosen by hand and *unheard* in
#: Chatterbox - /admin's "Hear 4 ways" is how one is checked. An admin's
#: respelling replaces any of them; removing one on /admin brings it back at
#: the next boot, so a wrong seed is fixed by setting the right one.
SEED = {
    # Countries and their people
    "Iran": "ih-RAHN", "Iranian": "ih-RAY-nee-un", "Iranians": "ih-RAY-nee-unz",
    "Iraq": "ih-RAHK", "Iraqi": "ih-RAH-kee", "Iraqis": "ih-RAH-keez",
    "Qatar": "KAH-tar", "Qatari": "KAH-tuh-ree", "Qataris": "KAH-tuh-reez",
    "Oman": "oh-MAHN", "Omani": "oh-MAH-nee", "Omanis": "oh-MAH-neez",
    "Bahrain": "bah-RAYN", "Bahraini": "bah-RAY-nee", "Bahrainis": "bah-RAY-neez", "Niger": "nee-ZHAIR", "Chile": "CHEE-lay",
    "Uruguay": "YOOR-uh-gway", "Venezuela": "veh-neh-ZWAY-luh",
    "Venezuelan": "veh-neh-ZWAY-lun", "Caribbean": "kair-ih-BEE-un", "Myanmar": "MYAHN-mar", "Lesotho": "leh-SOO-too",
    # Cities and places in the news
    "Tehran": "teh-RAHN", "Kyiv": "KEE-yiv", "Beijing": "bay-JING",
    "Doha": "DOH-hah", "Riyadh": "ree-YAHD", "Kabul": "KAH-bool",
    "Gaza": "GAH-zuh", "Rafah": "RAH-fah", "Tbilisi": "tuh-bih-LEE-see",
    "Qom": "kohm", "Hormuz": "hor-MOOZ", "Fordow": "for-DOH", "Natanz": "nah-TAHNZ",
    "Khan Younis": "KAHN yoo-NEES", "Kharkiv": "KAR-kiv", "Donetsk": "duh-NETSK",
    "Donbas": "don-BAHS", "Lviv": "luh-VEEV", "Kherson": "kehr-SAWN",
    "Odesa": "oh-DESS-uh", "Seoul": "SOHL", "Pyongyang": "pyuhng-YAHNG",
    "Xinjiang": "shin-JYAHNG", "Shenzhen": "shen-JEN", "Taipei": "ty-PAY",
    "Des Moines": "deh MOYN", "Tucson": "TOO-sahn", "Boise": "BOY-see",
    "Spokane": "spoh-KAN",
    # Names and groups in the news
    "Xi": "shee", "Xi Jinping": "shee jin-PING", "Erdogan": "AIR-doh-wahn",
    "Erdoğan": "AIR-doh-wahn", "Khamenei": "kah-meh-NAY-ee", "Houthi": "HOO-thee", "Houthis": "HOO-theez",
    "Hezbollah": "hez-boh-LAH", "Modi": "MOH-dee",
    "Putin": "POO-tin", "Hamas": "hah-MAHS", "Qaeda": "KY-duh",
    "Zelensky": "zeh-LEN-skee", "Zelenskyy": "zeh-LEN-skee",
    "Kim Jong Un": "KIM jong OON", "Macron": "mah-KROHN", "Merz": "MAIRTS",
    "Milei": "mee-LAY", "Guterres": "goo-TEHR-esh", "Noem": "NOHM",
    "Leavitt": "LEV-it", "Thune": "THOON", "Alito": "uh-LEE-toh",
    # Sports (§656's audit): names an English reading gets wrong
    "Jokic": "YOH-kitch", "Doncic": "DON-chitch",
    "Antetokounmpo": "an-teh-toh-KOON-poh", "Kelce": "KEL-see",
    "Mahomes": "muh-HOHMZ", "Mbappe": "em-bah-PAY", "Mbappé": "em-bah-PAY",
    "Djokovic": "JOH-kuh-vitch", "Gauff": "GAWF", "Pulisic": "POO-lih-sik",
    "Leclerc": "luh-KLAIR", "Draisaitl": "DRY-sy-tul",
    # Companies and products in markets stories
    "Huawei": "WAH-way", "Xiaomi": "SHOW-mee", "Hyundai": "HUN-day",
    "Porsche": "POR-shuh", "Chipotle": "chih-POHT-lay", "Wegovy": "wee-GOH-vee",
}

#: Seed names under three letters, allowed past `clean`'s length rule because
#: they were chosen by hand ("Xi" is said in every story about China).
_SHORT_SEED = frozenset({"Xi"})

#: How often a process re-reads the table, so a fix made on /admin (served
#: by one worker) reaches every other worker without a restart.
RELOAD_SECONDS = 60.0

#: How long a personal episode's respellings are held in memory (§165).
#: Long enough for the longest episode to finish speaking, and never written
#: down: a name from somebody's own document or private question is theirs.
HOLD_SECONDS = 30 * 60

#: Most entries a single episode may add. A writer listing forty names is not
#: telling us about hard names, it is listing every name.
MAX_PER_EPISODE = 12

_NAME = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿĀ-ſ][A-Za-zÀ-ÖØ-öø-ÿĀ-ſ'’.\- ]{1,59}$")
#: A code, one word of capitals and digits (§288): "ZBTB44" is a hard name
#: too, and a `<<SAY:>>` line for it used to be thrown away. A name with a
#: number beside it ("Area 51") is still `spoken_text`'s.
_CODE = re.compile(r"^[A-Z][A-Z]*\d[A-Z0-9]{0,10}$")
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
    if not name or not say or not (_NAME.match(name) or _CODE.match(name)) or not _SAY.match(say):
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


#: Letter pairs that are one sound when they meet, so the hyphen between them
#: stays: "mat-hew" joined would be "mathew", a th.
_DIGRAPHS = frozenset("th sh ch ph gh wh zh kh ng".split())
#: A respelling's short vowels, written with an h ("ih", "eh", "uh"), and
#: the consonants English doubles after one to keep it short (§657).
_SHORT_VOWELS = frozenset(("ih", "eh"))
#: Endings whose h is a real sound, not a vowel's mark ("shah", "pooh").
_KEEP_H = frozenset(("shah",))
_DOUBLES = frozenset("bdfglmnprst")


def _joined(word: str) -> str:
    """One respelled word's syllables as one word: "sah-gah-poh" -> "sahgahpoh",
    keeping a hyphen only where joining would make a new sound (§576).

    `word` keeps its capitals, which mark the stressed syllable; what comes
    back is lower case. A syllable's closing h only marks its vowel ("ih",
    "eh", "uh", "ah", "oh"), and joined it is a sound the voice breathes:
    "ih-RAHN" handed over as "Ihrahn" was heard as "eef-rahn" (§657). So
    where a marked vowel meets the next syllable inside a word:

    * a short "ih" or "eh" loses its h, and the consonant after it doubles
      where English doubles one to keep a vowel short ("Irrahn", as in
      irritate; "Terrahn"; "Zellenskee"), else just loses its h;
    * an unstressed "uh" - the schwa - is written "a", as English writes it
      ("ab-dul-kuh-REEM" -> "Abdulkareem", "uh-DAH-nee" -> "Adahnee"):
      doubled, it drew the stress ("Uddahnee");
    * any marked vowel before another vowel loses its h and keeps a hyphen,
      or the h is said ("ah-jah-EH-roh" was "Ahjahehroh").
    """
    raw = [p for p in word.split("-") if p]
    parts = [p.lower() for p in raw]
    if not parts:
        return ""
    out = parts[0]
    for i, part in enumerate(parts[1:], 1):
        meet = out[-1:] + part[:1]
        # An h after a vowel or an h is swallowed when joined ("doh-hah" ->
        # "Dohhah", "muh-HOHMZ" -> "Muhhohmz"): its hyphen stays (§656).
        if meet in _DIGRAPHS or (part[:1] == "h" and out[-1:] in "aeiouh"):
            out += "-" + part
            continue
        marked = out[-2:] if (out[-1:] == "h" and out[-2:-1] in "aeiou"
                              and out[-3:] not in _KEEP_H) else ""
        if marked and part[:1] in "aeiou":
            out = out[:-1] + "-" + part
        elif marked in _SHORT_VOWELS and part[:2] not in _DIGRAPHS:
            out = out[:-1] + (part[:1] if part[:1] in _DOUBLES else "") + part
        elif marked == "uh" and raw[i - 1].islower() and part[:2] not in _DIGRAPHS:
            out = out[:-2] + "a" + part
        elif marked in _SHORT_VOWELS or marked == "uh":
            out = out[:-1] + part           # before a digraph: the h only
        else:
            out += part
    return out


def voiced(say: str) -> str:
    """A respelling as the voice is handed it.

    Lower case, a capital only on each word's first letter, and each word's
    syllables run together. A stressed syllable written in capitals
    ("loo-TEH") is kept in the lexicon for anybody reading it, but not handed
    to the voice as capitals: `spoken_text` would read "TEH" as the letters
    T, E, H. The hyphens between syllables went to the voice too until §576,
    and Chatterbox said "Sah-gah-poh-loo-teh-leh" as six clipped pieces - the
    owner heard every respelled name as choppy - so a word is handed over
    whole ("Sahgahpohlooteleh"), as the words it learned from are written.
    """
    words = [_joined(w) for w in say.split()]
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


def _cleaned(pairs: Iterable) -> list[tuple[str, str]]:
    """`(name, say)` pairs or `{"name", "say"}` dicts, cleaned, at most
    `MAX_PER_EPISODE`."""
    out = []
    for raw in list(pairs or ())[:MAX_PER_EPISODE]:
        try:
            name, say = (raw["name"], raw["say"]) if isinstance(raw, dict) else raw
        except (KeyError, TypeError, ValueError):
            continue
        pair = clean(str(name), str(say))
        if pair:
            out.append(pair)
    return out


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
            if pair and len(n) >= 5:
                pairs.append(pair)
    return pairs


class Lexicon:
    """The shared respellings, held in memory and in the voice bank's table.

    `apply` is on the synthesis path for every chunk, so it reads a compiled
    pattern and never the database; the table is re-read at most every
    `RELOAD_SECONDS`, and at once after a write from this process.
    """

    def __init__(self, store=None, seed: bool = True) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[str, str]] = {}
        self._stored: dict[str, tuple[str, str]] = {}
        self._pattern: Optional[re.Pattern] = None
        self._loaded = 0.0
        #: name -> (say, source, expires): respellings from personal episodes,
        #: in this process only and never in the table. See `hold`.
        self._held: dict[str, tuple[str, str, float]] = {}
        #: False: the table is never seeded (`SEED`) - for tests about an
        #: empty lexicon.
        self._seeded = not seed

    def _bank(self):
        if self._store is not None:
            return self._store
        import voice_bank
        return voice_bank.bank()

    def _merged(self) -> dict[str, tuple[str, str]]:
        """The table's entries with any unexpired held ones under them: a
        stored respelling (an admin's above all) still wins."""
        now = time.time()
        self._held = {n: v for n, v in self._held.items() if v[2] > now}
        merged = {n: (v[0], v[1]) for n, v in self._held.items()}
        merged.update(self._stored)
        return merged

    def _compile(self) -> None:
        entries = self._merged()
        # A name in capitals ("IRAN", "HAMAS" in a headline) is matched too
        # (§656): `speakable` turns it into "Iran" only after the lexicon
        # has looked, and matching is by case. From four letters, so "XI"
        # the numeral is never "Xi".
        for name, value in list(entries.items()):
            upper = name.upper()
            if len(name.replace(" ", "")) >= 4 and upper != name:
                entries.setdefault(upper, value)
        self._entries = entries
        names = sorted(self._entries, key=len, reverse=True)
        self._pattern = (re.compile(
            # A hyphen is a boundary, so "Keawe" is found in "Jaron-Keawe";
            # a letter or an apostrophe before it is not ("O'Keawe").
            r"(?<![A-Za-zÀ-ÿĀ-ſ'’])(" + "|".join(re.escape(n) for n in names)
            + r")(?![A-Za-zÀ-ÿĀ-ſ])") if names else None)

    def _seed(self) -> None:
        """Write `SEED` to the table, once per process (§656). A seed never
        replaces an admin's respelling (`learn_pronunciation` ranks it), and a
        changed seed replaces the old one at the next boot."""
        self._seeded = True
        bank = self._bank()
        added = 0
        for name, say in SEED.items():
            try:
                if bank.learn_pronunciation(name, say, "seed"):
                    added += 1
            except Exception:  # noqa: BLE001 - a seed is never worth an outage
                log.exception("pronunciation seed for %r could not be kept", name)
                return
        # A name taken out of `SEED` is taken out of the table too; one an
        # admin has since set is an admin's row and is left alone.
        try:
            for row in bank.pronunciations():
                if row.get("source") == "seed" and row["name"] not in SEED:
                    bank.forget_pronunciation(row["name"])
        except Exception:  # noqa: BLE001
            log.exception("pronunciation: dropped seeds could not be removed")
        if added:
            log.info("pronunciation: %d seeded name(s) written", added)

    def reload(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._loaded < RELOAD_SECONDS:
            return
        if not self._seeded:
            self._seed()
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
            self._stored = {}
            for row in rows:
                for n, s in _split(row["name"], row["say"]):
                    self._stored[n] = (s, row["source"])
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

    def hold(self, pairs: Iterable, source: str) -> int:
        """Use these respellings in this process for `HOLD_SECONDS`, and never
        write them down.

        For a personal episode - an attachment, or a question the cache
        already refuses to share (`cache.is_shareable`). Its names still have
        to be said right, but the lexicon is shared by every listener and
        shown on /admin, and a name out of somebody's own document does not
        belong in either.
        """
        until = time.time() + HOLD_SECONDS
        held = 0
        with self._lock:
            for pair in _cleaned(pairs):
                for n, s in _split(*pair):
                    self._held[n] = (s, source, until)
                held += 1
            if held:
                self._compile()
        return held

    def learn(self, pairs: Iterable, source: str) -> int:
        """Keep what a brief or a writer said about names. Returns how many
        were new or changed. Never replaces an admin's respelling."""
        if source not in SOURCES:
            raise ValueError(source)
        pairs = list(pairs)     # read twice below (review)
        kept = 0
        for pair in _cleaned(pairs):
            try:
                if self._bank().learn_pronunciation(pair[0], pair[1], source):
                    kept += 1
            except Exception:  # noqa: BLE001
                log.exception("pronunciation for %r could not be kept", pair[0])
        if kept:
            log.info("pronunciation: %d name(s) learned from the %s", kept, source)
            self.reload(force=True)
            if source in ("writer", "brief"):
                # Checked against Wikipedia in the background (10.16 #6),
                # never on the episode's path: a model's guess Wikipedia
                # disagrees with is replaced before most listeners hear it.
                try:
                    import wiki_pronounce
                    wiki_pronounce.queue([p[0] for p in _cleaned(pairs)])
                except Exception:  # noqa: BLE001
                    log.warning("pronunciation: could not queue a Wikipedia check")
        return kept

    def set(self, name: str, say: str) -> tuple[str, str]:
        """An admin's respelling. Raises ValueError with a readable reason."""
        pair = clean(name, say)
        if not pair and name.strip() in _SHORT_SEED:
            # "Xi" is under `clean`'s three letters but is seeded, so an
            # admin must be able to replace it (§656).
            say = re.sub(r"\s+", " ", (say or "").strip())
            pair = (name.strip(), say) if _SAY.match(say) else None
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


def learn(pairs: Iterable, source: str, keep: bool = True) -> int:
    """Respellings from a brief or a writer: kept in the shared lexicon, or -
    `keep=False`, for a personal episode - only held in this process for a
    while (`Lexicon.hold`). Never raises."""
    try:
        lex = lexicon()
        return lex.learn(pairs, source) if keep else lex.hold(pairs, source)
    except Exception:  # noqa: BLE001
        log.exception("pronunciation learning failed")
        return 0


def known_in(text: str, limit: int = 20) -> list[str]:
    """Names the lexicon already holds that appear in `text`, so a writer
    handed that text need not list them again. Never raises."""
    try:
        lex = lexicon()
        lex.reload()
        # Stored names only: a held one came from somebody's personal episode.
        return sorted(n for n in dict(lex._stored)
                      if re.search(r"(?<![A-Za-z])" + re.escape(n) + r"(?![A-Za-z])",
                                   text or ""))[:limit]
    except Exception:  # noqa: BLE001
        return []
