"""§332: every voice was heard as the default.

Two causes, each tested where it is enforced:

* A myFAM or DailyFAM episode's voice was drawn when it was written, from the
  bank as it was then, and kept for its week - so everything written before a
  voice was added stayed in the default. When the bank changes, every episode
  nobody has heard is dealt again (`redeal_voices`, `app._redeal_voices`).
* Nothing said which voice a worker actually spoke. A worker now names the
  recording it cloned; the app refuses one that names another, and does not
  keep audio from one that names none.
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import pipeline_errors  # noqa: E402
import tts  # noqa: E402
import voice_bank  # noqa: E402
from cache import MemoryScriptCache, SqliteScriptCache  # noqa: E402
from remote_voice import RemoteChatterboxEngine, RemoteVoiceError  # noqa: E402
from tests.test_voice_bank_147 import RIGHTS, bank, wav  # noqa: E402,F401


# --- the deal follows the bank ------------------------------------------------


@pytest.mark.parametrize("make", ["memory", "sqlite"])
def test_only_unheard_episodes_are_dealt_again(make, tmp_path):
    cache = (MemoryScriptCache() if make == "memory"
             else SqliteScriptCache(str(tmp_path / "s.db")))
    cache.put("heard", ["a."], 3600, "q1", "", 1, origin="myfam", voice="reference_3")
    cache.put("unheard", ["a."], 3600, "q2", "", 1, origin="dailyfam",
              voice="reference_3")
    cache.put_audio("heard", "remote:reference_3", 24000, b"\x01\x00" * 2400,
                    ["a."], [0.0])
    assert cache.redeal_voices() == 1
    assert cache.voice_of("heard") == "reference_3", \
        "an episode with kept audio must keep the voice that replays it"
    assert cache.voice_of("unheard") == ""
    assert cache.set_voice("unheard", "nova") == "nova"


def test_adding_a_voice_deals_unheard_episodes_again_once(bank, monkeypatch, tmp_path):
    store = SqliteScriptCache(str(tmp_path / "scripts.db"))
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", store)
    # Written while the bank held only the default: the only draw there was.
    store.put("tile", ["a."], 3600, "why tides happen", "", 1, origin="myfam",
              voice=voice_bank.random_slug())
    assert store.voice_of("tile") == voice_bank.default_slug()
    appmod._redeal_voices()          # the boot that first sees this bank
    store.set_voice("tile", voice_bank.default_slug())

    bank.add("nova", "Nova", wav(), RIGHTS)
    assert appmod._redeal_voices() == 1
    assert store.voice_of("tile") == ""
    # The same bank again is not a new deal: a tile drawn since keeps its draw.
    store.set_voice("tile", "nova")
    assert appmod._redeal_voices() == 0
    assert store.voice_of("tile") == "nova"


# --- a worker says which voice it spoke ----------------------------------------


class Endpoint:
    url = "http://worker"
    rung = "test"
    transport = "http"

    def key(self):
        return "http:http://worker"


def _remote(monkeypatch, reply):
    sent = []

    async def speak(self, payload, config, endpoint):
        sent.append(payload)
        return reply(payload, config)

    async def endpoint(self, config):
        return Endpoint()

    async def no_replacement(self, endpoint, detail):
        return None

    monkeypatch.setattr(RemoteChatterboxEngine, "_speak", speak)
    monkeypatch.setattr(RemoteChatterboxEngine, "_endpoint", endpoint)
    monkeypatch.setattr(RemoteChatterboxEngine, "_after_failure", no_replacement)
    monkeypatch.setattr(RemoteChatterboxEngine, "_unconfirmed", set())
    return sent


def _audio(config, **extra):
    return {"audio": base64.b64encode(b"\x01\x00" * 240).decode(),
            "sample_rate": config.sample_rate, "format": "pcm_s16le", **extra}


def test_a_worker_that_cloned_another_recording_is_a_failure(bank, monkeypatch):
    bank.add("nova", "Nova", wav(), RIGHTS)
    _remote(monkeypatch, lambda p, c: _audio(c, voice_sha="0" * 64))
    with pytest.raises(RemoteVoiceError, match="cloned another recording"):
        asyncio.run(RemoteChatterboxEngine().synth("Hello.", 150, "remote:nova"))


def test_a_worker_that_confirms_the_voice_is_kept(bank, monkeypatch):
    bank.add("nova", "Nova", wav(), RIGHTS)
    sha = bank.get("nova").sha256
    _remote(monkeypatch, lambda p, c: _audio(c, voice_sha=p.get("voice_sha")))
    engine = RemoteChatterboxEngine()
    assert asyncio.run(engine.synth("Hello.", 150, "remote:nova"))
    assert not engine.voice_unconfirmed
    assert sha


def test_a_worker_that_cannot_say_plays_but_is_not_kept_and_is_reported_once(
        bank, monkeypatch):
    bank.add("nova", "Nova", wav(), RIGHTS)
    recorded = []
    monkeypatch.setattr(pipeline_errors, "record",
                        lambda *a, **k: recorded.append((a, k)))
    _remote(monkeypatch, lambda p, c: _audio(c))
    engine = RemoteChatterboxEngine()
    assert asyncio.run(engine.synth("Hello.", 150, "remote:nova"))
    asyncio.run(engine.synth("Again.", 150, "remote:nova"))
    assert engine.voice_unconfirmed
    assert len(recorded) == 1 and recorded[0][0][:2] == ("voice", "voice not confirmed")
    assert recorded[0][1]["severity"] == pipeline_errors.NOTICE


def test_the_default_voice_needs_no_confirmation(bank, monkeypatch):
    _remote(monkeypatch, lambda p, c: _audio(c))
    engine = RemoteChatterboxEngine()
    assert asyncio.run(engine.synth("Hello.", 150, None))
    assert not engine.voice_unconfirmed


def test_unconfirmed_audio_is_not_kept(tmp_path):
    from pipeline import GenerationStats, PodcastPipeline
    from tests.test_pipeline import FakeGenerator

    store = SqliteScriptCache(str(tmp_path / "s.db"))
    store.put("k", ["a."], 3600, "q", "", 1)
    engine = tts.DebugEngine()
    engine.voice_unconfirmed = True
    pipe = PodcastPipeline(generator=FakeGenerator(), engine=engine, cache=store,
                           voice="debug:nova")
    stats = GenerationStats()
    stats.audio_key = "k"
    asyncio.run(pipe._keep_audio(b"\x01\x00" * 2400, stats))
    assert not store.audio_voices("k")
    engine.voice_unconfirmed = False
    asyncio.run(pipe._keep_audio(b"\x01\x00" * 2400, stats))
    assert store.audio_voices("k") == ["debug:nova"]


def test_the_worker_names_the_recording_it_cloned(bank, monkeypatch):
    from voice_worker import synth as worker

    bank.add("nova", "Nova", wav(), RIGHTS)
    sha = bank.get("nova").sha256

    async def load():
        return 24000

    monkeypatch.setattr(tts.ChatterboxEngine, "diagnose",
                        classmethod(lambda cls: (True, "stub")))
    monkeypatch.setattr(tts.ChatterboxEngine, "_synth_blocking",
                        lambda self, text: (b"\x01\x00" * 240, 24000))
    monkeypatch.setattr(worker, "_load", load)
    reply = asyncio.run(worker.synthesise(
        {"text": "Hello.", "voice": "nova", "voice_sha": sha}))
    assert reply["voice_sha"] == sha
