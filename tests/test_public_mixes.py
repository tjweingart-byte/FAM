"""DailyFAM as a place to find other people's mixes (the 9.23 packet).

Four things: searching other listeners' public mixes by name, topic or
owner; adding one to your own DailyFAM with (+); sharing a whole mix from
its menu; and a narrow-it-down chip that no longer opens the keyboard.
"""
from __future__ import annotations

import os
import re
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import mixes as M  # noqa: E402
import sharing  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASSWORD = "a-long-enough-password"


@pytest.fixture
def store(tmp_path):
    return M.MixStore(str(tmp_path / "mixes.db"))


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "MIXES", M.MixStore(str(tmp_path / "api.db")))


def person(email: str, name: str = "", handle: str = "") -> TestClient:
    client = TestClient(appmod.app)
    res = client.post("/api/auth/signup", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    if handle:
        client.post("/api/me", json={"name": name, "handle": handle})
    return client


def make_mix(client, name, entries, public=True):
    made = client.post("/api/mixes", json={"name": name, "topic_ids": entries})
    assert made.status_code == 200, made.text
    if public:
        client.patch(f"/api/mixes/{made.json()['id']}", json={"public": True})
    return made.json()


# --- matching ---------------------------------------------------------------

def _mix(name, entries):
    return M.Mix("x", "owner", name, M.clean_items(entries), 0, 0, True)


def test_a_mix_is_found_by_its_name_or_a_topic_in_it():
    """The packet's own example: a mix called Gym holding AI updates is found
    by either."""
    gym = _mix("Gym", [{"query": "AI updates"}, "f:nfl~Eagles"])
    assert M.match_score(gym, "gym")
    assert M.match_score(gym, "AI updates")
    assert M.match_score(gym, "eagles")
    assert not M.match_score(gym, "cooking")


def test_a_mix_is_found_by_whose_it_is():
    gym = _mix("Gym", ["f:ai"])
    assert M.match_score(gym, "sam", "Sam Lee", "samlee")
    assert M.match_score(gym, "@samlee", "Sam Lee", "samlee")
    assert not M.match_score(gym, "@nobody", "Sam Lee", "samlee")


def test_every_word_has_to_be_found_somewhere():
    gym = _mix("Gym", ["f:ai"])
    assert M.match_score(gym, "gym sam", "Sam", "sam")
    assert not M.match_score(gym, "gym cooking", "Sam", "sam")


def test_words_match_from_their_start_not_anywhere_inside():
    """"ai" is a word people type; as a substring it is inside "daily" and
    "Taiwan", which would put every such mix under an AI search."""
    daily = _mix("Daily Taiwan", [{"query": "tea from taiwan"}])
    assert not M.match_score(daily, "ai")
    assert M.match_score(daily, "tai")      # a word being typed still finds it
    assert M.match_score(daily, "dai")


def test_a_followed_subject_is_found_by_its_catalogue_id():
    """The catalogue calls it "Artificial Intelligence"; people type "AI"."""
    assert M.match_score(_mix("Morning", ["f:ai"]), "ai")


def test_a_name_hit_outranks_a_topic_hit():
    by_name = _mix("AI mornings", ["f:stocks"])
    by_topic = _mix("Gym", [{"query": "AI mornings"}])
    assert M.match_score(by_name, "ai") > M.match_score(by_topic, "ai")


# --- the store --------------------------------------------------------------

def test_only_public_mixes_are_listed_and_never_your_own(store):
    store.create("a", "Mine", ["f:ai"])
    theirs = store.create("b", "Theirs", ["f:ai"])
    store.create("b", "Hidden", ["f:ai"])
    store.update("b", theirs.id, public=True)
    mine = store.create("a", "Mine public", ["f:ai"])
    store.update("a", mine.id, public=True)
    assert [m.name for m in store.all_public(exclude_user="a")] == ["Theirs"]


def test_a_private_mix_is_not_found_by_id(store):
    hidden = store.create("b", "Hidden", ["f:ai"])
    assert store.get_public(hidden.id) is None
    assert store.get_public(hidden.id, viewer="b") is not None


def test_adding_makes_a_copy_that_remembers_where_it_came_from(store):
    source = store.create("b", "Gym", ["f:ai", {"query": "AI updates"}])
    copy = store.add_copy("a", source, "@bee")
    assert copy.user_id == "a" and copy.id != source.id
    assert copy.source_id == source.id
    assert [i.id for i in copy.items] == [i.id for i in source.items]
    # A typed topic's "Added by you" is the owner's words, not the copier's.
    assert [i.subtitle for i in copy.items if i.custom] == ["From @bee's mix"]
    # The copy is theirs: the original changing does not change it.
    store.update("b", source.id, name="Renamed")
    assert store.get("a", copy.id).name == "Gym"


def test_the_same_mix_cannot_be_added_twice(store):
    source = store.create("b", "Gym", ["f:ai"])
    store.add_copy("a", source)
    with pytest.raises(M.MixError, match="already in your DailyFAM"):
        store.add_copy("a", source)
    assert store.added_from("a") == {source.id}


def test_a_clashing_name_takes_the_owners_handle(store):
    store.create("a", "Gym", ["f:ai"])
    source = store.create("b", "Gym", ["f:stocks"])
    assert store.add_copy("a", source, "@bee").name == "Gym · @bee"


def test_you_cannot_add_your_own_mix(store):
    mine = store.create("a", "Gym", ["f:ai"])
    with pytest.raises(M.MixError):
        store.add_copy("a", mine)


def test_deleting_an_account_leaves_no_id_in_other_peoples_copies(store):
    source = store.create("b", "Gym", ["f:ai"])
    copy = store.add_copy("a", source)
    store.forget("b")
    kept = store.get("a", copy.id)
    assert kept is not None and kept.source_user == ""
    assert kept.source_id == source.id      # still cannot be added twice


def test_a_copy_survives_a_restart(tmp_path):
    first = M.MixStore(str(tmp_path / "m.db"))
    source = first.create("b", "Gym", ["f:ai"])
    first.add_copy("a", source)
    again = M.MixStore(str(tmp_path / "m.db"))
    assert again.list_for_user("a")[0].source_id == source.id


# --- over HTTP ----------------------------------------------------------------

def test_search_lists_every_public_mix_and_then_narrows():
    bee = person("bee@fam.test", "Bee Owens", "bee")
    make_mix(bee, "Gym", [{"query": "AI updates"}])
    make_mix(bee, "Wind down", ["f:music"])
    make_mix(bee, "Secret", ["f:ai"], public=False)
    me = person("me@fam.test")

    everything = me.get("/api/mixes/public").json()["mixes"]
    assert sorted(m["name"] for m in everything) == ["Gym", "Wind down"]
    assert [m["name"] for m in me.get("/api/mixes/public?q=gym").json()["mixes"]] == ["Gym"]
    assert [m["name"] for m in me.get("/api/mixes/public?q=AI updates").json()["mixes"]] == ["Gym"]
    # Somebody's profile: all their public mixes.
    assert len(me.get("/api/mixes/public?q=@bee").json()["mixes"]) == 2


def test_a_search_result_names_its_owner_and_carries_no_listener_id():
    bee = person("bee@fam.test", "Bee Owens", "bee")
    make_mix(bee, "Gym", [{"query": "AI updates"}])
    row = person("me@fam.test").get("/api/mixes/public").json()["mixes"][0]
    assert row["owner"] == {"name": "Bee Owens", "handle": "bee"}
    assert row["mine"] is False and row["added"] is False
    assert "user_id" not in row and "source_user" not in row
    assert row["items"][0]["subtitle"] == "Typed in by @bee"


def test_your_own_mixes_are_not_in_your_search():
    me = person("me@fam.test", "Me", "me")
    make_mix(me, "Gym", ["f:ai"])
    assert me.get("/api/mixes/public").json()["mixes"] == []


def test_searching_needs_no_account():
    bee = person("bee@fam.test", "Bee", "bee")
    make_mix(bee, "Gym", ["f:ai"])
    guest = TestClient(appmod.app)
    assert [m["name"] for m in guest.get("/api/mixes/public").json()["mixes"]] == ["Gym"]


def test_plus_adds_it_to_your_dailyfam_beside_your_own():
    bee = person("bee@fam.test", "Bee", "bee")
    gym = make_mix(bee, "Gym", ["f:ai"])
    me = person("me@fam.test")
    make_mix(me, "Morning", ["f:news"], public=False)

    added = me.post(f"/api/mixes/{gym['id']}/add")
    assert added.status_code == 200, added.text
    assert added.json()["from"] == {"name": "Bee", "handle": "bee"}
    mine = me.get("/api/mixes").json()["mixes"]
    assert [m["name"] for m in mine] == ["Morning", "Gym"]
    assert mine[1]["from"]["handle"] == "bee"
    # And the (+) now reads as added, wherever the mix is drawn.
    assert me.get("/api/mixes/public").json()["mixes"][0]["added"] is True
    assert me.get("/api/person?handle=bee").json()["mixes"][0]["added"] is True
    again = me.post(f"/api/mixes/{gym['id']}/add")
    assert again.status_code == 400


def test_a_copy_whose_owner_has_no_name_does_not_say_from_nobody():
    anon = person("anon@fam.test")          # an account with no name or handle
    gym = make_mix(anon, "Gym", ["f:ai"])
    me = person("me@fam.test")
    me.post(f"/api/mixes/{gym['id']}/add")
    assert "from" not in me.get("/api/mixes").json()["mixes"][0]


def test_adding_needs_an_account():
    bee = person("bee@fam.test", "Bee", "bee")
    gym = make_mix(bee, "Gym", ["f:ai"])
    assert TestClient(appmod.app).post(f"/api/mixes/{gym['id']}/add").status_code == 401


def test_a_private_mix_cannot_be_opened_or_added():
    bee = person("bee@fam.test", "Bee", "bee")
    hidden = make_mix(bee, "Secret", ["f:ai"], public=False)
    me = person("me@fam.test")
    assert me.get(f"/api/mixes/public/{hidden['id']}").status_code == 404
    assert me.post(f"/api/mixes/{hidden['id']}/add").status_code == 404


def test_sharing_a_mix_gives_a_link_and_words_for_every_destination():
    bee = person("bee@fam.test", "Bee", "bee")
    gym = make_mix(bee, "Gym", ["f:ai", {"query": "AI updates"}])
    body = bee.post(f"/api/mixes/{gym['id']}/share",
                    headers={"host": "fam.example.com"}).json()
    assert body["url"].endswith(f"/m/{gym['id']}")
    assert set(body["targets"]) == set(sharing.TARGET_KEYS)
    assert "Gym" in body["targets"]["sms"]["text"]
    assert body["url"] in body["targets"]["sms"]["text"]
    assert body["card"].endswith(f"/api/mixes/{gym['id']}/card")


def test_only_a_public_mix_can_be_shared():
    bee = person("bee@fam.test", "Bee", "bee")
    hidden = make_mix(bee, "Secret", ["f:ai"], public=False)
    assert bee.post(f"/api/mixes/{hidden['id']}/share").status_code == 409
    # And only by its owner.
    theirs = make_mix(bee, "Gym", ["f:ai"])
    assert person("me@fam.test").post(f"/api/mixes/{theirs['id']}/share").status_code == 404


def test_a_shared_mix_link_opens_the_app_on_that_mix():
    res = TestClient(appmod.app).get("/m/abc123", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/?mix=abc123"


def test_a_shared_mix_has_a_story_card():
    bee = person("bee@fam.test", "Bee", "bee")
    gym = make_mix(bee, "Gym", ["f:ai"])
    card = TestClient(appmod.app).get(f"/api/mixes/{gym['id']}/card")
    assert card.status_code == 200
    assert card.headers["content-type"].startswith("image/svg+xml")
    assert "Daily mix" in card.text and "Gym" in card.text


def test_mix_share_templates_cover_every_destination():
    assert set(sharing.MIX_TEMPLATES) == set(sharing.TARGET_KEYS)


# --- the interface ------------------------------------------------------------

def _html() -> str:
    with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as f:
        return f.read()


def test_tapping_a_suggested_specific_does_not_open_the_keyboard():
    """The bug: a chip under "Narrow it down" called `box.focus()` on the
    "Type a ..." input, which on a phone slides the keyboard up. Only the box
    itself - typing and pressing Enter in it - may put the cursor back."""
    html = _html()
    body = html[html.index("function addFocus("):]
    body = body[:body.index("\n  }\n")]
    assert re.search(r"if\(typed\)\{\s*var box = document.getElementById\(\"focusInput\"\);"
                     r"\s*if\(box\) box.focus\(\);", body)
    # The chips call it without `typed`; only the box's Enter passes it.
    assert "addFocus(' + q + ',this.dataset.v)" in html
    assert "addFocus(' + q + ', this.value, false, true)" in html


def test_share_mix_is_in_the_mix_menu():
    html = _html()
    menu = html[html.index("function openMixMenu("):]
    menu = menu[:menu.index("\n  }\n")]
    assert '{ label: "Share mix", action: shareMix }' in menu


def test_dailyfam_has_a_search_bar_for_public_mixes():
    html = _html()
    screen = html[html.index('id="screen-playfam"'):html.index('id="screen-mixdetail"')]
    assert 'id="dailySearch"' in screen
    assert "/api/mixes/public?q=" in html
