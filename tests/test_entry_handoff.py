"""The FAM intro (PROBLEMS.md §196, §201): the wordmark's F and M fold into
the double chevron, the chevron flashes yellow as if charged, and the page
slides off to the left - every time the app opens, and when finishing
sign-in or sign-up hands over to DailyFAM."""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


def test_overlay_is_drawn_once_with_the_wordmark():
    assert INDEX.count('id="famHandoff"') == 1
    block = INDEX.split('id="famHandoff"', 1)[1].split("<!-- ============ LOADING", 1)[0]
    for part in ('class="wm-glyph f"', 'class="wm-a"', 'class="wm-glyph m"'):
        assert part in block


def test_overlay_never_takes_a_tap():
    css = INDEX.split(".fam-handoff{", 1)[1].split("}", 1)[0]
    assert "pointer-events:none" in css


def test_only_an_account_gets_the_handoff_and_the_screen_switches_first():
    body = _fn("finishEntry")
    assert 'beginEntryHandoff(document.querySelector(".screen.active"))' in body
    assert "AUTH.authenticated" in body.split("beginEntryHandoff(", 1)[0]
    # Captured before the switch, played after it: the page underneath is
    # DailyFAM from the first frame, so nothing waits on the animation.
    assert body.index("beginEntryHandoff(") < body.index("openMyFamTab()") \
        < body.index("handoff()")


def test_the_copy_of_the_page_carries_no_ids():
    body = _fn("beginEntryHandoff")
    assert 'copy.removeAttribute("id")' in body
    assert 'querySelectorAll("[id]")' in body
    # Not a screen: `.screen.active` and showScreen's sweep never find it.
    assert 'copy.className = "fam-handoff-copy"' in body
    assert "if(!wrap.hidden) return null" in body
    assert "prefers-reduced-motion: reduce" in body


def test_the_four_beats():
    body = _fn("playEntryHandoff")
    assert "scale(0.4)" in body              # F and M fold into the chevron
    assert "rotate(" not in body             # it no longer turns (§201)
    assert "--deeper" in body                # it flashes yellow, charged
    assert "drop-shadow" not in body         # with no glow around it
    assert "FAM_INTRO_CHARGE_MS" in body     # where a sound effect would start
    assert "translateX(-100%)" in body       # the page slides off to the left
    assert 'wrap.hidden = true' in body      # and the overlay is gone after


def test_it_plays_every_time_the_app_opens():
    body = _fn("bootToFirstScreen")
    # Set up over the splash before the first screen is chosen, played after
    # it is drawn: the app underneath never waits on the picture.
    assert body.index("beginEntryHandoff()") < body.index("hideSplash(!!intro)") \
        < body.index("routeFirstScreen()") < body.index("intro()")
    # The opening panel is blank - there is no page to copy yet.
    assert "if(from){" in _fn("beginEntryHandoff")


def test_the_intro_logo_sits_where_the_splash_draws_it():
    css = INDEX.split(".fam-handoff .fam-logo{", 1)[1].split("}", 1)[0]
    assert "top:50%" in css and "margin-top:-0.55em" in css
