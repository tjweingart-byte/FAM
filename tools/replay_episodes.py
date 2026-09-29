#!/usr/bin/env python3
"""Copy kept episodes from one deployment into staging, to replay for nothing (§172).

    python tools/replay_episodes.py --from https://fam.onrender.com \\
        --to https://fam-staging.onrender.com                      # dry run: lists them
    python tools/replay_episodes.py --from ... --to ... --limit 25 --yes

Staging holds no paid key, so every episode it writes is the canned sample
script in a placeholder tone. That is enough to test everything except the
content. For real content, this moves episodes production already wrote and
voiced - script, title, sources and kept audio - into staging's cache, where
they replay exactly as they played the first time and cost nothing.

What does not move: `author` (who asked first is about a listener, and staging
is not where listeners' data goes), and anything else about any listener.
Episodes only, never accounts, plays or history.

Admin at both ends: `--from-token` / `FAM_ADMIN_TOKEN_FROM` is the source's
`FAM_ADMIN_TOKEN`, `--to-token` / `FAM_ADMIN_TOKEN_TO` the target's. The target
refuses an import unless it is zero spend, so pointing `--to` at production by
mistake does nothing.

Runs on your machine, not on staging - staging makes no outbound connections.
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx


def _client(base: str, token: str) -> httpx.Client:
    return httpx.Client(base_url=base.rstrip("/"), timeout=120,
                        headers={"X-Admin-Token": token} if token else {})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--from", dest="source", required=True, help="source deployment URL")
    parser.add_argument("--to", dest="target", required=True, help="staging URL")
    parser.add_argument("--from-token", default=os.environ.get("FAM_ADMIN_TOKEN_FROM", ""))
    parser.add_argument("--to-token", default=os.environ.get("FAM_ADMIN_TOKEN_TO", ""))
    parser.add_argument("--limit", type=int, default=25, help="most-played first")
    parser.add_argument("--with-audio-only", action="store_true",
                        help="skip episodes with no kept audio (they would play in the tone)")
    parser.add_argument("--yes", action="store_true", help="actually copy; without it, list")
    args = parser.parse_args()

    with _client(args.source, args.from_token) as source, \
            _client(args.target, args.to_token) as target:
        health = target.get("/api/health").json()
        if not health.get("environment", {}).get("zero_spend"):
            print(f"{args.target} is not zero spend - it would refuse the import. "
                  "Point --to at staging.", file=sys.stderr)
            return 1
        listing = source.get("/api/admin/episodes", params={"limit": args.limit})
        if listing.status_code == 404:
            print(f"{args.source} answered 404: set --from-token to its FAM_ADMIN_TOKEN.",
                  file=sys.stderr)
            return 1
        listing.raise_for_status()
        episodes = listing.json()["episodes"]
        if args.with_audio_only:
            episodes = [e for e in episodes if e["audio"]]
        for episode in episodes:
            print(f"  {episode['plays']:>4} plays  {'audio' if episode['audio'] else 'text '}  "
                  f"{episode['title'][:70]}")
        if not args.yes:
            print(f"\n{len(episodes)} episode(s) would be copied. Nothing was; pass --yes.")
            return 0
        copied = 0
        for episode in episodes:
            data = source.get(f"/api/admin/episodes/{episode['key']}")
            data.raise_for_status()
            result = target.post("/api/admin/episodes", json=data.json())
            if result.status_code != 200:
                print(f"refused: {result.status_code} {result.text[:200]}", file=sys.stderr)
                return 1
            copied += 1
        print(f"\ncopied {copied} episode(s) into {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
