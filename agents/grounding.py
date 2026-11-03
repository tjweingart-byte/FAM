"""Mclovin's grounding checker: "does the source say this?" (§439, step 5; §437).

Code cannot judge whether a source supports a claim in other words - that
is the grounding checker's one question. It scores a sentence against the
packet sources most like it, 0 to 1; Mclovin's `support` check passes a
sentence at or above `SUPERVISOR_GROUNDING_HIGH`, fails it at or below
`SUPERVISOR_GROUNDING_LOW` when it names someone, a number or a date
(decided item 12: a settled fact with none of those may stay), and calls it
**unsure** in between. Only an unsure sentence goes to a small model
(`second_look`, `SUPERVISOR_MODEL`, Haiku 5.5) with the source sentences
beside it (decided item 26), and only while `support` is enforced; out of
time, it plays unchecked and is recorded (decided item 32).

**No checker is installed.** Which one, and where it runs, waits on the
owner (§437: HHEM-2.1-Open on a CPU, measured first; huggingface.co, where
its weights are, is refused by this environment). Until then
`SUPERVISOR_GROUNDING` is empty, `scorer()` is None, the `support` check is
not run at all, and nothing about any episode changes. A checker is plugged
in with `use(scorer)`, where `scorer(premise, hypothesis) -> float`; tests
use a fake one.
"""
from __future__ import annotations

import logging
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict

log = logging.getLogger("fam.agents")

Scorer = Callable[[str, str], float]

_SCORER: list = [None]
_WARNED: list = [False]


def use(scorer: Optional[Scorer]) -> None:
    """Plug a grounding checker in (or out, with None)."""
    _SCORER[0] = scorer


def scorer() -> Optional[Scorer]:
    """The checker in use, or None. `SUPERVISOR_GROUNDING` names one; none
    is built yet, so a named one that is not plugged in is said once."""
    if _SCORER[0] is not None:
        return _SCORER[0]
    from config import settings

    named = str(getattr(settings, "supervisor_grounding", "") or "").strip()
    if named and not _WARNED[0]:
        _WARNED[0] = True
        log.warning("SUPERVISOR_GROUNDING=%r: no grounding checker is installed yet "
                    "(§437, waiting on the owner); the support check does not run", named)
    return None


def score(premise: str, hypothesis: str) -> Optional[float]:
    """The checker's score, or None when there is none or it failed."""
    checker = scorer()
    if checker is None or not premise.strip():
        return None
    try:
        value = float(checker(premise, hypothesis))
        return min(1.0, max(0.0, value))
    except Exception:  # noqa: BLE001 - a checker that fails leaves the sentence unchecked
        log.warning("the grounding checker failed", exc_info=True)
        return None


class SecondLook(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supported: Literal["yes", "no", "unsure"]
    #: The source sentence that decides it, quoted, or "".
    source: str


SECOND_LOOK = """One sentence of an audio script, and the packet sources it was \
written from. Do the sources support what the sentence says? Answer "yes" \
only if a source sentence states it (in any words); "no" if no source \
states it or a source contradicts it; "unsure" if you cannot tell. Quote \
the source sentence that decides it. Nothing outside the sources is \
evidence."""


async def second_look(sentence: str, sources: str, *, ahead: bool = False) -> str:
    """"yes", "no", or "" (unsure, out of time, or failed - the sentence
    plays unchecked, recorded). One bounded call. Never raises."""
    try:
        from agents import models
        from config import settings

        request = models.AgentRequest(
            agent="supervisor", task="check a sentence", switch_setting="supervisor",
            model_setting="supervisor_model", rule_book="mclovin.md", schema=SecondLook,
            instructions=SECOND_LOOK,
            material=(("script", sentence), ("evidence", sources or "(no sources)")),
            timeout=float(settings.supervisor_timeout_seconds),
            max_tokens=int(settings.supervisor_max_tokens), effort="low",
            surface="ahead" if ahead else "search")
        result = await models.ask(request)
        if not result.ok or result.value.supported == "unsure":
            return ""
        return result.value.supported
    except Exception:  # noqa: BLE001
        log.warning("Mclovin's second look failed", exc_info=True)
        return ""
