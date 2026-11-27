"""§703: the original voice is no longer part of the app.

The bank listed the recording every worker is built with first, without it
being a row, so /admin showed one voice with no Remove button. The owner:
"That was the original voice and should no longer be a part of the app."

The bank is now its rows. Each rule is tested where it is enforced:

* it is not listed, known or drawn while the bank holds any voice;
* a request naming no voice - or naming the original, or a voice that left -
  is spoken in the house voice (the bank's oldest) and kept under its name;
* the last voice cannot be removed, because an empty bank is the original;
* an empty bank still speaks: the built-in recording, and says what it is.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import tts  # noqa: E402
import voice_bank  # noqa: E402
from remote_voice import RemoteChatterboxEngine  # noqa: E402
from tests.test_voice_bank_147 import RIGHTS, bank, wav  # noqa: E402,F401


@pytest.fixture
def two(bank):  # noqa: F811
    bank.add("nova", "Nova", wav(), RIGHTS)
    bank.add("ellis", "Ellis", wav(), RIGHTS)
    return bank


def test_the_original_voice_is_not_in_a_bank_that_holds_one(two):
    original = voice_bank.default_slug()
    listed = voice_bank.catalogue()
    assert [v.slug for v in listed] == ["nova", "ellis"]
    assert not any(v.default for v in listed)
    assert not voice_bank.known(original)
    assert not voice_bank.known(f"remote:{original}")
    assert not voice_bank.known("")
    assert {voice_bank.random_slug() for _ in range(200)} == {"nova", "ellis"}
    assert original not in voice_bank.redeal_signature().split(",")


def test_an_empty_bank_still_speaks_and_says_what_in(bank):  # noqa: F811
    listed = voice_bank.catalogue()
    assert [(v.slug, v.default) for v in listed] == [(voice_bank.default_slug(), True)]
    assert voice_bank.house_slug() == ""
    assert voice_bank.known(voice_bank.default_slug())
    assert RemoteChatterboxEngine.default_voice_id() == \
        f"remote:{RemoteChatterboxEngine.config().voice or 'reference_3'}"
    assert tts.ChatterboxEngine.reference_for(None) == tts.ChatterboxEngine.reference_path()


def test_the_house_voice_is_the_oldest_and_moves_only_when_it_leaves(two):
    assert voice_bank.house_slug() == "nova"
    two.add("maya", "Maya", wav(), RIGHTS)
    assert voice_bank.house_slug() == "nova"
    two.remove("nova")
    assert voice_bank.house_slug() == "ellis"


@pytest.mark.parametrize("named", [None, "", "remote:reference_3", "remote:default",
                                   "remote:gone"])
def test_no_voice_named_is_sent_to_the_worker_as_the_house_voice(two, named):
    payload = RemoteChatterboxEngine._payload(
        "Hello.", RemoteChatterboxEngine.config(), named)
    assert payload["voice"] == "nova"
    assert payload["voice_sha"] == two.get("nova").sha256
    assert "reference" not in payload
    retry = RemoteChatterboxEngine._payload(
        "Hello.", RemoteChatterboxEngine.config(), named, with_recording=True)
    assert retry["voice"] == "nova" and retry["reference"]


def test_audio_with_no_voice_named_is_kept_under_the_house_voice(two):
    assert RemoteChatterboxEngine.default_voice_id() == "remote:nova"
    assert tts.ChatterboxEngine.default_voice_id() == "chatterbox:nova"


def test_the_in_process_engine_clones_the_house_voice_not_the_original(two):
    for named in (None, "chatterbox:reference_3", "chatterbox:gone"):
        path = tts.ChatterboxEngine.reference_for(named)
        assert path.parent.name == "bank" and path.name.startswith("nova-"), named
    assert tts.ChatterboxEngine.reference_for("chatterbox:ellis").name.startswith("ellis-")


def test_admin_lists_only_removable_voices_and_keeps_the_last(two, monkeypatch):
    monkeypatch.setattr(appmod, "_require_admin", lambda request: None)
    client = TestClient(appmod.app)
    rows = client.get("/api/admin/voices").json()["voices"]
    assert [r["slug"] for r in rows] == ["nova", "ellis"]
    assert not any(r["default"] for r in rows), \
        "a row marked default is the one /admin draws with no Remove button"
    assert client.delete("/api/admin/voices/nova").json() == {"ok": True}
    last = client.delete("/api/admin/voices/ellis")
    assert last.status_code == 400 and "only voice" in last.text
    assert [v.slug for v in voice_bank.catalogue()] == ["ellis"]
    # Asking for one that is not there is still an honest "nothing removed".
    assert client.delete("/api/admin/voices/nobody").json() == {"ok": False}


def test_a_listener_is_never_offered_or_given_the_original(two, monkeypatch):
    class Engine(tts.DebugEngine):
        name = "remote"

        @classmethod
        def available(cls):
            return True

        voices = classmethod(lambda cls: RemoteChatterboxEngine.voices.__func__(cls))
        config = RemoteChatterboxEngine.config
        default_voice_id = classmethod(
            lambda cls: RemoteChatterboxEngine.default_voice_id.__func__(cls))

    monkeypatch.setattr(tts, "PRODUCTION_ENGINES", (Engine,))
    client = TestClient(appmod.app)
    page = client.get("/api/voices").json()
    assert [v["id"] for v in page["voices"]] == ["remote:nova", "remote:ellis"]
    assert page["default"] == page["selected"] == "remote:nova"
    # An installed client that still holds the original's id: accepted, and
    # kept as no choice at all - the house voice.
    assert client.post("/api/voices/choice",
                       json={"voice": "remote:reference_3"}).status_code == 200
    assert client.get("/api/voices").json()["selected"] == "remote:nova"
    assert appmod._episode_voice("u", "search", None, "remote:reference_3",
                                 picked=True) is None, \
        "the original, named by an old client, is no voice named: the house voice"
    assert client.post("/api/voices/choice",
                       json={"voice": "remote:nobody"}).status_code == 400
