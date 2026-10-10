"""Opus to the client (PROBLEMS.md §242): `/api/audio?fmt=opus`.

What it must keep true, each tested by what breaking it would cost:

* **The samples are the samples.** A client that drops the pre-skip and
  trims to the end marker holds exactly as many samples as PCM would have
  carried - or every caption after the first drifts.
* **A kept episode is passed through**, never decoded and re-encoded: no CPU
  and no second generation of loss on the plays that are most of the plays.
* **Nobody is broken.** `fmt=pcm` is byte-for-byte what it was; a rate Opus
  cannot carry, a server without PyAV, or `AUDIO_STREAM_OPUS=0` answers a
  request for Opus with PCM and without the header that says Opus.
"""
from __future__ import annotations

import dataclasses
import io
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio_codec  # noqa: E402
from config import settings  # noqa: E402

needs_opus = pytest.mark.skipif(not audio_codec.available()[0],
                                reason="PyAV is not installed")


def speech(seconds: float, rate: int = 24000) -> bytes:
    from test_audio_store import speech as reference
    return reference(seconds, rate)


def read_wire(body: bytes):
    """fam-opus v1 -> (packets, length from the end marker or None)."""
    packets, at, total = [], 0, None
    while at + 2 <= len(body):
        (n,) = struct.unpack(">H", body[at:at + 2])
        at += 2
        if n == 0:
            (total,) = struct.unpack(">I", body[at:at + 4])
            at += 4
            break
        packets.append(body[at:at + n])
        at += n
    assert at == len(body), "bytes after the end marker"
    return packets, total


def decode_wire(body: bytes, rate: int, preskip48: int):
    """What a client does: decode, drop the pre-skip, trim to the marker."""
    import av
    import numpy as np

    packets, total = read_wire(body)
    decoder = av.CodecContext.create("libopus", "r")
    resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
    out = bytearray()
    for packet in packets:
        for frame in decoder.decode(av.Packet(packet)):
            for f in resampler.resample(frame):
                out += bytes(f.planes[0])[: f.samples * 2]
    for f in resampler.resample(None):
        out += bytes(f.planes[0])[: f.samples * 2]
    samples = np.frombuffer(bytes(out), dtype="<i2")
    samples = samples[preskip48 * rate // 48000:]
    if total is not None:
        samples = samples[:total]
    return samples, total


def similarity(a, b) -> float:
    import numpy as np

    a = a.astype(float)
    b = b.astype(float)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


@needs_opus
def test_a_streamed_episode_decodes_to_exactly_its_samples():
    import numpy as np

    pcm = speech(3.37)
    stream = audio_codec.OpusStream(24000)
    body = bytearray()
    # Uneven chunks, as the voice hands them over, including a lone odd byte.
    cuts = [0, 1001, 1002, 9999, 50001, len(pcm)]
    for a, b in zip(cuts, cuts[1:]):
        body += stream.feed(pcm[a:b])
    body += stream.close()
    samples, total = decode_wire(bytes(body), 24000, stream.preskip)
    original = np.frombuffer(pcm, dtype="<i2")
    assert total == len(original) == len(samples)
    assert similarity(original, samples) > 0.9
    assert len(body) < len(pcm) / 10, "Opus should be far smaller than PCM"


@needs_opus
def test_a_kept_episode_is_passed_through_as_stored():
    import av

    pcm = speech(2.0)
    codec, blob = audio_codec.encode(pcm, 24000)
    assert codec == audio_codec.OPUS
    preskip, chunks = audio_codec.stored_packets(blob, len(pcm) // 2, 24000)
    framed = list(chunks)
    body = b"".join(framed)
    packets, total = read_wire(body)
    with av.open(io.BytesIO(blob), format="ogg") as container:
        stored = [bytes(p) for p in container.demux(audio=0) if p.size]
    assert packets == stored, "the stored packets were not sent as they are"
    assert total == len(pcm) // 2
    assert preskip == 312
    # Each chunk says how much PCM it stands for, so priming still counts
    # seconds and not Opus bytes.
    assert sum(audio_codec.pcm_len(c) for c in framed) >= len(pcm)


def _set(monkeypatch, module, **values):
    monkeypatch.setattr(module, "settings", dataclasses.replace(module.settings, **values))


def test_unsupported_rates_and_the_switch_mean_pcm(monkeypatch):
    assert not audio_codec.can_stream(22050)
    _set(monkeypatch, audio_codec, audio_stream_opus=False)
    assert not audio_codec.can_stream(24000)


def _client(tmp_path, monkeypatch, rate):
    import app as appmod
    from cache import SqliteScriptCache
    from fastapi.testclient import TestClient
    from pipeline import PodcastPipeline
    from test_audio_cache import CountingGenerator, CountingVoice

    import tts
    _set(monkeypatch, tts, sample_rate=rate)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", SqliteScriptCache(str(tmp_path / "s.db")))
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "DEMO_MODE", False)
    engine, gen = CountingVoice(), CountingGenerator()
    monkeypatch.setattr(
        appmod, "_make_pipeline",
        lambda voice=None, author="": PodcastPipeline(
            generator=gen, engine=engine, cache=appmod.SCRIPT_CACHE, voice=voice))
    return TestClient(appmod.app), engine


def _get(client, fmt):
    return client.get("/api/audio", params={
        "q": "why is the sky blue", "minutes": 1, "fmt": fmt})


@needs_opus
def test_the_endpoint_streams_opus_then_passes_the_kept_copy_through(tmp_path, monkeypatch):
    client, engine = _client(tmp_path, monkeypatch, 24000)
    first = _get(client, "opus")
    assert first.status_code == 200
    assert first.headers["x-fam-audio-format"] == "opus"
    assert first.headers["content-type"].startswith("audio/x-fam-opus")
    rate = int(first.headers["x-sample-rate"])
    samples, total = decode_wire(first.content, rate,
                                 int(first.headers["x-fam-opus-preskip"]))
    assert total and len(samples) == total
    voiced = engine.calls

    # The same episode as PCM is the reference length.
    pcm = _get(client, "pcm")
    assert pcm.headers["content-type"].startswith("audio/L16")
    assert "x-fam-audio-format" not in pcm.headers
    assert len(pcm.content) // 2 == total
    assert engine.calls == voiced, "a kept episode was voiced again"

    # Played again as Opus: from the kept copy, as stored.
    again = _get(client, "opus")
    samples2, total2 = decode_wire(again.content, rate,
                                   int(again.headers["x-fam-opus-preskip"]))
    assert total2 == total == len(samples2)
    assert engine.calls == voiced
    assert len(again.content) < len(pcm.content) / 8


def test_a_rate_opus_cannot_carry_answers_with_pcm(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, 22050)
    res = _get(client, "opus")
    assert res.status_code == 200
    assert "x-fam-audio-format" not in res.headers
    assert res.headers["content-type"].startswith("audio/L16")
    assert len(res.content) > 0 and len(res.content) % 2 == 0


def test_the_player_asks_for_opus_only_when_it_can_decode_it():
    """fam-audio.js is the spec for every client (audio-no-browser): it asks
    for Opus only where a decoder exists, and plays PCM whenever the server
    answers without the header that says Opus."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = open(os.path.join(root, "static", "fam-audio.js")).read()
    assert "AudioDecoder" in js and "isConfigSupported" in js
    assert '"X-FAM-Audio-Format"' in js
    assert "X-FAM-Opus-Preskip" in js
    assert "fmt=opus" in js and "fmt=pcm" in js
