#!/usr/bin/env python3
"""Set up the history archive on MongoDB Atlas, and prove it works (§308, ARCHIVE.md).

    MONGODB_URI='mongodb+srv://...' python tools/archive_setup.py

(or with MONGODB_URI in FAM_SECRETS / ~/.fam/env, which is better: nothing
typed into a shell's history.) Run it once from a machine that can reach the
cluster, before setting `ARCHIVE_BACKEND=atlas` on Render.

Verify, do not inspect. It:

1. connects and pings, timing ten pings (the network round trip);
2. creates the ordinary indexes (idempotent);
3. creates the `facts_vector` Atlas Vector Search index if it is missing,
   and waits until Atlas says it is queryable (a minute or two the first
   time);
4. writes a probe fact with a known vector, finds it again through
   `$vectorSearch` - the recall's exact query - timing it, and deletes it;
5. writes and reads back a probe intake record and episode link, and
   deletes them.

Exit status 0 only when every step answered. Costs nothing beyond the
cluster's own price: a few writes, reads and one index.
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: How long to wait for a new vector index to become queryable.
INDEX_WAIT_SECONDS = 600
#: How long a freshly written probe may take to appear in the index.
PROBE_WAIT_SECONDS = 120


def step(label: str, fn):
    started = time.perf_counter()
    try:
        out = fn()
    except Exception as exc:  # noqa: BLE001 - every failure is the answer
        print(f"  FAIL  {label}: {type(exc).__name__}: {exc}")
        raise SystemExit(1)
    print(f"  ok    {label} ({(time.perf_counter() - started) * 1000:.0f} ms)")
    return out


def main() -> int:
    import credentials

    credentials.load()
    import history_archive as ha
    from config import settings

    uri = ha._uri()
    if not uri:
        print("MONGODB_URI is not set (environment, FAM_SECRETS or ~/.fam/env).")
        return 1
    try:
        import pymongo  # noqa: F401
    except ImportError:
        print("pymongo is not installed: pip install -r requirements-archive.txt")
        return 1

    print(f"History archive on Atlas, database {settings.archive_db_name!r}")
    store = step("connect", lambda: ha.AtlasBackend(uri))

    def pings():
        times = []
        for _ in range(10):
            started = time.perf_counter()
            store.ping()
            times.append((time.perf_counter() - started) * 1000)
        return times

    times = step("ping x10", pings)
    print(f"        round trip: median {statistics.median(times):.1f} ms, "
          f"worst {max(times):.1f} ms")
    step("ordinary indexes", store.ensure_indexes)
    status = step("vector index", store.ensure_vector_index)
    if status != "ready":
        print(f"        {ha.VECTOR_INDEX} is {status}; waiting for it to be queryable")
        deadline = time.monotonic() + INDEX_WAIT_SECONDS
        while time.monotonic() < deadline:
            status = store.vector_index_status()
            if status == "ready":
                break
            time.sleep(5)
        if status != "ready":
            print(f"  FAIL  vector index still {status} after {INDEX_WAIT_SECONDS}s")
            return 1
        print("  ok    vector index is queryable")

    # A probe fact with a known vector: one-hot, so it is the nearest
    # neighbour of the same one-hot whatever else the archive holds.
    vector = [0.0] * ha.DIMS
    vector[7] = 1.0
    space = f"probe:{ha.DIMS}"
    stamp = int(time.time())
    probe = ha.Fact(url=f"https://probe.invalid/{stamp}", text="archive probe",
                    title="archive probe", published="2001-01-01", id=f"probe-{stamp}")
    step("write a probe fact", lambda: store.add_fact(probe, vector, space, time.time()))

    def find():
        deadline = time.monotonic() + PROBE_WAIT_SECONDS
        before = datetime.now(timezone.utc) - timedelta(days=1)
        while time.monotonic() < deadline:
            started = time.perf_counter()
            found = store.recall(vector, space, before, 1, 0.9, set())
            took = (time.perf_counter() - started) * 1000
            if found and found[0].id == probe.id:
                return took
            time.sleep(2)
        raise RuntimeError("the probe never came back from $vectorSearch")

    took = step("find it with $vectorSearch", find)
    print(f"        one recall: {took:.1f} ms")

    def timed_recalls():
        before = datetime.now(timezone.utc) - timedelta(days=1)
        out = []
        for _ in range(5):
            started = time.perf_counter()
            store.recall(vector, space, before, 6, 0.0, set())
            out.append((time.perf_counter() - started) * 1000)
        return out

    recalls = step("recall x5", timed_recalls)
    print(f"        recall: median {statistics.median(recalls):.1f} ms "
          f"(the grace beside the search is {settings.archive_grace_seconds:.1f} s)")
    step("delete the probe fact", lambda: store.facts_c.delete_one({"_id": probe.id}))

    finding = ha.Finding(url=probe.url, title="archive probe", host="probe.invalid",
                         grade="unverified", published="2001-01-01",
                         passages=("probe passage",))
    step("write a probe intake record",
         lambda: store.intake([finding], "probe", "", time.time()))
    episode = f"probe.{stamp}"
    step("write a probe episode link",
         lambda: store.link_episode(episode, "probe", stamp, [finding.as_link()],
                                    time.time()))

    def read_back():
        links = store.episode_links(episode)
        if not links or links[0].get("url") != probe.url:
            raise RuntimeError(f"read back {links!r}")

    step("read the episode link back", read_back)
    step("delete the probes", lambda: (
        store.intake_c.delete_one({"_id": ha.url_id(probe.url)}),
        store.episodes_c.delete_one({"_id": episode})))
    print("\nEverything answered. On Render, set ARCHIVE_BACKEND=atlas (and "
          "MONGODB_URI) for the fam service, redeploy, and check /api/health > "
          "archive says enabled: true, backend: atlas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
