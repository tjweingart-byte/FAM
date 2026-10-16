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
    # Sport, money, health, tech, culture, world and science, one each.
    for slug in ("marathon", "fed", "fever", "passkeys", "hamilton",
                 "suez", "apollo"):
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
])
def test_the_wider_list(host, tier):
    assert research.credibility(_url(host)) == tier


def test_no_outlet_is_in_two_tiers():
    assert not research.TIER_PRIMARY & research.TIER_ESTABLISHED


def test_a_regional_paper_is_still_a_towns_own_outlet():
    """Established for credibility, but local news may still learn it."""
    assert research.REGIONAL_OUTLETS <= research.TIER_ESTABLISHED
    assert "latimes.com" in research.REGIONAL_OUTLETS
    assert "cnn.com" not in research.REGIONAL_OUTLETS
    source = open(os.path.join(ROOT, "local_news.py"), encoding="utf-8").read()
    assert "host not in research.REGIONAL_OUTLETS" in source
