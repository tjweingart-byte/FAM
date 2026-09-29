#!/usr/bin/env python3
"""Record a client release, so the server keeps working for it (§172).

    python tools/cut_release.py --platform web                  # today's web client
    python tools/cut_release.py --platform web --version 2026.10.02
    python tools/cut_release.py --platform ios --version 1.0.0 --build 12 \\
        --source ../FAM-iOS/FAM                                   # at App Store submission
    python tools/cut_release.py --status ios 1.0.0 deprecated   # later: nudge to update
    python tools/cut_release.py --status ios 1.0.0 retired      # last resort: 426

What a cut writes:

* `releases/contracts/<platform>-<version>.json` - every `/api/` path the
  client's code names and the JSON shape of each bare GET, recorded against
  this checkout in a clean process (`release_contracts.py`). CI replays it
  against every later commit.
* for web, `releases/web/<version>/` - the client itself, kept whole and served
  at `/v/<version>/` so it can be opened and used against staging. Stamped with
  its version (`FAM_CLIENT`), its script path pointed into the archive, and its
  service worker left out (one registered from an old page would take over the
  live app's shell).
* an entry in `releases/registry.json`.

An iOS build is a binary and is not kept here: App Store Connect keeps every
uploaded build, and `--build` records which one this was. Cut it from the
commit the build was made against, with `--source` pointing at the Swift code,
so the routes it calls are read from what actually shipped.

Nothing is overwritten without `--force`: a contract is a promise already made
to phones in the field, and rewriting it is how a break gets approved by
accident.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import release_contracts as rc  # noqa: E402

REGISTRY = ROOT / "releases" / "registry.json"
CONTRACTS = ROOT / "releases" / "contracts"
WEB_ARCHIVE = ROOT / "releases" / "web"

#: The rewrites that make a copy of the live page into a kept release. Each
#: must match exactly once; a page that changed so one no longer matches stops
#: the cut rather than producing an archive that half works.
def _rewrites(version: str) -> list[tuple[str, str, str]]:
    return [
        ('var FAM_CLIENT = "web/live";', f'var FAM_CLIENT = "web/{version}";',
         "the client's name for itself"),
        ('<script src="/fam-audio.js"></script>',
         f'<script src="/v/{version}/fam-audio.js"></script>',
         "the audio engine this release shipped with"),
        ('navigator.serviceWorker.register("/sw.js")',
         'Promise.resolve(/* a kept release registers no service worker */)',
         "the service worker"),
    ]


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _registry() -> dict:
    try:
        return json.loads(REGISTRY.read_text())
    except FileNotFoundError:
        return {"releases": []}


def _write_registry(data: dict) -> None:
    data["releases"].sort(key=lambda r: (r["platform"], r.get("released", ""), r["version"]))
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(data, indent=2) + "\n")


def archive_web(version: str, force: bool) -> dict:
    folder = WEB_ARCHIVE / version
    if folder.exists() and not force:
        raise SystemExit(f"{folder.relative_to(ROOT)} exists. A kept release is not "
                         "rewritten; pass --force only if this version never shipped.")
    folder.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in rc.WEB_FILES:
        text = (ROOT / "static" / name).read_text(encoding="utf-8")
        if name == "index.html":
            for old, new, what in _rewrites(version):
                count = text.count(old)
                if count != 1:
                    raise SystemExit(f"static/index.html: expected exactly one {old!r} "
                                     f"({what}), found {count}. Update _rewrites in "
                                     "tools/cut_release.py to match the page.")
                text = text.replace(old, new)
        (folder / name).write_text(text, encoding="utf-8")
        files[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    manifest = {"platform": "web", "version": version, "commit": _commit(),
                "files": files}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def cut(platform: str, version: str, status: str, source: Path | None,
        build: str, notes: str, force: bool) -> None:
    data = _registry()
    existing = [r for r in data["releases"]
                if r["platform"] == platform and r["version"] == version]
    if existing and not force:
        raise SystemExit(f"{platform} {version} is already in releases/registry.json.")
    endpoints = rc.endpoints_in(rc.client_sources(platform, source))
    recorded = rc.record_in_subprocess(endpoints)
    patterns = [(p, re.compile(_regex(p))) for p in recorded["routes"]]
    routed = [e for e in endpoints if rc.routes(e, patterns)]
    unrouted = [e for e in endpoints if e not in routed]
    contract = {
        "platform": platform, "version": version, "commit": _commit(),
        "cut": dt.date.today().isoformat(),
        "routes": routed,
        # Named in the client's code but answered by nothing when it was cut
        # (a comment, a dead branch). Kept for the record, never checked.
        "unrouted_at_cut": unrouted,
        "shapes": recorded["shapes"],
    }
    CONTRACTS.mkdir(parents=True, exist_ok=True)
    contract_path = CONTRACTS / f"{platform}-{version}.json"
    contract_path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n")
    entry = {"platform": platform, "version": version, "status": status,
             "released": dt.date.today().isoformat(), "commit": contract["commit"],
             "contract": str(contract_path.relative_to(ROOT))}
    if build:
        entry["build"] = build
    if notes:
        entry["notes"] = notes
    if platform == "web":
        archive_web(version, force)
        entry["archive"] = str((WEB_ARCHIVE / version).relative_to(ROOT))
    data["releases"] = [r for r in data["releases"]
                        if not (r["platform"] == platform and r["version"] == version)]
    data["releases"].append(entry)
    _write_registry(data)
    print(f"cut {platform} {version}: {len(routed)} routes, "
          f"{len(recorded['shapes'])} response shapes"
          + (f", {len(unrouted)} unrouted literal(s) noted" if unrouted else ""))


def _regex(path: str) -> str:
    """A route path as a regex, the way Starlette compiles it."""
    out = re.sub(r"\{[^}:]+:path\}", ".*", path)
    out = re.sub(r"\{[^}]+\}", "[^/]+", out)
    return "^" + out + "$"


def set_status(platform: str, version: str, status: str) -> None:
    data = _registry()
    for entry in data["releases"]:
        if entry["platform"] == platform and entry["version"] == version:
            entry["status"] = status
            _write_registry(data)
            print(f"{platform} {version} is now {status}")
            return
    raise SystemExit(f"{platform} {version} is not in releases/registry.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--platform", choices=("web", "ios"))
    parser.add_argument("--version", default="")
    parser.add_argument("--build", default="", help="iOS build number")
    parser.add_argument("--source", type=Path, help="iOS: the app's source directory")
    parser.add_argument("--notes", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--status", nargs=3, metavar=("PLATFORM", "VERSION", "STATUS"),
                        help="change a release's status: supported | deprecated | retired")
    args = parser.parse_args()
    if args.status:
        platform, version, status = args.status
        if status not in ("supported", "deprecated", "retired"):
            raise SystemExit("status must be supported, deprecated or retired")
        set_status(platform, version, status)
        return
    if not args.platform:
        parser.error("--platform is required to cut a release")
    version = args.version or (dt.date.today().strftime("%Y.%m.%d")
                               if args.platform == "web" else "")
    if not version:
        parser.error("an iOS release needs --version (the App Store version)")
    cut(args.platform, version, "supported", args.source, args.build, args.notes, args.force)


if __name__ == "__main__":
    main()
