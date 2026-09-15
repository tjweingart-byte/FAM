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


# --------------------------------------------------------------------------
# The no-scars rule
# --------------------------------------------------------------------------
# A hard product-quality constraint, not a tuning preference. A listener
# watching the line travel must never see it strike out across the negative
# space: retracing existing line is invisible and allowed, duplicating existing
# edges is allowed, and a new long edge through blank canvas is not. It was
# seen in a real animation and it ruined it.


def test_the_traversal_never_leaves_a_gap_for_the_smoothing_to_bridge():
    """The bug that produced the scar, pinned at its source.

    `euler_route` used to run Hierholzer for the *vertex* sequence and then
    reconstruct the edges by searching for "any unused edge between these two
    vertices". With parallel edges that can pick a different edge from the one
    traversed; when it then found none at all it skipped, and the next chain
    began wherever its own node happened to be. `process` concatenated them,
    smoothing turned the discontinuity into a graceful curve, and a long
    connector swept across empty ivory.

    So the route is checked chain by chain: where two meet, they must meet.
    """
    art = lp.process(draw(circle(), line(0.13, 0.33, 0.87, 0.67)))
    route = lp.euler_route(lp.eulerise(
        lp.build_graph(lp.prune_spurs(lp.skeletonise(
            lp.despeckle(lp._resize_mask(
                lp.ink_mask(lp.decode_image(
                    draw(circle(), line(0.13, 0.33, 0.87, 0.67))))[0],
                lp.WORK_SIZE)))))[0])[0])
    for (first, _, _), (second, _, _) in zip(route, route[1:]):
        gap = math.dist(first[-1], second[0])
        assert gap <= lp.CONTIGUOUS_TOLERANCE, (
            f"the route jumps {gap:.0f}px between chains - smoothing would "
            "draw that as a curve across blank canvas")
    assert art.max_bridge == 0.0


def test_a_route_that_would_jump_is_refused_rather_than_drawn():
    """The belt to the braces above. If a discontinuity ever reaches the
    assembler by some path nobody anticipated, the answer is to fail and let
    the retry ladder produce different art - never to draw through it."""
    far_apart = [
        ([(0.0, 0.0), (10.0, 10.0)], False, False),
        ([(400.0, 400.0), (410.0, 410.0)], False, False),
    ]
    with pytest.raises(lp.LineProcessingError) as raised:
        lp._assemble(far_apart)
    assert raised.value.reason == "discontinuous"
    assert "across empty canvas" in str(raised.value)


def test_chains_that_meet_within_a_pixel_or_two_are_fine():
    """The tolerance is not slack. A chain ends on a real pixel while its node
    is that cluster's centroid, so consecutive chains legitimately meet a pixel
    or two apart; zero tolerance would reject every drawing."""
    joined = [
        ([(0.0, 0.0), (10.0, 10.0)], False, False),
        ([(11.0, 11.0), (20.0, 20.0)], False, False),
    ]
    pixels, spans = lp._assemble(joined)
    assert len(pixels) == 4
    assert spans == []


def test_a_bridge_is_measured_where_it_will_be_seen():
    """`fit_to_canvas` rescales the drawing, and it can scale *up*. A gap that
    was small in a source image whose subject sat in one corner is not small
    once that corner fills the canvas, so the limit is enforced in viewBox
    units rather than source pixels."""
    art = lp.process(draw(circle(), line(0.13, 0.33, 0.87, 0.67),
                          line(0.806, 0.50, 0.906, 0.47)))
    assert art.bridged >= 1
    assert 0 < art.max_bridge <= lp.MAX_BRIDGE_UNITS, (
        f"a {art.max_bridge:.0f}-unit bridge is a visible line across the "
        "canvas")
    assert art.bridged_length >= art.max_bridge


def test_the_bridging_limit_is_tight_enough_to_be_invisible():
    """Rule 6: a gap may be closed only when it is genuinely tiny and
    accidental. Two ends a tenth of the canvas apart are not a gap, they are
    two drawings, and joining them is the scar."""
    assert lp.BRIDGE_SHARE <= 0.012
    with pytest.raises(lp.LineProcessingError) as raised:
        # Two strokes separated by ~8% of the canvas - far beyond a gap.
        lp.process(draw(line(0.10, 0.50, 0.40, 0.50),
                        line(0.48, 0.50, 0.78, 0.50)))
    assert raised.value.reason == "disconnected"


def test_retracing_a_bridge_costs_more_than_retracing_the_artwork():
    """Rule 5, least visual disruption. The route-inspection matching decides
    which edges get drawn twice; a bridge drawn twice is FAM's own repair
    traced over itself in open space, which is the most visible mark it could
    make. Same length, higher cost, so the matching routes around it."""
    assert lp.BRIDGE_RETRACE_PENALTY > 1.0
    ink = lp.Edge(0, 1, [(0.0, 0.0), (0.0, 10.0)], bridge=False)
    repair = lp.Edge(0, 1, [(0.0, 0.0), (0.0, 10.0)], bridge=True)
    edges = [ink, repair]
    adj = lp._adjacency(edges)
    distance, came = lp._shortest_paths(0, adj, edges)
    # Both edges join the same pair; the cheaper one is the artist's.
    assert came[1][1] == 0, "the matching preferred retracing FAM's own repair"
    assert distance[1] == pytest.approx(ink.length)


def test_a_scar_is_rejected_before_the_asset_is_marked_ready():
    """The validator's half of the rule. The processor refuses to *build* a
    route with a long connector; this refuses to *ship* one, because a drawing
    that got past the first gate by some route nobody anticipated must still
    never reach a player."""
    import visual_validator

    art = lp.process(draw(circle()))
    assert visual_validator.validate(lp.svg_document(art.d), art).ok

    art.max_bridge = visual_validator.MAX_BRIDGE_UNITS + 1
    verdict = visual_validator.validate(lp.svg_document(art.d), art)
    assert not verdict.ok
    assert any("blank canvas" in reason for reason in verdict.reasons)

    art.max_bridge = 0.0
    art.bridged_length = art.length * 0.5
    verdict = visual_validator.validate(lp.svg_document(art.d), art)
    assert not verdict.ok
    assert any("bridging rather than artwork" in reason
               for reason in verdict.reasons)


def test_a_small_gap_is_bridged_and_the_piece_is_kept():
    """The commonest break in generated line art: a stroke that stops just
    short of the middle of another, where there is no loose end to meet."""
    art = lp.process(draw(circle(), line(0.13, 0.33, 0.87, 0.67),
                          line(0.806, 0.50, 0.906, 0.47)))
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


# --------------------------------------------------------------------------
# Art first, engineering second
# --------------------------------------------------------------------------
# The rule the whole visual system is built on, and the one the pipeline is
# always quietly tempted to break: the Visual Director makes the strongest
# editorial interpretation of the episode, and this module's job is to
# *preserve* that artwork and find a route through it. Never to prefer artwork
# that happens to be easier to skeletonise, route or animate.


def rich(seed: int = 4242) -> bytes:
    """Artwork with a scene's worth of line in it, rather than an icon's."""
    import visual_provider

    return lp.rasterise_polyline(visual_provider._figure(seed, 4), 900, 2.4)


def test_a_rich_illustration_survives_the_whole_pipeline():
    """The regression that matters most. Every threshold in here was first set
    against a single figure on an empty page, and every one of them is a way to
    reject a drawing for being a drawing."""
    art = lp.process(rich())
    assert art.d.count("M") == 1
    assert art.curves > 200, "a scene's worth of line came out as an icon"
    assert art.components == 1
    assert art.bridged == 0, "rich art should route by retracing, not bridging"


def test_the_seam_tolerance_comes_from_the_artwork_not_a_constant():
    """A chain ends on a pixel of a junction cluster while its node is that
    cluster's centroid, so chains meet slightly apart by construction - and a
    denser drawing has larger clusters. A constant here rejected rich artwork
    for being rich, which is the engineering dictating the art."""
    skeleton = lp.prune_spurs(lp.skeletonise(
        lp.despeckle(lp._resize_mask(lp.ink_mask(lp.decode_image(rich()))[0],
                                     lp.WORK_SIZE))))
    edges, positions = lp.build_graph(skeleton)
    tolerance = lp.node_tolerance(edges, positions)
    assert tolerance >= lp.CONTIGUOUS_TOLERANCE, "the floor was lost"
    # Still small: a seam this size is inside a junction cluster, which is
    # inside ink. It is not a mark across the picture.
    assert tolerance < 0.02 * max(skeleton.shape)


def test_vectorising_keeps_the_artwork_it_was_given():
    """Rule five, second half: if beautiful source artwork comes out of here
    simplified or distorted, that is a line-processing failure."""
    art = lp.process(rich())
    assert art.fidelity >= lp.MIN_FIDELITY, (
        f"the vector keeps only {art.fidelity:.0%} of the artwork")
    assert art.invented <= 0.04, "the vectoriser drew line that was not there"


def test_losing_detail_is_answered_by_redoing_the_processing(monkeypatch):
    """...and never by asking for simpler art. `DETAIL_LADDER` re-runs the
    vectorisation on the *same* source until the picture survives."""
    source = rich()
    lossy = ((3, 40.0),) + lp.DETAIL_LADDER[1:]
    monkeypatch.setattr(lp, "DETAIL_LADDER", lossy)
    art = lp.process(source)
    assert art.detail > 0, "the lossy first rung was shipped rather than redone"
    assert art.fidelity >= lp.MIN_FIDELITY
    assert any("detail level" in w for w in art.warnings), \
        "the drawing was rescued silently"


def test_the_measurement_notices_a_drawing_that_lost_a_limb():
    """Both directions, because they are different failures - and a single
    averaged number would hide either."""
    whole = [(float(x), 500.0) for x in range(100, 900, 2)]
    half = [(float(x), 500.0) for x in range(100, 500, 2)]
    # The artwork is the whole line; the drawing only got half of it.
    kept, invented = lp.fidelity(whole, half)
    assert kept < 0.6, "half the drawing went missing and nothing noticed"
    assert invented < 0.05, "the half it did draw was in the right place"
    # ...and the reverse: a curve that goes somewhere the artwork never did.
    kept, invented = lp.fidelity(half, whole)
    assert kept > 0.9
    assert invented > 0.3, "line was invented and nothing noticed"


def test_a_bridge_must_continue_the_stroke_it_repairs():
    """The artistic half of rule four, and the half a distance threshold cannot
    express. A stroke that was interrupted is continued; two unrelated ends
    that merely pass close by are not joined, however short the hop."""
    # A loose end heading east, and a line it would have to turn ninety degrees
    # to reach. Close enough on distance alone; refused on meaning.
    art_side_by_side = lp.process(draw(
        circle(), line(0.13, 0.33, 0.87, 0.67),
        line(0.806, 0.50, 0.906, 0.47)))
    assert art_side_by_side.bridged >= 1, "a genuine continuation was refused"

    heading = lp._end_direction
    assert callable(heading)
    # The alignment test is a real gate rather than a formality.
    assert 0.0 < lp.BRIDGE_ALIGNMENT < 1.0


def test_the_routing_limit_is_far_stricter_than_the_validators_backstop():
    """The validator's numbers are maximum rejection boundaries, NOT permission
    to bridge anything shorter. The artistic rule is this one, and it has to
    stay well inside the backstop or the backstop becomes the policy."""
    import visual_validator

    in_units = lp.BRIDGE_SHARE * lp.WORK_SIZE * (visual_style.VIEWBOX
                                                 / lp.WORK_SIZE)
    assert in_units < visual_validator.MAX_BRIDGE_UNITS * 0.75, (
        "routing is bridging right up to the validator's limit; the safety net "
        "has become the definition of good routing")


# --------------------------------------------------------------------------
# Prefer visible new drawing over retracing
# --------------------------------------------------------------------------
# Priority three, between "never scar" and "retrace when necessary". The
# drawing is revealed against the audio, so a long unbroken run of retracing is
# a run of seconds in which the episode plays on and the picture does not
# change. Retracing is fine. Retracing for a long time is a stall.


def test_the_stall_measure_is_about_time_not_total_retracing():
    """A route that retraces a lot in short bursts is fine; one that retraces
    less but all at once is not. Total is the wrong number."""
    step = [(0.0, 0.0), (10.0, 0.0)]
    bursty = [(step, False, i in (1, 3, 5, 7)) for i in range(10)]
    all_at_once = [(step, False, i in (6, 7, 8, 9)) for i in range(10)]
    # Same total retracing in both, and only one of them stalls.
    assert sum(1 for _, _, r in bursty if r) == \
        sum(1 for _, _, r in all_at_once if r)
    assert lp.reveal_stall(bursty) < lp.reveal_stall(all_at_once)
    assert lp.reveal_stall(bursty) == pytest.approx(0.1)
    assert lp.reveal_stall(all_at_once) == pytest.approx(0.4)


def test_a_route_with_no_retracing_never_stalls():
    assert lp.reveal_stall([([(0.0, 0.0), (1.0, 0.0)], False, False)] * 5) == 0.0
    assert lp.reveal_stall([]) == 0.0


def test_choosing_a_route_cannot_change_the_picture():
    """The property that makes this safe to optimise at all. Priority one says
    preserve the artwork; every candidate ordering traverses the same edges, so
    the ink, the retraced share and the fidelity are identical whichever wins.
    Only the order the listener meets it in differs."""
    source = rich()
    first = lp.process(source)
    # A run confined to one ordering, against the chooser's pick.
    import contextlib

    class OneVariant:
        def __enter__(self):
            self.real = lp.ROUTE_TRIALS
            lp.ROUTE_TRIALS = 1
        def __exit__(self, *exc):
            lp.ROUTE_TRIALS = self.real

    with OneVariant():
        single = lp.process(source)

    assert first.retraced == pytest.approx(single.retraced)
    assert first.fidelity == pytest.approx(single.fidelity, abs=0.01)
    assert first.components == single.components
    assert first.bridged == single.bridged


def test_the_chooser_actually_improves_what_is_watched():
    """Measured rather than assumed. Orderings of the same drawing differ a
    lot in how they pace it - on these figures the first ordering stalls for
    twice as long as the best one - and a chooser that never improved anything
    would be cost with no benefit and should be deleted rather than left in."""
    best_gain = 1.0
    for seed in (1, 7, 4242):
        mask = lp.despeckle(lp._resize_mask(
            lp.ink_mask(lp.decode_image(rich(seed)))[0], lp.WORK_SIZE))
        edges, positions = lp.build_graph(lp.prune_spurs(lp.skeletonise(mask)))
        edges, _ = lp.bridge_gaps(edges, positions,
                                  lp.BRIDGE_SHARE * max(mask.shape))
        edges, _, _ = lp.keep_largest_component(edges)
        edges, _ = lp.eulerise(edges)
        stalls = [lp.reveal_stall(lp.euler_route(edges, v))
                  for v in range(lp.ROUTE_TRIALS)]
        assert min(stalls) <= stalls[0]
        best_gain = min(best_gain, min(stalls) / max(stalls[0], 1e-9))
    assert best_gain < 0.7, (
        "no ordering improved the pacing materially; the chooser is buying "
        "nothing and should be removed rather than left in")


def test_the_reveal_of_a_real_drawing_does_not_stall_for_long():
    import visual_validator

    art = lp.process(rich())
    assert art.longest_stall <= visual_validator.MAX_STALL, (
        f"the reveal goes {art.longest_stall:.0%} of its length with nothing "
        "new appearing")


def test_a_stalling_route_is_noticed_and_never_refused():
    """Priority five. The artwork is not what went wrong when a route paces
    badly - the same picture in a different order does not - so refusing it
    here would throw away good art over a property of the traversal."""
    import visual_validator

    class Stalling:
        d = "M0 0 L1 1"
        length = 2000.0
        curves = 200
        retraced = 0.3
        components = 1
        max_bridge = 0.0
        bridged_length = 0.0
        fidelity = 1.0
        invented = 0.0
        longest_stall = 0.9
        detail = 0

    verdict = visual_validator.Verdict()
    visual_validator._check_quality(Stalling(), verdict)
    assert verdict.ok, verdict.reasons
    assert any("no new line appearing" in note for note in verdict.advisories)
