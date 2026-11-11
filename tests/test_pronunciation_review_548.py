"""The pronunciation list out to a reviewer and back as proposals, and the
four ways of handing the voice a stressed respelling (PROBLEMS.md §548)."""
from __future__ import annotations

import csv
import io
import pathlib

import pytest
from fastapi.testclient import TestClient

import app as appmod
import pronounce_review
import pronunciation
import voice_bank
import voice_check

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRACKER = (ROOT / "admin_ui" / "tracker.html").read_text()
ADMIN = {"X-Admin-Token": "secret"}


class _Cache:
    def __init__(self, episodes):
        self.episodes = episodes

    def recent(self, limit=40, **_):
        return [{"key": k, "title": t} for k, (t, _) in list(self.episodes.items())[:limit]]

    def get(self, key, current=True):
        return self.episodes[key][1]


@pytest.fixture
def lexicon(tmp_path):
    bank = voice_bank.VoiceBank(str(tmp_path / "voice_bank.db"))
    voice_bank.reset(bank)
    pronunciation.reset(pronunciation.Lexicon(bank))
    bank.learn_pronunciation("Sagapolutele", "sah-gah-poh-loo-TEH-leh", "writer")
    bank.learn_pronunciation("Wembanyama", "wem-ban-YAH-mah", "admin")
    yield bank
    pronunciation.reset(None)
    voice_bank.reset(None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "secret")
    return TestClient(appmod.app)


def _stored(bank):
    return {r["name"]: (r["say"], r["source"]) for r in bank.pronunciations()}


# --- out --------------------------------------------------------------------

def test_a_download_carries_every_name_with_a_sentence_it_was_heard_in(lexicon):
    cache = _Cache({"k1": ("Spurs win", ["The Spurs won.", "Wembanyama had 40 <<SAY: X = y>> points."])})
    rows = list(csv.DictReader(io.StringIO(pronounce_review.export_csv(lexicon, cache))))
    assert tuple(rows[0]) == pronounce_review.EXPORT_COLUMNS
    by = {r["name"]: r for r in rows}
    assert set(by) == {"Sagapolutele", "Wembanyama"}
    assert by["Wembanyama"]["context"] == "Wembanyama had 40  points."
    assert by["Wembanyama"]["episode"] == "Spurs win"
    assert by["Wembanyama"]["source"] == "admin"
    assert by["Sagapolutele"]["context"] == ""     # never met: still listed


def test_the_export_endpoint_is_admin_only_and_a_csv(client, lexicon, monkeypatch):
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", _Cache({}))
    assert client.get("/api/admin/pronunciations/export").status_code == 404
    r = client.get("/api/admin/pronunciations/export", headers=ADMIN)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "Sagapolutele" in r.text
    p = client.get("/api/admin/pronunciations/review-prompt", headers=ADMIN).json()
    assert p["prompt"] == pronounce_review.PROMPT and "name,say,confidence,reason" in p["prompt"]


# --- back -------------------------------------------------------------------

def test_an_upload_proposes_and_changes_nothing(client, lexicon):
    before = _stored(lexicon)
    reply = ("Here are my corrections:\n```csv\nname,say,confidence,reason\n"
             "Sagapolutele,sah-gah-POH-loo-teh-leh,high,NFL pronunciation guide\n"
             "Wembanyama,wem-ban-YAH-mah,high,already right\n"
             "Nikola Jokic,NEE-koh-lah YOH-kitch,medium,broadcast usage\n"
             "Bad Row,ipa /ˈbæd/,low,\n```\n")
    r = client.post("/api/admin/pronunciations/import", json={"csv": reply}, headers=ADMIN).json()
    assert _stored(lexicon) == before
    names = [p["name"] for p in r["proposals"]]
    assert names == ["Sagapolutele", "Nikola Jokic"]          # high first
    sag = r["proposals"][0]
    assert sag["current"] == "sah-gah-poh-loo-TEH-leh" and sag["current_source"] == "writer"
    assert sag["confidence"] == "high" and not sag["new"]
    assert r["proposals"][1]["new"]
    assert r["unchanged"] == 1
    assert [x["name"] for x in r["refused"]] == ["Bad Row"]


def test_a_file_edited_in_place_is_read_from_its_current_say_column(lexicon):
    text = pronounce_review.export_csv(lexicon).replace(
        "sah-gah-poh-loo-TEH-leh", "sahng-ah-poh-loo-TEH-leh")
    r = pronounce_review.read_upload(text, _stored(lexicon))
    assert [(p["name"], p["say"]) for p in r["proposals"]] == [
        ("Sagapolutele", "sahng-ah-poh-loo-TEH-leh")]
    assert r["unchanged"] == 1


def test_an_upload_without_the_columns_says_which_it_has(lexicon):
    with pytest.raises(ValueError, match="name column and a say column"):
        pronounce_review.read_upload("who,what\nA,b\n", {})
    with pytest.raises(ValueError, match="empty"):
        pronounce_review.read_upload("  ", {})


def test_a_word_count_mismatch_is_noted(lexicon):
    r = pronounce_review.read_upload("name,say\nNikola Jokic,NEE-koh-lah-YOH-kitch\n", {})
    assert "surname alone" in r["proposals"][0]["note"]


def test_accepting_is_saved_as_an_admins_and_wikipedia_never_replaces_it(client, lexicon):
    r = client.post("/api/admin/pronunciations",
                    json={"name": "Sagapolutele", "say": "sah-gah-POH-loo-teh-leh"}, headers=ADMIN)
    assert r.status_code == 200
    assert _stored(lexicon)["Sagapolutele"] == ("sah-gah-POH-loo-teh-leh", "admin")
    assert not lexicon.learn_pronunciation("Sagapolutele", "zzz", "wikipedia")


# --- stress -----------------------------------------------------------------

def test_four_ways_to_say_a_stressed_respelling():
    v = pronounce_review.stress_variants("JAIR-in sah-gah-poh-loo-TEH-leh")
    assert v == {"now": "Jair-in Sah-gah-poh-loo-teh-leh",
                 "joined": "Jairin Sahgahpohlootehleh",
                 "accent": "jáir-in sah-gah-poh-loo-téh-leh",
                 "spaced": "Jair in sah gah poh loo Teh leh"}
    assert v["now"] == pronunciation.voiced("JAIR-in sah-gah-poh-loo-TEH-leh")
    assert pronounce_review.has_stress("wem-ban-YAH-mah")
    assert not pronounce_review.has_stress("Wem-ban-yah-mah")


def test_hearing_four_ways_keeps_nothing(client, lexicon, monkeypatch):
    sent = []

    async def fake(text):
        sent.append(text)
        return {"pcm": b"\0\0" * 10, "rate": 24000, "heard": "wɛmbɑnjɑmɑ"}

    monkeypatch.setattr(voice_check, "say_and_hear", fake)
    before = (_stored(lexicon), lexicon.voice_checks())
    r = client.post("/api/admin/pronunciations/hear-variants",
                    json={"name": "Wembanyama"}, headers=ADMIN).json()
    assert [v["variant"] for v in r["variants"]] == ["now", "joined", "accent", "spaced"]
    assert all(v["audio"].startswith("data:audio/wav;base64,") and "wav" not in v
               for v in r["variants"])
    assert (_stored(lexicon), lexicon.voice_checks()) == before
    # A proposal is heard as proposed, not as on file.
    client.post("/api/admin/pronunciations/hear-variants",
                json={"name": "Wembanyama", "say": "WEM-ban-yah-mah"}, headers=ADMIN)
    assert sent[4] == "Wem-ban-yah-mah"
    bad = client.post("/api/admin/pronunciations/hear-variants",
                      json={"name": "Wembanyama", "say": "/ˈwɛm/"}, headers=ADMIN)
    assert bad.status_code == 400
    missing = client.post("/api/admin/pronunciations/hear-variants",
                          json={"name": "Nobody"}, headers=ADMIN)
    assert missing.status_code == 404


def test_the_admin_page_has_the_round_trip():
    for needle in ("reviewDownload", "/api/admin/pronunciations/export",
                   "/api/admin/pronunciations/review-prompt",
                   "/api/admin/pronunciations/import", "reviewAcceptHigh",
                   "data-rv-accept", "data-ways", "/api/admin/pronunciations/hear-variants"):
        assert needle in TRACKER, needle
    # Accepting goes through the admin's own save, one row at a time.
    assert 'api("/api/admin/pronunciations", { name: p.name, say: p.say })' in TRACKER


def test_the_accent_falls_on_a_vowel_not_a_leading_y():
    assert pronounce_review.stress_variants("wem-ban-YAH-mah")["accent"] == "wem-ban-yáh-mah"
    assert pronounce_review.stress_variants("MY-lz")["accent"] == "mý-lz"


def test_free_text_that_opens_like_a_formula_is_defused(lexicon):
    cache = _Cache({"k": ("=HYPERLINK(\"x\")", ["=cmd| Wembanyama scored."])})
    row = next(r for r in csv.DictReader(io.StringIO(
        pronounce_review.export_csv(lexicon, cache))) if r["name"] == "Wembanyama")
    assert row["context"].startswith("'=") and row["episode"].startswith("'=")
    assert row["current_say"] == "wem-ban-YAH-mah"


def test_prose_that_starts_with_name_is_not_taken_for_the_header():
    text = "Name, say and confidence follow.\nname,say,confidence\nWembanyama,WEM-ban-yah-mah,high\n"
    r = pronounce_review.read_upload(text, {})
    assert [p["name"] for p in r["proposals"]] == ["Wembanyama"]


def test_a_listeners_query_never_goes_out_only_the_writers_title(lexicon):
    class Untitled(_Cache):
        def recent(self, limit=40, **_):
            return [{"key": "k", "title": "", "query": "my own private words"}]
    cache = Untitled({"k": ("", ["Wembanyama scored."])})
    text = pronounce_review.export_csv(lexicon, cache)
    assert "my own private words" not in text and "Wembanyama scored." in text
