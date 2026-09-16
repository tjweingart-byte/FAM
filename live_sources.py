"""Live providers, and the contract a real one has to meet.

`live_facts.py` is the seam - routing, freshness, caching, failure semantics.
This is where actual providers live, and where configuration turns them on.

Why they are separate
---------------------
The seam has no vendor in it and must keep none. A provider is a credential, a
wire format and a set of quirks about what "in progress" is called this week;
the seam is the rule that a score too old to be current is withheld. Mixing
them means the rule acquires a vendor's exceptions, and this project has
already learned once what a fallback buried in a provider costs (§61, §51).

What a provider must supply
---------------------------
Two methods, and they answer different questions:

* **`resolve(brief) -> Entity | None`** - *which* thing is this about, per the
  provider's own catalogue. Never an identifier a model produced: a
  hallucinated game id or ticker does not fail, it returns somebody else's
  state, fresh and authoritative and wrong, and nothing downstream can catch
  it. A provider with a search endpoint uses it; one without needs a mapping
  it can defend.
* **`fetch(entity) -> LiveFacts | None`** - what is true about it *now*.

And it declares three things about itself: `cost_per_call`, so §73's rule that
spend is recorded when it is spent can be kept; `delayed_seconds`, because a
fifteen-minute-delayed feed described as current is the failure this module
exists to prevent; and a `verify()` that makes a real request, because §52 says
a readiness check that only confirms configuration is not a readiness check.

What a sports provider should return, where it has it
-----------------------------------------------------
Entity id, the teams, the scheduled start, the status, the score, the period,
the clock, the relevant current statistics, the final score when final, and the
timestamp the provider observed all that. **Fields it does not have are
omitted, never filled in.** `facts` is a list of short spoken sentences because
that is what reaches a voice - no scorelines in a shape nobody says aloud, no
abbreviations, no hostnames.

Nothing real is configured here
-------------------------------
`fake` is a deterministic stand-in for tests, `./demo.sh` and the eval suite.
It is selected explicitly or not at all - never a fallback - which is §51 and
§61 applied to facts: a stand-in that can be reached by accident is a stand-in
that reaches a listener. It says what it is in its own source name, so an
episode built on it is traceable to it from the log and `/api/health`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import live_facts
from config import settings
from live_facts import Entity, LiveFacts, LiveSource

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# The fake, for tests and demos
# --------------------------------------------------------------------------
class FakeSportsSource(LiveSource):
    """A scoreboard with no wire behind it. Selected explicitly, never fallen to.

    Its whole job is to let the in-progress path be exercised end to end -
    by tests, by `tools/ei_eval.py --only in-progress`, and by `./demo.sh` -
    without anybody needing a credential. It reports a game that is under way,
    so the state that produced §88 is the state it is easiest to reproduce.

    **It is not production data and never becomes it.** The name it reports
    says so, and that name reaches the prompt, the log and `/api/health`.
    """

    name = "fake scoreboard (NOT REAL DATA)"
    domain = "sports"
    cost_per_call = 0.0
    delayed_seconds = 0.0

    #: What it pretends is happening. Settable so a test can walk one entity
    #: through scheduled -> in_progress -> final without three providers.
    def __init__(self, status: str = live_facts.IN_PROGRESS,
                 label: str = "Kansas City Chiefs at Denver Broncos") -> None:
        self.status = live_facts.normalise_status(status)
        self.label = label
        self.calls = 0

    def diagnose(self) -> tuple[bool, str]:
        return True, ("the fake scoreboard is serving; it is a stand-in and "
                      "reports invented state, never real scores")

    async def verify(self) -> tuple[bool, str]:
        return True, "the fake scoreboard needs nothing and proves nothing"

    async def resolve(self, brief) -> Optional[Entity]:
        subject = (getattr(brief, "subject", "") or getattr(brief, "query", "")).lower()
        if not any(word in subject for word in ("chief", "bronco", "game", "match")):
            return None
        return Entity(domain="sports", provider=self.name, id="fake-game-1",
                      label=self.label,
                      starts_at=datetime.now(timezone.utc) - timedelta(hours=1))

    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        self.calls += 1
        now = datetime.now(timezone.utc)
        if self.status == live_facts.SCHEDULED:
            said = ["The Chiefs and the Broncos kick off tonight at seven thirty "
                    "Central.",
                    "Neither side has played a snap yet."]
        elif self.status == live_facts.FINAL:
            said = ["The Chiefs beat the Broncos twenty-seven to sixteen.",
                    "The game is over."]
        else:
            said = ["The Chiefs lead the Broncos twenty-one to seven.",
                    "There are about ten minutes left in the third quarter.",
                    "Patrick Mahomes has thrown for a hundred and eighty yards."]
        return LiveFacts(domain="sports", source=self.name, as_of=now,
                         facts=said, status=self.status, entity=entity)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
#: Provider name -> how to build it. A name not in here is a configuration
#: error and is reported as one rather than silently serving nothing.
BUILDERS = {
    "sports": {
        "fake": lambda: FakeSportsSource(status=settings.live_fake_sports_status),
    },
    "markets": {},
    "elections": {},
}


def configured() -> dict:
    """Which provider each domain is set to. Empty string means none."""
    return {
        "sports": settings.live_sports_provider,
        "markets": settings.live_markets_provider,
        "elections": settings.live_elections_provider,
    }


def install() -> dict:
    """Build and register every configured provider. Returns what happened.

    Re-runnable, like `prefetch_sources.install`: every test that opens a
    TestClient runs startup again, and a duplicate-registration guard that took
    the server down at boot would be a worse bug than the one it prevents. So
    it unregisters its own first and leaves everything else alone.

    A name nobody can build is an error that is *reported*, not raised. A
    deployment with a typo in `LIVE_SPORTS_PROVIDER` should start and say
    loudly that it has no scores, rather than fail to start - the episodes are
    still answerable, and the honest "no live feed" block is what the writer
    gets. The reverse - starting quietly with no provider and a config that
    says otherwise - is the failure this whole file exists to avoid.
    """
    installed: list = []
    problems: list = []

    for domain, name in configured().items():
        for source in list(live_facts.sources_for(domain)):
            if getattr(source, "_fam_installed", False):
                live_facts.unregister(source.name)
        if not name:
            continue
        builder = BUILDERS.get(domain, {}).get(name)
        if builder is None:
            problems.append(
                f"LIVE_{domain.upper()}_PROVIDER={name!r} is not a provider "
                f"this build knows. Known: "
                f"{', '.join(sorted(BUILDERS.get(domain, {}))) or 'none'}. "
                f"No live {domain} data will be available.")
            continue
        try:
            source = builder()
        except Exception as exc:  # noqa: BLE001 - a bad provider must not stop boot
            problems.append(f"{domain} provider {name!r} could not be built: {exc}")
            continue
        source._fam_installed = True  # noqa: SLF001 - our own marker, see above
        live_facts.register(source)
        installed.append(source.name)

    for problem in problems:
        log.error("live facts: %s", problem)
    if installed:
        log.info("live facts: installed %s", ", ".join(installed))
    else:
        log.info("live facts: no provider configured; live questions will be "
                 "answered from indexed articles and will say so")
    return {"installed": installed, "problems": problems,
            "configured": configured()}


def report() -> dict:
    """What configuration asked for, beside what is actually registered.

    Both, because they come apart: a name with a typo is configured and not
    registered, and that difference is invisible from a source list alone.
    """
    registered = {d: [s.name for s in live_facts.sources_for(d)
                      if getattr(s, "_fam_installed", False)]
                  for d in live_facts.LIVE_DOMAINS}
    return {"configured": configured(), "registered": registered,
            "known": {d: sorted(v) for d, v in BUILDERS.items()}}
