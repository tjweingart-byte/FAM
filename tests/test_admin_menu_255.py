"""The admin page is pages behind a menu, not one long scroll (§255).

The button at the top left opens a drawer; every page in the drawer is a
page on the tracker, every page is reachable from the drawer, and the cards
that were on the long page are each on exactly one page.
"""
from __future__ import annotations

import re
from pathlib import Path

PAGE = (Path(__file__).resolve().parent.parent / "admin_ui" / "tracker.html").read_text()


def _pages():
    return re.findall(r'data-page="([a-z]+)"', PAGE)


def test_the_menu_button_opens_a_drawer():
    assert 'id="menuBtn"' in PAGE and 'aria-controls="drawer"' in PAGE
    assert '<nav class="drawer" id="drawer"' in PAGE


def test_every_page_is_on_the_menu_and_every_link_is_a_page():
    pages = _pages()
    links = re.findall(r'data-nav="([a-z]+)"', PAGE)
    assert len(pages) == len(set(pages))
    assert set(pages) == set(links)
    # The ones asked for, by name.
    for name in ("ask", "feedback", "services", "accounts", "names"):
        assert name in pages


def test_each_card_sits_on_one_page():
    order = [(m.start(), m.group(1)) for m in re.finditer(r'data-page="([a-z]+)"', PAGE)]
    def page_of(marker):
        at = PAGE.index(marker)
        return [name for start, name in order if start < at][-1]
    assert page_of('id="kpis"') == "overview"
    assert page_of('id="thread"') == page_of('id="stores"') == "ask"
    assert page_of('id="modList"') == "reports"
    assert page_of('id="inboxList"') == "feedback"
    assert page_of('id="providers"') == "services"
    assert page_of('id="recent"') == "accounts"
    assert page_of('id="voiceList"') == "voices"
    assert page_of('id="sayList"') == "names"


def test_only_the_first_page_shows_before_a_choice():
    for m in re.finditer(r'<div class="page" data-page="([a-z]+)"[^>]*>', PAGE):
        assert (" hidden" in m.group(0)) == (m.group(1) != "overview")
