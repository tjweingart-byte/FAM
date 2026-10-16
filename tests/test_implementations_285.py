"""§285, the 10.16 implementations: the packet, the length, the examples,
and Made for you's View more (its own tiles first, written episodes only,
"all caught up"), and a tile's picture and label."""
from __future__ import annotations

import dataclasses
import os
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as appmod  # noqa: E402
import config  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import pipeline_errors  # noqa: E402
import research  # noqa: E402
import script_generator as sg  # noqa: E402
import thumbnails  # noqa: E402
import topics as T  # noqa: E402
from script_generator import build_prompt, plan_episode  # noqa: E402


# --------------------------------------------------------------------------
# 2. More from each source
# --------------------------------------------------------------------------
def test_six_sources_and_passages_of_up_to_a_thousand_characters():
    defaults = config.Settings()
    assert defaults.search_packet_sources == 6
    assert defaults.search_passage_chars == 1000
    assert defaults.parallel_chars_per_result >= (
        defaults.search_passage_chars * (defaults.search_highlights_per_source + 1))


def test_short_paragraphs_are_packed_into_one_passage():
    page = "\n\n".join(f"Paragraph {i} says something worth hearing here."
                       for i in range(12))
    cut = research.passages(page, 300)
    # Twelve short paragraphs no longer take twelve passage slots.
    assert len(cut) < 12
    assert all(len(p) <= 300 for p in cut)
    assert "Paragraph 0" in cut[0] and "Paragraph 1" in cut[0]
    # Nothing is lost in the packing.
    assert sum(p.count("Paragraph") for p in cut) == 12


def test_a_byline_scrap_is_still_dropped():
    assert research.passages(["By A. Writer\n\nThe vote passed on Tuesday "
                              "after a long night in the chamber."], 1000) == [
        "The vote passed on Tuesday after a long night in the chamber."]


# --------------------------------------------------------------------------
# 1 and 3. Length and the writing
# --------------------------------------------------------------------------
def test_two_minutes_no_longer_asks_for_two_or_three_facts():
    _name, mix = ei.depth_for(2)
    assert "two or three facts" not in mix
    assert "Nothing else fits" not in mix
    assert "mechanism" in mix


def test_the_prompt_keeps_the_ceiling_and_asks_for_the_time_to_be_used():
    prompt = build_prompt(plan_episode("a topic", 2))
    assert "not a quota" in prompt
    assert "resolving early is rare" in prompt
    assert "well under 300 words" in prompt
    assert not sg.SYSTEM_PROMPT.startswith("You write FAM: short")


def test_a_dropped_beat_gives_its_time_to_the_others():
    assert "its time" in ei.OUTLINE_GUIDE
    brief = ei.Brief(query="q", outline_beats=[{"does": "x", "needs": ["y"]}])
    assert "give a dropped part's time" in ei.build_outline_note(brief)


def test_every_example_says_its_minutes_and_its_words():
    block = sg.style_example_block()
    assert block.count("<example ") >= 3
    assert block.count(' minutes="') == block.count("<example ")
    assert ' minutes="4"' in block


@pytest.mark.parametrize("name", sorted(
    n for n in os.listdir(os.path.join(ROOT, "examples")) if n.endswith(".txt")))
def test_an_example_is_about_its_length(name):
    minutes = int(name.split("-", 1)[0])
    text = open(os.path.join(ROOT, "examples", name), encoding="utf-8").read()
    words = len(text.partition("\n\n")[2].split())
    # Within the writer's own valve (1.35x) and never under 0.9x.
    budget = minutes * config.Settings().target_wpm
    assert 0.9 * budget <= words <= 1.35 * budget, (name, words, budget)


def _reader(words: int, minutes: int = 2):
    plan = plan_episode("a question", minutes, search=False)
    reader = sg._ScriptReader(plan, sg.ScriptNotes())
    reader.emitted_words = words
    return reader


def _short_rows():
    pipeline_errors.flush()
    if not pipeline_errors.exists():
        return []
    return [r for r in pipeline_errors.store().since(0)
            if r["kind"] == "script well under its length"]


def test_a_script_well_under_its_length_is_a_notice():
    before = len(_short_rows())
    _reader(120)._note_if_short(0, cut_off=False)
    rows = _short_rows()
    assert len(rows) == before + 1
    assert all(r["severity"] == "notice" for r in rows)
    assert any("120 of about 300" in r["detail"] for r in rows)


def test_a_script_near_its_length_is_not():
    before = len(_short_rows())
    _reader(260)._note_if_short(0, cut_off=False)
    _reader(100)._note_if_short(0, cut_off=True)
    assert len(_short_rows()) == before


# --------------------------------------------------------------------------
# 5. Made for you's View more
# --------------------------------------------------------------------------
def _body(n):
    return {"topics": [{"id": f"t{i}", "cached": True} for i in range(n)],
            "ready": n}


def test_a_cycle_never_starts_again_it_is_caught_up():
    body = T.page_section(_body(5), {f"t{i}" for i in range(5)}, wrap=False)
    assert body["topics"] == [] and body["caught_up"] is True
    assert body["wrapped"] is False
    # The default is unchanged for every other rail.
    again = T.page_section(_body(5), {f"t{i}" for i in range(5)})
    assert again["wrapped"] is True and again["caught_up"] is False


def test_the_cycle_is_made_for_you_only():
    assert appmod._cycles_cached("from_history", "cached")
    assert not appmod._cycles_cached("from_history", "")
    assert not appmod._cycles_cached("most_played", "cached")


def test_a_guest_cycle_deals_written_episodes_then_is_caught_up(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    client = TestClient(appmod.app)
    url = "/api/myfam/section?key=from_history&page_size=8&cycle=cached"
    seen: list = []
    for _ in range(20):
        page = client.get(url + "&seen=" + ",".join(seen)).json()
        assert all(t["cached"] for t in page["topics"])
        assert not {t["id"] for t in page["topics"]} & set(seen)
        if page["caught_up"]:
            assert page["topics"] == []
            break
        seen += [t["id"] for t in page["topics"]]
    else:
        pytest.fail("never caught up")


def test_the_screen_opens_on_the_rails_own_tiles_and_ends_caught_up():
    html = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    assert "railTiles[sec.key] = sec.topics.slice();" in html
    assert 'var SECTION_CYCLES = { from_history: true };' in html
    assert '"&cycle=cached"' in html
    assert "You\\u2019re all caught up" in html
    assert "function restartSection()" in html


# --------------------------------------------------------------------------
# 5a. A tile's picture and its label
# --------------------------------------------------------------------------
@pytest.mark.parametrize("title", [
    "Shooting Reported at Oregon School", "Senator Steps Back Amid Probe",
    "Federal Probe Targets Ilhan Omar", "Attack Hits Yemen's Presidential Palace"])
def test_the_days_hard_news_has_a_facet(title):
    assert "world" in T.tags_for_text(title)


def test_a_hashed_picture_lends_no_label(monkeypatch):
    borrowed = {"node": "business", "facet": "business", "url": "/x.png",
                "fallback": True, "hashed": True}
    monkeypatch.setattr(thumbnails, "pick_for_player",
                        lambda *a, **k: dict(borrowed))
    assert thumbnails.pick_for_tile("tiger park reopens", ())["facet"] == ""
    # A tile with a facet of its own still prints it.
    assert thumbnails.pick_for_tile("x", ("world",))["facet"] == "world"


def test_a_tile_reads_its_title_and_angle_for_its_picture(monkeypatch):
    asked = {}

    def pick(text, tags=(), category="", key=""):
        asked.update(text=text, tags=tuple(tags), key=key)
        return None

    monkeypatch.setattr(thumbnails, "pick_for_tile", pick)
    tile = T.Topic(id="s1", title="Senator Steps Back Amid Probe",
                   subtitle="", query="lawmaker leave", tags=(), icon="",
                   angle="a lawmaker's leave of absence")
    tile._thumb()
    assert "Senator Steps Back" in asked["text"] and "leave of absence" in asked["text"]
    assert asked["key"] == "lawmaker leave"
    assert "world" in asked["tags"]
