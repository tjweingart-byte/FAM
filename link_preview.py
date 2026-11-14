"""The picture a shared link previews with in Messages, WhatsApp, X and the rest.

A FAM link used to arrive as words: the page named the Instagram story card
as its `og:image`, and that card is an SVG at 9:16. Messages and most link
previewers draw only JPEG or PNG, and want a square or wide picture, so they
skipped it and showed the title and the site. Spotify's link arrives as its
album art with the song under it; this is FAM's version of that (§559, the
owner's pick from four drawn options: the episode's art, with a play button).

`draw` makes a 1200x1200 JPEG:

* **With a picture** - the episode's own picture (the one the player shows,
  `thumbnails.pick_for_player`), cropped square, the FAM logo (the drawn
  wordmark, on a dark plate) in the top left and a copper play button bottom right. No words on it: the
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
#: WhatsApp drops a link preview whose image is over about 300 KB (§601),
#: so the link picture steps its quality down until it fits under this.
MAX_BYTES = 280_000
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


# The wordmark's strokes (`FAMLetters` in the app, `.wm-f` / `.wm-a` on the
# web): F, the chevron A that is the mark, and M, in their 24-unit boxes.
# Each letter is (its box's left, top, width, height, its strokes), and a
# stroke is a list of points; the F's corner is a quarter circle, sampled.
def _f_strokes() -> list[list[tuple[float, float]]]:
    import math

    arc = [(9.2 - 3.2 * math.cos(math.radians(a)), 9.2 - 3.2 * math.sin(math.radians(a)))
           for a in range(0, 91, 10)]
    return [[(6, 20), (6, 9.2), *arc, (16, 6)], [(6, 13), (14, 13)]]


LETTERS = (
    (4.55, 4.55, 12.9, 16.9, _f_strokes()),
    (3.55, 4.55, 16.9, 16.9, [[(5, 13), (12, 6), (19, 13)], [(5, 20), (12, 13), (19, 20)]]),
    (4.55, 4.55, 14.9, 16.9, [[(6, 20), (6, 6), (12, 15), (18, 6), (18, 20)]]),
)
#: The gaps either side of the A, in cap heights (`FAMLetters`' padding).
A_BEFORE, A_AFTER = 0.13, 0.22
#: Stroke width over cap height, as the app draws it.
STROKE = 2.9 / 16.9


def wordmark_width(cap: float) -> float:
    """How wide the wordmark is at a cap height of `cap` pixels."""
    return cap * (sum(w for _, _, w, _, _ in LETTERS) / 16.9 + A_BEFORE + A_AFTER)


def _wordmark(im, x: float, y: float, cap: float, colour=COPPER) -> None:
    """The FAM wordmark, drawn into `im` with its top left at (x, y) and
    `cap` pixels tall: round caps and joins, smoothed by drawing at four
    times the size and shrinking."""
    from PIL import Image, ImageDraw

    k = 4
    pad = cap * STROKE
    w, h = wordmark_width(cap) + 2 * pad, cap + 2 * pad
    layer = Image.new("L", (int(w * k) + 1, int(h * k) + 1), 0)
    d = ImageDraw.Draw(layer)
    scale = cap / 16.9 * k
    line = max(1, round(cap * STROKE * k))
    left = pad * k
    for i, (bx, by, bw, _bh, strokes) in enumerate(LETTERS):
        if i == 1:
            left += A_BEFORE * cap * k
        for stroke in strokes:
            pts = [(left + (px - bx) * scale, pad * k + (py - by) * scale) for px, py in stroke]
            d.line(pts, fill=255, width=line, joint="curve")
            for px, py in (pts[0], pts[-1]):
                d.ellipse((px - line / 2, py - line / 2, px + line / 2, py + line / 2), fill=255)
        left += bw * scale
        if i == 1:
            left += A_AFTER * cap * k
    mask = layer.resize((int(w) + 1, int(h) + 1), Image.LANCZOS)
    im.paste(Image.new("RGB", mask.size, colour), (round(x - pad), round(y - pad)), mask)


#: The badge's plate, top left, unchanged since §559; the wordmark sits in it.
BADGE = (40, 40, 250, 140)
BADGE_CAP = 52


def _badge(im) -> None:
    """The FAM wordmark - the logo, not the word typed (the owner's 11.12
    notes) - on the same dark rounded plate, top left."""
    from PIL import ImageDraw

    left, top, right, bottom = BADGE
    ImageDraw.Draw(im).rounded_rectangle(BADGE, radius=24, fill=INK)
    width = wordmark_width(BADGE_CAP)
    _wordmark(im, left + (right - left - width) / 2, top + (bottom - top - BADGE_CAP) / 2,
              BADGE_CAP)


def _play(d, cx: int = SIZE - 150, cy: int = SIZE - 150, r: int = 96) -> None:
    """A copper disc with a dark triangle, with a soft ring so it reads on a
    light picture as well as a dark one."""
    d.ellipse((cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6), fill=INK)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=COPPER)
    k = r * 0.42
    d.polygon([(cx - k * 0.75, cy - k), (cx - k * 0.75, cy + k),
               (cx + k * 1.05, cy)], fill=INK)


def _wrap(d, text: str, font, width: int, most: int = 0) -> list[str]:
    """`text` in lines no wider than `width`, at most `most` of them (0: as
    many as it takes); the last one ends in an ellipsis when it had to be
    cut. A shared episode's title is never cut (11.14): its callers pass no
    `most` and make the type smaller instead (`_fitted`)."""
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
    if most and len(lines) > most:
        lines = lines[:most]
        last = lines[-1]
        while last and d.textlength(last + "…", font=font) > width:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        lines[-1] = last + "…"
    return lines


#: The cover's title sizes, largest first, and the room they share: the
#: title is set at the largest size whose lines fit, so a long one is
#: smaller, never cut (11.14).
COVER_SIZES = (108, 92, 78, 66, 56, 48, 40)
COVER_ROOM = 640


def _fitted(d, text: str, face: str, sizes, width: int, room: int):
    """`(font, lines, line height)` at the largest size in `sizes` whose
    lines fit in `room`; the smallest if none does - every word either way."""
    for size in sizes:
        font = _font(face, size)
        lines = _wrap(d, text, font, width)
        step = round(size * 1.15)
        if len(lines) * step <= room:
            break
    return font, lines, step


def _cover(title: str, minutes: float):
    """No picture: the title on FAM's dark ground, a copper glow behind it."""
    from PIL import Image, ImageDraw, ImageFilter

    im = Image.new("RGB", (SIZE, SIZE), GROUND)
    glow = ImageDraw.Draw(im)
    glow.ellipse((-500, -600, 900, 700), fill=(84, 66, 52))
    im = im.filter(ImageFilter.GaussianBlur(160))
    _wordmark(im, 96, 110, 70)
    d = ImageDraw.Draw(im)
    font, lines, step = _fitted(d, title or "A FAM episode",
                                "BricolageGrotesque-SemiBold.ttf",
                                COVER_SIZES, 1000, COVER_ROOM)
    y = 330
    for line in lines:
        d.text((96, y), line, font=font, fill=PAPER)
        y += step
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


def _preview(picture: Optional[bytes], title: str, minutes: float):
    """The square preview as an image: the picture with the badge, else the
    cover; the play button on either."""
    from PIL import ImageDraw

    im = None
    if picture:
        try:
            im = _square(picture)
            _badge(im)
        except Exception:  # noqa: BLE001 - an unreadable picture
            log.warning("link preview: the episode's picture would not "
                        "open; drawing the cover instead")
            im = None
    if im is None:
        im = _cover(title, minutes)
    _play(ImageDraw.Draw(im))
    return im


def draw(picture: Optional[bytes], title: str, minutes: float) -> Optional[bytes]:
    """The preview as JPEG bytes; `None` only if Pillow itself fails."""
    try:
        im = _preview(picture, title, minutes)
        data = b""
        # Quality first; a picture still too big after that (very fine
        # detail) is drawn smaller, which every previewer scales anyway.
        for side in (SIZE, 960, 800, 640):
            frame = im if side == SIZE else im.resize((side, side))
            for quality in (QUALITY, 80, 74, 68, 60):
                out = io.BytesIO()
                frame.save(out, format="JPEG", quality=quality, optimize=True)
                data = out.getvalue()
                if len(data) <= MAX_BYTES:
                    return data
        return data
    except Exception:  # noqa: BLE001 - a preview is never worth a 500
        log.exception("link preview: could not draw")
        return None


# --- The player card, for Instagram and Snapchat stories (11.12 notes) ------
#
# The owner: a story should carry the card a FAM link makes in Messages - the
# picture with the logo and the play button, the title and the site under it
# - not the words-only story card (`sharing.story_card`). `card` is that
# bubble on its own, rounded, with clear corners: Instagram takes it as a
# sticker the person can move and size. `story` is it centred on a 9:16
# ground (the picture blurred and darkened behind it), for Snapchat and the
# share sheet, which take a whole picture.

#: The card's width, its corner, and the strip under the picture.
CARD_WIDTH = 900
CARD_RADIUS = 46
STRIP = (236, 232, 224)
STRIP_TITLE = (24, 21, 31)
STRIP_HOST = (120, 114, 128)
STORY = (1080, 1920)


def _card(picture: Optional[bytes], title: str, minutes: float, host: str):
    """The card as an RGBA image (clear outside its rounded corners)."""
    from PIL import Image, ImageDraw

    square = _preview(picture, title, minutes).resize((CARD_WIDTH, CARD_WIDTH), Image.LANCZOS)
    measure = ImageDraw.Draw(square)
    title_font = _font("Geist-SemiBold.ttf", 46)
    host_font = _font("Geist-Regular.ttf", 34)
    pad = 44
    # Every line of the title (11.14): the strip grows to hold it.
    lines = _wrap(measure, title or "A FAM episode", title_font, CARD_WIDTH - 2 * pad)
    strip_h = 40 + 58 * len(lines) + 12 + 42 + 36
    card = Image.new("RGBA", (CARD_WIDTH, CARD_WIDTH + strip_h), STRIP + (255,))
    card.paste(square, (0, 0))
    d = ImageDraw.Draw(card)
    y = CARD_WIDTH + 40
    for line in lines:
        d.text((pad, y), line, font=title_font, fill=STRIP_TITLE)
        y += 58
    d.text((pad, y + 12), host or "familiarize.net", font=host_font, fill=STRIP_HOST)
    mask = Image.new("L", card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, card.size[0] - 1, card.size[1] - 1),
                                           radius=CARD_RADIUS, fill=255)
    card.putalpha(mask)
    return card


def card(picture: Optional[bytes], title: str, minutes: float,
         host: str = "") -> Optional[bytes]:
    """The player card as PNG bytes (a sticker); `None` only if Pillow fails."""
    try:
        out = io.BytesIO()
        _card(picture, title, minutes, host).save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - never worth a 500
        log.exception("story card: could not draw")
        return None


def _ground(picture: Optional[bytes]):
    """The story's 9:16 ground: the picture filling it, blurred and
    darkened, else FAM's dark ground with a copper glow."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

    w, h = STORY
    if picture:
        try:
            im = ImageOps.exif_transpose(Image.open(io.BytesIO(picture))).convert("RGB")
            im = ImageOps.fit(im, (w // 4, h // 4), Image.LANCZOS)
            im = im.filter(ImageFilter.GaussianBlur(14)).resize((w, h), Image.LANCZOS)
            return ImageEnhance.Brightness(im).enhance(0.42)
        except Exception:  # noqa: BLE001 - the plain ground instead
            pass
    im = Image.new("RGB", (w, h), GROUND)
    ImageDraw.Draw(im).ellipse((-500, -400, 1100, 1100), fill=(84, 66, 52))
    return im.filter(ImageFilter.GaussianBlur(180))


def story(picture: Optional[bytes], title: str, minutes: float,
          host: str = "") -> Optional[bytes]:
    """The whole 1080x1920 story, the card centred, as JPEG bytes."""
    try:
        from PIL import Image, ImageFilter

        ground = _ground(picture)
        c = _card(picture, title, minutes, host)
        x = (STORY[0] - c.size[0]) // 2
        y = (STORY[1] - c.size[1]) // 2
        shadow = Image.new("L", ground.size, 0)
        shadow.paste(c.getchannel("A"), (x, y + 18))
        shadow = shadow.filter(ImageFilter.GaussianBlur(28))
        ground.paste(Image.new("RGB", ground.size, (0, 0, 0)), (0, 0),
                     shadow.point(lambda v: int(v * 0.55)))
        ground.paste(c, (x, y), c)
        out = io.BytesIO()
        ground.save(out, format="JPEG", quality=QUALITY, optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - never worth a 500
        log.exception("story: could not draw")
        return None
