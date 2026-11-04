"""The planter names a quote's speaker as the packet places it (§450).

Recreating §440's evaluation data, two controls - a packet quote given to
its own speaker - failed because the planter had named the wrong one: a
role ("Federal Reserve Chair") and a word cut off at the window's edge
("Summer Gam", because "Bréal" was unreadable). Mclovin's quote check was
right both times. Written for the test, never provider text.
"""
from types import SimpleNamespace

from agents.supervisor_cases import packet_quotes


def _quotes(text):
    return packet_quotes([SimpleNamespace(text=text)])


def test_the_nearest_name_is_the_speaker():
    text = ("Mara Quill opened the hearing. Later, “We will not move the "
            "deadline again this year,” said Tobias Venn.")
    assert _quotes(text) == [("We will not move the deadline again this year", "Tobias Venn")]


def test_a_role_is_never_the_speaker():
    text = ("The Central Bank Chair spoke after the vote. “I do not believe "
            "in promises about next year,” Orrin told reporters.")
    assert _quotes(text) == []


def test_accented_names_are_read():
    text = ("“See if we can hold a race across the old hill,” wrote "
            "Amélie Bréal in her letter.")
    assert _quotes(text) == [("See if we can hold a race across the old hill",
                              "Amélie Bréal")]


def test_a_name_cut_by_the_window_is_not_taken():
    filler = "x" * 108
    text = (f"“This is the plan we agreed on in the spring,” {filler} "
            "Summer Games were next.")
    # The 120 characters after the quote end inside "Summer Games": the
    # window holds "Summer Gam", which is never a speaker.
    window = text[text.index("”") + 1:][:120]
    assert window.endswith("Summer Gam")
    assert _quotes(text) == []


def test_a_role_in_front_of_a_name_keeps_the_name():
    """The review (§459): "Federal Reserve Chair Jerome Powell" gave no
    speaker at all; the role's organisation is never the speaker either."""
    text = ("Federal Reserve Chair Jerome Powell said “we will wait for more "
            "data before moving.”")
    assert _quotes(text) == [("we will wait for more data before moving", "Jerome Powell")]


def test_a_name_that_begins_with_an_accent_is_read():
    text = "Émile Zola wrote: “the truth is on the march and nothing will stop it.”"
    assert _quotes(text) == [("the truth is on the march and nothing will stop it",
                              "Émile Zola")]
