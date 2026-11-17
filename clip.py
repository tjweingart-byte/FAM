"""A short captioned video of an episode's opening, to post natively (§623).

Facebook holds back a post that links out (§621), and a group's admin asked
for the other thing a feed carries: "a quick 15-30 second audiogram/video
clip (with captions and visuals) to post natively on Facebook before linking
to the full episode". This is that file: the first whole sentences of an
episode's **kept** audio, 15 to 30 seconds of it, as an MP4 - the episode's
picture, its title, the words on screen as they are spoken, a bar that
fills, and a last card saying where the whole episode is.

Three rules hold it up:

* **Nothing is voiced or written for it.** It is cut from audio the cache
  already keeps, with the sentence timings measured when that audio was
  voiced (`StoredAudio.starts`). No kept audio, or audio with no timings,
  is "no clip" - said in words by the caller, never a clip with captions
  that are guessed.
* **The opening, in whole sentences.** An episode's first two sentences
  situate it and open its question ([situate], [opening-last]); they are
  the hook by construction, so no model chooses an excerpt. The clip ends
  where a sentence ends: the last one that finishes inside `LONGEST`. It
  may run short of `SHORTEST` when the episode's sentences fall that way -
  a clip that stops mid-word to fill a length would be worse.
* **FAM posts nothing** ([no-social-posting]). A person downloads the file
  and uploads it.

Pure: bytes and lists in, MP4 bytes out; no network, no model, nothing
stored. PyAV (already the Opus codec's dependency, `audio_codec`) carries
H.264 and AAC. `render` returns None when it cannot encode.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

import link_preview as lp

log = logging.getLogger(__name__)

#: The clip's length in seconds: what the admin asked for.
SHORTEST = 15.0
LONGEST = 30.0
#: The last card, in silence, after the words stop.
END_CARD_SECONDS = 2.5
#: 4:5, the tallest a feed shows whole - the post photo's shape (§622).
SIZE = lp.POST
FPS = 10
#: A caption on screen: at most this many words, so it is read at a glance.
CUE_WORDS = 7
#: The audio fades over this long at the cut, so a breath is not clipped.
FADE_SECONDS = 0.25


def opening(starts: list, total: float) -> tuple[int, float]:
    """`(sentences, seconds)`: how many whole sentences open the clip and
    where it ends. `starts[i]` is where sentence i begins; a sentence ends
    where the next begins, the last at `total`. (0, 0.0) when there are no
    usable timings or the first sentence alone runs past `LONGEST`."""
    try:
        marks = [float(s) for s in starts]
    except (TypeError, ValueError):
        return 0, 0.0
    if not marks or total <= 0 or any(b < a for a, b in zip(marks, marks[1:])):
        return 0, 0.0
    ends = marks[1:] + [float(total)]
    count, end = 0, 0.0
    for i, stop in enumerate(ends):
        if stop > LONGEST:
            break
        count, end = i + 1, stop
    return count, end


def cues(sentences: list, starts: list, count: int, end: float) -> list[tuple[float, float, str]]:
    """The captions: `(from, to, words)` in order. A sentence is shown a few
    words at a time, its span shared out by the letters in each piece -
    the sentence's start and end are measured, the pieces inside it are not,
    so no piece is ever in another sentence's time."""
    out: list[tuple[float, float, str]] = []
    for i in range(count):
        words = str(sentences[i]).split()
        if not words:
            continue
        t0 = float(starts[i])
        t1 = float(starts[i + 1]) if i + 1 < count else end
        pieces = [" ".join(words[j:j + CUE_WORDS]) for j in range(0, len(words), CUE_WORDS)]
        # A last piece of one or two words joins the piece before it.
        if len(pieces) > 1 and len(pieces[-1].split()) <= 2:
            pieces[-2:] = [pieces[-2] + " " + pieces[-1]]
        weight = sum(len(p) for p in pieces) or 1
        at = t0
        for p in pieces:
            span = (t1 - t0) * len(p) / weight
            out.append((at, at + span, p))
            at += span
    return out


def _base(picture: Optional[bytes], title: str, host: str):
    """Everything that does not change from frame to frame, and the box the
    caption is drawn in: `(image, (x, top, width, bottom))`."""
    from PIL import Image, ImageDraw, ImageFilter, ImageOps

    w, h = SIZE
    m = lp.POST_MARGIN
    im = Image.new("RGB", SIZE, lp.GROUND)
    top = 0
    if picture:
        try:
            src = ImageOps.exif_transpose(Image.open(io.BytesIO(picture))).convert("RGB")
            im.paste(ImageOps.fit(src, (w, lp.POST_PICTURE), Image.LANCZOS), (0, 0))
            top = lp.POST_PICTURE
        except Exception:  # noqa: BLE001 - an unreadable picture
            log.warning("clip: the episode's picture would not open")
    if not top:
        ImageDraw.Draw(im).ellipse((-500, -600, 900, 700), fill=(84, 66, 52))
        im = im.filter(ImageFilter.GaussianBlur(170))
        lp._wordmark(im, m, 110, 70)
        top = 250
    d = ImageDraw.Draw(im)
    font = lp._font("GeistMono-Bold.ttf", 28)
    line = lp._wrap(d, (title or "A FAM episode").upper(), font, w - 2 * m, 1)
    d.text((m, top + 44), line[0] if line else "", font=font, fill=lp.COPPER)
    cy = h - 108
    lp._play(d, cx=m + 56, cy=cy, r=52)
    x = m + 56 + 52 + 30
    d.text((x, cy - 46), "LISTEN TO THE FULL EPISODE",
           font=lp._font("GeistMono-Bold.ttf", 24), fill=lp.COPPER)
    d.text((x, cy - 8), host or "familiarize.net",
           font=lp._font("Geist-SemiBold.ttf", 36), fill=lp.PAPER)
    if top == lp.POST_PICTURE:
        lp._wordmark(im, w - m - lp.wordmark_width(40), cy - 20, 40)
    return im, (m, top + 110, w - 2 * m, h - 210)


def _captioned(base, box, words: str):
    """`base` with one caption set large in `box`."""
    from PIL import ImageDraw

    im = base.copy()
    d = ImageDraw.Draw(im)
    x, top, width, bottom = box
    for size in (68, 60, 52, 46):
        font = lp._font("BricolageGrotesque-SemiBold.ttf", size)
        lines = lp._wrap(d, words, font, width)
        step = round(size * 1.18)
        if len(lines) * step <= bottom - top:
            break
    y = top
    for text in lines:
        if y + step > bottom + 4:
            break
        d.text((x, y), text, font=font, fill=lp.PAPER)
        y += step
    return im


def _end_card(host: str):
    """The last card: the wordmark, and where the whole episode is."""
    from PIL import Image, ImageDraw, ImageFilter

    w, h = SIZE
    im = Image.new("RGB", SIZE, lp.GROUND)
    ImageDraw.Draw(im).ellipse((-400, -500, 900, 800), fill=(84, 66, 52))
    im = im.filter(ImageFilter.GaussianBlur(170))
    cap = 120
    lp._wordmark(im, (w - lp.wordmark_width(cap)) / 2, h / 2 - 220, cap)
    d = ImageDraw.Draw(im)
    for text, face, size, colour, y in (
            ("Hear the full episode on FAM", "BricolageGrotesque-SemiBold.ttf", 60,
             lp.PAPER, h / 2 - 10),
            (host or "familiarize.net", "Geist-SemiBold.ttf", 44, lp.COPPER, h / 2 + 90)):
        font = lp._font(face, size)
        d.text(((w - d.textlength(text, font=font)) / 2, y), text, font=font, fill=colour)
    return im


def _bar(im, fraction: float):
    """`im` with the progress bar drawn to `fraction`, as a new image."""
    from PIL import ImageDraw

    out = im.copy()
    w, h = SIZE
    m = lp.POST_MARGIN
    d = ImageDraw.Draw(out)
    y = h - 196
    d.rounded_rectangle((m, y, w - m, y + 8), radius=4, fill=(74, 69, 90))
    stop = m + max(8, round((w - 2 * m) * max(0.0, min(1.0, fraction))))
    d.rounded_rectangle((m, y, stop, y + 8), radius=4, fill=lp.COPPER)
    return out


def _faded(pcm: bytes, sample_rate: int, seconds: float) -> bytes:
    """The first `seconds` of 16-bit mono `pcm`, faded out at the cut."""
    import numpy as np

    n = max(0, min(len(pcm) // 2, int(seconds * sample_rate)))
    samples = np.frombuffer(pcm[:n * 2], dtype="<i2").astype(np.float32)
    fade = min(n, int(FADE_SECONDS * sample_rate))
    if fade:
        samples[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return samples.astype("<i2").tobytes()


def render(pcm: bytes, sample_rate: int, sentences: list, starts: list,
           picture: Optional[bytes], title: str, host: str = "") -> Optional[bytes]:
    """The clip as MP4 bytes (H.264, AAC), or None: no usable timings, a
    first sentence longer than the clip, or an encoder that failed."""
    try:
        total = len(pcm) / 2 / sample_rate if sample_rate else 0.0
        if len(starts) != len(sentences):
            return None
        count, end = opening(starts, total)
        if not count:
            return None
        shown = cues(sentences, starts, count, end)
        if not shown:
            return None
        import av
        import numpy as np

        base, box = _base(picture, title, host)
        out = io.BytesIO()
        with av.open(out, "w", format="mp4") as container:
            video = container.add_stream("libx264", rate=FPS)
            video.width, video.height = SIZE
            video.pix_fmt = "yuv420p"
            video.options = {"preset": "veryfast", "crf": "24", "tune": "stillimage"}
            audio = container.add_stream("aac", rate=44100)
            audio.layout = "stereo"

            # Video: one drawing per caption, the bar redrawn each frame.
            frames = int(round(end * FPS))
            drawn: dict[int, object] = {}
            cue = 0
            for n in range(frames):
                t = n / FPS
                while cue + 1 < len(shown) and t >= shown[cue][1]:
                    cue += 1
                # Before the first word and in a gap, the caption coming up.
                if cue not in drawn:
                    drawn.clear()
                    drawn[cue] = _captioned(base, box, shown[cue][2])
                frame = av.VideoFrame.from_image(_bar(drawn[cue], t / end))
                for packet in video.encode(frame):
                    container.mux(packet)
            card = av.VideoFrame.from_image(_end_card(host))
            for _ in range(int(round(END_CARD_SECONDS * FPS))):
                # A fresh frame each time: an encoded frame keeps its stamp.
                for packet in video.encode(av.VideoFrame.from_ndarray(
                        card.to_ndarray(), format=card.format.name)):
                    container.mux(packet)
            for packet in video.encode():
                container.mux(packet)

            # Audio: the opening, then silence under the last card.
            clip = _faded(pcm, sample_rate, end)
            clip += b"\x00\x00" * int(END_CARD_SECONDS * sample_rate)
            resampler = av.AudioResampler(format="fltp", layout="stereo", rate=44100)
            step = sample_rate  # a second at a time
            samples = np.frombuffer(clip, dtype="<i2")
            pts = 0
            for i in range(0, len(samples), step):
                piece = samples[i:i + step]
                frame = av.AudioFrame.from_ndarray(piece.reshape(1, -1), format="s16",
                                                   layout="mono")
                frame.sample_rate = sample_rate
                frame.pts = pts
                pts += len(piece)
                for resampled in resampler.resample(frame):
                    for packet in audio.encode(resampled):
                        container.mux(packet)
            for resampled in resampler.resample(None):
                for packet in audio.encode(resampled):
                    container.mux(packet)
            for packet in audio.encode():
                container.mux(packet)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - a clip is never worth a 500
        log.exception("clip: could not render")
        return None
