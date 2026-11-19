"""§656: an interest pill on a profile is a label, never a door."""
from pathlib import Path

INDEX = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text()


def test_profile_pills_open_nothing():
    for gone in ("openInterestAt", "openPersonInterest", "openThreadInterest"):
        assert gone not in INDEX, gone


def test_chips_are_spans_without_onclick():
    body = INDEX.split("function interestChipsHTML(rows){", 1)[1].split("\n  }\n", 1)[0]
    assert "onclick" not in body and '<span class="yf-chip label">' in body
    hub = INDEX.split("yfInterests = (p.interests_shown", 1)[1].split('"interests");', 1)[0]
    pill = hub.split("Add interests in Edit profile", 1)[0]
    assert '<span class="yf-chip label"' in pill and "openTopic" not in pill


def test_ios_pills_open_nothing():
    ios = Path(__file__).resolve().parent.parent / "ios" / "FAM" / "FAM"
    for f in ios.rglob("*.swift"):
        assert "InterestChipButton" not in f.read_text(), f
