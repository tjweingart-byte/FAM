"""A mix card in the mixes list is its picture, its name and an arrow (§537).

The owner's direction (11.10): "we just want the thumbnail of the playlist,
the title of the playlist, and an arrow pointing right" - the briefing count,
"daily" and public/private were named for removal. Read from the page's own
source, like the other interface tests, because the failure is a row that
quietly grows a line back.
"""
from __future__ import annotations

import pathlib

INDEX = (pathlib.Path(__file__).resolve().parent.parent / "static" / "index.html").read_text()


def _fn(name: str) -> str:
    start = INDEX.index(f"function {name}(")
    return INDEX[start:INDEX.index("\n  }\n", start)]


def _card(name: str) -> str:
    body = _fn(name)
    return body[body.index("'<div class=\"mix-card\""):]


def test_a_mix_card_is_picture_name_and_arrow():
    for name in ("renderMixList", "sampleMixCardHTML"):
        card = _card(name)
        assert 'class="mix-folder' in card, name
        assert 'class="mix-name"' in card, name
        assert 'class="mix-chev"' in card and "M9 6l6 6-6 6" in card, name


def test_the_removed_lines_stay_removed():
    for name in ("renderMixList", "sampleMixCardHTML"):
        card = _card(name)
        for gone in ("mix-meta", "mix-preview", "briefing", "daily", "public",
                     "private", "mix-by", "example"):
            assert gone not in card, (name, gone)
    assert "function jointLabel" not in INDEX


def test_search_results_keep_their_owner_line():
    """Somebody else's public mix still says whose it is."""
    assert 'mix-by' in INDEX and "ownerLabel(m.owner)" in INDEX
