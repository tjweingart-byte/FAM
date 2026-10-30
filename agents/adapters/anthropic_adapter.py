"""The Anthropic adapter: one Messages API call on the official SDK.

The same call the brief makes (`episode_intelligence._ask`): the rule books
and instructions as one cacheable system block, the material as the user
turn, and the answer constrained to the request's JSON schema through
`output_config.format`. The client is `anthropic_client.build_async_client`,
so there is still one HTTP configuration in the project.

The SDK's own retries are turned off for these calls: an agent makes at
most one call per task plus one retry, and that retry is Netenyahu's to
make (the Cost rules, docs/FAM_AGENTS.md), not a hidden second or third
request inside the time limit.
"""
from __future__ import annotations

import logging
from typing import Optional

from agents.adapters import Answer

log = logging.getLogger("fam.agents")


class AnthropicAdapter:
    vendor = "anthropic"

    def __init__(self) -> None:
        self._client = None

    def client(self):
        """The shared client, built on first use with the key in force."""
        if self._client is None:
            import credentials
            from anthropic_client import build_async_client

            self._client = build_async_client(
                credentials.active("ANTHROPIC_API_KEY") or None)
        return self._client

    async def send(self, *, model: str, system: str, user: str, schema: dict,
                   max_tokens: int, effort: str, client: Optional[object] = None
                   ) -> Answer:
        """One call. Raises whatever the SDK raises; `agents.models.ask`
        turns every exception into a fallback."""
        client = client or self.client()
        with_options = getattr(client, "with_options", None)
        if callable(with_options):
            client = with_options(max_retries=0)
        response = await client.messages.create(
            model=model,
            max_tokens=int(max_tokens),
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": effort,
                           "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": user}],
        )
        text = ""
        for block in getattr(response, "content", None) or ():
            if getattr(block, "type", "") == "text":
                text = str(getattr(block, "text", "") or "")
                break
        return Answer(text=text,
                      stop_reason=str(getattr(response, "stop_reason", "") or ""),
                      served_model=str(getattr(response, "model", "") or ""),
                      usage=getattr(response, "usage", None),
                      raw=response)


ADAPTER = AnthropicAdapter()
