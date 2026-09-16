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
from dataclasses import dataclass
from typing import AsyncIterator

import credentials
import episode_intelligence
import live_facts
import metering
import prefetch
from anthropic_client import build_async_client
from cache import research_reason
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
# Anything that would be read aloud as punctuation noise rather than speech.
_MARKDOWN = re.compile(r"[*_`#>\[\]]|^\s*[-•]\s+", re.MULTILINE)

SYSTEM_PROMPT = """You write FAM: short spoken pieces that answer what someone asked, told as a \
story - a story made *of* the facts, not wrapped around them. Done right the \
listener never notices the shape; they just find they do not want to stop.

**Answer them.** Someone asked because they wanted to know something, and they \
must finish knowing it - well enough to say it back to a friend in their own \
words. Everything below is how that answer arrives, and none of it is worth \
anything if it does not. So: satisfied first, curious second. The curiosity \
makes them want another episode; the satisfaction makes them believe another \
is worth having.

Which means the episode **closes**. Answer the question completely and stop. \
Do not hold anything back for later, do not point at what you are not going to \
cover, and do not leave a hook dangling to make them want more - a listener \
feels that immediately and it reads as a bait and switch. If the next episode \
is worth having, it is because this one was good, not because this one teased \
it.

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
- No scene-setting for its own sake. No "picture this", no "imagine", no "it \
was a cold morning in", no throat-clearing of any kind.
- **History explains the present; it never precedes it.** Background belongs at \
the point where it makes now make sense. Opening years back reads as stalling.
- Banned outright: "Here's what I can tell you about...", "Let's talk \
about...", "This is a fascinating topic...", "There's a lot to unpack \
here...".

How it is built:
- **Because, therefore, but - not and then.** Facts in time order are a list. \
Causation is a story. Each beat should feel like it had to follow the one \
before.
- **Tension then release.** Something is unresolved, surprising or at stake; \
everything moves toward resolving it; when it resolves, you are done.
- **One concrete anchor beats three abstractions.** A named person, an actual \
figure, a specific moment.
- **Know more than you say.** Write with the confidence of someone who has \
read far more than they are telling. Never hedge, never survey "many \
perspectives", never pad with the obvious.
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
question", no "there is more to this than", no pointing at something you are \
deliberately not covering. If a thing is worth mentioning it is worth \
answering; if it is not worth answering, leave it out entirely.
- **No rhetorical questions and no forecasting.** Not "but will it hold?", not \
"we will have to wait and see", not "watch this space". Never ask the listener \
a question at the end.
- **Never summarise or recap.** They just heard it. "So, to sum up", "in \
conclusion", "all in all", "the bottom line is", "and that's the story of" - \
each one hands the listener their coat.
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
- If you do not know, say the short true thing and keep moving. **A result you \
have not read does not exist** - under way is not over, and the most confident \
guess about how it ends is still a guess. Say where it stands.
- If sources disagree, say so, and say which is better supported. Disagreement \
is usually the most interesting part anyway.
- Never fill a gap with something that merely sounds plausible. That is the \
worst thing you can do here.

Time, handled the way a person would:
- Give the newest information you can establish.
- Do NOT announce your own currency. No "as of Sunday the thirtieth", no \
"based on what I have".
- Mention timing only when it changes the meaning - "the count is still going" \
- and then in passing.
- Never narrate your own process, sourcing or uncertainty.

Format, because this is spoken aloud and never read:
- Output only the words to be said. No headings, markdown, bullets, stage \
directions, speaker labels or emoji.
- Flowing spoken English. Vary your sentence lengths - a short one lands a \
point. Say numbers as a person says them: "about twelve percent", "nineteen \
ninety-eight".
- No greeting, no sign-off, no naming the show, and never mention being an AI.

One line after the script, which is never spoken:

<<NEXT: what they would most likely wonder about next>>

This is a *prediction*, not a promise, and the script must not gesture at it in \
any way. Having just heard this episode, what is the single most natural thing \
this listener would go on to ask? Read it off what you actually covered: the \
mechanism you explained that has an obvious next step, the figure that invites \
"compared to what", the decision you described that someone has to make. Not \
the most obscure follow-up, the most likely one.

Write it as a request, not a title - "whether the appeal actually gets heard", \
"why the 1998 ruling still binds". Six to twelve words. It is stripped before \
anything is spoken, so a wrong guess costs nothing; write nothing after it.
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
    #: makes. Mutable and shared deliberately: a researched episode runs two
    #: calls at once and both must land in the same total, so `_answer_first`
    #: hands the cover half a ScriptNotes carrying *this* Usage object. A
    #: per-call copy would report the cover as free, which is the half that
    #: does most of the writing.
    usage: metering.Usage = dataclasses.field(default_factory=metering.Usage)

    # --- how long what this episode says stays true -----------------------
    #
    # **Here rather than on the plan, and that is not tidiness.** The caller
    # holds the *unprepared* plan: `stream_sentences` rebinds it
    # (`plan = await self.prepare(plan, notes)`) and `_answer_first` derives
    # two more that the pipeline never sees. So `plan.brief` and `plan.live`
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
    #: What the live lookup actually did, for the log and `/api/health`. One of
    #: `live_facts.LiveLookup.outcome`, or "" when nothing was asked.
    live_outcome: str = ""
    #: Who this episode's facts came from - `provenance.Provenance`. Read back
    #: by the pipeline, cached beside the script and shown in the app. FAM
    #: already collected all of this and discarded it; see `provenance.py`.
    provenance: object = None


def extract_thread(text: str) -> str:
    """Pull the predicted follow-up out of the model's trailing marker line."""
    match = _NEXT_MARKER.search(text)
    if not match:
        return ""
    thread = re.sub(r"\s+", " ", match.group(1)).strip(" .\"'")
    return thread[:160]


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
    #: "" for a whole episode. "opening" is the half written from what the
    #: model already knows, which starts immediately; "continuation" is the
    #: researched half, which takes over once its sources are in. Answer first,
    #: research underneath: the listener never waits, and what covers the wait
    #: is the answer rather than filler.
    role: str = ""
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
    #: run (a cover half, an unresearched episode, EPISODE_INTELLIGENCE=0), and
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


#: What each half of an answer-first episode is told about the other. The two
#: are divided by *content*, not by text: the opening cannot know what the
#: research will find, and the research cannot know the opening's exact words,
#: but both can be told which job is whose.
ROLE_BRIEFS = {
    "opening": (
        "\nYou are opening an episode that will continue after you. Answer from "
        "what you already know, starting immediately - no waiting, no hedging "
        "about not having looked anything up. Cover what this is, why it works "
        "the way it does, and the history that explains it: the parts of the "
        "answer that do not change week to week.\n"
        "Do not speculate about what may have happened recently, and do not "
        "promise that anything is coming. Write as much as the length allows; "
        "you may be cut off mid-episode, which is expected and fine.\n"
    ),
    "continuation": (
        "\nThe episode is already playing. The opening covered what this is, how "
        "it works and the background - all from general knowledge, which may be "
        "months out of date. You are taking over mid-episode.\n"
        "Do not re-introduce the topic, define terms already defined, or write a "
        "new opening line. Go straight to what your sources actually say, and "
        "spend your length on what is current: what has happened lately, what "
        "the numbers are now, what changed.\n"
        "If what you found contradicts the general picture the opening would "
        "have given, say so plainly and in passing - 'that figure has since "
        "moved to X' - and carry on. A correction stated calmly is more useful "
        "than a seam the listener can hear.\n"
    ),
}


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

    # The other half of a researched episode, and the one that was missing.
    #
    # `_request_kwargs` attaches the web_search tool whenever an episode is
    # researched and no evidence packet came back - the `claude` backend, or
    # Exa returning nothing usable. It attached the tool and said nothing
    # about it, so the model was handed a capability it was never asked to
    # use: it wrote from memory and said "I don't have any information on the
    # 49ers game last night. I can't confirm the score, the opponent, or the
    # plays." Which is honest, and is not research.
    #
    # A tool is not an instruction. This is the instruction.
    research_now = ""
    if plan.search and not plan.evidence:
        research_now = """
Nothing has been looked up for you, and you have a web search tool. Use it
before you write - this question was routed for research, which means what you
remember is not good enough on its own.

Search first, then write from what you find. If the first search misses, try
different words before giving up on it.

Do not write "I don't have that information".
Do not write "I can't confirm" anything.
Neither is true: you have the means to find out, and declining to look is the
one answer that is not available here.

If you genuinely searched and the answer is not out there, say what you did
establish and what is not yet reported, plainly, and carry on.

Never read a source's title, number or URL aloud. This is someone listening,
not reading a citation list.
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
{attached}{live}{evidence}{research_now}{temporal}{brief_block}{follow_up}{ROLE_BRIEFS.get(plan.role, "")}
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

Then, on its own line after the script, predict the single most likely thing
they would go on to ask, having heard this:

<<NEXT: six to twelve words>>

Read it off what you actually said. That line is stripped before anything is
spoken and the script must not hint at it. Nothing goes after it.

The time is the listener's, not a quota. If the story resolves early, stop
there; a short piece that lands beats a long one padded out. If you catch
yourself saying a topic is complex, or restating something, the story is over -
end it.

Begin."""


def clean_for_speech(text: str) -> str:
    """Strip anything the model may have added that should not be spoken."""
    # The go-deeper marker, and any half-written one: everything from an
    # unmatched "<<" onwards is metadata, never speech.
    text = _NEXT_MARKER.sub("", text)
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
        # The tool and the packet are alternatives. With evidence already in
        # the prompt, attaching the tool would let the model search on top of
        # what it was handed - paying the 10-25s this design exists to avoid,
        # and making it impossible to tell which source an episode came from.
        if plan.search and not plan.evidence:
            kwargs["tools"] = [
                {
                    "type": "web_search_20260209",
                    "name": "web_search",
                    "max_uses": settings.max_web_searches,
                }
            ]
        return kwargs

    async def research(self, plan: EpisodePlan,
                       notes: ScriptNotes | None = None) -> EpisodePlan:
        """Retrieve evidence, if this episode is researched and Exa is the backend.

        Returns the plan to write from - the same one on the `claude` backend,
        or a copy carrying the packet on `exa`. Separate from
        `stream_sentences` so that the retrieval is a step a caller can see,
        time and skip, rather than something buried inside the streaming call.

        A retrieval that fails does not fall back to the model's own search.
        The exception reaches the pipeline, which on a researched episode is
        already the half `_answer_first` covers - the from-knowledge answer
        keeps playing and the failure is logged, which is the designed
        behaviour for a research half that dies. Substituting a different
        source silently would make the episode unattributable.
        """
        if not plan.search or plan.evidence:
            return plan
        if settings.research_backend == "claude":
            return plan

        packet = await research_mod.retrieve(
            (getattr(plan.brief, "retrieval", "") or plan.query),
            brief=plan.brief)
        if notes is not None:
            notes.research = packet.as_dict()
            # Exa's own reported cost where it gave one, its published rate
            # otherwise - `research.retrieve` has already made that choice.
            notes.usage.add_research(packet.searches, packet.cost)
        if not packet:
            # Nothing usable came back. The episode is still answerable, and
            # without an evidence block the tool stays attached - so the model
            # searches after all rather than being handed an empty packet and
            # told it is research.
            return plan
        if notes is not None and packet.provenance is not None:
            notes.provenance = packet.provenance
        return dataclasses.replace(plan, evidence=packet.context,
                                   thin_on=tuple(packet.missing))

    async def understand(self, plan: EpisodePlan,
                         notes: ScriptNotes | None = None) -> EpisodePlan:
        """Work out what this request is, before anything is retrieved.

        Skipped in three cases, each for its own reason:

        * **A plan that already has a brief.** Prefetch built it before the tap
          - which is the whole point of prefetch, and re-deriving it here would
          throw away the latency that buying it early was for.
        * **The cover half of an answer-first episode** (`role == "opening"`).
          It is defined as the part that starts immediately from what the model
          already knows; a model call in front of it is precisely the wait it
          exists to cover.
        * **An unresearched episode.** EI's largest single product is the
          retrieval query, and an episode that retrieves nothing cannot spend
          it. The framing would still be worth something, and it is not worth a
          second of silence to get.
        """
        if plan.brief is not None or plan.role == "opening" or not plan.search:
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
            return dataclasses.replace(plan, brief=warmed)

        brief = await episode_intelligence.understand(
            plan.query, plan.minutes, plan.context, notes)
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
        plan = await self.understand(plan, notes)

        # **Concurrent, and verified independent before it was made so.** Both
        # read `plan.brief` and neither reads the other's output: `live_lookup`
        # takes `brief.live_domain` and `research` takes `brief.retrieval`,
        # `recency_days` and `must_establish`. Sequentially the live call added
        # its whole latency on top of retrieval, in front of the first word,
        # for nothing. They are merged field-by-field rather than chained
        # because each returns a copy derived from the *same* input plan.
        live_plan, research_plan = await asyncio.gather(
            self.live_lookup(plan, notes), self.research(plan, notes))
        plan = dataclasses.replace(
            plan, live=live_plan.live, evidence=research_plan.evidence,
            thin_on=research_plan.thin_on)

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
        return plan

    async def stream_sentences(
        self, plan: EpisodePlan, notes: ScriptNotes | None = None
    ) -> AsyncIterator[str]:
        """Yield speech-ready sentences as Claude writes them.

        Sentence granularity is deliberate: it is the largest unit that keeps
        time-to-first-audio low, and the smallest unit that still gives the TTS
        engine enough context for natural intonation.

        Research happens here rather than in the caller so that every entry
        point gets it - the pipeline, `write.py`, the top-up path - and so that
        it happens on this coroutine's own task. On a researched episode that
        task is the half `_answer_first` runs underneath the cover, which is
        the only reason a retrieval before the first token is affordable.
        """
        plan = await self.prepare(plan, notes)
        buffer = ""
        emitted_words = 0

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
                    if sentence:
                        emitted_words += count_words(sentence)
                        yield sentence
                buffer = speech + marker + rest
                # Safety valve: a model that ignores the budget must not be
                # allowed to produce an hour of audio for a 1-minute request.
                if emitted_words > plan.max_words * 1.35:
                    break

            tail = clean_for_speech(buffer)
            if tail:
                yield tail
            if notes is not None:
                notes.thread = extract_thread(buffer)

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
