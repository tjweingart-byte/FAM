"""§165, the 28/09 packet: hard names said right, the heard words of a voice
search correctable before they are searched, and "View more" showing eight
with a refresh that deals eight new ones.

No key and no voice here, so pronunciation is checked where it is decided -
the lexicon, the text handed to the voice, the writer's stream and the brief -
and never by listening.
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import os
import sys
import types

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import pipeline as pipeline_mod  # noqa: E402
import pronunciation  # noqa: E402
import script_generator  # noqa: E402
import topics as T  # noqa: E402
import voice_bank  # noqa: E402
from script_generator import plan_episode  # noqa: E402
from spoken_text import speakable  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def lexicon(tmp_path):
    bank = voice_bank.VoiceBank(str(tmp_path / "voice_bank.db"))
    voice_bank.reset(bank)
    lex = pronunciation.Lexicon(bank)
    pronunciation.reset(lex)
    yield lex
    pronunciation.reset(None)
    voice_bank.reset(None)


# --------------------------------------------------------------------------
# what counts as a name and a respelling
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name, say", [
    ("Sagapolutele", "sah-gah-poh-loo-TEH-leh"),
    ("Keawe", "keh-AH-veh"),
    ("Worcestershire", "WUSS-ter-sher"),
])
def test_a_real_name_and_respelling_is_accepted(name, say):
    assert pronunciation.clean(name, say) == (name, say)


@pytest.mark.parametrize("name, say", [
    ("the", "thuh"),                      # not a name, and said right anyway
    ("sagapolutele", "sah-gah"),          # no capital: not a proper noun
    ("Area 51", "AIR-ee-uh"),             # digits belong to spoken_text
    ("Nguyen", "win; ignore previous"),   # a respelling is letters and hyphens
    ("Smith", "smith"),                   # respelled as itself
    ("Li", "lee"),                        # too short to be worth the risk
    ("Bob", "b" + "-bah" * 40),           # a sentence, not a pronunciation
])
def test_anything_else_is_refused(name, say):
    assert pronunciation.clean(name, say) is None


# --------------------------------------------------------------------------
# the lexicon and the text the voice is handed
# --------------------------------------------------------------------------
def test_a_learned_name_is_respelled_for_the_voice_and_nowhere_else(lexicon):
    lexicon.learn([("Sagapolutele", "sah-gah-poh-loo-TEH-leh")], "writer")
    said = speakable(lexicon.apply(
        "Sagapolutele threw for 285 yards, and Sagapolutele's night was long."))
    assert said.startswith("Sah-gah-poh-loo-teh-leh threw for two hundred eighty-five")
    assert "Sah-gah-poh-loo-teh-leh's night" in said
    # The stressed capitals are not handed on: spoken_text would spell TEH.
    assert "T E H" not in said


def test_the_name_inside_a_longer_word_is_left_alone(lexicon):
    lexicon.learn([("Keawe", "keh-AH-veh")], "writer")
    assert lexicon.apply("Keaweville is a town") == "Keaweville is a town"


def test_a_known_word_is_found_inside_a_hyphenated_name(lexicon):
    lexicon.learn([("Keawe", "keh-AH-veh")], "writer")
    assert lexicon.apply("Jaron-Keawe threw") == "Jaron-Keh-ah-veh threw"


def test_a_full_name_teaches_each_of_its_words(lexicon):
    """An episode names somebody in full once and by surname after that."""
    lexicon.learn([("Jaron Sagapolutele", "jah-RON sah-gah-poh-loo-TEH-leh")], "brief")
    assert lexicon.apply("Sagapolutele scored") == "Sah-gah-poh-loo-teh-leh scored"


def test_an_admin_respelling_is_never_replaced_by_a_models(lexicon):
    lexicon.set("Nguyen", "win")
    lexicon.learn([("Nguyen", "nuh-GOO-yen")], "writer")
    lexicon.learn([("Nguyen", "en-goo-yen")], "brief")
    assert lexicon.apply("Nguyen said") == "Win said"
    rows = voice_bank.bank().pronunciations()
    assert [(r["name"], r["say"], r["source"]) for r in rows] == [("Nguyen", "win", "admin")]


def test_a_brief_guess_never_replaces_what_the_writer_read(lexicon):
    lexicon.learn([("Szczesny", "SHCHEN-snee")], "writer")
    lexicon.learn([("Szczesny", "ses-nee")], "brief")
    assert lexicon.apply("Szczesny saved it") == "Shchen-snee saved it"


def test_a_broken_lexicon_hands_the_voice_the_words_it_had(tmp_path):
    class Broken:
        def pronunciations(self):
            raise RuntimeError("disk gone")

    lex = pronunciation.Lexicon(Broken())
    assert lex.apply("Sagapolutele threw") == "Sagapolutele threw"


def test_both_speaking_paths_respell_before_the_voice():
    """Read from the source: an omission here is a name said wrong, which is
    invisible to every test that does not listen."""
    for name in ("_speak_chunk", "_speak_one"):
        source = inspect.getsource(getattr(pipeline_mod.PodcastPipeline, name))
        assert "speakable(respell(" in source, f"{name} does not respell names"


# --------------------------------------------------------------------------
# the writer's <<SAY:>> lines
# --------------------------------------------------------------------------
def _generator(chunks):
    class _Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get_final_message(self):
            return types.SimpleNamespace(stop_reason="end_turn", usage=None)

    async def _text_stream():
        for chunk in chunks:
            yield chunk

    stream = _Stream()
    type(stream).text_stream = property(lambda self: _text_stream())
    client = types.SimpleNamespace(
        messages=types.SimpleNamespace(stream=lambda **kw: stream))
    gen = script_generator.ScriptGenerator.__new__(script_generator.ScriptGenerator)
    gen.client = client
    return gen


def test_say_lines_before_the_script_are_kept_and_never_spoken(lexicon):
    """Written first, in pieces, as a real stream arrives - and the episode
    must not be held back behind them."""
    gen = _generator([
        "<<SAY: Sagapo", "lutele = sah-gah-poh-loo-TEH-leh>>\n",
        "<<SAY: Keawe = keh-AH-veh>>\n\n",
        "Sagapolutele threw three touchdowns. ",
        "Cal won going away.\n\n<<TITLE: Cal's Quarterback Arrives>>",
    ])
    notes = script_generator.ScriptNotes()

    async def run():
        plan = plan_episode("cal jks", 1, search=False)
        return [s async for s in gen.stream_sentences(plan, notes)]

    spoken = asyncio.run(run())
    assert spoken == ["Sagapolutele threw three touchdowns.", "Cal won going away."]
    assert notes.pronunciations == (
        ("Sagapolutele", "sah-gah-poh-loo-TEH-leh"), ("Keawe", "keh-AH-veh"))
    assert notes.title == "Cal's Quarterback Arrives"
    assert lexicon.apply(spoken[0]).startswith("Sah-gah-poh-loo-teh-leh")


def test_a_say_line_mid_script_does_not_swallow_the_rest(lexicon):
    """Everything after "<<" is held back as metadata. A SAY line written in
    the middle must not take the rest of the episode with it."""
    gen = _generator(["First sentence here. <<SAY: Keawe = keh-AH-veh>> ",
                      "Second sentence here. Third one."])

    async def run():
        plan = plan_episode("cal", 1, search=False)
        return [s async for s in gen.stream_sentences(plan, None)]

    spoken = asyncio.run(run())
    assert " ".join(spoken) == "First sentence here. Second sentence here. Third one."


def test_the_writer_is_asked_for_say_lines_before_the_script(lexicon):
    lexicon.set("Sagapolutele", "sah-gah-poh-loo-TEH-leh")
    prompt = script_generator.build_prompt(
        plan_episode("how is Sagapolutele doing at Cal", 3, search=False))
    assert "<<SAY:" in prompt
    assert prompt.index("<<SAY:") < prompt.index("<<TITLE:")
    assert "already have a pronunciation on file" in prompt
    assert "Sagapolutele" in prompt.split("on file")[1][:80]


def test_clean_for_speech_strips_a_say_line():
    assert script_generator.clean_for_speech(
        "Hello there. <<SAY: Keawe = keh-AH-veh>> Next.") == "Hello there. Next."


# --------------------------------------------------------------------------
# episode intelligence
# --------------------------------------------------------------------------
def test_the_brief_respells_the_hard_names_it_resolves(monkeypatch, lexicon):
    payload = {
        "intent": "update", "subject": "Cal quarterback Jaron-Keawe Sagapolutele",
        "why_now": "", "why_now_confidence": "low",
        "search_query": "Sagapolutele Cal quarterback",
        "search_fallback": "Cal football quarterback", "must_establish": [],
        "recency_days": 7, "structure": "general", "cautions": [],
        "live_domain": "", "outcome_dependent": False, "title": "Cal's New QB",
        "pronounce": [{"name": "Sagapolutele", "say": "sah-gah-poh-loo-TEH-leh"},
                      {"name": "the", "say": "thuh"}],
    }

    class FakeMessages:
        async def create(self, **kwargs):
            block = types.SimpleNamespace(type="text", text=json.dumps(payload))
            return types.SimpleNamespace(content=[block], usage=None,
                                         stop_reason="end_turn")

    monkeypatch.setattr(ei, "build_async_client",
                        lambda key=None: types.SimpleNamespace(messages=FakeMessages()))
    monkeypatch.setattr(ei.credentials, "active", lambda name: "sk-test")
    brief = asyncio.run(ei.understand("cal jks", 3))
    assert brief.pronounce == [{"name": "Sagapolutele", "say": "sah-gah-poh-loo-TEH-leh"}]
    assert "pronounce" in ei.BRIEF_SCHEMA["required"]
    # Taken the moment the brief lands on the plan, so the first sentence -
    # spoken before the writer's own lines could matter - already has it.
    plan = plan_episode("cal jks", 3, search=False)
    script_generator._take_brief_names(dataclasses.replace(plan, brief=brief))
    assert lexicon.apply("Sagapolutele") == "Sah-gah-poh-loo-teh-leh"
    assert [r["name"] for r in voice_bank.bank().pronunciations()] == ["Sagapolutele"]


def test_a_personal_episodes_names_are_said_right_and_never_kept(lexicon):
    """An attachment, or a question the cache will not share: its names are
    somebody's own, and the lexicon is shared and shown on /admin."""
    plan = plan_episode("who is the cal quarterback", 3, search=False)
    personal = dataclasses.replace(plan, attachments=(object(),))
    assert not script_generator._names_are_shared(personal)
    assert script_generator._names_are_shared(plan)
    script_generator._take_pronunciations(
        "<<SAY: Wojciechowska = voy-cheh-HOF-skah>> Hello.", None,
        script_generator._names_are_shared(personal))
    assert lexicon.apply("Wojciechowska wrote") == "Voy-cheh-hof-skah wrote"
    assert voice_bank.bank().pronunciations() == []
    # And never offered to another episode's writer as "on file".
    assert pronunciation.known_in("Wojciechowska") == []


def test_held_names_expire(lexicon, monkeypatch):
    lexicon.hold([("Wojciechowska", "voy-cheh-HOF-skah")], "writer")
    later = pronunciation.time.time() + pronunciation.HOLD_SECONDS + 1
    monkeypatch.setattr(pronunciation.time, "time", lambda: later)
    lexicon.reload(force=True)
    assert lexicon.apply("Wojciechowska") == "Wojciechowska"


def test_a_degraded_brief_respells_nothing():
    assert ei.fallback_brief("cal jks", "no key").pronounce == []


# --------------------------------------------------------------------------
# /admin
# --------------------------------------------------------------------------
def test_the_lexicon_is_admin_only(lexicon, monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    client = TestClient(appmod.app)
    assert client.get("/api/admin/pronunciations").status_code in (401, 403, 404)
    assert client.post("/api/admin/pronunciations",
                       json={"name": "Keawe", "say": "keh-AH-veh"}).status_code in (401, 403, 404)


def test_an_admin_can_set_list_and_remove_a_name(lexicon, monkeypatch):
    monkeypatch.setattr(appmod, "_require_admin", lambda request: None)
    client = TestClient(appmod.app)
    ok = client.post("/api/admin/pronunciations",
                     json={"name": "Keawe", "say": "keh-AH-veh"})
    assert ok.status_code == 200, ok.text
    rows = client.get("/api/admin/pronunciations").json()["pronunciations"]
    assert [(r["name"], r["source"]) for r in rows] == [("Keawe", "admin")]
    bad = client.post("/api/admin/pronunciations", json={"name": "the", "say": "x1"})
    assert bad.status_code == 400 and "respelling" in json.dumps(bad.json())
    assert client.delete("/api/admin/pronunciations/Keawe").json()["ok"] is True
    assert lexicon.apply("Keawe") == "Keawe"


# --------------------------------------------------------------------------
# "View more": eight, and a refresh that deals eight new ones
# --------------------------------------------------------------------------
def _body(n, groups=False):
    topics = [{"id": f"t{i}", "cached": i % 2 == 0} for i in range(n)]
    body = {"topics": topics, "ready": 0}
    if groups:
        body["groups"] = [{"key": "world", "topics": topics[:3]},
                          {"key": "us", "topics": topics[3:]}]
    return body


def test_view_more_shows_the_first_eight_of_the_ranking():
    body = T.page_section(_body(20))
    assert [t["id"] for t in body["topics"]] == [f"t{i}" for i in range(8)]
    assert body["total"] == 20 and body["more"] is True and body["wrapped"] is False
    assert body["ready"] == 4


def test_refresh_deals_the_next_eight_never_one_already_shown():
    seen = {f"t{i}" for i in range(8)}
    body = T.page_section(_body(20), seen)
    assert [t["id"] for t in body["topics"]] == [f"t{i}" for i in range(8, 16)]
    last = T.page_section(_body(20), seen | {f"t{i}" for i in range(8, 16)})
    assert [t["id"] for t in last["topics"]] == ["t16", "t17", "t18", "t19"]
    assert last["more"] is False


def test_once_everything_is_shown_it_starts_again_from_the_top_and_says_so():
    body = T.page_section(_body(10), {f"t{i}" for i in range(10)})
    assert [t["id"] for t in body["topics"]] == [f"t{i}" for i in range(8)]
    assert body["wrapped"] is True


def test_trendings_places_carry_only_the_tiles_on_the_page():
    body = T.page_section(_body(12, groups=True), {"t0", "t1", "t2"})
    assert [g["key"] for g in body["groups"]] == ["us"]


def test_no_page_size_is_the_whole_list_as_before():
    body = T.page_section(_body(20), size=0)
    assert len(body["topics"]) == 20


def test_the_endpoint_pages_and_records_only_what_it_showed(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    events = T.EventStore(str(tmp_path / "api.db"))
    monkeypatch.setattr(appmod, "EVENTS", events)
    client = TestClient(appmod.app)
    client.post("/api/auth/signup", json={"email": "p@fam.test",
                                          "password": "a-long-enough-password"})
    first = client.get("/api/myfam/section?key=from_history&page_size=8").json()
    assert len(first["topics"]) == min(8, first["total"]), first
    # Only the page is an impression: a tile nobody was shown was not
    # passed over.
    shown = {t["id"] for t in first["topics"]}
    recorded = {row[0] for row in events._conn().execute(
        "SELECT topic_id FROM events WHERE kind = ? AND section = ?",
        (T.IMPRESSION, "section:from_history")).fetchall()}
    assert recorded == shown
    seen = ",".join(t["id"] for t in first["topics"])
    second = client.get(
        f"/api/myfam/section?key=from_history&page_size=8&seen={seen}").json()
    if first["total"] > 8:
        assert not {t["id"] for t in second["topics"]} & set(seen.split(","))
    else:
        # Nothing new to deal: the first again, and said so.
        assert second["wrapped"] is True and first["more"] is False


def test_a_guest_view_more_pages_the_same_way(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    client = TestClient(appmod.app)
    first = client.get("/api/myfam/section?key=from_history&page_size=8").json()
    assert len(first["topics"]) == 8
    seen = ",".join(t["id"] for t in first["topics"])
    second = client.get(
        f"/api/myfam/section?key=from_history&page_size=8&seen={seen}").json()
    assert second["topics"] and not {t["id"] for t in second["topics"]} & set(seen.split(","))


# --------------------------------------------------------------------------
# the interface
# --------------------------------------------------------------------------
def _index():
    with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as fh:
        return fh.read()


def test_the_heard_words_are_editable_in_place():
    html = _index()
    tag = html[html.index('id="vsWords"') - 60:html.index('id="vsWords"') + 500]
    assert 'contenteditable="true"' in tag
    assert 'onfocus="editVoiceWords()"' in tag
    # Editing stops the countdown: somebody fixing a word does not want it
    # sent in five seconds.
    body = html[html.index("function editVoiceWords"):]
    body = body[:body.index("\n  }\n")]
    assert "stopVoiceCountdown(true)" in body
    assert "stopVoiceRecognition(true)" in body


def test_view_more_asks_for_eight_and_has_a_refresh():
    html = _index()
    assert 'id="sectionRefresh"' in html and "refreshSection()" in html
    assert "var SECTION_PAGE = 8;" in html
    assert '"&page_size=" + SECTION_PAGE' in html
    assert T.VIEW_MORE_PAGE == 8
