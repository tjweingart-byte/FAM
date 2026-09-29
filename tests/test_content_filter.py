"""Slurs are taken out; swearing stays and marks the episode explicit (§171).

The owner's direction: no censorship of what sources say or how people
speak, except words made to be hateful to a group of people - and an E on any
episode that swears. The tests below pin both halves, and pin the half that
is easiest to get wrong: that nothing *else* is touched.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import content_filter as cf  # noqa: E402
import script_generator as sg  # noqa: E402
from cache import MemoryScriptCache, SqliteScriptCache  # noqa: E402
from pipeline import PodcastPipeline  # noqa: E402
from script_generator import plan_episode  # noqa: E402
from tts import DebugEngine  # noqa: E402

from test_pipeline import FakeGenerator  # noqa: E402


@pytest.mark.parametrize("said, heard", [
    ("He called them a nigger on air.", "He called them a slur on air."),
    ("Fans shouted faggots at the referee.", "Fans shouted slurs at the referee."),
    ("He used the word retard in a post.", "He used a slur in a post."),
    ("Kike was sprayed on the wall.", "A slur was sprayed on the wall."),
    ("\"Spic,\" he said.", "\"A slur,\" he said."),
    ("He wrote 'faggot' in the chat.", "He wrote 'a slur' in the chat."),
    ("He yelled sand niggers at them.", "He yelled slurs at them."),
    ("It was the Tranny joke that got him fired.",
     "It was a slur joke that got him fired."),
])
def test_a_slur_becomes_the_words_a_slur(said, heard):
    assert cf.scrub(said) == heard
    assert cf.slurs_in(said)
    assert not cf.slurs_in(heard)


@pytest.mark.parametrize("text", [
    # Letters inside other words - the Scunthorpe problem.
    "Scunthorpe beat Dickens in the Assessment Cup at the cocktail bar.",
    "The assassin's classic spices came from Pakistan and Japan.",
    "Sean Spicer spoke at the Kikkoman plant about the fagus trees.",
    # Idioms and compounds that contain a listed word.
    "The only chink in the Chiefs' armour is the right tackle.",
    "Chinks of light came through the blinds.",
    "They played honky-tonk until two in the morning.",
    "The additive is used to retard combustion, and it is a flame retardant.",
    # Words left off the list on purpose, because they are also names,
    # foods, sports, clinical terms or history.
    "Carrie Coon and Dick Van Dyke opened with a sambo exhibition.",
    "The Washington Redskins became the Commanders in 2022.",
    "Spastic cerebral palsy affects movement.",
    "The United Negro College Fund raised a record amount.",
    # Swearing is not censored.
    "The fucking Eagles blew it again, and the coach said so: shit.",
])
def test_nothing_else_is_touched(text):
    assert cf.scrub(text) == text


def test_every_deliberate_absence_is_really_absent():
    """A word explained in `DELIBERATELY_ABSENT` must not also be filtered,
    or the explanation is a lie."""
    for word in cf.DELIBERATELY_ABSENT:
        assert word not in cf.SLURS
        assert cf.scrub(f"They said {word} twice.") == f"They said {word} twice."


@pytest.mark.parametrize("text, explicit", [
    ("That was a fucking disaster.", True),
    ("Bullshit, said the senator.", True),
    ("He called the ref a bastard.", True),
    # Mild words, and words that are only rude in some senses, carry no E -
    # or the mark would be on every episode about Hell's Kitchen.
    ("Damn, what the hell happened at Hell's Kitchen?", False),
    ("The donkey is an ass, and the result was crap.", False),
    ("Dick Cheney and a cocktail in Scunthorpe.", False),
    ("", False),
])
def test_swearing_marks_an_episode_explicit(text, explicit):
    assert cf.is_explicit(text) is explicit
    assert cf.is_explicit([text]) is explicit


def test_the_writer_never_speaks_a_slur_and_keeps_its_swearing():
    """`clean_for_speech` is the one door every writer's sentences pass
    through before the voice, the captions and the cache."""
    assert sg.clean_for_speech("The crowd chanted chinks at him, and shit got ugly.") \
        == "The crowd chanted slurs at him, and shit got ugly."


def test_the_title_summary_and_next_line_are_scrubbed_too():
    text = ("Body.\n<<TITLE: The Wetback Remark That Ended a Career>>\n"
            "<<SUMMARY: Why he said wetback on air.>>\n"
            "<<NEXT: whether the network knew he said wetback before>>")
    assert sg.extract_title(text) == "A slur Remark That Ended a Career"
    assert "wetback" not in sg.extract_summary(text).lower()
    assert "wetback" not in sg.extract_thread(text).lower()


def test_the_writers_prompt_carries_no_word_list():
    """The filter is code, applied to what every writer yields; the prompt
    stays under its size guard and says nothing about swearing either way,
    so the writing is exactly what it was before §171."""
    prompt = sg.system_prompt().lower()
    assert "slur" not in prompt and "profan" not in prompt


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryScriptCache()
    return SqliteScriptCache(str(tmp_path / "scripts.db"))


def test_a_row_written_before_the_filter_is_scrubbed_on_the_way_out(tmp_path):
    """A kept episode lives a week. One written before §171 must not say what
    a new one could not - in its text, its title, or its kept audio."""
    store = SqliteScriptCache(str(tmp_path / "scripts.db"))
    store.put("k", ["He called them a kike.", "It went badly."], 600, "the remark",
              "whether the kike remark cost him", 1, "", "", "someone",
              title="The Kike Remark")
    assert store.get("k") == ["He called them a slur.", "It went badly."]
    assert "kike" not in store.title("k").lower()
    assert "kike" not in store.thread("k").lower()
    assert all("kike" not in e["title"].lower() for e in store.recent())
    # Its audio was voiced with the word in it and cannot be scrubbed: it is
    # dropped, and the episode is voiced again from the clean script.
    store.put_audio("k", "v", 24000, b"\x00\x00" * 2400,
                    ["He called them a kike.", "It went badly."], [0.0, 0.05])
    # Not promised to anybody either: a guest's tap is let through on these,
    # and must never reach the voice engine because of them.
    assert not store.has_audio("k", "v", 24000)
    assert not store.has_any_audio("k")
    assert store.get_audio("k", "v", 24000) is None
    assert not store.has_audio("k", "v", 24000)


def test_a_clean_rows_audio_is_still_kept(tmp_path):
    store = SqliteScriptCache(str(tmp_path / "scripts.db"))
    store.put("k", ["It went badly."], 600, "the remark", "", 1, "", "", "someone")
    store.put_audio("k", "v", 24000, b"\x00\x00" * 2400, ["It went badly."], [0.0])
    assert store.has_audio("k", "v", 24000) and store.has_any_audio("k")
    assert store.get_audio("k", "v", 24000) is not None


def test_a_kept_episode_says_whether_it_swears(store):
    store.put("rude", ["That was a fucking disaster."], 600, "the rude one",
              "", 1, "", "", "someone")
    store.put("clean", ["That was a disaster."], 600, "the clean one",
              "", 1, "", "", "someone")
    assert store.explicit("rude") is True
    assert store.explicit("clean") is False
    assert store.explicit("never written") is False
    by_query = {e["query"]: e["explicit"] for e in store.recent()}
    assert by_query == {"the rude one": True, "the clean one": False}


def test_the_player_is_told_an_episode_is_explicit(store):
    """`/api/next` carries `explicit`, read off the kept script."""
    pipe = PodcastPipeline(generator=FakeGenerator(), engine=DebugEngine(), cache=store)
    rude, clean = plan_episode("why the coach lost it", 1), plan_episode("how tides work", 1)

    async def run():
        for plan, line in ((rude, "He said the call was bullshit."),
                           (clean, "The moon pulls the sea.")):
            store.put(await pipe._cache_key(plan), [line], 600, plan.query, "",
                      plan.minutes, "", "", "someone", title="A Title")
        return (await pipe.episode_meta(rude, current_only=True),
                await pipe.episode_meta(clean, current_only=True))

    rude_meta, clean_meta = asyncio.run(run())
    assert rude_meta["explicit"] is True
    assert clean_meta["explicit"] is False
