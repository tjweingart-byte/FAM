"""Web retrieval, and Claude reading the packet instead of searching.

This codebase retrieves first - Parallel, then Perplexity, then GDELT when
the web backends come back empty - builds an evidence packet and puts it in the prompt, so the model reads rather
than searches. The model never searches: the `claude` backend that gave a
research call Anthropic's `web_search` tool is deleted (§135).

The packet is byte-for-byte the one the manual benchmark measured on
2026-09-05 and the experiment layer then repeated. That is the point of it:
the numbers already taken by hand stay comparable, so a change of shape has to
be deliberate. Several tests below pin that shape for exactly that reason.

Nothing here needs a PARALLEL_API_KEY or the network. The one Parallel
request (`research._search_parallel`) is stubbed, so the real retrieve path -
ranking, screening, packing, the retry and the ladder - still runs; a test
that stubbed `retrieve` itself would prove only that the stub works. The
request Parallel and Perplexity actually receive is pinned at `research._post`
in `test_search_backends_258.py`.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import threading
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import research  # noqa: E402
import script_generator as sg  # noqa: E402
from config import RESEARCH_BACKENDS, settings  # noqa: E402
from script_generator import ScriptNotes, build_prompt, plan_episode  # noqa: E402


class FakeResult:
    def __init__(self, title, url, highlights, published_date=None):
        self.title, self.url, self.highlights = title, url, highlights
        # Every web backend returns this and `build_packet` used to throw it
        # away, which is
        # what left episodes dating events by guesswork. Defaulted to None so
        # the undated case - a real one - stays exercised by the tests that do
        # not care about dates.
        self.published_date = published_date


RESULTS = [
    FakeResult("Fed holds rates", "https://reuters.com/a",
               ["Rates held at 4.25%.", "Third hold running.", "Ignored third."]),
    FakeResult("Markets react", "https://ft.com/b", ["Yields fell 6bp."]),
    FakeResult("What it means", "https://reuters.com/c", ["Cuts priced for June."]),
    FakeResult("Fourth source", "https://bbc.co.uk/d", ["Should not appear."]),
]


def stub_parallel(monkeypatch, answer) -> list:
    """Replace the one Parallel request with `answer(call)`, recording calls.

    `answer` gets the recorded call and returns the results, or raises as the
    vendor would. Everything around the request - ranking, screening, the
    packet, the retry, the ladder - is the real code.
    """
    calls: list = []

    def fake(query, num_results, recency_days=0, include_domains=(),
             objective="", extra_queries=(), hints=None):
        call = {"query": query, "num_results": num_results,
                "recency_days": recency_days,
                "include_domains": tuple(include_domains),
                "objective": objective, "extra_queries": tuple(extra_queries),
                "thread": threading.current_thread().name}
        calls.append(call)
        return answer(call), WEB_COST

    monkeypatch.setattr(research, "_search_parallel", fake)
    use_backend(monkeypatch, "parallel")
    return calls


#: What the stubbed search says it spent - deliberately not Parallel's
#: published rate, so a test can tell the backend's own figure was carried.
WEB_COST = 0.0031


@pytest.fixture
def web(monkeypatch):
    """A stubbed Parallel search, recording exactly how it was called."""
    return stub_parallel(monkeypatch, lambda call: RESULTS)


@pytest.fixture
def fake_gdelt(monkeypatch):
    """GDELT switched on and answering, without the network.

    Patched at `gdelt.retrieve`, so the real `research.retrieve_with_gdelt`
    runs - ranking, grading, dating and provenance included. A fixture that
    patched the rung itself would prove only that the stub works.
    """
    import gdelt

    calls: list = []

    async def retrieve(query, limit=0, recency_days=0):
        calls.append({"query": query, "recency_days": recency_days})
        return [gdelt._Result(
            title="Fed holds rates", url="https://reuters.com/a",
            published_date="2026-09-18", highlights=["Fed holds rates"],
            country="United States")]

    monkeypatch.setattr(gdelt, "settings",
                        dataclasses.replace(gdelt.settings, gdelt=True))
    monkeypatch.setattr(gdelt, "retrieve", retrieve)
    return calls


def use_backend(monkeypatch, value: str):
    patched = dataclasses.replace(settings, research_backend=value)
    monkeypatch.setattr(research, "settings", patched)
    monkeypatch.setattr(sg, "settings", patched)
    return patched


# --------------------------------------------------------------------------
# the packet, pinned to the benchmark
# --------------------------------------------------------------------------
def test_the_packet_is_shaped_as_the_benchmark_built_it():
    """The benchmark's shape, still reachable, still pinned.

    `SEARCH_DATED_PACKET=0` reproduces the packet the hand-measured 2026-09-05 run
    used - `SOURCE n / Title: / Key evidence:`, top 3, 2 highlights each - so
    those numbers stay comparable to anything measured against it today.
    """
    import dataclasses

    import config

    undated = dataclasses.replace(config.settings, search_dated_packet=False)
    original, config.settings = config.settings, undated
    research.settings = undated
    try:
        packet = research.build_packet(RESULTS, packet_sources=3,
                                       highlights_per_source=2)
    finally:
        config.settings = original
        research.settings = original
    assert packet.startswith("SOURCE 1\nTitle: Fed holds rates\nKey evidence:\n")
    assert "SOURCE 3" in packet and "SOURCE 4" not in packet
    assert "Ignored third." not in packet, "more than 2 highlights reached the packet"
    assert "Should not appear." not in packet, "a 4th source reached the packet"


def test_the_packet_dates_and_grades_every_source():
    """The shape production uses, and why it changed.

    An episode has to decide between "last night" and "two days ago", and the
    packet used to give it a title and some highlights to decide on. Both
    fields were already in the search's reply and were being discarded. The relative
    phrase is computed here rather than left to the model, so the blueprint's
    rule - relative labels come from normalized time, never guessed from prose
    - is enforced rather than requested.
    """
    packet = research.build_packet(RESULTS, packet_sources=3,
                                   highlights_per_source=2)
    assert "SOURCE 1\nTitle: " in packet
    assert "Published: " in packet
    assert "Source type: " in packet
    assert "Key evidence:" in packet
    assert "SOURCE 3" in packet and "SOURCE 4" not in packet
    assert "Ignored third." not in packet, "more than 2 highlights reached the packet"


def test_an_undated_source_says_so_rather_than_being_dated_today():
    """The one thing worse than no date is a wrong one. A source the search did not
    date must not acquire today's date on the way into the prompt."""
    packet = research.build_packet(
        [FakeResult("No date", "https://example.org/x", ["Something."])], 1, 1)
    assert "Published: unknown (date not stated)" in packet


def test_the_packet_carries_no_urls_or_numbers_for_the_voice_to_read():
    """It is spoken aloud downstream. A URL in the packet is a URL a model can
    read out, and a listener is not looking at a citation list.

    This is why the source grade reaches the prompt as a *description* rather
    than as the hostname it was derived from: the model needs to know it is
    reading a wire service in order to weigh it, and needs no way to say
    "reuters dot com" out loud. `domains()` still reports the hosts, out of
    band, for a person to judge.
    """
    packet = research.build_packet(RESULTS, 3, 2)
    assert "https://" not in packet
    assert "reuters.com" not in packet
    assert "ft.com" not in packet


def test_an_empty_result_set_is_an_empty_packet():
    assert research.build_packet([], 3, 2) == ""
    assert not research.Packet(context="")
    assert not research.Packet(context="   \n ")


def test_domains_are_distinct_and_ordered_for_a_person_to_judge():
    assert research.domains(RESULTS) == ["reuters.com", "ft.com", "bbc.co.uk"]


# --------------------------------------------------------------------------
# the call
# --------------------------------------------------------------------------
def test_retrieval_makes_the_call_the_benchmark_made(web):
    packet = asyncio.run(research.retrieve("what did the fed do"))
    assert len(web) == 1
    call = web[0]
    assert call["query"] == "what did the fed do"
    assert call["num_results"] == settings.search_num_results == 10
    # No brief: no window, no outlets, and nothing else to search for.
    assert call["recency_days"] == 0
    assert call["include_domains"] == ()
    assert packet.backend == "parallel" and packet.searches == 1
    assert packet.tried == ["parallel"]
    assert packet.results_returned == 4


def test_retrieval_does_not_run_on_the_event_loop(web):
    """The cover is speaking and the assembler is batching while this runs. A
    synchronous HTTP call on the loop would stop both - the same guarantee the
    speech engines are held to."""
    asyncio.run(research.retrieve("anything"))
    call = web[0]
    assert call["thread"] != "MainThread", (
        f"retrieval ran on the event loop, in {call['thread']}")


def test_the_reported_cost_is_the_backend_s_own(web):
    assert asyncio.run(research.retrieve("q")).cost == pytest.approx(WEB_COST)


def test_a_parallel_search_costs_its_published_rate(monkeypatch):
    """Parallel's reply carries no price, so a search is metered at the
    published rate (`SEARCH_COSTS`) - through the real request function."""
    monkeypatch.setenv("PARALLEL_API_KEY", "parallel-test-key-not-real")
    monkeypatch.setattr(research, "_post", lambda url, headers, body: {
        "results": [{"url": "https://reuters.com/a", "title": "Fed holds rates",
                     "excerpts": ["Rates held at 4.25% for a third meeting."]}]})
    use_backend(monkeypatch, "parallel")
    packet = asyncio.run(research.retrieve("q"))
    assert packet.cost == research.SEARCH_COSTS["parallel"]
    assert packet.searches == 1


def test_the_credential_never_reaches_the_result(monkeypatch):
    """The key goes in the request's header and nowhere else."""
    sent: list = []

    def post(url, headers, body):
        sent.append(headers)
        return {"results": [{"url": "https://reuters.com/a",
                             "title": "Fed holds rates",
                             "excerpts": ["Rates held at 4.25% for a third "
                                          "meeting running."]}]}

    monkeypatch.setenv("PARALLEL_API_KEY", "parallel-test-key-not-real")
    monkeypatch.setattr(research, "_post", post)
    use_backend(monkeypatch, "parallel")
    packet = asyncio.run(research.retrieve("q"))
    assert sent and sent[0]["x-api-key"] == "parallel-test-key-not-real"
    assert packet, "the stubbed reply did not reach the packet"
    assert "parallel-test-key-not-real" not in str(packet.as_dict())
    assert "parallel-test-key-not-real" not in packet.context


# --------------------------------------------------------------------------
# it refuses rather than guessing
# --------------------------------------------------------------------------
def test_the_claude_backend_is_gone_and_says_so():
    """The model never searches for FAM (§135). Deleted rather than switched
    off, so there is no rung, no backend and no function left to turn back
    on - and a deployment that still sets it is told what happened, by name,
    rather than being refused as though it had made a typo."""
    assert "claude" not in RESEARCH_BACKENDS
    # Perplexity is a search vendor's index, not a model searching (§258).
    assert research.FALLBACK_RUNGS == ("perplexity", "parallel", "gdelt")
    for name in ("retrieve_with_claude", "shape_claude_packet",
                 "research_client", "CLAUDE_RESEARCH_SYSTEM"):
        assert not hasattr(research, name), f"research.{name} is still here"
    # A deployment still setting it runs on the default, and says so -
    # replaced rather than refused, so the deploy that removed it still boots.
    left_over = dataclasses.replace(settings, research_backend="claude")
    assert left_over.research_backend == config.DEFAULT_RESEARCH_BACKEND
    assert left_over.research_backend_replaced == "claude"
    with pytest.raises(research.ResearchUnavailable, match="is not a backend"):
        asyncio.run(research.retrieve("q", backend="claude"))


@pytest.mark.parametrize("value", ["parallell", "web", "google", "none",
                                   "bing", "claude"])
def test_an_unrecognised_backend_is_refused_at_retrieval(value, monkeypatch):
    """The second gate. `Settings.__post_init__` is bypassable; this is not.

    `""` is deliberately not here: an empty override means "not specified", so
    it falls through to the configured backend rather than being a bad value.
    Nor is `"Parallel "` - case and surrounding whitespace normalise, exactly
    as they do for STREAMING_PIPELINE, because a deployment writing `PARALLEL`
    means parallel and refusing that is pedantry rather than safety.
    """
    use_backend(monkeypatch, "parallel")
    with pytest.raises(research.ResearchUnavailable) as exc:
        asyncio.run(research.retrieve("q", backend=value))
    assert "is not a backend" in str(exc.value)
    assert "falling back" in str(exc.value)


@pytest.mark.parametrize("value", ["PARALLEL", " parallel ", "Parallel"])
def test_case_and_whitespace_normalise_rather_than_being_refused(value,
                                                                 monkeypatch,
                                                                 web):
    """The tolerance is deliberate, and the same as STREAMING_PIPELINE's: a
    deployment writing `RESEARCH_BACKEND=PARALLEL` means parallel. It is the
    unrecognisable that is refused, not the differently-cased."""
    packet = asyncio.run(research.retrieve("q", backend=value))
    assert packet.backend == value.strip().lower()


def test_a_missing_key_names_the_variable_and_the_way_out(monkeypatch):
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("PARALLEL_API_KEYS", raising=False)
    use_backend(monkeypatch, "parallel")
    with pytest.raises(research.ResearchUnavailable) as exc:
        asyncio.run(research.retrieve("q"))
    assert "PARALLEL_API_KEY" in str(exc.value)
    assert "RESEARCH_BACKEND" in str(exc.value)


def test_a_search_failure_does_not_fall_back_inside_retrieve():
    """The failure this refuses. An episode that asked for Parallel and quietly got
    another retriever is unattributable - the fallback belongs to the ladder,
    which records it, and never to `retrieve`, which would not.

    Parsed rather than grepped: a substring search for "except" also matches
    the word "exception" in a comment, which is how this test first passed
    while proving nothing.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(research.retrieve)))
    handlers = [node for node in ast.walk(tree)
                if isinstance(node, ast.ExceptHandler)]
    assert not handlers, (
        "retrieve catches something; a failed backend must reach the caller "
        "rather than being turned into a different kind of research")

    # The one place allowed to swallow, and only that one. A *second* search
    # failing means research already happened and an optional improvement to it
    # did not - throwing the first packet away for that would make the episode
    # worse for nobody's benefit. Pinned here so the exemption cannot quietly
    # widen back into `retrieve`.
    second = ast.parse(textwrap.dedent(inspect.getsource(research._second_look)))
    assert len([n for n in ast.walk(second)
                if isinstance(n, ast.ExceptHandler)]) == 1, (
        "_second_look is the single deliberate exemption; it should have "
        "exactly one handler")


def test_a_backend_that_cannot_run_raises_instead_of_switching(monkeypatch):
    """The behaviour that check is about, exercised rather than read."""
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("PARALLEL_API_KEYS", raising=False)
    use_backend(monkeypatch, "parallel")
    with pytest.raises(research.ResearchUnavailable):
        asyncio.run(research.retrieve("q"))


# --------------------------------------------------------------------------
# Claude reads the packet
# --------------------------------------------------------------------------
def test_evidence_reaches_the_prompt_and_the_tool_does_not(web, monkeypatch):
    """The whole point: Claude reads rather than searches."""
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = plan_episode("what did the fed do today", 3, search=True)
    notes = ScriptNotes()

    researched = asyncio.run(generator.research(plan, notes))
    assert researched.evidence, "no evidence was attached to the plan"

    prompt = build_prompt(researched)
    assert "<evidence>" in prompt
    assert "Rates held at 4.25%." in prompt
    kwargs = generator._request_kwargs(researched)
    assert "tools" not in kwargs, (
        "the search tool was attached on top of an evidence packet")


def test_a_search_that_cannot_run_leaves_the_episode_answerable(monkeypatch):
    """A layer that adds quality must not subtract availability. No key, a
    refusal, a timeout - all of them are an unresearched episode, which is a
    thing this app already knows how to be."""
    import gdelt

    use_backend(monkeypatch, "parallel")
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("PARALLEL_API_KEYS", raising=False)
    monkeypatch.setattr(gdelt, "settings",
                        dataclasses.replace(gdelt.settings, gdelt=False))
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = plan_episode("what did the fed do today", 3, search=True)
    researched = asyncio.run(generator.research(plan, ScriptNotes()))
    assert researched is plan
    assert "tools" not in generator._request_kwargs(researched)


def test_an_unresearched_episode_never_retrieves(web, monkeypatch):
    """The default question costs nothing either way, and must not reach the
    search."""
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = plan_episode("what is the nasdaq", 3, search=False)
    assert asyncio.run(generator.research(plan, ScriptNotes())) is plan
    assert not web, "an unresearched episode searched"


def test_an_empty_packet_falls_to_gdelt_before_writing(monkeypatch,
                                                       fake_gdelt):
    """Retrieval succeeded and found nothing usable.

    With no Perplexity key the next rung is GDELT, and it runs before the
    first word, and the episode's own record names the backend that could not
    serve it.
    """
    stub_parallel(monkeypatch, lambda call: [])

    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = plan_episode("todays news", 3, search=True)
    notes = ScriptNotes()
    researched = asyncio.run(generator.research(plan, notes))
    assert "Fed holds rates" in researched.evidence
    assert "tools" not in generator._request_kwargs(researched)
    assert notes.research["backend"] == "gdelt"
    assert notes.research["fell_back_from"] == "parallel"


def test_a_backend_that_cannot_run_falls_back_out_loud(monkeypatch, fake_gdelt):
    """A web backend with no key used to raise, and the episode failed. What
    made that survivable was the cover half speaking underneath it, which is
    gone - so the other retriever gets one go, and says on the record that
    it did."""
    use_backend(monkeypatch, "parallel")
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("PARALLEL_API_KEYS", raising=False)

    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    notes = ScriptNotes()
    researched = asyncio.run(generator.research(
        plan_episode("todays news", 3, search=True), notes))
    assert researched.evidence
    assert notes.research["backend"] == "gdelt"
    assert notes.research["fell_back_from"] == "parallel"


def test_a_retriever_that_breaks_never_takes_the_episode_with_it(monkeypatch,
                                                                 fake_gdelt):
    """The availability rule, applied to what replaced the thing it was
    written for.

    `research.retrieve` raises `ResearchUnavailable` for a missing key and
    raises *whatever the vendor raised* for everything else - a 500, a
    timeout, a rate limit, a malformed reply. While the from-knowledge cover
    existed, that was survivable: the cover was already speaking and the
    researched half simply never arrived. With one stream it is the whole
    episode, so a blip at a search vendor would be a listener getting no
    audio at all.
    """
    def broken(call):
        raise RuntimeError("502 from the index")

    stub_parallel(monkeypatch, broken)

    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    notes = ScriptNotes()
    researched = asyncio.run(generator.research(
        plan_episode("todays news", 3, search=True), notes))

    # It did not raise, and it did not give up either: the next rung ran.
    assert "Fed holds rates" in researched.evidence
    assert notes.research["fell_back_from"] == "parallel"


def test_every_rung_failing_is_an_unresearched_episode_not_a_dead_one(monkeypatch):
    """The bottom of the ladder. Nothing retrieved, and the episode still
    exists - which is the state an unresearched episode has always been in."""
    def broken(call):
        raise RuntimeError("502 from the index")

    stub_parallel(monkeypatch, broken)

    import gdelt

    async def broken_gdelt(query, limit=0, recency_days=0):
        raise RuntimeError("GDELT is down too")

    monkeypatch.setattr(gdelt, "settings",
                        dataclasses.replace(gdelt.settings, gdelt=True))
    monkeypatch.setattr(gdelt, "retrieve", broken_gdelt)
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = plan_episode("todays news", 3, search=True)
    researched = asyncio.run(generator.research(plan, ScriptNotes()))
    assert researched is plan
    assert "tools" not in generator._request_kwargs(researched)


def test_a_packet_with_nothing_in_it_always_buys_the_second_look(monkeypatch):
    """The retry was gated on the brief naming something to establish, and
    `packet_covers("", [])` is True - so a search that found *absolutely
    nothing* counted as satisfied and the one retry that exists for this case
    never ran. The commonest way in is the recency window."""
    # Nothing inside the first window; something in the wider one.
    searched = stub_parallel(
        monkeypatch, lambda call: [] if call["recency_days"] == 1 else RESULTS)

    packet = asyncio.run(research.retrieve(
        "what happened overnight",
        brief=types.SimpleNamespace(recency_days=1, subject="the thing",
                                    retrieval="what happened overnight",
                                    must_establish=[])))
    calls = [call["recency_days"] for call in searched]
    assert len(calls) == 2, "the second look never ran"
    # §320: wider, never none - a dropped window let a March 2025 article
    # into a "last 24 hours" episode.
    assert calls[0] and calls[1], "the second look must keep a window"
    assert calls[1] > calls[0], "the second look's window must be wider"
    assert packet, "the retry found sources and they did not reach the packet"
    assert packet.retried


def test_the_prompt_tells_the_model_not_to_read_sources_aloud(web, monkeypatch):
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = asyncio.run(generator.research(
        plan_episode("what did the fed do today", 3, search=True), ScriptNotes()))
    prompt = build_prompt(plan)
    assert "Never read a source's title, number, date or URL aloud" in prompt
    assert "they win and you say so plainly" in prompt, (
        "the model must be told the evidence outranks what it recalls")


def test_what_retrieval_cost_is_recorded_for_a_person_to_read(web, monkeypatch):
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    notes = ScriptNotes()
    asyncio.run(generator.research(
        plan_episode("what did the fed do today", 3, search=True), notes))
    assert notes.research["backend"] == "parallel"
    assert notes.research["sources"] == ["reuters.com", "ft.com", "bbc.co.uk"]
    assert notes.research["cost"] == pytest.approx(WEB_COST)
    assert notes.research["packet_chars"] > 0


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------
def test_a_fresh_deployment_researches_with_perplexity():
    """The production default since §357, at the owner's direction (Parallel
    from §258): it needs a credential. Parallel is the second look and the
    next rung, and the keyless alternative is GDELT (§135)."""
    assert config.DEFAULT_RESEARCH_BACKEND == "perplexity"
    assert settings.research_backend == "perplexity"
    assert RESEARCH_BACKENDS == ("parallel", "perplexity", "gdelt")
    assert settings.research_second_look == "parallel"


def test_the_ladder_tries_the_other_index_then_gdelt(monkeypatch):
    """Whichever web backend is primary, the other comes next, then GDELT
    (§357) - and the primary is never asked twice."""
    monkeypatch.setattr(research, "_rung_available", lambda rung: True)
    assert research.ladder("perplexity") == ["perplexity", "parallel", "gdelt"]
    assert research.ladder("parallel") == ["parallel", "perplexity", "gdelt"]


def test_the_default_is_one_fact_in_one_place():
    """The constant and the setting cannot disagree, because the setting is
    built from the constant rather than repeating the literal."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "config.py").read_text()
    assert '"RESEARCH_BACKEND", DEFAULT_RESEARCH_BACKEND' in source
    assert settings.research_backend == config.DEFAULT_RESEARCH_BACKEND


def test_a_keyless_configuration_is_still_one_variable(monkeypatch, fake_gdelt):
    """A deployment with no search key must have a working configuration to move
    to, not a broken one to endure - and since §135 that is GDELT."""
    patched = dataclasses.replace(settings, research_backend="gdelt")
    monkeypatch.setattr(research, "settings", patched)
    assert asyncio.run(research.retrieve("q")), (
        "the keyless configuration must need no key and no package")


def test_the_env_example_ships_the_default_it_documents():
    """Following the documented setup must not configure the product against
    itself - PROBLEMS.md 54."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    lines = [line.strip() for line in (root / ".env.example").read_text().splitlines()]
    assert f"RESEARCH_BACKEND={config.DEFAULT_RESEARCH_BACKEND}" in lines


def test_every_image_installs_what_the_default_backend_needs():
    """Render once started with research already broken, and said so in a log
    line: the default backend's package was declared in a requirements file
    the CPU Dockerfile did not install (§54's shape - a setting settled in one
    place and not in the one that shipped).

    Parallel and Perplexity need nothing but `httpx`, which every image gets
    from requirements.txt.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    requirements = [line.split("#", 1)[0].strip().lower() for line in
                    (root / "requirements.txt").read_text().splitlines()]
    assert any(line.startswith("httpx") for line in requirements)
    for name in ("Dockerfile", "Dockerfile.gpu"):
        text = (root / name).read_text()
        assert "requirements.txt" in text


@pytest.mark.parametrize("value", ["bing", "parallell", "web", "", "google"])
def test_an_unrecognised_backend_is_refused_at_import(value):
    with pytest.raises(ValueError, match="is not a research backend"):
        dataclasses.replace(settings, research_backend=value)


@pytest.mark.parametrize("field, value", [
    ("search_num_results", 0), ("search_packet_sources", 0),
    ("search_highlights_per_source", 0), ("search_num_results", -1),
])
def test_a_zero_knob_is_refused_rather_than_sending_an_empty_packet(field, value):
    with pytest.raises(ValueError, match="must be at least 1"):
        dataclasses.replace(settings, **{field: value})


def test_the_packet_cannot_ask_for_more_sources_than_were_fetched():
    with pytest.raises(ValueError, match="exceeds"):
        dataclasses.replace(settings, search_num_results=3, search_packet_sources=5)


def test_health_says_which_backend_and_whether_it_can_run():
    report = research.report()
    assert report["backend"] == "perplexity"
    assert "backend_detail" in report
    assert set(report["web_backends"]) == {"parallel", "perplexity"}
    # In this container there is no PERPLEXITY_API_KEY, and the default is
    # perplexity - so `unavailable` is true, and that is the honest answer
    # rather than a test failure. What must never happen is it reading false
    # while research cannot run.
    ok, _ = research.backend_diagnose("perplexity")
    assert report["unavailable"] is (not ok)


def test_a_deployment_that_cannot_research_says_so_at_startup(caplog):
    """Not on a listener's first researched question. `perplexity` is the default and
    needs a credential; a missing one discovered mid-episode is the shape of
    failure this project has paid for most."""
    import logging

    import app

    with caplog.at_level(logging.WARNING, logger="app"):
        app._announce_research()

    if research.backend_available(research.settings.research_backend):  # pragma: no cover - not in this container
        pytest.skip("this machine can research; nothing to announce")
    messages = [record.getMessage() for record in caplog.records]
    assert any("RESEARCH UNAVAILABLE" in m for m in messages), messages
    # **What it says changed with §109 and the test had to change with it.**
    # It used to promise that researched episodes would FAIL; they now fall
    # down the ladder instead, so the warning names the rungs that will
    # actually serve. A warning that describes a failure mode the code no
    # longer has sends the next person looking for the wrong thing.
    assert any("fall down the ladder" in m for m in messages), messages
    # Named from `research.ladder()` rather than from a list written out
    # here, so the warning stays true of whatever this deployment has
    # switched on - with GDELT=0, the shipped default, it is nothing at all
    # since the model's own search went (§135), and it says so.
    rungs = research.ladder()[1:]
    assert any(all(rung in m for rung in rungs) for m in messages), (
        f"the warning must name the rungs that will actually serve: {rungs}")
    if not rungs:
        assert any("nothing else" in m for m in messages), messages
    assert not any("will FAIL" in m for m in messages)
    assert any("RESEARCH_BACKEND=gdelt" in m for m in messages), (
        "the warning must name the working configuration to move to")


def test_the_startup_warning_is_not_fatal():
    """Most questions are not researched. An app that refuses to start because
    one path is unconfigured is worse than one that starts and says which."""
    import app

    assert app._announce_research() is None


def test_health_names_the_missing_key(monkeypatch):
    """The prerequisite a deployment hits is named, by variable."""
    use_backend(monkeypatch, "parallel")
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("PARALLEL_API_KEYS", raising=False)
    report = research.report()
    assert report["unavailable"] is True
    assert "PARALLEL_API_KEY" in report["backend_detail"]
    assert report["web_backends"]["parallel"]["configured"] is False
    assert "PARALLEL_API_KEY" in report["web_backends"]["parallel"]["detail"]


def test_health_is_clear_when_parallel_can_actually_run(monkeypatch):
    use_backend(monkeypatch, "parallel")
    monkeypatch.setenv("PARALLEL_API_KEY", "parallel-test-key")
    report = research.report()
    assert report["web_backends"]["parallel"]["configured"] is True
    assert report["unavailable"] is False
    assert "parallel-test-key" not in str(report)


def test_the_gdelt_backend_never_reports_unavailable_for_a_missing_search_key(monkeypatch):
    """The web backends' prerequisites are not GDELT's problem."""
    import gdelt

    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(settings, research_backend="gdelt"))
    monkeypatch.setattr(gdelt, "settings",
                        dataclasses.replace(gdelt.settings, gdelt=True))
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    assert research.report()["unavailable"] is False


def test_a_listener_is_told_why_research_failed_not_to_read_a_log():
    """The remedy is already in the exception; throwing it away wastes it.

    ResearchUnavailable says which key is missing and what to set instead.
    friendly_error used to
    reduce that to "Generation failed: ResearchUnavailable. See the server log
    for details." - which is the one thing a person in a browser cannot do.
    """
    from app import friendly_error

    message = friendly_error(
        research.ResearchUnavailable(
            "PARALLEL_API_KEY is not set, so Parallel cannot retrieve "
            "anything. Set it, or set RESEARCH_BACKEND to another backend."))
    assert "PARALLEL_API_KEY is not set" in message
    assert "RESEARCH_BACKEND" in message
    assert "server log" not in message


# --------------------------------------------------------------------------
# through the real pipeline, end to end
# --------------------------------------------------------------------------
def test_a_researched_episode_plays_with_claude_reading_the_search_packet(
        web, monkeypatch):
    """The whole path: Parallel retrieves, the packet reaches the prompt, the model
    writes from it, the assembler chunks it and the engine speaks it.

    Nothing is stubbed but the search request itself and the two things that always are in
    this suite - the model and the voice. In particular the pipeline, the
    Phase 6 assembler and the duration contract are the real ones.
    """
    from pipeline import GenerationStats, PodcastPipeline
    from tts import DebugEngine

    seen_prompts: list = []

    class ReadsThePacket(sg.ScriptGenerator):
        """The real generator's research step, with only the model faked."""

        def __init__(self):
            pass

        async def stream_sentences(self, plan, notes=None):
            plan = await self.research(plan, notes)
            seen_prompts.append(build_prompt(plan))
            for i in range(30):
                yield f"Rates were held at four and a quarter percent, point {i}."

        async def top_up(self, plan, spoken_so_far, words_needed, notes=None):
            return
            yield ""  # pragma: no cover

    async def run():
        stats = GenerationStats()
        pipe = PodcastPipeline(generator=ReadsThePacket(), engine=DebugEngine(),
                               cache=None)
        total = 0
        async for chunk in pipe.stream_pcm(
                plan_episode("what did the fed do today", 1, search=True), stats):
            total += len(chunk)
        return stats, total

    stats, total = asyncio.run(run())

    assert total > 0, "no audio was produced"
    assert stats.sentences > 0

    # **One prompt, and it has the evidence in it.** This used to be two - a
    # cover half with no packet, whose words were the ones a listener heard
    # first, and the researched half behind it. Asserting on `seen_prompts[0]`
    # was checking the cover and calling it the researched half; now there is
    # only the one, and the first thing spoken comes out of it. §108.
    assert len(seen_prompts) == 1
    assert "<evidence>" in seen_prompts[0]
    assert "Rates held at 4.25%." in seen_prompts[0]
    assert "Yields fell 6bp." in seen_prompts[0], "only one source reached the prompt"

    # One retrieval for the episode - not one per sentence.
    assert len(web) == 1
    assert stats.audio_seconds <= 60 + 5, "the duration ceiling did not hold"


def test_research_runs_once_per_episode_not_once_per_sentence(web, monkeypatch):
    """`stream_sentences` calls `research` on entry. If that were inside the
    streaming loop it would pay for a retrieval per sentence, silently."""
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = plan_episode("what did the fed do today", 3, search=True)

    researched = asyncio.run(generator.research(plan, ScriptNotes()))
    # Researching an already-researched plan must be a no-op, or the answer
    # -first path (two calls on one plan) would retrieve twice.
    again = asyncio.run(generator.research(researched, ScriptNotes()))
    assert again is researched
    assert len(web) == 1


def test_the_evidence_survives_being_replaced_onto_the_plan(web, monkeypatch):
    """`research` returns a copy rather than mutating, and several steps in
    `prepare` do the same. The packet must survive all of them."""
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = plan_episode("what did the fed do today", 3, search=True)

    # An unresearched plan never retrieves, whatever the backend is set to.
    unresearched = dataclasses.replace(plan, search=False)
    assert asyncio.run(generator.research(unresearched, ScriptNotes())) is unresearched
    assert not web, "an unresearched episode searched"

    done = asyncio.run(generator.research(plan, ScriptNotes()))
    assert done.evidence
    assert dataclasses.replace(done, minutes=5).evidence == done.evidence



def test_a_left_over_claude_backend_is_announced(monkeypatch, caplog):
    import logging

    import app

    left_over = dataclasses.replace(settings, research_backend="claude")
    monkeypatch.setattr(research, "settings", left_over)
    assert research.report()["backend_replaced"] == "claude"
    with caplog.at_level(logging.WARNING, logger="app"):
        app._announce_research()
    assert any("no longer a backend" in r.getMessage() for r in caplog.records)
