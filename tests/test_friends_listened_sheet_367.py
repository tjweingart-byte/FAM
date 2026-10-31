"""§367: tapping a card's faces opens who listened - every friend, newest
first, each row their profile - and never plays the card."""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as fh:
    INDEX = fh.read()


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


def test_the_faces_are_a_button_that_does_not_play_the_card():
    chip = _fn("seedFriendsHtml")
    assert 'role="button"' in chip
    assert "event.stopPropagation(); openFriendsListened(" in chip


def test_the_sheet_lists_every_friend_and_opens_their_profile():
    sheet = _fn("openFriendsListened")
    # Every face, not the card's three.
    assert ".slice(" not in sheet
    assert "friendFaceHtml(f)" in sheet and "openListenedFriend(" in sheet
    assert 'id="friendsListenedOverlay"' in INDEX and 'id="flList"' in INDEX
    go = _fn("openListenedFriend")
    assert "closeFriendsListened()" in go and "openPersonProfile(handle)" in go
