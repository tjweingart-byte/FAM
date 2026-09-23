"""The generate-while-you-listen pipeline.

    Claude tokens -> sentences -> TTS -> raw PCM -> HTTP response -> speakers

Nothing is written to disk and nothing is encoded. The bytes leaving the TTS
engine are the bytes the browser plays.

Hitting the requested duration takes three mechanisms, because no single one is
enough on its own:

* **Budget** - the script is commissioned at the right word count up front.
* **Pacing** - the speaking rate is re-planned before every sentence, so small
  misses are absorbed invisibly (clamped to a range a listener accepts).
* **Trim / top-up** - a script that is too long is cut at a sentence boundary;
  one that is too short is extended with a second, smaller Claude request.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Optional

import time

from audio_utils import PaceController, pcm_duration, silence, streaming_wav_header
import prefetch
from cache import (ScriptCache, build_cache, cache_key, canonical_key, is_shareable,
                   key_bucket, ttl_for)
import metering
from episode_marks import EpisodeMarks, TimedClient
from config import STREAMING_PIPELINES, settings
import live_captions
from script_buffer import ASSEMBLER_TICK, ScriptBuffer
from script_generator import EpisodePlan, ScriptGenerator, ScriptNotes, count_words
from speech_assembly import (AssembledChunk, AssemblyPolicy,
                             SpeechAssembler, fit_to_budget)
import tts
from tts import TTSEngine, build_engine

log = logging.getLogger(__name__)

# How many sentences may sit synthesised-and-waiting. Small on purpose: this is
# the whole memory budget of a 10-minute episode.
#: Sentinel meaning "use the configured cache" - see PodcastPipeline.__init__.
AUTO = "auto"


class NotCached(Exception):
    """A replay-only request found nothing in the cache.

    Explore is built on the promise that it never spends a model call. If that
    promise lived only in the interface it would be one refactor away from
    being broken silently and expensively, so the pipeline refuses instead.
    """


async def key_for(plan: EpisodePlan, client=None) -> str:
    """Where an episode lives in the shared cache.

    **Module-level, and that is the point.** Prefetch writes a script before
    anyone asks for it, and a tap finds that script only if both sides compute
    the same key. Two implementations that agree today drift the first time one
    of them gains a field, and the failure is silent and total: every
    speculative script is paid for and never read, while the feed looks exactly
    as it did before. One function means they cannot disagree.

    Returns "" for an episode that is nobody else's business. An attachment
    makes it personal, and no key means no read, no write, and nothing that
    could reach another listener or Explore.
    """
    if plan.attachments:
        return ""
    canonical = None
    if settings.cache_semantic_key and client is not None:
        canonical = await canonical_key(plan.query, client)
    return cache_key(plan.query, plan.minutes, canonical, plan.context, plan.search)


def bucket_for(plan: EpisodePlan) -> str:
    """The set of entries an episode could stand in for. Shared for the same
    reason as `key_for`: a near-match bucket computed two ways is a bucket
    nothing is ever found in."""
    if plan.attachments or not settings.cache_vector:
        return ""
    return key_bucket(plan.minutes, plan.context, plan.search)

QUEUE_DEPTH = 4
# Silence inserted between sentences so the delivery does not sound rushed.
SENTENCE_GAP = 0.12
# A sentence may overshoot the remaining time by this much before it is cut.
OVERRUN_GRACE = 0.6
# Dead air worth going back to Claude for.
TOPUP_THRESHOLD = 4.0
# Cap the number of extra requests, so a model that keeps under-writing cannot
# turn one episode into an unbounded fan-out of API calls.
MAX_TOPUPS = 2
OPENER_BUFFER_TARGET = 6.0

# How far ahead of the listener the opener keeps the stream. Enough that a slow
# script cannot cause silence; small enough that a fast one wastes no preamble.
OPENER_HEADROOM_TARGET = 8.0

# Hard ceiling on opener fetches, so a wedged script call cannot fan out into
# unbounded API calls. The real limit is COLD_OPEN_MAX_SECONDS.
MAX_OPENER_FILLS = 12
# Residual gap after the last top-up, closed with room tone rather than a cut.
MAX_TAIL_SILENCE = 6.0


@dataclass
class _Pump:
    """A sentence stream that is already running in the background."""

    queue: asyncio.Queue
    task: asyncio.Task
    #: Items pulled off the queue early by prime(), consumed before it.
    pending: list = field(default_factory=list)
    primed: bool = False

    async def prime(self) -> object:
        """Wait for this stream's first item, without consuming it.

        Used to answer "is there more audio ready to follow?" before committing
        to play something that would otherwise run into silence.
        """
        if not self.primed:
            self.pending.append(await self.queue.get())
            self.primed = True
        return self.pending[0] if self.pending else None

    async def peek(self) -> None:
        """Wait until an item is available, without consuming it."""
        if self.pending:
            return
        item = await self.queue.get()
        self.pending.append(item)

    def ready(self) -> bool:
        """Is there an item available right now, without waiting?"""
        return bool(self.pending) or not self.queue.empty()

    async def next(self) -> object:
        if self.pending:
            return self.pending.pop(0)
        return await self.queue.get()

    async def close(self) -> None:
        self.task.cancel()
        try:
            await self.task
        except BaseException:
            pass


async def _replay(sentences: list[str]) -> AsyncIterator[str]:
    """Feed a cached script back through the normal speaking path."""
    for sentence in sentences:
        yield sentence


@dataclass
class GenerationStats:
    """Everything the UI needs to show, and the tests need to assert on."""

    plan_seconds: int = 0
    audio_seconds: float = 0.0
    words: int = 0
    sentences: int = 0
    engine: str = ""
    voice: str = ""
    sample_rate: int = settings.sample_rate
    truncated: bool = False
    topups: int = 0
    #: The episode's own title, off the model's trailing marker line. Empty
    #: when it wrote none, and every caller falls back to the question.
    title: str = ""
    #: One sentence on what the episode is, off `<<SUMMARY:>>` (§127).
    summary: str = ""
    #: "hit" | "miss" | "off" - whether this episode reused a shared script.
    #: "exact" | "near" | "" - *how* a hit was found. A near hit replayed an
    #: episode written for a differently-worded question, which is worth being
    #: able to see: it is the one kind of hit that can be wrong.
    match: str = ""
    #: Cosine of a near hit, 0.0 otherwise.
    match_score: float = 0.0
    #: True when this hit was on a script prefetch had written before anybody
    #: asked. The number CLAUDE.md's "how much to prefetch?" turns on, recorded
    #: on the serving path because a hit rate inferred anywhere else is a hit
    #: rate nobody should trust.
    prefetched: bool = False
    #: A mark name for the next synthesis to record, set once and consumed by
    #: whichever of `_speak_chunk`/`_speak_one` runs next. Lets a caller name
    #: one synthesis without threading a parameter through call sites that do
    #: not otherwise care about it.
    pending_synthesis_mark: str = ""

    def take_synthesis_mark(self, marks: EpisodeMarks) -> None:
        """Record and clear the one-shot mark, if one is waiting."""
        if self.pending_synthesis_mark:
            marks.mark(self.pending_synthesis_mark)
            self.pending_synthesis_mark = ""
    cache: str = "off"
    #: When generation began, for audio-produced vs wall-clock comparisons.
    started_at: float = field(default_factory=time.perf_counter)
    #: Total seconds spent inside the speech engine.
    synth_seconds: float = 0.0
    #: Smallest margin between audio produced and wall clock. Negative means
    #: the listener heard silence.
    min_headroom: float = 999.0
    #: True if the stream ever fell behind realtime.
    starved: bool = False
    #: Wall clock at which the first audio left the pipeline.
    first_audio_at: float = 0.0
    #: Named instants and per-synthesis records for this episode. Written to,
    #: never read back: instrumentation must not be able to change what a
    #: listener hears.
    marks: EpisodeMarks = field(default_factory=EpisodeMarks)
    script: list[str] = field(default_factory=list)
    #: Where each sentence in `script` starts in the audio, in seconds - kept
    #: with the audio (§132) so a stored replay can caption itself.
    starts: list[float] = field(default_factory=list)
    #: "stored" when the audio came out of the episode cache and the voice
    #: engine was never called; "kept" when this play's audio was written
    #: there for next time; "" otherwise.
    audio: str = ""
    #: The script key to keep this play's audio under, set by the path that
    #: knows it is allowed to - "" means keep nothing. On a near hit it is the
    #: neighbour's key, because the audio is the neighbour's script.
    audio_key: str = ""
    #: The cache key live captions are published under while this episode is
    #: being spoken, or "" for an episode that has none (an attachment, which
    #: is deliberately uncacheable and therefore deliberately uncaptioned).
    #:
    #: It is the *cache* key rather than a key of its own, so a live track and
    #: the cached script that replaces it a few seconds later are the same
    #: episode by construction - `/api/transcript` reads one and then the
    #: other under one key, and two key schemes could not silently disagree
    #: about which episode a caption belonged to.
    caption_key: str = ""
    #: The thread the episode left open, phrased as the follow-up a listener
    #: would ask for. Drives the one-tap suggestion in Go Deeper; empty when
    #: the model named none.
    thread: str = ""
    #: What this episode consumed. Copied off ScriptNotes when generation
    #: finishes, because `stats` is the object that reaches the endpoint and
    #: the endpoint is the only place that knows *whose* episode this was.
    usage: metering.Usage = field(default_factory=metering.Usage)

    @property
    def voiced_seconds(self) -> float:
        """Seconds of audio the voice engine actually made for this play.

        What metering allocates GPU cost from. A replay out of kept audio made
        none (§132), and billing it as though it had would put the saving
        this whole mechanism exists for back into the ledger as a cost.
        """
        return 0.0 if self.audio == "stored" else self.audio_seconds

    @property
    def drift(self) -> float:
        return self.audio_seconds - self.plan_seconds

    def as_dict(self) -> dict:
        return {
            "requested_seconds": self.plan_seconds,
            "audio_seconds": round(self.audio_seconds, 2),
            "drift_seconds": round(self.drift, 2),
            "words": self.words,
            "sentences": self.sentences,
            "engine": self.engine,
            "voice": self.voice,
            "truncated": self.truncated,
            "topups": self.topups,
            "cache": self.cache,
            "audio_cache": self.audio,
            "match": self.match,
            "match_score": round(self.match_score, 3),
            "prefetched": self.prefetched,
            "synth_seconds": round(self.synth_seconds, 2),
            "min_headroom": round(self.min_headroom, 1) if self.min_headroom < 999 else None,
            "starved": self.starved,
            "first_audio_at": round(self.first_audio_at, 2),
            "thread": self.thread,
        }


def _sentence_starts(spoken, chunk_start: float, audio_seconds: float) -> list:
    """Where each sentence of one synthesised chunk starts, in seconds.

    The chunk's own start is measured - it is how much audio came before it -
    and its length is measured too. Only the split *inside* one chunk is by
    characters, which is a handful of sentences at most, rather than the whole
    episode estimated against its planned length (§127).
    """
    lengths = [max(1, len(str(s))) for s in spoken]
    total = float(sum(lengths)) or 1.0
    starts, run = [], 0
    for n in lengths:
        starts.append(chunk_start + audio_seconds * (run / total))
        run += n
    return starts


class PodcastPipeline:
    def __init__(
        self,
        generator: Optional[ScriptGenerator] = None,
        engine: Optional[TTSEngine] = None,
        cache: ScriptCache | None | str = AUTO,
        voice: Optional[str] = None,
        cache_writes: bool = True,
        author: str = "",
    ):
        """`cache` takes a store, or AUTO to build the configured one, or None
        to disable caching.

        `cache_writes=False` reads the cache but never adds to it. That is not
        a tuning knob - it is what demo mode needs. Without credentials the
        writer is a canned sample script that describes how the audio pipeline
        works, and caching it stores that text under whatever the listener
        actually asked, where Explore and every other listener will later be
        served it as a real episode. Reads must stay on, because replaying is
        the one thing that needs no credentials at all.

        The explicit AUTO sentinel exists because `cache=None` previously meant
        "build the default", so passing None to switch caching *off* silently
        turned it on. That misread caused two separate test failures before it
        was noticed; a caller saying None now unambiguously gets no cache.
        """
        self.generator = generator or ScriptGenerator()
        self.engine = engine or build_engine()
        self.cache = build_cache() if cache is AUTO else cache
        self.cache_writes = cache_writes
        #: Passed to the engine on every sentence. The script is unaffected by
        #: it, which is why the script cache deliberately ignores voice.
        self.voice = voice
        #: Who is paying for this episode, stamped on anything written to the
        #: shared cache so Explore can leave them out of their own feed.
        #:
        #: **Deliberately not on `EpisodePlan`.** The plan is what an episode
        #: *is*, and `key_for` is built from it: a listener id there would be
        #: one field away from becoming part of the key, which would give
        #: every listener their own cache and throw away the shared-cost
        #: design the whole app rests on. It is a property of the request, so
        #: it lives on the thing built per request.
        self.author = author

    def _start(self, sentences: AsyncIterator[str],
               marks: Optional[EpisodeMarks] = None,
               completion_mark: str = "claude_complete",
               first_sentence_mark: str = "") -> "_Pump":
        """Begin consuming a sentence stream *now*, into a bounded queue.

        Starting is separated from speaking so two model calls can be in flight
        at once: the researched main script begins the moment the request
        arrives, while the cold open is what actually reaches the speakers
        first. The queue depth caps memory at a few seconds of audio however
        long the episode is.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_DEPTH)

        async def produce() -> None:
            try:
                async for sentence in sentences:
                    if marks is not None and first_sentence_mark:
                        marks.mark(first_sentence_mark)
                    await queue.put(sentence)
                # Here, and only here, is where the model stopped writing.
                # This used to be marked by the *consumer* on receiving the
                # sentinel, which is the end of speaking rather than the end of
                # generation - and on a truncated episode the consumer breaks
                # before the sentinel arrives, so it was never marked at all.
                # Most episodes truncate, so `claude_total` was usually absent
                # and the decoupling verdict had nothing to stand on.
                if marks is not None:
                    marks.mark(completion_mark)
            except asyncio.CancelledError:
                # `close()` cancelled us. The sentinel is deliberately NOT sent
                # here, and this must not be a `finally`: on the close path
                # nobody is draining, so a blocking put on a full queue would
                # suspend, swallow the cancellation that `close()` just
                # delivered, and `close()` would wait for a task that can never
                # finish. Re-raising ends the task, which is what `close()`
                # is waiting for. Nothing is lost - the sentinel only tells a
                # consumer the stream ended, and there is no consumer left.
                raise
            except Exception as exc:  # surfaced to the consumer, never swallowed
                await queue.put(exc)
            else:
                await queue.put(None)

        return _Pump(queue, asyncio.create_task(produce()))

    def _start_phase6(
        self,
        sentences: AsyncIterator[str],
        policy: Optional[AssemblyPolicy] = None,
        marks: Optional[EpisodeMarks] = None,
        completion_mark: str = "claude_complete",
        headroom: Optional[Callable[[], Optional[float]]] = None,
        first_sentence_mark: str = "",
    ) -> "_Pump":
        """`_start`, with the reader decoupled and the sentences assembled.

        **This is the production path.** `STREAMING_PIPELINE` defaults to
        `phase6`, so an ordinary request arrives here; `_start` is reached only
        by naming `legacy` deliberately.

            Claude stream
              -> reader          its own task, never touches this queue
              -> ScriptBuffer    bounded by CHARACTERS, ~20 episodes
              -> SpeechAssembler whole sentences -> speech-sized chunks
              -> this queue      bounded at QUEUE_DEPTH, as `_start`'s is
              -> _speak_phase6

        `_start` puts sentences straight onto the bounded queue, so a slow
        voice fills it and stops the reader - the Phase 6 4090 run reported a
        12.5s Claude stream as 66.7s, 64.3s of it that backpressure. Here the
        queue holds *chunks* and sits below the buffer, so filling it suspends
        the assembler and never the reader.

        Returns the same `_Pump` the rest of the pipeline expects. Its items
        are `AssembledChunk` rather than `str`, which is why the companion
        `_speak_phase6` exists: `_speak_one` synthesises one item per call and
        appends it to `stats.script`, so handing it a chunk would both coarsen
        the duration check and put a multi-sentence blob in the cache.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_DEPTH)


        async def produce() -> None:
            """Reader, buffer and assembler, with the reader owned explicitly.

            `script_buffer.assemble_chunks` composes the same three pieces and
            is the tested primitive, but it owns its reader inside an async
            generator - and cancelling a task that is iterating a generator
            which owns another task does not unwind reliably. Here the reader
            is a task this coroutine holds and cancels itself, and every await
            is a `sleep` or a `wait_for`, both of which always cancel.
            """
            buffer = ScriptBuffer()
            assembler = SpeechAssembler(policy=policy or AssemblyPolicy())

            async def read() -> None:
                try:
                    async for sentence in sentences:
                        if sentence and sentence.strip():
                            # The model produced a sentence. Separate from when
                            # the assembler releases it and from when it is
                            # spoken - which is the only way to tell research
                            # being slow apart from batching holding it.
                            if marks is not None and first_sentence_mark:
                                marks.mark(first_sentence_mark)
                            await buffer.put(sentence)
                    # The model stopped writing. Marked in the reader because
                    # this is the only place that knows it: the consumer's
                    # sentinel means speaking ended, which on a truncated
                    # episode never happens at all.
                    if marks is not None:
                        marks.mark(completion_mark)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    buffer.fail(exc)
                finally:
                    buffer.close()

            reader = asyncio.create_task(read())
            #: The pending `buffer.get()`, held across ticks and owned here.
            #:
            #: This used to be `await asyncio.wait_for(buffer.get(), tick)`,
            #: and that was the teardown defect: `wait_for` cancels its inner
            #: task on timeout, and when the inner task completes in the same
            #: turn it returns the result and *drops* the outer cancellation
            #: it was supposed to propagate. The producer then carried on
            #: round its loop having eaten the cancel `close()` sent, so it
            #: could never be shut down. `asyncio.wait` never cancels what it
            #: waits on, so an outer cancel passes straight through.
            waiting: Optional[asyncio.Task] = None
            try:
                while True:
                    if waiting is None:
                        waiting = asyncio.ensure_future(buffer.get())
                    done, _ = await asyncio.wait({waiting},
                                                 timeout=ASSEMBLER_TICK)
                    # What the listener has left to play. The assembler's
                    # headroom rule - "batching stops mattering when the
                    # listener is about to catch up" - is the whole defence
                    # against a mid-episode gap, and it was dead: both calls
                    # below passed no headroom, so it never once fired. The
                    # margin it guards is thin by design, because the first
                    # chunk is deliberately tiny: ~9 words is 3.6s of audio,
                    # and a 45-word chunk takes ~3.9s to synthesise at 4.6x
                    # realtime. Batching to the cap on a thin buffer is
                    # silence.
                    left = headroom() if headroom is not None else None
                    if not done:
                        # Nothing new: run the assembler's timer and headroom
                        # rules so text is never held indefinitely.
                        for chunk in assembler.due(left):
                            await queue.put(chunk)
                        continue
                    sentence, waiting = waiting.result(), None
                    if sentence is None:
                        for chunk in assembler.flush():
                            await queue.put(chunk)
                        break
                    for chunk in assembler.offer(sentence, left):
                        await queue.put(chunk)
                await queue.put(None)
            except asyncio.CancelledError:
                # Closed early. No sentinel: nobody is draining, and sending
                # one would only be another chance to block.
                raise
            except Exception as exc:  # surfaced to the consumer, never swallowed
                await queue.put(exc)
            finally:
                # Everything this coroutine started, it ends. The reader owns
                # nothing else, and the pending get is cancelled here rather
                # than left for the loop to finalise.
                if waiting is not None:
                    waiting.cancel()
                reader.cancel()

        return _Pump(queue, asyncio.create_task(produce()))


    async def _speak_phase6(
        self,
        pump: "_Pump",
        pace: PaceController,
        stats: GenerationStats,
        fatal: bool = True,
    ) -> AsyncIterator[bytes]:
        """`_speak` for a pump of assembled chunks. The production path.

        Deliberately a copy of `_speak`'s loop rather than a refactor of it.
        That was written when `_speak` was the shipped path and this one could
        not be allowed to disturb it; the reason has inverted but the shape is
        still right, because `tests/test_phase6_equivalence.py` compares the
        two columns and a shared implementation would compare nothing.
        """
        try:
            while True:
                item = await pump.next()
                if item is None:
                    # The sentinel means the *queue* is drained, not that the
                    # model finished - that is marked in the producer, which is
                    # the only place that knows. Recorded here: what was still
                    # waiting, as corroboration for the verdict, never as it.
                    stats.marks.backlog_at_claude_complete = pump.queue.qsize()
                    break
                if isinstance(item, Exception):
                    if fatal:
                        raise item
                    log.warning("optional stream failed; continuing", exc_info=item)
                    break
                async for chunk in self._speak_chunk(item, pace, stats):
                    yield chunk
                if stats.truncated:
                    break
        finally:
            stats.marks.mark("speaking_complete")
            if stats.marks.backlog_at_claude_complete is None:
                # Truncation stops the loop before the sentinel arrives, which
                # is most episodes. The queue depth at the moment speaking
                # ended answers the same question: was synthesis behind?
                stats.marks.backlog_at_claude_complete = pump.queue.qsize()
            # `_Pump.close()`, the same as `_speak`: it cancels the producer
            # *and* awaits it, so nothing this method started outlives it.
            await pump.close()

    async def _speak_chunk(
        self, chunk: AssembledChunk, pace: PaceController, stats: GenerationStats
    ) -> AsyncIterator[bytes]:
        """Synthesise one assembled chunk, cut to what still fits.

        `_speak_one` asks "does this sentence fit" once. A chunk is several
        sentences in one synthesis call, so the same question is asked for each
        of them first, by `fit_to_budget`, and the chunk is cut at the last
        boundary that fits. Only the final spoken sentence may cross the
        budget, and by at most `OVERRUN_GRACE` - the same bound `_speak_one`
        allows, rather than that plus a whole chunk.

        Accounting is per sentence even though synthesis is per chunk:
        `stats.script` stays a list of sentences, because the cache stores it
        and `_replay` feeds it back through the speaking path.
        """
        gap = silence(SENTENCE_GAP, self.engine.sample_rate)
        # Keep the controller's view of "words left" honest, as `_speak_one`
        # does: the model rarely hits the budget exactly.
        pace.total_words = max(pace.total_words, pace.spoken_words + chunk.words)

        wpm = pace.next_wpm()
        fit = fit_to_budget(chunk.parts, pace.remaining_seconds, wpm,
                            SENTENCE_GAP, OVERRUN_GRACE)
        if fit.truncated:
            stats.truncated = True
        if not fit.spoken:
            return

        # Marked here, in the synthesis path itself, rather than in the pump
        # loop. A mark placed in the loop once recorded the wrong item and
        # produced `first_sentence_to_synthesis` of -26.13s on the 4090 -
        # synthesis apparently beginning 26 seconds before a sentence existed.
        # Impossible numbers are how a misplaced mark announces itself.
        stats.marks.mark("first_sentence")
        tts_start = stats.marks.mark("first_tts_start")
        stats.take_synthesis_mark(stats.marks)
        started = time.perf_counter()
        pcm = await self.engine.synth(fit.text, wpm, self.voice)
        synth_seconds = time.perf_counter() - started
        tts_done = stats.marks.mark("first_tts_complete")
        if not pcm:
            return

        audio_seconds = pcm_duration(len(pcm), self.engine.sample_rate)
        stats.marks.add_chunk(fit.text, len(fit.spoken),
                              tts_done - synth_seconds, tts_done, audio_seconds)
        stats.synth_seconds += synth_seconds
        elapsed_wall = time.perf_counter() - stats.started_at
        headroom = pace.elapsed + audio_seconds - elapsed_wall
        if headroom < stats.min_headroom:
            stats.min_headroom = headroom
        log.debug(
            "chunk %d: %d sentence(s), %.2fs audio in %.2fs (%.0fx realtime), "
            "headroom %.1fs", chunk.index, len(fit.spoken), audio_seconds,
            synth_seconds, audio_seconds / synth_seconds if synth_seconds else 0,
            headroom,
        )
        if headroom < 0 and not stats.starved:
            stats.starved = True
            log.warning(
                "STARVED after %.1fs: only %.1fs of audio made in %.1fs of wall clock. "
                "The listener hears silence here. Synthesis so far: %.1fs.",
                elapsed_wall, pace.elapsed + audio_seconds, elapsed_wall, stats.synth_seconds,
            )
        # Where each sentence starts in the audio, measured before this chunk
        # is counted, so the caption panel follows the voice rather than
        # estimating it (§127).
        starts = _sentence_starts(fit.spoken, pace.elapsed, audio_seconds)
        pace.observe(len(pcm) + len(gap), fit.words)
        stats.sentences += len(fit.spoken)
        stats.words += fit.words
        stats.script.extend(fit.spoken)
        # On screen the moment it is handed to the voice, rather than when the
        # finished script reaches the cache. Captions used to poll the cache
        # and give up after twelve seconds, which is nothing like how long a
        # researched ten-minute episode takes to write - see live_captions.py.
        live_captions.publish(stats.caption_key, fit.spoken, starts)
        stats.starts.extend(starts)
        if not stats.first_audio_at:
            stats.first_audio_at = time.perf_counter() - stats.started_at
            log.info("first audio ready after %.2fs", stats.first_audio_at)
        yield pcm
        yield gap

    async def _speak(
        self,
        pump: "_Pump",
        pace: PaceController,
        stats: GenerationStats,
        fatal: bool = True,
    ) -> AsyncIterator[bytes]:
        """Synthesise an already-running sentence stream inside the time budget.

        `fatal=False` means a failure in this stream is logged and skipped
        rather than ending the episode - used for the optional cold open.
        """
        try:
            while True:
                item = await pump.next()
                if item is None:
                    break
                if isinstance(item, Exception):
                    if fatal:
                        raise item
                    log.warning("optional stream failed; continuing", exc_info=item)
                    break
                async for chunk in self._speak_one(item, pace, stats):
                    yield chunk
                if stats.truncated:
                    break
        finally:
            stats.marks.mark("speaking_complete")
            await pump.close()

    async def _speak_one(
        self, sentence: str, pace: PaceController, stats: GenerationStats
    ) -> AsyncIterator[bytes]:
        """Synthesise one sentence, or stop the episode if it no longer fits."""
        gap = silence(SENTENCE_GAP, self.engine.sample_rate)
        words = count_words(sentence)
        # Keep the controller's view of "words left" honest: the model rarely
        # hits the budget exactly, so grow the total when it overshoots rather
        # than sprinting through the remainder.
        pace.total_words = max(pace.total_words, pace.spoken_words + words)

        wpm = pace.next_wpm()
        # Will this sentence fit in the time that is left? Speeding up is
        # already clamped to a rate a listener accepts, so an over-long script
        # has to be cut rather than gabbled. Cutting at a sentence boundary is
        # why the pipeline works in sentences.
        estimated = words / (wpm / 60.0) + SENTENCE_GAP
        if estimated > pace.remaining_seconds + OVERRUN_GRACE:
            stats.truncated = True
            return

        stats.marks.mark("first_sentence")
        stats.marks.mark("first_tts_start")
        stats.take_synthesis_mark(stats.marks)
        started = time.perf_counter()
        pcm = await self.engine.synth(sentence, wpm, self.voice)
        synth_seconds = time.perf_counter() - started
        tts_done = stats.marks.mark("first_tts_complete")
        if not pcm:
            return

        # Compare audio produced against wall clock consumed. A listener hears
        # silence exactly when the second overtakes the first, so this is the
        # number that matters, and it is logged for every sentence.
        audio_seconds = pcm_duration(len(pcm), self.engine.sample_rate)
        stats.synth_seconds += synth_seconds
        elapsed_wall = time.perf_counter() - stats.started_at
        headroom = pace.elapsed + audio_seconds - elapsed_wall
        if headroom < stats.min_headroom:
            stats.min_headroom = headroom
        log.debug(
            "sentence %d: %.2fs audio in %.2fs (%.0fx realtime), headroom %.1fs",
            stats.sentences + 1, audio_seconds, synth_seconds,
            audio_seconds / synth_seconds if synth_seconds else 0, headroom,
        )
        if headroom < 0 and not stats.starved:
            stats.starved = True
            log.warning(
                "STARVED after %.1fs: only %.1fs of audio made in %.1fs of wall clock. "
                "The listener hears silence here. Synthesis so far: %.1fs.",
                elapsed_wall, pace.elapsed + audio_seconds, elapsed_wall, stats.synth_seconds,
            )
        stats.marks.add_chunk(sentence, 1, tts_done - synth_seconds, tts_done,
                              pcm_duration(len(pcm), self.engine.sample_rate))
        start = pace.elapsed
        pace.observe(len(pcm) + len(gap), words)
        stats.sentences += 1
        stats.words += words
        stats.script.append(sentence)
        stats.starts.append(start)
        live_captions.publish(stats.caption_key, (sentence,), (start,))
        if not stats.first_audio_at:
            stats.first_audio_at = time.perf_counter() - stats.started_at
            log.info("first audio ready after %.2fs", stats.first_audio_at)
        yield pcm
        yield gap

    # ---- which streaming architecture this request uses -------------------
    #
    # One decision, read from `settings.streaming_pipeline` at request time
    # and applied at every point a pump is made or spoken. **The default is
    # `phase6`**: an installation that has never heard of this setting gets the
    # validated architecture, and reaching the older one takes naming it.
    # Rolling back is still one environment variable and a restart.
    #
    # The two architectures are not blended: a pump made by `_start_phase6`
    # carries `AssembledChunk` and must be spoken by `_speak_phase6`, so the
    # three helpers below always agree with each other.

    def _phase6(self) -> bool:
        """True for Phase 6, False for legacy, and an error for anything else.

        Written as a membership test rather than `== "phase6"` on purpose.
        Equality makes every unrecognised value mean *legacy* - silently, at
        request time, on the listener's episode. That is the failure mode this
        project has lost the most time to, and now that phase6 is the default
        it would turn a typo into a downgrade nobody chose.

        `Settings.__post_init__` already refuses an unknown value at import.
        This is the second gate, because the first one is bypassable: a test
        substituting a settings object, or any code path that builds one
        without validation, would otherwise reach production semantics through
        a name that means nothing.
        """
        choice = settings.streaming_pipeline
        if choice not in STREAMING_PIPELINES:
            raise ValueError(
                f"STREAMING_PIPELINE={choice!r} is not a pipeline. Use one of: "
                f"{', '.join(STREAMING_PIPELINES)}. Refusing rather than "
                "falling back - an unrecognised value must never quietly "
                "select an architecture.")
        return choice == "phase6"

    @staticmethod
    def _headroom_probe(pace: PaceController, stats: GenerationStats):
        """Seconds of audio made but not yet played, read live.

        The same quantity `_speak_chunk` compares against zero to declare
        starvation, so there is one definition of "the listener is about to run
        out" rather than two. It is deliberately conservative - it counts from
        the start of generation rather than from the first byte, so it
        understates the buffer by the time-to-first-audio and therefore ships
        early rather than late.
        """
        def probe() -> Optional[float]:
            return pace.elapsed - (time.perf_counter() - stats.started_at)

        return probe

    def _pump_for(self, sentences: AsyncIterator[str],
                  stats: Optional[GenerationStats] = None,
                  pace: Optional[PaceController] = None,
                  completion_mark: str = "claude_complete",
                  first_sentence_mark: str = "") -> "_Pump":
        """Start a sentence stream under whichever architecture is selected.

        Each call builds its own pump, and under Phase 6 its own script buffer
        and assembler with it, so two streams in one episode - the body and a
        top-up - can never share assembler state or interleave their text into
        one chunk.
        """
        marks = stats.marks if stats is not None else None
        if not self._phase6():
            return self._start(sentences, marks, completion_mark,
                               first_sentence_mark)
        probe = (self._headroom_probe(pace, stats)
                 if pace is not None and stats is not None else None)
        return self._start_phase6(sentences, None, marks, completion_mark,
                                  probe, first_sentence_mark)

    def _speak_pump(self, pump: "_Pump", pace: PaceController,
                    stats: GenerationStats, fatal: bool = True) -> AsyncIterator[bytes]:
        return (self._speak_phase6(pump, pace, stats, fatal) if self._phase6()
                else self._speak(pump, pace, stats, fatal))

    async def _cache_key(self, plan: EpisodePlan) -> str:
        """Where this episode lives in the shared cache. "" when caching is off."""
        if not self.cache:
            return ""
        # `getattr`, not an attribute access: before this was extracted, the
        # client was only reached when CACHE_SEMANTIC_KEY was on, so a
        # generator without one worked fine. Reading it eagerly here made the
        # pipeline require an attribute it had never required.
        return await key_for(plan, getattr(self.generator, "client", None))

    def _bucket(self, plan: EpisodePlan) -> str:
        """The set of entries this episode could stand in for. "" when off."""
        if not self.cache:
            return ""
        return bucket_for(plan)

    async def title_for(self, plan: EpisodePlan) -> str:
        """The episode's own name, or "". Mirrors `thread_for` exactly.

        Only known once the script has been written, which is after the audio
        response headers have gone out - so the player opens on a provisional
        title derived from the question and swaps this in when it lands, the
        same way the Go Deeper chip fills.
        """
        title, _final = await self.title_state(plan)
        return title

    async def title_state(self, plan: EpisodePlan) -> tuple[str, bool]:
        """`(title, final)`: the best name this episode has right now.

        The cache's is the writer's own and is final. Failing that, the live
        track - which holds the brief's provisional title from before the
        first word, then the writer's when the script finishes (§127). The
        interface keeps asking until `final`, so a provisional title is
        replaced rather than kept.
        """
        if not is_shareable(plan.query):
            return "", False
        key = await self._cache_key(plan) if self.cache else ""
        if self.cache:
            reader = getattr(self.cache, "title", None)
            stored = reader(key) if reader is not None else ""
            if stored:
                return stored, True
        return live_captions.read_title(key)

    async def episode_meta(self, plan: EpisodePlan) -> dict:
        """Thread, title (and whether it is final) and summary, from one key.

        One `_cache_key` rather than one per field: with `CACHE_SEMANTIC_KEY`
        on, computing the key is a model call, and `/api/next` is polled
        every couple of seconds while an episode is being written.
        """
        empty = {"thread": "", "title": "", "title_final": False, "summary": ""}
        if not is_shareable(plan.query):
            return empty
        key = await self._cache_key(plan) if self.cache else ""
        out = dict(empty)
        if self.cache:
            out["thread"] = self.cache.thread(key) or ""
            stored = getattr(self.cache, "title", lambda _k: "")(key)
            if stored:
                out["title"], out["title_final"] = stored, True
            out["summary"] = getattr(self.cache, "summary", lambda _k: "")(key) or ""
        if not out["title"]:
            out["title"], out["title_final"] = live_captions.read_title(key)
        return out

    async def summary_for(self, plan: EpisodePlan) -> str:
        """The episode's one-sentence summary from the cache, or ""."""
        if not self.cache or not is_shareable(plan.query):
            return ""
        reader = getattr(self.cache, "summary", None)
        if reader is None:
            return ""
        return reader(await self._cache_key(plan))

    async def thread_for(self, plan: EpisodePlan) -> str:
        """The go-deeper thread of an episode that has already been generated.

        Read out of the cache, so it costs nothing and needs no second call.
        The thread is only known once the script has been written, which is
        after the audio response headers have gone out - hence a separate
        lookup rather than a header on /api/audio.
        """
        if not self.cache or not is_shareable(plan.query):
            return ""
        return self.cache.thread(await self._cache_key(plan))

    async def sources_for(self, plan: EpisodePlan) -> str:
        """Provenance JSON for an episode already generated, or "".

        Mirrors `thread_for` exactly, and for the same reason: what an episode
        drew on is only known once the script has been written, which is after
        the audio response headers have gone out.
        """
        if not is_shareable(plan.query):
            return ""
        key = await self._cache_key(plan) if self.cache else ""
        # In flight first, for the same reason captions read their live track
        # first: on the retrieval path the evidence packet exists before the
        # first sentence does, and the cache does not have it until the
        # episode has finished. A panel that can only appear after the last
        # word has answered "where is this coming from?" too late to matter.
        live = live_captions.read_sources(key)
        if live:
            return live
        if not self.cache:
            return ""
        reader = getattr(self.cache, "sources", None)
        if reader is None:
            return ""
        return reader(key)

    async def script_for(self, plan: EpisodePlan) -> list:
        """The written sentences for an episode already generated, or [].

        What live captions read. Mirrors `sources_for` and `thread_for`
        exactly, including the part that matters most: **it never generates.**
        A caption track that could trigger a write would be a second full
        Claude call for every episode somebody chose to read along with, which
        is the expensive half of an episode paid twice for one listen.

        So captions are available once the script is in the cache, which is
        well before the audio finishes - the script is written far faster than
        it is spoken - and are honestly unavailable for an episode that is not
        cached at all, which is what an attachment episode is by design.
        """
        if not self.cache or not is_shareable(plan.query):
            return []
        reader = getattr(self.cache, "get", None)
        if reader is None:
            return []
        return list(reader(await self._cache_key(plan)) or [])

    async def captions_for(self, plan: EpisodePlan) -> tuple:
        """`(sentences, live, done)` for live captions. Never generates.

        The live track first, the cache behind it - and that order is the
        whole point. `script_for` reads the cache, which is written once when
        the episode finishes, so on a first listen it holds nothing under this
        key until after the last word has been spoken. That is the one moment
        a caption panel is no use, and it is why the panel used to say "no
        transcript for this one" about perfectly ordinary long episodes.

        A live track is *this* generation; a cache entry may be an older one
        under the same key while a re-write is in flight. So a live track wins
        where both exist, rather than being a fallback for a cache miss.

        `done` distinguishes "still being written" from "that is all of it",
        which is what lets a client stop asking. Anything with no live track
        is finished as far as this process can tell - it is either cached, or
        an attachment episode, which has no captions by design.
        """
        key = await self._cache_key(plan) if is_shareable(plan.query) else ""
        live = live_captions.read(key)
        if live is not None:
            sentences, done = live
            return list(sentences), True, bool(done)
        return list(await self.script_for(plan)), False, True

    async def caption_starts(self, plan: EpisodePlan) -> list:
        """Where each live sentence starts in the audio, or [] if unmeasured.

        A separate read rather than a fourth element of `captions_for`, whose
        shape callers and tests already unpack. Only a live track has these:
        the cache stores sentences, and a replay publishes a fresh live track
        of its own as it is spoken, so a replay gets measured timings too.
        """
        key = await self._cache_key(plan) if is_shareable(plan.query) else ""
        return live_captions.read_starts(key)

    def _count_play(self, key: str) -> None:
        """One real play of `key`, for Explore's count. Never raises."""
        counter = getattr(self.cache, "record_play", None)
        if key and counter is not None:
            try:
                counter(key)
            except Exception:  # noqa: BLE001 - a counter never costs a play
                log.debug("could not count a play of %s", key, exc_info=True)

    # ---- kept audio (§132) -------------------------------------------------

    def _keeps_audio(self) -> bool:
        """Whether this pipeline reads and writes finished audio.

        A production voice only (`TTSEngine.keeps_audio`), and a cache that
        knows how - a test double standing in for the script cache does not,
        and must not be asked to.
        """
        return bool(settings.audio_cache and self.cache is not None
                    and getattr(self.engine, "keeps_audio", False)
                    and hasattr(self.cache, "get_audio"))

    def _audio_voice(self) -> str:
        """The voice half of an audio row's key. The engine is named even when
        no voice was chosen, so a default that moves to a different engine is
        a different voice rather than somebody else's audio.

        **No voice chosen means the default voice, by its own id** (§134).
        The app sends `remote:reference_3` - the first voice `/api/voices`
        lists - while a shared link, a tap made before that list loaded and
        every other caller that names no voice sent nothing, and nothing was
        keyed `remote:default`. Same engine, same weights, same reference
        recording: one episode stored twice and voiced on RunPod twice, by
        the one knob whose whole job is to stop that. The default is resolved
        to the id the app would have sent, but only when it belongs to *this*
        engine - a default on some other engine is not what this one speaks.
        """
        if self.voice:
            return self.voice
        try:
            default = tts.default_voice() or ""
        except Exception:  # noqa: BLE001 - a key, never a reason to fail a play
            default = ""
        if default.split(":", 1)[0] == self.engine.name:
            return default
        return f"{self.engine.name}:default"

    async def has_stored_audio(self, plan: EpisodePlan) -> bool:
        """True when this exact episode can be played without the voice engine.

        For `/api/audio` to decide whether to wake a serverless GPU: waking one
        for an episode that will be read out of SQLite is paying for a boot
        nobody uses. A hint only, and it answers False rather than spend a
        model call when the key needs one. Since §134 a near match counts: see
        below.
        """
        if (not self._keeps_audio() or settings.cache_semantic_key
                or not is_shareable(plan.query) or plan.attachments):
            return False
        key = await self._cache_key(plan)
        if not key:
            return False
        voice, rate = self._audio_voice(), self.engine.sample_rate
        if self.cache.has_audio(key, voice, rate):
            return True
        # **A near match plays the neighbour's audio, so it must not wake the
        # GPU either** (§134). The serving path already swaps to the
        # neighbour's key and reads its stored audio; this hint answered for
        # the exact key only, so every re-phrased replay of a kept episode
        # booted a serverless worker to read nothing. The same local scan the
        # serving path makes (milliseconds, no model call), and only when the
        # exact key has no live script - otherwise the serving path would
        # never look at a neighbour and nor may this.
        if plan.cached_only or self.cache.get(key):
            return False
        bucket = self._bucket(plan)
        near = self.cache.nearest(bucket, plan.query) if bucket else None
        return bool(near) and self.cache.has_audio(near[0], voice, rate)

    async def _play_stored(self, stored, stats: GenerationStats) -> AsyncIterator[bytes]:
        """Stream kept audio exactly as it was first streamed. No engine call.

        In one-second slices rather than one blob, so it reaches the player
        through the same streaming path, primes the same pre-roll and can be
        cancelled mid-episode like anything else.
        """
        stats.audio = "stored"
        stats.script = list(stored.sentences)
        stats.starts = list(stored.starts)
        stats.sentences = len(stored.sentences)
        stats.words = sum(count_words(s) for s in stored.sentences)
        live_captions.publish(stats.caption_key, stored.sentences,
                              stored.starts if len(stored.starts)
                              == len(stored.sentences) else None)
        pcm = stored.pcm
        step = max(2, int(stats.sample_rate) * 2)
        stats.first_audio_at = time.perf_counter() - stats.started_at
        log.info("stored audio for this episode: %.1fs, no synthesis",
                 pcm_duration(len(pcm), stats.sample_rate))
        try:
            for i in range(0, len(pcm), step):
                yield pcm[i:i + step]
                await asyncio.sleep(0)
        finally:
            stats.audio_seconds = pcm_duration(len(pcm), stats.sample_rate)
            live_captions.close(stats.caption_key)

    async def _keep_audio(self, pcm: bytes, stats: GenerationStats) -> None:
        """Write this play's audio for next time. Never raises.

        Off the event loop: compressing ten minutes of PCM is a few hundred
        milliseconds, and it happens after the last byte has already gone.
        """
        try:
            kept = await asyncio.to_thread(
                self.cache.put_audio, stats.audio_key, self._audio_voice(),
                self.engine.sample_rate, pcm, list(stats.script),
                list(stats.starts))
            if kept:
                stats.audio = "kept"
                log.info("kept %.1fs of audio for %s", pcm_duration(
                    len(pcm), self.engine.sample_rate), stats.audio_key[:12])
        except Exception:
            log.exception("could not keep this episode's audio; continuing")

    async def stream_pcm(
        self, plan: EpisodePlan, stats: Optional[GenerationStats] = None
    ) -> AsyncIterator[bytes]:
        """Yield raw PCM for the whole episode, starting as soon as possible.

        Records what it yields when the audio may be kept, and keeps it once
        the whole episode has gone out - never a partial one, because a stream
        the listener abandoned stops here without reaching the end.
        """
        stats = stats if stats is not None else GenerationStats()
        recorded = bytearray() if (self._keeps_audio() and self.cache_writes) else None
        inner = self._stream_pcm(plan, stats)
        try:
            async for chunk in inner:
                if recorded is not None and stats.audio != "stored":
                    recorded.extend(chunk)
                yield chunk
        finally:
            # Closed here rather than left to the collector: the inner stream
            # owns the model call and the synthesis queue, and a listener who
            # walks away must stop both at once, exactly as before it was
            # wrapped.
            await inner.aclose()
        if recorded and stats.audio_key and stats.sentences:
            await self._keep_audio(bytes(recorded), stats)

    async def _stream_pcm(
        self, plan: EpisodePlan, stats: GenerationStats
    ) -> AsyncIterator[bytes]:
        # Somebody is waiting on this one. Prefetch reads the clock this sets
        # and stands aside - a speculative episode that delays a real one has
        # inverted the entire point of prefetching.
        prefetch.note_live_generation()
        stats.plan_seconds = plan.target_seconds
        stats.engine = self.engine.name
        stats.voice = self.voice or ""
        stats.sample_rate = self.engine.sample_rate

        pace = PaceController(
            target_seconds=float(plan.target_seconds),
            total_words=plan.word_budget,
            sample_rate=self.engine.sample_rate,
        )

        # --- Cache: has anyone already asked for this? --------------------
        shareable = is_shareable(plan.query)
        key = await self._cache_key(plan) if shareable else ""
        bucket = self._bucket(plan) if shareable else ""
        # Captions are published under the cache key, so they are available
        # while the episode is being spoken rather than only once the finished
        # script has been written. Opened here - before the cache is consulted
        # - so that a replay publishes too: the same panel reads both, and a
        # replayed episode that produced no live track would be the one case
        # where the sentences existed and nothing showed them.
        stats.caption_key = key
        live_captions.open_track(key)
        if self.cache and shareable:
            cached = self.cache.get(key)
            if cached:
                stats.match = "exact"
            elif bucket and not plan.cached_only:
                # Nobody has asked this in these words. Someone may have asked
                # it in different ones - which is most of what the cache misses,
                # since the key is an exact token set and people do not phrase
                # questions the same way twice.
                #
                # Not for `cached_only`: Explore offers a specific episode that
                # a listener has already seen the title of, and handing them a
                # near neighbour instead would be answering a question they did
                # not tap. A replay surface has to replay.
                near = self.cache.nearest(bucket, plan.query)
                if near:
                    cached = self.cache.get(near[0])
                    if cached:
                        key = near[0]
                        stats.match, stats.match_score = "near", near[1]
                        log.info("near cache hit %.3f for %r", near[1], plan.query)
            if cached:
                stats.cache = "hit"
                # Counted here, where an episode is about to be *played*, and
                # nowhere that merely looks: `get` also runs for the pacing
                # probe and for prefetch, which is why `hits` could never be
                # the number on an Explore card (§134).
                self._count_play(key)
                stats.thread = self.cache.thread(key)
                # A replay knows its name before its first word, so the player
                # can show it from the first frame (§127).
                # Under the *listener's* key, which is the one their player
                # asks with - on a near hit `key` is the neighbour's.
                live_captions.publish_title(
                    stats.caption_key,
                    getattr(self.cache, "title", lambda _k: "")(key),
                    final=True)
                # If prefetch put this here, the guess came true. Counted at
                # the moment of the hit and with the key that actually hit,
                # because "how much to prefetch" cannot be answered by
                # anything except the hit rate - and a rate inferred from
                # anywhere but the serving path is a rate nobody should trust.
                stats.prefetched = prefetch.note_consumed(key)
                log.info("cache %s hit for %r (%d min)%s", stats.match, plan.query,
                         plan.minutes, " [warmed ahead of the tap]"
                         if stats.prefetched else "")
                # The audio, if it has been made before in this voice: read
                # from the database and the voice engine is never called. That
                # is the whole of §132 - a cached episode used to cost a GPU
                # round trip on every play.
                if self._keeps_audio():
                    stored = self.cache.get_audio(key, self._audio_voice(),
                                                  self.engine.sample_rate)
                    if stored is not None and stored.pcm:
                        async for chunk in self._play_stored(stored, stats):
                            yield chunk
                        return
                    # Not kept yet - a script from before §132, another voice,
                    # or evicted. Synthesised once more, and kept this time.
                    stats.audio_key = key
                # Replaying the same sentences through the same controller
                # reproduces the episode - the same script, in the same order,
                # for zero API tokens. Not sample-identical under Phase 6: the
                # assembler batches partly on elapsed time, and a replay feeds
                # sentences instantly where the original was paced by a model,
                # so the chunk boundaries differ and with them the number of
                # inter-chunk gaps. Measured at 0.161s over three minutes.
                async for chunk in self._speak_pump(
                        self._pump_for(_replay(cached), stats, pace), pace, stats):
                    yield chunk
                async for chunk in self._finish(pace, stats):
                    yield chunk
                return
        if plan.cached_only:
            # Nothing to replay, and generating is exactly what this request
            # promised not to do.
            raise NotCached(
                "That episode is no longer in the cache. Explore only replays "
                "episodes other listeners have already generated."
            )

        stats.cache = "miss" if self.cache else "off"

        # Time-to-first-token is invisible from outside `stream_sentences`,
        # which yields whole sentences. Wrapping the client observes the first
        # delta and changes nothing about the request - the same wrapper Phase
        # 6 measured with. Guarded because a test generator has no client.
        client = getattr(self.generator, "client", None)
        if client is not None and not isinstance(client, TimedClient):
            self.generator.client = TimedClient(client, stats.marks)

        # --- Generate ------------------------------------------------------
        # Nothing is spoken until the real script arrives. The opener that used
        # to cover this wait is gone: see PROBLEMS.md 55.
        notes = ScriptNotes()
        # So the generator can publish sources the moment retrieval produces
        # them, rather than leaving the panel waiting for the cache write at
        # the end of the episode. Same key as the captions beside them.
        notes.caption_key = key
        # The same clock for the steps in front of the writing call - brief,
        # live lookup, retrieval - which `claude_ttft` used to swallow whole.
        notes.marks = stats.marks
        # Pointed at the live accumulator *now*, not when generation finishes.
        # `Usage` is mutable and shared, so this makes `stats.usage` track the
        # episode as it goes - which is the difference between an episode that
        # dies after Exa has already billed being recorded and being free.
        stats.usage = notes.usage

        # One call, one stream, and it does not start until the writer has
        # everything: the brief, the live state and the evidence are all
        # settled inside `stream_sentences`' own `prepare` step before the
        # first token. There used to be a second, tool-less call racing this
        # one to the first word - see PROBLEMS.md §108 for why the thing it
        # bought was not worth what it cost.
        stats.marks.mark("claude_start")
        body = self._pump_for(
            self.generator.stream_sentences(plan, notes), stats, pace)
        async for chunk in self._speak_pump(body, pace, stats):
            yield chunk

        # The model under-wrote. Rather than pad minutes of silence, buy more
        # script: a top-up request is small, cheap and arrives while the
        # listener is still hearing the material already generated.
        while (
            settings.allow_topups
            and not stats.truncated
            and pace.remaining_seconds > TOPUP_THRESHOLD
            and stats.topups < MAX_TOPUPS
        ):
            stats.topups += 1
            words_needed = int(pace.remaining_seconds / 60.0 * settings.target_wpm)
            log.info("topping up %d words for %.1fs of dead air", words_needed, pace.remaining_seconds)
            before = pace.spoken_words
            # A top-up is more of the same episode, so it must not re-mark a
            # completion that already happened.
            extra = self._pump_for(
                self.generator.top_up(plan, " ".join(stats.script), words_needed,
                                      notes),
                stats, pace, completion_mark="topup_complete")
            async for chunk in self._speak_pump(extra, pace, stats):
                yield chunk
            if pace.spoken_words == before:
                break  # the top-up produced nothing; stop asking

        stats.thread = notes.thread
        stats.title = notes.title
        stats.summary = notes.summary
        # The writer's own name replaces the brief's provisional one, on the
        # live track as well as in the cache - an episode that is not cached
        # (a live game, ttl 0) would otherwise keep the guess for good.
        live_captions.publish_title(stats.caption_key, notes.title, final=True)

        if self.cache and self.cache_writes and shareable and stats.script:
            # How long this stays true, from what the episode was actually
            # built from - carried home on `notes` because the plan this scope
            # holds is the unprepared one. Zero means the episode describes
            # something that is still moving and must not be written at all:
            # `recent()` is the Explore feed, so a cached in-progress episode
            # is not only re-served, it is published. PROBLEMS.md §89.
            ttl = ttl_for(plan.query, live_status=notes.live_status,
                          outcome_dependent=notes.outcome_dependent,
                          recency_days=notes.recency_days)
            if ttl > 0:
                # The shareable half only: an attachment's title is the
                # listener's own document, and the script cache is shared and
                # feeds Explore. `Provenance.shareable` drops anything private.
                sources = ""
                if notes.provenance is not None:
                    sources = notes.provenance.to_json()
                # The summary rides as a keyword and only when there is one,
                # so a cache written before it existed - and every test
                # double that stands in for one - is still called exactly as
                # it always was.
                extra = {"summary": stats.summary} if stats.summary else {}
                self.cache.put(key, stats.script, ttl, plan.query, stats.thread,
                               plan.minutes, bucket, sources, self.author,
                               stats.title, **extra)
                # The listen that wrote it is its first play (§134).
                self._count_play(key)
                # The audio goes beside it once the tail pad is out, and only
                # when the script itself was kept - audio with no script row
                # would be an episode nothing can find or expire.
                stats.audio_key = key
                log.info("cached %d sentences for %r (ttl %ds)",
                         len(stats.script), plan.query, ttl)
            else:
                log.info("not caching %r: the live state is %r, so this episode "
                         "is stale the moment it is written",
                         plan.query, notes.live_status or "unestablished")

        async for chunk in self._finish(pace, stats):
            yield chunk

    async def _finish(
        self, pace: PaceController, stats: GenerationStats
    ) -> AsyncIterator[bytes]:
        """Close any residual gap with room tone.

        A second or two of quiet at the end reads as the episode finishing; a
        hard cut reads as a bug.

        Also where the live caption track is closed, because this is the one
        place both paths reach - a replay returns straight after it, and a
        generation ends with it. Before the empty-episode return below, so an
        episode that produced no speech is reported as finished rather than
        leaving a caption panel waiting for sentences that are not coming.
        """
        live_captions.close(stats.caption_key)
        # Never pad an episode that has no speech in it. Doing so manufactures
        # a few seconds of silence that looks like a valid episode to every
        # layer above, which is how an empty script reached listeners as
        # "it generated something but I hear nothing".
        if stats.sentences == 0:
            stats.audio_seconds = pace.elapsed
            return
        shortfall = min(pace.remaining_seconds, MAX_TAIL_SILENCE)
        if shortfall > 0.05:
            pad = silence(shortfall, self.engine.sample_rate)
            pace.observe(len(pad), 0)
            yield pad
        stats.audio_seconds = pace.elapsed

    async def stream_wav(
        self, plan: EpisodePlan, stats: Optional[GenerationStats] = None
    ) -> AsyncIterator[bytes]:
        """Same stream, prefixed with a live WAV header for <audio> playback."""
        yield streaming_wav_header(sample_rate=self.engine.sample_rate)
        async for chunk in self.stream_pcm(plan, stats):
            yield chunk
