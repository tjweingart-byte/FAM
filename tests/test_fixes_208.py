"""§208: the bugs the documentation pass found.

* account deletion left the listener's id on the scripts they wrote
  (`scripts.author`); it is cleared now, and the episodes stay;
* local news items were never deleted; they are pruned daily past
  `LOCAL_NEWS_KEEP_DAYS`, keeping each outlet's newest few;
* `Dockerfile.gpu` pinned 8 of 20 stores (tested in test_data_paths.py);
* `DEMO_MODE=0` in render.yaml was read by nothing;
* the web page ignored `X-FAM-Client-Status` and 426;
* CORS lacked PATCH and X-FAM-TZ; the share page sent no `X-FAM-Client`;
* a replayed episode's clock was timed from the search page's length.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cache as cache_mod  # noqa: E402
import local_news  # noqa: E402

INDEX = (ROOT / "static" / "index.html").read_text()


# --- authorship is cleared on account deletion -----------------------------

def test_anonymise_author_keeps_the_episodes_and_drops_the_name(tmp_path):
    store = cache_mod.SqliteScriptCache(str(tmp_path / "s.db"))
    store.put("a", ["One."], ttl=60, query="q", author="u-gone")
    store.put("b", ["Two."], ttl=60, query="r", author="u-stays")
    assert store.anonymise_author("u-gone") == 1
    assert store.get("a") == ["One."]
    authors = dict(store._conn().execute("SELECT key, author FROM scripts"))
    assert authors == {"a": "", "b": "u-stays"}
    assert store.anonymise_author("") == 0


def test_the_memory_backend_does_the_same():
    store = cache_mod.MemoryScriptCache()
    store.put("a", ["One."], ttl=60, query="q", author="u-gone")
    assert store.anonymise_author("u-gone") == 1
    assert store.get("a") == ["One."]
    assert store._authors == {"a": ""}


def test_erasing_a_listener_clears_their_authorship(monkeypatch, tmp_path):
    import app as appmod

    store = cache_mod.SqliteScriptCache(str(tmp_path / "s.db"))
    store.put("a", ["One."], ttl=60, query="q", author="u-gone")
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", store)
    removed = appmod.erase_listener("u-gone")
    assert removed["scripts_unattributed"] == 1
    assert store.get("a") == ["One."]
    assert dict(store._conn().execute("SELECT key, author FROM scripts")) == {"a": ""}


def test_the_demo_wipe_still_finds_the_seed_scripts(monkeypatch, tmp_path):
    """Erasing a listener now clears authorship, so the wipe must drop the
    seed's scripts before erasing the seed's listeners."""
    import demo_data

    store = cache_mod.SqliteScriptCache(str(tmp_path / "s.db"))
    seed = demo_data.SEED_USER_IDS[0]
    store.put("a", ["One."], ttl=60, query="q", author=seed)
    store.put("b", ["Two."], ttl=60, query="r", author="u-real")
    import app as appmod

    monkeypatch.setattr(appmod, "SCRIPT_CACHE", store)
    report = demo_data.wipe(cache=store, events=None,
                            erase_listener=appmod.erase_listener,
                            scope="seed", dry_run=False)
    assert report["scripts_removed"] == 1
    assert store.get("a") is None
    assert store.get("b") == ["Two."]


# --- local news is pruned ----------------------------------------------------

def test_old_items_are_pruned_and_each_outlet_keeps_its_newest(tmp_path):
    store = local_news.LocalNewsStore(str(tmp_path / "ln.db"))
    outlet = store.add_outlet("Gazette", "https://gazette.example/", town="Ross")
    now = time.time()
    old = now - 60 * 86400
    store.add_items(outlet, [local_news.Item(url=f"https://gazette.example/{i}",
                                             title=f"Old story number {i}",
                                             published=old + i)
                             for i in range(5)])
    store.add_items(outlet, [local_news.Item(url="https://gazette.example/new",
                                             title="A new story", published=now)])
    assert store.prune(now - 30 * 86400, keep_per_outlet=2) == 4
    assert store.counts()["items"] == 2
    kept = store.item_times(outlet)
    assert max(kept) == now


def test_the_prune_never_cuts_inside_the_evidence_window(monkeypatch, tmp_path):
    import dataclasses

    store = local_news.LocalNewsStore(str(tmp_path / "ln.db"))
    monkeypatch.setattr(local_news, "_STORE", [store])
    monkeypatch.setattr(local_news, "KEEP_PER_OUTLET", 0)
    monkeypatch.setattr(local_news.LocalNewsStore.prune, "__defaults__", (0,))
    monkeypatch.setattr(local_news, "settings", dataclasses.replace(
        local_news.settings, local_news_keep_days=3, local_news_window_days=14))
    outlet = store.add_outlet("Gazette", "https://gazette.example/", town="Ross")
    now = time.time()
    store.add_items(outlet, [local_news.Item(url="https://gazette.example/x",
                                             title="Ten days old",
                                             published=now - 10 * 86400)])
    local_news.prune_old(now)
    assert store.counts()["items"] == 1   # 10 days old: inside the 14-day window
    store.add_items(outlet, [local_news.Item(url="https://gazette.example/y",
                                             title="Twenty days old",
                                             published=now - 20 * 86400)])
    assert local_news.prune_old(now) == 1
    assert store.counts()["items"] == 1


# --- deployment and client wiring ------------------------------------------

def test_render_yaml_sets_no_setting_nothing_reads():
    services = yaml.safe_load((ROOT / "render.yaml").read_text())["services"]
    keys = {e["key"] for s in services for e in s.get("envVars", [])}
    assert "DEMO_MODE" not in keys


def test_the_web_page_listens_for_its_own_status():
    assert "X-FAM-Client-Status" in INDEX
    assert "res.status === 426" in INDEX
    assert "A newer version of FAM is available" in INDEX


def test_the_share_page_names_itself():
    listen = (ROOT / "static" / "listen.html").read_text()
    assert '"web/share"' in listen
    assert listen.index('"web/share"') < listen.index('/fam-audio.js')


def test_cors_allows_what_the_app_sends():
    source = (ROOT / "app.py").read_text()
    assert '"PATCH"' in source and '"X-FAM-TZ"' in source


def test_a_replay_is_timed_by_the_episode_not_the_search_setting():
    i = INDEX.index("function setPlayState")
    body = INDEX[i:i + 1200]
    assert "startProgress(FamAudio.duration()" in body
    assert "startProgress(selectedLengthMinutes * 60)" not in body
