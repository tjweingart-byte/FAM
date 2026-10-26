"""The history archive (§308): intake, episode links, the recall beside the search.

The SQLite backend is the real code on a temporary file. The Atlas backend
is the real code against a fake database that records what it was asked
(no MongoDB in CI, and no fake that speaks `$vectorSearch`). The sentence
model is replaced by a stub embedder, so vectors are known exactly.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import history_archive as ha  # noqa: E402
import research  # noqa: E402

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def _settings(monkeypatch, **values):
    """Replace the frozen settings where the archive and research read them."""
    changed = dataclasses.replace(ha.settings, **values)
    monkeypatch.setattr(ha, "settings", changed)
    monkeypatch.setattr(research, "settings", changed)
    return changed


def _unit(*hot):
    v = [0.0] * ha.DIMS
    for i in hot:
        v[i] = 1.0
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v]


#: Words the stub embedder maps to a direction, so "eagles" text lands near
#: other "eagles" text and nowhere near "fed".
_DIRECTIONS = {"eagles": 1, "fed": 2, "rates": 2, "hurts": 1, "tesla": 3}


def _stub_embed(text):
    hot = sorted({d for w, d in _DIRECTIONS.items() if w in text.lower()}) or [0]
    return _unit(*hot), f"onnx:{ha.DIMS}"


@pytest.fixture
def archive(monkeypatch, tmp_path):
    ha.reset()
    _settings(monkeypatch, archive=True, archive_backend="sqlite", archive_recall=True)
    monkeypatch.setattr(ha, "_embed", _stub_embed)
    store = ha.SQLiteBackend(str(tmp_path / "archive.db"))
    ha.use(store)
    yield store
    ha.reset()


def _finding(url="https://www.reuters.com/a", passages=("One.", "Two."),
             published="2026-10-20", grade="primary"):
    return ha.Finding(url=url, title="A title", host="reuters.com", grade=grade,
                      published=published, passages=tuple(passages))


# --- what a packet hands the archive --------------------------------------

def test_findings_are_the_packet_s_own_articles_dated_and_graded():
    results = [
        research.SearchResult(url="https://www.reuters.com/x", title="Wire story",
                              published_date="2026-10-20",
                              highlights=["First.", "Second.", "Third."]),
        research.SearchResult(url="ftp://nowhere/x", title="Not a web page"),
        research.SearchResult(url="https://example.org/y", title="Third result"),
    ]
    found = ha.findings_from(results, packet_sources=2, highlights_per_source=2)
    # Only what entered the packet (two sources), never a non-web address.
    assert [f.url for f in found] == ["https://www.reuters.com/x"]
    f = found[0]
    assert f.published == "2026-10-20"
    assert f.passages == ("First.", "Second.")
    assert f.grade == research.credibility(results[0])
    assert f.host == "reuters.com"


def test_a_web_packet_carries_its_findings(monkeypatch):
    results = [research.SearchResult(url="https://apnews.com/z", title="AP",
                                     published_date="2026-10-22",
                                     highlights=["It happened."])]
    monkeypatch.setattr(research, "_search_parallel",
                        lambda *a, **k: (results, 0.001))
    packet = research._retrieve_blocking("q", 10, 6, 2, backend="parallel")
    assert [f.url for f in packet.findings] == ["https://apnews.com/z"]
    # Out of band: never in what the writer reads.
    assert "findings" not in packet.as_dict()


# --- intake and links (SQLite) --------------------------------------------

def test_intake_keeps_one_record_per_article_and_merges_what_it_learns(archive):
    archive.intake([_finding(passages=("One.", "Two."))], "Eagles", "nfl", 1.0)
    archive.intake([_finding(passages=("Two.", "Three."))], "Jalen Hurts", "nfl", 2.0)
    counts = archive.counts()
    assert counts["intake"] == 1 and counts["undigested"] == 1
    import json
    from contextlib import closing

    with closing(archive._connect()) as db:
        row = db.execute("SELECT passages, subjects, categories, seen, first_seen, "
                         "last_seen FROM intake").fetchone()
    assert json.loads(row[0]) == ["One.", "Two.", "Three."]
    assert json.loads(row[1]) == ["Eagles", "Jalen Hurts"]
    assert json.loads(row[2]) == ["nfl"]
    assert (row[3], row[4], row[5]) == (2, 1.0, 2.0)


def test_intake_past_its_cap_drops_the_oldest(archive):
    for i in range(5):
        archive.intake([_finding(url=f"https://example.com/{i}")], "", "", float(i))
    assert archive.prune_intake(3) == 2
    from contextlib import closing

    with closing(archive._connect()) as db:
        kept = [r[0] for r in db.execute("SELECT url FROM intake ORDER BY first_seen")]
    assert kept == [f"https://example.com/{i}" for i in (2, 3, 4)]


def test_an_episode_keeps_its_article_links(archive):
    archive.link_episode("key.100", "key", 100, [_finding().as_link()], 1.0)
    links = archive.episode_links("key.100")
    assert links == [{"url": "https://www.reuters.com/a", "title": "A title",
                      "host": "reuters.com", "grade": "primary",
                      "published": "2026-10-20"}]
    assert archive.episode_links("nope.1") == []


def _notes(findings, category="nfl"):
    return SimpleNamespace(archive={"subject": "Eagles", "findings": findings},
                           category=category)


def test_after_episode_writes_intake_and_the_link(archive, monkeypatch):
    submitted = []
    monkeypatch.setattr(ha.EXECUTOR, "submit",
                        lambda fn, *a: submitted.append(fn(*a)))
    ha.after_episode(_notes([_finding()]), "key", 100.0, "tap")
    assert submitted, "nothing was queued"
    assert archive.counts()["intake"] == 1
    assert archive.episode_links("key.100")[0]["url"] == "https://www.reuters.com/a"
    assert ha.STATS["intake_articles"] == 1 and ha.STATS["episodes_linked"] == 1


def test_a_private_search_never_reaches_the_archive(archive, monkeypatch):
    monkeypatch.setattr(ha.EXECUTOR, "submit",
                        lambda *a: pytest.fail("a private search was queued"))
    ha.after_episode(_notes([_finding()]), "key", 100.0, ha.PRIVATE_ORIGIN)
    assert archive.counts()["intake"] == 0


def test_an_episode_with_nothing_retrieved_queues_nothing(archive, monkeypatch):
    monkeypatch.setattr(ha.EXECUTOR, "submit",
                        lambda *a: pytest.fail("queued an empty episode"))
    ha.after_episode(SimpleNamespace(archive=None, category=""), "key", 1.0, "tap")


def test_a_failing_backend_backs_off_and_is_reported(archive, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(archive, "intake", boom)
    ha._write([_finding()], "", "", "key.1", "key", 1.0)
    assert ha.STATS["errors"] == 1 and "disk gone" in ha.STATS["last_error"]
    assert ha._backing_off()
    # Backing off: nothing is asked of the backend at all.
    monkeypatch.setattr(archive, "intake",
                        lambda *a: pytest.fail("asked while backing off"))
    ha._write([_finding()], "", "", "key.2", "key", 2.0)
    assert ha.recall("eagles", 7) == []


# --- which backend, and when it is off -------------------------------------

def test_atlas_without_an_address_is_off_and_says_so(monkeypatch):
    ha.reset()
    _settings(monkeypatch, archive=True, archive_backend="atlas")
    monkeypatch.setattr(ha, "_uri", lambda: "")
    ok, why = ha.diagnose()
    assert not ok and "MONGODB_URI" in why
    # Never a quiet fall back to the local file.
    assert ha.backend() is None
    assert ha.report()["enabled"] is False


def test_an_unknown_backend_is_refused_by_name(monkeypatch):
    _settings(monkeypatch, archive=True, archive_backend="postgres")
    ok, why = ha.diagnose()
    assert not ok and "postgres" in why


def test_archive_off_turns_everything_off(monkeypatch):
    _settings(monkeypatch, archive=False)
    assert ha.diagnose() == (False, "ARCHIVE=0")
    assert ha.look("anything", SimpleNamespace()) is None


# --- the recall ------------------------------------------------------------

def _fact(text, published, url=None, kind="sentence"):
    return ha.Fact(url=url or f"https://example.com/{abs(hash(text))}",
                   title="Earlier story", host="example.com", grade="established",
                   published=published, text=text, subject="", kind=kind)


def test_the_recall_finds_relevant_history_before_the_window(archive):
    ha.add_fact(_fact("The Eagles won the Super Bowl.", "2025-02-10"))
    ha.add_fact(_fact("The Fed held rates.", "2025-06-01"))
    # Inside the window: the search's job, never the archive's.
    ha.add_fact(_fact("The Eagles signed a kicker.", "2026-10-22"))
    found = ha.recall("how did the eagles do", window_days=7, now=NOW)
    assert [f.text for f in found] == ["The Eagles won the Super Bowl."]
    assert found[0].published == "2025-02-10" and found[0].score > 0.9


def test_a_fact_with_no_date_is_never_recalled(archive):
    ha.add_fact(_fact("The Eagles are a team.", ""))
    assert ha.recall("eagles", window_days=1, now=NOW) == []


def test_a_superseded_fact_is_never_recalled(archive):
    fid = ha.add_fact(_fact("The Eagles won.", "2025-02-10"))
    from contextlib import closing

    with closing(archive._connect()) as db, db:
        db.execute("UPDATE facts SET active = 0, superseded_by = 'x' WHERE id = ?",
                   (fid,))
    assert ha.recall("eagles", window_days=1, now=NOW) == []


def test_vectors_from_another_model_are_never_compared(archive):
    fact = _fact("The Eagles won.", "2025-02-10")
    archive.add_fact(fact, _unit(1), "onnx:999", time.time())
    assert ha.recall("eagles", window_days=1, now=NOW) == []


def test_without_a_sentence_model_there_is_no_recall(archive, monkeypatch):
    ha.add_fact(_fact("The Eagles won.", "2025-02-10"))
    monkeypatch.setattr(ha, "_embed", lambda text: (None, ""))
    assert ha.recall("eagles", window_days=1, now=NOW) == []
    with pytest.raises(RuntimeError):
        ha.add_fact(_fact("Another.", "2025-02-10"))


@pytest.mark.parametrize("brief, why", [
    (None, "no brief"),
    (SimpleNamespace(live_domain="weather"), "weather"),
    (SimpleNamespace(place="Haddonfield, NJ"), "town"),
])
def test_the_recall_stays_off_where_old_facts_would_mislead(archive, brief, why):
    assert why in ha.recall_wanted(brief)


def test_an_ordinary_brief_may_be_given_history(archive):
    assert ha.recall_wanted(SimpleNamespace(subject="The Eagles")) == ""


@pytest.mark.parametrize("brief", [
    SimpleNamespace(outcome_dependent=True),
    SimpleNamespace(live_domain="markets"),
    SimpleNamespace(live_domain="sports"),
])
def test_sports_markets_and_results_are_given_history(archive, brief):
    """§313, the owner's direction: their history is the most valuable the
    archive holds. The guards, not the recall, keep it from passing for now."""
    assert ha.recall_wanted(brief) == ""


# --- into the packet -------------------------------------------------------

def _web_packet():
    return research.Packet(
        context=("SOURCE 1\nTitle: Today\nPublished: 2026-10-23 (yesterday)\n"
                 "Source type: wire service\nKey evidence:\nIt happened.\n"),
        sources=["apnews.com"], backend="parallel",
        findings=[ha.Finding(url="https://apnews.com/today")])


def _done(value):
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    future.set_result(value)
    return future


def test_recalled_facts_join_the_packet_labelled_dated_and_without_a_host():
    async def go():
        packet = _web_packet()
        facts = [_fact("The Eagles won the Super Bowl.", "2025-02-10",
                       url="https://www.espn.com/old"),
                 _fact("Hurts was named MVP.", "2025-02-10",
                       url="https://www.espn.com/old"),
                 # Already in the packet: never repeated as history.
                 _fact("Duplicate.", "2025-01-01", url="https://apnews.com/today")]
        await research.finish_history_look(packet, _done(facts))
        return packet

    packet = asyncio.new_event_loop().run_until_complete(go())
    assert packet.archive == "added"
    text = packet.context
    assert "SOURCE 2\nTitle: Earlier story\nPublished: 2025-02-10" in text
    assert research.TIER_LABELS["archive"] in text
    assert "The Eagles won the Super Bowl.\nHurts was named MVP." in text
    assert "Duplicate." not in text and "SOURCE 3" not in text
    # Never a hostname in what the writer reads; credited out of band.
    assert "espn" not in text
    assert "www.espn.com" in packet.sources
    assert "archive" in packet.provenance.retrievers


def test_no_history_beside_an_empty_packet():
    async def go():
        packet = research.Packet(backend="parallel")
        look = _done([_fact("The Eagles won.", "2025-02-10")])
        await research.finish_history_look(packet, look)
        return packet

    packet = asyncio.new_event_loop().run_until_complete(go())
    assert packet.archive == "no web sources" and not packet.context


def test_a_late_recall_is_dropped_not_waited_for(monkeypatch):
    _settings(monkeypatch, archive_grace_seconds=0.01)

    async def go():
        packet = _web_packet()
        look = asyncio.get_event_loop().create_future()   # never answers
        started = time.monotonic()
        await research.finish_history_look(packet, look)
        return packet, time.monotonic() - started

    packet, waited = asyncio.new_event_loop().run_until_complete(go())
    assert packet.archive == "late" and waited < 0.5
    assert research.TIER_LABELS["archive"] not in packet.context


def test_the_writer_is_told_what_earlier_reporting_is_for():
    import script_generator as sg

    source = open(sg.__file__).read()
    assert "earlier reporting from FAM's archive is" in source
    assert 'TIER_LABELS["archive"] in plan.evidence' in source


# --- the Atlas backend, against a fake database ----------------------------

class _Collection:
    def __init__(self):
        self.calls = []
        self.docs = {}
        self.results = []

    def update_one(self, query, update, upsert=False):
        self.calls.append(("update_one", query, update, upsert))

    def replace_one(self, query, doc, upsert=False):
        self.calls.append(("replace_one", query, doc, upsert))
        self.docs[query["_id"]] = doc

    def find_one(self, query):
        return self.docs.get(query["_id"])

    def create_index(self, *a, **k):
        self.calls.append(("create_index", a))

    def aggregate(self, pipeline):
        self.calls.append(("aggregate", pipeline))
        return iter(self.results)


class _Database(dict):
    def __missing__(self, name):
        self[name] = _Collection()
        return self[name]


def test_atlas_intake_is_one_upsert_per_article():
    store = ha.AtlasBackend(database=_Database())
    store.intake([_finding()], "Eagles", "nfl", NOW.timestamp())
    kind, query, update, upsert = [c for c in store.intake_c.calls
                                   if c[0] == "update_one"][0]
    assert upsert and query == {"_id": ha.url_id("https://www.reuters.com/a")}
    assert update["$setOnInsert"]["digested"] is False
    assert update["$addToSet"] == {"passages": {"$each": ["One.", "Two."]},
                                   "subjects": "Eagles", "categories": "nfl"}
    assert update["$inc"] == {"seen": 1}


def test_atlas_recall_asks_vector_search_with_its_filters_and_converts_the_score():
    store = ha.AtlasBackend(database=_Database())
    before = NOW - timedelta(days=7)
    store.facts_c.results = [
        {"_id": "a", "url": "https://x.com/1", "title": "T", "grade": "primary",
         "published": datetime(2025, 2, 10, tzinfo=timezone.utc),
         "text": "Close.", "score": 0.95},
        # (1 + cosine) / 2 = 0.6 -> cosine 0.2: below the floor.
        {"_id": "b", "url": "https://x.com/2", "text": "Far.", "score": 0.6},
    ]
    found = store.recall(_unit(1), "onnx:384", before, 6, 0.45, set())
    stage = store.facts_c.calls[-1][1][0]["$vectorSearch"]
    assert stage["index"] == ha.VECTOR_INDEX and stage["path"] == "embedding"
    assert stage["filter"] == {"active": True, "space": "onnx:384",
                               "published": {"$lt": before}}
    assert [f.text for f in found] == ["Close."]
    assert found[0].score == pytest.approx(0.9) and found[0].published == "2025-02-10"


def test_atlas_links_and_facts_round_trip_through_the_fake():
    store = ha.AtlasBackend(database=_Database())
    store.link_episode("k.1", "k", 1, [_finding().as_link()], NOW.timestamp())
    assert store.episode_links("k.1")[0]["host"] == "reuters.com"
    fid = store.add_fact(_fact("A fact.", "2025-02-10"), _unit(1), "onnx:384",
                         NOW.timestamp())
    doc = store.facts_c.docs[fid]
    assert doc["published"] == datetime(2025, 2, 10, tzinfo=timezone.utc)
    assert doc["active"] is True and len(doc["embedding"]) == ha.DIMS


def test_the_vector_index_is_sized_for_the_installed_model():
    vector = ha.VECTOR_INDEX_DEFINITION["fields"][0]
    assert vector["numDimensions"] == ha.DIMS == 384
    assert vector["similarity"] == "cosine"
    assert {f["path"] for f in ha.VECTOR_INDEX_DEFINITION["fields"]
            if f["type"] == "filter"} == {"active", "published", "space"}


# --- staging and health ----------------------------------------------------

def test_staging_never_reaches_atlas():
    import spend_guard

    assert "MONGODB_URI" in spend_guard.PAID_CREDENTIALS
    assert spend_guard.FORCED["ARCHIVE_BACKEND"] == "sqlite"


def test_health_reports_the_archive_without_sending_anything(monkeypatch):
    import app

    _settings(monkeypatch, archive=True, archive_backend="atlas")
    monkeypatch.setattr(ha, "_uri", lambda: "")
    report = app._archive_report()
    assert report["enabled"] is False and "MONGODB_URI" in report["reason"]
    assert report["backend"] == "atlas"


def test_end_to_end_a_kept_fact_reaches_the_next_packet(archive):
    """A fact in the archive, the real `look` in its own pool, the real merge."""
    ha.add_fact(_fact("The Eagles won Super Bowl LIX.", "2025-02-10",
                      url="https://www.espn.com/lix"))

    async def go():
        brief = SimpleNamespace(subject="The Eagles", recency_days=7)
        look = ha.look("The Eagles how did the season go", brief)
        assert look is not None
        packet = _web_packet()
        await research.finish_history_look(packet, look)
        return packet

    packet = asyncio.new_event_loop().run_until_complete(go())
    assert packet.archive == "added"
    assert "The Eagles won Super Bowl LIX." in packet.context
    assert ha.STATS["recalls"] == 1 and ha.STATS["recalled_facts"] == 1
