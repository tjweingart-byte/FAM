"""Words the voice may not know, found in what was written (PROBLEMS.md §325).

The owner (10.27): "we want a way to check the pronunciation on these that
can apply to all words within, not only ones that wikipedia can cross-check."

The lexicon (`pronunciation.py`) holds only names a model chose to list, and
Wikipedia only answers for names with an article. Neither looks at the rest
of a script. This does: every word in recently written episodes is looked up
in the **CMU Pronouncing Dictionary** (134,000 English words and common
names, `pronounce_data/cmudict.dict.gz`, BSD-licensed - the data file, not
the GPL `cmudict` package that wraps it). A word the dictionary has is one an
English voice has very likely met; a word it lacks, that the lexicon does not
respell either, is a word the voice will guess at. Those are listed on /admin
by how often episodes use them, to hear (`voice_check.py`) and respell.

The dictionary also gives the round trip what a common word *should* sound
like (`expected`), so the voice can be checked on words nobody respelled.

**Never on an episode's path.** A scan reads the cache in the background, on
an admin's asking; it never touches a script while somebody waits. Private
searches are not listed by `recent` and attached episodes are never cached,
so what is somebody's own is never read.
"""
from __future__ import annotations

import gzip
import logging
import os
import re
import threading
import time
from collections import Counter
from typing import Iterable, Optional

log = logging.getLogger("hard_words")

DICTIONARY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "pronounce_data", "cmudict.dict.gz")
#: How many recent episodes one scan reads.
SCAN_EPISODES = 600
#: How many words a scan keeps, most used first.
KEEP = 300

_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*(?:-[A-Za-z][A-Za-z'’]*)*")
_LOCK = threading.Lock()
_DICT: dict = {}

# ARPAbet (the dictionary's alphabet) as IPA, for `wiki_pronounce`'s
# IPA-to-respelling table. AH unstressed is the schwa; ER unstressed is ɚ.
_ARPABET = {
    "AA": "ɑ", "AE": "æ", "AH": "ʌ", "AO": "ɔ", "AW": "aʊ", "AY": "aɪ",
    "B": "b", "CH": "tʃ", "D": "d", "DH": "ð", "EH": "ɛ", "ER": "ɝ",
    "EY": "eɪ", "F": "f", "G": "ɡ", "HH": "h", "IH": "ɪ", "IY": "iː",
    "JH": "dʒ", "K": "k", "L": "l", "M": "m", "N": "n", "NG": "ŋ",
    "OW": "oʊ", "OY": "ɔɪ", "P": "p", "R": "ɹ", "S": "s", "SH": "ʃ",
    "T": "t", "TH": "θ", "UH": "ʊ", "UW": "uː", "V": "v", "W": "w",
    "Y": "j", "Z": "z", "ZH": "ʒ",
}


def _load() -> dict:
    """word (lower case) -> its first pronunciation, in ARPAbet. Loaded once,
    on first use (~22 MB); {} when the file is missing, said once."""
    with _LOCK:
        if _DICT:
            return _DICT
        try:
            with gzip.open(DICTIONARY, "rt", encoding="utf-8") as fh:
                for line in fh:
                    line = line.split("#", 1)[0].strip()
                    if not line:
                        continue
                    word, _, phones = line.partition(" ")
                    if "(" in word:          # "read(2)": a second reading
                        continue
                    _DICT.setdefault(word.lower(), phones.strip())
        except OSError as exc:
            log.warning("hard_words: the pronouncing dictionary could not be "
                        "read (%s); every word will look unknown", exc)
        return _DICT


def available() -> bool:
    """Whether the dictionary is on this server - asked without loading it,
    so the admin page does not pay ~22 MB to say so."""
    return bool(_DICT) or os.path.exists(DICTIONARY)


def arpabet_to_ipa(phones: str) -> str:
    """"K AH0 R IY1 M" -> "kəˈɹiːm": the stress mark before the stressed
    syllable's onset consonant, so the respelling capitalises the syllable."""
    out: list = []
    for raw in phones.split():
        base, stress = raw.rstrip("012"), raw[len(raw.rstrip("012")):]
        ipa = _ARPABET.get(base, "")
        if base == "AH" and stress == "0":
            ipa = "ə"
        elif base == "ER" and stress == "0":
            ipa = "ɚ"
        if stress == "1":
            # One consonant before the vowel opens its syllable.
            at = len(out) - 1 if out and not _vowelish(out[-1]) else len(out)
            out.insert(at, "ˈ")
        out.append(ipa)
    return "".join(out)


def _vowelish(ipa: str) -> bool:
    return bool(ipa) and ipa[0] in "ɑæʌɔaɛɝeɪioʊuəɚ"


def expected(word: str) -> str:
    """How the dictionary says `word`, as the lexicon's respelling, or ""."""
    phones = _load().get((word or "").lower().replace("’", "'"))
    if not phones:
        return ""
    import wiki_pronounce

    return wiki_pronounce.ipa_to_respelling(arpabet_to_ipa(phones))


_SUFFIXES = ("'s", "s'", "s", "es", "ed", "d", "ing", "er", "ers", "ly", "est")


def known(word: str) -> bool:
    """Whether the dictionary has `word`, or it is a plain form of one it has
    ("Eagles'", "kickoffs", "re-signed") - each part of a hyphenated word."""
    d = _load()
    w = (word or "").lower().replace("’", "'").strip("'")
    if not w:
        return True
    if w in d:
        return True
    if "-" in w:
        return all(known(part) for part in w.split("-") if part)
    for suffix in _SUFFIXES:
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            stem = w[: -len(suffix)]
            if stem in d or (suffix in ("ed", "ing", "er", "est") and stem + "e" in d):
                return True
            # "running" -> "run": a doubled final consonant.
            if suffix in ("ed", "ing", "er") and len(stem) > 3 and stem[-1] == stem[-2] \
                    and stem[:-1] in d:
                return True
    return False


def at_risk(text: str, respelled: Iterable[str] = ()) -> list:
    """The words in `text` the voice may not know: not in the dictionary,
    not respelled by the lexicon (`respelled`, any word of a lexicon name),
    not an initialism (spoken as letters) and longer than two letters."""
    covered = {w.lower() for name in respelled for w in re.split(r"[\s\-]+", name) if w}
    out = []
    for match in _WORD.finditer(text or ""):
        word = match.group(0).replace("’", "'")
        bare = word[:-2] if word.lower().endswith("'s") else word.rstrip("'")
        if len(bare) < 3 or (bare.isupper() and len(bare) <= 6):
            continue
        if bare.lower() in covered or all(p.lower() in covered for p in bare.split("-")):
            continue
        if not known(bare):
            out.append(bare)
    return out


def _sentence_with(word: str, sentences: list) -> str:
    pattern = re.compile(r"(?<![A-Za-z])" + re.escape(word) + r"(?![A-Za-z])")
    for s in sentences:
        if pattern.search(s):
            return s.strip()[:240]
    return ""


def scan(cache, limit: int = SCAN_EPISODES) -> dict:
    """Read the most recent `limit` episodes in `cache`, keep the `KEEP`
    words most used that the voice may not know, and say what was read.
    Replaces the last scan. Never raises."""
    import pronunciation
    import voice_bank

    started = time.time()
    try:
        respelled = list(pronunciation.lexicon().entries())
    except Exception:  # noqa: BLE001
        respelled = []
    counts: Counter = Counter()
    episodes: Counter = Counter()
    example: dict = {}
    read = 0
    try:
        rows = cache.recent(limit=limit)
    except Exception as exc:  # noqa: BLE001
        log.warning("hard_words: the cache could not be listed: %s", exc)
        return {"episodes": 0, "words": 0, "error": str(exc)}
    for row in rows:
        try:
            sentences = cache.get(row["key"], current=False) or []
        except Exception:  # noqa: BLE001
            continue
        read += 1
        text = pronunciation.strip_markers(" ".join(sentences))
        found = at_risk(text, respelled)
        counts.update(found)
        for w in set(found):
            episodes[w] += 1
            if w not in example:
                example[w] = (_sentence_with(w, sentences), row.get("title") or row.get("query") or "")
    try:
        bank = voice_bank.bank()
        ok = bank.hard_words_ok()
        kept = [{"word": w, "count": n, "episodes": episodes[w],
                 "example": example[w][0], "title": example[w][1]}
                for w, n in counts.most_common() if w not in ok][:KEEP]
        bank.replace_hard_words(kept)
    except Exception as exc:  # noqa: BLE001
        log.warning("hard_words: the scan could not be kept: %s", exc)
        return {"episodes": read, "words": 0, "error": str(exc)}
    log.info("hard_words: %d episodes read, %d words the voice may not know "
             "(%.1fs)", read, len(kept), time.time() - started)
    return {"episodes": read, "words": len(kept), "at": time.time()}


STATE = {"running": False, "episodes": 0, "words": 0, "at": 0.0, "error": ""}


def scan_in_background(cache, limit: Optional[int] = None) -> bool:
    """Start a scan unless one is running. Never waits."""
    with _LOCK:
        if STATE["running"]:
            return False
        STATE["running"] = True

    def run() -> None:
        result = {}
        try:
            result = scan(cache, limit or SCAN_EPISODES)
        finally:
            STATE.update(running=False, episodes=result.get("episodes", 0),
                         words=result.get("words", 0), at=time.time(),
                         error=result.get("error", ""))

    threading.Thread(target=run, daemon=True, name="hard-words").start()
    return True
