"""Credentials never reach a log line (§144).

GNews, Finnhub and others take their key as a query parameter, and httpx logs
every request URL at INFO. An `HTTPStatusError` carries the same URL in its
message, so a provider's 422 printed the key a second time, in a traceback.
On 24/09 that put the Finnhub token in plain text in Render's logs - GNews had
a filter, Finnhub did not, because the filter lived in `gnews.py` and covered
`apikey=` only.

Two halves, because a key reaches a log two ways:

* `Redact`, a logging filter installed on the httpx loggers at import, blanks
  every credential-shaped parameter in the request line.
* `redact(text)` is for anything that composes a message from a URL - an
  exception string - before it is logged or raised.
"""
from __future__ import annotations

import logging
import re

#: Query parameters that carry a credential in any provider FAM calls.
PATTERN = re.compile(
    r"((?:apikey|api_key|apiKey|token|access_token|key)=)[^&\s'\"]+",
    re.IGNORECASE)


def redact(text: str) -> str:
    return PATTERN.sub(r"\1[redacted]", text or "")


class Redact(logging.Filter):
    """Blank a key out of any log line that carries a URL with it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never lose a log line to a filter
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def install() -> None:
    """Idempotent. Import-time in every module that sends a keyed request."""
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, Redact) for f in logger.filters):
            logger.addFilter(Redact())


install()
