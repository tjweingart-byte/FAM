"""Ask every configured live provider to prove it works. Nothing else does.

    python tools/verify_live.py            # every configured domain
    python tools/verify_live.py --domain sports
    python tools/verify_live.py --query "Chiefs game"

**Why this exists rather than a flag on `/api/health`.** §52 is the rule it
implements: four consecutive failures on a real machine all had the same shape,
a check that answered a cheaper question than the one being asked and then
reported OK. "A credential is set" is not "the credential is accepted"; "a
provider is registered" is not "the provider answers, in time, in a shape we
can read, with a timestamp we interpret correctly".

`diagnose()` is the cheap question and runs on every lookup, which is why it
must stay cheap. `verify()` is the real one: it resolves an entity and fetches
its state over the network. That belongs in a preflight and a development loop,
not at startup - a network call at boot turns a provider outage into a server
that will not start, which is a worse failure than the one it was guarding.

What a pass means
-----------------
Credentials work, the network is reachable, the provider responded, the
response parsed, and the status and timestamp came back in a shape this build
understands. What it does **not** mean is that the data is correct - nothing
here can check that, and a provider confidently serving last week's scores
would pass. Read the printed facts.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_facts  # noqa: E402
import live_sources  # noqa: E402
from config import settings  # noqa: E402


class _Brief:
    """The two fields `resolve` is contractually allowed to read."""

    def __init__(self, query: str, domain: str) -> None:
        self.query = query
        self.subject = query
        self.live_domain = domain


async def check(source, query: str) -> bool:
    print(f"\n  {source.name}  [{source.domain}]")

    ok, detail = source.diagnose()
    print(f"    configured   {'yes' if ok else 'NO'} - {detail}")
    if not ok:
        return False

    ok, detail = await source.verify()
    print(f"    verify()     {'PASS' if ok else 'FAIL'} - {detail}")

    brief = _Brief(query, source.domain)
    try:
        entity = await asyncio.wait_for(
            source.resolve(brief), timeout=settings.live_timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - this tool reports, never raises
        print(f"    resolve      FAILED - {type(exc).__name__}: {exc}")
        return False
    if entity is None:
        # Not a failure of the provider. It is a real answer, and the one the
        # writer is told about as "no matching entity" rather than as absence.
        print(f"    resolve      no entity for {query!r} - the provider does "
              "not cover this, which is not the same as it being broken")
        return ok

    print(f"    resolve      {entity.key}  ({entity.label})")

    try:
        facts = await asyncio.wait_for(
            source.fetch(entity), timeout=settings.live_timeout_seconds)
    except Exception as exc:  # noqa: BLE001
        print(f"    fetch        FAILED - {type(exc).__name__}: {exc}")
        return False
    if facts is None or not facts.facts:
        print("    fetch        nothing reported for this entity")
        return ok

    age = facts.age_seconds()
    limit = live_facts.MAX_AGE_SECONDS.get(source.domain, 0)
    fresh = facts.is_fresh()
    print(f"    status       {facts.status}"
          f"{'' if facts.status in live_facts.STATUSES else '  <- UNMAPPED'}")
    print(f"    as_of        {facts.as_of.isoformat()}  ({age:.0f}s old, "
          f"limit {limit:.0f}s) - {'FRESH' if fresh else 'STALE, would be withheld'}")
    if facts.delayed_seconds:
        print(f"    delayed      {facts.delayed_seconds:.0f}s by design - the "
              "prompt will say delayed, never current")
    print(f"    cost         ${source.cost_per_call:.4f} per fetch")
    print("    facts:")
    for line in facts.facts:
        print(f"      - {line}")

    # The two things most likely to be silently wrong in a new provider, and
    # the two the seam cannot catch for itself.
    problems = []
    if facts.status == live_facts.UNKNOWN:
        problems.append(
            "status came back UNKNOWN - either the provider did not say, or "
            "its vocabulary is not mapped in this source's `fetch`. Nothing "
            "downstream will speak a result while this is true.")
    if facts.as_of.tzinfo is None:
        problems.append("as_of is naive; it must be timezone-aware")
    if facts.as_of > datetime.now(timezone.utc):
        problems.append("as_of is in the future - a clock or a timezone is wrong")
    for problem in problems:
        print(f"    WARNING      {problem}")

    return ok and not problems


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default="",
                        help=f"one of: {', '.join(live_facts.LIVE_DOMAINS)}")
    parser.add_argument("--query", default="Chiefs game",
                        help="what to resolve, as a listener would type it")
    args = parser.parse_args()

    print(f"LIVE_FACTS={'1' if settings.live_facts else '0'}")
    installed = live_sources.install()
    for problem in installed["problems"]:
        print(f"  CONFIG ERROR  {problem}")
    print(f"  configured    {installed['configured']}")

    domains = [args.domain] if args.domain else list(live_facts.LIVE_DOMAINS)
    checked = 0
    passed = 0
    for domain in domains:
        for source in live_facts.sources_for(domain):
            ok, _ = source.diagnose()
            if not ok:
                continue
            checked += 1
            if await check(source, args.query):
                passed += 1

    print()
    if not checked:
        # The shipped state, and it is a state rather than a failure. Saying
        # "no live data" here must never be read as "there is no live data in
        # the world" - that distinction is the whole of this subsystem.
        print("No live provider is configured on this machine, so there was "
              "nothing to verify.")
        print("That is the shipped default. Live questions are answered from "
              "indexed articles and the writer is told plainly that no live "
              "feed exists - it does not invent a score.")
        print("Set LIVE_SPORTS_PROVIDER (see .env.example) to connect one.")
        return 0

    print(f"{passed}/{checked} provider(s) verified.")
    return 0 if passed == checked else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
