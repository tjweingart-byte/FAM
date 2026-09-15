"""What the illustration should depict - decided before anything is drawn.

The Visual Director draws nothing. It is the other half of `episode_
intelligence`: EI works out what the *episode* is about, and this works out
what the *picture* is about, from the same understanding, at the same moment.

    QUERY / TOPIC / RECOMMENDATION
                |
          RESEARCH / RETRIEVAL
                |
         EPISODE UNDERSTANDING
            /            \\
    EPISODE WRITER    VISUAL DIRECTOR
           |                |
      SCRIPT CHUNKS     VISUAL BRIEF

**The fork is the design.** The alternative - read the finished script and
illustrate it - cannot work here, because on search the audio starts before the
script is finished. An illustrator downstream of the writer would be an
illustrator that can only ever start after the listener already has sound, and
FAM's whole architecture is about starting things earlier rather than covering
the wait they cause.

So the director is a *sibling* consumer. It takes the best semantic context
that exists at the moment it runs, in a priority that differs by surface:

* **search** - the research packet, then the EI brief, then the typed query.
* **myFAM** - the recommendation's own meaning (title, tags, why it was
  picked), then the brief, then the packet.
* **DailyFAM** - the mix member's topic, then the brief, then the packet.

and never the completed script.

Two rules it inherits from EI, for the same reasons:

* **It must not be able to subtract availability.** Every failure - no key, a
  timeout, a refusal, unreadable JSON - falls back to a brief derived from the
  query alone, marked `degraded`, and says so in the log and on
  `/api/health`. A director that had quietly stopped running would otherwise
  look identical from outside to one that was working, and the episodes would
  merely be illustrated worse.
* **It never asserts a fact.** A picture cannot be wrong about a scoreline,
  but it can be wrong about *what happened*, and the safest way to never draw
  yesterday's result is to never be asked to. The brief describes forms and
  metaphors; it is explicitly told not to depict events, numbers or outcomes.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
from dataclasses import dataclass, field

import credentials
from anthropic_client import build_async_client
from config import settings

log = logging.getLogger(__name__)

#: How complex the drawing may be. Three bands, described as pictures in
#: `visual_style.COMPLEXITY_NOTE` rather than as element counts - the same
#: reasoning as DEPTH_BANDS for duration: a number to hit becomes a number to
#: pad to.
COMPLEXITIES = ("low", "medium", "high")

#: What is never in a FAM illustration, whatever the subject. The per-episode
#: `avoid` list the model returns is added to this, never substituted for it.
BASELINE_AVOID = (
    "text", "logos", "shading", "photorealism", "cartoon", "iconography",
    "infographic", "clutter",
)


@dataclass
class VisualBrief:
    """What one FAM illustration is of.

    Typed rather than a loose dict for the reason every schema in this project
    is typed: `visual_style.image_prompt` reads six of these fields by name,
    and a renamed key in an untyped payload is a prompt that quietly loses a
    line.
    """

    #: The episode this belongs to, in words. Not drawn; carried for the log,
    #: the stored metadata and the admin surface.
    query: str = ""
    #: The idea being illustrated, resolved. "AI infrastructure expansion",
    #: not "nvidia".
    subject: str = ""
    #: The main object on the page. Concrete and drawable: "semiconductor
    #: chip", not "growth".
    primary_form: str = ""
    #: What the line does with that form - the picture's one idea.
    visual_metaphor: str = ""
    #: How it sits on the square.
    composition: str = "centred subject with generous negative space"
    #: The feeling, in two or three adjectives.
    tone: str = "intelligent, elegant, restrained"
    #: low | medium | high.
    complexity: str = "medium"
    #: Subject-specific things to keep out, on top of BASELINE_AVOID.
    avoid: list = field(default_factory=lambda: list(BASELINE_AVOID))
    #: True when the model call did not happen or could not be used, and this
    #: brief was derived from the query alone. Reported, never hidden.
    degraded: bool = False
    #: Why it degraded. For the log, the stored metadata and /api/health.
    notes: list = field(default_factory=list)
    #: Which surface asked, and what it was able to offer. Kept so that "which
    #: kinds of context produce good pictures" is answerable later.
    source: str = ""

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)

    @property
    def usable(self) -> bool:
        """Enough to draw from. An empty subject and an empty form is not."""
        return bool(self.subject.strip() or self.primary_form.strip())


DIRECTOR_SYSTEM = (
    "You are FAM's visual director. FAM is an audio briefing app, and every "
    "episode carries one continuous-line illustration - a single unbroken "
    "charcoal line on warm ivory, in the manner of an editorial illustration "
    "at the head of a long magazine piece.\n\n"
    "You decide what the illustration is OF. You never draw it, and you never "
    "describe how it is drawn: the line weight, the colours and the drawing "
    "technique are fixed and are not yours to choose.\n\n"
    "Three things matter.\n\n"
    "1. DRAWABLE AS ONE LINE. Choose forms with clear silhouettes that connect "
    "to each other. A skyline, a wave, a chip, a bridge, a hand, a horizon - "
    "yes. A crowd, a page of text, a chart, a logo - no.\n\n"
    "2. AN IDEA, NOT A LABEL. The picture should carry the episode's thought, "
    "not illustrate its title. For an episode about a company's data-centre "
    "spending, chip circuitry opening out into a city grid says something; a "
    "picture of a building does not.\n\n"
    "3. NEVER DEPICT AN EVENT OR AN OUTCOME. You are working before the facts "
    "are settled, and a picture that shows a result can be wrong in a way "
    "nobody can correct later. Draw the subject and the idea, never the "
    "scoreline, the number, the winner or the verdict. No text of any kind.\n\n"
    "Answer with the JSON object you are asked for and nothing else."
)

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string",
                    "description": "The idea being illustrated, resolved, in a few words."},
        "primary_form": {"type": "string",
                         "description": "The concrete, drawable main object on the page."},
        "visual_metaphor": {"type": "string",
                            "description": "What the line does with that form - one sentence."},
        "composition": {"type": "string",
                        "description": "How it sits on the square, including the negative space."},
        "tone": {"type": "string", "description": "Two or three adjectives."},
        "complexity": {"type": "string", "enum": list(COMPLEXITIES)},
        "avoid": {"type": "array", "items": {"type": "string"},
                  "description": "Things to keep out of THIS image in particular."},
    },
    "required": ["subject", "primary_form", "visual_metaphor", "composition",
                 "tone", "complexity"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# What the director is given
# --------------------------------------------------------------------------
#: How much of the evidence packet to show. The director is choosing a shape,
#: not learning the story, and a whole packet in front of a small model call is
#: seconds bought for nothing.
EVIDENCE_CHARS = 1400


def build_director_prompt(query: str, *, context: str = "", brief=None,
                          evidence: str = "", topic: str = "",
                          surface: str = "search") -> str:
    """Everything the director knows, in the order of its authority.

    The ordering is the surface's priority list made concrete: whatever is
    richest goes last, because the last thing said is the thing a model leans
    on hardest.
    """
    parts = [f"THE EPISODE\nSomeone asked FAM: {query!r}"]
    if context:
        parts.append(f"It is a follow-up to an episode about: {context}")
    if topic:
        parts.append(f"FAM recommended it to them as: {topic}")
    if brief is not None:
        known = []
        for label, value in (
            ("what they are asking for", getattr(brief, "intent", "")),
            ("the subject, resolved", getattr(brief, "subject", "")),
            ("why it may be being asked today", getattr(brief, "why_now", "")),
            ("the shape of the story", getattr(brief, "structure", "")),
        ):
            value = str(value or "").strip()
            if value:
                known.append(f"- {label}: {value}")
        if known:
            parts.append("WHAT FAM WORKED OUT ABOUT IT\n" + "\n".join(known))
    if evidence:
        parts.append("WHAT THE RESEARCH FOUND (for the idea only - do not "
                     "illustrate any specific event, number or outcome in it)\n"
                     + evidence[:EVIDENCE_CHARS])
    parts.append(
        "Decide what the one continuous-line illustration for this episode "
        "should be of. Remember it must be drawable without lifting the pen, "
        "and it must carry the idea rather than label the topic."
    )
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# The floor
# --------------------------------------------------------------------------
_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "at", "is",
    "are", "was", "were", "what", "why", "how", "who", "when", "where", "did",
    "does", "do", "about", "me", "my", "explain", "tell", "latest", "news",
    "update", "today", "this", "that", "with", "from", "into", "over",
}


def _keywords(text: str, limit: int = 6) -> list[str]:
    words = [w for w in re.findall(r"[A-Za-z0-9']+", text or "")
             if w.lower() not in _STOPWORDS and len(w) > 2]
    seen, out = set(), []
    for word in words:
        low = word.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(word)
        if len(out) >= limit:
            break
    return out


def fallback_visual_brief(query: str, reason: str, *, source: str = "") -> VisualBrief:
    """The brief FAM uses when the director could not produce one.

    Deliberately still a *drawable* brief rather than nothing: the subject is
    the question's own content words, the form is left to the image model, and
    the style block does the rest. An episode still gets an illustration, and
    it is exactly as art-directed as it was before this module existed - which
    is the floor this layer must never drop below.

    Note what it does **not** do: it does not refuse. A director outage must
    cost picture quality and never the picture.
    """
    words = _keywords(query)
    subject = " ".join(words) or (query or "").strip()
    log.warning("visual director degraded (%s); directing from the query alone",
                reason)
    return VisualBrief(
        query=query,
        subject=subject,
        primary_form="",
        visual_metaphor=f"the idea of {subject}, drawn as one continuous line"
                        if subject else "",
        degraded=True,
        notes=[reason],
        source=source,
    )


# --------------------------------------------------------------------------
# The call
# --------------------------------------------------------------------------
async def direct(query: str, *, context: str = "", brief=None,
                 evidence: str = "", topic: str = "", surface: str = "search",
                 notes=None) -> VisualBrief:
    """Decide what this episode's illustration is of. Never raises.

    `brief` is an `episode_intelligence.Brief` when one exists - on search it
    almost always does, because the visual job waits for the same understanding
    the writer is given. `evidence` is the research packet's context block.
    Both are optional and both are *free* here: they were paid for by the
    episode, and reading them costs nothing.

    `notes` is the episode's `ScriptNotes` when this runs on a live episode, so
    the director's tokens land in the same usage total as the writing call.
    A visual that cost a model call and reported nothing would under-report
    exactly the thing this feature adds to the bill.
    """
    query = (query or "").strip()
    source = surface or "search"
    if not query:
        return fallback_visual_brief(query, "the query is empty", source=source)
    if not settings.visual_director:
        return fallback_visual_brief(query, "VISUAL_DIRECTOR=0", source=source)

    prompt = build_director_prompt(query, context=context, brief=brief,
                                   evidence=evidence, topic=topic,
                                   surface=surface)
    try:
        client = build_async_client(credentials.active("ANTHROPIC_API_KEY"))
        response = await asyncio.wait_for(
            client.messages.create(
                model=settings.visual_director_model,
                max_tokens=settings.visual_director_max_tokens,
                system=DIRECTOR_SYSTEM,
                output_config={
                    "effort": settings.visual_director_effort,
                    "format": {"type": "json_schema", "schema": BRIEF_SCHEMA},
                },
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=settings.visual_director_timeout_seconds,
        )
    except asyncio.TimeoutError:
        return fallback_visual_brief(
            query,
            f"the director did not answer within "
            f"{settings.visual_director_timeout_seconds}s",
            source=source)
    except Exception as exc:  # noqa: BLE001 - availability outranks diagnosis
        return fallback_visual_brief(query, f"the director call failed: {exc}",
                                     source=source)

    if notes is not None:
        try:
            notes.usage.add_model_call(settings.visual_director_model,
                                       getattr(response, "usage", None))
        except Exception:  # noqa: BLE001 - a ledger miss must not lose the art
            log.debug("could not record director usage", exc_info=True)

    if getattr(response, "stop_reason", "") == "refusal":
        return fallback_visual_brief(query, "the director declined the request",
                                     source=source)
    try:
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
    except (StopIteration, AttributeError, ValueError, TypeError) as exc:
        return fallback_visual_brief(
            query, f"the director returned nothing readable: {exc}", source=source)

    result = from_payload(data, query=query, source=source)
    log.info("visual direction %r -> form=%r metaphor=%r complexity=%s",
             query, result.primary_form, result.visual_metaphor,
             result.complexity)
    return result


def from_payload(data: dict, *, query: str = "", source: str = "") -> VisualBrief:
    """A model payload, made safe.

    Separate from `direct` so the gate is testable without a key, and because
    every value here has a way of being wrong that matters: a complexity
    outside the three bands reaches `visual_style` as an unknown key, and an
    `avoid` list that *replaces* the baseline is how "no text" stops being said
    at all.
    """
    complexity = str(data.get("complexity", "") or "").strip().lower()
    if complexity not in COMPLEXITIES:
        complexity = "medium"
    avoid = [str(item).strip() for item in (data.get("avoid") or [])
             if str(item).strip()]
    # Added to, never substituted for. The baseline is what keeps text and
    # logos out of every FAM image regardless of what the model thought to say.
    merged = list(BASELINE_AVOID) + [a for a in avoid
                                     if a.lower() not in BASELINE_AVOID]
    return VisualBrief(
        query=query,
        subject=str(data.get("subject", "") or "").strip(),
        primary_form=str(data.get("primary_form", "") or "").strip(),
        visual_metaphor=str(data.get("visual_metaphor", "") or "").strip(),
        composition=str(data.get("composition", "") or "").strip()
        or "centred subject with generous negative space",
        tone=str(data.get("tone", "") or "").strip()
        or "intelligent, elegant, restrained",
        complexity=complexity,
        avoid=merged,
        source=source,
    )


def report() -> dict:
    """What `/api/health` says about the director."""
    return {
        "enabled": settings.visual_director,
        "model": settings.visual_director_model,
        "timeout_seconds": settings.visual_director_timeout_seconds,
        "note": "" if settings.visual_director else
                "VISUAL_DIRECTOR=0: illustrations are being directed from the "
                "raw query, with no understanding of the episode behind them",
    }
