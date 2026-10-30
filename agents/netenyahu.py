"""Netenyahu, the overlord - as code, never a model (§345).

docs/agent_rules/netenyahu.md: direct every other agent - when it works and
what it does - and know each one's purpose, abilities and limits. The
owner's choice (§341): no planner model; the rules in code.

* **Per episode** (`for_episode`), called by the pipeline as an episode
  starts: which agents may work on it. Code in microseconds, no I/O while
  `AGENTS=0`, and never anything in front of the first word. A private
  search or an attached file gets no agent at all (every-agent.md, "never"
  4). An agent works only if it is switched on, its rule books pass the
  lock, it serves this surface, and it is not held. Today nothing passes:
  every switch is off and every agent is held until the owner signs.
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
  only when `AGENTS=1`; with no agent built there is no handler, so a
  claimed task is handed back untouched.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import threading
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
        hold(role, reason)
        return reason
    except Exception:  # noqa: BLE001
        log.exception("netenyahu could not count a result")
        return None


def hold(role: str, reason: str) -> None:
    """Hold an agent and tell Keith (the log and `pipeline_errors` until he
    exists). Releasing it is the owner's act, never an agent's."""
    from agents import tasks

    tasks.store().hold(role, reason)
    log.warning("netenyahu held %s: %s", role, reason)
    try:
        import pipeline_errors

        pipeline_errors.record("agents", f"{role}: held", reason)
    except Exception:  # noqa: BLE001
        pass


def _held() -> dict:
    from agents import tasks

    if not tasks.exists():
        return {}
    return {role: reason for role, (reason, _at) in tasks.store().held().items()}


def reset() -> None:
    """Tests: forget the failure counts."""
    with _LOCK:
        _FAILURES.clear()


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


async def tick(now: Optional[datetime.datetime] = None,
               handlers: Optional[dict] = None) -> dict:
    """One round: claim and run at most `OVERLORD_MAX_TASKS_PER_TICK` due
    tasks. Returns what it did. Never raises."""
    if not settings.agents:
        return {"ran": 0, "why": "AGENTS=0"}
    if in_edition_window(now):
        return {"ran": 0, "why": "an edition is being written"}
    from agents import tasks

    handlers = HANDLERS if handlers is None else handlers
    held = _held()
    ran, skipped = 0, []
    try:
        for task in tasks.store().due(int(settings.overlord_max_tasks_per_tick)):
            agent, kind, _key = task.split(":", 2)
            handler: Optional[Callable] = handlers.get((agent, kind))
            if handler is None or agent in held:
                skipped.append(task)
                continue
            if not tasks.store().claim(task):
                continue
            try:
                await handler(task)
                tasks.store().finish(task)
                report(agent, "ok")
            except Exception as exc:  # noqa: BLE001 - one task never stops the loop
                tasks.store().fail(task, f"{type(exc).__name__}: {exc}")
                report(agent, "failed")
            ran += 1
    except Exception:  # noqa: BLE001
        log.exception("netenyahu's tick failed")
    return {"ran": ran, "skipped": skipped}


async def run_forever() -> None:
    """The background loop, started only when `AGENTS=1`."""
    while True:
        await tick()
        await asyncio.sleep(float(settings.overlord_tick_seconds))
