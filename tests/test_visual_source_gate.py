"""Would this feel premium enough to be a finished myFAM episode thumbnail?

The gate in front of the vectoriser, and the reason it is in front rather than
inside. FAM's line processor is good enough to turn almost anything into one
ordered path - which is exactly the danger. A pipeline that rescues an icon
puts an icon in the feed, and every threshold downstream of here is a
measurement of the *vector* rather than a judgement about the *art*.

The distinction this file exists to keep sharp: **continuous line describes how
a FAM illustration is drawn, never how much is drawn.** An image model asked for
"one continuous line" returns a pictogram unless something refuses one.

What it deliberately does not test, because it cannot: "generic" and
"inconsistent with the approved references" are taste. They belong to the image
model, the style block and `visual_references/`, and a gate that pretended to
measure them would be worse than one that says it does not.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import line_processor as lp  # noqa: E402
import visual_provider  # noqa: E402
import visual_style  # noqa: E402
import visual_validator as vv  # noqa: E402

SIZE = 720


def paint(strokes, size: int = SIZE, width: float = 1.5,
          colour: tuple | None = None) -> bytes:
    coverage = np.zeros((size, size), dtype=np.float32)
    for stroke in strokes:
        np.maximum(coverage,
                   lp._coverage([(x * size, y * size) for x, y in stroke],
                                size, width), out=coverage)
    paper = np.array(lp._hex(visual_style.PAPER), dtype=np.float32)
    ink = np.array(colour or lp._hex(visual_style.INK), dtype=np.float32)
    blended = (paper[None, None, :] * (1 - coverage[:, :, None])
               + ink[None, None, :] * coverage[:, :, None])
    return lp.encode_png(np.clip(blended, 0, 255).astype(np.uint8))


def ring(cx=0.5, cy=0.5, r=0.3, n=500) -> list:
    return [(cx + r * math.cos(t / n * math.tau),
             cy + r * math.sin(t / n * math.tau)) for t in range(n + 1)]


def scene() -> list:
    """A stand-in for an editorial illustration: a figure's worth of structure
    rather than one closed outline."""
    strokes = [[(0.10, 0.16 + row * 0.08), (0.90, 0.16 + row * 0.08)]
               for row in range(9)]
    strokes += [[(0.10 + col * 0.10, 0.14), (0.10 + col * 0.10, 0.86)]
                for col in range(9)]
    return strokes


# --------------------------------------------------------------------------
# The icon test
# --------------------------------------------------------------------------
def test_an_icon_is_refused_however_cleanly_it_is_drawn():
    """A single closed outline is one perfect continuous line and is not a FAM
    illustration. This is the whole point of the gate: the pipeline would have
    vectorised it beautifully."""
    verdict = vv.screen_source(paint([ring()]))
    assert not verdict.ok
    assert any("icon" in reason for reason in verdict.reasons), verdict.reasons
    # And it says the thing the failure is actually about.
    assert any("how it is drawn" in reason for reason in verdict.reasons)


def test_an_editorial_scene_is_accepted():
    verdict = vv.screen_source(paint(scene()))
    assert verdict.ok, verdict.reasons


def test_the_placeholder_provider_can_pass_its_own_gate():
    """If it could not, the whole no-credential demo path would be dead and the
    gate would only ever be exercised in tests - which is how a gate calibrated
    against nothing real gets shipped."""
    for seed in (1, 7, 4242, 99999):
        data = lp.rasterise_polyline(visual_provider._figure(seed, 4), 900, 2.4)
        verdict = vv.screen_source(data)
        assert verdict.ok, (seed, verdict.reasons)


def test_a_speck_on_the_canvas_is_refused():
    """The hard bound: at this size it is not a composition at all."""
    verdict = vv.screen_source(paint([ring(r=0.09)]))
    assert not verdict.ok
    assert any("speck" in reason for reason in verdict.reasons), verdict.reasons


def test_clutter_is_refused_at_the_other_end():
    dense = [[(0.06, 0.06 + i * 0.012), (0.94, 0.06 + i * 0.012)]
             for i in range(72)]
    dense += [[(0.06 + i * 0.012, 0.06), (0.06 + i * 0.012, 0.94)]
              for i in range(72)]
    verdict = vv.screen_source(paint(dense))
    assert not verdict.ok
    assert any("fill or a photograph" in r or "scribble" in r
               for r in verdict.reasons), verdict.reasons


def test_a_blank_canvas_is_refused():
    verdict = vv.screen_source(paint([]))
    assert not verdict.ok


def test_unreadable_bytes_fail_rather_than_raise():
    """A gate that can throw can take the episode down with the picture."""
    verdict = vv.screen_source(b"not an image at all")
    assert not verdict.ok
    assert verdict.reasons


# --------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------
def test_colour_is_refused_when_it_can_be_seen_and_said_when_it_cannot():
    """FAM line art is charcoal on ivory. But "we did not look" and "there was
    nothing to find" are different answers, and reporting the first as the
    second is the silent-success failure this project keeps paying for."""
    coloured = paint(scene(), colour=(220, 40, 30))
    verdict = vv.screen_source(coloured)
    if lp.colour_share(coloured) is None:
        assert "not measured" in verdict.metrics["source_colour_note"]
    else:
        assert not verdict.ok
        assert any("coloured" in reason for reason in verdict.reasons)


# --------------------------------------------------------------------------
# It reports itself
# --------------------------------------------------------------------------
def test_every_measurement_reaches_the_verdict():
    """A refusal nobody can act on is a refusal nobody can tune."""
    verdict = vv.screen_source(paint(scene()))
    for key in ("source_ink", "source_extent", "source_crossings"):
        assert key in verdict.metrics
    assert "source_gate" in vv.report()


def test_the_crossing_measure_tells_an_outline_from_a_drawing():
    """The number the icon test rests on, checked directly rather than through
    three layers of pipeline."""
    mask_icon = lp.despeckle(lp._resize_mask(
        lp.ink_mask(lp.decode_image(paint([ring()])))[0], lp.WORK_SIZE))
    mask_scene = lp.despeckle(lp._resize_mask(
        lp.ink_mask(lp.decode_image(paint(scene())))[0], lp.WORK_SIZE))
    icon = lp.line_crossings(mask_icon)
    drawing = lp.line_crossings(mask_scene)
    assert icon < vv.MIN_SOURCE_CROSSINGS <= drawing
    assert drawing > icon * 2, (icon, drawing)


# --------------------------------------------------------------------------
# A heuristic is a rejection aid, not a definition of good FAM art
# --------------------------------------------------------------------------
# The failure this section exists to stop: FAM refusing a beautiful drawing
# because it scored low on a density number. The approved references are the
# source of truth, and at least one of them - a single seated figure with the
# page left empty around it - is exactly the composition these measures mark
# down. If a metric and the art disagree, the metric is wrong.


def spare() -> list:
    """Sophisticated and sparse: a figure's gesture, given the page. Fewer
    marks than the grid above by a wide margin, and better."""
    return [
        [(0.30, 0.70), (0.34, 0.52), (0.44, 0.40), (0.56, 0.36)],
        [(0.56, 0.36), (0.66, 0.40), (0.70, 0.52), (0.66, 0.66)],
        [(0.34, 0.52), (0.50, 0.58), (0.66, 0.52)],
        [(0.24, 0.74), (0.76, 0.74)],
    ]


def test_a_spare_beautiful_composition_is_drawn_not_refused():
    """The whole point. It is noticed, it is reported, and it is drawn."""
    verdict = vv.screen_source(paint(spare()))
    assert verdict.ok, verdict.reasons
    # Noticed rather than ignored - the number is still worth having.
    assert verdict.advisories or verdict.metrics["source_crossings"] >= \
        vv.MIN_SOURCE_CROSSINGS


def test_an_advisory_is_never_a_refusal():
    """Structural, so it cannot be undone by adding one more `fail` call in the
    wrong branch."""
    verdict = vv.Verdict()
    verdict.advise("sparse for a FAM scene")
    assert verdict.ok
    assert verdict.advisories
    assert not verdict.reasons
    assert verdict.as_dict()["advisories"] == ["sparse for a FAM scene"]


def test_the_icon_test_is_a_structural_fact_not_a_density_threshold():
    """The measurement that forced this design. A hard density bound was tried,
    set just above a plain circle at 2.4 - and the spare figure study above
    scores 2.3. Density cannot tell restraint from a pictogram, so it does not
    get to decide.

    Structure can, categorically: a pictogram is a closed outline, with nothing
    meeting anything and nothing stopping anywhere."""
    def measured(strokes):
        mask = lp.despeckle(lp._resize_mask(
            lp.ink_mask(lp.decode_image(paint(strokes)))[0], lp.WORK_SIZE))
        return lp.structure(mask), lp.line_crossings(mask)

    (icon_structure, icon_density) = measured([ring()])
    (spare_structure, spare_density) = measured(spare())

    # The density measure genuinely cannot separate them: 2.0 against 2.3.
    assert abs(icon_density - spare_density) < 0.5
    # The structural one separates them completely.
    assert sum(icon_structure) < vv.MIN_SOURCE_STRUCTURE <= sum(spare_structure)
    assert not hasattr(vv, "HARD_MIN_SOURCE_CROSSINGS"),         "the density bound came back as a refusal"


def test_every_soft_measure_has_a_hard_bound_well_beyond_it_or_none_at_all():
    """A target that doubles as a refusal is a definition, not an aid."""
    assert vv.HARD_MIN_SOURCE_EXTENT < vv.MIN_SOURCE_EXTENT
    assert vv.HARD_MAX_SOURCE_INK > vv.MAX_SOURCE_INK
    assert vv.HARD_MAX_SOURCE_CROSSINGS > vv.MAX_SOURCE_CROSSINGS


def test_the_report_says_which_numbers_can_actually_refuse_a_picture():
    """An unenforced threshold looks exactly like an enforced one from
    outside - the tier system's lesson, applied to pixels."""
    gate = vv.report()["source_gate"]
    assert set(gate["refuses"]) and set(gate["advises"])
    assert "rejection aid" in gate["note"]
