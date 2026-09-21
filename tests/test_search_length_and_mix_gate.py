"""Three interface facts that were each true in one place and wrong in another.

**The search page's length.** `selectedLengthMinutes` is 2, and said so in a
comment explaining why; the markup printed "3 min" in five separate places.
So a listener landed on search reading "3 min", opened the menu and found
"2 min" ticked as their current choice, and a question typed without touching
either generated two minutes. Nothing was broken - the number was simply
settled in one place and copied into five, which is `.env.example` against
`config.py` in a different file. The literals are painted over at boot by
`paintLengthControls`, and this pins them to the variable so they cannot drift
apart again silently.

**The topic bank is not the account.** `/api/mixes` answers 401 to a listener
without one, and `loadMixes` used to return on that branch *before* taking the
topic bank off the other promise - an endpoint that is not account-gated and
had answered perfectly. `topicBank` stayed empty, and the picker reads that
emptiness as "Could not load the topic list": a fact about the listener's
account, reported as a fact about the server. The visible symptom was that
searching in the DailyFAM picker offered nothing to add, so the (+) after a
search had nothing to do.

**And the (+) is not offered to somebody who cannot keep a mix.** It opened a
naming modal and then a whole picker, and refused only at the save - a control
with nothing behind it, which this project has deleted twice before. The
sign-up buttons `renderMixesLocked` already draws are the way in.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _default_minutes() -> int:
    m = re.search(r"var selectedLengthMinutes = (\d+);", INDEX)
    assert m, "selectedLengthMinutes is no longer declared as a literal"
    return int(m.group(1))


def test_the_search_page_opens_on_the_length_it_will_generate():
    """Two minutes, and the packet asked for it by name."""
    assert _default_minutes() == 2


def test_every_length_placeholder_agrees_with_the_variable():
    """The markup's literals are placeholders, not a second answer.

    They exist because an empty control flashes. They are overwritten before
    the first screen is drawn - but a placeholder that disagrees is still the
    interface saying two different things about one setting, and this is the
    check that fails when somebody edits one of the five.
    """
    want = "%d min" % _default_minutes()
    for element_id in ("lengthVal", "gcLengthVal", "lengthModalVal"):
        m = re.search(r'id="%s"[^>]*>(\d+ min)' % element_id, INDEX)
        assert m, "no length placeholder found for #%s" % element_id
        assert m.group(1) == want, "#%s says %r, the variable says %r" % (
            element_id, m.group(1), want)
    for element_id in ("speedLenPill", "paSpeedLenPill"):
        m = re.search(r'id="%s"[^>]*>([^<]+)</div>' % element_id, INDEX)
        assert m, "no pill placeholder found for #%s" % element_id
        assert m.group(1).strip().endswith(want), (
            "#%s says %r, the variable says %r" % (element_id, m.group(1), want))


def test_the_speed_placeholders_agree_with_the_default_speed():
    """`1x`, the way `pillText` writes it - the markup used to say `1×`, which
    is a different string, so the pill changed character the first time
    anything repainted it."""
    m = re.search(r'var SPEED_DEFAULT = "([^"]+)";', INDEX)
    assert m, "SPEED_DEFAULT is no longer declared as a literal"
    speed = m.group(1)
    for element_id in ("speedLenPill", "paSpeedLenPill"):
        found = re.search(r'id="%s"[^>]*>([^<]+)</div>' % element_id, INDEX)
        assert found.group(1).strip().startswith(speed)
    found = re.search(r'id="speedModalVal"[^>]*>([^<]+)</div>', INDEX)
    assert found.group(1).strip() == speed


def test_one_function_paints_the_length_and_the_menu_delegates_to_it():
    """The menu used to hold its own copy of where the number is printed, so
    a sixth place to print it would have been added to the markup and to the
    menu and to nothing else."""
    assert "function paintLengthControls(){" in INDEX
    menu = INDEX[INDEX.index("function openLengthMenu()"):]
    menu = menu[:menu.index("function ", 10)]
    assert "paintLengthControls();" in menu
    assert "gcLengthVal" not in menu, (
        "openLengthMenu is writing the label itself again")


def test_the_bank_is_taken_before_the_locked_branch_returns():
    """`/api/topics` is not account-gated, so its answer survives a 401 from
    `/api/mixes`. Read off the source, because the failure is silent: the
    picker renders a sentence about the server and nothing throws."""
    body = INDEX[INDEX.index("function loadMixes()"):]
    body = body[:body.index("function renderMixesLocked()")]
    took_bank = body.index("topicBank = res[1].topics;")
    locked = body.index("if(res[0].locked)")
    assert took_bank < locked, (
        "loadMixes returns on the locked branch before taking the topic bank, "
        "so the picker says 'Could not load the topic list' to a listener "
        "whose topic list loaded fine")


def test_the_new_mix_button_starts_hidden_and_is_shown_by_the_mix_list():
    """Hidden in the markup rather than shown and taken away: a control that
    appears and then vanishes reads as a fault."""
    m = re.search(r'<button class="myfam-dots-btn" id="newMixBtn"[^>]*>', INDEX)
    assert m, "the new-mix button no longer carries #newMixBtn"
    assert " hidden" in m.group(0), "the new-mix button is no longer hidden by default"
    assert ".myfam-dots-btn[hidden]{ display:none; }" in INDEX, (
        "tools/check_css.py's rule: a hidden control needs the rule that "
        "makes [hidden] mean something")

    locked = INDEX[INDEX.index("function renderMixesLocked()"):]
    locked = locked[:locked.index("function showNewMixButton")]
    assert "showNewMixButton(false);" in locked

    shown = INDEX[INDEX.index("function renderMixList(){"):]
    shown = shown[:shown.index("function mixById")]
    assert "showNewMixButton(true);" in shown
