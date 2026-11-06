"""The owner's guardrails of §429: an accusation is said as who makes it,
private people are named only as the reporting names them, and the archive
keeps only approved providers' material whether or not Consuela runs."""
from __future__ import annotations

import history_archive as ha
import script_generator as sg


def test_the_writer_is_told_an_accusation_is_not_a_finding():
    text = sg.system_prompt()
    assert "**An accusation is not a finding.**" in text
    assert 'Say who accuses ("prosecutors say")' in text
    assert "where it stands and any denial" in text
    assert "fact only once a verdict, ruling or" in text
    # Compatible with never mentioning the material (§256).
    assert "Naming the accuser is not mentioning your material." in text


def test_the_writer_is_told_how_to_name_private_people():
    text = sg.system_prompt()
    assert "Private people (victims, witnesses) are named only as the reporting" in text
    assert "a child accused of a crime, never." in text


def test_gnews_is_approved_and_polymarket_is_pending():
    assert ha.approved("GNews") and ha.approved("gnews")
    assert not ha.approved("Polymarket")


def test_the_archive_keeps_only_approved_providers_with_consuela_off(monkeypatch):
    monkeypatch.setattr(ha, "consuela_runs", lambda: False)
    found = [ha.Finding(url="https://a.com/1", provider="perplexity"),
             ha.Finding(url="https://a.com/2", provider="parallel"),
             ha.Finding(url="https://a.com/3", provider=""),
             ha.Finding(url="https://a.com/4", provider="gdelt")]
    assert [f.provider for f in ha._kept(found)] == ["perplexity", "gdelt"]
    live = [{"source": "API-Sports"}, {"source": "Finnhub"}, {"source": "BALLDONTLIE"},
            {"source": "Polymarket"}]
    assert [r["source"] for r in ha.kept_live(live)] == ["BALLDONTLIE"]
