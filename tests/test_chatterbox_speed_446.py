"""§446: Chatterbox faster on the same card, saying the same thing.

What is pinned, with no GPU and no torch:

* the fast path is off by default, and off it is exactly `model.generate`
  with the voice's six settings, after the chunk's seed;
* on, it runs only for the chatterbox-tts version it mirrors and the English
  model, and anything else falls back to the stock call with a reason;
* the worker reports which path it runs;
* every new setting is copied into `.env.example`.

With torch, transformers and chatterbox-tts 0.1.7 installed (a GPU pod, or a
scratch environment), one more test runs both loops on a small random T3 with
the same seed and requires the same speech tokens: the arithmetic and the
order of random draws are 0.1.7's.
"""
from __future__ import annotations

import dataclasses
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatterbox_speed  # noqa: E402
import config  # noqa: E402
import tts  # noqa: E402


def _set(monkeypatch, **changes):
    monkeypatch.setattr(chatterbox_speed, "settings",
                        dataclasses.replace(chatterbox_speed.settings, **changes))


class _Model:
    def __init__(self, multilingual=False, gpt=False):
        self.t3 = types.SimpleNamespace(
            hp=types.SimpleNamespace(is_multilingual=multilingual), is_gpt=gpt)
        self.calls = []

    def generate(self, text, **kwargs):
        self.calls.append(("stock", text, kwargs))
        return "stock-wav"


def test_off_by_default_and_off_is_the_stock_call(monkeypatch):
    assert config.Settings().chatterbox_fast is False
    model = _Model()
    _set(monkeypatch, chatterbox_fast=False)
    assert chatterbox_speed.speak(model, "Hello.", tts.CHATTERBOX_GENERATION) == "stock-wav"
    assert model.calls == [("stock", "Hello.", tts.CHATTERBOX_GENERATION)]


def test_on_it_runs_only_for_the_version_it_mirrors(monkeypatch):
    _set(monkeypatch, chatterbox_fast=True)
    monkeypatch.setattr(chatterbox_speed, "_version", lambda: "0.1.8")
    model = _Model()
    assert chatterbox_speed.speak(model, "Hi.", tts.CHATTERBOX_GENERATION) == "stock-wav"
    ok, why = chatterbox_speed.applies(model)
    assert not ok and "0.1.8" in why and chatterbox_speed.MIRRORS in why


@pytest.mark.parametrize("kind,word", [("multilingual", "attention"), ("gpt", "Turbo")])
def test_models_it_does_not_mirror_fall_back(monkeypatch, kind, word):
    _set(monkeypatch, chatterbox_fast=True)
    monkeypatch.setattr(chatterbox_speed, "_version", lambda: chatterbox_speed.MIRRORS)
    model = _Model(multilingual=kind == "multilingual", gpt=kind == "gpt")
    ok, why = chatterbox_speed.applies(model)
    assert not ok and word in why
    assert chatterbox_speed.speak(model, "Hi.", tts.CHATTERBOX_GENERATION) == "stock-wav"


def test_on_and_applicable_it_takes_the_fast_path_with_the_same_settings(monkeypatch):
    _set(monkeypatch, chatterbox_fast=True)
    monkeypatch.setattr(chatterbox_speed, "_version", lambda: chatterbox_speed.MIRRORS)
    seen = []
    monkeypatch.setattr(chatterbox_speed, "generate",
                        lambda model, text, **kw: seen.append((text, kw)) or "fast-wav")
    model = _Model()
    assert chatterbox_speed.speak(model, "Hi.", tts.CHATTERBOX_GENERATION) == "fast-wav"
    assert seen == [("Hi.", tts.CHATTERBOX_GENERATION)] and model.calls == []


def test_the_engine_seeds_then_speaks_through_the_one_switch(monkeypatch, tmp_path):
    """The seed (§176) is set before the call, whichever path speaks."""
    order = []
    torch = types.ModuleType("torch")

    import contextlib

    torch.inference_mode = contextlib.nullcontext
    torch.manual_seed = lambda seed: order.append(("seed", seed))
    monkeypatch.setitem(sys.modules, "torch", torch)
    import numpy as np

    class Wav:
        def squeeze(self, _):
            return self

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return np.zeros(24, dtype="float32")

    model = types.SimpleNamespace(sr=24000, device="cuda", conds=("c",),
                                  prepare_conditionals=lambda *a, **k: None)
    monkeypatch.setattr(chatterbox_speed, "speak",
                        lambda m, text, gen: order.append(("speak", text, gen)) or Wav())
    reference = tmp_path / "reference_3.wav"
    reference.write_bytes(b"RIFF0000WAVE")
    monkeypatch.setattr(tts.ChatterboxEngine, "_model", classmethod(lambda cls: model))
    monkeypatch.setattr(tts.ChatterboxEngine, "reference_path", staticmethod(lambda: reference))
    monkeypatch.setattr(tts.ChatterboxEngine, "_conds", {})
    monkeypatch.setattr(tts.ChatterboxEngine, "_shas", {})
    tts.ChatterboxEngine()._synth_blocking("One sentence.")
    assert [o[0] for o in order] == ["seed", "speak"]
    assert order[1][2] is tts.CHATTERBOX_GENERATION


def test_the_worker_says_which_path_it_runs(monkeypatch):
    _set(monkeypatch, chatterbox_fast=True, chatterbox_bf16=False, chatterbox_compile=True)
    from voice_worker import register

    speed = register.identity()["speed"]
    assert speed["fast"] is True and speed["compile"] is True and speed["bf16"] is False
    assert speed["mirrors"] == chatterbox_speed.MIRRORS


def test_every_speed_setting_is_copied_into_env_example():
    text = (ROOT / ".env.example").read_text()
    for name, value in (("CHATTERBOX_FAST", "0"), ("CHATTERBOX_BF16", "0"),
                        ("CHATTERBOX_COMPILE", "0"), ("CHATTERBOX_EOS_CHECK_EVERY", "16")):
        assert f"\n{name}={value}\n" in text, name
    s = config.Settings()
    assert (s.chatterbox_fast, s.chatterbox_bf16, s.chatterbox_compile,
            s.chatterbox_eos_check_every) == (False, False, False, 16)


# --- the arithmetic, where torch is installed --------------------------------

def _tiny_t3():
    torch = pytest.importorskip("torch")
    pytest.importorskip("transformers")
    pytest.importorskip("chatterbox")
    from chatterbox.models.t3 import llama_configs
    from chatterbox.models.t3.modules.cond_enc import T3Cond
    from chatterbox.models.t3.modules.t3_config import T3Config
    from chatterbox.models.t3.t3 import T3

    # The conditioning encoder is built for 1024 wide, so the width stays and
    # the depth goes: two layers instead of thirty.
    tiny = dict(llama_configs.LLAMA_520M_CONFIG_DICT, intermediate_size=1024,
                num_hidden_layers=2)
    llama_configs.LLAMA_CONFIGS["Tiny_test"] = tiny
    hp = T3Config.english_only()
    hp.llama_config_name = "Tiny_test"
    hp.speech_cond_prompt_len = 8
    torch.manual_seed(0)
    t3 = T3(hp).eval()
    cond = T3Cond(speaker_emb=torch.randn(1, hp.speaker_embed_size),
                  cond_prompt_speech_tokens=torch.randint(0, 6561, (1, 8)),
                  emotion_adv=0.5 * torch.ones(1, 1, 1))
    return torch, t3, hp, cond


@pytest.mark.parametrize("every", [1, 4, 16])
def test_the_fast_loop_draws_the_same_tokens_as_chatterbox(monkeypatch, every):
    torch, t3, hp, cond = _tiny_t3()
    text = torch.tensor([[hp.start_text_token, 5, 9, 12, 40, 3, hp.stop_text_token]] * 2)
    kwargs = dict(t3_cond=cond, text_tokens=text, max_new_tokens=60, temperature=0.8,
                  cfg_weight=0.5, repetition_penalty=1.2, min_p=0.05, top_p=1.0)
    for seed in (1, 2, 3, 4, 5):
        torch.manual_seed(seed)
        with torch.inference_mode():
            stock = t3.inference(**kwargs)
        torch.manual_seed(seed)
        with torch.inference_mode():
            fast = chatterbox_speed.t3_inference(t3, check_every=every, **kwargs)
        assert torch.equal(stock, fast), f"seed {seed}: {stock.tolist()} != {fast.tolist()}"


def test_the_fast_loop_stops_at_the_first_stop_token(monkeypatch):
    """A stop token drawn mid-interval ends the chunk there: later draws are discarded."""
    torch, t3, hp, cond = _tiny_t3()
    # Make the stop token overwhelmingly likely from the fifth step on.
    real = t3.speech_head.forward
    steps = {"n": 0}

    def head(h):
        out = real(h)
        steps["n"] += 1
        if steps["n"] >= 5:
            out[..., hp.stop_speech_token] += 1e4
        return out

    monkeypatch.setattr(t3.speech_head, "forward", head)
    text = torch.tensor([[hp.start_text_token, 5, 9, hp.stop_text_token]] * 2)
    torch.manual_seed(7)
    with torch.inference_mode():
        fast = chatterbox_speed.t3_inference(
            t3, t3_cond=cond, text_tokens=text, max_new_tokens=50, temperature=0.8,
            cfg_weight=0.5, repetition_penalty=1.2, min_p=0.05, top_p=1.0, check_every=16)
    tokens = fast.view(-1).tolist()
    assert tokens[-1] == hp.stop_speech_token
    assert tokens.count(hp.stop_speech_token) == 1
    assert len(tokens) == 5
