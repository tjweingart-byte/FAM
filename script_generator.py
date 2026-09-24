"""Turn a search query into a podcast script of a requested length.

Two things matter here:

1. **Length control.** The requested minutes are converted into a word budget
   at the narrator's nominal rate. The budget is given to Claude as a hard
   constraint with a per-section breakdown, because "write a 6 minute podcast"
   alone produces wildly variable lengths.

2. **Streaming.** The script is streamed sentence by sentence so speech
   synthesis can start before the model has finished writing. This is what
   makes audio playable within a couple of seconds instead of after the whole
   episode is generated.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import time
from dataclasses import dataclass
from typing import AsyncIterator

import credentials
import episode_intelligence
import live_facts
import metering
import prefetch
from anthropic_client import build_async_client
from cache import research_reason
import live_captions
from config import settings

import research as research_mod

log = logging.getLogger(__name__)


def load_style_examples() -> list[tuple[str, str]]:
    """Briefings from examples/ that show the model the house voice.

    Rules describe a style; examples demonstrate one, and a model matches a
    demonstration far more closely than a description. This is the most direct
    control there is over how the writing sounds.

    Files are `<minutes>-<slug>.txt`: first line the query, blank line, then the
    script. Read once at import; an empty folder changes nothing.
    """
    import pathlib

    directory = pathlib.Path(__file__).resolve().parent / "examples"
    found: list[tuple[str, str]] = []
    if not directory.is_dir():
        return found

    for path in sorted(directory.glob("*.txt")):
        try:
            head, _, body = path.read_text(encoding="utf-8").partition("\n\n")
        except OSError:
            continue
        query, script = head.strip(), body.strip()
        if query and script:
            found.append((query, script))
        else:
            log.warning("skipping style example %s: expected a query, a blank line, then the script",
                        path.name)
    if found:
        log.info("loaded %d style example(s) from examples/", len(found))
    return found


STYLE_EXAMPLES = load_style_examples()


def style_example_block() -> str:
    """The examples, formatted for the system prompt. Empty when there are none."""
    if not STYLE_EXAMPLES:
        return ""
    parts = [
        "\nThis is the house voice. Match its rhythm, its directness and the way "
        "it opens and closes. Do not borrow its facts or its topics - only how it "
        "sounds. They show the spoken script only; you must still end with the "
        "<<NEXT: ...>> line:\n"
    ]
    for query, script in STYLE_EXAMPLES:
        words = len(script.split())
        parts.append(f'<example query="{query}" words="{words}">\n{script}\n</example>\n')
    return "\n".join(parts)

# The last few openers spoken by this server. Fed back into the prompt so a
# listener working through several episodes does not hear the same shape of
# introduction every time - the single most noticeable tell of a generated show.
_RECENT_OPENERS: list[str] = []
_RECENT_LIMIT = 8


def remember_opener(text: str) -> None:
    if not text:
        return
    _RECENT_OPENERS.append(text.strip())
    del _RECENT_OPENERS[:-_RECENT_LIMIT]

_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+")
# The one line the model may write that is not speech: its prediction of what
# this listener would most likely ask next, read off what the episode covered.
# Stripped before synthesis and handed to the interface, which offers it as a
# one-tap "go deeper". The script itself never hints at it.
_NEXT_MARKER = re.compile(r"<<\s*NEXT\s*:\s*([^<>]{1,160}?)\s*>>", re.I)
#: The episode's own title, on the same kind of trailing line as NEXT and for
#: exactly the same reason: it is written *by* the model, off what it actually
#: covered, and it costs no second call.
#:
#: The alternative would have been a model call in front of - or behind - every
#: episode purely to name it, which is the expensive half of an episode spent
#: on a label. A marker line is free.
_TITLE_MARKER = re.compile(r"<<\s*TITLE\s*:\s*([^<>]{1,120}?)\s*>>", re.I)
#: One sentence saying what the episode is, on the same kind of line and for
#: the same reason as TITLE: free, written off what was actually covered, and
#: never spoken. What a "Pick up where you left off" card draws under its
#: title, so a half-heard episode reads like every other myFAM tile (§127).
_SUMMARY_MARKER = re.compile(r"<<\s*SUMMARY\s*:\s*([^<>]{1,240}?)\s*>>", re.I)
# Anything that would be read aloud as punctuation noise rather than speech.
_MARKDOWN = re.compile(r"[*_`#>\[\]]|^\s*[-•]\s+", re.MULTILINE)

# --------------------------------------------------------------------------
# the first sentence is the whole audition
#
# PROBLEMS.md §94. A researched episode opened with "I don't have anything
# reliable on last night's Dodgers score to hand you, and I'm not going to
# guess a result and dress it up as fact" - and then, four sentences later,
# gave the innings, the pitcher, the two home runs and the magic number,
# correctly. Nothing was wrong with the episode. Everything was wrong with the
# first ten seconds of it, and the first ten seconds are the only part a
# listener uses to decide whether there will be an eleventh.
#
# The cause was structural rather than a lapse: the words were written
# *before* the facts arrived. The cover half of the old answer-first path ran
# with `search=False` and no packet, and the tool path wrote into a search it
# had not made yet. In both, the model was asked a question whose answer it
# did not yet hold, and the honest thing for a lone answerer to do is say so.
#
# **Both of those are gone** (PROBLEMS.md §108). Nothing is written before the
# retrieval finishes now, on either backend, so the model is never in that
# position in the first place - which is a fix where the prompt wording and
# this guard were both mitigations.
#
# The guard stays, because thin evidence still exists and a model handed a
# packet that misses the part it was asked about can still reach for a
# disclaimer. A prompt rule that fails silently is not a fix - the rule "no
# hedging about not having looked anything up" was already written when the
# Dodgers episode hedged - and every drop is still logged and carried on
# `ScriptNotes.meta_openings`, where it now means something different and
# sharper: the retrieval came back thin, not that the writer was guessing.
#
# What it may drop is narrow on purpose: sentences about the *writer's own
# access* to information, and only while nothing real has been said yet. Once a
# sentence of the episode proper is out, the guard is off for good - a piece
# that mentions what is unresolved *in the world*, which §88 requires, must
# pass through untouched.

#: Quoted speech is somebody else's sentence. Stripped before matching so that
#: a coach saying "I don't know yet" is not read as FAM disclaiming.
_QUOTED = re.compile(r"[\"“‘']([^\"“”‘’']{0,200})['\"”’]")

#: A sentence about what the writer does or does not have. Every one of these
#: is a fact about our own retrieval, never a fact about the world - the
#: distinction §88 and §89 are both built on, applied here to the one place it
#: costs the most.
_META_PHRASES = (
    r"\bi\s+(?:don'?t|do not|can'?t|cannot|couldn'?t|could not|haven'?t|"
    r"have not|didn'?t|did not|won'?t|will not)\b[^.?!]{0,60}?\b(?:have|hold|"
    r"know|knows|confirm|verify|see|seen|find|found|access|tell|give|hand|"
    r"report|say|state)\b",
    r"\bi(?:'?m| am)\s+not\s+(?:going\s+to\s+)?(?:guess|speculate|invent|"
    r"make\s+(?:that|one)\s+up|going\s+to\s+pretend)",
    r"\bnot\s+going\s+to\s+guess\b",
    r"\bdress\s+(?:it|that|them)\s+up\s+as\s+fact",
    r"\bwhat\s+i\s+can\s+tell\s+you\b",
    r"\bhere'?s\s+what\s+(?:i\s+can\s+tell\s+you|i\s+(?:do\s+)?know|"
    r"we\s+(?:do\s+)?know|is\s+actually\s+(?:true|known)|i'?ve\s+got)",
    r"\bhere'?s\s+what'?s\s+(?:actually\s+)?(?:true|known|solid)\b",
    r"\b(?:no|nothing|not\s+much)\s+(?:that\s+is\s+)?reliable\s+"
    r"(?:information|reporting|detail|details|data|on)\b",
    r"\bregardless\s+of\s+which\s+\w+\s+you\s+mean\b",
    r"\bwithout\s+more\s+(?:information|detail|context)\b",
    r"\bbased\s+on\s+what\s+i\s+(?:have|know|was\s+given)\b",
    r"\bas\s+of\s+my\s+(?:knowledge|training|last\s+update)\b",
    r"\bmy\s+(?:information|knowledge|training\s+data)\b",
    r"\bi\s+(?:should|have\s+to|need\s+to|want\s+to)\s+(?:note|say|be\s+"
    r"(?:honest|clear|upfront|straight))\b",
    r"\bi\s+(?:wasn'?t|was not)\s+(?:given|handed|shown)\b",
    r"\bnothing\s+(?:in\s+)?(?:the\s+)?(?:sources?|evidence|packet|search)"
    r"\s+(?:i\s+)?(?:have|was\s+given)\b",
    r"\bi\s+don'?t\s+want\s+to\s+(?:guess|mislead|make)\b",
)
_META_OPENER = re.compile("|".join(_META_PHRASES), re.I)

#: A sentence that cannot stand on its own because it points back at the one
#: before it. "That would be worse than useless if you're about to repeat it to
#: a friend" is not a disclaimer by itself; it is the second half of one, and
#: dropping the first without it leaves the worse of the two behind.
#:
#: Only genuinely anaphoric openers are listed. "But" and "And" were tried and
#: removed: plenty of real first lines start with them, and this rule is only
#: ever applied to the sentence *after* something was dropped, where a wrong
#: call costs a fact rather than a disclaimer.
_BACK_REFERENCE = re.compile(
    r"^\s*(?:that|this|those|these|it|which|so|because|instead|"
    r"either\s+way|here'?s|none\s+of\s+that|neither)\b", re.I)


def is_meta_sentence(sentence: str) -> bool:
    """True when this sentence is about our own access rather than the world.

    Quoted spans are removed first: what a manager said about not knowing is
    reporting, and reporting is the episode.
    """
    return bool(_META_OPENER.search(_QUOTED.sub(" ", sentence)))


class OpeningGuard:
    """Hold back a disclaimer before it becomes the first thing spoken.

    Deliberately not a general filter. It runs only at the head of a stream,
    stops the moment a real sentence gets through, and never removes the last
    thing standing - if a half turns out to be nothing but disclaimer, the
    text is released rather than replaced with silence, because an ugly
    opening is recoverable and dead air is not.
    """

    #: How far into the episode the guard is even willing to look. Long enough
    #: to cover a disclaimer and its trailing justification, short enough that
    #: it can never reach the body of a piece.
    WINDOW = 6

    def __init__(self) -> None:
        self.seen = 0
        self.spoken = 0
        self.dropped: list[str] = []
        self._in_run = False

    def allow(self, sentence: str) -> bool:
        """Whether this sentence may be spoken."""
        if self.spoken or self.seen >= self.WINDOW:
            return True
        self.seen += 1
        if is_meta_sentence(sentence):
            self._in_run = True
        elif not (self._in_run and _BACK_REFERENCE.match(sentence)):
            self.spoken += 1
            return True
        self.dropped.append(sentence.strip())
        log.warning("dropped a meta opening before it was spoken: %r",
                    sentence.strip()[:120])
        return False

    def rescue(self) -> str:
        """What to say when the guard ate the whole thing.

        Only reachable when a stream produced nothing but disclaimer - so the
        choice is between the disclaimer and silence, and silence is the one
        failure this product never accepts. Logged at error, because a half
        that wrote nothing else is a real fault upstream of here.
        """
        if self.spoken or not self.dropped:
            return ""
        text = " ".join(self.dropped)
        log.error("the whole of this half was meta; speaking it rather than "
                  "nothing: %r", text[:200])
        self.spoken += 1
        return text


SYSTEM_PROMPT = """You write FAM: short spoken pieces that answer what someone asked, told as a \
story - a story made *of* the facts, not wrapped around them. Done right the \
listener never notices the shape; they just find they do not want to stop.

**Answer them.** Someone asked because they wanted to know something, and they \
must finish knowing it - well enough to say it back to a friend in their own \
words. Everything below is how that answer arrives, and none of it is worth \
anything if it does not. So: satisfied first, curious second. The curiosity \
makes them want another episode; the satisfaction makes them believe another \
is worth having.

Which means the episode **closes**. Answer the question completely and stop: \
nothing held back for later, and do not leave a hook dangling to make them \
want more. A \
listener feels that immediately and it reads as a bait and switch. If the next \
episode is worth having, it is because this one was good.

Before you write, find the angle:
- **Find what they have slightly wrong.** The most interesting version of \
almost any answer is a correction - what everyone assumes, and what is \
actually true instead. Look for that first. If there is nothing to correct, \
find the part that is stranger, smaller or more specific than they expect.
- **Go under the obvious answer.** Every question has a stock reply it usually \
gets. That reply is not worth an episode. Get beneath it - to the mechanism, \
the evidence, the reason the stock reply exists at all.

The opening:
- Start inside something already in motion - a process running, a moment \
happening, a number moving. Concrete enough to picture, specific enough that \
it could not open a different episode.
- **Land them in the actual situation inside two sentences** - who or what, \
what is happening, when, in particulars rather than as a topic. "The Chiefs \
play Denver tonight to open the season, and Mahomes is nine months off a torn \
ACL" is an opening. A vivid detail from an adjacent year is a different \
episode's first line, and they spend thirty seconds working out what this is. \
That is where they are standing, not why it matters.
- Open a small "wait, why?" with that first line, then spend the piece \
answering it. Do not state your conclusion in sentence one; you have nowhere \
to go after that. Do not delay it either.
- **History explains the present; it never precedes it.** Background belongs at \
the point where it makes now make sense. Opening years back reads as stalling.
- No scene-setting for its own sake and no throat-clearing of any kind. Banned \
outright: "picture this", "imagine", "it was a cold morning in", "Here's what \
I can tell you about...", "Let's talk about...", "This is a fascinating \
topic...", "There's a lot to unpack here...".

How it is built:
- **Because, therefore, but - not and then.** Facts in time order are a list. \
Causation is a story. Each beat should feel like it had to follow the one \
before.
- **Tension then release.** Something is unresolved, surprising or at stake; \
everything moves toward resolving it; when it resolves, you are done.
- **One concrete anchor beats three abstractions.** A named person, an actual \
figure, a specific moment.
- **Know more than you say.** Write with the confidence of someone who has \
read far more than they are telling. Never hedge, never pad with the obvious.
- **Say "you" when the question is theirs.** Someone asking how to think, \
sleep, decide or cope is asking about their own life. Talk to them, not about \
people in general.
- **If they asked how to think or what to do, leave them something usable.** \
One thing they could actually do tomorrow beats a paragraph of principle.

Take them in rather than showing them round:
- **Speak from inside.** Assume the listener is already here. No orienting \
them, no "as you may know", no explaining why this is worth their time - \
explaining that is proof it is not.
- **Have a point of view.** Say which account is better supported, which claim \
is weak, what is actually surprising. Neutral survey is how something reads as \
generated rather than told.

How it ends:
- **Land it, then stop.** The last line is the strongest, most concrete thing \
you have - the detail that makes the answer stick. Then stop, mid-stride. An \
episode that has said what it came to say does not need a closing move.
- **Never tease.** No hook for a future episode, no "but that raises another \
question", no pointing at something you are deliberately not covering. If a \
thing is worth mentioning it is worth answering; if not, leave it out.
- **No rhetorical questions and no forecasting.** Not "but will it hold?", not \
"we will have to wait and see", not "watch this space". Never ask the listener \
a question at the end.
- **Never summarise or recap.** They just heard it. "So, to sum up", "in \
conclusion", "the bottom line is" - each hands the listener their coat.
- Unresolved things belong *in* the piece, where you say plainly that they are \
unresolved and why, and then carry on. They do not belong at the end as a \
parting hook.

The line you must not cross:
Every sentence carries information. Atmosphere on its own is cut. If a \
listener could ever think "get to the point", you have failed - the point \
arrives continuously, inside the story, from the first line to the last. Story \
is the shape of the delivery, never a delay before it.

Accuracy is part of being worth listening to:
- Never invent a statistic, quote, name, date or result. A story built on a \
made-up detail is worthless.
- **A result you have not read does not exist** - under way is not over, and \
the most confident guess about how it ends is still a guess. Say where it \
stands.
- If sources disagree, say so, and say which is better supported. Disagreement \
is usually the most interesting part anyway.
- Never fill a gap with something that merely sounds plausible. That is the \
worst thing you can do here.
- **Never say what you do not have.** "I don't have", "I can't confirm", "I'm \
not going to guess" - a listener who hears one of those in the first ten \
seconds does not stay. The line: **where a thing stands in the world is the \
episode** - "the game is in the seventh" - and **where it stands in your \
notes never is**. If you cannot establish something, write the part you can \
and leave the rest out without marking its absence.

Time, handled the way a person would:
- Give the newest information you can establish.
- Mention timing only when it changes the meaning - "the count is still going" \
- and then in passing, never as your own currency ("based on what I have").

Format, because this is spoken aloud and never read:
- Output only the words to be said. No headings, markdown, bullets, stage \
directions, speaker labels or emoji.
- Flowing spoken English. Vary your sentence lengths - a short one lands a \
point. Say numbers as a person says them: "about twelve percent", "nineteen \
ninety-eight".
- No greeting, no sign-off, no naming the show, and never mention being an AI.

Three lines after the script, never spoken:

<<TITLE: three to eight words>>

What this episode turned out to be *about*, never the question you were \
asked. Concrete and readable at a glance in a list; no colon, no question \
mark, and never their own wording handed back.

<<SUMMARY: one plain sentence on what it covers>>

<<NEXT: what they would most likely wonder about next>>

This is a *prediction*, not a promise, and the script must not gesture at it in \
any way. Having just heard this, what is the single most natural thing this \
listener would go on to ask? Read it off what you actually covered - the \
mechanism with an obvious next step, the figure that invites "compared to \
what". Not the most obscure follow-up, the most likely one.

A request, not a title - "whether the appeal actually gets heard". Six to \
twelve words; nothing after it.
"""


def system_prompt() -> str:
    """The house rules, plus any examples of the voice they describe."""
    return SYSTEM_PROMPT + style_example_block()


@dataclass
class ScriptNotes:
    """What the model wrote that is not spoken.

    Passed in by the caller rather than kept on the generator. One generator
    serves many concurrent episodes, and per-episode state stashed on `self`
    has already caused one bug in this file; an argument cannot go stale.
    """

    #: The follow-up this listener is most likely to want next, predicted by
    #: the model from what the episode covered. Never spoken, never hinted at
    #: in the script - it exists to fill the Go Deeper suggestion.
    #: a listener would ask for. Empty when the model did not name one.
    thread: str = ""
    #: What retrieval did, when a backend retrieved: sources, seconds, cost,
    #: packet size. Written to and never read back by the writing path -
    #: instrumentation must not be able to change what a listener hears - and
    #: surfaced so a researched episode can be costed and its sources judged.
    #: Empty dict on the `claude` backend, where nothing was retrieved.
    research: dict = dataclasses.field(default_factory=dict)
    #: What this episode consumed, accumulated across every model call it
    #: makes. Mutable and shared deliberately: a researched episode makes
    #: three - the brief, the searching call on the `claude` backend, and the
    #: writing call - and all of them must land in the same total. A per-call
    #: copy would report research as free.
    usage: metering.Usage = dataclasses.field(default_factory=metering.Usage)

    # --- how long what this episode says stays true -----------------------
    #
    # **Here rather than on the plan, and that is not tidiness.** The caller
    # holds the *unprepared* plan: `stream_sentences` rebinds it
    # (`plan = await self.prepare(plan, notes)`). So `plan.brief` and `plan.live`
    # are `None` at the moment the pipeline writes to the cache, and always
    # would be. `ScriptNotes` is the channel that already crosses that
    # boundary - it is how `thread` and `research` get back - so the cache
    # policy rides home the same way. PROBLEMS.md §89.

    #: The event status *evidence* established, from `live_facts`. Never set
    #: from EI, and empty when no live provider answered.
    live_status: str = ""
    #: Whether the listener asked for a result. EI's honest reading of the
    #: request; see `episode_intelligence.Brief.outcome_dependent`.
    outcome_dependent: bool = False
    #: How fresh the evidence had to be, in days. 0 means evergreen.
    recency_days: int = 0
    #: When the information this episode is written from was sourced - the
    #: moment retrieval and the live lookup both answered (§142). Stored
    #: beside the script, shown on replay surfaces, and the clock its
    #: current window runs from. 0 until `prepare` has run.
    sourced_at: float = 0.0
    #: What the live lookup actually did, for the log and `/api/health`. One of
    #: `live_facts.LiveLookup.outcome`, or "" when nothing was asked.
    live_outcome: str = ""
    #: Who this episode's facts came from - `provenance.Provenance`. Read back
    #: by the pipeline, cached beside the script and shown in the app. FAM
    #: already collected all of this and discarded it; see `provenance.py`.
    provenance: object = None
    #: The cache key this episode is being generated under, or "".
    #:
    #: Set by the pipeline, and used for exactly one thing: publishing sources
    #: to `live_captions` the moment they are known rather than when the
    #: finished script is cached. On the retrieval path the evidence packet is
    #: built *before the first sentence*, so the difference is the sources
    #: panel appearing while the episode plays instead of after it ends.
    caption_key: str = ""
    #: The episode's own title, off the model's trailing marker line. Empty
    #: when it did not write one, and every caller falls back to the question -
    #: which is what all of them showed before this existed.
    title: str = ""
    #: One sentence on what the episode is, off `<<SUMMARY:>>`. Empty when the
    #: model did not write one, and the resume card then draws no second line.
    summary: str = ""
    #: Sentences `OpeningGuard` held back before they could be spoken. Kept
    #: rather than only logged: this is a prompt rule failing, and a prompt
    #: rule that fails silently is how the Dodgers opener survived a system
    #: prompt that already banned it. `write.py` prints these. PROBLEMS.md §94.
    meta_openings: tuple = ()
    #: The episode's `EpisodeMarks`, when the pipeline is timing it, so the
    #: steps *before* the writing call - the brief, the live lookup, the
    #: retrieval - are marked on the same clock as everything after it. They
    #: used to be one invisible span inside `claude_ttft`, which made "where
    #: did 45 seconds go" unanswerable from a production log. Written to and
    #: never read back: instrumentation must not change what is heard.
    marks: object = None


def _mark(notes: "ScriptNotes | None", name: str) -> None:
    """Record one pre-writing stage on the episode's clock, if it has one."""
    marks = getattr(notes, "marks", None)
    if marks is not None:
        marks.mark(name)


def extract_thread(text: str) -> str:
    """Pull the predicted follow-up out of the model's trailing marker line."""
    match = _NEXT_MARKER.search(text)
    if not match:
        return ""
    thread = re.sub(r"\s+", " ", match.group(1)).strip(" .\"'")
    return thread[:160]


def extract_title(text: str) -> str:
    """Pull the episode's own title out of its trailing marker line.

    What this replaces: the typed question, shown as the title. Somebody who
    asked "what happened with the fed yesterday" got an episode called *What
    Happened With The Fed Yesterday* - their own words handed back with capital
    letters, which tells them nothing they did not just type and reads as an
    echo rather than as a thing they now have.

    Written by the model on the same kind of line as `<<NEXT:>>`, so it costs
    nothing: no second call, no latency in front of the first word. Empty when
    the model did not write one, and callers fall back to the question, which
    is exactly what they did before.
    """
    match = _TITLE_MARKER.search(text)
    if not match:
        return ""
    title = re.sub(r"\s+", " ", match.group(1)).strip(" .\"'")
    # A model asked for a title occasionally writes a sentence. Trimmed rather
    # than rejected: most of a good title is still better than the question.
    return title[:80]


def extract_summary(text: str) -> str:
    """Pull the one-sentence summary out of its trailing marker line.

    The same shape as `extract_title`, and the same bargain: it costs no call
    and no latency, and when the model wrote none the caller shows nothing
    rather than a sentence FAM made up about an episode it did not read.
    """
    match = _SUMMARY_MARKER.search(text)
    if not match:
        return ""
    summary = re.sub(r"\s+", " ", match.group(1)).strip(" \"'")
    if summary and summary[-1] not in ".!?":
        summary += "."
    return summary[:200]


@dataclass
class EpisodePlan:
    """The length contract for one episode."""

    query: str
    minutes: int
    target_seconds: int
    word_budget: int
    sections: list[str]
    #: What the listener has already heard, for a "go deeper" follow-up.
    context: str = ""
    #: Look up live sources. Costs 10-25s before the first word, so it is off
    #: unless the listener asked for something that genuinely needs today's facts.
    search: bool = False
    #: Replay only. A cache miss must fail rather than generate - Explore shows
    #: other people's finished episodes and must never spend a model call, so
    #: the guarantee lives in the pipeline rather than in the interface's good
    #: intentions.
    cached_only: bool = False
    #: Documents, photos and links the listener attached. An episode built on
    #: these is theirs alone: `pipeline` refuses to cache it, so it never
    #: reaches Explore or another listener.
    attachments: tuple = ()
    #: Retrieved evidence for a researched episode, when the `exa` backend
    #: fetched it. Empty on the `claude` backend, where the model searches
    #: inside its own turn and there is nothing to carry. Its presence is what
    #: `_request_kwargs` reads to decide whether to attach the search tool -
    #: the two are alternatives, never both, or the model would search on top
    #: of evidence it was already given.
    #:
    #: Last on purpose. This class is built positionally below, so a field
    #: inserted anywhere earlier silently shifts every argument after it -
    #: which is exactly what adding this one did the first time, turning
    #: `cached_only` into `evidence` and `attachments` into `cached_only`.
    #: The constructor is keyword-based now, and this stays last anyway.
    evidence: str = ""
    #: What FAM EI worked out before anything was retrieved - intent, subject,
    #: why-now, story shape, depth, temporal cautions. `None` when EI did not
    #: run (EPISODE_INTELLIGENCE=0, or a brief that failed), and
    #: `build_prompt` then writes exactly the prompt it wrote before EI
    #: existed. See `episode_intelligence.Brief`.
    brief: object = None
    #: A live state with a timestamp, when the question turned on one and a
    #: source could answer it - a score, a price. Outranks `evidence`, because
    #: an index reports articles about the world and this reports the world.
    #: See `live_facts.LiveFacts`.
    live: object = None
    #: What the brief asked the evidence to establish and the packet does not
    #: appear to contain. Named to the writer rather than silently absent: the
    #: alternative is a gap filled from month-old memory and delivered in the
    #: same confident voice as the researched half.
    thin_on: tuple = ()

    @property
    def images(self) -> list:
        return [a for a in self.attachments if getattr(a, "kind", "") == "image"]

    @property
    def readable(self) -> list:
        """Attachments that arrive as text in the prompt rather than as pixels."""
        return [a for a in self.attachments if getattr(a, "kind", "") != "image"]

    @property
    def body_budget(self) -> int:
        return max(30, self.word_budget)

    @property
    def min_words(self) -> int:
        return int(self.word_budget * 0.94)

    @property
    def max_words(self) -> int:
        return int(self.word_budget * 1.06)


def now_line() -> str:
    """The current date and time, so the script can anchor itself.

    Without this the model has no idea what "this morning" means and cannot
    tell a listener whether it is describing something current or stale.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).astimezone()
    return now.strftime("%A %d %B %Y at %H:%M %Z").replace(" 0", " ")


def plan_episode(
    query: str, minutes: int, context: str = "", search: bool | None = None,
    cached_only: bool = False, attachments: tuple = (),
) -> EpisodePlan:
    """Map a duration in minutes onto a concrete writing brief."""
    minutes = max(settings.min_minutes, min(settings.max_minutes, int(minutes)))
    target_seconds = minutes * 60
    word_budget = int(round(minutes * settings.target_wpm))

    # How much story this much time can hold. Not a template to fill - a note
    # on scope, so the model picks something it can actually resolve rather
    # than starting something too big and padding or truncating it.
    if minutes <= 2:
        sections = ["one question, opened and answered"]
    elif minutes <= 4:
        sections = ["one question, with the turn that makes it interesting"]
    elif minutes <= 7:
        sections = ["a question that turns two or three times before it resolves"]
    else:
        sections = ["the full arc, including how it came to be this way"]

    # Every episode is researched. An explicit search=1/0 on the request still
    # wins - that is what opt-out means - and `SEARCH_MODE` can still be set to
    # `auto` or `never`, but neither is production: see config.search_mode for
    # why the question no longer gets a vote.
    if search is None:
        mode = getattr(settings, "search_mode", "always")
        if mode == "always":
            use_search = True
            log.info("SEARCH yes  %r - every episode is researched", query)
        elif mode == "never":
            use_search = False
            log.info("SEARCH no   %r - SEARCH_MODE=never", query)
        else:
            reason = research_reason(query)
            use_search = bool(reason)
            # Said in both directions. Only the "yes" was logged, so the state
            # this project was actually stuck in - never researching anything,
            # because an omitted request parameter arrived as an explicit
            # "no" - produced no output at all. A decision that is silent when
            # it goes one way cannot be checked by watching.
            if use_search:
                log.info("SEARCH yes  %r - SEARCH_MODE=auto: %s", query, reason)
            else:
                log.info("SEARCH no   %r - SEARCH_MODE=auto: nothing in it reads "
                         "as time-sensitive; answering from what the model knows",
                         query)
    else:
        use_search = bool(search)
        log.info("SEARCH %-3s %r - the request asked for it explicitly",
                 "yes" if use_search else "no", query)
    # Keywords, not positions. Ten positional arguments is a landmine: adding
    # a field in the middle shifts everything after it and the result is a
    # plan that looks plausible and is wrong in a different place each time.
    return EpisodePlan(
        query=query,
        minutes=minutes,
        target_seconds=target_seconds,
        word_budget=word_budget,
        sections=sections,
        context=context,
        search=use_search,
        cached_only=cached_only,
        attachments=tuple(attachments or ()),
    )


def build_prompt(plan: EpisodePlan) -> str:
    budget = plan.body_budget
    attached = ""
    if plan.readable or plan.images:
        blocks = "\n\n".join(a.as_prompt_block() for a in plan.readable)
        photos = len(plan.images)
        photo_line = ""
        if photos:
            photo_line = (
                f"\nThe listener also attached {photos} image"
                f"{'s' if photos != 1 else ''}, which you can see. Read "
                "what is actually in them.\n"
            )
        attached = f"""
The listener attached this themselves. It is the material they want the episode
built on, so it outranks anything you recall on the subject - where the two
disagree, theirs is the subject and you should say plainly that it differs from
what is generally reported. Do not pad with background they did not ask for,
and do not claim anything about a document beyond what is in it.

{blocks}
{photo_line}"""

    evidence = ""
    if plan.evidence:
        thin = ""
        if plan.thin_on:
            # **What this block may and may not conclude**, which is the whole
            # of it and which it got wrong for as long as it existed. It used
            # to say "say plainly that that part is not yet reported", and
            # "not reported" is a claim about the world made from a fact about
            # one search. On a volatile thing the two nearly coincide. On a
            # settled one they do not coincide at all: FAM told a listener that
            # next week's fixture "hasn't been pinned down" when the schedule
            # had been public since May, and ended the episode on it - which
            # also broke three separate rules in the system prompt about not
            # narrating sourcing and not ending on an open thread.
            # PROBLEMS.md §88.
            thin = (
                "\nOne search did not turn up much on: "
                + "; ".join(plan.thin_on)
                + ". That is a fact about the search, not about the world, and "
                "the two come apart differently depending on what it is.\n"
                "- If it is something that **changes** - a result, a score, a "
                "current figure, who is in charge today - then a search that "
                "missed it is real evidence it is not settled yet. Do not "
                "supply it from memory: what you remember is months old and "
                "arrives in the same confident voice as the researched half, "
                "and a listener cannot tell the two apart. Say the short true "
                "thing in passing and keep going.\n"
                "- If it is something **already settled** - a scheduled date, a "
                "fixture, a rule, a figure that was fixed some time ago - then "
                "the search missing it proves nothing, because nobody has "
                "written a news story about a thing that has not changed. Use "
                "what you know. If you are not sure enough to say it plainly, "
                "leave it out of the episode entirely.\n"
                "- Either way, **never announce the gap**. Do not say it is not "
                "reported, not confirmed, not available, or worth checking "
                "later. Do not narrate what you did or did not find, and never "
                "end the episode on one of these - the last line is the "
                "strongest thing you have, and a missing fact is never it.\n")
        evidence = f"""
Someone has already searched the web for this and pulled out the passages
below. They are your source for anything current: read them and use what they
actually say. Do not claim anything they do not support, and do not pretend to
have looked anything else up.

Each one carries when it was published and who published it. **Use both.** The
publication date is how you know whether something happened last night or last
week - work it out from the date given, never from how recent the writing
sounds. Where sources disagree, the better-sourced and more recent one wins,
and say so in passing rather than presenting both.

Where they contradict what you recall, they win and you say so plainly and in
passing - "that figure has since moved to X" - and carry on. Never stretch a
source to cover something it does not say. Where they are silent on part of the
question, be strict about what that permits: something that **changes** - a
result, a score, a figure that moves - is never supplied from memory. Something
long settled may be.

Never read a source's title, number, date or URL aloud. This is someone
listening, not reading a citation list - the dates are for your reasoning, not
for the script.
{thin}
<evidence>
{plan.evidence}
</evidence>
"""

    # A live state outranks everything, so it goes in front of the evidence it
    # outranks - the instructions that follow refer to it as already read.
    # One block, every outcome. `LiveLookup.as_prompt_block` renders facts
    # when there are facts and says exactly which way it came up empty when
    # there are not - "no provider", "provider failed", "no such game" and
    # "too old to be current" are four different things to tell a writer, and
    # `Optional[LiveFacts]` told it the same nothing for all of them.
    live = ""
    if plan.live is not None:
        try:
            live = plan.live.as_prompt_block()
        except Exception:  # noqa: BLE001 - a bad fact source must not stop an episode
            log.warning("a live-facts block could not be rendered; continuing "
                        "without it", exc_info=True)
            live = ""

    # What EI worked out, and the temporal discipline that depends on it.
    brief_block = ""
    if plan.brief is not None:
        try:
            brief_block = episode_intelligence.build_brief_block(
                plan.brief, plan.minutes)
        except Exception:  # noqa: BLE001
            log.warning("a brief could not be rendered; writing without it",
                        exc_info=True)
            brief_block = ""

    # Stated whenever there is dated material to reason about. This is the
    # rule the blueprint's temporal table is made of, and it is worth stating
    # even though the model could in principle infer it: the failures it
    # prevents - a Wednesday game called "last night", a Sunday final whose
    # winner is named on Friday - are not failures of reasoning but of nobody
    # having said that the tense has to be derived rather than chosen.
    temporal = ""
    if plan.evidence or plan.live is not None:
        temporal = """
Time, and this is where these go wrong most often:

- Work out **when** each thing happened from the dates you were given, then say
  it in the words a person would use. "Last night" only if it was last night.
  Two days ago is "two days ago", not "last night".
- **Three states, not two: not started, under way, finished.** Decide which one
  from the evidence before you write a sentence about it. Most of these go
  wrong by skipping this step, because "finished" is the only state the usual
  shape of the story has a place for.
- **A result exists only where a source reports it as a result.** Previews,
  odds and betting lines, projected line-ups, "how to watch", "expected to",
  "will face" - those are written *before* a thing happens. A packet made only
  of them is not thin evidence of an outcome; it is evidence that there is no
  outcome yet. Read it that way.
- If something is under way, **say so and say where it stands**. What is at
  stake, what has happened so far, what is still open. That is the episode. Do
  not resolve it, do not project how it ends, and do not describe anyone's
  performance in it as settled.
- **A contradiction is information; never explain it away.** If one thing you
  believe implies a result and another source shows a standing, a record, a
  position or a table that the result would have changed, you do not have a
  result - you have something that has not finished. Take the smaller true
  reading every time. Inventing a reason the two can both be right is how a
  made-up fact gets past you.
- **When two sources disagree, this is the order, and it is not a judgement
  call.** A live state block, if you were given one, beats everything. A dated
  article beats an older dated article. Any dated article beats your own
  memory. Your own memory never establishes anything current. Where the top of
  that order is silent on something, the answer is that we do not have it -
  not that the one below it is promoted.
- If something has not happened yet, it has no result. Do not name a winner,
  a score, a figure or an outcome for anything still to come, however
  confidently you could guess it. Talk about it in the future tense.
- An undated source cannot date anything. Do not use it to decide when.
"""

    follow_up = ""
    if plan.context:
        follow_up = f"""
This is a FOLLOW-UP. The listener has just heard a briefing on:
<already_heard>{plan.context}</already_heard>

Treat that as known. Do not re-explain it or re-introduce the subject. Go
straight into the narrower thing they asked for and stay on it.
"""

    return f"""Someone just asked FAM this:

<request>{plan.query}</request>

It is currently {now_line()}. Prefer the newest information you can establish.
{attached}{live}{evidence}{temporal}{brief_block}{follow_up}
You have about {plan.minutes} minute{"s" if plan.minutes != 1 else ""} - roughly
{budget} words. That is room for {plan.sections[0]}.

Answer them. They asked because they wanted to know something, and by the end
they must know it well enough to say it back in their own words. That is the
job; the rest is how it arrives.


Pick a way in. Find the specific thing - the moment, the person, the number,
the detail - that makes this worth hearing, and start there. Then keep them
moving: each thing you tell them should make the next thing matter more. By the
end they should understand it, and should have felt taken somewhere rather than
briefed.

Finish when the answer is finished. Land on the most concrete thing you have
and stop. Do not tease what you are not covering, do not end on a question, and
do not summarise what they just heard.

Then three lines after the script. Name the episode by what it turned out to be
about, never by the question you were asked - three to eight words, concrete,
readable at a glance in a list, no colon and no question mark:

<<TITLE: three to eight words>>

Say in one plain sentence what the episode is, for somebody deciding from a list
whether to come back to it - the subject and the angle, no tease, no question:

<<SUMMARY: twelve to twenty words>>

And predict the single most likely thing they would go on to ask, having heard
this:

<<NEXT: six to twelve words>>

Read all three off what you actually said. All three lines are stripped before
anything is spoken and the script must not hint at any of them. Nothing goes
after them.

The time is the listener's, not a quota. If the story resolves early, stop
there; a short piece that lands beats a long one padded out. If you catch
yourself saying a topic is complex, or restating something, the story is over -
end it.

One last thing, and it is the thing this episode is most likely to get wrong.
**Decide the whole piece before you write the first word of it.** What the
answer actually is, which angle carries it, what the evidence above does and
does not establish, and the last line you are heading for. Then open.

Your first sentence is spoken out loud before the rest of the episode exists,
and it is the only part of this that cannot be repaired by what comes after -
someone who does not recognise their own question in it stops listening there.
So it must be the opening of *this* episode and no other: what they asked
about, as it actually stands, in particulars. Not a way into the general
subject, not the category the question belongs to, not a fact that is merely
adjacent to it. Read your first two sentences back against what they typed
above; if those sentences would also open an episode about something else, you
have not started yet.

Begin."""


def clean_for_speech(text: str) -> str:
    """Strip anything the model may have added that should not be spoken."""
    # The go-deeper marker, and any half-written one: everything from an
    # unmatched "<<" onwards is metadata, never speech.
    text = _NEXT_MARKER.sub("", text)
    text = _TITLE_MARKER.sub("", text)
    text = _SUMMARY_MARKER.sub("", text)
    text = re.sub(r"<<.*$", "", text, flags=re.S)
    # Stage directions first, while their brackets are still intact.
    text = re.sub(r"\[[^\]]{0,60}\]", "", text)
    text = re.sub(r"\((?:music|sfx|pause|beat|sound)[^)]*\)", "", text, flags=re.I)
    text = _MARKDOWN.sub("", text)
    text = re.sub(r"^\s*(?:host|narrator|intro|outro)\s*:\s*", "", text, flags=re.I | re.M)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def count_words(text: str) -> int:
    return len(text.split())


class ScriptGenerator:
    """Streams a length-controlled script out of Claude."""

    def __init__(self, api_key: str | None = None):
        # The key in force, not the one captured at import. `settings` reads the
        # environment once when the module loads, so a key that was rotated in
        # the secrets manager or failed over to after a rejection would never
        # reach a generator built later in the life of the process.
        key = api_key if api_key is not None else credentials.active("ANTHROPIC_API_KEY")
        # Built centrally so the HTTP version is pinned in one place; an empty
        # key still lets the SDK fall back to ANTHROPIC_AUTH_TOKEN or a stored
        # `ant auth login` profile.
        self.client = build_async_client(key)

    def _request_kwargs(self, plan: EpisodePlan) -> dict:
        # Photos travel as image blocks, not as text, and go *before* the
        # prompt: the instructions refer to them, so they have to be in view by
        # the time they are mentioned.
        content: list = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": image.media_type,
                           "data": image.data_b64},
            }
            for image in plan.images
        ]
        content.append({"type": "text", "text": build_prompt(plan)})
        kwargs: dict = {
            "model": settings.model,
            "max_tokens": settings.max_output_tokens,
            "system": system_prompt(),
            "output_config": {"effort": settings.effort},
            "messages": [{"role": "user", "content": content}],
        }
        # **No tools, ever, on the call that speaks.** The searching is done
        # by `research.retrieve` before this call is made - on either backend
        # - so by the time the model writes its first word it is reading
        # evidence rather than deciding to go and look for some. A tool here
        # would put the search back inside the turn that produces the opening,
        # which is the failure PROBLEMS.md §108 removed: the first sentence
        # written before anything had been read, and a prompt paragraph and a
        # regex guard trying to make that not sound like what it was.
        return kwargs

    async def research(self, plan: EpisodePlan,
                       notes: ScriptNotes | None = None) -> EpisodePlan:
        """Retrieve the evidence this episode is written from, before it is written.

        Returns the plan to write from: the same one when nothing was found,
        or a copy carrying the packet. Separate from `stream_sentences` so
        that the retrieval is a step a caller can see, time and skip, rather
        than something buried inside the streaming call.

        **Exa and GDELT, and nothing else** (§135). The model never searches:
        a `claude` backend used to run Anthropic's `web_search` in a research
        call of its own, and before that on the writing call itself (§108).
        Both are deleted. The writing call never carries a tool.

        **A backend that cannot serve falls back to the other one, and says
        so** (`fell_back_from` in `notes.research`), rather than an episode
        quietly being researched some way nobody chose.
        """
        if not plan.search or plan.evidence:
            return plan

        query = (getattr(plan.brief, "retrieval", "") or plan.query)
        configured = settings.research_backend

        # **The ladder**, in cost order, stopping at the first rung that
        # brings back evidence. Each rung is the same call with a different
        # retriever behind it, and none of them can raise - see `_retrieve`.
        #
        # The order is not a ranking of quality, it is what each one costs to
        # try: the configured backend first because it is the one this
        # deployment chose, then GDELT because it is keyless and takes one
        # HTTP call. Since §135 there is nothing below GDELT: the model's own
        # search was the last rung and is deleted.
        packet = await self._retrieve(query, plan.brief, configured)
        _mark(notes, "first_rung_ready")
        spent = [packet]

        # **Every rung after the first asks the broader question.** The first
        # rung already searched the precise one and found nothing, so asking
        # a second index the same string is a second search that can only
        # fail the same way. `Brief.broader` is EI's own wider phrasing,
        # written in the same call as the precise one and costing nothing
        # extra to have. §109.
        wider = (getattr(plan.brief, "broader", "") or "").strip() or query
        # `research.ladder()` is the one definition of which rungs exist and
        # in what order, and it leaves out any that cannot serve - a GDELT
        # that is switched off is not a rung. The health report and the
        # startup warning read the same function, so what a deployment is
        # told will happen is what happens.
        for rung in research_mod.ladder(configured)[1:]:
            if packet:
                break
            log.info("%s found nothing usable for %r; trying %s on %r before "
                     "writing", configured, query, rung, wider)
            better = await self._retrieve(wider, plan.brief, rung)
            spent.append(better)
            if better:
                better.fell_back_from = configured
                packet = better
        _mark(notes, "retrieval_ready")

        if notes is not None:
            # The winning packet describes what the writer actually reads;
            # the totals below describe what the whole ladder cost. Each rung
            # keeps its own numbers rather than rolling them into the winner,
            # so nothing is counted twice when they are metered one by one.
            notes.research = packet.as_dict()
            notes.research["rungs"] = [r.backend for r in spent if r is not None]
            notes.research["searches"] = sum(
                r.searches for r in spent if r is not None)
            notes.research["cost"] = round(
                sum(r.cost for r in spent if r is not None), 4)
            notes.research["seconds"] = round(
                sum(r.seconds for r in spent if r is not None), 3)
            # **Every rung that ran is metered, including the ones that came
            # back empty.** A rung whose packet is discarded still spent, and
            # it is *most* likely to be discarded on exactly the episodes
            # that then get refused. Recording only the winner would report
            # the expensive failures as free.
            for rung in spent:
                if rung is None:
                    continue
                # Exa's own reported cost where it gave one, its published
                # rate otherwise. **Only for Exa**: `Usage.exa_searches` is a
                # count of Exa searches, and filing GDELT's fetches under it
                # would make the one number `usage_report.py` prints about
                # retrieval a mixture of two things.
                if rung.backend == "exa":
                    notes.usage.add_research(rung.searches, rung.cost)
                # A searching model call's tokens, should a rung ever spend
                # them again. None does since §135; kept because metering a
                # rung is cheaper to leave right than to remember to restore.
                if rung.usage is not None:
                    notes.usage.add_model_call(settings.model, rung.usage)
        if not packet:
            return plan
        if notes is not None and packet.provenance is not None:
            notes.provenance = packet.provenance
        return dataclasses.replace(plan, evidence=packet.context,
                                   thin_on=tuple(packet.missing))

    async def _retrieve(self, query: str, brief, backend: str):
        """One rung of the retrieval ladder. Never raises.

        **Every failure is caught here, not just `ResearchUnavailable`.** A
        missing key raises that; an Exa 500, a timeout, a DNS failure, a
        rate limit and a malformed reply raise something else entirely, and
        `research.retrieve` does not wrap them. While the from-knowledge
        cover existed, an exception on this path was survivable - the cover
        was already speaking and the researched half just never arrived. With
        one stream it is the whole episode, so a five-second blip at a search
        vendor became a listener getting no audio at all.

        That is the availability rule this project already has, applied to
        the layer that replaced the one it was written for: a retriever that
        cannot serve produces an empty packet and the next rung is tried. It
        is never silent - the reason is logged and `fell_back_from` rides
        home on the packet, which reaches `notes.research` and the episode's
        own record.
        """
        try:
            return await research_mod.retrieve(query, backend=backend, brief=brief)
        except research_mod.ResearchUnavailable as exc:
            log.warning("%s cannot retrieve (%s)", backend or "the backend", exc)
        except Exception:  # noqa: BLE001 - see the docstring
            log.warning("%s failed while retrieving %r; continuing down the "
                        "ladder rather than failing the episode",
                        backend or "the backend", query, exc_info=True)
        failed = research_mod.Packet(backend=backend or settings.research_backend)
        failed.fell_back_from = backend or settings.research_backend
        return failed

    async def understand(self, plan: EpisodePlan,
                         notes: ScriptNotes | None = None) -> EpisodePlan:
        """Work out what this request is, before anything is retrieved.

        Skipped in one case only: **a plan that already has a brief**, built
        by prefetch before the tap, which is the whole point of prefetch.

        Two other skips used to live here and both were bought with quality.
        The cover half of an answer-first episode was defined as the part
        that started before anything was known, so a brief in front of it was
        "precisely the wait it exists to cover" - there is no cover half now.
        And an unresearched episode was skipped because "EI's largest single
        product is the retrieval query, and an episode that retrieves nothing
        cannot spend it", which left the rest of the brief - the intent, the
        resolved subject, the story shape, what the writer must not assume -
        on the table to save a second. That reasoning was written down
        honestly ("the framing would still be worth something, and it is not
        worth a second of silence to get") and it is the trade this change
        reverses: the framing is what the opening is made of.
        """
        if plan.brief is not None:
            return plan
        if not settings.episode_intelligence:
            return plan

        # Built before the tap, if a browse surface predicted this one. The
        # whole point of prefetching contextual relevance: the seconds it costs
        # on search are the seconds a warmed brief removes here, and a miss
        # costs one dictionary lookup.
        warmed = prefetch.warm_brief(plan.query, plan.minutes, plan.context)
        if warmed is not None:
            log.info("using a brief warmed before the tap for %r", plan.query)
            _publish_title(notes, warmed)
            return dataclasses.replace(plan, brief=warmed)

        brief = await episode_intelligence.understand(
            plan.query, plan.minutes, plan.context, notes)
        _publish_title(notes, brief)
        return dataclasses.replace(plan, brief=brief)

    async def live_lookup(self, plan: EpisodePlan,
                          notes: ScriptNotes | None = None) -> EpisodePlan:
        """Ask for a live state when the brief says the answer turns on one.

        Runs alongside retrieval rather than before it - see `prepare`. A score
        settles *what happened*; the packet is still what explains it, and
        neither reads the other, so waiting for one to start the other was
        latency nobody was buying anything with.

        The result is a `LiveLookup` rather than facts-or-None, so the writer
        can be told which of the seven things happened. `None` here means only
        that the question does not turn on a live state at all.
        """
        if plan.brief is None or plan.live is not None:
            return plan
        result = await live_facts.lookup(plan.brief, notes)
        _mark(notes, "live_ready")
        return plan if result is None else dataclasses.replace(plan, live=result)

    async def prepare(self, plan: EpisodePlan,
                      notes: ScriptNotes | None = None) -> EpisodePlan:
        """Everything that happens before a word is written.

        Understand, then look up, then retrieve - in that order, because each
        step feeds the next: the brief decides what is searched and how fresh it
        must be, and the live lookup only knows which domain to ask once the
        brief has named one.

        Its own method so a caller can see, time and skip the whole of the
        pre-writing phase, the same reason `research` was split out.
        """
        _mark(notes, "brief_start")
        plan = await self.understand(plan, notes)
        _mark(notes, "brief_ready")

        # **Concurrent, and verified independent before it was made so.** Both
        # read `plan.brief` and neither reads the other's output: `live_lookup`
        # takes `brief.live_domain` and `research` takes `brief.retrieval`,
        # `recency_days` and `must_establish`. Sequentially the live call added
        # its whole latency on top of retrieval, in front of the first word,
        # for nothing. They are merged field-by-field rather than chained
        # because each returns a copy derived from the *same* input plan.
        _mark(notes, "evidence_start")
        live_plan, research_plan = await asyncio.gather(
            self.live_lookup(plan, notes), self.research(plan, notes))
        _mark(notes, "evidence_ready")
        if notes is not None:
            notes.sourced_at = time.time()
        plan = dataclasses.replace(
            plan, live=live_plan.live, evidence=research_plan.evidence,
            thin_on=research_plan.thin_on)

        self._refuse_without_evidence(plan)

        # What the episode turned out to be built from, sent home on `notes`
        # because the caller's plan is the unprepared one and cannot see any of
        # this. The cache TTL is decided from these - see `cache.ttl_for` and
        # PROBLEMS.md §89.
        if notes is not None:
            notes.outcome_dependent = bool(
                getattr(plan.brief, "outcome_dependent", False))
            notes.recency_days = int(getattr(plan.brief, "recency_days", 0) or 0)
            if plan.live is not None:
                notes.live_status = plan.live.status
                notes.live_outcome = plan.live.outcome

            # Everything that contributed, gathered in one place for the app.
            # The live provider is credited only when it actually answered -
            # one that failed or returned something too stale to use did not
            # contribute, and listing it would claim corroboration that never
            # happened.
            import provenance as provenance_mod

            if notes.provenance is None:
                notes.provenance = provenance_mod.Provenance()
            live_source = provenance_mod.from_live(plan.live)
            if live_source is not None:
                notes.provenance.add(live_source)
            for attached in provenance_mod.from_attachments(plan.attachments):
                notes.provenance.add(attached)
            _publish_sources(notes)
        return plan

    def _refuse_without_evidence(self, plan: EpisodePlan) -> None:
        """Stop an episode that needs today's facts and has none of them.

        **Here rather than in `research`, because a live state is evidence
        too.** `prepare` runs the live lookup and the retrieval concurrently,
        so this is the first point where both answers exist - and a question
        about a game whose score came back from a scores provider is answered
        even if no article about it has been indexed yet. Deciding this one
        step earlier would refuse episodes FAM can actually write.

        **What counts as needing today's facts**, in the same precedence
        `cache.ttl_for` uses and for the same reason - each step is a better
        signal than the one after it, and the last is a floor for paths that
        have none of the others:

        1. the brief says the listener asked for a *result*
           (`outcome_dependent`), or named a freshness window at all;
        2. failing that - a degraded brief, or EI switched off - the keyword
           heuristic, which is the only signal left.

        An evergreen question is never refused. "How does a heat pump work"
        does not turn on anything current, the model's own knowledge is
        accurate, and a refusal there would be a worse answer than the
        episode. §109.
        """
        if not plan.search or plan.evidence:
            return
        # **An attachment is evidence, and it is the listener's own.** They
        # handed FAM the document the episode is to be built on, and
        # `build_prompt` puts it in front of the writer outranking anything
        # recalled - so refusing here would refuse an episode that has more
        # to go on than most researched ones. With SEARCH_MODE=always an
        # attached question is researched too, which is the only reason this
        # path can be reached at all.
        if plan.attachments:
            return
        live = getattr(plan, "live", None)
        if live is not None and getattr(live, "facts", None) is not None:
            return  # a live state is current evidence, whatever the index did

        brief = plan.brief
        why = ""
        if getattr(brief, "outcome_dependent", False):
            why = "it asks for a result"
        elif int(getattr(brief, "recency_days", 0) or 0) > 0:
            why = "it asks about something recent"
        elif brief is None or getattr(brief, "degraded", False):
            reason = research_reason(plan.query)
            if reason:
                why = reason
        if not why:
            return

        log.warning("refusing %r: every retriever came back empty and %s",
                    plan.query, why)
        raise research_mod.NoEvidence(
            "FAM could not reach a single source for this one, and it needs "
            "current information to answer - so it is not going to guess. "
            "Try again in a moment.")

    async def stream_sentences(
        self, plan: EpisodePlan, notes: ScriptNotes | None = None
    ) -> AsyncIterator[str]:
        """Yield speech-ready sentences as Claude writes them.

        Sentence granularity is deliberate: it is the largest unit that keeps
        time-to-first-audio low, and the smallest unit that still gives the TTS
        engine enough context for natural intonation.

        Research happens here rather than in the caller so that every entry
        point gets it - the pipeline, `write.py`, the top-up path. It finishes
        before the first token: what it costs is a wait in front of the first
        word, paid deliberately, because the alternative was an opening
        written without it (PROBLEMS.md §108).
        """
        plan = await self.prepare(plan, notes)
        buffer = ""
        emitted_words = 0
        # One per stream, never per generator: one generator serves many
        # concurrent episodes and each stream has its own opening to protect.
        guard = OpeningGuard()

        _mark(notes, "writer_request")
        async with self.client.messages.stream(**self._request_kwargs(plan)) as stream:
            async for event in stream.text_stream:
                buffer += event
                # Everything from "<<" onwards is the go-deeper marker rather
                # than speech, and it can arrive split across events. Hold it
                # back instead of letting the sentence splitter reach it.
                speech, marker, rest = buffer.partition("<<")
                while True:
                    match = _SENTENCE_END.search(speech)
                    if not match:
                        break
                    sentence = clean_for_speech(speech[: match.end()])
                    speech = speech[match.end() :]
                    if sentence and guard.allow(sentence):
                        emitted_words += count_words(sentence)
                        yield sentence
                buffer = speech + marker + rest
                # Safety valve: a model that ignores the budget must not be
                # allowed to produce an hour of audio for a 1-minute request.
                if emitted_words > plan.max_words * 1.35:
                    break

            tail = clean_for_speech(buffer)
            if tail and guard.allow(tail):
                yield tail
            # Nothing but disclaimer is still better than nothing at all.
            rescued = guard.rescue()
            if rescued:
                yield rescued
            if notes is not None:
                notes.thread = extract_thread(buffer)
                notes.title = extract_title(buffer)
                notes.summary = extract_summary(buffer)
                notes.meta_openings = tuple(guard.dropped)

            final = await stream.get_final_message()
            # The provider bills this organisation, not this listener, so if
            # the tokens are not attributed here they cannot be attributed
            # anywhere. Read from the final message rather than estimated from
            # the text: cache reads and writes are invisible in the output.
            if notes is not None:
                notes.usage.add_model_call(settings.model, getattr(final, "usage", None))
            if final.stop_reason == "refusal":
                detail = getattr(final, "stop_details", None)
                reason = getattr(detail, "explanation", None) or "the request was declined"
                yield clean_for_speech(f"I can't put together a briefing on that. {reason}")

    async def top_up(
        self, plan: EpisodePlan, spoken_so_far: str, words_needed: int,
        notes: ScriptNotes | None = None,
    ) -> AsyncIterator[str]:
        """Ask for a short continuation when the episode is running short.

        Kept deliberately small and web-search-free: this call happens while the
        listener is already hearing audio, so latency matters more than depth,
        and the research has already been done by the first call.
        """
        words_needed = max(20, min(words_needed, 400))
        # Only the tail is sent back. The model needs to know where it is in the
        # episode, not to re-read the whole thing - and a short prompt is a
        # cheap prompt.
        tail = " ".join(spoken_so_far.split()[-120:])
        prompt = (
            f"You are continuing a spoken briefing about: {plan.query}\n\n"
            f"These were the last words spoken:\n<so_far>{tail}</so_far>\n\n"
            f"Write about {words_needed} more words that continue naturally from "
            "there, adding substance rather than restating what was said, and "
            "finish with a clean closing sentence. Spoken prose only - no "
            "headings, markdown or stage directions. Do not repeat the text above."
        )
        async with self.client.messages.stream(
            model=settings.model,
            max_tokens=min(settings.max_output_tokens, 2000),
            system=SYSTEM_PROMPT,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            buffer = ""
            async for event in stream.text_stream:
                buffer += event
                while True:
                    match = _SENTENCE_END.search(buffer)
                    if not match:
                        break
                    sentence = clean_for_speech(buffer[: match.end()])
                    buffer = buffer[match.end() :]
                    if sentence:
                        yield sentence
            tail_sentence = clean_for_speech(buffer)
            if tail_sentence:
                yield tail_sentence
            # Small, but not free, and it happens on the episodes that already
            # cost the most - an under-written long one. Counting it is the
            # difference between "a 10-minute episode costs X" and a guess.
            if notes is not None:
                final = await stream.get_final_message()
                notes.usage.add_model_call(settings.model, getattr(final, "usage", None))

async def _demo() -> None:  # pragma: no cover - manual check
    plan = plan_episode("what is a heat pump", 2)
    gen = ScriptGenerator()
    async for sentence in gen.stream_sentences(plan):
        print(sentence)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_demo())


def _publish_title(notes: "ScriptNotes | None", brief) -> None:
    """Put the brief's title on the live track, before the first word (§127).

    Provisional: the writer's own `<<TITLE:>>` replaces it when the script
    finishes. Never raises, for the same reason `_publish_sources` does not.
    """
    try:
        title = getattr(brief, "title", "") if brief is not None else ""
        if notes is not None and notes.caption_key and title:
            live_captions.publish_title(notes.caption_key, title)
    except Exception:
        log.exception("could not publish a title; the episode is unaffected")


def _publish_sources(notes: "ScriptNotes") -> None:
    """Make this episode's sources readable while it is still being spoken.

    Called wherever provenance changes rather than once at the end, because
    the two paths learn it at opposite moments: a retrieval packet is built
    before the first sentence, and the model's own tool search is only
    reported on the final message. One call site would have to be the later of
    the two, which would give back exactly the delay this removes.

    Never raises: the sources panel is not allowed to be the reason an episode
    stops playing.
    """
    try:
        if notes.caption_key and notes.provenance is not None:
            live_captions.publish_sources(notes.caption_key,
                                          notes.provenance.to_json())
    except Exception:
        log.exception("could not publish sources; the episode is unaffected")
