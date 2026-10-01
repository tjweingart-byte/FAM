"""The 10.1 implementations packet, the second set (PROBLEMS.md §181)."""

from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import autocorrect  # noqa: E402
from cache import MemoryScriptCache, ttl_for  # noqa: E402
from config import settings  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    return TestClient(appmod.app)


# --- 1. Go Deeper is yellow and in capitals --------------------------------

def test_go_deeper_is_yellow_and_in_capitals():
    css = INDEX.split("  .go-deeper-pill{", 1)[1].split("}", 1)[0]
    assert "background:var(--deeper)" in css
    assert "--deeper:#FFD23F" in INDEX
    assert ">Go Deeper</button>" not in INDEX
    assert INDEX.count(">GO DEEPER</button>") == 4


# --- 2. Find new friends on myFAM's friends rail ---------------------------

def test_the_friends_rail_offers_find_new_friends():
    body = _fn("renderMyFamFeed")
    # The same pill as YourFAM's, opening the same page.
    assert 'class="yf-find" onclick="openFriends()"' in body
    assert "Find new friends" in body
    # In place of the sentence when they follow nobody ...
    assert 'sec.key === "followers" && !sec.has_circle' in body
    # ... and under the episodes when there are some.
    assert "heading + rail + findFriends" in body


def test_the_feed_says_whether_they_follow_anybody():
    src = (ROOT / "topics.py").read_text()
    assert '**({"has_circle": bool(circle)} if key == "followers" else {})' in src


# --- 3a. The mic sits beside attach, the same size -------------------------

def test_the_mic_is_on_the_attach_line():
    controls = INDEX.split('<div class="fam-controls">', 1)[1].split(
        'id="attachFile"', 1)[0]
    mic = controls.index('id="voiceMicBtn"')
    attach = controls.index('id="attachBtn"')
    assert 0 < mic < attach
    css = INDEX.split("  .voice-mic{", 1)[1].split("}", 1)[0]
    assert "width:36px; height:36px" in css
    assert ".voice-mic[hidden]{ display:none; }" in INDEX


# --- 3b. Trending searches, and no cached scores ---------------------------

def _put(store, key, query, *, ttl=3600, plays=0, title="", minutes=2,
         origin="search"):
    store.put(key, ["A sentence."], ttl=ttl, query=query, minutes=minutes,
              title=title, origin=origin)
    for _ in range(plays):
        store.record_play(key)


def test_trending_searches_are_current_searched_episodes(client, monkeypatch):
    store = MemoryScriptCache()
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", store)
    _put(store, "a", "how reusable rockets work", plays=5,
         title="Why Rockets Land Themselves")
    _put(store, "b", "what the fed does next", plays=9)
    _put(store, "c", "eagles score right now", ttl=0, plays=50)   # not current
    _put(store, "d", "a myfam tile", plays=40, origin="myfam")      # not a search
    _put(store, "e", "How reusable rockets WORK?", plays=1)          # same question
    out = client.get("/api/searches/trending").json()["searches"]
    assert [s["query"] for s in out] == ["what the fed does next",
                                         "how reusable rockets work"]
    assert out[1]["title"] == "Why Rockets Land Themselves"
    assert out[0]["minutes"] == 2
    assert len(out) <= appmod.TRENDING_SEARCHES_MAX <= 10


def test_trending_searches_never_generate(client, monkeypatch):
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", MemoryScriptCache())
    assert client.get("/api/searches/trending").json() == {"searches": []}


def test_a_sports_result_not_yet_final_is_never_current():
    # The listener asked for a result, in sports, and no evidence settled it.
    assert ttl_for("bills game", outcome_dependent=True,
                   live_domain="sports") == 0
    assert ttl_for("bills game", outcome_dependent=True, live_status="scheduled",
                   live_domain="sports") == 0
    assert ttl_for("bills game", live_status="in_progress",
                   live_domain="sports") == 0
    # A final result does not move.
    assert ttl_for("bills game", outcome_dependent=True, live_status="final",
                   live_domain="sports") > 0
    # Outside sports, and for a preview, nothing changes.
    assert ttl_for("who wins the vote", outcome_dependent=True,
                   live_domain="elections") == min(
        settings.cache_ttl_seconds, settings.cache_ttl_volatile)
    assert ttl_for("bills preview", live_status="scheduled",
                   live_domain="sports") > 0


def test_the_domain_reaches_the_cache_policy():
    for f in ("pipeline.py", "prefetch.py"):
        src = (ROOT / f).read_text()
        assert 'live_domain=getattr(notes, "live_domain", "")' in src, f
    sg = (ROOT / "script_generator.py").read_text()
    assert 'notes.live_domain = str(getattr(plan.brief, "live_domain", "") or "")' in sg


def test_the_search_box_shows_trending_searches_on_focus():
    assert 'id="trendSearches"' in INDEX and "Trending searches" in INDEX
    assert 'fetch("/api/searches/trending")' in _fn("loadTrendSearches")
    paint = _fn("paintTrendSearches")
    assert "document.activeElement === input && !input.value.trim()" in paint
    run = _fn("runTrendSearch")
    # A search, at the length its episode was written for.
    assert "exploreMinutes: t.minutes" in run and 'surface: "search"' in run


# --- 4. Autocorrect at send, and titles spelled correctly ------------------

def test_a_search_is_corrected_as_a_whole_when_it_is_sent():
    run = _fn("runSearch")
    assert "acCorrectQuestion(typed)" in run
    assert "deriveTitleFromPrompt(titleText || val)" in _fn("sendSearch")
    apply = _fn("acApply")
    # The title takes every correction; the question keeps a word put back.
    assert "title = title.slice(0, w.start) + fix" in apply
    assert "if(!AC_REVERTED[w.word.toLowerCase()])" in apply


needs_speller = pytest.mark.skipif(not autocorrect.available(),
                                   reason="pyspellchecker is not installed")


@needs_speller
def test_correct_text_fixes_the_last_word_and_leaves_names():
    assert autocorrect.correct_text("whta happend in the elecion") == \
        "what happened in the election"
    assert autocorrect.correct_text("Teh price of bitcoin") == "The price of bitcoin"
    assert autocorrect.correct_text("Messi and Mahomes") == "Messi and Mahomes"


@needs_speller
def test_trending_and_explore_never_title_a_misspelling(client, monkeypatch):
    store = MemoryScriptCache()
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", store)
    _put(store, "a", "whta happend in the elecion", plays=2)
    out = client.get("/api/searches/trending").json()["searches"]
    assert out[0]["query"] == "whta happend in the elecion"
    assert out[0]["spelled"] == "what happened in the election"
    cards = client.get("/api/explore").json()["episodes"]
    assert cards[0]["title"] == "What happened in the election"


def test_the_writers_are_told_to_spell_titles_correctly():
    assert "**Spelled correctly** even when the request is not" in \
        (ROOT / "episode_intelligence.py").read_text()
    # In the episode's own instructions; the system prompt is held lean
    # (`test_the_prompt_stays_lean`).
    assert "Spell it correctly, even where the question was misspelled" in \
        (ROOT / "script_generator.py").read_text()
