"""A stream that broke part-way, asked for again (PROBLEMS.md §685).

The web player (`static/fam-audio.js`) asks again by the episode's id with
`reconnect=1`, skipping the samples it already holds. The browser half is
driven in `tools/smoke_reconnect.py`; this pins the two halves together:

* **The same play, not a second one**: the reconnect records no play row,
  so a dropped connection never counts twice on Most played or a profile.
* **It replays, never writes**: asked by `episode=`, so it is that episode
  or a 409 - a different episode is never spliced onto the first.
* The player says so in the request it builds.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_audio_links_252 import _client  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _recording(monkeypatch, appmod):
    rows = []
    monkeypatch.setattr(appmod, "_listener", lambda request: "listener-685")
    monkeypatch.setattr(appmod, "_remembers", lambda request: True)
    monkeypatch.setattr(appmod.EVENTS, "record", lambda event: rows.append(event) or 1)
    return rows


def _get(client, **extra):
    return client.get("/api/audio", params={
        "q": "why is the sky blue", "minutes": 1, "fmt": "pcm", **extra})


def test_a_reconnect_records_no_second_play(tmp_path, monkeypatch):
    client, _, appmod = _client(tmp_path, monkeypatch)
    rows = _recording(monkeypatch, appmod)
    first = _get(client)
    assert first.status_code == 200
    assert len(rows) == 1, "the first request is the play"
    again = _get(client, episode=first.headers["x-fam-episode"], reconnect=1)
    assert again.status_code == 200
    # The production voice is seeded from voice + words (`[op-voice]`); the
    # test voice is not, so the length is what is pinned here.
    assert len(again.content) == len(first.content), "the same episode, whole"
    assert len(rows) == 1, "a reconnect is the same play, never a second"


def test_a_reconnect_is_counted_on_admin(tmp_path, monkeypatch):
    import pipeline_errors

    client, _, appmod = _client(tmp_path, monkeypatch)
    noted = []
    monkeypatch.setattr(pipeline_errors, "record",
                        lambda *a, **k: noted.append((a, k)))
    first = _get(client)
    assert not [n for n in noted if "asked again" in n[0][1]]
    _get(client, episode=first.headers["x-fam-episode"], reconnect=1)
    assert [n for n in noted if "asked again" in n[0][1]], "a broken stream is visible"


def test_a_reconnect_for_an_episode_not_kept_is_refused(tmp_path, monkeypatch):
    client, engine, appmod = _client(tmp_path, monkeypatch)
    first = _get(client)
    episode = first.headers["x-fam-episode"]
    appmod.SCRIPT_CACHE._conn().execute("DELETE FROM scripts")
    voiced = engine.calls
    again = _get(client, episode=episode, reconnect=1)
    assert again.status_code == 409, "never a new episode under the old one's front"
    assert engine.calls == voiced, "nothing voiced for a reconnect it refused"


def test_the_player_asks_again_by_the_episodes_id():
    js = open(os.path.join(ROOT, "static", "fam-audio.js"), encoding="utf-8").read()
    assert '"&reconnect=1"' in js
    assert "rate !== serverRate" in js, "never spliced at another rate"
    assert 'c.state === "running" && wantPaused' in js, "the listener's pause wins"
    assert '"&episode=" + encodeURIComponent(episodeId)' in js
    assert 'replace("&fmt=FMT", "&fmt=pcm")' in js, "asked again as PCM, the same samples"
    assert "playbackLog: playbackLog" in js
    html = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    assert "out.playback = playback" in html, "Report a problem sends the player's log"
    assert "onInterrupted:" in html
