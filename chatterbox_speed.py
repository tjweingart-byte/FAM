"""Chatterbox, faster on the same card, saying the same thing.

PROBLEMS.md §446. One RTX 4090 makes ~4.6 seconds of audio a second (§75),
and at a million listeners that rate is most of the bill. Most of the time
goes into the autoregressive half (T3: a 0.5B Llama writing 25 speech tokens a
second of audio, one at a time), and chatterbox-tts 0.1.7's own loop
(`T3.inference`) spends much of it on work whose results it throws away:

* **It asks every step for every layer's attention map and hidden state**
  (`output_attentions=True, output_hidden_states=True`). The English model never
  reads either - only the multilingual one feeds attentions to its alignment
  analyzer - and asking for attentions forces transformers off SDPA onto eager
  attention, which materialises a (2, 16, n, n) matrix in all 30 layers on
  every token.
* **It waits for the CPU after every token** (`if next_token == stop`, an
  implicit `.item()`), so the GPU idles between steps.
* **It rebuilds its wrapper and a tqdm bar on every chunk.**

`generate()` here is ChatterboxTTS.generate with that loop replaced by
`t3_inference()`, which does the same arithmetic in the same order - the same
CFG mix, repetition penalty, temperature, min-p, top-p and `multinomial` draws,
so a seeded chunk (§176) draws the same random numbers - and differs in four
ways only:

1. the transformer is asked for its last hidden state alone, so attention
   stays on SDPA. That is the one numerical change: SDPA and eager attention
   agree to float rounding, not to the bit, so a token whose top candidates are
   a rounding apart can come out differently. `verify_voice.py --fingerprint`
   and `tools/bench_voice_speed.py` are how that is checked on a card;
2. the end of speech is checked every `CHATTERBOX_EOS_CHECK_EVERY` tokens
   instead of every token. Tokens drawn after the stop token are discarded, and
   every token before it is drawn exactly as before - the same steps, the same
   random numbers - so the audio is the same; a chunk costs at most a few
   wasted steps at its end;
3. optional bf16 for the transformer (`CHATTERBOX_BF16`), which changes the
   numbers more and must be listened to;
4. optional `torch.compile` of the transformer (`CHATTERBOX_COMPILE`), cached
   on disk with `TORCHINDUCTOR_CACHE_DIR` so a cold worker does not pay it.
   The worker's boot synthesis (`voice_worker.synth._load`) is the first chunk,
   so the compile happens there, not on a listener's chunk.

The generation settings are untouched: `tts.CHATTERBOX_GENERATION` is the
voice. S3Gen (the flow-matching decoder and vocoder) is untouched too; it is
already parallel and is the next thing to measure, not to guess at.

Nothing here runs unless `CHATTERBOX_FAST=1`, and it only ever runs on the
chatterbox-tts version it mirrors (`MIRRORS`) with the English model: anything
else falls back to the stock `model.generate`, with a log line saying why.
Every function imports torch late, so this module imports on a machine with no
torch at all - the build container, CI.
"""
from __future__ import annotations

import functools
import logging
import os
from typing import Optional

from config import settings

log = logging.getLogger(__name__)

#: The chatterbox-tts release whose `ChatterboxTTS.generate` and
#: `T3.inference` this file reproduces. A different version may have changed
#: either, and a mirror of the wrong code is worse than the slow original.
MIRRORS = "0.1.7"

#: `generate()`'s own limit on speech tokens per chunk (0.1.7 hard-codes it).
MAX_NEW_TOKENS = 1000

#: Codes at or above this are not speech (0.1.7 filters them the same way).
SPEECH_VOCAB = 6561

_why_not: dict = {}


@functools.lru_cache(maxsize=1)
def _version() -> str:
    """The installed chatterbox-tts, read once: `applies()` asks per chunk."""
    try:
        from importlib.metadata import version

        return version("chatterbox-tts")
    except Exception:  # noqa: BLE001 - no package is an answer, not an error
        return ""


def applies(model) -> tuple[bool, str]:
    """Whether the fast path may speak for this model, and why not if not."""
    if not settings.chatterbox_fast:
        return False, "CHATTERBOX_FAST is off"
    version = _version()
    if version != MIRRORS:
        return False, f"chatterbox-tts {version or 'not installed'} is not {MIRRORS}"
    t3 = getattr(model, "t3", None)
    hp = getattr(t3, "hp", None)
    if t3 is None or hp is None:
        return False, "the model has no T3 stage"
    if getattr(hp, "is_multilingual", False):
        # The multilingual model reads every step's attentions; this loop
        # does not compute them.
        return False, "the multilingual model needs attention maps"
    if getattr(t3, "is_gpt", False):
        return False, "the GPT-2 backbone (Turbo) has its own loop"
    return True, "fast T3 loop"


def report(model=None) -> dict:
    """What a worker says about its speed path, for its identity."""
    out = {
        "fast": bool(settings.chatterbox_fast),
        "bf16": bool(settings.chatterbox_bf16),
        "compile": bool(settings.chatterbox_compile),
        "eos_check_every": int(settings.chatterbox_eos_check_every),
        "mirrors": MIRRORS,
    }
    if model is not None:
        out["applies"], out["detail"] = applies(model)
    return out


def _note_fallback(reason: str) -> None:
    if reason not in _why_not:
        _why_not[reason] = True
        log.warning("chatterbox fast path not used: %s", reason)


def _transformer(t3):
    """The transformer to call: compiled once if asked, else the module."""
    if not settings.chatterbox_compile:
        return t3.tfmr
    compiled = getattr(t3, "_fam_compiled", None)
    if compiled is None:
        import torch

        # Dynamic shapes: the cache grows a token a step, and a chunk's
        # prompt length varies. Recompiling per length would be worse than
        # not compiling at all.
        if not os.environ.get("TORCHINDUCTOR_CACHE_DIR"):
            log.warning("CHATTERBOX_COMPILE without TORCHINDUCTOR_CACHE_DIR: "
                        "every cold worker compiles again (20-60 s)")
        compiled = torch.compile(t3.tfmr, dynamic=True)
        t3._fam_compiled = compiled
    return compiled


def _autocast(device):
    import contextlib

    import torch

    if settings.chatterbox_bf16 and str(device).startswith("cuda"):
        return torch.autocast(device_type="cuda", dtype=torch.bfloat16)
    return contextlib.nullcontext()


def _step(t3, tfmr, inputs_embeds, past):
    """One transformer pass: logits for the last position, and the cache.

    0.1.7 computed `speech_head(hidden_states[-1])` with every layer's hidden
    states and attentions requested; `hidden_states[-1]` is the final-norm
    output, which is `last_hidden_state`, so the logits are the same numbers
    without either request.
    """
    with _autocast(inputs_embeds.device):
        out = tfmr(
            inputs_embeds=inputs_embeds,
            past_key_values=past,
            use_cache=True,
            return_dict=True,
        )
    hidden = out.last_hidden_state
    if hidden.dtype != inputs_embeds.dtype:
        hidden = hidden.to(inputs_embeds.dtype)
    return t3.speech_head(hidden), out.past_key_values


def t3_inference(t3, *, t3_cond, text_tokens, max_new_tokens: int,
                 temperature: float, cfg_weight: float, repetition_penalty: float,
                 min_p: float, top_p: float, check_every: Optional[int] = None):
    """`T3.inference` (0.1.7) for the English model, without its waste.

    Returns the predicted speech tokens, (1, n), ending with the stop token
    when one was drawn - exactly what 0.1.7 returns.
    """
    import torch
    from transformers.generation.logits_process import (
        MinPLogitsWarper,
        RepetitionPenaltyLogitsProcessor,
        TopPLogitsWarper,
    )
    from chatterbox.models.t3.t3 import _ensure_BOT_EOT

    every = max(1, int(check_every or settings.chatterbox_eos_check_every or 1))
    hp = t3.hp
    _ensure_BOT_EOT(text_tokens, hp)
    text_tokens = torch.atleast_2d(text_tokens).to(dtype=torch.long, device=t3.device)
    initial_speech_tokens = hp.start_speech_token * torch.ones_like(text_tokens[:, :1])
    embeds, _len_cond = t3.prepare_input_embeds(
        t3_cond=t3_cond,
        text_tokens=text_tokens,
        speech_tokens=initial_speech_tokens,
        cfg_weight=cfg_weight,
    )
    device = embeds.device
    tfmr = _transformer(t3)

    bos_token = torch.tensor([[hp.start_speech_token]], dtype=torch.long, device=device)
    bos_embed = t3.speech_emb(bos_token)
    bos_embed = bos_embed + t3.speech_pos_emb.get_fixed_embedding(0)
    bos_embed = torch.cat([bos_embed, bos_embed])  # the CFG pair, as 0.1.7
    inputs_embeds = torch.cat([embeds, bos_embed], dim=1)

    generated_ids = bos_token.clone()
    predicted = []
    top_p_warper = TopPLogitsWarper(top_p=top_p)
    min_p_warper = MinPLogitsWarper(min_p=min_p)
    repetition = RepetitionPenaltyLogitsProcessor(penalty=float(repetition_penalty))
    stop = hp.stop_speech_token

    logits, past = _step(t3, tfmr, inputs_embeds, None)
    checked = 0
    for i in range(max_new_tokens):
        logits_step = logits[:, -1, :]
        cond = logits_step[0:1, :]
        uncond = logits_step[1:2, :]
        cfg = torch.as_tensor(cfg_weight, device=cond.device, dtype=cond.dtype)
        mixed = cond + cfg * (cond - uncond)

        ids_for_proc = generated_ids[:1, ...]
        mixed = repetition(ids_for_proc, mixed)
        if temperature != 1.0:
            mixed = mixed / temperature
        mixed = min_p_warper(ids_for_proc, mixed)
        mixed = top_p_warper(ids_for_proc, mixed)

        probs = torch.softmax(mixed, dim=-1)
        next_token = torch.multinomial(probs, num_samples=1)
        predicted.append(next_token)
        generated_ids = torch.cat([generated_ids, next_token], dim=1)

        # The only host sync: every `every` tokens, and on the last step.
        if (i + 1) % every == 0 or i + 1 == max_new_tokens:
            recent = torch.cat(predicted[checked:], dim=1)
            hits = (recent.view(-1) == stop).nonzero()
            if hits.numel():
                first = checked + int(hits[0, 0])
                del predicted[first + 1:]
                break
            checked = len(predicted)

        next_embed = t3.speech_emb(next_token)
        next_embed = next_embed + t3.speech_pos_emb.get_fixed_embedding(i + 1)
        next_embed = torch.cat([next_embed, next_embed])
        logits, past = _step(t3, tfmr, next_embed, past)

    return torch.cat(predicted, dim=1)


def generate(model, text: str, *, repetition_penalty=1.2, min_p=0.05, top_p=1.0,
             exaggeration=0.5, cfg_weight=0.5, temperature=0.8):
    """`ChatterboxTTS.generate` (0.1.7) with the fast T3 loop.

    The caller has already prepared the conditionals (`tts._condition`); like
    0.1.7, this refuses to run without them rather than read a recording.
    """
    import torch
    import torch.nn.functional as F
    from chatterbox.models.s3tokenizer import drop_invalid_tokens
    from chatterbox.models.t3.modules.cond_enc import T3Cond
    from chatterbox.tts import punc_norm

    assert model.conds is not None, "prepare_conditionals first"
    if exaggeration != model.conds.t3.emotion_adv[0, 0, 0]:
        held = model.conds.t3
        model.conds.t3 = T3Cond(
            speaker_emb=held.speaker_emb,
            cond_prompt_speech_tokens=held.cond_prompt_speech_tokens,
            emotion_adv=exaggeration * torch.ones(1, 1, 1),
        ).to(device=model.device)

    text = punc_norm(text)
    text_tokens = model.tokenizer.text_to_tokens(text).to(model.device)
    if cfg_weight > 0.0:
        text_tokens = torch.cat([text_tokens, text_tokens], dim=0)
    sot = model.t3.hp.start_text_token
    eot = model.t3.hp.stop_text_token
    text_tokens = F.pad(text_tokens, (1, 0), value=sot)
    text_tokens = F.pad(text_tokens, (0, 1), value=eot)

    with torch.inference_mode():
        speech_tokens = t3_inference(
            model.t3,
            t3_cond=model.conds.t3,
            text_tokens=text_tokens,
            max_new_tokens=MAX_NEW_TOKENS,
            temperature=temperature,
            cfg_weight=cfg_weight,
            repetition_penalty=repetition_penalty,
            min_p=min_p,
            top_p=top_p,
        )
        speech_tokens = speech_tokens[0]
        speech_tokens = drop_invalid_tokens(speech_tokens)
        speech_tokens = speech_tokens[speech_tokens < SPEECH_VOCAB]
        speech_tokens = speech_tokens.to(model.device)
        wav, _ = model.s3gen.inference(speech_tokens=speech_tokens, ref_dict=model.conds.gen)
        wav = wav.squeeze(0).detach().cpu().numpy()
        watermarked = model.watermarker.apply_watermark(wav, sample_rate=model.sr)
    return torch.from_numpy(watermarked).unsqueeze(0)


def speak(model, text: str, generation: dict):
    """The one call `ChatterboxEngine` makes: fast when it applies, else stock."""
    ok, reason = applies(model)
    if not ok:
        if settings.chatterbox_fast:
            _note_fallback(reason)
        return model.generate(text, **generation)
    return generate(model, text, **generation)
