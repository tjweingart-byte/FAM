"""The pronunciation list, out to a reviewer and back as proposals (PROBLEMS.md §548).

Wikipedia checks each name a model adds (§289), but most of the names on
file are sports players, and for most of them Wikipedia has nothing to say
(no pronunciation in the article's first lines), so the model's guess stands. This is the
round trip the owner asked for: download every respelling with what
Wikipedia said and a sentence the name was heard in, have another reader
(a Claude chat, the leagues' media pronunciation guides) correct it with a
fixed prompt, and upload what came back.

**An upload changes nothing.** It is read into proposals - each one beside
the respelling it would replace - and an admin accepts them one at a time or
the high-confidence ones together on /admin. An accepted one is saved as an
admin's (`pronunciation.Lexicon.set`), which no model and no Wikipedia
check replaces (`[op-voice]`), so a wrong guess accepted blind would never
be corrected. That is why accepting is a person's act.

`stress_variants` is the other half: `pronunciation.voiced` lowercases a
respelling before the voice gets it (the voice would spell "TEH" out as letters), so the
stress the lexicon records never reaches Chatterbox. The variants are ways
of writing the same respelling that might carry stress anyway, for
`tools/stress_trial.py` and /admin's "Hear 4 ways" to compare by ear.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Iterable, Optional

import pronunciation

#: The columns of a download, in order. The reviewer returns `name`, `say`,
#: `confidence` and `reason` (see PROMPT); the rest are context.
EXPORT_COLUMNS = ("name", "current_say", "source", "wikipedia_say",
                  "wikipedia_ipa", "wikipedia_status", "context", "episode")

#: How many recent episodes a download reads for context sentences.
CONTEXT_EPISODES = 300

#: Upload headers that mean the corrected respelling, best first. A reviewer
#: that edits the downloaded file in place leaves it in `current_say`.
_SAY_HEADERS = ("say", "new_say", "suggested_say", "respelling", "current_say")
_REASON_HEADERS = ("reason", "why", "notes", "source_used")
CONFIDENCES = ("high", "medium", "low")
MAX_UPLOAD_ROWS = 5000

PROMPT = """You are checking how a text-to-speech voice says names in a news-audio app.
The voice reads English spelling only, so each hard name is respelled.
Attached is a CSV of every respelling on file. Columns:
  name            the name as written
  current_say     how the voice is told to say it now
  source          who wrote current_say: admin (a person), wikipedia, writer or brief (two AI models)
  wikipedia_say / wikipedia_ipa / wikipedia_status   what Wikipedia said, if anything
  context         a sentence the name appeared in, to tell you WHO this is
  episode         the episode that sentence came from

For each row, work out how THIS person or place actually says their own name
(American English broadcast pronunciation for US sports and public figures; the
native pronunciation, anglicised the way US broadcasters say it, otherwise).
Best evidence first: the person saying it themselves, a league or team media
pronunciation guide (NFL, NBA, NHL, MLB, NCAA, Premier League, ATP/WTA),
Wikipedia's IPA, then broadcast usage. Do not guess from spelling alone.

Respelling rules - follow them exactly or the row is refused:
  - letters, hyphens and spaces only; no IPA, digits, accents or apostrophes
  - syllables joined by hyphens; one space between words
  - the SAME number of words as the name (Jaron Sagapolutele = JAIR-in sah-gah-poh-loo-TEH-leh)
  - the stressed syllable of each word in CAPITALS, everything else lower case
  - sounds: a (cat), ah (father), ay (day), eh (bed), ee (see), ih (sit), eye (my),
    oh (go), oo (food), uu (book), ow (now), oy (boy), uh (cup), ur (fur), aw (law);
    g is always hard, j as in jam, zh as in vision, kh as in loch, th as in thin, dh as in this
  - a name the voice already says right needs no row

Return ONLY a CSV, in a code block, with exactly these columns:
  name,say,confidence,reason
  - only rows you would CHANGE (or add, for a name you know is missing), name spelled exactly as given
  - confidence: high (you have the person's own or an official pronunciation),
    medium (good secondary evidence), low (an educated guess)
  - reason: one short phrase naming the evidence, e.g. "NFL pronunciation guide"
If current_say is already right, leave the row out. If you cannot tell who the
name refers to, leave it out rather than guess.
"""


# --------------------------------------------------------------------------
# Out
# --------------------------------------------------------------------------

def _contexts(cache, names: Iterable[str], limit: int = CONTEXT_EPISODES) -> dict:
    """name -> (sentence, episode title) for each name met in the `limit`
    most recent episodes. Never raises: context is a help, not the list."""
    names = sorted({n for n in names if n}, key=len, reverse=True)
    found: dict = {}
    if cache is None or not names:
        return found
    pattern = re.compile(r"(?<![A-Za-zÀ-ÿĀ-ſ'’])(" + "|".join(
        re.escape(n) for n in names) + r")(?![A-Za-zÀ-ÿĀ-ſ])")
    try:
        rows = cache.recent(limit=limit)
    except Exception:  # noqa: BLE001
        return found
    for row in rows:
        if len(found) == len(names):
            break
        try:
            sentences = cache.get(row["key"], current=False) or []
        except Exception:  # noqa: BLE001
            continue
        title = row.get("title") or row.get("query") or ""
        for sentence in sentences:
            text = pronunciation.strip_markers(sentence)
            for m in pattern.finditer(text):
                if m.group(1) not in found:
                    found[m.group(1)] = (text.strip()[:240], title[:120])
    return found


def export_rows(bank, cache=None) -> list[dict]:
    """Every respelling on file, as download rows (`EXPORT_COLUMNS`)."""
    rows = bank.pronunciations()
    try:
        checks = {c["name"]: c for c in bank.pronunciation_checks()}
    except Exception:  # noqa: BLE001
        checks = {}
    contexts = _contexts(cache, [r["name"] for r in rows])
    out = []
    for r in rows:
        w = checks.get(r["name"]) or {}
        sentence, episode = contexts.get(r["name"], ("", ""))
        out.append({"name": r["name"], "current_say": r["say"],
                    "source": r["source"], "wikipedia_say": w.get("say") or "",
                    "wikipedia_ipa": w.get("ipa") or "",
                    "wikipedia_status": w.get("status") or "",
                    "context": sentence, "episode": episode})
    return out


#: Columns that hold text from outside (episodes, Wikipedia), not a name or a
#: respelling - a cell there that opens like a formula is defused.
_FREE_TEXT = ("context", "episode", "wikipedia_ipa", "wikipedia_status")


def _defused(value: str) -> str:
    """A spreadsheet runs a cell opening with = + - @ as a formula; a leading
    apostrophe makes it text. Names and respellings open with a letter
    (`pronunciation.clean`), so only free text needs it."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def export_csv(bank, cache=None) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in export_rows(bank, cache):
        writer.writerow({k: _defused(v) if k in _FREE_TEXT else v for k, v in row.items()})
    return buf.getvalue()


# --------------------------------------------------------------------------
# Back
# --------------------------------------------------------------------------

def _unwrapped(text: str) -> str:
    """The CSV inside whatever a chat wrapped it in: a ``` fence, a line of
    prose before the header."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    fence = re.search(r"```[a-zA-Z]*\n(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    lines = text.split("\n")
    say = "|".join(_SAY_HEADERS).replace("_", "[ _]?")
    for i, line in enumerate(lines):
        if re.match(r"\s*\"?name\"?\s*[,\t;]", line, re.I) and \
                re.search(r"[,\t;]\s*\"?(" + say + r")\"?\s*([,\t;]|$)", line, re.I):
            return "\n".join(lines[i:])
    return text


def _pick(row: dict, headers: Iterable[str]) -> str:
    for h in headers:
        if row.get(h):
            return str(row[h]).strip()
    return ""


def read_upload(text: str, entries: dict) -> dict:
    """What an uploaded CSV proposes, against `entries` (name -> (say,
    source), the lexicon's stored rows). Changes nothing.

    Returns `{"proposals": [...], "unchanged": n, "refused": [...]}`. A
    proposal is `{name, say, current, current_source, confidence, reason,
    new}`; a refusal is `{line, name, why}` in words an admin can act on.
    """
    body = _unwrapped(text)
    if not body.strip():
        raise ValueError("The file is empty.")
    try:
        dialect = csv.Sniffer().sniff(body.split("\n", 1)[0], delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(body), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("The file has no header row.")
    reader.fieldnames = [re.sub(r"[^a-z_]", "", (h or "").strip().lower().replace(" ", "_"))
                         for h in reader.fieldnames]
    if "name" not in reader.fieldnames or not any(h in reader.fieldnames for h in _SAY_HEADERS):
        raise ValueError(
            "The file needs a name column and a say column (name,say,confidence,reason) - "
            f"it has {', '.join(h for h in reader.fieldnames if h) or 'none'}.")
    proposals: dict = {}
    refused: list = []
    unchanged = 0
    for n, row in enumerate(reader, start=1):
        i = reader.line_num
        if n > MAX_UPLOAD_ROWS:
            refused.append({"line": i, "name": "",
                            "why": f"Only the first {MAX_UPLOAD_ROWS} rows are read."})
            break
        name = str(row.get("name") or "").strip()
        say = _pick(row, _SAY_HEADERS)
        if not name and not say:
            continue
        pair = pronunciation.clean(name, say)
        if not pair:
            refused.append({"line": i, "name": name[:60], "why": (
                "not a respelling the voice can use (letters, hyphens and "
                "spaces only, a name with a capital)" if say else "no respelling given")})
            continue
        name, say = pair
        current = entries.get(name)
        # Exactly, case and all: a change of stress ("TEH" to "POH") is a
        # change, and the commonest one a reviewer makes.
        if current and current[0] == say:
            unchanged += 1
            continue
        if len(say.split()) != len(name.split()):
            # Allowed - `_split` just keeps the whole name only - but said,
            # because the surname alone is then not respelled.
            note = "a different number of words from the name, so the surname alone will not be respelled"
        else:
            note = ""
        confidence = str(row.get("confidence") or "").strip().lower()
        proposals[name] = {
            "name": name, "say": say,
            "current": current[0] if current else "",
            "current_source": current[1] if current else "",
            "confidence": confidence if confidence in CONFIDENCES else "",
            "reason": _pick(row, _REASON_HEADERS)[:200],
            "new": current is None, "note": note}
    order = {c: i for i, c in enumerate(CONFIDENCES)}
    listed = sorted(proposals.values(),
                    key=lambda p: (order.get(p["confidence"], 3), p["name"].lower()))
    return {"proposals": listed, "unchanged": unchanged, "refused": refused}


# --------------------------------------------------------------------------
# Stress: four ways to hand the voice the same respelling
# --------------------------------------------------------------------------

_ACUTE = {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú", "y": "ý"}


def _stressed(syllable: str) -> bool:
    letters = [c for c in syllable if c.isalpha()]
    return len(letters) >= 1 and all(c.isupper() for c in letters) and \
        (len(letters) > 1 or len(syllable) == 1)


def _accented(syllable: str) -> str:
    """The syllable with an acute on its first vowel - a y only when it has
    no other ("YAH" is yáh, not ýah)."""
    vowel = "[aeiou]" if re.search("[aeiou]", syllable) else "y"
    return re.sub(vowel, lambda m: _ACUTE[m.group(0)], syllable, count=1)


def has_stress(say: str) -> bool:
    """Whether the respelling marks a stressed syllable in capitals."""
    return any(_stressed(s) for w in (say or "").split() for s in w.split("-"))


def stress_variants(say: str) -> dict:
    """Four spellings of one respelling, by name:

    * `now`: what the voice is handed today (`pronunciation.voiced`) - no stress
    * `joined`: the syllables run together, no hyphens - one word, no stress
    * `accent`: lower case with an acute accent on the stressed vowel
    * `spaced`: every syllable its own word, the stressed one capitalised
    """
    words = (say or "").split()
    joined, accent, spaced = [], [], []
    for word in words:
        sylls = [s for s in word.split("-") if s]
        joined.append("".join(sylls).lower().capitalize())
        acc, spc = [], []
        for s in sylls:
            low = s.lower()
            if _stressed(s):
                acc.append(_accented(low))
                spc.append(low.capitalize())
            else:
                acc.append(low)
                spc.append(low)
        accent.append("-".join(acc))
        spaced.append(" ".join(spc))
    return {"now": pronunciation.voiced(say), "joined": " ".join(joined),
            "accent": " ".join(accent), "spaced": " ".join(spaced)}


#: Said around the name, so it is heard in a sentence the way an episode says it.
CARRIER = "Tonight's story starts with {}, and it ends there too."


async def hear_variants(name: str, say: Optional[str] = None,
                        sentence: bool = False) -> list[dict]:
    """Say each of `stress_variants` with the production voice and hear it
    back (`voice_check.say_and_hear`). Keeps nothing - not in the lexicon,
    not in the voice checks. Raises `voice_check.VoiceCheckUnavailable`."""
    import base64

    import voice_check
    import wiki_pronounce
    from spoken_text import speakable

    if say is None:
        entry = pronunciation.lexicon().entries().get(name)
        if not entry:
            raise ValueError(f"{name} has no respelling on file.")
        say = entry[0]
    out = []
    for label, text in stress_variants(say).items():
        sent = speakable(CARRIER.format(text) if sentence else text).strip()
        reply = await voice_check.say_and_hear(sent)
        heard = reply.get("heard") or ""
        heard_say = wiki_pronounce.ipa_to_respelling(heard.replace(" ", ""))
        out.append({"variant": label, "sent": sent, "heard": heard,
                    "heard_say": heard_say,
                    # In a sentence the recogniser hears the whole sentence,
                    # so only the bare name is scored.
                    "score": (voice_check.similarity(say, heard_say)
                              if heard_say and not sentence else None),
                    "wav": voice_check.wav(reply["pcm"], reply.get("rate") or 24000)})
    for row in out:
        row["audio"] = "data:audio/wav;base64," + base64.b64encode(row["wav"]).decode("ascii")
    return out
