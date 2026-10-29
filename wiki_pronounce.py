"""How Wikipedia says a name, checked against the lexicon (10.16 #6).

The owner (10.16): "cross check the list of how names are said with the new
wikipedia plugins ... the list is pretty inclusive, but still has gaps and
some of the pronunciations are incorrect on the admin page. Wikipedia is very
good with pronunciation and should help to correct pronunciations that are
already in the admin page and produce new ones."

The lexicon (`pronunciation.py`, §165) is filled by two guesses - the brief's,
made before anything was read, and the writer's - and an admin's fixes. An
English Wikipedia article about a person or place usually opens with how the
name is said, written by people who checked: a `{{respell}}` template (already
a respelling, "ab-DOOL-kə-REEM"), or `{{IPAc-en}}` / `{{IPA}}` in the
International Phonetic Alphabet, which this module turns into the same
respelling the voice is handed. Wikidata's "IPA transcription" (P898) is read
when the article has neither.

**Resolved the reference look's way (§278), never guessed**: Wikidata's
search must return exactly one item whose English label (or the alias it
matched) is the name, ignoring case and accents; two or more is no answer,
because a pronunciation of the wrong "John Smith" is worse than none.

**What it changes.** Wikipedia ranks below an admin and above the writer and
the brief (`pronunciation.SOURCES`): a model's guess that Wikipedia disagrees
with is replaced, and an admin's fix never is - Wikipedia's reading is shown
beside it on /admin, to take or leave. New names are checked as they are
learned, in the background (`queue`), and the whole list on the admin's
asking (`check_all`). **Never on an episode's path**: nothing here runs while
somebody waits, and a name is spoken from whatever the lexicon held.

Every request goes through `wikimedia._send` - counted, identified, paced to
three at once and backing off on a 429 - and only while the reference look is
on (`WIKIPEDIA=1`; staging and the tests keep it off). A name's answer, "not
on Wikipedia" included, is kept (`voice_bank.pronunciation_checks`) and not
asked again for `RECHECK_DAYS`.

**Unverified against the real services** for the same reason as
`wikimedia.py`: the build container cannot reach en.wikipedia.org. The
template shapes are read from Wikipedia's documentation and pinned in
`tests/test_admin_page_289.py`.
"""
from __future__ import annotations

import difflib
import logging
import re
import threading
import time
from typing import Iterable, Optional

log = logging.getLogger("wiki_pronounce")

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
#: Wikidata's "IPA transcription".
P_IPA = "P898"
#: How long a name's answer stands before it is asked again.
RECHECK_DAYS = 30
#: Most names one `check_all` asks about, so a long list is spread over runs.
MAX_PER_RUN = 400

#: What a check found. `agrees`/`differs` compare with the lexicon's entry.
FOUND, AGREES, DIFFERS, NONE, AMBIGUOUS, FAILED = (
    "found", "agrees", "differs", "not on Wikipedia", "ambiguous", "failed")


# --------------------------------------------------------------------------
# IPA -> respelling (Wikipedia's own pronunciation respelling key)
# --------------------------------------------------------------------------
#: Longest first, so "aɪər" is read before "aɪ". Vowels carry True.
_PHONES = (
    # r-coloured vowels
    ("aɪər", "eye-ur", True), ("aʊər", "ow-ur", True), ("ɪər", "eer", True),
    ("ɛər", "air", True), ("ʊər", "oor", True), ("ɜːr", "ur", True),
    ("ɑːr", "ar", True), ("ɔːr", "or", True), ("ɔər", "or", True),
    ("ɜr", "ur", True), ("ər", "ər", True), ("ɝ", "ur", True), ("ɚ", "ər", True),
    # diphthongs
    ("eɪ", "ay", True), ("aɪ", "eye", True), ("ɔɪ", "oy", True),
    ("oʊ", "oh", True), ("əʊ", "oh", True), ("aʊ", "ow", True),
    ("ju:", "yoo", True), ("juː", "yoo", True),
    # long vowels
    ("iː", "ee", True), ("uː", "oo", True), ("ɑː", "ah", True),
    ("ɔː", "aw", True), ("ɜː", "ur", True), ("eː", "ay", True),
    ("oː", "oh", True), ("aː", "ah", True), ("ɛː", "eh", True),
    ("yː", "ew", True), ("øː", "ur", True),
    # short vowels
    ("æ", "a", True), ("ɑ", "ah", True), ("ɒ", "o", True), ("ɔ", "aw", True),
    ("ɛ", "e", True), ("e", "eh", True), ("ɪ", "i", True), ("i", "ee", True),
    ("ʊ", "uu", True), ("u", "oo", True), ("ʌ", "u", True), ("ə", "ə", True),
    ("ɐ", "uh", True), ("a", "ah", True), ("o", "oh", True), ("y", "ew", True),
    ("ø", "ur", True), ("œ", "ur", True), ("ɨ", "i", True), ("ɯ", "oo", True),
    ("ɘ", "ə", True), ("ɵ", "ur", True), ("ɤ", "uh", True),
    # consonants
    ("tʃ", "ch", False), ("t͡ʃ", "ch", False), ("dʒ", "j", False),
    ("d͡ʒ", "j", False), ("ts", "ts", False), ("t͡s", "ts", False),
    ("hw", "wh", False), ("ʃ", "sh", False), ("ʒ", "zh", False),
    ("θ", "th", False), ("ð", "dh", False), ("ŋ", "ng", False),
    ("ɲ", "ny", False), ("ʎ", "ly", False), ("x", "kh", False),
    ("χ", "kh", False), ("ç", "h", False), ("ɡ", "g", False), ("g", "g", False),
    ("j", "y", False), ("ɹ", "r", False), ("ɾ", "r", False), ("r", "r", False),
    ("ʁ", "r", False), ("ʔ", "", False), ("ɫ", "l", False), ("ɬ", "hl", False),
    ("b", "b", False), ("d", "d", False), ("f", "f", False), ("h", "h", False),
    ("k", "k", False), ("l", "l", False), ("m", "m", False), ("n", "n", False),
    ("p", "p", False), ("s", "s", False), ("t", "t", False), ("v", "v", False),
    ("w", "w", False), ("z", "z", False), ("β", "b", False), ("ɣ", "g", False),
    ("c", "k", False), ("q", "k", False), ("ɦ", "h", False),
)
_STRESS, _SECONDARY = "ˈ", "ˌ"
#: Marks with nothing to say to a voice: length on its own, nasal and other
#: diacritics, tie bars, brackets, slashes.
_IGNORED = set("ːˑ̩̯̃͡ʰʲʷ̥̬̪()[]/‿|'’")


def _phones(ipa: str) -> list:
    """`[(respelt, is_vowel, stress)]` and word breaks as `None`."""
    text = (ipa or "").replace("'", _STRESS).replace("ɚ", "ər")
    out: list = []
    stress = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in (" ", "-", "_"):
            out.append(None)
            i += 1
            continue
        if ch == _STRESS:
            stress = 2
            out.append(("|", False, 0))
            i += 1
            continue
        if ch == _SECONDARY:
            stress = max(stress, 1)
            out.append(("|", False, 0))
            i += 1
            continue
        if ch == ".":
            out.append(("|", False, 0))
            i += 1
            continue
        for sound, said, vowel in _PHONES:
            if text.startswith(sound, i):
                out.append((said, vowel, stress if vowel else 0))
                if vowel:
                    stress = 0
                i += len(sound)
                break
        else:
            i += 1      # a mark this table does not voice (_IGNORED and others)
    return out


def _syllables(word: list) -> list:
    """`[(text, stressed)]` for one word: one vowel each, a single consonant
    before a vowel going with it, marked breaks kept."""
    sylls: list = []
    current: list = []
    pending: list = []         # consonants since the last vowel
    stressed = 0
    seen_vowel = False
    for said, vowel, stress in word:
        if said == "|":
            if seen_vowel:
                sylls.append(("".join(current + pending), stressed))
                current, pending, stressed, seen_vowel = [], [], 0, False
            continue
        if vowel:
            if seen_vowel:
                # Between two vowels: the last consonant opens the next one.
                carry = pending[-1:] if pending else []
                sylls.append(("".join(current + pending[:-1] if pending else current),
                              stressed))
                current, stressed = list(carry), 0
            else:
                current += pending
            pending = []
            current.append(said)
            stressed = max(stressed, stress)
            seen_vowel = True
        else:
            pending.append(said)
    tail = "".join(current + pending)
    if tail:
        if seen_vowel or not sylls:
            sylls.append((tail, stressed))
        else:
            text, s = sylls[-1]
            sylls[-1] = (text + tail, s)
    return [(t, s) for t, s in sylls if t]


def ipa_to_respelling(ipa: str) -> str:
    """IPA as the lexicon's respelling: hyphens between syllables, the
    stressed syllable in capitals - "æbˈduːlkəriːm" -> "ab-DOOL-kə-reem".
    "" when nothing in it can be voiced."""
    words: list = [[]]
    for phone in _phones(ipa):
        if phone is None:
            if words[-1]:
                words.append([])
        else:
            words[-1].append(phone)
    out = []
    for word in words:
        sylls = _syllables(word)
        if not sylls:
            continue
        out.append("-".join(t.upper() if s == 2 else t for t, s in sylls))
    said = " ".join(out).replace("ə", "uh").replace("Ə", "UH")
    return _plain(said)


def _plain(said: str) -> str:
    """Letters, hyphens and spaces only - what `pronunciation.clean` takes."""
    said = re.sub(r"[^A-Za-z'\- ]", "", said)
    said = re.sub(r"-{2,}", "-", said)
    return " ".join(w.strip("-") for w in said.split() if w.strip("-"))


def respell_template(params: list) -> str:
    """`{{respell|ab|DOOL|kə|REEM}}` -> "ab-DOOL-kuh-REEM". A "_" or a
    parameter with a space is a word break."""
    words: list = [[]]
    for raw in params:
        part = raw.strip()
        if not part or "=" in part:
            continue
        if part == "_":
            words.append([])
            continue
        pieces = part.split()
        for n, piece in enumerate(pieces):
            if n:
                words.append([])
            words[-1].append(piece)
    said = " ".join("-".join(w) for w in words if w)
    return _plain(said.replace("ə", "uh").replace("Ə", "UH").replace("ɪ", "i"))


# --------------------------------------------------------------------------
# Wikitext
# --------------------------------------------------------------------------
_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9 _-]*?)\s*\|([^{}]*)\}\}")
#: IPAc-en's labels and notes: words, not sounds.
_IPAC_LABELS = frozenset({"lang", "local", "pron", "US", "UK", "AU", "CA", "NZ",
                          "IE", "SA", "also", "or", "audio", "En-us", "en-us"})


#: What separates two readings in `{{IPAc-en}}`: only the first is taken,
#: or "KAR-lohs, KAR-los" would be said as one doubled name.
_ALTERNATIVE = frozenset({",", ",_", ", ", ";", "or", "also", "/"})


def _first_reading(params: list) -> list:
    """`{{IPAc-en}}`'s sounds up to the first alternative, labels skipped -
    a label before any sound ("US", "UK") starts the reading, one after
    them ends it."""
    out: list = []
    for raw in params:
        part = raw.strip()
        if not part or "=" in part:
            continue
        if part in _ALTERNATIVE or part in _IPAC_LABELS:
            if out:
                break
            continue
        out.append(part)
    return out


#: How far back from a pronunciation template its subject is looked for.
ANCHOR_CHARS = 300


def _unmarked(wikitext: str) -> str:
    """Wikitext as the words a reader sees: templates, references and
    comments dropped, links as their shown words, bold and italics bare."""
    text = re.sub(r"<!--.*?-->", " ", wikitext or "", flags=re.S)
    text = re.sub(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", " ", text, flags=re.S)
    while True:
        bare = re.sub(r"\{\{[^{}]*\}\}", " ", text)
        if bare == text:
            break
        text = bare
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)
    return text.replace("'''", "").replace("''", "")


def says_name(before: str, name: str) -> bool:
    """Whether the words just before a pronunciation template are `name`'s
    (10.27): a lead says several things ("Count Dooku, also known as Darth
    Tyranus (/tɪˈrænəs/)"), and a template belongs to the name it follows,
    not to the article. The phrase read is the last clause with words in it
    - a comma, semicolon or full stop starts a new one - and it must hold a
    word of the name."""
    import wikimedia

    clauses = [c for c in re.split(r"[,;.]", _unmarked(before))
               if re.search(r"[^\W\d_]", c)]
    if not clauses:
        return False
    said = set(wikimedia.normalise(clauses[-1]).split())
    words = wikimedia.normalise(name).split()
    wanted = [w for w in words if len(w) >= 3] or words
    return any(w in said for w in wanted)


def fits(name: str, say: str) -> bool:
    """Whether a reading says the whole name and only it: one word of
    respelling per word of the name (10.27). The lexicon swaps the whole
    name for the reading, so "A'ja Wilson" read as A'ja's "AY-zhuh" would
    drop "Wilson", and "Andoni Iraola" read as his full "Andoni Iraola
    Sagarna" would add a word nobody wrote."""
    return bool(say) and len(name.split()) == len(say.split())


def from_wikitext(wikitext: str, name: str = "") -> Optional[dict]:
    """How an article's lead says its subject: `{"say", "ipa", "how"}` from
    the first `{{respell}}`, else `{{IPAc-en}}`, else `{{IPA}}`/`{{IPA-xx}}`;
    None when it says nothing. Given the `name` asked about, only a template
    that follows that name (`says_name`) and reads all of it (`fits`) counts."""
    found = {}
    for match in _TEMPLATE.finditer(wikitext or ""):
        if name and not says_name(
                wikitext[max(0, match.start() - ANCHOR_CHARS):match.start()], name):
            continue
        kind = match.group(1).strip().lower().replace("_", " ")
        params = match.group(2).split("|")
        if kind == "respell" and "respell" not in found:
            say = respell_template(params)
            if say and (not name or fits(name, say)):
                found["respell"] = {"say": say, "ipa": "", "how": "Wikipedia's respelling"}
        elif kind in ("ipac-en", "ipac en") and "ipac" not in found:
            ipa = "".join(_first_reading(params))
            say = ipa_to_respelling(ipa)
            if say and (not name or fits(name, say)):
                found["ipac"] = {"say": say, "ipa": ipa, "how": "Wikipedia's English IPA"}
        elif (kind == "ipa" or kind.startswith("ipa-") or kind.startswith("ipa ")) \
                and "ipa" not in found:
            plain = [p.strip() for p in params if p.strip() and "=" not in p]
            # {{IPA|de|ˈʃmɪt}} names its language first; {{IPA-de|ˈʃmɪt}} not.
            if kind == "ipa" and len(plain) > 1 and re.fullmatch(r"[a-z-]{2,8}", plain[0]):
                lang, plain = plain[0], plain[1:]
            else:
                lang = kind[4:] if len(kind) > 4 else ""
            ipa = plain[0] if plain else ""
            say = ipa_to_respelling(ipa)
            if say and (not name or fits(name, say)):
                found["ipa"] = {"say": say, "ipa": ipa,
                                "how": "Wikipedia's IPA" + (f" ({lang})" if lang else "")}
    for key in ("respell", "ipac", "ipa"):
        if key in found:
            return found[key]
    return None


# --------------------------------------------------------------------------
# Asking Wikipedia
# --------------------------------------------------------------------------
def _lead(title: str) -> str:
    import wikimedia

    status, parsed = wikimedia._send("GET", WIKIPEDIA_API, params={
        "action": "parse", "page": title, "prop": "wikitext", "section": "0",
        "redirects": "1", "format": "json", "formatversion": "2"})
    if status != 200 or not isinstance(parsed, dict):
        raise wikimedia.WikimediaError(f"wikipedia answered HTTP {status}")
    return str(((parsed.get("parse") or {}).get("wikitext")) or "")


def lookup(name: str) -> dict:
    """What Wikipedia says about how `name` is said. Never raises:
    `{"name", "status", "say", "ipa", "how", "title", "url"}`."""
    import wikimedia
    from urllib.parse import quote

    out = {"name": name, "status": NONE, "say": "", "ipa": "", "how": "",
           "title": "", "url": ""}
    try:
        found = wikimedia._wikidata({"action": "wbsearchentities", "search": name,
                                     "language": "en", "type": "item", "limit": "5"})
        qid, ambiguous = wikimedia.pick(found.get("search") or [], name)
        if not qid:
            out["status"] = AMBIGUOUS if ambiguous else NONE
            return out
        data = wikimedia._wikidata({"action": "wbgetentities", "ids": qid,
                                    "props": "claims|sitelinks", "sitefilter": "enwiki"})
        entity = (data.get("entities") or {}).get(qid) or {}
        title = ((entity.get("sitelinks") or {}).get("enwiki") or {}).get("title", "")
        said = None
        if title:
            out["title"] = title
            out["url"] = wikimedia.ARTICLE_URL + quote(title.replace(" ", "_"))
            try:
                said = from_wikitext(_lead(title), name)
            except wikimedia.BackingOff:
                raise
            except wikimedia.WikimediaError as exc:
                # An article that cannot be read is not the end of it:
                # Wikidata's own IPA is asked next.
                log.info("wiki_pronounce: %s's lead could not be read (%s)", title, exc)
        if said is None:
            for claim in (entity.get("claims") or {}).get(P_IPA) or []:
                value = (((claim.get("mainsnak") or {}).get("datavalue") or {})
                         .get("value"))
                say = ipa_to_respelling(str(value or ""))
                if fits(name, say):
                    said = {"say": say, "ipa": str(value), "how": "Wikidata's IPA"}
                    break
        if said:
            out.update(said)
            out["status"] = FOUND
        return out
    except Exception as exc:  # noqa: BLE001 - a check is only ever advice
        log.info("wiki_pronounce: %r could not be checked (%s)", name, exc)
        out["status"] = FAILED
        return out


def _letters(say: str) -> str:
    return re.sub(r"[^a-z]", "", (say or "").lower().replace("uh", "ə"))


def agrees(ours: str, theirs: str) -> bool:
    """Whether two respellings are the same pronunciation, near enough -
    spelling a vowel "ah" or "a" is not a disagreement worth a change."""
    a, b = _letters(ours), _letters(theirs)
    if not a or not b:
        return False
    # A stress in a different place is a different pronunciation, however
    # alike the letters (review: "KAR-los" against "kar-LOS").
    sa, sb = _stressed(ours), _stressed(theirs)
    if sa is not None and sb is not None and sa != sb:
        return False
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


#: How alike Wikipedia's reading must be to the name's spelling, or to the
#: guess it would replace, to replace it unasked (10.27). Irish and other
#: spellings far from their sounds ("Caoimhe" = "KEE-vuh") fall under it
#: against the spelling, but a model's guess at them is near Wikipedia's;
#: an article's other name ("Dooku" read as "tih-RA-nuhs") is near neither.
RESEMBLES = 0.25


def resembles(name: str, say: str, guess: str = "") -> bool:
    """Whether a reading is plausibly of this name: near its spelling or
    near the guess it replaces. One that is near neither is only shown on
    /admin, beside the entry, to take or leave."""
    theirs = _letters(say)
    if not theirs:
        return False
    return any(difflib.SequenceMatcher(None, mine, theirs).ratio() >= RESEMBLES
               for mine in (re.sub(r"[^a-z]", "", name.lower()), _letters(guess))
               if mine)


def _stressed(say: str):
    """Which syllable is in capitals, counting across words, or None."""
    sylls = [s for s in re.split(r"[-\s]+", say or "") if s]
    marked = [i for i, s in enumerate(sylls) if len(s) > 1 and s.isupper()
              or (len(s) == 1 and s.isupper() and len(sylls) > 1)]
    return marked[0] if marked else None


def check(name: str, apply: bool = True) -> dict:
    """Look one name up, compare it with the lexicon, keep the answer, and -
    when the lexicon's entry is a model's guess Wikipedia disagrees with -
    replace it with Wikipedia's (`apply`). An admin's entry is never touched."""
    import pronunciation
    import voice_bank

    result = lookup(name)
    bank = voice_bank.bank()
    entry = next((r for r in bank.pronunciations() if r["name"] == name), None)
    if result["status"] == FOUND and entry:
        result["status"] = AGREES if agrees(entry["say"], result["say"]) else DIFFERS
        if result["status"] == DIFFERS and apply \
                and entry["source"] in ("writer", "brief", "wikipedia") \
                and resembles(name, result["say"], entry["say"]):
            if pronunciation.lexicon().learn([(name, result["say"])], "wikipedia"):
                result["applied"] = True
                result["status"] = AGREES
    elif apply and entry and entry["source"] == "wikipedia" \
            and result["status"] in (NONE, AMBIGUOUS):
        # Wikipedia no longer stands behind a reading it gave before the
        # guards (10.27): it goes, and the next model to meet the name
        # says it again.
        if pronunciation.lexicon().remove(name):
            result["withdrawn"] = True
    if result["status"] == FAILED:
        # Not kept (review): a failure - a 429's back-off above all - says
        # nothing about the name, and a kept one would skip it for a month.
        return result
    try:
        bank.keep_pronunciation_check(result)
    except Exception:  # noqa: BLE001
        log.warning("wiki_pronounce: the answer for %r could not be kept", name)
    return result


# --------------------------------------------------------------------------
# In the background
# --------------------------------------------------------------------------
_QUEUE: list = []
_LOCK = threading.Lock()
_RUNNING = [False]
#: What the admin page says about the run in progress or the last one.
STATE = {"running": False, "done": 0, "total": 0, "applied": 0,
         "started": 0.0, "finished": 0.0, "last_done": 0, "last_applied": 0}


def enabled() -> bool:
    try:
        import wikimedia
        return wikimedia.enabled()
    except Exception:  # noqa: BLE001
        return False


def _due(names: Iterable[str], force: bool = False) -> list:
    import voice_bank

    try:
        checked = {r["name"]: r["at"] for r in voice_bank.bank().pronunciation_checks()}
    except Exception:  # noqa: BLE001
        checked = {}
    cutoff = time.time() - RECHECK_DAYS * 86400
    out = [n for n in dict.fromkeys(n for n in names if n)
           if force or checked.get(n, 0) < cutoff]
    # Never asked first, then the longest since (10.27): a forced run over a
    # list longer than `MAX_PER_RUN` goes on where the last one stopped.
    return sorted(out, key=lambda n: checked.get(n, 0))


def queue(names: Iterable[str], force: bool = False) -> int:
    """Check these names in the background; returns how many were queued.
    Names checked in the last `RECHECK_DAYS` are skipped unless `force`.
    Never raises, never waits."""
    if not enabled():
        return 0
    try:
        wanted = _due(names, force)
        with _LOCK:
            fresh = [n for n in wanted if n not in _QUEUE][:MAX_PER_RUN]
            _QUEUE.extend(fresh)
            STATE["total"] += len(fresh)
            start = bool(_QUEUE) and not _RUNNING[0]
            if start:
                _RUNNING[0] = True
                STATE.update(running=True, started=time.time())
        if start:
            threading.Thread(target=_drain, daemon=True,
                             name="wiki-pronounce").start()
        return len(fresh)
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_pronounce: could not queue checks: %s", exc)
        return 0


def check_all(force: bool = False) -> int:
    """Queue every name in the lexicon (the admin's button)."""
    import voice_bank

    try:
        rows = voice_bank.bank().pronunciations()
    except Exception:  # noqa: BLE001
        return 0
    prune(rows)
    return queue([r["name"] for r in rows], force=force)


def prune(rows: Optional[list] = None) -> list:
    """Withdraw Wikipedia's readings that do not say the whole name (10.27,
    `fits`) - kept before that guard existed. No request is made; returns
    the names withdrawn. Never raises."""
    import pronunciation
    import voice_bank

    gone = []
    try:
        rows = voice_bank.bank().pronunciations() if rows is None else rows
        for row in rows:
            if row["source"] == "wikipedia" and not fits(row["name"], row["say"]):
                if pronunciation.lexicon().remove(row["name"]):
                    gone.append(row["name"])
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_pronounce: could not prune: %s", exc)
    if gone:
        log.info("wiki_pronounce: withdrew %d reading(s) that did not fit "
                 "their names: %s", len(gone), ", ".join(gone))
    return gone


def _finish() -> None:
    """Called holding `_LOCK`: the run is over."""
    _RUNNING[0] = False
    STATE.update(running=False, finished=time.time(),
                 last_done=STATE["done"], last_applied=STATE["applied"],
                 done=0, total=0, applied=0)


def _drain() -> None:
    finished = False
    try:
        while True:
            with _LOCK:
                if not _QUEUE:
                    # Over in the same locked step that found the queue
                    # empty (review): a `queue` between the two would see a
                    # worker still running and leave its names stranded.
                    _finish()
                    finished = True
                    return
                name = _QUEUE.pop(0)
            result = check(name)
            with _LOCK:
                STATE["done"] += 1
                if result.get("applied"):
                    STATE["applied"] += 1
    finally:
        if not finished:
            with _LOCK:
                _QUEUE.clear()
                _finish()
