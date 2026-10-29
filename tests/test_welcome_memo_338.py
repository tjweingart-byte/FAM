"""The waitlist's samples never make a visitor wait for a re-ranking (§338)."""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


def _counting(monkeypatch, delay=0.0):
    calls = []

    def rank(stores):
        calls.append(time.monotonic())
        time.sleep(delay)
        episodes = [{"query": f"q{len(calls)}"}]
        appmod._WELCOME_MEMO.update(at=time.monotonic(), episodes=episodes,
                                    stores=stores)
        return episodes

    monkeypatch.setattr(appmod, "_rank_welcome", rank)
    monkeypatch.setattr(appmod, "_WELCOME_MEMO", {"at": 0.0, "episodes": []})
    monkeypatch.setattr(appmod, "_WELCOME_REFRESHING", threading.Event())
    return calls


def test_visitors_arriving_together_share_one_ranking(monkeypatch):
    calls = _counting(monkeypatch, delay=0.2)
    out = []
    threads = [threading.Thread(target=lambda: out.append(appmod._welcome_episodes()))
               for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 1
    assert all(o == [{"query": "q1"}] for o in out)


def test_a_stale_memo_answers_at_once_and_refreshes_behind(monkeypatch):
    calls = _counting(monkeypatch, delay=0.3)
    appmod._welcome_episodes()
    stores = appmod._WELCOME_MEMO["stores"]
    appmod._WELCOME_MEMO.update(at=time.monotonic() - appmod.WELCOME_MEMO_SECONDS - 1,
                                stores=stores)
    started = time.monotonic()
    assert appmod._welcome_episodes() == [{"query": "q1"}]
    assert appmod._welcome_episodes() == [{"query": "q1"}]
    assert time.monotonic() - started < 0.2      # nobody waited for the ranking
    deadline = time.monotonic() + 3
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.4)
    assert len(calls) == 2                        # one refresh, not two
    assert appmod._welcome_episodes() == [{"query": "q2"}]


def test_the_samples_are_ranked_off_the_event_loop(monkeypatch):
    import inspect
    source = inspect.getsource(appmod.welcome_samples)
    assert "asyncio.to_thread(_welcome_episodes)" in source
