"""Exa retrieval, and Claude reading the packet instead of searching.

Two ways to research an episode, and the difference is who does the looking.
On `claude` the model gets Anthropic's `web_search` tool and searches inside
its own turn. On `exa` this codebase retrieves first, builds an evidence packet
and puts it in the prompt - so the model reads rather than searches.

The packet is byte-for-byte the one the manual benchmark measured on
2026-09-05 and the experiment layer then repeated. That is the point of it:
the numbers already taken by hand stay comparable, so a change of shape has to
be deliberate. Several tests below pin that shape for exactly that reason.

Nothing here needs an EXA_API_KEY, `exa_py`, or the network. The client is
stubbed at its import, so the real call signature is still exercised - a test
that stubbed `retrieve` itself would prove only that the stub works.
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
        # Exa returns this and `build_packet` used to throw it away, which is
        # what left episodes dating events by guesswork. Defaulted to None so
        # the undated case - a real one - stays exercised by the tests that do
        # not care about dates.
        self.published_date = published_date


class FakeReply:
    def __init__(self, results, cost=None):
        self.results = results
        self.cost_dollars = types.SimpleNamespace(total=cost) if cost else None


RESULTS = [
    FakeResult("Fed holds rates", "https://reuters.com/a",
               ["Rates held at 4.25%.", "Third hold running.", "Ignored third."]),
    FakeResult("Markets react", "https://ft.com/b", ["Yields fell 6bp."]),
    FakeResult("What it means", "https://reuters.com/c", ["Cuts priced for June."]),
    FakeResult("Fourth source", "https://bbc.co.uk/d", ["Should not appear."]),
]


@pytest.fixture
def exa(monkeypatch):
    """A stubbed Exa client, recording exactly how it was called."""
    calls: list = []

    class FakeExa:
        def __init__(self, key):
            calls.append({"key": key})

        def search_and_contents(self, query, **kwargs):
            calls.append({"query": query, "thread": threading.current_thread().name,
                          **kwargs})
            return FakeReply(RESULTS, cost=0.0031)

    module = types.ModuleType("exa_py")
    module.Exa = FakeExa
    monkeypatch.setitem(sys.modules, "exa_py", module)
    monkeypatch.setenv("EXA_API_KEY", "exa-test-key-not-real")
    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(settings, research_backend="exa"))
    return calls


#: What the searching call comes back with: text blocks in the shape it was
#: asked for, and the tool-result blocks provenance is read off. Shaped like
#: the SDK's objects rather than like a convenient dict, because the code
#: under test reads `block.type` and `block.text`.
CLAUDE_REPORT = (
    "SOURCE 1\n"
    "Title: Fed holds rates\n"
    "URL: https://reuters.com/a\n"
    "Published: 2026-09-18\n"
    "Key evidence:\n"
    "Rates held at 4.25%, the third hold running.\n"
)


@pytest.fixture
def fake_search(monkeypatch):
    """The model's own search, without a credential or the network."""
    calls: list = []

    class _Message:
        stop_reason = "end_turn"
        usage = types.SimpleNamespace(input_tokens=900, output_tokens=200,
                                      cache_read_input_tokens=0,
                                      cache_creation_input_tokens=0)
        content = [
            types.SimpleNamespace(
                type="web_search_tool_result",
                content=[types.SimpleNamespace(
                    url="https://reuters.com/a", title="Fed holds rates",
                    page_age="1 day ago")]),
            types.SimpleNamespace(type="text", text=CLAUDE_REPORT),
        ]

    class _Messages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return _Message()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(research, "research_client", lambda: _Client())
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

    `EXA_DATED_PACKET=0` reproduces the packet the hand-measured 2026-09-05 run
    used - `SOURCE n / Title: / Key evidence:`, top 3, 2 highlights each - so
    those numbers stay comparable to anything measured against it today.
    """
    import dataclasses

    import config

    undated = dataclasses.replace(config.settings, exa_dated_packet=False)
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
    fields were already in Exa's reply and were being discarded. The relative
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
    """The one thing worse than no date is a wrong one. A source Exa did not
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
def test_retrieval_makes_the_call_the_benchmark_made(exa):
    packet = asyncio.run(research.retrieve("what did the fed do"))
    call = [c for c in exa if "query" in c][0]
    assert call["query"] == "what did the fed do"
    assert call["type"] == research.DEFAULT_SEARCH_TYPE == "fast"
    assert call["num_results"] == 8
    assert call["highlights"] is True
    assert packet.backend == "exa" and packet.searches == 1
    assert packet.results_returned == 4


def test_retrieval_does_not_run_on_the_event_loop(exa):
    """The cover is speaking and the assembler is batching while this runs. A
    synchronous HTTP call on the loop would stop both - the same guarantee the
    speech engines are held to."""
    asyncio.run(research.retrieve("anything"))
    call = [c for c in exa if "query" in c][0]
    assert call["thread"] != "MainThread", (
        f"retrieval ran on the event loop, in {call['thread']}")


def test_the_reported_cost_is_the_response_s_own_when_it_gives_one(exa):
    assert asyncio.run(research.retrieve("q")).cost == pytest.approx(0.0031)


def test_a_response_without_a_cost_falls_back_to_the_published_rate(monkeypatch):
    class Silent:
        def __init__(self, key):
            pass

        def search_and_contents(self, query, **kwargs):
            return FakeReply(RESULTS, cost=None)

    module = types.ModuleType("exa_py")
    module.Exa = Silent
    monkeypatch.setitem(sys.modules, "exa_py", module)
    monkeypatch.setenv("EXA_API_KEY", "k")
    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(settings, research_backend="exa"))
    assert asyncio.run(research.retrieve("q")).cost == research.COST_PER_SEARCH


def test_the_credential_never_reaches_the_result(exa):
    packet = asyncio.run(research.retrieve("q"))
    assert "exa-test-key-not-real" not in str(packet.as_dict())
    assert "exa-test-key-not-real" not in packet.context


# --------------------------------------------------------------------------
# it refuses rather than guessing
# --------------------------------------------------------------------------
def test_the_claude_backend_retrieves_nothing_and_that_is_not_an_error(monkeypatch):
    use_backend(monkeypatch, "claude")
    packet = asyncio.run(research.retrieve("what did the fed do"))
    assert packet.backend == "claude"
    assert not packet, "the claude backend must not produce a packet"


@pytest.mark.parametrize("value", ["exaa", "web", "google", "none", "exa-py"])
def test_an_unrecognised_backend_is_refused_at_retrieval(value, monkeypatch):
    """The second gate. `Settings.__post_init__` is bypassable; this is not.

    `""` is deliberately not here: an empty override means "not specified", so
    it falls through to the configured backend rather than being a bad value.
    Nor is `"Exa "` - case and surrounding whitespace normalise, exactly as
    they do for STREAMING_PIPELINE, because a deployment writing `EXA` means
    exa and refusing that is pedantry rather than safety.
    """
    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(settings, research_backend="claude"))
    with pytest.raises(research.ResearchUnavailable) as exc:
        asyncio.run(research.retrieve("q", backend=value))
    assert "is not a backend" in str(exc.value)
    assert "falling back" in str(exc.value)


@pytest.mark.parametrize("value", ["EXA", " exa ", "Exa", "CLAUDE"])
def test_case_and_whitespace_normalise_rather_than_being_refused(value,
                                                                 monkeypatch,
                                                                 exa):
    """The tolerance is deliberate, and the same as STREAMING_PIPELINE's: a
    deployment writing `RESEARCH_BACKEND=EXA` means exa. It is the
    unrecognisable that is refused, not the differently-cased."""
    packet = asyncio.run(research.retrieve("q", backend=value))
    assert packet.backend == value.strip().lower()


def test_a_missing_package_says_so_rather_than_failing_obscurely(monkeypatch):
    monkeypatch.setitem(sys.modules, "exa_py", None)
    monkeypatch.setenv("EXA_API_KEY", "k")
    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(settings, research_backend="exa"))
    with pytest.raises(research.ResearchUnavailable, match="exa_py is not installed"):
        asyncio.run(research.retrieve("q"))


def test_a_missing_key_names_the_variable_and_the_way_out(monkeypatch):
    module = types.ModuleType("exa_py")
    module.Exa = lambda key: None
    monkeypatch.setitem(sys.modules, "exa_py", module)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(settings, research_backend="exa"))
    with pytest.raises(research.ResearchUnavailable) as exc:
        asyncio.run(research.retrieve("q"))
    assert "EXA_API_KEY" in str(exc.value)
    assert "RESEARCH_BACKEND=claude" in str(exc.value)


def test_exa_failure_does_not_become_a_claude_search():
    """The failure this refuses. An episode that asked for Exa and quietly got
    the model's own search is unattributable - and would report Exa's cost of
    zero while paying Claude's.

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
    monkeypatch.setitem(sys.modules, "exa_py", None)
    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(settings, research_backend="exa"))
    with pytest.raises(research.ResearchUnavailable):
        asyncio.run(research.retrieve("q"))


# --------------------------------------------------------------------------
# Claude reads the packet
# --------------------------------------------------------------------------
def test_evidence_reaches_the_prompt_and_the_tool_does_not(exa, monkeypatch):
    """The whole point: on `exa`, Claude reads rather than searches."""
    use_backend(monkeypatch, "exa")
    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(settings, research_backend="exa"))
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


def test_the_claude_backend_retrieves_before_the_writing_call(monkeypatch,
                                                               fake_search):
    """It used to hand the writing call a tool and let it search mid-episode,
    which is how a first sentence came to be written before anything had been
    looked up. It is a retrieval of its own now. PROBLEMS.md §108."""
    use_backend(monkeypatch, "claude")
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = plan_episode("what did the fed do today", 3, search=True)
    notes = ScriptNotes()
    researched = asyncio.run(generator.research(plan, notes))

    assert "Rates held at 4.25%" in researched.evidence
    assert "<evidence>" in build_prompt(researched)
    assert "tools" not in generator._request_kwargs(researched), (
        "the call that speaks was given a search tool")
    assert notes.research["backend"] == "claude"
    assert notes.research["sources"] == ["reuters.com"]
    # The searching call's tokens are the episode's, and were invisible for as
    # long as the searching happened inside the writing turn.
    assert notes.usage.model_calls == 1


def test_the_packet_the_model_writes_is_graded_and_dated_in_code(monkeypatch,
                                                                 fake_search):
    """Three rules from the Exa path, applied to evidence that arrives as
    prose: no hostname reaches the writer, the grade is computed from the URL
    rather than taken from the model, and the relative phrase is subtraction."""
    use_backend(monkeypatch, "claude")
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    researched = asyncio.run(generator.research(
        plan_episode("what did the fed do today", 3, search=True), ScriptNotes()))

    assert "reuters.com" not in researched.evidence
    assert "URL:" not in researched.evidence
    assert ("Source type: " + research.TIER_LABELS["primary"]
            in researched.evidence)
    # The model wrote "Published: 2026-09-18" and nothing else; "yesterday" is
    # subtraction done here, which is the rule §82 paid for.
    assert "Published: 2026-09-18 (" in researched.evidence


def test_a_search_that_cannot_run_leaves_the_episode_answerable(monkeypatch):
    """A layer that adds quality must not subtract availability. No key, a
    refusal, a timeout - all of them are an unresearched episode, which is a
    thing this app already knows how to be."""
    use_backend(monkeypatch, "claude")

    def broken():
        raise RuntimeError("no credential")

    monkeypatch.setattr(research, "research_client", broken)
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = plan_episode("what did the fed do today", 3, search=True)
    researched = asyncio.run(generator.research(plan, ScriptNotes()))
    assert researched is plan
    assert "tools" not in generator._request_kwargs(researched)


def test_an_unresearched_episode_never_retrieves(exa, monkeypatch):
    """The default question costs nothing either way, and must not reach Exa."""
    use_backend(monkeypatch, "exa")
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = plan_episode("what is the nasdaq", 3, search=False)
    assert asyncio.run(generator.research(plan, ScriptNotes())) is plan
    assert not [c for c in exa if "query" in c], "an unresearched episode searched"


def test_an_empty_packet_asks_the_model_to_look_before_writing(monkeypatch,
                                                                fake_search):
    """Retrieval succeeded and found nothing usable.

    The old behaviour was to leave the search tool attached to the writing
    call, which is a fallback to the model's own search that nobody could see
    and that happened while the episode was being spoken. The same fallback
    now happens as a retrieval, before the first word, and the episode's own
    record names the backend that could not serve it.
    """
    class Empty:
        def __init__(self, key):
            pass

        def search_and_contents(self, query, **kwargs):
            return FakeReply([])

    module = types.ModuleType("exa_py")
    module.Exa = Empty
    monkeypatch.setitem(sys.modules, "exa_py", module)
    monkeypatch.setenv("EXA_API_KEY", "k")
    use_backend(monkeypatch, "exa")

    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = plan_episode("todays news", 3, search=True)
    notes = ScriptNotes()
    researched = asyncio.run(generator.research(plan, notes))
    assert "Rates held at 4.25%" in researched.evidence
    assert "tools" not in generator._request_kwargs(researched)
    assert notes.research["fell_back_from"] == "exa"


def test_a_backend_that_cannot_run_falls_back_out_loud(monkeypatch, fake_search):
    """Exa with no key used to raise, and the episode failed. What made that
    survivable was the cover half speaking underneath it, which is gone - so
    the other retriever gets one go, and says on the record that it did."""
    use_backend(monkeypatch, "exa")
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "exa_py", None)

    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    notes = ScriptNotes()
    researched = asyncio.run(generator.research(
        plan_episode("todays news", 3, search=True), notes))
    assert researched.evidence
    assert notes.research["backend"] == "claude"
    assert notes.research["fell_back_from"] == "exa"


def test_a_retriever_that_breaks_never_takes_the_episode_with_it(monkeypatch,
                                                                 fake_search):
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
    class Broken:
        def __init__(self, key):
            pass

        def search_and_contents(self, query, **kwargs):
            raise RuntimeError("502 from the index")

    module = types.ModuleType("exa_py")
    module.Exa = Broken
    monkeypatch.setitem(sys.modules, "exa_py", module)
    monkeypatch.setenv("EXA_API_KEY", "k")
    use_backend(monkeypatch, "exa")

    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    notes = ScriptNotes()
    researched = asyncio.run(generator.research(
        plan_episode("todays news", 3, search=True), notes))

    # It did not raise, and it did not give up either: the next rung ran.
    assert "Rates held at 4.25%" in researched.evidence
    assert notes.research["fell_back_from"] == "exa"


def test_every_rung_failing_is_an_unresearched_episode_not_a_dead_one(monkeypatch):
    """The bottom of the ladder. Nothing retrieved, and the episode still
    exists - which is the state an unresearched episode has always been in."""
    class Broken:
        def __init__(self, key):
            pass

        def search_and_contents(self, query, **kwargs):
            raise RuntimeError("502 from the index")

    module = types.ModuleType("exa_py")
    module.Exa = Broken
    monkeypatch.setitem(sys.modules, "exa_py", module)
    monkeypatch.setenv("EXA_API_KEY", "k")
    use_backend(monkeypatch, "exa")

    def broken_client():
        raise RuntimeError("no credential either")

    monkeypatch.setattr(research, "research_client", broken_client)
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
    calls: list = []

    class WindowedMiss:
        def __init__(self, key):
            pass

        def search_and_contents(self, query, **kwargs):
            calls.append(kwargs.get("start_published_date"))
            # Nothing inside the window; everything outside it.
            if kwargs.get("start_published_date"):
                return FakeReply([])
            return FakeReply(RESULTS, cost=0.0031)

    module = types.ModuleType("exa_py")
    module.Exa = WindowedMiss
    monkeypatch.setitem(sys.modules, "exa_py", module)
    monkeypatch.setenv("EXA_API_KEY", "k")
    use_backend(monkeypatch, "exa")

    packet = asyncio.run(research.retrieve(
        "what happened overnight",
        brief=types.SimpleNamespace(recency_days=1, subject="the thing",
                                    retrieval="what happened overnight",
                                    must_establish=[])))
    assert len(calls) == 2, "the windowless second look never ran"
    assert calls[0] and not calls[1], "the second look must drop the window"
    assert packet, "the retry found sources and they did not reach the packet"
    assert packet.retried


def test_the_prompt_tells_the_model_not_to_read_sources_aloud(exa, monkeypatch):
    use_backend(monkeypatch, "exa")
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = asyncio.run(generator.research(
        plan_episode("what did the fed do today", 3, search=True), ScriptNotes()))
    prompt = build_prompt(plan)
    assert "Never read a source's title, number, date or URL aloud" in prompt
    assert "they win and you say so plainly" in prompt, (
        "the model must be told the evidence outranks what it recalls")


def test_what_retrieval_cost_is_recorded_for_a_person_to_read(exa, monkeypatch):
    use_backend(monkeypatch, "exa")
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    notes = ScriptNotes()
    asyncio.run(generator.research(
        plan_episode("what did the fed do today", 3, search=True), notes))
    assert notes.research["backend"] == "exa"
    assert notes.research["sources"] == ["reuters.com", "ft.com", "bbc.co.uk"]
    assert notes.research["cost"] == pytest.approx(0.0031)
    assert notes.research["packet_chars"] > 0


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------
def test_a_fresh_deployment_researches_with_exa():
    """The production default. Reversed from `claude`, deliberately, and with
    a real cost attached: it needs a second credential."""
    assert config.DEFAULT_RESEARCH_BACKEND == "exa"
    assert settings.research_backend == "exa"
    assert "claude" in RESEARCH_BACKENDS and "exa" in RESEARCH_BACKENDS


def test_the_default_is_one_fact_in_one_place():
    """The constant and the setting cannot disagree, because the setting is
    built from the constant rather than repeating the literal."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "config.py").read_text()
    assert '"RESEARCH_BACKEND", DEFAULT_RESEARCH_BACKEND' in source
    assert settings.research_backend == config.DEFAULT_RESEARCH_BACKEND


def test_rollback_to_claude_is_still_one_variable(monkeypatch):
    """A deployment with no Exa key must have a working configuration to move
    to, not a broken one to endure."""
    patched = dataclasses.replace(settings, research_backend="claude")
    monkeypatch.setattr(research, "settings", patched)
    assert patched.research_backend == "claude"
    assert not asyncio.run(research.retrieve("q")), (
        "the fallback configuration must need no key and no package")


def test_the_env_example_ships_the_default_it_documents():
    """Following the documented setup must not configure the product against
    itself - PROBLEMS.md 54."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    lines = [line.strip() for line in (root / ".env.example").read_text().splitlines()]
    assert f"RESEARCH_BACKEND={config.DEFAULT_RESEARCH_BACKEND}" in lines


def test_every_image_installs_the_backend_it_defaults_to():
    """Render started with research already broken, and said so in a log line.

    `research.py` imports `exa_py`, which is declared only in
    requirements-exa.txt. The CPU Dockerfile - the one Render builds - installed
    requirements.txt alone, so the image came up with RESEARCH_BACKEND=exa and
    no Exa: `diagnose` reported "exa_py is not installed" and every researched
    episode would raise ResearchUnavailable rather than search another way.
    Dockerfile.gpu had always installed both; this is the same shape as §54,
    where a setting was settled in one place and not in the one that shipped.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for name in ("Dockerfile", "Dockerfile.gpu"):
        text = (root / name).read_text()
        assert "requirements-exa.txt" in text, (
            f"{name} does not install exa_py, but RESEARCH_BACKEND defaults to "
            f"{config.DEFAULT_RESEARCH_BACKEND!r}")


@pytest.mark.parametrize("value", ["exaa", "web", "", "google"])
def test_an_unrecognised_backend_is_refused_at_import(value):
    with pytest.raises(ValueError, match="is not a research backend"):
        dataclasses.replace(settings, research_backend=value)


@pytest.mark.parametrize("field, value", [
    ("exa_num_results", 0), ("exa_packet_sources", 0),
    ("exa_highlights_per_source", 0), ("exa_num_results", -1),
])
def test_a_zero_knob_is_refused_rather_than_sending_an_empty_packet(field, value):
    with pytest.raises(ValueError, match="must be at least 1"):
        dataclasses.replace(settings, **{field: value})


def test_the_packet_cannot_ask_for_more_sources_than_were_fetched():
    with pytest.raises(ValueError, match="exceeds"):
        dataclasses.replace(settings, exa_num_results=3, exa_packet_sources=5)


def test_health_says_which_backend_and_whether_it_can_run():
    report = research.report()
    assert report["backend"] == "exa"
    assert "exa_detail" in report
    # In this container there is no EXA_API_KEY, and the default is now exa -
    # so `unavailable` is true, and that is the honest answer rather than a
    # test failure. What must never happen is it reading false while research
    # cannot run.
    ok, _ = research.diagnose()
    assert report["unavailable"] is (not ok)


def test_a_deployment_that_cannot_research_says_so_at_startup(caplog):
    """Not on a listener's first researched question. `exa` is the default and
    needs a credential; a missing one discovered mid-episode is the shape of
    failure this project has paid for most."""
    import logging

    import app

    with caplog.at_level(logging.WARNING, logger="app"):
        app._announce_research()

    if research.available():  # pragma: no cover - not in this container
        pytest.skip("this machine can research; nothing to announce")
    messages = [record.getMessage() for record in caplog.records]
    assert any("RESEARCH UNAVAILABLE" in m for m in messages), messages
    assert any("will FAIL rather than search another way" in m for m in messages)
    assert any("RESEARCH_BACKEND=claude" in m for m in messages), (
        "the warning must name the working configuration to move to")


def test_the_startup_warning_is_not_fatal():
    """Most questions are not researched. An app that refuses to start because
    one path is unconfigured is worse than one that starts and says which."""
    import app

    assert app._announce_research() is None


def _exa_backend(monkeypatch):
    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(settings, research_backend="exa"))


def _exa_py(monkeypatch, installed: bool):
    """Decide whether `import exa_py` succeeds, whatever this machine has.

    `diagnose()` reports the *first* missing prerequisite, so on a machine
    without the package the key branch is unreachable and a test that asserts
    on it fails for a reason that has nothing to do with the behaviour. A None
    entry in sys.modules makes the import raise; a bare module makes it
    succeed. Both are deterministic, which matters because the suite must pass
    with exa_py absent - see test_exa_is_not_a_fresh_install_requirement.
    """
    monkeypatch.setitem(sys.modules, "exa_py",
                        types.ModuleType("exa_py") if installed else None)


def test_health_names_the_missing_package_when_exa_py_is_absent(monkeypatch):
    _exa_backend(monkeypatch)
    _exa_py(monkeypatch, installed=False)
    report = research.report()
    assert report["unavailable"] is True
    assert "exa_py" in report["exa_detail"]


def test_health_names_the_missing_key_once_the_package_is_there(monkeypatch):
    """The prerequisite a deployment hits second must be reported too.

    This is the case that used to be untestable on a machine without the
    package: it reported "exa_py is not installed" and never reached the key.
    """
    _exa_backend(monkeypatch)
    _exa_py(monkeypatch, installed=True)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    report = research.report()
    assert report["unavailable"] is True
    assert "EXA_API_KEY" in report["exa_detail"]


def test_health_is_clear_when_exa_can_actually_run(monkeypatch):
    _exa_backend(monkeypatch)
    _exa_py(monkeypatch, installed=True)
    monkeypatch.setenv("EXA_API_KEY", "exa-test-key")
    report = research.report()
    assert report["exa_configured"] is True
    assert report["unavailable"] is False


def test_the_claude_backend_never_reports_unavailable_for_a_missing_exa_key(monkeypatch):
    """Exa's prerequisites are not the Claude backend's problem."""
    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(settings, research_backend="claude"))
    _exa_py(monkeypatch, installed=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    assert research.report()["unavailable"] is False


def test_a_listener_is_told_why_research_failed_not_to_read_a_log():
    """The remedy is already in the exception; throwing it away wastes it.

    ResearchUnavailable says "exa_py is not installed, pip install -r
    requirements-exa.txt" or which key is missing. friendly_error used to
    reduce that to "Generation failed: ResearchUnavailable. See the server log
    for details." - which is the one thing a person in a browser cannot do.
    """
    from app import friendly_error

    message = friendly_error(
        research.ResearchUnavailable(
            "exa_py is not installed. `pip install -r requirements-exa.txt`"))
    assert "exa_py is not installed" in message
    assert "requirements-exa.txt" in message
    assert "server log" not in message


def test_exa_is_not_a_fresh_install_requirement():
    """`requirements.txt` must not pull it in - the default backend needs it
    never, and the suite runs without it."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for line in (root / "requirements.txt").read_text().splitlines():
        assert "exa" not in line.split("#", 1)[0].lower()
    assert (root / "requirements-exa.txt").exists()


# --------------------------------------------------------------------------
# through the real pipeline, end to end
# --------------------------------------------------------------------------
def test_a_researched_episode_plays_with_claude_reading_the_exa_packet(exa,
                                                                       monkeypatch):
    """The whole path: Exa retrieves, the packet reaches the prompt, the model
    writes from it, the assembler chunks it and the engine speaks it.

    Nothing is stubbed but Exa itself and the two things that always are in
    this suite - the model and the voice. In particular the pipeline, the
    Phase 6 assembler and the duration contract are the real ones.
    """
    from pipeline import GenerationStats, PodcastPipeline
    from tts import DebugEngine

    use_backend(monkeypatch, "exa")
    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(settings, research_backend="exa"))

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
    assert len([c for c in exa if "query" in c]) == 1
    assert stats.audio_seconds <= 60 + 5, "the duration ceiling did not hold"


def test_research_runs_once_per_episode_not_once_per_sentence(exa, monkeypatch):
    """`stream_sentences` calls `research` on entry. If that were inside the
    streaming loop it would pay for a retrieval per sentence, silently."""
    use_backend(monkeypatch, "exa")
    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(settings, research_backend="exa"))
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = plan_episode("what did the fed do today", 3, search=True)

    researched = asyncio.run(generator.research(plan, ScriptNotes()))
    # Researching an already-researched plan must be a no-op, or the answer
    # -first path (two calls on one plan) would retrieve twice.
    again = asyncio.run(generator.research(researched, ScriptNotes()))
    assert again is researched
    assert len([c for c in exa if "query" in c]) == 1


def test_the_evidence_survives_being_replaced_onto_the_plan(exa, monkeypatch):
    """`research` returns a copy rather than mutating, and several steps in
    `prepare` do the same. The packet must survive all of them."""
    use_backend(monkeypatch, "exa")
    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(settings, research_backend="exa"))
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = plan_episode("what did the fed do today", 3, search=True)

    # An unresearched plan never retrieves, whatever the backend is set to.
    unresearched = dataclasses.replace(plan, search=False)
    assert asyncio.run(generator.research(unresearched, ScriptNotes())) is unresearched
    assert not [c for c in exa if "query" in c], "an unresearched episode searched"

    done = asyncio.run(generator.research(plan, ScriptNotes()))
    assert done.evidence
    assert dataclasses.replace(done, minutes=5).evidence == done.evidence

