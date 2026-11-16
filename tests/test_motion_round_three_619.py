"""Motion, round three: the shared picks (PROBLEMS.md §619).

Read off the shipped page rather than a browser: the smoke run's "Round-three
motion holds nothing and leaves nothing behind" drives the same things live.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()


def _motion():
    start = INDEX.index("  var Motion = (function(){")
    return INDEX[start:INDEX.index("  Motion.watchOverlays();", start)]


def test_the_curves_are_the_skills_and_named_once():
    for token in ("--ease-out:cubic-bezier(.32,.72,0,1);",
                  "--ease-in:cubic-bezier(.55,0,1,.45);",
                  "--ease-in-out:cubic-bezier(.77,0,.175,1);"):
        assert token in INDEX, token
    motion = _motion()
    # The script reads the tokens; it does not keep its own copy.
    assert 'token("--ease-out"' in motion and 'msToken("--dur-page"' in motion
    # No bare keyword curve is left in the module (tokens: A).
    assert '"ease-out"' not in motion and '"ease-in"' not in motion
    assert "cubic-bezier(.2,.8,.2,1)" not in motion


def test_every_spring_token_ends_at_rest_inside_the_ceiling():
    for name in ("--spring-page", "--spring-soft", "--spring-icon"):
        curve = re.search(name + r":linear\(([^)]*)\);", INDEX).group(1)
        points = [float(p) for p in curve.split(",")]
        assert points[0] == 0 and points[-1] == 1, name
        # Follow-through overshoots under 4% of its travel (the skill).
        assert max(points) < 1.04, name
    assert "--dur-spring:450ms" in INDEX


def test_sheets_and_modals_are_transitions_that_turn_round():
    assert "@keyframes mo-lift" not in INDEX and "@keyframes mo-in" not in INDEX
    assert ".sheet-overlay.closing > *{ transform:translateY(100%);" in INDEX
    assert ".modal-overlay.closing > *{ opacity:0; transform:scale(.97);" in INDEX
    assert "@starting-style{" in INDEX
    # The way out is inert while it plays.
    assert "el.inert = true; el._motionInert = true;" in _motion()


def test_reduced_motion_is_all_off_but_the_loaders_pulse():
    motion = _motion()
    # No crossfade under reduced motion any more: a screen change is instant.
    assert "if(!how || !from || from === to || !on()) return;" in motion
    assert "animation-duration:120ms" not in INDEX
    assert ".fam-loading-dots i{ transform:none; animation:famLdPulse" in INDEX
    assert "html.theme-switching *" in INDEX
    assert 'root.classList.add("theme-switching");' in INDEX


def test_the_burst_is_a_quiet_icon_swap_and_a_word():
    assert "motion-ring" not in INDEX and "motion-spark" not in INDEX
    assert "--dur-label:900ms" in INDEX


def test_progress_is_drawn_by_scale_never_width():
    assert ".nowbar-fill{ height:100%; width:100%;" in INDEX
    assert '"scaleX(" + Math.min(1, at / total) + ")"' in INDEX
    assert '$("tourFill").style.transform = "scaleX("' in INDEX
    assert "tourFill\").style.width" not in INDEX


def test_an_undo_line_stays_five_seconds():
    assert "var SNACK_ACTION_MS = 5000;" in INDEX


def test_the_press_and_the_intro_are_unchanged():
    motion = _motion()
    # press: C - today's squeeze, on the tokens.
    assert "Math.max(0.9, 1 - 8 / Math.max(r.width, r.height))" in motion
    assert "var over = 1 + (1 - s) * 0.3;" in motion
