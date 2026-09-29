"""Older clients keep working (§172, client_versions.py, release_contracts.py).

Every release in `releases/registry.json` that is not retired has a contract:
the `/api/` paths its code calls and the JSON shape of each GET. This file
replays all of them against the current code. A change that would break an app
already on somebody's phone fails here, naming the release and the key, on the
day it is made - not on the day a listener opens an old version.

The fix for a failure here is almost never to edit the contract. It is to keep
the old field or route alongside the new one, or to serve the new shape under
`/api/v2` (see `API_PREFIX` in app.py). Retiring the release is the last resort
and is a status change in the registry, not an edit to its contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as appmod  # noqa: E402
import client_versions  # noqa: E402
import release_contracts as rc  # noqa: E402

RELEASES = client_versions.releases()
LIVE = [r for r in RELEASES if r.status != "retired"]


def _contract(release) -> dict:
    return json.loads((ROOT / release.contract).read_text())


@pytest.fixture(scope="module")
def current_shapes():
    """The current server's answers, recorded exactly as the contracts were:
    a fresh process, empty databases, zero spend."""
    paths = sorted({p for r in LIVE for p in _contract(r)["shapes"]})
    if not paths:
        return {}
    return rc.record_in_subprocess(paths)["shapes"]


def test_the_registry_is_well_formed_and_every_release_has_its_contract():
    assert RELEASES, "releases/registry.json lists no releases"
    for release in RELEASES:
        assert release.contract, f"{release.label} has no contract"
        contract = _contract(release)
        assert (contract["platform"], contract["version"]) == (release.platform, release.version)
        assert contract["routes"], f"{release.label}'s contract names no routes"


@pytest.mark.parametrize("release", LIVE, ids=lambda r: r.label)
def test_every_route_an_installed_client_calls_still_routes(release):
    patterns = rc.route_patterns(appmod.app)
    gone = [path for path in _contract(release)["routes"] if not rc.routes(path, patterns)]
    assert not gone, (
        f"{release.label} ({release.status}) calls {gone}, which no longer route. "
        "Keep the old path (an alias to the new handler is enough), or retire the "
        "release in releases/registry.json if nobody is on it any more.")


@pytest.mark.parametrize("release", LIVE, ids=lambda r: r.label)
def test_every_response_an_installed_client_reads_keeps_its_shape(release, current_shapes):
    problems = []
    for path, recorded in _contract(release)["shapes"].items():
        if path not in current_shapes:
            problems.append(f"{path}: no longer answers a GET with JSON")
            continue
        problems += [f"{path} {p}" for p in rc.breaks(recorded, current_shapes[path])]
    assert not problems, (
        f"{release.label} ({release.status}) reads fields this server no longer "
        "sends:\n  " + "\n  ".join(problems) + "\nAdd fields rather than renaming "
        "or removing them; a new shape belongs under /api/v2.")


def test_the_live_web_client_calls_nothing_that_does_not_route():
    """The page this server ships today, checked against the same rules - a
    typo in a fetch path fails here before any release is cut from it."""
    patterns = rc.route_patterns(appmod.app)
    dead = [p for p in rc.endpoints_in(rc.client_sources("web")) if not rc.routes(p, patterns)]
    assert not dead, f"static/index.html calls {dead}, which route nowhere"


# --- Kept web releases are whole and servable -------------------------------


@pytest.mark.parametrize("release", [r for r in RELEASES if r.platform == "web"],
                         ids=lambda r: r.label)
def test_a_kept_web_release_is_intact(release):
    folder = ROOT / release.archive
    manifest = json.loads((folder / "manifest.json").read_text())
    for name, digest in manifest["files"].items():
        body = (folder / name).read_bytes()
        assert hashlib.sha256(body).hexdigest() == digest, (
            f"{release.archive}/{name} was edited after it shipped. A kept release "
            "is what users ran; cut a new release instead.")
    page = (folder / "index.html").read_text(encoding="utf-8")
    assert f'var FAM_CLIENT = "web/{release.version}";' in page
    assert f'src="/v/{release.version}/fam-audio.js"' in page
    assert 'serviceWorker.register("/sw.js")' not in page


@pytest.fixture
def client():
    return TestClient(appmod.app)


def test_a_kept_web_release_is_served_whole(client):
    web = [r for r in LIVE if r.platform == "web"]
    assert web, "no kept web release to serve"
    release = web[0]
    page = client.get(f"/v/{release.version}/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert f"web/{release.version}" in page.text
    script = client.get(f"/v/{release.version}/fam-audio.js")
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]
    # Only what the manifest lists: no walking out of the archive.
    assert client.get(f"/v/{release.version}/../../config.py").status_code == 404
    assert client.get(f"/v/{release.version}/manifest.json").status_code == 404
    assert client.get("/v/1999.01.01/").status_code == 404


# --- The promise, as the server keeps it -----------------------------------


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"releases": [
        {"platform": "ios", "version": "1.0.0", "status": "retired"},
        {"platform": "ios", "version": "1.1.0", "status": "deprecated"},
        {"platform": "ios", "version": "1.2.0", "status": "supported"},
    ]}))
    client_versions.reload(path)
    try:
        yield path
    finally:
        client_versions.reload()


def test_a_retired_client_is_told_to_update_and_does_no_work(registry, client):
    response = client.get("/api/myfam", headers={"X-FAM-Client": "ios/1.0.0+7"})
    assert response.status_code == 426
    body = response.json()
    assert "no longer supported" in body["error"] and body["client"]["update_required"]
    # The versioned path is the same promise.
    assert client.get("/api/v1/myfam", headers={"X-FAM-Client": "ios/1.0.0"}).status_code == 426
    # It can still find out why, and the monitor can still see the server.
    status = client.get("/api/client-status", headers={"X-FAM-Client": "ios/1.0.0"})
    assert status.status_code == 200 and status.json()["status"] == "retired"
    assert client.get("/api/health", headers={"X-FAM-Client": "ios/1.0.0"}).status_code == 200


def test_a_deprecated_client_is_served_and_nudged(registry, client):
    response = client.get("/api/myfam", headers={"X-FAM-Client": "ios/1.1.0"})
    assert response.status_code == 200
    assert response.headers["X-FAM-Client-Status"] == "deprecated"
    status = client.get("/api/client-status", headers={"X-FAM-Client": "ios/1.1.0"}).json()
    assert status["update_recommended"] and not status["update_required"]


@pytest.mark.parametrize("header", ["", "ios/9.9.9+1", "web/live", "not a version"])
def test_an_unknown_or_current_client_is_served_as_before(registry, client, header):
    """A TestFlight build, a simulator, the live page, or no header at all."""
    headers = {"X-FAM-Client": header} if header else {}
    assert client.get("/api/myfam", headers=headers).status_code == 200


def test_health_counts_who_is_still_calling(registry, client):
    client.get("/api/myfam", headers={"X-FAM-Client": "ios/1.2.0"})
    seen = client.get("/api/health").json()["clients"]["seen_since_boot"]
    assert seen.get("ios/1.2.0", 0) >= 1


def test_a_malformed_registry_is_refused(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"releases": [
        {"platform": "ios", "version": "1.0.0", "status": "retierd"}]}))
    with pytest.raises(ValueError, match="retierd"):
        client_versions.load(path)


def test_the_web_client_names_itself_on_every_api_call():
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert 'var FAM_CLIENT = "web/live";' in page
    assert 'headers.set("X-FAM-Client", FAM_CLIENT)' in page


def test_breaks_is_a_superset_check():
    recorded = {"a": "string", "b": [{"c": "number"}], "d": "null", "e": []}
    assert rc.breaks(recorded, {"a": "string", "b": [{"c": "number", "new": "string"}],
                                "d": "string", "e": [], "extra": "boolean"}) == []
    problems = rc.breaks(recorded, {"a": "number", "b": [{}], "e": {}})
    assert "a: was string, is now number" in problems
    assert "b[0].c: removed" in problems
    assert "d: removed" in problems
    assert any(p.startswith("e: was a list") for p in problems)
    assert os.path.exists(ROOT / "releases" / "registry.json")
