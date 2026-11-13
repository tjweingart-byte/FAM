"""Dashes never reach the voice as hyphens (PROBLEMS.md §574).

The owner heard respelled names and NVIDIA as choppy and asked whether the
dashes were the cause. Two were: respellings went to Chatterbox as
"Sah-gah-poh-loo-teh-leh", and Chatterbox's own clean-up
(`chatterbox.tts.punc_norm`, inside every `generate`) turns an em dash into
a hyphen after it has softened " - ", so "won—again—on Sunday" reached the
model as "won-again-on Sunday".
"""
from __future__ import annotations

import pytest

import pronunciation
from spoken_text import speakable


@pytest.mark.parametrize("written, said", [
    ("The Eagles won—again—on Sunday.", "The Eagles won, again, on Sunday."),
    ("The Eagles won — again — on Sunday.", "The Eagles won, again, on Sunday."),
    ("The Eagles won – again – on Sunday.", "The Eagles won, again, on Sunday."),
    ("He said - finally - yes.", "He said, finally, yes."),
    ("The Paris–Roubaix race", "The Paris Roubaix race"),
    ("—and then he left.", "and then he left."),
    ("It ended—.", "It ended."),
    ("He paused—, then went on.", "He paused, then went on."),
])
def test_a_dash_used_as_punctuation_is_a_pause(written, said):
    assert speakable(written) == said


@pytest.mark.parametrize("written, said", [
    ("a well-known 49ers fan", "a well-known forty-niners fan"),
    ("The 2019–2023 seasons", "The twenty nineteen to twenty twenty-three seasons"),
    ("won 24–17", "won twenty-four to seventeen"),
    ("COVID-19 rules", "Covid-nineteen rules"),
])
def test_hyphenated_words_and_ranges_are_as_before(written, said):
    assert speakable(written) == said


def test_no_dash_glues_words_once_chatterbox_has_cleaned_it():
    """What `punc_norm` would make of our output: no "-" between words that
    were not one hyphenated word to begin with."""
    out = speakable("The Eagles won—again—on Sunday – twice – and lost")
    for dash in ("—", "–", " - "):
        assert dash not in out
    assert "won-again" not in out.replace("—", "-")


def test_a_respelling_reaches_the_voice_as_whole_words():
    assert pronunciation.voiced("sah-gah-poh-loo-TEH-leh") == "Sahgahpohlootehleh"
    assert pronunciation.voiced("JAIR-in sah-gah-poh-loo-TEH-leh") == "Jairin Sahgahpohlootehleh"
    assert speakable(pronunciation.voiced("YAH-nis")) == "Yahnis"


def test_a_hyphen_stays_where_joining_would_make_a_new_sound():
    assert pronunciation.voiced("mat-hew") == "Mat-hew"       # not "Mathew", a th
    assert pronunciation.voiced("lon-gah") == "Lon-gah"       # not "longah", an ng
    assert pronunciation.voiced("mis-ter") == "Mister"
