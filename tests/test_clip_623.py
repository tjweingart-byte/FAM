"""§623: an episode's opening as a captioned video clip to post natively.

`clip.py` cuts 15-30 seconds from audio the cache already keeps and sets the
words on screen from the measured sentence timings. These pin what it must
never do: voice or guess anything, stop mid-sentence, or run for a stranger.
"""
from __future__ import annotations

import io
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import clip  # noqa: E402

RATE = 24000
SENTENCES = ["A man who had not spoken in eleven years said good morning to his daughter.",
             "The implant reads what the brain is trying to make the lips do.",
             "A model turns that into text at sixty words a minute.",
             "The catch is the surgery.", "And the price.", "Which nobody has set."]
STARTS = [0.4, 8.9, 15.6, 22.0, 27.5, 33.0]


def _pcm(seconds: float) -> bytes:
    import numpy as np

    t = np.arange(int(seconds * RATE)) / RATE
    return (np.sin(2 * np.pi * 220 * t) * 6000).astype("<i2").tobytes()


def test_the_clip_ends_where_a_sentence_ends_inside_thirty_seconds():
    # Sentence 4 ends at 27.5; sentence 5 would end at 33, past the limit.
    assert clip.opening(STARTS, 40.0) == (4, 27.5)
    # The last sentence ends where the audio does.
    assert clip.opening([0.0, 5.0], 12.0) == (2, 12.0)


def test_no_timings_or_a_first_sentence_too_long_is_no_clip():
    assert clip.opening([], 40.0) == (0, 0.0)
    assert clip.opening([0.0, 31.0], 60.0) == (0, 0.0)
    assert clip.opening([5.0, 2.0], 40.0) == (0, 0.0)  # not in order
    assert clip.opening(["x"], 40.0) == (0, 0.0)
    assert clip.render(_pcm(40), RATE, SENTENCES, [], None, "T") is None
    assert clip.render(_pcm(40), RATE, SENTENCES, STARTS[:3], None, "T") is None


def test_captions_stay_inside_their_own_sentences_time():
    shown = clip.cues(SENTENCES, STARTS, 4, 27.5)
    assert " ".join(c[2] for c in shown) == " ".join(SENTENCES[:4])
    assert all(len(c[2].split()) <= clip.CUE_WORDS + 2 for c in shown)
    assert shown[0][0] == STARTS[0] and abs(shown[-1][1] - 27.5) < 1e-6
    for (_a, end, _w), (start, _b, _x) in zip(shown, shown[1:]):
        assert abs(end - start) < 1e-6
    # The piece that opens sentence 2 starts exactly where sentence 2 does.
    second = next(c for c in shown if c[2].startswith("The implant"))
    assert abs(second[0] - STARTS[1]) < 1e-6


def test_the_clip_is_an_mp4_with_the_opening_and_an_end_card():
    import av

    data = clip.render(_pcm(40), RATE, SENTENCES, STARTS, None,
                       "Brain Implants Giving Paralyzed Patients a Voice", "fam.audio")
    assert data and data[4:8] == b"ftyp"
    with av.open(io.BytesIO(data)) as c:
        video, audio = c.streams.video[0], c.streams.audio[0]
        assert (video.width, video.height) == clip.SIZE
        assert video.codec_context.name == "h264" and audio.codec_context.name == "aac"
        length = float(video.duration * video.time_base)
    assert abs(length - (27.5 + clip.END_CARD_SECONDS)) < 0.3
    assert clip.SHORTEST <= 27.5 <= clip.LONGEST


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FAM_DATA_DIR", str(tmp_path))
    store = appmod.sharing.ShareStore(str(tmp_path / "shares.db"))
    monkeypatch.setattr(appmod, "SHARES", store)
    appmod._CLIPS.clear()
    return TestClient(appmod.app)


def _share(client) -> str:
    made = client.post("/api/share", json={
        "query": "how do brain implants restore speech", "minutes": 2, "title": "A Voice"})
    return made.json()["share"]["id"]


def test_a_stranger_cannot_start_a_clip(client):
    """A clip is seconds of processor time: admin only, unlike the photo."""
    shared = _share(client)
    assert client.get(f"/api/admin/share/{shared}/clip.mp4").status_code in (401, 403, 404)


def test_an_episode_with_no_kept_audio_has_no_clip_and_says_why(client, monkeypatch):
    monkeypatch.setattr(appmod, "_require_admin", lambda request: None)
    shared = _share(client)
    monkeypatch.setattr(appmod, "_clip_audio", lambda record: None)
    got = client.get(f"/api/admin/share/{shared}/clip.mp4")
    assert got.status_code == 409 and "no kept audio" in got.json()["error"]
    assert client.get("/api/admin/share/nope/clip.mp4").status_code == 404


def test_the_clip_is_cut_from_kept_audio_and_saved_as_a_file(client, monkeypatch):
    import cache

    monkeypatch.setattr(appmod, "_require_admin", lambda request: None)
    shared = _share(client)
    kept = cache.StoredAudio(blob=b"", sample_rate=RATE, sentences=SENTENCES,
                             starts=STARTS)
    monkeypatch.setattr(cache.StoredAudio, "pcm", lambda self: _pcm(40))
    asked = []
    monkeypatch.setattr(appmod, "_clip_audio", lambda record: asked.append(1) or kept)
    got = client.get(f"/api/admin/share/{shared}/clip.mp4")
    assert got.status_code == 200 and got.headers["content-type"] == "video/mp4"
    assert got.headers["content-disposition"].endswith('.mp4"')
    assert got.content[4:8] == b"ftyp"
    # Asked again, it is the clip already made.
    assert client.get(f"/api/admin/share/{shared}/clip.mp4").content == got.content
    assert asked == [1]
    # Audio kept without timings: no guessed captions.
    appmod._CLIPS.clear()
    kept.starts = []
    got = client.get(f"/api/admin/share/{shared}/clip.mp4")
    assert got.status_code == 409 and "sentence timings" in got.json()["error"]


def test_the_clip_never_voices_anything():
    """It reads the cache; the words `engine.synth`/`pipeline.stream` are not
    in the module, and the endpoint builds no pipeline."""
    import inspect

    source = inspect.getsource(clip)
    assert "anthropic" not in source and "pipeline" not in source
    body = inspect.getsource(appmod._clip_audio)
    assert "get_audio" in body and "put_audio" not in body and "_make_pipeline" not in body


def test_the_outreach_card_offers_the_clip():
    import pathlib

    page = (pathlib.Path(__file__).resolve().parent.parent
            / "admin_ui" / "groups.html").read_text()
    assert "Video clip for a post" in page
    assert "/api/admin/share/$1/clip.mp4" in page
    # Same origin, whatever host the share link names: the pattern eats the
    # link's scheme and host, so the admin's session goes with the request.
    assert 'replace(/^.*\\/s\\/' in page
