"""Keith's fixes: a closed list, each reversible, each proven by a real check (§490).

keith.md "always" 2-4: fix a problem yourself only when the fix is on your
list of allowed fixes and the owner has switched that fix on; prove every
fix worked with a real check; write every fix into the fix report. "Never"
7: never try the same fix twice - if it fails, it goes to the owner.

**The list** (FAM_AGENTS.md, Keith's tab), each already a function in the
code:

* `reverify_voice` - for repeated voice errors: ask the voice ladder for a
  fresh, verified address now (`voice_control.current(force=True)`), which
  calls the worker. Verified when it answers. It starts and stops nothing.
* `rerun_job` - for a background loop that stopped with an error: start it
  again, once in the server's life (§495), if it is one the app registered as restartable
  (`incidents.RESTARTABLE`: the GDELT sync, local feeds, weather, the story
  sweep, the outbox drain, the audio sweep). Verified when it is still
  running a minute later.
* `hold_agent` - for an agent that broke its rule book (item 4's checks):
  ask Netenyahu to hold it (`netenyahu.hold`). Verified when Netenyahu's
  store, read back, holds it. Releasing a hold is the owner's act, never Keith's.

Not on it, and said: "hold a background sweep until a provider's limit
resets" - every rationed provider already stops itself at its own ceiling
(GNews, Tiingo, Wikimedia, X, the image model), so a hold would repeat it;
a provider near its quota is an incident instead (`incidents.from_quotas`).

**Observe only until the owner switches each on** (`IT_FIXES`, a list of the
names above; empty by default): until then each fix that would apply is
written to the report as `would`, once per incident, so the owner can watch
what Keith would have done. One attempt per fix per incident (per time it
opened); a fix that fails is written into the incident's plan ("Keith tried
... it did not work") and stays the owner's. Never raises.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import incidents as INC

log = logging.getLogger("keith_fixes")

#: How long a re-run loop must keep running to count as fixed.
RERUN_VERIFY_SECONDS = 60.0

#: Loops started again in this process: cause -> (task, started).
RERUNS: dict = {}
#: Loops already started again once in this process (§495): never twice,
#: however often the incident closes and reopens.
RERUN_ONCE: set = set()
#: Restarted loops, held so they are not collected (asyncio keeps only a
#: weak reference) and cancelled with the server's own loops at shutdown.
RUNNING: set = set()


def enabled() -> set:
    from config import settings

    return {name.strip() for name in (getattr(settings, "it_fixes", "") or "").split(",")
            if name.strip() in FIXES}


# ------------------------------------------------------------------ the fixes
def _voice_applies(incident: dict) -> bool:
    return incident["cause"].startswith("errors:voice:")


async def _voice_run(incident: dict) -> tuple[str, str]:
    import voice_control

    try:
        endpoint = await asyncio.wait_for(voice_control.current(force=True), 60)
    except Exception as exc:  # noqa: BLE001 - a failed fix is an outcome
        return "failed", f"the voice ladder found no worker that answers ({type(exc).__name__})"
    label = " ".join(x for x in (getattr(endpoint, "rung", ""),
                                 getattr(endpoint, "url", "")) if x) or "a worker"
    return "verified", f"the voice ladder re-verified and a worker answered ({label})"


def _loop_name(incident: dict) -> str:
    return incident["cause"].split(":", 1)[1] if incident["cause"].startswith("loop:") else ""


def _rerun_applies(incident: dict) -> bool:
    name = _loop_name(incident)
    return name in INC.RESTARTABLE and name not in RERUN_ONCE


async def _rerun_run(incident: dict) -> tuple[str, str]:
    name = _loop_name(incident)
    try:
        task = asyncio.get_running_loop().create_task(INC.RESTARTABLE[name]())
    except Exception as exc:  # noqa: BLE001
        return "failed", f"{name} could not be started again ({type(exc).__name__})"
    INC.watch_task(task)
    RERUN_ONCE.add(name)
    RUNNING.add(task)
    task.add_done_callback(RUNNING.discard)
    # The incident stays open (its loop still listed) until the check a
    # minute on: closed then, it never reopens into a second attempt (§495).
    RERUNS[incident["cause"]] = (task, time.time())
    return "pending", (f"{name} started again; checked in "
                       f"{int(RERUN_VERIFY_SECONDS)} s that it is still running")


def _hold_applies(incident: dict) -> bool:
    return incident["cause"].startswith("breach:") and bool(
        (incident.get("detail") or {}).get("role"))


async def _hold_run(incident: dict) -> tuple[str, str]:
    from agents import netenyahu

    role = incident["detail"]["role"]
    await asyncio.to_thread(netenyahu.hold, role, f"Keith: {incident['title']}"[:200])
    try:
        held = await asyncio.to_thread(netenyahu._read_held)   # the store, read back
    except Exception as exc:  # noqa: BLE001
        return "failed", f"asked Netenyahu to hold {role}; could not read it back ({type(exc).__name__})"
    if role in held:
        return "verified", f"Netenyahu holds {role}; releasing it is yours"
    return "failed", f"asked Netenyahu to hold {role}, and the roster does not show it held"


#: The closed list: name -> (applies, run, what it would do in words).
FIXES = {
    "reverify_voice": (_voice_applies, _voice_run,
                       "re-verify the voice ladder now"),
    "rerun_job": (_rerun_applies, _rerun_run,
                  "start the stopped loop again, once, and check it keeps running"),
    "hold_agent": (_hold_applies, _hold_run,
                   "ask Netenyahu to hold the agent"),
}


# --------------------------------------------------------------- the round
async def act(now: Optional[float] = None) -> list:
    """After a watch round: settle pending re-runs, then for each open
    incident run (or, while off, record) each fix that applies and was not
    tried on this opening. Returns what was recorded. Never raises."""
    now = time.time() if now is None else now
    done = []
    # The store's reads and writes go to a thread (§495): this runs on the
    # event loop, and a fix itself may start a task there.
    off = asyncio.to_thread
    try:
        store = await off(INC.store)
        done.extend(await off(_settle_reruns, store, now))
        on = enabled()
        for incident in await off(store.open_incidents):
            for name, (applies, run, words) in FIXES.items():
                try:
                    if not applies(incident):
                        continue
                    tried = await off(store.tried, incident["cause"],
                                      incident["opened_at"], name)
                    if name not in on:
                        if not tried:
                            await off(store.add_fix, incident["cause"],
                                      incident["opened_at"], name, "observe", "would",
                                      f"would {words}", now)
                            done.append((incident["cause"], name, "would"))
                        continue
                    if any(t != "would" for t in tried):
                        continue                         # one attempt: never twice
                    outcome, how = await run(incident)
                    if outcome == "pending" and incident["cause"] in RERUNS:
                        # Timed on the round's own clock, as it is checked.
                        RERUNS[incident["cause"]] = (RERUNS[incident["cause"]][0], now)
                    await off(store.add_fix, incident["cause"], incident["opened_at"],
                              name, "act", outcome, how, now)
                    done.append((incident["cause"], name, outcome))
                    await off(_tell_the_plan, store, incident["cause"], name, outcome,
                              how, now)
                except Exception as exc:  # noqa: BLE001 - one fix never stops the rest
                    log.warning("fix %s on %s failed to run: %s", name,
                                incident["cause"], exc)
    except Exception as exc:  # noqa: BLE001
        log.warning("keith's fixes: the round failed: %s", exc)
    return done


def _settle_reruns(store, now: float) -> list:
    out = []
    for cause, (task, started) in list(RERUNS.items()):
        if now - started < RERUN_VERIFY_SECONDS and not task.done():
            continue
        RERUNS.pop(cause, None)
        row = store.get(cause)
        opened = row["opened_at"] if row else started
        if task.done():
            how = "it stopped again within a minute; this is yours"
            outcome = "failed"
        else:
            how = f"still running {int(now - started)} s after it was started again"
            outcome = "verified"
            # Running again: the incident closes on the next round. If it
            # stops later it reopens, and is the owner's - never re-run twice.
            name = cause.split(":", 1)[1]
            with INC._LOOPS_LOCK:
                INC.LOOPS.pop(name, None)
        store.add_fix(cause, opened, "rerun_job", "act", outcome, how, now)
        _tell_the_plan(store, cause, "rerun_job", outcome, how, now)
        out.append((cause, "rerun_job", outcome))
    return out


def _tell_the_plan(store, cause: str, fix: str, outcome: str, how: str,
                   now: float) -> None:
    at = time.strftime("%H:%M UTC", time.gmtime(now))
    if outcome == "failed":
        text = (f"Keith tried {fix} at {at}: {how}. It did not work, and he "
                "does not try it twice - this is yours.")
    elif outcome == "pending":
        text = f"Keith tried {fix} at {at}: {how}."
    else:
        text = f"Keith tried {fix} at {at}: {how} (verified)."
    store.set_tried(cause, text)


def reset() -> None:
    """For tests: no re-runs pending, made or held in this process."""
    RERUNS.clear()
    RERUN_ONCE.clear()
    RUNNING.clear()


def report(days: float = 7.0, now: Optional[float] = None) -> dict:
    """The fix report, answerable at any moment (keith.md "always" 4): every
    fix and every `would` in `days`, newest first, and which fixes are on.
    Never raises, never creates the store."""
    now = time.time() if now is None else now
    try:
        rows = INC.store().fixes_since(now - days * 86400) if INC.exists() else []
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read the fix report: %s", exc)
        rows = []
    return {"enabled": sorted(enabled()), "available": sorted(FIXES),
            "fixes": rows}
