"""The algorithm PDF and deck describe the algorithm that ships (§202).

`tools/algorithm_docs.py` reads every number from the code, so the documents
cannot be wrong - only out of date. These fail when they are, and say the
command that fixes it. No reportlab or python-pptx needed.
"""
import importlib.util
from pathlib import Path

import topics as T

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "algorithm_docs", ROOT / "tools" / "algorithm_docs.py")
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)


def test_the_documents_describe_the_algorithm_that_ships():
    assert not D.is_stale(), (
        "The algorithm moved and its PDF and deck did not. Run: "
        "python tools/algorithm_docs.py (needs requirements-docs.txt)")


def test_a_new_algorithm_version_says_what_changed():
    assert D.CHANGES[0][0] == T.ALGO_VERSION, (
        "ALGO_VERSION moved: add an entry at the top of CHANGES in "
        "tools/algorithm_docs.py saying what changed")


def test_the_fingerprint_moves_with_a_weight(monkeypatch):
    before = D.fingerprint()
    monkeypatch.setitem(T.EVENT_WEIGHT, "play", T.EVENT_WEIGHT["play"] + 1)
    assert D.fingerprint() != before


def test_every_signal_with_a_weight_is_documented():
    assert {k for k, _w, _what in D.facts()["weights"]} == set(T.EVENT_WEIGHT)
