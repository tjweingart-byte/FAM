"""Captions build up while the episode is spoken, not after it is cached.

The reported bug: turning captions on produced "Catching up with the script…"
and then, twelve seconds later, "No transcript for this one - an episode you
attached a file to is never stored". Neither sentence was true. The episode was
an ordinary researched one, it was being written normally, and the panel was
reading the script *cache* - which is written once, at the end.

So the failure was structural rather than a poll count: polling a place the
answer is not yet in cannot be fixed by polling it more. These tests pin the
two halves of the fix - that sentences are published as they are handed to the
voice, and that the endpoint prefers that live track to the cache.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_captions  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    live_captions.reset()
    yield
    live_captions.reset()


# --- the registry ---------------------------------------------------------


def test_nothing_is_known_about_an_episode_nobody_is_speaking():
    """None rather than an empty list: "no track" and "a track with nothing in
    it yet" send the caller to different places."""
    assert live_captions.read("nobody") is None


def test_sentences_appear_as_they_are_spoken():
    live_captions.open_track("k")
    assert live_captions.read("k") == ([], False)
    live_captions.publish("k", ["One."])
    assert live_captions.read("k") == (["One."], False)
    live_captions.publish("k", ["Two.", "Three."])
    assert live_captions.read("k") == (["One.", "Two.", "Three."], False)


def test_finishing_is_said_rather_than_inferred():
    """Without it, "still being written" and "that is the whole episode" are
    the same answer - which is the guess the twelve-second poll was making."""
    live_captions.open_track("k")
    live_captions.publish("k", ["One."])
    assert live_captions.read("k")[1] is False
    live_captions.close("k")
    assert live_captions.read("k") == (["One."], True)


def test_generating_the_same_episode_again_starts_a_new_track():
    """A cache entry expires and the same key is generated again. Appending to
    the first run's sentences would show the episode twice, once stale."""
    live_captions.open_track("k")
    live_captions.publish("k", ["Old."])
    live_captions.close("k")
    live_captions.open_track("k")
    assert live_captions.read("k") == ([], False)


def test_an_episode_with_no_key_is_not_tracked():
    """An attachment is deliberately uncacheable, so it has no key and no
    captions - that stays one rule rather than becoming a special case."""
    live_captions.open_track("")
    live_captions.publish("", ["Private."])
    live_captions.close("")
    assert live_captions.read("") is None
    assert not live_captions._TRACKS


def test_blank_sentences_are_not_published():
    live_captions.open_track("k")
    live_captions.publish("k", ["", "   ", "Real."])
    assert live_captions.read("k") == (["Real."], False)


def test_publishing_without_opening_still_works():
    """The speaking path must never depend on an ordering it cannot enforce: a
    missing caption is a missing caption, never a dropped episode."""
    live_captions.publish("k", ["One."])
    assert live_captions.read("k") == (["One."], False)


def test_the_table_is_bounded():
    for i in range(live_captions.MAX_TRACKS + 20):
        live_captions.open_track(f"k{i}")
        live_captions.publish(f"k{i}", ["x"])
    assert len(live_captions._TRACKS) <= live_captions.MAX_TRACKS


def test_a_finished_track_is_dropped_once_nobody_comes_back(monkeypatch):
    """The cache holds it after this, so expiry costs a listener nothing."""
    live_captions.open_track("k")
    live_captions.publish("k", ["One."])
    live_captions.close("k")
    with live_captions._LOCK:
        live_captions._TRACKS["k"].touched -= live_captions.KEEP_SECONDS + 1
    assert live_captions.read("k") is None


def test_an_unfinished_track_is_never_expired():
    """A slow researched episode is still being written; dropping it would
    put the panel back on the cache, which is exactly what did not work."""
    live_captions.open_track("k")
    live_captions.publish("k", ["One."])
    with live_captions._LOCK:
        live_captions._TRACKS["k"].touched -= live_captions.KEEP_SECONDS * 10
    assert live_captions.read("k") == (["One."], False)


# --- the pipeline publishes, and the endpoint prefers it ------------------


def test_the_speaking_path_publishes_every_sentence():
    """Read off the pipeline's own source rather than by generating an
    episode: the claim is that both speaking paths publish, and one of them is
    the legacy path that a production run never enters."""
    import pathlib

    source = (pathlib.Path(__file__).resolve().parent.parent / "pipeline.py").read_text()
    for marker in ("stats.script.extend(fit.spoken)", "stats.script.append(sentence)"):
        at = source.index(marker)
        after = source[at:at + 400]
        assert "live_captions.publish" in after, (
            f"{marker!r} appends to the script without publishing a caption; "
            "a sentence on its way to the voice is the whole of what this "
            "feature shows")


def test_the_caption_reader_never_reaches_the_generator():
    """The rule this shares with `script_for`: a caption track that could
    trigger a write would be a second full Claude call for every episode
    somebody chose to read along with."""
    import inspect

    import pipeline as pipeline_mod

    body = inspect.getsource(pipeline_mod.PodcastPipeline.captions_for)
    assert "self.generator" not in body
    assert "stream_sentences" not in body


# --- end to end, through a real generation --------------------------------


def test_the_transcript_builds_up_while_the_episode_is_spoken():
    """The whole point, asserted against the pipeline rather than the registry:
    a listener reading along sees sentences arrive, instead of nothing at all
    until the finished script reaches the cache."""
    import asyncio

    from cache import MemoryScriptCache
    from pipeline import GenerationStats, PodcastPipeline
    from script_generator import plan_episode
    from tts import DebugEngine

    from test_pipeline import FakeGenerator

    async def run():
        pipe = PodcastPipeline(generator=FakeGenerator(), engine=DebugEngine(),
                               cache=MemoryScriptCache())
        plan = plan_episode("what is the fed doing", 1, "", False)
        counts = []
        async for _chunk in pipe.stream_pcm(plan, GenerationStats()):
            sentences, live, done = await pipe.captions_for(plan)
            counts.append((len(sentences), live, done))
        return counts, await pipe.captions_for(plan)

    counts, (final, live, done) = asyncio.run(run())
    assert counts, "the episode produced no audio, so nothing was measured"
    first_seen = counts[0][0]
    assert first_seen, "no caption was available while the first audio played"
    assert counts[-1][0] > first_seen, "the transcript never grew"
    assert all(c[1] for c in counts), "captions did not come from the live track"
    assert not counts[0][2], "the episode was reported finished on its first chunk"
    assert done and live and len(final) == counts[-1][0]


def test_a_replayed_episode_still_has_its_captions():
    """A replay speaks from the cache, so the sentences exist before the first
    word. The panel must not care which of the two it is reading."""
    import asyncio

    from cache import MemoryScriptCache
    from pipeline import GenerationStats, PodcastPipeline
    from script_generator import plan_episode
    from tts import DebugEngine

    from test_pipeline import FakeGenerator

    async def run():
        pipe = PodcastPipeline(generator=FakeGenerator(), engine=DebugEngine(),
                               cache=MemoryScriptCache())
        plan = plan_episode("what is the fed doing", 1, "", False)
        async for _chunk in pipe.stream_pcm(plan, GenerationStats()):
            pass
        written = len((await pipe.captions_for(plan))[0])
        live_captions.reset()          # as a fresh worker would start
        again = GenerationStats()
        async for _chunk in pipe.stream_pcm(plan, again):
            pass
        return written, again.cache, await pipe.captions_for(plan)

    written, cache, (sentences, _live, done) = asyncio.run(run())
    assert cache == "hit", "the second run generated instead of replaying"
    assert len(sentences) == written and done


# --- and the sources panel, which had the same shape of bug ---------------


def test_sources_are_readable_before_the_episode_is_cached():
    live_captions.open_track("k")
    assert live_captions.read_sources("k") == ""
    live_captions.publish_sources("k", '{"items": []}')
    assert live_captions.read_sources("k") == '{"items": []}'


def test_publishing_sources_twice_keeps_the_later_one():
    """Retrieval knows some of it before the first sentence and the model's own
    tool search adds to it at the end; the second call is the fuller answer."""
    live_captions.open_track("k")
    live_captions.publish_sources("k", "first")
    live_captions.publish_sources("k", "first and second")
    assert live_captions.read_sources("k") == "first and second"


def test_an_episode_with_no_key_publishes_no_sources():
    live_captions.publish_sources("", "anything")
    assert live_captions.read_sources("") == ""
    assert not live_captions._TRACKS


def test_empty_provenance_is_not_published_over_a_real_answer():
    """`to_json` returns "" when there is nothing to say, and a blank must not
    wipe a strip that is already showing publishers - the same rule the
    interface keeps when its first fetch lands early."""
    live_captions.open_track("k")
    live_captions.publish_sources("k", '{"items": [1]}')
    live_captions.publish_sources("k", "")
    assert live_captions.read_sources("k") == '{"items": [1]}'


def test_the_pipeline_reads_the_live_sources_before_the_cache():
    import inspect

    import pipeline as pipeline_mod

    body = inspect.getsource(pipeline_mod.PodcastPipeline.sources_for)
    assert body.index("live_captions.read_sources") < body.index('getattr(self.cache, "sources"')
