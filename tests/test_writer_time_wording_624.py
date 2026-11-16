"""How the writer is told to date what it says (§624).

Sentences like "the latest price we have" and "nothing I can establish"
echoed the instruction "the newest information you can establish". The
instruction now names the sources, and the request gives the form the
writer was reaching for: a moving figure with its own time, as a fact.
"""
from __future__ import annotations

import script_generator as sg
from script_generator import build_prompt, plan_episode


def test_the_phrase_the_leaks_echoed_is_gone():
    request = build_prompt(plan_episode("how is the stock doing", 2))
    assert "you can establish" not in sg.SYSTEM_PROMPT + request
    assert "- Give the newest information the sources date.\n" in sg.SYSTEM_PROMPT
    assert "Prefer the newest information the sources date." in request


def test_the_request_gives_a_moving_figure_its_own_time():
    request = " ".join(build_prompt(plan_episode("how is the stock doing", 2)).split())
    assert ("Anything that moves - a price, a score, a count - is said with its own "
            "time, as a fact about the world") in request
    assert 'never "the latest price we have"' in request


def test_what_the_writer_is_shown_as_wrong_is_what_the_check_drops():
    # The instruction's bad example and the guard agree (§622).
    assert sg.is_material_sentence("That is the latest price we have.")
    assert not sg.is_material_sentence("It closed Friday at ninety-five.")


def test_the_evidence_block_names_the_plural_too():
    import inspect

    assert '"that is not in what I was given", "the latest we have".' in " ".join(
        inspect.getsource(sg.build_prompt).split())
