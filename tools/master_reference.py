"""Trim and level a voice's reference recording so Chatterbox clones it well.

    python tools/master_reference.py ~/.fam/voices/reference_3.wav
    python tools/master_reference.py in.wav --out out.wav

Chatterbox learns a voice's accent and prosody from the **first six seconds**
of its reference (PROBLEMS.md §176). The default recording opened with 1.3s of
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
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import voice_bank  # noqa: E402

KEEP_LEAD = voice_bank.KEEP_LEAD
TARGET_DBFS = voice_bank.TARGET_DBFS
PEAK_CEILING_DBFS = voice_bank.PEAK_CEILING_DBFS


def master(audio: bytes) -> bytes:
    """The recording, trimmed and levelled. 16-bit PCM WAV in and out.

    The same function the admin page's upload uses (§328).
    """
    if voice_bank.recording_stats(audio)["lead_silence"] is None:
        raise SystemExit("only 16-bit PCM WAV is supported; convert it first")
    return voice_bank.master_recording(audio)


def describe(label: str, audio: bytes) -> None:
    s = voice_bank.recording_stats(audio)

    def num(value, fmt):
        return "n/a" if value is None else format(value, fmt)

    print(f"{label:7s} {s['seconds']:5.2f}s  {s['sample_rate']} Hz  "
          f"lead silence {num(s['lead_silence'], '.2f')}s  "
          f"speech {num(s['loudness_dbfs'], '.1f')} dBFS  "
          f"peak {num(s['peak_dbfs'], '.1f')} dBFS")


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
