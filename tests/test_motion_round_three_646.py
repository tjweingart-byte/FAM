"""Motion, round three: the first audit (PROBLEMS.md §646).

Read off the shipped page. The smoke run's "An Undo whose request has gone
does nothing" and the story checks in "dailyFAM's round-three motion holds
nothing" drive the Undo and the story drag live; these pin the rest of the
races the audit found, so a later edit cannot quietly undo them.
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


def test_an_undo_whose_request_has_gone_is_taken_down_with_it():
    flush = _fn("flushSnackCommit")
    assert "if(keepLine !== true) endSnackLine();" in flush
    assert flush.index("endSnackLine()") < flush.index("f();")
    # The Undo itself does nothing once its commit has gone.
    assert "if(commit && snackCommit !== commit){ endSnackLine(); return; }" in _fn("snack")
    assert "flushSnackCommit(true);" in _fn("snack")
    assert "hideSnack();" in _fn("logOut") and "endSnackLine();" in _fn("logOut")
    # A commit sent as the page closes survives it.
    assert "keepalive: true" in _fn("recordFamEvent")
    gone = _fn("goneChat")
    assert gone.count("keepalive: true") == 2


def test_a_reread_shelf_keeps_a_pending_removal_off_and_undo_adds_once():
    assert "withoutPending(d.items, d.folders, savedPendingRemove" in _fn("loadSaved")
    assert "withoutPending(d.vibes, d.folders, vibePendingRemove, vibeKey)" in _fn("loadMyVibe")
    assert "if(savedItem(id)) return;" in _fn("removeSaved")
    assert "if(myVibes.some(function(x){ return vibeKey(x) === key; })) return;" in _fn("unvibe")
    assert 'keepalive: true' in _fn("removeSaved") and 'keepalive: true' in _fn("unvibe")


def test_not_interested_undo_after_a_redraw_draws_the_rails_again():
    assert "if(redrawn && feedSectionsLast) renderMyFamFeed(feedSectionsLast);" in _fn("tileNotInterested")
    assert _fn("renderMyFamFeed").index("feedSectionsLast = sections;") < \
        _fn("renderMyFamFeed").index("notInterestedPending[t.id]")
    assert "if(!rail._endsWatched){" in _fn("watchRailEnds")


def test_a_story_drag_is_captured_and_never_outlives_the_viewer():
    assert "if(drag) try { el.setPointerCapture(e.pointerId); } catch(err){}" in INDEX
    assert "if(drag){ storySwipe = null; endDrag(0, 0, 0, 0, true); }" in INDEX
    assert 'el.addEventListener("lostpointercapture"' in INDEX
    close = _fn("closeStory")
    assert close.index("if(storyDragEnd) storyDragEnd();") < close.index("Motion.shrinkStory(ov)")
    # Declared without a value, or it would undo what the gestures set.
    assert "  var storyDragEnd;\n" in INDEX
    assert "if(ending || el.hidden) return;" in INDEX
    assert "c.fin = fin;" in INDEX and "cubeEnd: cubeEnd" in INDEX


def test_the_smoke_story_checks_cannot_skip():
    assert "yf-vibe" not in SMOKE
    assert "def story_face():" in SMOKE
    assert 'assert i >= 0, "no face on DailyFAM\'s row has stories to open"' in SMOKE


def test_a_kept_category_panel_takes_no_tap_while_the_next_loads():
    load = _fn("loadCategoryEpisodes")
    assert 'el.setAttribute("aria-busy", "true");\n    el.inert = true;' in load
    assert "catPanelIdle(el); draw();" in load
    assert "catPanelIdle();" in _fn("clearCategory") and "catPanelIdle();" in _fn("openCategoryFind")
    assert "if(catPage.loading) return;" in _fn("playCategoryAll")
    assert "catPage.loading) return;" in _fn("toggleFollowCategory")


def test_a_redraw_never_lands_under_a_carried_row():
    settle = _fn("settleMixBody")
    assert "if(mixDrag && mixDrag.row.isConnected){" in settle
    assert "if(!d.row.isConnected){ mixDrag = null; return; }" in _fn("tiltMixDrag")
    assert "if(mixDrag && !mixDrag.row.isConnected) mixDrag = null;" in _fn("startMixDrag")


def test_the_players_said_captions_are_dimmed():
    assert re.search(r"#screen-player \.captions-panel\.on \.cc-text \.cc-said\{[^}]*"
                     r"color:rgba\(244,239,228,0\.5\); \}", INDEX)
    assert "@keyframes ccIn{ from{ color:rgba(244,239,228,0.5); } }" in INDEX
    assert "animation:ccIn var(--dur-colour) var(--ease-out) backwards;" in INDEX


def test_fast_second_taps_send_what_they_show():
    # Settings' switches: the row's action turns round with the switch.
    assert 'row.setAttribute("onclick", next)' in _fn("flipSetting")
    # A mix's Public/Private: only the last tap's answer paints.
    vis = _fn("toggleMixVisibility")
    assert "if(seq !== mixVisSeq[id]){" in vis and "now.public = mixVisWant[id]" in vis
    # Follow: only the latest paint takes the class away.
    assert "if(btn._paint === paint)" in _motion_fn("followButton")
    # Search: a cancel frees the next press; a late speller answer cannot.
    assert "searchSending = false;" in _fn("cancelGeneration")
    assert "if(searchSending === sending) searchSending = false;" in _fn("runSearch")
    assert 'if(key !== "_custom") searchAsked = null;' in _fn("generate")


def test_late_answers_do_not_write_over_true_ones():
    assert "if(refused) return;" in _fn("fileInFolder")
    assert "personPublished = null;" in _fn("openPersonProfile")
    assert "authKnown = true;\n          authUnreachable = true;" in _fn("refreshAuth")
    assert 'classList.remove("is-searching")' in _fn("runMyFamSearch")
    assert 'classList.remove("is-searching")' in _fn("openMyFamSearch")
    # A topic being added is not played as its bare title.
    assert "pending: true" in _fn("addRecTopic") and "pending: true" in _fn("toggleTopicInMix")
    assert "!t.pending" in _fn("todayOrder")


def test_motion_leaves_nothing_stale_behind():
    assert "if(el && el._picFade){ el._picFade(); }" in _motion_fn("picture")
    assert "if(edge){ if(edge.engaged) undrag(edge); edge = null; }" in _motion_fn("screen")
    assert ".motion-roll-old::before{ content:attr(data-n); }" in INDEX
    assert 'old.setAttribute("data-n", String(before));' in _motion_fn("roll")
    assert "was.anims.forEach(function(a){ try { a.cancel(); } catch(e){} });" in _motion_fn("column")
    assert "if(!g.isConnected){ if(g._landed) g._landed(); return; }" in _motion_fn("gather")
    assert 'wrap.style.pointerEvents = "none";' in INDEX
    assert "VOICE.underHide()" in _fn("clearGenOverlay")
