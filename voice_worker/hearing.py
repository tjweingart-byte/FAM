"""What the voice actually said, heard back as sounds (PROBLEMS.md §325).

Every other pronunciation check compares text with text: a model's
respelling with Wikipedia's, a lexicon entry with a dictionary's. None of
them says what Chatterbox did with it, and Chatterbox is the only thing a
listener hears. This module is the other half of the round trip: the worker
synthesises a word, and a phoneme recogniser listens to that audio and writes
down the sounds it heard, in IPA. `voice_check.py` (on the app) compares them
with what the lexicon or the dictionary meant.

**A phoneme recogniser, not a transcriber.** Whisper writes words, and for a
name it does not know it writes the nearest word it does - "Sagapolutele"
comes back as whatever spelling it guesses, which says nothing about the
sounds. `facebook/wav2vec2-lv-60-espeak-cv-ft` (Apache-2.0) is trained to
write the sounds themselves, in espeak's IPA, in any language, with no
dictionary to round them to a word. It carries no stress marks; the check
compares sounds only.

**Loaded on first use, on the card the voice is on.** ~1.2 GB of weights,
fetched once into `HF_HOME` (the network volume on RunPod, as Chatterbox's
are) and kept resident after. Nothing an episode does reaches it: only a
request that asks `hear` loads it, and only /admin sends those.

The tokenizer is not used to decode: transformers' phoneme tokenizer wants
the `phonemizer` package and espeak installed just to start, for encoding
text this module never encodes. CTC decoding is done here from the model's
own `vocab.json` - argmax, collapse repeats, drop the blank.
"""
from __future__ import annotations

import json
import logging
import os
import threading

log = logging.getLogger(__name__)

#: The recogniser. Overridable for a model with the same CTC-over-IPA shape.
MODEL = (os.environ.get("VOICE_HEARING_MODEL")
         or "facebook/wav2vec2-lv-60-espeak-cv-ft").strip()
#: The rate the recogniser was trained at.
RATE = 16000
#: Tokens that are not sounds.
_SPECIAL = frozenset({"<pad>", "<s>", "</s>", "<unk>", "|", ""})

_LOCK = threading.Lock()
_LOADED: dict = {}


def available() -> tuple[bool, str]:
    """Whether this machine can hear at all - the packages, not the weights."""
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        from huggingface_hub import hf_hub_download  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"cannot hear: {type(exc).__name__}: {exc}"
    return True, MODEL


def _load() -> dict:
    with _LOCK:
        if _LOADED:
            return _LOADED
        import torch
        from huggingface_hub import hf_hub_download
        from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForCTC

        device = "cuda" if torch.cuda.is_available() else "cpu"
        with open(hf_hub_download(MODEL, "vocab.json"), encoding="utf-8") as fh:
            vocab = json.load(fh)
        model = Wav2Vec2ForCTC.from_pretrained(MODEL).to(device).eval()
        _LOADED.update(
            model=model, device=device,
            features=Wav2Vec2FeatureExtractor.from_pretrained(MODEL),
            tokens={int(i): t for t, i in vocab.items()},
            blank=int(model.config.pad_token_id or 0))
        log.info("hearing: %s loaded on %s", MODEL, device)
        return _LOADED


def _resample(samples, rate: int):
    """`samples` (float32 numpy) at `RATE`."""
    import numpy as np

    if rate == RATE or not len(samples):
        return samples
    try:
        import torch
        import torchaudio.functional as F

        return F.resample(torch.from_numpy(samples), rate, RATE).numpy()
    except Exception:  # noqa: BLE001 - linear is coarse but never absent
        n = int(round(len(samples) * RATE / float(rate)))
        return np.interp(np.linspace(0, len(samples) - 1, n),
                         np.arange(len(samples)), samples).astype("float32")


def decode(ids, tokens: dict, blank: int) -> list:
    """CTC: repeats collapsed, the blank and non-sounds dropped."""
    out, last = [], None
    for i in ids:
        i = int(i)
        if i != last and i != blank:
            token = tokens.get(i, "")
            if token not in _SPECIAL:
                out.append(token)
        last = i
    return out


def recognise(pcm: bytes, rate: int) -> str:
    """The sounds in 16-bit mono `pcm`, as IPA with a space between phones.
    Raises if the recogniser cannot be loaded."""
    import numpy as np
    import torch

    loaded = _load()
    samples = np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0
    samples = _resample(samples, int(rate))
    inputs = loaded["features"](samples, sampling_rate=RATE, return_tensors="pt")
    with torch.inference_mode():
        logits = loaded["model"](inputs.input_values.to(loaded["device"])).logits
    ids = torch.argmax(logits, dim=-1)[0].tolist()
    return " ".join(decode(ids, loaded["tokens"], loaded["blank"]))
