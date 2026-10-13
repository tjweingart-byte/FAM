"""Parallel and Perplexity replace Exa (§258, at the owner's direction).

Parallel is the default backend; Perplexity, a second index, is the second
look on a thin packet and the first rung below it; GDELT stays the free
floor. Nothing here reaches the network: the one request each backend makes
goes through `research._post`, which is stubbed.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import consent  # noqa: E402
import provider_usage  # noqa: E402
import research  # noqa: E402
import script_generator as sg  # noqa: E402
import spend_guard  # noqa: E402
from script_generator import ScriptNotes, plan_episode  # noqa: E402

PARALLEL_REPLY = {
    "search_id": "search_1",
    "results": [
        {"url": "https://www.reuters.com/a", "title": "Eagles beat Giants",
         "publish_date": "2026-10-11",
         "excerpts": ["The Eagles beat the Giants 27-17 on Sunday. Hurts threw "
                      "two touchdowns.\n\nThe win moves them to 5-1."]},
        {"url": "https://blog.example/b", "title": "Undated",
         "excerpts": ["Something about the game that is long enough to keep."]},
    ],
    "usage": [{"name": "sku_search", "count": 1}],
}

PERPLEXITY_REPLY = {
    "id": "x",
    "results": [
        {"url": "https://apnews.com/c", "title": "Giants fall to Eagles",
         "snippet": "Philadelphia won 27-17. " * 40,
         "date": "2026-10-11", "last_updated": "2026-10-12"},
        {"url": "https://old.example/d", "title": "Edited later",
         "snippet": "A page from long ago that somebody edited this week.",
         "last_updated": "2026-10-12"},
    ],
}


@pytest.fixture
def wire(monkeypatch):
    """Every request either backend makes, answered from `replies`."""
    sent: list = []
    replies: dict = {research.PARALLEL_URL: [PARALLEL_REPLY],
                     research.PERPLEXITY_URL: [PERPLEXITY_REPLY]}

    def post(url, headers, body):
        sent.append({"url": url, "headers": headers, "body": body})
        queue = replies[url]
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(research, "_post", post)
    monkeypatch.setenv("PARALLEL_API_KEY", "par-key")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "ppx-key")
    import credentials

    credentials.reset()
    yield sent, replies
    credentials.reset()


def use(monkeypatch, **changes):
    patched = dataclasses.replace(config.settings, **changes)
    monkeypatch.setattr(research, "settings", patched)
    monkeypatch.setattr(sg, "settings", patched)
    return patched


def brief(**fields):
    base = dict(recency_days=3, subject="the Eagles", retrieval="Eagles Giants score",
                broader="Eagles season", must_establish=[], live_domain="")
    base.update(fields)
    return types.SimpleNamespace(**base)


# --------------------------------------------------------------------------
# the requests
# --------------------------------------------------------------------------
def test_parallel_is_asked_in_fast_mode_with_the_window_and_no_listener_words(
        wire, monkeypatch):
    sent, _ = wire
    use(monkeypatch, research_backend="parallel")
    packet = asyncio.run(research.retrieve(
        "Eagles Giants score",
        brief=brief(query="did my eagles win??",
                    must_establish=["Hurts touchdowns"])))
    request = sent[0]
    assert request["url"] == "https://api.parallel.ai/v1/search"
    assert request["headers"] == {"x-api-key": "par-key"}
    body = request["body"]
    # Omitted, Parallel would run `advanced` at five times the price.
    assert body["mode"] == "fast"
    assert body["search_queries"] == ["Eagles Giants score"]
    assert body["advanced_settings"]["max_results"] == config.settings.exa_num_results
    assert body["advanced_settings"]["source_policy"]["after_date"].count("-") == 2
    # The objective is written from the brief, never the listener's words.
    assert "Hurts touchdowns" in body["objective"]
    assert "did my eagles win" not in body["objective"]
    assert packet.backend == "parallel" and packet.tried == ["parallel"]


def test_an_evergreen_question_sends_no_window(wire, monkeypatch):
    sent, _ = wire
    use(monkeypatch, research_backend="parallel")
    asyncio.run(research.retrieve("how a heat pump works",
                                  brief=brief(recency_days=0)))
    assert "source_policy" not in sent[0]["body"]["advanced_settings"]


def test_perplexity_is_asked_fast_with_its_own_date_format(wire, monkeypatch):
    sent, _ = wire
    use(monkeypatch, research_backend="perplexity")
    asyncio.run(research.retrieve("Eagles Giants score", brief=brief()))
    request = sent[0]
    assert request["url"] == "https://api.perplexity.ai/search"
    assert request["headers"] == {"Authorization": "Bearer ppx-key"}
    body = request["body"]
    assert body["search_type"] == "fast"
    month, day, year = body["search_after_date_filter"].split("/")
    assert len(year) == 4 and len(month) == 2 and len(day) == 2


def test_the_local_rung_sends_the_known_outlets_to_both(wire, monkeypatch):
    sent, _ = wire
    for backend, field_name in (("parallel", None), ("perplexity", None)):
        use(monkeypatch, research_backend=backend)
        asyncio.run(research.retrieve("San Anselmo council", backend=backend,
                                      brief=brief(),
                                      include_domains=("marinij.com",)))
    assert sent[0]["body"]["advanced_settings"]["source_policy"][
        "include_domains"] == ["marinij.com"]
    assert sent[1]["body"]["search_domain_filter"] == ["marinij.com"]


# --------------------------------------------------------------------------
# the replies
# --------------------------------------------------------------------------
def test_results_reach_the_packet_dated_graded_and_in_passages(wire, monkeypatch):
    use(monkeypatch, research_backend="parallel")
    packet = asyncio.run(research.retrieve("Eagles Giants score", brief=brief()))
    assert "Title: Eagles beat Giants" in packet.context
    assert "Published: 2026-10-11" in packet.context
    assert "wire service" in packet.context
    # The packet carries grades, never hostnames.
    assert "reuters" not in packet.context
    assert "The win moves them to 5-1." in packet.context
    assert packet.cost == research.SEARCH_COSTS["parallel"]
    assert packet.provenance.retrievers == ["parallel"]


def test_a_last_updated_date_is_never_read_as_publication(wire, monkeypatch):
    """An old page edited yesterday is not news from yesterday."""
    use(monkeypatch, research_backend="perplexity")
    results, _ = research._search_perplexity("x", 8)
    edited = [r for r in results if r.title == "Edited later"][0]
    assert edited.published_date == ""
    assert research.published_at(edited) is None


def test_one_long_snippet_becomes_several_passages():
    text = "Philadelphia won 27-17. " * 60
    cut = research.passages(text, 300)
    assert len(cut) > 2
    assert all(len(p) <= 303 for p in cut)
    # Markdown headings and bullets do not reach the voice.
    assert research.passages(["## Heading\n- a bullet that is long enough"]) == [
        "Heading a bullet that is long enough"]
    # A minus sign is not a bullet.
    assert research.passages(["-3.5% was the fall in the index on Monday."]) == [
        "-3.5% was the fall in the index on Monday."]


# --------------------------------------------------------------------------
# the second look and the ladder
# --------------------------------------------------------------------------
def test_the_second_look_goes_to_perplexity_without_the_window(wire, monkeypatch):
    sent, replies = wire
    replies[research.PARALLEL_URL] = [{"results": []}]
    use(monkeypatch, research_backend="parallel")
    packet = asyncio.run(research.retrieve("Eagles Giants score", brief=brief()))
    assert [r["url"] for r in sent] == [research.PARALLEL_URL,
                                        research.PERPLEXITY_URL]
    assert "search_after_date_filter" not in sent[1]["body"]
    assert sent[1]["body"]["query"] == "Eagles season", "the second look asks the broader query"
    assert packet.retried and packet.backend == "perplexity"
    assert packet.tried == ["parallel", "perplexity"]
    assert packet.cost == pytest.approx(0.002), "both searches are paid for"


def test_without_a_perplexity_key_the_second_look_stays_on_parallel(
        wire, monkeypatch):
    sent, replies = wire
    monkeypatch.delenv("PERPLEXITY_API_KEY")
    import credentials

    credentials.reset()
    replies[research.PARALLEL_URL] = [{"results": []}]
    use(monkeypatch, research_backend="parallel")
    asyncio.run(research.retrieve("Eagles Giants score", brief=brief()))
    assert [r["url"] for r in sent] == [research.PARALLEL_URL] * 2
    assert research.ladder("parallel") == ["parallel"] + (
        ["gdelt"] if research._rung_available("gdelt") else [])


def test_second_look_same_keeps_the_retry_on_the_configured_backend(
        wire, monkeypatch):
    sent, replies = wire
    replies[research.PARALLEL_URL] = [{"results": []}]
    use(monkeypatch, research_backend="parallel", research_second_look="same")
    asyncio.run(research.retrieve("Eagles Giants score", brief=brief()))
    assert [r["url"] for r in sent] == [research.PARALLEL_URL] * 2


def test_the_ladder_is_parallel_then_perplexity_then_gdelt(wire, monkeypatch):
    use(monkeypatch, research_backend="parallel")
    monkeypatch.setattr(research, "_rung_available",
                        lambda rung: rung in ("perplexity", "gdelt"))
    assert research.ladder() == ["parallel", "perplexity", "gdelt"]


def test_exa_still_researches_while_its_key_is_kept(wire, monkeypatch):
    """A deploy that lands before the new keys are set must not lose research:
    with only EXA_API_KEY, Exa is the rung that serves."""
    use(monkeypatch, research_backend="parallel")
    monkeypatch.setattr(research, "_rung_available", lambda rung: rung == "exa")
    assert research.ladder() == ["parallel", "exa"]


def test_the_local_rung_falls_to_perplexity_when_parallel_cannot_serve(
        wire, monkeypatch):
    """The town's outlets are still searched with only one search key."""
    sent, _ = wire
    monkeypatch.delenv("PARALLEL_API_KEY")
    import credentials

    credentials.reset()
    use(monkeypatch, research_backend="parallel")
    seen = {}

    async def research_local(place, brief=None, exa=None):
        seen["packet"] = await exa("San Anselmo council", ("marinij.com",), "")
        return types.SimpleNamespace(packet=seen["packet"], gap=False,
                                     attempts=[], as_dict=lambda: {})

    import local_news
    import places

    async def resolve(text):
        return None

    monkeypatch.setattr(local_news, "research_local", research_local)
    monkeypatch.setattr(places, "resolve", resolve)
    monkeypatch.setattr(sg, "settings", dataclasses.replace(
        sg.settings, weather=False))
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = dataclasses.replace(plan_episode("san anselmo news", 2, search=True),
                               brief=brief(place="San Anselmo, CA"))
    asyncio.run(generator._research_local(plan, ScriptNotes(), "San Anselmo, CA"))
    assert [r["url"] for r in sent] == [research.PERPLEXITY_URL]
    assert seen["packet"].backend == "perplexity"


def test_a_long_question_is_cut_for_parallel(wire, monkeypatch):
    sent, _ = wire
    use(monkeypatch, research_backend="parallel")
    asyncio.run(research.retrieve("why " * 200, brief=brief()))
    assert len(sent[0]["body"]["search_queries"][0]) <= research.PARALLEL_MAX_QUERY_CHARS + 3


def test_a_parallel_outage_falls_to_perplexity_and_says_so(wire, monkeypatch):
    sent, replies = wire
    replies[research.PARALLEL_URL] = [RuntimeError("502 from parallel")]
    use(monkeypatch, research_backend="parallel")
    notes = ScriptNotes()
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = dataclasses.replace(plan_episode("eagles score", 2, search=True),
                               brief=brief())
    researched = asyncio.run(generator.research(plan, notes))
    assert researched.evidence
    assert notes.research["backend"] == "perplexity"
    assert notes.research["fell_back_from"] == "parallel"
    assert notes.research["rungs"][:2] == ["parallel", "perplexity"]
    # The failed vendor is counted as a failure on /admin.
    row = [r for r in provider_usage.report() if r["provider"] == "parallel"][0]
    assert row["failed_today"] >= 1, row


def test_a_rung_that_ran_as_the_second_look_is_not_asked_again(wire, monkeypatch):
    sent, replies = wire
    replies[research.PARALLEL_URL] = [{"results": []}]
    replies[research.PERPLEXITY_URL] = [{"results": []}]
    use(monkeypatch, research_backend="parallel")
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = dataclasses.replace(plan_episode("eagles score", 2, search=True),
                               brief=brief())
    asyncio.run(generator.research(plan, ScriptNotes()))
    assert [r["url"] for r in sent].count(research.PERPLEXITY_URL) == 1


def test_every_paid_search_is_metered(wire, monkeypatch):
    sent, replies = wire
    replies[research.PARALLEL_URL] = [{"results": []}]
    use(monkeypatch, research_backend="parallel")
    notes = ScriptNotes()
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = dataclasses.replace(plan_episode("eagles score", 2, search=True),
                               brief=brief())
    asyncio.run(generator.research(plan, notes))
    assert notes.usage.exa_searches == 2
    assert notes.usage.exa_cost == pytest.approx(0.002)


# --------------------------------------------------------------------------
# configuration, staging and consent
# --------------------------------------------------------------------------
@pytest.mark.parametrize("field_name, value", [
    ("research_second_look", "gdelt"), ("research_second_look", "google"),
    ("parallel_mode", "slow"), ("perplexity_search_type", "pro"),
])
def test_a_value_that_is_not_a_choice_is_refused(field_name, value):
    with pytest.raises(ValueError):
        dataclasses.replace(config.settings, **{field_name: value})


@pytest.mark.parametrize("name, attr, default", [
    ("RESEARCH_SECOND_LOOK", "research_second_look", "perplexity"),
    ("PARALLEL_MODE", "parallel_mode", "fast"),
    ("PERPLEXITY_SEARCH_TYPE", "perplexity_search_type", "fast"),
])
def test_an_empty_setting_is_its_default_not_a_crash(monkeypatch, name, attr,
                                                     default):
    """A key added in a dashboard with no value must not stop the server."""
    monkeypatch.setenv(name, "")
    assert getattr(config.Settings(), attr) == default


def test_staging_removes_both_keys():
    assert {"PARALLEL_API_KEY", "PERPLEXITY_API_KEY"} <= set(
        spend_guard.PAID_CREDENTIALS)


def test_a_missing_key_is_named_and_refused_not_searched(monkeypatch):
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    import credentials

    credentials.reset()
    use(monkeypatch, research_backend="parallel")
    ok, detail = research.backend_diagnose("parallel")
    assert not ok and "PARALLEL_API_KEY" in detail
    with pytest.raises(research.ResearchUnavailable, match="PARALLEL_API_KEY"):
        asyncio.run(research.retrieve("x"))


def test_the_new_wording_asks_everyone_again():
    """A yes to a notice that named Exa is not a yes to Parallel."""
    assert consent.VERSION >= 2
    body = consent.AI_NOTICE["body"]
    assert "Parallel" in body and "Perplexity" in body and "Exa" not in body
