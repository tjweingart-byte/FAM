"""§147: Explore is searches only, a bank of voices, and two minutes off search.

Four rules from one packet, each tested where it is enforced rather than
where it is drawn:

* Explore holds episodes a *search* wrote, and nothing else in the shared
  cache - `scripts.origin`, stamped per request like `author`.
* The voice bank lives in the app's database, with a rights record per voice,
  and a worker that lacks a recording is sent it once.
* Search is spoken in the voice the listener chose; every other surface in a
  voice drawn from the bank per episode and kept beside the script.
* Every episode that is not a search is two minutes, whatever is sent.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import wave

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import tts  # noqa: E402
import voice_bank  # noqa: E402
from cache import MemoryScriptCache, SqliteScriptCache  # noqa: E402
from pipeline import PodcastPipeline  # noqa: E402
from tests.test_pipeline import FakeGenerator  # noqa: E402

RIGHTS = {"consent": "yes", "commercial_use": "yes",
          "synthetic_voice_cleared": "yes"}


def wav(seconds: float = 4.0, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x01" * int(seconds * rate))
    return buf.getvalue()


@pytest.fixture
def bank(tmp_path, monkeypatch):
    monkeypatch.setenv("FAM_VOICES_DIR", str(tmp_path / "voices"))
    store = voice_bank.VoiceBank(str(tmp_path / "bank.db"))
    voice_bank.reset(store)
    yield store
    voice_bank.reset(None)


# --- Explore: searches only -------------------------------------------------


@pytest.mark.parametrize("make", [MemoryScriptCache,
                                  lambda: None])
def test_explore_reads_only_what_a_search_wrote(make, tmp_path):
    cache = make() or SqliteScriptCache(str(tmp_path / "s.db"))
    for query, origin in (("why the sky is blue", "search"),
                          ("the fed this week", "myfam"),
                          ("eagles daily", "dailyfam"),
                          ("a warmed guess", "prefetch"),
                          ("written before origins", "")):
        cache.put(query, [f"{query}."], 3600, query, "", 2, origin=origin)
    everything = {e["query"] for e in cache.recent(10)}
    searched = [e["query"] for e in cache.recent(10, origin="search")]
    assert len(everything) == 5
    assert searched == ["why the sky is blue"]


def test_a_search_rewriting_an_entry_makes_it_a_searched_one(tmp_path):
    cache = SqliteScriptCache(str(tmp_path / "s.db"))
    cache.put("k", ["a."], 3600, "q", "", 2, origin="myfam", voice="nova")
    cache.put("k", ["a."], 3600, "q", "", 2, origin="prefetch", voice="ellis")
    assert cache.origin_of("k") == "myfam", "a non-search re-write moved the origin"
    cache.put("k", ["a."], 3600, "q", "", 2, origin="search", voice="maya")
    assert cache.origin_of("k") == "search"
    assert cache.voice_of("k") == "nova", "the first voice an episode had must stick"
    assert cache.set_voice("k", "maya") == "nova"


def test_the_explore_endpoint_passes_the_origin_filter(monkeypatch, tmp_path):
    store = SqliteScriptCache(str(tmp_path / "s.db"))
    store.put("a", ["x."], 3600, "searched thing", "", 2, author="someone",
              origin="search")
    store.put("b", ["x."], 3600, "a myfam tile", "", 2, author="someone",
              origin="myfam")
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", store)
    body = TestClient(appmod.app).get("/api/explore").json()
    assert [e["query"] for e in body["episodes"]] == ["searched thing"]


# --- the bank ---------------------------------------------------------------


def test_a_voice_needs_its_rights_and_a_real_recording(bank):
    with pytest.raises(voice_bank.VoiceBankError, match="consent"):
        bank.add("nova", "Nova", wav(), {"consent": "yes"})
    with pytest.raises(voice_bank.VoiceBankError, match="WAV"):
        bank.add("nova", "Nova", b"not audio at all", RIGHTS)
    with pytest.raises(voice_bank.VoiceBankError, match="seconds"):
        bank.add("nova", "Nova", wav(1.0), RIGHTS)
    with pytest.raises(voice_bank.VoiceBankError):
        bank.add("Bad Slug!", "Nova", wav(), RIGHTS)
    bank.add("nova", "Nova", wav(), RIGHTS, description="Warm")
    with pytest.raises(voice_bank.VoiceBankError, match="already"):
        bank.add("nova", "Nova again", wav(5), RIGHTS)


def test_the_catalogue_is_the_default_voice_then_the_bank(bank):
    bank.add("nova", "Nova", wav(), RIGHTS)
    bank.add("ellis", "Ellis", wav(), RIGHTS)
    slugs = [v.slug for v in voice_bank.catalogue()]
    assert slugs[0] == voice_bank.default_slug()
    assert set(slugs[1:]) == {"nova", "ellis"}
    assert voice_bank.known("remote:nova") and voice_bank.known("nova")
    assert not voice_bank.known("remote:nobody")
    draws = {voice_bank.random_slug() for _ in range(200)}
    assert draws == set(slugs), "a random draw must be able to reach every voice"


def test_a_choice_is_kept_and_forgotten_with_the_account(bank):
    bank.choose("u1", "remote:nova")
    assert bank.choice("u1") == "nova"
    assert bank.forget("u1") == 1
    assert bank.choice("u1") == ""


def test_a_machine_without_the_recording_says_so_by_the_marker(bank):
    """A worker told a voice by fingerprint never substitutes the default
    for it: it asks for the recording. A voice nobody knows any more - no
    fingerprint, not in the bank - is a stale choice and gets the default."""
    voice_bank.reset(voice_bank.VoiceBank(bank.path + ".empty"))
    with pytest.raises(tts.TTSUnavailable, match=voice_bank.MISSING_MARKER):
        tts.ChatterboxEngine.reference_for("remote:nova", "ab" * 32)
    assert (tts.ChatterboxEngine.reference_for("remote:gone")
            == tts.ChatterboxEngine.reference_path())


def test_a_voice_that_left_the_bank_is_sent_as_the_default(bank):
    from remote_voice import RemoteChatterboxEngine

    payload = RemoteChatterboxEngine._payload(
        "Hello.", RemoteChatterboxEngine.config(), "remote:gone")
    assert "voice_sha" not in payload
    assert payload["voice"] == (RemoteChatterboxEngine.config().voice or None)


def test_the_app_side_bank_materialises_with_its_rights(bank):
    bank.add("nova", "Nova", wav(), RIGHTS)
    path = tts.ChatterboxEngine.reference_for("chatterbox:nova")
    assert path.exists() and path.parent.name == "bank"
    cleared, _ = tts.ChatterboxEngine.rights_cleared(path)
    assert cleared
    # A worker sent the recording keeps it, and checks the fingerprint.
    audio, rights, sha = bank.recording("nova")
    with pytest.raises(voice_bank.VoiceBankError, match="fingerprint"):
        voice_bank.materialize("nova", "0" * 64, audio, rights)


def test_the_remote_payload_sends_the_recording_only_on_the_retry(bank):
    from remote_voice import RemoteChatterboxEngine

    bank.add("nova", "Nova", wav(), RIGHTS)
    config = RemoteChatterboxEngine.config()
    first = RemoteChatterboxEngine._payload("Hello.", config, "remote:nova")
    assert first["voice"] == "nova" and first["voice_sha"]
    assert "reference" not in first
    retry = RemoteChatterboxEngine._payload("Hello.", config, "remote:nova",
                                            with_recording=True)
    assert retry["rights"] == RIGHTS and retry["reference"]
    default = RemoteChatterboxEngine._payload("Hello.", config, None)
    assert "voice_sha" not in default


def test_the_remote_engine_retries_a_missing_voice_with_its_recording(bank, monkeypatch):
    from remote_voice import RemoteChatterboxEngine, RemoteVoiceError
    import base64

    bank.add("nova", "Nova", wav(), RIGHTS)
    sent = []

    async def speak(self, payload, config, endpoint):
        sent.append(payload)
        if "reference" not in payload:
            raise RemoteVoiceError(f"HTTP 422: {voice_bank.MISSING_MARKER}: no copy")
        return {"audio": base64.b64encode(b"\x00\x00" * 240).decode(),
                "sample_rate": config.sample_rate, "format": "pcm_s16le"}

    class Endpoint:
        url = "http://worker"
        rung = "test"
        transport = "http"

    async def endpoint(self, config):
        return Endpoint()

    monkeypatch.setattr(RemoteChatterboxEngine, "_speak", speak)
    monkeypatch.setattr(RemoteChatterboxEngine, "_endpoint", endpoint)
    pcm = asyncio.run(RemoteChatterboxEngine().synth("Hello.", 150, "remote:nova"))
    assert pcm and len(sent) == 2 and "reference" in sent[1]


# --- which voice, and how long ----------------------------------------------


class BankEngine(tts.DebugEngine):
    """Stands in for the production voice: keeps audio, records its voice."""

    name = "chatterbox"
    keeps_audio = True
    spoken: list = []

    @classmethod
    def available(cls):
        return True

    @classmethod
    def voices(cls):
        return [tts.Voice(id=f"chatterbox:{v.slug}", label=v.label,
                          engine="chatterbox") for v in voice_bank.catalogue()]

    @classmethod
    def default_voice_id(cls):
        return f"chatterbox:{voice_bank.default_slug()}"

    async def synth(self, text, wpm, voice=None):
        type(self).spoken.append(voice)
        return await super().synth(text, wpm, None)


@pytest.fixture
def served(bank, monkeypatch, tmp_path):
    bank.add("nova", "Nova", wav(), RIGHTS)
    bank.add("ellis", "Ellis", wav(), RIGHTS)
    store = SqliteScriptCache(str(tmp_path / "scripts.db"))
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", store)
    monkeypatch.setattr(appmod, "DEMO_MODE", False)
    monkeypatch.setattr(tts, "PRODUCTION_ENGINES", (BankEngine,))
    BankEngine.spoken = []
    monkeypatch.setattr(
        appmod, "_make_pipeline",
        lambda voice=None, author="": PodcastPipeline(
            generator=FakeGenerator(), engine=BankEngine(), cache=store,
            voice=voice, author=author))
    return store


def test_a_myfam_tap_is_two_minutes_in_a_bank_voice_it_keeps(served):
    client = TestClient(appmod.app)
    res = client.get("/api/audio?q=why+tides+happen&minutes=7&fmt=pcm"
                     "&surface=myfam&voice=chatterbox:ellis")
    assert res.status_code == 200
    rows = served.recent(10)
    assert [(r["minutes"], r["origin"]) for r in rows] == [(2, "myfam")], \
        "a myFAM episode must be two minutes whatever the client asked"
    kept = rows[0]["voice"]
    assert voice_bank.known(kept)
    heard = {v for v in BankEngine.spoken}
    assert len(heard) == 1
    # The client's voice is not a myFAM episode's to choose...
    if not voice_bank.is_default(kept):
        assert heard == {f"chatterbox:{kept}"}
    # ...and the next play of the same episode is the same voice.
    BankEngine.spoken = []
    client.get("/api/audio?q=why+tides+happen&minutes=2&fmt=pcm&surface=myfam")
    assert served.voice_of(rows[0]["key"]) == kept
    assert not served.recent(10, origin="search"), "a myFAM episode reached Explore"


def test_a_search_is_spoken_in_the_listeners_voice_and_reaches_explore(served):
    client = TestClient(appmod.app)
    assert client.post("/api/voices/choice",
                       json={"voice": "chatterbox:nova"}).status_code == 200
    assert client.get("/api/voices").json()["selected"] == "chatterbox:nova"
    res = client.get("/api/audio?q=how+volcanoes+form&minutes=5&fmt=pcm&surface=search")
    assert res.status_code == 200
    assert set(BankEngine.spoken) == {"chatterbox:nova"}
    rows = served.recent(10, origin="search")
    assert [(r["query"], r["minutes"], r["voice"]) for r in rows] == \
        [("how volcanoes form", 5, "nova")]


def test_a_voice_outside_the_bank_is_refused(served):
    res = TestClient(appmod.app).post("/api/voices/choice",
                                      json={"voice": "chatterbox:nobody"})
    assert res.status_code == 400


def test_background_editions_stamp_their_origin_and_a_bank_voice():
    for path, origin in (("daily_edition.py", "dailyfam"),
                         ("trending_bank.py", "trending"),
                         ("prefetch.py", "prefetch")):
        source = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), path)).read()
        assert f'"origin"] = "{origin}"' in source, path
        assert "voice_bank.random_slug()" in source, path


def test_browse_lengths_are_fixed_in_code():
    import config
    import daily_edition

    assert config.BROWSE_MINUTES == 2
    assert daily_edition.minutes() == 2
    assert not hasattr(config.settings, "daily_edition_minutes")
    assert not hasattr(config.settings, "trending_bank_minutes")


def test_account_deletion_forgets_the_voice_choice_and_survives_a_broken_bank(
        bank, monkeypatch):
    """The choice is per-listener data, so deleting an account removes it -
    and a bank that cannot open fails its own row, never the whole erase."""
    bank.add("nova", "Nova", wav(), RIGHTS)
    bank.choose("leaver", "nova")
    removed = appmod.erase_listener("leaver")
    assert removed["voice_choice"] == 1 and bank.choice("leaver") == ""

    def broken():
        raise RuntimeError("the bank could not open")
    monkeypatch.setattr(voice_bank, "bank", broken)
    removed = appmod.erase_listener("another")
    assert removed["voice_choice"] == -1
    assert removed["events"] >= 0, "a broken bank stopped the rest of the erase"


def test_a_sentence_carries_the_fingerprint_and_never_reads_the_recording(
        bank, monkeypatch):
    bank.add("nova", "Nova", wav(), RIGHTS)
    monkeypatch.setattr(bank, "recording", lambda slug: pytest.fail(
        "an ordinary sentence read the whole recording"))
    assert set(voice_bank.wire_fields("nova")) == {"voice_sha"}
