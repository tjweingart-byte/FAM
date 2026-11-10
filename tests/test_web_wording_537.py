"""Two wording fixes on the web page (PROBLEMS.md §537, decision 7).

A count says "1 friend" and "1 follower", never "1 friends"; and the View
more Refresh button's label names the number it deals, pinned to the variable
that deals it (`SECTION_PAGE`, §363) so the two cannot drift apart again.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def test_refresh_says_how_many_it_deals():
    page = int(re.search(r"var SECTION_PAGE = (\d+);", PAGE).group(1))
    label = re.search(r'id="sectionRefresh"[^>]*aria-label="([^"]+)"', PAGE).group(1)
    assert label == f"Show {page} different episodes"
    assert "eight different" not in PAGE


def test_friends_counts_are_singular_at_one():
    body = PAGE[PAGE.index("function renderFriends()"):]
    body = body[:body.index("</div>';") + 8]
    assert "'</b> friends" not in body and "'</b> followers" not in body
    assert 'plural(c.friends || 0, "friend")' in body
    assert 'plural(c.followers || 0, "follower")' in body


def test_no_count_on_the_page_prints_a_bare_plural():
    """Anywhere a number is glued to "friends" or "followers" by hand."""
    bare = re.findall(r"""\+ ['"]</b> (?:friends|followers)\b""", PAGE)
    assert not bare, bare
