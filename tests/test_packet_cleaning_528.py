"""The packet Shakespeare numbers is cleaned first (§528).

What he chose from was the sources' raw lines: markup, glued sentences,
sentences cut at "Oct.", repeats, "add us to Google" lines - and, as source
P1, the guidance written around the live block for the writer ("Not knowing
is not the same as nothing having happened..."), numbered as if it were a
fact. Markup is taken off, never a word changed; tables stay (a standings
table is what a sports episode lands on, §527). Made-up text here, never a
source's.
"""
from __future__ import annotations

import dataclasses

from agents import editor
from script_generator import plan_episode


def test_markup_comes_off_and_the_words_stay():
    out = editor._split("Shares slid 4% on **Monday** after the firm said "
                        "[it had opened an inquiry](https://example.com/a) into its chief.")
    assert out == ["Shares slid 4% on Monday after the firm said it had opened an "
                   "inquiry into its chief."]
    # A link the excerpt cut in half, and a bare address.
    assert editor._split("They announced that [it had opened an inquiry") == [
        "They announced that it had opened an inquiry"]
    assert "https" not in " ".join(editor._split(
        "Read the full figures from the agency at https://example.com/x today please."))


def test_glued_and_cut_sentences_are_mended():
    assert editor._split("It is not clear what the terms are.The talks resume in Qatar on Monday.") == [
        "It is not clear what the terms are.", "The talks resume in Qatar on Monday."]
    out = editor._split("Strikes have eased since Oct. 10, but they continue almost every day.")
    assert out == ["Strikes have eased since Oct. 10, but they continue almost every day."]


def test_tables_stay_and_the_page_around_the_article_goes():
    table = "Hawks|5|2|1|.688|182|172| Ravens|4|5|0|.444|246|236|"
    assert editor._split(table) == [table]
    assert editor._split("Get more news on Add Example News to Google right now.") == []
    assert editor._split("The newest thing") == []                     # a fragment
    assert editor._split("Inflation remains elevated.") == ["Inflation remains elevated."]
    dump = " ".join(["Team"] * 200) + "."
    dropped = __import__("collections").Counter()
    assert editor._split(dump, dropped) == [] and dropped["run_on"] == 1


def test_a_repeat_inside_one_source_is_numbered_once():
    out = editor._split("The central bank held its rate at four percent.\n"
                        "**The central bank held its rate at four percent.**")
    assert out == ["The central bank held its rate at four percent."]


class _Facts:
    def __init__(self, facts, status):
        self.facts, self.status = facts, status


class _Lookup:
    def __init__(self, outcome, facts):
        self.outcome, self.facts = outcome, facts

    def as_prompt_block(self):
        return ("This question turns on a live state.\n**Not knowing is not the same "
                "as nothing having happened.** You do not know whether this has started.")


def test_the_live_block_gives_him_its_facts_never_its_guidance():
    import live_facts

    with_facts = _Lookup(live_facts.FACTS, _Facts(["Eagles 21, Giants 14, final."], "final"))
    assert editor._live_sentences(with_facts) == ["Status: final.",
                                                  "Eagles 21, Giants 14, final."]
    assert editor._live_sentences(_Lookup("no_feed", None)) == []
    plan = dataclasses.replace(plan_episode("how did the eagles do", 1.5), search=False,
                               evidence="", live=_Lookup("no_feed", None))
    numbered = editor.number_packets(plan)
    assert not any("Not knowing" in s for src in numbered for s in src.sentences)
