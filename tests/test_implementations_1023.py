"""The 10.23 packet (PROBLEMS.md §306)."""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import research  # noqa: E402
from remote_voice import RemoteVoiceError  # noqa: E402
from voice_worker import register  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")


def _js(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


# --- a failed episode says sorry, never a type name ---------------------------

def test_a_voice_failure_reads_as_the_owners_apology():
    said = appmod.listener_error(RemoteVoiceError("no voice worker can speak"))
    assert said == appmod.LISTENER_APOLOGY
    assert said.startswith("We're sorry that your episode is not available")
    assert said.endswith("-The FAM Team")
    assert "RemoteVoiceError" not in said and "server log" not in said


def test_admin_keeps_the_type_name_and_the_listener_gets_the_apology():
    # /admin's record is friendly_error; the 502 a listener reads is not.
    assert "Generation failed: RemoteVoiceError" in appmod.friendly_error(
        RemoteVoiceError("x"))
    assert "pipeline_errors.record(\"episode\", \"failed before any audio\",\n" \
           "                               friendly_error(exc)" in APP
    assert "detail=listener_error(exc)" in APP
    assert "detail=friendly_error(exc)" not in APP


def test_a_sentence_about_their_own_request_passes_through():
    exc = research.NoEvidence("Nothing has been published about that yet.")
    assert appmod.listener_error(exc) == appmod.friendly_error(exc)


# --- the worker says when its heartbeat stops landing -------------------------

def test_a_failed_heartbeat_is_a_warning(monkeypatch, caplog):
    monkeypatch.setenv("FAM_APP_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("VOICE_REGISTRY_TOKEN", "shhh")
    monkeypatch.setenv("PUBLIC_WORKER_URL", "https://voice.example")
    with caplog.at_level(logging.WARNING, logger="voice_worker.register"):
        landed = asyncio.run(register.announce(True, "ready", 24000, timeout=0.5))
    assert landed is False
    assert any("registration to http://127.0.0.1:9 failed" in r.getMessage()
               and r.levelno == logging.WARNING for r in caplog.records)


# --- the DailyFAM tab takes you back to the top -------------------------------

def test_the_dailyfam_tab_scrolls_to_the_top_when_already_there():
    body = _js("openMyFamTab")
    guard = body.index('stack.length === 1 && stack[0] === "myfam"')
    assert guard < body.index("loadMyFamFeed()"), "it redraws before checking"
    assert 'querySelector("#screen-myfam > .scroll")' in body
    assert "scrollTo({ top: 0" in body
    # Only when DailyFAM is really on screen and scrolled down; otherwise the
    # tab opens and loads it as before (a screen can show without the stack).
    assert "scroller.scrollTop > 0" in body
    assert 'getElementById("screen-myfam").classList.contains("active")' in body


# --- "Add to myFAM Playlist", the logo drawn ----------------------------------

def test_every_add_to_playlist_row_draws_the_myfam_logo():
    assert 'menuLabel("add", "Add to playlist")' not in INDEX
    assert INDEX.count("addToPlaylistLabel(),") == 3
    label = _js("addToPlaylistLabel")
    # One flex item, or the row's gap splits "Add to", the mark and "Playlist".
    assert '"<span>Add to " + myFamMark() + " Playlist</span>"' in label
    mark = _js("myFamMark")
    # The heading's own glyphs, so the brand changes in one place; the
    # letters kept for copy and screen readers.
    assert '.wordmark[aria-label="myFAM"]' in mark
    assert '<span class="menu-mark-text">FAM</span>' in mark


# --- covers beside the playlists ----------------------------------------------

def test_the_add_sheet_shows_each_playlists_cover_on_the_left():
    row = _js("mixPickRow")
    assert row.index("coverStyle(m)") < row.index("escapeHtml(m.name)")
    assert "folderSVG()" in row, "a mix with no cover shows the folder"
    assert "items.push({ label: mixPickRow(m)" in _js("openAddToMix")


# --- the line stays on the drawn phone ----------------------------------------

def test_the_toast_is_inset_by_the_phones_bezel():
    assert ".toast, .snack{ position:absolute; left:14px; right:14px;" in INDEX
    assert "@media (max-width:520px){ .toast, .snack{ left:0; right:0; } }" in INDEX
    # The override has to come after the rule it overrides.
    assert INDEX.index("left:14px; right:14px;") < INDEX.index(
        ".toast, .snack{ left:0; right:0; }")


# --- a friend's vibe: plane sends, arrows VIBE! -------------------------------

def test_the_paper_plane_sends_and_the_vibe_arrows_share_to_your_story():
    viewer = INDEX.split('id="storyOverlay"', 1)[1].split('id="vibeComposer"', 1)[0]
    box = viewer.split('<div class="story-reply-box">', 1)[1].split("</div>", 1)[0]
    assert 'id="storyReplyInput"' in box
    assert 'type="submit" class="story-send"' in box, "the plane is not in the box"
    reshare = viewer.split('id="storyReshare"', 1)[1].split("</button>", 1)[0]
    assert 'aria-label="VIBE! to your story"' in reshare
    assert "M4 10.4A4.4 4.4 0 0 1 8.4 6H18" in reshare, "not the VIBE! arrows"
    assert "M21.5 3 10.2 14.3" not in reshare, "the plane is still the share"
