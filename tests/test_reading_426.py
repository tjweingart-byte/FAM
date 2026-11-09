"""Ask the listener when a name is ambiguous (decided item 15, §426).

C3P0 says a name means two things; before any plugin or Sonic is asked the
episode stops and the readings go back - on `/api/audio` (300) and
`/api/progress` - to a client that sent `ask_reading=1`. The chosen reading
comes back as `reading=`, which is part of the episode's key and is told to
the brief and the writer. An older client gets C3P0's likeliest reading,
kept but never current; anything written ahead of the tap stays unwritten.
Nando's tie call never chooses between the readings of an undecided name.
"""
from __future__ import annotations

import asyncio
import dataclasses
import types

import pytest
from fastapi.testclient import TestClient

import app as appmod
import cache
import episode_intelligence as ei
import live_captions
import pipeline as pipeline_mod
import script_generator as sg
from agents import contextualizer
from tests.test_pipeline import FakeGenerator
from tts import DebugEngine

AMBIGUOUS = {"ambiguous": True, "readings": ["United Airlines", "Manchester United"],
             "likeliest": "Manchester United"}


class Runs:
    def __init__(self, *roles):
        self.roles = set(roles)

    def runs(self, role):
        return role in self.roles


def notes_for(*roles, **flags):
    notes = sg.ScriptNotes()
    notes.agents = Runs(*roles)
    for k, v in flags.items():
        setattr(notes, k, v)
    return notes


def plan_with(said, **changes):
    plan = sg.plan_episode("how did United do", 2, search=True)
    brief = ei.Brief(query=plan.query, subject="United", contextualized=dict(said))
    return dataclasses.replace(plan, brief=brief, **changes)


@pytest.fixture(autouse=True)
def fresh():
    contextualizer.reset()
    yield
    contextualizer.reset()


# ------------------------------------------------------------------ the key
def test_the_reading_joins_the_question_once():
    """Review (§432): the choice is part of the question, so every surface
    that names an episode by its question - saves, shares, history, the
    offline copy - names the chosen reading's episode."""
    plan = appmod._validated_plan("how did United do", 2, reading="Manchester United")
    assert plan.query == "how did United do (Manchester United)"
    assert plan.reading == "Manchester United"
    again = appmod._validated_plan("how did United do (Manchester United)", 2,
                                   reading="Manchester United")
    assert again.query == plan.query                     # never added twice
    assert appmod._validated_plan("how did United do", 2).query == "how did United do"


def test_the_key_is_the_questions_and_a_saved_question_finds_it():
    chosen = appmod._validated_plan("how did United do", 2, search=True, reading="Manchester United")
    other = appmod._validated_plan("how did United do", 2, search=True, reading="United Airlines")
    plain = appmod._validated_plan("how did United do", 2, search=True)
    # A save or share sends only the question, with the reading in it.
    saved = appmod._validated_plan("how did United do (Manchester United)", 2, search=True)
    keys = [asyncio.run(pipeline_mod.key_for(p)) for p in (chosen, other, plain, saved)]
    assert len(set(keys[:3])) == 3 and keys[0] == keys[3]
    # Asking is not part of the key: the same episode either way.
    assert asyncio.run(pipeline_mod.key_for(dataclasses.replace(plain, ask_reading=True))) \
        == keys[2]


# ------------------------------------------------------------------ what the brief and the writer read
def test_the_brief_prompt_names_the_chosen_reading_only_when_there_is_one():
    plain = ei.build_ei_prompt("how did United do", 2, "", "now")
    assert ei.build_ei_prompt("how did United do", 2, "", "now", reading="") == plain
    chosen = ei.build_ei_prompt("how did United do", 2, "", "now", reading="Manchester United")
    assert "chose: Manchester United" in chosen and "not ambiguous" in chosen
    # A follow-up keeps its own line beside it.
    both = ei.build_ei_prompt("q", 2, "the derby", "now", reading="Manchester United")
    assert "chose: Manchester United" in both and "follow-up" in both


def test_c3p0s_call_is_told_the_reading_too():
    text = contextualizer._moment("how did United do", 2, "", "now", (), "Manchester United")
    assert "chose: Manchester United" in text
    assert "chose:" not in contextualizer._moment("how did United do", 2, "", "now", ())


def test_the_writer_is_told_which_one_and_nothing_else_changes():
    assert ei.READING_NOTE.startswith("A name in the request means more than one thing")
    b = ei.Brief(query="q", subject="United")
    assert "means more than one thing" not in ei.build_brief_block(b, 2)
    b.reading = "Manchester United"
    block = ei.build_brief_block(b, 2)
    assert ei.READING_NOTE.format(reading="Manchester United") in block


# ------------------------------------------------------------------ deciding
def test_nothing_is_ambiguous_while_c3p0_is_off():
    assert sg.ambiguous_readings(plan_with(AMBIGUOUS), notes_for()) == ((), "")
    assert sg.ambiguous_readings(plan_with(AMBIGUOUS), None) == ((), "")
    plan = plan_with(AMBIGUOUS, ask_reading=True)
    assert sg._settle_reading(plan, notes_for()) is plan


def test_a_chosen_reading_is_never_asked_again():
    plan = plan_with(AMBIGUOUS, reading="Manchester United", ask_reading=True)
    assert sg.ambiguous_readings(plan, notes_for("contextualizer")) == ((), "")


@pytest.mark.parametrize("said", [{}, {"ambiguous": False},
                                  {"ambiguous": True, "readings": ["only one"]}])
def test_not_ambiguous_means_unchanged(said):
    plan = plan_with(said, ask_reading=True)
    assert sg._settle_reading(plan, notes_for("contextualizer")) is plan


def test_a_client_that_can_ask_is_stopped_with_the_readings():
    key = "k-ask"
    live_captions.open_track(key)
    notes = notes_for("contextualizer", caption_key=key)
    with pytest.raises(sg.ReadingNeeded) as stop:
        sg._settle_reading(plan_with(AMBIGUOUS, ask_reading=True), notes)
    assert stop.value.readings == ("United Airlines", "Manchester United")
    assert stop.value.likeliest == "Manchester United"
    progress = live_captions.read_progress(key)
    assert progress["readings"] == ["United Airlines", "Manchester United"]
    assert progress["likeliest"] == "Manchester United"
    assert progress["steps"]["contextualized"] and not progress["steps"]["retrieved"]
    assert contextualizer.report()["since_boot"]["reading_asked"] == 1


@pytest.mark.parametrize("flag", ["ahead", "prefetch"])
def test_ahead_of_the_tap_it_stays_unwritten(flag):
    with pytest.raises(sg.ReadingNeeded):
        sg._settle_reading(plan_with(AMBIGUOUS), notes_for("contextualizer", **{flag: True}))
    assert contextualizer.report()["since_boot"]["reading_unwritten"] == 1


def test_an_older_client_gets_the_likeliest_reading():
    notes = notes_for("contextualizer")
    plan = sg._settle_reading(plan_with(AMBIGUOUS), notes)
    assert plan.brief.reading == "Manchester United"
    assert notes.reading_assumed == "Manchester United"
    assert contextualizer.report()["since_boot"]["reading_assumed"] == 1


def test_an_older_client_with_no_likeliest_is_written_as_before():
    said = dict(AMBIGUOUS, likeliest="")
    plan = plan_with(said)
    assert sg._settle_reading(plan, notes_for("contextualizer")) is plan


def test_progress_without_readings_is_the_shape_it_always_was():
    key = "k-plain"
    live_captions.open_track(key)
    assert set(live_captions.read_progress(key)) == {"known", "cached", "steps"}
    live_captions.publish_readings(key, ["only one"])     # fewer than two: nothing
    assert "readings" not in live_captions.read_progress(key)
    live_captions.publish_readings("never-opened", ["a", "b"])   # never opens a track
    assert live_captions.read_progress("never-opened")["known"] is False


# ------------------------------------------------------------------ over HTTP
class Asks(FakeGenerator):
    """Stops after the brief, as the real generator does for a client that asks."""

    def __init__(self):
        super().__init__()
        self.plans = []

    async def stream_sentences(self, plan, notes=None):
        self.plans.append(plan)
        if plan.ask_reading and not plan.reading:
            raise sg.ReadingNeeded(AMBIGUOUS["readings"], AMBIGUOUS["likeliest"])
        async for s in super().stream_sentences(plan, notes):
            yield s


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", None)
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "DEMO_MODE", False)
    return TestClient(appmod.app)


def use(monkeypatch, generator):
    monkeypatch.setattr(appmod, "_make_pipeline",
                        lambda voice=None, author="": pipeline_mod.PodcastPipeline(
                            generator=generator, engine=DebugEngine(), cache=None,
                            voice=voice))


def test_audio_answers_300_with_the_readings_and_refunds(client, monkeypatch):
    generator = Asks()
    use(monkeypatch, generator)
    refunds = []
    monkeypatch.setattr(appmod, "_refund", lambda verdict, user: refunds.append(user))
    res = client.get("/api/audio?q=how+did+United+do&minutes=1&fmt=pcm&ask_reading=1")
    assert res.status_code == 300
    body = res.json()
    assert body["readings"] == AMBIGUOUS["readings"]
    assert body["likeliest"] == "Manchester United"
    assert refunds, "nothing was heard, so nothing is charged"
    # The chosen reading resumes it, as a new request carrying `reading=`.
    res = client.get("/api/audio?q=how+did+United+do&minutes=1&fmt=pcm&ask_reading=1"
                     "&reading=Manchester+United")
    assert res.status_code == 200 and len(res.content) > 44
    assert generator.plans[-1].reading == "Manchester United"
    assert generator.plans[-1].query == "how did United do (Manchester United)"


def test_an_older_client_is_never_stopped(client, monkeypatch):
    generator = Asks()
    use(monkeypatch, generator)
    res = client.get("/api/audio?q=how+did+United+do&minutes=1&fmt=pcm")
    assert res.status_code == 200
    assert generator.plans[-1].ask_reading is False and generator.plans[-1].reading == ""


@pytest.mark.parametrize("path", ["/api/progress", "/api/next", "/api/transcript",
                                  "/api/sources"])
def test_every_key_shaped_endpoint_takes_the_reading(client, monkeypatch, path):
    seen = []
    real = appmod._validated_plan

    def spy(*a, **k):
        plan = real(*a, **k)
        seen.append(plan.reading)
        return plan
    monkeypatch.setattr(appmod, "_validated_plan", spy)
    client.get(f"{path}?q=how+did+United+do&minutes=2&reading=Manchester+United")
    assert seen and seen[-1] == "Manchester United"


# ------------------------------------------------------------------ Nando's tie call
def test_the_tie_call_never_chooses_between_readings_of_an_undecided_name():
    src = open(sg.__file__, encoding="utf-8").read()
    # Since §501 the same rule covers the pick a warmed brief carries.
    i = src.index("if pick and pick in (tie, ahead_pick) and not plan.reading:")
    block = src[i:i + 900]
    assert "bare.ambiguous" in block and 'prior, "", unsure=unsure' in block
    # And ahead of the tap it is not asked over one either.
    assert "and not placed.ambiguous\n" in src and \
        'not getattr(notes, "category_ahead_asked", False)):' in src   # §506


def test_the_ahead_writers_leave_an_ambiguous_episode_unwritten():
    import daily_edition
    import prefetch
    import trending_bank

    for module in (prefetch, daily_edition, trending_bank):
        assert "ambiguous_readings(plan, notes)[0]" in open(module.__file__,
                                                            encoding="utf-8").read()
    assert "ambiguous" in daily_edition._summarise({"q": {"status": "ambiguous"}})
    assert daily_edition._summarise({"q": {"status": "ambiguous"}})["ambiguous"] == 1
