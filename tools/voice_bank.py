"""Add, list and remove the voices in FAM's bank (PROBLEMS.md §147).

    python tools/voice_bank.py list
    python tools/voice_bank.py add nova --label Nova --file nova.wav \\
        --rights nova.rights.json [--description "Warm, unhurried"]
    python tools/voice_bank.py remove nova

Against a running deployment rather than the local database, add
`--url https://your-app.onrender.com` and set `FAM_ADMIN_TOKEN` to the
server's admin token - the same credential `/api/usage` takes:

    FAM_ADMIN_TOKEN=... python tools/voice_bank.py --url https://... list

A recording is a few seconds of one person speaking clearly, as a WAV. Its
rights record is JSON clearing `consent`, `commercial_use` and
`synthetic_voice_cleared` ("yes" each) - the same record the default voice
has beside it, because a cloned voice is somebody's voice. Nothing is added
without one.

Where it goes: the app's database (`VOICE_BANK_DB`). The GPU worker never
needs a copy put on it by hand - the first episode asked of it in a new
voice sends the recording along, and it keeps it.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _remote(args, method: str, path: str, body: dict | None = None) -> dict:
    import httpx

    token = os.environ.get("FAM_ADMIN_TOKEN", "").strip()
    if not token:
        sys.exit("set FAM_ADMIN_TOKEN to the server's admin token")
    response = httpx.request(method, args.url.rstrip("/") + path,
                             headers={"X-Admin-Token": token},
                             json=body, timeout=60.0)
    if response.status_code == 404:
        sys.exit("404: wrong URL, or the token does not match FAM_ADMIN_TOKEN "
                 "on the server (a wrong token is answered as not found)")
    if response.status_code >= 400:
        sys.exit(f"{response.status_code}: {response.text[:400]}")
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="",
                        help="a running deployment, instead of the local database")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    add = sub.add_parser("add")
    add.add_argument("slug")
    add.add_argument("--label", required=True)
    add.add_argument("--file", required=True, help="the reference recording (WAV)")
    add.add_argument("--rights", required=True, help="the rights record (JSON)")
    add.add_argument("--description", default="")
    add.add_argument("--replace", action="store_true")
    remove = sub.add_parser("remove")
    remove.add_argument("slug")
    args = parser.parse_args()

    import voice_bank

    if args.command == "list":
        if args.url:
            voices = _remote(args, "GET", "/api/admin/voices")["voices"]
        else:
            voices = [v.as_dict() for v in voice_bank.catalogue()]
        for v in voices:
            mark = " (default)" if v.get("default") else ""
            print(f"  {v['slug']:<20} {v['label']}{mark}"
                  + (f"  - {v['description']}" if v.get("description") else ""))
        return 0

    if args.command == "add":
        with open(args.file, "rb") as fh:
            audio = fh.read()
        with open(args.rights, encoding="utf-8") as fh:
            rights = json.load(fh)
        if args.url:
            _remote(args, "POST", "/api/admin/voices", {
                "slug": args.slug, "label": args.label,
                "description": args.description, "rights": rights,
                "audio": base64.b64encode(audio).decode("ascii"),
                "replace": args.replace})
        else:
            try:
                voice_bank.bank().add(args.slug, args.label, audio, rights,
                                      description=args.description,
                                      replace=args.replace)
            except voice_bank.VoiceBankError as exc:
                sys.exit(str(exc))
        print(f"added {args.slug} ({args.label})")
        return 0

    if args.command == "remove":
        if args.url:
            gone = _remote(args, "DELETE", f"/api/admin/voices/{args.slug}")["ok"]
        else:
            gone = voice_bank.bank().remove(args.slug)
        print(f"removed {args.slug}" if gone else f"no voice called {args.slug}")
        return 0 if gone else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
