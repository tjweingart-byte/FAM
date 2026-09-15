"""The artwork really does become one continuous line.

Every test here draws its own source image, so the right answer is known rather
than eyeballed. That is the whole reason `line_processor.rasterise_polyline`
exists as a public function: a fixture PNG would make these tests about one
picture somebody happened to save, where drawing the input makes them about the
geometry.

The four properties that matter, and what breaks without each:

* **One path, in order.** The player reveals a share of a path. Two paths, or
  one whose points are not in the order the pen travels, is not something a
  percentage means anything about.
* **The pen never jumps.** A straight line drawn across empty ivory to reach
  the next piece is a scar, and it is what a naive "just concatenate the
  strokes" implementation produces on every image.
* **Badly connected art is refused, not rescued.** The answer to a picture in
  three pieces is a different picture.
* **It is smooth.** A skeleton emitted directly is a staircase, and at 1536px
  that reads as a tremor - the "crude vector tracing" the brief rules out.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import line_processor as lp  # noqa: E402
import visual_style  # noqa: E402

SIZE = 900


def draw(*strokes, size: int = SIZE, width: float = 1.4) -> bytes:
    """Render strokes as FAM renders things: charcoal on ivory."""
    coverage = np.zeros((size, size), dtype=np.float32)
    for stroke in strokes:
        points = [(x * size, y * size) for x, y in stroke]
        np.maximum(coverage, lp._coverage(points, size, width), out=coverage)
    paper = np.array(lp._hex(visual_style.PAPER), dtype=np.float32)
    ink = np.array(lp._hex(visual_style.INK), dtype=np.float32)
    blended = (paper[None, None, :] * (1 - coverage[:, :, None])
               + ink[None, None, :] * coverage[:, :, None])
    return lp.encode_png(np.clip(blended, 0, 255).astype(np.uint8))


def sample(fn, n: int = 600) -> list:
    return [fn(i / n) for i in range(n + 1)]


def circle(cx=0.5, cy=0.5, r=0.3) -> list:
    return sample(lambda t: (cx + r * math.cos(t * math.tau),
                             cy + r * math.sin(t * math.tau)))


def line(x0, y0, x1, y1) -> list:
    return sample(lambda t: (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))


# --------------------------------------------------------------------------
# The shape of the output
# --------------------------------------------------------------------------
def test_a_circle_becomes_one_closed_path():
    art = lp.process(draw(circle()))
    assert art.d.startswith("M")
    assert art.d.count("M") == 1, "the pen was lifted"
    assert art.components == 1
    assert art.retraced == 0.0, "a closed loop needs no retracing at all"
    start, end = art.points[0], art.points[-1]
    assert math.dist(start, end) < 6, "the loop did not close"


def test_the_svg_is_one_path_and_nothing_else():
    art = lp.process(draw(circle()))
    document = lp.svg_document(art.d)
    assert document.count("<path") == 1
    assert 'fill="none"' in document
    assert "<script" not in document
    assert "href" not in document


def test_the_drawing_is_fitted_inside_the_safe_margin():
    """A composition that runs off the canvas reads as a crop of something
    bigger. The source here is deliberately larger than its frame."""
    art = lp.process(draw(circle(r=0.48)))
    xs = [p[0] for p in art.points]
    ys = [p[1] for p in art.points]
    margin = visual_style.SAFE_MARGIN
    assert min(xs) >= margin - 1 and min(ys) >= margin - 1
    assert max(xs) <= visual_style.VIEWBOX - margin + 1
    assert max(ys) <= visual_style.VIEWBOX - margin + 1


# --------------------------------------------------------------------------
# The pen never jumps
# --------------------------------------------------------------------------
def test_the_route_never_leaps_across_the_canvas():
    """The property that separates this from concatenating strokes.

    A figure of eight has a junction, so its traversal has real choices to
    make; whichever it makes, consecutive points must stay adjacent. A jump
    here is a visible straight line through empty ivory - exactly what the
    feature promises never to draw.
    """
    eight = sample(lambda t: (0.5 + 0.30 * math.sin(t * math.tau * 2) * 0.8,
                              0.5 + 0.30 * math.cos(t * math.tau)))
    art = lp.process(draw(eight))
    hops = [math.dist(a, b) for a, b in zip(art.points, art.points[1:])]
    assert max(hops) < 40, f"the pen jumped {max(hops):.0f} units across the canvas"


def test_a_small_gap_is_bridged_and_the_piece_is_kept():
    """The commonest break in generated line art: a stroke that stops just
    short of the middle of another, where there is no loose end to meet."""
    art = lp.process(draw(circle(), line(0.13, 0.33, 0.87, 0.67),
                          line(0.812, 0.50, 0.912, 0.47)))
    assert art.bridged >= 1, "the gap was not closed"
    assert art.components == 1
    # The tail survived: the drawing reaches past the circle's right edge.
    assert max(p[0] for p in art.points) > 800


def test_retracing_happens_and_stays_small():
    """A shape with odd-degree vertices has no Euler path, so the route has to
    double back over line that is already drawn. That is allowed and invisible;
    what is not allowed is doing a lot of it."""
    art = lp.process(draw(circle(), line(0.13, 0.33, 0.87, 0.67)))
    assert art.components == 1
    assert 0 <= art.retraced < 0.35


# --------------------------------------------------------------------------
# Refusing rather than rescuing
# --------------------------------------------------------------------------
def test_two_separate_drawings_are_refused():
    with pytest.raises(lp.LineProcessingError) as raised:
        lp.process(draw(line(0.08, 0.10, 0.23, 0.20),
                        line(0.70, 0.80, 0.90, 0.90)))
    assert raised.value.reason == "disconnected"


def test_a_blank_canvas_is_refused():
    blank = np.zeros((SIZE, SIZE, 3), dtype=np.uint8)
    blank[:, :] = lp._hex(visual_style.PAPER)
    with pytest.raises(lp.LineProcessingError) as raised:
        lp.process(lp.encode_png(blank))
    assert raised.value.reason == "blank"


def test_a_dark_image_is_refused_rather_than_thresholded_into_noise():
    dark = np.zeros((SIZE, SIZE, 3), dtype=np.uint8)
    with pytest.raises(lp.LineProcessingError) as raised:
        lp.process(lp.encode_png(dark))
    assert raised.value.reason == "not_line_art"


def test_something_that_is_not_an_image_is_refused_with_a_reason():
    with pytest.raises(lp.LineProcessingError) as raised:
        lp.process(b"this is not a PNG")
    assert raised.value.reason in ("undecodable", "blank", "not_line_art")


# --------------------------------------------------------------------------
# Smoothness
# --------------------------------------------------------------------------
def test_the_line_is_smooth_rather_than_a_pixel_staircase():
    """The order of the operations, measured.

    A skeleton is quantised to whole pixels, so a smooth curve arrives as a row
    of one-pixel steps. Simplifying before smoothing locks those in - every
    step deviates by about a pixel, which is more than any useful tolerance -
    and the curve fitting then faithfully reproduces a tremor. This measures
    the turn angle between consecutive segments: a staircase alternates
    sharply, a smooth curve does not.
    """
    art = lp.process(draw(circle()))
    turns = []
    points = art.points
    for a, b, c in zip(points, points[1:], points[2:]):
        first = (b[0] - a[0], b[1] - a[1])
        second = (c[0] - b[0], c[1] - b[1])
        na = math.hypot(*first)
        nb = math.hypot(*second)
        if na < 1e-6 or nb < 1e-6:
            continue
        cos = max(-1.0, min(1.0, (first[0] * second[0] + first[1] * second[1])
                            / (na * nb)))
        turns.append(math.degrees(math.acos(cos)))
    assert turns, "no geometry to measure"
    assert max(turns) < 25, f"a {max(turns):.0f} degree kink in a circle"
    assert sum(turns) / len(turns) < 6


def test_smoothing_keeps_the_ends_exactly_where_they_were():
    points = [(float(i), float(i % 2)) for i in range(40)]
    smoothed = lp.smooth(points)
    assert smoothed[0] == points[0]
    assert smoothed[-1] == points[-1]
    assert len(smoothed) == len(points)


# --------------------------------------------------------------------------
# The thumbnail comes from the vector
# --------------------------------------------------------------------------
def test_the_thumbnail_is_rendered_from_the_path():
    """Not from the source artwork. The player's last frame and the tile have
    to be the same picture, and two renderers that merely agree today are two
    renderers that will disagree later."""
    art = lp.process(draw(circle()))
    png = lp.render_png(art.points, 256)
    grey = lp.decode_image(png)
    assert grey.shape == (256, 256)
    assert grey.min() < 0.4, "there is no line in the thumbnail"
    assert grey.max() > 0.9, "there is no paper in the thumbnail"
    # A line drawing, not a fill.
    assert float((grey < 0.55).mean()) < 0.1


def test_the_png_encoder_and_decoder_agree_without_pillow():
    """The built-in reader is the one a deployment without Pillow uses, so it
    is exercised directly rather than only when the optional library happens
    to be missing."""
    art = lp.process(draw(circle()))
    png = lp.render_png(art.points, 128)
    grey = lp._decode_png(png)
    assert grey.shape == (128, 128)
    assert grey.min() < 0.4


# --------------------------------------------------------------------------
# The pieces, individually
# --------------------------------------------------------------------------
def test_a_staircase_is_not_a_junction():
    """The bug that made one smooth curve come back as nine hundred branches.

    Eight-connected neighbour counting calls every bend in a thinned line a
    T-junction, because the diagonal step and the two orthogonal steps that go
    round it are all "neighbours". The diagonal only counts when nothing else
    reaches it.
    """
    skeleton = np.zeros((9, 13), dtype=bool)
    skeleton[3, 1:6] = True          # a horizontal run
    skeleton[4, 6:9] = True          # stepping down
    skeleton[5, 9:12] = True         # and down again
    degrees = lp.degree_map(skeleton)
    assert (degrees[skeleton] <= 2).all(), (
        f"a plain staircase registered as a junction: "
        f"{sorted(set(degrees[skeleton].tolist()))}")
    assert (degrees[skeleton] == 1).sum() == 2, "a line has exactly two ends"


def test_thinning_reduces_a_thick_stroke_to_its_centreline():
    mask = np.zeros((40, 40), dtype=bool)
    mask[18:23, 5:35] = True         # a 5px-thick horizontal bar
    thin = lp.skeletonise(mask)
    assert thin.sum() < mask.sum() / 3
    rows = sorted(set(np.nonzero(thin)[0].tolist()))
    assert len(rows) == 1, f"the centreline is {len(rows)} pixels thick"


def test_despeckle_drops_dust_and_keeps_drawing():
    mask = np.zeros((60, 60), dtype=bool)
    mask[30, 5:55] = True            # a line
    mask[5, 5] = True                # a speck
    cleaned = lp.despeckle(mask)
    assert cleaned[30, 20]
    assert not cleaned[5, 5]
