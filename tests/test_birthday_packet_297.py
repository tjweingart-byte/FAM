"""The Birthday packet (PROBLEMS.md §297): Instagram's faces row on DailyFAM,
a story with no episode, and "Forgot password?" on every log-in."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import moderation as moderation_mod  # noqa: E402
import social as social_mod  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)


def listener(name: str, handle: str) -> TestClient:
    c = TestClient(appmod.app)
    r = c.post("/api/auth/signup", json={"email": f"{handle}@example.com",
                                         "password": "password12"})
    assert r.status_code == 200, r.text
    assert c.post("/api/me", json={"name": name, "handle": handle}).status_code == 200
    return c


def uid(c: TestClient) -> str:
    return c.get("/api/auth/me").json()["user_id"]


def friends(a: TestClient, b: TestClient) -> None:
    a.post("/api/friends/follow", json={"user_id": uid(b)})
    b.post("/api/friends/follow", json={"user_id": uid(a)})


NOTE = {"text": "Big day. Thirty and still asking questions.",
        "style": {"bg": "sunset", "font": "strong", "size": 30,
                  "stickers": [{"k": "\U0001F525", "x": 0.3, "y": 0.2}]}}


# --- a story with no episode -------------------------------------------------

def test_a_note_needs_an_account():
    guest = TestClient(appmod.app)
    assert guest.post("/api/story/note", json=NOTE).status_code == 401


def test_a_note_goes_up_on_your_story_and_your_friends_rows():
    ann, ben = listener("Ann", "ann"), listener("Ben", "ben")
    friends(ann, ben)
    r = ann.post("/api/story/note", json=NOTE)
    assert r.status_code == 200, r.text
    note = r.json()
    assert note["kind"] == "note" and note["style"]["bg"] == "sunset"

    # Your own face leads the row with your story on it.
    me = ann.get("/api/circle", params={"notes": 1}).json()["me"]
    assert me["handle"] == "ann"
    assert [s["kind"] for s in me["stories"]] == ["note"]
    assert ann.get("/api/profile", params={"notes": 1}).json()["stories"][0]["text"] == NOTE["text"]

    # A friend's row plays it.
    row = ben.get("/api/circle", params={"notes": 1}).json()["circle"]
    ann_row = next(p for p in row if p["handle"] == "ann")
    assert ann_row["stories"][0]["kind"] == "note"
    assert ann_row["stories"][0]["text"] == NOTE["text"]


def test_an_older_client_never_sees_a_note():
    """A client that does not ask for notes would draw one as an episode."""
    ann, ben = listener("Ann", "ann"), listener("Ben", "ben")
    friends(ann, ben)
    ann.post("/api/story/note", json=NOTE)
    assert "me" not in ben.get("/api/circle").json()
    for person in ben.get("/api/circle").json()["circle"]:
        assert not person["stories"]
    assert ann.get("/api/profile").json()["stories"] == []


def test_a_note_is_not_a_vibe():
    """Every reader of `echoes` means an episode: counts, labels, the sweep."""
    ann = listener("Ann", "ann")
    ann.post("/api/story/note", json=NOTE)
    profile = ann.get("/api/profile", params={"notes": 1}).json()
    assert profile["echo_count"] == 0
    assert appmod.SOCIAL.vibed_episodes() == []


def test_notes_and_vibes_play_in_time_order():
    ann, ben = listener("Ann", "ann"), listener("Ben", "ben")
    friends(ann, ben)
    ann.post("/api/vibe", json={"query": "why bonds move", "minutes": 2, "title": "Bonds"})
    ann.post("/api/story/note", json=NOTE)
    stories = next(p for p in ben.get("/api/circle", params={"notes": 1}).json()["circle"]
                   if p["handle"] == "ann")["stories"]
    assert [s.get("kind", "vibe") for s in stories] == ["vibe", "note"]


def test_a_note_is_clamped():
    ann = listener("Ann", "ann")
    r = ann.post("/api/story/note", json={"text": "x " * 400,
                                          "style": {"bg": "url(https://evil)", "size": 900,
                                                    "stickers": [{"k": "<script>"}]}})
    assert r.status_code == 200
    note = r.json()
    assert len(note["text"]) <= social_mod.MAX_NOTE
    assert note["style"]["bg"] == social_mod.NOTE_BACKGROUNDS[0]
    assert note["style"]["size"] == 48
    assert note["style"]["stickers"] == []
    assert "thumb" not in note["style"] and "frame" not in note["style"]


def test_an_empty_note_is_refused():
    ann = listener("Ann", "ann")
    assert ann.post("/api/story/note", json={"text": "   "}).status_code == 400
    # Stickers alone are a story.
    assert ann.post("/api/story/note", json={"text": "", "style": {
        "stickers": [{"k": "\U0001F525"}]}}).status_code == 200


def test_only_the_poster_takes_a_note_down():
    ann, ben = listener("Ann", "ann"), listener("Ben", "ben")
    nid = ann.post("/api/story/note", json=NOTE).json()["id"]
    assert ben.delete(f"/api/story/note/{nid}").json()["ok"] is False
    assert ann.delete(f"/api/story/note/{nid}").json()["ok"] is True
    assert ann.get("/api/profile", params={"notes": 1}).json()["stories"] == []


def test_a_note_can_be_reported_and_removed(monkeypatch):
    monkeypatch.setattr(appmod, "_require_admin", lambda request: None)
    assert "note" in moderation_mod.KINDS
    ann, ben = listener("Ann", "ann"), listener("Ben", "ben")
    friends(ann, ben)
    nid = ann.post("/api/story/note", json=NOTE).json()["id"]
    r = ben.post("/api/report", json={"kind": "note", "target": str(nid), "reason": "spam"})
    assert r.status_code == 200, r.text
    assert r.json()["hidden"] is True
    ben.post(f"/api/admin/reports/{r.json()['id']}/resolve", json={"action": "removed"})
    assert appmod.SOCIAL.note_row(nid) is None


def test_deleting_an_account_erases_its_notes():
    ann = listener("Ann", "ann")
    user = uid(ann)
    nid = ann.post("/api/story/note", json=NOTE).json()["id"]
    assert ann.request("DELETE", "/api/account").status_code == 200
    assert appmod.SOCIAL.note_row(nid) is None
    assert appmod.SOCIAL.notes_among([user], 0) == {}


def test_the_backgrounds_are_the_servers_set():
    """The viewer maps each name to its colours; both lists name the same set."""
    block = INDEX.split("var NOTE_BACKGROUNDS = [", 1)[1].split("];", 1)[0]
    names = [line.split('"')[1] for line in block.strip().splitlines()]
    assert tuple(names) == social_mod.NOTE_BACKGROUNDS


# --- the composer, and where it opens -----------------------------------------

def test_the_plus_opens_the_note_composer_from_dailyfam_and_yourfam():
    assert "openNoteComposer()" in _fn("myFaceHTML")
    assert "openNoteComposer()" in _fn("renderProfile")


def test_the_composer_offers_background_and_speak_for_a_note():
    assert 'data-tool="bg"' in INDEX and 'class="vcs-tool note-only"' in INDEX
    assert ".vcs:not(.note) .vcs-tool.note-only" in INDEX
    assert "speechRecognitionCtor()" in _fn("vcsDictate")
    assert '"/api/story/note"' in _fn("postNoteStory")


def test_a_note_in_the_viewer_has_no_episode_controls():
    show = _fn("showStory")
    assert 'overlay.classList.toggle("note", isNote(v))' in show
    assert ".story.note .story-play" in INDEX
    assert "openNoteMenu" in _fn("openStoryMenu")
    assert "if(!isNote(v)) fetch(\"/api/rate\"" in _fn("likeStory")


# --- Instagram's row ----------------------------------------------------------

def test_the_dailyfam_row_leads_with_your_face_and_rings_stories():
    row = _fn("circleRowHTML")
    assert row.index("myFaceHTML()") < row.index("yfCircle.map")
    assert "yf-vibe" not in row
    assert '"/api/circle?notes=1"' in _fn("loadMyFamCircle")
    # FAM's gold, never Instagram's rainbow.
    assert "yf-ring.fresh{ background:linear-gradient(45deg,#7A5A2E" in INDEX
    assert "#FEDA75" not in INDEX.split(".myfam-circle .yf-ring.fresh", 1)[1].split("}", 1)[0]
    # Drawn for any account, so the (+) is there before you follow anybody.
    assert "!yfCircle.length && !yfMe" in _fn("paintMyFamCircle")


# --- Forgot password on every log-in -------------------------------------------

def test_the_reset_page_is_served_to_anybody():
    r = TestClient(appmod.app).get("/reset")
    assert r.status_code == 200
    assert "/api/auth/reset/start" in r.text and "/api/auth/reset/finish" in r.text
    # Never sends anybody to another site afterwards.
    assert "(?![\\/\\\\])" in r.text


@pytest.mark.parametrize("page", ["tracker.html", "waitlist.html", "thumbnails.html"])
def test_every_admin_login_links_to_the_reset_page(page):
    html = (ROOT / "admin_ui" / page).read_text()
    assert 'id="forgotLink" hidden' in html
    assert 'href="/reset?next=/admin' in html
    assert 'fetch("/api/auth/reset"' in html


def test_the_app_and_waitlist_logins_keep_theirs():
    assert "Forgot password?" in INDEX
    assert "Forgot password?" in (ROOT / "static" / "waitlist.html").read_text()
