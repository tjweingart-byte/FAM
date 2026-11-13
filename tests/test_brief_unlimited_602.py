"""§602: the brief has no time limit, its seconds are accounted for, and
"the voice fell behind" is counted from the first audio, never the tap."""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import pipeline_errors  # noqa: E402
from pipeline import GenerationStats  # noqa: E402
from tests.test_ei_split_268 import _with, model  # noqa: E402,F401


# --------------------------------------------------------------------------
# the brief is waited for
# --------------------------------------------------------------------------
def test_no_limit_by_default_on_either_path():
    fresh = config.Settings()
    assert fresh.ei_timeout_seconds == 0 and fresh.ei_ahead_timeout_seconds == 0
    assert ei.brief_ceiling(False) is None and ei.brief_ceiling(True) is None


def test_a_slow_brief_is_waited_for_not_degraded(model, monkeypatch):
    """What took 8 s and became a plainer episode is now simply waited on."""
    _with(monkeypatch, ei_timeout_seconds=0.0)
    model["delay"] = {"understand": 0.3, "search": 0.3}
    brief = asyncio.run(ei.understand("did the eagles win", 2))
    assert not brief.degraded


def test_a_ceiling_set_by_hand_still_works(model, monkeypatch):
    """`EI_TIMEOUT_SECONDS` above 0 puts §268's ceiling back."""
    _with(monkeypatch, ei_timeout_seconds=0.05)
    assert ei.brief_ceiling(False) == 0.05
    model["delay"] = {"understand": 0.3, "search": 0.3}
    brief = asyncio.run(ei.understand("did the eagles win", 2))
    assert brief.degraded and "0.05" in brief.notes[0]


def test_the_outline_ahead_is_waited_for_with_no_ceiling(model, monkeypatch):
    _with(monkeypatch, ei_ahead_timeout_seconds=0.0)
    model["delay"] = {"outline": 0.2}
    brief = asyncio.run(ei.understand("did the eagles win", 2, ahead=True))
    assert not brief.degraded
    assert not any("outline did not answer" in n for n in brief.notes)


def test_c3p0_is_given_the_clients_own_limit_when_the_brief_has_none():
    from agents import roster

    seconds, _ = roster.get("contextualizer").ceiling("search")
    assert seconds == config.NO_BRIEF_LIMIT_SECONDS


# --------------------------------------------------------------------------
# where its seconds go
# --------------------------------------------------------------------------
def test_each_part_reports_tokens_speed_and_cache(model):
    ei._PART_USAGE.clear()
    asyncio.run(ei.understand("did the eagles win", 2))
    usage = ei.report()["parts_usage"]
    assert {"understand", "search"} <= set(usage)
    row = usage["understand"]
    assert row["n"] == 1
    assert set(row) >= {"output_tokens", "tokens_per_second", "cache_hits",
                        "cache_writes"}


def test_the_briefs_instructions_live_an_hour(monkeypatch):
    _with(monkeypatch, prompt_cache=True, ei_prompt_cache_ttl="1h",
          prompt_cache_ttl="5m")
    [block] = ei.ei_system()
    assert block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    _with(monkeypatch, prompt_cache=True, ei_prompt_cache_ttl="",
          prompt_cache_ttl="5m")
    [block] = ei.ei_system()
    assert "ttl" not in block["cache_control"], "empty follows PROMPT_CACHE_TTL"


# --------------------------------------------------------------------------
# the voice fell behind: from the first audio, never the tap
# --------------------------------------------------------------------------
def _recorded(monkeypatch) -> list:
    seen = []
    monkeypatch.setattr(pipeline_errors, "record",
                        lambda *a, **k: seen.append(a))
    return seen


def test_a_long_wait_before_the_first_word_is_not_silence(monkeypatch):
    """The screenshot's case: 18.5 s from the tap to a 5.8 s first chunk.
    The listener was on the loading screen, not in a gap."""
    seen = _recorded(monkeypatch)
    stats = GenerationStats()
    stats.started_at = time.perf_counter() - 18.5
    assert stats.note_headroom(5.8) == 5.8
    assert not stats.starved and not seen


def test_real_silence_after_playback_starts_is_still_recorded(monkeypatch):
    seen = _recorded(monkeypatch)
    stats = GenerationStats()
    stats.started_at = time.perf_counter() - 30.0
    stats.first_audio_at = 10.0          # 20 s of playing since
    assert stats.note_headroom(12.0) < 0
    assert stats.starved
    [(stage, kind, detail, *_)] = seen
    assert (stage, kind) == ("episode", "voice fell behind")
    assert "into playback" in detail


def test_ahead_of_playback_is_not_behind(monkeypatch):
    seen = _recorded(monkeypatch)
    stats = GenerationStats()
    stats.started_at = time.perf_counter() - 30.0
    stats.first_audio_at = 10.0
    assert stats.note_headroom(25.0) > 0 and not seen
