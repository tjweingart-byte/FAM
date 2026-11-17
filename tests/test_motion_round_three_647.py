"""Motion, round three: the second audit (PROBLEMS.md §647).

Read off the shipped page. The smoke run's "A refused switch flips back;
reduced motion moves no line; a tile's save holds" drives the first three
live - the source pins of §646 passed while a refused switch stayed on - and
yourFAM's Saved Undo now checks the row came back. These pin the rest, so a
later edit cannot quietly undo them.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()
SMOKE = (ROOT / "tools" / "smoke_preview.py").read_text()


def _fn(name):
    start = INDEX.index("  function " + name + "(")
    return INDEX[start:INDEX.index("\n  }\n", start)]


def _motion_fn(name):
    start = INDEX.index("    function " + name + "(")
    return INDEX[start:INDEX.index("\n    }\n", start)]


def test_a_flipped_switch_is_always_patched_by_the_next_redraw():
    flip = _fn("flipSetting")
    assert "row._html = null;" in flip
    # patchSettings skips only a row that is still what it drew.
    assert "if(was._html === held[i]) return;" in _fn("patchSettings")
    assert "A refused switch flips back" in SMOKE
    assert "/api/preferences" in SMOKE and "new Response('no', { status: 500 })" in SMOKE


def test_reduced_motion_beats_the_shown_lines_transitions():
    block = re.search(
        r"@media \(prefers-reduced-motion: reduce\)\{\s*\.toast, \.snack, \.toast\.show, \.snack\.show,"
        r"\s*\.toast::before, \.snack::before, \.toast\.show::before, \.snack\.show::before\{ transition:none; \}",
        INDEX)
    assert block, "the reduced-motion block does not name the .show selectors"
    assert 'page.emulate_media(reduced_motion="reduce")' in SMOKE


def test_a_tiles_save_survives_an_older_shelf_read():
    load = _fn("loadTileSaves")
    assert "var seq = tileSaveSeq;" in load
    assert "if(!d || seq !== tileSaveSeq) return;" in load
    assert "tileSavePending" in load
    save = _fn("saveTile")
    assert "tileSaveSeq++;" in save and "tileSavePending[key] = !on;" in save
    assert save.count("settle();") == 2
    assert "an older shelf read undid a tile's save" in SMOKE


def test_log_out_waits_for_a_pending_commit_and_ignores_late_answers():
    assert "return f();" in _fn("flushSnackCommit")
    assert "return flushSnackCommit();" in _fn("hideSnack")
    out = _fn("logOut")
    assert "var pending = hideSnack();" in out
    assert "LOGOUT_COMMIT_WAIT_MS" in out and "var LOGOUT_COMMIT_WAIT_MS = 1500;" in INDEX
    assert out.index("Promise.race(") < out.index('fetch("/api/auth/logout"')
    assert "authEpoch++;" in out
    gone = _fn("goneChat")
    assert "var epoch = authEpoch;" in gone and "return req.then(" in gone
    assert gone.count("if(epoch !== authEpoch) return;") == 2
    # The shelves' commits hand log-out their request too.
    assert 'return fetch("/api/saved/"' in INDEX and 'return fetch("/api/vibe?q="' in INDEX


def test_a_tap_or_timer_turn_ends_with_the_viewer():
    turn = _motion_fn("turnStory")
    assert "return c;" in turn and "return null;" in turn
    assert "storyTurning = Motion.turnStory(" in _fn("turnToFriend")
    assert "if(storyTurning){ var tt = storyTurning; storyTurning = null; Motion.cubeEnd(tt); }" in INDEX


def test_public_private_takes_the_keyboard():
    start = INDEX.index('html += \'<div class="mix-vis" data-flip="vis"')
    vis = INDEX[start:INDEX.index("role=\"switch\"", start) + 200]
    assert 'tabindex="0"' in vis
    assert "toggleMixVisibility(); }" in vis and "event.preventDefault();" in vis


def test_mark_playing_keeps_the_icon_key():
    assert "icon._iconKey = true;" in _fn("markPlaying")
    assert 'document.getElementById("paPlayIcon")._iconKey = document.getElementById("paPlayIcon2")._iconKey = true;' in INDEX


def test_the_saved_undo_smoke_check_counts_the_row():
    assert "Undo did not put the saved row back, or still deleted it" in SMOKE
    assert '{"rows": gone_n, "row": 1, "deletes": 0}' in SMOKE
