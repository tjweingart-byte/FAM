#!/usr/bin/env python3
"""Prove the R2 audio bucket works before turning it on (§237, AUDIO_STORE.md).

    AUDIO_STORE=r2 AUDIO_BUCKET=fam-audio R2_ACCOUNT_ID=... \\
    R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... python tools/verify_audio_store.py

Verify, do not inspect: it writes a real episode-sized object to `recent/`,
reads it back, copies it to `kept/` in Infrequent Access (the sweep's exact
request), reads that, and deletes both - timing each, because the first-byte
time is the delay a cold replay adds. It also packs ten seconds of the
reference voice as Opus, so a machine missing PyAV says so here rather than
silently keeping zlib.

Costs a handful of Class A/B operations, inside R2's free allowance.
Exit status 0 only when every step answered.
"""
from __future__ import annotations

import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio_codec  # noqa: E402
import audio_store  # noqa: E402


def step(label: str, fn):
    started = time.perf_counter()
    try:
        out = fn()
    except Exception as exc:  # noqa: BLE001 - every failure is the answer
        print(f"  FAIL  {label}: {exc}")
        raise SystemExit(1)
    print(f"  ok    {label} ({(time.perf_counter() - started) * 1000:.0f} ms)")
    return out


def main() -> int:
    ok, why = audio_codec.available()
    print(f"codec: {audio_codec.describe()}")
    if not ok:
        print(f"  FAIL  Opus is unavailable here: {why}")
        return 1
    store = audio_store.get_store()
    if store is None:
        print(f"  FAIL  no bucket: {audio_store.status().get('error') or 'AUDIO_STORE is not r2'}")
        return 1
    print(f"bucket: {store.describe()}")
    pcm = b"\x00\x01" * 24000 * 10
    codec, blob = step("pack 10s as Opus", lambda: audio_codec.encode(pcm, 24000))
    if codec != audio_codec.OPUS:
        print("  FAIL  packed as zlib, not Opus")
        return 1
    tag = secrets.token_hex(6)
    recent = audio_store.object_name(audio_store.RECENT, f"_verify-{tag}")
    kept = audio_store.swap_prefix(recent, audio_store.KEPT)
    step(f"PUT {recent} ({len(blob)} bytes)", lambda: store.put(recent, blob))
    back = step(f"GET {recent}", lambda: store.get(recent))
    if back != blob:
        print("  FAIL  read back different bytes")
        return 1
    step(f"COPY to {kept} as {audio_store.INFREQUENT}",
         lambda: store.copy(recent, kept, audio_store.INFREQUENT))
    if step(f"GET {kept}", lambda: store.get(kept)) != blob:
        print("  FAIL  the kept copy differs")
        return 1
    step("DELETE both", lambda: (store.delete(recent), store.delete(kept)))
    if store.get(recent) is not None:
        print("  FAIL  a deleted object is still there")
        return 1
    print("all steps passed - set AUDIO_STORE=r2 on the service")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
