"""§240: an episode with little to go on says so, and nobody else is served it.

The owner's direction: "no company called General Atomics" sat on DailyFAM
for days - an episode that had found little, reached for a connection, and
concluded from absence that a real company did not exist. An episode on a thin
subject now says what it has, then says plainly that this is all it found; and
it is kept for the one listener who heard it, never for anybody else.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import types

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import script_generator as sg  # noqa: E402
import trending_bank  # noqa: E402
from cache import ARCHIVE_ORIGIN, SqliteScriptCache, archive_key, parse_episode_id  # noqa: E402
from pipeline import PodcastPipeline  # noqa: E402
from tts import DebugEngine  # noqa: E402

from test_pipeline import FakeGenerator  # noqa: E402


def _plan(**kw):
    plan = sg.plan_episode("general atomics", 2, search=True)
    return dataclasses.replace(plan, **kw)


# --- deciding it in code ---------------------------------------------------

def test_no_evidence_at_all_is_limited():
    assert sg.limited_material(_plan(evidence=""))


def test_evidence_that_missed_everything_the_brief_needed_is_limited():
    brief = types.SimpleNamespace(must_establish=["who owns it", "what it makes"])
    assert sg.limited_material(_plan(evidence="x", brief=brief,
                                     thin_on=("who owns it", "what it makes")))
    # Half is still an episode: the word test is coarse, so only "none" counts.
    assert not sg.limited_material(_plan(evidence="x", brief=brief,
                                         thin_on=("who owns it",)))


def test_never_on_an_attachment_a_town_or_a_live_answer():
    assert not sg.limited_material(_plan(evidence="", attachments=("a",)))
    assert not sg.limited_material(_plan(evidence="", local=object()))
    live = types.SimpleNamespace(facts=object())
    assert not sg.limited_material(_plan(evidence="", live=live))
    assert not sg.limited_material(_plan(evidence="", search=False))


def test_the_writer_is_told_to_say_what_it_has_then_that_it_is_all():
    told = sg.build_prompt(_plan(evidence="", limited=True))
    assert "this is all you could find" in told and "does not exist" in told
    assert "very little on what they asked" in told
    # Every episode may judge its own material thin, and mark it.
    plain = sg.build_prompt(_plan(evidence="x"))
    assert "very little on what they asked" not in plain
    assert "<<LIMITED>>" in plain and "honest beats full" in plain
    # And never infers absence, in the cacheable block every episode reads.
    assert "never means a thing does not exist" in sg.SYSTEM_PROMPT


# --- the writer's own marker -----------------------------------------------

def test_the_limited_marker_is_read_and_never_spoken():
    notes = sg.ScriptNotes()
    reader = sg._ScriptReader(_plan(), notes)
    text = ("General Atomics builds drones in San Diego. That is about all the "
            "reporting on them we could find.\n<<TITLE: General Atomics>>\n<<LIMITED>>")
    spoken = reader.feed(text) + reader.finish()
    assert notes.limited
    assert not any("LIMITED" in s or "<<" in s for s in spoken)
    assert sg.clean_for_speech("Done. <<LIMITED>>") == "Done."


# --- kept for its listener, served to nobody else --------------------------

class Thin(FakeGenerator):
    def __init__(self):
        super().__init__(ratio=1.0)
        self.calls = 0

    async def stream_sentences(self, plan, notes=None):
        self.calls += 1
        if notes is not None:
            notes.limited = True
        async for s in super().stream_sentences(plan, notes):
            yield s


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "DEMO_MODE", False)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", SqliteScriptCache(str(tmp_path / "e.db")))
    gen = Thin()
    monkeypatch.setattr(
        appmod, "_make_pipeline",
        lambda voice=None, author="": PodcastPipeline(
            generator=gen, engine=DebugEngine(), cache=appmod.SCRIPT_CACHE, voice=voice))
    with TestClient(appmod.app) as c:
        c.gen = gen
        yield c


def test_a_limited_episode_replays_for_its_listener_and_is_written_again_for_others(client):
    q = "general atomics"
    first = client.get(f"/api/audio?q={q}&minutes=1&fmt=pcm&surface=search")
    assert first.status_code == 200
    heard = first.headers["X-FAM-Episode"]
    key, sourced = parse_episode_id(heard)
    cache = appmod.SCRIPT_CACHE

    # Nothing under the shared key: no hit, no near match, no rail, no Explore.
    assert cache.get(key) is None and cache.get(key, current=False) is None
    assert cache.origin_of(archive_key(key, sourced)) == ARCHIVE_ORIGIN
    assert not [r for r in cache.recent(limit=50) if r.get("query") == q]

    # Its own listener's history still plays exactly what they heard.
    assert cache.resolve_episode(heard) == archive_key(key, sourced)
    replay = client.get(f"/api/audio?q={q}&minutes=1&fmt=pcm&surface=search"
                        f"&episode={heard}")
    assert replay.status_code == 200
    assert client.gen.calls == 1

    # The next person to ask gets a fresh search, not this one.
    client.get(f"/api/audio?q={q}&minutes=1&fmt=pcm&surface=search")
    assert client.gen.calls == 2


# --- the writers that fill shared rails ahead of time ----------------------

def test_trending_never_writes_or_offers_a_limited_story():
    notes = sg.ScriptNotes(limited=True)
    result = trending_bank._finish(types.SimpleNamespace(query="q"), None, "k",
                                   notes, ["One sentence."], None, 2, 0.0, "trending")
    assert result["status"] == trending_bank.LIMITED and not result["key"]


def test_a_limited_trending_story_is_left_off_the_rail(monkeypatch):
    class Story:
        def __init__(self, sid):
            self.id = sid

        def expired(self, now):
            return False

    edition = trending_bank.Edition(
        slot="s", built_at=0.0, source="gnews",
        stories=[Story("a"), Story("b")],
        episodes={"a": {"status": "written"}, "b": {"status": trending_bank.LIMITED}})
    monkeypatch.setattr(trending_bank, "current", lambda now=None: edition)
    assert [s.id for s in trending_bank.stories_now(1.0)] == ["a"]


# --- the memory a restart can be explained by ------------------------------

def test_health_reports_memory(client):
    memory = client.get("/api/health").json()["memory"]
    assert set(memory) == {"rss_mb", "peak_mb"}
    assert memory["peak_mb"] is None or memory["peak_mb"] > 0
