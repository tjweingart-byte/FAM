"""What a shipped client depends on, recorded so the server can be held to it.

`tools/cut_release.py` writes one contract per release, and
`tests/test_client_contracts.py` replays every contract whose release is not
retired (client_versions.py, PROBLEMS.md §172). A contract has two parts:

* **routes** - every `/api/` path the client's code names, read out of the
  code rather than typed by hand (a literal ending in `/` is a prefix the
  client builds on, `"/api/mixes/" + id`). Each must still route. Removing or
  renaming an endpoint an installed app calls fails here.
* **shapes** - for every one of those paths that answers a bare GET, the shape
  of the JSON it returned when the release was cut: keys, and the type under
  each. The current answer must be a superset. Adding a field passes; removing
  one, renaming one, or turning a list into an object fails, with the release
  and the key named.

Both sides are recorded the same way - a fresh process, empty databases in a
temporary directory, zero spend, the test suite's hermetic environment - so a
difference is a difference in the code and never in the data a test happened
to leave behind.

What a contract cannot see: POST bodies and the meaning of a value. Those are
what opening the kept release on staging (`/v/<version>/`) is for.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

#: The files that are the web client. `sw.js` is not: a kept release never
#: registers one (it would take over the live app's shell).
WEB_FILES = ("index.html", "fam-audio.js")

_LITERAL = re.compile(r"""["'`](/api/[A-Za-z0-9_\-/]*)""")

#: Diagnostic surfaces whose nested keys describe the machine rather than the
#: API (which engines are installed, which databases exist). Only their
#: top-level keys are held to a contract.
SHALLOW = ("/api/health",)

#: Never called while recording: a GET here changes state or waits on the world.
NOT_RECORDED = ("/api/auth/logout",)


def endpoints_in(texts) -> list[str]:
    """Every `/api/` literal in the client's code, sorted, without duplicates."""
    found = set()
    for text in texts:
        for match in _LITERAL.finditer(text):
            path = match.group(1)
            if path in ("/api/", "/api"):
                continue
            found.add(path)
    return sorted(found)


def client_sources(platform: str, source: Path | None = None) -> list[str]:
    """The text of a client's code: the web files, or every text file under an
    iOS source directory (Swift, plists, anything that names a path)."""
    if platform == "web":
        base = source or (ROOT / "static")
        return [(base / name).read_text(encoding="utf-8") for name in WEB_FILES]
    if source is None:
        raise ValueError("an iOS release needs --source: the app's source directory")
    texts = []
    for path in sorted(Path(source).rglob("*")):
        if path.is_file() and path.suffix in (".swift", ".m", ".h", ".plist", ".json", ".strings"):
            try:
                texts.append(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
    return texts


def route_patterns(app) -> list[tuple[str, "re.Pattern"]]:
    out = []
    for route in app.routes:
        regex = getattr(route, "path_regex", None)
        path = getattr(route, "path", "")
        if regex is not None and path.startswith("/api/"):
            out.append((path, regex))
    return out


def routes(endpoint: str, patterns) -> bool:
    """Whether `endpoint` still reaches a handler. A prefix (`/api/mixes/`)
    needs some route beneath it; an exact path needs a route that matches it."""
    if endpoint.endswith("/"):
        return any(path.startswith(endpoint) and len(path) > len(endpoint)
                   or regex.match(endpoint + "x") for path, regex in patterns)
    return any(regex.match(endpoint) for _, regex in patterns)


def shape(value, depth: int = 0, limit: int = 12):
    """The shape of a JSON value: an object's keys with their shapes, a list's
    first element, or a scalar's type name."""
    if depth >= limit:
        return "any"
    if isinstance(value, dict):
        return {str(k): shape(v, depth + 1, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0], depth + 1, limit)] if value else []
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "null"


def shallow(recorded):
    if isinstance(recorded, dict):
        return {k: "any" for k in recorded}
    return recorded


def breaks(recorded, current, where: str = "") -> list[str]:
    """Every way `current` fails to keep the promise `recorded` made.

    A superset check: a key recorded must still be there, a type recorded must
    still be that type. `null` and an empty list recorded promise only that the
    key exists, since the value was not there to learn from.
    """
    here = where or "(body)"
    if recorded in ("any", "null") or recorded == []:
        if recorded == [] and not isinstance(current, list):
            return [f"{here}: was a list, is now {_name(current)}"]
        return []
    if isinstance(recorded, dict):
        if not isinstance(current, dict):
            return [f"{here}: was an object, is now {_name(current)}"]
        out = []
        for key, sub in recorded.items():
            if key not in current:
                out.append(f"{where}.{key}: removed" if where else f"{key}: removed")
                continue
            out += breaks(sub, current[key], f"{where}.{key}" if where else key)
        return out
    if isinstance(recorded, list):
        if not isinstance(current, list):
            return [f"{here}: was a list, is now {_name(current)}"]
        if not current:
            return []
        return breaks(recorded[0], current[0], f"{where}[0]")
    if current == "null" or current is None:
        return []  # a value that is absent this time is not a type change
    got = _name(current) if not isinstance(current, str) or current not in (
        "string", "number", "boolean", "null", "any") else current
    if got != recorded:
        return [f"{here}: was {recorded}, is now {got}"]
    return []


def _name(value) -> str:
    if isinstance(value, dict):
        return "an object"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, str) and value in ("string", "number", "boolean", "null", "any"):
        return value
    return shape(value) if not isinstance(value, (dict, list)) else "?"


# --- Recording, always in a fresh process ---------------------------------


def data_variables() -> list[str]:
    """Every `data_path("VAR", ...)` in the code - the same derivation
    `tests/test_data_paths.py` uses, so a new store is isolated too."""
    names = set()
    for path in ROOT.glob("*.py"):
        names.update(re.findall(r'data_path\(\s*"([A-Z0-9_]+)"', path.read_text(encoding="utf-8")))
    return sorted(names)


def record_in_subprocess(paths: list[str]) -> dict:
    """Record the current server's answers in a clean process. Returns
    {"routes": [...], "shapes": {path: shape}}."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="fam-contract-") as tmp:
        env = {k: v for k, v in os.environ.items()}
        for name in data_variables():
            env[name] = str(Path(tmp) / f"{name.lower()}.db")
        env["ZERO_SPEND"] = "1"
        env["FAM_ENV"] = ""
        env["FAM_IGNORE_DOTENV"] = "1"
        env["PYTHONPATH"] = str(ROOT)
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys, json, release_contracts as rc; "
             "print(json.dumps(rc._record_here(json.loads(sys.argv[1]))))",
             json.dumps(paths)],
            cwd=tmp, env=env, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError("recording the server's answers failed:\n" + result.stderr[-4000:])
    return json.loads(result.stdout.strip().splitlines()[-1])


def _record_here(paths: list[str]) -> dict:
    """Runs inside the fresh process. Clears the same environment the test
    suite clears, then asks the app."""
    import logging

    logging.disable(logging.CRITICAL)
    sys.path.insert(0, str(ROOT))
    # The suite's hermetic environment, reused rather than restated: importing
    # it clears every variable config.py reads.
    keep = {k: os.environ[k] for k in data_variables() + ["ZERO_SPEND"] if k in os.environ}
    # Loaded by path rather than imported: this module ships, `tests` is not a
    # package the app depends on, and only the recorder (a development and CI
    # step) ever needs it.
    import importlib.util

    spec = importlib.util.spec_from_file_location("_fam_hermetic", ROOT / "tests" / "conftest.py")
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
    os.environ.update(keep)
    from fastapi.testclient import TestClient

    import app as appmod

    patterns = route_patterns(appmod.app)
    shapes = {}
    with TestClient(appmod.app) as client:
        for path in paths:
            if path.endswith("/") or path in NOT_RECORDED or not routes(path, patterns):
                continue
            try:
                response = client.get(path)
            except Exception:  # noqa: BLE001 - a path that cannot be recorded is left out
                continue
            if response.status_code != 200:
                continue
            if "json" not in response.headers.get("content-type", ""):
                continue
            recorded = shape(response.json())
            shapes[path] = shallow(recorded) if path in SHALLOW else recorded
    return {"routes": [p for p, _ in patterns], "shapes": shapes}
