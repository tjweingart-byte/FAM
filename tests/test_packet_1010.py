"""10.10: a misspelled search, the picture a searched episode plays over, and
the picture on an Instant feedback report.

* **#1** autocorrect takes "supr" to "super", not the swap "spur"; Go Deeper's
  and What's next's boxes are corrected at send like the search box; EI is
  told a near-name is the real thing ("general automics" is General Atomics)
  and its gate no longer reverts a corrected spelling to the typo; the writer
  is told never to report the misspelling as a thing that does not exist.
* **#2** the brief names the episode's category before the first word, on the
  live track beside its title; the player's card is drawn from it (and from
  the writer's, once written) and says so (`placed`), and the player lets a
  placed picture replace one matched off the typed words.
* **#3** a feedback report keeps the picture the player showed - only FAM's
  own thumbnail route - and the inbox draws it.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as appmod  # noqa: E402
import autocorrect  # noqa: E402
import cache as cache_mod  # noqa: E402
import categories as C  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import feedback  # noqa: E402
import live_captions  # noqa: E402
import script_generator as G  # noqa: E402
import thumbnails as th  # noqa: E402
import topics as T  # noqa: E402


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------
# #1 autocorrect and EI on a misspelled search
# --------------------------------------------------------------------------
@pytest.mark.skipif(not autocorrect.available(),
                    reason="pyspellchecker is not installed")
def test_a_swap_far_less_common_than_another_fix_is_not_taken():
    assert autocorrect.correct_text("who won the supr bowl") == "who won the super bowl"
    # A swap with nothing far commoner beside it is still taken outright.
    assert autocorrect.correct_text("Teh fed") == "The fed"
    assert autocorrect.correct_text("the leage table") == "the league table"


def test_every_search_box_is_corrected_at_send():
    html = _read("static", "index.html")
    for box in ('id="goDeeperInput"', 'id="nextUpSearchInput"'):
        tag = html[html.index(box) - 200: html.index(box) + 400]
        tag = tag[tag.rindex("<input"):]
        tag = tag[:tag.index(">")]
        assert "data-autocorrect" in tag and 'autocorrect="on"' in tag, box
    assert html.count("acCorrectQuestion(val)") == 2


def test_ei_is_told_a_near_name_is_the_real_thing():
    assert "general automics\" is General Atomics" in ei.EI_SYSTEM
    assert "never for the typo" in ei.EI_SYSTEM
    prompt = ei.build_ei_prompt("general automics", 2)
    assert "correct what is misspelled" in prompt


def test_the_writer_never_reports_the_typo_as_a_missing_thing():
    prompt = G.build_prompt(G.plan_episode("general automics", 2))
    assert "never tell them that nothing spelled" in prompt


@pytest.mark.parametrize("asked,searching", [
    ("general automics", "General Atomics Aeronautical drone contracts"),
    ("ohtanni", "Shohei Ohtani home run record"),
    ("nvidea", "Nvidia quarterly results"),
])
def test_the_gate_keeps_a_corrected_spelling(asked, searching):
    brief = ei.Brief(query=asked, intent="explainer", subject=searching,
                     search_query=searching)
    ei.gate(brief, asked)
    assert brief.search_query == searching
    assert not any("shares no subject" in n for n in brief.notes)


def test_the_gate_still_reverts_a_replaced_subject():
    brief = ei.Brief(query="eagles roster", intent="explainer",
                     subject="New York Giants", search_query="Giants playoff odds")
    ei.gate(brief, "eagles roster")
    assert brief.search_query == "eagles roster"


def test_short_words_are_never_respellings():
    assert not ei._respelled("apple", "ample")
    assert not ei._respelled("eagles", "giants")
    assert ei._respelled("automics", "atomics")


# --------------------------------------------------------------------------
# #2 the brief's category picks the player's picture
# --------------------------------------------------------------------------
def test_the_brief_asks_for_a_category():
    assert "category" in ei.BRIEF_SCHEMA["properties"]
    assert "category" in ei.BRIEF_SCHEMA["required"]
    assert "- **category**" in ei.build_ei_prompt("general automics", 2)
    assert ei.fallback_brief("x", "test").category == ""


def test_the_live_track_holds_the_category_and_never_opens_one():
    live_captions.reset()
    live_captions.publish_category("k", "drone makers")
    assert live_captions.read_category("k") == "", "never opens a track"
    live_captions.open_track("k")
    live_captions.publish_category("k", "  drone   makers ")
    assert live_captions.read_category("k") == "drone makers"
    live_captions.reset()


def test_publish_title_puts_the_brief_category_on_the_track():
    live_captions.reset()
    live_captions.open_track("k2")
    notes = G.ScriptNotes()
    notes.caption_key = "k2"
    G._publish_title(notes, ei.Brief(query="q", title="T", category="drone makers"))
    assert live_captions.read_category("k2") == "drone makers"
    live_captions.reset()


@pytest.fixture
def tree():
    t = T.category_tree()
    C.apply_seed(t)
    return t


def test_the_card_is_drawn_from_the_category_on_the_live_track(tree, monkeypatch):
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", cache_mod.MemoryScriptCache())
    q = "what happened at the press conference"
    key = appmod._episode_key(appmod._validated_plan(q, 3))
    live_captions.reset()
    live_captions.open_track(key)
    live_captions.publish_category(key, "heavyweight boxing")
    asked = {}

    def spy(text, key="", category="", tags=None):
        asked["category"] = category
        return {"node": category or "x", "facet": "sports",
                "url": "/api/thumb/x", "fallback": False}
    monkeypatch.setattr(th, "pick_for_player", spy)
    try:
        with TestClient(appmod.app) as client:
            body = client.get("/api/episode/card",
                              params={"q": q, "minutes": 3}).json()
            assert asked["category"] == "boxing"
            assert body["placed"] is True
            # Nothing on the track for another question: matched off the words.
            body = client.get("/api/episode/card",
                              params={"q": "something else", "minutes": 3}).json()
            assert asked["category"] == "" and body["placed"] is False
    finally:
        live_captions.reset()


def test_a_follow_ups_card_is_keyed_with_what_it_follows(tree, monkeypatch):
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", cache_mod.MemoryScriptCache())
    q, ctx = "the economics of it", "Fury v Usyk"
    key = appmod._episode_key(appmod._validated_plan(q, 2, ctx))
    live_captions.reset()
    live_captions.open_track(key)
    live_captions.publish_category(key, "heavyweight boxing")
    try:
        assert appmod._playing_category(q, 2, "", ctx) == "boxing"
        assert appmod._playing_category(q, 2) == ""
    finally:
        live_captions.reset()


def test_the_player_lets_a_placed_picture_replace_a_word_match():
    html = _read("static", "index.html")
    assert "var may = d.placed ? !PLAYER_CARD.fromTile" in html
    assert '&context=" + encodeURIComponent(PLAYER_CARD.context)' in html
    # A title landing asks again unless the tile's own picture is showing.
    start = html.index("function refreshPlayerCard(title){")
    body = html[start:html.index("}", html.index("askPlayerCard(", start))]
    assert "PLAYER_CARD.fromTile) return;" in body
    assert "PLAYER_CARD.thumb && !PLAYER_CARD.borrowed) return;" not in body


def test_the_writers_category_replaces_the_briefs_on_the_track():
    source = _read("pipeline.py")
    at = source.index("live_captions.publish_title(stats.caption_key, notes.title, final=True)")
    assert "live_captions.publish_category(stats.caption_key, notes.category)" in \
        source[at:at + 400]


# --------------------------------------------------------------------------
# #3 a feedback report keeps the episode's picture
# --------------------------------------------------------------------------
def test_only_fams_own_thumbnails_are_kept():
    assert feedback.thumb_path("/api/thumb/boxing?v=1728000000") == \
        "/api/thumb/boxing?v=1728000000"
    assert feedback.thumb_path("/api/thumb/combat%20sports") == "/api/thumb/combat%20sports"
    for bad in ("https://evil.example/x.png", "/api/thumb/../admin",
                "javascript:alert(1)", "/api/thumb/x\" onerror=\"y", ""):
        assert feedback.thumb_path(bad) == "", bad
    snap = feedback.episode_snapshot("q", 2, "t", {}, [], thumb="/api/thumb/boxing")
    assert snap["thumb"] == "/api/thumb/boxing"
    assert feedback.episode_snapshot("q", 2, thumb="http://x/y.png")["thumb"] == ""


def test_a_report_keeps_the_picture_the_player_showed(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "FEEDBACK",
                        feedback.FeedbackStore(str(tmp_path / "f.db")))
    monkeypatch.setattr(appmod, "_player_picture",
                        lambda *a, **k: {"url": "/api/thumb/server-pick"})
    with TestClient(appmod.app) as client:
        client.post("/api/feedback", json={
            "text": "wrong picture", "screen": "player",
            "episode": {"q": "general automics", "minutes": 2,
                        "thumb": "/api/thumb/drones?v=5"}})
        client.post("/api/feedback", json={
            "text": "no picture sent", "screen": "player",
            "episode": {"q": "general automics", "minutes": 2,
                        "thumb": "https://elsewhere.example/p.png"}})
    rows = {r["text"]: r for r in appmod.FEEDBACK.list()}
    assert rows["wrong picture"]["episode"]["thumb"] == "/api/thumb/drones?v=5"
    assert rows["no picture sent"]["episode"]["thumb"] == "/api/thumb/server-pick"


def test_the_inbox_and_the_page_carry_the_picture():
    admin = _read("admin_ui", "tracker.html")
    assert "function episodeThumb(e)" in admin
    assert '<img class="thumb" src="' in admin
    html = _read("static", "index.html")
    assert 'thumb: (typeof PLAYER_CARD !== "undefined" && PLAYER_CARD.thumb) || ""' in html
    preview = _read("preview", "build_live_preview.py")
    assert "fd-thumb" in preview
