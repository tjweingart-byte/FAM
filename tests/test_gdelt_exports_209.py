"""§209: GDELT is read from its export files, never its search API.

The DOC search API allows one request every five seconds per address, and
Render's address is shared: on 1/10 GDELT refused 384 of 384. The exports are
plain files published every fifteen minutes, so one background job downloads
each new one and everything else - the story sweep, the theme volumes, an
episode's fallback search - reads the copy on disk.

What these pin:
* the downloads are a constant: `lastupdate.txt` and new files only, however
  many listeners there are;
* no read path can reach the network;
* a copy that is empty or stale is an outage, never a quiet news day;
* the file shapes FAM reads (recorded rows, not a live download - the build
  container cannot reach GDELT).
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import io
import pathlib
import re
import time
import zipfile
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import config
import gdelt
import provider_usage

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def on(monkeypatch):
    patched = dataclasses.replace(config.settings, gdelt=True,
                                  gdelt_export_backfill_files=2,
                                  gdelt_export_keep_hours=24.0,
                                  gdelt_export_rows_per_file=1500)
    monkeypatch.setattr(gdelt, "settings", patched)
    return patched


def line(url, title, date, source="reuters.com", themes="", persons="",
         orgs="", locations=""):
    cols = [""] * 27
    cols[0], cols[1], cols[2], cols[3], cols[4] = "x", date, "1", source, url
    cols[7], cols[9], cols[11], cols[13] = themes, locations, persons, orgs
    cols[26] = f"<PAGE_TITLE>{title}</PAGE_TITLE>"
    return "\t".join(cols)


def zipped(lines) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("x.gkg.csv", "\n".join(lines) + "\n")
    return buffer.getvalue()


def stamp(minutes_ago: int = 0) -> datetime:
    now = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return now.replace(minute=now.minute - now.minute % 15, second=0,
                       microsecond=0)


def gdelt_date(at: datetime) -> str:
    return at.strftime("%Y%m%d%H%M%S")


FED = [
    ("https://reuters.com/1", "Fed cuts interest rates as Powell signals more", "reuters.com"),
    ("https://cnbc.com/2", "Powell: Fed cuts rates for the second time", "cnbc.com"),
    ("https://bbc.co.uk/3", "Federal Reserve cuts rates; Powell hints at December", "bbc.co.uk"),
    ("https://nikkei.jp/4", "Markets rally as Powell and the Fed cut rates", "nikkei.jp"),
    ("https://dw.de/5", "Fed rate cut: what Powell said", "dw.de"),
]


class Upstream:
    """GDELT's file server, recorded: what it was asked and what it holds."""

    def __init__(self, newest: datetime, files: dict, md5_ok: bool = True):
        self.asked: list = []
        self.newest = newest
        self.files = files
        self.md5_ok = md5_ok

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(str(request.url))
        path = request.url.path
        if path.endswith("lastupdate.txt"):
            name = gdelt.name_for(self.newest)
            blob = self.files.get(name, b"")
            digest = hashlib.md5(blob).hexdigest() if self.md5_ok else "0" * 32
            text = (f"1 aaaa http://data.gdeltproject.org/gdeltv2/{gdelt_date(self.newest)}.export.CSV.zip\n"
                    f"1 bbbb http://data.gdeltproject.org/gdeltv2/{gdelt_date(self.newest)}.mentions.CSV.zip\n"
                    f"{len(blob)} {digest} http://data.gdeltproject.org/gdeltv2/{name}\n")
            return httpx.Response(200, text=text)
        if path.endswith(".txt"):
            return httpx.Response(200, text="reuters.com\tUS\tUnited States\n"
                                            "cnbc.com\tUS\tUnited States\n")
        name = path.rsplit("/", 1)[-1]
        if name in self.files:
            return httpx.Response(200, content=self.files[name])
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def upstream_with_fed() -> Upstream:
    newest = stamp()
    rows = [line(u, t, gdelt_date(newest), source=s,
                 themes="ECON_INFLATION;TAX_FNCACT_PRESIDENT",
                 persons="jerome powell", orgs="federal reserve")
            for u, t, s in FED]
    earlier = newest - timedelta(minutes=15)
    files = {gdelt.name_for(newest): zipped(rows),
             gdelt.name_for(earlier): zipped([line(
                 "https://lemonde.fr/7", "Rail strike shuts Paris stations",
                 gdelt_date(earlier), source="lemonde.fr", themes="SPORTS")])}
    return Upstream(newest, files)


# --------------------------------------------------------------------------
# The downloads are a constant
# --------------------------------------------------------------------------
def test_a_sync_fetches_the_newest_file_and_what_it_missed(on):
    up = upstream_with_fed()

    async def go():
        async with up.client() as client:
            return await gdelt.sync(client=client)

    result = run(go())
    assert result["fetched"] == 2, result
    held = gdelt.store().counts()
    assert held == {"files": 2, "articles": 6}
    assert gdelt.STATE.failures_in_a_row == 0 and gdelt.STATE.last_ok


def test_a_second_sync_asks_only_for_lastupdate(on):
    """Fifteen minutes later with nothing new published, a sync is one small
    request - the cost per day is fixed by GDELT's clock, not FAM's traffic."""
    up = upstream_with_fed()

    async def go():
        async with up.client() as client:
            await gdelt.sync(client=client)
            up.asked.clear()
            return await gdelt.sync(client=client)

    result = run(go())
    assert result["fetched"] == 0
    assert [u for u in up.asked if not u.endswith("lastupdate.txt")] == [], up.asked


def test_a_file_gdelt_skipped_is_not_asked_for_again(on):
    up = upstream_with_fed()
    missing = gdelt.name_for(up.newest - timedelta(minutes=30))

    async def go():
        async with up.client() as client:
            await gdelt.sync(client=client)
            up.asked.clear()
            await gdelt.sync(client=client)

    run(go())
    assert not any(missing in u for u in up.asked)


def test_a_file_that_fails_its_checksum_is_not_kept(on):
    up = upstream_with_fed()
    up.md5_ok = False

    async def go():
        async with up.client() as client:
            return await gdelt.sync(client=client)

    result = run(go())
    assert "checksum" in result.get("error", "")
    assert not gdelt.store().has_file(gdelt.name_for(up.newest))
    assert gdelt.STATE.failures_in_a_row == 1
    assert "checksum" in gdelt.report()["last_error"]


def test_every_download_is_counted_for_the_admin_page(on):
    up = upstream_with_fed()

    async def go():
        async with up.client() as client:
            await gdelt.sync(client=client)

    run(go())
    provider_usage.flush()
    row = {r["provider"]: r for r in provider_usage.report()}["gdelt"]
    # The domain list, lastupdate.txt, the newest file and two behind it
    # (one of which GDELT never published).
    assert row["today"] == len(up.asked) == 5


def test_a_sync_with_gdelt_off_sends_nothing(monkeypatch):
    monkeypatch.setattr(gdelt, "settings",
                        dataclasses.replace(config.settings, gdelt=False))
    up = upstream_with_fed()

    async def go():
        async with up.client() as client:
            return await gdelt.sync(client=client)

    assert run(go()) == {"skipped": "GDELT=0"}
    assert up.asked == []


def test_old_articles_are_dropped(on):
    old = time.time() - 30 * 3600
    gdelt.store().add_file("old.gkg.csv.zip", old, [
        {"url": "https://a/1", "title": "t", "domain": "a", "country": "",
         "seen": old, "themes": "", "names": ""}], {"SPORTS": 3})
    assert gdelt.store().prune(24.0) == 1
    assert gdelt.store().counts() == {"files": 0, "articles": 0}


# --------------------------------------------------------------------------
# Reading never reaches GDELT
# --------------------------------------------------------------------------
@pytest.fixture
def seeded(on):
    up = upstream_with_fed()

    async def go():
        async with up.client() as client:
            await gdelt.sync(client=client)

    run(go())
    return up


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("a read path opened an HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", refuse)
    monkeypatch.setattr(gdelt.httpx, "AsyncClient", refuse)


def test_an_episode_s_search_reads_the_copy(seeded, no_network):
    results = run(gdelt.retrieve("why did the Fed cut rates, Powell", recency_days=3))
    assert results and all("Fed" in r.title or "Powell" in r.title for r in results)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", results[0].published_date)
    by_url = {r.url: r.country for r in results}
    assert by_url["https://reuters.com/1"] == "united states", "the domain list placed it"
    assert by_url["https://nikkei.jp/4"] == "japan", "the country code placed it"


def test_a_thousand_listeners_cost_gdelt_nothing(seeded, no_network):
    """The whole point: the fallback rung scales with listeners and GDELT's
    count does not move."""
    provider_usage.flush()
    before = {r["provider"]: r for r in provider_usage.report()}["gdelt"]["today"]

    async def go():
        return await asyncio.gather(*(gdelt.retrieve("Powell Fed rates")
                                      for _ in range(200)))

    assert all(run(go()))
    provider_usage.flush()
    after = {r["provider"]: r for r in provider_usage.report()}["gdelt"]["today"]
    assert after == before


def test_one_shared_word_is_a_namesake_not_a_match(seeded, no_network):
    assert run(gdelt.retrieve("Paris Hilton fashion week")) == []


def test_volumes_count_every_article_not_only_the_kept_ones(on, monkeypatch):
    monkeypatch.setattr(gdelt, "settings", dataclasses.replace(
        gdelt.settings, gdelt_export_rows_per_file=2))
    newest = stamp()
    rows = [line(f"https://s{i}.com/a", f"Story {i}", gdelt_date(newest),
                 themes="SPORTS") for i in range(5)]
    kept, counts = gdelt.parse_gkg_file(zipped(rows), keep=2)
    assert len(kept) == 2 and counts == {"SPORTS": 5}


def test_the_story_sweep_reads_the_copy(seeded, no_network):
    import story_sources

    rows = run(story_sources.GdeltSignals().collect(32))
    fed = rows[0]
    assert fed.coverage == 5, [r.subject for r in rows]
    assert "Powell" in fed.subject or "Fed" in fed.subject


def test_an_empty_copy_is_an_outage_not_a_quiet_day(on, no_network):
    import story_sources

    with pytest.raises(RuntimeError, match="empty or stale"):
        run(story_sources.GdeltSignals().collect(32))


def test_a_stale_copy_is_an_outage_too(on, no_network):
    old = time.time() - 3 * 3600
    gdelt.store().add_file("x.gkg.csv.zip", old, [], {"SPORTS": 9})
    with pytest.raises(gdelt.ExportStale):
        run(gdelt.volume_for("SPORTS"))


def test_the_story_source_waits_for_the_first_file(on):
    """After a boot the copy may be empty: reported as a skip, so the sweep
    asks again next tick instead of being stamped swept for two hours."""
    import story_sources

    source = story_sources.GdeltSignals()
    assert "first export file" in source.idle(time.time())
    gdelt.store().add_file("x.gkg.csv.zip", time.time(), [], {})
    assert source.idle(time.time()) == ""


# --------------------------------------------------------------------------
# The search API is gone
# --------------------------------------------------------------------------
def test_nothing_in_the_app_calls_gdelts_search_api():
    offenders = []
    for path in ROOT.glob("*.py"):
        if "api.gdeltproject.org" in path.read_text():
            offenders.append(path.name)
    assert not offenders, offenders


def test_the_pacer_breaker_and_proxy_are_gone():
    """They rationed the search API; with nothing calling it they would be
    controls with nothing behind them."""
    for name in ("PACER", "BREAKER", "_get", "proxy_state", "DOC_API"):
        assert not hasattr(gdelt, name), name
    for field_name in ("gdelt_request_gap_seconds", "gdelt_episode_wait_seconds",
                       "gdelt_breaker_failures", "gdelt_proxy_url"):
        assert not hasattr(config.settings, field_name), field_name


def test_lastupdate_is_read_for_the_gkg_line_only():
    text = ("1 a http://x/20261006141500.export.CSV.zip\n"
            "2 b http://x/20261006141500.mentions.CSV.zip\n"
            "3 c http://x/20261006141500.gkg.csv.zip\n")
    assert gdelt.parse_lastupdate(text) == ("http://x/20261006141500.gkg.csv.zip", "c")
    assert gdelt.parse_lastupdate("") == ("", "")


def test_a_title_s_entities_are_decoded():
    row = gdelt.parse_gkg_line(line("https://a.com/x", "Q&amp;A: what&#39;s next",
                                    "20261006141500"))
    assert row["title"] == "Q&A: what's next"


def test_health_reports_the_copy_without_a_url_to_a_credential(seeded):
    report = gdelt.report()
    assert report["source"] == "export files"
    assert report["files"] == 2 and report["articles"] == 6
    assert report["newest_age_seconds"] is not None
    assert not re.search(r"proxy", " ".join(report), re.I)
