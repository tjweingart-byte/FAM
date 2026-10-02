"""The sign-in hand-off (PROBLEMS.md §196): finishing sign-in or sign-up
folds the FAM wordmark into the double chevron, turns it right, and slides
the page off to the left onto DailyFAM."""

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
    assert "AUTH.authenticated ? beginEntryHandoff() : null" in body
    # Captured before the switch, played after it: the page underneath is
    # DailyFAM from the first frame, so nothing waits on the animation.
    assert body.index("beginEntryHandoff()") < body.index("openMyFamTab()") \
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
    assert "rotate(90deg)" in body           # the chevron turns right
    assert "translateX(-100%)" in body       # the page slides off to the left
    assert 'wrap.hidden = true' in body      # and the overlay is gone after
