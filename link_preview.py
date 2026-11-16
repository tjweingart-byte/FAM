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
    cut. A shared episode's title is never cut (§615): its callers pass no
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
#: smaller, never cut (§615).
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
    pill = _length(minutes)
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


def _jpeg(im) -> bytes:
    """`im` as a JPEG under `MAX_BYTES` where that can be had: quality
    first; a picture still too big after that (very fine detail) is drawn
    smaller, which every previewer scales anyway."""
    data = b""
    w, h = im.size
    for scale in (1.0, 0.8, 2 / 3, 8 / 15):
        frame = im if scale == 1.0 else im.resize((round(w * scale), round(h * scale)))
        for quality in (QUALITY, 80, 74, 68, 60):
            out = io.BytesIO()
            frame.save(out, format="JPEG", quality=quality, optimize=True)
            data = out.getvalue()
            if len(data) <= MAX_BYTES:
                return data
    return data


def draw(picture: Optional[bytes], title: str, minutes: float) -> Optional[bytes]:
    """The preview as JPEG bytes; `None` only if Pillow itself fails."""
    try:
        return _jpeg(_preview(picture, title, minutes))
    except Exception:  # noqa: BLE001 - a preview is never worth a 500
        log.exception("link preview: could not draw")
        return None


# --- The wide card, for Facebook (§621) -------------------------------------
#
# Facebook draws a link's picture at 1.91:1 and crops a square one to that
# strip: the badge and the play button of the square preview fell outside
# it, and what was left was a picture with nothing saying it is audio. A
# group's admin, after a post of a FAM link reached 168 people with no
# clicks: "users scroll past them without a visual hook. Adding the episode
# title directly on the thumbnail will definitely help." So Facebook's
# crawler is named this picture instead (`sharing.wants_wide`): 1200x630,
# the owner's pick of two drawn layouts - **the picture beside the words**:
# the episode's picture, square and untouched, on the left; the wordmark,
# the length, the title and the play button on FAM's ground on the right.
# With no picture of its own, the same words across the whole card.
#
# **A long title is cut here, with an ellipsis** (the owner, on seeing one
# set small enough to fit: "way too much text"). This is the one drawing
# where §615's "never cut" gives way: a feed is scrolled, not read, and
# Facebook prints the whole title under the picture anyway. Messages and
# the rest keep the wordless square (§559).

WIDE = (1200, 630)
WIDE_MARGIN = 50
#: Beside a picture: the title's sizes, largest first, the height its lines
#: share, and the most lines it is given at the smallest size.
WIDE_SIZES = (58, 52, 46, 40)
WIDE_ROOM = 260
WIDE_LINES = 4
#: With no picture, across the card: larger, and three lines.
WIDE_COVER_SIZES = (76, 66)
WIDE_COVER_ROOM = 270
WIDE_COVER_LINES = 3
WIDE_COVER_WIDTH = 860


def _length(minutes: float) -> str:
    # A half minute stays a half minute (§332): "1.5 MIN EPISODE".
    try:
        length = max(0.5, float(minutes or 1))
    except (TypeError, ValueError):
        length = 1.0
    return f"{length:g} MIN EPISODE"


def _clipped(d, text: str, sizes, width: int, room: int, most: int):
    """`(font, lines, line height)`: the largest size whose lines fit in
    `room`; at the smallest, at most `most` lines, the last ending in an
    ellipsis."""
    font, lines, step = _fitted(d, text, "BricolageGrotesque-SemiBold.ttf",
                                sizes, width, room)
    if len(lines) > most:
        lines = _wrap(d, text, font, width, most)
    return font, lines, step


def _wide(picture: Optional[bytes], title: str, minutes: float):
    """The wide card as an image."""
    from PIL import Image, ImageDraw, ImageFilter, ImageOps

    w, h = WIDE
    title = title or "A FAM episode"
    left = None
    if picture:
        try:
            src = ImageOps.exif_transpose(Image.open(io.BytesIO(picture))).convert("RGB")
            left = ImageOps.fit(src, (h, h), Image.LANCZOS)
        except Exception:  # noqa: BLE001 - an unreadable picture
            log.warning("link preview: the episode's picture would not "
                        "open; drawing the wide cover instead")
    if left is None:
        im = Image.new("RGB", WIDE, GROUND)
        ImageDraw.Draw(im).ellipse((-400, -500, 800, 500), fill=(84, 66, 52))
        im = im.filter(ImageFilter.GaussianBlur(140))
        _badge(im)
        d = ImageDraw.Draw(im)
        _play(d, cx=w - 150, cy=h - 150, r=84)
        font, lines, step = _clipped(d, title, WIDE_COVER_SIZES, WIDE_COVER_WIDTH,
                                     WIDE_COVER_ROOM, WIDE_COVER_LINES)
        x, y = 60, h - 60 - len(lines) * step
        small = _font("GeistMono-Bold.ttf", 26)
    else:
        im = Image.new("RGB", WIDE, GROUND)
        im.paste(left, (0, 0))
        x = h + WIDE_MARGIN
        _wordmark(im, x, 54, 44)
        d = ImageDraw.Draw(im)
        _play(d, cx=x + 62, cy=h - 112, r=58)
        font, lines, step = _clipped(d, title, WIDE_SIZES, w - x - WIDE_MARGIN,
                                     WIDE_ROOM, WIDE_LINES)
        y = 180
        small = _font("GeistMono-Bold.ttf", 24)
    d.text((x, y - 44), _length(minutes), font=small, fill=COPPER)
    for line in lines:
        d.text((x, y), line, font=font, fill=PAPER)
        y += step
    return im


def wide(picture: Optional[bytes], title: str, minutes: float) -> Optional[bytes]:
    """The wide card as JPEG bytes; `None` only if Pillow itself fails."""
    try:
        return _jpeg(_wide(picture, title, minutes))
    except Exception:  # noqa: BLE001 - a preview is never worth a 500
        log.exception("link preview: could not draw the wide card")
        return None


# --- The photo for a native post (§622) -------------------------------------
#
# Facebook holds back a post that links out, whatever the link looks like:
# the group admin's post of a FAM link reached 168 people where his posts
# reach thousands. What is not held back is a photo. So this is the episode
# as a photo somebody posts themselves - 1080x1350, the tallest a feed
# shows whole - with the link put in the first comment by the person
# posting. The picture on top; under it the length, the title, the
# episode's one line (its hook, as a card draws it) and where to listen,
# because the photo itself carries no link. FAM posts nothing: this is a
# file a person downloads.

POST = (1080, 1350)
POST_PICTURE = 720
POST_MARGIN = 64
POST_TITLE_SIZES = (72, 64, 56)
POST_TITLE_ROOM = 250
POST_TITLE_LINES = 3
POST_HOOK_LINES = 3


def _post(picture: Optional[bytes], title: str, hook: str, minutes: float,
          host: str):
    """The photo as an image."""
    from PIL import Image, ImageDraw, ImageFilter, ImageOps

    w, h = POST
    m = POST_MARGIN
    title = title or "A FAM episode"
    im = Image.new("RGB", POST, GROUND)
    top = 0
    if picture:
        try:
            src = ImageOps.exif_transpose(Image.open(io.BytesIO(picture))).convert("RGB")
            im.paste(ImageOps.fit(src, (w, POST_PICTURE), Image.LANCZOS), (0, 0))
            top = POST_PICTURE
        except Exception:  # noqa: BLE001 - an unreadable picture
            log.warning("link preview: the episode's picture would not "
                        "open; drawing the post without it")
    if not top:
        ImageDraw.Draw(im).ellipse((-500, -600, 900, 700), fill=(84, 66, 52))
        im = im.filter(ImageFilter.GaussianBlur(170))
        _wordmark(im, m, 110, 70)
    d = ImageDraw.Draw(im)
    width = w - 2 * m
    # With a picture the words start under it; without one they are set
    # larger and lower, as the cover's are.
    sizes = POST_TITLE_SIZES if top else (96, 84, 72)
    room = POST_TITLE_ROOM if top else 420
    most = POST_TITLE_LINES if top else 4
    font, lines, step = _clipped(d, title, sizes, width, room, most)
    y = top + 62 if top else 330
    d.text((m, y), _length(minutes), font=_font("GeistMono-Bold.ttf", 28), fill=COPPER)
    y += 56
    for line in lines:
        d.text((m, y), line, font=font, fill=PAPER)
        y += step
    hook = " ".join((hook or "").split())
    if hook:
        hook_font = _font("Geist-Regular.ttf", 36)
        y += 18
        # As many whole lines as there is room for above the foot.
        fits = min(POST_HOOK_LINES, (h - 190 - y) // 48)
        for line in (_wrap(d, hook, hook_font, width, fits) if fits > 0 else ()):
            d.text((m, y), line, font=hook_font, fill=MUTED)
            y += 48
    # The foot: the play button, and where to listen - the photo has no link.
    cy = h - 108
    _play(d, cx=m + 56, cy=cy, r=52)
    x = m + 56 + 52 + 30
    d.text((x, cy - 46), "LISTEN TO THE FULL EPISODE",
           font=_font("GeistMono-Bold.ttf", 24), fill=COPPER)
    d.text((x, cy - 8), host or "familiarize.net",
           font=_font("Geist-SemiBold.ttf", 36), fill=PAPER)
    if top:
        _wordmark(im, w - m - wordmark_width(40), cy - 20, 40)
    return im


def post(picture: Optional[bytes], title: str, hook: str, minutes: float,
         host: str = "") -> Optional[bytes]:
    """The photo for a native post as JPEG bytes; `None` only if Pillow
    fails. Not held to `MAX_BYTES`: it is uploaded by a person, not fetched
    by a previewer."""
    try:
        out = io.BytesIO()
        _post(picture, title, hook, minutes, host).save(
            out, format="JPEG", quality=90, optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - never worth a 500
        log.exception("link preview: could not draw the post photo")
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
    # Every line of the title (§615): the strip grows to hold it.
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
