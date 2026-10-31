"""Netenyahu, the overlord - as code, never a model (§350).

docs/agent_rules/netenyahu.md: direct every other agent - when it works and
what it does - and know each one's purpose, abilities and limits. The
owner's choice (§346): no planner model; the rules in code.

* **Per episode** (`for_episode`), called by the pipeline as an episode
  starts: which agents may work on it. Code in microseconds, no I/O while
  `AGENTS=0`, and never anything in front of the first word. A private
  search or an attached file gets no agent at all (every-agent.md, "never"
  4). An agent works only if it is switched on, its rule books pass the
  lock, it serves this surface, and it is not held. Both the holds and the
  lock's disk check are copies refreshed in the background (§354), so
  deciding reads no database and no file while a listener waits. With the
  switches at their defaults nothing passes: every switch is off (the owner
  signed the rule books, §352; C3P0 and Nando are built, §354-§361).
* **The closed vocabulary** (`validate`): the only actions anything may
  ask of Netenyahu are `run(agent, task)`, `hold(agent, reason)` and
  `note(text)`, checked against the roster. There is no "change a rule",
  "change a setting", "raise a ceiling", "turn a dry run off" or "delete".
  An agent's output is data: free text from it never becomes an action.
* **Holding** (`report`): an agent that fails `HOLD_AFTER` times running is
  held, with why, and Keith is told (the log and `pipeline_errors` until
  Keith exists). Never for what it spent: a cost is not a failure.
* **The background** (`run_forever`, `tick`): every `OVERLORD_TICK_SECONDS`,
  at most `OVERLORD_MAX_TASKS_PER_TICK` claimed tasks, never during the
  05:00 and 17:00 Eastern edition writes, never for a held agent. Started
  only when `AGENTS=1`; no background agent is built yet, so there is no
  handler and a claimed task is handed back untouched.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from config import settings

log = logging.getLogger("fam.agents")

#: The agents that may work on an episode, in the order they start
#: (netenyahu.md, "always" 2). Consuela and Keith are background only.
EPISODE_AGENTS = ("contextualizer", "categorizer", "retriever", "editor", "supervisor")

#: Netenyahu's whole vocabulary.
ACTIONS = ("run", "hold", "note")

#: Failures in a row that hold an agent. A cost is never one.
HOLD_AFTER = 3

#: The edition writes background work keeps out of: Eastern time, and how
#: long after each start (netenyahu.md, "always" 4).
EDITION_HOURS = (5, 17)
EDITION_QUIET_MINUTES = 60


@dataclass(frozen=True)
class EpisodeRun:
    """Which agents may work on one episode, and why each other one may not."""

    agents: tuple = ()
    why_not: dict = field(default_factory=dict)

    def runs(self, role: str) -> bool:
        return role in self.agents


#: What every episode gets while `AGENTS=0`: nobody, decided before anything
#: is read.
OFF = EpisodeRun((), {"all": "AGENTS=0"})


def for_episode(surface: str, *, private: bool = False, attached: bool = False) -> EpisodeRun:
    """Which agents may work on this episode. Never raises, never waits.

    `surface` is who is waiting: `search` for any episode a listener is in
    front of, `ahead` for one written before the tap.
    """
    if not settings.agents:
        return OFF
    if private:
        return EpisodeRun((), {"all": "a private search: no agent sees it"})
    if attached:
        return EpisodeRun((), {"all": "an attached file: no agent sees it"})
    try:
        from agents import roster

        held = _held()
        if held is UNKNOWN:
            return EpisodeRun((), {"all": "the holds are not read yet"})
        runs, why = [], {}
        for role in EPISODE_AGENTS:
            agent = roster.get(role)
            if surface not in agent.surfaces:
                why[role] = f"not on {surface}"
                continue
            if role in held:
                why[role] = f"held: {held[role]}"
                continue
            state = roster.status(role)
            if not state["may_run"]:
                why[role] = state["why_not"]
                continue
            runs.append(role)
        return EpisodeRun(tuple(runs), why)
    except Exception as exc:  # noqa: BLE001 - routing never costs an episode
        log.exception("netenyahu could not route an episode")
        return EpisodeRun((), {"all": f"routing failed: {type(exc).__name__}"})


# ------------------------------------------------------------------ the vocabulary
def validate(action: dict) -> tuple:
    """`(True, "")` for an action from the closed vocabulary that the roster
    allows, else `(False, why)`. Anything that is not exactly an action -
    "set HOUSEKEEPER_DEDUPE_DRY_RUN=0", a ceiling, a rule - is refused."""
    if not isinstance(action, dict):
        return False, "not an action"
    name = action.get("action")
    if name not in ACTIONS:
        return False, f"{name!r} is not one of {', '.join(ACTIONS)}"
    if name == "note":
        extra = set(action) - {"action", "text"}
        return (not extra and isinstance(action.get("text"), str),
                "" if not extra else f"unexpected {sorted(extra)}")
    from agents import roster

    agent = action.get("agent")
    if agent not in roster.ROSTER:
        return False, f"no agent {agent!r}"
    if name == "hold":
        extra = set(action) - {"action", "agent", "reason"}
        if extra or not isinstance(action.get("reason"), str):
            return False, "a hold is an agent and a reason, nothing else"
        return True, ""
    extra = set(action) - {"action", "agent", "task"}
    if extra:
        return False, f"unexpected {sorted(extra)}"
    if action.get("task") not in roster.get(agent).tasks:
        return False, f"{agent} has no task {action.get('task')!r}"
    return True, ""


# ------------------------------------------------------------------ holding
_FAILURES: dict = {}
_LOCK = threading.Lock()
#: The statuses that count against an agent. Off, held, consent, staging,
#: no model and unpriced are not its failures, and a cost never is.
FAILING = frozenset({"timeout", "failed", "unreadable", "invalid", "wrong_model",
                     "out_of_room"})


def report(role: str, status: str) -> Optional[str]:
    """Count one result for `role`. Returns the reason when this result
    holds it. Never raises."""
    try:
        with _LOCK:
            if status == "ok":
                _FAILURES.pop(role, None)
                return None
            if status not in FAILING:
                return None
            _FAILURES[role] = count = _FAILURES.get(role, 0) + 1
        if count < HOLD_AFTER:
            return None
        reason = f"{count} failures in a row (last: {status})"
        if HOLD_OFF_LOOP:
            # `report` is called on the listener's path (§361): the copy
            # holds the agent at once, the store is written in the
            # background.
            _remember_hold(role, reason)
            threading.Thread(target=hold, args=(role, reason),
                             name="netenyahu-hold", daemon=True).start()
        else:
            hold(role, reason)
        return reason
    except Exception:  # noqa: BLE001
        log.exception("netenyahu could not count a result")
        return None


def hold(role: str, reason: str) -> None:
    """Hold an agent and tell Keith (the log and `pipeline_errors` until he
    exists). Releasing it is the owner's act, never an agent's."""
    from agents import tasks

    _remember_hold(role, reason)
    try:
        tasks.store().hold(role, reason)
    except Exception:  # noqa: BLE001 - the copy still holds it
        log.exception("netenyahu could not write the hold of %s", role)
    log.warning("netenyahu held %s: %s", role, reason)
    try:
        import pipeline_errors

        pipeline_errors.record("agents", f"{role}: held", reason)
    except Exception:  # noqa: BLE001
        pass


def _read_held() -> dict:
    """The holds as the task store has them. Reads the database: never on
    the request path (`_held` is)."""
    from agents import tasks

    if not tasks.exists():
        return {}
    return {role: reason for role, (reason, _at) in tasks.store().held().items()}


#: **The holds, kept off the request path** (§353's carry-over, §354).
#: `for_episode` runs as an episode starts, so it reads this copy and never
#: the database; a background thread re-reads it at most every
#: `HELD_REFRESH_SECONDS`, boot primes it (`prime`), and a hold made here is
#: written into it at once. Until it has been read once nobody knows what is
#: held, so no agent works (`UNKNOWN`): a missed agent costs one episode some
#: quality, a held agent that ran would break the hold.
HELD_REFRESH_SECONDS = 30.0
UNKNOWN = None
_HELD: dict = {"roles": UNKNOWN, "at": 0.0, "running": False}
_HELD_LOCK = threading.Lock()
#: Holds made in this process (§361): kept in the copy until a read of the
#: store shows them, so a refresh racing a hold's write - whenever it began
#: - can never drop it from the copy.
_MADE: dict = {}
#: Write a hold to the store off the listener's path (§361). Tests write it
#: inline (`tests/conftest.py`).
HOLD_OFF_LOOP = True


def _remember_hold(role: str, reason: str) -> None:
    with _HELD_LOCK:
        _MADE[role] = (reason, time.monotonic())
        if _HELD["roles"] is not UNKNOWN:
            _HELD["roles"] = dict(_HELD["roles"], **{role: reason})


def refresh_held() -> Optional[dict]:
    """Re-read the holds into the copy. Off the request path; never raises.
    A failed read keeps the last copy and waits a full interval before the
    next try, so a store that is down is not asked once per episode."""
    try:
        roles = dict(_read_held())
    except Exception:  # noqa: BLE001
        log.exception("netenyahu could not read the holds")
        with _HELD_LOCK:
            _HELD["running"] = False
            _HELD["at"] = time.monotonic()
        return _HELD["roles"]
    with _HELD_LOCK:
        now = time.monotonic()
        for role in [r for r, (_why, made) in _MADE.items()
                     if r in roles or now - made > 2 * HELD_REFRESH_SECONDS]:
            # The store has it now - or never will (a write that failed is
            # logged; the owner may have released it): not sticky (§363).
            del _MADE[role]
        for role, (reason, _made) in _MADE.items():
            roles.setdefault(role, reason)   # written yet? kept until it is
        _HELD.update(roles=roles, at=time.monotonic(), running=False)
    return dict(roles)


def prime() -> None:
    """Boot: read the holds (and the rule books' disk check) once, before
    any listener, so the first episode has an answer. Only when `AGENTS=1`."""
    if not settings.agents:
        return
    refresh_held()
    from agents import rulebooks

    rulebooks.load()
    rulebooks.refresh_disk()
    # Nando's vocabulary, built once here rather than on the first request
    # (§361).
    import category_vocabulary

    category_vocabulary.index()


def _refresh_held_soon() -> None:
    with _HELD_LOCK:
        # Once an interval, read or not: a store down since boot is not
        # asked once per episode (review, §361).
        if _HELD["running"] or (_HELD["at"] and
                                time.monotonic() - _HELD["at"] < HELD_REFRESH_SECONDS):
            return
        _HELD["running"] = True
    try:
        threading.Thread(target=refresh_held, name="netenyahu-holds", daemon=True).start()
    except Exception:  # noqa: BLE001
        with _HELD_LOCK:
            _HELD["running"] = False


def _held() -> Optional[dict]:
    """The holds from the copy - no database read - or `UNKNOWN` before the
    first read, which starts one in the background."""
    _refresh_held_soon()
    roles = _HELD["roles"]
    return None if roles is UNKNOWN else dict(roles)


def reset() -> None:
    """Tests: forget the failure counts and the copy of the holds."""
    with _LOCK:
        _FAILURES.clear()
    with _HELD_LOCK:
        _HELD.update(roles=UNKNOWN, at=0.0, running=False)
        _MADE.clear()


# ------------------------------------------------------------------ the background
#: (agent, kind) -> an async handler taking the task id. None is built: each
#: background agent registers its own in its phase.
HANDLERS: dict = {}


def in_edition_window(now: Optional[datetime.datetime] = None) -> bool:
    """Whether an edition is being written now (05:00 or 17:00 Eastern, for
    `EDITION_QUIET_MINUTES`)."""
    from zoneinfo import ZoneInfo

    now = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(
        ZoneInfo("America/New_York"))
    return any(now.hour == hour and now.minute < EDITION_QUIET_MINUTES
               for hour in EDITION_HOURS)


#: The loop's pace is clamped: a setting of 0 would spin on the event loop,
#: and NaN or infinity would stop it (§353).
TICK_FLOOR_SECONDS, TICK_CEILING_SECONDS = 5.0, 3600.0


async def tick(now: Optional[datetime.datetime] = None,
               handlers: Optional[dict] = None) -> dict:
    """One round: claim and run at most `OVERLORD_MAX_TASKS_PER_TICK` due
    tasks. Returns what it did. Never raises: a round that fails anywhere,
    the store's own reads included, is logged and the next round tries
    again (§353)."""
    try:
        return await _tick(now, handlers)
    except Exception:  # noqa: BLE001 - the loop outlives any one round
        log.exception("netenyahu's tick failed")
        return {"ran": 0, "why": "the round failed; the next one tries again"}


async def _tick(now, handlers) -> dict:
    if not settings.agents:
        return {"ran": 0, "why": "AGENTS=0"}
    if in_edition_window(now):
        return {"ran": 0, "why": "an edition is being written"}
    from agents import tasks

    handlers = HANDLERS if handlers is None else handlers
    held = refresh_held()
    if held is UNKNOWN:
        return {"ran": 0, "why": "the holds could not be read"}
    # Only tasks something here can run: a task for an agent not built yet,
    # or held, stays queued without taking a place in this round (§353).
    kinds = [f"{agent}:{kind}" for agent, kind in handlers if agent not in held]
    ran = 0
    for task in tasks.store().due(int(settings.overlord_max_tasks_per_tick), kinds=kinds):
        parts = task.split(":", 2)
        if len(parts) != 3:
            log.warning("netenyahu skipped a task id it cannot read: %r", task)
            continue
        agent, kind, _key = parts
        handler: Optional[Callable] = handlers.get((agent, kind))
        token = tasks.store().claim(task) if handler is not None else ""
        if not token:
            continue
        try:
            await handler(task)
            tasks.store().finish(task, token=token)
            report(agent, "ok")
        except Exception as exc:  # noqa: BLE001 - one task never stops the loop
            tasks.store().fail(task, f"{type(exc).__name__}: {exc}", token=token)
            report(agent, "failed")
        ran += 1
    return {"ran": ran}


def tick_seconds() -> float:
    try:
        seconds = float(settings.overlord_tick_seconds)
    except (TypeError, ValueError):
        seconds = TICK_FLOOR_SECONDS
    return min(TICK_CEILING_SECONDS, max(TICK_FLOOR_SECONDS, seconds))


async def run_forever() -> None:
    """The background loop, started only when `AGENTS=1`."""
    while True:
        await tick()
        await asyncio.sleep(tick_seconds())
