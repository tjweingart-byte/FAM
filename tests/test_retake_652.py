"""§652: a reading that ran twice its length is read once more, on the worker.

The owner, 11.18: "The first sentence is still being repeated twice
sometimes in episodes." §637 guarded the writer (`RepeatGuard`) and only
reported the voice. The voice now takes such a chunk again from a second
seed and keeps the take nearer the planned length. No GPU: the model is a
stub whose reading length depends on the seed it was given.
"""
from __future__ import annotations

import contextlib
import dataclasses
import math
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pace_level  # noqa: E402
import pipeline  # noqa: E402
import tts  # noqa: E402
from tts import ChatterboxEngine  # noqa: E402

RATE = 24000
OPENING = "The Eagles beat the Giants on Sunday night."  # 8 words: 3.2 s at 150


def _seconds_for(words: int, times: float) -> float:
    return words * 60.0 / 150.0 * times


def _card(monkeypatch, tmp_path, lengths: dict, **changes):
    """A model that reads for `lengths[take]` seconds - take 0 the first
    seed, take 1 the retake's - and records the seeds it was given."""
    import numpy as np

    calls = types.SimpleNamespace(seeds=[], generated=0)

    class Wav:
        def __init__(self, seconds):
            n = int(seconds * RATE)
            self.samples = (0.3 * np.sin(2 * math.pi * 220 * np.arange(n) / RATE)
                            ).astype("float32")

        def squeeze(self, axis):
            return self

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return self.samples

    class Model:
        sr = RATE
        device = "cuda"
        conds = None

        def prepare_conditionals(self, path, exaggeration=0.5):
            self.conds = ("conds", path)

        def generate(self, text, **kwargs):
            take = calls.generated
            calls.generated += 1
            return Wav(lengths[take])

    torch = types.ModuleType("torch")
    torch.inference_mode = contextlib.nullcontext
    torch.manual_seed = lambda seed: calls.seeds.append(seed)
    monkeypatch.setitem(sys.modules, "torch", torch)
    reference = tmp_path / "reference_3.wav"
    reference.write_bytes(b"RIFF" + b"\x00" * 64)
    model = Model()
    monkeypatch.setattr(ChatterboxEngine, "_model", classmethod(lambda cls: model))
    monkeypatch.setattr(ChatterboxEngine, "reference_path", staticmethod(lambda: reference))
    monkeypatch.setattr(ChatterboxEngine, "_conds", {})
    monkeypatch.setattr(ChatterboxEngine, "_shas", {})
    monkeypatch.setattr(ChatterboxEngine, "retakes", 0)
    monkeypatch.setattr(tts, "settings", dataclasses.replace(
        tts.settings, target_wpm=150.0, chatterbox_seeded=True,
        chatterbox_seed_salt="", **changes))
    monkeypatch.setattr(pace_level, "settings", tts.settings)
    return calls, reference


def _heard(pcm: bytes) -> float:
    return pace_level.voiced_seconds(pcm, RATE)


def test_a_reading_said_twice_is_taken_again_and_the_retake_kept(monkeypatch, tmp_path):
    calls, reference = _card(monkeypatch, tmp_path,
                             {0: _seconds_for(8, 2.0), 1: _seconds_for(8, 1.0)})
    pcm, _ = ChatterboxEngine()._synth_blocking(OPENING)
    assert calls.generated == 2
    assert abs(_heard(pcm) - _seconds_for(8, 1.0)) < 0.1
    assert ChatterboxEngine.retakes == 1
    # Both draws reproducible: the same sentence gives the same two seeds.
    sha = ChatterboxEngine.reference_sha256(reference)
    assert calls.seeds == [tts.chunk_seed(sha, OPENING, ""),
                           tts.chunk_seed(sha, OPENING, "\nretake")]


def test_an_ordinary_reading_is_read_once(monkeypatch, tmp_path):
    calls, _ = _card(monkeypatch, tmp_path, {0: _seconds_for(8, 1.2)})
    ChatterboxEngine()._synth_blocking(OPENING)
    assert calls.generated == 1 and ChatterboxEngine.retakes == 0


def test_a_slow_reading_is_not_a_second_one(monkeypatch, tmp_path):
    calls, _ = _card(monkeypatch, tmp_path, {0: _seconds_for(8, 1.45)})
    ChatterboxEngine()._synth_blocking(OPENING)
    assert calls.generated == 1


def test_a_retake_cut_short_is_not_swapped_in(monkeypatch, tmp_path):
    """Nearer the planned length wins; a retake as far off (cut to half)
    leaves the first take, whose words are at least all there."""
    calls, _ = _card(monkeypatch, tmp_path,
                     {0: _seconds_for(8, 2.0), 1: _seconds_for(8, 0.5)})
    pcm, _ = ChatterboxEngine()._synth_blocking(OPENING)
    assert calls.generated == 2
    assert abs(_heard(pcm) - _seconds_for(8, 2.0)) < 0.1


def test_one_retake_at_most(monkeypatch, tmp_path):
    calls, _ = _card(monkeypatch, tmp_path,
                     {0: _seconds_for(8, 2.2), 1: _seconds_for(8, 1.9)})
    pcm, _ = ChatterboxEngine()._synth_blocking(OPENING)
    assert calls.generated == 2
    assert abs(_heard(pcm) - _seconds_for(8, 1.9)) < 0.1


def test_a_short_chunk_is_never_judged(monkeypatch, tmp_path):
    calls, _ = _card(monkeypatch, tmp_path, {0: 4.0})
    ChatterboxEngine()._synth_blocking("No. Not yet.")
    assert calls.generated == 1


def test_the_retake_can_be_switched_off(monkeypatch, tmp_path):
    calls, _ = _card(monkeypatch, tmp_path, {0: _seconds_for(8, 2.0)},
                     chatterbox_retake=False)
    ChatterboxEngine()._synth_blocking(OPENING)
    assert calls.generated == 1


def test_the_worker_and_the_report_share_one_measure():
    assert pipeline.SAID_TWICE_RATIO is pace_level.SAID_TWICE_RATIO
    assert pipeline.SAID_TWICE_MIN_WORDS is pace_level.SAID_TWICE_MIN_WORDS
    assert 1.4 < pace_level.SAID_TWICE_RATIO < 2.0


def test_the_setting_is_copied_where_it_is_settled():
    import config

    assert config.settings.chatterbox_retake is True
    assert "CHATTERBOX_RETAKE=1" in (ROOT / ".env.example").read_text()
