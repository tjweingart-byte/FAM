"""How a kept episode's audio is packed: Opus at 24 kbps, or zlib.

Kept audio used to be zlib-compressed PCM (§132), which on speech saves about a
quarter. Opus at 24 kbps is about a twelfth of zlib's size for speech (§237,
measured on `reference_3.wav`: a two-minute episode is 4.45 MB as zlib, 0.48 MB
as Opus at 32 kbps and 0.36 MB at 24). The voice is 24 kHz, so it holds nothing
above 12 kHz, which 24 kbps already carries; the owner chose 24 over 32. That difference is what makes it
affordable to keep audio in object storage at all.

**Nothing changes for the listener or the player.** Opus is how audio is
*stored*; it is decoded back to the same 16-bit PCM before it is streamed to
a client that asked for PCM, so every installed client, the captions'
`starts` and the transport see exactly what they saw before. A client that
asks for Opus (§242, at the bottom of this file) gets the stored packets
themselves. Decoding is about 200x realtime and is done a slice at a
time as the episode plays, so the first word waits for one Opus page and not
for the whole episode.

Two honest fallbacks, never a silent one:

* **No codec library** (PyAV not installed): zlib, as before, and
  `available()` says why, which `/api/health` reports.
* **A rate Opus cannot carry** (it takes 8/12/16/24/48 kHz; the development
  engines speak 22.05 kHz): zlib. Production voices are 24 kHz.

Every row records which codec packed it (`episode_audio.codec`), so old zlib
rows keep playing after this ships and the two never have to be told apart by
guessing. The decoded length is pinned to the original's (`frames`), so a
codec's padding can never move a caption.
"""
from __future__ import annotations

import io
import logging
import struct
import zlib
from typing import Iterator, Optional

from config import settings

log = logging.getLogger(__name__)

OPUS = "opus"
ZLIB = "zlib"

#: The rates libopus encodes without resampling.
OPUS_RATES = (8000, 12000, 16000, 24000, 48000)

#: zlib level 1: measured on the reference recording at 74% of the raw size in
#: 30 ms a second of audio. Level 6 bought under half a percent more for twice
#: the time, and lzma's 59% cost fifteen times as long.
ZLIB_LEVEL = 1

#: 16-bit mono, the only shape FAM's engines produce (`config.sample_width`).
SAMPLE_BYTES = 2

#: How much PCM one decoded slice carries before it is handed on: about one
#: second at 24 kHz, the same slice `_play_stored` has always streamed.
SLICE_BYTES = 48000

try:  # pragma: no cover - which branch runs depends on the machine
    import av  # type: ignore
    _AV_ERROR = ""
except Exception as exc:  # ImportError, or a broken shared library
    av = None
    _AV_ERROR = f"PyAV is not installed ({exc.__class__.__name__})"


def available() -> tuple[bool, str]:
    """Whether Opus can be written here, and if not, why."""
    if av is None:
        return False, _AV_ERROR
    return True, ""


def chosen(sample_rate: int) -> str:
    """The codec a new row at this rate is packed with."""
    if str(settings.audio_codec).lower() != OPUS:
        return ZLIB
    if av is None or int(sample_rate) not in OPUS_RATES:
        return ZLIB
    return OPUS


def encode(pcm: bytes, sample_rate: int) -> tuple[str, bytes]:
    """(codec, packed bytes). Falls back to zlib, and says so in the log,
    rather than failing: a codec problem must never cost the episode its
    kept audio."""
    codec = chosen(sample_rate)
    if codec == OPUS and len(pcm) >= SAMPLE_BYTES:
        try:
            return OPUS, _encode_opus(pcm, int(sample_rate))
        except Exception:
            log.exception("opus encode failed; keeping this episode as zlib")
    return ZLIB, zlib.compress(pcm, ZLIB_LEVEL)


def decode(codec: str, blob: bytes, sample_rate: int, frames: int = 0) -> bytes:
    """The whole PCM back. For tests and exports; playback uses `slices`."""
    return b"".join(slices(codec, blob, sample_rate, frames))


def slices(codec: str, blob: bytes, sample_rate: int,
           frames: int = 0) -> Iterator[bytes]:
    """PCM a slice at a time, exactly `frames` samples long when that is
    known. A generator, so the caller pays for each slice as it plays."""
    if codec != OPUS:
        pcm = zlib.decompress(blob)
        for i in range(0, len(pcm), SLICE_BYTES):
            yield pcm[i:i + SLICE_BYTES]
        return
    want = int(frames) * SAMPLE_BYTES if frames else None
    sent = 0
    pending = bytearray()
    for piece in _decode_opus(blob, int(sample_rate)):
        if want is not None:
            piece = piece[:max(0, want - sent - len(pending))]
        pending.extend(piece)
        if len(pending) >= SLICE_BYTES:
            sent += len(pending)
            yield bytes(pending)
            pending.clear()
        if want is not None and sent + len(pending) >= want:
            break
    if want is not None and sent + len(pending) < want:
        # A decoder that came up short (it should not) is padded with silence
        # rather than letting the captions run ahead of the voice.
        pending.extend(b"\x00" * (want - sent - len(pending)))
    if pending:
        yield bytes(pending)


def _bitrate() -> int:
    return max(6000, int(settings.audio_opus_bitrate))


def _encode_opus(pcm: bytes, sample_rate: int) -> bytes:
    pcm = pcm[: len(pcm) - (len(pcm) % SAMPLE_BYTES)]
    out = io.BytesIO()
    with av.open(out, mode="w", format="ogg") as container:
        stream = container.add_stream("libopus", rate=sample_rate)
        stream.bit_rate = _bitrate()
        stream.layout = "mono"
        stream.options = {"application": "voip"}
        codec = stream.codec_context
        size = codec.frame_size or (sample_rate // 50)
        step = size * SAMPLE_BYTES
        pts = 0
        for i in range(0, len(pcm), step):
            chunk = pcm[i:i + step]
            n = len(chunk) // SAMPLE_BYTES
            if n < size:
                chunk = chunk + b"\x00" * ((size - n) * SAMPLE_BYTES)
            frame = av.AudioFrame(format="s16", layout="mono", samples=size)
            frame.planes[0].update(chunk)
            frame.sample_rate = sample_rate
            frame.pts = pts
            pts += size
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return out.getvalue()


def _decode_opus(blob: bytes, sample_rate: int) -> Iterator[bytes]:
    with av.open(io.BytesIO(blob), mode="r", format="ogg") as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                yield bytes(out.planes[0])[: out.samples * SAMPLE_BYTES]
        for out in resampler.resample(None):
            yield bytes(out.planes[0])[: out.samples * SAMPLE_BYTES]


def describe() -> dict:
    """For `/api/health`: what new audio is packed with, and why."""
    ok, why = available()
    wanted = str(settings.audio_codec).lower()
    return {"codec": OPUS if (ok and wanted == OPUS) else ZLIB,
            "wanted": wanted, "opus_bitrate": _bitrate(),
            **({"why_not_opus": why} if wanted == OPUS and not ok else {})}


def ratio(codec: str, blob: Optional[bytes], frames: int) -> float:
    """Packed size over raw size, for logs."""
    raw = int(frames) * SAMPLE_BYTES
    return round(len(blob or b"") / raw, 3) if raw else 0.0


# ------------------------------------------------------- Opus to the client
#
# §242, at the owner's direction: a client that can decode Opus asks for it
# (`/api/audio?fmt=opus`) and receives about a sixteenth of the bytes raw PCM
# costs. It is still a stream - decoded as it arrives, nothing written - which
# is what `no-audio-files` protects (§1, §239). Every other client is served
# PCM exactly as before.
#
# The wire format, "fam-opus v1", is deliberately the smallest thing a web
# page (WebCodecs), an iPhone (AudioConverter) and a test can all read:
#
#   repeated:  u16 big-endian length N > 0, then N bytes of one Opus packet
#   last:      u16 0, then u32 big-endian - the episode's length in samples
#              at `X-Sample-Rate`, after the pre-skip is dropped
#
# with `X-FAM-Audio-Format: opus` and `X-FAM-Opus-Preskip` (the encoder's
# start-up delay in 48 kHz samples, as an Ogg OpusHead states it). A client
# drops the pre-skip, then trims to the end marker's length, and holds exactly
# the samples PCM would have carried - so captions' `starts`, seek and the
# offline shelf see what they always saw. A stream cut short has no marker,
# which a client treats as the end of what arrived.

#: The end-of-stream marker's first two bytes: a packet of length zero.
END_MARKER = b"\x00\x00"

#: Packets per framed chunk when a kept episode is passed through: 50 x 20 ms,
#: the same one-second cadence `slices` streams PCM at.
PACKETS_PER_CHUNK = 50


class Framed(bytes):
    """Opus bytes already in the wire format, with how much PCM they stand
    for (`pcm_bytes`) - which is what the server's pre-roll and its
    accounting count, so priming one second still means one second."""

    pcm_bytes: int = 0

    def __new__(cls, data: bytes, pcm_bytes: int = 0):
        obj = super().__new__(cls, data)
        obj.pcm_bytes = int(pcm_bytes)
        return obj


def pcm_len(chunk: bytes) -> int:
    """The PCM a chunk stands for: its length, or a `Framed` chunk's own."""
    return getattr(chunk, "pcm_bytes", len(chunk))


def frame(packet: bytes) -> bytes:
    return struct.pack(">H", len(packet)) + packet


def end_marker(samples: int) -> bytes:
    return END_MARKER + struct.pack(">I", max(0, int(samples)))


def can_stream(sample_rate: int) -> bool:
    """Whether a stream at this rate can go out as Opus here."""
    return (bool(settings.audio_stream_opus) and av is not None
            and int(sample_rate) in OPUS_RATES)


def _preskip(extradata: Optional[bytes]) -> int:
    """Pre-skip from an OpusHead, in 48 kHz samples; 0 when there is none."""
    if extradata and extradata[:8] == b"OpusHead" and len(extradata) >= 12:
        return struct.unpack("<H", extradata[10:12])[0]
    return 0


def stored_packets(blob: bytes, frames: int, sample_rate: int,
                   ) -> tuple[int, Iterator[Framed]]:
    """A kept Opus episode as the wire format, without decoding a sample.

    (pre-skip, chunks): the packets exactly as they were stored, so a replay
    costs neither the CPU of decoding and re-encoding nor a second generation
    of loss. Ends with the marker, carrying the original length (`frames`).
    """
    container = av.open(io.BytesIO(blob), mode="r", format="ogg")
    stream = container.streams.audio[0]
    preskip = _preskip(stream.codec_context.extradata)
    per_packet = max(1, int(sample_rate) // 50) * SAMPLE_BYTES

    def chunks() -> Iterator[Framed]:
        try:
            batch, count = bytearray(), 0
            for packet in container.demux(stream):
                if not packet.size:
                    continue
                batch += frame(bytes(packet))
                count += 1
                if count >= PACKETS_PER_CHUNK:
                    yield Framed(bytes(batch), count * per_packet)
                    batch, count = bytearray(), 0
            batch += end_marker(frames)
            yield Framed(bytes(batch), count * per_packet)
        finally:
            container.close()

    return preskip, chunks()


class OpusStream:
    """PCM in, wire-format Opus out, 20 ms at a time - for audio that is
    being voiced as it streams, or kept as zlib. One per response."""

    def __init__(self, sample_rate: int):
        self.sample_rate = int(sample_rate)
        codec = av.CodecContext.create("libopus", "w")
        codec.sample_rate = self.sample_rate
        codec.layout = "mono"
        codec.format = "s16"
        codec.bit_rate = _bitrate()
        codec.options = {"application": "voip"}
        codec.open()
        self._codec = codec
        self._size = codec.frame_size or (self.sample_rate // 50)
        self._pending = bytearray()
        self._pts = 0
        self._fed = 0
        self.preskip = _preskip(codec.extradata)

    @property
    def samples(self) -> int:
        """Samples fed so far. Counted from bytes, not per chunk: a chunk
        may end half way through a sample."""
        return self._fed // SAMPLE_BYTES

    def _encode(self, chunk: bytes) -> bytes:
        frame_ = av.AudioFrame(format="s16", layout="mono", samples=self._size)
        frame_.planes[0].update(chunk)
        frame_.sample_rate = self.sample_rate
        frame_.pts = self._pts
        self._pts += self._size
        return b"".join(frame(bytes(p)) for p in self._codec.encode(frame_))

    def feed(self, pcm: bytes) -> bytes:
        self._pending += pcm
        step = self._size * SAMPLE_BYTES
        whole = len(self._pending) - len(self._pending) % step
        self._fed += len(pcm)
        out = [self._encode(bytes(self._pending[i:i + step]))
               for i in range(0, whole, step)]
        del self._pending[:whole]
        return b"".join(out)

    def close(self) -> bytes:
        """The tail, padded to a whole frame, the encoder flushed, and the
        end marker with the true length."""
        out = []
        step = self._size * SAMPLE_BYTES
        if self._pending:
            tail = bytes(self._pending) + b"\x00" * (step - len(self._pending) % step)
            out.append(self._encode(tail[:step]))
            self._pending.clear()
        out.extend(frame(bytes(p)) for p in self._codec.encode(None))
        out.append(end_marker(self.samples))
        return b"".join(out)
