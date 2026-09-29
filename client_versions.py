"""Every client release FAM has shipped, and the promise the server keeps to each.

An app on somebody's phone cannot be redeployed with the server. From the day
FAM is in the App Store there will always be older versions installed, and the
server has to keep working for them (STAGING.md, PROBLEMS.md §172). This module
is the record that makes that checkable rather than hoped:

* **`releases/registry.json`** lists every shipped client - web or iOS - with a
  status: `supported` (works, nothing said), `deprecated` (works, and every
  API response carries `X-FAM-Client-Status: deprecated` so the app can suggest
  an update), or `retired` (the API answers 426 with a sentence and the store
  link). Retiring is the last resort and is a line in that file.
* **A contract per release** (`releases/contracts/`), cut by
  `tools/cut_release.py`: every `/api/` path the client's code names, and the
  shape of every GET it can make. `tests/test_client_contracts.py` replays each
  contract of each release that is not retired against the current server, so
  a change that would break an installed app fails CI on the day it is made.
* **Web releases are kept whole and playable** (`releases/web/<version>/`) and
  served at `/v/<version>/`, against this same server. That is how an old
  version is actually *used* on staging - open it and tap through - rather
  than argued about. iOS binaries are not source and are not kept here; the
  registry records their build numbers and App Store Connect keeps the builds.

Clients say who they are with `X-FAM-Client: <platform>/<version>`
(`ios/1.0.0+12`, `web/2026.09.29`). A client that says nothing, or names a
version the registry has never heard of - a TestFlight build, a developer's
simulator - is served normally: refusing what we do not recognise would break
exactly the builds staging exists to test.

`web/live` is the page this server itself serves, which is always current and
never in the registry.
"""
from __future__ import annotations

import json
import re
import threading
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from paths import PROJECT_ROOT

HEADER = "X-FAM-Client"
STATUS_HEADER = "X-FAM-Client-Status"

REGISTRY_PATH = PROJECT_ROOT / "releases" / "registry.json"
RELEASES_DIR = PROJECT_ROOT / "releases"

STATUSES = ("supported", "deprecated", "retired")
PLATFORMS = ("web", "ios")

#: The web page this server serves is always the newest client.
LIVE = ("web", "live")

#: Paths a retired client may still reach: enough to be told it is retired and
#: to show the update screen, and nothing that does work.
RETIRED_MAY_REACH = ("/api/health", "/api/client-status")

_CLIENT = re.compile(r"^\s*([a-z]+)/([0-9A-Za-z][0-9A-Za-z.\-]*)(?:\+([0-9A-Za-z.\-]+))?\s*$")


@dataclass(frozen=True)
class Release:
    platform: str
    version: str
    status: str
    released: str = ""
    commit: str = ""
    build: str = ""
    archive: str = ""
    contract: str = ""
    notes: str = ""

    @property
    def label(self) -> str:
        return f"{self.platform} {self.version}"


def parse(header: Optional[str]) -> Optional[tuple[str, str, str]]:
    """`ios/1.0.0+12` -> ("ios", "1.0.0", "12"). None for anything else."""
    if not header:
        return None
    m = _CLIENT.match(header)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3) or ""


def load(path=None) -> list[Release]:
    """Read the registry. Refuses a malformed one rather than guessing: a typo
    that turned `retired` into an unknown status would quietly serve a version
    the owner decided to stop serving."""
    path = path or REGISTRY_PATH
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    out = []
    seen = set()
    for entry in raw.get("releases", []):
        release = Release(**{k: str(v) for k, v in entry.items()
                             if k in Release.__dataclass_fields__})
        if release.platform not in PLATFORMS:
            raise ValueError(f"releases/registry.json: {release.label}: platform "
                             f"must be one of {', '.join(PLATFORMS)}")
        if release.status not in STATUSES:
            raise ValueError(f"releases/registry.json: {release.label}: status "
                             f"{release.status!r} is not one of {', '.join(STATUSES)}")
        if (release.platform, release.version) in seen:
            raise ValueError(f"releases/registry.json: {release.label} is listed twice")
        seen.add((release.platform, release.version))
        out.append(release)
    return out


_LOCK = threading.Lock()
_RELEASES: list[Release] = load()
_SEEN: Counter = Counter()


def releases() -> list[Release]:
    return list(_RELEASES)


def reload(path=None) -> None:
    """Tests, and a deployment that wants to re-read the file without a restart."""
    global _RELEASES
    _RELEASES = load(path)


def find(platform: str, version: str) -> Optional[Release]:
    for release in _RELEASES:
        if release.platform == platform and release.version == version:
            return release
    return None


def status_for(header: Optional[str]) -> dict:
    """What the server makes of a client header, in the shape
    `/api/client-status` returns."""
    parsed = parse(header)
    if parsed is None:
        return {"client": header or "", "known": False, "status": "unknown",
                "supported": True, "update_recommended": False,
                "update_required": False, "message": ""}
    platform, version, build = parsed
    if (platform, version) == LIVE:
        return {"client": f"{platform}/{version}", "known": True, "status": "current",
                "supported": True, "update_recommended": False,
                "update_required": False, "message": ""}
    release = find(platform, version)
    if release is None:
        return {"client": f"{platform}/{version}", "build": build, "known": False,
                "status": "unknown", "supported": True, "update_recommended": False,
                "update_required": False, "message": ""}
    retired = release.status == "retired"
    deprecated = release.status == "deprecated"
    message = ""
    if retired:
        message = (f"This version of FAM ({release.label}) is no longer supported. "
                   "Update FAM to keep listening.")
    elif deprecated:
        message = (f"A newer version of FAM is available. This one ({release.label}) "
                   "still works, and will stop working in a future update.")
    return {"client": f"{platform}/{version}", "build": build, "known": True,
            "status": release.status, "supported": not retired,
            "update_recommended": deprecated or retired,
            "update_required": retired, "message": message}


def record(header: Optional[str]) -> None:
    """Count who called, for `/api/health`: the answer to "is anybody still on
    1.0?" before anything is retired."""
    parsed = parse(header)
    key = f"{parsed[0]}/{parsed[1]}" if parsed else ("none" if not header else "unparsed")
    with _LOCK:
        if key in _SEEN or len(_SEEN) < 200:  # bounded: the header is client-supplied
            _SEEN[key] += 1


def report() -> dict:
    with _LOCK:
        seen = dict(_SEEN)
    return {
        "registry": [{"platform": r.platform, "version": r.version,
                      "status": r.status} for r in _RELEASES],
        "seen_since_boot": seen,
    }


def archive_dir(version: str):
    """The kept copy of a web release, or None if there is none to serve."""
    release = find("web", version)
    if release is None or not release.archive:
        return None
    path = (PROJECT_ROOT / release.archive).resolve()
    if RELEASES_DIR.resolve() not in path.parents:
        return None
    return path if path.is_dir() else None


def manifest(version: str) -> dict:
    folder = archive_dir(version)
    if folder is None:
        return {}
    try:
        return json.loads((folder / "manifest.json").read_text())
    except (OSError, ValueError):
        return {}
