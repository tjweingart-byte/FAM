"""§241: the per-request steps are plain ASGI, the app shell is compressed
once per version, and password hashing runs off the event loop.

The load test (§240) found one Python process on one core doing all the
work. These are the three changes that buy back the most of that core
without changing anything a listener sees.
"""
from __future__ import annotations

import asyncio
import gzip
import os
import sys

from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts as accounts_mod  # noqa: E402
import app as appmod  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _shell() -> bytes:
    with open(os.path.join(ROOT, "static", "index.html"), "rb") as fh:
        return fh.read()


def test_no_request_runs_through_base_http_middleware():
    """`@app.middleware("http")` runs every request in an extra task and
    copies every response chunk through a channel; at load that was most of
    the app's own cost per request. Pinned so it does not come back."""
    wrapped = [m for m in appmod.app.user_middleware
               if m.cls is BaseHTTPMiddleware]
    assert not wrapped, "a @app.middleware('http') was added back (§241)"


def test_the_shell_is_compressed_and_unchanged_when_unpacked():
    client = TestClient(appmod.app)
    resp = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in resp.headers.get("vary", "")
    assert int(resp.headers["content-length"]) < len(_shell()) / 2
    assert resp.content == _shell()  # the client unpacked it
    # The session step still ran on the page (it mints identity for "/").
    assert "set-cookie" in resp.headers


def test_a_client_that_did_not_ask_gets_it_plain():
    resp = TestClient(appmod.app).get("/", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in resp.headers
    assert resp.content == _shell()


def test_each_version_of_a_page_is_compressed_once(monkeypatch):
    calls = []
    real = gzip.compress

    def counting(data, *a, **kw):
        calls.append(len(data))
        return real(data, *a, **kw)

    monkeypatch.setattr(appmod.gzip, "compress", counting)
    client = TestClient(appmod.app)
    for _ in range(3):
        assert client.get("/fam-audio.js",
                          headers={"Accept-Encoding": "gzip"}).status_code == 200
    # At most once (zero if an earlier test already kept it).
    assert len(calls) <= 1


def test_the_api_and_pictures_are_never_compressed():
    """The API's one stream is audio, whose first byte must not wait for a
    compressor; pictures are compressed already."""
    client = TestClient(appmod.app)
    api = client.get("/api/categories", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in api.headers
    png = client.get("/icon-512.png", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in png.headers


def test_an_unchanged_page_is_still_a_304():
    client = TestClient(appmod.app)
    first = client.get("/", headers={"Accept-Encoding": "gzip"})
    again = client.get("/", headers={"Accept-Encoding": "gzip",
                                     "If-None-Match": first.headers["etag"]})
    assert again.status_code == 304


def test_the_version_prefix_and_client_status_still_apply():
    client = TestClient(appmod.app)
    assert client.get("/api/v1/auth/me").status_code == 200


def test_signing_up_and_in_hash_off_the_event_loop(monkeypatch, tmp_path):
    """scrypt is ~50 ms of CPU; on the loop that is 50 ms nobody else is
    served. In a worker thread there is no running loop - which is what this
    checks, at the moment of hashing."""
    on_loop = []
    real_hash, real_verify = accounts_mod.hash_password, accounts_mod.verify_password

    def _where():
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)

    def hash_password(*a, **kw):
        _where()
        return real_hash(*a, **kw)

    def verify_password(*a, **kw):
        _where()
        return real_verify(*a, **kw)

    monkeypatch.setattr(accounts_mod, "hash_password", hash_password)
    monkeypatch.setattr(accounts_mod, "verify_password", verify_password)
    monkeypatch.setattr(appmod, "ACCOUNTS",
                        accounts_mod.AccountStore(str(tmp_path / "accounts.db")))
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    client = TestClient(appmod.app)
    client.get("/api/auth/me")
    email, password = "thread@example.invalid", "a long enough password"
    assert client.post("/api/auth/signup", json={
        "email": email, "password": password, "accept_terms": True}).status_code == 200
    assert client.post("/api/auth/login", json={
        "email": email, "password": password}).status_code == 200
    assert on_loop and not any(on_loop), "scrypt ran on the event loop"


def test_holds_agrees_with_get_and_counts_nothing(tmp_path):
    from cache import MemoryScriptCache, SqliteScriptCache
    for cache in (MemoryScriptCache(), SqliteScriptCache(str(tmp_path / "s.db"))):
        cache.put("live", ["One."], 600, "a question", "", 2)
        cache.put("gone", ["One."], -1, "an old question", "", 2)
        def hits():
            stats = cache.stats()
            return stats.get("hits_served", stats.get("hits", 0))

        before, found = hits(), 0
        for key in ("live", "gone", "never"):
            for current in (True, False):
                held = cache.holds(key, current)
                got = cache.get(key, current) is not None
                assert held == got, (type(cache), key, current)
                found += got
        for _ in range(5):
            cache.holds("live")
        # Only the `get`s counted; the probes did not.
        assert hits() - before == found, type(cache)


def test_a_limiter_looks_for_departed_clients_at_most_once_a_minute(monkeypatch):
    monkeypatch.setattr(appmod, "_last_pruned", {})
    store = {"client-%d" % i: 0.0 for i in range(600)}
    scans = []

    def stamp(v):
        scans.append(v)
        return v

    appmod._prune(store, 10_000.0, stamp)
    assert len(store) == 0, "idle clients were not forgotten"
    store.update({"client-%d" % i: 10_000.0 for i in range(600)})
    scans.clear()
    appmod._prune(store, 10_001.0, stamp)          # within the minute
    assert not scans
    appmod._prune(store, 10_000.0 + appmod.LIMITER_PRUNE_EVERY + 1, stamp)
    assert len(scans) == 600
