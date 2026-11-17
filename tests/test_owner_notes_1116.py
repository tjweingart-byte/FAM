"""The owner's 11.16 notes (PROBLEMS.md §637), on the web.

Each is something the page must keep doing, read from the page itself; the
smoke run (`tools/smoke_preview.py`) presses the same controls in a browser.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    return re.search(r"function " + name + r"\([^)]*\)\{(.*?)\n  \}", INDEX, re.S).group(1)


def test_a_mix_menu_offers_edit_topics_to_owner_and_member():
    menu = _fn("openMixMenu")
    assert menu.count('{ label: "Edit topics", action: editMixTopics }') == 2


def test_editing_a_mix_puts_an_x_on_every_topic_and_removes_by_id():
    body = _fn("renderMixBody")
    assert "mix-topic-x" in body and "removeMixTopic(this.dataset.id)" in body
    # Nothing plays from the page while it is being edited.
    assert "doneEditingTopics()" in body
    remove = _fn("removeMixTopic")
    # The whole remaining list is sent, a typed topic as its words.
    assert '"PATCH", "/api/mixes/" + m.id, { topic_ids: payload }' in remove
    assert "i.custom ? { query: i.query, title: i.title } : i.id" in remove
    # A mix opened afresh is never still being edited.
    assert "mixEditing = false;" in _fn("openMix")


def test_the_day_bubbles_are_gone_from_a_mix():
    assert "mix-bub" not in INDEX
    assert "mixBubblesHTML" not in INDEX


def test_captions_start_off_and_nothing_is_remembered():
    assert 'localStorage.setItem("fam.captions"' not in INDEX
    assert 'localStorage.removeItem("fam.captions")' in INDEX
    player = _fn("populatePlayer")
    assert 'panel.classList.remove("on"); panel.classList.add("off");' in player
    assert "toggleCC()" not in player


def test_captions_keep_what_was_said_and_scroll():
    draw = _fn("renderCaptions")
    assert "for(var i = 0; i < here; i++)" in draw and "cc-said" in draw
    # Nothing ahead of the voice: the loop stops at the sentence being read.
    assert "captionSentences[here]" in draw
    assert "if(captionFollow) captionToVoice(box);" in draw
    assert 'id="cc-text" onscroll="captionScrolled(this)"' in INDEX
    assert "cc-leaving" not in INDEX


def test_friends_is_two_tabs():
    draw = _fn("renderFriends")
    assert 'tab("friends", "Friends", friends.length)' in draw
    assert 'tab("following", "Following", following.length)' in draw
    assert "fr-counts" not in INDEX
    # Somebody who follows and is not followed back still has a place.
    assert "Follows you" in draw and "Following back makes you friends." in draw


def test_private_search_is_a_lock_and_the_page_keeps_its_colours():
    bubble = re.search(r'<button[^>]*id="privateBubble".*?</button>', INDEX, re.S).group(0)
    assert 'class="lock-open"' in bubble and 'class="lock-shut"' in bubble
    assert ">Private<" not in bubble
    assert "dimDown" not in INDEX
    assert 'id="privateNote"' not in INDEX
    assert "#0F0E12" not in INDEX      # the charcoal ground
