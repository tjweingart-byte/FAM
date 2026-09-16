"""Understand the request before researching it.

FAM EI is the layer between what someone typed and what the pipeline goes and
looks up. It answers the questions the blueprint asks - what is being asked for,
why it might be being asked now, what the retrieval must actually find, what
shape the story takes, and how deep the chosen duration should go - and it does
all of that **before** Exa is called, so that the one search FAM pays for is a
search for the right thing.

Why it sits here and not after retrieval
----------------------------------------
A gate downstream of Exa can only judge an episode built on whatever the packet
happened to contain. If the query that produced the packet was wrong - "Nvidia"
searched literally when what the listener wanted was yesterday's results - then
by the time anything is checked the evidence is already the wrong evidence and
no amount of critique recovers it. So the gate is here: between the typed words
and the search.

What that buys, and what it cannot
----------------------------------
It can classify the request, resolve the subject, decide how fresh the evidence
has to be, and turn a two-word query into a retrieval that finds something. It
**cannot verify a fact**, because nothing has been retrieved yet and the model's
own knowledge is months old. So it never asserts what happened - it states what
must be *established*, and hands the writing path cautions instead of claims.
Event status is settled downstream, from dated evidence (`research.build_packet`).

The cost of being here
----------------------
One model call in front of the first word, on the search path. That is a
deliberate, recorded trade: it breaks CLAUDE.md's one-sentence spec ("within
about a second audio starts"), and it was chosen anyway because the writing
quality is the product and a fast wrong episode is worth less than a slower
right one. The browse surfaces do not pay it at all - there the brief is built
before the tap, which is the same trick CLAUDE.md has always recommended and
the reason prefetch exists.

Nothing here may prevent an episode. Every failure - a malformed reply, a
missing key, a timeout, a gate that trips - falls back to a brief built from the
raw query and says so in the log and in `Brief.degraded`. A layer that adds
quality must not be able to subtract availability.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import credentials
import metering
from anthropic_client import build_async_client
from config import settings

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# The vocabulary
# --------------------------------------------------------------------------
#: What job the listener is asking FAM to do. A closed set on purpose: an open
#: one gives a different label every run and nothing downstream can switch on it.
INTENTS = (
    # "recap" says what the *listener* wants, never what the world has done.
    # It used to read "something finished; tell me what happened", and that was
    # a claim - made by the one layer in FAM that is forbidden to make claims,
    # about the one thing it cannot possibly know. Nothing has been retrieved
    # when this is chosen, so whether the thing has finished is unknown here by
    # construction; the evidence settles it downstream. See PROBLEMS.md §88.
    "recap",       # tell me what happened, if it has happened
    "preview",     # something is coming; tell me what to expect
    "causal",      # why did X do Y
    "update",      # where does X stand now
    "explainer",   # how does X work
    "comparison",  # X versus Y
    "teaching",    # teach me to do or think about X
    "discovery",   # tell me something about X, unprompted
)

#: The default shape of each kind of story.
#:
#: **These are shapes, not templates, and the difference is the whole lesson of
#: PROBLEMS.md's prompt rewrite.** The old prompt imposed the same five beats on
#: every topic, so a golf recap had to invent "the main debate or open question"
#: to fill a slot - the prompt was *requesting* invention. A structure named
#: here is what this kind of story usually looks like when the material supports
#: it, and `build_structure_note` says plainly that a beat with nothing real
#: behind it is dropped rather than filled.
STRUCTURES = {
    "breaking": (
        "what happened, then what is actually known, then what is not yet "
        "known, then why it matters, then what happens next"
    ),
    "business": (
        "what was expected, then what the number actually was, then the "
        "surprise in the gap, then how the market took it, then how management "
        "explained it, then what it implies"
    ),
    "sports_recap": (
        "what was at stake, then the turns the game actually hinged on, then "
        "the result, then who decided it, then what it changes"
    ),
    "sports_preview": (
        "what is at stake, then where both sides currently stand, then the "
        "matchup that decides it, then what to watch for, then when it happens"
    ),
    #: The shape the vocabulary had no word for, and whose absence produced
    #: PROBLEMS.md §88. Between the start of an event and the existence of a
    #: report about it, every other shape here demands an outcome - and a shape
    #: that demands one is a shape that will be given one, invented, because
    #: dropping the beat leaves nothing. This is the episode that is genuinely
    #: available in that window, and it is a real episode rather than a
    #: consolation: what is at stake and what has happened so far is most of
    #: what a listener asking mid-event actually wants.
    "in_progress": (
        "what is at stake, then how it stands right now, then what has already "
        "been settled and what that has changed, then what is still open - and "
        "it ends there, because it has not finished"
    ),
    "explainer": (
        "the intuition first, then the mechanism underneath it, then one "
        "concrete case of it working, then what follows from it, then the way "
        "to think about it afterwards"
    ),
    "historical": (
        "the setup, then the catalyst, then the turn, then the consequence, "
        "then what it left behind"
    ),
    "trend": (
        "what set it off, then how much attention it is actually getting, then "
        "the competing explanations, then which part genuinely matters"
    ),
    "general": "",  # no imposed shape; the house voice carries it
}

#: The shapes EI is allowed to *pick*, which is not all of them.
#:
#: `in_progress` is deliberately absent. Choosing it would be EI asserting that
#: the event is under way - the same claim about the world that `recap` used to
#: smuggle in, in the opposite direction, and equally unknowable from a layer
#: that has retrieved nothing. It is reached only downstream, by the writer,
#: from evidence: `build_structure_note` names it as the alternative when the
#: outcome a shape needs is not in the packet. See PROBLEMS.md §88.
PICKABLE_STRUCTURES = tuple(s for s in STRUCTURES if s != "in_progress")

#: Which structure an intent reaches for when the model does not name one.
INTENT_STRUCTURE = {
    "recap": "breaking",
    "preview": "sports_preview",
    "causal": "breaking",
    "update": "breaking",
    "explainer": "explainer",
    "comparison": "explainer",
    "teaching": "explainer",
    "discovery": "trend",
}

#: What more time is allowed to buy. The blueprint's point, and the one this
#: codebase most needed: duration is an *editorial* constraint, not a word
#: count. `plan_episode` already caps the words; this says what the extra
#: minutes should be spent on, so that a longer episode is deeper rather than
#: slower. Paired with CLAUDE.md's settled rule that duration is a ceiling: a
#: band describes what the time *may* hold, never what must be produced.
DEPTH_BANDS = (
    (2, "orientation", (
        "the core of it, two or three facts that are genuinely essential, and "
        "one reason it matters. Nothing else fits, so choose hard rather than "
        "compressing everything"
    )),
    (4, "understanding", (
        "the essential facts, the causal explanation that makes them hang "
        "together, the context needed to place them, and what follows next"
    )),
    (7, "depth", (
        "the background that explains how it got this way, the factors pulling "
        "against each other, the nuance that a shorter version has to flatten, "
        "and the implications"
    )),
    (99, "full", (
        "the whole arc - origins, the competing forces, the turns it took, "
        "what is genuinely unsettled about it, and where it leaves things"
    )),
)


def depth_for(minutes: int) -> tuple[str, str]:
    """The editorial goal and content mix for a duration."""
    for ceiling, name, mix in DEPTH_BANDS:
        if minutes <= ceiling:
            return name, mix
    return DEPTH_BANDS[-1][1], DEPTH_BANDS[-1][2]


# --------------------------------------------------------------------------
# The brief
# --------------------------------------------------------------------------
@dataclass
class Brief:
    """What FAM worked out about the request before it went looking.

    Two consumers, deliberately: `research` reads `search_query`,
    `recency_days` and `must_establish` to retrieve well, and
    `script_generator` reads the rest to write well. One call pays for both,
    which is what keeps this to a single extra model call per episode.
    """

    query: str = ""
    #: What the listener is actually asking FAM to do. One of INTENTS.
    intent: str = "explainer"
    #: The entity or event, resolved. "Nvidia" stays "Nvidia"; "the fed" becomes
    #: "the US Federal Reserve" so retrieval and writing agree on the subject.
    subject: str = ""
    #: Why this may be being asked today. A **hypothesis**, never a claim - see
    #: the module docstring. Empty when nothing plausible suggests itself.
    why_now: str = ""
    #: high | medium | low. Governs how hard the episode may lean on `why_now`.
    #: `low` means treat the request as evergreen and do not invent a trigger.
    why_now_confidence: str = "low"
    #: What actually goes to Exa. The single highest-value field here.
    search_query: str = ""
    #: What the evidence has to contain for this episode to be worth making.
    #: Read by the sufficiency check, which retries once when the packet
    #: mentions none of it.
    must_establish: list = field(default_factory=list)
    #: How old evidence may be before it stops answering the question. 0 means
    #: the question is evergreen and no recency window should be applied.
    recency_days: int = 0
    #: Which shape this story takes. A key of STRUCTURES.
    structure: str = "general"
    #: What the writing path must be careful about - almost always temporal.
    #: "the match is on Sunday and has not been played" is a caution; it stops
    #: the writer describing a result that cannot exist yet.
    cautions: list = field(default_factory=list)
    #: Which live-fact domain this belongs to, if any - see `live_facts`. An
    #: article index lags a scoreboard, and this is what routes around it.
    live_domain: str = ""
    #: True when what the listener wants *is* a result - a final score, a
    #: winner, a verdict, a closing price - which does not exist until the thing
    #: it comes from concludes.
    #:
    #: **This is a property of the question, never of the world**, which is what
    #: makes it askable here. EI cannot know whether the game has finished; it
    #: can know perfectly well that "who won" has no answer until one has. The
    #: distinction is the whole of PROBLEMS.md §88: the old `recap` label
    #: answered the second question while pretending to answer the first, and
    #: every stage downstream then took a finished event as given.
    outcome_dependent: bool = False
    #: True when the model call did not happen or could not be used, and this
    #: brief was assembled from the raw query. Reported, never hidden: an EI
    #: layer that silently degrades is the "quietly worse than intended"
    #: failure this project has lost the most time to.
    degraded: bool = False
    #: Why it degraded, or why the gate intervened. For the log and /api/health.
    notes: list = field(default_factory=list)

    @property
    def retrieval(self) -> str:
        """The string to actually search for. Never empty."""
        return (self.search_query or self.query).strip()

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def fallback_brief(query: str, reason: str) -> Brief:
    """The brief FAM uses when EI could not produce one.

    Deliberately the *current* behaviour: search the raw query, impose no
    structure, claim no why-now. An episode still gets made, and it is exactly
    as good as it was before this module existed - which is the floor this
    layer must never drop below.
    """
    log.warning("episode intelligence degraded (%s); using the raw query", reason)
    return Brief(query=query, subject=query.strip(), search_query=query.strip(),
                 degraded=True, notes=[reason])


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------
#: Words that carry no subject, so agreement on them proves nothing. Used only
#: by the drift check below.
_STOPWORDS = {
    "a", "about", "after", "all", "an", "and", "any", "are", "as", "at", "be",
    "been", "before", "but", "by", "can", "did", "do", "does", "for", "from",
    "get", "had", "has", "have", "how", "i", "if", "in", "is", "it", "its",
    "just", "last", "me", "most", "my", "new", "news", "not", "now", "of", "on",
    "one", "or", "our", "out", "over", "should", "so", "some", "tell", "than",
    "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "to", "today", "up", "us", "was", "we", "were", "what", "when", "where",
    "which", "who", "why", "will", "with", "would", "you", "your",
}

_WORD = re.compile(r"[a-z0-9']+")


def _content_words(text: str) -> set:
    return {w for w in _WORD.findall((text or "").lower())
            if w not in _STOPWORDS and len(w) > 2}


def _shares_subject(asked: set, searching: set) -> bool:
    """Do these two look like they are about the same thing?

    Prefix matching in both directions, not equality, and that is the whole
    substance of it. Equality fails on exactly the transformation this layer
    exists to perform: "the fed" becomes "US Federal Reserve rate decision" and
    shares not one token with what was typed, so an equality test would revert
    the single most valuable rewrite EI makes. `fed` is a prefix of `federal`,
    and that is enough to tell resolution apart from replacement.

    Loose on purpose. A false "shares" costs nothing here - the packet comes
    back without what `must_establish` asked for and the retry fires - while a
    false "does not" throws away a good search every time.
    """
    for a in asked:
        for b in searching:
            if a == b:
                return True
            shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
            if len(shorter) >= 3 and longer.startswith(shorter):
                return True
    return False


def gate(brief: Brief, query: str) -> Brief:
    """Screen the brief before anything is retrieved.

    This is the checkpoint the packet asks for, placed where it can still
    change the outcome. It repairs what it can and records what it repaired;
    it refuses nothing, because refusing would mean no episode.

    The checks, and why each exists:

    * **The search query must still be about the query.** A model asked to
      improve a search occasionally replaces it, and every stage after this one
      would then be correct about the wrong subject. Requiring one shared
      content word - by prefix, not by equality, so that resolving a subject
      is not mistaken for replacing one - catches a wholesale replacement. A query of pure stopwords
      ("what is it") has nothing to share, so it is exempt rather than
      rewritten - that is the case improvement helps most.

      **It deliberately does not catch semantic drift**, and cannot: the same
      test that would reject "apple varieties" becoming "Apple Inc guidance"
      also rejects "the fed" becoming "US Federal Reserve rate decision", which
      is the single most valuable thing this layer does. Resolving a subject
      and replacing it look identical to a token comparison. That failure is
      caught downstream instead, where there is evidence to catch it with: a
      search for the wrong subject comes back not containing what
      `must_establish` asked for, and `research.retrieve` spends its one retry
      on `subject` rather than on the drifted query.
    * **The vocabulary must be closed.** An intent or structure outside the set
      cannot be switched on downstream, so an unknown one is mapped back rather
      than passed through as a string nobody handles.
    * **A why-now must carry its confidence honestly.** `high` confidence with
      no hypothesis behind it is the blueprint's "do not infer too much"
      failure, and it is the one that puts an invented trigger in front of an
      episode. Downgraded rather than trusted.
    * **A request about a specific moment must be researched against a
      window.** An episode asking what happened does not want an article from
      eighteen months ago ranked first because it is well linked.
    * **A question whose answer is a result must say so, and must carry the
      caution that nothing is confirmed yet.** Unconditionally - see the
      comment on it below, which is the bug that made PROBLEMS.md §88 possible.
    """
    problems: list = []

    if brief.intent not in INTENTS:
        problems.append(f"intent {brief.intent!r} is not one of {', '.join(INTENTS)}")
        brief.intent = "explainer"

    if brief.structure not in PICKABLE_STRUCTURES:
        problems.append(f"structure {brief.structure!r} is not one EI may pick")
        brief.structure = INTENT_STRUCTURE.get(brief.intent, "general")

    if brief.why_now_confidence not in ("high", "medium", "low"):
        problems.append(f"why-now confidence {brief.why_now_confidence!r} is not a level")
        brief.why_now_confidence = "low"

    if not brief.why_now.strip() and brief.why_now_confidence != "low":
        problems.append("why-now confidence claimed without a hypothesis behind it")
        brief.why_now_confidence = "low"

    asked = _content_words(query)
    searching = _content_words(brief.search_query)
    if not brief.search_query.strip():
        problems.append("no search query was produced")
        brief.search_query = query.strip()
    elif asked and not _shares_subject(asked, searching):
        problems.append(
            f"the search query {brief.search_query!r} shares no subject with "
            f"the question; searching what was asked instead")
        brief.search_query = query.strip()

    try:
        brief.recency_days = max(0, int(brief.recency_days))
    except (TypeError, ValueError):
        problems.append("recency window was not a number")
        brief.recency_days = 0

    # A recap or an update is a question about a moment. Retrieved without a
    # window it competes against every well-ranked article ever written on the
    # subject, which is how a two-year-old explainer ends up being the evidence
    # for "what happened last night".
    if brief.intent in ("recap", "update", "preview", "causal") and not brief.recency_days:
        problems.append(
            f"a {brief.intent} with no recency window; defaulting to "
            f"{settings.ei_default_recency_days} days")
        brief.recency_days = settings.ei_default_recency_days

    # A question whose answer is a result is outcome-dependent whether or not
    # the model said so. Both of these are asking for something that does not
    # exist until the thing concludes, and a live domain is the case where the
    # gap between concluding and being reported is longest.
    brief.outcome_dependent = (bool(brief.outcome_dependent)
                               or brief.intent in ("recap", "update")
                               or bool(brief.live_domain))

    # Nothing has been retrieved, so the result of anything recent is unknown
    # here by construction. Saying so is what stops the writer inventing one.
    #
    # **Unconditionally, and first in the list.** It used to be added only
    # `if not brief.cautions` - so the single most important caution in FAM was
    # suppressed by the presence of any other caution at all, which is to say
    # it was suppressed on most recaps, because a model asked for cautions
    # produces some. That is PROBLEMS.md §88's smallest and most expensive bug:
    # a guard that looks present in the source, passes a test written with an
    # empty list, and is absent in production. Prepending also puts it out of
    # reach of the `[:6]` truncation below, which could otherwise drop it.
    if brief.outcome_dependent:
        mandatory = (
            "nothing has been confirmed yet, and an event that has started is "
            "not an event that has finished: state a result only if the "
            "evidence actually reports it as final")
        if not any("confirmed yet" in c for c in brief.cautions):
            brief.cautions.insert(0, mandatory)

    brief.must_establish = [str(q).strip() for q in (brief.must_establish or [])
                            if str(q).strip()][:6]
    brief.cautions = [str(c).strip() for c in (brief.cautions or []) if str(c).strip()][:6]
    if not brief.subject.strip():
        brief.subject = query.strip()

    if problems:
        log.info("EI gate repaired %d thing(s) for %r: %s",
                 len(problems), query, "; ".join(problems))
        brief.notes.extend(problems)
    return brief


# --------------------------------------------------------------------------
# The model call
# --------------------------------------------------------------------------
BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "subject": {"type": "string"},
        "why_now": {"type": "string"},
        "why_now_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "search_query": {"type": "string"},
        "must_establish": {"type": "array", "items": {"type": "string"}},
        "recency_days": {"type": "integer"},
        "structure": {"type": "string", "enum": list(PICKABLE_STRUCTURES)},
        "cautions": {"type": "array", "items": {"type": "string"}},
        "live_domain": {"type": "string", "enum": ["", "sports", "markets"]},
        "outcome_dependent": {"type": "boolean"},
    },
    "required": ["intent", "subject", "why_now", "why_now_confidence",
                 "search_query", "must_establish", "recency_days", "structure",
                 "cautions", "live_domain", "outcome_dependent"],
    "additionalProperties": False,
}

EI_SYSTEM = """You are the editorial intelligence in front of a spoken-briefing \
app. You do not write the episode. You work out what the request actually is, \
and what one web search has to find for the episode to be worth hearing.

You have not looked anything up and your own knowledge is months out of date. \
So you never state what happened. You state what must be established, and you \
flag what the writer must not assume.

The search query is the most important thing you produce. It is sent verbatim \
to a neural search engine over news and web content. Write what a good \
researcher would type - the resolved subject, the specific event or figure \
wanted, and the words that would appear in a report of it. Not a question, not \
a sentence, and never just the listener's words repeated back.

Judge the "why now" honestly. If a dominant recent event plausibly explains the \
question, say so and mark it high. If several might, say the most likely one \
and mark it medium. If nothing does, leave it empty and mark it low - an \
invented reason for asking is worse than none, because the episode will be \
built around it.

You do not know whether anything has happened, finished, or even started. That \
is not something to work around - it is the one thing you are certain of. Ask \
instead whether the answer they want is a *result*, which is a question about \
their request and not about the world, and say so. Then write cautions that \
hold whichever way it turns out."""


def build_ei_prompt(query: str, minutes: int, context: str = "",
                    now: str = "") -> str:
    depth_name, depth_mix = depth_for(minutes)
    follow_up = ""
    if context:
        follow_up = (
            f"\nThis is a follow-up. They have already heard a briefing on: "
            f"{context}\nThe search must find what is *new* relative to that, "
            "not the same ground again.\n")
    return f"""Someone asked a briefing app this:

<request>{query}</request>

It is currently {now}.
{follow_up}
The episode will run about {minutes} minute{"s" if minutes != 1 else ""}, which
is room for {depth_name}: {depth_mix}. Let that shape how much the search needs
to find - a short episode needs the core established well, a long one needs the
surrounding factors too.

Work out:

- **intent** - what job is being asked for.
- **subject** - the entity or event, resolved and unambiguous. Expand what is
  abbreviated or implied so that retrieval and writing agree on what this is
  about. Keep the listener's own sense of it; do not substitute a different
  subject that happens to be more newsworthy.
- **why_now** - what recent development plausibly put this in their head today,
  if any, and **why_now_confidence** for how sure that is. Nothing plausible
  means empty and low. Do not claim to know their motive.
- **search_query** - what to actually search for. See the rules above.
- **must_establish** - the two to four things the evidence has to contain for
  this episode to work. Short noun phrases, not sentences. These are checked
  against what comes back, so make them things a report would actually say.
- **recency_days** - how recent evidence must be to answer this. A question
  about a specific recent event is days; a question about how something works
  is 0, meaning age does not matter.
- **structure** - the shape this story takes. Pick the one that fits the
  material, not the one that sounds most dramatic; `general` if none fits.
- **cautions** - what the writer must not assume, especially about time. If
  something may not have happened yet, or may have happened days rather than
  hours ago, say so here. This is where a wrong tense gets caught.
- **live_domain** - `sports` if this turns on a score, fixture or standing;
  `markets` if it turns on a price, index or rate; empty otherwise.
- **outcome_dependent** - true if what they want is a result that only exists
  once something concludes: a final score, a winner, a verdict, a closing
  price, a vote count. This is about their question, not about the world - you
  have no idea whether the thing has finished, and "who won" is
  outcome-dependent whether it finished an hour ago or is still going. False
  for how something works, what someone is like, or what is at stake."""


async def understand(query: str, minutes: int = 3, context: str = "",
                     notes=None, now: str | None = None) -> Brief:
    """Work out what this request is, before anything is retrieved.

    Never raises. Every failure path returns `fallback_brief`, which searches
    the raw query - the behaviour this layer replaced - so an EI outage costs
    quality and never availability.

    `notes` is the episode's `ScriptNotes`, when there is one: this call is a
    real model call on the generation path and must land in the same usage
    total as the writing call, or a researched episode reports at less than it
    cost.
    """
    query = (query or "").strip()
    if not query:
        return fallback_brief(query, "the query is empty")
    if not settings.episode_intelligence:
        return fallback_brief(query, "EPISODE_INTELLIGENCE=0")

    now = now or datetime.now(timezone.utc).astimezone().strftime(
        "%A %d %B %Y at %H:%M %Z").replace(" 0", " ")

    try:
        client = build_async_client(credentials.active("ANTHROPIC_API_KEY"))
        response = await asyncio.wait_for(
            client.messages.create(
                model=settings.ei_model,
                max_tokens=settings.ei_max_tokens,
                system=EI_SYSTEM,
                output_config={
                    "effort": settings.ei_effort,
                    "format": {"type": "json_schema", "schema": BRIEF_SCHEMA},
                },
                messages=[{
                    "role": "user",
                    "content": build_ei_prompt(query, minutes, context, now),
                }],
            ),
            timeout=settings.ei_timeout_seconds,
        )
    except asyncio.TimeoutError:
        return fallback_brief(
            query, f"EI did not answer within {settings.ei_timeout_seconds}s")
    except Exception as exc:  # noqa: BLE001 - availability outranks diagnosis here
        return fallback_brief(query, f"EI call failed: {exc}")

    if notes is not None:
        notes.usage.add_model_call(settings.ei_model, getattr(response, "usage", None))

    # `output_config.format` guarantees valid JSON in the first text block -
    # but a refusal stops before any of that, and a refusal parsed as a brief
    # would be a silent degradation rather than a visible one.
    if getattr(response, "stop_reason", "") == "refusal":
        return fallback_brief(query, "EI declined the request")
    try:
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
    except (StopIteration, AttributeError, ValueError, TypeError) as exc:
        return fallback_brief(query, f"EI returned nothing readable: {exc}")

    brief = Brief(
        query=query,
        intent=str(data.get("intent", "")),
        subject=str(data.get("subject", "")),
        why_now=str(data.get("why_now", "")),
        why_now_confidence=str(data.get("why_now_confidence", "")),
        search_query=str(data.get("search_query", "")),
        must_establish=list(data.get("must_establish") or []),
        recency_days=data.get("recency_days", 0),
        structure=str(data.get("structure", "")),
        cautions=list(data.get("cautions") or []),
        live_domain=str(data.get("live_domain", "")),
        outcome_dependent=bool(data.get("outcome_dependent", False)),
    )
    brief = gate(brief, query)
    log.info("EI %r -> intent=%s structure=%s recency=%dd outcome=%s "
             "why_now=%s(%s) search=%r", query, brief.intent, brief.structure,
             brief.recency_days, "pending" if brief.outcome_dependent else "n/a",
             brief.why_now or "-", brief.why_now_confidence, brief.retrieval)
    return brief


# --------------------------------------------------------------------------
# What the writer is told
# --------------------------------------------------------------------------
def build_structure_note(brief: Brief) -> str:
    """The story shape, phrased so it cannot become a template to fill.

    The wording is the whole of it. PROBLEMS.md records that the previous
    prompt's fixed five beats made the model invent material for beats the
    topic had nothing for - so this says, in the same breath as naming the
    shape, that a beat with nothing behind it is dropped.
    """
    shape = STRUCTURES.get(brief.structure, "")
    if not shape:
        return ""
    note = (
        f"\nA {brief.structure.replace('_', ' ')} story usually goes: {shape}.\n"
        "That is the shape it takes when the material supports it, not a set of "
        "boxes. Drop any part you have nothing real for and spend the time on "
        "the parts you do - a beat filled with something invented to occupy it "
        "is the worst thing in the episode.\n"
    )
    # The one beat that cannot simply be dropped, because dropping it leaves
    # the shape with nothing in it - which is why "drop what you have nothing
    # for" was not enough on its own, and why the alternative has to be named
    # rather than left to be worked out. See PROBLEMS.md §88.
    if brief.outcome_dependent and brief.structure != "in_progress":
        note += (
            "\nThat shape turns on an outcome, and you do not yet know there is "
            "one. If the evidence does not report the result as final, this is "
            "not the shape - write the in-progress one instead: "
            f"{STRUCTURES['in_progress']}. Do not keep the shape and fill the "
            "missing beat; that is the single failure this note exists to "
            "prevent.\n"
        )
    return note


def build_brief_block(brief: Brief, minutes: int) -> str:
    """Everything EI worked out, as the writer needs to read it.

    Empty when EI degraded: a fallback brief knows nothing the writer does not
    already know from the query, and printing its emptiness as though it were
    findings would be worse than silence.
    """
    if brief.degraded:
        return ""

    depth_name, depth_mix = depth_for(minutes)
    lines = [f"\nThis was worked out about the request before anything was "
             f"looked up.\n\nThey are asking for: {brief.intent}, about "
             f"{brief.subject}."]

    if brief.why_now and brief.why_now_confidence == "high":
        lines.append(
            f"What most likely put it in their head today: {brief.why_now}. "
            "Frame the episode around that - but it is a hypothesis about the "
            "news, not about them, so never tell them why they asked.")
    elif brief.why_now and brief.why_now_confidence == "medium":
        lines.append(
            f"Possibly relevant right now: {brief.why_now}. Worth covering if "
            "the evidence bears it out; do not build the episode on it and do "
            "not assert it as the reason for anything.")
    else:
        lines.append(
            "Nothing recent obviously prompted this, so treat it as a standing "
            "question rather than news. Do not manufacture a reason it is "
            "topical today.")

    if brief.must_establish:
        lines.append("The episode has to actually answer: "
                     + "; ".join(brief.must_establish) + ".")

    lines.append(
        f"At {minutes} minute{'s' if minutes != 1 else ''} the goal is "
        f"{depth_name}: {depth_mix}. More time buys more of that, never more "
        "words about the same thing.")

    # Said before the cautions, because it is the one that changes what the
    # episode *is* rather than how a sentence is worded.
    if brief.outcome_dependent:
        lines.append(
            "What they are asking for is a result, and a result only exists "
            "once the thing it comes from has finished. Nothing here knows "
            "whether it has - that is settled by the evidence you were given "
            "and by nothing else. So: find the outcome reported as an outcome, or "
            "write the episode about something that has not finished yet. "
            "Both are real episodes. Choosing between them by guessing is not.")

    if brief.cautions:
        lines.append("Be careful about this, and take the evidence over your "
                     "own recollection every time: "
                     + "; ".join(brief.cautions) + ".")

    return "\n".join(lines) + "\n" + build_structure_note(brief)


def report() -> dict:
    """What EI will do on this server right now - for /api/health.

    Reports configuration, not capability: like `research.diagnose` it does not
    spend a call to prove the credential works, and unlike a check that stops
    at "a key is set", it says which of the two states a deploy is in so that a
    silently degraded EI is visible from outside.
    """
    return {
        "enabled": bool(settings.episode_intelligence),
        "model": settings.ei_model,
        "effort": settings.ei_effort,
        "timeout_seconds": settings.ei_timeout_seconds,
        "default_recency_days": settings.ei_default_recency_days,
        "intents": list(INTENTS),
        "structures": [s for s in PICKABLE_STRUCTURES if s != "general"],
        # Reported because its absence is what produced §88, and a deploy
        # running a build without it looks identical from outside to one that
        # has it - right up until a listener asks about a game in progress.
        "outcome_status_checked": "in_progress" in STRUCTURES,
    }
