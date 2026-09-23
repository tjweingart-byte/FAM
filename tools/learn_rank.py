"""Fit the order of "Made for you" to what listeners actually tapped.

    python tools/learn_rank.py              # measure, and store the model if it wins
    python tools/learn_rank.py --dry-run    # measure only, store nothing
    python tools/learn_rank.py --clear      # remove the stored model

Reads thirty days of offers from the event log (`impression_outcomes`: one
row per tile per listener per hour it was in front of them, taken or not),
rebuilds - **as of the moment of each offer** - the six signals the ranker
would have had, and fits a logistic regression to the outcome. See
`learned_rank.py` for what the model is allowed to do with the result, which
is re-order and never select.

**Point in time, or it measures nothing.** Every feature is computed from
what was in the log *before* that offer: the taste profile from earlier
events, fatigue from earlier occasions, the tile's tap rate from earlier offers
whose play had already happened. Using today's profile would let the tap
itself leak into its own prediction - a listener who played a tile has a
profile that likes it - and the model would learn to recognise answers rather
than to predict them.

**It stores a model only if it wins.** The most recent fifth of the offers is
held out; the model is fitted on the rest and has to rank the held-out ones
better than the hand-tuned score by `learned_rank.MIN_AUC_GAIN`, on at least
`MIN_POSITIVES` taps on each side of the split. Otherwise nothing is written
and the hand-tuned order stays, and the report says which condition failed.
`--force` stores a losing model for inspection, and `learned_rank.active`
still refuses to serve it.

What it cannot see, stated so it is known: a live story that has since left
the pool cannot be resolved to a tile, so its offers are dropped and counted;
freshness and a listener's place are not in the log, so they stay hand-applied
on top of the model.

Spends nothing: no model call, no network. With a semantic model installed
(`tools/install_embed_model.py`) the `semantic` feature is real; without one it
is zero everywhere and the fit gives it no weight.
"""
from __future__ import annotations

import argparse
import bisect
import heapq
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import learned_rank  # noqa: E402
import taste_vectors  # noqa: E402
import topics  # noqa: E402


#: The rails the model is ever applied to. Offers on the others (Trending,
#: the crowd rows, friends, what you missed) still move the running tap
#: rates, because serving's engagement table counts every rail - but they are
#: not training rows: their order was never the hand-tuned score's, and a
#: baseline scored on offers it never ranked is a baseline that loses by
#: default. The first version trained on all of them and "won" on exactly
#: that (review of §128).
RANKED_SECTIONS = ("from_history", "section:from_history", "next_up")

#: Rows the serving path reads per listener (`EventStore.for_user`'s default),
#: so a training profile is built from the same window a live one is.
SERVING_HISTORY = 400


def training_rows(store: topics.EventStore, now: float, since: float,
                  limit: int = 0) -> tuple[list, list, dict]:
    """`(rows, labels, counts)`, in time order.

    Only offers the model could ever re-order become rows: Made for you and
    the post-episode popup, and only tiles whose hand-tuned score cleared
    `RELEVANCE_FLOOR` - the set `rank_from_history` hands a model.

    Linear in the offers (a heap of pending taps, and the per-listener state
    memoised per occasion hour) - §122's rule, since a month of impressions
    is the size this runs at and a quadratic pass that is fine on a test's
    fifty rows is an afternoon on a real log.
    """
    outcomes = store.impression_outcomes(since=since, now=now)
    if limit:
        outcomes = outcomes[-limit:]
    known = topics.known_topics(now)
    history: dict[str, list] = {}
    times: dict[str, list] = {}
    counts = {"offers": len(outcomes), "unresolved": 0, "used": 0,
              "other_rails": 0, "under_floor": 0}

    # Point-in-time engagement: offers so far, and taps whose play has
    # already happened. A tap is known at `at + lag`, not at its offer, so
    # it waits in a heap until the clock passes it.
    offered: dict[str, int] = defaultdict(int)
    took: dict[str, int] = defaultdict(int)
    all_offered = 0
    all_took = 0
    pending: list = []
    # Point-in-time fatigue: earlier occasions of (listener, tile).
    occasions: dict[tuple[str, str], int] = defaultdict(int)
    # (listener, events before this offer, hour) -> profile, familiar, prior.
    # A feed writes a dozen offers in the same hour off the same history, so
    # this turns a taste() per offer into a taste() per page.
    state: dict = {}

    rows, labels = [], []
    for row in outcomes:
        at, user, tid = row["at"], row["user_id"], row["topic_id"]
        while pending and pending[0][0] < at:
            _when, done = heapq.heappop(pending)
            took[done] += 1
            all_took += 1
        topic = known.get(tid)
        ranked_here = row.get("section") in RANKED_SECTIONS
        if topic is not None and not ranked_here:
            counts["other_rails"] += 1
        elif topic is not None:
            if user not in history:
                # Oldest first, so "before this offer" is a prefix.
                history[user] = sorted(store.for_user(user, limit=2000),
                                       key=lambda e: e.at)
                times[user] = [e.at for e in history[user]]
            n = bisect.bisect_left(times[user], at)
            key = (user, n, int(at // topics.FATIGUE_BUCKET))
            if key not in state:
                # Newest first, and no deeper than serving reads.
                prior = list(reversed(history[user][max(0, n - SERVING_HISTORY):n]))
                state[key] = (prior, topics.taste(prior, at),
                              topics.familiar_words(prior))
            prior, profile, familiar = state[key]
            damp = topics.fatigue({tid: occasions[(user, tid)]})
            engage = topics.engagement({tid: (offered[tid], took[tid])},
                                       all_offered, all_took)
            semantic = taste_vectors.for_listener(prior, [topic], at, known=known,
                                                  settled=True)
            feats = learned_rank.features(topic, profile, semantic, damp,
                                          engage, familiar)
            if learned_rank.hand_score(feats) <= topics.RELEVANCE_FLOOR:
                counts["under_floor"] += 1
            else:
                rows.append(feats)
                labels.append(bool(row["taken"]))
                counts["used"] += 1
        else:
            counts["unresolved"] += 1
        # Update the running state *after* the row, so a row never sees itself.
        occasions[(user, tid)] += 1
        offered[tid] += 1
        all_offered += 1
        if row["taken"] and row["lag"] is not None:
            heapq.heappush(pending, (at + row["lag"], tid))
    return rows, labels, counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", default=None, help="event database (default: the app's)")
    parser.add_argument("--days", type=float, default=topics.IMPRESSION_TTL / 86400)
    parser.add_argument("--limit", type=int, default=0,
                        help="use only the most recent N offers")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="store the model even if it loses (never served)")
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args()

    store = topics.EventStore(args.db)
    if args.clear:
        store.clear_learned_model()
        print("Stored model removed; the hand-tuned order is in force.")
        return 0

    now = time.time()
    semantic = taste_vectors.describe()
    print(f"Event log: {store.path}")
    print("Semantic feature: " + ("on" if semantic.get("enabled")
                                  else "off (" + semantic.get("reason", "") + ")"))
    started = time.perf_counter()
    rows, labels, counts = training_rows(store, now, now - args.days * 86400,
                                         args.limit)
    print(f"Offers: {counts['offers']}; used {counts['used']} "
          f"(Made for you / next-up, above the floor); other rails "
          f"{counts['other_rails']}, under the floor {counts['under_floor']}, "
          f"unresolvable {counts['unresolved']} (stories no longer held) "
          f"- {time.perf_counter() - started:.1f}s")
    model, report = learned_rank.train(rows, labels, now=now)
    print(f"Taps: {report['positives']} of {report['rows']}")
    if "auc_learned" in report:
        print(f"Held out: {report['held_out']} offers, "
              f"{report['held_out_positives']} taps")
        print(f"  AUC, hand-tuned order  {report['auc_hand']:.4f}")
        print(f"  AUC, learned order     {report['auc_learned']:.4f}")
    print("Verdict: " + report["verdict"])
    if model is not None:
        print("Weights (standardised):")
        for name, weight in zip(learned_rank.FEATURES, model.weights):
            print(f"  {name:<11} {weight:+.3f}")

    if args.dry_run or model is None:
        print("Nothing stored.")
        return 0
    if not report.get("beat_hand") and not args.force:
        print("Nothing stored: the hand-tuned order stays in force.")
        return 0
    current = learned_rank.active(store, now)
    if current is not None and not report.get("beat_hand"):
        print("WARNING: this replaces a model that is in force with one that "
              "lost; serving falls back to the hand-tuned order.")
    store.save_learned_model(model.to_json(), now)
    print("Stored. " + ("In force from the next page load (within "
                        f"{int(learned_rank.CACHE_SECONDS)}s on a running server)."
                        if report.get("beat_hand")
                        else "Held for inspection and NOT served: it lost."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
