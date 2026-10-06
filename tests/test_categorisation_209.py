"""§209: one episode, one category, everywhere it is read.

Pinned here, in the order they were fixed:

* **A1** an event names the episode it is about, so a search at any length
  (and a replay) is logged under its own category;
* **A3** an episode being written logs its play once the writer has said
  what it is about, not before;
* **A2** the player draws an episode by its category, as its tile does;
* **A4** the writer's words are scrubbed, and a re-write with new words does
  not inherit the old episode's category;
* **E18** every written episode logs what each categoriser said;
* **B6** a written live story is ranked under the writer's category;
* **B7** the writer is shown the names its question's branches use;
* **C9** a category matches only as a phrase, in order.
"""
from __future__ import annotations

import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import cache as cache_mod  # noqa: E402
import categories as C  # noqa: E402
import category_audit  # noqa: E402
import script_generator as G  # noqa: E402
import stories as S  # noqa: E402
import thumbnails as th  # noqa: E402
import topics as T  # noqa: E402
from pipeline import PodcastPipeline  # noqa: E402
from script_generator import plan_episode  # noqa: E402
from tts import DebugEngine  # noqa: E402

from test_pipeline import FakeGenerator  # noqa: E402

QUERY = "What a chaotic press conference signals about the fight (Tyson Fury)"


@pytest.fixture
def tree():
    t = T.category_tree()
    C.apply_seed(t)
    return t


@pytest.fixture
def written(monkeypatch):
    """A memory cache, and a way to put an episode in it at any length."""
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", cache_mod.MemoryScriptCache())

    def put(query=QUERY, minutes=2, **extra):
        key = appmod._episode_key(appmod._validated_plan(query, minutes))
        appmod.SCRIPT_CACHE.put(key, ["A sentence."], 600, query, "", minutes,
                                "", "", "", **extra)
        return key
    return put


# --------------------------------------------------------------------------
# A1: an event is filed under the episode that was heard
# --------------------------------------------------------------------------
def test_a_search_at_another_length_is_logged_under_its_category(tree, written):
    """It used to be looked up at the browse length, so a three-minute
    search never found its own category and taught taste the keywords."""
    written(minutes=3, category="heavyweight boxing")
    assert "boxing" not in appmod._event_tags("", QUERY, 2)
    assert "boxing" in appmod._event_tags("", QUERY, 3)


def test_the_heard_episode_id_names_the_episode_whatever_the_length(tree, written):
    key = written(minutes=5, category="heavyweight boxing")
    heard = cache_mod.episode_id(key, appmod.SCRIPT_CACHE.sourced_at(key))
    # Asked at the wrong length, the id still finds it.
    assert "boxing" in appmod._event_tags("", QUERY, 2, heard)


def test_the_event_endpoint_takes_the_length_and_the_episode(tree, written,
                                                              monkeypatch):
    written(minutes=3, category="heavyweight boxing")
    monkeypatch.setattr(appmod, "_remembers", lambda request: True)
    seen = []
    monkeypatch.setattr(appmod.EVENTS, "record", lambda event: seen.append(event))
    with TestClient(appmod.app) as client:
        res = client.post("/api/event", json={"kind": "complete", "text": QUERY,
                                              "minutes": 3})
        assert res.status_code == 200
        # An older client sends neither, and is looked up as it always was.
        assert client.post("/api/event", json={"kind": "skip", "text": QUERY}
                           ).status_code == 200
    assert "boxing" in seen[0].tags
    assert "boxing" not in seen[1].tags


def test_the_client_sends_the_heard_episode_with_complete_and_skip():
    html = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "static", "index.html"),
        encoding="utf-8").read()
    assert 'recordFamEvent("skip", episodeTopicId, title, "", heardEpisode())' in html
    assert html.count("episodeThread,\n                     heardEpisode());") == 2
    assert "FamAudio.episode()" in html


# --------------------------------------------------------------------------
# A3 and E18: a fresh write logs its play, and its audit row, under the
# writer's category
# --------------------------------------------------------------------------
class Categorising(FakeGenerator):
    """A writer that says what its episode was about."""

    def __init__(self, words: str):
        super().__init__(ratio=1.0)
        self.words = words

    async def stream_sentences(self, plan, notes=None):
        async for s in super().stream_sentences(plan, notes):
            yield s
        if notes is not None:
            notes.category = self.words


@pytest.fixture
def client(monkeypatch, tmp_path, tree):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "DEMO_MODE", False)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE",
                        cache_mod.SqliteScriptCache(str(tmp_path / "e.db")))
    gen = Categorising("heavyweight boxing")
    monkeypatch.setattr(
        appmod, "_make_pipeline",
        lambda voice=None, author="": PodcastPipeline(
            generator=gen, engine=DebugEngine(), cache=appmod.SCRIPT_CACHE,
            voice=voice))
    with TestClient(appmod.app) as c:
        c.post("/api/auth/signup", json={"email": "c@b.com",
                                         "password": "password12"})
        yield c


def _plays():
    rows = appmod.EVENTS._conn().execute(
        "SELECT id, tags FROM events WHERE kind = 'play' ORDER BY id").fetchall()
    return [(r[0], set(filter(None, r[1].split(",")))) for r in rows]


def test_a_fresh_episodes_play_is_logged_at_once_then_filed_under_its_category(
        client, monkeypatch):
    """Logged when the audio is served - myFAM drawn while it plays must
    know it was heard, and a skip must never precede its play - and the
    same row is re-filed once the writer has said what it was about."""
    at_serve = []
    real = appmod.EVENTS.record

    def record(event):
        row = real(event)
        if event.kind == "play":
            at_serve.append(set(event.tags))
        return row
    monkeypatch.setattr(appmod.EVENTS, "record", record)
    q = "what a chaotic press conference signals about the fight"
    res = client.get(f"/api/audio?q={q}&minutes=1&fmt=pcm&surface=search")
    assert res.status_code == 200
    assert res.headers["X-FAM-Cache"] != "hit"
    assert len(at_serve) == 1 and "boxing" not in at_serve[0], \
        "logged at serve time, before anything was written"
    plays = _plays()
    assert len(plays) == 1, "a written episode's play was logged twice or never"
    assert {"boxing", "combat sports", "sports"} <= plays[0][1]

    # And its replay is logged at once, under the same category.
    again = client.get(f"/api/audio?q={q}&minutes=1&fmt=pcm&surface=search")
    assert again.headers["X-FAM-Cache"] == "hit"
    plays = _plays()
    assert len(plays) == 2 and "boxing" in plays[1][1]


def test_a_play_whose_episode_was_never_stored_keeps_its_keyword_tags(
        client, monkeypatch):
    """Left before it was written: nothing to re-file it under, and never
    the category of an older episode under the same key."""
    monkeypatch.setattr(appmod.SCRIPT_CACHE, "resolve_episode", lambda e: "")
    q = "what a chaotic press conference signals about the fight"
    client.get(f"/api/audio?q={q}&minutes=1&fmt=pcm&surface=search")
    plays = _plays()
    assert len(plays) == 1 and "boxing" not in plays[0][1]


def test_retag_corrects_one_row_and_never_raises():
    store = appmod.EVENTS
    row = store.record(T.Event("u", "play", "", "q", ("science",)))
    other = store.record(T.Event("u", "play", "", "q2", ("science",)))
    assert row and other and row != other
    assert store.retag(row, ("boxing", "sports"))
    tags = dict(store._conn().execute("SELECT id, tags FROM events").fetchall())
    assert tags[row] == "boxing,sports" and tags[other] == "science"
    assert store.retag(None, ("x",)) is False
    assert store.record(T.Event("u", "not-a-kind", "", "q", ())) is None


def test_a_written_episode_logs_what_each_categoriser_said(client):
    q = "what a chaotic press conference signals about the fight"
    client.get(f"/api/audio?q={q}&minutes=1&fmt=pcm&surface=search")
    rows = T.category_tree().audit_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["query"] == q and row["origin"] == "search"
    assert row["words"] == "heavyweight boxing"
    assert row["writer"] == "boxing" and row["writer_facet"] == "sports"
    assert row["composer"] == ""   # not a pool story


# --------------------------------------------------------------------------
# E18: the audit's numbers
# --------------------------------------------------------------------------
def test_the_audit_counts_agreement_and_names_the_disagreements(tree):
    category_audit.note("what Fury said about the fight", "heavyweight boxing",
                        "trending", composer="boxing")
    category_audit.note("a boxing study on concussions", "heavyweight boxing",
                        "trending", composer="science")
    category_audit.note("how tides work", "", "prefetch")
    category_audit.note("something odd", "zzzz qqqq", "search")
    found = category_audit.summary(days=1)
    assert found["episodes"] == 4
    assert found["worded"] == 3 and found["placed"] == 2
    assert found["placed_rate"] == round(2 / 3, 3)
    assert found["with_composer"] == 2
    assert found["writer_vs_composer"] == 0.5
    assert found["composer_to_writer"] == [
        {"composer": "science", "writer": "sports", "episodes": 1}]
    assert found["examples"]["unplaced"] == [
        {"query": "something odd", "words": "zzzz qqqq"}]


def test_an_empty_audit_reports_no_data_rather_than_zero(tree):
    found = category_audit.summary(days=1)
    assert found["episodes"] == 0
    assert found["placed_rate"] is None
    assert found["writer_vs_composer"] is None


def test_a_wipe_of_the_tree_empties_the_audit(tree):
    category_audit.note("what Fury said", "heavyweight boxing", "search")
    assert tree.audit_rows()
    tree.clear()
    assert tree.audit_rows() == []


def test_the_audit_composer_is_the_pool_storys_own(tree, monkeypatch):
    story = S.Story(subject="fury", title="Fury", angle="a", query="q fury",
                    category="boxing", first_seen=time.time())
    monkeypatch.setattr(S, "pool", lambda: S.Pool(stories=[story]))
    assert category_audit.composer_category("q fury") == "boxing"
    assert category_audit.composer_category("other") == ""


# --------------------------------------------------------------------------
# A2: the player draws an episode by its category
# --------------------------------------------------------------------------
def test_the_player_card_passes_the_written_category(tree, written, monkeypatch):
    written(minutes=3, category="heavyweight boxing")
    asked = {}

    def spy(text, key="", category=""):
        asked["category"] = category
        return None
    monkeypatch.setattr(th, "pick_for_player", spy)
    with TestClient(appmod.app) as client:
        client.get("/api/episode/card", params={"q": QUERY, "minutes": 3})
        assert asked["category"] == "boxing"
        client.get("/api/episode/card", params={"q": QUERY})
        assert asked["category"] == "", "no length named: nothing to look up"


def test_pick_for_player_puts_the_category_first(tree, monkeypatch):
    """The category outranks the words when it borrows, as on a tile."""
    monkeypatch.setattr(th, "_exists", lambda: True)

    class Held:
        def approved(self):
            return {"boxing": ("b.png", "sports"), "science": ("s.png", "science")}

        def refresh_if_changed(self):
            return None
    monkeypatch.setattr(th, "store", lambda: Held())
    monkeypatch.setattr(th, "url_for", lambda node, f: f"/t/{f}")
    th._MEMO.clear()
    found = th.pick_for_player("a new study about a fight", category="boxing")
    assert found["node"] == "boxing"


# --------------------------------------------------------------------------
# A4: the writer's words, and a re-write
# --------------------------------------------------------------------------
def test_the_category_words_are_scrubbed_and_need_letters():
    assert G.extract_category("<<CATEGORY: 2026 >>") == ""
    assert G.extract_category("<<CATEGORY: heavyweight boxing.>>") == \
        "heavyweight boxing"
    assert G.extract_category("no marker") == ""


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_a_rewrite_with_new_words_does_not_keep_the_old_category(kind, tmp_path):
    store = (cache_mod.MemoryScriptCache() if kind == "memory"
             else cache_mod.SqliteScriptCache(str(tmp_path / "s.db")))
    store.put("k", ["About boxing."], 600, "q", category="heavyweight boxing")
    # The same words, no category: kept (a longer TTL, fresher sources).
    store.put("k", ["About boxing."], 600, "q")
    assert store.category("k") == "heavyweight boxing"
    # New words, no category: the old one described the old script.
    store.put("k", ["About something else now."], 600, "q")
    assert store.category("k") == ""
    # New words with their own: theirs.
    store.put("k", ["About tennis."], 600, "q", category="tennis")
    assert store.category("k") == "tennis"


# --------------------------------------------------------------------------
# B6: a written live story is ranked as what it turned out to be about
# --------------------------------------------------------------------------
def _story(**kw):
    base = dict(subject="fury joshua", title="Fury and Joshua Face Off",
                angle="What a chaotic press conference signals", query=QUERY,
                tags=("science", "sports"), category="")
    base.update(kw)
    return S.Story(**base)


def test_a_written_news_story_is_ranked_under_the_writers_category(tree,
                                                                    monkeypatch):
    monkeypatch.setattr(T, "_WRITTEN_CATEGORY",
                        lambda q: "boxing" if q == QUERY else "")
    tile = T.topics_from_stories([_story()])[0]
    assert tile.category == "boxing"
    assert {"boxing", "combat sports", "sports"} <= set(tile.tags)
    assert "science" not in tile.tags
    assert "boxing" in T.topic_tags(tile)


def test_a_written_game_keeps_its_providers_tags(tree, monkeypatch):
    monkeypatch.setattr(T, "_WRITTEN_CATEGORY", lambda q: "boxing")
    tile = T.topics_from_stories([_story(domain=S.SPORTS,
                                         tags=("sports", "nfl"))])[0]
    assert tile.category == "boxing"   # the picture
    assert tile.tags == ("sports", "nfl")


def test_the_composers_overruled_node_goes_with_its_guess(tree, monkeypatch):
    """Same facet, different subject: the composer's node and what only it
    implied stop being scored; the facet stays."""
    monkeypatch.setattr(T, "_WRITTEN_CATEGORY", lambda q: "boxing")
    composed = S.refine_tags(("sports",), "mixed martial arts", QUERY)
    tile = T.topics_from_stories([_story(tags=composed,
                                         category="mixed martial arts")])[0]
    assert "mixed martial arts" not in tile.tags
    assert {"boxing", "sports"} <= set(tile.tags)


def test_without_a_source_a_story_ranks_as_before(tree, monkeypatch):
    monkeypatch.setattr(T, "_WRITTEN_CATEGORY", None)
    tile = T.topics_from_stories([_story(category="boxing")])[0]
    assert tile.tags == ("science", "sports")
    assert tile.category == "boxing"


def test_the_server_probe_reads_the_cache_at_the_browse_length(tree, written):
    written(minutes=appmod.BROWSE_MINUTES, category="heavyweight boxing")
    assert T._WRITTEN_CATEGORY is appmod._written_category_probe
    assert appmod._written_category_probe(QUERY) == "boxing"
    assert appmod._written_category_probe("never written") == ""


# --------------------------------------------------------------------------
# B7: the writer is shown the names its question's branches use
# --------------------------------------------------------------------------
def test_the_writer_is_shown_its_questions_branches(tree):
    lines = S.writer_vocabulary("the heavyweight boxing title fight")
    assert lines and all(line.startswith("- sports / ") for line in lines)
    assert len(lines) <= S.WRITER_VOCABULARY_LINES
    assert S.writer_vocabulary("") == []


def test_the_writers_own_branch_comes_first_down_to_the_team(tree):
    """A Bengals question is offered the team and its path, deepest first -
    not only the league - so "use those exact words" never pushes the writer
    up from the most specific name."""
    lines = S.writer_vocabulary("can the cincinnati bengals make the playoffs")
    assert "cincinnati bengals" in lines[0] or "bengals" in lines[0]
    assert lines[0].startswith("- sports / ")


def test_a_grown_tree_does_not_crowd_sports_out_of_the_writers_list(tree):
    """The composer's list is capped and filled facet by facet alphabetically;
    the writer's is built per facet, so a big business branch costs a sports
    question nothing."""
    for i in range(200):
        tree.mint(f"business thing{chr(97 + i % 26)}{chr(97 + i // 26)}", "business")
    lines = S.writer_vocabulary("the heavyweight boxing title fight")
    assert lines and all(line.startswith("- sports / ") for line in lines)


def test_the_writers_list_is_rebuilt_only_when_the_tree_is(tree):
    S.writer_vocabulary("boxing")
    shape = S._WRITER_SHAPE
    S.writer_vocabulary("boxing again")
    assert S._WRITER_SHAPE is shape
    tree.mint("bare knuckle boxing", "boxing")
    assert any("bare knuckle boxing" in line
               for line in S.writer_vocabulary("bare knuckle boxing tonight"))


def test_the_vocabulary_is_in_the_user_turn_never_the_system_prompt(tree):
    plan = plan_episode("the heavyweight boxing title fight", 2)
    prompt = G.build_prompt(plan)
    assert "FAM files episodes under names like these" in prompt
    assert "- sports / " in prompt
    assert "names like these" not in G.SYSTEM_PROMPT
    assert prompt.index("- sports / ") < prompt.index("<<CATEGORY:")


# --------------------------------------------------------------------------
# C9: a phrase, in order
# --------------------------------------------------------------------------
@pytest.fixture
def hockey(tree):
    tree.mint("carolina hurricanes", "sports")
    tree.mint("bank england", "money")
    return tree


@pytest.mark.parametrize("text", [
    "Carolina Hurricanes win in overtime",
    "can the carolina hurricanes repeat",
    "Carolina's Hurricanes",
])
def test_a_phrase_in_order_matches(hockey, text):
    assert "carolina hurricanes" in hockey.match(text)


@pytest.mark.parametrize("text", [
    "Hurricanes hitting the Carolina coast",
    "hurricanes carolina",
    "carolina braces as two hurricanes form",
])
def test_the_same_words_out_of_order_or_apart_do_not(hockey, text):
    assert "carolina hurricanes" not in hockey.match(text)


def test_short_words_between_do_not_break_a_run(hockey):
    assert "bank england" in hockey.match("what the Bank of England did")
