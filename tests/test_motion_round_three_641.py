"""Motion, round three: dailyFAM's picks (PROBLEMS.md §641).

Read off the shipped page rather than a browser: the smoke run's "dailyFAM's
round-three motion holds nothing and leaves nothing behind" drives the same
things live.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()


def _motion():
    start = INDEX.index("  var Motion = (function(){")
    return INDEX[start:INDEX.index("  Motion.watchOverlays();", start)]


def _fn(name):
    start = INDEX.index("  function " + name + "(")
    return INDEX[start:INDEX.index("\n  }\n", start)]


def test_every_new_time_is_a_token_read_by_motion():
    for token in ("--dur-loader-in:300ms", "--dur-loader-out:340ms", "--dur-develop-ready:420ms",
                  "--dur-arrive:350ms", "--dur-stagger:30ms", "--dur-deal-out:160ms",
                  "--dur-spinner-after:300ms", "--dur-spinner-min:500ms", "--dur-digit:320ms",
                  "--dur-mark:600ms", "--dur-story-hide:150ms", "--dur-story-show:200ms",
                  "--dur-story-spring:400ms", "--dur-reply:220ms", "--dur-fold:220ms",
                  "--dur-gap:240ms"):
        assert token in INDEX, token
    motion = _motion()
    for name in ("--dur-loader-out", "--dur-arrive", "--dur-fold", "--dur-story-spring"):
        assert 'msToken("' + name + '"' in motion, name
    # Nothing new moves for longer than the ceiling.
    for name, ms in re.findall(r"--dur-([a-z-]+):(\d+)ms", INDEX):
        # Search (§643): the loader's beat and the ring inside it, the
        # reduced-motion pulse and the reading line are loading indicators'
        # loops; "ack" is how long a colour holds. Messages (§644): the
        # typing dots' loop is an activity indicator, and "Sending…" waits
        # a second before it says anything.
        if name in ("develop", "label", "faces", "mark", "sheen", "sheen-delay", "spinner-min",
                    "beat", "beat-ring", "ring-pulse", "read-bar", "ack", "typing", "sending-after") \
                or name.endswith("-at"):   # a delay before a movement (§642), not the movement
            continue    # a wait's look, a word on screen, or a hold - not a movement
        assert int(ms) <= 450, (name, ms)
    assert '"ease-out"' not in motion and '"ease-in"' not in motion


def test_one_picture_from_the_tile_to_the_player():
    motion = _motion()
    # The player opens under DailyFAM's loader with no flight of its own...
    assert 'if(how === "player-open" && dailyLoader()) return;' in motion
    # ...and the loader's picture is the one that flies, when it hands over.
    assert "Motion.handOff(famLoadingEl());" in _fn("handOffGenOverlay")
    assert "handOffGenOverlay" in _fn("finishGenOverlay")
    started = _fn("onGenerationStarted")
    assert started.index('navigate("player")') < started.rindex("finishGenOverlay()")
    assert 'if(kind === "daily") Motion.intoLoader(el);' in _fn("paintLoadingVariant")


def test_a_ready_tile_develops_before_the_loader_can_leave():
    assert "(tile.cached ? ' ready' : '')" in _fn("paintLoadingVariant")
    assert ".fam-dev.develop.ready .fam-dev-pic{ animation-duration:var(--dur-develop-ready);" in INDEX
    # The scale is gone; the sheen is time passing, so linear.
    assert "@keyframes famDevelop{ from{ filter:blur(16px) grayscale(1) brightness(1.4); } to{ filter:none; } }" in INDEX
    assert "animation:famDevSheen var(--dur-sheen) var(--dur-sheen-delay) linear infinite" in INDEX
    # A ready tile's friends are already in place.
    assert ".fam-dev.ready .fam-dev-faces .src-mark, .fam-dev.ready + .fam-dev-names{ animation:none; }" in INDEX


def test_the_rows_above_the_rails_are_drawn_as_last_seen():
    feed = _fn("loadMyFamFeed")
    assert feed.index("paintRememberedLateRows()") < feed.index("loadMyFamCircle()")
    assert 'rememberLateRow("famLastPickUp"' in _fn("loadGoDeeper")
    assert 'rememberLateRow("famLastCircle"' in _fn("loadMyFamCircle")
    forget = _fn("forgetOfflineCopies")
    assert 'removeItem("famLastPickUp")' in forget and 'removeItem("famLastCircle")' in forget
    # Kept per account.
    assert "kept.who === (AUTH.user_id" in _fn("rememberedLateRow")


def test_identical_redraws_leave_the_page_alone():
    assert "if(html !== feedHtmlDrawn" in _fn("renderMyFamFeed")
    assert "el._drawn === html" in _fn("paintMyFamCircle")
    assert "sign === goDeeperDrawn" in _fn("renderGoDeeper")


def test_a_save_lights_on_the_tap_and_is_put_back_if_refused():
    save = _fn("saveTile")
    assert save.index("paintTileSaves()") < save.index("var req = on")
    assert 'Motion.celebrate(\'[data-tile-save="\' + topicId + \'"]\', "SAVED", true)' in save
    assert 'class="ts-on"' in _fn("seedActsHtml") and 'class="ts-off"' in _fn("seedActsHtml")


def test_a_removal_can_be_undone_and_is_sent_when_the_line_goes():
    assert "var commit = action && action.done;" in _fn("snack")
    assert "flushSnackCommit()" in _fn("hideSnack")
    nope = _fn("tileNotInterested")
    assert nope.index('label: "Undo"') < nope.index('recordFamEvent("not_interested"')
    assert "done: function(){" in nope
    dismiss = _fn("dismissGoDeeper")
    assert 'label: "Undo"' in dismiss and "loadGoDeeper" not in dismiss
    assert ".gd-x::after{ content:\"\"; position:absolute; top:-4px; right:-4px; width:44px; height:44px; }" in INDEX


def test_view_more_opens_on_the_rails_tiles_and_refresh_deals():
    opened = _fn("openSection")
    assert opened.index("preview: true") < opened.index('navigate("section")')
    assert 'loadSectionPage("deal")' in _fn("refreshSection")
    page = _fn("loadSectionPage")
    assert "Motion.DUR.spinnerAfter" in page and "Motion.DUR.spinnerMin" in page
    assert "Motion.deal(" in page
    # Trending's grouped grid arrives and lights its saves too.
    assert INDEX.count("arriving();") >= 3


def test_stories_fill_by_scale_and_hold_quickly():
    assert "@keyframes storyFill{ from{ transform:scaleX(0); } to{ transform:scaleX(1); } }" in INDEX
    assert ".story.held .story-head, .story.held .story-card, .story.held .story-bars{ opacity:0;\n    transition:opacity var(--dur-story-hide) var(--ease-in); }" in INDEX


def test_a_story_drags_down_to_close_and_turns_under_the_finger():
    assert "var STORY_CLOSE_PX = 80, STORY_FLICK = 0.1;" in INDEX
    motion = _motion()
    assert "function cubeDrag(" in motion and "function cubeLetGo(" in motion
    assert "oldShade: 0.3 * p" in motion
    assert "Motion.ringFade(" in _fn("closeStory")
    assert "Motion.sendWords(input, text);" in _fn("sendStoryReply")
    assert "input.value = text" in _fn("sendStoryReply")
