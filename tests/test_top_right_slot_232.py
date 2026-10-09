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


def test_search_pill_sits_in_the_headers_slot():
    """Search's exploreFAM pill is where DailyFAM's and myFAM's circles are:
    the fixed header's top padding and side inset, at the circles' height."""
    header = _css(".myfam-header-fixed")
    top, side = re.search(r"padding:(\d+)px (\d+)px", header).groups()
    slot = _css(".home-explore")
    assert "top:%spx" % top in slot and "right:%spx" % side in slot
    assert "height:34px" in _css(".home-explore.xfam-pill")
    assert "height:34px" in _css(".myfam-head-btn")
    assert "width:34px; height:34px" in _css(".new-mix-plus")


def test_the_two_circles_wear_one_ring():
    ring = "border:1px solid var(--border-strong)"
    assert ring in _css(".myfam-head-btn") and ring in _css(".new-mix-plus")


def test_myfams_header_is_fixed_like_dailyfams():
    for screen, button in (("myfam", 'id="myfamSearchBtn"'),
                           ("playfam", 'id="newMixBtn"')):
        page = _section(screen)
        head = page.index('class="myfam-header myfam-header-fixed"')
        assert head < page.index(button) < page.index('<div class="scroll"'), screen


def test_the_pill_beside_made_for_you_is_small():
    pill = _css(".xfam-pill")
    assert "height:24px" in pill and "font-size:11px" in pill


def test_the_loading_screen_counts_no_seconds():
    body = _fn("advanceGenSteps")
    assert "node.textContent = genStatusNote;" in body
    assert "now - st." not in body.split("node = document", 1)[1]
    assert '+ "s"' not in body
