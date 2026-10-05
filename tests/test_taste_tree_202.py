"""§202: a listener's taste is a subject tree, chosen interests never fade,
and the owner's new weights - including adding something to a mix.

"If I consistently listen to content about the Cincinnati Bengals, that
should be under [sports, american football, bengals]. The algorithm should
know that I am interested in the Bengals, but also relate that to the sport
of football as a whole."
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as appmod
import categories as C
import mixes as M
import topics as T

DAY = 86400.0
NOW = 1_790_000_000.0


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    store = C.CategoryStore(str(tmp_path / "categories.db"))
    monkeypatch.setattr(T, "_CATEGORIES", store)
    C.apply_seed(store)
    T.reset_topic_tags()
    yield store
    T.reset_topic_tags()


def ev(kind, text, days_ago=0.0, user="u"):
    return T.Event(user, kind, "", text, T.tags_for_text(text),
                   at=NOW - days_ago * DAY)


def tile(query, tags=("sports",), tid="t"):
    return T.Topic(tid, query.title(), "", query, tags, "leaf")


# --- the weights --------------------------------------------------------------


def test_the_owners_weights():
    assert T.EVENT_WEIGHT["search"] == 1.5
    assert T.EVENT_WEIGHT["complete"] == 1.5
    assert T.EVENT_WEIGHT["pick"] == 2.2
    assert T.EVENT_WEIGHT[T.MIX_ADD] == 2.0
    assert T.MIX_ADD in T.EVENT_KINDS
    # Unchanged by §202.
    assert (T.EVENT_WEIGHT["play"], T.EVENT_WEIGHT["skip"], T.EVENT_WEIGHT["vibe"],
            T.EVENT_WEIGHT["save"], T.EVENT_WEIGHT["share"]) == (1.0, -0.5, 2.5, 2.0, 2.0)


# --- the tree -----------------------------------------------------------------


def test_a_team_is_filed_under_its_league_sport_and_facet(tree):
    assert T.lineage("bengals") == ["nfl", "american football", "sports"]
    assert {"bengals", "nfl", "american football", "sports"} <= set(
        T.tags_for_text("cincinnati bengals injury report"))


#: Questions FAM is asked that share every word with a team's name.
NOT_SPORT = (
    "hurricanes hitting the carolina coast this week",
    "will there be lightning in tampa bay tonight",
    "avalanche warning in colorado this weekend",
    "how hot is the miami heat wave going to get",
    "thunderstorms and thunder in oklahoma city",
    "why tech giants are leaving san francisco",
    "meat packers and beef prices",
    "what canadian senators in ottawa voted on",
    "private jets flying out of new york",
    "how police chiefs in kansas city are chosen",
    "ev chargers in los angeles",
    "is the bull market in chicago futures over",
    "ravens and crows are smarter than you think",
    "wildfire flames near calgary",
)


def test_a_seeded_team_never_catches_an_everyday_question(tree):
    import category_seed as S
    teams = {p for p, parent in S.rows() if parent in ("nfl", "nba", "mlb", "nhl")}
    for question in NOT_SPORT:
        caught = teams & set(tree.match(question))
        assert not caught, f"{question!r} was filed under {caught}"


def test_the_specific_subject_takes_the_whole_signal_and_headings_a_share(tree):
    shares = T.tag_shares(["sports", "american football", "nfl", "bengals"])
    s = T.ANCESTOR_SHARE
    assert shares == pytest.approx({"bengals": 1.0, "nfl": s,
                                    "american football": s ** 2, "sports": s ** 3})


def test_a_tag_stored_without_its_ancestry_still_reaches_it(tree):
    assert set(T.tag_shares(["bengals"])) == {"bengals", "nfl",
                                              "american football", "sports"}


def test_a_bengals_fan_knows_the_bengals_best_and_football_too(tree):
    profile = T.taste([ev("complete", "bengals playoff chances", d)
                       for d in (0, 1, 2)], NOW)
    assert profile["bengals"] == 1.0
    assert 0 < profile["sports"] < profile["american football"] < profile["nfl"] < 1.0


def test_the_specific_story_outranks_the_league_which_outranks_the_sport(tree):
    profile = T.taste([ev("complete", "bengals playoff chances", d)
                       for d in (0, 1, 2)], NOW)
    bengals = T._affinity(tile("cincinnati bengals injury report"), profile)
    league = T._affinity(tile("nfl trade deadline winners"), profile)
    other = T._affinity(tile("premier league title race"), profile)
    assert bengals > league > other


def test_the_tree_view_nests_the_profile(tree):
    profile = T.taste([ev("complete", "bengals playoff chances")], NOW)
    top = T.taste_tree(profile)
    sport = next(n for n in top if n["id"] == "sports")
    path = [sport["id"]]
    node = sport
    while node["children"]:
        node = node["children"][0]
        path.append(node["id"])
    assert path == ["sports", "american football", "nfl", "bengals"]


def test_a_skip_spreads_down_the_tree_the_same_way(tree):
    profile = T.taste([ev("complete", "bengals playoff chances"),
                       ev("skip", "nba draft prospects")], NOW)
    assert profile["nba"] < 0


# --- interests ----------------------------------------------------------------


def test_a_chosen_interest_is_constant_however_much_is_heard(tree):
    light = T.taste([ev("complete", "bengals playoff chances")], NOW, ["culture"])
    heavy = T.taste([ev("complete", "bengals playoff chances", d / 10)
                     for d in range(200)], NOW, ["culture"])
    assert light["culture"] == heavy["culture"] == T.INTEREST_WEIGHT


def test_a_chosen_interest_never_decays(tree):
    fresh = T.taste([ev("play", "bengals playoff chances", 0)], NOW, ["science"])
    later = T.taste([ev("play", "bengals playoff chances", 0)], NOW + 365 * DAY,
                    ["science"])
    assert fresh["science"] == later["science"] == T.INTEREST_WEIGHT


def test_a_chosen_interest_outweighs_the_strongest_listening(tree):
    profile = T.taste([ev("complete", "bengals playoff chances")], NOW, ["culture"])
    assert profile["culture"] > max(v for k, v in profile.items() if k != "culture")


def test_a_skip_never_takes_a_chosen_interest_below_its_floor(tree):
    profile = T.taste([ev("skip", "how the oscars are decided", d)
                       for d in range(5)], NOW, ["culture"])
    assert profile["culture"] == T.INTEREST_WEIGHT


def test_listening_adds_to_a_chosen_interest(tree):
    profile = T.taste([ev("complete", "bengals playoff chances")], NOW, ["nfl"])
    assert profile["nfl"] > T.INTEREST_WEIGHT


def test_a_named_subject_from_the_interests_page_counts_through_the_tree(tree):
    profile = T.taste([], NOW, ["nfl"])  # the catalogue's NFL entry
    s = T.ANCESTOR_SHARE
    assert profile["nfl"] == T.INTEREST_WEIGHT
    assert profile["american football"] == pytest.approx(T.INTEREST_WEIGHT * s)
    assert profile["sports"] == pytest.approx(T.INTEREST_WEIGHT * s * s)


def test_two_interests_on_one_heading_are_not_a_louder_one(tree):
    profile = T.taste([], NOW, ["sports", "nfl"])
    assert profile["sports"] == T.INTEREST_WEIGHT


def test_interests_alone_still_make_a_profile():
    assert T.taste([], NOW, ["tech"]) == {"tech": T.INTEREST_WEIGHT}
    assert T.taste([], NOW) == {}


# --- mixes --------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch, tmp_path, tree):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "MIXES", M.MixStore(str(tmp_path / "mixes.db")))
    monkeypatch.setattr(appmod, "EVENTS", T.EventStore(str(tmp_path / "events.db")))
    c = TestClient(appmod.app)
    res = c.post("/api/auth/signup", json={"email": "mixer@fam.test",
                                           "password": "a-long-enough-password"})
    assert res.status_code == 200, res.text
    return c


def _mix_adds(user=None):
    rows = []
    for uid in ([user] if user else _users()):
        rows += [e for e in appmod.EVENTS.for_user(uid) if e.kind == T.MIX_ADD]
    return rows


def _users():
    with appmod.EVENTS._conn() as db:
        return [r[0] for r in db.execute("SELECT DISTINCT user_id FROM events")]


def test_adding_to_a_mix_is_a_taste_signal(client):
    made = client.post("/api/mixes", json={
        "name": "Sundays", "topic_ids": ["f:nfl~Bengals"]}).json()
    adds = _mix_adds()
    assert len(adds) == 1
    assert adds[0].topic_id == "f:nfl~Bengals"
    assert {"sports", "bengals"} <= set(adds[0].tags)
    # Only what is new: a rename adds nothing, a second item adds one.
    client.patch(f"/api/mixes/{made['id']}", json={"name": "Game day"})
    assert len(_mix_adds()) == 1
    res = client.patch(f"/api/mixes/{made['id']}",
                       json={"topic_ids": ["f:nfl~Bengals", "f:basketball"]})
    assert res.status_code == 200, res.text
    assert sorted(e.topic_id for e in _mix_adds()) == ["f:basketball", "f:nfl~Bengals"]


def test_a_mix_item_reaches_the_tree(tree):
    item = M.followed_item("f:nfl~Bengals")
    assert {"sports", "nfl", "bengals"} <= set(M.taste_tags(item))
