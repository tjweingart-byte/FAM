"""Artwork drawn on purpose, so the right answer is known rather than eyeballed.

Shared by `test_line_processor.py` and `test_visual_source_gate.py`, and the
sharing is the point rather than the tidiness: the source gate's thresholds
were calibrated against artwork rendered this way, and the line processor's
against artwork rendered the other way. Two renderers that drifted apart would
mean the gate was being tested on pictures the processor never sees, and
neither suite would notice.

A fixture PNG would make these tests about one picture somebody happened to
save. Drawing the input makes them about the geometry.
"""
from __future__ import annotations

import math

import numpy as np

import line_processor as lp


def render(strokes, size: int, width: float, colour=None) -> bytes:
    """Strokes in 0..1 coordinates, rendered as FAM renders things: charcoal on
    ivory, anti-aliased, with round caps and joins."""
    coverage = np.zeros((size, size), dtype=np.float32)
    for stroke in strokes:
        points = [(x * size, y * size) for x, y in stroke]
        np.maximum(coverage, lp._coverage(points, size, width), out=coverage)
    return lp.ink_on_paper(coverage, colour or ())


def sample(fn, n: int = 600) -> list:
    return [fn(i / n) for i in range(n + 1)]


def circle(cx: float = 0.5, cy: float = 0.5, r: float = 0.3,
           n: int = 600) -> list:
    """One closed outline - which is, structurally, every pictogram."""
    return sample(lambda t: (cx + r * math.cos(t * math.tau),
                             cy + r * math.sin(t * math.tau)), n)


def line(x0: float, y0: float, x1: float, y1: float) -> list:
    return sample(lambda t: (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
