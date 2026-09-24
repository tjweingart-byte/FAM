"""The loading screen's five steps are read from the episode, not a clock (§147).

It used to guess its stage from elapsed time - "reading sources" at four
seconds, whatever the server was doing. The steps are now checked off from the
marks the pipeline records as each one really finishes, served by
`/api/progress` off the live track. These pin that path end to end, and the
two rules around it: asking can never start an episode, and a replay has no
steps to walk.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_captions  # noqa: E402
from episode_marks import EpisodeMarks  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
STEPS = ("contextualized", "retrieved", "verified", "written")


@pytest.fixture(autouse=True)
def clean():
    live_captions.reset()
    yield
    live_captions.reset()


def test_nothing_is_known_about_an_episode_nobody_is_making():
    got = live_captions.read_progress("nobody")
    assert got["known"] is False and not any(got["steps"].values())
    assert live_captions.read_progress("")["known"] is False


def test_each_step_is_checked_by_its_own_mark():
    marks = EpisodeMarks()
    live_captions.open_track("k")
    live_captions.attach_marks("k", marks)
    assert live_captions.read_progress("k") == {
        "known": True, "cached": False, "steps": dict.fromkeys(STEPS, False)}
    for i, (_, mark) in enumerate(live_captions.PROGRESS_STEPS):
        marks.mark(mark)
        steps = live_captions.read_progress("k")["steps"]
        assert [steps[s] for s in STEPS] == [n <= i for n in range(4)]


def test_the_list_only_fills_from_the_top():
    """A later mark implies every earlier step happened - a path that skips
    one mark (no brief on a degraded run) must not leave a hole in the list."""
    marks = EpisodeMarks()
    live_captions.open_track("k")
    live_captions.attach_marks("k", marks)
    marks.mark("claude_first_token")
    steps = live_captions.read_progress("k")["steps"]
    assert [steps[s] for s in STEPS] == [True, True, True, False]


def test_a_replay_has_nothing_to_walk():
    live_captions.open_track("k")
    live_captions.mark_cached("k")
    got = live_captions.read_progress("k")
    assert got["cached"] is True and all(got["steps"].values())


def test_restarting_a_track_forgets_the_last_episodes_marks():
    """The same key written again must not open on the old run's checks."""
    old = EpisodeMarks()
    old.mark("first_sentence")
    live_captions.open_track("k")
    live_captions.attach_marks("k", old)
    live_captions.open_track("k")
    assert live_captions.read_progress("k")["known"] is False


def test_the_progress_reader_never_reaches_the_generator():
    """Polled every 700ms through the whole wait - a read that could write
    would be an episode per poll."""
    import pipeline as pipeline_mod

    body = inspect.getsource(pipeline_mod.PodcastPipeline.progress_for)
    assert "self.generator" not in body and "stream_" not in body
    assert "self.cache" not in body


def test_a_written_episode_reports_its_steps_while_it_is_made():
    from cache import MemoryScriptCache
    from pipeline import GenerationStats, PodcastPipeline
    from script_generator import plan_episode
    from tts import DebugEngine

    from test_pipeline import FakeGenerator

    async def run():
        pipe = PodcastPipeline(generator=FakeGenerator(), engine=DebugEngine(),
                               cache=MemoryScriptCache())
        plan = plan_episode("what is the fed doing", 1, "", False)
        first = None
        stats = GenerationStats()
        async for _chunk in pipe.stream_pcm(plan, stats):
            if first is None:
                first = await pipe.progress_for(plan)
        live_captions.reset()          # as a fresh worker would start
        again = GenerationStats()
        replay = None
        async for _chunk in pipe.stream_pcm(plan, again):
            if replay is None:
                replay = await pipe.progress_for(plan)
        return stats.cache, first, again.cache, replay

    cache, first, again, replay = asyncio.run(run())
    assert cache == "miss" and again == "hit"
    # By the first audio every step before it has happened.
    assert first["known"] and not first["cached"] and all(first["steps"].values())
    assert replay["cached"] is True


def test_the_audio_response_says_whether_it_was_a_replay():
    """The loading screen holds a written episode's audio until its list is
    done and starts a replay at once - it can only tell them apart if told,
    and a browser can only read a header that is exposed."""
    source = (ROOT / "app.py").read_text()
    assert '"X-FAM-Cache": stats.cache' in source
    expose = source[source.index("expose_headers="):]
    assert '"X-FAM-Cache"' in expose[:200]


def test_the_interface_holds_the_audio_until_the_list_is_done():
    index = (ROOT / "static" / "index.html").read_text()
    audio = (ROOT / "static" / "fam-audio.js").read_text()
    assert "startGate: genStepsGate" in index
    assert "handlers.startGate" in audio
    # Every step on screen for at least two seconds - the owner's floor.
    assert "var GEN_STEP_MIN_MS = 2000;" in index
