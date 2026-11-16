"""How the writer is told to date what it says (§624).

Sentences like "the latest price we have" and "nothing I can establish"
echoed the instruction "the newest information you can establish". The
instruction now names the sources, and the request gives the form the
writer was reaching for: a moving figure with its own time, as a fact.
"""
from __future__ import annotations

import script_generator as sg
from script_generator import build_prompt, plan_episode


def _request() -> str:
    """A researched episode's request - with evidence, so the evidence and
    timing blocks are in it, as they are for every episode that is heard."""
    import dataclasses

    plan = dataclasses.replace(plan_episode("how is the stock doing", 2), evidence=(
        "SOURCE 1\nTitle: Shares close lower\nPublished: 2026-11-13 (3 days ago)\n"
        "Source type: established outlet\nKey evidence:\nThe shares closed at ninety."))
    request = " ".join(build_prompt(plan).split())
    assert "The listener has never seen these passages" in request
    return request


def test_the_phrase_the_leaks_echoed_is_gone():
    request = _request()
    assert "you can establish" not in sg.SYSTEM_PROMPT + request
    assert "- Give the newest information the sources date.\n" in sg.SYSTEM_PROMPT
    assert "Prefer the newest information the sources date." in request


def test_the_request_gives_a_moving_figure_its_own_time():
    request = _request()
    assert ("Anything that moves - a price, a score, a count - is said with its own "
            "time, as a fact about the world") in request
    assert 'never "the latest price we have"' in request


def test_what_the_writer_is_shown_as_wrong_is_what_the_check_drops():
    # Each bad example in the instructions is one the guard catches (§622, §627).
    request = _request()
    assert '"that is not in what I was given", "the latest we have".' in request
    for leak in ("That is the latest price we have.", "That is the latest we have.",
                 "That is not in what I was given."):
        assert sg.is_material_sentence(leak), leak
    assert not sg.is_material_sentence("It closed Friday at ninety-five.")
