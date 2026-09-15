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

And one job that happens in a *different* pass, before any of them:

* **Worth drawing at all** - `screen_source` looks at the raster the image
  model returned, before the vectoriser touches it, and asks the one question
  that decides whether FAM looks premium: *would this feel premium enough to
  appear as a finished myFAM episode thumbnail?* A provable icon, a coloured
  image, a speck or a page of scribble is regenerated rather than vectorised;
  everything softer than that is *noticed* and drawn anyway. It is a separate
  entry point because it is a judgement about the **art**, and everything else
  here is a judgement about the **vector** - the two fail for different reasons
  and only one of them is the pipeline's fault.

The verdict carries `reasons` rather than a boolean, because `visuals` uses
them to choose the next rung of the retry ladder, and because "the visual
failed" in a log is a sentence nobody can act on.

**The direction of every fix here is fixed, and it is the rule the whole
visual system is built on: the art comes first and the engineering preserves
it.** A drawing that arrives beautiful and leaves ugly is a line-processing
failure, and the answer is to preserve the source and fix the processing -
never to ask for simpler artwork so the vectoriser has an easier time. Nothing
in this module may be relaxed in the other direction.
"""
from __future__ import annotations

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
#: Retracing is allowed - the pen goes back over its own line - and it is the
#: *preferred* way to keep the route continuous, because a retrace adds no mark
#: to the picture at all where a bridge adds one that was never in the artwork.
#:
#: **Raised from 0.35, and the old number was the engineering dictating the
#: art.** A rich editorial scene has far more loose ends than a single icon
#: does, so route inspection has more of them to pair, and measured across two
#: dozen figures the honest retrace for this style is about 30% with a spread
#: that touched 37%. A 35% ceiling therefore rejected perfectly good
#: illustrations for the crime of being illustrations, and the way to pass it
#: was to draw something simpler.
#:
#: There is still a ceiling, because retracing does cost something - not ink,
#: but *pace*: while the pen redraws, the reveal is not revealing. At 60% the
#: pen still spends three fifths of its travel on line the listener has not
#: seen, which reads as a drawing appearing rather than a pen fidgeting.
MAX_RETRACED = 0.60
#: The hard one. A bridge is the only mark in a finished drawing that was not
#: in the artwork, and past this length it stops being an invisible repair of
#: an accidental gap and becomes a line drawn across empty canvas - a scar.
#: In viewBox units, so 18 is 1.8% of the canvas width.
#:
#: This is a product-quality constraint rather than a tuning preference: a
#: listener watching the line travel must never see it strike out across the
#: negative space. The processor already refuses to *build* such a route; this
#: refuses to *ship* one, because a drawing that got past the first gate by
#: some path nobody anticipated must still not reach a player.
#:
#: **A SAFETY NET, NOT AN ARTISTIC ALLOWANCE.** This number and the one below
#: are maximum *rejection boundaries*. They are not permission to bridge
#: anything shorter, and a route must never prefer a synthetic bridge on the
#: grounds that it would pass here. The artistic rule is stricter and lives in
#: `line_processor.BRIDGE_SHARE` (0.8% of the canvas, about six working
#: pixels) with `BRIDGE_ALIGNMENT` beside it: a bridge repairs a genuinely
#: tiny accidental gap between endpoints that belong to the same intended
#: stroke, and nothing else. Everything else retraces ink the artist drew.
MAX_BRIDGE_UNITS = 18.0
#: And all of them together, as a share of the whole drawing. Several bridges
#: each under the limit still add up to a picture that is partly invention.
MAX_BRIDGED_SHARE = 0.02
#: How much of the accepted source artwork the finished vector must still
#: contain. Below this the drawing that reaches a listener is not the drawing
#: that was approved: detail has been smoothed away, a form has been rounded
#: off, or a whole passage has gone.
#:
#: **A failure here is a LINE PROCESSING failure and is never answered by
#: simplifying the art.** `line_processor.DETAIL_LADDER` already re-ran the
#: vectorisation on the same source before this was measured; reaching here
#: means even the gentlest pass could not keep the picture, which is a bug in
#: the processing rather than a fact about the artwork.
MIN_FIDELITY = 0.90
#: How much of the finished vector may pass nowhere near the source artwork.
#: Line the artist never drew, by another name - and a different failure from
#: losing line, which is why it is a second number rather than an average.
MAX_INVENTED = 0.04
#: How long the reveal may go without new line appearing, as a share of the
#: journey - the longest unbroken run of retracing.
#:
#: **Advisory, never a refusal.** The artwork is not at fault when a route
#: stalls: the same picture drawn in a different order does not stall, which is
#: why `line_processor.best_route` tries several orderings and keeps the best.
#: Refusing a drawing here would be throwing away good art over a property of
#: the traversal, which is the numbers overruling the picture twice over.
MAX_STALL = 0.10
#: Fewer curves than this is not a drawing; more is detail nobody can see at
#: the size these are shown, and a path the player has to reveal smoothly.
#:
#: The floor was raised from 12 with the style: twelve curves is an icon, and
#: a FAM illustration is a scene. It is a weak test of richness - a scribble
#: has plenty of curves - but it is the one that catches the specific failure
#: of an image model answering "one continuous line" with a pictogram.
MIN_CURVES = 40
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
    """Why something was refused - and, separately, what was merely noticed.

    **`reasons` refuse; `advisories` do not.** The split exists because a
    quality heuristic is a rejection aid and not a definition of good FAM art,
    and the two get confused the moment they share a list. A composition can be
    spare, elegant and sophisticated and still score low on a density measure -
    the meditation reference is exactly that - and refusing it for the number
    would be the numbers overriding the picture, which is the one thing the
    visual system is not allowed to do.

    So a measurement that is *evidence* of a problem rather than *proof* of one
    goes in `advisories`: recorded on the record, in the trace, on the log and
    on `/api/health`, and costing the picture nothing. Only the bounds that
    catch something provable - a blank canvas, a coloured one, a single closed
    outline - refuse.
    """

    ok: bool = True
    reasons: list = field(default_factory=list)
    advisories: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def fail(self, reason: str) -> "Verdict":
        self.ok = False
        self.reasons.append(reason)
        return self

    def advise(self, note: str) -> "Verdict":
        """Noticed, reported, and never a refusal on its own."""
        self.advisories.append(note)
        return self

    def as_dict(self) -> dict:
        return {"ok": self.ok, "reasons": list(self.reasons),
                "advisories": list(self.advisories),
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

    # Did the drawing survive being vectorised? Reported both ways, because
    # losing the artwork and inventing line are different failures.
    kept = float(getattr(art, "fidelity", 1.0))
    invented = float(getattr(art, "invented", 0.0))
    verdict.metrics["fidelity"] = round(kept, 4)
    verdict.metrics["invented"] = round(invented, 4)
    verdict.metrics["detail"] = int(getattr(art, "detail", 0))
    if kept < MIN_FIDELITY:
        verdict.fail(
            f"the vector keeps only {kept:.0%} of the source artwork; the "
            "drawing a listener would see is not the drawing that was "
            "approved. This is a line-processing failure - preserve the source "
            "and fix the processing, do not ask for simpler art")
    if invented > MAX_INVENTED:
        verdict.fail(
            f"{invented:.0%} of the finished curve passes nowhere near the "
            "source artwork; the vectoriser is drawing line the artist did not")

    # How the reveal is *paced*. Retracing is allowed and is what keeps the pen
    # off the negative space; retracing for a long time is a stretch where the
    # audio plays on and the picture does not change. Noticed, never refused -
    # the drawing is not what went wrong.
    stall = float(getattr(art, "longest_stall", 0.0))
    verdict.metrics["longest_stall"] = round(stall, 4)
    if stall > MAX_STALL:
        verdict.advise(
            f"the reveal goes {stall:.0%} of its length with no new line "
            "appearing; the route retraces in one long run rather than in "
            "short ones")

    # The no-scars rule. Everything else here is about whether the picture is
    # good; this is about whether it contains a mark FAM invented.
    max_bridge = float(getattr(art, "max_bridge", 0.0))
    bridged_length = float(getattr(art, "bridged_length", 0.0))
    verdict.metrics["max_bridge"] = round(max_bridge, 2)
    verdict.metrics["bridged_length"] = round(bridged_length, 2)
    if max_bridge > MAX_BRIDGE_UNITS:
        verdict.fail(
            f"the route crosses {max_bridge:.0f} units of blank canvas in one "
            f"step; anything over {MAX_BRIDGE_UNITS:.0f} is a visible line "
            "that was never in the artwork")
    if length and bridged_length / length > MAX_BRIDGED_SHARE:
        verdict.fail(
            f"{bridged_length / length:.1%} of the drawing is bridging rather "
            "than artwork; the illustration was too broken to repair invisibly")


# --------------------------------------------------------------------------
# Before the vectoriser: is this art worth drawing?
# --------------------------------------------------------------------------
# Two tiers, and the distinction is the whole design of this gate.
#
# **Hard bounds refuse. Targets only advise.** A richness heuristic is a
# rejection aid, never a definition of good FAM art: a spare, beautifully
# composed illustration - the meditation reference is one - scores lower on
# every density measure here than a busier drawing that is worse. Refusing it
# for the number would be a numeric heuristic overriding visual quality, which
# is the one thing this system may not do.
#
# So each measure has a *hard* bound set where the failure is provable, and a
# *target* set where FAM's art usually sits. Missing the target is recorded and
# reported and costs the picture nothing.

#: Below this there is nothing on the page at all - there is no drawing to
#: vectorise, so this one refuses.
MIN_SOURCE_INK = 0.004
#: Above this the image is a fill, a wash or a photograph rather than line art.
HARD_MAX_SOURCE_INK = 0.35
#: Where FAM's negative space usually leaves the ink. Denser than this is worth
#: noticing and is not worth refusing: a bold composition can be dark.
MAX_SOURCE_INK = 0.16
#: A drawing smaller than this is a speck, whatever it is a speck of.
HARD_MIN_SOURCE_EXTENT = 0.25
#: Where a composition that fills its square sits. Below it, a drawing is
#: probably a symbol floating in space - probably, which is why it advises.
MIN_SOURCE_EXTENT = 0.45
#: How many separate runs of ink a straight scan across the picture meets, on
#: average. **This is the icon test**, and it is the measure that decides
#: whether FAM's feed is illustrations or pictograms.
#:
#: Measured: a plain circle, the shape of every icon, scores 2.0 - almost every
#: scan line crosses it exactly twice, because that is what a single closed
#: outline *is*. A scene with a figure, a desk and a window behind it scores 8.
#:
#: **It advises and never refuses**, and the reason is a measurement rather
#: than a principle. A hard bound was tried here, set just above the circle at
#: 2.4 - and a spare figure study, one gesture with the page left empty around
#: it, scores 2.3. That is the exact composition the approved references are
#: full of, and it would have been refused for the number. Density cannot tell
#: restraint from a pictogram, so it does not get to decide; `MIN_SOURCE_
#: STRUCTURE` below does, on a structural fact instead.
MIN_SOURCE_CROSSINGS = 3.0
#: The other end. A scan meeting sixty separate marks is crossing pattern fill
#: or scribble; two dozen is merely dense, and dense can be magnificent.
HARD_MAX_SOURCE_CROSSINGS = 60.0
MAX_SOURCE_CROSSINGS = 26.0
#: **The icon test, done structurally.** A pictogram is a closed outline:
#: nothing meets anything and nothing stops anywhere, so it has no junctions
#: and no loose ends at all. `line_processor.structure` measures exactly that -
#: a plain circle is `(0, 0)`, a spare figure study is `(1, 5)`, a scene is
#: `(81, 18)`.
#:
#: This is the one richness bound that refuses, and it can, because it is a
#: fact about the drawing rather than a threshold somebody guessed: a single
#: line meeting another line, or a single loose end, is enough to clear it. It
#: cannot misfire on restraint, which is the whole reason the density measure
#: above was demoted to an advisory.
MIN_SOURCE_STRUCTURE = 1
#: How much of the image may be meaningfully coloured. FAM line art is charcoal
#: on ivory, and this one refuses rather than advises: it is an explicit style
#: violation rather than a judgement about composition.
MAX_SOURCE_COLOUR = 0.02


def screen_source(data: bytes) -> Verdict:
    """Is this artwork good enough to be a FAM episode's thumbnail?

    Run on the raster the image model returned, **before** the vectoriser sees
    it. The one question behind every threshold below is the one the product
    actually cares about: *would this feel premium enough to appear as a
    finished myFAM episode thumbnail?* If not, the answer is a different
    picture, not a cleverer traversal - so this gate spends a few milliseconds
    to save the retry ladder a whole vectorisation, and more importantly to
    stop good engineering from rescuing bad art into the feed.

    **It refuses far less than it measures, on purpose.** These numbers are
    rejection aids, not a definition of good FAM art. A spare, sophisticated
    composition - one figure, beautifully placed, with the page left empty
    around it - scores lower on every density measure here than a busier
    drawing that is worse, and refusing it for the number would be exactly the
    failure of letting a heuristic overrule the picture. So each measure has a
    hard bound where the failure is *provable* (a blank canvas, a coloured one,
    a speck, a single closed outline) and a target where FAM's art usually
    sits. Missing the target lands in `advisories`, which are recorded on the
    record, in the trace and on `/api/health`, and cost the picture nothing.

    The one richness bound that does refuse is structural rather than
    statistical - a pictogram is a closed outline, with no junctions and no
    loose ends - and that is precisely why it is allowed to. Density was tried
    as a hard bound and had to be demoted: set just above a plain circle at
    2.4, it refused a spare figure study at 2.3.

    What it can refuse: blank, a speck, a closed outline, a fill, scribble,
    coloured. What it can only *notice*: sparse, dense, small. And two things
    it does not touch at all - "generic" and "inconsistent with the approved
    references" are taste, they belong to the image model, `visual_style` and
    `visual_references/`, and a gate that pretended to measure them would be
    worse than one that says it does not.

    Never raises, for the same reason `validate` never does: a gate that can
    throw can take the episode down with the picture.

    It costs a decode, a threshold and a thinning pass - roughly fifty
    milliseconds, all of which `process` then does again. Repeating it is the
    price of the gate standing in front of the work rather than inside it, and
    of it being testable on its own; the thinning in particular is not
    optional, because the structural icon test is the one bound here worth
    refusing on.
    """
    verdict = Verdict()
    try:
        import line_processor

        # The same preparation `process` will do, through the same function -
        # so the gate is judging the mask that actually gets vectorised.
        mask, _ = line_processor.prepare_mask(data)
    except Exception as exc:  # noqa: BLE001
        return verdict.fail(f"the artwork could not be read ({exc})")

    if not mask.any():
        return verdict.fail("the artwork is blank")

    ink = float(mask.mean())
    rows, cols = mask.nonzero()
    extent = max(int(rows.max() - rows.min()),
                 int(cols.max() - cols.min())) / float(max(mask.shape))
    crossings = line_processor.line_crossings(mask)
    verdict.metrics.update({"source_ink": round(ink, 5),
                            "source_extent": round(extent, 4),
                            "source_crossings": round(crossings, 2)})

    if ink < MIN_SOURCE_INK:
        verdict.fail(f"there is almost nothing drawn ({ink:.2%} of the canvas "
                     "is line); this is a sketch, not an illustration")
    elif ink > HARD_MAX_SOURCE_INK:
        verdict.fail(f"{ink:.0%} of the canvas is ink; this is a fill or a "
                     "photograph rather than line art")
    elif ink > MAX_SOURCE_INK:
        verdict.advise(f"{ink:.0%} of the canvas is ink, denser than FAM's "
                       "usual negative space")

    if extent < HARD_MIN_SOURCE_EXTENT:
        verdict.fail(f"the drawing spans only {extent:.0%} of its square; it "
                     "is a speck on the canvas")
    elif extent < MIN_SOURCE_EXTENT:
        verdict.advise(f"the drawing spans {extent:.0%} of its square, which "
                       "may be a symbol floating in space rather than a "
                       "composition")

    junctions, endpoints = line_processor.structure(mask)
    verdict.metrics.update({"source_junctions": junctions,
                            "source_endpoints": endpoints})
    if junctions + endpoints < MIN_SOURCE_STRUCTURE:
        verdict.fail(
            "this is an icon, not an editorial illustration: it is a closed "
            "outline with nothing meeting anything and nothing stopping "
            "anywhere. Continuous line describes how it is drawn, never how "
            "much is drawn")

    if crossings < MIN_SOURCE_CROSSINGS:
        verdict.advise(
            f"sparse for a FAM scene (a scan meets {crossings:.1f} marks; the "
            f"usual is {MIN_SOURCE_CROSSINGS:.0f} or more) - which a spare, "
            "beautifully composed illustration also is, so it is drawn")
    elif crossings > HARD_MAX_SOURCE_CROSSINGS:
        verdict.fail(f"a scan across this meets {crossings:.0f} separate "
                     "marks; it is pattern fill or scribble rather than a "
                     "drawing")
    elif crossings > MAX_SOURCE_CROSSINGS:
        verdict.advise(f"dense for this style (a scan meets {crossings:.0f} "
                       "separate marks)")

    colour = line_processor.colour_share(data)
    verdict.metrics["source_colour"] = colour
    if colour is None:
        # Said out loud rather than passed over. "We did not look" and "there
        # was nothing to find" are different answers, and reporting the first
        # as the second is the silent-success failure this project keeps
        # paying for.
        verdict.metrics["source_colour_note"] = (
            "not measured - Pillow is not installed, so the image could only "
            "be read as luminance")
    elif colour > MAX_SOURCE_COLOUR:
        verdict.fail(f"{colour:.0%} of the artwork is coloured; FAM line art "
                     "is charcoal on ivory")
    return verdict


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


def report() -> dict:
    return {
        "min_length": MIN_LENGTH,
        "max_length": MAX_LENGTH,
        "min_extent": MIN_EXTENT,
        "max_edge_share": MAX_EDGE_SHARE,
        "max_retraced": MAX_RETRACED,
        "max_bridge_units": MAX_BRIDGE_UNITS,
        "max_bridged_share": MAX_BRIDGED_SHARE,
        "min_fidelity": MIN_FIDELITY,
        "max_invented": MAX_INVENTED,
        "max_stall": MAX_STALL,
        "min_curves": MIN_CURVES,
        # Split the way the gate is split, so it is visible from outside which
        # numbers can actually refuse a picture and which only comment on one.
        "source_gate": {
            "refuses": {
                "min_ink": MIN_SOURCE_INK,
                "max_ink": HARD_MAX_SOURCE_INK,
                "min_extent": HARD_MIN_SOURCE_EXTENT,
                "min_structure": MIN_SOURCE_STRUCTURE,
                "max_crossings": HARD_MAX_SOURCE_CROSSINGS,
                "max_colour": MAX_SOURCE_COLOUR,
            },
            "advises": {
                "max_ink": MAX_SOURCE_INK,
                "min_extent": MIN_SOURCE_EXTENT,
                "min_crossings": MIN_SOURCE_CROSSINGS,
                "max_crossings": MAX_SOURCE_CROSSINGS,
            },
            "note": "the advisory numbers never refuse a picture. A richness "
                    "heuristic is a rejection aid, not a definition of good "
                    "FAM art, and a spare beautiful composition scores low on "
                    "every one of them.",
        },
    }
