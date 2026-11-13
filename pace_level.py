"""One pace for a whole episode: each chunk's speaking rate levelled.

The owner's 11.12 notes, from a listener: "Pacing also feels like somewhat
of an issue for it. Will randomly speed up and slow down at different parts
in the fam."

**Why it happens.** Chatterbox has no speaking-rate control (`tts.py`: `wpm`
is accepted and ignored), and an episode is voiced a chunk at a time - a few
sentences per synthesis call (`speech_assembly`). Each call samples its own
speech tokens, so each chunk settles on its own tempo: one comes out brisk,
the next measured, and the listener hears the voice speed up and slow down
at the seams. Seeding (§176) makes a sentence the same every time; it does
not make two chunks the same speed.

**What this does.** After each chunk is voiced, `PaceLeveller.level`
measures its rate - letters said per second of voiced audio, the silence at
either end not counted - and compares it with the episode's own pace so far,
the median of the chunks before it and this one. A chunk further than
`PACE_LEVEL_DEADBAND` from that pace is time-stretched towards it, by at most
`PACE_LEVEL_MAX` either way, with WSOLA: the waveform is cut into overlapping
windows and laid back down at the new spacing, each window shifted to where
it best lines up with the last, so the voice's pitch is untouched
([speed-pitch]: a speed change must not change the voice). The first chunk
is never changed: it is the pace the rest are levelled to.

Only the voice's audio changes. Captions are timed from the levelled audio
(it is measured before `_sentence_starts`), and kept audio is the levelled
audio, so a replay sounds as the first play did. Engines with a rate control
of their own (espeak, say) and the placeholder tone are never touched
(`TTSEngine.levels_pace`). `PACE_LEVEL=0` turns it off.

Pure numpy, no model, nothing stored. Never raises: anything unexpected
returns the chunk as it came.
"""
from __future__ import annotations

import logging
import re
import statistics
from typing import Optional

from config import settings

log = logging.getLogger(__name__)

#: Frames of 20 ms decide where speech starts and ends in a chunk.
FRAME_SECONDS = 0.02
#: A frame is speech when its loudness is above this fraction of the chunk's
#: loudest frames (the 95th percentile), so a quiet take is judged by itself.
VOICED_FRACTION = 0.08
#: A chunk shorter than this is too short to measure a rate from.
MIN_VOICED_SECONDS = 1.2
#: Letters fewer than this, likewise.
MIN_LETTERS = 20

_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def letters(text: str) -> int:
    """How many letters the voice was handed - the measure of how much it
    had to say (words differ in length too much to count them)."""
    return len(_LETTER.findall(text or ""))


def voiced_seconds(pcm: bytes, sample_rate: int) -> float:
    """Seconds from the first frame of speech to the last."""
    import numpy as np

    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32)
    frame = max(1, int(sample_rate * FRAME_SECONDS))
    n = len(samples) // frame
    if n == 0:
        return 0.0
    rms = np.sqrt(np.mean(samples[: n * frame].reshape(n, frame) ** 2, axis=1))
    loud = float(np.percentile(rms, 95))
    if loud <= 0:
        return 0.0
    voiced = np.nonzero(rms > loud * VOICED_FRACTION)[0]
    if len(voiced) == 0:
        return 0.0
    return (int(voiced[-1]) - int(voiced[0]) + 1) * frame / sample_rate


def rate(pcm: bytes, sample_rate: int, text: str) -> Optional[float]:
    """Letters per voiced second, or None when the chunk is too short to say."""
    count = letters(text)
    seconds = voiced_seconds(pcm, sample_rate)
    if count < MIN_LETTERS or seconds < MIN_VOICED_SECONDS:
        return None
    return count / seconds


def stretch(pcm: bytes, sample_rate: int, speed: float) -> bytes:
    """`pcm` (16-bit mono) played `speed` times as fast, pitch unchanged:
    WSOLA - windows of ~40 ms laid down half a window apart, each read from
    `speed` times that far along the original, nudged within a few
    milliseconds to the spot that best continues the last window laid."""
    import numpy as np

    if abs(speed - 1.0) < 1e-3:
        return pcm
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32)
    win = max(64, int(sample_rate * 0.04) // 2 * 2)
    hop_out = win // 2
    hop_in = hop_out * speed
    tolerance = max(1, int(sample_rate * 0.008))
    if len(x) < win + 2 * tolerance + 2:
        return pcm
    window = np.hanning(win).astype(np.float32)
    frames = int((len(x) - win - tolerance) / hop_in)
    if frames < 2:
        return pcm
    out_len = frames * hop_out + win
    out = np.zeros(out_len, dtype=np.float32)
    norm = np.zeros(out_len, dtype=np.float32)
    # The first window as it is.
    prev = 0
    out[:win] += x[:win] * window
    norm[:win] += window
    for k in range(1, frames):
        # What the last window would have continued with, naturally.
        natural = prev + hop_out
        target = x[natural:natural + win] if natural + win <= len(x) else None
        nominal = int(round(k * hop_in))
        lo = max(0, nominal - tolerance)
        hi = min(len(x) - win, nominal + tolerance)
        if hi < lo:
            break
        if target is None or len(target) < win:
            best = min(max(nominal, lo), hi)
        else:
            region = x[lo:hi + win]
            # Cross-correlation of the natural continuation over the region.
            scores = np.correlate(region, target, mode="valid")
            best = lo + int(np.argmax(scores))
        o = k * hop_out
        out[o:o + win] += x[best:best + win] * window
        norm[o:o + win] += window
        prev = best
    # Where the windows thin out at the very ends, divide by what was laid.
    norm[norm < 1e-3] = 1.0
    y = np.clip(out / norm, -32768, 32767).astype("<i2")
    return y.tobytes()


class PaceLeveller:
    """One episode's pace: hand it each chunk as it is voiced."""

    def __init__(self, enabled: Optional[bool] = None,
                 most: Optional[float] = None, deadband: Optional[float] = None):
        self.enabled = settings.pace_level if enabled is None else enabled
        self.most = settings.pace_level_max if most is None else most
        self.deadband = settings.pace_level_deadband if deadband is None else deadband
        self.rates: list[float] = []

    def target(self) -> Optional[float]:
        return statistics.median(self.rates) if self.rates else None

    def speed_for(self, measured: float) -> float:
        """How much faster (>1) or slower (<1) to play a chunk measured at
        `measured`, given the chunks before it; it is then counted."""
        self.rates.append(measured)
        target = statistics.median(self.rates)
        if len(self.rates) == 1 or target <= 0:
            return 1.0
        speed = target / measured
        if abs(speed - 1.0) <= self.deadband:
            return 1.0
        return max(1.0 - self.most, min(1.0 + self.most, speed))

    def level(self, pcm: bytes, sample_rate: int, text: str) -> bytes:
        """The chunk at the episode's pace; as it came when off, too short to
        measure, close enough already, or on any failure."""
        if not self.enabled or not pcm:
            return pcm
        try:
            measured = rate(pcm, sample_rate, text)
            if measured is None:
                return pcm
            speed = self.speed_for(measured)
            if speed == 1.0:
                return pcm
            log.debug("pace: chunk at %.1f letters/s against %.1f, played at %.2fx",
                      measured, self.target() or 0, speed)
            return stretch(pcm, sample_rate, speed)
        except Exception:  # noqa: BLE001 - the chunk as it came, never silence
            log.exception("pace: could not level a chunk; left as voiced")
            return pcm
