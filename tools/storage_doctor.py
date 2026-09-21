"""One command that says whether a redeploy will erase this app's listeners.

    python tools/storage_doctor.py                  this machine
    python tools/storage_doctor.py --url https://fam.example.com
    python tools/storage_doctor.py --json

The failure this exists for does not announce itself. It arrives as "everybody
has to sign up again", once per deploy, and from outside a wiped database and a
fresh install are the same thing: the app comes up, the schema is created, the
health page is green, and every account, mix, message, share link and saved
episode is gone. Nothing in that sequence is an error.

`/api/health` has measured it since §107 - `st_dev` against the code's own
filesystem, so a mounted volume is a different device and anything on the
code's device goes with the image. What was missing was somebody asking. This
asks, against a running deployment or against this machine, and prints the one
sentence and the one fix.

What it is careful about, because the answer is easy to get confidently wrong:

* **Ephemeral is not always a fault.** On a laptop nothing is mounted and the
  databases sit beside the code, which is correct there. What makes it a fault
  is a store whose environment variable was *set* to an absolute path and that
  is still on the code's filesystem - that deployment asked for a disk and did
  not get one.
* **It reports what the server measured**, never what the settings say. A
  store pointed at `/data` on a host with no disk actually attached is the
  whole case this exists for, and it is the one a settings check cannot see.

Exit codes: 0 everything durable (or correctly ephemeral on a dev machine);
1 at least one store will be erased by a redeploy; 2 could not ask.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _local_report() -> dict:
    """Ask this process, without starting a server."""
    import app as app_mod

    databases = app_mod._database_report()
    return {"storage": app_mod._storage_summary(databases),
            "databases": databases}


def _remote_report(url: str) -> dict:
    import httpx

    base = url.rstrip("/")
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        body = client.get(f"{base}/api/health").json()
    return {"storage": body.get("storage", {}),
            "databases": body.get("databases", []),
            "build": body.get("build", "unknown")}


def _render(report: dict) -> int:
    storage = report.get("storage") or {}
    databases = report.get("databases") or []
    ephemeral = storage.get("ephemeral") or []
    unknown = storage.get("unknown") or []
    by_name = {d.get("name"): d for d in databases}

    if "build" in report:
        print(f"build      {report['build']}")
    print(f"durable    {len(storage.get('durable') or [])}")
    print(f"ephemeral  {len(ephemeral)}")
    if unknown:
        print(f"unknown    {len(unknown)}")
    print()

    for entry in databases:
        # Two different things read as "image", and only one of them is a
        # fault - see the module docstring. A laptop's databases sit beside
        # the code because nothing asked them to sit anywhere else, and
        # printing ERASED next to twelve of them every time somebody runs
        # this is how a warning stops being read.
        told = "set" if entry.get("configured") else "default"
        mark = {"disk": "keeps", "memory": "memory", "unknown": "?"}.get(
            entry.get("persistence"),
            "ERASED" if entry.get("configured") else "local")
        print(f"  {mark:<7} {entry.get('name','?'):<14} {told:<8} {entry.get('path','')}")
    print()

    if not ephemeral and not unknown:
        print("Every database is on a volume separate from the code. A "
              "redeploy keeps them.")
        return 0

    # The distinction that decides whether this is a bug or a laptop.
    configured = [n for n in ephemeral if (by_name.get(n) or {}).get("configured")]
    print(storage.get("note") or "")
    print()
    if configured:
        print("This deployment asked for a mounted disk and did not get one.")
        print(f"Set and still ephemeral: {', '.join(configured)}")
        print()
        print("On Render, in the service's Settings:")
        print("  1. Add a disk. Mount Path /data, 1 GB is plenty.")
        print("     render.yaml declares it, but a service created from the")
        print("     dashboard rather than from the blueprint has none - and")
        print("     a disk added later needs a redeploy to take effect.")
        print("  2. Redeploy. The next push keeps its listeners.")
        print()
        print("Anything written before the disk is attached is inside the")
        print("image and cannot be recovered from here.")
        return 1
    print("Nothing here was configured to live anywhere in particular, so "
          "this is a development machine and the databases are beside the "
          "code. That is correct here and would not be on a container host.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="",
                        help="A running deployment to ask instead of this machine.")
    parser.add_argument("--json", action="store_true",
                        help="The raw report, for a script or a CI step.")
    args = parser.parse_args(argv)

    try:
        report = _remote_report(args.url) if args.url else _local_report()
    except Exception as exc:  # noqa: BLE001 - a doctor reports, it does not raise
        print(f"Could not ask: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
        storage = report.get("storage") or {}
        return 1 if (storage.get("ephemeral") or storage.get("unknown")) else 0
    return _render(report)


if __name__ == "__main__":
    raise SystemExit(main())
