"""Understand the request before researching it.

FAM EI is the layer between what someone typed and what the pipeline goes and
looks up. It answers the questions the blueprint asks - what is being asked for,
why it might be being asked now, what the retrieval must actually find, what
shape the story takes, and how deep the chosen duration should go - and it does
all of that **before** the search is called, so that the one search FAM pays for is a
search for the right thing.

Why it sits here and not after retrieval
----------------------------------------
A gate downstream of the search can only judge an episode built on whatever the packet
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
import collections
import dataclasses
import difflib
import json
import logging
import re
import time
from dataclasses import dataclass, field

import credentials
import live_facts
import metering
import named_slots
import pipeline_errors
import pronunciation
from anthropic_client import build_async_client
from config import DEFAULT_MINUTES, settings

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
        "the answer, the mechanism or turn that explains it, the concrete "
        "particulars that make it stick, and why it matters. Choose one thread "
        "and give it the whole time rather than skimming several"
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
    #: What actually goes to the search. The single highest-value field here.
    search_query: str = ""
    #: The same search, broader, for when the first comes back with nothing.
    #: Written in the same call as `search_query` and costing a handful of
    #: output tokens, which is what makes it affordable: §82 ruled out a model
    #: call to rephrase a thin search, and this is not one. §109.
    search_fallback: str = ""
    #: Other wordings of the precise search, sent beside it in the same
    #: request (§260). Parallel takes several short keyword queries and asks
    #: for two or three; one query was one guess at the words a report would
    #: use. Never the broader search: these hunt the same thing, and
    #: `search_fallback` is still kept for when they all miss.
    search_angles: list = field(default_factory=list)
    #: What the search is for, in a sentence or two a search engine reads
    #: (§260): the thing wanted, the moment in dates, and which sources
    #: answer it. Parallel ranks and cuts its excerpts against this, so it is
    #: the retriever's half of the brief. Never a result and never the
    #: listener's words. Empty on a degraded brief, and
    #: `research.objective_for` then writes one from the fields above.
    search_objective: str = ""
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
    #: The bare name(s) a live provider would list the subject under -
    #: "Kansas City Chiefs", "NVIDIA", "Kansas City Chiefs, Buffalo Bills" -
    #: with no description, year or angle (§265). `subject` is written for
    #: retrieval and the writer ("the Chiefs' 2026 playoff chances"), and a
    #: provider's search matched nothing against that. Only ever what a
    #: provider's own catalogue is searched with; never an id.
    live_entity: str = ""
    #: The domain code chose to ask a provider on when `live_domain` was
    #: empty (`live_route`, §265), and why. Set in code, never by the model,
    #: and never read as `live_domain`: it asks a provider and changes
    #: nothing else about the episode.
    live_route: str = ""
    live_route_why: str = ""
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
    #: The town or city the request is about, as "Town, Region, Country",
    #: when what they want is what is happening *there* - its news, its
    #: events, its weather (§194). Empty for everything else, including a
    #: question that merely mentions a place ("the history of Paris"). Named
    #: as typed; where it is comes from a catalogue (`places`), never from
    #: here, and never from the listener's saved location.
    place: str = ""
    #: What the episode is called until the writer names it. Empty on a
    #: degraded brief, and the player then keeps the title derived from the
    #: question - which is what it showed before this existed (§127).
    title: str = ""
    #: What kind of thing the episode is about, in two to four words ("drone
    #: makers", "heavyweight boxing") - the writer's `<<CATEGORY:>>`, decided
    #: before the first word. The player's picture is chosen from it while the
    #: episode is still being written (`live_captions.publish_category`);
    #: the writer's own replaces it once the script is cached (§209). Empty
    #: on a degraded brief.
    category: str = ""
    #: The key of the named broadcast slot the request names
    #: (`named_slots.SLOTS`), set in code and never by the model: "Sunday
    #: Night Football" is one game, and a scoreboard picks it by kick-off.
    named_slot: str = ""
    #: Names in the request or its resolved subject that the voice is likely
    #: to get wrong, each `{"name", "say"}` with a plain-letters respelling
    #: (§165). Taken into `pronunciation`'s lexicon by
    #: `ScriptGenerator.understand`, which knows whether the episode is
    #: personal, and applied only to the text handed to the voice. Empty when there are none, and on a
    #: degraded brief.
    pronounce: list = field(default_factory=list)
    #: **The plan of the piece, from the question alone** (§270, the third
    #: part of a split brief). Each beat is `{"does", "needs"}`: what that
    #: part of the episode has to find out, and what the evidence must hold
    #: for it - never a fact, a figure or a result (`_outline_from` drops any
    #: line that carries a digit). Empty when the part did not answer in time.
    outline_beats: list = field(default_factory=list)
    #: What the first two sentences must situate ([situate]), as slots:
    #: `{"who", "what", "when"}`, each the *kind* of thing that goes there.
    #: The writer fills them from the evidence and still decides the opening
    #: last ([opening-last]).
    outline_opening: dict = field(default_factory=dict)
    #: What the last line has to land on - the kind of thing, never the thing.
    outline_ending: str = ""
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

    @property
    def broader(self) -> str:
        """What to search when `retrieval` came back with nothing.

        EI's own broader phrasing, then the resolved subject, then the raw
        query - in that order, because each is a wider net than the one
        before and the last two exist for a degraded brief, which has no
        fallback of its own. Never equal to `retrieval`: searching the same
        string twice is a second search that cannot find anything new.
        """
        for candidate in (self.search_fallback, self.subject, self.query):
            text = (candidate or "").strip()
            if text and text.lower() != self.retrieval.lower():
                return text
        return ""

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


#: The narrowest recency window FAM will actually apply, in days.
#:
#: The window is a *filter*: nothing published outside it is even considered.
#: A one-day window on a subject nothing was published about yesterday returns
#: zero results, and an empty search is the worst outcome retrieval has (§109).
#: Widening it costs almost nothing, because `rank_results` sorts newest-first
#: within a grade anyway - so a two-day window still answers "last night" with
#: last night's report when one exists, and answers it at all when one does
#: not. The packet carries every source's date and the writer computes the
#: relative phrase from it, so a two-day-old source is spoken of as two days
#: old rather than as last night.
#:
#: Zero is untouched and means evergreen: no window at all.
RECENCY_FLOOR_DAYS = 2

#: How many other wordings go beside the precise search (§260). Parallel's
#: own guidance is two or three queries in all.
MAX_ANGLES = 2
#: Parallel wants keyword queries of three to six words; a longer one is a
#: sentence, and is cut rather than sent.
MAX_ANGLE_CHARS = 80
#: The objective is a sentence or two. Longer is the model writing the
#: episode into the search.
MAX_OBJECTIVE_CHARS = 600


def _window(days) -> int:
    """The recency window to apply, with the floor above enforced."""
    try:
        asked = int(days or 0)
    except (TypeError, ValueError):
        return 0
    if asked <= 0:
        return 0
    return max(asked, RECENCY_FLOOR_DAYS)


def clean_title(title: str, query: str) -> str:
    """A brief's title, or "" when it is not one worth showing.

    Refused, not trimmed, when it is the question handed back: the whole point
    is that somebody who typed a question is not shown it again with capital
    letters on, and a title that only re-cases the query is exactly that.
    """
    text = " ".join(str(title or "").split()).strip(" .\"'")
    text = text.rstrip("?").strip()
    if not text:
        return ""
    def words(s: str) -> str:
        return " ".join(re.findall(r"[a-z0-9']+", s.lower()))
    if words(text) == words(query):
        return ""
    return text[:80]


#: How each brief since boot ended (§248): `ok`, or the reason it degraded to
#: the raw query - `out_of_room`, `timeout`, `failed`, `refused`,
#: `unreadable`. A degraded brief already says so per episode in the log; this
#: is the rate, which is the number that decides whether a model or a ceiling
#: is right, and it is in /api/health (`episode_intelligence.since_boot`).
_OUTCOMES: "collections.Counter[str]" = collections.Counter()


def fallback_brief(query: str, reason: str) -> Brief:
    """The brief FAM uses when EI could not produce one.

    Deliberately the *current* behaviour: search the raw query, impose no
    structure, claim no why-now. An episode still gets made, and it is exactly
    as good as it was before this module existed - which is the floor this
    layer must never drop below.
    """
    log.warning("episode intelligence degraded (%s); using the raw query", reason)
    return apply_named_slot(
        Brief(query=query, subject=query.strip(), search_query=query.strip(),
              degraded=True, notes=[reason]), query)


def apply_named_slot(brief: Brief, query: str) -> Brief:
    """Resolve a named broadcast slot in code, whatever the model made of it.

    "Sunday night football recap" once became an episode about an afternoon
    game, because the phrase was read as a description rather than as the
    name of one game (`named_slots`). A degraded brief gets this too: it has
    no model call to have got it right, and the search is the half it keeps.
    """
    slot = named_slots.find(query)
    if slot is None:
        return brief
    brief.named_slot = slot.key
    name = slot.name.lower()
    # The name goes into both searches verbatim, because it is what a report
    # of that game is headlined with - and a search without it matches every
    # game played that day.
    if name not in (brief.search_query or "").lower():
        brief.search_query = f"{slot.name} {slot.league} {brief.search_query}".strip()
    if brief.search_fallback and name not in brief.search_fallback.lower():
        brief.search_fallback = f"{slot.name} {brief.search_fallback}".strip()
    if name not in (brief.subject or "").lower():
        brief.subject = f"{slot.name} ({slot.definition})"
    brief.search_angles = [
        a if name in a.lower() else f"{slot.name} {a}"
        for a in (brief.search_angles or [])]
    if brief.search_objective and name not in brief.search_objective.lower():
        brief.search_objective = (f"{slot.name} is {slot.definition}. "
                                  f"{brief.search_objective}")
    caution = (
        f'"{slot.name}" is the name of one specific game: {slot.definition}. '
        f"The episode is about that game and no other - identify which teams "
        f"played in it from the evidence before saying anything, and never "
        f"substitute another game from the same day because it was reported "
        f"more prominently")
    if not any(slot.name in c for c in brief.cautions):
        # After the "nothing confirmed yet" caution when there is one, which
        # `gate` keeps first on purpose.
        at = 1 if brief.cautions and "confirmed yet" in brief.cautions[0] else 0
        brief.cautions.insert(at, caution)
        brief.cautions = brief.cautions[:6]
    wanted = f"which teams played in {slot.name} ({slot.when})"
    if not any(slot.name in m for m in brief.must_establish):
        brief.must_establish.insert(0, wanted)
        brief.must_establish = brief.must_establish[:6]
    # Once: a degraded brief that kept the search half's words passes here
    # twice, in `fallback_brief` and in `gate` (review, §268).
    if f"named slot: {slot.name}" not in brief.notes:
        brief.notes.append(f"named slot: {slot.name}")
    return brief


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
            if _respelled(a, b):
                return True
    return False


#: How alike two words must be (`difflib` ratio) for one to be the other
#: respelled. 0.8 takes "automics"/"atomics" (0.93), "ohtanni"/"ohtani"
#: (0.92) and "nvidea"/"nvidia" (0.83), and leaves "eagles"/"giants" (0.33)
#: apart; "apple"/"ample" (0.8) is under `RESPELL_MIN_LETTERS`. Loose, like
#: the prefix test it sits beside: a false "shares" costs nothing here.
RESPELL_RATIO = 0.8
RESPELL_MIN_LETTERS = 6


def _respelled(a: str, b: str) -> bool:
    """Is one of these the other with its spelling corrected?

    EI is told to search for the real name, never the typo ("general automics"
    is General Atomics), and a corrected spelling shares no prefix with what
    was typed - so without this the drift check below reverted exactly the
    correction it was asked for, and the search went out for the typo.
    Six letters or more on the longer side, and the same first or last
    letter: short words are a letter apart from too many others for
    nearness to mean anything.
    """
    if (max(len(a), len(b)) < RESPELL_MIN_LETTERS
            or (a[:1] != b[:1] and a[-1:] != b[-1:])):
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= RESPELL_RATIO


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
        # Written about the same drifted subject, so neither is kept: the
        # objective falls back to one composed in code (§260).
        brief.search_angles = []
        brief.search_objective = ""

    # **The other wordings are held to the same subject** (§260): one that
    # drifted would pull reports of something else into the same request.
    # Held to the question *or* the resolved search, because an angle may
    # rightly name what the question only implies - the quarterback of
    # "did my eagles win" - and the search it rides beside has already
    # been held to the question above.
    anchors = (_content_words(query) | _content_words(brief.search_query)
               | _content_words(brief.subject))
    angles: list = []
    for angle in brief.search_angles or []:
        text = " ".join(str(angle or "").split())[:MAX_ANGLE_CHARS]
        if not text or text.lower() == brief.search_query.strip().lower():
            continue
        if text.lower() in (a.lower() for a in angles):
            continue
        if anchors and not _shares_subject(anchors, _content_words(text)):
            problems.append(f"the search angle {text!r} shares no subject "
                            "with the question; dropped")
            continue
        angles.append(text)
    brief.search_angles = angles[:MAX_ANGLES]
    brief.search_objective = " ".join(
        str(brief.search_objective or "").split())[:MAX_OBJECTIVE_CHARS]

    # **One place decides the window**, and this is it - the gate is where
    # every other field of the brief is normalised, so a floor applied at
    # parse time would have been a second rule in a second place, with the
    # 14-day default below able to walk past it.
    try:
        asked = max(0, int(brief.recency_days))
    except (TypeError, ValueError):
        problems.append("recency window was not a number")
        asked = 0
    brief.recency_days = _window(asked)
    if brief.recency_days != asked:
        problems.append(
            f"a {asked}-day window is narrow enough to return nothing; "
            f"widened to {brief.recency_days}")

    # A recap or an update is a question about a moment. Retrieved without a
    # window it competes against every well-ranked article ever written on the
    # subject, which is how a two-year-old explainer ends up being the evidence
    # for "what happened last night".
    if brief.intent in ("recap", "update", "preview", "causal") and not brief.recency_days:
        problems.append(
            f"a {brief.intent} with no recency window; defaulting to "
            f"{settings.ei_default_recency_days} days")
        brief.recency_days = _window(settings.ei_default_recency_days)

    # A question whose answer is a result is outcome-dependent whether or not
    # the model said so. Both of these are asking for something that does not
    # exist until the thing concludes, and a live domain is the case where the
    # gap between concluding and being reported is longest.
    #
    # **Except weather** (§194): a forecast is never a result and has no
    # "finished", so the result caution and the in-progress shape it brings
    # would only make a weather episode hedge about nothing.
    brief.outcome_dependent = (bool(brief.outcome_dependent)
                               or brief.intent in ("recap", "update")
                               or bool(brief.live_domain
                                       and brief.live_domain != "weather"))

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

    # **A team is not a town** (§268). "Did the Eagles win" named as
    # Philadelphia sends the question down the local ladder, which keeps only
    # what names the town (§194) - and a national story about a team reached
    # the writer with no sources at all. When the provider name the brief
    # resolved (`live_entity`) is a team in the league roster
    # (`live_route.ROSTER`), or a sports brief's question or subject names
    # one, the question is about the team, never its city's news. **Never on
    # any other brief** (review, §268): the roster's names are everyday
    # phrases too - "Miami heat wave", "Minnesota wild fires", "Utah jazz
    # festival" - and a weather or local question must keep its town.
    teams: list = []
    if brief.place and brief.live_domain != "weather":
        import live_route

        teams = live_route.teams_in(brief.live_entity)
        if not teams and brief.live_domain == "sports":
            teams = live_route.teams_in(" ".join([query, brief.subject]))
        if teams:
            problems.append(
                f"place {brief.place!r} dropped: the question names "
                f"{teams[0][1]}, a team, not a town's news")
            brief.place = ""

    apply_named_slot(brief, query)

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
        # **The broader one, written in the same breath as the precise one.**
        # An empty search is most often a query that was too specific for the
        # index rather than a subject nothing was published about, and the
        # cheapest possible rephrasing is the one that costs no second call:
        # EI already has the request in front of it, so it writes both. §109,
        # and it is the reason §82's "never a model call to rephrase" is
        # untouched - this is not another call.
        "search_fallback": {"type": "string"},
        # **Written for the search engine that reads them** (§260): Parallel
        # takes an objective in plain language beside two or three short
        # keyword queries, and ranks and cuts its excerpts against both.
        "search_angles": {"type": "array", "items": {"type": "string"}},
        "search_objective": {"type": "string"},
        "must_establish": {"type": "array", "items": {"type": "string"}},
        "recency_days": {"type": "integer"},
        "structure": {"type": "string", "enum": list(PICKABLE_STRUCTURES)},
        "cautions": {"type": "array", "items": {"type": "string"}},
        # **Read from `live_facts.LIVE_DOMAINS` rather than written out
        # again.** This was a hand-kept second copy of that tuple and it
        # drifted: `elections` was added there, never here, and because this
        # schema is enforced strictly the model *could not* emit it. So a
        # configured, healthy, keyless Polymarket sat registered and was never
        # once asked anything - the registry said yes and the router could
        # never say `elections`. Two vocabularies that must agree are one
        # vocabulary; `test_live_domain_enum_is_the_routing_vocabulary` keeps
        # it that way.
        "live_domain": {"type": "string",
                        "enum": [""] + list(live_facts.LIVE_DOMAINS)},
        # **The bare name a provider lists the subject under** (§265), so a
        # scoreboard or a quote search is asked with "Kansas City Chiefs"
        # rather than a sentence about them.
        "live_entity": {"type": "string"},
        "outcome_dependent": {"type": "boolean"},
        # **The town a local question is about** (§194). It routes the
        # question to that town's own outlets and its weather, instead of a
        # national index that has never heard of it.
        "place": {"type": "string"},
        # **What the episode is called, decided before a word of it exists**
        # (§127). The model's own `<<TITLE:>>` line is better - it knows what
        # the episode turned out to cover - but it arrives with the last token,
        # and until then the player showed the listener's own words with
        # capital letters on. This call is already being made, in front of the
        # first word, so a handful of output tokens buys a real title from the
        # first frame. It names a subject and an angle and never a result, for
        # the same reason nothing else here may: nothing has been looked up.
        "title": {"type": "string"},
        # **What kind of thing this is, decided before the first word.** The
        # writer's `<<CATEGORY:>>` arrives with its last token, after a
        # searched episode has been playing for a minute over a picture
        # matched off the typed words - which on a misspelled or oblique
        # question is the wrong one. Two to four words, placed on the tree in
        # code (`stories.resolve_category`), never trusted as a node id.
        "category": {"type": "string"},
        # **How to say the hard names, decided while the subject is being
        # resolved** (§165). The voice reads English spelling and has no
        # lexicon, so "Sagapolutele" is a guess unless somebody respells it.
        # This call already names the subject, so a handful of output tokens
        # respells it too - and it runs before the first word, which is the
        # only mention a pronunciation arriving later could miss.
        "pronounce": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"},
                               "say": {"type": "string"}},
                "required": ["name", "say"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["intent", "subject", "why_now", "why_now_confidence",
                 "search_query", "search_fallback", "search_angles",
                 "search_objective", "must_establish",
                 "recency_days", "structure", "cautions", "live_domain",
                 "live_entity", "outcome_dependent", "place", "title", "category",
                 "pronounce"],
    "additionalProperties": False,
}

EI_SYSTEM = """You are the editorial intelligence in front of a spoken-briefing \
app. You do not write the episode. You work out what the request actually is, \
and what one web search has to find for the episode to be worth hearing.

You have not looked anything up and your own knowledge is months out of date. \
So you never state what happened. You state what must be established, and you \
flag what the writer must not assume.

The search query is the most important thing you produce. It is sent verbatim \
to a web search engine over news and web content. Write what a good \
researcher would type - the resolved subject, the specific event or figure \
wanted, and the words that would appear in a report of it. Not a question, not \
a sentence, and never just the listener's words repeated back.

**A search that comes back empty is the worst outcome here**, worse than one \
that comes back broad: the episode is then written from memory of unknown age, \
or not written at all. So you write two queries - the precise one, and a \
broader one to fall back to - and you set the recency window to the widest \
span that still answers the question.

Judge the "why now" honestly. If a dominant recent event plausibly explains the \
question, say so and mark it high. If several might, say the most likely one \
and mark it medium. If nothing does, leave it empty and mark it low - an \
invented reason for asking is worse than none, because the episode will be \
built around it.

You do not know whether anything has happened, finished, or even started. That \
is not something to work around - it is the one thing you are certain of. Ask \
instead whether the answer they want is a *result*, which is a question about \
their request and not about the world, and say so. Then write cautions that \
hold whichever way it turns out.

**Not every request names a thing.** Some ask for a *kind* of thing - "founders \
lesson of the day", "a stoic quote", "a history story", "a word to know". That \
is a request for one good example, chosen by you, never the title of a blog, \
column, newsletter or podcast to go and find. Do not search for the words as \
a name, and never let the episode become a report on whether such a thing \
exists or has been updated. Resolve the subject to what it is about ("lessons \
from how founders built their companies"), search for the substance a good \
example would come from (a specific founder's documented decision, what they \
learned, and what happened), leave why_now empty and low, and set recency to \
0 unless the kind of thing is itself news.

**And some phrases that read as descriptions are proper names.** "Sunday \
Night Football" is not football played on a Sunday night: it is the name of \
one game, NBC's prime-time NFL game that week. The same goes for "Monday Night \
Football", "Thursday Night Football", "Sunday Night Baseball", "Hockey Night in \
Canada", "El Clasico", "the Iron Bowl", a named derby or rivalry, a named show \
or segment. When a request uses a name like that, resolve the subject to the \
single thing it names, put the name itself in the search query verbatim (it is \
what reports of it are headlined with), and never widen it to the category - \
a recap of Sunday Night Football is never a recap of whichever game that \
Sunday was reported most.

**Requests are typed fast on a phone, and names are misspelled.** A word that \
is nearly the name of something real is that thing: "general automics" is \
General Atomics, "ohtanni" is Shohei Ohtani, "nvidea" is Nvidia. Resolve the \
subject to the real name, spelled the way its owner spells it, and search \
for the real name - never for the typo, which no report was written under. \
The misspelling is never a different, unknown entity, never something to \
investigate, and never worth mentioning: nobody wants to hear that no \
company called "General Automics" exists. Only where two real things are \
equally near do you keep the listener's spelling and say so in cautions."""


FIELD_GUIDE = """- **intent** - what job is being asked for.
- **subject** - the entity or event, resolved and unambiguous. Expand what is
  abbreviated or implied, and correct what is misspelled ("general automics"
  is General Atomics), so that retrieval and writing agree on what this is
  about. Keep the listener's own sense of it; do not substitute a different
  subject that happens to be more newsworthy.
- **why_now** - what recent development plausibly put this in their head today,
  if any, and **why_now_confidence** for how sure that is. Nothing plausible
  means empty and low. Do not claim to know their motive.
- **search_query** - what to actually search for. See the rules above.
- **search_fallback** - the same search, broader, for when the first one comes
  back with nothing. Drop the qualifiers and keep the subject: if the first is
  "Bank of England November rate decision vote split", this is "Bank of England
  interest rate decision". It should be hard for this one to return nothing at
  all. Never identical to the first, and never so broad it is a different
  subject.
- **search_angles** - one or two more keyword queries for the same thing,
  sent beside search_query in the same request: three to six words each,
  worded the way a *different* report of it would be headlined - the box
  score rather than the recap, the filing rather than the coverage, the
  person's name rather than the team's ("Jalen Hurts touchdowns Giants",
  "Eagles Giants box score"). Never the broader search, never a question,
  never a repeat of search_query.
- **search_objective** - one or two sentences telling the search engine what
  this search is for, read on its own with nothing else: the resolved subject;
  for anything recent, **the moment as dates** worked out from the current time
  above ("the Eagles-Giants game played on or about Sunday 11 October 2026",
  never "last night"); what a useful source contains; and the kind of source
  that answers it best (a league's own site, a wire report, a company filing,
  a central bank's statement, the town's own paper, an explainer from a
  university or a manufacturer) and what does not (previews, opinion,
  fantasy advice, a simulated or video-game season). Never a result - you have
  not looked anything up - and never the listener's own words: write it as
  a researcher briefing a colleague.
- **must_establish** - the two to four things the evidence has to contain for
  this episode to work. Short noun phrases, not sentences. These are checked
  against what comes back, so make them things a report would actually say.
- **recency_days** - how recent evidence must be to answer this. A question
  about a specific recent event is days; a question about how something works
  is 0, meaning age does not matter. **Give the widest window that is still
  honest**, not the narrowest that would do. This filters what the search may
  even consider, and the evidence is sorted newest-first inside it anyway - so
  a window that is too wide costs nothing and a window that is too narrow
  returns nothing at all. "Last night" is answered as well by two days as by
  one.
- **structure** - the shape this story takes. Pick the one that fits the
  material, not the one that sounds most dramatic; `general` if none fits.
- **cautions** - what the writer must not assume, especially about time. If
  something may not have happened yet, or may have happened days rather than
  hours ago, say so here. This is where a wrong tense gets caught.
- **live_domain** - `sports` if this turns on a score, fixture or standing,
  or on a player's season, form or injury;
  `markets` if it turns on a price, index or rate; `elections` if it turns on
  a vote, a race, a referendum or the odds on one; empty otherwise. Pick the
  thing the answer *turns on*, not the subject area: "how does the electoral
  college work" is an explainer and empty, "who is ahead in the Senate race"
  is `elections`. `weather` if what they want is the weather or a forecast
  somewhere - and then **place** names where.
- **live_entity** - whenever the request is about a team, a game between
  teams, a player or athlete, a company or fund or index, or a race, its bare
  name as a scoreboard, a roster or a stock search lists it, whatever
  live_domain is: "Kansas City Chiefs", "Kansas City Chiefs, Buffalo Bills",
  "Patrick Mahomes", "NVIDIA", "S&P 500". Full official
  names, no description, no year, no angle, no possessive. Empty when the
  request names none of these.
- **outcome_dependent** - true if what they want is a result that only exists
  once something concludes: a final score, a winner, a verdict, a closing
  price, a vote count. This is about their question, not about the world - you
  have no idea whether the thing has finished, and "who won" is
  outcome-dependent whether it finished an hour ago or is still going. False
  for how something works, what someone is like, or what is at stake.
- **place** - when the request is about what is going on in one particular
  town, city or county - its local news, its events, its council, its
  weather - that place as "Town, State or Region, Country", spelled correctly
  ("San Anselmo, California, US"). Empty when the request only mentions a
  place on the way to something else ("the history of Paris", "why is
  Detroit's car industry shrinking"), and empty for a whole country. Never a
  place they did not name: if they say "my town" without naming it, empty.
- **title** - what this episode is called in a list: three to seven words
  naming the resolved subject and the angle, in title case. **Clear first,
  curious second**: the actual person, team, company or event by name, so a
  reader knows at once what it is about, with an angle that makes them want
  to hear what the episode says - never a metaphor or allusion that only
  makes sense afterwards. **Never their own wording handed back** - "what
  happened with the fed yesterday" is not a title, "The Fed's Rate Call and
  Who It Hurts" is, and "A Pause With Consequences" is too vague to tap. No question
  mark, no colon, and never a result, a score or a winner: you have not
  looked anything up. **Spelled correctly** even when the request is not -
  "elecion" is the election, and a misspelled name is spelled the way its
  owner spells it.
- **category** - what kind of thing the subject is, in two to four words,
  its most specific kind: "drone makers", "heavyweight boxing", "Federal
  Reserve policy" - never just "news", "business" or "sport". It chooses the
  picture the episode plays over, so name the kind of thing the episode is
  about, not the kind of question asked.
- **pronounce** - the names in the request or in your resolved subject that a
  voice reading English spelling would probably say wrong: people, places,
  teams, companies whose spelling does not tell an English reader how to say
  them ("Sagapolutele", "Keawe", "Szczesny", "Nguyen", "Worcestershire",
  "Hermes"). Each is `name` exactly as it is spelled and `say` as plain
  English syllables joined by hyphens, one word per word of the name, with
  the stressed syllable in capitals: "sah-gah-poh-loo-TEH-leh". List each
  hard word on its own as well as the full name, because an episode names
  somebody in full once and by surname after that. Only names you actually
  know how to say - a confident wrong respelling is worse than none - and
  nothing an English speaker already says right. Empty for most requests."""


def _entries(guide: str) -> dict:
    """`FIELD_GUIDE` cut at each "- **name**", in order, text unchanged."""
    out: dict = {}
    for chunk in re.split(r"\n(?=- \*\*)", guide.strip("\n")):
        name = re.match(r"- \*\*([a-z_]+)\*\*", chunk).group(1)
        out[name] = chunk
    return out


#: Each field's instructions, keyed by the field. `why_now` carries
#: `why_now_confidence` with it, as the guide always has.
FIELD_ENTRIES = _entries(FIELD_GUIDE)


def field_guide(fields=None) -> str:
    """The guide for these fields, or for all of them, joined as written."""
    names = list(FIELD_ENTRIES) if fields is None else [
        n for n in FIELD_ENTRIES if n in set(fields)]
    return "\n".join(FIELD_ENTRIES[n] for n in names)


# --------------------------------------------------------------------------
# The brief as two calls at once (§268)
# --------------------------------------------------------------------------
#: **Two halves of one brief, asked at the same moment.** On Haiku at medium
#: effort the one call wrote ~1,500 tokens of thinking and JSON, and at Haiku's
#: rate that is 6-9 s - so about half the briefs passed the 8 s ceiling and
#: searched the raw query (`since_boot` read 5 timeouts in 11, 10.14). Each
#: half writes about half of that and they run side by side, so the brief takes
#: about as long as the slower half. The cut is by what each needs to reason
#: about, and neither reads the other:
#:
#: * **understand** - what the request is: the job, the resolved subject, why
#:   now, what the evidence must contain, the window, the shape, the cautions,
#:   which provider it turns on and under what name, whether the answer is a
#:   result, and the town. Everything downstream that decides *how* the
#:   episode is researched and written reads these.
#: * **search** - the words: the precise and broader searches, the other
#:   wordings, the objective a search engine reads, and the episode's title,
#:   category and pronunciations.
#:
#: Each half resolves the subject for itself; the gate holds the searches to
#: the question exactly as it held the one call's.
PARTS = {
    "understand": ("intent", "subject", "why_now", "must_establish",
                   "recency_days", "structure", "cautions", "live_domain",
                   "live_entity", "outcome_dependent", "place"),
    "search": ("search_query", "search_fallback", "search_angles",
               "search_objective", "title", "category", "pronounce"),
}


def _schema_for(fields) -> dict:
    """`BRIEF_SCHEMA` cut to these fields - the same definitions, never a
    second copy that could drift from the first."""
    names = [n for n in BRIEF_SCHEMA["properties"]
             if n in fields or (n == "why_now_confidence" and "why_now" in fields)]
    return {
        "type": "object",
        "properties": {n: BRIEF_SCHEMA["properties"][n] for n in names},
        "required": names,
        "additionalProperties": False,
    }


UNDERSTAND_SCHEMA = _schema_for(PARTS["understand"])
SEARCH_SCHEMA = _schema_for(PARTS["search"])

#: **The third part: the plan of the piece** (§270, at the owner's direction).
#: Written from the question alone, beside the other two, so the writer is
#: handed a shape to check against the evidence rather than designing the
#: piece from nothing in front of the first word. Not in `BRIEF_SCHEMA`: the
#: one-call brief (`EI_SPLIT=0`) is unchanged, byte for byte.
OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "does": {"type": "string"},
                    "needs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["does", "needs"],
                "additionalProperties": False,
            },
        },
        "opening_who": {"type": "string"},
        "opening_what": {"type": "string"},
        "opening_when": {"type": "string"},
        "ending": {"type": "string"},
    },
    "required": ["beats", "opening_who", "opening_what", "opening_when",
                 "ending"],
    "additionalProperties": False,
}

OUTLINE_GUIDE = """Plan the shape of the episode - nothing else. Someone \
else will search and write it, from evidence you will not see.

**State no facts.** Not a result, a score, a figure, a date, a winner, or \
anything you would have to look up - you do not know them, and what you \
recall may be months old. Every line says what a part of the episode has to \
*find out*, never what it will say: "what the final score was", never "the \
Eagles won".

- **beats** - the parts of the episode in the order a listener should hear \
them, as many as the length above has room for and no more. For each: \
`does`, what that part answers or explains, in a short phrase; `needs`, the \
one to three things the evidence must contain for that part to exist. A \
part nobody could find evidence for is dropped by the writer and its time \
given to the parts that remain, so do not pad.
- **opening_who / opening_what / opening_when** - what the first two \
sentences must pin down, as the *kind* of thing: "the team", "the result of \
their latest game", "the day it was played". These are slots the writer \
fills from the evidence.
- **ending** - what the last line should land on: the most concrete thing \
the episode will have, as a kind of thing ("the figure that settles the \
question"). Never a question, a tease, or what happens next."""

#: What each call is asked to return. Written out so every schema FAM sends
#: is one `tests/test_structured_output_schemas.py` can find and check.
FORMATS = {
    "whole": {"type": "json_schema", "schema": BRIEF_SCHEMA},
    "understand": {"type": "json_schema", "schema": UNDERSTAND_SCHEMA},
    "search": {"type": "json_schema", "schema": SEARCH_SCHEMA},
    "outline": {"type": "json_schema", "schema": OUTLINE_SCHEMA},
}


def build_ei_prompt(query: str, minutes: int, context: str = "",
                    now: str = "", covered=(), fields=None) -> str:
    """The brief's user turn: the request, the moment, and what to work out.

    `fields` names the entries of `FIELD_GUIDE` to ask for, in its order;
    `None` asks for all of them, which is the one-call brief's text exactly.
    The two halves of a split brief each ask for their own (§268).
    """
    depth_name, depth_mix = depth_for(minutes)
    follow_up = ""
    if context:
        follow_up = (
            f"\nThis is a follow-up. They have already heard a briefing on: "
            f"{context}\nThe search must find what is *new* relative to that, "
            "not the same ground again.\n")
    if covered:
        # A daily episode's earlier editions
        # (`daily_edition.with_earlier_editions`).
        # Without them "a new one every day" is a promise nothing can keep:
        # the same request on consecutive days is the same request, and the
        # likeliest example is the likeliest example every morning.
        follow_up += (
            "\nThis is today's edition of an episode they get every day. "
            "Earlier editions were called: "
            + "; ".join(f'"{t}"' for t in covered)
            + ".\nPick something different for today and aim the search at "
            "it - a different example if they asked for a kind of thing, "
            "what is new since then if it is news.\n")
    return f"""Someone asked a briefing app this:

<request>{query}</request>

It is currently {now}.
{follow_up}
The episode will run about {minutes} minute{"s" if minutes != 1 else ""}, which
is room for {depth_name}: {depth_mix}. Let that shape how much the search needs
to find - a short episode needs the core established well, a long one needs the
surrounding factors too.

Work out:

""" + field_guide(fields)


def _pronounce_pairs(raw) -> list:
    """The model's `pronounce` list, cleaned to what `pronunciation` accepts."""
    out = []
    for item in list(raw or [])[:pronunciation.MAX_PER_EPISODE]:
        if not isinstance(item, dict):
            continue
        pair = pronunciation.clean(str(item.get("name", "")), str(item.get("say", "")))
        if pair:
            out.append({"name": pair[0], "say": pair[1]})
    return out


#: **Worked examples, in the instructions' own cached block** (§268). A
#: smaller model follows a finished example more closely than a rule, and the
#: failures that reached listeners were ones an example shows at a glance: a
#: window that matched the words of the request ("today" is days, "how does"
#: is none), the bare name a provider lists, a caution about time, searches a
#: report would be headlined with. Each is a whole brief, so both halves of a
#: split brief read the same block and the cache holds one copy of it. None
#: of them asserts a result, and every date in them is worked out from the
#: moment the example names.
EXAMPLE_BRIEFS = (
    ("did the eagles win", "Monday 12 October 2026 at 08:10 EDT", 2, {
        "intent": "recap",
        "subject": "the Philadelphia Eagles' most recent NFL game as of "
                   "Monday 12 October 2026",
        "why_now": "an NFL Sunday has just been played and the Eagles were "
                   "likely on it",
        "why_now_confidence": "medium",
        "search_query": "Philadelphia Eagles game result",
        "search_fallback": "Philadelphia Eagles",
        "search_angles": ["Eagles box score", "Jalen Hurts Eagles game"],
        "search_objective": "The Philadelphia Eagles' most recent NFL game "
                            "before Monday 12 October 2026: the final score, "
                            "the plays that decided it and what it does to "
                            "their record. Best answered by a wire report, the "
                            "NFL's own site or the team's; not previews, "
                            "betting lines or fantasy advice.",
        "must_establish": ["final score", "opponent", "deciding plays"],
        "recency_days": 3,
        "structure": "sports_recap",
        "cautions": ["the game may not have been played or finished; state a "
                     "result only if a source reports it as final",
                     "work out the day of the game from the source dates - "
                     "a Thursday game is not last night on a Monday"],
        "live_domain": "sports",
        "live_entity": "Philadelphia Eagles",
        "outcome_dependent": True,
        "place": "",
        "title": "The Eagles' Latest Game and What Decided It",
        "category": "NFL football",
        "pronounce": [],
    }),
    ("nvda stock today", "Wednesday 14 October 2026 at 11:30 EDT", 2, {
        "intent": "update",
        "subject": "NVIDIA's share price on Wednesday 14 October 2026",
        "why_now": "",
        "why_now_confidence": "low",
        "search_query": "NVIDIA stock price today",
        "search_fallback": "NVIDIA shares",
        "search_angles": ["NVDA shares move", "Nvidia stock analysts"],
        "search_objective": "How NVIDIA (NASDAQ: NVDA) shares are trading on "
                            "Wednesday 14 October 2026 and why: the move, the "
                            "reason given for it and any company news behind "
                            "it. Best answered by financial wires and market "
                            "news; not long-term price targets, opinion or "
                            "trading tips.",
        "must_establish": ["share price move", "reason for the move"],
        "recency_days": 2,
        "structure": "business",
        "cautions": ["the price moves through the day: give it as of when it "
                     "was reported, and never as a close unless a source "
                     "reports the close",
                     "any figure recalled rather than reported is months old"],
        "live_domain": "markets",
        "live_entity": "NVIDIA",
        "outcome_dependent": True,
        "place": "",
        "title": "Why Nvidia Shares Are Moving Today",
        "category": "semiconductor stocks",
        "pronounce": [],
    }),
    ("latest on shohei ohtani", "Tuesday 13 October 2026 at 19:05 PDT", 3, {
        "intent": "update",
        "subject": "Shohei Ohtani, the Los Angeles Dodgers' two-way player",
        "why_now": "the MLB postseason is played in October",
        "why_now_confidence": "medium",
        "search_query": "Shohei Ohtani Dodgers news",
        "search_fallback": "Shohei Ohtani",
        "search_angles": ["Ohtani Dodgers playoff game",
                          "Shohei Ohtani stats October 2026"],
        "search_objective": "The newest reporting on Shohei Ohtani of the Los "
                            "Angeles Dodgers in the week to Tuesday 13 October "
                            "2026: his latest games, his health and his role. "
                            "Best answered by wire reports, MLB's own site and "
                            "Dodgers coverage; not fantasy advice, betting "
                            "lines or reviews of earlier seasons.",
        "must_establish": ["most recent game", "current status"],
        "recency_days": 7,
        "structure": "breaking",
        "cautions": ["\"latest\" means the last week: earlier seasons are "
                     "background, never the news",
                     "whether the Dodgers are still playing comes from dated "
                     "evidence, not from memory"],
        "live_domain": "sports",
        "live_entity": "Los Angeles Dodgers",
        "outcome_dependent": True,
        "place": "",
        "title": "Shohei Ohtani's October So Far",
        "category": "Major League Baseball",
        "pronounce": [{"name": "Shohei", "say": "SHOH-hay"},
                      {"name": "Ohtani", "say": "oh-TAH-nee"}],
    }),
    ("how do heat pumps work", "Friday 9 October 2026 at 12:00 BST", 4, {
        "intent": "explainer",
        "subject": "how air-source heat pumps heat and cool a home",
        "why_now": "",
        "why_now_confidence": "low",
        "search_query": "how air source heat pumps work refrigerant cycle",
        "search_fallback": "how heat pumps work",
        "search_angles": ["heat pump cold weather efficiency",
                          "heat pump versus gas boiler explained"],
        "search_objective": "An explanation of how air-source heat pumps move "
                            "heat into and out of a home: the refrigerant "
                            "cycle, why they use less energy than resistance "
                            "heating and how cold weather affects them. Best "
                            "answered by an explainer from a university, an "
                            "energy agency or a manufacturer; not installers' "
                            "sales pages.",
        "must_establish": ["refrigerant cycle", "efficiency against boilers",
                           "cold-weather performance"],
        "recency_days": 0,
        "structure": "explainer",
        "cautions": [],
        "live_domain": "",
        "live_entity": "",
        "outcome_dependent": False,
        "place": "",
        "title": "How a Heat Pump Moves Heat Uphill",
        "category": "home heating",
        "pronounce": [],
    }),
)


def examples_block() -> str:
    """The worked examples as the instructions carry them."""
    parts = ["\n\nWorked examples. They show the shape of a good brief - how "
             "specific the searches are, how the window follows the words of "
             "the request, how a caution is worded, which bare name a "
             "scoreboard or a quote search lists. Nothing in them is a fact to "
             "reuse: each was written before anything was looked up, at the "
             "moment it names."]
    for query, now, minutes, brief in EXAMPLE_BRIEFS:
        parts.append(f"\n<example>\nRequest: {query}\nIt is currently {now}. "
                     f"About {minutes} minutes.\nBrief: "
                     f"{json.dumps(brief, ensure_ascii=False)}\n</example>")
    return "".join(parts)


def ei_system():
    """The brief's `system`: its instructions and, unless `EI_EXAMPLES=0`, the
    worked examples - one block, marked cacheable unless `PROMPT_CACHE=0`.

    Byte-identical from call to call and shared by both halves of a split
    brief, so after the first brief the instructions are read from the cache
    (§268). Everything that varies - the request, the moment, the fields
    asked for - is in the user turn after it.
    """
    text = EI_SYSTEM + (examples_block() if settings.ei_examples else "")
    if not settings.prompt_cache:
        return text
    marker: dict = {"type": "ephemeral"}
    if str(settings.prompt_cache_ttl or "").strip() == "1h":
        marker["ttl"] = "1h"
    return [{"type": "text", "text": text, "cache_control": marker}]


#: How many recent briefs' wall-clock times each path keeps (§268).
SECONDS_KEPT = 200
#: Every brief's ending by path - `search` (a listener is waiting) or `ahead`
#: (prefetch and the editions, built before any tap) - and how long each took
#: from the moment it was asked to the moment the pipeline had it, degraded
#: or not. `_OUTCOMES` stays the total. The per-path figures are what decide
#: whether an effort level or a timeout is right for each.
_BY_PATH: dict = {"search": collections.Counter(), "ahead": collections.Counter()}
_SECONDS: dict = {"search": collections.deque(maxlen=SECONDS_KEPT),
                  "ahead": collections.deque(maxlen=SECONDS_KEPT)}
#: How each half of a split brief ended - or the one call, as `whole`.
_PART_OUTCOMES: dict = {}


def _count(kind: str, path: str) -> None:
    _OUTCOMES[kind] += 1
    _BY_PATH.setdefault(path, collections.Counter())[kind] += 1


async def _ask(client, part: str, query: str, minutes: int, context: str,
               now: str, covered: tuple, timeout: float, notes,
               path: str = "") -> tuple:
    """One brief call: `(kind, data, reason)`, `kind` "ok" or why it failed.
    Never raises."""
    if part == "outline":
        content = (build_ei_prompt(query, minutes, context, now, covered, ())
                   + OUTLINE_GUIDE)
    else:
        content = build_ei_prompt(query, minutes, context, now, covered,
                                  None if part == "whole" else PARTS[part])
    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=settings.ei_model,
                max_tokens=settings.ei_max_tokens,
                system=ei_system(),
                output_config={
                    "effort": settings.ei_effort,
                    "format": FORMATS[part],
                },
                messages=[{"role": "user", "content": content}],
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return "timeout", None, f"EI did not answer within {timeout:g}s"
    except Exception as exc:  # noqa: BLE001 - availability outranks diagnosis here
        return "failed", None, f"EI call failed: {exc}"

    if notes is not None:
        notes.usage.add_model_call(settings.ei_model, getattr(response, "usage", None))

    # `output_config.format` guarantees valid JSON in the first text block -
    # but a refusal stops before any of that, and a refusal parsed as a brief
    # would be a silent degradation rather than a visible one.
    if getattr(response, "stop_reason", "") == "refusal":
        pipeline_errors.refusal(f"brief: {part}", settings.ei_model, path,
                                query, response)
        return "refused", None, "EI declined the request"
    # **A brief cut off by its own ceiling** (§248). The thinking and the
    # JSON share `EI_MAX_TOKENS`, so a brief that thought for longer than the
    # ceiling allowed stops mid-object and reads as unparseable - which used
    # to be reported as "nothing readable", hiding that the cure is a setting.
    if getattr(response, "stop_reason", "") == "max_tokens":
        return ("out_of_room", None,
                f"EI ran out of room at EI_MAX_TOKENS={settings.ei_max_tokens} "
                "before the brief was finished")
    try:
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (StopIteration, AttributeError, ValueError, TypeError) as exc:
        return "unreadable", None, f"EI returned nothing readable: {exc}"
    return "ok", data, ""


#: The fields the search half writes, which a brief keeps from it even when
#: the other half failed and the brief is degraded (§268): a resolved search
#: is worth having beside a raw-query brief, and the gate still holds it to
#: the question.
_SEARCH_FIELDS = PARTS["search"]


def _search_fields(data: dict, query: str) -> dict:
    """The search half's fields as `Brief` holds them."""
    return {
        "search_query": str(data.get("search_query", "")),
        "search_fallback": str(data.get("search_fallback", "")),
        "search_angles": [str(a) for a in (data.get("search_angles") or [])
                          if isinstance(a, str)],
        "search_objective": str(data.get("search_objective", "") or ""),
        "title": clean_title(str(data.get("title", "")), query),
        "category": " ".join(str(data.get("category", "") or "").split())[:80],
        "pronounce": _pronounce_pairs(data.get("pronounce")),
    }


async def understand(query: str, minutes: int = DEFAULT_MINUTES, context: str = "",
                     notes=None, now: str | None = None, covered=(),
                     ahead: bool = False) -> Brief:
    """Work out what this request is, before anything is retrieved.

    Never raises. Every failure path returns `fallback_brief`, which searches
    the raw query - the behaviour this layer replaced - so an EI outage costs
    quality and never availability.

    `notes` is the episode's `ScriptNotes`, when there is one: this call is a
    real model call on the generation path and must land in the same usage
    total as the writing call, or a researched episode reports at less than it
    cost.

    `ahead` is True for a brief nobody is waiting on - prefetch and the
    editions - which may take `EI_AHEAD_TIMEOUT_SECONDS` rather than the
    search path's `EI_TIMEOUT_SECONDS` (§268).

    **Two calls at once unless `EI_SPLIT=0`** (§268, `PARTS`). The brief is
    the understanding half's: when that half fails the brief is degraded,
    as the one call's failure always made it, but keeps the search half's
    wording if that half answered. When only the search half fails the brief
    stands and the gate searches the question, as it does for any brief
    without a search.
    """
    query = (query or "").strip()
    if not query:
        return fallback_brief(query, "the query is empty")
    if not settings.episode_intelligence:
        return fallback_brief(query, "EPISODE_INTELLIGENCE=0")

    # The listener's clock, as the writer's is (`listener_clock`, 10.1).
    import listener_clock

    now = now or listener_clock.now_line()
    path = "ahead" if ahead else "search"
    timeout = float(settings.ei_ahead_timeout_seconds if ahead
                    else settings.ei_timeout_seconds)
    parts = ("understand", "search") if settings.ei_split else ("whole",)
    started = time.monotonic()
    outline = None

    try:
        client = build_async_client(credentials.active("ANTHROPIC_API_KEY"))
    except Exception as exc:  # noqa: BLE001
        answers = [("failed", None, f"EI call failed: {exc}")] * len(parts)
    else:
        # **The plan of the piece, beside the other two** (§270). Started
        # with them and never allowed to hold the brief: once they are in it
        # gets `EI_OUTLINE_GRACE_SECONDS` more on the search path (all of the
        # ceiling ahead, where nobody waits), then the brief goes without it.
        main = asyncio.gather(*(
            _timed(part, path, _ask(client, part, query, minutes, context, now,
                                    tuple(covered or ()), timeout, notes, path),
                   notes)
            for part in parts))
        outline_task = None
        if len(parts) == 2 and settings.ei_outline:
            outline_task = asyncio.ensure_future(_timed(
                "outline", path,
                _ask(client, "outline", query, minutes, context, now,
                     tuple(covered or ()), timeout, notes, path)))
        try:
            answers = await main
        except BaseException:
            if outline_task is not None:
                outline_task.cancel()
            raise
        if outline_task is not None and answers[0][0] != "ok":
            # A degraded brief shows no plan, so it is not waited for
            # (review, §273).
            outline_task.cancel()
        elif outline_task is not None:
            outline = await _outline_by(outline_task, ahead, started, timeout)
    _SECONDS.setdefault(path, collections.deque(maxlen=SECONDS_KEPT)).append(
        round(time.monotonic() - started, 3))
    for part, (kind, _data, _why) in zip(parts, answers):
        _PART_OUTCOMES.setdefault(part, collections.Counter())[kind] += 1
    if outline is not None:
        _PART_OUTCOMES.setdefault("outline", collections.Counter())[outline[0]] += 1
        if outline[0] == "late":
            # The part's own row is written when it ends; a late one never
            # does, so its wait is recorded as the grace it was given.
            pipeline_errors.step("brief: outline", path, "late",
                                 time.monotonic() - started)

    head_kind, head, head_why = answers[0]
    words: dict = {}
    if len(parts) == 2:
        search_kind, search, search_why = answers[1]
        if search_kind == "ok":
            words = _search_fields(search, query)

    pipeline_errors.step("brief", path, head_kind, _SECONDS[path][-1])
    if head_kind != "ok":
        _count(head_kind, path)
        # On /admin's pipeline errors (§269): a degraded brief is the first
        # thing to know about, and §268's timeouts sat in /api/health.
        pipeline_errors.record("brief", f"{head_kind} ({path})", head_why, query)
        brief = fallback_brief(query, head_why)
        if words:
            for name, value in words.items():
                setattr(brief, name, value)
            brief.notes.append("the search half answered; its searches are kept")
            brief = gate(brief, query)
        return brief

    data = dict(head)
    if len(parts) == 2:
        data.update({k: v for k, v in (search or {}).items() if k in _SEARCH_FIELDS})
    brief = Brief(
        query=query,
        intent=str(data.get("intent", "")),
        subject=str(data.get("subject", "")),
        why_now=str(data.get("why_now", "")),
        why_now_confidence=str(data.get("why_now_confidence", "")),
        must_establish=list(data.get("must_establish") or []),
        recency_days=data.get("recency_days", 0),
        structure=str(data.get("structure", "")),
        cautions=list(data.get("cautions") or []),
        live_domain=str(data.get("live_domain", "")),
        live_entity=" ".join(str(data.get("live_entity", "") or "").split())[:120],
        outcome_dependent=bool(data.get("outcome_dependent", False)),
        place=" ".join(str(data.get("place", "") or "").split())[:120],
        **_search_fields(data, query),
    )
    if len(parts) == 2 and search_kind != "ok":
        brief.notes.append(f"the search half did not answer ({search_kind}): "
                           f"{search_why}")
        pipeline_errors.record("brief", f"search half {search_kind} ({path})",
                               search_why, query, pipeline_errors.NOTICE)
    if outline is not None:
        if outline[0] == "ok":
            _outline_from(brief, outline[1], minutes)
        else:
            # The writer plans the piece itself, as it did before §270.
            brief.notes.append(f"the outline did not answer ({outline[0]}): "
                               f"{outline[2]}")
    brief = gate(brief, query)
    _count("ok", path)
    log.info("EI %r -> intent=%s structure=%s recency=%dd outcome=%s "
             "why_now=%s(%s) search=%r fallback=%r [%s, %.2fs]", query,
             brief.intent, brief.structure, brief.recency_days,
             "pending" if brief.outcome_dependent else "n/a",
             brief.why_now or "-", brief.why_now_confidence, brief.retrieval,
             brief.broader, path, _SECONDS[path][-1])
    return brief


#: The episode mark the loading screen's first step waits for (§288): the
#: request's meaning is written - the understanding half, or the one call.
UNDERSTOOD_MARK = "brief_understood"


async def _timed(part: str, path: str, ask, notes=None) -> tuple:
    """One part's answer, with how long it took recorded for /admin (§270).

    The understanding half answering marks `UNDERSTOOD_MARK` on the episode's
    clock the moment it lands (§288): that is the request contextualized,
    and the loading screen's first step had been held to the slowest of all
    three calls."""
    began = time.monotonic()
    answer = await ask
    pipeline_errors.step(f"brief: {'whole' if part == 'whole' else part}", path,
                         answer[0], time.monotonic() - began)
    if part in ("understand", "whole") and answer[0] == "ok":
        marks = getattr(notes, "marks", None)
        if marks is not None:
            marks.mark(UNDERSTOOD_MARK)
    return answer


async def _outline_by(task, ahead: bool, started: float, timeout: float) -> tuple:
    """The outline's answer, or why it was not waited for. Never raises.

    On the search path it may finish at most `EI_OUTLINE_GRACE_SECONDS`
    after the other two parts, so it can never be the slowest part of the
    brief by more than that; ahead it has the brief's whole ceiling.
    """
    if ahead:
        wait = max(0.0, timeout - (time.monotonic() - started))
    else:
        wait = float(settings.ei_outline_grace_seconds)
    try:
        # `wait_for` cancels the task itself on a timeout, and on a cancel
        # of this coroutine - a listener who left - takes the task with it
        # (review, §273: a shield kept it running to its own ceiling).
        return await asyncio.wait_for(task, wait)
    except asyncio.TimeoutError:
        return "late", None, f"not back {wait:g}s after the rest of the brief"
    except asyncio.CancelledError:
        task.cancel()
        raise
    except Exception as exc:  # noqa: BLE001 - the brief stands without it
        return "failed", None, f"{type(exc).__name__}: {exc}"


#: Most beats an episode of each length is planned with: a short episode
#: has room for the core and little else (`DEPTH_BANDS`).
def max_beats(minutes: int) -> int:
    return max(2, min(7, int(minutes) + 2))


_OUTLINE_DIGIT = re.compile(r"\d")


def _outline_line(value, limit: int) -> str:
    """One line of the outline, or "" when it may not reach the writer.

    **A line with a digit in it is dropped**: a score, a price, a date or a
    count in the plan is a fact stated before anything was looked up, which
    is the one thing the plan may never do. Coarse on purpose - "the 2026
    season" is lost too, and the writer, reading the evidence, says it.
    """
    text = " ".join(str(value or "").split())
    if not text or _OUTLINE_DIGIT.search(text):
        return ""
    return text[:limit]


def _outline_from(brief: "Brief", data: dict, minutes: int) -> None:
    """The outline's answer onto the brief, held to what a plan may say."""
    beats = []
    for raw in list((data or {}).get("beats") or []):
        if not isinstance(raw, dict):
            continue
        does = _outline_line(raw.get("does"), 160)
        if not does:
            continue
        needs = [n for n in (_outline_line(x, 100)
                             for x in list(raw.get("needs") or [])[:3]) if n]
        beats.append({"does": does, "needs": needs})
        if len(beats) >= max_beats(minutes):
            break
    brief.outline_beats = beats
    opening = {slot: _outline_line((data or {}).get(f"opening_{slot}"), 120)
               for slot in ("who", "what", "when")}
    brief.outline_opening = {k: v for k, v in opening.items() if v}
    brief.outline_ending = _outline_line((data or {}).get("ending"), 160)


def build_outline_note(brief: "Brief") -> str:
    """The plan of the piece, as the writer reads it (§270), or ""."""
    if not brief.outline_beats:
        return ""
    lines = ["\nA plan for the piece, made from the question before anything "
             "was looked up. It states no facts: each line says what a part of "
             "the episode has to find out, not what it will say."]
    for beat in brief.outline_beats:
        needs = f" (needs: {'; '.join(beat['needs'])})" if beat["needs"] else ""
        lines.append(f"- {beat['does']}{needs}")
    if brief.outline_opening:
        slots = "; ".join(f"{k} - {v}" for k, v in brief.outline_opening.items())
        lines.append(f"The first two sentences have to pin down: {slots}. Fill "
                     "them from the evidence.")
    if brief.outline_ending:
        lines.append(f"It should land on: {brief.outline_ending}.")
    lines.append(
        "It is a starting shape, not a set of boxes and not evidence. Keep a "
        "part only when the evidence supports it, drop or reorder freely, give "
        "a dropped part's time to the parts the evidence does support, and "
        "add what the evidence shows that the plan did not foresee. Decide the "
        "opening last, once you know what the evidence says - the plan only "
        "says what it has to situate.")
    return "\n".join(lines) + "\n"


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

    return ("\n".join(lines) + "\n" + build_structure_note(brief)
            + build_outline_note(brief))


def _spread(seconds) -> dict:
    """`n`, median, 90th percentile and slowest of some brief times."""
    values = sorted(seconds)
    if not values:
        return {"n": 0}

    def at(share: float) -> float:
        return values[min(len(values) - 1, int(share * len(values)))]

    return {"n": len(values), "median": at(0.5), "p90": at(0.9),
            "max": values[-1]}


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
        "max_tokens": settings.ei_max_tokens,
        # How the briefs since boot ended: `ok` or why each degraded (§248).
        "since_boot": dict(_OUTCOMES),
        # The same, split by who was waiting (§268): `search` is a listener
        # in front of the first word, `ahead` is prefetch and the editions.
        "since_boot_by_path": {p: dict(c) for p, c in _BY_PATH.items()},
        # How long the recent briefs on each path took, failures included -
        # the number that says whether an effort level fits its timeout.
        "seconds_by_path": {p: _spread(s) for p, s in _SECONDS.items()},
        # Each half of a split brief, or the one call as `whole`.
        "parts_since_boot": {p: dict(c) for p, c in _PART_OUTCOMES.items()},
        "split": bool(settings.ei_split),
        # The plan of the piece as a third part (§270), and how long it may
        # hold a searched brief after the other two are in.
        "outline": bool(settings.ei_split and settings.ei_outline),
        "outline_grace_seconds": settings.ei_outline_grace_seconds,
        "examples": bool(settings.ei_examples),
        "ahead_timeout_seconds": settings.ei_ahead_timeout_seconds,
        "default_recency_days": settings.ei_default_recency_days,
        "intents": list(INTENTS),
        "structures": [s for s in PICKABLE_STRUCTURES if s != "general"],
        # Reported because its absence is what produced §88, and a deploy
        # running a build without it looks identical from outside to one that
        # has it - right up until a listener asks about a game in progress.
        "outcome_status_checked": "in_progress" in STRUCTURES,
    }
