"""The lock on each agent's rule book (docs/FAM_AGENTS.md, Shared rules,
Locked rule books).

**Not built yet.** The lock - a manifest of SHA-256 fingerprints signed with
the owner's Ed25519 key, checked at boot and before every agent call - is
its own change. Until it exists this module answers the only safe thing:
every rule book is unchecked, so every agent is held. `agents.models.ask`
asks `check` before every call, so the change that builds the lock replaces
this answer and nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass

NOT_BUILT = "the rule-book lock is not built yet, so no rule book is checked"


@dataclass(frozen=True)
class Lock:
    """Whether one agent may run under its rule books, and which version."""

    ok: bool
    reason: str = ""
    #: The signed manifest's version, and the agent's own book's SHA-256.
    version: str = ""
    sha: str = ""
    #: The rule books' text, as the prompt's cached block carries it.
    text: str = ""
    #: The file that differs, when one does.
    file: str = ""


def check(book: str) -> Lock:
    """The lock for an agent whose rule book is `book`. Never raises."""
    return Lock(ok=False, reason=NOT_BUILT)


def report() -> dict:
    """What `/api/health` and `/admin` say about the lock."""
    return {"built": False, "signed": False, "held": "every agent",
            "reason": NOT_BUILT}
