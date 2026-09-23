"""DailyFAM mixes follow subjects, narrowed or whole, with a cover (§137).

A mix used to hold bank episodes - one-off stories. It now holds catalogue
subjects (`f:nfl`), each optionally narrowed to one specific
(`f:nfl~Eagles`), and every play asks for that subject as of today. These
tests pin the server half of that: the id grammar, the prompt a play sends,
the date that keeps one day's briefing from being served the next, and the
cover photo.
"""
from __future__ import annotations

import base64
import os
import sys
from datetime import date

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import cache  # noqa: E402
import mixes as M  # noqa: E402
import prefetch_sources  # noqa: E402
import topics as T  # noqa: E402

PASSWORD = "a-long-enough-password"
JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0 not really").decode()


@pytest.fixture
def store(tmp_path):
    return M.MixStore(str(tmp_path / "mixes.db"))


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "MIXES", M.MixStore(str(tmp_path / "api.db")))
    c = TestClient(appmod.app)
    res = c.post("/api/auth/signup", json={"email": "follow@fam.test", "password": PASSWORD})
    assert res.status_code == 200, res.text
    return c


# --- following a subject ------------------------------------------------------


def test_a_followed_subject_is_a_catalogue_entry_not_an_episode():
    item = M.followed_item("f:nfl")
    assert item.follow and not item.custom
    assert (item.id, item.title, item.base, item.focus) == ("f:nfl", "NFL", "f:nfl", ())
    assert item.icon == T.CATALOGUE_BY_ID["nfl"].icon


def test_a_narrowed_subject_names_its_specific():
    item = M.followed_item("f:soccer~San%20Diego%20FC")
    assert item.title == "Soccer · San Diego FC"
    assert item.focus == ("San Diego FC",) and item.base == "f:soccer"
    assert "San Diego FC" in item.subtitle


def test_an_unknown_subject_is_refused_rather_than_dropped():
    with pytest.raises(M.MixError):
        M.clean_items(["f:not-a-subject"])


def test_a_team_and_its_whole_league_are_two_briefings(store):
    """The product ask: "NFL - Eagles" *and* "NFL" in one mix, each its own
    row and play button."""
    mix = store.create("u", "Game day", ["f:nfl~Eagles", "f:nfl~Chiefs", "f:nfl"])
    assert [i.title for i in mix.items] == ["NFL · Eagles", "NFL · Chiefs", "NFL"]
    assert {i.base for i in mix.items} == {"f:nfl"}


def test_the_same_specific_twice_is_one_briefing():
    items = M.clean_items(["f:nfl~Eagles", "f:nfl~eagles", "f:nfl~%20Eagles%20"])
    assert len(items) == 1


def test_a_specific_cannot_smuggle_in_a_delimiter():
    """`,` separates stored items and `~`/`|` delimit a focus."""
    item = M.followed_item("f:nfl~" + "A%2CB%7EC")
    assert item.focus == ("A B C",)
    long = M.followed_item("f:nfl~" + "x" * 200)
    assert len(long.focus[0]) == M.MAX_FOCUS


def test_ids_are_encoded_the_way_the_interface_encodes_them():
    """`encodeURIComponent` leaves `!'()*` alone. An id the page builds and
    the one stored here must be the same string, or Edit topics loses a pick."""
    for raw in ("O'Brien", "Team (A)", "Canelo Álvarez", "S&P 500"):
        from urllib.parse import quote
        js_encoded = quote(raw, safe="!'()*")
        assert M.followed_item("f:nfl~" + js_encoded).id == "f:nfl~" + js_encoded


def test_followed_items_survive_a_restart(tmp_path):
    path = str(tmp_path / "m.db")
    made = M.MixStore(path).create("u", "Morning", ["f:stocks~Nvidia", "fed-next-move"])
    back = M.MixStore(path).get("u", made.id)
    assert back.items == made.items
    assert back.items[0].follow and not back.items[1].follow


def test_an_older_mix_of_bank_episodes_still_reads(store):
    """Mixes made before §137 hold bank ids, and still play."""
    mix = store.create("u", "Old", ["fed-next-move", "sleep-science"])
    got = store.get("u", mix.id).as_dict()
    assert [i["id"] for i in got["items"]] == ["fed-next-move", "sleep-science"]
    assert all(i["daily_prompt"] == "" for i in got["items"])


# --- the daily prompt -----------------------------------------------------------


def test_every_play_is_that_days_edition():
    day = date(2026, 9, 23)
    whole = M.prompt_for(M.followed_item("f:nfl"), day)
    narrow = M.prompt_for(M.followed_item("f:nfl~Eagles"), day)
    assert whole.startswith("The latest on NFL as of Wednesday, September 23, 2026")
    assert "Eagles (NFL)" in narrow and "Cover only Eagles, not NFL in general" in narrow


def test_a_typed_topic_is_followed_the_same_way():
    item = M.custom_item("Stanford")
    assert "The latest on Stanford as of" in M.prompt_for(item, date(2026, 9, 23))


def test_a_bank_episode_plays_as_itself():
    item = M.clean_items(["fed-next-move"])[0]
    assert M.prompt_for(item) == item.query


def test_the_date_reads_like_the_interface_prints_it():
    """`toLocaleDateString("en-US", {weekday: "long", month: "long", day:
    "numeric", year: "numeric"})` on the same day."""
    assert M.date_label(date(2026, 9, 3)) == "Thursday, September 3, 2026"
    assert len(M.LONGEST_DATE) >= max(
        len(M.date_label(date(2026, m, 28))) for m in range(1, 13))


def test_every_prompt_fits_the_endpoints_that_echo_it_back():
    """`/api/next`, a vibe and a saved item cap a query at 300 characters."""
    worst_focus = "x" * M.MAX_FOCUS
    longest_label = max(T.INTEREST_CATALOGUE, key=lambda i: len(i.label)).id
    items = [M.followed_item(f"f:{longest_label}~{worst_focus}"),
             M.followed_item(f"f:{longest_label}"),
             M.custom_item("y" * M.MAX_QUERY)]
    for item in items:
        prompt = M.daily_prompt(item).replace(M.DAILY_DATE, M.LONGEST_DATE)
        assert len(prompt) <= M.MAX_PROMPT, (item.id, len(prompt))
    # The short one keeps the whole instruction.
    assert M.daily_prompt(M.followed_item("f:nfl")).endswith(M.DAILY_ENDINGS[0])


def test_yesterdays_briefing_is_never_todays():
    """The date is in the words, and the words are the cache key: the handoff
    worried that a long focus pushed it past a 60-character cut and that the
    near-match cache would serve yesterday's Eagles. Neither happens here."""
    item = M.followed_item("f:soccer~San Diego FC")
    monday = M.prompt_for(item, date(2026, 9, 21))
    tuesday = M.prompt_for(item, date(2026, 9, 22))
    assert cache.cache_key(monday, 3) != cache.cache_key(tuesday, 3)
    assert cache.comparable(tuesday, monday) != ""


def test_the_same_day_is_shared_between_listeners():
    day = date(2026, 9, 23)
    a = M.prompt_for(M.followed_item("f:nfl~Eagles"), day)
    b = M.prompt_for(M.clean_items(["f:nfl~Eagles"])[0], day)
    assert cache.cache_key(a, 3) == cache.cache_key(b, 3)


def test_prefetch_warms_the_words_a_tap_sends(store):
    store.create("u", "Morning", ["f:nfl~Eagles", "fed-next-move"])
    got = prefetch_sources.MixSource(store).candidates("u")
    queries = {c.query for c in got}
    assert M.prompt_for(M.followed_item("f:nfl~Eagles")) in queries
    assert T.BANK_BY_ID["fed-next-move"].query in queries
    followed = [c for c in got if c.query.startswith("The latest")][0]
    assert followed.topic_id == "", "a followed subject is not a bank tile"


# --- covers -----------------------------------------------------------------------


def test_a_cover_is_kept_and_can_be_removed(store):
    mix = store.create("u", "Morning", ["f:nfl"], cover=JPEG)
    assert store.get("u", mix.id).cover == JPEG
    store.update("u", mix.id, name="Early")
    assert store.get("u", mix.id).cover == JPEG, "omitting the cover kept it"
    store.update("u", mix.id, cover="")
    assert store.get("u", mix.id).cover == ""


@pytest.mark.parametrize("bad", [
    "https://example.com/cover.jpg",
    "data:text/html;base64,PGgxPg==",
    "data:image/jpeg;base64,not base64!!",
    "data:image/jpeg;base64," + "A" * M.MAX_COVER_CHARS,
])
def test_a_cover_that_is_not_a_photo_is_refused(store, bad):
    with pytest.raises(M.MixError):
        store.create("u", "Morning", ["f:nfl"], cover=bad)


def test_following_and_covers_over_http(client):
    made = client.post("/api/mixes", json={
        "name": "Game day", "topic_ids": ["f:nfl~Eagles", "f:nfl", {"query": "Stanford"}],
        "cover": JPEG}).json()
    assert made["cover"] == JPEG
    eagles, nfl, typed = made["items"]
    assert eagles["follow"] and eagles["focus"] == ["Eagles"] and eagles["base"] == "f:nfl"
    assert "{date}" in eagles["daily_prompt"] and "{date}" in typed["daily_prompt"]
    assert typed["title"] == "Stanford", "a typed topic keeps its case"
    assert "follow" not in typed

    patched = client.patch(f"/api/mixes/{made['id']}", json={"cover": ""}).json()
    assert patched["cover"] == "" and len(patched["items"]) == 3

    bad = client.post("/api/mixes", json={"name": "X", "topic_ids": ["f:nope"]})
    assert bad.status_code == 400 and "nope" in bad.json()["error"]


def test_preferences_serve_what_the_picker_ranks_with(client):
    body = client.get("/api/preferences").json()
    assert body["tag_parent"] == T.TAG_PARENT
    assert body["tag_labels"] == T.TAG_LABELS
    nfl = [c for c in body["catalogue"] if c["id"] == "nfl"][0]
    assert nfl["focus_noun"] == "team" and "Eagles" in nfl["focus_picks"]


def test_every_suggestion_list_belongs_to_a_real_subject():
    assert set(T.FOCUS_HINTS) <= set(T.CATALOGUE_BY_ID)
    for noun, picks in T.FOCUS_HINTS.values():
        assert noun and picks
        for pick in picks:
            assert M.clean_focus(pick) == pick, f"{pick!r} would be altered when followed"
