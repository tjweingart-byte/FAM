"""Checking every word, not only names Wikipedia knows (PROBLEMS.md §325).

1. The dictionary: words outside the CMU Pronouncing Dictionary and the
   lexicon, found in written episodes, most used first.
2. The round trip: the production voice says a word, a phoneme recogniser on
   the same card hears it, and the sounds are compared with what was meant.
"""
from __future__ import annotations

import asyncio
import base64
import types

import pytest
from fastapi.testclient import TestClient

import app as appmod
import hard_words
import pronunciation
import remote_voice
import voice_bank
import voice_check
from remote_voice import RemoteChatterboxEngine

ADMIN = {"X-Admin-Token": "secret"}
PCM = b"\x01\x00" * 2400


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "secret")
    return TestClient(appmod.app)


@pytest.fixture
def lexicon(tmp_path):
    bank = voice_bank.VoiceBank(str(tmp_path / "voice_bank.db"))
    voice_bank.reset(bank)
    pronunciation.reset(pronunciation.Lexicon(bank))
    yield bank
    pronunciation.reset(None)
    voice_bank.reset(None)


# --- 1. the dictionary -------------------------------------------------------

def test_the_dictionary_knows_english_and_its_plain_forms():
    assert hard_words.available()
    for word in ("banana", "Eagles", "kickoffs", "re-signed", "running", "Chiefs"):
        assert hard_words.known(word), word
    for word in ("Sagapolutele", "Antetokounmpo", "Dooku"):
        assert not hard_words.known(word), word


def test_the_dictionary_says_how_a_word_should_sound():
    assert hard_words.arpabet_to_ipa("K AH0 R IY1 M") == "kəˈɹiːm"
    assert hard_words.expected("banana") == "buh-NA-nuh"
    assert hard_words.expected("Antetokounmpo") == ""


def test_a_word_at_risk_is_unknown_unrespelled_and_not_an_initialism():
    text = ("Giannis Antetokounmpo scored as the Bucks beat the Celtics; "
            "Sagapolutele and the NFL watched Mahomes's throw.")
    found = hard_words.at_risk(text, ["Jaron Sagapolutele"])
    assert found == ["Antetokounmpo", "Mahomes"]


class FakeCache:
    def __init__(self, episodes):
        self.episodes = episodes

    def recent(self, limit=40, **_):
        return [{"key": k, "title": t, "query": t} for k, t, _ in self.episodes][:limit]

    def get(self, key, current=True):
        return next(s for k, _, s in self.episodes if k == key)


def test_a_scan_counts_words_across_episodes_and_skips_what_is_settled(lexicon):
    lexicon.learn_pronunciation("Antetokounmpo", "ahn-teh-toh-KOON-boh", "admin")
    lexicon.hard_word_ok("Brunson")
    cache = FakeCache([
        ("a", "Bucks win", ["Antetokounmpo had 40.", "Mahomes watched Brunson."]),
        ("b", "Chiefs", ["Mahomes threw twice.", "Then Mahomes ran."]),
    ])
    got = hard_words.scan(cache)
    assert got["episodes"] == 2
    rows = lexicon.hard_words()
    assert [r["word"] for r in rows] == ["Mahomes"]
    assert rows[0]["count"] == 3 and rows[0]["episodes"] == 2
    assert rows[0]["example"] == "Mahomes watched Brunson." and rows[0]["title"] == "Bucks win"


# --- 2. the round trip ---------------------------------------------------------

def test_sounds_are_compared_by_class_not_by_spelling():
    assert voice_check.similarity("kuh-REEM", "kuhr-EEM") == 1.0
    assert voice_check.verdict(voice_check.similarity("KEE-vuh", "kivuh")) == voice_check.MATCHES
    assert voice_check.verdict(voice_check.similarity("DOO-koo", "tih-RA-nuhs")) == voice_check.DIFFERS
    assert voice_check.verdict(None) == voice_check.NO_EXPECTATION


def test_a_check_sends_what_an_episode_sends_and_compares(lexicon, monkeypatch):
    lexicon.learn_pronunciation("Sagapolutele", "sah-gah-poh-loo-TEH-leh", "admin")
    sent = []

    async def say_and_hear(text):
        sent.append(text)
        return {"pcm": PCM, "rate": 24000, "heard": "s ɑ ɡ ɑ p oʊ l uː t ɛ l ɛ",
                "heard_by": "stub"}

    monkeypatch.setattr(voice_check, "say_and_hear", say_and_hear)
    result = run(voice_check.check("Sagapolutele", with_audio=True))
    assert sent == ["Sah-gah-poh-loo-teh-leh"], "the respelling, as the voice gets it"
    assert result["expected"] == "sah-gah-poh-loo-TEH-leh"
    assert result["verdict"] == voice_check.MATCHES
    assert result["audio"].startswith("data:audio/wav;base64,")
    assert lexicon.voice_checks()[0]["word"] == "Sagapolutele"
    # A dictionary word is compared with the dictionary.
    result = run(voice_check.check("banana"))
    assert result["expected_from"] == "dictionary"


def test_nothing_is_heard_on_the_placeholder_tone(client, lexicon):
    got = client.post("/api/admin/pronunciations/hear", json={"name": "Dooku"},
                      headers=ADMIN)
    assert got.status_code == 409
    body = got.json()
    assert "placeholder tone" in (body.get("detail") or body.get("error") or "")


def test_the_worker_hears_what_it_said(monkeypatch):
    from voice_worker import hearing
    from voice_worker import synth as module

    class StubEngine:
        sample_rate = 24000

        async def synth(self, text, wpm=0.0, voice=None):
            return PCM

    monkeypatch.setattr(module, "preflight", lambda: (True, "stub"))
    monkeypatch.setattr(module, "ChatterboxEngine", StubEngine)
    monkeypatch.setattr(hearing, "available", lambda: (True, "stub"))
    monkeypatch.setattr(hearing, "recognise", lambda pcm, rate: "d uː k uː")
    reply = run(module.synthesise({"text": "Dooku.", "hear": True}))
    assert reply["heard"] == "d uː k uː" and base64.b64decode(reply["audio"]) == PCM
    # Not asked, not heard: an episode's chunk is unchanged.
    assert "heard" not in run(module.synthesise({"text": "Dooku."}))
    # A card that cannot hear still returns the audio, and says why.
    monkeypatch.setattr(hearing, "available", lambda: (False, "no transformers"))
    assert run(module.synthesise({"text": "Dooku.", "hear": True}))["heard_error"] == "no transformers"


def test_ctc_decoding_collapses_repeats_and_drops_the_blank():
    from voice_worker import hearing

    tokens = {0: "<pad>", 1: "d", 2: "uː", 3: "k"}
    assert hearing.decode([1, 1, 0, 2, 2, 0, 3, 0, 2], tokens, 0) == ["d", "uː", "k", "uː"]


def test_an_older_worker_is_named_not_guessed_at(monkeypatch):
    config = remote_voice.RemoteConfig(
        transport="http", url="https://w", api_key="", sample_rate=24000,
        timeout=1.0, connect_timeout=1.0, concurrency=1, voice="")
    monkeypatch.setattr(RemoteChatterboxEngine, "config", classmethod(lambda cls: config))

    async def endpoint(self, cfg):
        return types.SimpleNamespace(url="https://w", transport="http", token="")

    reply = {"audio": base64.b64encode(PCM).decode(), "sample_rate": 24000,
             "format": "pcm_s16le"}

    async def speak(self, payload, cfg, ep):
        assert payload["hear"] is True
        return dict(reply)

    monkeypatch.setattr(RemoteChatterboxEngine, "_endpoint", endpoint)
    monkeypatch.setattr(RemoteChatterboxEngine, "_speak", speak)
    with pytest.raises(remote_voice.RemoteVoiceError, match="Rebuild"):
        run(RemoteChatterboxEngine().hear("Dooku."))
    reply["heard"] = "d uː k uː"
    assert run(RemoteChatterboxEngine().hear("Dooku."))["heard"] == "d uː k uː"


def test_the_admin_page_lists_hard_words_and_can_hide_one(client, lexicon):
    lexicon.replace_hard_words([{"word": "Mahomes", "count": 3, "episodes": 2},
                                {"word": "Brunson", "count": 1, "episodes": 1}])
    lexicon.keep_voice_check({"word": "Mahomes", "heard_say": "muh-HOHMZ",
                              "verdict": "matches", "score": 0.9})
    got = client.get("/api/admin/hard-words", headers=ADMIN).json()
    assert [w["word"] for w in got["words"]] == ["Mahomes", "Brunson"]
    assert got["words"][0]["voice"]["verdict"] == "matches" and got["dictionary"]
    client.post("/api/admin/hard-words/ok", json={"name": "Brunson"}, headers=ADMIN)
    # Respelled since the scan: no longer the voice's guess.
    pronunciation.lexicon().set("Mahomes", "muh-HOHMZ")
    assert client.get("/api/admin/hard-words", headers=ADMIN).json()["words"] == []


def test_the_page_has_the_controls():
    import pathlib

    page = (pathlib.Path(__file__).resolve().parent.parent / "admin_ui" / "tracker.html").read_text()
    for needle in ('id="hearAll"', 'id="hardCard"', 'id="hardScan"', 'id="hardHear"',
                   "data-hear=", "data-hard-ok=", "/api/admin/hard-words/scan"):
        assert needle in page, needle


def test_a_full_wipe_takes_the_scan_with_the_scripts(lexicon):
    import demo_data

    lexicon.replace_hard_words([{"word": "Mahomes", "count": 3, "episodes": 2,
                                 "example": "Mahomes threw.", "title": "Chiefs"}])
    lexicon.hard_word_ok("Brunson")

    class Cache:
        def clear(self):
            return 0

    events = types.SimpleNamespace(clear=lambda: 0, count=lambda: 0)
    demo_data.wipe(cache=Cache(), events=events, erase_listener=lambda uid: None,
                   scope="all", dry_run=False)
    assert lexicon.hard_words() == []
    assert lexicon.hard_words_ok() == {"Brunson"}, "a fact about the voice stays"
