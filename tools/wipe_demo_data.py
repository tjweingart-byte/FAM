"""Take the seeded demonstration data back out, so the feed is only real use.

    python tools/wipe_demo_data.py                  what it would remove
    python tools/wipe_demo_data.py --yes            remove the seed
    python tools/wipe_demo_data.py --all --yes      and everything else
    python tools/wipe_demo_data.py --url https://fam.example.com --yes

`tools/seed_demo.py` writes the history the browse surfaces need on a fresh
install: three invented listeners, a handful of real episodes in the shared
cache, and plays against them. That is what makes Explore non-empty and the
crowd rails rankable on day one, and it is the right thing to have while
showing somebody the product.

It is the wrong thing to have while **measuring** it. Every seeded play is a
vote in `topics.taste`, every seeded script is a tile the crowd rails sort to
the front, and none of it came from a listener. So a question like "is myFAM
recommending the right things" cannot be answered on a deployment that still
holds it: some unknown share of the answer is three people who do not exist.

This is the inverse of that tool and deliberately lives beside it.

## What each mode removes

`--seed` (the default) is exactly what `seed_demo.py` wrote:

* the three demonstration listeners, through the same per-store erasure
  account deletion uses - so events, mixes, social rows, preferences, quota
  counters, messages, saved items and shares all go together, and a store
  added later is covered without this file being edited;
* the scripts those listeners authored, by `author`, which is the column the
  cache already keeps so Explore can leave somebody's own episodes off their
  own feed. A script with no author is left alone: it was not provably seed
  data, and guessing is how a real listener's episode gets deleted.

`--all` additionally empties **the whole script cache and the whole event
log**, and with them the three things that were derived from that log rather
than stored beside it: the grown vocabulary (`categories.py`, minted from
what listeners searched for), the copy of it each worker holds in process,
and the engagement table. A wipe that left those would rank a blank-slate
feed on episodes nobody can play any more, and nothing on the outside would
say so. That is the true blank slate - "wipe all the fake episode titles
completely so we start seeing only new ones" - and it is a bigger thing than
it looks: real listening goes with it, and the taste model starts from
nothing for everybody. It costs no data that cannot be regenerated (a script
is ~$0.03, and its kept audio goes with it and is re-voiced once on the
next play - §131) and it does cost history that cannot.

**What it deliberately does not remove**, because none of it is an episode:
a mix holds topic ids, a saved item and a vibe hold a question - all three
are pointers, so they survive and play again from a freshly written script.
Accounts, credentials and the metering ledger are untouched in both scopes.

Neither mode touches accounts, credentials or the metering ledger. Somebody
who signed up stays signed up; what the app spent stays reconcilable.

## Why this can also be done over HTTP

`POST /api/admin/wipe`, behind `FAM_ADMIN_TOKEN`, does the same thing. A
container host is where this is actually needed and is the one place there is
no shell - and a destructive operation nobody can reach is a destructive
operation somebody performs with `rm` on the wrong file instead.

Nothing happens without `--yes`. A dry run is the default because this is not
reversible and because the useful half of the answer is the count.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import demo_data  # noqa: E402

#: The listeners the seed invents, from the one place that declares them.
SEED_USER_IDS = demo_data.SEED_USER_IDS


def wipe(scope: str = "seed", dry_run: bool = True) -> dict:
    """This machine's stores, handed to `demo_data.wipe`.

    The decision about what a wipe *is* lives there, so this command and
    `POST /api/admin/wipe` cannot come to mean two different things. All
    this adds is which stores to do it to.
    """
    import app as app_mod

    return demo_data.wipe(cache=app_mod.SCRIPT_CACHE, events=app_mod.EVENTS,
                          erase_listener=app_mod.erase_listener,
                          scope=scope, dry_run=dry_run)


def _remote(url: str, scope: str, dry_run: bool, token: str) -> dict:
    import httpx

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        response = client.post(
            f"{url.rstrip('/')}/api/admin/wipe",
            json={"scope": scope, "dry_run": dry_run},
            headers={"X-Admin-Token": token})
        response.raise_for_status()
        return response.json()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true",
                        help="Also empty the whole script cache and event log.")
    parser.add_argument("--yes", action="store_true",
                        help="Actually do it. Without this, nothing is removed.")
    parser.add_argument("--url", default="",
                        help="A running deployment, instead of this machine.")
    parser.add_argument("--token", default=os.environ.get("FAM_ADMIN_TOKEN", ""),
                        help="FAM_ADMIN_TOKEN for --url. Read from the "
                             "environment by default.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    scope = "all" if args.all else "seed"
    dry_run = not args.yes

    try:
        if args.url:
            if not args.token:
                print("--url needs --token or FAM_ADMIN_TOKEN.", file=sys.stderr)
                return 2
            report = _remote(args.url, scope, dry_run, args.token)
        else:
            report = wipe(scope, dry_run)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not wipe: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    if dry_run:
        print(f"Dry run ({scope}). Nothing has been removed.\n")
        print(f"  seeded listeners     {len(SEED_USER_IDS)}"
              f"  ({', '.join(SEED_USER_IDS)})")
        if "scripts_by_seed" in report:
            print(f"  scripts they wrote   {report['scripts_by_seed']}"
                  f" of {report['scripts_total']} readable")
        if scope == "all":
            print(f"  events in the log    {report.get('events_total', '?')}"
                  "  (ALL of them go, not only the seed's)")
            print(f"  scripts in the cache {report.get('scripts_total', '?')}"
                  "  (ALL of them go)")
            print(f"  words in the vocabulary {report.get('categories_total', '?')}"
                  "  (minted from those events)")
        print("\nRe-run with --yes to do it.")
        return 0

    print(f"Wiped ({scope}).\n")
    for user_id, removed in report["listeners"].items():
        if isinstance(removed, dict):
            total = sum(v for v in removed.values() if isinstance(v, int) and v > 0)
            print(f"  {user_id:<16} {total} row(s)")
    print(f"  scripts removed  {report.get('scripts_removed', 0)}")
    if scope == "all":
        print(f"  events removed   {report.get('events_removed', 0)}")
        print(f"  stories dropped  {report.get('stories_dropped', 0)}")
        print(f"  vocabulary       {report.get('categories_dropped', 0)} node(s)")
    print("\nThe browse surfaces will be thin until real listening fills them. "
          "That is the point: what they show now is measured, not seeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
