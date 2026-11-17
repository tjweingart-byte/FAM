"""Private mode, and a profile's vibes and interests for followers only (§637).

The owner: a listener can turn on private mode in Settings so nobody sees
what they search or listen to - followers still see what they vibe - and
somebody who does not follow a listener sees neither their interests nor
what they have vibed.
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as appmod  # noqa: E402
import preferences as prefs_mod  # noqa: E402

INDEX = (ROOT / "static" / "index.html").read_text()


def _fn(name):
    start = INDEX.index(f"function {name}(")
    return INDEX[start:INDEX.index("\n  }\n", start)]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


def _account(c, name, interests=("science",)):
    c.post("/api/auth/signup", json={"email": f"{name}@b.com", "password": "password12"})
    c.post("/api/me", json={"name": name.title(), "handle": name})
    c.post("/api/preferences", json={"interests": list(interests)})


def _uid(handle):
    return appmod._person_by_handle(handle)


# --- the preference --------------------------------------------------------

def test_private_mode_is_off_by_default_and_saved(tmp_path):
    store = prefs_mod.PreferenceStore(str(tmp_path / "p.db"))
    assert store.get("u1").private_mode is False
    assert store.get("u1").as_dict()["private_mode"] is False
    store.save("u1", private_mode=True)
    assert store.get("u1").private_mode is True
    # A write of something else leaves it alone.
    store.save("u1", tour_done=True)
    assert store.get("u1").private_mode is True
    assert store.private_among(["u1", "u2", ""]) == {"u1"}


def test_private_mode_is_written_through_the_api(client):
    _account(client, "ann")
    assert client.get("/api/preferences").json()["private_mode"] is False
    assert client.post("/api/preferences", json={"private_mode": True}).json()["private_mode"] is True
    assert client.get("/api/preferences").json()["private_mode"] is True


def test_who_is_private_fails_closed(monkeypatch, tmp_path):
    store = prefs_mod.PreferenceStore(str(tmp_path / "p.db"))

    def broken():
        raise RuntimeError("down")
    monkeypatch.setattr(store, "_conn", broken)
    assert store.private_among(["a", "b"]) == {"a", "b"}


# --- a profile for followers ------------------------------------------------

def test_a_non_follower_sees_no_vibes_or_interests(client):
    with TestClient(appmod.app) as beth:
        _account(beth, "beth", interests=("science", "sports"))
        beth.post("/api/vibe", json={"query": "why bonds move", "minutes": 3, "title": "Bonds"})
        beth.post("/api/mixes", json={"name": "Morning", "topic_ids": ["fed-next-move"]})
    _account(client, "ian")
    body = client.get("/api/person?handle=beth").json()
    assert body["locked"] is True
    assert body["vibes"] == [] and body["interests"] == [] and body["interest_labels"] == []
    # Still the page: name, mixes they made public, the numbers.
    assert body["name"] == "Beth" and body["mixes"]
    # And their vibes as posts are not open to them either.
    assert client.get("/api/vibe/posts?handle=beth").status_code == 404


def test_a_follower_sees_vibes_and_interests(client):
    with TestClient(appmod.app) as beth:
        _account(beth, "beth", interests=("science",))
        beth.post("/api/vibe", json={"query": "why bonds move", "minutes": 3, "title": "Bonds"})
    _account(client, "ian")
    assert client.post("/api/friends/follow", json={"handle": "beth"}).status_code == 200
    body = client.get("/api/person?handle=beth").json()
    assert body["locked"] is False
    assert body["vibes"] and body["interests"]
    assert client.get("/api/vibe/posts?handle=beth").status_code == 200


def test_being_followed_is_not_enough(client):
    """Following is what opens a page: Beth following Ian shows Ian nothing
    more of Beth's."""
    with TestClient(appmod.app) as beth:
        _account(beth, "beth")
        beth.post("/api/vibe", json={"query": "why bonds move", "minutes": 3, "title": "Bonds"})
        _account(client, "ian")
        beth.post("/api/friends/follow", json={"handle": "ian"})
    body = client.get("/api/person?handle=beth").json()
    assert body["locked"] is True and body["vibes"] == []


def test_a_private_account_still_shows_its_vibes_to_followers(client):
    with TestClient(appmod.app) as beth:
        _account(beth, "beth")
        beth.post("/api/preferences", json={"private_mode": True})
        beth.post("/api/vibe", json={"query": "why bonds move", "minutes": 3, "title": "Bonds"})
    _account(client, "ian")
    client.post("/api/friends/follow", json={"handle": "beth"})
    body = client.get("/api/person?handle=beth").json()
    assert body["private"] is True and body["vibes"]


# --- listening hidden ------------------------------------------------------

def test_private_listeners_are_left_out_of_the_shown_circle(client):
    with TestClient(appmod.app) as beth:
        _account(beth, "beth")
        beth.post("/api/preferences", json={"private_mode": True})
    with TestClient(appmod.app) as cal:
        _account(cal, "cal")
    b, c = _uid("beth"), _uid("cal")
    assert appmod._listening_shown([b, c]) == [c]


def test_a_private_friend_is_no_face_on_a_card(client, monkeypatch):
    with TestClient(appmod.app) as beth:
        _account(beth, "beth")
        beth.post("/api/preferences", json={"private_mode": True})
        _account(client, "ian")
        beth.post("/api/friends/follow", json={"handle": "ian"})
        client.post("/api/friends/follow", json={"handle": "beth"})
    asked = {}

    def listened(store, friends, tiles, untiled=None):
        asked["friends"] = list(friends)
        return {t["id"]: list(friends) for t in tiles}
    monkeypatch.setattr(appmod.topics_mod, "friends_who_listened", listened)
    tile = {"id": "t1", "query": "why bonds move"}
    faces = appmod._mark_friends_listened(_uid("ian"), [tile])
    assert asked["friends"] == [] and faces == [] and "friends_faces" not in tile


def test_private_mode_overrides_searches_public():
    src = (ROOT / "app.py").read_text()
    assert "shows.searches_public and not shows.private_mode" in src


def test_the_friends_rail_reads_only_listeners_who_show_it():
    src = (ROOT / "app.py").read_text()
    assert "circle = SOCIAL.circle_of(" not in src.replace(
        "circle = SOCIAL.circle_of(listener) if listener else []", "")
    assert src.count("_listening_shown(SOCIAL.circle_of(") == 3


# --- the interface ---------------------------------------------------------

def test_settings_has_a_private_mode_row():
    body = _fn("renderSettings")
    assert 'settingsRow("Private mode"' in body and "setPrivateMode(" in body
    assert "savePreferences({ private_mode: !!on })" in _fn("setPrivateMode")


def test_a_locked_profile_says_follow_to_see():
    page = _fn("renderFriendProfile")
    assert "personLocked = !!pub.locked" in page
    assert "if(!personLocked)" in page
    shelf = _fn("showPersonShelf")
    assert "Follow to see their vibes and interests." in shelf


def test_private_mode_is_the_first_row_in_settings():
    """The owner: "This should be at the top of the settings page"."""
    body = _fn("renderSettings")
    assert body.index('settingsRow("Private mode"') < body.index('set-group">You<')
    assert body.index('settingsRow("Private mode"') < body.index('settingsRow("Name and handle"')


# --- the iPhone ------------------------------------------------------------

SWIFT = ROOT / "ios" / "FAM" / "FAM"


def test_the_iphone_has_the_row_first_in_settings():
    settings = (SWIFT / "Views" / "SettingsView.swift").read_text()
    body = settings.split("var body: some View {", 1)[1]
    assert body.index("privateModeRow") < body.index("youGroup")
    assert 'SetRow(label: "Private mode"' in settings
    assert 'savePreference(["private_mode": on])' in settings
    # The web's note, word for word.
    web = _fn("renderSettings").split('settingsRow("Private mode"', 1)[1]
    note = web.split('<div class="set-note">', 1)[1].split("</div>", 1)[0]
    swift_note = settings.split('SetRow(label: "Private mode"', 1)[1].split(
        'SetNote(text: "', 1)[1].split('")', 1)[0]
    # The same words; only the escapes differ (`\u2019` / `\u{2019}`).
    assert note == swift_note.replace("\\u{", "\\u").replace("}", "")
    assert "let privateMode: Bool?" in (SWIFT / "API.swift").read_text()


def test_the_iphone_draws_a_locked_profile_and_reloads_on_follow():
    api = (SWIFT / "API.swift").read_text()
    assert "let locked: Bool?" in api and "let `private`: Bool?" in api
    person = (SWIFT / "Views" / "PersonView.swift").read_text()
    assert "Follow to see their vibes and interests." in person
    follow = person.split("private func follow() {", 1)[1].split("\n    }\n", 1)[0]
    assert "await load()" in follow


def test_the_web_asks_the_page_again_after_a_follow():
    assert "refreshPersonIfOpen();" in _fn("followPerson")
    assert "refreshPersonIfOpen();" in _fn("unfollowPerson")
    assert "refreshPersonIfOpen();" in _fn("followHandle")
    assert "if(!keep)" in _fn("loadPersonProfile")
