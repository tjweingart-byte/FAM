"""Stock Chatterbox against the fast loop, on this card, same seeds (§446).

    python tools/bench_voice_speed.py                 # 5 sentences, 3 rounds
    python tools/bench_voice_speed.py --rounds 5 --bf16 --compile
    python tools/bench_voice_speed.py --save out/     # .wav pairs to listen to

Run on a GPU pod with the voice volume attached - the same place
`verify_voice.py` runs. For each sentence it seeds the chunk exactly as
production does (`tts.chunk_seed`), runs chatterbox-tts's own
`T3.inference` and `chatterbox_speed.t3_inference` on the same inputs, and
reports:

* **tokens identical**: how many sentences drew the same speech tokens. The
  fast loop uses SDPA attention where the stock loop falls back to eager, so a
  token whose top candidates are a rounding apart can differ; with bf16 on,
  more will. Identical tokens mean identical audio (S3Gen is untouched).
* **T3 time** for each loop and the speed-up, and **realtime factor** for the
  whole chunk (tokens + S3Gen + watermark), stock and fast.

Then listen: `--save` writes each sentence both ways. Before turning
`CHATTERBOX_FAST=1` on for listeners, also run
`CHATTERBOX_FAST=1 python verify_voice.py --fingerprint`.

Reads and writes nothing else, installs nothing, calls no network.
"""
from __future__ import annotations

import argparse
import dataclasses
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _sync(torch):
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--rounds", type=int, default=3, help="timed rounds per sentence")
    p.add_argument("--bf16", action="store_true", help="fast loop with CHATTERBOX_BF16")
    p.add_argument("--compile", action="store_true", help="fast loop with CHATTERBOX_COMPILE")
    p.add_argument("--save", help="folder for stock/fast .wav pairs")
    args = p.parse_args(argv)

    import torch

    import chatterbox_speed
    import config
    import tts
    from verify_voice import FINGERPRINT_SENTENCES

    fast_settings = dataclasses.replace(
        config.settings, chatterbox_fast=True, chatterbox_bf16=args.bf16,
        chatterbox_compile=args.compile)
    chatterbox_speed.settings = fast_settings
    ok, reason = tts.ChatterboxEngine.diagnose()
    if not ok:
        print(f"cannot run: {reason}")
        return 2
    engine = tts.ChatterboxEngine()
    model = engine._model()
    applies, why = chatterbox_speed.applies(model)
    if not applies:
        print(f"the fast loop does not apply here: {why}")
        return 2
    reference = engine.reference_path()
    sha = engine.reference_sha256(reference)
    gen = dict(tts.CHATTERBOX_GENERATION)
    out = pathlib.Path(args.save) if args.save else None
    if out:
        out.mkdir(parents=True, exist_ok=True)

    from chatterbox.tts import punc_norm
    import torch.nn.functional as F

    def tokens_in(text):
        t = model.tokenizer.text_to_tokens(punc_norm(text)).to(model.device)
        t = torch.cat([t, t], dim=0)
        t = F.pad(t, (1, 0), value=model.t3.hp.start_text_token)
        return F.pad(t, (0, 1), value=model.t3.hp.stop_text_token)

    def run_t3(fast, text):
        torch.manual_seed(tts.chunk_seed(sha, text, config.settings.chatterbox_seed_salt))
        kwargs = dict(t3_cond=model.conds.t3, text_tokens=tokens_in(text),
                      max_new_tokens=chatterbox_speed.MAX_NEW_TOKENS,
                      temperature=gen["temperature"], cfg_weight=gen["cfg_weight"],
                      repetition_penalty=gen["repetition_penalty"],
                      min_p=gen["min_p"], top_p=gen["top_p"])
        _sync(torch)
        start = time.perf_counter()
        with torch.inference_mode():
            if fast:
                toks = chatterbox_speed.t3_inference(model.t3, **kwargs)
            else:
                toks = model.t3.inference(**kwargs)
        _sync(torch)
        return toks.detach().cpu(), time.perf_counter() - start

    def run_chunk(fast, text):
        torch.manual_seed(tts.chunk_seed(sha, text, config.settings.chatterbox_seed_salt))
        _sync(torch)
        start = time.perf_counter()
        with torch.inference_mode():
            wav = (chatterbox_speed.generate(model, text, **gen) if fast
                   else model.generate(text, **gen))
        _sync(torch)
        return wav, time.perf_counter() - start

    with torch.inference_mode():
        engine._condition(model, reference, sha)
    # One untimed pass each: loads kernels, and pays the compile if asked.
    run_chunk(False, "Warming up.")
    run_chunk(True, "Warming up.")

    same = 0
    t3_stock, t3_fast, rtf_stock, rtf_fast = [], [], [], []
    print(f"card: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu'}")
    print(f"fast loop: bf16={args.bf16} compile={args.compile} "
          f"eos_check_every={fast_settings.chatterbox_eos_check_every}\n")
    for i, text in enumerate(FINGERPRINT_SENTENCES):
        a, _ = run_t3(False, text)
        b, _ = run_t3(True, text)
        identical = a.shape == b.shape and bool((a == b).all())
        same += identical
        for _ in range(args.rounds):
            t3_stock.append(run_t3(False, text)[1])
            t3_fast.append(run_t3(True, text)[1])
            wav_s, sec_s = run_chunk(False, text)
            wav_f, sec_f = run_chunk(True, text)
            audio = wav_s.shape[-1] / model.sr
            rtf_stock.append(audio / sec_s)
            rtf_fast.append(wav_f.shape[-1] / model.sr / sec_f)
        print(f"{i + 1}. tokens {'identical' if identical else 'DIFFER'} "
              f"({a.shape[-1]} vs {b.shape[-1]})  {text[:60]}")
        if out:
            import numpy as np
            import wave

            for name, wav in (("stock", wav_s), ("fast", wav_f)):
                pcm = (np.clip(wav.squeeze(0).numpy(), -1, 1) * 32767).astype("<i2")
                with wave.open(str(out / f"{i + 1}-{name}.wav"), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(model.sr)
                    w.writeframes(pcm.tobytes())

    med = statistics.median
    print(f"\ntokens identical: {same} of {len(FINGERPRINT_SENTENCES)} sentences")
    print(f"T3 (speech tokens): stock {med(t3_stock):.2f} s, fast {med(t3_fast):.2f} s, "
          f"{med(t3_stock) / med(t3_fast):.2f}x faster")
    print(f"whole chunk realtime factor: stock {med(rtf_stock):.2f}x, "
          f"fast {med(rtf_fast):.2f}x")
    print("\nNext: CHATTERBOX_FAST=1 python verify_voice.py --fingerprint, and listen"
          + (f" to {out}/" if out else " (--save writes the pairs)") + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
