"""Motion, round three: Messages' picks (PROBLEMS.md §644).

Read off the shipped page rather than a browser: the smoke run's "Messages'
round-three motion holds nothing and leaves nothing behind" drives the same
things live.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()


def _motion():
    start = INDEX.index("  var Motion = (function(){")
    return INDEX[start:INDEX.index("  Motion.watchOverlays();", start)]


def _fn(name):
    start = INDEX.index("  function " + name + "(")
    return INDEX[start:INDEX.index("\n  }\n", start)]


def _mfn(name):
    motion = _motion()
    start = motion.index("    function " + name + "(")
    return motion[start:motion.index("\n    }\n", start)]


def test_every_pick_is_the_one_built():
    spec = json.loads((ROOT / "docs" / "motion" / "round-three" / "messages.json").read_text())
    picked = {a["id"]: a["picked"] for a in spec["animations"]}
    assert picked == {"chat-bubbles": "A", "typing-dots": "B", "send-confirm": "B", "chat-to-top": "A",
                      "unread-badge": "A", "read-clears": "B", "header-chips": "B", "chat-loading": "C",
                      "share-pick": "C", "vibe-reply-like": "C", "new-chat-pick": "B", "send-failed": "C",
                      "delete-chat": "B"}


def test_every_new_time_is_a_token_read_by_motion():
    for token in ("--dur-column:220ms", "--dur-column-stagger:40ms", "--dur-typing:1200ms",
                  "--dur-dots-grow:240ms", "--dur-dots-out:80ms", "--dur-sending-after:1000ms",
                  "--dur-line-out:120ms", "--dur-reorder:280ms", "--dur-row-new:240ms",
                  "--dur-badge-in:220ms", "--dur-badge-out:160ms", "--dur-chips-close:190ms",
                  "--dur-arc:360ms", "--dur-slot:200ms", "--dur-unpick:240ms", "--dur-expand:250ms",
                  "--dur-collapse:190ms", "--dur-row-out:180ms", "--dur-undo-in:220ms"):
        assert token in INDEX, token
    motion = _motion()
    for name in ("--dur-column", "--dur-dots-grow", "--dur-sending-after", "--dur-reorder",
                 "--dur-badge-in", "--dur-arc", "--dur-undo-in"):
        assert '("' + name + '"' in motion, name
    # The tail pop and the badge's pop and bump are gone (no overshoot, no
    # scale from 0); nothing bare is left in the module.
    assert "popMessages" not in INDEX
    assert "scale(1.04)" not in motion and "scale(1.25)" not in motion and "scale(1.3)" not in motion
    assert '"ease-out"' not in motion


def test_the_column_rises_and_the_scroll_is_kept():
    column = _mfn("column")
    assert "translateY(" in column and "DUR.columnStagger" in column and "Math.min(order.indexOf(mi), 5)" in column
    assert "scale" not in column and "opacity" not in column
    body = _fn("renderThreadBody")
    # Only from the bottom: anywhere else the reader's place is kept.
    assert "if(first || atBottom || o.bottom) pinThread(body);" in body
    assert "else body.scrollTop = oldTop;" in body
    assert "body.scrollTop = body.scrollHeight" not in body
    # A second message turns a rise round from where it is.
    assert "Motion.columnCarry(body)" in body
    # Measured as laid out, never by the rising rows' transforms.
    assert "last.offsetTop + last.offsetHeight" in _fn("threadHeight")
    # The key keeps the server's spacing, so an acknowledged send is not new.
    assert 'replace(/\\s+/g, " ")' in body


def test_their_message_grows_out_of_the_dots():
    grow = _mfn("dotsToMessage")
    assert "inset(" in grow and "EASE_IO" in grow and "DUR.dotsTextAt" in grow
    body = _fn("renderThreadBody")
    assert "Motion.dotsToMessage(grown, dots, dotsCopy)" in body
    # One element for the dots, never redrawn: it arrives and leaves.
    assert "typingRowHTML" not in INDEX
    assert "Motion.fadeCopy(row.firstChild, Motion.DUR.lineOut)" in _fn("paintTyping")
    assert "if(typingChanged && !added) paintTyping();" in _fn("pollThread")
    # An opacity wave, kept under reduced motion, mid-wave from frame one.
    assert "@keyframes typingDot{ 0%,60%,100%{ opacity:.35; } 30%{ opacity:1; } }" in INDEX
    assert ".msg-typing i{ animation:none" not in INDEX
    assert ".msg-typing i:nth-child(1){ animation-delay:-.3s; }" in INDEX


def test_sending_is_said_only_past_a_second_and_a_failed_send_stays():
    assert ".msg-row.pending .msg-bubble{ opacity:0.55; }" not in INDEX
    arm = _fn("armSending")
    assert "Motion.DUR.sendingAfter" in arm and "Motion.fadeUp(el, Motion.DUR.colour)" in arm
    assert "Motion.fadeCopy(el, Motion.DUR.lineOut)" in _fn("settleSending")
    post = _fn("postDraft")
    assert "draft.failed = true;" in post and "armSending();" in post
    # The box stays clear and nothing is taken off the screen (the reversal).
    send = _fn("sendThreadMessage")
    assert "input.value = val" not in send and "splice(" not in post
    assert "Not sent · Tap to retry" in _fn("renderThreadBody")
    assert "postDraft(m);" in _fn("retryThreadMessage")


def test_a_chat_glides_to_the_top_only_from_the_network():
    rows = _fn("renderThreadRows")
    assert 'data-thread="' in rows and "Motion.reorder(before" in rows
    assert "(o.fetched || o.undo) && Motion.on()" in rows
    # The search box's keys redraw in place.
    assert 'oninput="renderThreadRows()"' in INDEX
    # Never under a finger.
    assert "if(o.fetched && threadListPressed){ threadListOwed = true; return; }" in rows
    reorder = _mfn("reorder")
    assert "DUR.reorder" in reorder and "EASE_IO" in reorder and "translateY(-8px)" in reorder
    # A keyboard can open a chat.
    assert 'role="button" tabindex="0"' in rows


def test_the_count_rolls_and_a_change_out_of_sight_is_owed():
    badge = _mfn("badge")
    assert "b._owed = { shown: was, text: before }" in badge
    move = _mfn("badgeMove")
    assert "translateY(\" + (14 * dir) + \"px)" in move and "DUR.roll" in move
    assert "scale(.7)" in move and "DUR.badgeIn" in move and "DUR.badgeOut" in move
    # Played when the screen has landed, on a tab change too.
    assert _motion().count("owed(to);") == 2
    # The app's first paint is still; log-out clears it.
    assert "setUnreadDot(d.unread, true);" in _fn("startNotificationPolling")
    assert "paintMessageBadges(0, true);" in _fn("logOut")


def test_read_on_the_tap_and_the_inbox_in_hand():
    opened = _fn("openThreadWith")
    assert opened.index('navigate("thread")') < opened.index("markThreadRead(id);")
    assert "t.unread = 0;" in _fn("markThreadRead")
    # The inbox is read with the notifications' first call; chats are kept.
    assert "fetchInbox().catch(function(){})" in _fn("startNotificationPolling")
    assert "threadMemory[threadId] = {" in _fn("rememberThread")
    assert "if(mem){ pollThread(); startThreadPolling(); }" in opened
    for line in ("threadMemory = {};", "inboxLoaded = false;", "threadsGone = {};"):
        assert line in _fn("logOut"), line
    # "Loading" only past 300 ms, and then held 500 ms.
    late = _fn("lateLoader")
    assert "Motion.DUR.spinnerAfter" in late and "Motion.DUR.spinnerMin" in late
    assert "lateLoader(" in _fn("loadThread") and "lateLoader(" in _fn("renderThreadList")
    # A banner for another chat replaces the one open.
    assert "var inPlace = threadScreenOpen();" in opened
    assert "if(inPlace) Motion.fadeUp(body, Motion.DUR.colour);" in opened


def test_the_chips_keep_their_room():
    assert ".thread-chips-room{ display:grid; grid-template-rows:34px;" in INDEX
    assert ".yf-thread-head .yf-chips:empty{ display:none; }" not in INDEX
    assert 'id="threadChipsRoom"' in INDEX
    chips = _fn("loadThreadInterests")
    assert 'room.classList.add("closed")' in chips and "Motion.DUR.stagger" in chips


def test_delete_and_leave_go_with_undo_and_send_on_the_line_going():
    for name in ("confirmDeleteChat", "confirmLeaveGroup"):
        assert "showActionSheet" not in _fn(name), name
    gone = _fn("goneChat")
    assert 'label: "Undo"' in gone and "done: function(){" in gone
    assert gone.index("done: function(){") < gone.index('method: "DELETE"')
    # Block keeps its question.
    assert "showActionSheet" in _fn("blockPerson")
    # The row leaves once the list has landed, and is never drawn again.
    assert "Motion.landed(takeLeavingRow)" in _fn("renderThreadRows")
    assert "Motion.removeRow(row, redraw, \"#threadList .thread-row\", Motion.DUR.rowOut)" in _fn("takeLeavingRow")
    assert "!threadsGone[t[\"with\"]]" in _fn("fetchInbox")
    assert "flushSnackCommit();" in _fn("logOut")


def test_share_pick_turns_the_bottom_row():
    sheet = INDEX[INDEX.index('id="shareOverlay"'):INDEX.index('id="shareOverlay"') + 9000]
    bottom = sheet[sheet.index('id="shareBottom"'):]
    assert bottom.index('id="shareIdle"') < bottom.index('id="shareConfirm"')
    assert ".share-bottom > *{ grid-area:1 / 1;" in INDEX
    assert "Motion.crossFade(" in _fn("drawShareChoice")
    # The ring is its own circle, growing in; no box-shadow ring.
    assert ".share-contact.selected .av{ box-shadow" not in INDEX
    assert ".share-contact.selected::after, .share-contact.on::after{ opacity:1; transform:none; }" in INDEX


def test_a_like_is_a_heart_on_the_card():
    body = _fn("renderThreadBody")
    assert 'class="msg-react"' in body and "msg-like" not in INDEX
    assert "Motion.react(el)" in body
    react = _mfn("react")
    assert "scale(.3)" in react and "blur(3px)" in react and "EASE_SWAP" in react
    # The banner says what it was.
    assert '"Liked your vibe"' in _fn("pollNotifications")


def test_new_message_faces_gather():
    pick = _fn("startChatWith")
    assert "newChatPicked.push(i)" in pick
    assert "paintNewChatContacts();" in pick.split("} else {")[1].split("}")[0]   # only when not drawn
    assert "Motion.gather(" in pick and "Motion.ungather(" in pick
    gather = _mfn("gather")
    assert "arcFrames(" in gather and "0.15" in gather and "DUR.arc" in gather
    assert 'id="newChatNameRoom"' in INDEX and 'class="nc-name"' in INDEX
    assert "Motion.pending(btn)" in _fn("startGroupChat")
