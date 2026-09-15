"""What the picture is of, decided before anything is drawn.

The director is EI's sibling, and it inherits EI's two hard rules. Both are
tested here because both are invisible when they break:

* **It may never subtract availability.** No key, a timeout, a refusal,
  unreadable JSON - every one of them still produces a drawable brief. A
  director outage costs picture quality and never the picture.
* **It never asserts a fact.** It runs before anything has been retrieved, so a
  brief that described an event would be describing one nobody has checked.

And the style, which is the other half of the prompt: one artist, every
episode, not a parameter.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import visual_director as director  # noqa: E402
import visual_style  # noqa: E402
from config import settings  # noqa: E402


def direct(**kwargs):
    return asyncio.run(director.direct(**kwargs))


# --------------------------------------------------------------------------
# It never subtracts availability
# --------------------------------------------------------------------------
def test_with_no_key_it_still_produces_a_drawable_brief():
    """The floor. An episode still gets an illustration, and it is exactly as
    art-directed as it was before this module existed."""
    brief = direct(query="how do undersea cables get repaired")
    assert brief.degraded is True
    assert brief.usable, "the fallback produced nothing to draw"
    assert brief.notes, "it degraded without saying why"


def test_a_timeout_degrades_rather_than_raising(monkeypatch):
    async def hang(coro, *args, **kwargs):
        # Closed rather than dropped: an un-awaited coroutine is a warning in
        # the log of every later test, which is noise a real problem hides in.
        coro.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(director, "settings",
                        dataclasses.replace(settings, visual_director=True))
    monkeypatch.setattr(asyncio, "wait_for", hang)
    brief = direct(query="what is going on with shipping")
    assert brief.degraded is True
    assert "did not answer" in brief.notes[0]


def test_any_exception_at_all_degrades(monkeypatch):
    """`availability outranks diagnosis` - the broad except is deliberate and
    this is the test that keeps it honest."""
    monkeypatch.setattr(director, "settings",
                        dataclasses.replace(settings, visual_director=True))

    def explode(*args, **kwargs):
        raise RuntimeError("the network is on fire")

    monkeypatch.setattr(director, "build_async_client", explode)
    brief = direct(query="anything at all")
    assert brief.degraded is True
    assert "on fire" in brief.notes[0]


def test_it_being_switched_off_says_so(monkeypatch):
    monkeypatch.setattr(director, "settings",
                        dataclasses.replace(settings, visual_director=False))
    brief = direct(query="a perfectly ordinary question")
    assert brief.degraded
    assert any("VISUAL_DIRECTOR=0" in note for note in brief.notes)
    assert brief.usable, "switching the director off must not stop the drawing"


def test_an_empty_query_does_not_raise():
    brief = direct(query="")
    assert brief.degraded is True


def test_the_report_names_the_state_it_is_in(monkeypatch):
    assert director.report()["enabled"] is True, "the director ships on"
    monkeypatch.setattr(director, "settings",
                        dataclasses.replace(settings, visual_director=False))
    report = director.report()
    assert report["enabled"] is False
    assert "VISUAL_DIRECTOR=0" in report["note"]


# --------------------------------------------------------------------------
# The payload gate
# --------------------------------------------------------------------------
def test_an_unknown_complexity_becomes_medium():
    """It reaches `visual_style` as a dictionary key, and an unknown one would
    silently drop the whole complexity line out of the prompt."""
    brief = director.from_payload({"subject": "x", "complexity": "extreme"})
    assert brief.complexity == "medium"
    assert visual_style.COMPLEXITY_NOTE[brief.complexity]


def test_the_baseline_avoid_list_is_added_to_never_replaced():
    """The failure this stops: a model that returns its own `avoid` list and
    thereby stops "no text" being said at all."""
    brief = director.from_payload({"subject": "x", "avoid": ["crowds"]})
    for item in director.BASELINE_AVOID:
        assert item in brief.avoid
    assert "crowds" in brief.avoid


def test_a_missing_composition_still_composes():
    brief = director.from_payload({"subject": "x"})
    assert brief.composition
    assert brief.tone


# --------------------------------------------------------------------------
# What it is told, and what it is told not to do
# --------------------------------------------------------------------------
def test_the_prompt_carries_the_episodes_understanding():
    class Brief:
        intent = "update"
        subject = "the US Federal Reserve"
        why_now = "a rate decision this week"
        structure = "event_recap"

    prompt = director.build_director_prompt(
        "what did the fed do", brief=Brief(),
        evidence="Reuters, yesterday: the committee held rates.",
        topic="Rates and what they mean")
    assert "US Federal Reserve" in prompt
    assert "rate decision this week" in prompt
    assert "Reuters" in prompt
    assert "Rates and what they mean" in prompt


def test_the_evidence_is_capped_rather_than_pasted_whole():
    """The director is choosing a shape, not learning the story, and a whole
    packet in front of a small call is seconds bought for nothing."""
    prompt = director.build_director_prompt("q", evidence="x" * 9000)
    assert prompt.count("x") <= director.EVIDENCE_CHARS


def test_the_system_prompt_forbids_depicting_an_outcome():
    """It runs before anything is retrieved, so a picture that showed a result
    would be showing one nobody has checked."""
    assert "NEVER DEPICT AN EVENT OR AN OUTCOME" in director.DIRECTOR_SYSTEM
    assert "No text" in director.DIRECTOR_SYSTEM


# --------------------------------------------------------------------------
# One artist
# --------------------------------------------------------------------------
def test_the_style_is_a_constant_not_a_parameter():
    """FAM looks like one artist. The style block is identical for every
    episode; only the subject changes."""
    first = visual_style.image_prompt(director.from_payload({"subject": "a chip"}))
    second = visual_style.image_prompt(director.from_payload({"subject": "a wave"}))
    block = visual_style.style_block()
    assert block in first and block in second
    assert "a chip" in first and "a wave" in second


def test_the_ladder_escalates_structure_and_never_the_art_direction():
    """The retries exist for structural failures. Escalating the *style* on one
    would be answering a question nobody asked."""
    brief = director.from_payload({"subject": "a bridge"})
    first = visual_style.image_prompt(brief, 1)
    second = visual_style.image_prompt(brief, 2)
    third = visual_style.image_prompt(brief, 3)
    assert visual_style.style_block() in third
    assert visual_style.CONTINUITY_INSISTENCE not in first
    assert visual_style.CONTINUITY_INSISTENCE in second
    assert visual_style.CONNECTED_RICHNESS_INSISTENCE in third


def test_no_rung_of_the_ladder_ever_asks_for_a_simpler_picture():
    """Art first, engineering second. The third rung used to read "draw this as
    simply as it can possibly be drawn... leave out every detail that is not
    the idea itself" - a structural failure in FAM's vectoriser being answered
    by making the illustration worse, on exactly the subjects that had already
    failed twice. It is deleted rather than disabled."""
    assert not hasattr(visual_style, "SIMPLIFY_INSISTENCE")
    brief = director.from_payload({"subject": "a bridge"})
    for attempt in (1, 2, 3, 4):
        prompt = visual_style.image_prompt(brief, attempt)
        assert "as simply as" not in prompt.lower()
        assert "leave out every detail" not in prompt.lower()
    # And the rung that replaced it says so in as many words.
    assert "Do not simplify" in visual_style.CONNECTED_RICHNESS_INSISTENCE


def test_the_style_says_the_things_that_make_it_this_style():
    prompt = visual_style.image_prompt(director.from_payload({"subject": "x"}))
    for phrase in (visual_style.PAPER, visual_style.INK, "negative space",
                   "one extremely fine charcoal line"):
        assert phrase in prompt
    for banned in ("shading", "text", "cartoon"):
        assert banned in prompt


def test_references_are_absent_and_said_to_be_absent():
    """Empty is the normal state today. It is reported rather than silently
    nothing, because it is the strongest lever on how these look."""
    report = visual_style.report()
    if not report["references"]:
        assert "visual_references/" in report["reference_note"]


def test_a_reference_folder_is_read_when_it_exists(tmp_path, monkeypatch):
    """Named, not scavenged. The house style is `ACTIVE_REFERENCES`; a file in
    the folder that is not one of them is a reserve, and a stray `.txt` is not
    an illustration. See tests/test_visual_references.py for the whole of it."""
    monkeypatch.setattr(visual_style, "REFERENCE_DIR", tmp_path)
    (tmp_path / "notes.txt").write_text("not an image")
    (tmp_path / "one.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    assert visual_style.references() == [], \
        "a file nobody named was shown to the image model"

    for stem in visual_style.ACTIVE_REFERENCES:
        (tmp_path / f"{stem}.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    found = visual_style.references()
    assert [r.name for r in found] == \
        [f"{stem}.png" for stem in visual_style.ACTIVE_REFERENCES]
    assert found[0].media_type == "image/png"
    assert found[0].data_b64


# --------------------------------------------------------------------------
# Art first, engineering second
# --------------------------------------------------------------------------
# FAM is not one simple icon drawn with one line. FAM is one rich idea,
# interpreted as one beautiful editorial illustration, revealed through one
# continuous line. These pin the half of that which lives in the prompt.


def test_the_director_is_told_to_interpret_the_story_before_it_draws():
    """Five questions, in order, and only then a picture. A director that skips
    to the scene can only put the topic's nouns on a page - which is how an
    episode about two companies working together becomes a cloud, a head and a
    handshake."""
    for question in ("What happened?", "Why does it matter?", "What changed?",
                     "deeper human, business, cultural or technological idea",
                     "What scene or visual metaphor expresses that idea best?"):
        assert question in director.DIRECTOR_SYSTEM, question
    assert "Only then describe the illustration" in director.DIRECTOR_SYSTEM


def test_the_worked_example_is_in_the_prompt():
    """The bad answer and the good one, side by side. A rule describes; an
    example is matched - the same reasoning as `examples/` for the writing."""
    system = director.DIRECTOR_SYSTEM
    assert "BAD" in system and "GOOD" in system
    assert "handshake" in system
    assert "MEANS" in system


def test_the_interpretation_is_required_not_optional():
    """It is what the picture is really of, so a model that skipped it would be
    describing a scene it had not thought about."""
    for field in ("what_changed", "deeper_idea", "scene"):
        assert field in director.BRIEF_SCHEMA["properties"]
        assert field in director.BRIEF_SCHEMA["required"]


def test_the_reading_reaches_the_image_prompt_and_leads_it():
    """Before the objects, because it is what the objects are for."""
    brief = director.from_payload({
        "subject": "enterprise software", "what_changed": "they signed a deal",
        "deeper_idea": "software stops being a tool and becomes a colleague",
        "scene": "a working office where a person and an AI collaborate",
        "primary_form": "a desk", "visual_metaphor": "m",
        "composition": "c", "tone": "t", "complexity": "high"})
    prompt = visual_style.image_prompt(brief)
    assert "becomes a colleague" in prompt
    assert "a working office" in prompt
    assert prompt.index("becomes a colleague") < prompt.index("a desk"), \
        "the objects were named before what they are for"


def test_a_degraded_brief_still_asks_for_a_scene():
    """An outage in this module must cost picture *quality*, never the kind of
    picture. The old fallback said "the idea of X, drawn as one continuous
    line", which names one thing on an empty page - the shape of an icon."""
    brief = director.fallback_visual_brief("nvidia earnings", "no key")
    assert brief.degraded
    assert brief.usable
    assert "scene" in brief.scene
    prompt = visual_style.image_prompt(brief)
    assert "The scene:" in prompt


def test_the_style_says_a_scene_and_refuses_an_icon():
    block = visual_style.style_block()
    assert "CONTINUOUS LINE DOES NOT MEAN SIMPLE DRAWING" in block
    assert "WHAT THE PICTURE IS OF" in block
    assert "HOW MUCH IS IN IT" in block
    for banned in ("pictograms", "clip art", "generic corporate illustration",
                   "poster design", "handshake"):
        assert banned in block, banned
    assert "a SCENE, not a symbol" in block


def test_every_complexity_band_is_still_an_illustration():
    """`low` used to read "very few elements - one form, drawn large", which
    made the band the director happened to pick decide whether an episode got a
    picture or a pictogram."""
    for band in director.COMPLEXITIES:
        note = visual_style.COMPLEXITY_NOTE[band]
        assert "scene" in note, band


def test_the_baseline_avoid_keeps_the_new_bans_whatever_the_model_returns():
    brief = director.from_payload({"subject": "x", "avoid": ["crowds"]})
    for banned in ("pictograms", "clip art", "poster design", "colour",
                   "handshake or two-symbols-meeting imagery"):
        assert banned in brief.avoid, banned


def test_the_style_version_moved_with_the_style():
    """It is part of the visual key, so a bump retires every stored
    illustration - which is the point: a feed half icons and half illustrations
    is worse than either."""
    assert visual_style.STYLE.version >= 2
