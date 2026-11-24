"""A mix card in the mixes list (§544; option A since §616).

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


def test_a_mix_card_is_the_mocks_row():
    """§544's row (picture, name, arrow) became option A, the stacked deck
    (§616), and then the row of the owner's 11.24 mock (§688): the picture,
    the name, the count, the topics and a play button. Both lists draw it."""
    card = _fn("playlistCardHTML")
    for need in ('class="pl-art', 'class="pl-name"', 'class="pl-meta"',
                 'class="pl-topics"', 'class="pl-play"', "coverOf(m)"):
        assert need in card, need
    assert "mixes.map(playlistCardHTML)" in _fn("renderMixList")
    assert "playlistCardHTML(m)" in _fn("sampleMixCardHTML")
    assert "function mixCardAHTML" not in INDEX


def test_what_the_mock_brought_back_and_what_stays_off():
    """§688 reverses two of §544's removals because the mock draws them -
    the briefing count with "daily", and the topics. The rest stay off the
    card: public/private and whose it is are on the playlist's own page."""
    card = _fn("playlistCardHTML")
    assert "briefing" in card and "daily" in card
    for gone in ("mix-preview", "public", "private", "mix-by"):
        assert gone not in card, gone
    assert "function jointLabel" not in INDEX


def test_search_results_keep_their_owner_line():
    """Somebody else's public mix still says whose it is."""
    assert 'mix-by' in INDEX and "ownerLabel(m.owner)" in INDEX
