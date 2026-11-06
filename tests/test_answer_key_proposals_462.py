"""The proposed answer-key rules for Consuela and Sonic (§462): each
proposal is its signed book plus exactly one rule in the shape of
`nando.md` rule 12, and goes once the owner has signed it."""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROPOSALS = ROOT / "docs" / "agent_rule_proposals"
SIGNED = ROOT / "docs" / "agent_rules"

CONSUELA_RULE = """12. Let your answer key - a stronger model chosen by the owner - grade a
    sample of your work each night, in the background: whether the
    sentences you kept are the article's key ones, whether each figure's
    "happened" or "expected" is right, and whether each pair you merged or
    kept apart was right. Its grades only teach you and measure you; they
    never re-file, merge, restore or delete anything, and never change an
    episode.
"""
SONIC_RULE = """8. Let your answer key - a stronger model chosen by the owner - grade a
   sample of finished episodes' history packets each night, in the
   background: whether each item was relevant to the question, and whether
   the archive held something you missed. Its grades only teach you and
   measure you; they never change the archive or an episode.
"""
CASES = {
    # book: (the line the rule follows, the rule)
    "consuela.md": ("   FAM's automated check shows you make no wrong merges.\n", CONSUELA_RULE),
    "sonic.md": ("   without history.\n", SONIC_RULE),
}


@pytest.mark.parametrize("book", sorted(CASES))
def test_each_proposal_adds_only_its_answer_key_rule_and_goes_once_signed(book):
    anchor, rule = CASES[book]
    signed = (SIGNED / book).read_bytes().decode("utf-8")
    proposed = PROPOSALS / book
    if rule in signed:
        assert not proposed.exists(), f"{book} is signed: delete the proposal"
        return
    assert signed.count(anchor) == 1
    assert proposed.read_bytes().decode("utf-8") == signed.replace(anchor, anchor + rule)


@pytest.mark.parametrize("book", sorted(CASES))
def test_the_rule_is_nandos_rule_12_in_shape(book):
    rule = " ".join(CASES[book][1].split())
    assert "Let your answer key - a stronger model chosen by the owner" in rule
    assert "in the background" in rule
    assert "Its grades only teach you and measure you" in rule
    assert "never change an episode" in rule or "never change the archive or an episode" in rule


def test_the_list_matches_what_the_code_approves():
    # Kept from tests/test_consuela_proposal_398.py, retired here: Perplexity
    # was signed into consuela.md's never 8 (§412); GNews approved by the
    # owner on 2026-11-03 (§429); Polymarket pending.
    import history_archive as ha
    from config import settings

    approved = {p for p in settings.housekeeper_approved_providers if ha.approved(p)}
    assert approved == {"fam", "gdelt", "tiingo", "balldontlie", "perplexity", "gnews"}
    assert "Perplexity)." in (SIGNED / "consuela.md").read_text(encoding="utf-8")
