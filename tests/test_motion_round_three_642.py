"""Motion, round three: myFAM's picks (PROBLEMS.md §642).

Read off the shipped page rather than a browser: the smoke run's "myFAM's
round-three motion holds nothing and leaves nothing behind" drives the same
things live.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()


def _motion():
    start = INDEX.index("  var Motion = (function(){")
    return INDEX[start:INDEX.index("  Motion.watchOverlays();", start)]


def _fn(name):
    start = INDEX.index("  function " + name + "(")
    return INDEX[start:INDEX.index("\n  }\n", start)]


def test_every_new_time_is_a_token_read_by_motion():
    for token in ("--dur-glide:300ms", "--dur-glide-in:120ms", "--dur-glide-out:150ms",
                  "--dur-deck-stagger:40ms", "--dur-fan:240ms", "--dur-underline:240ms",
                  "--dur-chip:260ms", "--dur-swap:300ms", "--ease-swap:cubic-bezier(.25,.1,0,1)",
                  "--dur-knob:220ms", "--dur-shackle:180ms", "--dur-rec-slot:250ms",
                  "--dur-sort:200ms", "--dur-drop:260ms", "--dur-dot-in:240ms", "--dur-square:300ms"):
        assert token in INDEX, token
    motion = _motion()
    for name in ("--dur-glide", "--dur-knob", "--dur-chip", "--dur-square", "--ease-swap"):
        assert '("' + name + '"' in motion, name


def test_the_glide_hands_the_light_on_by_transform():
    # No width animation and no overshoot: the rest pose is the CSS.
    assert "@keyframes famGlideDot" not in INDEX and "@keyframes famGlidePick" not in INDEX
    assert ".fam-glide-card.on{ opacity:1; border-color:var(--copper); transform:scale(1.05); }" in INDEX
    assert "Motion.glide(el, glide.pos, from, to);" in _fn("paintLoadingVariant")
    assert '<b class="fam-glide-pill"' in _fn("paintLoadingVariant")
    # It leaves as a copy, without finishing the player's push under it.
    start = INDEX.index("    function glideOut(")
    glide_out = INDEX[start:INDEX.index("\n    }\n", start)]
    assert "settle()" not in glide_out and "inertCopy(loader)" in glide_out


def test_back_from_a_mix_is_a_pop():
    assert 'onclick="closeMixPage()"' in INDEX
    close = _fn("closeMixPage")
    assert "goBack();" in close and "loadMixes()" in close
    assert "closeMixPage()" in _fn("cancelNewMix")
    assert "goBack();" in _fn("closeAddTopics")
    # Edits redraw the open mix in place, never push it onto itself.
    for name in ("toggleMixVisibility", "renameMix"):
        assert "openMix(" not in _fn(name), name


def test_edits_change_on_the_tap_and_roll_back():
    toggle = _fn("toggleTopicInMix")
    assert toggle.index("markAddTopicRows(id, adding)") < toggle.index("saveMix(")
    assert "markAddTopicRows(id, !adding)" in toggle and "renderAddTopics()" not in toggle
    vis = _fn("toggleMixVisibility")
    assert vis.index("paintMixVisibility(next)") < vis.index("saveMix(")
    # §646: a refused last tap goes back to what the server last said.
    assert "paintMixVisibility(back)" in vis
    rec = _fn("addRecTopic")
    assert rec.index("Motion.joinRow(") < rec.index("saveMix(") and "Motion.putBack(" in rec
    follow = _fn("toggleFollowCategory")
    assert follow.index("mark(at === -1") < follow.index("fetch(") and "mark(at !== -1, kept)" in follow


def test_a_refused_reorder_goes_back():
    move = _fn("moveMixTopic")
    assert "now.items = kept;" in move and "Motion.putBack(" in move


def test_the_switch_slides_the_knob_that_is_there():
    assert "transition:transform var(--dur-knob) var(--ease-in-out)" in INDEX
    assert "transition:transform var(--dur-shackle) var(--ease-out) var(--dur-knob)" in INDEX
    # An identical redraw leaves the page alone.
    assert "if(body._drawn === html && body.innerHTML) return;" in _fn("renderMixBody")


def test_the_tabs_and_chips():
    tab = _fn("showPlayfamTab")
    assert "Motion.panels(" in tab and 'how.type === "click" && how.detail > 0' in tab
    # Explore ("cats") is the left tab since §691; the rule slides right for Playlists.
    assert ".pf-tabs:not(.cats)::after{ transform:translateX(100%); }" in INDEX
    assert "Motion.panels(scroller, cats ? -1 : 1, change)" in tab
    load = _fn("loadCategoryEpisodes")
    assert "Motion.DUR.spinnerAfter" in load and "Motion.DUR.spinnerMin" in load


def test_the_async_buttons_and_the_dot():
    assert "Motion.pending(btn)" in _fn("confirmNewMix")
    assert "renderPublicMix()" not in _fn("markMixAdded")
    assert "Motion.dot(el, on)" in _fn("drawMixDot")
    assert "Motion.fileIn(deck);" in _fn("finishMix")
