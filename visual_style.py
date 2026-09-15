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
from dataclasses import dataclass
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

#: **The active set, named.** These three illustrations are FAM's house style,
#: and they are listed here rather than being whichever files happen to sort
#: first. The difference matters: a set that depends on alphabetical order is a
#: set that changes when somebody adds a file, renames one, or copies the
#: folder onto a filesystem that sorts differently - silently, and in the one
#: place where silence is most expensive, because the whole product's look
#: moves with it.
#:
#: Entries are **stems**, not filenames, so it does not matter whether they
#: were saved as .png or .jpg; `REFERENCE_SUFFIXES` is tried in order.
#:
#: The three, and why these three: they cover what a FAM episode is usually
#: about - a person, an idea, motion - and between them they demonstrate the
#: full range of the line, from a pure figure through figure-with-architecture
#: to figure-with-landscape.
ACTIVE_REFERENCES = (
    "meditation",
    "profile-globe-city",
    "runner",
)

#: Approved, kept, and deliberately not in the active set. Recorded here so the
#: decision is written down rather than inferred from an absence.
#:
#: The whale is the most beautiful of the four and the least representative:
#: the baleen striping and the wave curls are far denser than anything the
#: style block asks for, and as a reference it would bias every episode toward
#: more line than the style wants. To try it, move its stem into
#: `ACTIVE_REFERENCES` and take one out - the set is capped at
#: `MAX_REFERENCES`, and swapping is the point of keeping a reserve.
RESERVE_REFERENCES = (
    "whale",
)


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
    #: What the drawing is *of*, as a class of picture. This is the block that
    #: separates an editorial illustration from an icon, and it is part of the
    #: house style rather than the per-episode brief because the answer is the
    #: same for every episode: a scene, never a symbol.
    subject_matter: tuple[str, ...] = ()
    composition: tuple[str, ...] = ()
    #: How much is on the page. Named separately from composition because
    #: "continuous line" is read as "simple drawing" by every image model
    #: unless something says otherwise, and this is the something.
    richness: tuple[str, ...] = ()
    behaviour: tuple[str, ...] = ()
    character: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()

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
    #
    # 2: art first. Version 1 described a look and left "how much is on the
    # page" to the model, which reads "one continuous line" as "one simple
    # icon" every time. This version says what a FAM illustration is *of* - a
    # scene, a relationship, a metaphor - and how much is in it.
    version=2,
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
    subject_matter=(
        "a SCENE, not a symbol: a place, a moment, a relationship between "
        "things, with enough around the subject to say where and why",
        "an environment the subject sits inside - a room, a street, a "
        "landscape, a workspace, a horizon - rendered in the same fine line",
        "people drawn as people: a real figure with posture and attention, "
        "not a stick figure, not a silhouette, not a head-and-shoulders icon",
        "a visual metaphor that carries the episode's idea, so that the "
        "picture says what the development MEANS and not merely who was in it",
        "relationships and consequences over labels: what changed, and what it "
        "changed for, rather than the logos of the parties involved",
    ),
    composition=(
        "generous negative space; roughly half the canvas is untouched ivory, "
        "and the drawn half is where the detail lives",
        "a clear focal point with depth around it: foreground, subject, and "
        "something receding behind",
        "asymmetry is welcome; dead-centre symmetry is not required",
        "editorial, like the opening illustration of a long magazine piece - "
        "the kind of drawing a reader stops on",
        "nothing touches the edge of the canvas",
    ),
    richness=(
        "CONTINUOUS LINE DOES NOT MEAN SIMPLE DRAWING. One unbroken line is "
        "how this is drawn, not how much is drawn.",
        "rich but restrained: real, observed detail - the fall of a sleeve, "
        "the pitch of a roofline, the set of a shoulder - and nothing "
        "decorative on top of it",
        "enough going on that watching the line arrive feels like discovery, "
        "and there is something new to find on a second look",
        "detail earns its place by meaning something; density for its own "
        "sake is clutter and is worse than nothing",
        "if it could be redrawn as a single icon without losing the idea, "
        "it is not yet a FAM illustration",
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
        "confident enough to leave things out - and confident enough to draw "
        "the thing properly rather than gesture at it",
        "intelligent rather than decorative",
        "premium: this has to hold its own as the cover of a finished episode",
    ),
    avoid=(
        "thick or variable-width lines",
        "shading, hatching, cross-hatching, stippling",
        "fills of any kind, gradients, colour",
        "text, letters, numbers, labels, captions, watermarks, signatures",
        "logos or brand marks, or a concept built around one",
        "cartoon or comic styling, mascots, faces with expressions",
        "icons, pictograms, app-icon or sticker styling, clip art",
        "generic corporate illustration - the lightbulb, the rocket, the "
        "cloud, the gear, the head full of cogs",
        "infographic furniture: arrows, charts, callouts, diagram layouts",
        "an equation of symbols - 'this thing plus that thing', a handshake, "
        "two marks meeting in the middle - standing in for an idea",
        "poster design, title-card layouts, anything arranged around where "
        "words would go",
        "photorealism and three-dimensional rendering",
        "clutter, busy detail, repeated pattern fill",
        "random scribble used to fill space",
        "many small disconnected strokes",
    ),
)


#: What is never in a FAM illustration, whatever the subject - the same rule as
#: `STYLE.avoid` above, in the shorter form the *per-episode* brief carries.
#:
#: **Two lists, deliberately, and they must not drift apart.** `STYLE.avoid` is
#: the house style's NEVER section and is described to the model at length;
#: this is the floor under `visual_director.VisualBrief.avoid`, which a model
#: returns and which is *added* to this rather than substituted for it - so a
#: model that answers with its own short `avoid` list cannot be how "no text"
#: stops being said. They live next to each other here because the failure is
#: adding a ban to one and forgetting the other, and that is only visible if
#: they are in the same file. `visual_director` imports this one.
BASELINE_AVOID = (
    "text", "logos", "shading", "colour", "gradients", "photorealism",
    "cartoon", "icons", "pictograms", "clip art",
    "generic corporate illustration", "infographic", "poster design",
    "handshake or two-symbols-meeting imagery", "clutter",
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
        "WHAT THE PICTURE IS OF\n" + _bullets(STYLE.subject_matter) + "\n\n"
        "HOW MUCH IS IN IT\n" + _bullets(STYLE.richness) + "\n\n"
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

#: Attempt 3. Structure failed twice, so the drawing's *connective tissue* is
#: the thing to change - not how much is in it.
#:
#: **This replaced `SIMPLIFY_INSISTENCE`, and the replacement is the point.**
#: The old rung told the model to "draw this as simply as it can possibly be
#: drawn... leave out every detail that is not the idea itself", which is the
#: engineering dictating the art: a structural failure in FAM's vectoriser was
#: being answered by making the illustration worse. Two attempts in, it was
#: also the rung most likely to produce the finished picture, so the feed's
#: hardest subjects were systematically its most icon-like tiles.
#:
#: The failure it is answering is real, and it has an answer that costs the art
#: nothing: the forms need to *touch*. A scene whose elements overlap and flow
#: into one another is exactly as rich and is drawable in one stroke, where the
#: same elements floating apart are not. So this rung asks for contact, not for
#: less. It is deliberately not called SIMPLIFY anything - a knob left behind
#: is an invitation to turn it back on.
CONNECTED_RICHNESS_INSISTENCE = (
    "The drawing keeps ALL of its richness - the scene, the figure, the "
    "environment, the detail. Do not simplify it, do not reduce it to a "
    "symbol, and do not leave elements out.\n\n"
    "Change only how the parts are JOINED. Every element must physically "
    "touch another: let the figure overlap the architecture, let the horizon "
    "run into the object, let a fold of cloth carry on into the thing behind "
    "it. Compose it so a single pen could travel the whole scene without "
    "lifting - through contact and overlap, never by leaving things out. "
    "Absolutely no floating marks, no detached details, no dots."
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
        "Look at how much is IN them. They are illustrations, not icons: a "
        "figure with real posture and weight, an environment around it, "
        "observed detail that rewards a second look. Match that level of "
        "richness as closely as you match the line weight.\n\n"
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
    meaning = (getattr(brief, "deeper_idea", "") or "").strip()
    scene = (getattr(brief, "scene", "") or "").strip()
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
    # Before the objects, because it is what the objects are for. A model given
    # a list of things to draw draws the things; given what they mean first, it
    # draws a picture about that and uses the things to say it.
    if meaning:
        lines.append(f"- What this is really about: {meaning}")
    # The scene leads the drawing instructions. `primary_form` on its own is
    # the line that produced icons: one object, named, on an empty page.
    if scene:
        lines.append(f"- The scene: {scene}")
    if form:
        lines.append(f"- At the centre of it: {form}")
    if metaphor:
        lines.append(f"- The idea the drawing carries: {metaphor}")
    if composition:
        lines.append(f"- Composition: {composition}")
    if tone:
        lines.append(f"- Tone: {tone}")
    lines.append(f"- How much is in it: {COMPLEXITY_NOTE.get(complexity, COMPLEXITY_NOTE['medium'])}")
    if avoid:
        lines.append("- For this image in particular, avoid: " + ", ".join(avoid))

    parts = ["\n".join(lines), style_block()]
    if attempt >= 3:
        parts.append(CONNECTED_RICHNESS_INSISTENCE)
    elif attempt >= 2:
        parts.append(CONTINUITY_INSISTENCE)
    return "\n\n".join(parts)


#: What each complexity band means as *drawing*, rather than as a number. The
#: same reasoning as `DEPTH_BANDS` for duration: "medium" has to be a
#: description of the picture or it becomes a stroke count to hit.
#:
#: **Rewritten so that the floor is still an illustration.** These used to run
#: from "very few elements - one form, drawn large" upward, which made `low` a
#: licence to draw an icon and made the band the director picked the thing that
#: decided whether an episode got a picture or a pictogram. Every band now
#: describes a scene; what changes across them is how much world is around the
#: subject, not whether there is one.
COMPLEXITY_NOTE = {
    "low": "an intimate scene - one figure or object rendered with real "
           "observed detail, with just enough of its surroundings to say "
           "where it is, and a great deal of empty ivory",
    "medium": "a scene with depth - the subject, something it is acting on or "
              "reacting to, and an environment receding behind them; still "
              "roughly half empty ivory",
    "high": "a fuller scene - the subject within a working environment, with "
            "several supporting elements and a sense of a larger world beyond "
            "the frame; detailed but never cluttered, and still with clear "
            "negative space",
}


@dataclass(frozen=True)
class Reference:
    """One approved FAM illustration, ready to be sent to a provider."""

    name: str
    media_type: str
    data_b64: str


def available() -> list[str]:
    """Every illustration file in the folder, alphabetically.

    Separate from `references()` because "what is here" and "what is in force"
    are different questions, and the gap between them is the thing worth
    seeing. This one is *not* the selection - `ACTIVE_REFERENCES` is.
    """
    if not REFERENCE_DIR.is_dir():
        return []
    return [path.name for path in sorted(REFERENCE_DIR.iterdir())
            if path.is_file() and path.suffix.lower() in REFERENCE_SUFFIXES]


def resolve(stem: str) -> Path | None:
    """The file for one named reference, whatever it was saved as.

    Tries `REFERENCE_SUFFIXES` in order, so `meditation` finds
    `meditation.png` before `meditation.jpg`. Two files with the same stem and
    different extensions is a muddle rather than an error: the first wins and
    the log says which, because silently picking one of two files somebody
    thought were the same file is exactly the kind of thing that is discovered
    a month later.
    """
    if not REFERENCE_DIR.is_dir():
        return None
    found = [REFERENCE_DIR / f"{stem}{suffix}" for suffix in REFERENCE_SUFFIXES
             if (REFERENCE_DIR / f"{stem}{suffix}").is_file()]
    if len(found) > 1:
        log.warning("reference %r exists as %s; using %s", stem,
                    ", ".join(path.name for path in found), found[0].name)
    return found[0] if found else None


def missing() -> list[str]:
    """Active references that are named and not on disk.

    The failure that matters. The manifest says the house style is these three
    illustrations; if one of them is not there, FAM is drawing in a style that
    is two thirds of the one it was told to draw in, and nothing else notices.
    """
    return [stem for stem in ACTIVE_REFERENCES if resolve(stem) is None]


def references(limit: int = MAX_REFERENCES) -> list[Reference]:
    """The approved FAM illustrations being shown to the image model.

    Empty is not a failure: a provider that is given no reference falls back to
    the written style, which is exactly the behaviour before this folder
    existed. It is read from disk on every call rather than cached, because the
    folder being fillable without a restart is most of what makes it a usable
    lever.

    **Which three is a decision, not an accident.** `ACTIVE_REFERENCES` names
    them, in order, and nothing else in the folder is ever sent. A selection
    that depended on alphabetical order would move the moment somebody added a
    file or renamed one - silently, and taking the whole product's look with
    it. Files that are present and not named are a reserve; a name that is not
    present is a loud warning, because FAM would otherwise be drawing in
    two-thirds of the style it was told to draw in and reporting healthy.
    """
    absent = missing()
    if absent:
        log.warning(
            "%d of FAM's %d house-style references are missing from %s: %s. "
            "Episodes will be drawn from a partial style. Put the files there, "
            "or change ACTIVE_REFERENCES.",
            len(absent), len(ACTIVE_REFERENCES), REFERENCE_DIR,
            ", ".join(absent))
    spare = [name for name in available()
             if name.rsplit(".", 1)[0] not in ACTIVE_REFERENCES]
    if spare:
        log.info("%s held in reserve, not shown to the image model",
                 ", ".join(spare))

    found: list[Reference] = []
    for stem in ACTIVE_REFERENCES[:limit]:
        path = resolve(stem)
        if path is None:
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            log.warning("could not read visual reference %s: %s", path.name, exc)
            continue
        media_type = mimetypes.guess_type(path.name)[0] or "image/png"
        found.append(Reference(path.name, media_type,
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
    absent = missing()
    reserve = [name for name in present
               if name.rsplit(".", 1)[0] not in ACTIVE_REFERENCES]
    return {
        "style": STYLE.name,
        "version": STYLE.version,
        "paper": STYLE.paper,
        "ink": STYLE.ink,
        "stroke_width": STYLE.stroke_width,
        "references": names,
        # Four lists rather than one, because they answer four different
        # questions and only the first is visible from the artwork: what is in
        # force, what FAM was told to use, what is named and not on disk, and
        # what is sitting in the folder unused. A deployment that lost one file
        # is the case that matters, and `references` alone cannot show it -
        # two names in that list looks exactly like a two-reference style.
        "references_active": list(ACTIVE_REFERENCES),
        "references_missing": absent,
        "references_reserve": reserve,
        "references_available": present,
        "max_references": MAX_REFERENCES,
        "reference_dir": str(REFERENCE_DIR),
        "reference_note": (
            "no approved references on this machine - the style is being "
            "described in words only. Drop two or three approved FAM "
            "illustrations into visual_references/ to show the model the "
            "house voice instead of describing it."
        ) if not names else (
            f"{len(absent)} of FAM's {len(ACTIVE_REFERENCES)} house-style "
            f"references are missing ({', '.join(absent)}); episodes are being "
            f"drawn from a partial style. Put the files in "
            f"{REFERENCE_DIR}, or change ACTIVE_REFERENCES."
        ) if absent else "",
    }
