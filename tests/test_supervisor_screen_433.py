"""Mclovin's source screen (Phase 3, decided items 6 and 13, §433).

Opinion pieces and blog posts are dropped from both packets inside
`research.screen_results` and on the archive's facts - only while Mclovin
works on the episode. An asked-for person's own pieces are kept and marked
as opinion; official blogs and "analysis" are kept; a page code cannot
settle is kept and counted. With him off nothing changes.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

import research
from agents import netenyahu, supervisor
from agents.handoffs import ResearchPlan
from script_generator import ScriptGenerator, ScriptNotes, plan_episode


@pytest.fixture(autouse=True)
def fresh():
    supervisor.reset()
    yield
    supervisor.reset()


@dataclass
class Page:
    url: str
    title: str = ""
    published_date: str = "2026-11-02"
    highlights: list = field(default_factory=lambda: ["The Fed held rates at 4%."])


# ------------------------------------------------------------------ what a page says it is
@pytest.mark.parametrize("url,title,kind", [
    # Opinion, from the section in the address.
    ("https://www.nytimes.com/2026/11/01/opinion/tariffs.html", "Why tariffs fail", "opinion"),
    ("https://www.washingtonpost.com/opinions/2026/11/01/fed/", "The Fed", "opinion"),
    ("https://www.theguardian.com/commentisfree/2026/nov/01/x", "x", "opinion"),
    ("https://www.latimes.com/opinion/letters-to-the-editor/story", "Readers", "opinion"),
    ("https://example.com/news/editorial/the-board", "The board", "opinion"),
    # Opinion, from the title's label.
    ("https://www.wsj.com/articles/fed-wrong-123", "Opinion | The Fed is wrong", "opinion"),
    ("https://www.cnn.com/2026/11/01/x", "Op-Ed: Rates must fall", "opinion"),
    ("https://www.example.com/x", "Rates must fall - Opinion", "opinion"),
    ("https://www.example.com/x", "Rates must fall (opinion)", "opinion"),
    # Blogs on a blogging platform, whatever the path.
    ("https://someone.substack.com/p/the-fed", "The Fed", "blog"),
    ("https://medium.com/@someone/the-fed-1", "The Fed", "blog"),
    ("https://rates.blogspot.com/2026/11/x.html", "x", "blog"),
    ("https://rates.wordpress.com/x", "x", "blog"),
    # Analysis is reporting.
    ("https://www.reuters.com/markets/x", "Analysis: The Fed's next move", "analysis"),
    ("https://www.cnn.com/2026/11/01/politics/analysis/x", "x", "analysis"),
    # Official blogs and newsrooms are primary sources.
    ("https://www.whitehouse.gov/blog/2026/11/01/x", "x", "official"),
    ("https://www.federalreserve.gov/newsevents/blogs/x", "x", "official"),
    ("https://www.apple.com/newsroom/2026/11/x/", "Apple announces", "official"),
    ("https://example-co.com/press-releases/x", "x", "official"),
    # A blog section code cannot settle is kept, counted.
    ("https://blog.google/products/x", "x", "unsettled"),
    ("https://www.example-co.com/blog/x", "x", "unsettled"),
    ("https://www.cnbc.com/blogs/x", "x", "unsettled"),
    # Reporting, including words that only look like a label.
    ("https://apnews.com/article/fed-holds-rates", "Fed holds rates", "reporting"),
    ("https://www.cnn.com/2026/politics/opinion-poll-shows-lead/", "Poll", "reporting"),
    ("https://www.example.com/news/x", "Opinion polls tighten in Ohio", "reporting"),
    ("https://www.example.com/news/blogger-arrested", "Blogger arrested", "reporting"),
    ("https://www.example.com/opinion", "Opinion", "opinion"),
])
def test_what_a_page_says_it_is(url, title, kind):
    assert supervisor.classify(url, title).kind == kind


def test_opinion_section_beats_an_analysis_label():
    # An analysis column inside the opinion pages is still the opinion pages.
    assert supervisor.classify("https://www.bloomberg.com/opinion/articles/x",
                               "Analysis: the Fed").kind == "opinion"


def test_classify_never_raises():
    assert supervisor.classify(None, None).kind in ("reporting", "unsettled")


# ------------------------------------------------------------------ whose opinion
@pytest.mark.parametrize("url,title,passages,theirs", [
    ("https://paulkrugman.substack.com/p/tariffs", "Tariffs", (), True),
    ("https://www.nytimes.com/column/paul-krugman/tariffs.html", "Tariffs", (), True),
    ("https://www.example.com/opinion/x", "Paul Krugman: tariffs fail", (), True),
    ("https://www.example.com/opinion/x", "Tariffs fail | Paul Krugman", (), True),
    ("https://www.example.com/opinion/x", "Tariffs fail", ("By Paul Krugman",), True),
    # About him, not by him.
    ("https://www.example.com/opinion/paul-krugman-is-wrong", "Krugman is wrong", (), False),
    ("https://www.example.com/opinion/x", "Why Paul Krugman is wrong on tariffs", (), False),
    ("https://www.example.com/opinion/x", "x", ("Paul Krugman argued last week",), False),
])
def test_a_piece_by_the_person(url, title, passages, theirs):
    assert supervisor.by("Paul Krugman", url, title, passages) is theirs


def test_a_one_word_name_is_never_found_in_an_address():
    assert not supervisor.by("Obama", "https://obama.substack.com/p/x", "x")
    assert supervisor.by("Obama", "https://x.com/opinion/y", "Obama: my view")


# ------------------------------------------------------------------ off changes nothing
PAGES = [Page("https://apnews.com/article/fed", "Fed holds rates"),
         Page("https://www.nytimes.com/2026/11/01/opinion/fed.html", "The Fed is wrong"),
         Page("https://someone.substack.com/p/fed", "My take on the Fed"),
         Page("https://www.example-co.com/blog/fed", "Our view"),
         Page("https://www.reuters.com/markets/x", "Analysis: The Fed")]


def test_off_the_screen_is_a_no_op():
    assert supervisor.active() is None
    assert research.screen_results(PAGES, query="the fed") == PAGES
    assert supervisor.screen(PAGES) == (PAGES, [])
    assert supervisor.SINCE_BOOT == {}


def test_off_the_packet_is_byte_for_byte_what_it_was():
    before = research.build_packet(PAGES, 5, 2)
    with supervisor.screening(""):
        pass
    assert research.build_packet(PAGES, 5, 2) == before
    assert "Opinion:" not in before


# ------------------------------------------------------------------ on
def test_on_opinion_and_blogs_are_dropped_and_counted():
    with supervisor.screening(""):
        kept = research.screen_results(PAGES, query="the fed")
    assert [p.url for p in kept] == [PAGES[0].url, PAGES[3].url, PAGES[4].url]
    counts = supervisor.report()["since_boot"]
    assert counts["screen_dropped_opinion"] == 1
    assert counts["screen_dropped_blog"] == 1
    assert counts["screen_unsettled"] == 1
    assert counts["screen_pages"] == 5
    assert supervisor.report()["screen_drop_rate"] == 0.4


def test_drops_are_logged_with_their_hosts(caplog):
    with caplog.at_level("INFO"), supervisor.screening(""):
        research.screen_results(PAGES, query="the fed")
    assert "nytimes.com (opinion section in the address)" in caplog.text
    assert "someone.substack.com (a blogging platform)" in caplog.text


def test_an_asked_for_opinion_keeps_only_that_persons_pieces():
    pages = [Page("https://paulkrugman.substack.com/p/tariffs", "Tariffs"),
             Page("https://www.example.com/opinion/x", "Why Paul Krugman is wrong"),
             Page("https://www.example.com/opinion/y", "Tariffs | Paul Krugman"),
             Page("https://apnews.com/article/tariffs", "Tariffs rise")]
    with supervisor.screening("Paul Krugman"):
        kept = research.screen_results(pages, query="what does paul krugman think")
        packet = research.build_packet(kept, 5, 2)
    assert [p.url for p in kept] == [pages[0].url, pages[2].url, pages[3].url]
    assert packet.count("Opinion: Paul Krugman's own view - tell it as their view, "
                        "never as fact") == 2
    assert supervisor.report()["since_boot"]["screen_opinion_kept"] == 2


def test_the_screen_runs_before_the_sports_count():
    # A column must not count as one of the known outlets that lets the
    # sports screen drop the unknown ones.
    pages = [Page("https://www.espn.com/nfl/story/_/id/1/x", "Eagles win"),
             Page("https://www.espn.com/nfl/columns/x", "My take"),
             Page("https://unknown-sports-site.com/x", "Eagles win 24-10")]
    with supervisor.screening(""):
        kept = research.screen_results(pages, live_domain="sports")
    assert [p.url for p in kept] == [pages[0].url, pages[2].url]


def test_threads_and_tasks_started_inside_see_the_screen():
    async def go():
        with supervisor.screening("Paul Krugman"):
            in_thread = await asyncio.to_thread(supervisor.active)
            in_task = await asyncio.ensure_future(_active())
        return in_thread, in_task, supervisor.active()

    async def _active():
        return supervisor.active()

    in_thread, in_task, after = asyncio.run(go())
    assert in_thread.opinion_of == in_task.opinion_of == "Paul Krugman"
    assert after is None


def test_a_failing_screen_keeps_everything(monkeypatch):
    monkeypatch.setattr(supervisor, "classify", lambda *a: 1 / 0)
    with supervisor.screening(""):
        assert research.screen_results(PAGES) == PAGES


# ------------------------------------------------------------------ the archive's packet
@dataclass
class Fact:
    url: str
    title: str = ""
    text: str = "The Fed held rates."
    published: str = "2026-10-01"


def test_the_archive_facts_are_screened_the_same_way():
    facts = [Fact("https://apnews.com/article/a"),
             Fact("https://www.nytimes.com/2026/10/01/opinion/b.html"),
             Fact("https://x.substack.com/p/c"),
             Fact("live:balldontlie:nfl:1")]
    assert supervisor.screen_facts(facts) == facts            # off
    with supervisor.screening(""):
        kept = supervisor.screen_facts(facts)
    assert [f.url for f in kept] == [facts[0].url, facts[3].url]
    assert supervisor.report()["since_boot"]["screen_archive_dropped_opinion"] == 1


def test_finish_history_look_screens_the_recall():
    async def go():
        packet = research.Packet(context="SOURCE 1\nTitle: x\n", sources=["apnews.com"])
        packet.findings = []
        loop = asyncio.get_running_loop()
        look = loop.create_future()
        look.set_result([_fact("https://www.nytimes.com/2026/10/01/opinion/b.html")])
        with supervisor.screening(""):
            await research.finish_history_look(packet, look)
        return packet

    packet = asyncio.run(go())
    assert packet.archive == "nothing relevant"


def _fact(url):
    import history_archive

    return history_archive.Fact(url=url, text="The Fed held rates.", title="b",
                                published="2026-10-01")


# ------------------------------------------------------------------ in force only while Mclovin works
class _Seen(ScriptGenerator):
    def __init__(self):
        self.seen = "unset"

    async def _research(self, plan, notes=None):
        self.seen = supervisor.active()
        return plan


def _notes(roles, opinion=""):
    notes = ScriptNotes()
    notes.agents = netenyahu.EpisodeRun(tuple(roles), {})
    notes.research_plan = ResearchPlan(opinion_requested=opinion)
    return notes


def test_the_research_step_screens_only_when_mclovin_works():
    plan = plan_episode("the fed", 1.5)
    off = _Seen()
    asyncio.run(off.research(plan, _notes(())))
    assert off.seen is None
    asyncio.run(off.research(plan, None))
    assert off.seen is None
    on = _Seen()
    asyncio.run(on.research(plan, _notes(("supervisor",), "Paul Krugman")))
    assert on.seen == supervisor.Screen("Paul Krugman")
    assert supervisor.active() is None


def test_health_reports_the_screen():
    from agents import models

    with supervisor.screening(""):
        research.screen_results(PAGES)
    block = models.health()
    assert block["supervisor"]["since_boot"]["screen_pages"] == 5
