"""The sweeps' scores and prices kept in the history archive (§315).

At the owner's direction: the sports and markets sweeps already fetch the
day's games and the watchlist's prices, so what they saw is kept too, with
no request of its own:

* a game once it is **final** - never one under way or not started - as one
  fact per game, however many sweeps see it;
* a symbol's latest price, one fact per symbol per trading day, worded as
  the provider said it ("last traded at"), never "closed at";
* nothing new is embedded when nothing changed;
* a failure never costs the sweep.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import balldontlie_scores as bs  # noqa: E402
import history_archive as ha  # noqa: E402
import story_sources  # noqa: E402
from balldontlie import LEAGUES  # noqa: E402
from tests.test_history_archive_308 import archive, _stub_embed  # noqa: E402,F401


def _game(gid, state, home=24, away=17, when="2026-10-25T17:00:00Z"):
    return {"id": gid, "status_state": state, "datetime": when,
            "home_team": {"id": 1, "full_name": "Philadelphia Eagles"},
            "visitor_team": {"id": 2, "full_name": "New York Giants"},
            "home_team_score": home, "visitor_team_score": away}


@pytest.fixture
def queued(monkeypatch):
    """Run the archive's queued work at once, and count it."""
    calls = []

    def submit(fn, *args):
        calls.append(args)
        return fn(*args)

    monkeypatch.setattr(ha.EXECUTOR, "submit", submit)
    return calls


# -- sports ------------------------------------------------------------------
def test_only_a_final_game_is_kept_once(archive, queued):
    rows = [_game(1, "final"), _game(2, "in_progress"), _game(3, "scheduled")]
    bs._keep_finals(LEAGUES["nfl"], rows, {}, "BALLDONTLIE")
    assert archive.counts()["facts"] == 1
    found = ha.recall("eagles", window_days=1,
                      now=datetime(2026, 10, 27, tzinfo=timezone.utc))
    assert found[0].text.startswith(LEAGUES["nfl"].label + ", 2026-10-25: ")
    assert "beat" in found[0].text and found[0].published == "2026-10-25"
    # The next sweep sees the same final: nothing is queued again.
    before = len(queued)
    bs._keep_finals(LEAGUES["nfl"], rows, {}, "BALLDONTLIE")
    assert len(queued) == before and archive.counts()["facts"] == 1


def test_a_corrected_final_replaces_the_old_one(archive, queued):
    bs._keep_finals(LEAGUES["nfl"], [_game(1, "final", 24, 17)], {}, "BALLDONTLIE")
    bs._keep_finals(LEAGUES["nfl"], [_game(1, "final", 27, 17)], {}, "BALLDONTLIE")
    assert archive.counts()["facts"] == 1
    found = ha.recall("eagles", window_days=1,
                      now=datetime(2026, 10, 27, tzinfo=timezone.utc))
    assert "27" in found[0].text


def test_a_broken_archive_never_costs_the_sweep(monkeypatch):
    monkeypatch.setattr(ha, "keep_swept", lambda records: 1 / 0)
    bs._keep_finals(LEAGUES["nfl"], [_game(1, "final")], {}, "BALLDONTLIE")


# -- markets -----------------------------------------------------------------
def _quote(price, change, stamp):
    return {"c": price, "dp": change, "t": stamp}


def test_one_price_per_symbol_per_day_the_latest_kept(archive, queued):
    day = datetime(2026, 10, 26, 15, tzinfo=timezone.utc).timestamp()
    later = datetime(2026, 10, 26, 20, tzinfo=timezone.utc).timestamp()
    story_sources._keep_prices([("NVDA", _quote(180.0, 1.2, day))], True, "Tiingo")
    story_sources._keep_prices([("NVDA", _quote(182.43, 2.5, later))], False, "Tiingo")
    assert archive.counts()["facts"] == 1
    found = ha.recall("nvidia", window_days=1,
                      now=datetime(2026, 10, 28, tzinfo=timezone.utc))
    assert found and "182.43" in found[0].text and "180.00" not in found[0].text
    record = ha.swept_price("NVDA", "Nvidia", 182.43, 2.5,
                            datetime.fromtimestamp(later, timezone.utc), False, "Tiingo")
    text = record["lines"][0]
    assert text == ("Nvidia (NVDA) last traded at 182.43, up 2.5 percent on the "
                    "day, as of 20:00 UTC on 2026-10-26 with the US market closed.")
    assert "closed at" not in text


def test_a_new_trading_day_is_a_new_fact(archive, queued):
    monday = datetime(2026, 10, 26, 20, tzinfo=timezone.utc).timestamp()
    tuesday = datetime(2026, 10, 27, 20, tzinfo=timezone.utc).timestamp()
    story_sources._keep_prices([("SPY", _quote(600.0, 0.1, monday))], False, "Tiingo")
    story_sources._keep_prices([("SPY", _quote(601.0, 0.2, tuesday))], False, "Tiingo")
    assert archive.counts()["facts"] == 2


def test_an_unchanged_price_is_not_embedded_again(archive, queued):
    stamp = datetime(2026, 10, 26, 20, tzinfo=timezone.utc).timestamp()
    rows = [("SPY", _quote(600.0, 0.1, stamp))]
    story_sources._keep_prices(rows, False, "Tiingo")
    before = len(queued)
    story_sources._keep_prices(rows, False, "Tiingo")
    assert len(queued) == before


def test_a_quote_with_no_price_is_skipped(archive, queued):
    story_sources._keep_prices([("SPY", None), ("QQQ", {"c": 0})], None, "Tiingo")
    assert archive.counts()["facts"] == 0


def test_without_a_sentence_model_nothing_is_kept_and_it_is_counted(archive, queued,
                                                                    monkeypatch):
    monkeypatch.setattr(ha, "_embed", lambda text: (None, ""))
    bs._keep_finals(LEAGUES["nfl"], [_game(1, "final")], {}, "BALLDONTLIE")
    assert archive.counts()["facts"] == 0
    assert ha.STATS["swept_skipped"] == 1 and ha.STATS["errors"] == 0


def test_a_recalled_provider_fact_is_credited_by_name_never_its_key():
    """A live or swept fact has no web address: the sources panel names the
    provider, and its `live:` key never becomes a label, a link or a host."""
    import asyncio

    import provenance
    import research

    fact = ha.Fact(url="live:Tiingo:Nvidia (NVDA):2026-10-26T20:00:00+00:00",
                   text="Nvidia (NVDA) last traded at 182.43.", title="Nvidia",
                   host="Tiingo", grade="primary", published="2026-10-26",
                   kind="live")

    async def go():
        packet = research.Packet(
            context=("SOURCE 1\nTitle: Today\nPublished: 2026-10-28 (today)\n"
                     "Source type: wire service\nKey evidence:\nIt moved.\n"),
            sources=["apnews.com"], backend="parallel",
            findings=[ha.Finding(url="https://apnews.com/today")])
        future = asyncio.get_event_loop().create_future()
        future.set_result([fact])
        await research.finish_history_look(packet, future)
        return packet

    packet = asyncio.new_event_loop().run_until_complete(go())
    assert packet.archive == "added" and "182.43" in packet.context
    labels = [item.label for item in packet.provenance.items]
    assert "Tiingo" in labels
    assert not any("live:" in label for label in labels)
    assert not any("live:" in host for host in packet.sources)
    credit = next(i for i in packet.provenance.items if i.label == "Tiingo")
    assert credit.kind == provenance.LIVE and credit.url == ""
