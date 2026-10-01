"""The 10.1 implementations packet, the second set (PROBLEMS.md §182)."""

from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import autocorrect  # noqa: E402
from cache import MemoryScriptCache, cache_key, ttl_for  # noqa: E402
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

def test_the_mic_and_attach_are_on_the_bars_right():
    """§190: Google's shape - the mic and attach inside the search bar, on
    its right, after the question."""
    bar = INDEX.split('<div class="fam-input-box" id="searchBar">', 1)[1].split(
        '<div class="attach-row"', 1)[0]
    box = bar.index('id="searchInput"')
    mic = bar.index('id="voiceMicBtn"')
    attach = bar.index('id="attachBtn"')
    assert 0 < box < mic < attach
    assert ".voice-mic[hidden]{ display:none; }" in INDEX


def test_length_and_voice_are_bubbles_that_do_not_print_the_choice():
    """§190: two bubbles under the bar; the choice is in the menus only."""
    controls = INDEX.split('<div class="fam-controls">', 1)[1].split(
        'id="trendSearches"', 1)[0]
    assert 'onclick="openLengthMenu()"' in controls and ">Length</button>" in controls
    assert 'onclick="openVoiceMenu()"' in controls and ">Voice</button>" in controls
    for gone in ('id="lengthVal"', 'id="homeVoiceName"', " min<"):
        assert gone not in controls, gone


def test_the_length_is_two_minutes_again_on_coming_back():
    assert "var DEFAULT_LENGTH_MINUTES = 2;" in INDEX
    block = INDEX.split("var DEFAULT_LENGTH_MINUTES = 2;", 1)[1].split("});", 1)[0]
    assert 'document.visibilityState !== "visible"' in block
    assert "selectedLengthMinutes = DEFAULT_LENGTH_MINUTES;" in block


# --- 3b. Trending searches, and no cached scores ---------------------------

def _put(store, key, query, *, ttl=3600, plays=0, title="", minutes=2,
         origin="search", context=""):
    # The key a search for these words computes - `key` only tells the rows
    # apart in the test, so each gets its own question.
    key = cache_key(query, minutes, None, context, True)
    store.put(key, ["A sentence."], ttl=ttl, query=query, minutes=minutes,
              title=title, origin=origin)
    for _ in range(plays):
        store.record_play(key)


@pytest.fixture
def searches(tmp_path, monkeypatch):
    """An event log to search into; `searches(query, n, ago)` records `n`
    listeners searching `query` `ago` seconds back."""
    import time as _time
    import topics as topics_mod
    store = topics_mod.EventStore(str(tmp_path / "events.db"))
    monkeypatch.setattr(appmod, "EVENTS", store)
    counter = iter(range(10_000))

    def record(query, n=1, ago=60.0):
        for _ in range(n):
            store.record(topics_mod.Event(user_id="u%d" % next(counter),
                                          kind="search", text=query,
                                          at=_time.time() - ago))
    return record


def test_trending_searches_are_current_searched_episodes(client, monkeypatch, searches):
    store = MemoryScriptCache()
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", store)
    _put(store, "a", "how reusable rockets work", plays=5,
         title="Why Rockets Land Themselves")
    _put(store, "b", "what the fed does next", plays=9)
    _put(store, "c", "eagles score right now", ttl=0, plays=50)   # not current
    _put(store, "d", "a myfam tile", plays=40, origin="myfam")      # not a search
    _put(store, "e", "How reusable rockets WORK?", plays=1, minutes=3)  # same question
    _put(store, "f", "and the economics of it", plays=30,          # a follow-up
         context="how reusable rockets work")
    _put(store, "g", "why the dollar fell", plays=80)              # searched long ago
    # §190: most searched in the last two hours, in order - plays only
    # break ties.
    searches("how reusable rockets work", 3)
    searches("What the Fed does next?", 2)
    for q in ("eagles score right now", "a myfam tile", "and the economics of it"):
        searches(q, 5)
    searches("why the dollar fell", 9, ago=appmod.TRENDING_SEARCHES_WINDOW + 60)
    out = client.get("/api/searches/trending").json()["searches"]
    assert [s["query"] for s in out] == ["how reusable rockets work",
                                         "what the fed does next"]
    assert [s["searches"] for s in out] == [3, 2]
    assert out[0]["title"] == "Why Rockets Land Themselves"
    assert out[1]["minutes"] == 2
    assert len(out) <= appmod.TRENDING_SEARCHES_MAX <= 10


def test_one_listener_searching_twice_is_one_search(client, monkeypatch, searches):
    import time as _time
    import topics as topics_mod
    store = MemoryScriptCache()
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", store)
    _put(store, "a", "how reusable rockets work")
    _put(store, "b", "what the fed does next")
    for _ in range(5):
        appmod.EVENTS.record(topics_mod.Event(user_id="same", kind="search",
                                              text="what the fed does next",
                                              at=_time.time() - 30))
    searches("how reusable rockets work", 2)
    out = client.get("/api/searches/trending").json()["searches"]
    assert [s["query"] for s in out] == ["how reusable rockets work",
                                         "what the fed does next"]


def test_trending_searches_never_generate(client, monkeypatch):
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", MemoryScriptCache())
    assert client.get("/api/searches/trending").json() == {"searches": []}


def _ttl_through_gate(query, intent, live_status=""):
    """`ttl_for` with the brief EI would actually hand over: `gate` marks
    every live-domain brief outcome-dependent, which the first version of the
    sports rule missed (its tests passed combinations EI never produces)."""
    import episode_intelligence as ei
    brief = ei.gate(ei.Brief(query=query, intent=intent, subject=query,
                             search_query=query, live_domain="sports"), query)
    assert brief.outcome_dependent
    return ttl_for(query, live_status=live_status,
                   outcome_dependent=brief.outcome_dependent,
                   recency_days=brief.recency_days,
                   live_domain=brief.live_domain, intent=brief.intent)


def test_a_sports_score_or_update_not_yet_final_is_never_current():
    # A score or a game update, with no provider answer or one not settled.
    assert _ttl_through_gate("bills score", "recap") == 0
    assert _ttl_through_gate("how is the bills game going", "update",
                             live_status="unknown") == 0
    assert _ttl_through_gate("bills game", "update", live_status="in_progress") == 0
    assert _ttl_through_gate("who won bills game", "recap", live_status="scheduled") == 0
    # A final result does not move.
    assert _ttl_through_gate("who won the bills game", "recap",
                             live_status="final") > 0


def test_a_sports_preview_keeps_its_two_hours():
    """§173: "upcoming dodgers game" asked twice in an afternoon is one
    episode. The sports rule must not take that back."""
    assert _ttl_through_gate("upcoming dodgers game", "preview",
                             live_status="scheduled") == settings.cache_ttl_volatile
    assert _ttl_through_gate("upcoming dodgers game", "preview") == \
        min(settings.cache_ttl_seconds, settings.cache_ttl_volatile)
    assert _ttl_through_gate("how the dodgers rotation works", "explainer") > 0


def test_outside_sports_nothing_changes():
    assert ttl_for("who wins the vote", outcome_dependent=True,
                   live_domain="elections", intent="recap") == min(
        settings.cache_ttl_seconds, settings.cache_ttl_volatile)


def test_the_domain_reaches_the_cache_policy():
    for f in ("pipeline.py", "prefetch.py"):
        src = (ROOT / f).read_text()
        assert 'live_domain=getattr(notes, "live_domain", "")' in src, f
        assert 'intent=getattr(notes, "intent", "")' in src, f
    sg = (ROOT / "script_generator.py").read_text()
    assert 'notes.live_domain = str(getattr(plan.brief, "live_domain", "") or "")' in sg
    assert 'notes.intent = str(getattr(plan.brief, "intent", "") or "")' in sg


def test_the_search_page_shows_trending_searches_under_the_bubbles():
    assert 'id="trendSearches"' in INDEX and "Trending searches" in INDEX
    assert 'fetch("/api/searches/trending")' in _fn("loadTrendSearches")
    paint = _fn("paintTrendSearches")
    # §190: shown whenever the box is empty, not only while it is focused.
    assert "!input.value.trim() && !ATTACHED.length" in paint
    assert "document.activeElement" not in paint
    assert 'if(id === "home") loadTrendSearches();' in INDEX
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
def test_trending_and_explore_never_title_a_misspelling(client, monkeypatch, searches):
    store = MemoryScriptCache()
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", store)
    _put(store, "a", "whta happend in the elecion", plays=2)
    searches("whta happend in the elecion")
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


def test_the_player_names_a_searcher_only_when_they_chose_to(client, monkeypatch):
    """§190: the player's menu shows who searched an episode only when that
    listener turned on `searches_public`, and never an id."""
    store = MemoryScriptCache()
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", store)
    key = cache_key("how reusable rockets work", 2, None, "", True)
    store.put(key, ["A sentence."], ttl=3600, query="how reusable rockets work",
              minutes=2, origin="search", author="author-1")
    monkeypatch.setattr(appmod.SOCIAL, "person",
                        lambda uid: {"handle": "rocketfan"} if uid == "author-1" else {})
    ask = lambda: client.get("/api/episode/card",
                             params={"q": "How reusable rockets work?"}).json()
    assert ask()["searcher"] == ""
    appmod.PREFS.save("author-1", searches_public=True)
    out = ask()
    assert out["searcher"] == "@rocketfan"
    assert "author-1" not in str(out)
