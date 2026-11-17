"""Motion, round three: search's picks (PROBLEMS.md §643).

Read off the shipped page rather than a browser: the smoke run's "search's
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


def _mfn(name):
    motion = _motion()
    start = motion.index("    function " + name + "(")
    return motion[start:motion.index("\n    }\n", start)]


def test_every_new_time_is_a_token_read_by_motion():
    for token in ("--dur-focus:120ms", "--dur-loader-fade:200ms", "--dur-mark-glide:320ms",
                  "--dur-row-in:240ms", "--dur-row-stagger:40ms", "--dur-loader-leave:160ms",
                  "--dur-step-tick:220ms", "--dur-step-rail:260ms", "--dur-beat:2400ms",
                  "--dur-beat-ring-at:920ms", "--dur-iris:340ms", "--dur-list-stagger:40ms",
                  "--dur-wipe:320ms", "--ease-wipe:cubic-bezier(.4,0,.2,1)", "--dur-hand:420ms",
                  "--dur-hop:240ms", "--dur-voice-in:300ms", "--dur-voice-out:240ms",
                  "--dur-drain:260ms", "--dur-ripple:420ms", "--dur-read-bar:1200ms"):
        assert token in INDEX, token
    motion = _motion()
    for name in ("--dur-loader-fade", "--dur-iris", "--dur-beat", "--dur-wipe", "--ease-wipe",
                 "--dur-ripple", "--dur-drain", "--dur-voice-in"):
        assert '("' + name + '"' in motion, name


def test_the_five_steps_keep_their_timing():
    # rule:search-no-cover: restyled, never shortened, hidden or faked.
    for line in ("var GEN_STEP_MIN_MS = 2000;", "var GEN_STEP_FIRST_MIN_MS = 500;",
                 "var GEN_STEP_LAST_BEAT_MS = 400;", "var GEN_MIN_VISIBLE_MS = 450;"):
        assert line in INDEX, line
    # A check is drawn inside the last beat: 80 + 220 ms for the tick.
    assert "transition:stroke-dashoffset var(--dur-step-tick) var(--ease-out) var(--dur-step-tick-at)" in INDEX
    # No overshoot and no box-shadow ring.
    assert "cubic-bezier(.3,1.6,.5,1)" not in INDEX
    assert "@keyframes famStepPulse" not in INDEX


def test_a_step_is_a_thread_and_its_ring_beats_with_the_chevrons():
    steps = INDEX.split('<ol class="fam-steps"', 1)[1].split("</ol>", 1)[0]
    assert steps.count('pathLength="1"') == 5
    assert steps.count('class="fam-step-ring"') == 5
    assert steps.count('class="fam-step-rail"') == 4      # none after the last
    ring = _mfn("stepRing")
    assert "chev.getAnimations()" in ring and "iterations: Infinity" in ring
    assert "Motion.stepRing(el);" in _fn("paintGenSteps")
    # Reduced motion: still chevrons on search's loader, the ring pulses.
    assert ".fam-loading.stepped .fam-loading-mark .chev{ animation:none; }" in INDEX
    assert "animation:famRingPulse" in INDEX


def test_the_loader_opens_on_the_press():
    run = _fn("runSearch")
    assert run.index("openGenOverlayNow()") < run.index("fixed.then(")
    assert "if(seq !== genCancelSeq) return;" in run
    gen = _fn("generate")
    assert "var early = genEarly;" in gen and "Motion.loaderIn(overlay" in gen
    deeper = _fn("confirmGoDeeper")
    assert deeper.index("openGenOverlayNow()") < deeper.index("fixed.then(")


def test_the_loader_leaves_as_a_copy_and_opens_onto_the_player():
    cancel = _fn("cancelGeneration")
    assert cancel.index("Motion.loaderLeave(") < cancel.index("clearGenOverlay()")
    assert "restoreAskedSearch()" in cancel
    assert "Motion.loaderLeave(" in _fn("onGenerationFailed")
    assert 'if(loader.classList.contains("stepped")){ iris(loader); return; }' in _motion()
    iris = _mfn("iris")
    assert "path(evenodd" in iris and "DUR.iris" in iris
    # The player does not push under it.
    assert 'if(how === "player-open" && searchLoader()){ swiped = null; return; }' in _motion()


def test_go_deeper_sends_the_line_that_was_seen():
    show = _fn("showThreadSuggestion")
    assert "goDeeperChipHeld()" in show and "Motion.wipeIn(" in show
    use = _fn("useThreadSuggestion")
    assert "goDeeperHold.pressed || goDeeperShown" in use
    assert "goDeeperSuggestionText()" not in use
    assert 'chip.addEventListener("pointerdown"' in INDEX
    assert "var GO_DEEPER_HOLD_MS = 300;" in INDEX


def test_the_mic_and_go_share_one_slot():
    bar = INDEX.split('<span class="bar-slot" id="barSlot">', 1)[1].split("</span>", 1)[0]
    assert 'id="voiceMicBtn"' in bar and 'id="searchGoBtn"' in bar
    assert 'id="searchGoBtn" hidden' not in INDEX
    assert 'slot.classList.toggle("typing"' in _fn("paintSearchBar")
    assert ".fam-input-box:focus-within{ border-color:var(--copper); }" in INDEX


def test_voice_search_grows_from_the_mic_and_ripples_per_word():
    assert "voiceLevelLoop" not in INDEX and "--lvl" not in INDEX
    assert 'class="vs-waves"' not in INDEX
    assert "Motion.voiceOpen(box" in _fn("openVoiceSearch")
    assert "Motion.voiceClose(box)" in _fn("closeVoiceSearch")
    assert "Motion.ripple(" in _fn("voiceHeard")
    send = _fn("sendVoiceSearch")
    assert send.index("runSearch()") < send.index("closeVoiceSearch(")
    assert ".fam-loading.over-voice{ z-index:460; }" in INDEX


def test_search_now_drains_back():
    stop = _fn("stopVoiceCountdown")
    assert 'fill.style.transform = "scaleX(0)";' in stop and "Motion.drain(fill, at)" in stop
    assert 'scaleX(1)" : "scaleX(0)"' not in stop


def test_the_bubbles_act_and_never_print():
    assert 'class="ack-hand" d="M12 9v4l2.5 2.5"' in INDEX
    assert INDEX.count('class="ack-bar"') == 5
    assert 'if(changed) ackSearchBubble("lengthBubble");' in INDEX
    assert 'ackSearchBubble("voiceBubble")' in INDEX


def test_lists_arrive_once_and_searching_waits():
    trend = _fn("paintTrendSearches")
    assert "trendShownQ[q]" in trend and "Motion.arriveList(fresh)" in trend
    run = _fn("runMyFamSearch")
    assert "Motion.DUR.spinnerAfter" in run and 'body.classList.add("is-searching")' in run


def test_which_one_swaps_in_one_cell_and_the_reading_bar_moves():
    assert '<div class="reading-own-cell">' in INDEX
    assert "Motion.ownSwap(pill, row)" in _fn("openReadingOwn")
    assert "restoreAskedSearch()" in _fn("answerReading")
    rule = INDEX.split(".attach-chip.working::after{", 1)[1].split("}", 1)[0]
    assert "animation:attachReading var(--dur-read-bar) linear infinite" in rule
    # A progress indicator: no reduced-motion rule stops it.
    assert not re.search(r"reduce\)\{[^@]*attach-chip\.working::after\{ animation:none", INDEX)


def test_the_private_lock_swaps_on_the_tap_only():
    assert "paintPrivateSearch(on, true);" in _fn("setPrivateSearch")
    paint = _fn("paintPrivateSearch")
    assert "if(bubble && tapped && was !== privateSearch)" in paint and "Motion.swapShown(" in paint
