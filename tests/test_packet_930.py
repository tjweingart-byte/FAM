"""The 9.30 implementations packet (PROBLEMS.md §178)."""

from __future__ import annotations

import json
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import feedback  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
ADMIN = {"X-Admin-Token": "secret"}
INDEX = (ROOT / "static" / "index.html").read_text()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "secret")
    return TestClient(appmod.app)


# --- 1. the logo on a shared episode's page --------------------------------

def test_the_landing_page_draws_the_mark_not_the_letters():
    page = (ROOT / "static" / "listen.html").read_text()
    brand = page.split('class="brand"', 1)[1].split("</button>", 1)[0]
    assert brand.count("<svg") == 3, "the F, the chevrons and the M"
    assert 'aria-label="FAM"' in brand
    # The same three paths as the app's own wordmark.
    for path in ('d="M6 20V9.2A3.2 3.2 0 0 1 9.2 6H16"', 'd="M5 13l7-7 7 7"',
                 'd="M6 20V6l6 9 6-9v14"'):
        assert path in brand and path in INDEX
    assert 'type="button">FAM</button>' not in page


# --- 2. What's next leads with Go Deeper's suggestion ---------------------

def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


def test_the_lead_tile_is_what_go_deeper_would_offer():
    body = _fn("buildNextUpTiles")
    # The `<<NEXT:>>` line, else the title-built follow-up Go Deeper uses.
    assert "episodeThread || nextUpFallback(baseKey)" in body
    assert "What's the bigger story behind" in _fn("nextUpFallback")
    assert _fn("nextUpFallback").split("return", 1)[1].strip() == \
        _fn("goDeeperFallback").split("return", 1)[1].strip()
    # And it plays the way Go Deeper does: a follow-up on this episode.
    assert "startFollowUp(tile.query, tile.baseKey, tile.minutes)" in _fn("playNextUp")
    assert "startFollowUp(val, baseKey, goDeeperMinutes)" in _fn("confirmGoDeeper")


def test_the_search_box_sits_above_back_and_stops_the_countdown():
    card = INDEX.split('id="nextUpOverlay"', 1)[1].split('id="sheetOverlay"', 1)[0]
    assert card.index('id="nextUpSearch"') < card.index('id="nextUpBack"')
    assert 'onfocus="pauseNextUp()"' in card
    pause = _fn("pauseNextUp")
    assert "stopNextUpTimer()" in pause
    # A typed search is a follow-up on what just played.
    assert "startFollowUp(val" in _fn("submitNextUpSearch")


# --- 4. interests as one line on a profile ---------------------------------

def test_both_profiles_draw_interests_as_one_scrolling_line():
    own = _fn("renderProfile")
    friend = _fn("renderFriendProfile")
    assert "interestLineHTML(" in own and "interestLineHTML(" in friend
    # Under Edit profile and Saved for Later on your own page.
    assert own.index("Saved for Later") < own.index("interestLineHTML(")
    line = _fn("interestLineHTML")
    assert ">Interests</span>" in line and "yf-chips-line" in line
    css = INDEX.split(".yf-chips.yf-chips-line{", 1)[1].split("}", 1)[0]
    assert "flex-wrap:nowrap" in css and "overflow-x:auto" in css


# --- 6. a report filed on the player keeps its episode --------------------

class _Pipeline:
    async def episode_meta(self, plan, current_only=False):
        return {"title": "The Colts in London", "thread": ""}

    async def sources_for(self, plan):
        return json.dumps({"items": [
            {"label": "nfl.com", "title": "Week 5 preview", "at": "2026-09-29",
             "tier": "Primary source", "kind": "article",
             "url": "https://www.nfl.com/news/x", "private": False}],
            "retrievers": ["exa"]})

    async def captions_for(self, plan):
        return (["Washington is one and two.", "They play Sunday."], False, True)


def test_a_report_from_the_player_keeps_title_sources_and_transcript(client, monkeypatch):
    seen = {}

    def make(*a, **k):
        seen["made"] = True
        return _Pipeline()
    monkeypatch.setattr(appmod, "_make_pipeline", make)
    rid = client.post("/api/feedback", json={
        "text": "Wrong record", "screen": "player",
        "episode": {"q": "colts commanders london", "minutes": 2,
                    "context": "", "title": "shown title"}}).json()["id"]
    report = [r for r in client.get("/api/admin/feedback", headers=ADMIN)
              .json()["reports"] if r["id"] == rid][0]
    ep = report["episode"]
    assert ep["title"] == "The Colts in London"
    assert ep["sources"][0]["label"] == "nfl.com"
    assert ep["sources"][0]["url"] == "https://www.nfl.com/news/x"
    assert ep["transcript"] == ["Washington is one and two.", "They play Sunday."]
    assert ep["query"] == "colts commanders london" and ep["minutes"] == 2


def test_a_report_off_the_player_has_no_episode(client, monkeypatch):
    monkeypatch.setattr(appmod, "_make_pipeline",
                        lambda *a, **k: pytest.fail("looked up an episode"))
    rid = client.post("/api/feedback", json={"text": "Tabs overlap",
                                             "screen": "myfam"}).json()["id"]
    report = [r for r in client.get("/api/admin/feedback", headers=ADMIN)
              .json()["reports"] if r["id"] == rid][0]
    assert report["episode"] is None


def test_a_report_is_kept_when_its_episode_cannot_be_read(client, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("no cache")
    monkeypatch.setattr(appmod, "_make_pipeline", broken)
    r = client.post("/api/feedback", json={
        "text": "Stalled", "episode": {"q": "fed rates", "minutes": 2, "title": "Fed"}})
    assert r.status_code == 200
    rid = r.json()["id"]
    report = [x for x in client.get("/api/admin/feedback", headers=ADMIN)
              .json()["reports"] if x["id"] == rid][0]
    assert report["episode"]["title"] == "Fed"
    assert report["episode"]["transcript"] == []


def test_an_old_inbox_gains_the_episode_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE reports (id TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL, screen TEXT NOT NULL DEFAULT '', build TEXT NOT NULL DEFAULT '',
        page TEXT NOT NULL DEFAULT '', viewport TEXT NOT NULL DEFAULT '',
        agent TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
        resolved_at REAL NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '')""")
    conn.execute("INSERT INTO reports (id, text, created) VALUES ('a', 'old', 1)")
    conn.commit()
    conn.close()
    store = feedback.FeedbackStore(str(path))
    assert store.get("a")["episode"] is None


def test_a_long_transcript_is_trimmed_and_says_so():
    snap = feedback.episode_snapshot("q", 10, "t", {}, ["x" * 1000] * 100)
    assert sum(len(s) for s in snap["transcript"]) <= feedback.MAX_EPISODE_TRANSCRIPT
    assert snap["transcript_cut"] is True


def test_the_inbox_has_an_episode_button():
    page = (ROOT / "admin_ui" / "tracker.html").read_text()
    assert "data-episode=" in page and "function episodeHTML" in page


# --- 3 and 5: sports numbers from the scoreboard, and the sources ----------

import asyncio  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import live_facts  # noqa: E402
import live_sources as LS  # noqa: E402
import research  # noqa: E402

NFL = LS.SPORTS["american-football"]
NOW = datetime.now(timezone.utc).replace(microsecond=0)
TEAMS = [
    {"id": 1, "name": "Washington Commanders", "city": "Washington"},
    {"id": 2, "name": "Indianapolis Colts", "city": "Indianapolis"},
    {"id": 3, "name": "Seattle Seahawks", "city": "Seattle"},
    {"id": 4, "name": "New York Giants", "city": "New York"},
    {"id": 5, "name": "New York Jets", "city": "New York"},
]


def _game(gid, home, away, when, short, hs=None, as_=None, stage="Regular Season",
          venue=None):
    names = {t["id"]: t["name"] for t in TEAMS}
    return {"game": {"id": gid, "stage": stage, "status": {"short": short},
                     "date": {"timestamp": int(when.timestamp())},
                     "venue": venue or {}},
            "teams": {"home": {"id": home, "name": names[home]},
                      "away": {"id": away, "name": names[away]}},
            "scores": {"home": {"total": hs}, "away": {"total": as_}}}


WASHINGTON = [
    _game(10, 1, 3, NOW - timedelta(days=40), "FT", 30, 3, stage="Pre Season"),
    _game(11, 4, 1, NOW - timedelta(days=17), "FT", 21, 17),
    _game(12, 1, 5, NOW - timedelta(days=10), "FT", 10, 24),
    _game(13, 3, 1, NOW - timedelta(days=3), "FT", 20, 27),
    _game(14, 2, 1, NOW + timedelta(days=4), "NS", 0, 0,
          venue={"name": "Tottenham Hotspur Stadium", "city": "London"}),
]


def test_a_record_is_counted_from_regular_season_finals():
    said = LS.season_facts(NFL, TEAMS[0], WASHINGTON, now=NOW)
    # The pre-season win is not in it: one win, two losses.
    assert said[0] == ("The Washington Commanders have won 1 and lost 2 in the "
                       "regular season so far.")
    assert "beat the Seattle Seahawks 27 to 20" in said[1]
    assert "has not been played yet" in said[2]
    assert "Indianapolis Colts" in said[2] and "London" in said[2]


def test_a_game_not_started_has_no_score_and_says_when():
    entity = live_facts.Entity(domain="sports", provider="API-Sports",
                               id="american-football:14", label="x")
    facts = LS.ApiSportsSource().to_facts(WASHINGTON[-1], entity, NFL)
    text = " ".join(facts.facts)
    assert facts.status == live_facts.SCHEDULED
    assert "level" not in text and "have not started yet" in text
    assert "Kick-off is" in text


def _fake_api(monkeypatch):
    calls = []

    async def fake(url, params, timeout):
        calls.append((url.rsplit("/", 1)[-1], dict(params)))
        if url.endswith("/teams"):
            return {"response": TEAMS}
        if params.get("live"):
            return {"response": []}
        if params.get("team") == "1":
            return {"response": WASHINGTON}
        if params.get("team") == "2":
            return {"response": [WASHINGTON[-1]]}
        if params.get("id") == "14":
            return {"response": [WASHINGTON[-1]]}
        return {"response": []}
    monkeypatch.setattr(LS, "api_sports_json", fake)
    monkeypatch.setattr(LS, "TEAMS", {})
    monkeypatch.setattr(LS, "SCHEDULES", {})
    monkeypatch.setattr(LS, "CARD", {})
    return calls


def test_a_game_later_this_week_is_found_on_the_teams_schedule(monkeypatch):
    _fake_api(monkeypatch)
    brief = SimpleNamespace(subject="Colts Commanders London game",
                            query="colts commanders london", named_slot="")
    entity = asyncio.run(LS.ApiSportsSource().resolve(brief))
    assert entity is not None and entity.id == "american-football:14"


def test_the_game_carries_both_teams_seasons(monkeypatch):
    _fake_api(monkeypatch)
    entity = live_facts.Entity(domain="sports", provider="API-Sports",
                               id="american-football:14", label="x")
    facts = asyncio.run(LS.ApiSportsSource().fetch(entity))
    text = " ".join(facts.facts)
    assert "Washington Commanders have won 1 and lost 2" in text
    assert "Indianapolis Colts have not finished a regular-season game" in text
    # Settled games may be stated even beside a game that has not started.
    assert "may be stated" in facts.as_prompt_block()


def test_a_city_two_teams_share_names_neither():
    assert LS.teams_named(TEAMS, "the new york game") == []
    assert [t["id"] for t in LS.teams_named(TEAMS, "giants at commanders")] == [4, 1]


def test_the_live_block_says_never_to_argue_the_sources():
    entity = live_facts.Entity(domain="sports", provider="API-Sports",
                               id="american-football:14", label="x")
    block = live_facts.LiveFacts(domain="sports", source="API-Sports",
                                 as_of=datetime.now(timezone.utc),
                                 facts=["x"], status=live_facts.FINAL,
                                 entity=entity).as_prompt_block()
    assert "Never tell the listener that sources disagree" in block
    assert "say so in passing" not in block


def _result(url, title="", highlights=()):
    return SimpleNamespace(url=url, title=title, highlights=list(highlights),
                           published_date="2026-09-29")


def test_a_sim_league_page_never_reaches_the_packet():
    fake = _result("https://2kolf.com/jayden-daniels-throws-3-tds",
                   "Jayden Daniels throws 3 TDs as Commanders top Colts 45-31")
    madden = _result("https://example.org/week-4",
                     "Week 4 recap", ["Our Madden franchise results are in"])
    real = _result("https://www.cbssports.com/nfl/gametracker/x", "Colts at Commanders")
    kept = research.screen_results([fake, madden, real], query="colts commanders")
    assert kept == [real]
    # Unless the question is about the game itself.
    assert madden in research.screen_results([madden], query="madden 27 franchise mode")


def test_a_sports_question_drops_unknown_outlets_once_two_known_answered():
    blog = _result("https://someblog.net/colts", "Colts win")
    known = [_result("https://www.cbssports.com/a"), _result("https://sports.yahoo.com/b")]
    assert research.screen_results(known + [blog], live_domain="sports") == known
    # One known outlet is not enough to throw the rest away.
    assert blog in research.screen_results(known[:1] + [blog], live_domain="sports")
    # And off sports, an unknown outlet stays, graded as unknown.
    assert blog in research.screen_results(known + [blog], live_domain="")


def test_the_writer_never_argues_a_fact_out_loud():
    import script_generator
    text = open(script_generator.__file__).read()
    assert "Disagreement \\\nis usually the most interesting part" not in text
    assert "say only the \\\nbetter-supported one - never the argument" in text
