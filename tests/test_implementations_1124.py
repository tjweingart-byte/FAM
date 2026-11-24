"""The owner's 11.24 packet (PROBLEMS.md §690), read from the sources.

1. The player's top-left button is the back chevrons (`test_packet_1012`).
2. Private search says what it is before it is first turned on.
3. A tap on the mini player opens the full player.
4. The like and VIBE! buttons on a friend's story take a tap anywhere in
   their box on the iPhone.
5. One subject's edition never plays for another's (`test_dailyfam_follow`).

The browser run presses 2 and 3 (`tools/smoke_preview.py`); the Swift here
is read, not built - nothing in this repository can run the iPhone app.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
IOS = ROOT / "ios" / "FAM" / "FAM"


def _swift(name: str) -> str:
    return (IOS / name).read_text(encoding="utf-8")


def _fn(name: str) -> str:
    start = INDEX.index("function " + name + "(")
    return INDEX[start:INDEX.index("\n  }\n", start)]


# --- 2. Private search explains itself ---------------------------------------

def test_the_lock_carries_its_word():
    """"Add the word Private on the lock too" (the owner, 11.24), reversing
    the bare lock of the 11.16 notes; the page is still not repainted."""
    bubble = re.search(r'<button[^>]*id="privateBubble".*?</button>', INDEX, re.S).group(0)
    assert bubble.endswith("</svg>Private</button>")
    assert 'onclick="tapPrivateLock()"' in bubble
    lock = _swift("Views/SearchView.swift").split("private var privateLock", 1)[1]
    lock = lock.split("private func tapPrivateLock", 1)[0]
    assert 'Text("Private")' in lock and "Capsule()" in lock


def test_the_first_tap_explains_before_it_turns_anything_on():
    tap = _fn("tapPrivateLock")
    # On already, or explained once on this device: one tap, either way.
    assert "if(privateSearch || privateExplained()){ setPrivateSearch(!privateSearch); return; }" in tap
    assert "showActionSheet(" in tap
    assert "Turn on Private search" in tap
    # Turned on only by the sheet's own row, which is also what remembers.
    row = tap.split("Turn on Private search", 1)[1]
    assert "localStorage.setItem(PRIVATE_EXPLAINED_KEY" in row and "setPrivateSearch(true)" in row
    for said in ("history", "Friends and other listeners never see it", "recommends"):
        assert said in INDEX.split("var PRIVATE_EXPLAIN_LINES", 1)[1][:400]
    assert "until you tap the lock again" in tap


def test_the_iphone_explains_it_the_same_way():
    view = _swift("Views/SearchView.swift")
    lock = view.split("private var privateLock", 1)[1].split("private func tapPrivateLock", 1)[0]
    assert "Button { tapPrivateLock() }" in lock
    tap = view.split("private func tapPrivateLock", 1)[1].split("// MARK:", 1)[0]
    assert "if on || UserDefaults.standard.bool(forKey: Self.privateExplainedKey)" in tap
    assert "explainPrivate = true" in tap and "setPrivateSearch(true)" not in tap
    assert 'privateExplainedKey = "fam.privateExplained"' in tap
    assert '"fam.privateExplained"' in INDEX
    # Turned on, and remembered, only by the sheet's own row.
    shown = view.split(".famSheet(isPresented: $explainPrivate)", 1)[1].split(".famThemed()", 1)[0]
    assert "UserDefaults.standard.set(true, forKey: Self.privateExplainedKey)" in shown
    assert "model.setPrivateSearch(true)" in shown
    sheet = view.split("struct PrivateExplainSheet", 1)[1]
    assert 'Text("Turn on Private search")' in sheet and "SheetCancelButton { dismiss() }" in sheet
    # The web's words, line for line - never the action sheet's small
    # upper-case header, which is what a long explanation was first put in.
    web = INDEX.split("var PRIVATE_EXPLAIN_LINES", 1)[1].split("];", 1)[0]
    for line in re.findall(r'"([^"]+)"', sheet.split("static let lines", 1)[1].split("]", 1)[0]):
        assert line.replace("\\u{2019}", "\\u2019") in web, line
    assert "While the lock is closed, anything you search or Go Deeper on:" in sheet
    assert "While the lock is closed, anything you search or Go Deeper on:" in INDEX
    assert "It stays on until you tap the lock again." in sheet
    assert "monoLabel" not in sheet and "SheetGroupLabel" not in sheet


# --- 3. the mini player opens the player --------------------------------------

def test_a_tap_on_the_mini_player_opens_the_player():
    bar = re.search(r'<div class="nowbar" id="nowBar"[^>]*>', INDEX).group(0)
    assert 'onclick="openNowPlaying()"' in bar
    assert 'navigate("player")' in _fn("openNowPlaying")
    # Its own buttons do not open it as well.
    assert 'id="nowPlay" onclick="event.stopPropagation();' in INDEX
    mini = _swift("Views/PlayerView.swift").split("struct MiniPlayer", 1)[1]
    tap, squeeze = mini.index(".onTapGesture {"), mini.index(".tapSqueeze()")
    # The tap first, the squeeze beside it - never the other way about.
    assert tap < squeeze
    assert "model.showPlayer = true" in mini[tap:squeeze]


# --- 4. the story's like and VIBE! --------------------------------------------

def test_the_storys_buttons_take_a_tap_anywhere_in_their_box():
    row = _swift("Views/Stories.swift").split("private var replyRow", 1)[1].split("// MARK:", 1)[0]
    for call in ("Button { like() }", "Button { reshare() }", "Button(action: sendReply)"):
        label = row.split(call, 1)[1].split(".buttonStyle(.press)", 1)[0]
        assert ".contentShape(Rectangle())" in label, call


def test_every_pressed_button_is_hit_across_its_box():
    """The cause was general: an outlined icon in a frame is hit only on its
    line. `.press` gives every button its whole box - the button style, not
    `PressedLabel`, which also squeezes cards that are not buttons."""
    motion = _swift("Motion.swift")
    style = motion.split("struct PressButtonStyle", 1)[1].split("extension ButtonStyle", 1)[0]
    assert ".contentShape(Rectangle())" in style
    label = motion.split("struct PressedLabel", 1)[1].split("struct TapSqueeze", 1)[0]
    assert ".contentShape(" not in label
