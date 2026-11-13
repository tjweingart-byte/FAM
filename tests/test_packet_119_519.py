"""The owner's 11.9 packet (PROBLEMS.md §519).

Six things, each pinned where a regression would quietly undo it: a Go Deeper
that ended a myFAM playlist, a queue sheet that ran edge to edge, share/vibe/
save sitting on the sources pill, a transport of outlined circles, tab labels
that did not match the wordmarks, captions that were a strip at the bottom
covering the transport - and the daily limit screen, which nobody should see
while everyone has unlimited access.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    return re.search(r"function " + name + r"\([^)]*\)\{(.*?)\n  \}", INDEX, re.S).group(1)


def test_a_go_deeper_on_a_playlist_episode_is_a_detour():
    follow = _fn("startFollowUp")
    assert "mixDetourFor(baseKey)" in follow and ".mixDetour = detour" in follow
    detour = _fn("mixDetourFor")
    assert "baseKey === mixPlay.key" in detour
    # A Go Deeper on the follow-up is the same detour.
    assert "b.mixDetour === mixPlay.key" in detour


def test_the_playlist_waits_behind_the_detour_and_carries_on():
    body = _fn("populatePlayer")
    branch = body[body.index("t.mixDetour && mixPlay && mixPlay.key === t.mixDetour"):]
    branch = branch[:branch.index("} else if(!t.fromMix)")]
    # §536: when the detour ends the interrupted episode resumes (resumeMix),
    # rather than the playlist jumping to its next episode.
    assert "resumeMix(detourOf)" in branch and "playOpts.nextUp = false" in branch
    # The order matters: the detour is tested before "anything else ends it".
    assert body.index("t.mixDetour && mixPlay") < body.index("} else if(!t.fromMix){\n      mixPlay = null;")


def test_a_detour_that_fails_goes_back_to_the_playlist():
    body = _fn("mixItemFailed")
    assert "off.mixDetour === mixPlay.key" in body
    assert "resumeMix(mixPlay.key)" in body  # back to the interrupted episode (§536)


def test_the_interrupted_episode_resumes_where_it_was():
    """§536, the owner's rule: Go Deeper only interrupts a playlist. The
    episode it left comes back at its position, as the same audio, and a
    skip on the follow-up ends the detour the same way."""
    follow = _fn("startFollowUp")
    assert "noteMixInterrupted()" in follow
    note = _fn("noteMixInterrupted")
    assert "FamAudio.position()" in note and "FamAudio.episode()" in note
    resume = _fn("resumeMix")
    assert "t.episode = r.episode" in resume and "pendingResumeSeek" in resume
    assert "advanceMix(fromKey)" in resume  # nothing to resume: the next one
    skip = _fn("skipMixItem")
    assert "on.mixDetour === from" in skip and "resumeMix(from)" in skip


def test_the_queue_sheet_has_room():
    body = _fn("openQueue")
    assert "q-sheet-title" in body and "queuePic(" in body
    css = re.search(r"\.q-row\{([^}]*)\}", INDEX).group(1)
    assert "padding:7px 14px 7px 22px" in css
    assert re.search(r"\.q-pic\{[^}]*width:42px", INDEX)


def test_share_vibe_and_save_sit_clear_of_the_sources():
    css = re.search(r"\.p-side\{([^}]*)\}", INDEX).group(1)
    assert "bottom:34px" in css


def test_the_transport_is_five_slots_with_play_in_the_middle():
    row = re.search(r'<div class="pc-row1">(.*?)\n          </div>', INDEX, re.S).group(1)
    order = re.findall(r'class="(pc-slot|mini-ctrl-btn seek|mini-play-btn|mini-ctrl-btn mix-skip)"', row)
    assert order == ["pc-slot", "mini-ctrl-btn seek", "mini-play-btn",
                     "mini-ctrl-btn seek", "mini-ctrl-btn mix-skip"]
    # Columns that shrink: fixed slots overflowed a 312px-wide phone.
    assert "grid-template-columns:minmax(0,1fr) minmax(0,1fr) 74px minmax(0,1fr) minmax(0,1fr)" in INDEX
    # Bare glyphs: no outlined circle behind the ±15 and next on the player.
    rule = re.search(r"#screen-player \.pc-row1 \.mini-ctrl-btn\{([^}]*)\}", INDEX).group(1)
    assert "background:none" in rule and "border:0" in rule


def test_the_tabs_are_spelled_as_the_wordmarks():
    tabbars = INDEX.count('<div class="tabbar">')
    for label in ("dailyFAM", "myFAM", "yourFAM"):
        assert INDEX.count('<div class="lbl">' + label + '</div>') == tabbars
    assert '<div class="lbl">DailyFAM</div>' not in INDEX
    assert '<div class="lbl">YourFAM</div>' not in INDEX


def test_captions_are_a_pill_and_read_over_the_picture():
    stage = INDEX[INDEX.index('<div class="mini-stage" id="playerStage">'):]
    stage = stage[:stage.index('<div class="p-titlerow">')]
    assert 'id="ccPanel"' in stage and 'id="cc-text"' in stage
    row2 = re.search(r'<div class="pc-row2">(.*?)</div>\n        </div>', INDEX, re.S).group(1)
    assert 'id="ccIcon"' in row2 and "Captions" in row2 and 'onclick="toggleCC()"' in row2
    # Remembered between episodes, on this device only.
    assert "rememberCaptions(true)" in _fn("toggleCC")
    assert "captionsWanted()" in _fn("populatePlayer")


def test_everyone_is_unlimited_in_production_for_now():
    services = yaml.safe_load((ROOT / "render.yaml").read_text())["services"]
    fam = next(s for s in services if s["name"] == "fam")
    env = {e["key"]: e.get("value") for e in fam.get("envVars", [])}
    assert env.get("ENFORCE_QUOTAS") == "0"
