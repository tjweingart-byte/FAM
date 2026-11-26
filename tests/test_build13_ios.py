"""iOS build 13 (PROBLEMS.md §700): the audit of Main since build 12 found one
thing the iPhone lacked - the follow step and following in the app (§696,
§697; pinned in `tests/test_follow_ios_700.py`) - and one small gap, What's
next coming up offline. The app names five new routes, so the contract is
recut.

The Swift is read, not compiled: `ios.yml` compiles it on a Mac."""

from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
IOS = ROOT / "ios" / "FAM" / "FAM"
FOLLOW_ROUTES = ("/api/follow", "/api/follow/tree", "/api/follow/preview",
                 "/api/follow/suggest", "/api/follow/mix")


def _swift(name: str) -> str:
    return (IOS / name).read_text()


def test_the_build_is_thirteen_and_the_registry_agrees():
    project = (ROOT / "ios" / "FAM" / "project.yml").read_text()
    build = re.search(r'CURRENT_PROJECT_VERSION: "(\d+)"', project).group(1)
    assert int(build) >= 13
    ios = [r for r in json.loads((ROOT / "releases" / "registry.json").read_text())["releases"]
           if r["platform"] == "ios"]
    assert ios[0]["build"] == build
    # The history of the earlier builds is kept in the notes, not replaced.
    assert "build 12" in ios[0]["notes"] and "build 13" in ios[0]["notes"]


def test_the_contract_was_recut_for_the_follow_routes():
    contract = json.loads((ROOT / "releases" / "contracts" / "ios-1.0.0.json").read_text())
    named = json.dumps(contract)
    for route in FOLLOW_ROUTES:
        assert f'"{route}"' in named, route
    # And the app really names each: a contract promises what the code calls.
    api = _swift("API.swift")
    for route in FOLLOW_ROUTES:
        assert '"api/v1' + route[len("/api"):] + '"' in api, route


def test_a_recut_only_added_to_what_build_twelve_was_promised():
    """`old-clients`: build 12 is on phones. Everything its contract named
    is still named."""
    contract = json.loads((ROOT / "releases" / "contracts" / "ios-1.0.0.json").read_text())
    named = json.dumps(contract)
    for route in ("/api/categories/episodes", "/api/myfam", "/api/next", "/api/audio",
                  "/api/saved", "/api/friends/follow", "/api/preferences"):
        assert f'"{route}"' in named, route


def test_whats_next_is_not_offered_offline():
    """With one tile enough to show it (§694), an offline phone's only tile
    was a Go Deeper that cannot run. The web's review fix, on the iPhone."""
    now = _swift("NowPlaying.swift")
    assert "var offline: () -> Bool = { false }" in now
    offer = now[now.index("private func offerNextUp("):][:300]
    assert "guard let ep = episode, !offline() else { return }" in offer
    assert "nowPlaying.offline = { [weak self] in self?.isOffline ?? false }" in _swift("AppModel.swift")


def test_the_parity_table_says_what_the_swift_does():
    parity = (ROOT / "ios" / "FAM" / "PARITY.md").read_text()
    row = [line for line in parity.splitlines()
           if line.startswith("| The follow step and following in the app")]
    assert len(row) == 1 and "**Not built**" not in row[0]
    # Inside the table, not after the last heading.
    assert parity.index(row[0]) < parity.index("## Build 7's second audit")
    assert "`/api/sources` at the first word, +2 s, +6 s, +20 s" in parity
