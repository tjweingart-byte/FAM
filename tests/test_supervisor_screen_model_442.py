"""Mclovin's small model on pages the source screen cannot settle (§442).

Ahead of the tap only: the pages code kept as unsettled (a blog section on a
host that is neither a blogging platform nor a primary source) are judged by
`SUPERVISOR_SCREEN_MODEL` in one call an episode; opinion and blog pages -
never the asked-for person's own - leave the packet, the archive's intake
and the credits. On the tap, or with Mclovin off, nothing is asked.
"""
from __future__ import annotations

import asyncio
import dataclasses

import pytest

import research
import script_generator as sg
from agents import models, netenyahu, supervisor
from agents.handoffs import ResearchPlan
from script_generator import ScriptNotes, plan_episode


@pytest.fixture(autouse=True)
def fresh():
    supervisor.reset()
    supervisor._JUDGED.clear()
    yield
    supervisor.reset()
    supervisor._JUDGED.clear()


@dataclasses.dataclass
class Page:
    url: str
    title: str
    published_date: str = "2026-11-02"
    highlights: list = dataclasses.field(default_factory=lambda: ["Words on the page."])


PAGES = [Page("https://apnews.com/article/fed", "Fed holds rates"),
         Page("https://www.example-co.com/blog/our-take", "Our take on rates"),
         Page("https://blog.example-bank.com/news/x", "Bank announces new branch"),
         Page("https://www.example-co.com/blog/krugman", "Rates | Paul Krugman")]


def _ask(kinds, status=models.OK):
    asked = []

    async def ask(request, notes=None, client=None):
        asked.append(request)
        if status != models.OK:
            return models.AgentResult(status, "supervisor", "screen")
        return models.AgentResult(models.OK, "supervisor", "screen", value=supervisor.PageKinds(
            pages=[supervisor._Page(page=i, kind=k) for i, k in enumerate(kinds, 1)]))
    return ask, asked


def test_the_screen_records_what_it_could_not_settle():
    with supervisor.screening(""):
        kept = research.screen_results(PAGES)
        unsettled = supervisor.active().unsettled
    assert len(kept) == 4                                     # kept, on the tap
    assert [u["url"] for u in unsettled] == [PAGES[1].url, PAGES[2].url, PAGES[3].url]
    assert unsettled[0]["passage"] == "Words on the page."


def test_one_call_judges_them_and_opinion_and_blogs_go(monkeypatch):
    ask, asked = _ask(["blog", "official", "opinion"])
    monkeypatch.setattr(models, "ask", ask)
    with supervisor.screening(""):
        research.screen_results(PAGES)
        drop = asyncio.run(supervisor.judge_unsettled(supervisor.active()))
    assert len(asked) == 1
    request = asked[0]
    assert request.model_setting == "supervisor_screen_model"
    assert request.surface == "ahead" and request.rule_book == "mclovin.md"
    assert "[1] address: https://www.example-co.com/blog/our-take" in dict(request.material)["evidence"]
    assert [p["url"] for p in drop] == [PAGES[1].url, PAGES[3].url]
    assert supervisor.SINCE_BOOT["screen_model_dropped_blog"] == 1


def test_the_asked_for_persons_own_page_stays(monkeypatch):
    monkeypatch.setattr(models, "ask", _ask(["blog", "official", "opinion"])[0])
    with supervisor.screening("Paul Krugman"):
        research.screen_results(PAGES)
        drop = asyncio.run(supervisor.judge_unsettled(supervisor.active()))
    assert [p["url"] for p in drop] == [PAGES[1].url]


@pytest.mark.parametrize("status", [models.TIMEOUT, models.OFF, models.INVALID, models.FAILED])
def test_anything_but_an_answer_keeps_every_page(monkeypatch, status):
    monkeypatch.setattr(models, "ask", _ask([], status)[0])
    with supervisor.screening(""):
        research.screen_results(PAGES)
        assert asyncio.run(supervisor.judge_unsettled(supervisor.active())) == []


def test_unsure_is_kept_and_a_page_is_judged_once(monkeypatch):
    ask, asked = _ask(["unsure", "official", "blog"])
    monkeypatch.setattr(models, "ask", ask)
    with supervisor.screening(""):
        research.screen_results(PAGES)
        first = asyncio.run(supervisor.judge_unsettled(supervisor.active()))
    assert [p["url"] for p in first] == [PAGES[3].url]
    ask2, asked2 = _ask(["opinion"])
    monkeypatch.setattr(models, "ask", ask2)
    with supervisor.screening(""):
        research.screen_results(PAGES)
        again = asyncio.run(supervisor.judge_unsettled(supervisor.active()))
    # Only the unsure page is asked again; the others are remembered.
    assert len(asked2) == 1 and "our-take" in dict(asked2[0].material)["evidence"]
    assert "example-bank" not in dict(asked2[0].material)["evidence"]
    assert {p["url"] for p in again} == {PAGES[1].url, PAGES[3].url}


EVIDENCE = """SOURCE 1
Title: Fed holds rates
Published: 2026-11-02 (yesterday)
Key evidence:
The Fed held.

SOURCE 2
Title: Our take on rates
Published: 2026-11-02 (yesterday)
Key evidence:
We think rates should fall.
"""


def test_a_ruled_out_page_leaves_the_packet():
    out, n = supervisor.drop_sources(EVIDENCE, ["Our take on rates"])
    assert n == 1 and "Our take on rates" not in out and "Fed holds rates" in out


class _Seen(sg.ScriptGenerator):
    def __init__(self):
        pass

    async def _research(self, plan, notes=None):
        research.screen_results(PAGES)
        return dataclasses.replace(plan, evidence=EVIDENCE)


def _notes(ahead):
    notes = ScriptNotes()
    notes.ahead = ahead
    notes.agents = netenyahu.EpisodeRun(("supervisor",), {})
    notes.research_plan = ResearchPlan()
    notes.research = {}
    return notes


def test_ahead_of_the_tap_the_packet_loses_the_ruled_out_page(monkeypatch):
    ask, asked = _ask(["blog", "official", "unsure"])
    monkeypatch.setattr(models, "ask", ask)
    notes = _notes(ahead=True)
    out = asyncio.run(_Seen().research(plan_episode("rates", 1.5), notes))
    assert "Our take on rates" not in out.evidence and "Fed holds rates" in out.evidence
    assert notes.research["screen_model_dropped"] == 1


def test_on_the_tap_nothing_is_asked(monkeypatch):
    ask, asked = _ask(["blog", "official", "opinion"])
    monkeypatch.setattr(models, "ask", ask)
    out = asyncio.run(_Seen().research(plan_episode("rates", 1.5), _notes(ahead=False)))
    assert asked == [] and "Our take on rates" in out.evidence


def test_with_mclovin_off_nothing_is_recorded_or_asked(monkeypatch):
    ask, asked = _ask(["blog"])
    monkeypatch.setattr(models, "ask", ask)
    notes = _notes(ahead=True)
    notes.agents = netenyahu.EpisodeRun((), {})
    out = asyncio.run(_Seen().research(plan_episode("rates", 1.5), notes))
    assert asked == [] and out.evidence == EVIDENCE
