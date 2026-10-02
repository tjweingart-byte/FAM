"""The 10.2 packet: the interface half (the waitlist half is in
test_waitlist.py)."""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    return HTML.split(f"function {name}(", 1)[1].split("\n  }\n", 1)[0]


def _player() -> str:
    return HTML[HTML.index('id="screen-player"'):HTML.index('id="screen-playall"')]


def test_five_trending_searches_and_smaller():
    assert "var TREND_SHOWN = 5;" in HTML
    assert "TREND_SEARCHES.slice(0, TREND_SHOWN)" in _fn("paintTrendSearches")
    chip = HTML.split("  .trend-chip{", 1)[1].split("}", 1)[0]
    assert "font-size:12px" in chip and "min-height:32px" in chip


def test_go_deeper_is_at_the_top_and_nothing_says_now_playing():
    player = _player()
    top = player.split('<div class="player-top">', 1)[1].split('<div class="mini-stage"', 1)[0]
    assert ">GO DEEPER</button>" in top
    assert "Now playing" not in player.split("-->", 1)[1].replace('"Now playing" was', "")
    pill = HTML.split("  #screen-player .player-top .go-deeper-pill{", 1)[1].split("}", 1)[0]
    # A fifth shorter than the 12px-padded, 14px pill it was.
    assert "padding:8px 34px" in pill and "font-size:13px" in pill


def test_the_picture_is_its_own_shape_not_the_whole_screen():
    css = HTML.split("  #screen-player > .player-bg{", 1)[1].split("}", 1)[0]
    assert "aspect-ratio:4 / 3" in css and "inset:0" not in css


def test_share_vibe_and_save_stand_on_the_right_and_in_the_menu():
    side = _player().split('<div class="p-side" id="playerSide">', 1)[1].split("</div>", 1)[0]
    assert side.index("openShareModal()") < side.index('id="playerEcho"') < side.index('id="playerSave"')
    row2 = _player().split('<div class="pc-row2">', 1)[1].split("</div>\n        </div>", 1)[0]
    assert "playerEcho" not in row2 and "playerSave" not in row2
    menu = _fn("openPlayerMenu")
    assert "action: toggleEcho" in menu and "action: saveForLater" in menu


def test_the_mix_search_has_room_under_the_plus():
    assert "#screen-playfam .dm-search{ margin-top:18px; }" in HTML
