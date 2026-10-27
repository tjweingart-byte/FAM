"""Say a word with the production voice, hear it back, and compare
(PROBLEMS.md §325).

Every other check here compares text with text. This one checks what a
listener would hear: the word goes through exactly what an episode's words go
through (`pronunciation.respell`, then `spoken_text.speakable`), Chatterbox
says it, and a phoneme recogniser on the same card writes down the sounds it
heard (`voice_worker/hearing.py`). Those sounds, turned into the lexicon's
respelling style (`wiki_pronounce.ipa_to_respelling`), are compared with what
was meant:

* a lexicon entry's respelling, when the word has one (an admin's, Wikipedia's
  or a model's);
* else the CMU dictionary's pronunciation (`hard_words.expected`);
* else nothing - the reading is shown, and only a person can judge it.

**The verdict is a hint, not a ruling.** The recogniser hears an American
English voice reading a respelling, with no stress marks and its own vowel
habits, so the comparison is by sound class (`sound_key`), and the thresholds
(`MATCH`, `UNSURE`) are a first guess until they have been checked against
the owner's ears - the audio is returned with every check for exactly that.
Nothing here changes the lexicon by itself.

Needs a voice that speaks: the remote worker (`RemoteChatterboxEngine.hear`)
or Chatterbox in this process. On the placeholder tone there is nothing to
hear, and the check says so.
"""
from __future__ import annotations

import asyncio
import base64
import difflib
import io
import logging
import re
import time
import wave
from typing import Iterable, Optional

log = logging.getLogger("voice_check")

#: At or above: the voice said what was meant, near enough.
MATCH = 0.7
#: At or above (and under `MATCH`): worth a listen.
UNSURE = 0.5
#: Most words one background run says - each costs a second or two of GPU.
MAX_PER_RUN = 150

MATCHES, UNSURE_, DIFFERS, NO_EXPECTATION = "matches", "unsure", "differs", "heard"


class VoiceCheckUnavailable(RuntimeError):
    """Nothing on this server can say and hear a word; the message says why."""


# --------------------------------------------------------------------------
# Comparing two respellings by sound
# --------------------------------------------------------------------------
#: Spellings of one sound, folded to one letter, longest first. Both sides
#: are respellings, but one is a person's or a model's and the other comes
#: from the recogniser through `ipa_to_respelling`, so "ah"/"a", "ee"/"i"
#: and "oo"/"u" must not count as differences.
_FOLD = (("eye", "Y"), ("igh", "Y"), ("ay", "E"), ("ey", "E"), ("ee", "I"),
         ("ea", "I"), ("ih", "i"), ("ah", "a"), ("aa", "a"), ("aw", "o"),
         ("oh", "O"), ("oa", "O"), ("ow", "W"), ("oo", "U"), ("uu", "U"),
         ("uh", "u"), ("eh", "e"), ("zh", "Z"), ("sh", "S"), ("ch", "C"),
         ("th", "T"), ("dh", "T"), ("ng", "N"), ("kh", "k"), ("ck", "k"),
         ("c", "k"), ("q", "k"), ("ph", "f"), ("ə", "u"))


def sound_key(say: str) -> str:
    """A respelling as a string of sound classes, for comparing two."""
    text = re.sub(r"[^a-zə]", "", (say or "").lower())
    for spelled, sound in _FOLD:
        text = text.replace(spelled, sound)
    # A doubled letter is one sound ("ll", "ss").
    return re.sub(r"(.)\1+", r"\1", text)


def similarity(meant: str, heard: str) -> Optional[float]:
    a, b = sound_key(meant), sound_key(heard)
    if not a or not b:
        return None
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 3)


def verdict(score: Optional[float]) -> str:
    if score is None:
        return NO_EXPECTATION
    return MATCHES if score >= MATCH else UNSURE_ if score >= UNSURE else DIFFERS


# --------------------------------------------------------------------------
# Saying and hearing
# --------------------------------------------------------------------------
def what_is_sent(word: str) -> str:
    """Exactly what the voice is handed for `word` in an episode."""
    import pronunciation
    from spoken_text import speakable

    return speakable(pronunciation.respell(word.strip())).strip()


def meant(word: str) -> tuple:
    """(respelling meant, where it came from) - the lexicon's entry, else the
    dictionary's, else ("", "")."""
    import hard_words
    import pronunciation

    entry = pronunciation.lexicon().entries().get(word)
    if entry:
        return entry[0], entry[1]
    said = hard_words.expected(word)
    return (said, "dictionary") if said else ("", "")


async def say_and_hear(text: str) -> dict:
    """`{"pcm", "rate", "heard", "heard_by"}` from whatever speaks here."""
    from tts import ChatterboxEngine, build_engine

    engine = build_engine()
    if hasattr(engine, "hear"):
        reply = await engine.hear(text)
        reply["rate"] = engine.sample_rate
        return reply
    if isinstance(engine, ChatterboxEngine):
        from voice_worker import hearing

        ready, detail = hearing.available()
        if not ready:
            raise VoiceCheckUnavailable(detail)
        pcm = await engine.synth(text, wpm=0.0)
        heard = await asyncio.to_thread(hearing.recognise, pcm, engine.sample_rate)
        return {"pcm": pcm, "rate": engine.sample_rate, "heard": heard,
                "heard_by": hearing.MODEL}
    raise VoiceCheckUnavailable(
        "No voice speaks on this server (the placeholder tone is playing), so "
        "there is nothing to hear. The round trip needs the Chatterbox worker.")


def wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm)
    return buf.getvalue()


async def check(word: str, keep: bool = True, with_audio: bool = False) -> dict:
    """Say `word`, hear it, compare it with what was meant. Raises
    `VoiceCheckUnavailable` (nothing speaks) or the voice's own error."""
    import wiki_pronounce

    word = word.strip()
    sent = what_is_sent(word)
    expected, source = meant(word)
    reply = await say_and_hear(sent)
    heard_ipa = reply.get("heard") or ""
    heard_say = wiki_pronounce.ipa_to_respelling(heard_ipa.replace(" ", ""))
    score = similarity(expected, heard_say) if expected else None
    result = {"word": word, "sent": sent, "expected": expected,
              "expected_from": source, "heard": heard_ipa, "heard_say": heard_say,
              "heard_by": reply.get("heard_by", ""), "score": score,
              "verdict": verdict(score) if heard_say else DIFFERS}
    if keep:
        try:
            import voice_bank

            voice_bank.bank().keep_voice_check(result)
        except Exception:  # noqa: BLE001
            log.warning("voice_check: the result for %r could not be kept", word)
    if with_audio:
        result["audio"] = "data:audio/wav;base64," + base64.b64encode(
            wav(reply["pcm"], reply.get("rate") or 24000)).decode("ascii")
    return result


# --------------------------------------------------------------------------
# In the background (the admin's "Hear them all")
# --------------------------------------------------------------------------
STATE = {"running": False, "done": 0, "total": 0, "differs": 0,
         "started": 0.0, "finished": 0.0, "error": ""}
#: The run in progress, held strongly - asyncio keeps tasks only weakly.
_TASKS: set = set()


def run_in_background(words: Iterable[str]) -> int:
    """Hear these words one at a time, as a task on the running event loop -
    the loop the voice's connection pool belongs to, so a thread with a loop
    of its own never shares it. Returns how many were queued; 0 when a run
    is already going. Stops at the first failure of the voice itself: a
    worker that is down fails every word. Call from inside the loop."""
    wanted = list(dict.fromkeys(w for w in words if w))[:MAX_PER_RUN]
    if STATE["running"] or not wanted:
        return 0
    STATE.update(running=True, done=0, total=len(wanted), differs=0,
                 started=time.time(), error="")

    async def run() -> None:
        try:
            for word in wanted:
                try:
                    result = await check(word)
                except VoiceCheckUnavailable as exc:
                    STATE["error"] = str(exc)
                    return
                except Exception as exc:  # noqa: BLE001 - the voice is down
                    STATE["error"] = f"{type(exc).__name__}: {exc}"[:300]
                    return
                STATE["done"] += 1
                if result["verdict"] in (DIFFERS, UNSURE_):
                    STATE["differs"] += 1
        finally:
            STATE.update(running=False, finished=time.time())

    task = asyncio.get_running_loop().create_task(run())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return len(wanted)
