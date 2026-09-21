"""Which tiles actually get tapped, and which rails convert.

    python tools/ctr_report.py                  # the last 30 days
    python tools/ctr_report.py --days 7
    python tools/ctr_report.py --by section     # section | topic | algo | tag
    python tools/ctr_report.py --listener abc   # one listener only
    python tools/ctr_report.py --json

The feed has always answered one question - *will this listener like this
tile* - with `_affinity` over their taste profile. It has never answered the
other one: *will they actually tap it*. This report is the measurement that
question needs, and the notable thing about it is that **it needs no new
collection at all**. Every impression written since the feature shipped
carries the listener, the tile, the rail and the ranking version, and every
play carries the listener, the tile and the time. Joining the two is a
click-through rate, thirty days deep, and until this file nothing read it.

Three rules it keeps, each of which is a way of not lying with a number:

* **Occasions, not renders.** One row per (listener, tile) shown, never one
  per impression. A rail scrolls and a page gets refreshed; counting renders
  would report the most-reloaded feed as the least effective one, which is the
  same trap `FATIGUE_BUCKET` exists to avoid one layer down.
* **No data is `-`, never `0%`.** A tile shown twice has not got a low
  conversion rate, it has no rate. Zero out of zero reads as failure and is
  actually silence - `prefetch.py` learned this and reports `None`; so does
  this.
* **It reports and never ranks.** Nothing imports this module. The engagement
  term that reads the same rows for the *ranking* lives in `topics.py`, where
  it can be tested against a feed; this is the thing that says whether that
  term is worth having and whether it helped after it shipped.

The `--by algo` breakdown is the reason `ALGO_VERSION` is stamped on every
impression. Two versions of the ranking on the same log are a GROUP BY rather
than an archaeology project, which is what makes a ranking change something
that can be judged instead of believed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import topics as topics_mod  # noqa: E402

#: Below this many impressions a rate is not reported. Not a tuning knob - it
#: is the line between a measurement and a coincidence. Three shows and one
#: tap is not a 33% conversion rate, and printing it as one invites somebody
#: to act on it.
MIN_SHOWN = 8

#: Which field of a joined row each `--by` dimension reads. A dict rather than
#: `row.get(dimension)`, because a name that does not match a key comes back
#: as one empty group holding everything - a wrong answer that looks like a
#: right one, which is the failure this whole file exists to avoid.
FIELD = {"section": "section", "topic": "topic_id", "algo": "algo"}


def _rate(taken: int, shown: int):
    """The conversion rate, or None when there is not enough to have one."""
    if shown < MIN_SHOWN:
        return None
    return taken / shown


def _pct(rate) -> str:
    return "  -  " if rate is None else f"{rate * 100:5.1f}%"


def _label(key: str, dimension: str) -> str:
    """A tile id is not a thing anybody recognises; its title is."""
    if dimension != "topic":
        return key or "(none)"
    known = topics_mod.known_topics()
    topic = known.get(key)
    return topic.title if topic else key


def collect(rows: list[dict], dimension: str) -> list[dict]:
    """Group the joined rows by one dimension and rate each group."""
    buckets: dict[str, dict] = defaultdict(
        lambda: {"shown": 0, "taken": 0, "lags": []})
    for row in rows:
        if dimension == "tag":
            keys = topics_mod.facets_only(
                topics_mod.tags_for_id(row["topic_id"])) or ["(untagged)"]
        else:
            # `topic` is spelled `topic_id` in the row, and reading the wrong
            # key does not raise - it groups the whole feed into one nameless
            # bucket and reports the global rate as though it were a finding.
            keys = [row[FIELD[dimension]] or ""]
        for key in keys:
            bucket = buckets[key]
            bucket["shown"] += 1
            if row["taken"]:
                bucket["taken"] += 1
                if row["lag"] is not None:
                    bucket["lags"].append(row["lag"])

    out = []
    for key, bucket in buckets.items():
        lags = sorted(bucket["lags"])
        out.append({
            "key": key,
            "label": _label(key, dimension),
            "shown": bucket["shown"],
            "taken": bucket["taken"],
            "rate": _rate(bucket["taken"], bucket["shown"]),
            # The median rather than the mean, for the reason `usage_report`
            # prints one: a single tile tapped nine days after it was shown
            # would drag an average into meaninglessness.
            "median_lag": lags[len(lags) // 2] if lags else None,
        })
    # Unrated groups last rather than treated as zero, so a thin tail cannot
    # look like the worst-performing part of the feed.
    out.sort(key=lambda r: (r["rate"] is None, -(r["rate"] or 0), -r["shown"]))
    return out


def _lag(seconds) -> str:
    if seconds is None:
        return "   -"
    if seconds < 90:
        return f"{seconds:3.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:3.0f}m"
    return f"{seconds / 3600:3.0f}h"


def report(days: int, dimension: str, listener: str = "",
           as_json: bool = False) -> int:
    store = topics_mod.EventStore()
    since = time.time() - days * 86400
    rows = store.impression_outcomes(since)
    if listener:
        rows = [r for r in rows if r["user_id"] == listener]

    shown = len(rows)
    taken = sum(1 for r in rows if r["taken"])
    listeners = len({r["user_id"] for r in rows})
    groups = collect(rows, dimension)

    if as_json:
        print(json.dumps({
            "db": store.path, "days": days, "by": dimension,
            "listener": listener or None,
            "shown": shown, "taken": taken,
            "rate": _rate(taken, shown), "listeners": listeners,
            "min_shown": MIN_SHOWN, "groups": groups,
        }, indent=2))
        return 0

    print(f"{store.path}")
    print(f"the last {days} days, by {dimension}"
          + (f", for {listener}" if listener else ""))
    print()
    if not shown:
        # Four ways to be empty and they are not the same thing. Saying
        # "0% conversion" here would be a claim about the feed made from a
        # fact about the database.
        print("  No impressions in the window.")
        print("  Either nothing has drawn myFAM on this database, or the")
        print("  impressions have aged past IMPRESSION_TTL"
              f" ({topics_mod.IMPRESSION_TTL // 86400} days).")
        print(f"  The log holds {store.count()} events in total.")
        return 0

    print(f"  offered {shown} tiles to {listeners} listener(s), "
          f"{taken} taken  ->  {_pct(_rate(taken, shown))}")
    print()
    print(f"  {'':34} {'shown':>6} {'taken':>6} {'rate':>7}  {'median lag':>10}")
    for group in groups:
        print(f"  {group['label'][:34]:34} {group['shown']:>6} "
              f"{group['taken']:>6} {_pct(group['rate']):>7}  "
              f"{_lag(group['median_lag']):>10}")
    print()
    print(f"  A rate is printed only above {MIN_SHOWN} impressions; below that")
    print("  there is no rate, which is not the same as a low one.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=30,
                        help="window in days (default 30, the impression TTL)")
    parser.add_argument("--by", default="section",
                        choices=("section", "topic", "algo", "tag"),
                        help="what to group by (default section)")
    parser.add_argument("--listener", default="",
                        help="one listener id, for a single-user view")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    return report(args.days, args.by, args.listener, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
