"""Explore names the wait in front of a card's first word (§360, option C).

Three steps under the title - Finding the episode, Downloading audio,
Starting - ticked from FamAudio's own `onStage` marks as each really happens.
Unlike search's steps (§148) nothing is held for them: no `startGate`, no
minimum time per step (`latency-start-earlier`, `motion`).
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
AUDIO = (ROOT / "static" / "fam-audio.js").read_text(encoding="utf-8")
PAGE = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _body(src: str, name: str) -> str:
    start = src.index("function " + name + "(")
    nxt = re.search(r"\n  function \w+\(", src[start + 10:])
    return src[start: start + 10 + (nxt.start() if nxt else len(src))]


def test_audio_says_each_stage_from_the_real_event():
    play = _body(AUDIO, "play")
    assert 'stage("found")' in play
    # Every way the bytes can arrive marks "arriving": the stream, and both
    # forms of a replay link.
    assert "arriving(res.body.getReader())" in play
    assert "arriving(file.body.getReader())" in _body(AUDIO, "playLinked")
    assert 'stage("arriving")' in _body(AUDIO, "playWhole")


def test_found_is_said_only_for_an_episode_the_server_gave():
    play = _body(AUDIO, "play")
    assert play.index("if (!res.ok)") < play.index('stage("found")')


def test_reel_wires_the_marks_and_holds_nothing():
    reel = _body(PAGE, "playReel")
    assert "onStage: steps.mark" in reel
    assert "startGate" not in reel
    assert "steps.done()" in reel.split("onFirstAudio")[1].split("onEnd")[0]
    assert "steps.done()" in reel.split("onError")[1]


def test_steps_have_no_clock_but_the_show_threshold():
    steps = _body(PAGE, "startReelSteps")
    # One timer, and it only decides whether to show them at all.
    assert steps.count("setTimeout(") == 1
    assert "REEL_STEPS_AFTER_MS" in steps
    assert "setInterval" not in steps
    assert 'id="reelSteps"' in PAGE
    assert '"Finding the episode", "Downloading audio", "Starting"' in PAGE


def test_a_stopped_card_clears_its_steps():
    assert "reelStepsRun.done()" in _body(PAGE, "stopReelProgress")
    # A redraw clears another card's steps, never its own still loading.
    assert "reelStepsRun.ep !== ep) reelStepsRun.done()" in _body(PAGE, "renderReel")
