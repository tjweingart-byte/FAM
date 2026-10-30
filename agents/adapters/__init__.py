"""One adapter per model vendor, on that vendor's official SDK.

An adapter does one thing: send one request and hand back what came back -
the text, why it stopped, the model the answer says it came from, and the
usage block. Everything else (the time limit, consent, refusals, the schema,
metering) is `agents.models`', so it is written once.

Only `anthropic` exists. `google-genai` and the OpenAI-compatible adapter
come as their own changes, each with its package, its `metering.PRICES`
rows and its `spend_guard.PAID_CREDENTIALS` key (§342).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Answer:
    """What one vendor call returned."""

    text: str
    stop_reason: str
    #: The model the vendor says answered - checked against the one asked.
    served_model: str
    #: The vendor's usage object, as `metering.Usage.add_model_call` reads it.
    usage: object = None
    #: The vendor's whole response, for a refusal's details.
    raw: object = None


def adapter_for(vendor: str) -> Optional[object]:
    """The adapter for `vendor`, or None when none is built."""
    if vendor == "anthropic":
        from agents.adapters import anthropic_adapter as module

        return module.ADAPTER
    return None


#: Vendors with an adapter, for messages and tests.
BUILT = ("anthropic",)
