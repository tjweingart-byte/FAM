"""Where an episode's facts came from, and who is allowed to see them.

FAM already collected all of this and discarded it at the last step - the
domains were extracted, graded, dated, stored on `notes.research`, and never
read back. These tests are mostly about the two rules that make surfacing it
safe rather than about the plumbing:

1. **The display channel is not the prompt channel.** The evidence packet
   carries source *grades* and deliberately never hostnames, because a domain
   in the packet is a domain the voice can read out. Showing sources in the app
   is not a reason to let the model cite them aloud.
2. **A listener's own documents are theirs.** The script cache is shared and
   feeds Explore, so an attachment title must never be written to it.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cache as cache_mod  # noqa: E402
import live_facts  # noqa: E402
import provenance as P  # noqa: E402


class FakeResult:
    def __init__(self, url, title="", published_date=""):
        self.url = url
        self.title = title
        self.published_date = published_date


# --------------------------------------------------------------------------
# the two channels stay separate
# --------------------------------------------------------------------------
def test_nothing_in_the_provenance_path_reaches_a_prompt():
    """The rule this whole feature is one refactor away from breaking.

    CLAUDE.md: the packet carries the grade and never the hostname, because a
    domain in the packet is a domain the voice can read out. The obvious next
    step once sources are displayed - "let the model cite them" - would undo
    that, so the separation is asserted rather than trusted."""
    import inspect

    import research
    import script_generator as sg

    prompt = inspect.getsource(sg.build_prompt)
    assert "provenance" not in prompt
    assert "hostname" not in prompt

    # The packet builder still describes tiers and never hosts.
    packet = research.build_packet(
        [FakeResult("https://www.reuters.com/a", "A headline", "2026-09-14")],
        packet_sources=1, highlights_per_source=1)
    assert "reuters" not in packet.lower()
    assert "wire service" in packet.lower()


def test_the_display_side_does_carry_the_publisher():
    """The other half of the same rule: out of band, it is exactly what a
    person needs to judge the episode."""
    found = P.from_results(
        [FakeResult("https://www.reuters.com/a", "A headline", "2026-09-14")],
        retriever="exa")
    item = found.items[0]
    assert item.label == "reuters.com"
    assert item.title == "A headline"
    assert item.at == "2026-09-14"
    assert "wire service" in item.tier


@pytest.mark.parametrize("given, expected", [
    ("https://www.bbc.co.uk/news/x", "bbc.co.uk"),
    ("http://apnews.com/a?b=c", "apnews.com"),
    ("www.ft.com", "ft.com"),
    ("", ""),
])
def test_a_host_is_shown_rather_than_a_url(given, expected):
    assert P.clean_host(given) == expected


def test_no_publisher_display_name_lookup():
    """Deliberately not a host -> masthead map. A stale one shows the wrong
    masthead, which on a provenance panel is worse than showing the domain."""
    assert P.clean_host("https://reuters.com/x") == "reuters.com"


# --------------------------------------------------------------------------
# private things stay private
# --------------------------------------------------------------------------
class FakeAttachment:
    def __init__(self, name):
        self.name = name
        self.kind = "pdf"


def test_an_attachment_title_is_never_written_to_the_shared_cache():
    """The script cache is shared and feeds Explore. A cached attachment title
    would show one listener the name of another listener's file."""
    found = P.Provenance()
    found.add(P.Attribution(label="reuters.com", kind=P.ARTICLE))
    for item in P.from_attachments([FakeAttachment("my blood results.pdf")]):
        found.add(item)

    assert len(found.items) == 2
    assert "my blood results.pdf" not in found.to_json()
    stored = P.Provenance.from_json(found.to_json())
    labels = [i.label for i in stored.items]
    assert "reuters.com" in labels
    assert "my blood results.pdf" not in labels, "a private title was cached"


def test_attachments_are_shown_to_the_listener_who_attached_them():
    items = P.from_attachments([FakeAttachment("q3-report.docx")])
    assert items[0].label == "q3-report.docx"
    assert items[0].kind == P.ATTACHMENT
    assert items[0].private is True
    # And they reach the live response, which is not the cached one.
    found = P.Provenance(items=items)
    assert found.as_dict()["count"] == 1


# --------------------------------------------------------------------------
# a live provider is credited only when it actually answered
# --------------------------------------------------------------------------
def test_a_live_provider_that_answered_is_credited():
    facts = live_facts.LiveFacts(
        domain="sports", source="a scoreboard",
        as_of=datetime.now(timezone.utc), facts=["They lead by two."],
        status=live_facts.IN_PROGRESS)
    item = P.from_live(live_facts.LiveLookup("sports", live_facts.FACTS, facts))
    assert item is not None
    assert item.label == "a scoreboard"
    assert item.kind == P.LIVE


@pytest.mark.parametrize("outcome", [
    live_facts.NOT_CONFIGURED, live_facts.PROVIDER_FAILED,
    live_facts.TIMEOUT, live_facts.STALE, live_facts.NO_ENTITY,
])
def test_a_live_provider_that_did_not_answer_is_never_credited(outcome):
    """Listing a provider that failed, timed out or returned something too
    stale to use would be claiming corroboration that did not happen."""
    assert P.from_live(live_facts.LiveLookup("sports", outcome)) is None


def test_a_delayed_feed_says_so_in_the_panel_too():
    """The prompt already refuses to call delayed data current. The panel a
    listener reads must not undo that."""
    facts = live_facts.LiveFacts(
        domain="markets", source="a quotes feed",
        as_of=datetime.now(timezone.utc), facts=["It is at forty-two ten."],
        status=live_facts.UNKNOWN, delayed_seconds=1200)
    item = P.from_live(live_facts.LiveLookup("markets", live_facts.FACTS, facts))
    assert "delayed" in item.tier


# --------------------------------------------------------------------------
# a cache hit shows the same sources a fresh generation does
# --------------------------------------------------------------------------
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_provenance_survives_a_cache_hit(backend, tmp_path):
    """Without this, a shared or Explore episode would show an empty panel
    while a freshly generated one showed a full list - the same inconsistency
    the `thread` column already solved."""
    store = (cache_mod.MemoryScriptCache() if backend == "memory"
             else cache_mod.SqliteScriptCache(str(tmp_path / "c.db")))
    found = P.Provenance(retrievers=["exa"])
    found.add(P.Attribution(label="apnews.com", kind=P.ARTICLE, tier="wire service"))
    store.put("k", ["One."], 60, "q", "thread", 3, "", found.to_json())

    back = P.Provenance.from_json(store.sources("k"))
    assert [i.label for i in back.items] == ["apnews.com"]
    assert back.retrievers == ["exa"]


def test_an_expired_entry_reports_no_sources(tmp_path):
    store = cache_mod.SqliteScriptCache(str(tmp_path / "c.db"))
    store.put("k", ["One."], -1, "q", "", 3, "", '{"items":[{"label":"x"}]}')
    assert store.sources("k") == ""


def test_unreadable_stored_provenance_is_not_an_error():
    """A panel that failed to parse must never disturb playback."""
    assert P.Provenance.from_json("not json").items == []
    assert P.Provenance.from_json("").items == []


# --------------------------------------------------------------------------
# corroboration is counted honestly
# --------------------------------------------------------------------------
def test_one_outlet_quoted_three_times_is_one_line():
    found = P.from_results([
        FakeResult("https://reuters.com/a", "A"),
        FakeResult("https://reuters.com/b", "B"),
        FakeResult("https://apnews.com/c", "C"),
    ], retriever="exa")
    assert sorted(i.label for i in found.items) == ["apnews.com", "reuters.com"]


def test_which_indexes_were_consulted_is_separate_from_which_outlets_published():
    """"Two indexes agreed" is a different claim from "two newspapers agreed",
    and collapsing them would overstate the corroboration."""
    found = P.from_results([FakeResult("https://reuters.com/a")], retriever="exa")
    second = P.from_results([FakeResult("https://bbc.co.uk/b")], retriever="gdelt")
    for item in second.items:
        found.add(item)
    found.retrievers.append("gdelt")

    body = found.as_dict()
    assert body["count"] == 2
    assert body["retrievers"] == ["exa", "gdelt"]


def test_a_source_with_no_date_says_so_rather_than_being_filled_in():
    """Same rule as `research.published_at`: an undated source that looks
    dated is how an old article becomes "last night"."""
    found = P.from_results([FakeResult("https://example.com/a", "T", "")])
    assert found.items[0].at == ""


# --------------------------------------------------------------------------
# live captions read the same cache, and never write to it
# --------------------------------------------------------------------------
def _pipe(store):
    """A pipeline with nothing in it but a cache. `script_for` reads the cache
    and nothing else, which is the whole point of it."""
    import pipeline as pipeline_mod

    pipe = pipeline_mod.PodcastPipeline.__new__(pipeline_mod.PodcastPipeline)
    pipe.cache = store
    # `_cache_key` reaches the generator for the semantic-key client, which is
    # off here; an object with no `client` is exactly what it is written to
    # tolerate.
    pipe.generator = object()
    return pipe


def test_the_transcript_is_read_from_the_cache(tmp_path):
    """What the captions panel shows. The same key the script is stored under,
    and the same read `sources_for` and `thread_for` make."""
    import asyncio

    import pipeline as pipeline_mod
    import script_generator

    store = cache_mod.SqliteScriptCache(str(tmp_path / "c.db"))
    plan = script_generator.plan_episode("why bonds move", 2)
    key = asyncio.run(pipeline_mod.key_for(plan))
    store.put(key, ["One.", "Two."], 60, plan.query, "", plan.minutes, "", "")
    assert asyncio.run(_pipe(store).script_for(plan)) == ["One.", "Two."]


def test_an_uncached_episode_has_no_transcript_rather_than_a_new_one(tmp_path):
    """The rule the whole feature rests on: captions that could trigger a
    write would be a second full Claude call for every episode somebody chose
    to read along with - the expensive half of an episode, paid twice for one
    listen. So a miss is an empty list, never a generation."""
    import asyncio

    import script_generator

    store = cache_mod.SqliteScriptCache(str(tmp_path / "c.db"))
    plan = script_generator.plan_episode("nothing is cached for this", 2)
    assert asyncio.run(_pipe(store).script_for(plan)) == []


def test_an_attachment_episode_has_no_transcript():
    """An attached episode is never cached, so there is nothing to read back -
    which is the privacy rule working, not a failure. A listener's own
    document must not reach another listener through a caption track any more
    than through Explore."""
    import asyncio

    import script_generator

    store = cache_mod.MemoryScriptCache()
    plan = script_generator.plan_episode(
        "what does my contract say", 2, attachments=(object(),))
    assert asyncio.run(_pipe(store).script_for(plan)) == []


# --------------------------------------------------------------------------
# the episode's own title, on the same kind of line and in the same column
# --------------------------------------------------------------------------
def test_the_title_comes_off_a_marker_line_the_voice_never_reads():
    """The alternative was a model call in front of - or behind - every
    episode purely to name it, which is the expensive half of an episode spent
    on a label. A marker line is free."""
    import script_generator as sg

    text = ("The strait is quieter than it was. "
            "<<TITLE: The Two-Mile Lane That Moves the Oil>> "
            "<<NEXT: whether the insurers widen the zone again>>")
    assert sg.extract_title(text) == "The Two-Mile Lane That Moves the Oil"
    assert sg.extract_thread(text) == "whether the insurers widen the zone again"
    spoken = sg.clean_for_speech(text)
    assert "TITLE" not in spoken and "Two-Mile" not in spoken
    assert spoken == "The strait is quieter than it was."


def test_an_episode_with_no_title_line_falls_back_rather_than_failing():
    """Which is what every episode did before this existed."""
    import script_generator as sg

    assert sg.extract_title("Just the script. <<NEXT: something>>") == ""
    assert sg.extract_title("") == ""


def test_both_prompts_ask_for_the_title():
    import script_generator as sg

    assert "<<TITLE:" in sg.SYSTEM_PROMPT
    assert "<<TITLE:" in sg.build_prompt(sg.plan_episode("why bonds move", 2))
    # And say what it must not be, because the failure this replaces is a
    # title that is the question.
    assert "never the question you were" in sg.SYSTEM_PROMPT


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_a_title_survives_a_cache_hit(backend, tmp_path):
    """Stored beside the script for the same reason `thread` is: a replayed
    episode has no `notes`, so without this a shared or Explore episode would
    be titled with whatever the first listener happened to type while a
    freshly generated one had a real name."""
    store = (cache_mod.MemoryScriptCache() if backend == "memory"
             else cache_mod.SqliteScriptCache(str(tmp_path / "c.db")))
    store.put("k", ["One."], 60, "q", "thread", 3, "", "", "", "A Real Name")
    assert store.title("k") == "A Real Name"


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_a_rewrite_keeps_the_title_it_has(backend, tmp_path):
    """A longer TTL or fresher sources must not blank the name of an episode
    that is already in somebody's feed - the same rule authorship keeps."""
    store = (cache_mod.MemoryScriptCache() if backend == "memory"
             else cache_mod.SqliteScriptCache(str(tmp_path / "c.db")))
    store.put("k", ["One."], 60, "q", "", 3, "", "", "", "A Real Name")
    store.put("k", ["One."], 600, "q", "", 3, "", "", "")
    assert store.title("k") == "A Real Name"


def test_explore_cards_carry_the_title_and_fall_back_to_the_question(tmp_path):
    store = cache_mod.SqliteScriptCache(str(tmp_path / "c.db"))
    store.put("named", ["One."], 60, "why bonds move", "", 3, "", "", "",
              "What Moves A Bond")
    store.put("unnamed", ["One."], 60, "why stocks move", "", 3)
    by_key = {e["key"]: e for e in store.recent(10)}
    assert by_key["named"]["title"] == "What Moves A Bond"
    assert by_key["unnamed"]["title"] == ""
