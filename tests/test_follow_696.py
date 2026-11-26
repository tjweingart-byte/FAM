"""§696: "What topics do you want to follow", and following from inside
the app.

The owner, 11.26, choosing from ten designs: the flow of general topics then
specifics, each general topic opening into its larger subjects and each of
those into what is under it (leagues, then teams, with a box to type one);
the Made for you row built as topics are chosen; the wording "follow", never
"your interests"; "Follow this?" after a search; and - "we want to implement
all of them" - a reason on a card, the followed teams' scores above the
rails, a first mix made from the choices, and the daily reminder naming one.
"""
from __future__ import annotations

import os
import pathlib
import sys
import time
import types

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import categories as C  # noqa: E402
import category_seed  # noqa: E402
import live_captions  # noqa: E402
import mixes as M  # noqa: E402
import push  # noqa: E402
import stories  # noqa: E402
import topics as T  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    store = C.CategoryStore(str(tmp_path / "categories.db"))
    monkeypatch.setattr(T, "_CATEGORIES", store)
    C.apply_seed(store)
    T.reset_topic_tags()
    monkeypatch.setattr(T, "world_inventory", lambda *a, **k: ([], []))
    stories.seed([])
    yield store
    stories.seed([])
    T.reset_topic_tags()


def game(home, away):
    scope, key, label = stories._geography(())
    return stories.Story(
        subject=f"{home} vs {away}", title=f"{home} vs {away}", angle="tonight",
        query=f"{home} vs {away} tonight", domain=stories.SPORTS, source="test",
        tags=("sports",), strength=0.8, first_seen=time.time(),
        shelf_life=stories.DOMAIN_SHELF_LIFE[stories.SPORTS], countries=(),
        geo_scope=scope, geo_key=key, geo=label)


# --- the tree the screen offers -------------------------------------------

def test_general_topics_then_subjects_then_teams(tree):
    offered = T.follow_tree()
    assert [w["id"] for w in offered["worlds"]] == [w for w, _l in T.FOLLOW_WORLDS]
    sports = offered["worlds"][0]
    subjects = [s["label"] for s in sports["subtopics"]]
    for wanted in ("American Football", "Baseball", "Rugby", "Soccer"):
        assert wanted in subjects
    football = next(s for s in sports["subtopics"] if s["label"] == "American Football")
    under = [m["label"] for m in football["more"]]
    # Its own subjects first, then what is under them.
    assert under.index("NFL") < under.index("Dallas Cowboys")
    for team in ("New England Patriots", "Cincinnati Bengals", "Kansas City Chiefs"):
        assert team in under, team


def test_every_team_the_tree_leaves_out_has_a_league(tree):
    listed = sorted(n for names in category_seed.LEFT_LEAGUE.values() for n in names)
    assert listed == sorted(category_seed.LEFT_TO_GROW)


def test_a_followed_label_is_read_back_as_its_subject(tree):
    assert T.follow_node("Soccer") == "football"
    assert T._tags_for_interest("Soccer") == ("football",)
    assert "bengals" in T.interest_shares(["Cincinnati Bengals"])
    # One the tree leaves out is followed by name (§695).
    assert T.followed_names(["New England Patriots"]) == ("new england patriots",)


def test_every_offered_label_makes_a_tile(tree):
    offered = T.follow_tree()
    labels = [m["label"] for w in offered["worlds"] for s in w["subtopics"]
              for m in [s] + s["more"]]
    assert len(labels) > 500
    for label in labels:
        tiles = T.interest_topics([label])
        assert tiles and tiles[0].title.startswith(label), label


# --- "Follow this?" --------------------------------------------------------

def test_what_a_search_offers_to_follow(tree):
    assert T.follow_suggestion("who won the boston celtics game") == "Boston Celtics"
    assert T.follow_suggestion("taylor swift tour dates", (), "Taylor Swift") == "Taylor Swift"
    assert T.follow_suggestion("what happened with the fed", (),
                               "the US Federal Reserve") == "Federal Reserve"
    # Never a bare field, a sentence, or something already followed.
    assert T.follow_suggestion("sports news today") == ""
    assert T.follow_suggestion("explain it", (), "the long history of the thing in full") == ""
    assert T.follow_suggestion("taylor swift tour dates", ("Taylor Swift",),
                               "Taylor Swift") == ""


def test_the_briefs_subject_never_opens_a_track():
    """`publish_title`'s rule: only the speaking path opens a track."""
    live_captions.publish_subject("follow-696-no-track", "Taylor Swift")
    assert live_captions.read_subject("follow-696-no-track") == ""


# --- the feed --------------------------------------------------------------

def test_a_card_says_which_followed_topic_it_is_there_for(tree, tmp_path):
    stories.seed([game("Boston Celtics", "New York Knicks")])
    log = T.EventStore(str(tmp_path / "e.db"))
    feed = T.build_feed(log, "me", has_account=True, country="US",
                        interests=("Boston sports", "Taylor Swift"))
    made = next(s for s in feed["sections"] if s["key"] == "from_history")["topics"]
    why = {t["title"]: t.get("why") for t in made}
    assert why["Boston Celtics vs New York Knicks"] == "Boston Celtics"
    assert why["Taylor Swift: What's New This Week"] == "Taylor Swift"


def test_the_followed_teams_games_are_reported(tree, tmp_path):
    stories.seed([game("Boston Celtics", "New York Knicks"),
                  game("New England Patriots", "Buffalo Bills"),
                  game("Los Angeles Lakers", "Denver Nuggets")])
    log = T.EventStore(str(tmp_path / "e.db"))
    feed = T.build_feed(log, "me", has_account=True, country="US",
                        interests=("Boston sports",))
    assert {t["title"] for t in feed["teams"]} == {
        "Boston Celtics vs New York Knicks", "New England Patriots vs Buffalo Bills"}
    # Nobody's teams, nothing reported - and never a made-up line.
    assert T.build_feed(log, "me", has_account=True, interests=("ai",))["teams"] == []
    stories.seed([])
    assert T.team_scores(("Boston sports",)) == []


# --- the daily reminder ----------------------------------------------------

def test_the_reminder_names_a_followed_topic_a_mix_holds(monkeypatch):
    item = M.custom_item("what is the latest news on Boston Celtics this week",
                         "Boston Celtics")
    mix = types.SimpleNamespace(name="My topics", items=[item], played_at=0.0)
    store = types.SimpleNamespace(list_for_user=lambda _u: [mix])
    import daily_edition
    monkeypatch.setattr(daily_edition, "is_active", lambda m, now: True)
    monkeypatch.setattr(push, "_edition_report", lambda now: {})
    named = push.message_for("u", store, time.time(), ("Taylor Swift", "Boston Celtics"))
    assert named["title"] == "Boston Celtics: your myFAM is ready"
    assert push.message_for("u", store, time.time())["title"] == "Your myFAM is ready"
    assert push.message_for("u", store, time.time(), ("Chess",))["title"] \
        == "Your myFAM is ready"


# --- the API ---------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path, tree):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "EVENTS", T.EventStore(str(tmp_path / "api.db")))
    monkeypatch.setattr(appmod, "MIXES", M.MixStore(str(tmp_path / "mixes.db")))
    monkeypatch.setattr(appmod, "_write_mix_ahead", lambda mix, before=None: None)
    c = TestClient(appmod.app)
    c.post("/api/auth/signup", json={"email": f"f{time.time_ns()}@fam.test",
                                     "password": "a-long-enough-password"})
    return c


def test_the_tree_and_the_preview_are_apis(client):
    worlds = client.get("/api/follow/tree").json()["worlds"]
    assert len(worlds) == 8 and worlds[0]["subtopics"]
    tiles = client.post("/api/follow/preview", json={
        "interests": ["sports"], "topics": ["Boston Celtics", "Taylor Swift"]}).json()["topics"]
    assert [t["title"] for t in tiles] == ["Boston Celtics: What's New This Week",
                                           "Taylor Swift: What's New This Week"]


def test_following_from_inside_the_app(client):
    asked = client.get("/api/follow/suggest", params={
        "q": "taylor swift tour dates", "subject": "Taylor Swift"}).json()
    assert asked == {"topic": "Taylor Swift"}
    done = client.post("/api/follow", json={"topic": "Taylor Swift"}).json()
    assert done["following"] and "Taylor Swift" in done["topics"]
    again = client.get("/api/follow/suggest", params={
        "q": "taylor swift tour dates", "subject": "Taylor Swift"}).json()
    assert again == {"topic": ""}
    kept = client.get("/api/preferences").json()
    assert "Taylor Swift" in [t["id"] for t in kept["topics_chosen"]]


def test_a_guest_is_not_asked(tree, monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    guest = TestClient(appmod.app)
    assert guest.get("/api/follow/suggest", params={
        "q": "who won the boston celtics game"}).json() == {"topic": ""}
    assert guest.post("/api/follow", json={"topic": "x"}).status_code in (401, 403)


def test_the_choices_become_a_first_mix_once(client):
    client.post("/api/preferences", json={"topics": ["Boston Celtics", "Taylor Swift"]})
    made = client.post("/api/follow/mix").json()
    assert made["made"] and made["mix"]["name"] == appmod.FOLLOW_MIX_NAME
    assert [i["title"] for i in made["mix"]["items"]] == ["Boston Celtics", "Taylor Swift"]
    assert made["mix"]["public"] is True
    assert client.post("/api/follow/mix").json() == {"mix": None, "made": False}


def test_my_topics_is_for_followers_while_its_owner_is_private(client, monkeypatch):
    """§697, the owner: "make my topics public (but only visible to someone
    who follows if someone is on private mode in settings)"."""
    client.post("/api/preferences", json={"topics": ["Boston Celtics"]})
    mix_id = client.post("/api/follow/mix").json()["mix"]["id"]
    owner = client.get("/api/auth/me").json()["user_id"]
    stored = appmod.MIXES.get(owner, mix_id)
    assert stored.source_id == M.FROM_FOLLOWS

    other = TestClient(appmod.app)
    other.post("/api/auth/signup", json={"email": f"o{time.time_ns()}@fam.test",
                                         "password": "a-long-enough-password"})
    viewer = other.get("/api/auth/me").json()["user_id"]

    def sees():
        listed = [m["id"] for m in other.get("/api/mixes/public").json()["mixes"]]
        opened = other.get(f"/api/mixes/public/{mix_id}").status_code
        return mix_id in listed, opened

    # Public, like every new mix.
    assert sees() == (True, 200)
    # Private mode: gone for a stranger, exactly as a mix that is not there.
    client.post("/api/preferences", json={"private_mode": True})
    assert sees() == (False, 404)
    assert not appmod._mix_shown_to(stored, "")
    # Following is the door; the owner always sees their own.
    monkeypatch.setattr(appmod.SOCIAL, "is_following",
                        lambda me, them: me == viewer and them == owner)
    assert sees() == (True, 200)
    assert appmod._mix_shown_to(stored, owner)
    # Any other public mix of a private listener is as public as it was.
    plain = appmod.MIXES.create(owner, "Another", [], "", public=True)
    monkeypatch.setattr(appmod.SOCIAL, "is_following", lambda me, them: False)
    assert appmod._mix_shown_to(plain, viewer)
    # And a store that cannot say who is private hides it (fails closed).
    client.post("/api/preferences", json={"private_mode": False})
    monkeypatch.setattr(appmod.PREFS, "private_among", lambda ids: set(ids))
    assert not appmod._mix_shown_to(stored, viewer)


# --- the screen ------------------------------------------------------------

def test_the_screen_says_follow_not_interests():
    assert "What topics do you want to follow?" in INDEX
    assert '<div class="entry-h">Your interests</div>' not in INDEX
    assert 'settingsRow("Topics you follow"' in INDEX


def test_the_row_is_built_as_topics_are_chosen():
    paint = _fn("paintFollowPreview")
    assert '"/api/follow/preview"' in paint
    for change in ("toggleFollowWorld", "tapFollowSubject", "tapFollowLeaf",
                   "addFollowTyped", "addTypedInterest", "removeFollowed"):
        assert "paintFollowPreview()" in _fn(change), change


def test_a_private_search_is_never_asked_about():
    ask = _fn("maybeAskFollow")
    assert "privateSearch" in ask and 'currentPlaySurface !== "search"' in ask
