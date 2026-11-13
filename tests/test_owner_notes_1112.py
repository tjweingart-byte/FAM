"""The owner's 11.12 notes (PROBLEMS.md §590): the place is optional, Return
sends a message, a message's banner returns to an open chat rather than
stacking it, the link picture wears the FAM logo, a story carries the player
card, and each chunk of an episode is levelled to one pace."""
from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as appmod  # noqa: E402
import link_preview  # noqa: E402
import pace_level  # noqa: E402
import sharing  # noqa: E402

INDEX = (ROOT / "static" / "index.html").read_text()
IOS = ROOT / "ios" / "FAM" / "FAM"


def swift(name: str) -> str:
    return next(IOS.rglob(name)).read_text()


# --- the place is optional ------------------------------------------------

def test_the_place_says_optional_wherever_it_is_asked():
    """The activation card and the identity step both mark the place (and
    the activation card the gender) "optional", on the web and the iPhone;
    the server never needed it."""
    assert re.search(r'for="actPlace">Where you live <span class="auth-opt">optional</span>', INDEX)
    assert re.search(r'class="act-label">Gender <span class="auth-opt">optional</span>', INDEX)
    assert re.search(r'Where you are <span class="auth-opt">optional</span></div>\s*'
                     r'<input class="modal-input" id="identityCity"', INDEX)
    settings_view = swift("SettingsView.swift")
    assert 'FieldLabel(text: "Where you live", optional: true)' in settings_view
    assert 'FieldLabel(text: "Gender", optional: true)' in settings_view
    assert 'FieldLabel(text: "Where you are", optional: true)' in swift("ProfileEdit.swift")
    assert "var optional = false" in swift("YouParts.swift")


def test_the_activation_step_saves_with_no_place(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as client:
        client.post("/api/auth/signup", json={"email": "p1112@b.com", "password": "password12"})
        got = client.post("/api/activation", json={"birth_date": "1990-04-02"})
        assert got.status_code == 200, got.text
        assert got.json()["needed"] is False


# --- Return sends ----------------------------------------------------------

def test_return_sends_on_the_iphone():
    chat = swift("ChatView.swift")
    assert "Self.returnPressed(old, now)" in chat
    assert ".submitLabel(.send)" in chat
    assert "static func returnPressed" in chat


# --- a banner returns to the open chat ------------------------------------

def test_a_banner_for_an_open_chat_goes_back_to_it():
    social = swift("Social.swift")
    assert "returnToThread(chat.with)" in social
    body = social.split("private func returnToThread", 1)[1].split("\n    }\n", 1)[0]
    assert "coverPath = Array(coverPath.prefix(i + 1))" in body
    assert "messagesPath = Array(messagesPath.prefix(i + 1))" in body


# --- the link picture's logo ----------------------------------------------

def test_the_badge_is_the_drawn_wordmark_not_typed_letters():
    """The plate is where it was; the wordmark's copper strokes are inside
    it, and the badge no longer types "FAM" in a font."""
    from PIL import Image

    src = io.BytesIO()
    Image.new("RGB", (800, 600), (230, 230, 230)).save(src, format="PNG")
    im = Image.open(io.BytesIO(link_preview.draw(src.getvalue(), "T", 1))).convert("RGB")
    left, top, right, bottom = link_preview.BADGE
    copper = 0
    for x in range(left + 8, right - 8, 3):
        for y in range(top + 8, bottom - 8, 3):
            r, g, b = im.getpixel((x, y))
            copper += r > 180 and 140 < g < 210 and b < 140
    assert copper > 40, "no wordmark in the badge"
    # Outside the plate, the picture is untouched.
    assert im.getpixel((right + 30, top + 30))[0] > 200
    source = (ROOT / "link_preview.py").read_text()
    badge = source.split("def _badge", 1)[1].split("\ndef ", 1)[0]
    assert 'd.text(' not in badge and '"FAM"' not in badge


def test_the_wordmark_fits_its_plate():
    left, top, right, bottom = link_preview.BADGE
    assert link_preview.wordmark_width(link_preview.BADGE_CAP) < right - left - 20
    assert link_preview.BADGE_CAP < bottom - top - 20


# --- the player card in a story -------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "SHARES", sharing.ShareStore(str(tmp_path / "shares.db")))
    return TestClient(appmod.app)


@pytest.fixture
def shared(client):
    got = client.post("/api/share", json={
        "query": "what nvidia announced", "minutes": 1, "title": "Nvidia's Latest Moves"})
    assert got.status_code == 200
    return got.json()


def test_a_share_names_its_story_pictures(shared):
    sid = shared["share"]["id"]
    assert shared["story_sticker"].endswith(f"/api/share/{sid}/sticker.png")
    assert shared["story_image"].endswith(f"/api/share/{sid}/story.jpg")
    # The words-only card is still there for installed builds.
    assert "/api/share/card?share=" in shared["card"]


def test_the_sticker_is_the_player_card_with_clear_corners(client, shared):
    from PIL import Image

    got = client.get(f"/api/share/{shared['share']['id']}/sticker.png")
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"
    im = Image.open(io.BytesIO(got.content))
    assert im.mode == "RGBA" and im.size[0] == link_preview.CARD_WIDTH
    assert im.size[1] > im.size[0], "no title strip under the picture"
    assert im.getpixel((0, 0))[3] == 0, "the corner is not clear"
    assert im.getpixel((im.size[0] // 2, im.size[1] - 20))[3] == 255


def test_the_story_is_the_card_on_nine_by_sixteen(client, shared):
    from PIL import Image

    got = client.get(f"/api/share/{shared['share']['id']}/story.jpg")
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
    im = Image.open(io.BytesIO(got.content))
    assert im.size == link_preview.STORY


def test_the_story_pictures_pass_the_waitlist_gate(client, shared):
    from starlette.requests import Request

    sid = shared["share"]["id"]
    for name in ("sticker.png", "story.jpg", "preview.jpg"):
        scope = {"type": "http", "method": "GET", "path": f"/api/share/{sid}/{name}",
                 "query_string": b"", "headers": []}
        assert appmod._shared_episode_request(Request(scope)), name


def test_an_unknown_share_has_no_story(client):
    assert client.get("/api/share/nope/story.jpg").status_code == 404
    assert client.get("/api/share/nope/sticker.png").status_code == 404


def test_the_iphone_hands_instagram_the_card_as_a_sticker():
    handoff = swift("StoryHandoff.swift")
    assert '"com.instagram.sharedSticker.stickerImage": sticker' in handoff
    assert "com.instagram.sharedSticker.backgroundTopColor" in handoff
    share = swift("SocialShare.swift")
    assert "api.shareImage(story)" in share
    assert "handoff.send(png: png, sticker: sticker, caption: text)" in share
    # The share sheet gets the picture alone; the words wait on the clipboard.
    assert "activity = [file]\n" in share
    api = swift("API.swift")
    assert "case url, targets, card, storySticker, storyImage, `public`" in api
    assert "storyImage: link.storyImage" in swift("PlayerSheets.swift")


def test_the_web_story_uses_the_drawn_story():
    fn = INDEX.split("function shareStoryCard(t){", 1)[1].split("\n  }\n", 1)[0]
    assert "shareTargets.story_image" in fn and "r.blob()" in fn


# --- one pace ---------------------------------------------------------------

def _tone(seconds: float, sr: int = 24000) -> bytes:
    import numpy as np

    t = np.arange(int(sr * seconds)) / sr
    wave = 0.3 * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    return (wave * 32767).astype("<i2").tobytes()


def test_stretch_changes_length_not_pitch():
    import numpy as np

    sr = 24000
    pcm = _tone(4, sr)
    for speed in (0.87, 1.15):
        out = np.frombuffer(pace_level.stretch(pcm, sr, speed), "<i2").astype(float)
        assert abs(len(out) / (len(pcm) / 2) - 1 / speed) < 0.03
        freqs = np.fft.rfftfreq(len(out), 1 / sr)
        assert abs(freqs[np.argmax(abs(np.fft.rfft(out)))] - 180) < 3


def test_the_first_chunk_sets_the_pace_and_a_fast_one_is_slowed():
    sr = 24000
    words = "a" * 80
    leveller = pace_level.PaceLeveller(enabled=True, most=0.15, deadband=0.04)
    first = _tone(4, sr)
    assert leveller.level(first, sr, words) == first
    fast = pace_level.stretch(_tone(4, sr), sr, 1.3)
    out = leveller.level(fast, sr, words)
    assert len(out) > len(fast) * 1.08, "a fast chunk was not slowed"
    # Never more than the most either way.
    assert len(out) < len(fast) * 1.2


def test_a_chunk_close_to_the_pace_is_left_alone():
    sr = 24000
    words = "a" * 80
    leveller = pace_level.PaceLeveller(enabled=True, most=0.15, deadband=0.04)
    leveller.level(_tone(4, sr), sr, words)
    near = pace_level.stretch(_tone(4, sr), sr, 1.03)
    assert leveller.level(near, sr, words) == near


def test_off_and_short_chunks_are_untouched():
    sr = 24000
    fast = pace_level.stretch(_tone(4, sr), sr, 1.3)
    off = pace_level.PaceLeveller(enabled=False)
    off.level(_tone(4, sr), sr, "a" * 80)
    assert off.level(fast, sr, "a" * 80) == fast
    short = pace_level.PaceLeveller(enabled=True)
    assert short.level(_tone(0.5, sr), sr, "a" * 80) == _tone(0.5, sr)
    assert short.rates == []


def test_only_engines_without_a_rate_control_are_levelled():
    import remote_voice
    import tts

    assert tts.ChatterboxEngine.levels_pace and remote_voice.RemoteChatterboxEngine.levels_pace
    assert not tts.EspeakEngine.levels_pace and not tts.DebugEngine.levels_pace


def test_the_setting_is_copied():
    env = (ROOT / ".env.example").read_text()
    for line in ("PACE_LEVEL=1", "PACE_LEVEL_MAX=0.15", "PACE_LEVEL_DEADBAND=0.04"):
        assert line in env
    from config import settings

    assert settings.pace_level is (os.environ.get("PACE_LEVEL", "1") not in ("0", "false", "no", "off"))
