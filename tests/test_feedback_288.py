"""The 10.16 instant feedback (PROBLEMS.md §288)."""
from __future__ import annotations

import asyncio
import os
import pathlib
import sqlite3
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import live_captions  # noqa: E402
import messages as messages_mod  # noqa: E402
from cache import cache_key, episode_id  # noqa: E402
from episode_marks import EpisodeMarks  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


# --- 1. the first loading step ----------------------------------------------

def test_the_first_step_is_the_understanding_half_not_the_whole_brief():
    """The slowest of three calls held the first check for 5-7 s on Haiku."""
    assert dict(live_captions.PROGRESS_STEPS)["contextualized"] == "brief_understood"
    key = "f" * 64
    live_captions.open_track(key)
    marks = EpisodeMarks()
    live_captions.attach_marks(key, marks)
    marks.mark("brief_start")
    assert not live_captions.read_progress(key)["steps"]["contextualized"]
    marks.mark("brief_understood")
    steps = live_captions.read_progress(key)["steps"]
    assert steps["contextualized"] and not steps["retrieved"]


class _Notes:
    def __init__(self):
        self.marks = EpisodeMarks()


async def _answer(kind):
    return kind, {}, ""


def test_the_understanding_half_marks_the_clock_the_moment_it_answers():
    notes = _Notes()
    asyncio.run(ei._timed("search", "search", _answer("ok"), notes))
    assert "brief_understood" not in notes.marks.events
    asyncio.run(ei._timed("understand", "search", _answer("timeout"), notes))
    assert "brief_understood" not in notes.marks.events, "a failed half understood nothing"
    asyncio.run(ei._timed("understand", "search", _answer("ok"), notes))
    assert "brief_understood" in notes.marks.events
    # Without a clock (prefetch, the editions' own notes) it is a no-op.
    asyncio.run(ei._timed("whole", "ahead", _answer("ok"), None))


def test_the_chevrons_are_layers_not_svg_paths():
    """An SVG child's animation runs on the main thread and stalled."""
    mark = INDEX.split('<div class="fam-loading-mark"', 1)[1].split("</div>", 1)[0]
    assert mark.count('<span class="chev c') == 3
    assert "<path class=" not in mark
    rule = INDEX.split(".fam-loading-mark .chev{", 1)[1].split("}", 1)[0]
    assert "filter" not in rule, "the shadow is redrawn every frame"
    assert "will-change:transform, opacity" in rule


# --- 2. an episode sent in a message is the episode that was sent ----------

def test_the_store_opens_twice_on_either_backend(tmp_path):
    """The widening runs on every open; the second finds the column there."""
    path = str(tmp_path / "again.db")
    messages_mod.MessageStore(path)
    store = messages_mod.MessageStore(path)
    ep = "d" * 64 + ".1700000001"
    store.send("x", "y", kind="episode", query="q", minutes=2, episode=ep)
    assert store.thread("y", "x")[-1].episode == ep


@pytest.mark.sqlite_file
def test_a_message_keeps_its_episode_and_an_old_table_is_widened(tmp_path):
    path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, thread TEXT NOT NULL,
        sender TEXT NOT NULL, recipient TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'text', text TEXT NOT NULL DEFAULT '',
        query TEXT NOT NULL DEFAULT '', minutes INTEGER NOT NULL DEFAULT 0,
        title TEXT NOT NULL DEFAULT '', at REAL NOT NULL)""")
    conn.execute("INSERT INTO messages (thread, sender, recipient, kind, query,"
                 " minutes, at) VALUES ('a|b', 'a', 'b', 'episode', 'old', 2, 1)")
    conn.commit()
    conn.close()
    store = messages_mod.MessageStore(path)
    ep = "e" * 64 + ".1700000000"
    store.send("a", "b", kind="episode", query="new", minutes=2, episode=ep)
    store.send("a", "b", kind="text", text="hi", episode=ep)
    rows = store.thread("b", "a")
    assert [m.episode for m in rows] == ["", ep, ""], "a text carries no episode"
    assert rows[1].as_dict("b")["episode"] == ep
    assert store.inbox("b")[0]["last"]["episode"] == ""
    assert sorted(store.episodes()) == [("new", 2, ep), ("old", 2, "")]


def _signed_in(client, email, name, handle):
    client.post("/api/auth/signup", json={"email": email, "password": "password12"})
    client.post("/api/me", json={"name": name, "handle": handle})
    return client.get("/api/auth/me").json()["user_id"]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


def test_a_share_names_the_kept_episode_and_never_another_question(client):
    query = "why do bonds move when rates rise"
    key = cache_key(query, 3, None, "", True)
    appmod.SCRIPT_CACHE.put(key, ["Bonds move."], 3600, query, "", 3,
                            bucket="", title="Bonds", origin="search",
                            sourced_at=time.time() - 60)
    heard = episode_id(key, appmod.SCRIPT_CACHE.sourced_at(key))
    other = "c" * 64
    appmod.SCRIPT_CACHE.put(other, ["Other."], 3600, "something else", "", 3,
                            bucket="", title="Other", origin="search")

    ana = _signed_in(client, "ana285@b.com", "Ana", "ana285")
    client.post("/api/auth/logout")
    _signed_in(client, "ben285@b.com", "Ben", "ben285")
    client.post("/api/friends/follow", json={"handle": "ana285"})

    # An older client sends no id: the server names what is kept.
    sent = client.post("/api/messages", json={"to": ana, "query": query, "minutes": 3})
    assert sent.json()["message"]["episode"] == heard
    # The sender's own id is kept when it is this question's.
    sent = client.post("/api/messages", json={"to": ana, "query": query,
                                              "minutes": 3, "episode": heard})
    assert sent.json()["message"]["episode"] == heard
    # Another question's id is never carried under this one.
    sent = client.post("/api/messages", json={
        "to": ana, "query": query, "minutes": 3,
        "episode": episode_id(other, appmod.SCRIPT_CACHE.sourced_at(other))})
    assert sent.json()["message"]["episode"] == heard
    # Nothing kept: the message plays by its question, as before.
    sent = client.post("/api/messages", json={"to": ana, "query": "never asked", "minutes": 3})
    assert sent.json()["message"]["episode"] == ""

    # Its audio is held past the week, like a shared link's.
    assert key in appmod._held_episode_keys()


def test_listen_replays_the_sent_episode_and_asks_again_only_if_it_is_gone():
    body = INDEX.split("function playSharedEpisode(i){", 1)[1].split("\n  }", 1)[0]
    assert 'episode: m.episode || ""' in body and "askIfGone: !!m.episode" in body
    failed = INDEX.split("function onGenerationFailed(message, err){", 1)[1][:600]
    assert "err.status === 409" in failed and "gone.askIfGone" in failed
    send = INDEX.split("function sendEpisodeToThread(ep){", 1)[1].split("\n  }", 1)[0]
    assert 'episode: ep.episode || ""' in send
    current = INDEX.split("function currentEpisode(){", 1)[1].split("\n  }", 1)[0]
    assert "FamAudio.episode()" in current


# --- 3 and 4. a story, told, with no word about its material ---------------

import script_generator as sg  # noqa: E402


@pytest.mark.parametrize("sentence", [
    "This is not a part of the information I received.",
    "The information I received doesn't say how many.",
    "My sources don't say when the count ends.",
    "That wasn't provided to me.",
    "Based on what I have, the vote is on Sunday.",
    "The documents I have show the ship ran aground.",
])
def test_talk_of_the_material_is_caught(sentence):
    assert sg.is_material_sentence(sentence)


@pytest.mark.parametrize("sentence", [
    "Reuters reported the real rose four percent.",
    "Sources close to the team say he will play.",
    "The evidence suggests the drug works.",
    "Part of the deal is not public.",
    'The coach said "that is not part of what I was given" and laughed.',
    "Investors were given the information on Monday.",
    # The review's (§288): wire and court phrasing, never the writer's own.
    "The source did not say when the deal would close.",
    "The sources do not show a link, investigators said.",
    "The video was not part of the evidence presented to the jury.",
    "Officials put the damage at two billion dollars, based on the "
    "information available so far.",
    "The figures appear in the materials provided to investors.",
    "That was not part of the information released by police.",
])
def test_reporting_is_not_talk_of_the_material(sentence):
    assert not sg.is_material_sentence(sentence)


def test_the_material_check_drops_it_anywhere_and_says_so():
    """The opening check stops after six sentences; this one never does."""
    guard = sg.MaterialGuard()
    body = [f"Fact number {i} is that the price rose." for i in range(8)]
    assert all(guard.allow(s) for s in body)
    assert not guard.allow("That is not part of the information I received.")
    assert guard.dropped == ["That is not part of the information I received."]


def test_the_writer_is_told_to_tell_it_and_never_mention_its_material():
    prompt = sg.SYSTEM_PROMPT
    assert "or mention your material at all" in prompt
    assert "information I received" in prompt.replace("\n", " ")
    assert "source by source, are a list" in prompt.replace(" \\\n", " ").replace("\n", " ")
    # And the evidence block says it where the passages are.
    import dataclasses
    plan = dataclasses.replace(sg.plan_episode("sugar prices", 2),
                               evidence="[1] something")
    block = sg.build_prompt(plan)
    assert "never one source after another" in block
    assert "the information I received" in block


def test_there_is_a_news_example_beside_the_explainer():
    """Ours was dropped in the merge for §287's own Suez example."""
    queries = [q for q, _ in sg.STYLE_EXAMPLES]
    assert "why does the suez canal matter so much" in queries
    assert len(queries) >= 2


# --- 5. the pause inside the first line ------------------------------------

from spoken_text import speakable  # noqa: E402
import pronunciation  # noqa: E402


def test_a_code_is_spelled_for_the_voice_and_kept_in_the_captions():
    assert speakable("A protein called ZBTB44 acts as a brake.") == \
        "A protein called Z B T B forty-four acts as a brake."
    assert speakable("CD4 cells") == "C D four cells"
    # One letter and a number reads as it always did.
    assert speakable("Q3 and the G7") == "Q three and the G seven"
    # Letters said as a word stay a word (review, §288).
    assert speakable("COP29 opened in Baku") == "COP29 opened in Baku"
    assert speakable("COVID19") == "COVID19"


def test_a_say_line_may_name_a_code():
    assert pronunciation._CODE.match("ZBTB44")
    assert pronunciation.clean("ZBTB44", "zee-bee-tee-bee forty-four") is not None
    assert pronunciation.clean("Area 51", "AIR-ee-uh") is None


def _sentences(text):
    out = []
    while True:
        match = sg.find_sentence_end(text)
        if not match:
            return out, text
        out.append(text[:match.end()].strip())
        text = text[match.end():]


def test_an_abbreviation_does_not_end_a_sentence():
    said, held = _sentences("Dr. Patel said it on Oct. 14 in Nature. The U.S. "
                            "economy grew. It came from the U.S. Then it fell. ")
    assert said == ["Dr. Patel said it on Oct. 14 in Nature.",
                    "The U.S. economy grew.", "It came from the U.S.",
                    "Then it fell."]
    assert held == ""
    # What decides it has not arrived: wait for the next word.
    assert _sentences("He went to the U.S. ") == ([], "He went to the U.S. ")


# --- 3a. a Trending episode's sources reach the player ----------------------

def test_an_empty_live_track_does_not_hide_the_kept_sources():
    import provenance as provenance_mod
    from pipeline import PodcastPipeline
    from script_generator import plan_episode
    from tts import DebugEngine
    from cache import MemoryScriptCache
    from test_pipeline import FakeGenerator

    cache = MemoryScriptCache()
    pipe = PodcastPipeline(FakeGenerator(["One."]), DebugEngine(), cache=cache)
    plan = plan_episode("is brazil's election moving sugar prices", 2)
    key = asyncio.run(pipe._cache_key(plan))
    raw = ('{"items": [{"label": "Reuters", "title": "Sugar rallies", '
           '"url": "https://www.reuters.com/x"}], "retrievers": []}')
    cache.put(key, ["One."], 3600, plan.query, "", 2, sources=raw)
    live_captions.open_track(key)
    live_captions.mark_cached(key)           # a replay, as the pipeline marks it
    live_captions.publish_sources(key, '{"items": []}')
    found = provenance_mod.Provenance.from_json(asyncio.run(pipe.sources_for(plan)))
    assert found.items, "the replay's empty track hid the kept sources"


def test_sources_and_transcript_read_the_key_the_audio_was_written_under():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    for route in ('@app.get("/api/sources")', '@app.get("/api/transcript")'):
        head = src.split(route, 1)[1].split('"""', 1)[0]
        assert "search: bool | None = Query(None)" in head, route


def test_the_player_asks_for_sources_until_it_has_some():
    assert "askSourcesUntilShown(episodeStamp, 0);" in INDEX
    body = INDEX.split("function askSourcesUntilShown(stamp, tries){", 1)[1][:400]
    assert "stamp !== episodeStamp || sourcesNow.length" in body


def test_a_rewrite_never_shows_the_sources_of_the_episode_it_replaces():
    """Review, §288: while a key is written again the cache holds the old
    episode, and its sources are not this one's."""
    from pipeline import PodcastPipeline
    from script_generator import plan_episode
    from tts import DebugEngine
    from cache import MemoryScriptCache
    from test_pipeline import FakeGenerator

    cache = MemoryScriptCache()
    pipe = PodcastPipeline(FakeGenerator(["One."]), DebugEngine(), cache=cache)
    plan = plan_episode("what did the senate pass tonight", 2)
    key = asyncio.run(pipe._cache_key(plan))
    old = ('{"items": [{"label": "AP", "title": "Last week", '
           '"url": "https://apnews.com/x"}], "retrievers": []}')
    cache.put(key, ["Old."], 3600, plan.query, "", 2, sources=old)
    live_captions.open_track(key)            # being written again
    assert asyncio.run(pipe.sources_for(plan)) == ""
    live_captions.close(key)                 # finished: the cache is this one
    assert "Last week" in asyncio.run(pipe.sources_for(plan))


def test_one_sources_chain_at_a_time_and_none_while_paused():
    body = INDEX.split("function askSourcesUntilShown(stamp, tries){", 1)[1][:700]
    assert "clearTimeout(sourcesRetryTimer)" in body
    assert "if(!isPlaying)" in body
