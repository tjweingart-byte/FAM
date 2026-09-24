"""§149: a typed DailyFAM topic that asks for a *kind of thing*.

"Founders lesson of the day" was sent as "The latest on Founders lesson of
the day ... what happened in the last 24 hours", so episode intelligence went
looking for a publication by that name and the episode reported that it had
not been updated lately. Three halves, pinned separately:

* a typed topic is worded so it can be either a subject or a kind of thing,
  and EI - the model call that can tell - is told how to read the second;
* a daily edition is told what its earlier editions were called, read from
  the cache under the keys those days' prompts produce;
* both EI and the writer are shown that list, and nothing else changes.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
from datetime import date
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import daily_edition  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import mixes as M  # noqa: E402
from cache import MemoryScriptCache  # noqa: E402
from pipeline import key_for  # noqa: E402
from script_generator import build_prompt, plan_episode  # noqa: E402

DAY = date(2026, 9, 24)
TOPIC = "Founders lesson of the day"


def _typed(day=DAY):
    return M.prompt_for(M.custom_item(TOPIC), day)


# --- the words --------------------------------------------------------------


def test_a_typed_topic_is_not_worded_as_news_about_a_named_thing():
    prompt = _typed()
    assert "The latest on" not in prompt
    assert f'"{TOPIC}"' in prompt and "Thursday, September 24, 2026" in prompt
    # Both readings are offered; EI decides which one this is.
    assert "kind of thing" in prompt and "last 24 hours" in prompt
    assert "never one from an earlier day" in prompt


def test_a_followed_subject_keeps_the_news_wording():
    prompt = M.prompt_for(M.followed_item("f:nfl"), DAY)
    assert prompt.startswith("The latest on NFL as of Thursday, September 24, 2026")
    assert "kind of thing" not in prompt


def test_the_longest_typed_topic_still_fits():
    item = M.custom_item("x" * M.MAX_QUERY)
    prompt = M.daily_prompt(item).replace(M.DAILY_DATE, M.LONGEST_DATE)
    assert len(prompt) <= M.MAX_PROMPT
    # A short topic keeps the whole instruction.
    assert _typed().endswith(M.TYPED_ENDINGS[0])


def test_the_preview_shim_builds_the_same_words():
    import json
    import shutil
    import subprocess

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "preview"))
    import build_preview

    js = build_preview.mix_items_js()
    assert json.dumps(M.TYPED_HEAD) in js
    node = shutil.which("node")
    if not node:
        return
    item = M.custom_item(TOPIC)
    script = js + "\nprocess.stdout.write(mixDailyPrompt(" + json.dumps(
        {"custom": True, "follow": False, "query": item.query}) + "));"
    out = subprocess.run([node, "-e", script], capture_output=True, text=True,
                         check=True).stdout
    assert out == M.daily_prompt(item)


# --- yesterday's prompt -----------------------------------------------------


def test_earlier_prompts_are_the_same_words_on_earlier_days():
    earlier = M.earlier_prompts(_typed(), 3)
    assert earlier == [_typed(date(2026, 9, 23)), _typed(date(2026, 9, 22)),
                       _typed(date(2026, 9, 21))]
    # Across a month boundary, and for a followed subject too.
    first = M.prompt_for(M.followed_item("f:nfl"), date(2026, 10, 1))
    assert M.earlier_prompts(first, 1) == [
        M.prompt_for(M.followed_item("f:nfl"), date(2026, 9, 30))]


def test_anything_that_is_not_a_dated_prompt_has_no_earlier_editions():
    assert M.earlier_prompts("how do tides work", 6) == []
    assert M.earlier_prompts("", 6) == []
    # A weekday that is not that date's is not one of ours.
    assert M.earlier_prompts("news for Monday, September 24, 2026", 6) == []


# --- what the edition is told -----------------------------------------------


def _store_edition(store, day, title):
    query = _typed(day)
    plan = plan_episode(query, daily_edition.minutes())
    key = asyncio.run(key_for(plan))
    store.put(key, ["Said."], 86400, query, title=title)


def test_the_earlier_editions_titles_are_read_newest_first():
    store = MemoryScriptCache()
    _store_edition(store, date(2026, 9, 22), "Airbnb and the Cereal Boxes")
    _store_edition(store, date(2026, 9, 23), "Why Slack Began as a Game")
    plan = plan_episode(_typed(), daily_edition.minutes())
    got = asyncio.run(daily_edition.with_earlier_editions(plan, store))
    assert got.covered == ("Why Slack Began as a Game", "Airbnb and the Cereal Boxes")


def test_a_first_edition_or_an_ordinary_search_is_left_alone():
    store = MemoryScriptCache()
    plan = plan_episode(_typed(), daily_edition.minutes())
    assert asyncio.run(daily_edition.with_earlier_editions(plan, store)) is plan
    search = plan_episode("how do tides work", 3)
    assert asyncio.run(daily_edition.with_earlier_editions(search, store)) is search
    assert asyncio.run(daily_edition.with_earlier_editions(plan, None)) is plan


def test_a_broken_cache_never_stops_the_episode():
    class Broken:
        def title(self, _key):
            raise RuntimeError("disk gone")

    plan = plan_episode(_typed(), daily_edition.minutes())
    assert asyncio.run(daily_edition.with_earlier_editions(plan, Broken())) is plan


def test_covered_is_not_part_of_what_the_episode_is():
    plan = plan_episode(_typed(), 2)
    told = dataclasses.replace(plan, covered=("Yesterday's",))
    assert asyncio.run(key_for(plan)) == asyncio.run(key_for(told))


def test_the_edition_writes_with_the_earlier_titles(tmp_path, monkeypatch):
    on = dataclasses.replace(config.settings, daily_edition=True)
    monkeypatch.setattr(config, "settings", on)
    store = MemoryScriptCache()
    _store_edition(store, date(2026, 9, 23), "Why Slack Began as a Game")
    seen = {}

    class Writer:
        client = None

        async def understand(self, plan, notes):
            seen["understand"] = plan.covered
            return dataclasses.replace(plan, brief=SimpleNamespace(degraded=False))

        async def stream_sentences(self, plan, notes):
            seen["write"] = plan.covered
            notes.title = "How Stripe Did Things That Don't Scale"
            yield "One."

    result = asyncio.run(daily_edition.write_episode(
        _typed(), Writer(), store, daily_edition.minutes(), current_until=0))
    assert result["status"] in ("written", "volatile")
    assert seen == {"understand": ("Why Slack Began as a Game",),
                    "write": ("Why Slack Began as a Game",)}


# --- what EI and the writer read --------------------------------------------


def test_ei_is_told_a_kind_of_thing_is_not_a_publication():
    assert "kind* of thing" in ei.EI_SYSTEM
    assert "never the title of a blog" in ei.EI_SYSTEM


def test_ei_is_shown_the_earlier_editions():
    prompt = ei.build_ei_prompt(_typed(), 2, now="Thursday",
                                covered=("Why Slack Began as a Game",))
    assert '"Why Slack Began as a Game"' in prompt
    assert "Pick something different" in prompt
    assert "Earlier editions" not in ei.build_ei_prompt(_typed(), 2, now="Thursday")


def test_the_writer_is_shown_the_earlier_editions():
    plan = plan_episode(_typed(), 2)
    assert "Earlier editions" not in build_prompt(plan)
    told = build_prompt(dataclasses.replace(
        plan, covered=("Why Slack Began as a Game",)))
    assert '"Why Slack Began as a Game"' in told
    assert "Today's must be a different one" in told
