"""The 10.16 implementations: six changes to the admin page (PROBLEMS.md §289).

1. Pipeline errors: every kind opens on what it means, the categories of
   episode it hit, its times of day and its newest cases; the page is tabbed
   and the menu stays open on a wide screen.
2. Reported content says what it is for and where listeners report from.
3. A feedback report's transcript is the episode that played, found by its id
   and read whether or not it is still current.
4. GDELT: a failing file is written off, the quick looks are capped, failing
   syncs back off, and the requests are counted by what they fetched.
5. Finnhub: a ticker named with its exchange is looked up; indices, futures
   and foreign listings are never chosen; a refused symbol is not asked again
   that day.
6. How names are said, checked against Wikipedia.
"""
from __future__ import annotations

import asyncio
import dataclasses
import pathlib
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import app as appmod
import config
import gdelt
import live_sources
import pipeline_errors
import pronunciation
import provider_usage
import voice_bank
import wiki_pronounce

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRACKER = (ROOT / "admin_ui" / "tracker.html").read_text()
INDEX = (ROOT / "static" / "index.html").read_text()
ADMIN = {"X-Admin-Token": "secret"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "secret")
    return TestClient(appmod.app)


@pytest.fixture
def errors(tmp_path):
    pipeline_errors.reset(pipeline_errors.ErrorStore(str(tmp_path / "errors.db")))
    yield
    pipeline_errors.reset(None)


# --- 1. an error kind, opened ----------------------------------------------

def test_counted_kinds_are_one_kind_with_an_explanation(errors):
    for n in (1, 2, 3):
        pipeline_errors.record(
            "writer", f"score check removed {n} sentence{'s' if n > 1 else ''}",
            "the Eagles won 31-17", "eagles score", pipeline_errors.NOTICE)
    pipeline_errors.record("live", "BALLDONTLIE scores: provider_failed",
                           "RateLimited: this minute's are spent", "nfl scores")
    rep = pipeline_errors.report()
    kinds = {k["kind"]: k for k in rep["kinds"]}
    assert "score check removed sentences" in kinds
    score = kinds["score check removed sentences"]
    assert score["week"] == 3 and len(score["examples"]) == 3
    assert {e["kind"] for e in score["examples"]} == {
        "score check removed 1 sentence", "score check removed 2 sentences",
        "score check removed 3 sentences"}
    assert score["explain"]["title"] == "The score check removed a sentence"
    assert len(score["times"]) == 3
    live = kinds["BALLDONTLIE scores: provider_failed"]
    assert live["explain"]["title"] == "A live data provider broke"
    assert "Rate limited" in live["explain"]["hint"]
    # Errors still sort first.
    assert rep["kinds"][0]["severity"] == "error"


def test_every_recorded_kind_has_written_words():
    """Each kind the code records has an explanation of its own, not the
    stage's fallback or the unknown one."""
    cases = [("brief", "timeout (search)"), ("brief", "search half timeout (search)"),
             ("brief", "out_of_room (ahead)"), ("brief", "refused (search)"),
             ("brief", "unreadable (search)"), ("live", "Finnhub: stale"),
             ("live", "Finnhub: timeout"), ("research", "no sources"),
             ("research", "parallel unavailable"), ("research", "parallel failed"),
             ("research", "evidence cut timeout"), ("writer", "opening check removed sentences"),
             ("writer", "figure check removed sentences"), ("writer", "ran out of room (batch)"),
             ("writer", "refused (batch)"), ("writer", "claude-haiku-5-5 failed"),
             ("edition", "Trending episode failed"), ("episode", "no voice"),
             ("episode", "failed before any audio"), ("episode", "no speech"),
             ("episode", "stream broke mid-flight"), ("episode", "voice fell behind")]
    titles = set()
    for stage, kind in cases:
        words = pipeline_errors.explain(stage, kind)
        assert words["what"] and words["heard"] and words["act"], (stage, kind)
        titles.add(words["title"])
    assert len(titles) >= 20


def test_a_kind_names_the_categories_of_its_questions(errors, monkeypatch):
    monkeypatch.setattr(pipeline_errors, "_category_of",
                        lambda q: "Sport · nfl" if "eagles" in q else "Money & markets")
    pipeline_errors.record("research", "no sources", "", "eagles injuries")
    pipeline_errors.record("research", "no sources", "", "eagles depth chart")
    pipeline_errors.record("research", "no sources", "", "oil prices")
    kind = pipeline_errors.report()["kinds"][0]
    assert kind["categories"] == [{"name": "Sport · nfl", "count": 2},
                                  {"name": "Money & markets", "count": 1}]


def test_the_category_reader_never_raises():
    assert isinstance(pipeline_errors._category_of("who won the eagles game"), str)
    assert pipeline_errors._category_of("") in ("Uncategorised", "")


def test_the_errors_page_is_tabbed_and_each_kind_opens():
    card = TRACKER.split('id="errorsCard"', 1)[1].split('data-page="services"', 1)[0]
    for tab in ("kinds", "newest", "steps", "refusals"):
        assert f'data-errtab="{tab}"' in card and f'data-errbox="{tab}"' in card
    assert 'id="pipelineErrors"' in card and 'id="pipelineNewest"' in card
    assert "function kindPanel(" in TRACKER and "function drawKinds(" in TRACKER
    panel = TRACKER.split("function kindPanel(", 1)[1].split("\n  }\n", 1)[0]
    for words in ("What happened:", "What the listener noticed:", "Likely causes:",
                  "What to do:", "Categories of episode", "Time of day", "Newest cases"):
        assert words in panel
    # Hours are bucketed in the reader's clock, from the times sent.
    assert "getHours()" in panel


def test_the_menu_stays_open_on_a_wide_screen():
    wide = TRACKER.split("@media (min-width:1180px) {", 1)[1].split("\n  }\n", 1)[0]
    assert "nav.drawer { transform:none; visibility:visible" in wide
    assert 'document.body.classList.toggle("signed-in"' in TRACKER


# --- 2. reported content ---------------------------------------------------

def test_reported_content_says_what_it_is_and_where_reports_come_from():
    card = TRACKER.split('id="modCard"', 1)[1].split("</section>", 1)[0]
    assert "App Store guideline 1.2" in card and "within 24 hours" in card
    where = card.split('id="modWhere"', 1)[1]
    for kind in ("Episode", "Comment", "Vibe", "Person", "Group", "Message"):
        assert f"<b>{kind}</b>" in where
    # Every kind the server takes has a row, and every place the app reports.
    import moderation
    assert {k.capitalize() for k in moderation.KINDS} <= {
        "Episode", "Comment", "Vibe", "Person", "Group", "Message"}
    for kind in ("episode", "vibe", "person", "group", "comment"):
        assert f'reportContent({{ kind: "{kind}"' in INDEX
    assert 'class="due' in TRACKER and "overdue by" in TRACKER


# --- 3. a report's transcript ----------------------------------------------

class _Empty:
    """A pipeline whose current-episode lookups find nothing: the episode
    went stale before the report, or its key is not the question's."""

    async def episode_meta(self, plan, current_only=False):
        return {"title": ""}

    async def sources_for(self, plan):
        return ""

    async def captions_for(self, plan):
        return [], False, True


class _Kept:
    def __init__(self, key):
        self.key = key
        self.asked = []

    def resolve_episode(self, episode):
        return self.key if episode.startswith(self.key) else ""

    def get(self, key, current=True):
        self.asked.append(current)
        if key != self.key:
            return None
        return None if current else ["The Eagles won.", "It was close."]

    def title(self, key):
        return "Eagles edge the Giants" if key == self.key else ""

    def sources(self, key):
        return ('{"items": [{"label": "espn.com", "title": "Recap", "kind": "article",'
                ' "url": "https://espn.com/x", "private": false}], "retrievers": ["parallel"]}')


def test_a_report_reads_the_episode_that_played_even_once_stale(client, monkeypatch):
    key = "a" * 64
    kept = _Kept(key)
    monkeypatch.setattr(appmod, "_make_pipeline", lambda *a, **k: _Empty())
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", kept)
    rid = client.post("/api/feedback", json={
        "text": "Wrong score", "screen": "player",
        "episode": {"q": "eagles giants", "minutes": 2, "id": key + ".1760000000"}
    }).json()["id"]
    report = [r for r in client.get("/api/admin/feedback", headers=ADMIN)
              .json()["reports"] if r["id"] == rid][0]
    ep = report["episode"]
    assert ep["transcript"] == ["The Eagles won.", "It was close."]
    assert ep["title"] == "Eagles edge the Giants"
    assert ep["sources"][0]["label"] == "espn.com"
    assert False in kept.asked, "a kept episode is read whether or not current"


def test_the_page_sends_the_episode_id_and_the_reel():
    body = INDEX.split("var PLAYER_SCREENS", 1)[1].split("function close()", 1)[0]
    assert "explore: 1" in body.split("\n", 1)[0]
    assert body.count("FamAudio.episode()") == 2
    assert 'audioOwner === "reel"' in body and "reelCurrent" in body


# --- 4. GDELT --------------------------------------------------------------

@pytest.fixture
def gdelt_on(monkeypatch):
    patched = dataclasses.replace(config.settings, gdelt=True,
                                  gdelt_export_backfill_files=2,
                                  gdelt_export_keep_hours=24.0,
                                  gdelt_export_retry_seconds=0.0,
                                  gdelt_export_file_tries=2,
                                  gdelt_export_max_waits=2)
    monkeypatch.setattr(gdelt, "settings", patched)
    return patched


def test_a_file_that_keeps_failing_is_written_off(gdelt_on):
    import httpx
    from tests.test_gdelt_exports_211 import upstream_with_fed

    up = upstream_with_fed()
    broken = gdelt.name_for(up.newest - timedelta(minutes=15))
    real = up.handler

    def handler(request):
        if request.url.path.endswith(broken):
            up.asked.append(str(request.url))
            return httpx.Response(500)
        return real(request)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            for _ in range(3):
                await gdelt.sync(client=c)

    asyncio.run(go())
    asked = [u for u in up.asked if u.endswith(broken)]
    # Two syncs, each with its one retry; the third sync does not ask.
    assert len(asked) == 4, asked
    assert gdelt.store().has_file(broken)


def test_a_timed_out_gkg_file_is_not_asked_twice_in_one_sync():
    import httpx

    assert not gdelt._worth_retrying("GKG file", httpx.ReadTimeout("slow"))
    assert gdelt._worth_retrying("lastupdate.txt", httpx.ReadTimeout("slow"))
    assert gdelt._worth_retrying("GKG file", httpx.ConnectError("refused"))


def test_failing_syncs_back_off(gdelt_on):
    now = 1_760_000_000.0
    base = gdelt.next_poll_in(now)
    assert gdelt.next_poll_in(now, failures=1) == base
    assert gdelt.next_poll_in(now, failures=2) == base + 900
    assert gdelt.next_poll_in(now, failures=3) == base + 3 * 900
    assert gdelt.next_poll_in(now, failures=9) == base + 7 * 900


def test_gdelt_requests_are_counted_by_what_they_fetched(gdelt_on):
    from tests.test_gdelt_exports_211 import upstream_with_fed

    up = upstream_with_fed()

    async def go():
        async with up.client() as client:
            await gdelt.sync(client=client)

    asyncio.run(go())
    rows = {r["provider"]: r for r in provider_usage.report()}
    parts = {b["league"]: b for b in rows["gdelt"]["breakdown"]}
    assert parts["lastupdate.txt"]["today"] == 1
    assert parts["GKG file"]["today"] == 2
    assert parts["GKG file"]["label"] == "GKG file"


# --- 5. Finnhub --------------------------------------------------------------

def test_a_ticker_named_with_its_exchange_is_read():
    assert live_sources.ticker_named("Oracle Corporation (NYSE: ORCL)") == "ORCL"
    assert live_sources.ticker_named("Apple (NASDAQ:AAPL)") == "AAPL"
    assert live_sources.ticker_named("Berkshire Hathaway (NYSE: BRK.B)") == "BRK.B"
    assert live_sources.ticker_named("Nasdaq Composite") == ""


def test_indices_futures_and_refused_symbols_are_never_chosen():
    assert live_sources.quotable({"symbol": "AAPL", "type": "Common Stock"})
    assert not live_sources.quotable({"symbol": "^IXIC", "type": "Index"})
    assert not live_sources.quotable({"symbol": "SB1!", "type": "Commodity Future"})
    assert not live_sources.quotable({"symbol": "BINANCE:BTCUSDT", "type": "Crypto"})
    live_sources.note_refused("ZZZT")
    try:
        assert not live_sources.quotable({"symbol": "ZZZT", "type": "Common Stock"})
    finally:
        live_sources._REFUSED.clear()


def test_a_named_ticker_is_resolved_only_on_an_exact_match(monkeypatch):
    async def fake(url, headers, params, timeout):
        return {"result": [{"symbol": "ORCL", "type": "Common Stock",
                            "description": "ORACLE CORP"},
                           {"symbol": "ORCL.MX", "type": "Common Stock"}]}

    monkeypatch.setattr(live_sources, "_json", fake)

    class Brief:
        live_entity = "Oracle Corporation (NYSE: ORCL)"
        subject = query = "what is oracle trading at"

    entity = asyncio.run(live_sources.FinnhubSource().resolve(Brief()))
    assert entity is not None and entity.id == "ORCL"


# --- 6. Wikipedia --------------------------------------------------------------

def test_ipa_becomes_the_lexicons_respelling():
    assert wiki_pronounce.ipa_to_respelling("ˌæbduːlkəˈriːm") == "ab-dool-kuh-REEM"
    assert wiki_pronounce.ipa_to_respelling("səˈɡɑːpoʊluːˈteɪleɪ") == "suh-GAH-poh-loo-TAY-lay"
    assert wiki_pronounce.ipa_to_respelling("ˈdʒɒkɪtʃ") == "JO-kich"


def test_an_articles_respelling_wins_over_its_ipa():
    lead = ("'''Kylian Mbappé''' ({{IPAc-en|əm|ˈ|b|æ|p|eɪ}}, "
            "{{respell|əm|BAP|ay}}; {{IPA|fr|kiljɑ̃ mbape}})")
    got = wiki_pronounce.from_wikitext(lead)
    assert got["say"] == "uhm-BAP-ay" and got["how"] == "Wikipedia's respelling"
    ipac = wiki_pronounce.from_wikitext("{{IPAc-en|ˈ|j|ɑː|n|ɪ|s}}")
    assert ipac["say"] == "YAH-nis" and ipac["how"] == "Wikipedia's English IPA"
    other = wiki_pronounce.from_wikitext("{{IPA|pl|ˈlɛxɔ}}")
    assert other["how"] == "Wikipedia's IPA (pl)"
    assert wiki_pronounce.from_wikitext("no pronunciation here") is None


@pytest.fixture
def lexicon(tmp_path):
    bank = voice_bank.VoiceBank(str(tmp_path / "voice_bank.db"))
    voice_bank.reset(bank)
    pronunciation.reset(pronunciation.Lexicon(bank))
    yield bank
    pronunciation.reset(None)
    voice_bank.reset(None)


def _wikipedia_says(monkeypatch, say):
    monkeypatch.setattr(wiki_pronounce, "lookup", lambda name: {
        "name": name, "status": wiki_pronounce.FOUND, "say": say, "ipa": "",
        "how": "Wikipedia's respelling", "title": name, "url": ""})


def test_wikipedia_replaces_a_models_guess(lexicon, monkeypatch):
    lexicon.learn_pronunciation("Mbappe", "em-bap-ee", "writer")
    _wikipedia_says(monkeypatch, "uhm-BAP-ay")
    result = wiki_pronounce.check("Mbappe")
    assert result.get("applied") and result["status"] == wiki_pronounce.AGREES
    row = lexicon.pronunciations()[0]
    assert row["say"] == "uhm-BAP-ay" and row["source"] == "wikipedia"
    assert lexicon.pronunciation_checks()[0]["name"] == "Mbappe"
    # A writer's later guess never replaces Wikipedia's.
    assert not lexicon.learn_pronunciation("Mbappe", "em-bap-ee", "writer")


def test_an_admins_respelling_is_only_shown_beside_wikipedias(lexicon, monkeypatch):
    pronunciation.lexicon().set("Mbappe", "em-BAP-ay")
    _wikipedia_says(monkeypatch, "uhm-BOP-oh")
    result = wiki_pronounce.check("Mbappe")
    assert result["status"] == wiki_pronounce.DIFFERS and not result.get("applied")
    row = lexicon.pronunciations()[0]
    assert row["source"] == "admin" and row["say"] == "em-BAP-ay"


def test_close_enough_agrees():
    assert wiki_pronounce.agrees("ab-DOOL-kah-reem", "ab-dool-kuh-REEM")
    assert not wiki_pronounce.agrees("JO-kich", "SMITH")


def test_nothing_is_checked_while_the_look_is_off(lexicon):
    assert not wiki_pronounce.enabled()
    assert wiki_pronounce.queue(["Mbappe"]) == 0


def test_the_admin_page_lists_wikipedias_reading(client, lexicon, monkeypatch):
    lexicon.learn_pronunciation("Mbappe", "em-BAP-ay", "admin")
    lexicon.keep_pronunciation_check({"name": "Mbappe", "status": "differs",
                                      "say": "uhm-BAP-ay", "how": "x"})
    got = client.get("/api/admin/pronunciations", headers=ADMIN).json()
    assert got["pronunciations"][0]["wikipedia"]["say"] == "uhm-BAP-ay"
    assert got["wikipedia"]["enabled"] is False
    refused = client.post("/api/admin/pronunciations/wikipedia", headers=ADMIN)
    body = refused.json()
    assert refused.status_code == 409
    assert "WIKIPEDIA=0" in (body.get("detail") or body.get("error") or "")
    card = TRACKER.split('id="sayCard"', 1)[1].split("</section>", 1)[0]
    assert 'id="wikiAll"' in card and 'id="sayWiki"' in card
    assert "data-say-use" in TRACKER


def test_a_model_learning_a_name_queues_a_check(lexicon, monkeypatch):
    queued = []
    monkeypatch.setattr(wiki_pronounce, "queue", lambda names, force=False: queued.extend(names))
    pronunciation.learn([("Sagapolutele", "sah-gah-poh-loo-TEH-leh")], "writer")
    assert queued == ["Sagapolutele"]
