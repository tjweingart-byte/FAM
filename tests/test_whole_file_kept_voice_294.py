"""Replays that stay off the server (PROBLEMS.md §294).

Two ways a replay used to go back through Render, each tested by what it
would cost if it came back:

* **A player that cannot decode an Opus stream** (Safari without WebCodecs'
  Opus) streamed every replay as PCM - about 6 MB a two-minute episode of
  billed bandwidth. One that can decode the whole file asks `fmt=pcm&link=1`
  and is handed the link; with no file it still streams PCM, and a replay
  that does stream as PCM is counted in health. The browser half is driven
  in `tools/smoke_opus.py`.
* **A search by somebody who never picked a voice** was voiced again in the
  default whenever another listener's voice was kept - a GPU job and a
  first-play stream for audio that already existed. It now replays the kept
  voice. §447 (the owner, 11.04) widened this to every search whose voice
  was not picked for it: a standing choice - the default, or the voice last
  picked - plays the kept voice; a voice picked for this search
  (`voice_picked=1`) is honoured exactly.
"""
from __future__ import annotations

import base64
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import audio_codec  # noqa: E402
import voice_bank  # noqa: E402
from tests.test_audio_links_252 import _client, _get, bucket  # noqa: E402,F401
from tests.test_voice_bank_147 import BankEngine, bank, served  # noqa: E402,F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
needs_opus = pytest.mark.skipif(not audio_codec.available()[0],
                                reason="PyAV is not installed")


# --- a player that decodes the whole file -------------------------------------


@needs_opus
def test_a_pcm_player_that_can_read_the_file_is_handed_the_link(tmp_path, monkeypatch, bucket):
    client, engine, appmod_ = _client(tmp_path, monkeypatch)
    first = _get(client, fmt="pcm", link=1)
    # A first play has no file: it streams PCM, as voiced.
    assert "x-fam-audio-link" not in first.headers
    assert first.headers["content-type"].startswith("audio/L16")
    voiced = engine.calls
    again = _get(client, fmt="pcm", link=1)
    assert again.headers["x-fam-audio-link"] == "1"
    assert again.json()["format"] == "ogg-opus"
    assert engine.calls == voiced
    assert appmod_._AUDIO_LINKS["issued"] == 1
    assert appmod_._AUDIO_LINKS["pcm_replays"] == 0


@needs_opus
def test_a_pcm_replay_through_the_server_is_counted_in_health(tmp_path, monkeypatch, bucket):
    client, _, appmod_ = _client(tmp_path, monkeypatch)
    _get(client, fmt="pcm")
    _get(client, fmt="pcm")  # a replay, and nobody asked for a link
    links = client.get("/api/health").json()["audio"]["links"]
    assert links["pcm_replays"] == 1
    assert links["issued"] == 0


def test_the_probe_is_kept_audio_and_decodes():
    """The player's probe is a real Ogg Opus file at the stream's rate: a
    probe the browser rejects for its own reasons would send every Safari
    replay back through the server, silently."""
    js = open(os.path.join(ROOT, "static", "fam-audio.js")).read()
    start = js.index('var OGG_OPUS_PROBE = "') + len('var OGG_OPUS_PROBE = "')
    blob = base64.b64decode(js[start:js.index('"', start)])
    assert blob[:4] == b"OggS" and b"OpusHead" in blob[:64]
    if audio_codec.available()[0]:
        pcm = audio_codec.decode(audio_codec.OPUS, blob, 24000, 2400)
        assert len(pcm) == 2400 * 2


def test_a_whole_file_fallback_streams_pcm_not_opus():
    """The stream behind a link that will not play is in the format the
    player can decode: Opus for WebCodecs, PCM for a whole-file player."""
    js = open(os.path.join(ROOT, "static", "fam-audio.js")).read()
    assert 'streamUrl = url.replace("&fmt=FMT", "&fmt=" + fmt) + "&link_failed=1"' in js
    assert "(linkedOpus ? playLinked : playWhole)" in js


# --- the voice a search is spoken in ------------------------------------------


def _search(client, voice="", picked=False):
    return client.get("/api/audio", params={
        "q": "how volcanoes form", "minutes": 3, "fmt": "pcm", "surface": "search",
        **({"voice": voice} if voice else {}),
        **({"voice_picked": 1} if picked else {})})


def _default():
    return f"chatterbox:{voice_bank.default_slug()}"


def test_a_listener_who_never_chose_replays_the_kept_voice(served):
    first = TestClient(appmod.app)
    assert first.post("/api/voices/choice",
                      json={"voice": "chatterbox:nova"}).status_code == 200
    assert _search(first, voice="chatterbox:nova", picked=True).status_code == 200
    assert BankEngine.spoken and set(BankEngine.spoken) == {"chatterbox:nova"}

    BankEngine.spoken = []
    other = TestClient(appmod.app)
    # The app names the voice it shows, which is the default for somebody
    # who never chose.
    res = _search(other, voice=_default())
    assert res.status_code == 200
    assert BankEngine.spoken == [], "kept audio was voiced again for a listener with no choice"
    assert res.headers["x-fam-cache"] == "hit"


def test_a_standing_choice_of_the_default_replays_the_kept_voice(served):
    """§447: the voice last picked - the default here - is not a pick for
    this search, so kept audio in another voice plays as a replay."""
    first = TestClient(appmod.app)
    first.post("/api/voices/choice", json={"voice": "chatterbox:nova"})
    _search(first, voice="chatterbox:nova", picked=True)
    BankEngine.spoken = []
    chose = TestClient(appmod.app)
    assert chose.post("/api/voices/choice", json={"voice": _default()}).status_code == 200
    res = _search(chose, voice=_default())
    assert BankEngine.spoken == [], "a standing choice re-voiced a cached search"
    assert res.headers["x-fam-cache"] == "hit"


def test_a_standing_choice_of_another_voice_replays_the_kept_voice(served):
    first = TestClient(appmod.app)
    first.post("/api/voices/choice", json={"voice": "chatterbox:nova"})
    _search(first, voice="chatterbox:nova", picked=True)
    BankEngine.spoken = []
    other = TestClient(appmod.app)
    other.post("/api/voices/choice", json={"voice": "chatterbox:ellis"})
    _search(other, voice="chatterbox:ellis")
    assert BankEngine.spoken == [], "the voice last picked re-voiced a cached search"


def test_a_voice_picked_for_this_search_is_honoured(served):
    first = TestClient(appmod.app)
    first.post("/api/voices/choice", json={"voice": "chatterbox:nova"})
    _search(first, voice="chatterbox:nova", picked=True)
    BankEngine.spoken = []
    _search(TestClient(appmod.app), voice="chatterbox:ellis", picked=True)
    assert set(BankEngine.spoken) == {"chatterbox:ellis"}


def test_picking_the_default_for_this_search_is_honoured(served):
    first = TestClient(appmod.app)
    first.post("/api/voices/choice", json={"voice": "chatterbox:nova"})
    _search(first, voice="chatterbox:nova", picked=True)
    BankEngine.spoken = []
    _search(TestClient(appmod.app), voice=_default(), picked=True)
    assert BankEngine.spoken, "a pick of the default for this search was overridden"
    assert "chatterbox:nova" not in BankEngine.spoken


def test_with_nothing_kept_a_listener_who_never_chose_hears_the_default(served):
    _search(TestClient(appmod.app), voice=_default())
    assert BankEngine.spoken, "nothing kept, so the episode is voiced"
    assert not {"chatterbox:nova", "chatterbox:ellis"} & set(BankEngine.spoken)


def test_with_nothing_kept_the_voice_last_picked_speaks(served):
    """Nothing to replay: the standing choice is what the episode is voiced in."""
    client = TestClient(appmod.app)
    client.post("/api/voices/choice", json={"voice": "chatterbox:ellis"})
    _search(client, voice="chatterbox:ellis")
    assert set(BankEngine.spoken) == {"chatterbox:ellis"}


def test_the_app_sends_a_pick_once_and_never_from_settings():
    html = open(os.path.join(ROOT, "static", "index.html")).read()
    js = open(os.path.join(ROOT, "static", "fam-audio.js")).read()
    assert 'listener.voicePicked ? "&voice_picked=1"' in js
    assert 'voicePicked: surface === "search" && voicePickedForSearch' in html
    assert 'if(surface === "search") voicePickedForSearch = false;' in html
    assert "openVoiceMenu('settings')" in html
    assert 'if(voiceMenuFrom !== "settings") voicePickedForSearch = true;' in html


def test_switching_voice_on_the_player_waits_for_the_choice():
    html = open(os.path.join(ROOT, "static", "index.html")).read()
    assert "kept.then(function(){ generate(key); });" in html
