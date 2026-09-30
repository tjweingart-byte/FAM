"""§176: the voice that drifted - seeded chunks, one recording, pinned code.

The owner heard the voice "sometimes good, and sometimes a southern twang".
These pin the fixes, none of which needs a GPU to test:

* each chunk's sampling is seeded from the voice and the words, and the
  recording is prepared once rather than re-read on every chunk;
* a worker says which recording it clones, and one that differs from
  `VOICE_REFERENCE_FINGERPRINT` is refused - by the app and by its own boot check;
* chatterbox-tts is pinned, and the worker reports its weights;
* the bank refuses a recording that starts in silence or is too quiet, and
  `tools/master_reference.py` fixes both without touching the original;
* `verify_voice.py --fingerprint` turns "sounds off" into numbers.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import io
import math
import pathlib
import sys
import types
import wave

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import tts  # noqa: E402
import voice_bank  # noqa: E402
import voice_control  # noqa: E402
from tts import ChatterboxEngine  # noqa: E402


def tone(seconds: float = 4.0, rate: int = 24000, amplitude: int = 3200,
         lead: float = 0.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        silent = int(lead * rate)
        out.writeframes(b"\x00\x00" * silent + b"".join(
            int(amplitude * math.sin(2 * math.pi * 220 * i / rate)).to_bytes(
                2, "little", signed=True)
            for i in range(int(seconds * rate))))
    return buf.getvalue()


def _settings(monkeypatch, module, **changes):
    monkeypatch.setattr(module, "settings",
                        dataclasses.replace(module.settings, **changes))


# --- seeded chunks, prepared once -----------------------------------------

def test_a_chunk_seed_is_the_voice_and_the_words_and_nothing_else():
    a = tts.chunk_seed("sha-a", "The bridge opens in spring.")
    assert a == tts.chunk_seed("sha-a", "The bridge opens in spring.")
    assert a != tts.chunk_seed("sha-a", "The bridge opens in summer.")
    assert a != tts.chunk_seed("sha-b", "The bridge opens in spring.")
    assert 0 <= a < 2 ** 31


def test_a_salt_rerolls_every_sentence(monkeypatch, tmp_path):
    """Seeding makes a bad take permanent; the salt is the way out of one."""
    assert tts.chunk_seed("sha", "Same words.", "take-2") != \
        tts.chunk_seed("sha", "Same words.")
    calls, reference = _fake_card(monkeypatch, tmp_path)
    _settings(monkeypatch, tts, chatterbox_seed_salt="take-2")
    ChatterboxEngine()._synth_blocking("Same words.")
    sha = ChatterboxEngine.reference_sha256(reference)
    assert calls.seeds == [tts.chunk_seed(sha, "Same words.", "take-2")]


def test_mastering_describes_a_silent_file_without_crashing(capsys):
    from tools import master_reference

    master_reference.describe("before", tone(amplitude=0))
    assert "n/a" in capsys.readouterr().out


def _fake_card(monkeypatch, tmp_path):
    """A model that records what it was asked, and a torch that records seeds."""
    import numpy as np

    calls = types.SimpleNamespace(prepared=[], seeds=[], generated=[])

    class FakeWav:
        def squeeze(self, axis):
            return self

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return np.zeros(240, dtype="float32")

    class FakeModel:
        sr = 24000
        device = "cuda"
        conds = None

        def prepare_conditionals(self, path, exaggeration=0.5):
            calls.prepared.append((path, exaggeration))
            self.conds = ("conds", path)

        def generate(self, text, **kwargs):
            assert "audio_prompt_path" not in kwargs, "re-reads the recording per chunk"
            assert self.conds is not None
            calls.generated.append((text, self.conds, kwargs))
            return FakeWav()

    torch = types.ModuleType("torch")
    torch.inference_mode = contextlib.nullcontext
    torch.manual_seed = lambda seed: calls.seeds.append(seed)
    monkeypatch.setitem(sys.modules, "torch", torch)
    reference = tmp_path / "reference_3.wav"
    reference.write_bytes(tone())
    model = FakeModel()
    monkeypatch.setattr(ChatterboxEngine, "_model", classmethod(lambda cls: model))
    monkeypatch.setattr(ChatterboxEngine, "reference_path",
                        staticmethod(lambda: reference))
    monkeypatch.setattr(ChatterboxEngine, "_conds", {})
    monkeypatch.setattr(ChatterboxEngine, "_shas", {})
    return calls, reference


def test_the_recording_is_prepared_once_and_every_chunk_is_seeded(monkeypatch, tmp_path):
    calls, reference = _fake_card(monkeypatch, tmp_path)
    engine = ChatterboxEngine()
    engine._synth_blocking("First sentence.")
    engine._synth_blocking("Second sentence.")
    engine._synth_blocking("First sentence.")

    assert calls.prepared == [(str(reference), tts.CHATTERBOX_GENERATION["exaggeration"])]
    sha = ChatterboxEngine.reference_sha256(reference)
    assert calls.seeds == [tts.chunk_seed(sha, "First sentence."),
                           tts.chunk_seed(sha, "Second sentence."),
                           tts.chunk_seed(sha, "First sentence.")]
    # The six generation settings are unchanged - they are the voice.
    for _text, _conds, kwargs in calls.generated:
        assert kwargs == tts.CHATTERBOX_GENERATION


def test_a_replaced_recording_is_a_new_voice_not_a_stale_cache(monkeypatch, tmp_path):
    calls, reference = _fake_card(monkeypatch, tmp_path)
    engine = ChatterboxEngine()
    engine._synth_blocking("One.")
    reference.write_bytes(tone(amplitude=6000, seconds=5.0))
    engine._synth_blocking("One.")
    assert len(calls.prepared) == 2
    assert calls.seeds[0] != calls.seeds[1]


def test_seeding_can_be_switched_off(monkeypatch, tmp_path):
    calls, _ = _fake_card(monkeypatch, tmp_path)
    _settings(monkeypatch, tts, chatterbox_seeded=False)
    ChatterboxEngine()._synth_blocking("Unseeded.")
    assert calls.seeds == [] and len(calls.generated) == 1


# --- one recording, checked -----------------------------------------------

def test_the_engine_refuses_a_recording_that_is_not_the_pinned_one(monkeypatch, tmp_path):
    reference = tmp_path / "reference_3.wav"
    reference.write_bytes(tone())
    (tmp_path / "reference_3.rights.json").write_text(
        '{"consent": "yes", "commercial_use": "yes", "synthetic_voice_cleared": "yes"}')
    monkeypatch.setitem(sys.modules, "chatterbox", types.ModuleType("chatterbox"))
    monkeypatch.setitem(sys.modules, "chatterbox.tts", types.ModuleType("chatterbox.tts"))
    monkeypatch.setattr(ChatterboxEngine, "device", classmethod(lambda cls: "cuda"))
    monkeypatch.setattr(ChatterboxEngine, "reference_path",
                        staticmethod(lambda: reference))
    monkeypatch.setattr(ChatterboxEngine, "_shas", {})
    sha = tts.file_sha256(reference)

    _settings(monkeypatch, tts, voice_reference_sha256=sha)
    assert ChatterboxEngine.diagnose()[0] is True
    _settings(monkeypatch, tts, voice_reference_sha256="0" * 64)
    ready, detail = ChatterboxEngine.diagnose()
    assert ready is False and "not the pinned recording" in detail


def _verdict(sha):
    return voice_control.Verdict(True, "ready", contract=voice_control.CONTRACT_VERSION,
                                 sample_rate=24000, reference_sha256=sha)


def _check(monkeypatch, pinned, reported):
    async def fake_http(client, endpoint):
        return _verdict(reported)

    monkeypatch.setattr(voice_control, "_verify_http", fake_http)
    _settings(monkeypatch, voice_control, voice_reference_sha256=pinned,
              remote_voice_sample_rate=24000)
    endpoint = voice_control.Endpoint("http", "http://worker", "test", "why")
    return asyncio.run(voice_control.verify(endpoint))


def test_a_worker_cloning_another_recording_is_refused(monkeypatch):
    verdict = _check(monkeypatch, "a" * 64, "b" * 64)
    assert verdict.ok is False and "different recording" in verdict.detail
    assert verdict.as_dict()["reference_sha256"] == "b" * 64


def test_the_right_recording_and_an_unpinned_app_both_pass(monkeypatch):
    assert _check(monkeypatch, "a" * 64, "a" * 64).ok
    assert _check(monkeypatch, "", "b" * 64).ok


def test_a_worker_too_old_to_say_is_not_refused(monkeypatch):
    """A layer that adds quality must not subtract availability."""
    assert _check(monkeypatch, "a" * 64, "").ok


def test_health_says_whether_the_recording_is_pinned(monkeypatch):
    _settings(monkeypatch, voice_control, voice_reference_sha256="")
    assert voice_control.report()["reference"]["state"] == "unpinned"
    _settings(monkeypatch, voice_control, voice_reference_sha256="a" * 64)
    assert voice_control.report()["reference"] == {"expected": "a" * 64,
                                                   "state": "pinned"}


def test_the_worker_reports_its_recording_and_weights(monkeypatch):
    from voice_worker import register

    monkeypatch.setattr(ChatterboxEngine, "reference_sha256",
                        classmethod(lambda cls, reference=None: "c" * 64))
    monkeypatch.setattr(ChatterboxEngine, "weights_revision",
                        classmethod(lambda cls: "deadbeef"))
    who = register.identity()
    assert who["reference_sha256"] == "c" * 64
    assert who["weights_revision"] == "deadbeef"
    assert "chatterbox_version" in who


def test_the_weights_revision_is_read_from_the_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    ref = tmp_path / "hub" / "models--ResembleAI--chatterbox" / "refs" / "main"
    ref.parent.mkdir(parents=True)
    ref.write_text("0123abcd\n")
    assert ChatterboxEngine.weights_revision() == "0123abcd"


# --- pinned code ----------------------------------------------------------

def test_chatterbox_is_pinned_to_the_release_the_voice_was_judged_on():
    text = (ROOT / "requirements-chatterbox.txt").read_text()
    assert "\nchatterbox-tts==0.1.7\n" in text


def test_the_settings_are_copied_to_env_example():
    text = (ROOT / ".env.example").read_text()
    assert "CHATTERBOX_SEEDED=1" in text
    assert "VOICE_REFERENCE_FINGERPRINT" in text
    assert config.settings.chatterbox_seeded is True


# --- the recording itself -------------------------------------------------

def test_the_bank_refuses_a_recording_that_starts_in_silence():
    with pytest.raises(voice_bank.VoiceBankError, match="silence"):
        voice_bank.check_recording(tone(lead=1.3))
    voice_bank.check_recording(tone(lead=0.2))


def test_the_bank_refuses_a_quiet_or_low_rate_recording():
    with pytest.raises(voice_bank.VoiceBankError, match="quiet"):
        voice_bank.check_recording(tone(amplitude=500))
    with pytest.raises(voice_bank.VoiceBankError, match="Hz"):
        voice_bank.check_recording(tone(rate=8000))


def test_mastering_trims_and_levels_and_never_overwrites(tmp_path):
    from tools import master_reference

    source = tmp_path / "reference_3.wav"
    original = tone(lead=1.3, amplitude=900)
    source.write_bytes(original)
    assert master_reference.main([str(source)]) == 0
    assert source.read_bytes() == original
    mastered = (tmp_path / "reference_3.mastered.wav").read_bytes()
    stats = voice_bank.recording_stats(mastered)
    assert stats["lead_silence"] <= 0.15
    assert -21.5 < stats["loudness_dbfs"] < -18.5
    assert stats["peak_dbfs"] <= master_reference.PEAK_CEILING_DBFS + 0.1
    voice_bank.check_recording(mastered)
    with pytest.raises(SystemExit):
        master_reference.main([str(source), "--out", str(source)])


# --- the fingerprint ------------------------------------------------------

def test_the_fingerprint_passes_a_voice_that_holds():
    import verify_voice

    result = verify_voice.voice_match([1, 0, 0], [[0.95, 0.1, 0], [0.97, 0, 0.1]])
    assert result["match_min"] > 0.9 and result["consistency_min"] > 0.9
    assert verify_voice.judge(result) == []


def test_the_fingerprint_fails_a_voice_that_drifts():
    import verify_voice

    result = verify_voice.voice_match([1, 0, 0], [[1, 0, 0], [0.6, 0.8, 0]])
    failures = verify_voice.judge(result)
    assert any("chunk 2" in f for f in failures)
    assert any("changes between sentences" in f for f in failures)
