"""The proposed consuela.md: Perplexity joins never 8's list, and nothing else changes (§398)."""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROPOSED = ROOT / "docs" / "agent_rule_proposals" / "consuela.md"
SIGNED = ROOT / "docs" / "agent_rules" / "consuela.md"

BEFORE = "   (approved so far: GDELT, with its citation; Tiingo; BALLDONTLIE).\n"
AFTER = ("   (approved so far: GDELT, with its citation; Tiingo; BALLDONTLIE;\n"
         "   Perplexity).\n")


def test_the_proposal_only_adds_perplexity_and_goes_once_signed():
    signed = SIGNED.read_bytes().decode("utf-8")
    if AFTER in signed:
        assert not PROPOSED.exists(), "signed: delete the proposal"
        return
    assert signed.count(BEFORE) == 1
    assert PROPOSED.read_bytes().decode("utf-8") == signed.replace(BEFORE, AFTER)


def test_the_list_matches_what_the_code_approves():
    import history_archive as ha
    from config import settings

    approved = {p for p in settings.housekeeper_approved_providers if ha.approved(p)}
    assert approved == {"fam", "gdelt", "tiingo", "balldontlie", "perplexity"}
