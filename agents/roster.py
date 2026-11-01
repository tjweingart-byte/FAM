"""The roster: every agent, what it is for, and its limits (§349).

Netenyahu's first responsibility (docs/agent_rules/netenyahu.md, "know
every agent"): each agent's purpose, tasks, surfaces, pipeline stage,
ceilings, fallback and rule book, in one place that code, `/admin` and the
tests read. It is data about the agents, never a way to change them: an
agent's rules are its rule book's, its switch and model are the owner's
settings, and nothing here can raise a ceiling.

The ceilings name `config.py` settings rather than holding numbers, so a
ceiling is set in one place. C3P0 and Nando work inside the brief's call
(§346), so theirs are the brief's own (`EI_*`).
"""
from __future__ import annotations

from dataclasses import dataclass

from agents import names
from config import settings

#: Who is waiting when an agent works.
SEARCH, AHEAD, BACKGROUND = "search", "ahead", "background"


@dataclass(frozen=True)
class Agent:
    role: str
    #: The owner's name for it - internal, never shown to a listener.
    name: str
    purpose: str
    tasks: tuple
    #: `SEARCH` (a listener in front of the first word), `AHEAD` (written
    #: before a tap), `BACKGROUND` (nobody waiting).
    surfaces: tuple
    #: Its boxes on the guide's pipeline drawing (`<!-- stages: -->`).
    stage: str
    rule_book: str
    switch: str
    models: tuple
    #: `(surface, timeout setting, call-size setting)`; a setting of ""
    #: means the agent makes no model call there (code only).
    ceilings: tuple
    #: What runs when it fails, is late, or is off.
    fallback: str
    #: May any of its material be a listener's own words?
    sees_listener_words: bool
    #: Model calls per task, and retries, at most (the Cost rules).
    calls_per_task: int = 1
    retries: int = 1

    def ceiling(self, surface: str) -> tuple:
        """`(timeout seconds, max tokens)` for `surface`, read from the
        settings now; `(None, None)` where the agent makes no call there."""
        for where, timeout, size in self.ceilings:
            if where == surface:
                if not timeout:
                    return None, None
                return (float(getattr(settings, timeout)),
                        int(getattr(settings, size)) if size else None)
        return None, None


def _agent(role, purpose, tasks, surfaces, stage, ceilings, fallback, words):
    name, book, switch, models = names.AGENTS[role]
    return Agent(role=role, name=name, purpose=purpose, tasks=tuple(tasks),
                 surfaces=tuple(surfaces), stage=stage, rule_book=book,
                 switch=switch, models=models, ceilings=tuple(ceilings),
                 fallback=fallback, sees_listener_words=words)


ROSTER: dict = {a.role: a for a in (
    _agent("overlord",
           "Directs every agent: when it works and what it does; knows each one's limits",
           ("route an episode", "schedule background work", "hold an agent"),
           (SEARCH, AHEAD, BACKGROUND), "orchestrate",
           ((BACKGROUND, "", ""),),
           "Code alone: with no agent on, every step runs as it did before the agents",
           False),
    _agent("contextualizer",
           "With the brief: which thing a name means, which plugins and what to ask "
           "them, what Sonic fetches, the title",
           ("contextualise", "retitle"), (SEARCH, AHEAD), "context",
           ((SEARCH, "ei_timeout_seconds", "ei_max_tokens"),
            (AHEAD, "ei_ahead_timeout_seconds", "ei_max_tokens")),
           "The brief's own plan, as today", True),
    _agent("categorizer",
           "Pins each request to one node of the category tree, and confirms it "
           "after the writer",
           ("place", "confirm", "follow", "judge", "train", "re-file", "draft vocabulary", "draft entities"),
           (SEARCH, AHEAD, BACKGROUND), "categorize",
           ((SEARCH, "categorizer_timeout_seconds", "categorizer_max_tokens"),
            (AHEAD, "categorizer_timeout_seconds", "categorizer_max_tokens"),
            (BACKGROUND, "categorizer_judge_timeout_seconds",
             "categorizer_judge_max_tokens")),
           "The tree's code placement (`resolve_category`), as today", True),
    _agent("retriever",
           "Reads MongoDB only and brings back one date-ordered history packet",
           ("recall",), (SEARCH, AHEAD), "retrieve",
           ((SEARCH, "", ""), (AHEAD, "", "")),
           "`history_archive.look`, as today; late means the episode goes without history",
           True),
    _agent("editor",
           "With the writer: summarises both packets by sentence number, outlines the "
           "story, shapes its open, flow and close",
           ("outline",), (SEARCH, AHEAD), "editor",
           ((SEARCH, "editor_timeout_seconds", "editor_max_tokens"),
            (AHEAD, "editor_ahead_timeout_seconds", "editor_max_tokens")),
           "The writer reads the packets as it does today", True),
    _agent("supervisor",
           "Screens sources and checks every sentence against the two packets as it "
           "is written",
           ("screen", "check a sentence", "check the script"),
           (SEARCH, AHEAD), "check",
           ((SEARCH, "supervisor_timeout_seconds", "supervisor_max_tokens"),
            (AHEAD, "supervisor_timeout_seconds", "supervisor_max_tokens")),
           "Today's code guards; a check out of time plays its sentence unchecked "
           "and the miss is recorded", True),
    _agent("housekeeper",
           "MongoDB's only writer: consolidates each packet as it enters, files it by "
           "node, dedupes, consolidates again at 90 days",
           ("consolidate", "dedupe", "re-file", "comb"), (BACKGROUND,), "housemaid",
           ((BACKGROUND, "housekeeper_timeout_seconds", "housekeeper_max_tokens"),),
           "Today's archive intake, unchanged; dry runs report and change nothing",
           False),
    _agent("it",
           "Watches errors, usage and agents; fixes only from a closed list; owns "
           "/admin's content",
           ("watch", "write up an incident", "summarise"), (BACKGROUND,), "monitor",
           ((BACKGROUND, "it_timeout_seconds", "it_max_tokens"),),
           "/admin as it is today; nothing is fixed", False),
)}


def get(role: str) -> Agent:
    return ROSTER[role]


def status(role: str) -> dict:
    """Whether an agent may run now, and why not: its switches and its lock.
    Reads settings and the lock only; never raises."""
    from agents import rulebooks

    agent = ROSTER[role]
    on = bool(settings.agents) and bool(getattr(settings, agent.switch, False))
    lock = rulebooks.check(agent.rule_book)
    why = ("" if on and lock.ok else
           "AGENTS=0" if not settings.agents else
           f"{agent.switch.upper()}=0" if not on else lock.reason)
    return {"role": role, "name": agent.name, "on": on, "may_run": on and lock.ok,
            "why_not": why, "rule_book": agent.rule_book,
            "rulebook_version": lock.version, "held_file": lock.file}
