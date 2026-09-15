"""What has to be true before a listener is shown a picture.

Three separate jobs that happen to run in one pass, and it is worth naming them
separately because they fail for different reasons and want different answers:

* **Structural** - is this one ordered path with a real `d`, in a real
  viewBox? If not, the vectoriser produced something the player cannot reveal,
  and there is a bug here rather than in the art.
* **Safe** - an SVG is a document, not an image. It can carry script, event
  handlers, external references and embedded HTML, and this one is served to a
  browser. FAM generates every byte of these files itself, which is exactly the
  reasoning that would let a hole through unnoticed: the check is cheap, and
  "we wrote it so it must be fine" has never been a security argument.
* **Good enough to show** - is it mostly blank, is it a scribble, is the
  subject a speck in the corner, is it pressed against the edges? These are the
  failures an image model actually produces, and they are the ones a retry can
  fix - which is why they are checked here rather than left to taste.

The verdict carries `reasons` rather than a boolean, because `visuals` uses
them to choose the next rung of the retry ladder, and because "the visual
failed" in a log is a sentence nobody can act on.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import visual_style

#: Below this, the drawing is a squiggle rather than an illustration. In
#: viewBox units, on a 1000-wide canvas - so 900 is roughly one circle.
MIN_LENGTH = 900.0
#: Above this it is scribble: no continuous-line illustration of one subject
#: needs sixty canvas-widths of line, and what produces that number is a model
#: filling space.
MAX_LENGTH = 60_000.0
#: A drawing must occupy a reasonable share of its square. A beautiful subject
#: rendered at 8% of the canvas is a thumbnail of nothing.
MIN_EXTENT = 0.30
#: ...and must not fill it wall to wall either, which is what "no negative
#: space" looks like as a number.
MAX_EXTENT = 0.995
#: How much of the path may sit in the outermost band of the canvas before the
#: composition reads as a crop rather than a picture.
MAX_EDGE_SHARE = 0.18
#: Retracing is allowed - the pen goes back over its own line - but past this
#: it stops being invisible.
MAX_RETRACED = 0.35
#: Fewer curves than this is not a drawing; more is detail nobody can see at
#: the size these are shown, and a path the player has to reveal smoothly.
MIN_CURVES = 12
MAX_CURVES = 6000

#: Things an SVG can contain that this one never should.
FORBIDDEN_TAGS = ("script", "foreignObject", "image", "use", "animate",
                  "animateTransform", "set", "iframe", "style")
FORBIDDEN_PATTERNS = (
    (re.compile(r"<!DOCTYPE", re.I), "a DOCTYPE, which can carry entities"),
    (re.compile(r"<!ENTITY", re.I), "an entity declaration"),
    (re.compile(r"\son[a-z]+\s*=", re.I), "an event handler attribute"),
    (re.compile(r"javascript:", re.I), "a javascript: URL"),
    (re.compile(r"url\(\s*['\"]?(https?:|//)", re.I), "an external url() reference"),
    (re.compile(r"xlink:href|(?<![a-zA-Z-])href\s*=", re.I), "an external reference"),
)


@dataclass
class Verdict:
    ok: bool = True
    reasons: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def fail(self, reason: str) -> "Verdict":
        self.ok = False
        self.reasons.append(reason)
        return self

    def as_dict(self) -> dict:
        return {"ok": self.ok, "reasons": list(self.reasons),
                "metrics": dict(self.metrics)}


def validate(svg: str, art=None, thumbnail: bytes = b"") -> Verdict:
    """Everything, in one pass. Never raises - a validator that can throw is a
    validator that can take the episode down with the picture."""
    verdict = Verdict()
    _check_safe(svg, verdict)
    root = _check_structure(svg, verdict)
    if root is not None:
        _check_geometry(root, verdict, art)
    if art is not None:
        _check_quality(art, verdict)
    if thumbnail:
        _check_thumbnail(thumbnail, verdict)
    return verdict


def _check_safe(svg: str, verdict: Verdict) -> None:
    for pattern, description in FORBIDDEN_PATTERNS:
        if pattern.search(svg):
            verdict.fail(f"the SVG contains {description}")
    lowered = svg.lower()
    for tag in FORBIDDEN_TAGS:
        if f"<{tag.lower()}" in lowered:
            verdict.fail(f"the SVG contains a <{tag}> element")


def _check_structure(svg: str, verdict: Verdict):
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        verdict.fail(f"the SVG does not parse: {exc}")
        return None
    if _local(root.tag) != "svg":
        verdict.fail("the document is not an <svg>")
        return None
    box = (root.get("viewBox") or "").split()
    if len(box) != 4:
        verdict.fail("the SVG has no usable viewBox")
        return None
    try:
        width, height = float(box[2]), float(box[3])
    except ValueError:
        verdict.fail("the viewBox is not numeric")
        return None
    if width <= 0 or height <= 0 or abs(width - height) > 1e-6:
        verdict.fail("the viewBox is not a positive square")
        return None
    verdict.metrics["view_box"] = " ".join(box)

    paths = [el for el in root.iter() if _local(el.tag) == "path"]
    if not paths:
        verdict.fail("the SVG has no path in it")
        return None
    if len(paths) > 1:
        # One episode, one drawing, one path. More than one means the pen was
        # lifted, which is the whole thing this feature promises it never does.
        verdict.fail(f"the SVG has {len(paths)} paths; there must be exactly one")
        return None
    path = paths[0]
    d = (path.get("d") or "").strip()
    if not d:
        verdict.fail("the path is empty")
        return None
    if d.count("M") + d.count("m") > 1:
        verdict.fail("the path lifts the pen (more than one moveto)")
    fill = (path.get("fill") or "").strip().lower()
    if fill not in ("none",):
        verdict.fail(f"the path is filled (fill={fill or 'unset'!r}); FAM line "
                     "art is stroke only")
    verdict.metrics["d_length"] = len(d)
    return root


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _check_geometry(root, verdict: Verdict, art) -> None:
    """Where the drawing sits on its square.

    Read from the path's own numbers rather than from `art`, so that an SVG
    that came from somewhere else - a regeneration, a stored record, a file
    somebody edited - is judged on what it actually contains.
    """
    path = next((el for el in root.iter() if _local(el.tag) == "path"), None)
    if path is None:
        return
    numbers = [float(n) for n in
               re.findall(r"-?\d+(?:\.\d+)?", path.get("d") or "")]
    if len(numbers) < 4:
        verdict.fail("the path has too few points to be a drawing")
        return
    xs, ys = numbers[0::2], numbers[1::2]
    size = float((root.get("viewBox") or "0 0 1000 1000").split()[2])
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    extent = max(right - left, bottom - top) / size
    verdict.metrics["extent"] = round(extent, 4)
    if extent < MIN_EXTENT:
        verdict.fail(f"the subject fills only {extent:.0%} of the canvas")
    if extent > MAX_EXTENT:
        verdict.fail("the drawing fills the whole canvas, leaving no negative space")

    margin = visual_style.SAFE_MARGIN
    outside = sum(1 for x, y in zip(xs, ys)
                  if x < margin or y < margin
                  or x > size - margin or y > size - margin)
    share = outside / max(1, len(xs))
    verdict.metrics["edge_share"] = round(share, 4)
    if share > MAX_EDGE_SHARE:
        verdict.fail(f"{share:.0%} of the line is in the margin; the "
                     "composition is running off the canvas")


def _check_quality(art, verdict: Verdict) -> None:
    length = float(getattr(art, "length", 0.0))
    curves = int(getattr(art, "curves", 0))
    retraced = float(getattr(art, "retraced", 0.0))
    components = int(getattr(art, "components", 1))
    verdict.metrics.update({"length": round(length, 1), "curves": curves,
                            "retraced": round(retraced, 4),
                            "components": components})
    if length < MIN_LENGTH:
        verdict.fail(f"the line is only {length:.0f} long; there is almost "
                     "nothing on the page")
    if length > MAX_LENGTH:
        verdict.fail(f"the line is {length:.0f} long; that is a scribble, not "
                     "an illustration")
    if curves < MIN_CURVES:
        verdict.fail("the drawing has too little shape in it")
    if curves > MAX_CURVES:
        verdict.fail(f"the drawing has {curves} curves in it; too dense to "
                     "reveal smoothly")
    if retraced > MAX_RETRACED:
        verdict.fail(f"{retraced:.0%} of the route is drawn over line already "
                     "drawn; the artwork was too badly connected")


def _check_thumbnail(thumbnail: bytes, verdict: Verdict) -> None:
    """The picture actually renders, and has both paper and ink in it.

    "The file is not empty" is the cheaper question, and this project's
    standing rule is that a check must perform the real action rather than
    confirm that it was configured. So the thumbnail is decoded and looked at:
    a PNG of a blank ivory square is a perfectly valid PNG.
    """
    try:
        import line_processor

        grey = line_processor.decode_image(thumbnail)
    except Exception as exc:  # noqa: BLE001
        verdict.fail(f"the thumbnail did not render ({exc})")
        return
    if grey.size == 0:
        verdict.fail("the thumbnail is empty")
        return
    darkest = float(grey.min())
    ink_share = float((grey < 0.55).mean())
    verdict.metrics["thumbnail_ink"] = round(ink_share, 5)
    if darkest > 0.5:
        verdict.fail("the thumbnail has no line in it")
    if ink_share > 0.30:
        verdict.fail(f"the thumbnail is {ink_share:.0%} ink; far too dense for "
                     "this style")


def is_fatal(reasons) -> bool:
    """Whether a failure is worth regenerating for.

    Everything is, today - every reason above is either a bad picture or a bad
    vectorisation, and both are answered by drawing it again. The function
    exists so that the *decision* has a name and a place, rather than being an
    unconditional retry somebody has to infer from the absence of a condition.
    """
    return bool(reasons)


def report() -> dict:
    return {
        "min_length": MIN_LENGTH,
        "max_length": MAX_LENGTH,
        "min_extent": MIN_EXTENT,
        "max_edge_share": MAX_EDGE_SHARE,
        "max_retraced": MAX_RETRACED,
    }
