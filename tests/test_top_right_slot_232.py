"""One top-right slot on every tab; a smaller exploreFAM pill; no seconds on
the loading screen (PROBLEMS.md §232)."""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()


def _css(selector):
    return INDEX.split("  " + selector + "{", 1)[1].split("}", 1)[0]


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


def _section(screen):
    start = INDEX.index('<section class="screen" id="screen-' + screen + '">')
    return INDEX[start:INDEX.index("</section>", start)]


def test_the_two_circles_wear_one_ring():
    # The second circle was myFAM's (+), then the "Create new mix" pill
    # (§498), which is a dashed row under the cards since §691. The circle
    # top right of a playlist's page (its sliders) wears the same ring.
    ring = "border:1px solid var(--border-strong)"
    assert ring in _css(".myfam-head-btn") and ring in _css(".pl-tune")


def test_myfams_header_is_fixed_like_dailyfams():
    # DailyFAM's bookshelf moved to search (10.12 packet #3); its header
    # is still fixed. myFAM's (+) is the row under its cards since §691.
    for screen in ("myfam", "playfam"):
        page = _section(screen)
        head = page.index('class="myfam-header myfam-header-fixed"')
        assert head < page.index('<div class="scroll"'), screen
    page = _section("playfam")
    head = page.index('class="myfam-header myfam-header-fixed"')
    assert head < page.index('<div class="scroll"') < page.index('id="newMixBtn"')


def test_the_loading_screen_counts_no_seconds():
    body = _fn("advanceGenSteps")
    assert "node.textContent = genStatusNote;" in body
    assert "now - st." not in body.split("node = document", 1)[1]
    assert '+ "s"' not in body
