"""The picture a shared link previews with in Messages, WhatsApp, X and the rest.

A FAM link used to arrive as words: the page named the Instagram story card
as its `og:image`, and that card is an SVG at 9:16. Messages and most link
previewers draw only JPEG or PNG, and want a square or wide picture, so they
skipped it and showed the title and the site. Spotify's link arrives as its
album art with the song under it; this is FAM's version of that (§559, the
owner's pick from four drawn options: the episode's art, with a play button).

`draw` makes a 1200x1200 JPEG:

* **With a picture** - the episode's own picture (the one the player shows,
  `thumbnails.pick_for_player`), cropped square, a small FAM badge in the
  top left and a copper play button bottom right. No words on it: the
  bubble prints the title under the picture, as Spotify's does.
* **Without one** - the episode has no approved picture: its title set large
  on FAM's dark ground with the length, the same play button. A link never
  previews as nothing.

The play button is drawn on, not a control: Messages makes the whole bubble
one tap, which opens the episode. Pure: bytes in, bytes out, no network, no
model, nothing stored. Never raises - `None` means "draw nothing", and the
page then names no picture rather than a broken one.

The typefaces are the app's own (Bricolage Grotesque, Geist), read from the
iOS bundle's copies; a machine without them draws in Pillow's default face
rather than failing.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SIZE = 1200
QUALITY = 86
INK = (24, 21, 31)
PAPER = (244, 239, 228)
COPPER = (224, 181, 99)
GROUND = (42, 39, 51)
MUTED = (171, 163, 196)

FONT_DIR = Path(__file__).resolve().parent / "ios" / "FAM" / "FAM" / "Fonts"


def _font(name: str, size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        try:
            return ImageFont.load_default(size)
        except TypeError:  # Pillow before 10.1 takes no size
            return ImageFont.load_default()


def _square(picture: bytes):
    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(picture))
    im = ImageOps.exif_transpose(im).convert("RGB")
    w, h = im.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    return im.crop((left, top, left + side, top + side)).resize(
        (SIZE, SIZE), Image.LANCZOS)


def _badge(d) -> None:
    """The FAM wordmark on a dark rounded plate, top left."""
    d.rounded_rectangle((40, 40, 250, 140), radius=24, fill=INK)
    d.text((72, 52), "FAM", font=_font("BricolageGrotesque-SemiBold.ttf", 70),
           fill=COPPER)


def _play(d, cx: int = SIZE - 150, cy: int = SIZE - 150, r: int = 96) -> None:
    """A copper disc with a dark triangle, with a soft ring so it reads on a
    light picture as well as a dark one."""
    d.ellipse((cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6), fill=INK)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=COPPER)
    k = r * 0.42
    d.polygon([(cx - k * 0.75, cy - k), (cx - k * 0.75, cy + k),
               (cx + k * 1.05, cy)], fill=INK)


def _wrap(d, text: str, font, width: int, most: int) -> list[str]:
    """`text` in lines no wider than `width`, at most `most` of them; the
    last one ends in an ellipsis when it had to be cut."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if d.textlength(trial, font=font) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > most:
        lines = lines[:most]
        last = lines[-1]
        while last and d.textlength(last + "…", font=font) > width:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        lines[-1] = last + "…"
    return lines


def _cover(title: str, minutes: float):
    """No picture: the title on FAM's dark ground, a copper glow behind it."""
    from PIL import Image, ImageDraw, ImageFilter

    im = Image.new("RGB", (SIZE, SIZE), GROUND)
    glow = ImageDraw.Draw(im)
    glow.ellipse((-500, -600, 900, 700), fill=(84, 66, 52))
    im = im.filter(ImageFilter.GaussianBlur(160))
    d = ImageDraw.Draw(im)
    d.text((96, 90), "FAM", font=_font("BricolageGrotesque-SemiBold.ttf", 96),
           fill=COPPER)
    font = _font("BricolageGrotesque-SemiBold.ttf", 108)
    y = 330
    for line in _wrap(d, title or "A FAM episode", font, 1000, 5):
        d.text((96, y), line, font=font, fill=PAPER)
        y += 124
    # A half minute stays a half minute (§332): "1.5 MIN EPISODE".
    try:
        length = max(0.5, float(minutes or 1))
    except (TypeError, ValueError):
        length = 1.0
    pill = f"{length:g} MIN EPISODE"
    small = _font("GeistMono-Bold.ttf", 34)
    w = d.textlength(pill, font=small)
    d.rounded_rectangle((96, y + 50, 96 + w + 54, y + 50 + 72), radius=36,
                        fill=COPPER)
    d.text((123, y + 65), pill, font=small, fill=INK)
    return im


def draw(picture: Optional[bytes], title: str, minutes: float) -> Optional[bytes]:
    """The preview as JPEG bytes; `None` only if Pillow itself fails."""
    try:
        from PIL import ImageDraw

        im = None
        if picture:
            try:
                im = _square(picture)
                _badge(ImageDraw.Draw(im))
            except Exception:  # noqa: BLE001 - an unreadable picture
                log.warning("link preview: the episode's picture would not "
                            "open; drawing the cover instead")
                im = None
        if im is None:
            im = _cover(title, minutes)
        _play(ImageDraw.Draw(im))
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=QUALITY, optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - a preview is never worth a 500
        log.exception("link preview: could not draw")
        return None
