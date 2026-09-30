"""Prove the voice actually works on this machine.

    python verify_voice.py
    python verify_voice.py --save     # also write a .wav to listen to
    python verify_voice.py --fingerprint   # does every chunk sound like the voice?

Synthesises one sentence with every voice the server would offer and reports
the duration, sample rate and how fast it ran. Writes nothing except the
optional sample.

This exists because the production engine cannot be exercised on every machine
that builds FAM - Chatterbox needs a GPU and a reference recording, and the
build container has neither - so this is the check that closes that gap on a
machine that does. It reports what it finds and does not install anything.

It is engine-agnostic on purpose: it asks `list_voices()` what the server would
serve rather than naming an engine, so it keeps working when the engine
changes. It said "piper" everywhere until Piper was removed, which is exactly
the coupling that made it need rewriting rather than reading.
"""
from __future__ import annotations

import asyncio
import struct
import sys
import time

import voice_store
from audio_utils import pcm_duration
from tts import engine_for_voice, engine_report, list_voices, production_engines

SENTENCE = (
    "This is a test of the voice this app speaks with. "
    "If this sounds like a person rather than a robot, it is working."
)

#: Below this and an episode starves: the listener hears gaps, because
#: synthesis is not keeping ahead of playback.
REALTIME_FLOOR = 1.0

# ---------------------------------------------------------------- fingerprint
#
# §176: "sometimes it sounds good, and sometimes it has a southern twang" was a
# judgement made by ear, one episode at a time, which is how a drifting voice
# goes unnoticed until a listener says so. `--fingerprint` turns it into two
# numbers. Each sentence below is spoken as its own chunk - which is how an
# episode is spoken - and Chatterbox's own speaker encoder embeds each one and
# the reference recording:
#
# * **match**: how close each chunk is to the recording. Low means the worker
#   is cloning a different file, or the model has wandered off the voice.
# * **consistency**: how close the chunks are to each other. Low means the
#   voice changes from sentence to sentence - the twang that comes and goes.
#
# The floors are first estimates, not measurements: nobody has run this on the
# card yet. Run it once on a voice that sounds right, read the numbers, and
# move the floors (or pass --match-floor / --consistency-floor) to just under
# them - after that a pod swap, a new recording or a package bump that changes
# the voice fails here before a listener hears it.
FINGERPRINT_SENTENCES = (
    "The committee voted on Tuesday to delay the bridge repairs until spring.",
    "Nobody expected the smallest team in the league to win four in a row.",
    "Interest rates rose again, and mortgage applications fell for a third month.",
    "The telescope found water vapour above the ice of a moon around Saturn.",
    "She sold the company for a dollar, and kept the name.",
)
MATCH_FLOOR = 0.80
CONSISTENCY_FLOOR = 0.85


def voice_match(reference, chunks) -> dict:
    """Cosine similarities between speaker embeddings, and what they mean.

    `reference` is one embedding, `chunks` one per spoken sentence; both are
    plain sequences of floats, so this is testable with no model at all.
    """
    import numpy as np

    def unit(v):
        v = np.asarray(v, dtype=np.float64).ravel()
        n = np.linalg.norm(v)
        return v / n if n else v

    ref = unit(reference)
    got = [unit(c) for c in chunks]
    to_reference = [float(ref @ c) for c in got]
    between = [float(a @ b) for i, a in enumerate(got) for b in got[i + 1:]]
    return {
        "match_min": min(to_reference) if to_reference else 0.0,
        "match_mean": sum(to_reference) / len(to_reference) if to_reference else 0.0,
        "consistency_min": min(between) if between else 1.0,
        "per_chunk": to_reference,
    }


def judge(result: dict, match_floor: float = MATCH_FLOOR,
          consistency_floor: float = CONSISTENCY_FLOOR) -> list[str]:
    """The sentences that fail, empty when the voice holds."""
    failures = []
    if result["match_min"] < match_floor:
        worst = result["per_chunk"].index(result["match_min"])
        failures.append(
            f"chunk {worst + 1} is {result['match_min']:.3f} from the recording "
            f"(floor {match_floor}): the voice left the reference")
    if result["consistency_min"] < consistency_floor:
        failures.append(
            f"two chunks are only {result['consistency_min']:.3f} alike "
            f"(floor {consistency_floor}): the voice changes between sentences")
    return failures


def _floor(flag: str, default: float) -> float:
    args = sys.argv[1:]
    if flag in args:
        try:
            return float(args[args.index(flag) + 1])
        except (IndexError, ValueError):
            raise SystemExit(f"{flag} needs a number, e.g. {flag} {default}")
    return default


async def fingerprint() -> int:
    """Speak the fixed sentences in the default voice and measure the match."""
    import numpy as np

    from tts import ChatterboxEngine

    ready, detail = ChatterboxEngine.diagnose()
    if not ready:
        print(f"Chatterbox cannot run here: {detail}")
        print("Run this on the GPU worker, where the voice is spoken.")
        return 1
    import librosa

    engine = ChatterboxEngine()
    reference = ChatterboxEngine.reference_path()
    print(f"Voice      {reference.name}  sha256 "
          f"{ChatterboxEngine.reference_sha256(reference)[:16]}")
    print(f"Weights    {ChatterboxEngine.weights_revision() or 'unknown'}")
    encoder = ChatterboxEngine._model().ve
    rate_in = 16000

    def embed(samples, rate):
        wav = librosa.resample(np.asarray(samples, dtype=np.float32),
                               orig_sr=rate, target_sr=rate_in)
        return encoder.embeds_from_wavs([wav], sample_rate=rate_in)[0]

    ref_wav, _ = librosa.load(str(reference), sr=rate_in)
    ref_embedding = embed(ref_wav, rate_in)
    chunks = []
    for sentence in FINGERPRINT_SENTENCES:
        pcm = await engine.synth(sentence, 150)
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        chunks.append(embed(samples, engine.sample_rate))
        if "--save" in sys.argv[1:]:
            write_wav(f"fingerprint_{len(chunks)}.wav", pcm, engine.sample_rate)

    result = voice_match(ref_embedding, chunks)
    for i, score in enumerate(result["per_chunk"], 1):
        print(f"  chunk {i}  match {score:.3f}")
    print(f"match       min {result['match_min']:.3f}  mean {result['match_mean']:.3f}")
    print(f"consistency min {result['consistency_min']:.3f}")
    failures = judge(result, _floor("--match-floor", MATCH_FLOOR),
                     _floor("--consistency-floor", CONSISTENCY_FLOOR))
    if failures:
        print("\nThe voice does not hold:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("\nEvery chunk sounds like the recording, and like each other.")
    return 0


def write_wav(name: str, pcm: bytes, rate: int) -> None:
    header = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
              + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
              + b"data" + struct.pack("<I", len(pcm)))
    with open(name, "wb") as handle:
        handle.write(header + pcm)


async def main() -> int:
    store = voice_store.ensure_ready()
    print(f"Voice store: {store['dir']}")

    report = engine_report()
    print(f"Production engine(s): {', '.join(report['production_engines'])}")
    print(f"Speaking with       : {report['selected']}")

    if report["interim"]:
        # Not a lower-quality voice. There is no second engine any more, so
        # this is a tone - and saying "the app still works" here is exactly the
        # silent success this project has lost the most time to.
        print("\n  NO VOICE ON THIS MACHINE. What plays is a placeholder tone,")
        print("  not FAM. Nothing below is a judgement of how FAM sounds.")
        # Asks the backend this deployment actually selected, not the
        # in-process default: with VOICE_BACKEND=remote the reason is a
        # missing endpoint, and naming Chatterbox's would send whoever
        # reads this to a GPU that was never going to be used.
        for cls in production_engines():
            print(f"    {cls.name}: {cls.diagnose()[1]}")
        print("    Fix: pip install -r requirements-chatterbox.txt, on a GPU")
        print("         machine, with a reference recording whose rights record")
        print("         clears consent, commercial use and synthetic voice.")
        print("         See RUNPOD_PRODUCTION.md.\n")

    voices = list_voices()
    print(f"\n{len(voices)} voice(s) offered:\n")

    slowest = None
    for voice in voices:
        engine = engine_for_voice(voice.id)
        started = time.perf_counter()
        try:
            pcm = await engine.synth(SENTENCE, 150, voice.id)
        except Exception as exc:  # noqa: BLE001
            print(f"  {voice.id:34} FAILED - {type(exc).__name__}: {exc}")
            continue
        elapsed = time.perf_counter() - started
        seconds = pcm_duration(len(pcm), engine.sample_rate)
        ratio = seconds / elapsed if elapsed else 0.0
        slowest = ratio if slowest is None else min(slowest, ratio)
        print(
            f"  {voice.id:34} {seconds:5.2f}s audio  {engine.sample_rate} Hz  "
            f"{elapsed:5.2f}s to make ({ratio:6.1f}x realtime)"
        )
        if "--save" in sys.argv[1:]:
            name = voice.id.replace(":", "_") + ".wav"
            write_wav(name, pcm, engine.sample_rate)
            print(f"      wrote {name} - listen to it")

    if report["interim"]:
        print("\nThis machine cannot speak. The timings above are a tone "
              "generator's.")
        return 1
    if slowest is not None and slowest < REALTIME_FLOOR:
        print(f"\nWorking, but at {slowest:.1f}x realtime an episode would "
              "starve mid-sentence.")
        print("Chatterbox refuses CPU for this reason; check the device it "
              "actually loaded on.")
        return 1
    print("\nThe production voice is installed and working on this machine.")
    return 0


if __name__ == "__main__":
    if "--fingerprint" in sys.argv[1:]:
        raise SystemExit(asyncio.run(fingerprint()))
    raise SystemExit(asyncio.run(main()))
