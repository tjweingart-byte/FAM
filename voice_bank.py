"""The bank of voices: several cloned voices, one of them chosen per episode.

PROBLEMS.md §147. FAM had one voice - one reference recording that
Chatterbox clones - and the owner asked for a bank of them:

* **On searchFAM the listener chooses.** A voice is a setting of theirs,
  changed on the search page (the button between the length and "attach
  link") or under Listening in Settings. It is kept here per listener, so a
  choice made on one visit is the voice of the next search too.
* **Everywhere else FAM chooses, at random, once per episode.** A myFAM tile,
  a DailyFAM edition, a Trending episode: none of them offer a voice picker,
  and each is spoken in a voice drawn from this bank. Once, and kept beside
  the script (`scripts.voice`), because the audio of a cached episode is kept
  per voice (§132) - a new draw on every play would voice every replay again
  on the GPU, which is exactly the bill §132 exists to stop.

## What a bank voice is

A reference recording and the rights to clone it, stored **in the app's
database** (`VOICE_BANK_DB`) rather than on the GPU. That is the whole point
of keeping it here: the voice runs on a rented pod that is replaced without
notice (§112), and a bank that lived on the pod would be lost with it. The
app is where the recordings are durable; the worker is where they are used.

So the worker is told *which* voice by id and fingerprint, and keeps its own
copy under `~/.fam/voices/bank/`. When it has never heard of one it says so
(`MISSING_MARKER`) and the app sends the recording once, with its rights
record, on the retry - one upload per voice per worker, never per sentence.

The default voice - the reference recording FAM has always cloned - is part
of the bank without being a row in it: it is already on every worker, and it
is the voice every request that names none has always been spoken in.

## Rights

A cloned voice is somebody's voice. A recording is refused unless its rights
record clears the same three fields `ChatterboxEngine.rights_cleared` asks of
the default one (consent, commercial use, synthetic voice cleared), and the
record travels with the recording, so the worker's own gate checks it again.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import random
import re
import sqlite3
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import voice_store
from paths import data_path

log = logging.getLogger(__name__)

#: What a worker says when it is asked for a bank voice it has no copy of. The
#: app looks for exactly this string, attaches the recording and asks again.
MISSING_MARKER = "voice-not-on-worker"

#: The fields a rights record must clear. The same three the default voice's
#: record is checked for (`tts.ChatterboxEngine.rights_cleared`).
RIGHTS_FIELDS = ("consent", "commercial_use", "synthetic_voice_cleared")

#: A voice id is a slug: it is part of an audio cache key and a file name.
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

#: A reference recording is seconds of speech. Anything bigger is a mistake,
#: and it would be sent to a worker whole.
MAX_REFERENCE_BYTES = 8 * 1024 * 1024

#: The label the default voice has always had in the picker.
DEFAULT_LABEL = "FAM"


class VoiceBankError(ValueError):
    """A voice could not be added, phrased for whoever is adding it."""


@dataclass(frozen=True)
class BankVoice:
    slug: str
    label: str
    description: str = ""
    sha256: str = ""
    added: float = 0.0
    default: bool = False

    def as_dict(self) -> dict:
        return {"slug": self.slug, "label": self.label,
                "description": self.description, "default": self.default}


# ---------------------------------------------------------------------------
# Ids
# ---------------------------------------------------------------------------
def slug_of(voice: Optional[str]) -> str:
    """The bank slug in a voice id: "remote:nova" and "nova" are both "nova"."""
    text = (voice or "").strip()
    if ":" in text:
        text = text.split(":", 1)[1]
    return text.lower()


def default_slug() -> str:
    """The default voice's slug: its reference recording's name."""
    try:
        from tts import ChatterboxEngine

        return ChatterboxEngine.reference_path().stem.lower()
    except Exception:  # noqa: BLE001 - a name, never a reason to fail
        return "reference_3"


def is_default(voice: Optional[str]) -> bool:
    slug = slug_of(voice)
    return not slug or slug == "default" or slug == default_slug()


def check_rights(record: dict) -> None:
    if not isinstance(record, dict):
        raise VoiceBankError("the rights record must be an object")
    for field in RIGHTS_FIELDS:
        if str(record.get(field)).strip().lower() not in ("yes", "true"):
            raise VoiceBankError(
                f"the rights record does not clear {field!r}; a cloned voice "
                "is somebody's voice, and FAM does not clone it without "
                "consent, commercial use and synthetic voice all cleared")


def check_recording(audio: bytes) -> float:
    """Refuse anything that is not a usable WAV. Returns its length in seconds."""
    if not audio:
        raise VoiceBankError("the recording is empty")
    if len(audio) > MAX_REFERENCE_BYTES:
        raise VoiceBankError(
            f"the recording is {len(audio) // 1024} KB; a reference is a few "
            f"seconds of speech and must be under {MAX_REFERENCE_BYTES // 1024 // 1024} MB")
    try:
        with wave.open(io.BytesIO(audio)) as wav:
            frames, rate = wav.getnframes(), wav.getframerate()
    except (wave.Error, EOFError) as exc:
        raise VoiceBankError(f"the recording is not a WAV file: {exc}") from exc
    seconds = frames / float(rate or 1)
    if seconds < 3:
        raise VoiceBankError(
            f"the recording is {seconds:.1f}s; Chatterbox needs at least a few "
            "seconds of clean speech to clone a voice")
    return seconds


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------
class VoiceBank:
    """The bank's recordings, and each listener's chosen voice for search."""

    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("VOICE_BANK_DB", "voice_bank.db", path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS voices (
                       slug        TEXT PRIMARY KEY,
                       label       TEXT NOT NULL,
                       description TEXT NOT NULL DEFAULT '',
                       sha256      TEXT NOT NULL,
                       audio       BLOB NOT NULL,
                       rights      TEXT NOT NULL,
                       added       REAL NOT NULL
                   )""")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS choices (
                       user_id TEXT PRIMARY KEY,
                       voice   TEXT NOT NULL,
                       updated REAL NOT NULL
                   )""")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    # -- recordings --------------------------------------------------------

    def add(self, slug: str, label: str, audio: bytes, rights: dict,
            description: str = "", replace: bool = False) -> BankVoice:
        slug = (slug or "").strip().lower()
        if not SLUG.match(slug):
            raise VoiceBankError(
                "a voice id is lower-case letters, digits, '-' and '_', "
                "up to 40 characters")
        if slug == default_slug() or slug == "default":
            raise VoiceBankError(f"{slug!r} is the default voice's name")
        label = (label or "").strip()[:40]
        if not label:
            raise VoiceBankError("a voice needs a label listeners can read")
        check_rights(rights)
        check_recording(audio)
        if not replace and self.get(slug) is not None:
            raise VoiceBankError(
                f"there is already a voice called {slug!r}. Give the new one "
                "its own id: kept audio is keyed on the id, so a replaced "
                "recording would replay episodes in the old voice")
        sha = hashlib.sha256(audio).hexdigest()
        now = time.time()
        self._conn().execute(
            "INSERT OR REPLACE INTO voices"
            " (slug, label, description, sha256, audio, rights, added)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (slug, label, (description or "").strip()[:160], sha, audio,
             json.dumps(rights), now))
        log.info("voice bank: added %s (%s, %d KB)", slug, label, len(audio) // 1024)
        return BankVoice(slug, label, (description or "").strip()[:160], sha, now)

    def remove(self, slug: str) -> bool:
        cur = self._conn().execute("DELETE FROM voices WHERE slug = ?",
                                   (slug_of(slug),))
        return bool(cur.rowcount)

    def get(self, slug: str) -> Optional[BankVoice]:
        row = self._conn().execute(
            "SELECT slug, label, description, sha256, added FROM voices"
            " WHERE slug = ?", (slug_of(slug),)).fetchone()
        return BankVoice(*row) if row else None

    def voices(self) -> list[BankVoice]:
        rows = self._conn().execute(
            "SELECT slug, label, description, sha256, added FROM voices"
            " ORDER BY added, slug").fetchall()
        return [BankVoice(*row) for row in rows]

    def recording(self, slug: str) -> Optional[tuple[bytes, dict, str]]:
        """(audio, rights record, sha256) for one voice, or None."""
        row = self._conn().execute(
            "SELECT audio, rights, sha256 FROM voices WHERE slug = ?",
            (slug_of(slug),)).fetchone()
        if not row:
            return None
        try:
            rights = json.loads(row[1])
        except json.JSONDecodeError:
            rights = {}
        return bytes(row[0]), rights, row[2]

    # -- each listener's search voice --------------------------------------

    def choice(self, user_id: str) -> str:
        if not user_id:
            return ""
        row = self._conn().execute(
            "SELECT voice FROM choices WHERE user_id = ?", (user_id,)).fetchone()
        return row[0] if row else ""

    def choose(self, user_id: str, voice: str) -> None:
        if not user_id:
            return
        self._conn().execute(
            "INSERT OR REPLACE INTO choices (user_id, voice, updated)"
            " VALUES (?, ?, ?)", (user_id, slug_of(voice)[:40], time.time()))

    def forget(self, user_id: str) -> int:
        cur = self._conn().execute("DELETE FROM choices WHERE user_id = ?",
                                   (user_id,))
        return cur.rowcount or 0


_BANK: Optional[VoiceBank] = None
_BANK_LOCK = threading.Lock()


def bank() -> VoiceBank:
    """The process's bank, opened on first use."""
    global _BANK
    with _BANK_LOCK:
        if _BANK is None:
            _BANK = VoiceBank()
        return _BANK


def opened() -> Optional[VoiceBank]:
    """The bank if something has opened it, for `storage_doctor`."""
    return _BANK


def reset(store: Optional[VoiceBank] = None) -> None:
    """For tests: use `store`, or reopen from the environment on next use."""
    global _BANK
    with _BANK_LOCK:
        _BANK = store


# ---------------------------------------------------------------------------
# The catalogue every surface reads
# ---------------------------------------------------------------------------
def catalogue() -> list[BankVoice]:
    """Every voice in the bank, the default first. Never raises."""
    voices = [BankVoice(default_slug(), DEFAULT_LABEL, "The FAM voice",
                        default=True)]
    try:
        voices.extend(bank().voices())
    except Exception:  # noqa: BLE001 - the default voice still speaks
        log.exception("voice bank: could not read the bank")
    return voices


def known(voice: Optional[str]) -> bool:
    slug = slug_of(voice)
    return is_default(slug) or any(v.slug == slug for v in catalogue())


def random_slug(rng: random.Random | None = None) -> str:
    """A voice for an episode nobody chose one for (myFAM, DailyFAM)."""
    voices = catalogue()
    return (rng or random).choice(voices).slug


# ---------------------------------------------------------------------------
# The recording on the machine that speaks
# ---------------------------------------------------------------------------
def local_dir() -> Path:
    return voice_store.voices_dir() / "bank"


def local_path(slug: str, sha: str) -> Path:
    return local_dir() / f"{slug_of(slug)}-{sha[:12]}.wav"


def local_reference(slug: str, sha: str = "") -> Optional[Path]:
    """This machine's copy of a bank voice, if it has one."""
    slug = slug_of(slug)
    if sha:
        path = local_path(slug, sha)
        return path if path.exists() else None
    folder = local_dir()
    if not folder.is_dir():
        return None
    found = sorted(folder.glob(f"{slug}-*.wav"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return found[0] if found else None


def materialize(slug: str, sha: str, audio: bytes, rights: dict) -> Path:
    """Write a bank voice (and its rights record) where Chatterbox reads it.

    Checked again here, because this is the machine that clones it: a worker
    must not take a recording on the app's word alone. Written to a temporary
    name first, so a half-written file never looks like a voice.
    """
    check_rights(rights)
    if hashlib.sha256(audio).hexdigest() != sha:
        raise VoiceBankError("the recording does not match its fingerprint")
    path = local_path(slug, sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    for target, data in ((path.with_suffix(".rights.json"),
                          json.dumps(rights).encode("utf-8")), (path, audio)):
        partial = target.with_name(target.name + ".partial")
        partial.write_bytes(data)
        partial.replace(target)
    return path


def reference_from_bank(slug: str) -> Optional[Path]:
    """This machine's copy, written from the app's own bank when it has one.

    The in-process engine's path: the machine that speaks is the machine
    with the database, so there is nothing to send anybody.
    """
    try:
        held = bank().recording(slug)
    except Exception:  # noqa: BLE001
        log.exception("voice bank: could not read %s", slug)
        return None
    if held is None:
        return None
    audio, rights, sha = held
    existing = local_reference(slug, sha)
    if existing is not None:
        return existing
    return materialize(slug, sha, audio, rights)


def wire_fields(slug: str, with_recording: bool = False) -> dict:
    """What a request to a worker carries about a bank voice.

    The fingerprint always; the recording only on the retry after a worker
    said it had none (`MISSING_MARKER`). Empty for the default voice, which
    every worker already has.
    """
    if is_default(slug):
        return {}
    held = bank().recording(slug)
    if held is None:
        return {}
    audio, rights, sha = held
    fields = {"voice_sha": sha}
    if with_recording:
        fields["reference"] = base64.b64encode(audio).decode("ascii")
        fields["rights"] = rights
    return fields
