"""Mclovin's sentence checks while the writer writes (Phase 3, piece 2, §434).

Every check is code, against the two packets only. Enforced checks send a
failing sentence back to the writer once; a second failure, a late rewrite
or one past the episode's count leaves it out. The others count. With
Mclovin off the reader hands out plain sentences, exactly as before.
"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timezone

import pytest

import script_generator as sg
from agents import netenyahu
from agents import supervisor_checks as checks
from agents.handoffs import FAILURES, ResearchPlan
from config import settings
from script_generator import ScriptNotes, plan_episode

NOW = datetime(2026, 11, 3, 15, tzinfo=timezone.utc)

EVIDENCE = """SOURCE 1
Title: Fed holds rates steady as Powell signals patience
Published: 2026-11-02 (yesterday)
Source type: wire service, paper of record or primary source
Key evidence:
Jerome Powell said "we are not in a hurry to cut rates" at a news conference in Washington.
The Federal Reserve held its benchmark rate at 4.25 percent, its first pause this year.

SOURCE 2
Title: Eagles beat Cowboys in Philadelphia
Published: 2026-10-20 (two weeks ago)
Source type: established outlet
Key evidence:
Jalen Hurts threw for 312 yards as the Eagles beat the Cowboys 31-17 in Philadelphia.

SOURCE 3
Title: Apple opens its biggest store in India
Published: 2026-11-01 (two days ago)
Source type: established outlet
Key evidence:
Apple opened its largest store in Mumbai, Tim Cook said, calling India "a market we believe in deeply".
"""


def setting(monkeypatch, **values):
    import config

    changed = dataclasses.replace(config.settings, **values)
    monkeypatch.setattr(config, "settings", changed)
    monkeypatch.setattr(sg, "settings", changed)


@pytest.fixture(autouse=True)
def fresh():
    checks.reset()
    yield
    checks.reset()


def plan(evidence=EVIDENCE):
    return dataclasses.replace(plan_episode("what did the fed do", 1.5),
                               search=False, evidence=evidence)


def checker(evidence=EVIDENCE, advice=False):
    notes = ScriptNotes()
    notes.research_plan = ResearchPlan(advice_request=advice)
    return checks.Checker(plan(evidence), notes, now=NOW)


def verdict(sentence, **kw):
    v = checker(**kw).check(sentence)
    return {c.check: c.result for c in v.checks}, v


# ------------------------------------------------------------------ quotes
def test_a_quote_in_the_packets_by_its_speaker_passes():
    got, _ = verdict('"We are not in a hurry to cut rates," Powell said on Monday.')
    assert got["quote"] == "pass"


def test_a_quote_not_in_the_packets_fails():
    got, v = verdict('"We will cut rates in December," Powell said.')
    assert got["quote"] == "fail"
    assert v.failing[0].failure == "quote_not_said"


def test_a_quote_given_to_the_wrong_person_fails():
    got, v = verdict('"We are not in a hurry to cut rates," Tim Cook said.')
    assert got["quote"] == "fail"
    assert v.failing[0].failure == "quote_wrong_speaker"


def test_a_short_quoted_term_is_not_a_quote():
    assert verdict('Analysts called it a "patient" Fed.')[0]["quote"] == "pass"


@pytest.mark.parametrize("sentence,result", [
    ('"We are not in a hurry to cut rates," Powell said.', "pass"),
    ('"Mr. Powell and the U.S. economy are fine," she said.', "pass"),
    ('"We are not in a hurry. We will wait for the data," Powell said.', "fail"),
    ('"Rates hold! Markets rise," he said.', "fail"),
])
def test_a_quote_is_at_most_one_sentence(sentence, result):
    got, v = verdict(sentence)
    assert got["quote_length"] == result
    if result == "fail":
        # Counted until the owner signs the mclovin.md that names it (§435).
        assert v.counted[0].failure == "quote_too_long"
        assert not any(c.check == "quote_length" for c in v.failing)


# ------------------------------------------------------------------ websites, advice
@pytest.mark.parametrize("sentence,result", [
    ("You can read more on federalreserve.gov.", "fail"),
    ("It is all on reuters dot com.", "fail"),
    ("The Fed held rates on Monday.", "pass"),
])
def test_a_website_in_speech(sentence, result):
    assert verdict(sentence)[0]["website"] == result


@pytest.mark.parametrize("sentence,advice,result", [
    ("You should refinance your mortgage now.", False, "fail"),
    ("Your best bet is to move your savings into bonds.", False, "fail"),
    ("You should know that the Fed held rates.", False, "pass"),
    ("Doctors say you should get the flu shot every year.", False, "pass"),
    ('"You should refinance now," one broker said.', False, "pass"),
    ("I'd recommend talking it through first.", True, "fail"),
])
def test_advice_in_fams_own_voice(sentence, advice, result):
    assert verdict(sentence, advice=advice)[0]["advice"] == result


# ------------------------------------------------------------------ superlatives
@pytest.mark.parametrize("sentence,result", [
    ("It was the Fed's first pause this year.", "pass"),
    ("Apple opened its biggest store yet in Mumbai.", "pass"),
    ("Hurts set a record with 312 yards.", "fail"),
    ("It was the only rate decision Powell made alone.", "fail"),
])
def test_a_superlative_needs_the_packets(sentence, result):
    got, v = verdict(sentence)
    assert got["superlative"] == result
    # Counted only, the owner's choice (§435): never sent back.
    assert not any(c.check == "superlative" for c in v.failing)


# ------------------------------------------------------------------ counted checks
@pytest.mark.parametrize("sentence,result", [
    ("The Fed held its rate at 4.25 percent.", "pass"),
    ("Hurts threw for 312 yards.", "pass"),
    ("Hurts threw for 340 yards.", "fail"),
    ("Two teams met in Philadelphia.", "pass"),     # a count of things
    ("In 2019 the rate was 2.4 percent.", "pass"),  # history is left alone
])
def test_every_number_is_counted(sentence, result):
    got, v = verdict(sentence)
    assert got["figures"] == result
    assert not any(c.check == "figures" for c in v.failing)   # never sent back


@pytest.mark.parametrize("sentence,result", [
    ("The Federal Reserve held its rate yesterday, Powell said.", "pass"),
    ("Last night Jalen Hurts and the Eagles beat the Cowboys in Philadelphia.", "fail"),
    ("The Eagles beat the Cowboys in Philadelphia this season.", "pass"),
])
def test_relative_dates_by_the_listeners_clock(sentence, result):
    assert verdict(sentence)[0]["dates"] == result


def test_two_stories_joined_by_no_source_are_counted():
    got, _ = verdict("Powell and Jalen Hurts both had a big week in Philadelphia.")
    assert got["mixed"] == "fail"
    assert verdict("Powell spoke in Washington as the Federal Reserve held.")[0]["mixed"] == "pass"


def test_the_named_failures_are_the_rule_books():
    for name in checks.FAILURE_OF.values():
        assert name in FAILURES or name in checks.PROPOSED_FAILURES
    # Proposed, not yet signed: never in the signed list, always in the proposal.
    import pathlib

    proposal = pathlib.Path("docs/agent_rule_proposals/mclovin.md").read_text()
    assert not set(checks.PROPOSED_FAILURES) & set(FAILURES)
    assert "at most one sentence" in proposal and "count only" in proposal
    assert {"quote_not_said", "quote_wrong_speaker"} <= set(FAILURES)


def test_the_default_enforced_set_is_pinned():
    assert checks.enforced() == frozenset(checks.DEFAULT_ENFORCED)
    assert settings.supervisor_enforce == ",".join(checks.DEFAULT_ENFORCED)


def test_a_check_that_breaks_plays_its_sentence_unchecked(monkeypatch):
    monkeypatch.setitem(checks._BY_NAME, "quote", lambda *a: 1 / 0)
    v = checker().check("Powell said rates hold.")
    assert {c.check: c.result for c in v.checks}["quote"] == "unchecked"
    assert not v.failing
    assert checks.SINCE_BOOT["sentences_unchecked"] == 1


def test_past_the_time_limit_the_rest_is_unchecked(monkeypatch):
    setting(monkeypatch, supervisor_timeout_seconds=1e-9)
    v = checker().check("Powell said rates hold.")
    assert [c.result for c in v.checks][1:] == ["unchecked"] * (len(checks.CHECKS) - 1)


# ------------------------------------------------------------------ in the writing
class _Final:
    stop_reason = "end_turn"
    usage = None


class _Stream:
    def __init__(self, text):
        self.text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def gen():
            for chunk in self.text.split(" "):
                yield chunk + " "
        return gen()

    async def get_final_message(self):
        return _Final()


class _Answer:
    def __init__(self, text):
        self.content = [type("B", (), {"type": "text", "text": text})()]
        self.usage = None


def generator(script, rewrites=(), delay=0.0):
    gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    asked = []
    queue = list(rewrites)

    class _Messages:
        def stream(self, **kwargs):
            return _Stream(script)

        async def create(self, **kwargs):
            asked.append(kwargs)
            if delay:
                await asyncio.sleep(delay)
            return _Answer(queue.pop(0) if queue else "NONE")

    gen.client = type("C", (), {"messages": _Messages()})()
    return gen, asked


SCRIPT = ('The Federal Reserve held its rate at 4.25 percent. '
          '"We will cut rates in December," Powell said. '
          'Powell spoke in Washington.')


def run(gen, roles=("supervisor",), p=None):
    notes = ScriptNotes()
    notes.agents = netenyahu.EpisodeRun(tuple(roles), {})
    notes.research_plan = ResearchPlan()

    async def go():
        return [s async for s in gen.stream_prepared(p or plan(), notes)]

    return asyncio.run(go()), notes


def test_off_nothing_is_checked_or_asked():
    gen, asked = generator(SCRIPT)
    spoken, notes = run(gen, roles=())
    assert spoken == ["The Federal Reserve held its rate at 4.25 percent.",
                      '"We will cut rates in December," Powell said.',
                      "Powell spoke in Washington."]
    assert asked == [] and notes.supervisor is None
    assert checks.SINCE_BOOT == {}


def test_a_failing_sentence_is_rewritten_once_and_kept():
    gen, asked = generator(SCRIPT, rewrites=[
        '"We are not in a hurry to cut rates," Powell said.'])
    spoken, notes = run(gen)
    assert spoken[1] == '"We are not in a hurry to cut rates," Powell said.'
    assert len(asked) == 1
    request = asked[0]
    assert request["system"] == sg.REPAIR_INSTRUCTIONS
    content = request["messages"][0]["content"]
    assert "We will cut rates in December" in content
    assert "is not in the packets" in content
    assert "we are not in a hurry to cut rates" in content.lower()   # the source's words
    assert notes.supervisor["repaired"][0]["now"].startswith('"We are not')
    assert checks.SINCE_BOOT["repair_kept"] == 1


def test_a_rewrite_that_fails_again_is_left_out():
    gen, asked = generator(SCRIPT, rewrites=['"Rates fall in December," Powell said.'])
    spoken, notes = run(gen)
    assert spoken == ["The Federal Reserve held its rate at 4.25 percent.",
                      "Powell spoke in Washington."]
    assert len(asked) == 1                      # never asked twice
    assert notes.supervisor["dropped"][0]["why"] == "the rewrite failed again"


def test_no_true_version_is_left_out():
    gen, _ = generator(SCRIPT, rewrites=["NONE"])
    spoken, notes = run(gen)
    assert len(spoken) == 2
    assert notes.supervisor["dropped"][0]["why"] == "the writer had no true version"


def test_a_late_rewrite_is_left_out_and_the_rest_plays(monkeypatch):
    setting(monkeypatch, supervisor_repair_timeout_seconds=0.1)
    gen, _ = generator(SCRIPT, rewrites=["Powell spoke."], delay=1.0)
    spoken, notes = run(gen)
    assert spoken[-1] == "Powell spoke in Washington."
    assert notes.supervisor["dropped"][0]["why"] == "the rewrite was late"


def test_past_the_episodes_rewrites_a_failure_is_left_out(monkeypatch):
    setting(monkeypatch, supervisor_max_repairs=0)
    gen, asked = generator(SCRIPT, rewrites=["x"])
    spoken, notes = run(gen)
    assert asked == [] and len(spoken) == 2
    assert notes.supervisor["dropped"][0]["why"] == "past the episode's rewrites"


def test_counted_failures_play_and_are_recorded():
    gen, asked = generator("Hurts threw for 340 yards in Philadelphia.")
    spoken, notes = run(gen)
    assert spoken == ["Hurts threw for 340 yards in Philadelphia."]
    assert asked == []
    row = notes.supervisor["rows"][0]
    assert (row["check"], row["result"], row["enforced"]) == ("figures", "fail", False)
    assert row["failure"] == "number_not_in_packets"


def test_a_batched_script_settles_its_repairs():
    gen, asked = generator("", rewrites=['"We are not in a hurry to cut rates," Powell said.'])
    notes = ScriptNotes()
    notes.agents = netenyahu.EpisodeRun(("supervisor",), {})
    notes.research_plan = ResearchPlan()
    message = _Answer(SCRIPT)
    message.stop_reason, message.model = "end_turn", "claude-haiku-5-5"
    p = plan()
    sentences = gen.sentences_from_message(p, notes, message)
    assert any(isinstance(s, sg.Repair) for s in sentences)
    settled = asyncio.run(sg.settle_repairs(gen, p, notes, sentences))
    assert all(isinstance(s, str) for s in settled)
    assert settled[1].startswith('"We are not')


def test_settle_with_nothing_to_repair_touches_nothing():
    sentences = ["a.", "b."]
    assert asyncio.run(sg.settle_repairs(object(), None, None, sentences)) is sentences


def test_health_reports_the_checks():
    from agents import models

    checker().check("You should refinance your mortgage now.")
    block = models.health()["supervisor"]
    assert block["checks"]["since_boot"]["advice_fail"] == 1
    assert block["checks"]["enforced"] == sorted(checks.DEFAULT_ENFORCED)
    assert block["repairs"] == {"timeout_seconds": 2.0, "max_per_episode": 4}
