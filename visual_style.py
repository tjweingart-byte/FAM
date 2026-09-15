"""One visual language, written down once.

"A single line connecting us all." Every eligible FAM episode gets **one**
continuous-line illustration, and the same vector serves both roles it is asked
to play - the finished image is the thumbnail, the partially-drawn image is the
player. There is deliberately no second artwork for the second role, for the
same reason the script cache stores scripts rather than audio: the expensive
thing is made once and used twice.

This module holds the part of that which never changes from episode to episode.
`visual_director` decides *what* to draw; this decides *how everything is
drawn*, and it does not rotate. FAM should look like one artist, so the style
is a constant and not a parameter - a per-episode style would give a feed where
every tile came from a different studio, which is the one thing a visual system
is for preventing.

Three consumers:

* `visual_provider` turns `image_prompt()` into a request to an image model.
* `line_processor` reads `INK`, `PAPER` and the geometry constants to know what
  is line and what is paper, and what the vector it emits must look like.
* the player and the thumbnail read `PAPER`, `INK` and `STROKE_WIDTH`, so the
  square a listener watches being drawn is the square they saw on the tile.

**The reference images are the strongest lever here.** The same thing
`examples/` is to the writing, `visual_references/` is to the drawing: rules
describe a style loosely, and a provider that supports reference conditioning
matches an example closely. Two or three approved FAM illustrations in that
folder move the output further than any further wording below.

And when they are present they **outrank the wording** - `reference_preamble`
says so to the model in as many words. A description and a demonstration of the
same style never agree exactly; line weight and the amount of empty space are
the two that always differ. Without an explicit ordering the model averages
them, and the average is the generic look everything below exists to rule out.
"""
from __future__ import annotations

import base64
import logging
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent

#: Warm ivory. The canvas a listener stares at for three minutes, so it is a
#: paper colour rather than a white one - white reads as "not loaded yet",
#: which is exactly the wrong thing for a square that is deliberately blank at
#: the start of an episode.
PAPER = "#F8F4EA"
#: Charcoal, not black. Black on ivory is a printer; this is a pen.
INK = "#171820"
#: Fine. The line has to stay a line at 1536px and still be visible at the
#: 132px a myFAM tile gives it, and the answer to that is a thin stroke that
#: scales rather than a heavier one that does not.
STROKE_WIDTH = 1.4
#: Every vector is emitted in this space, whatever the source image measured.
#: One coordinate system means the thumbnail, the player and the iOS client
#: can all be handed the same `d` attribute and get the same picture.
VIEWBOX = 1000
#: Nothing may be drawn nearer than this to the edge, in viewBox units. A
#: composition that runs off the canvas reads as a crop of something bigger,
#: and the validator rejects one that does.
SAFE_MARGIN = 40

#: Where approved FAM illustrations live, for providers that can be shown one.
REFERENCE_DIR = PROJECT_ROOT / "visual_references"
#: What a reference may be. Anything else in the folder is ignored rather than
#: sent - an image model handed a .DS_Store is a wasted request.
REFERENCE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
#: How many references to show at once. More is not better: a provider given
#: eight references averages them into something that looks like none of them.
MAX_REFERENCES = 3


@dataclass(frozen=True)
class StyleSpec:
    """The constant half of every image request.

    Frozen, and built once at the bottom of this module. It is a dataclass
    rather than loose module constants so that a test can assert on the whole
    of it, and so `visual_provider` has one object to log and store beside the
    artwork - "which style produced this" has to be answerable later, when the
    style has moved on and the old tiles are still in the feed.
    """

    name: str
    version: int
    paper: str
    ink: str
    stroke_width: float
    #: The house description, in the order an art director would say it.
    canvas: tuple[str, ...] = ()
    line: tuple[str, ...] = ()
    composition: tuple[str, ...] = ()
    behaviour: tuple[str, ...] = ()
    character: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    references: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "paper": self.paper,
            "ink": self.ink,
            "stroke_width": self.stroke_width,
        }


STYLE = StyleSpec(
    name="fam-single-line",
    # Bumped when the look changes. It is part of the visual key, so a bump
    # retires every stored illustration rather than leaving a feed that is half
    # one style and half another - which is worse than either style alone.
    version=1,
    paper=PAPER,
    ink=INK,
    stroke_width=STROKE_WIDTH,
    canvas=(
        "a perfect square",
        f"a flat, warm ivory ground, exactly the colour {PAPER}, edge to edge",
        "no border, no frame, no vignette, no paper texture, no drop shadow",
    ),
    line=(
        f"one extremely fine charcoal line, the colour {INK}",
        "uniform visual weight from end to end - never tapering, never thickening",
        "rounded ends and rounded joins, as though drawn with a fine pen held flat",
        "the line is the only mark on the page",
    ),
    composition=(
        "generous negative space; at least half the canvas is untouched ivory",
        "one clear subject with a strong silhouette, given room to breathe",
        "asymmetry is welcome; dead-centre symmetry is not required",
        "editorial, like the opening illustration of a long magazine piece",
        "nothing touches the edge of the canvas",
    ),
    behaviour=(
        "long, flowing, unbroken paths that wander with intent",
        "elegant loops and returns rather than short disconnected strokes",
        "the whole drawing reads as one journey of the pen across the page",
        "the pen never leaves the paper: every part of the image is connected "
        "to every other part",
    ),
    character=(
        "sophisticated, restrained, human, timeless",
        "confident enough to leave things out",
        "intelligent rather than decorative",
    ),
    avoid=(
        "thick or variable-width lines",
        "shading, hatching, cross-hatching, stippling",
        "fills of any kind, gradients, colour",
        "text, letters, numbers, labels, captions, watermarks, signatures",
        "logos or brand marks",
        "cartoon or comic styling, mascots, faces with expressions",
        "icon sets, pictograms, infographic furniture, arrows, charts",
        "photorealism and three-dimensional rendering",
        "clutter, busy detail, repeated pattern fill",
        "random scribble used to fill space",
        "many small disconnected strokes",
    ),
)


def _bullets(items) -> str:
    return "\n".join(f"- {item}" for item in items)


def style_block() -> str:
    """The house style, as prose an image model is given verbatim.

    Deliberately one string rather than something assembled per request: every
    episode is drawn by the same artist, so every episode is described to that
    artist in the same words.
    """
    return (
        "FAM SINGLE-LINE ILLUSTRATION - house style\n\n"
        "CANVAS\n" + _bullets(STYLE.canvas) + "\n\n"
        "THE LINE\n" + _bullets(STYLE.line) + "\n\n"
        "COMPOSITION\n" + _bullets(STYLE.composition) + "\n\n"
        "HOW THE LINE MOVES\n" + _bullets(STYLE.behaviour) + "\n\n"
        "CHARACTER\n" + _bullets(STYLE.character) + "\n\n"
        "NEVER\n" + _bullets(STYLE.avoid)
    )


#: Attempt 2. The first attempt failed structurally - the art looked like one
#: line and was not - so this says the one thing that failure is about, loudly,
#: and changes nothing else. Escalating the *style* on a structural failure
#: would be answering a question nobody asked.
CONTINUITY_INSISTENCE = (
    "CRITICAL, above every other instruction: the drawing must be ONE single "
    "unbroken continuous line. Start the pen at one point, never lift it, and "
    "finish. Every stroke must physically touch the rest of the drawing - no "
    "floating marks, no separate pieces, no detached details, no dots. If a "
    "detail cannot be reached without lifting the pen, leave it out."
)

#: Attempt 3. Structure failed twice, so the subject is the thing to change:
#: fewer forms, larger, fewer crossings. A simpler picture is a picture that
#: can be drawn in one stroke.
SIMPLIFY_INSISTENCE = (
    "Draw this as simply as it can possibly be drawn. ONE single unbroken "
    "continuous line, one large central form, very few crossings, and a great "
    "deal of empty ivory. Fewer elements drawn larger is better than more "
    "elements drawn smaller. Leave out every detail that is not the idea "
    "itself."
)


def reference_preamble(references) -> str:
    """What to say when the model is being *shown* the style rather than told it.

    One sentence of it is load-bearing: **the images outrank the words.** The
    style block below is a careful description of a look, and a description and
    a demonstration of the same look never agree exactly - line weight and the
    amount of empty space are the two that always differ. Without an explicit
    ordering the model averages them, and the averaged result is neither: it is
    the generic thing the style block exists to rule out.

    The references are named rather than merely attached so that the prompt kept
    beside the finished artwork says which images governed it. A year from now,
    "why does this one look different" is answerable only if the record knows
    what it was shown.
    """
    names = ", ".join(getattr(ref, "name", "?") for ref in references)
    return (
        "THE ATTACHED IMAGES ARE THE STYLE.\n"
        f"You have been given {len(references)} approved FAM illustration(s)"
        + (f" ({names})" if names else "") + ". They define the house style: "
        "the exact line weight, the ivory ground, the restraint, the amount of "
        "empty space, the way the line flows and returns, and the overall "
        "character of the drawing.\n\n"
        "Match them. Draw a NEW illustration of the subject described below in "
        "that style. Do not copy, trace, collage or reuse their subjects - only "
        "how they are drawn. Draw ONE illustration on ONE square canvas, "
        "whatever the reference images are arranged as.\n\n"
        "The written style notes below describe the same look in words. Where "
        "the words and the images disagree, FOLLOW THE IMAGES - they are the "
        "source of truth and the words are only an approximation of them."
    )


def image_prompt(brief, attempt: int = 1) -> str:
    """The whole request: what to draw, then how FAM draws everything.

    `brief` is a `visual_director.VisualBrief`. It is duck-typed rather than
    imported so that this module has no dependency on the director - the style
    is the constant and the brief is the variable, and a constant that imports
    its variable is a constant that cannot be tested on its own.

    `attempt` escalates the *structural* insistence and never the art
    direction, because the retries this ladder exists for are structural
    failures. See `visuals.RETRY_LADDER`, which is where the ladder is decided;
    this only knows how to say each rung.
    """
    subject = (getattr(brief, "subject", "") or "").strip()
    form = (getattr(brief, "primary_form", "") or "").strip()
    metaphor = (getattr(brief, "visual_metaphor", "") or "").strip()
    composition = (getattr(brief, "composition", "") or "").strip()
    tone = (getattr(brief, "tone", "") or "").strip()
    complexity = (getattr(brief, "complexity", "") or "medium").strip()
    avoid = [str(item).strip() for item in (getattr(brief, "avoid", None) or [])
             if str(item).strip()]

    lines = ["WHAT TO DRAW"]
    if subject:
        lines.append(f"- Subject: {subject}")
    if form:
        lines.append(f"- The main form on the page: {form}")
    if metaphor:
        lines.append(f"- The idea the drawing carries: {metaphor}")
    if composition:
        lines.append(f"- Composition: {composition}")
    if tone:
        lines.append(f"- Tone: {tone}")
    lines.append(f"- Complexity: {COMPLEXITY_NOTE.get(complexity, COMPLEXITY_NOTE['medium'])}")
    if avoid:
        lines.append("- For this image in particular, avoid: " + ", ".join(avoid))

    parts = ["\n".join(lines), style_block()]
    if attempt >= 3:
        parts.append(SIMPLIFY_INSISTENCE)
    elif attempt >= 2:
        parts.append(CONTINUITY_INSISTENCE)
    return "\n\n".join(parts)


#: What each complexity band means as *drawing*, rather than as a number. The
#: same reasoning as `DEPTH_BANDS` for duration: "medium" has to be a
#: description of the picture or it becomes a stroke count to hit.
COMPLEXITY_NOTE = {
    "low": "very few elements - one form, drawn large, with a great deal of "
           "empty ivory around it",
    "medium": "one dominant form with one or two supporting gestures; still "
              "mostly empty ivory",
    "high": "one dominant form with several supporting gestures, still "
            "uncluttered and still with clear negative space",
}


@dataclass(frozen=True)
class Reference:
    """One approved FAM illustration, ready to be sent to a provider."""

    name: str
    media_type: str
    data_b64: str


def available() -> list[str]:
    """Every illustration in the folder, in the order they are considered.

    Separate from `references()` because "what is here" and "what is being
    used" are different questions, and the gap between them is the thing worth
    seeing. Alphabetical, which is what makes the selection controllable: name
    the three you want `01-`, `02-`, `03-` and the rest sort after them.
    """
    if not REFERENCE_DIR.is_dir():
        return []
    return [path.name for path in sorted(REFERENCE_DIR.iterdir())
            if path.is_file() and path.suffix.lower() in REFERENCE_SUFFIXES]


def references(limit: int = MAX_REFERENCES) -> list[Reference]:
    """The approved FAM illustrations being shown to the image model.

    Empty is not a failure: a provider that is given no reference falls back to
    the written style, which is exactly the behaviour before this folder
    existed. It is read from disk on every call rather than cached, because the
    folder being fillable without a restart is most of what makes it a usable
    lever.

    **A file that is present and not used says so.** The cap is real - a model
    given many references averages them into something that looks like none of
    them - but a fourth illustration dropped in and silently ignored is the
    quiet kind of wrong this project keeps paying for: the folder looks right,
    the health page looks right, and one of the pictures defining the house
    style is simply not in the room. Alphabetical order makes the choice
    controllable; the log line makes it visible.
    """
    names = available()
    if not names:
        return []
    if len(names) > limit:
        log.warning(
            "%d approved references in %s but only %d are shown to the image "
            "model: using %s, NOT using %s. Rename to change the choice - they "
            "are taken in alphabetical order.",
            len(names), REFERENCE_DIR, limit,
            ", ".join(names[:limit]), ", ".join(names[limit:]))
    found: list[Reference] = []
    for name in names[:limit]:
        path = REFERENCE_DIR / name
        try:
            raw = path.read_bytes()
        except OSError as exc:
            log.warning("could not read visual reference %s: %s", name, exc)
            continue
        media_type = mimetypes.guess_type(name)[0] or "image/png"
        found.append(Reference(name, media_type,
                               base64.b64encode(raw).decode("ascii")))
    return found


def report() -> dict:
    """What `/api/health` says about the style.

    The reference count is here because an empty folder is invisible from
    outside and is the difference between art that matches FAM and art that
    merely matches the adjectives above.
    """
    names = [ref.name for ref in references()]
    present = available()
    unused = [name for name in present if name not in names]
    return {
        "style": STYLE.name,
        "version": STYLE.version,
        "paper": STYLE.paper,
        "ink": STYLE.ink,
        "stroke_width": STYLE.stroke_width,
        "references": names,
        # What is in the folder as well as what is in force. A file that is
        # present and unused is invisible from the first list alone, and an
        # illustration somebody added expecting it to count is exactly the
        # thing worth being able to see from outside.
        "references_available": present,
        "references_unused": unused,
        "max_references": MAX_REFERENCES,
        "reference_dir": str(REFERENCE_DIR),
        "reference_note": (
            "no approved references on this machine - the style is being "
            "described in words only. Drop two or three approved FAM "
            "illustrations into visual_references/ to show the model the "
            "house voice instead of describing it."
        ) if not names else (
            f"{len(unused)} approved reference(s) in the folder are not being "
            f"shown to the image model ({', '.join(unused)}); at most "
            f"{MAX_REFERENCES} are used, in alphabetical order."
        ) if unused else "",
    }
