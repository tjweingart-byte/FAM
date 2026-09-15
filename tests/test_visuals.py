"""One episode, one drawing - and never on exploreFAM.

The orchestration, the store and the two exclusions. What each group is here
to stop:

* **The key.** Prefetching a drawing before a tap and looking one up on the tap
  are two pieces of code that must compute the same string. `pipeline.key_for`
  has a test that reads the pipeline's own source for exactly this reason; the
  same doctrine applies here, and the same failure - silent, total, every
  speculative drawing paid for and never read.
* **Eligibility.** exploreFAM replays finished episodes and is built on the
  promise that it cannot spend. A picture is a spend. The refusal lives in
  `visuals.eligible`, server-side, so it holds even if an interface asked.
* **Idempotency.** A tap, a re-tap, a retry, a duplicate event and a restart
  must produce one drawing between them.
* **Availability.** Every failure path has to leave an episode that plays.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import line_processor  # noqa: E402
import understanding  # noqa: E402
import visual_provider  # noqa: E402
import visuals  # noqa: E402
from config import settings  # noqa: E402


#: Every module that reads a setting on this path. `Settings` is frozen and
#: each module holds its own reference, so a change has to be applied to all of
#: them at once - the same idiom `tests/test_prefetch.py` uses, gathered here
#: because this feature spans four modules rather than one.
TUNED = ("visuals", "visual_provider", "visual_director")


def tune(monkeypatch, **changes):
    """Apply settings to every module on the drawing path."""
    import config
    import visual_director as director

    patched = dataclasses.replace(config.settings, **changes)
    for name in TUNED:
        monkeypatch.setattr(sys.modules[name], "settings", patched)
    del director
    return patched


@pytest.fixture
def synthetic(monkeypatch):
    """A provider that draws a real continuous line, locally and free.

    Selected explicitly, which is the point: `synthetic` is never a fallback in
    the product either. A test that wants a picture asks for one.
    """
    tune(monkeypatch, visual_image_provider="synthetic", visual_director=False,
         visual_understanding_wait_seconds=0.2, visual_source_pixels=640,
         visual_thumbnail_pixels=256)
    return visual_provider.build_provider()


# --------------------------------------------------------------------------
# The key
# --------------------------------------------------------------------------
def test_the_key_ignores_length_voice_and_model():
    """A three-minute episode and a ten-minute one about the same question are
    different scripts and the same subject, so they share a drawing. The same
    reasoning that keeps voice out of the script cache key."""
    first = visuals.key_for("what is happening with interest rates")
    assert first
    assert visuals.key_for("What is happening with interest rates?") == first
    assert visuals.key_for("what is happening with interest rates",
                           context="an earlier episode") != first


def test_the_style_version_is_in_the_key(monkeypatch):
    """A new house style retires every old drawing rather than leaving a feed
    that is half one style and half another."""
    import visual_style

    before = visuals.key_for("sample question about something")
    monkeypatch.setattr(visual_style, "STYLE",
                        dataclasses.replace(visual_style.STYLE, version=99))
    assert visuals.key_for("sample question about something") != before


def test_one_place_computes_the_key():
    """The doctrine, asserted rather than described.

    `visuals.key_for` is module-level and everything delegates to it. A second
    implementation is the failure this feature could not see: every warmed
    drawing paid for and never found, with the feed looking exactly as it did.
    """
    source = (open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "visuals.py")).read())
    assert source.count("def key_for(") == 1
    for module in ("app.py", "pipeline.py"):
        text = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), module)).read()
        assert "def key_for" not in text or module == "pipeline.py", module


# --------------------------------------------------------------------------
# exploreFAM
# --------------------------------------------------------------------------
def test_explore_never_gets_a_drawing():
    """The hard exclusion. Both ways of saying it, because a client could send
    either: the surface name and the replay-only flag."""
    question = "what did the fed decide about rates"
    assert visuals.eligible(question, surface="search") is True
    assert visuals.eligible(question, surface="explore") is False
    assert visuals.eligible(question, cached_only=True) is False
    assert visuals.eligible(question, surface="explore", cached_only=True) is False


def test_explore_is_not_in_the_eligible_surfaces():
    assert "explore" in visuals.EXCLUDED_SURFACES
    assert "explore" not in visuals.ELIGIBLE_SURFACES


def test_requesting_one_for_explore_starts_nothing(synthetic):
    async def run():
        return visuals.request("something other listeners heard",
                               surface="explore", cached_only=True)

    assert asyncio.run(run()) == ""
    assert visuals.store().counts() == {}


def test_an_attached_episode_is_never_drawn():
    """An episode with attachments is that listener's alone. `pipeline`
    refuses to cache it, so this refuses to draw it - a shared illustration is
    reachable by another listener by definition."""
    assert visuals.eligible("summarise this document", attachments=("x",)) is False


# --------------------------------------------------------------------------
# The job
# --------------------------------------------------------------------------
def drawn(query: str, **kwargs):
    """Run one drawing to completion and hand back the record."""
    async def run():
        visual_id = visuals.request(query, **kwargs)
        assert visual_id, "nothing was started"
        for _ in range(400):
            await asyncio.sleep(0.05)
            record = visuals.store().get(visual_id)
            if record and record.status in ("ready", "failed", "unconfigured"):
                return record
        raise AssertionError("the drawing never finished")

    return asyncio.run(run())


def test_a_drawing_runs_end_to_end(synthetic):
    record = drawn("how do undersea cables get repaired", surface="search")
    assert record.status == "ready", record.error
    assert record.d.startswith("M")
    assert record.provider == "synthetic"
    assert record.metrics["components"] == 1
    payload = record.public()
    assert payload["status"] == "ready"
    assert payload["d"] == record.d
    assert payload["vector_url"].endswith(".svg")
    assert payload["thumbnail_url"].endswith(".png")
    # Said out loud, everywhere. A placeholder that looks like the real thing
    # is the failure this project has lost the most time to.
    assert payload["placeholder"] is True


def test_the_assets_land_on_disk_beside_each_other(synthetic):
    record = drawn("what makes a good espresso shot", surface="search")
    folder = visuals.store().assets / record.id
    for name in ("source.png", "visual.svg", "thumbnail.png", "metadata.json"):
        assert (folder / name).exists(), f"{name} was not written"


def test_the_served_svg_is_one_safe_path(synthetic):
    record = drawn("why do leaves change colour", surface="search")
    document = visuals.svg(record.id)
    assert document.count("<path") == 1
    assert "<script" not in document
    verdict = __import__("visual_validator").validate(document)
    assert verdict.ok, verdict.reasons


def test_the_thumbnail_is_rebuilt_from_the_path_if_the_file_is_gone(synthetic):
    """The vector is the canonical asset and the PNG is a convenience, so a
    wiped asset directory costs milliseconds rather than a picture."""
    record = drawn("how does noise cancelling actually work", surface="search")
    (visuals.store().assets / record.id / "thumbnail.png").unlink()
    rebuilt = visuals.thumbnail(record.id)
    assert len(rebuilt) > 1000
    assert line_processor.decode_image(rebuilt).min() < 0.5


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------
def test_the_same_episode_is_drawn_once(synthetic):
    """A tap, a re-tap and a duplicate event between them produce one drawing."""
    query = "what is going on with shipping rates"

    async def run():
        first = visuals.request(query, surface="search")
        again = visuals.request(query, surface="myfam")
        third = visuals.request(query, surface="search")
        assert first == again == third
        for _ in range(400):
            await asyncio.sleep(0.05)
            record = visuals.store().get(first)
            if record and record.status in ("ready", "failed", "unconfigured"):
                return record
        raise AssertionError("never finished")

    record = asyncio.run(run())
    assert record.status == "ready"
    assert record.attempts == 1, "it was drawn more than once"
    assert visuals.store().counts() == {"ready": 1}


def test_a_finished_drawing_is_not_drawn_again(synthetic):
    query = "how do tides work"
    first = drawn(query, surface="search")

    async def run():
        return visuals.request(query, surface="myfam")

    assert asyncio.run(run()) == first.id
    assert visuals.store().get(first.id).attempts == 1


# --------------------------------------------------------------------------
# Failure leaves an episode that plays
# --------------------------------------------------------------------------
def test_no_provider_is_unconfigured_rather_than_failed(monkeypatch):
    """Nothing is broken; something has not been set up. The two want
    different sentences on a health page and different behaviour from a
    client - one is worth polling, the other is not."""
    tune(monkeypatch, visual_image_provider="none")
    record = drawn("what happened at the summit", surface="search")
    assert record.status == "unconfigured"
    assert "VISUAL_IMAGE_PROVIDER" in record.error
    assert record.public()["status"] == "unconfigured"
    assert "d" not in record.public()


def test_a_provider_that_keeps_failing_ends_as_failed(monkeypatch, synthetic):
    async def explode(*args, **kwargs):
        raise visual_provider.VisualProviderError("the image model said no")

    monkeypatch.setattr(visual_provider.SyntheticProvider, "generate", explode)
    record = drawn("a question the provider dislikes", surface="search")
    assert record.status == "failed"
    assert "said no" in record.error
    assert record.attempts == settings.visual_max_retries


def test_art_that_cannot_be_drawn_as_one_line_is_retried_then_failed(
        monkeypatch, synthetic):
    """The retry ladder exists for exactly this, and giving up is a state
    rather than an exception: the episode still plays."""
    import numpy as np

    def two_pieces(*args, **kwargs):
        size = 400
        image = np.zeros((size, size, 3), dtype=np.uint8)
        image[:, :] = line_processor._hex("#F8F4EA")
        image[40:44, 40:160] = line_processor._hex("#171820")
        image[300:304, 240:360] = line_processor._hex("#171820")
        return line_processor.encode_png(image)

    async def generate(self, brief, *, attempt=1, references=()):
        return visual_provider.GeneratedImage(
            data=two_pieces(), provider="synthetic", model="stub")

    monkeypatch.setattr(visual_provider.SyntheticProvider, "generate", generate)
    record = drawn("a picture in two pieces", surface="search")
    assert record.status == "failed"
    assert record.attempts == settings.visual_max_retries
    assert "one line" in record.error or "pieces" in record.error


def test_the_ladder_escalates_rather_than_repeating(monkeypatch, synthetic):
    """Attempt two insists on continuity; attempt three asks for something
    simpler. Changing the house style on a structural failure would be
    answering a question nobody asked."""
    import visual_style

    seen = []

    async def generate(self, brief, *, attempt=1, references=()):
        seen.append(visual_style.image_prompt(brief, attempt))
        raise visual_provider.VisualProviderError("no")

    monkeypatch.setattr(visual_provider.SyntheticProvider, "generate", generate)
    drawn("something", surface="search")
    assert len(seen) == 3
    assert visual_style.CONTINUITY_INSISTENCE not in seen[0]
    assert visual_style.CONTINUITY_INSISTENCE in seen[1]
    assert visual_style.SIMPLIFY_INSISTENCE in seen[2]


def test_the_feature_being_off_starts_nothing(monkeypatch):
    tune(monkeypatch, visuals=False)

    async def run():
        return visuals.request("anything at all", surface="search")

    assert asyncio.run(run()) == ""
    assert visuals.describe("anything at all")["status"] == "none"


# --------------------------------------------------------------------------
# Spending
# --------------------------------------------------------------------------
def test_the_daily_ceiling_stops_a_runaway(monkeypatch, synthetic):
    record = drawn("the first one", surface="search")
    record.cost_usd = 99.0
    visuals.store().put(record)
    tune(monkeypatch, visual_image_provider="synthetic",
         visual_daily_budget_usd=1.0)
    ready, why = visuals.within_budget()
    assert ready is False
    assert "budget is spent" in why

    async def run():
        return visuals.request("the second one", surface="search")

    assert asyncio.run(run()) == ""


def test_a_warm_stands_aside_for_a_listener(monkeypatch, synthetic):
    """Prefetch's rule, for the same reason: a speculative picture that delays
    a real episode has inverted the point of drawing it early."""
    tune(monkeypatch, visual_image_provider="synthetic",
         visual_quiet_seconds=600.0)
    visuals.note_live_generation()

    async def run():
        return visuals.warm([("a tile nobody has tapped", "", "#1 on myFAM")])

    assert asyncio.run(run()) == []
    assert visuals.store().counts() == {}


def test_a_warm_carries_its_reason(synthetic):
    """The only way to judge what is worth drawing ahead is to see which kinds
    of guess got looked at, so the reason survives to the record."""
    async def run():
        started = visuals.warm(
            [("how do heat pumps work", "", "#2 in trending on myFAM")],
            surface="myfam")
        assert started
        for _ in range(400):
            await asyncio.sleep(0.05)
            record = visuals.store().get(started[0])
            if record and record.status in ("ready", "failed", "unconfigured"):
                return record
        raise AssertionError("never finished")

    record = asyncio.run(run())
    assert record.surface == "myfam"
    assert record.reason == "#2 in trending on myFAM"


# --------------------------------------------------------------------------
# The fork
# --------------------------------------------------------------------------
def test_the_drawing_reads_the_episodes_own_understanding(monkeypatch, synthetic):
    """The whole design. The writer announces what it worked out and the
    director reads the same thing, rather than either waiting on the other or
    paying for a second model call."""
    tune(monkeypatch, visual_image_provider="synthetic",
         visual_understanding_wait_seconds=5.0, visual_director=True,
         visual_source_pixels=640, visual_thumbnail_pixels=256)
    seen = {}

    async def direct(query, *, context="", brief=None, evidence="", topic="",
                     surface="search", notes=None):
        seen["brief"] = brief
        seen["evidence"] = evidence
        import visual_director

        return visual_director.fallback_visual_brief(query, "stubbed")

    import visual_director

    monkeypatch.setattr(visual_director, "direct", direct)
    query = "what did the central bank do this week"

    async def run():
        visual_id = visuals.request(query, surface="search")
        await asyncio.sleep(0.4)
        understanding.publish_episode(query, "", brief={"subject": "the ECB"},
                                      evidence="Reuters, yesterday: ...")
        for _ in range(400):
            await asyncio.sleep(0.05)
            record = visuals.store().get(visual_id)
            if record and record.status in ("ready", "failed", "unconfigured"):
                return record
        raise AssertionError("never finished")

    record = asyncio.run(run())
    assert record.status == "ready"
    assert seen["brief"] == {"subject": "the ECB"}
    assert "Reuters" in seen["evidence"]


def test_waiting_for_an_understanding_that_never_comes_still_draws(synthetic):
    """A browse-surface warm runs when no episode is being written at all. The
    timeout is an ordinary answer, not a failure."""
    record = drawn("an evergreen question about bread", surface="myfam")
    assert record.status == "ready"


# --------------------------------------------------------------------------
# Regeneration
# --------------------------------------------------------------------------
def test_a_failed_regeneration_leaves_the_old_drawing_alone(monkeypatch, synthetic):
    """The whole point of the manual redo: it can never turn a good
    illustration into a blank square."""
    query = "how does a heat exchanger work"
    good = drawn(query, surface="search")
    assert good.status == "ready"

    async def explode(*args, **kwargs):
        raise visual_provider.VisualProviderError("the model is down")

    monkeypatch.setattr(visual_provider.SyntheticProvider, "generate", explode)
    result = asyncio.run(visuals.regenerate(query))
    assert result["ok"] is False
    # The record says failed, and the drawing it was holding is still there.
    assert visuals.store().get(good.id).d == good.d


def test_a_successful_regeneration_replaces_it(synthetic):
    query = "why is the sky blue"
    first = drawn(query, surface="search")
    result = asyncio.run(visuals.regenerate(query))
    assert result["ok"] is True
    assert visuals.store().get(first.id).status == "ready"


# --------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------
def test_every_stage_is_counted(synthetic):
    drawn("what is a carry trade", surface="search")
    events = visuals.report()["events"]
    for name in ("visual_job_queued", "visual_generation_started",
                 "visual_generation_completed", "visual_processing_started",
                 "visual_ready"):
        assert events[name] >= 1, f"{name} was never counted"


def test_latency_is_none_rather_than_zero_when_there_is_no_data():
    """Zero out of zero reads as instant or broken, and it is actually
    silence. The rule prefetch reports its hit rate under."""
    assert visuals.report()["latency"]["visual_total_latency_ms"] is None


def test_health_names_the_state_a_deploy_is_in(monkeypatch):
    tune(monkeypatch, visual_image_provider="none")
    report = visuals.report()
    assert report["enabled"] is True
    assert report["provider"]["configured"] is False
    assert "VISUAL_IMAGE_API_KEY" in report["provider"]["reason"] \
        or "VISUAL_IMAGE_PROVIDER" in report["provider"]["reason"]
    assert report["excluded"] == ["explore"]
