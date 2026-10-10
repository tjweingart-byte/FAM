"""The brief, composer, placer and thumbnails follow `MODEL` again (§248).

§227 put them on Haiku 5.5; the owner moved them back to Sonnet. What §227
added that is not about Haiku stays and is pinned here: an empty setting means
the default, metering prices an episode whose calls run on different models
call by call, every current model has a price, and boot checks any model a
call uses besides `MODEL` - by default only the photo check (§225).
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metering  # noqa: E402

SMALL = ("EI_MODEL", "STORIES_MODEL", "CATEGORIES_MODEL", "THUMBNAILS_MODEL")


@pytest.fixture
def fresh_config(monkeypatch):
    """Reload config against an environment the test controls."""
    for name in SMALL + ("MODEL", "ADMIN_ASK_MODEL",
                         "THUMBNAILS_CLAUDE_INPUT_PER_MTOK",
                         "THUMBNAILS_CLAUDE_OUTPUT_PER_MTOK"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FAM_IGNORE_DOTENV", "1")
    import config

    def load():
        importlib.reload(config)
        return config

    yield load
    for name in SMALL + ("MODEL",):
        monkeypatch.delenv(name, raising=False)
    importlib.reload(config)


def _small_models(settings):
    return (settings.ei_model, settings.stories_model,
            settings.categories_model, settings.thumbnails_model)


def test_the_four_calls_follow_the_writers_model(fresh_config):
    config = fresh_config()
    assert config.settings.model == "claude-sonnet-5"
    assert set(_small_models(config.settings)) == {"claude-sonnet-5"}


def test_setting_model_moves_them_with_the_writer(fresh_config, monkeypatch):
    """`render.yaml` sets MODEL; one model to reason about per deployment."""
    monkeypatch.setenv("MODEL", "claude-opus-5-5")
    config = fresh_config()
    assert set(_small_models(config.settings)) == {"claude-opus-5-5"}


def test_only_the_photo_check_stays_on_haiku(fresh_config):
    """§225, the owner's separate call, is not reverted."""
    config = fresh_config()
    assert config.settings.image_check_model == config.SMALL_MODEL == "claude-haiku-5-5"


@pytest.mark.parametrize("name", SMALL)
def test_each_call_can_still_be_moved(fresh_config, monkeypatch, name):
    monkeypatch.setenv(name, "claude-haiku-5-5")
    config = fresh_config()
    assert "claude-haiku-5-5" in _small_models(config.settings)


@pytest.mark.parametrize("name", SMALL)
def test_an_empty_setting_is_the_default_not_an_empty_model(fresh_config,
                                                            monkeypatch, name):
    """`CATEGORIES_MODEL=` in a copied .env once meant a model named ''."""
    monkeypatch.setenv(name, "")
    config = fresh_config()
    assert set(_small_models(config.settings)) == {"claude-sonnet-5"}


def test_the_admin_box_stays_on_the_writers_model(fresh_config):
    """Writing SQL against a live schema is not the brief's small extraction;
    it followed the brief's model until the brief moved."""
    fresh_config()
    import admin_tracker
    from config import settings
    assert admin_tracker.ask_model() == settings.model


def test_thumbnail_spend_is_priced_at_the_writers_rates(fresh_config):
    config = fresh_config()
    assert (config.settings.thumbnails_claude_input_per_mtok,
            config.settings.thumbnails_claude_output_per_mtok) == \
        metering.PRICES[config.settings.thumbnails_model]


@pytest.mark.parametrize("model, rate", [
    ("claude-fable-5-1", (10.00, 50.00)),
    ("claude-opus-5-5", (4.00, 20.00)),
    ("claude-sonnet-5-5", (2.00, 10.00)),
    ("claude-haiku-5-5", (0.10, 0.50)),
])
def test_every_current_model_has_a_price(model, rate):
    """An unpriced model counts as $0 in prefetch's dollar budget."""
    assert metering.PRICES[model] == rate


def test_an_episode_on_two_models_is_priced_call_by_call():
    """The brief on Haiku, then the writer on Sonnet: each at its own rate."""
    u = metering.Usage()
    u.add_model_call("claude-haiku-5-5", {"input_tokens": 1_000_000,
                                          "output_tokens": 1_000_000})
    u.add_model_call("claude-sonnet-5", {"input_tokens": 1_000_000,
                                         "output_tokens": 1_000_000})
    cost = metering.price_of(u)
    assert u.model == "claude-sonnet-5"
    assert cost.priced is True
    assert cost.claude_input == pytest.approx(0.10 + 2.00)
    assert cost.claude_output == pytest.approx(0.50 + 10.00)


def test_one_unpriced_call_marks_the_episode_unpriced():
    u = metering.Usage()
    u.add_model_call("claude-from-the-future", {"input_tokens": 10})
    u.add_model_call("claude-sonnet-5", {"input_tokens": 10})
    assert metering.price_of(u).priced is False


def test_cache_reads_use_the_published_rate_where_it_is_not_a_tenth():
    for model, per_read in (("claude-fable-5-1", 0.25),
                            ("claude-opus-5-5", 0.20),
                            ("claude-sonnet-5", 0.20),
                            ("claude-haiku-5-5", 0.01)):
        u = metering.Usage()
        u.add_model_call(model, {"cache_read_input_tokens": 1_000_000})
        assert metering.price_of(u).cache_read == pytest.approx(per_read), model


def test_a_ledger_row_carries_the_per_call_total(tmp_path):
    ledger = metering.MeterStore(str(tmp_path / "m.db"))
    u = metering.Usage()
    u.add_model_call("claude-haiku-5-5", {"input_tokens": 1_000_000})
    u.add_model_call("claude-sonnet-5", {"input_tokens": 1_000_000})
    ledger.record("u", u)
    assert ledger.rows()[0]["claude_usd"] == pytest.approx(2.10)


# --- the boot check ---------------------------------------------------------

class _Retriever:
    def __init__(self, missing=()):
        self.missing, self.asked = set(missing), []

        outer = self

        class _Models:
            async def retrieve(self, model):
                outer.asked.append(model)
                if model in outer.missing:
                    import anthropic
                    raise anthropic.NotFoundError.__new__(anthropic.NotFoundError)
                return {"id": model}

        self.models = _Models()


def _boot_check(monkeypatch, client, state="ok"):
    import asyncio

    import app as appmod
    monkeypatch.setattr(appmod, "build_async_client", lambda: client)
    monkeypatch.setitem(appmod.CREDENTIALS, "state", state)
    monkeypatch.delitem(appmod.CREDENTIALS, "models", raising=False)
    asyncio.run(appmod._verify_small_models())
    return appmod.CREDENTIALS


def test_boot_checks_the_photo_checks_model_beside_the_writer(monkeypatch):
    """The writer's check covers every call on `MODEL`; the photo check's
    Haiku is a second thing an account may not have."""
    client = _Retriever()
    creds = _boot_check(monkeypatch, client)
    assert client.asked == ["claude-haiku-5-5"]
    assert creds["models"] == {"claude-haiku-5-5": "ok"}


def test_a_missing_small_model_is_said_at_boot_and_named(monkeypatch):
    creds = _boot_check(monkeypatch, _Retriever(missing={"claude-haiku-5-5"}))
    assert "claude-haiku-5-5" in creds["models"]["claude-haiku-5-5"]
    assert "not available" in creds["models"]["claude-haiku-5-5"]


def test_no_second_question_when_the_key_already_failed(monkeypatch):
    client = _Retriever()
    _boot_check(monkeypatch, client, state="rejected")
    assert client.asked == []
