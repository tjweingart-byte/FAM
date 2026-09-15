"""Turning artwork that *looks* like one line into artwork that *is* one line.

This is the part of the feature that cannot be skipped. An image model asked
for continuous-line art returns something that reads as one stroke and is, as
geometry, a few hundred disconnected marks. The player does not need a picture
of a line; it needs an ordered route, because the whole transport - pause,
seek, rewind, 2x - is "reveal the first N% of one path" and there is no such
thing as the first N% of a pile of strokes.

So: raster in, one ordered `d` attribute out.

    decode -> isolate ink -> despeckle -> skeletonise -> graph
           -> bridge small gaps -> eulerise -> traverse
           -> smooth -> simplify -> fit curves -> one SVG path

Three decisions in there are worth stating, because each is a place the naive
answer is wrong:

* **The pen never leaves the paper, so retracing is allowed and jumping is
  not.** This is the hard one, and it is a product-quality constraint rather
  than a tuning preference: a listener watching the line travel must never see
  it strike out across the negative space. A mark that was not in the artwork
  is a scar, and one of them ruins an otherwise good animation.

  A drawing whose graph has odd-degree vertices has no Euler path; the
  route-inspection ("Chinese postman") answer is to duplicate the cheapest set
  of *existing* edges until one exists. Retracing a line the artist drew is
  invisible, so duplicating edges is free in the only currency that matters.
  Drawing a straight line across empty ivory to reach the next piece is not,
  and this module will never do it. In order of preference: follow the
  linework; retrace existing geometry to get back to undrawn work, by the
  shortest such route; bridge only a gap tiny enough to read as an accident of
  the artwork; otherwise reject the art and regenerate it.

  Three things enforce that, because one of them is a single point of failure:
  `_shortest_paths` prices a bridge at `BRIDGE_RETRACE_PENALTY` times its
  length so the matching retraces ink in preference to repairs, `_assemble`
  refuses to *build* a route whose consecutive strokes do not touch, and
  `visual_validator` refuses to *ship* one whose bridging is visible. The
  route also carries its own edge identities out of Hierholzer rather than
  being reconstructed from the vertex sequence afterwards - with parallel
  edges, which a duplicating route is full of, reconstruction can pick the
  wrong edge, and the mark it leaves is exactly the scar above.
* **Skeletonise, do not trace the outline.** A stroke has two sides; its
  outline is a long thin loop that draws every line twice and looks like it.
  Zhang-Suen thinning collapses each stroke to its centreline, which is the
  thing the pen actually travelled.
* **Smooth, then simplify, then fit - in that order.** The skeleton is a
  staircase of whole pixels, and emitted directly it is visibly jagged at
  1536px: exactly the "crude vector tracing" the brief rules out. The order
  matters and the obvious one is wrong. Simplifying first *locks the staircase
  in*, because every step deviates by about a pixel - more than any tolerance
  small enough to keep real detail - so the steps are the points that survive
  and the curve fitting then dutifully draws a tremor. Smoothing first removes
  deviation at the scale of one sample, Douglas-Peucker then removes the
  redundant points, and centripetal Catmull-Rom puts the curvature back as
  cubic Béziers that stay smooth at any size.

Dependencies: numpy, which FAM already requires. Pillow is used when present
and is not required - there is a PNG decoder here for the case where it is
absent, because a deployment that cannot draw a picture because of an optional
imaging library would be a feature that ships broken more often than not.
"""
from __future__ import annotations

import logging
import math
import struct
import time
import zlib
from dataclasses import dataclass, field

import numpy as np

import visual_style

log = logging.getLogger(__name__)


class LineProcessingError(RuntimeError):
    """The artwork could not become one continuous line.

    Carries `reason`, a short machine-readable code, so `visuals` can decide
    whether the answer is a different rung of the retry ladder or giving up.
    """

    def __init__(self, message: str, reason: str = "unprocessable") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class LineArt:
    """One canonical drawing.

    `d` is the whole product. Everything else is how it got there, kept because
    "is this a good vectorisation" is a question that needs numbers - a path
    with 40% retracing and one with 2% look identical in a thumbnail and feel
    completely different being drawn.
    """

    d: str = ""
    view_box: str = f"0 0 {visual_style.VIEWBOX} {visual_style.VIEWBOX}"
    #: The flattened polyline in viewBox units, for rasterising the thumbnail
    #: from the *same* geometry the player reveals. This is what makes "the
    #: final frame equals the thumbnail" true by construction rather than by
    #: two renderers happening to agree.
    points: list = field(default_factory=list)
    length: float = 0.0
    curves: int = 0
    components: int = 1
    bridged: int = 0
    #: Share of the route that is drawn over line already drawn. Small is
    #: invisible; large means the art was badly connected.
    retraced: float = 0.0
    #: The longest single bridge in the finished drawing, in viewBox units, and
    #: the total of all of them. These are the only marks in the route that
    #: were not in the artwork, so they are the numbers the no-scars rule is
    #: enforced on - `visual_validator` rejects a drawing whose longest bridge
    #: is something a listener could see as a line across empty ivory.
    max_bridge: float = 0.0
    bridged_length: float = 0.0
    #: How much of the source artwork the finished curve still passes near,
    #: and how much of the finished curve passes nowhere near the artwork.
    #: The answer to "did the beautiful drawing survive being vectorised",
    #: measured rather than eyeballed. See `fidelity`.
    fidelity: float = 1.0
    invented: float = 0.0
    #: The longest unbroken stretch of retracing, as a share of the reveal -
    #: how long the listener goes with the audio playing and no new line
    #: appearing. Retracing is fine; retracing for a long time is a stall.
    longest_stall: float = 0.0
    #: Which rung of `DETAIL_LADDER` produced this. 0 is the default; anything
    #: higher means the default lost detail and the processing was retried
    #: gently rather than the artwork being redrawn simpler.
    detail: int = 0
    ink_share: float = 0.0
    warnings: list = field(default_factory=list)
    timings_ms: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "view_box": self.view_box,
            "length": round(self.length, 1),
            "curves": self.curves,
            "components": self.components,
            "bridged": self.bridged,
            "max_bridge": round(self.max_bridge, 2),
            "bridged_length": round(self.bridged_length, 2),
            "fidelity": round(self.fidelity, 4),
            "invented": round(self.invented, 4),
            "longest_stall": round(self.longest_stall, 4),
            "detail": self.detail,
            "retraced": round(self.retraced, 4),
            "ink_share": round(self.ink_share, 5),
            "warnings": list(self.warnings),
            "timings_ms": dict(self.timings_ms),
        }


# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------
#: Side length the mask is worked at. High enough to keep fine detail, low
#: enough that thinning a full-size generation does not take seconds. The
#: output is resolution-independent - it is a vector - so this trades
#: processing time against how much detail survives, not against final quality.
WORK_SIZE = 720
#: Ink is anything this much darker than the paper. Relative rather than
#: absolute: the model is asked for ivory and returns something near it, and a
#: fixed threshold would either lose a light line or swallow a warm ground.
INK_RATIO = 0.74
#: Specks below this many pixels are dust, not drawing.
MIN_SPECK = 12
#: A skeleton twig shorter than this is a thinning artefact - the little hairs
#: thinning grows at a stroke end - and not a stroke anyone drew.
SPUR_PIXELS = 7
#: How far apart two loose ends may be and still be called a gap, as a share of
#: the canvas. Beyond this it is not a gap, it is two drawings.
#:
#: **This is the artistic limit, and it is deliberately far below what the
#: validator will tolerate.** `visual_validator.MAX_BRIDGE_UNITS` is a
#: rejection boundary - the point past which a mark is provably a scar - and
#: reading it as permission to bridge anything shorter is exactly backwards.
#: A bridge is only ever the repair of a *genuinely tiny accidental* gap
#: between endpoints that visually belong to the same intended stroke. Everything
#: else retraces existing ink, and art that needs more than that is regenerated.
#:
#: Tightened 0.035 -> 0.02 -> 0.01. At 1% of a 720px working image this is
#: about seven pixels, which lands as ten units on the finished canvas: on the
#: player that is under three screen pixels of travel, and on a tile it is
#: under two. That is the scale at which a join reads as the artist having
#: closed the line themselves rather than as a mark going somewhere.
#:
#: Note it is well inside `visual_validator.MAX_BRIDGE_UNITS` (18) and is meant
#: to be. The backstop is where a mark becomes provably a scar; this is where a
#: repair stops being invisible, and the second number is the one routing obeys.
BRIDGE_SHARE = 0.01
#: How nearly a gap must point the way the pen was already going, as a cosine.
#:
#: The distance test alone cannot tell a break in one stroke from two unrelated
#: ends that happen to pass near each other, and those want opposite answers: the
#: first is a repair, the second is a mark across the picture that is short
#: enough to sneak through. So the loose end's own direction is measured, and a
#: gap is only closed when the pen was heading at the thing it is being joined
#: to. 0.5 is sixty degrees - generous enough for a curve that was interrupted
#: mid-bend, tight enough to refuse a right-angled hop onto a passing line.
BRIDGE_ALIGNMENT = 0.5
#: The outer bound on a bridge, in final viewBox units, lives in
#: `visual_validator.MAX_BRIDGE_UNITS` and is enforced there - `LineArt` merely
#: reports `max_bridge` for it to judge. It used to be declared here as well,
#: with the same name and the same value and nothing reading it, which is the
#: shape of a constant that silently stops agreeing with itself.
#: How much more expensive a bridge is to retrace than real ink, when the
#: route-inspection matching is choosing which edges to duplicate. Retracing a
#: line the artist drew is invisible; retracing a bridge draws FAM's own repair
#: a second time, in the middle of empty ivory, which is the most visible mark
#: in the picture. Same length, different cost - rule 5 of the traversal
#: policy, "least visual disruption".
BRIDGE_RETRACE_PENALTY = 6.0
#: How far apart two chains may be where they meet before the route is called
#: broken, in working pixels. **A floor, not the whole answer** - the real
#: bound is computed per drawing by `node_tolerance`, because it depends on the
#: artwork.
#:
#: Not zero, and the reason is structural rather than a fudge: a chain begins
#: and ends on an actual *pixel* of a junction cluster, while the node it is
#: attached to is that cluster's centroid. Two chains meeting at one node
#: therefore meet a little apart by construction, bounded by the cluster's own
#: size - and a cluster is bigger in a dense drawing, where several strokes
#: pass close together, than in a sparse one.
#:
#: Which is why a constant here was wrong in the one direction that matters:
#: four pixels is right for a single figure on an empty page and too tight for
#: a scene, so rich artwork was being rejected for being rich. The engineering
#: must serve the art; this is one of the places it was quietly doing the
#: opposite.
CONTIGUOUS_TOLERANCE = 4.0
#: A component smaller than this share of the total may be dropped as debris.
#: Anything bigger is the artwork being genuinely in pieces, which is a
#: regeneration and not something to paper over.
MINOR_COMPONENT_SHARE = 0.04
#: Douglas-Peucker tolerance, in viewBox units (the canvas is 1000 wide).
SIMPLIFY_EPSILON = 1.1
#: Points per cubic when flattening for the thumbnail. Eight is past the point
#: where more is visible at 1536px.
FLATTEN_STEPS = 8


# --------------------------------------------------------------------------
# Raster in
# --------------------------------------------------------------------------
def decode_image(data: bytes) -> np.ndarray:
    """Image bytes to an (H, W) float array of luminance in 0..1.

    Pillow when it is installed, because it reads everything; the built-in PNG
    reader otherwise, because the provider is asked for PNG and a missing
    optional library must not be the reason an episode has no picture.
    """
    try:
        from PIL import Image  # type: ignore
        import io

        with Image.open(io.BytesIO(data)) as img:
            grey = img.convert("L")
            return np.asarray(grey, dtype=np.float32) / 255.0
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 - fall through to the built-in reader
        log.debug("Pillow could not read the image (%s); using the built-in reader", exc)
    return _decode_png(data)


def colour_share(data: bytes) -> float | None:
    """How much of the image is meaningfully coloured, or `None` if unknown.

    FAM line art is charcoal on ivory: every pixel is a neutral, and anything
    saturated is the model having ignored the style. Measured as the share of
    pixels whose max and min channels differ by more than a hair.

    Returns `None` rather than `0.0` when the colour cannot be read - the
    built-in PNG reader below hands back luminance, so on a machine without
    Pillow there is no colour to measure. Zero-because-neutral and
    zero-because-we-did-not-look are different answers, and reporting the
    second as the first is exactly the silent pass this project keeps paying
    for.
    """
    try:
        from PIL import Image  # type: ignore
        import io

        with Image.open(io.BytesIO(data)) as img:
            rgb = np.asarray(img.convert("RGB"), dtype=np.int16)
    except ImportError:
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("could not read colour from the artwork (%s)", exc)
        return None
    if rgb.size == 0:
        return None
    spread = rgb.max(axis=2) - rgb.min(axis=2)
    return float((spread > 24).mean())


def structure(mask: np.ndarray) -> tuple[int, int]:
    """How many junctions and loose ends the drawing has.

    `(junctions, endpoints)` - skeleton pixels where three or more lines meet,
    and where one line stops.

    It answers a question density cannot, and the difference matters because
    the statistical measure gets the hard case wrong. A **pictogram is
    structurally a closed outline**: nothing meets anything, nothing stops
    anywhere, so both numbers are zero. Measured, a plain circle scores
    `(0, 0)`; a spare figure study - one gesture, the page left empty, exactly
    the composition a density heuristic marks down hardest - scores `(1, 5)`,
    and a full scene scores `(81, 18)`.

    So "is this provably an icon" is a structural fact rather than a threshold
    somebody guessed, and it cannot misfire on restraint. That is what makes it
    safe to *refuse* on, where `line_crossings` is only safe to comment on.

    It costs a thinning pass - about twenty milliseconds - which is the price
    of the gate being able to tell those two apart.
    """
    skeleton = prune_spurs(skeletonise(mask))
    if not skeleton.any():
        return 0, 0
    degrees = degree_map(skeleton)
    values = degrees[skeleton]
    return int((values >= 3).sum()), int((values == 1).sum())


def line_crossings(mask: np.ndarray) -> float:
    """How many separate runs of ink a straight scan meets, on average.

    A cheap, honest measure of how much picture is on the page, and the one
    that tells an icon from an illustration. A circle, a cloud or a lightbulb
    is crossed twice by almost every scan line; a scene with a figure, a desk
    and a window behind it is crossed six, eight, a dozen times. It costs one
    array diff, which is what makes it usable as a gate in front of the work
    rather than a measurement taken afterwards.

    Averaged over the rows and columns that contain any ink at all, so a
    drawing that leaves the top third of the canvas empty - which the house
    style positively asks for - is not marked down for it.
    """
    if not mask.any():
        return 0.0
    totals = []
    for axis_mask in (mask, mask.T):
        padded = np.zeros((axis_mask.shape[0], axis_mask.shape[1] + 1), dtype=bool)
        padded[:, :-1] = axis_mask
        starts = padded[:, :-1] & ~np.concatenate(
            [np.zeros((axis_mask.shape[0], 1), dtype=bool), padded[:, :-2]], axis=1)
        per_line = starts.sum(axis=1)
        live = per_line[per_line > 0]
        totals.append(float(live.mean()) if live.size else 0.0)
    return sum(totals) / 2.0


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _decode_png(data: bytes) -> np.ndarray:
    """A PNG reader for the ordinary case, in the standard library.

    Handles what an image API actually returns: 8-bit, non-interlaced,
    grey/RGB/RGBA. Anything else raises rather than guessing - a picture
    decoded wrongly would reach the skeletoniser as noise and be rejected there
    with a misleading reason.
    """
    if not data.startswith(_PNG_SIGNATURE):
        raise LineProcessingError(
            "the provider returned an image FAM cannot read without Pillow "
            "(install Pillow, or have the provider return PNG)", "undecodable")
    pos = len(_PNG_SIGNATURE)
    width = height = depth = colour = interlace = 0
    idat = bytearray()
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if depth != 8 or interlace != 0 or colour not in (0, 2, 4, 6):
        raise LineProcessingError(
            f"unsupported PNG (depth={depth} colour={colour} interlace={interlace}); "
            "install Pillow to read it", "undecodable")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[colour]
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    out = np.zeros((height, stride), dtype=np.uint8)
    previous = np.zeros(stride, dtype=np.uint8)
    at = 0
    for row in range(height):
        filter_type = raw[at]
        at += 1
        line = bytearray(raw[at:at + stride])
        at += stride
        _unfilter(filter_type, line, previous, channels)
        out[row] = np.frombuffer(bytes(line), dtype=np.uint8)
        previous = out[row]
    pixels = out.reshape(height, width, channels).astype(np.float32) / 255.0
    if channels == 1:
        grey = pixels[:, :, 0]
    elif channels == 2:
        grey = pixels[:, :, 0]
    else:
        grey = (0.299 * pixels[:, :, 0] + 0.587 * pixels[:, :, 1]
                + 0.114 * pixels[:, :, 2])
    return grey


def _unfilter(filter_type: int, line: bytearray, previous: np.ndarray,
              channels: int) -> None:
    """PNG row filters, in place. Sequential by construction - Sub, Average
    and Paeth all read the byte this one just produced."""
    stride = len(line)
    if filter_type == 0:
        return
    if filter_type == 2:  # Up: the whole row at once
        line[:] = ((np.frombuffer(bytes(line), dtype=np.uint8).astype(np.int16)
                    + previous.astype(np.int16)) % 256).astype(np.uint8).tobytes()
        return
    for i in range(stride):
        left = line[i - channels] if i >= channels else 0
        up = int(previous[i]) if previous.size else 0
        upleft = int(previous[i - channels]) if (i >= channels and previous.size) else 0
        if filter_type == 1:
            line[i] = (line[i] + left) & 0xFF
        elif filter_type == 3:
            line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
        elif filter_type == 4:
            p = left + up - upleft
            pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
            best = left if (pa <= pb and pa <= pc) else (up if pb <= pc else upleft)
            line[i] = (line[i] + best) & 0xFF
        else:
            raise LineProcessingError(f"unknown PNG filter {filter_type}",
                                      "undecodable")


def ink_mask(grey: np.ndarray) -> tuple[np.ndarray, float]:
    """Which pixels are line, and how much of the canvas that is.

    The paper level is read from the image rather than assumed: the model is
    asked for a specific ivory and returns something close to it, and a
    threshold pinned to a constant would swallow a warm ground or lose a light
    line the first time it drifts.
    """
    paper = float(np.percentile(grey, 88))
    if paper <= 0.05:
        # An almost-black image. Nothing here is a FAM illustration, and
        # thresholding it would produce a mask of everything.
        raise LineProcessingError("the artwork is almost entirely dark",
                                  "not_line_art")
    mask = grey < paper * INK_RATIO
    return mask, float(mask.mean())


def _resize_mask(mask: np.ndarray, size: int) -> np.ndarray:
    """Down-sample by "any ink in the block wins".

    Averaging would thin a one-pixel line into something below any threshold,
    which is how a perfectly good drawing becomes an empty mask.
    """
    h, w = mask.shape
    if max(h, w) <= size:
        return mask
    scale = size / float(max(h, w))
    out_h, out_w = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
    rows = (np.arange(h) * out_h // h)
    cols = (np.arange(w) * out_w // w)
    # Counted with bincount rather than scattered with `np.logical_or.at`,
    # which is unbuffered and takes seconds on a megapixel.
    flat = (rows[:, None] * out_w + cols[None, :]).ravel()
    hits = np.bincount(flat[mask.ravel()], minlength=out_h * out_w)
    return (hits > 0).reshape(out_h, out_w)


def prepare_mask(data: bytes) -> tuple[np.ndarray, float]:
    """Image bytes to the ink mask everything downstream reasons about.

    Decode, threshold, scale to `WORK_SIZE`, drop the dust. Returns the mask
    and the share of the *original* image that was ink.

    One function rather than four lines repeated, and the reason is correctness
    rather than tidiness: `visual_validator.screen_source` judges whether the
    artwork is worth drawing and `process` then draws it, and if those two ever
    prepared the mask differently the gate would be screening something other
    than the thing that gets vectorised. It also stops the validator reaching
    into `_resize_mask`, which is private for a reason.
    """
    mask, ink_share = ink_mask(decode_image(data))
    return despeckle(_resize_mask(mask, WORK_SIZE)), ink_share


# --------------------------------------------------------------------------
# Skeleton
# --------------------------------------------------------------------------
def _neighbours(padded: np.ndarray) -> list:
    """The eight neighbours of every pixel, as eight aligned arrays, clockwise
    from north - the order Zhang-Suen's A(P1) transition count assumes."""
    return [
        padded[:-2, 1:-1], padded[:-2, 2:], padded[1:-1, 2:], padded[2:, 2:],
        padded[2:, 1:-1], padded[2:, :-2], padded[1:-1, :-2], padded[:-2, :-2],
    ]


def skeletonise(mask: np.ndarray, max_passes: int = 80) -> np.ndarray:
    """Zhang-Suen thinning, vectorised.

    Written out here rather than imported from scikit-image because that is a
    large dependency for one function, and this one is 30 lines of numpy that
    the tests can pin exactly.
    """
    img = mask.copy()
    for _ in range(max_passes):
        changed = False
        for step in (0, 1):
            padded = np.pad(img, 1)
            p2, p3, p4, p5, p6, p7, p8, p9 = _neighbours(padded)
            ring = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            b = sum(n.astype(np.uint8) for n in ring[:8])
            a = sum(((~ring[i]) & ring[i + 1]).astype(np.uint8) for i in range(8))
            base = img & (b >= 2) & (b <= 6) & (a == 1)
            if step == 0:
                cond = base & ~(p2 & p4 & p6) & ~(p4 & p6 & p8)
            else:
                cond = base & ~(p2 & p4 & p8) & ~(p2 & p6 & p8)
            if cond.any():
                img &= ~cond
                changed = True
        if not changed:
            break
    return img


def degree_map(skel: np.ndarray) -> np.ndarray:
    """How many ways on there are from each skeleton pixel.

    **A diagonal neighbour does not count when an orthogonal one already
    reaches it.** Without that rule a plain staircase - a horizontal run
    turning into a diagonal one, which is what every curve looks like after
    thinning - registers as a T-junction, and a drawing of one smooth line
    comes back as nine hundred junctions and two hundred branches. It was not
    the thinning that was wrong; it was counting eight neighbours when the pen
    only has four ways to go.
    """
    padded = np.pad(skel, 1)
    north, north_east, east, south_east, south, south_west, west, north_west = \
        _neighbours(padded)
    # A diagonal step is real only when neither of the two orthogonal steps
    # that would go round it exists.
    north_east = north_east & ~(north | east)
    south_east = south_east & ~(south | east)
    south_west = south_west & ~(south | west)
    north_west = north_west & ~(north | west)
    total = sum(n.astype(np.uint8) for n in
                (north, north_east, east, south_east,
                 south, south_west, west, north_west))
    return total * skel


NEIGHBOUR_OFFSETS = ((-1, 0), (-1, 1), (0, 1), (1, 1),
                     (1, 0), (1, -1), (0, -1), (-1, -1))


def _components(pixels: set) -> list:
    """8-connected groups of the given pixels."""
    seen, groups = set(), []
    for start in pixels:
        if start in seen:
            continue
        stack, group = [start], []
        seen.add(start)
        while stack:
            y, x = stack.pop()
            group.append((y, x))
            for dy, dx in NEIGHBOUR_OFFSETS:
                nxt = (y + dy, x + dx)
                if nxt in pixels and nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        groups.append(group)
    return groups


def despeckle(mask: np.ndarray, minimum: int = MIN_SPECK) -> np.ndarray:
    """Drop ink islands too small to be anything anyone drew."""
    pixels = {(int(y), int(x)) for y, x in zip(*np.nonzero(mask))}
    if not pixels:
        return mask
    out = np.zeros_like(mask)
    for group in _components(pixels):
        if len(group) >= minimum:
            ys, xs = zip(*group)
            out[np.array(ys), np.array(xs)] = True
    return out


def prune_spurs(skel: np.ndarray, minimum: int = SPUR_PIXELS) -> np.ndarray:
    """Remove the short hairs thinning grows at stroke ends.

    Iterative because removing one spur can expose another, and bounded
    because a pathological skeleton must not spin.
    """
    img = skel.copy()
    for _ in range(6):
        deg = degree_map(img)
        ends = list(zip(*np.nonzero(deg == 1)))
        removed = False
        for y, x in ends:
            start = (int(y), int(x))
            if not img[start]:
                continue          # already removed as part of another spur
            path = [start]
            prev, cur = None, start
            reached_junction = False
            for _ in range(minimum + 1):
                nbrs = [n for n in _pixel_neighbours(img, cur) if n != prev]
                if len(nbrs) != 1:
                    break
                prev, cur = cur, nbrs[0]
                if deg[cur] >= 3:
                    # The junction itself is where the twig is attached, and
                    # stays: what is removed is everything between here and
                    # the loose end.
                    reached_junction = True
                    break
                path.append(cur)
            if reached_junction and len(path) <= minimum:
                for py, px in path:
                    img[py, px] = False
                removed = True
        if not removed:
            break
    return img


def _pixel_neighbours(img: np.ndarray, at: tuple) -> list:
    """The ways on from one pixel, under the same rule as `degree_map`.

    The two have to agree exactly. A classifier that says "two ways on" and a
    walker that then finds three is a walk that takes an arbitrary turn at
    every bend in the line.
    """
    y, x = at
    h, w = img.shape

    def ink(ny, nx) -> bool:
        return 0 <= ny < h and 0 <= nx < w and bool(img[ny, nx])

    out = []
    for dy, dx in NEIGHBOUR_OFFSETS:
        ny, nx = y + dy, x + dx
        if not ink(ny, nx):
            continue
        if dy and dx and (ink(y + dy, x) or ink(y, x + dx)):
            continue      # redundant diagonal: an orthogonal step goes round it
        out.append((ny, nx))
    return out


# --------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------
@dataclass
class Edge:
    u: int
    v: int
    points: list           # pixel coordinates, u-end first
    bridge: bool = False
    #: A copy made by `eulerise` so the pen can get back to undrawn work. The
    #: geometry is identical to the original's, so drawing it adds no line to
    #: the picture - which is what makes retracing invisible, and also what
    #: makes a long run of it a stretch where nothing appears.
    duplicate: bool = False

    @property
    def length(self) -> float:
        return _polyline_length(self.points)


def build_graph(skel: np.ndarray) -> tuple[list, dict]:
    """The skeleton as a multigraph of chains between junctions and ends.

    Nodes are clusters of pixels whose degree is not two - a junction is
    usually two or three touching pixels after thinning, and treating each as
    its own node would litter the graph with one-pixel edges that survive into
    the traversal as jitter.
    """
    deg = degree_map(skel)
    ink = {(int(y), int(x)) for y, x in zip(*np.nonzero(skel))}
    node_pixels = {p for p in ink if deg[p] != 2}
    node_of: dict = {}
    positions: dict = {}
    for index, group in enumerate(_components(node_pixels)):
        for pixel in group:
            node_of[pixel] = index
        ys = [p[0] for p in group]
        xs = [p[1] for p in group]
        positions[index] = (sum(ys) / len(ys), sum(xs) / len(xs))

    edges: list = []
    consumed: set = set()
    seen_direct: set = set()

    for pixel in sorted(node_pixels):
        start_node = node_of[pixel]
        for neighbour in _pixel_neighbours(skel, pixel):
            if node_of.get(neighbour) == start_node:
                continue
            if neighbour in consumed:
                continue
            path = [pixel, neighbour]
            prev, cur = pixel, neighbour
            steps = 0
            limit = skel.size + 8   # a chain cannot be longer than the image
            while cur not in node_of and steps < limit:
                steps += 1
                consumed.add(cur)
                nbrs = [n for n in _pixel_neighbours(skel, cur) if n != prev]
                # A degree-2 pixel has exactly one way on. More than one means
                # a diagonal shortcut past a junction; take the first and let
                # the junction end the chain on the next step.
                if not nbrs:
                    break
                prev, cur = cur, nbrs[0]
                path.append(cur)
            end_node = node_of.get(cur)
            if end_node is None:
                continue
            if len(path) == 2:
                # Two touching clusters: no interior to consume, so dedupe on
                # the pair of pixels instead.
                key = (min(pixel, cur), max(pixel, cur))
                if key in seen_direct:
                    continue
                seen_direct.add(key)
            edges.append(Edge(start_node, end_node, path))

    # Closed loops with no junction on them have no node pixels at all, so the
    # sweep above never sees them. They are real drawing - a circle is the
    # commonest single shape in this style - so each becomes a self-loop.
    remaining = ink - consumed - node_pixels
    for group in _components(remaining):
        loop = _trace_loop(skel, group)
        if len(loop) < 4:
            continue
        index = len(positions)
        positions[index] = (loop[0][0], loop[0][1])
        edges.append(Edge(index, index, loop))

    return edges, positions


def _trace_loop(skel: np.ndarray, group: list) -> list:
    start = min(group)
    members = set(group)
    path = [start]
    prev, cur = None, start
    while True:
        nbrs = [n for n in _pixel_neighbours(skel, cur)
                if n in members and n != prev]
        if not nbrs:
            break
        prev, cur = cur, nbrs[0]
        if cur == start:
            path.append(start)
            break
        path.append(cur)
        if len(path) > len(group) + 2:
            break
    return path


def _polyline_length(points) -> float:
    total = 0.0
    for (y0, x0), (y1, x1) in zip(points, points[1:]):
        total += math.hypot(y1 - y0, x1 - x0)
    return total


def _degrees(edges: list) -> dict:
    """How many edge-ends meet at each node.

    Counted from the edges rather than from an adjacency list, and that is the
    whole reason this is a function: **a self-loop contributes two to its
    vertex's degree while appearing once in any adjacency list.** Counting the
    list makes every vertex carrying a loop look odd, and an Euler path that
    starts in the wrong place is a drawing that starts in the middle of a line.
    Three copies of this loop used to sit in three functions.
    """
    degree: dict = {}
    for edge in edges:
        degree[edge.u] = degree.get(edge.u, 0) + 1
        degree[edge.v] = degree.get(edge.v, 0) + 1
    return degree


class _Union:
    def __init__(self) -> None:
        self.parent: dict = {}

    def find(self, item):
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent[rb] = ra
        return True

    @classmethod
    def of(cls, edges: list) -> "_Union":
        """Which pieces of the drawing are joined to which."""
        union = cls()
        for edge in edges:
            union.union(edge.u, edge.v)
        return union


#: Never close more than this many gaps in one drawing. A picture needing a
#: dozen repairs is not a picture with gaps in it, it is a picture made of
#: separate marks, and the honest answer to that is to draw it again.
MAX_BRIDGES = 12
#: How finely a chain is searched for an attachment point. Every third pixel is
#: within a pixel and a half of the true nearest point, which is well inside
#: the tolerance a "gap" is judged by.
ATTACH_STRIDE = 3


def _end_direction(edges: list, node: int, positions: dict,
                   look: int = 6) -> tuple:
    """Which way the pen was travelling when it ran out of line at `node`.

    Read a few pixels back along the loose end's own stroke rather than from
    the chain's two endpoints, because the answer wanted is the *local*
    heading: a long stroke that curls at the very end would otherwise report
    the direction of its overall span, which is not where its tip is pointing.

    Returns a unit vector in (row, column), or `(0.0, 0.0)` when the stroke is
    too short to have a direction - which the caller must treat as "cannot
    tell", never as "aligned".
    """
    here = positions[node]
    for edge in edges:
        if edge.u == node:
            points = edge.points
        elif edge.v == node:
            points = list(reversed(edge.points))
        else:
            continue
        back = points[min(look, len(points) - 1)]
        dy, dx = here[0] - back[0], here[1] - back[1]
        norm = math.hypot(dy, dx)
        if norm > 1e-6:
            return (dy / norm, dx / norm)
    return (0.0, 0.0)


def bridge_gaps(edges: list, positions: dict, span: float) -> tuple[list, int]:
    """Close what is obviously a gap, and nothing else.

    **The artistic rule, which is stricter than any threshold in the
    validator.** A synthetic bridge exists only to repair a genuinely tiny
    accidental gap between endpoints that visually and semantically belong to
    the same intended stroke. It is never chosen because it happens to be short
    enough to pass a check. The routing preference, in order:

    1. existing undrawn geometry;
    2. existing drawn geometry, retraced - the pen travels back along a line it
       already drew, which is invisible;
    3. a tiny legitimate gap repair, and only if necessary;
    4. otherwise reject the artwork and draw it again.

    Only (3) is this function, and it is the last resort before (4).

    Two tests, and both have to pass. One side must be a **loose end** - a
    degree-one node, where the pen would otherwise have to stop - and the gap
    must be shorter than `span`, which is `BRIDGE_SHARE` of the canvas and is
    about six pixels. That much was always here. What is new is the second
    test: **the gap must point the way the pen was already going**
    (`BRIDGE_ALIGNMENT`). Distance alone cannot tell a stroke that was
    interrupted from two unrelated ends that happen to pass near each other,
    and the second is a mark across the picture that is merely short enough to
    sneak through. A line that stops mid-curve is continued; a line that would
    have to turn a corner to reach its neighbour is not.

    The other side may be another loose end **or a point part-way along another
    stroke**, and the second case is the one that matters in practice: the
    commonest break in generated line art is a stroke that stops just short of
    the middle of another, where there is no loose end to meet. Attaching
    there splits the stroke it lands on, which is exactly what a real pen does
    when it rejoins a line.

    Only ever *across* pieces. A bridge inside a piece that is already
    connected buys nothing and costs a visible mark.
    """
    added = 0
    for _ in range(MAX_BRIDGES):
        union = _Union.of(edges)
        ends = [node for node, count in _degrees(edges).items() if count == 1]
        if not ends or len({union.find(e.u) for e in edges}) < 2:
            break

        best = None       # (distance, loose_end, edge_index, point_index)
        for node in ends:
            here = positions[node]
            root = union.find(node)
            heading = _end_direction(edges, node, positions)
            if heading == (0.0, 0.0):
                # No readable direction is "cannot tell", and cannot tell is
                # not permission: a stroke too short to have a heading is
                # debris, and bridging off debris is inventing a line.
                continue
            for index, edge in enumerate(edges):
                if union.find(edge.u) == root:
                    continue
                points = edge.points
                for at in range(0, len(points), ATTACH_STRIDE):
                    point = points[at]
                    dy, dx = point[0] - here[0], point[1] - here[1]
                    distance = math.hypot(dy, dx)
                    if distance > span or distance < 1e-9:
                        continue
                    # Was the pen already going there? A break in one stroke
                    # says yes; two strokes passing near each other says no.
                    if (heading[0] * dy + heading[1] * dx) / distance < BRIDGE_ALIGNMENT:
                        continue
                    if best is None or distance < best[0]:
                        best = (distance, node, index, at)
        if best is None:
            break

        _, node, index, at = best
        target = edges.pop(index)
        landing = target.points[at]
        if at <= 0:
            anchor = target.u
        elif at >= len(target.points) - 1:
            anchor = target.v
        else:
            anchor = max(positions) + 1
            positions[anchor] = landing
            edges.append(Edge(target.u, anchor, target.points[:at + 1],
                              bridge=target.bridge))
            edges.append(Edge(anchor, target.v, target.points[at:],
                              bridge=target.bridge))
        if at <= 0 or at >= len(target.points) - 1:
            edges.append(target)
        edges.append(Edge(node, anchor, [positions[node], landing], bridge=True))
        added += 1
    return edges, added


def keep_largest_component(edges: list) -> tuple[list, int, float]:
    """Keep the main drawing; report what was dropped.

    Debris is dropped silently because it is debris. Anything substantial is
    *reported* rather than dropped, and `visuals` turns that into a
    regeneration: an illustration missing a third of itself is not a rescue, it
    is a different picture.
    """
    union = _Union.of(edges)
    groups: dict = {}
    for edge in edges:
        groups.setdefault(union.find(edge.u), []).append(edge)
    if len(groups) <= 1:
        return edges, 1, 0.0
    ranked = sorted(groups.values(), key=lambda g: sum(e.length for e in g),
                    reverse=True)
    total = sum(e.length for g in ranked for e in g)
    kept = ranked[0]
    dropped = total - sum(e.length for e in kept)
    return kept, len(groups), (dropped / total if total else 0.0)


# --------------------------------------------------------------------------
# One stroke
# --------------------------------------------------------------------------
def _adjacency(edges: list) -> dict:
    adj: dict = {}
    for index, edge in enumerate(edges):
        adj.setdefault(edge.u, []).append((edge.v, index))
        if edge.v != edge.u:
            adj.setdefault(edge.v, []).append((edge.u, index))
    return adj


def _shortest_paths(source: int, adj: dict, edges: list) -> tuple[dict, dict]:
    """Dijkstra for pairing odd vertices, over what a retrace *costs*.

    Cost, not length. This pairing decides which existing edges get drawn a
    second time, and a bridge drawn twice is FAM's own repair traced over
    itself in the middle of the negative space - far more visible than the same
    distance of the artist's own line. Weighting it makes the matching route
    around it where it can.
    """
    import heapq

    distance = {source: 0.0}
    came: dict = {}
    queue = [(0.0, source)]
    while queue:
        here, node = heapq.heappop(queue)
        if here > distance.get(node, math.inf):
            continue
        for other, index in adj.get(node, ()):
            edge = edges[index]
            # Length is what it measures; cost is what it looks like.
            step = here + edge.length * (BRIDGE_RETRACE_PENALTY
                                         if edge.bridge else 1.0)
            if step < distance.get(other, math.inf):
                distance[other] = step
                came[other] = (node, index)
                heapq.heappush(queue, (step, other))
    return distance, came


def eulerise(edges: list) -> tuple[list, float]:
    """Duplicate the cheapest edges until the pen can draw it without lifting.

    Route inspection, with two deliberate simplifications.

    The pairing of odd-degree vertices is **greedy nearest-first** rather than
    a minimum-weight perfect matching. Blossom on a few hundred vertices is a
    great deal of code to save a retrace nobody can see; the greedy pairing is
    within a few per cent on drawings of this shape, and the number it is
    optimising - invisible retracing - is reported so a bad one shows up.

    And it aims for an Euler **path**, not a circuit: the two odd vertices
    furthest apart are left unpaired, so the drawing starts at one loose end
    and finishes at the other. That is both cheaper and what a pen does.
    """
    adj = _adjacency(edges)
    odd = sorted(node for node, count in _degrees(edges).items()
                 if count % 2 == 1)
    if len(odd) <= 2:
        return edges, 0.0

    tables = {node: _shortest_paths(node, adj, edges) for node in odd}
    # Leave the most expensive pair unjoined: they become the two ends of the
    # stroke, which costs nothing, where joining them would cost the most.
    worst, keep_open = -1.0, (odd[0], odd[1])
    for i, a in enumerate(odd):
        for b in odd[i + 1:]:
            span = tables[a][0].get(b, math.inf)
            if span != math.inf and span > worst:
                worst, keep_open = span, (a, b)
    pending = [node for node in odd if node not in keep_open]

    duplicated = 0.0
    while len(pending) >= 2:
        best = None
        for i, a in enumerate(pending):
            for b in pending[i + 1:]:
                span = tables[a][0].get(b, math.inf)
                if best is None or span < best[0]:
                    best = (span, a, b)
        if best is None or best[0] == math.inf:
            break
        _, a, b = best
        came = tables[a][1]
        node = b
        while node != a and node in came:
            previous, index = came[node]
            original = edges[index]
            edges.append(Edge(original.u, original.v, list(original.points),
                              bridge=original.bridge, duplicate=True))
            duplicated += original.length
            node = previous
        pending.remove(a)
        pending.remove(b)
    return edges, duplicated


def euler_route(edges: list, variant: int = 0) -> list:
    """Hierholzer's algorithm: the order the pen travels, as oriented chains.

    Returns `[(points, is_bridge, is_retrace), ...]` where each chain's first
    point is the previous chain's last. **That guarantee is the whole
    function**, and it is what this did not previously provide.

    `variant` rotates each vertex's adjacency list before the walk. Every
    variant traverses exactly the same multiset of edges and therefore draws
    exactly the same picture - what changes is only the *order* the pen visits
    it in, which is what the listener watches. `best_route` uses that to pick
    an ordering that keeps new line appearing; see `reveal_stall`.

    It used to run Hierholzer for the *vertex* sequence and then reconstruct
    the edges from it by searching for "any unused edge between these two
    vertices". Two things go wrong with that, and they compound:

    * with parallel edges - two different chains joining the same pair of
      junctions, which line art produces constantly - the search can pick a
      different edge from the one the algorithm actually traversed, after
      which the reconstruction is walking a route nobody planned;
    * and when it then found no edge at all between consecutive vertices, it
      `continue`d. The next chain appended started wherever its own node was,
      which could be anywhere on the canvas.

    `process` concatenated those chains without checking, smoothing turned the
    discontinuity into a graceful curve, and the result was a long connector
    sweeping across empty ivory that was never in the artwork. A scar.

    So the edge indices are recorded *as they are used*, during the traversal,
    and the trail is exact by construction rather than by reconstruction. There
    is nothing left to search for and nothing to fail to find.

    Starts at an odd-degree vertex when there is one, which is what makes the
    result an open stroke with a beginning and an end rather than a loop that
    happens to start in the middle of a line.
    """
    if not edges:
        return []
    adj = _adjacency(edges)
    if variant:
        # Rotate rather than shuffle: deterministic, seedless, and enough to
        # reach genuinely different orderings of the same drawing.
        for node, items in adj.items():
            if len(items) > 1:
                at = (node + variant) % len(items)
                adj[node] = items[at:] + items[:at]
    odd = [node for node, count in _degrees(edges).items() if count % 2 == 1]
    start = min(odd) if odd else min(adj)

    used = [False] * len(edges)
    pointer = {node: 0 for node in adj}
    stack = [start]
    # Parallel to `stack`, holding the edge that got us to each vertex on it.
    arrived_by: list = []
    circuit: list = []
    while stack:
        node = stack[-1]
        items = adj.get(node, ())
        while pointer[node] < len(items) and used[items[pointer[node]][1]]:
            pointer[node] += 1
        if pointer[node] == len(items):
            stack.pop()
            if arrived_by:
                circuit.append(arrived_by.pop())
            continue
        other, index = items[pointer[node]]
        used[index] = True
        pointer[node] += 1
        arrived_by.append(index)
        stack.append(other)
    circuit.reverse()

    # Orient each chain the way it is actually travelled. `at` is where the pen
    # is; an edge that does not touch it means the trail is inconsistent, which
    # would be a bug in the walk above rather than in the artwork - so it says
    # so instead of drawing whatever it has.
    route: list = []
    at = start
    for index in circuit:
        edge = edges[index]
        if edge.u == at:
            points, at = edge.points, edge.v
        elif edge.v == at:
            points, at = list(reversed(edge.points)), edge.u
        else:
            raise LineProcessingError(
                "the traversal left the drawing - an edge in the route does "
                "not touch where the pen is", "broken_route")
        route.append((points, edge.bridge, edge.duplicate))
    return route


# --------------------------------------------------------------------------
# From staircase to curve
# --------------------------------------------------------------------------
def simplify(points: list, epsilon: float = SIMPLIFY_EPSILON) -> list:
    """Douglas-Peucker, iteratively so a long path cannot blow the stack."""
    if len(points) < 3:
        return list(points)
    array = np.asarray(points, dtype=np.float64)
    keep = np.zeros(len(array), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(array) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        start, end = array[first], array[last]
        span = end - start
        norm = math.hypot(span[0], span[1])
        segment = array[first + 1:last]
        if norm == 0:
            distances = np.hypot(segment[:, 0] - start[0], segment[:, 1] - start[1])
        else:
            distances = np.abs(
                span[0] * (start[1] - segment[:, 1])
                - (start[0] - segment[:, 0]) * span[1]) / norm
        index = int(np.argmax(distances))
        if distances[index] > epsilon:
            at = first + 1 + index
            keep[at] = True
            stack.append((first, at))
            stack.append((at, last))
    return [tuple(p) for p in array[keep]]


def smooth(points: list, passes: int = 3) -> list:
    """Take the pixel staircase off before the curve is fitted.

    A skeleton is quantised to whole pixels, so a line that is very nearly
    straight arrives as a row of one-pixel steps. Douglas-Peucker cannot remove
    those - each step genuinely deviates by about a pixel, which is more than
    any tolerance small enough to keep real detail - so simplifying first
    *keeps* the wobble and then fits smooth curves through it. At 1536px that
    reads as a hand tremor, which is the one thing separating this from "crude
    vector tracing".

    A binomial filter (1-2-1, a few passes) removes deviation at the scale of a
    single sample and leaves everything larger intact. The ends are pinned, so
    the stroke still starts and finishes exactly where the pen did.
    """
    if len(points) < 5 or passes <= 0:
        return list(points)
    array = np.asarray(points, dtype=np.float64)
    for _ in range(passes):
        middle = (array[:-2] + 2 * array[1:-1] + array[2:]) / 4.0
        array = np.vstack([array[:1], middle, array[-1:]])
    return [tuple(p) for p in array]


def _dedupe(points: list, minimum: float = 0.35) -> list:
    out = [points[0]]
    for point in points[1:]:
        if math.hypot(point[0] - out[-1][0], point[1] - out[-1][1]) >= minimum:
            out.append(point)
    return out


def to_beziers(points: list) -> list:
    """Centripetal Catmull-Rom through the points, as cubic Béziers.

    Centripetal (alpha = 0.5) rather than uniform for one concrete reason: a
    route that doubles back on itself - which every retraced edge does - makes
    a uniform Catmull-Rom spline overshoot into a loop that was never in the
    drawing. Centripetal parameterisation is the standard cure and is provably
    cusp- and self-intersection-free between control points.
    """
    if len(points) < 2:
        return []
    pts = [points[0]] + list(points) + [points[-1]]
    out = []
    alpha = 0.5
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        t0 = 0.0
        t1 = t0 + max(1e-6, math.dist(p0, p1) ** alpha)
        t2 = t1 + max(1e-6, math.dist(p1, p2) ** alpha)
        t3 = t2 + max(1e-6, math.dist(p2, p3) ** alpha)
        m1 = _tangent(p0, p1, p2, t0, t1, t2)
        m2 = _tangent(p1, p2, p3, t1, t2, t3)
        span = (t2 - t1) / 3.0
        c1 = (p1[0] + m1[0] * span, p1[1] + m1[1] * span)
        c2 = (p2[0] - m2[0] * span, p2[1] - m2[1] * span)
        out.append((p1, c1, c2, p2))
    return out


def _tangent(a, b, c, ta, tb, tc):
    return (
        (c[0] - a[0]) / max(1e-6, tc - ta),
        (c[1] - a[1]) / max(1e-6, tc - ta),
    )


def path_d(beziers: list) -> str:
    if not beziers:
        return ""
    start = beziers[0][0]
    parts = [f"M{start[0]:.2f} {start[1]:.2f}"]
    for _, c1, c2, end in beziers:
        parts.append(f"C{c1[0]:.2f} {c1[1]:.2f} {c2[0]:.2f} {c2[1]:.2f} "
                     f"{end[0]:.2f} {end[1]:.2f}")
    return " ".join(parts)


def flatten(beziers: list, steps: int = FLATTEN_STEPS) -> list:
    """The curve as points again - what the thumbnail is drawn from.

    The thumbnail is rasterised from this rather than from the pre-curve
    polyline so that the picture on the tile is the picture the player finishes
    on, down to the curvature.
    """
    if not beziers:
        return []
    out = [beziers[0][0]]
    for p0, c1, c2, p3 in beziers:
        for step in range(1, steps + 1):
            t = step / steps
            u = 1 - t
            x = (u ** 3 * p0[0] + 3 * u * u * t * c1[0]
                 + 3 * u * t * t * c2[0] + t ** 3 * p3[0])
            y = (u ** 3 * p0[1] + 3 * u * u * t * c1[1]
                 + 3 * u * t * t * c2[1] + t ** 3 * p3[1])
            out.append((x, y))
    return out


def canvas_transform(points: list, size: int = visual_style.VIEWBOX,
                     margin: int = visual_style.SAFE_MARGIN) -> tuple:
    """The uniform scale-and-centre that puts these pixels on the square.

    Split out of `fit_to_canvas` so the *same* transform can be applied to
    something else - specifically the skeleton, which `fidelity` has to place
    on the same canvas as the finished vector in order to compare them. Two
    transforms computed separately from two point sets would differ by exactly
    the detail being measured, which would make the measurement meaningless.
    """
    ys = [p[0] for p in points]
    xs = [p[1] for p in points]
    top, left = min(ys), min(xs)
    height = max(1e-6, max(ys) - top)
    width = max(1e-6, max(xs) - left)
    usable = size - 2 * margin
    scale = min(usable / width, usable / height)
    return (scale, (size - width * scale) / 2.0, (size - height * scale) / 2.0,
            left, top)


def apply_transform(transform: tuple, points) -> list:
    """Pixel (row, column) to canvas (x, y), under a transform from above."""
    scale, offset_x, offset_y, left, top = transform
    return [((x - left) * scale + offset_x, (y - top) * scale + offset_y)
            for y, x in points]


def fit_to_canvas(points: list, size: int = visual_style.VIEWBOX,
                  margin: int = visual_style.SAFE_MARGIN) -> tuple:
    """Pixel coordinates (row, column) into viewBox coordinates (x, y).

    Scaled uniformly and centred, so the drawing keeps its proportions and
    lands inside the safe margin whatever the source image measured. Returns
    `(points, scale)` - see the note on the return.
    """
    if not points:
        return [], 1.0
    transform = canvas_transform(points, size, margin)
    fitted = apply_transform(transform, points)
    scale = transform[0]
    # The scale comes back with the points because a length measured in the
    # source is not a length anybody sees. A bridge is judged on what it looks
    # like on the finished canvas, and this is the only place that conversion
    # is known.
    return fitted, scale


#: How far, in viewBox units, a drawn curve may sit from the artwork it came
#: from and still count as the same line. Six on a 1000-wide canvas is 0.6% -
#: under a stroke width at the size these are shown, so a difference inside it
#: is invisible and a difference outside it is a change to the picture.
FIDELITY_TOLERANCE = 6.0
#: Good enough to stop walking `DETAIL_LADDER`: the vector still contains this
#: much of the artwork it came from.
#:
#: **A target, not a floor, and that is why it is no longer called
#: MIN_FIDELITY.** The floor - the number deciding whether a drawing may reach
#: a listener at all - is `visual_validator.MIN_FIDELITY`, and it is lower.
#: This one only decides when to stop trying gentler settings. Two numbers
#: sharing one name across two modules is how they quietly stop meaning what
#: the reader thinks they mean.
FIDELITY_TARGET = 0.94
#: Vectorisation settings, gentlest last. `process` walks this ladder on the
#: *same* source artwork - the same decode, the same skeleton, the same route -
#: and keeps the first pass that clears `FIDELITY_TARGET`. It is cheap: only the
#: smoothing, simplification and curve fitting are redone, which is a few
#: milliseconds on a few thousand points, and not the skeletonisation.
#:
#: The first rung is what every drawing used to get unconditionally. The rest
#: exist because "the vectoriser lost detail" now has a remedy that costs the
#: artwork nothing, where before it had only two: ship it, or throw away good
#: art and draw something simpler.
DETAIL_LADDER = (
    (3, SIMPLIFY_EPSILON),   # the default: smoothest, fewest points
    (2, 0.6),                # keep more of the small stuff
    (1, 0.25),               # near-verbatim; the staircase is barely touched
)


def _occupancy(points, size: int, cell: float, path: bool = True) -> np.ndarray:
    """Which cells of a coarse grid the ink touches.

    Quantised rather than rasterised with a stroke width: the question is
    "was there line near here", and a grid whose cell is the tolerance answers
    it in one array operation.

    `path` says whether consecutive points are joined. True for a polyline, so
    a long straight run cannot step over a cell it crosses; **False for a point
    cloud** - the skeleton arrives as unordered pixels, and joining those in
    array order would draw lines between unrelated parts of the picture and
    then score the vector against them.
    """
    n = max(1, int(math.ceil(size / cell)))
    grid = np.zeros((n, n), dtype=bool)
    if not points:
        return grid
    xs: list = []
    ys: list = []
    previous = None
    for x, y in points:
        if path and previous is not None:
            span = math.dist(previous, (x, y))
            steps = int(span / (cell * 0.5))
            for i in range(1, steps):
                t = i / steps
                xs.append(previous[0] + (x - previous[0]) * t)
                ys.append(previous[1] + (y - previous[1]) * t)
        xs.append(x)
        ys.append(y)
        previous = (x, y)
    col = np.clip((np.asarray(xs) / cell).astype(int), 0, n - 1)
    row = np.clip((np.asarray(ys) / cell).astype(int), 0, n - 1)
    grid[row, col] = True
    return grid


def _dilate(grid: np.ndarray) -> np.ndarray:
    """One cell in every direction, by shifted ORs."""
    out = grid.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            out |= np.roll(np.roll(grid, dy, axis=0), dx, axis=1)
    return out


def fidelity(source_points, drawn_points,
             tolerance: float = FIDELITY_TOLERANCE) -> tuple[float, float]:
    """How much of the artwork survived vectorisation, and how much was added.

    Returns `(kept, invented)`. `kept` is the share of the source artwork that
    the finished curve still passes near; `invented` is the share of the
    finished curve that passes nowhere near the artwork.

    **Both directions, because they are different failures.** A vector that
    lost a figure's hands scores badly on `kept` and perfectly on `invented`; a
    vector that struck out across the page scores the reverse. One number
    averaging them would hide either.

    Both point sets are in the same canvas coordinates, placed by the same
    `canvas_transform` - see the note there on why that has to be true.
    """
    if not source_points or not drawn_points:
        return 0.0, 0.0
    size = visual_style.VIEWBOX
    # The source is a cloud of skeleton pixels, the drawing is a path. Getting
    # that the wrong way round joins unrelated parts of the picture.
    source = _occupancy(source_points, size, tolerance, path=False)
    drawn = _occupancy(drawn_points, size, tolerance, path=True)
    source_total = int(source.sum())
    drawn_total = int(drawn.sum())
    if not source_total or not drawn_total:
        return 0.0, 0.0
    kept = int((source & _dilate(drawn)).sum()) / source_total
    invented = int((drawn & ~_dilate(source)).sum()) / drawn_total
    return kept, invented


def node_tolerance(edges: list, positions: dict) -> float:
    """How far two chains at the same junction may legitimately meet apart.

    Derived from this drawing rather than assumed. Every chain starts and ends
    on a pixel of a junction cluster while its node is that cluster's centroid,
    so the offset between the two is measurable directly - and the worst seam
    the traversal can produce is two such offsets back to back, plus a pixel
    for the diagonal step between neighbouring pixels.

    The point of measuring it: a gap this size is **inside a junction cluster**,
    which is inside ink. It is not a mark across the picture, which is what the
    no-scars rule is actually about. A drawing with more line in it has larger
    clusters and therefore a larger legitimate seam, and a constant tolerance
    would refuse exactly the rich artwork this system exists to produce.
    """
    worst = 0.0
    for edge in edges:
        if edge.points:
            for node, point in ((edge.u, edge.points[0]),
                                (edge.v, edge.points[-1])):
                at = positions.get(node)
                if at is not None:
                    worst = max(worst, math.dist(at, point))
    return max(CONTIGUOUS_TOLERANCE, 2.0 * worst + 1.5)


#: How many orderings of the same drawing to try before settling on one. Each
#: is a linear walk over the graph, so this is a handful of milliseconds even
#: on a dense illustration - and what it buys is the difference between a
#: reveal that keeps producing picture and one that spends ten seconds going
#: back over line the listener has already seen.
ROUTE_TRIALS = 8
#: Good enough to stop looking: no unbroken retrace longer than this share of
#: the journey.
STALL_TARGET = 0.06


def reveal_stall(route: list) -> float:
    """The longest stretch of the reveal in which no new line appears.

    As a share of the whole journey, because that is how a listener meets it:
    the drawing is revealed against the audio, so a run of retracing is a run
    of seconds in which the episode plays on and the picture does not change.

    Retracing itself is fine and is the thing that keeps the pen off the
    negative space - **priority four**. What this measures is priority *three*:
    where two valid routes both preserve the artwork and both avoid a scar,
    prefer the one that keeps new drawing arriving. A route is not better for
    retracing less in total; it is better for never retracing for long.
    """
    if not route:
        return 0.0
    lengths = [_polyline_length(points) for points, _, _ in route]
    total = sum(lengths)
    if total <= 0:
        return 0.0
    worst = run = 0.0
    for length, (_, _, retrace) in zip(lengths, route):
        run = run + length if retrace else 0.0
        worst = max(worst, run)
    return worst / total


def best_route(edges: list, tolerance: float) -> tuple:
    """The same drawing, in the order that reveals it best.

    Returns `(pixels, bridge_spans, stall, warnings)`.

    **It cannot change the picture.** Every candidate traverses exactly the
    same multiset of edges, so the ink, the retraced share and the fidelity are
    identical whichever one wins; only the order differs. That is what makes
    this safe to optimise at all - the priority order says preserve the artwork
    first, and this provably does not touch it.

    A candidate that fails to assemble is skipped rather than fatal, and said
    out loud when a later one succeeds: it means one ordering of this graph was
    inconsistent, which is worth knowing even though the drawing was fine.
    """
    warnings: list = []
    best = None
    failure = None
    for variant in range(ROUTE_TRIALS):
        candidate = euler_route(edges, variant)
        if not candidate:
            continue
        try:
            pixels, spans = _assemble(candidate, tolerance)
        except LineProcessingError as exc:
            failure = exc
            continue
        stall = reveal_stall(candidate)
        if best is None or stall < best[2]:
            best = (pixels, spans, stall)
        if stall <= STALL_TARGET:
            break
    if best is None:
        raise failure or LineProcessingError("no route through the artwork",
                                             "no_route")
    if failure is not None:
        warnings.append(f"one traversal of this drawing was discontinuous "
                        f"({failure}); a different ordering was used")
    return best[0], best[1], best[2], warnings


def vectorise(canvas_points: list, artwork: list) -> tuple:
    """Points to curves, gently enough that the drawing survives.

    Returns `(d, flat, beziers, kept, invented, detail)`.

    **Smooth, then simplify, then fit** - in that order, because simplifying
    first locks the pixel staircase into the points that survive and no amount
    of curve fitting afterwards takes it out again.

    Then walk `DETAIL_LADDER` until the result still contains `artwork`. This
    is the "art first" rule made mechanical: when a beautiful drawing comes out
    of here simplified or distorted that is a **line-processing** failure, and
    the answer is to preserve the source and redo the processing rather than
    ask for simpler art. Each rung repeats only the smoothing and the curve
    fitting - a few milliseconds - and never the skeletonisation.

    Raises when no rung produced a path at all, which is a drawing with too
    little in it rather than a drawing that came out badly.
    """
    best = None
    for detail, (passes, epsilon) in enumerate(DETAIL_LADDER):
        points = simplify(smooth(canvas_points, passes), epsilon)
        beziers = to_beziers(points)
        d = path_d(beziers)
        flat = flatten(beziers)
        if not d:
            continue
        kept, invented = fidelity(artwork, flat)
        if best is None or kept > best[3]:
            best = (d, flat, beziers, kept, invented, detail)
        if kept >= FIDELITY_TARGET:
            break
    if best is None:
        raise LineProcessingError("the traced line was too short to draw",
                                  "too_short")
    return best


def _assemble(route: list, tolerance: float = CONTIGUOUS_TOLERANCE) -> tuple:
    """Oriented chains into one polyline, refusing to jump.

    **The hard rule this enforces: the pen never crosses blank canvas.** A
    drawing is allowed to retrace line it has already drawn - that is
    invisible - and it is allowed to close a gap so small it reads as an
    accident of the artwork. It is never allowed to travel from one place to
    another across empty ivory, because after smoothing that becomes a long
    graceful curve through the negative space that was never in the
    illustration, and it ruins an otherwise good animation.

    Every step is checked rather than assumed. Where two chains meet they must
    actually meet - within `tolerance`, which is this drawing's own junction
    geometry (see `node_tolerance`) and exists only because a chain ends on a
    real pixel while its node is that cluster's centroid. A gap that size is
    inside a junction cluster, and therefore inside ink. A larger one is not
    something to draw through; it means the traversal is wrong, and the honest
    answer is to fail and let the retry ladder produce different art. It used
    to be concatenated in silence.

    Returns the polyline and the length of each bridge crossed, so the scale of
    what *was* invented is measurable rather than a matter of trust.
    """
    pixels: list = []
    spans: list = []
    for points, is_bridge, _ in route:
        if not points:
            continue
        if not pixels:
            pixels.extend(points)
        else:
            gap = math.dist(pixels[-1], points[0])
            if gap > tolerance:
                raise LineProcessingError(
                    f"the route jumps {gap:.0f} pixels between strokes, which "
                    "would draw a line across empty canvas that is not in the "
                    "artwork", "discontinuous")
            pixels.extend(points[1:] if gap <= 1.0 else points)
        if is_bridge:
            spans.append(_polyline_length(points))
    return pixels, spans


# --------------------------------------------------------------------------
# Looking inside it
# --------------------------------------------------------------------------
class _Trace:
    """Writes each stage to disk, or does nothing at all.

    Nothing at all is the production case and the default. Every method is a
    no-op when no directory was given, so the instrumentation costs one
    attribute check per stage and cannot change what `process` returns.

    What the stages are for: a finished drawing that is worse than the artwork
    it came from has four possible causes, and they look identical from the
    outside. The threshold can lose a pale line; the thinning can break a
    stroke; the traversal can take a route that retraces half the picture; the
    smoothing can round a deliberate corner off. `2-ink-mask` against the
    source separates the first, `3-skeleton` the second, `4-route` against
    `5-final` the last two, and `6-overlay` says in one glance how much of the
    original survived at all.
    """

    def __init__(self, directory) -> None:
        self.dir = None
        if directory is None:
            return
        from pathlib import Path

        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _write(self, name: str, data: bytes) -> None:
        if self.dir is None:
            return
        try:
            (self.dir / name).write_bytes(data)
        except OSError as exc:  # noqa: BLE001 - a trace never breaks a drawing
            log.warning("could not write trace %s: %s", name, exc)

    def source(self, data: bytes) -> None:
        """The artwork exactly as the provider returned it, byte for byte."""
        self._write("1-source.png", data)

    def mask(self, name: str, mask: np.ndarray) -> None:
        if self.dir is None:
            return
        paper = np.array(_hex(visual_style.PAPER), dtype=np.uint8)
        ink = np.array(_hex(visual_style.INK), dtype=np.uint8)
        image = np.where(mask[:, :, None], ink[None, None, :], paper[None, None, :])
        self._write(f"{name}.png", encode_png(image.astype(np.uint8)))

    def path(self, name: str, points: list) -> None:
        """One stage's geometry, rendered the way the product renders it."""
        if self.dir is None or len(points) < 2:
            return
        self._write(f"{name}.png", render_png(points, 1024))

    def overlay(self, name: str, mask: np.ndarray, points: list) -> None:
        """The finished line laid over the ink it was traced from.

        The single most useful frame: what the vectoriser kept, and what it
        quietly dropped. The ink goes down faint and the finished line goes
        over it in a colour that could not be mistaken for charcoal.

        **The two are put into the same frame first, and that is the whole
        difficulty.** `fit_to_canvas` crops the drawing to its own ink and
        rescales it into the safe margin, so the vector no longer sits where
        the artwork did - laying them on top of each other raw produces two
        similar shapes at different sizes, which looks like a vectoriser that
        drifted and is actually just the frame. The same transform is applied
        to the ink here, so a difference in this image is a real difference.
        """
        if self.dir is None or len(points) < 2 or not mask.any():
            return
        size = 1024
        image = np.full((size, size, 3), 250.0, dtype=np.float32)

        # The ink, through `fit_to_canvas`'s transform: cropped to its own
        # bounding box, scaled uniformly, centred.
        rows, cols = np.nonzero(mask)
        top, bottom = int(rows.min()), int(rows.max())
        left, right = int(cols.min()), int(cols.max())
        crop = mask[top:bottom + 1, left:right + 1]
        margin = visual_style.SAFE_MARGIN * size / visual_style.VIEWBOX
        usable = size - 2 * margin
        height, width = crop.shape
        scale = min(usable / max(1, width), usable / max(1, height))
        out_w, out_h = max(1, int(width * scale)), max(1, int(height * scale))
        placed = crop[(np.arange(out_h) * height // out_h).clip(0, height - 1)[:, None],
                      (np.arange(out_w) * width // out_w).clip(0, width - 1)[None, :]]
        at_x, at_y = int((size - out_w) / 2), int((size - out_h) / 2)
        window = image[at_y:at_y + out_h, at_x:at_x + out_w]
        window[placed] = window[placed] * 0.45 + 255.0 * 0.55 * 0.35

        # The finished path, in a colour no FAM illustration contains.
        factor = size / float(visual_style.VIEWBOX)
        coverage = _coverage([(x * factor, y * factor) for x, y in points],
                             size, 1.3)
        mark = np.array((214, 78, 46), dtype=np.float32)
        image = (image * (1 - coverage[:, :, None])
                 + mark[None, None, :] * coverage[:, :, None])
        self._write(f"{name}.png", encode_png(np.clip(image, 0, 255).astype(np.uint8)))


# --------------------------------------------------------------------------
# The whole thing
# --------------------------------------------------------------------------
def process(data: bytes, trace=None) -> LineArt:
    """Source artwork to one canonical ordered path.

    Raises `LineProcessingError` with a `reason` when the art cannot become one
    stroke. That is not a bug report, it is the designed answer: `visuals`
    catches it and goes round the retry ladder, because the fix for badly
    connected art is different art, not a cleverer traversal.

    CPU-bound and deliberately synchronous. Every caller runs it in a thread -
    audio is streaming on the event loop, and this is the one part of the
    feature heavy enough to be heard if it were not.

    `trace` is a directory to write the intermediate stages into, and it is
    **off in production and changes nothing when it is**: with `None` this
    function does exactly what it did before, including not importing anything
    extra. It exists because "the drawing came out worse than the artwork" is a
    question with four possible answers - the threshold lost the line, the
    thinning broke it, the traversal took a bad route, or the smoothing rounded
    it off - and they are indistinguishable from the finished picture. Each
    stage on disk tells them apart in one look, and `fidelity` puts a number on
    the last of them.
    """
    timings: dict = {}
    warnings: list = []

    stage = _Trace(trace)
    stage.source(data)

    began = time.monotonic()
    mask, ink_share = prepare_mask(data)
    stage.mask("2-ink-mask", mask)
    timings["decode"] = int((time.monotonic() - began) * 1000)
    if not mask.any():
        raise LineProcessingError("there is no line in the artwork", "blank")

    began = time.monotonic()
    skeleton = prune_spurs(skeletonise(mask))
    stage.mask("3-skeleton", skeleton)
    timings["skeletonise"] = int((time.monotonic() - began) * 1000)
    if not skeleton.any():
        raise LineProcessingError("the artwork thinned away to nothing", "blank")

    began = time.monotonic()
    edges, positions = build_graph(skeleton)
    if not edges:
        raise LineProcessingError("the artwork has no traceable line in it",
                                  "no_graph")
    edges, bridged = bridge_gaps(edges, positions,
                                 BRIDGE_SHARE * max(skeleton.shape))
    edges, components, dropped_share = keep_largest_component(edges)
    if dropped_share > MINOR_COMPONENT_SHARE:
        raise LineProcessingError(
            f"the artwork is in {components} separate pieces "
            f"({dropped_share:.0%} of it is not joined to the rest), so it "
            "cannot be drawn without lifting the pen",
            "disconnected")
    if components > 1:
        warnings.append(f"dropped {components - 1} fragment(s) of debris")

    total_before = sum(edge.length for edge in edges)
    edges, duplicated = eulerise(edges)
    # Several valid orderings of the same drawing, and the one that keeps new
    # line arriving wins. It cannot change the picture - see `best_route`.
    pixels, bridge_spans, stall, route_warnings = best_route(
        edges, node_tolerance(edges, positions))
    warnings.extend(route_warnings)
    timings["route"] = int((time.monotonic() - began) * 1000)

    began = time.monotonic()
    transform = canvas_transform(pixels)
    fit_scale = transform[0]
    canvas_points = apply_transform(transform, pixels)
    # Bridges measured where they will be seen. A gap that was six pixels in a
    # source image whose subject filled one corner is not six pixels once that
    # corner fills the canvas.
    bridges = [length * fit_scale for length in bridge_spans]
    # The route as traversed, before any smoothing. Compared against the final
    # render this is the whole of what smoothing and simplification cost.
    stage.path("4-route", canvas_points)
    canvas_points = _dedupe(canvas_points)

    # The artwork itself, on the same canvas, to measure the drawing against.
    # The *skeleton* rather than the route, so that what is being checked is
    # "does the finished curve still contain the picture" and not merely "does
    # it still contain the path I chose through the picture".
    artwork = apply_transform(
        transform, [(int(y), int(x)) for y, x in zip(*np.nonzero(skeleton))])

    d, flat, beziers, kept, invented, detail = vectorise(canvas_points, artwork)
    if detail:
        warnings.append(
            f"vectorised at detail level {detail} to keep the artwork "
            f"({kept:.0%} of it survives)")
    timings["vectorise"] = int((time.monotonic() - began) * 1000)

    stage.path("5-final", flat)
    # Against the mask rather than the source image: the mask is what the
    # skeletoniser actually saw, so a difference here is the vectoriser's and
    # not the threshold's - which `2-ink-mask` already answers on its own.
    stage.overlay("6-overlay", mask, flat)

    return LineArt(
        d=d,
        points=flat,
        length=_flat_length(flat),
        curves=len(beziers),
        components=components,
        bridged=bridged,
        max_bridge=max(bridges) if bridges else 0.0,
        bridged_length=sum(bridges),
        fidelity=kept,
        invented=invented,
        longest_stall=stall,
        detail=detail,
        retraced=(duplicated / total_before) if total_before else 0.0,
        ink_share=ink_share,
        warnings=warnings,
        timings_ms=timings,
    )


def _flat_length(points: list) -> float:
    total = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        total += math.hypot(x1 - x0, y1 - y0)
    return total


# --------------------------------------------------------------------------
# Raster out
# --------------------------------------------------------------------------
def _coverage(points: list, size: int, half_width: float) -> np.ndarray:
    """Anti-aliased coverage for a stroked polyline, per pixel.

    Distance-to-segment rather than a scan-line fill, which gives round caps
    and round joins for free - the two things `visual_style` specifies about
    the pen - and gives a clean edge without supersampling the whole canvas.
    `maximum` rather than a sum, so a line crossing itself does not darken.
    """
    canvas = np.zeros((size, size), dtype=np.float32)
    pad = int(math.ceil(half_width + 1.5))
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        lo_x = max(0, int(math.floor(min(x0, x1))) - pad)
        hi_x = min(size, int(math.ceil(max(x0, x1))) + pad + 1)
        lo_y = max(0, int(math.floor(min(y0, y1))) - pad)
        hi_y = min(size, int(math.ceil(max(y0, y1))) + pad + 1)
        if lo_x >= hi_x or lo_y >= hi_y:
            continue
        xs = np.arange(lo_x, hi_x, dtype=np.float32) + 0.5
        ys = np.arange(lo_y, hi_y, dtype=np.float32) + 0.5
        px = xs[None, :] - x0
        py = ys[:, None] - y0
        dx, dy = x1 - x0, y1 - y0
        squared = dx * dx + dy * dy
        if squared < 1e-9:
            distance = np.sqrt(px * px + py * py)
        else:
            t = np.clip((px * dx + py * dy) / squared, 0.0, 1.0)
            distance = np.sqrt((px - t * dx) ** 2 + (py - t * dy) ** 2)
        block = np.clip(half_width + 0.5 - distance, 0.0, 1.0)
        np.maximum(canvas[lo_y:hi_y, lo_x:hi_x], block,
                   out=canvas[lo_y:hi_y, lo_x:hi_x])
    return canvas


def _hex(colour: str) -> tuple:
    colour = colour.lstrip("#")
    return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))


def encode_png(rgb: np.ndarray) -> bytes:
    """A PNG, from the standard library.

    Pillow would do this too; this does not need it, and a thumbnail that only
    renders on machines with an optional imaging library is a thumbnail that is
    missing in exactly the deployment nobody checked.
    """
    height, width, _ = rgb.shape
    raw = bytearray()
    for row in range(height):
        raw.append(0)                       # filter: none
        raw.extend(rgb[row].tobytes())

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (_PNG_SIGNATURE + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def ink_on_paper(coverage: np.ndarray, ink: tuple = ()) -> bytes:
    """Anti-aliased coverage, composited onto FAM's ivory, as a PNG.

    The last step of every raster this module produces - the thumbnail, the
    synthetic provider's source art, the tests' fixtures - and it was written
    out three times. One copy means the tile, the placeholder and the fixtures
    cannot end up on subtly different paper.
    """
    paper = np.array(_hex(visual_style.PAPER), dtype=np.float32)
    charcoal = np.array(ink or _hex(visual_style.INK), dtype=np.float32)
    blended = (paper[None, None, :] * (1 - coverage[:, :, None])
               + charcoal[None, None, :] * coverage[:, :, None])
    return encode_png(np.clip(blended, 0, 255).astype(np.uint8))


def render_png(points: list, size: int, stroke_width: float = 0.0,
               view_box: int = visual_style.VIEWBOX) -> bytes:
    """The finished illustration, as pixels, from the vector.

    **The thumbnail is rendered from the path, never from the source
    artwork.** The player's last frame and the tile have to be the same
    picture; rendering one from the vector and the other from the raster the
    model produced guarantees that they are not, and the difference is exactly
    the sort that nobody notices until a listener does.
    """
    stroke_width = stroke_width or visual_style.STROKE_WIDTH
    scale = size / float(view_box)
    scaled = [(x * scale, y * scale) for x, y in points]
    return ink_on_paper(
        _coverage(scaled, size, max(0.55, stroke_width * scale / 2.0)))


def rasterise_polyline(points01: list, size: int, stroke_px: float) -> bytes:
    """A polyline in 0..1 space, drawn as FAM draws things.

    Used by the synthetic provider to produce source artwork without a network,
    and by the tests to produce artwork whose correct answer is known.
    """
    scaled = [(x * size, y * size) for x, y in points01]
    return ink_on_paper(_coverage(scaled, size, max(0.6, stroke_px / 2.0)))


def svg_document(d: str, view_box: str = "", stroke: str = "",
                 stroke_width: float = 0.0, background: str = "") -> str:
    """The canonical asset, as a file.

    One path, no groups, no defs, no styles, nothing that could execute. The
    validator checks this rather than trusting it, because the SVG is served to
    browsers and an image pipeline is a perfectly ordinary way to get script
    into a page.
    """
    view_box = view_box or f"0 0 {visual_style.VIEWBOX} {visual_style.VIEWBOX}"
    stroke = stroke or visual_style.INK
    stroke_width = stroke_width or visual_style.STROKE_WIDTH
    background = background or visual_style.PAPER
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" '
        f'width="1000" height="1000" role="img">'
        f'<rect width="100%" height="100%" fill="{background}"/>'
        f'<path id="fam-line" d="{d}" fill="none" stroke="{stroke}" '
        f'stroke-width="{stroke_width}" stroke-linecap="round" '
        f'stroke-linejoin="round"/>'
        f'</svg>'
    )
