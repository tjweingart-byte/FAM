"""Trim and level a voice's reference recording so Chatterbox clones it well.

    python tools/master_reference.py ~/.fam/voices/reference_3.wav
    python tools/master_reference.py in.wav --out out.wav

Chatterbox learns a voice's accent and prosody from the **first six seconds**
of its reference (PROBLEMS.md §174). The default recording opened with 1.3s of
silence - a fifth of that window spent on nothing - and was quiet, which leaves
more of the voice to the model's own prior. This:

* trims leading silence to `KEEP_LEAD` seconds before the first word,
* scales the speech to `TARGET_DBFS`, never past a `PEAK_CEILING_DBFS` peak,
* writes a **new file** (`<name>.mastered.wav` unless `--out` says otherwise)
  and never touches the original - a changed reference is a changed voice, and
  it gets a listening test before it replaces anything.

It prints the numbers before and after, and the new file's sha256, which is
what `VOICE_REFERENCE_FINGERPRINT` pins once the new recording is the one in use.
The rights record is not copied: a new recording is a new file, and whoever
puts it into service puts its record beside it.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import pathlib
import sys
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import voice_bank  # noqa: E402

KEEP_LEAD = 0.1
TARGET_DBFS = -20.0
PEAK_CEILING_DBFS = -1.0


def master(audio: bytes) -> bytes:
    """The recording, trimmed and levelled. 16-bit PCM WAV in and out."""
    import numpy as np

    stats = voice_bank.recording_stats(audio)
    if stats["lead_silence"] is None:
        raise SystemExit("only 16-bit PCM WAV is supported; convert it first")
    with wave.open(io.BytesIO(audio)) as wav:
        params = wav.getparams()
        raw = wav.readframes(wav.getnframes())
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    channels = params.nchannels
    rate = params.framerate

    cut = max(0.0, stats["lead_silence"] - KEEP_LEAD)
    samples = samples[int(cut * rate) * channels:]

    gain_db = 0.0
    if stats["loudness_dbfs"] is not None:
        gain_db = TARGET_DBFS - stats["loudness_dbfs"]
        gain_db = min(gain_db, PEAK_CEILING_DBFS - stats["peak_dbfs"])
    samples = np.clip(samples * (10 ** (gain_db / 20)), -1.0, 1.0)

    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes((samples * 32767.0).astype("<i2").tobytes())
    return out.getvalue()


def describe(label: str, audio: bytes) -> None:
    s = voice_bank.recording_stats(audio)
    print(f"{label:7s} {s['seconds']:5.2f}s  {s['sample_rate']} Hz  "
          f"lead silence {s['lead_silence']:.2f}s  "
          f"speech {s['loudness_dbfs']:.1f} dBFS  peak {s['peak_dbfs']:.1f} dBFS")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("recording", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    source = args.recording.expanduser()
    out = (args.out or source.with_suffix(".mastered.wav")).expanduser()
    if out.resolve() == source.resolve():
        raise SystemExit("refusing to overwrite the original; choose another --out")
    audio = source.read_bytes()
    mastered = master(audio)
    out.write_bytes(mastered)

    describe("before", audio)
    describe("after", mastered)
    try:
        voice_bank.check_recording(mastered)
        print("check   passes the voice bank's recording check")
    except voice_bank.VoiceBankError as exc:
        print(f"check   still refused: {exc}")
    print(f"wrote   {out}")
    print(f"        VOICE_REFERENCE_FINGERPRINT={hashlib.sha256(mastered).hexdigest()}")
    print("Listen to it before it replaces anything: it is a changed voice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
