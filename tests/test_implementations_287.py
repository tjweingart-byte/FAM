"""§287: more style examples (one, two and five minutes, across the
facets) and a wider list of trusted sources."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import research  # noqa: E402
import script_generator as sg  # noqa: E402


def _url(host):
    return SimpleNamespace(url=f"https://{host}/a/story")


def test_there_is_a_one_two_four_and_five_minute_example():
    minutes = set(sg.EXAMPLE_MINUTES.values())
    assert {1, 2, 4, 5} <= minutes


def test_the_two_minute_examples_cover_the_facets():
    names = os.listdir(os.path.join(ROOT, "examples"))
    two = [n for n in names if n.startswith("2-") and n.endswith(".txt")]
    assert len(two) >= 7
    # Sport (a recap and an explainer), money, health, tech, culture, world
    # and science.
    for slug in ("singapore-sprint", "marathon", "fed", "autoimmune",
                 "passkeys", "house-music", "suez", "apollo"):
        assert any(slug in n for n in two), slug


@pytest.mark.parametrize("host,tier", [
    ("afp.com", "primary"), ("cbc.ca", "primary"), ("formula1.com", "primary"),
    ("pgatour.com", "primary"), ("nasdaq.com", "primary"),
    ("onlinelibrary.wiley.com", "primary"), ("heart.org", "primary"),
    ("www.mhlw.go.jp", "primary"), ("gov.br", "unverified"),
    ("www.planalto.gov.br", "primary"), ("aljazeera.com", "established"),
    ("mayoclinic.org", "established"), ("transfermarkt.com", "established"),
    ("arxiv.org", "established"), ("latimes.com", "established"),
    ("politifact.com", "established"), ("example-blog.net", "unverified"),
    # A listed outlet's readers are not its reporters (review).
    ("forums.macrumors.com", "unverified"), ("community.cnet.com", "unverified"),
    ("sciencedirect.com", "established"), ("dictionary.cambridge.org", "unverified"),
])
def test_the_wider_list(host, tier):
    assert research.credibility(_url(host)) == tier


def test_no_outlet_is_in_two_tiers():
    assert not research.TIER_PRIMARY & research.TIER_ESTABLISHED


def test_a_regional_paper_is_still_a_towns_own_outlet():
    """Established for credibility, but local news may still learn it."""
    assert research.REGIONAL_OUTLETS <= research.TIER_ESTABLISHED
    assert {"latimes.com", "smh.com.au", "thestar.com"} <= research.REGIONAL_OUTLETS
    assert "cnn.com" not in research.REGIONAL_OUTLETS
    source = open(os.path.join(ROOT, "local_news.py"), encoding="utf-8").read()
    assert "host not in research.REGIONAL_OUTLETS" in source


def test_a_heading_packed_into_a_passage_ends_as_a_sentence():
    assert research.passages(["## Eagles beat the Cowboys in overtime\n\n"
                              "The Eagles won on a late field goal."], 1000) == [
        "Eagles beat the Cowboys in overtime. The Eagles won on a late field goal."]


def test_a_refused_script_is_not_also_noted_as_short(monkeypatch):
    import pipeline_errors
    from script_generator import plan_episode

    noted = []
    monkeypatch.setattr(pipeline_errors, "record",
                        lambda *a, **k: noted.append(a[1]))
    reader = sg._ScriptReader(plan_episode("q", 2, search=False), sg.ScriptNotes())
    reader.emitted_words = 40
    reader.finish()
    for outcome in ("refused", "out_of_room", "over_budget"):
        reader.note_if_short(outcome)
    assert "script well under its length" not in noted
    reader.note_if_short("ok")
    assert "script well under its length" in noted


def test_examples_signpost_nothing_and_never_sum_up():
    for name in os.listdir(os.path.join(ROOT, "examples")):
        if not name.endswith(".txt"):
            continue
        text = open(os.path.join(ROOT, "examples", name), encoding="utf-8").read()
        for phrase in ("Here's the mechanism", "Here is the machinery",
                       "So the cause was", "championship leader",
                       "leading the championship"):
            assert phrase not in text, (name, phrase)
