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


def test_a_mix_card_is_option_a():
    """§544's row (picture, name, arrow) became option A, the stacked deck,
    at the owner's pick of six (§616): the cover with today's stack, the
    name on it, and today's headline under it. Both lists draw it."""
    card = _fn("mixCardAHTML")
    for need in ('class="mix-a-cover', 'class="mix-a-stack"', 'class="mix-a-name"',
                 "mixHeadlineHTML(m)", "m.cover_tiles"):
        assert need in card, need
    assert "mixCardAHTML(m, \"openMix('\" + m.id + \"')\")" in _fn("renderMixList")
    assert "mixCardAHTML(m, \"openMix('sample')\")" in _fn("sampleMixCardHTML")


def test_the_removed_lines_stay_removed():
    """§544's removals hold on the new card: no briefing count, no "daily",
    no public/private, no owner line."""
    card = _fn("mixCardAHTML") + _fn("mixHeadlineHTML")
    for gone in ("mix-meta", "mix-preview", "briefing", "daily", "public",
                 "private", "mix-by", "example"):
        assert gone not in card, gone
    assert "function jointLabel" not in INDEX


def test_search_results_keep_their_owner_line():
    """Somebody else's public mix still says whose it is."""
    assert 'mix-by' in INDEX and "ownerLabel(m.owner)" in INDEX
