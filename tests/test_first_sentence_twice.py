"""The opening sentence heard twice in a row (the owner, 2026-11-16).

"the beginning of some of the episodes sometimes repeat the first sentence
twice. So it'll sound something like this: 'X. X.'"

The first complete sentence goes to the voice alone, as the first chunk
(`speech_assembly`), so anything that makes the opening twice is heard as
exactly that. Three things are pinned here:

1. **The pipeline never voices a sentence twice by itself** - writing an
   episode, replaying its script in another voice, voicing it ahead (§610).
2. **A writer that says its opening again is not spoken twice**
   (`script_generator.RepeatGuard`): a stream, a batched answer, and the
   tandem's second writer call (§456) restating what it was told had been
   spoken. Each of these reached the voice twice before the guard.
3. **A voice that says a chunk twice is on /admin**
   (`PodcastPipeline._note_if_said_twice`): report only - the audio is the
   voice's - so the next one names its sentence.

No key and no voice engine: the writer is a stub stream, the voice a tone.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline as pipeline_mod  # noqa: E402
import pipeline_errors  # noqa: E402
import script_generator as sg  # noqa: E402
from cache import MemoryScriptCache  # noqa: E402
from pipeline import GenerationStats, PodcastPipeline  # noqa: E402
from script_generator import RepeatGuard, ScriptNotes, plan_episode  # noqa: E402
from tts import DebugEngine  # noqa: E402

import test_editor_tandem_456 as tandem  # noqa: E402
from test_audio_cache import lossless_packing  # noqa: E402,F401

X = "The Eagles beat the Cowboys by seventeen points on Sunday night."
Y = "Jalen Hurts threw two touchdowns before the half."
Z = "The division is now theirs to lose."
REST = [f"Detail number {i} is its own sentence about the game." for i in range(12)]


@pytest.fixture(autouse=True)
def _clean_errors():
    pipeline_errors.store().clear()
    yield
    pipeline_errors.store().clear()


def _kinds() -> set:
    return {(k["stage"], k["kind"]) for k in pipeline_errors.report()["kinds"]}


# --- the writer, as a stub stream --------------------------------------------

class _Final:
    stop_reason = "end_turn"
    usage = types.SimpleNamespace(input_tokens=100, output_tokens=50,
                                  cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0)
    model = "claude-haiku-5-5"

    def __init__(self, text=""):
        self.content = [types.SimpleNamespace(type="text", text=text)]


def _writer(text: str):
    """A `ScriptGenerator` whose writer call streams `text` a word at a time."""
    gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)

    class _Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        @property
        def text_stream(self):
            async def chunks():
                for word in text.split(" "):
                    yield word + " "
            return chunks()

        async def get_final_message(self):
            return _Final()

    gen.client = types.SimpleNamespace(messages=types.SimpleNamespace(
        stream=lambda **kw: _Stream()))
    return gen


def _written(text: str, notes=None) -> list:
    plan = dataclasses.replace(plan_episode("did the eagles win", 2), search=False)

    async def collect():
        return [s async for s in _writer(text).stream_prepared(plan, notes or ScriptNotes())]

    return asyncio.run(collect())


# --- 2. a writer that says its opening again ---------------------------------

def test_a_streamed_opening_said_twice_is_spoken_once():
    spoken = _written(" ".join([X, X, Y, Z]))
    assert spoken == [X, Y, Z]
    # And it is on /admin's errors, like every other check that caught the writer.
    assert ("writer", "repeat check removed sentences") in _kinds()


def test_punctuation_and_capitals_do_not_hide_a_repeat():
    again = X.rstrip(".").upper() + "!"
    assert _written(" ".join([X, again, Y])) == [X, Y]


def test_a_writer_that_starts_over_does_not_replay_its_opening():
    # "X. Y. X. Y. Z": the second run of the opening is dropped, both sentences.
    assert _written(" ".join([X, Y, X, Y, Z])) == [X, Y, Z]


def test_a_batched_answer_is_read_by_the_same_rule():
    plan = dataclasses.replace(plan_episode("did the eagles win", 2), search=False)
    gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    out = gen.sentences_from_message(plan, ScriptNotes(), _Final(" ".join([X, X, Y, Z])))
    assert out == [X, Y, Z]


def test_the_town_gap_line_is_not_said_again_by_the_writer(monkeypatch):
    gap = "Nothing new came out of Ardmore this week."
    monkeypatch.setattr(sg._ScriptReader, "opening", lambda self: (
        self.repeats.note(gap), [gap])[1])
    assert _written(" ".join([gap, X, Y])) == [gap, X, Y]


def test_the_guard_is_narrow():
    guard = RepeatGuard()
    # Short sentences are a writer's choice.
    assert guard.allow("No.") and guard.allow("No.")
    assert guard.allow("Not this time.") and guard.allow("Not this time.")
    # A different sentence that shares most of its words is spoken.
    guard = RepeatGuard()
    assert guard.allow(X)
    assert guard.allow(X.replace("seventeen", "eighteen"))
    assert not guard.allow(X) and guard.dropped == [X]
    # Past the head of the script it looks at nothing: a refrain is the writer's.
    guard = RepeatGuard()
    assert guard.allow(X)
    for i in range(RepeatGuard.WINDOW):
        assert guard.allow(f"Filler sentence number {i} goes here.")
    assert guard.allow(X)


def test_a_script_of_one_sentence_over_and_over_still_reaches_its_budget():
    """The budget valve counts what is spoken (§273's test streams one
    sentence 120 times): the guard drops the head's repeats and no more."""
    plan = plan_episode("q", 1)
    reader = sg._ScriptReader(plan, ScriptNotes())
    out = reader.feed(" ".join(["This is one more full sentence."] * 400) + " ")
    assert len(out) == 400 - (RepeatGuard.WINDOW - 1)
    assert reader.over_budget


def test_the_tandem_second_call_restating_the_opening_is_spoken_once(monkeypatch):
    """§456: the second writer call is handed what was spoken and told to
    carry on. One that says the opening again was heard as "X. X."."""
    tandem.settings_for(monkeypatch, words=8)
    tandem.shakespeare(monkeypatch, delay=0.05)
    opening = tandem.FIRST.split(". ")[0] + "."
    restated = opening + " " + tandem.SECOND
    gen, asked, _finals = tandem.generator(texts=(tandem.FIRST, restated))
    spoken, notes, _ = tandem.run(gen)
    assert notes.editor_tandem == "handed_over" and len(asked) == 2
    assert spoken[0] == opening
    assert spoken.count(opening) == 1
    assert spoken[-2:] == tandem.SECOND.replace(". ", ".\n").split("\n")


def test_the_tandem_hand_over_is_otherwise_what_it_was(monkeypatch):
    tandem.settings_for(monkeypatch, words=8)
    tandem.shakespeare(monkeypatch, delay=0.05)
    gen, _asked, _finals = tandem.generator()
    spoken, notes, _ = tandem.run(gen)
    assert notes.editor_tandem == "handed_over"
    assert spoken == [tandem.FIRST.split(". ")[0] + "."] \
        + tandem.SECOND.replace(". ", ".\n").split("\n")
    assert not any(kind.startswith("repeat check") for _stage, kind in _kinds())


# --- 1. the pipeline itself ---------------------------------------------------

class _Recording(DebugEngine):
    """A production-shaped voice that keeps what it was handed."""

    name = "recording"
    keeps_audio = True

    def __init__(self, gap: float = 0.0) -> None:
        self.said: list = []
        self.gap = gap

    async def synth(self, text, wpm, voice=None):
        self.said.append(text)
        await asyncio.sleep(self.gap)
        return await super().synth(text, wpm, voice)


class _Sentences:
    """A generator that yields `sentences`, some time apart."""

    def __init__(self, sentences, gap: float = 0.0):
        self.sentences, self.gap = list(sentences), gap

    async def stream_sentences(self, plan, notes=None):
        for sentence in self.sentences:
            await asyncio.sleep(self.gap)
            yield sentence

    async def top_up(self, *args, **kwargs):
        return
        yield ""  # pragma: no cover


def _voiced_once(engine) -> bool:
    return " ".join(engine.said).count(X) == 1


@pytest.mark.parametrize("writer_gap,voice_gap", [(0.0, 0.0), (0.02, 0.06), (0.06, 0.02)])
def test_no_path_of_the_pipeline_voices_the_opening_twice(writer_gap, voice_gap):
    """Written, replayed from its script in another voice, and voiced ahead
    (§610): each hands the opening to the voice exactly once, first and alone."""
    script = [X, Y, Z] + REST
    cache = MemoryScriptCache()
    plan = plan_episode("did the eagles win", 2)

    async def main():
        first = _Recording(voice_gap)
        pipe = PodcastPipeline(generator=_Sentences(script, writer_gap),
                               engine=first, cache=cache)
        stats = GenerationStats()
        async for _ in pipe.stream_pcm(plan, stats):
            pass
        assert first.said[0] == X, "the opening is the first chunk, on its own"
        assert _voiced_once(first)
        assert stats.script[:3] == [X, Y, Z] and stats.script.count(X) == 1

        replay = _Recording(voice_gap)
        pipe = PodcastPipeline(generator=_Sentences([]), engine=replay,
                               cache=cache, voice="recording:other")
        again = GenerationStats()
        async for _ in pipe.stream_pcm(plan, again):
            pass
        assert again.cache == "hit" and _voiced_once(replay)

        ahead = _Recording(voice_gap)
        pipe = PodcastPipeline(generator=_Sentences([]), engine=ahead,
                               cache=cache, voice="recording:third")

        async def free() -> None:
            return None

        assert await pipe.voice_ahead(stats.audio_key, plan.query, 2, free) == "voiced"
        assert _voiced_once(ahead)
        kept = cache.get_audio(stats.audio_key, "recording:third", ahead.sample_rate)
        assert kept is not None and list(kept.sentences).count(X) == 1

    asyncio.run(main())


def test_a_writer_that_repeats_its_opening_reaches_the_voice_once():
    """End to end: the writer's stream says "X. X. Y.", the voice is handed
    X once, and the kept script holds it once."""
    gen = _writer(" ".join([X, X, Y, Z] + REST))

    async def prepared(plan, notes=None):
        return plan

    gen.prepare = prepared
    engine = _Recording()
    cache = MemoryScriptCache()
    pipe = PodcastPipeline(generator=gen, engine=engine, cache=cache)
    plan = dataclasses.replace(plan_episode("did the eagles win", 2), search=False)
    stats = GenerationStats()

    async def main():
        async for _ in pipe.stream_pcm(plan, stats):
            pass

    asyncio.run(main())
    assert engine.said[0] == X and _voiced_once(engine)
    assert stats.script.count(X) == 1
    assert cache.get(stats.audio_key).count(X) == 1


# --- 3. a voice that says a chunk twice ----------------------------------------

class _SaysTheOpeningTwice(_Recording):
    """The voice reads its first chunk, then reads it again."""

    async def synth(self, text, wpm, voice=None):
        pcm = await super().synth(text, wpm, voice)
        return pcm + pcm if len(self.said) == 1 else pcm


def _play(engine) -> GenerationStats:
    pipe = PodcastPipeline(generator=_Sentences([X, Y, Z] + REST), engine=engine,
                           cache=MemoryScriptCache())
    stats = GenerationStats()

    async def main():
        async for _ in pipe.stream_pcm(plan_episode("did the eagles win", 2), stats):
            pass

    asyncio.run(main())
    return stats


def test_a_voice_that_says_the_first_chunk_twice_is_on_admin():
    stats = _play(_SaysTheOpeningTwice())
    assert stats.said_twice
    rows = [k for k in pipeline_errors.report()["kinds"]
            if k["stage"] == "voice" and k["kind"] == "a chunk ran twice its length"]
    assert len(rows) == 1 and rows[0]["week"] == 1, "once an episode"
    assert X in rows[0]["last_detail"] and "the first chunk" in rows[0]["last_detail"]
    said = pipeline_errors.explain("voice", "a chunk ran twice its length")
    assert said["title"] == "The voice took twice as long over a chunk"
    # Report only: the script and the captions still hold the sentence once.
    assert stats.script.count(X) == 1


def test_an_ordinary_voice_is_not_reported():
    stats = _play(_Recording())
    assert not stats.said_twice
    assert ("voice", "a chunk ran twice its length") not in _kinds()


def test_a_placeholder_tone_is_never_measured():
    class Tone(_SaysTheOpeningTwice):
        keeps_audio = False

    assert not _play(Tone()).said_twice


def test_the_measure_is_the_planned_rate(monkeypatch):
    """One and a half times as long is a slow reading, not a second one."""
    class Slow(_Recording):
        async def synth(self, text, wpm, voice=None):
            return await super().synth(text, wpm / 1.4, voice)

    assert pipeline_mod.SAID_TWICE_RATIO > 1.4
    assert not _play(Slow()).said_twice


def test_both_checks_are_explained_on_the_errors_page():
    assert pipeline_errors.explain(
        "writer", "repeat check removed 1 sentence")["title"] == \
        "The repeat check removed a sentence"
