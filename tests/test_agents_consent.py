"""A listener's words go only to a vendor the consent covers (§347).

`consent.py`'s notice names Anthropic as the company that receives what a
listener types, says or attaches (App Store guideline 5.1.2(i)). An agent
request carrying those words is refused, in code, for any other vendor -
before its adapter is touched - and FAM's own material is not held back by
it. Adding a vendor to `MODEL_VENDORS` without new wording is the mistake
this pins.
"""
from __future__ import annotations

import asyncio

import pytest

import consent
import metering
from agents import adapters, models, rulebooks
from agents.adapters import Answer
from tests.test_agents_model_layer import Pick, request, use


class FakeAdapter:
    vendor = "google"

    def __init__(self):
        self.sent = []

    async def send(self, **kw):
        self.sent.append(kw)
        return Answer(text='{"title": "t", "choice": "b"}', stop_reason="end_turn",
                      served_model=kw["model"], usage={"input_tokens": 10, "output_tokens": 5})


@pytest.fixture
def google(monkeypatch, tmp_path):
    """A second vendor, adapter and price faked: the check must hold on its
    own, not because no other adapter happens to exist yet."""
    fake = FakeAdapter()
    real = adapters.adapter_for
    monkeypatch.setattr(models.adapters, "adapter_for",
                        lambda vendor: fake if vendor == "google" else real(vendor))
    monkeypatch.setitem(metering.PRICES, "google:gemini-test", (0.3, 2.5))
    use(monkeypatch, agents=True)
    use(monkeypatch, housekeeper=True)
    use(monkeypatch, housekeeper_model="google:gemini-test")
    monkeypatch.setattr(models.rulebooks, "check",
                        lambda book: rulebooks.Lock(ok=True, version="1", sha="s"))
    monkeypatch.setattr(models.spend_guard, "enabled", lambda: False)
    models.reset(metering.MeterStore(str(tmp_path / "m.db")))
    yield fake
    models.reset(None)


def housekeeper(words: bool):
    return request(agent="housekeeper", task="consolidate", switch_setting="housekeeper",
                   model_setting="housekeeper_model", rule_book="consuela.md",
                   carries_listener_words=words, surface="background",
                   # FAM's own material is archive, never the `request` tag a
                   # listener's words travel in (§353).
                   material=((("request", "the Eagles game"),) if words
                             else (("archive", "Eagles 24, Giants 17 (AP, Oct 26)"),)))


def test_only_anthropic_is_covered_today():
    assert consent.MODEL_VENDORS == ("anthropic",)
    assert consent.covers("anthropic") and consent.covers(" Anthropic ")
    assert not consent.covers("google") and not consent.covers("openai")
    assert not consent.covers("")


def test_a_listeners_words_are_refused_to_an_uncovered_vendor_before_sending(google):
    result = asyncio.run(models.ask(housekeeper(words=True)))
    assert result.status == models.CONSENT and result.fell_back
    assert "google" in result.reason and google.sent == []
    assert result.cost_usd is None  # nothing was sent, so nothing was spent


def test_a_request_tagged_material_needs_consent_even_unflagged(google):
    """The flag left at its default no longer skips the check: material
    tagged `request` is a listener's words (§353)."""
    unflagged = request(agent="housekeeper", task="consolidate", switch_setting="housekeeper",
                        model_setting="housekeeper_model", rule_book="consuela.md",
                        surface="background", material=(("request", "my own question"),))
    assert unflagged.carries_listener_words is False
    result = asyncio.run(models.ask(unflagged))
    assert result.status == models.CONSENT and google.sent == []


def test_fams_own_material_may_go_to_another_vendor(google):
    result = asyncio.run(models.ask(housekeeper(words=False)))
    assert result.ok and result.value == Pick(title="t", choice="b")
    assert len(google.sent) == 1


def test_a_covered_vendor_receives_a_listeners_words(google, monkeypatch):
    from tests.test_agents_model_layer import FakeClient

    use(monkeypatch, housekeeper_model="anthropic:claude-haiku-5-5")
    client = FakeClient()
    result = asyncio.run(models.ask(housekeeper(words=True), client=client))
    assert result.ok and len(client.calls) == 1


def test_the_notice_names_every_covered_vendor():
    """A vendor in `MODEL_VENDORS` that the notice the listener agreed to
    does not name would be consent nobody gave."""
    for vendor in consent.MODEL_VENDORS:
        assert vendor.lower() in consent.AI_NOTICE["body"].lower()
        assert vendor.lower() in consent.PROVIDER.lower()
