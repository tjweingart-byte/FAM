"""A cold voice is waited for, woken by every episode, and never billed for
audio nobody will hear (§497).

The loading screen gave up a flat 45 s after the tap, and a cold serverless
worker alone takes longer than that (73 s measured), so episodes stuck at
"Generating the audio" and then said "Is the server running?" - two and three
times in a row. These pin the three halves of the fix:

* `/api/progress` tells the interface how long its voice may take
  (`voice_wait`, the server's REMOTE_VOICE_TIMEOUT) and the page waits that
  long once the script is handed over, instead of a flat 45 s;
* an episode's own wake is never throttled by the app-open wake;
* a RunPod job is cancelled when nobody will hear it, and `/runsync` hands
  back a job id early enough that a cold job can be cancelled at all.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import live_captions  # noqa: E402
import remote_voice  # noqa: E402
from remote_voice import RemoteChatterboxEngine, RemoteVoiceError  # noqa: E402
from tests.test_remote_voice import (  # noqa: E402
    FakeResponse, configure, install, run)

ROOT = pathlib.Path(__file__).resolve().parent.parent


# --- the interface waits for the voice, not a flat 45 s ---------------------

def test_progress_tells_the_page_how_long_the_voice_may_take(monkeypatch):
    from fastapi.testclient import TestClient

    import app as appmod

    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    remote = dataclasses.replace(config.settings, voice_backend="remote",
                                 remote_voice_transport="runpod",
                                 runpod_endpoint_id="e", runpod_api_key="k",
                                 remote_voice_timeout=180.0)
    monkeypatch.setattr(appmod, "settings", remote)
    assert appmod._voice_wait() == 180.0
    local = dataclasses.replace(config.settings, voice_backend="chatterbox")
    monkeypatch.setattr(appmod, "settings", local)
    assert appmod._voice_wait() is None, "a voice on this machine has no cold start"

    live_captions.reset()
    got = TestClient(appmod.app).get("/api/progress", params={
        "q": "why do bonds move", "minutes": 3}).json()
    assert "voice_wait" in got and "steps" in got, "the old fields stay"


def test_the_page_waits_for_the_voice_once_the_script_is_handed_over():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    # The limit is on silence from the server, moved by every step...
    assert "var GEN_QUIET_MS = 45000;" in index
    assert "if(moved) holdGenTimeout(Date.now() + GEN_QUIET_MS" in index
    # ...and becomes the server's own voice limit, never a copied number.
    assert "d.voice_wait * 1000" in index
    assert "st.handedAt + wait + GEN_VOICE_MARGIN_MS" in index
    # The flat timer from the tap is gone.
    assert "}, 45000);" not in index


def test_a_late_progress_answer_cannot_stop_a_playing_episode():
    """The gate clears the limit when audio arrives, while the list walks its
    last steps. A poll answered after that must not arm it again: the timer
    would fire mid-episode and call FamAudio.stop()."""
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "if(st !== genSteps || st.audioReady || !d || !d.steps) return;" in index
    assert "if(genSteps && genSteps.audioReady) return;" in index


# --- every episode wakes the voice ------------------------------------------

def test_an_episodes_wake_is_not_throttled_by_the_opens(monkeypatch):
    configure(monkeypatch)
    client = install(monkeypatch, FakeResponse({"id": "w1"}),
                     FakeResponse({"id": "w2"}))
    run(RemoteChatterboxEngine.wake())             # the app opened
    run(RemoteChatterboxEngine.wake())             # it came back: throttled
    run(RemoteChatterboxEngine.wake(force=True))   # an episode is being written
    assert len(client.posts) == 2


def test_the_audio_request_forces_its_wake():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "_wake_remote_voice(force=True)" in source


# --- nothing is billed for audio nobody will hear ---------------------------

def test_runsync_hands_back_a_job_id_early(monkeypatch):
    configure(monkeypatch)
    client = install(monkeypatch, FakeResponse({"status": "COMPLETED", "output": {
        "audio": "AQI=", "sample_rate": 24000, "format": "pcm_s16le"}}))
    run(RemoteChatterboxEngine().synth("Hello.", 150))
    assert client.posts[0]["url"].endswith(
        f"/runsync?wait={remote_voice.RUNSYNC_WAIT_MS}")
    assert 1000 <= remote_voice.RUNSYNC_WAIT_MS < 90000, "RunPod's default is 90 s"


def test_a_job_nobody_will_hear_is_cancelled(monkeypatch):
    """The listener left while the worker was still booting."""
    configure(monkeypatch, remote_voice_timeout=60.0)
    client = install(monkeypatch,
                     FakeResponse({"status": "IN_QUEUE", "id": "job-7"}),
                     FakeResponse({"status": "IN_QUEUE", "id": "job-7"}),
                     FakeResponse({"status": "IN_QUEUE", "id": "job-7"}),
                     FakeResponse({}))

    async def leave():
        task = asyncio.create_task(RemoteChatterboxEngine().synth("Hello.", 150))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)   # let the cancel go out
        await asyncio.sleep(0)

    run(leave())
    assert client.posts[-1]["url"].endswith("/cancel/job-7")


def test_a_job_past_the_voices_limit_is_cancelled_too(monkeypatch):
    configure(monkeypatch, remote_voice_timeout=0.0)
    client = install(monkeypatch,
                     FakeResponse({"status": "IN_QUEUE", "id": "job-8"}),
                     FakeResponse({}))

    async def speak():
        with pytest.raises(RemoteVoiceError, match="REMOTE_VOICE_TIMEOUT"):
            await RemoteChatterboxEngine().synth("Hello.", 150)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    run(speak())
    assert client.posts[-1]["url"].endswith("/cancel/job-8")


def test_a_cancel_that_fails_costs_nothing(monkeypatch):
    configure(monkeypatch, remote_voice_timeout=0.0)
    install(monkeypatch, FakeResponse({"status": "IN_QUEUE", "id": "job-9"}),
            ConnectionError("gone"))

    async def speak():
        with pytest.raises(RemoteVoiceError):
            await RemoteChatterboxEngine().synth("Hello.", 150)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    run(speak())   # the failed cancel raised nothing
