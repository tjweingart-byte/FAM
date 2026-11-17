"""Motion, round three: yourFAM's picks (PROBLEMS.md §645).

Read off the shipped page rather than a browser: the smoke run's "yourFAM's
round-three motion holds nothing and leaves nothing behind" drives the same
things live.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()


def _motion():
    start = INDEX.index("  var Motion = (function(){")
    return INDEX[start:INDEX.index("  Motion.watchOverlays();", start)]


def _fn(name):
    start = INDEX.index("  function " + name + "(")
    return INDEX[start:INDEX.index("\n  }\n", start)]


def _mfn(name):
    motion = _motion()
    start = motion.index("    function " + name + "(")
    return motion[start:motion.index("\n    }\n", start)]


def test_every_pick_is_the_one_built():
    spec = json.loads((ROOT / "docs" / "motion" / "round-three" / "yourfam.json").read_text())
    picked = {a["id"]: a["picked"] for a in spec["animations"]}
    assert picked == {"hub-arrival": "A", "shelf-switch": "A", "follow-button": "C", "friends-regroup": "A",
                      "person-fill": "A", "saved-remove": "A", "settings-late-rows": "C", "vibe-delete": "A",
                      "interest-picker": "B", "edit-profile-open": "B", "changes-land": "B", "folder-file": "A",
                      "settings-switches": "A", "story-editor": "B"}


def test_every_new_time_is_a_token_read_by_motion():
    for token in ("--dur-hub-in:300ms", "--dur-hub-section:80ms", "--dur-hub-tile:40ms", "--dur-shelf-bar:250ms",
                  "--dur-follow:260ms", "--dur-regroup:320ms", "--dur-regroup-out:150ms",
                  "--dur-count-roll:350ms", "--dur-tile-gone:150ms", "--dur-grid-close:250ms",
                  "--dur-flood:250ms", "--dur-chip-drain:180ms", "--dur-check:200ms",
                  "--dur-page-sheet-in:300ms", "--dur-page-sheet-out:220ms", "--dur-photo-develop:320ms",
                  "--dur-pill-new:200ms", "--dur-folder-arc:380ms", "--dur-bump:220ms", "--dur-switch:200ms",
                  "--dur-into-ring:380ms", "--dur-ring-draw:400ms", "--dur-undo-back:200ms"):
        assert token in INDEX, token
    motion = _motion()
    for name in ("--dur-hub-in", "--dur-follow", "--dur-regroup", "--dur-flood", "--dur-page-sheet-in",
                 "--dur-folder-arc", "--dur-into-ring", "--dur-ring-draw"):
        assert '("' + name + '"' in motion, name
    assert '"ease-out"' not in motion


def test_follow_is_built_on_transforms_and_keeps_its_tick():
    button = _mfn("followButton")
    # No width animation: the paint layer scales, its neighbours glide.
    assert "width:" not in button and "{ width" not in button
    assert "scaleX(" in button and "DUR.follow" in button and "slideFrom(" in button
    assert "motion-tick path" in button
    # Following carries its tick in its own markup, so a redraw keeps it.
    assert 'following ? TICK + "Following"' in _mfn("followLabel")
    assert "Motion.followLabel(" in _fn("personRow")
    assert ".follow-btn.is-following" in INDEX
    # The line is for refusals only.
    assert 'toast("Following")' not in INDEX and 'toast("Unfollowed")' not in INDEX


def test_a_follow_redraws_the_profile_after_the_graph():
    assert "loadFriends().then(function(){ refreshPersonIfOpen(); })" in _fn("followPerson")
    assert "loadFriends().then(function(){ refreshPersonIfOpen(); })" in _fn("unfollowPerson")
    back = _fn("followBack")
    assert back.index("closeFollowerPopup();") < back.index("fetch(")
    assert "Motion.follow(who.user_id, false)" in back


def test_friends_regroup_by_flip_and_rolls_the_counts():
    draw = _fn("drawFriends")
    assert "Motion.regroup(before, el" in draw and "Motion.DUR.countRoll" in draw
    regroup = _mfn("regroup")
    assert "DUR.regroup" in regroup and "translateX(-8px)" in regroup and "EASE_IO" in regroup


def test_the_hub_is_reconciled_not_rebuilt():
    render = _fn("renderProfile")
    assert "patchParts(body, html" in render and "Motion.hubArrive(body)" in render
    assert 'getElementById("profileBody").innerHTML = html' not in render
    assert "hubArrived = true" in _mfn("hubArrive")
    # The stale number and ring.
    delete = _fn("deleteMyVibe")
    assert "leaveScale: 0.92" in delete and "rollDelay" in delete and "refreshHub()" in delete
    assert delete.index("profileVibes.splice(at, 1)") < delete.index("fetch(")
    assert "refreshHub();" in _fn("removeFromMyStory")


def test_the_shelves_switch_by_side():
    shelf = _fn("drawShelf")
    assert "Motion.panels(grid" in shelf and 'classList.toggle("mixes"' in shelf
    assert ".yf-tabs.mixes::after{ transform:translateX(100%); }" in INDEX
    assert 'behavior: Motion.on() ? "smooth" : "auto"' in _fn("scrollToGrid")


def test_a_friends_page_keeps_its_rows():
    render = _fn("renderFriendProfile")
    assert "if(published){\n      html += '<div class=\"yf-stats\">'" not in render
    assert "yf-int-room" in render and "patchParts(body, html, patchPersonPart)" in render
    assert "Motion.arriveList(" in _fn("showPersonShelf")


def test_settings_opens_whole_and_switches_in_place():
    assert "preloadSettings()" in _fn("refreshAuth")
    assert "renderSettings);" not in _fn("openSettings")
    assert "patchSettings(el, html)" in _fn("renderSettings")
    patch = _fn("patchSettings")
    assert "settingsPressing" in patch and "settingsOwed = html" in patch
    switch = _fn("settingsSwitch")
    assert 'role="switch"' in switch and "mix-switch" in switch and "set-go" not in switch
    for label in ("Dark mode", "Keep the voice's pitch", "Big stories", "Private search", "Private mode"):
        assert 'settingsSwitch("' + label in INDEX, label
    assert "transition-duration:var(--dur-switch)" in INDEX


def test_saved_rows_leave_with_undo():
    remove = _fn("removeSaved")
    assert '"Undo"' in remove and "done: function()" in remove and "Motion.removeRow(row" in remove
    assert remove.index("snack(") < remove.index('method: "DELETE"')
    assert "loadSaved();\n        toast(\"Removed\")" not in remove
    assert '"Undo"' in _fn("unvibe")


def test_filing_flies_to_the_chip():
    file_ = _fn("fileInFolder")
    assert "Motion.toFolder(copy, chip" in file_ and '"up"' in file_
    arc = _mfn("toFolder")
    assert "DUR.folderArc" in arc and "1.15" in arc and "bump(chip)" in arc


def test_interests_flood_in_place():
    toggle = _fn("toggleIdentityInterest")
    assert "Motion.flood(chip" in toggle and "renderIdentityInterests();\n" not in toggle.split("if(!chip)")[1]
    flood = _mfn("flood")
    assert "circle(" in flood and "DUR.chipDrain" in flood and "DUR.checkAt" in flood
    assert ".yf-int-chips .yf-int.on{ background:none; font-weight:400; }" in INDEX


def test_editors_are_page_sheets():
    screen = _mfn("screen")
    assert 'how === "sheet-up"' in screen and 'how === "sheet-down"' in screen
    assert 'editing ? "sheet-up" : undefined' in _fn("openIdentity")
    assert '"sheet-down"' in _fn("closeIdentity") and '"sheet-down"' in _fn("closeLocation")


def test_a_story_posts_into_the_ring():
    post = _fn("postNoteStory")
    assert "markMyStoryUp();" in post and "Motion.intoRing(canvas, myStoryFace()" in post
    ring = _mfn("intoRing")
    assert "DUR.intoRing" in ring and "DUR.ringDraw" in ring and "rotate(-90" in ring
    assert "Motion.growStory(" in _fn("openNoteComposer")
    assert "Motion.shrinkStory(ov, Motion.EASE.IN)" in _fn("closeVibeComposer")
    assert "Motion.drawerIn(panel)" in _fn("vcsTool")
