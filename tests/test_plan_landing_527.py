"""The plan-following measure, read against real endings (§527).

Read by hand on 10 episodes, the writer landed where Shakespeare planned in
about six; the measure said none. It missed numbers read out ("seven point
nine percent" for "7.9%"), long source "sentences" (a standings table, a
press release's header) and so on. Made-up sentences here, never a source's.
"""
from __future__ import annotations

import episode_quality as q


def _landing(planned, ending):
    return q.plan_following({"landing": [planned]}, ["An opening.", "A middle."] + ending)["landing"]


def test_a_figure_read_out_is_the_figure_printed():
    assert _landing("The broad jobless measure rose to 7.9% from 7.6% in March.",
                    ["Hiring held up.", "The figure that matters is seven point nine percent."]) == 1.0


def test_a_long_table_is_matched_by_its_names_and_numbers():
    table = ("League standings East|W|L| Hawks|5|2| Ravens|4|5| Owls|2|7| "
             "Foxes|2|5| conference table after week nine with points for and against")
    assert _landing(table, ["The table after Sunday: the Hawks five and two; the Ravens "
                            "four and five; the Owls two and seven."]) == 1.0


def test_a_distinctive_figure_counts_twice_a_small_one_once():
    assert q._distinctive(7.9) and q._distinctive(299) and not q._distinctive(2)
    # One small shared number and nothing else is not a use.
    assert _landing("Two teams met in the cup.", ["It ended two days later.",
                                                  "Rain fell."]) == 0.0


def test_an_unrelated_ending_is_not_a_landing():
    assert _landing("The company's shares fell 4% on Monday after the probe.",
                    ["Regulators in Britain say the risk is very low.",
                     "Cooked food is safe."]) == 0.0
