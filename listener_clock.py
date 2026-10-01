"""Whose clock an episode is written on.

The server runs in UTC, and everything that told a model what time it is
read the server's clock: `now_line()` said "Thursday 2 October at 04:00 UTC"
to somebody in California at 9pm on the Wednesday, so a Red Sox game that
ended thirty minutes earlier was written up as last night's (the owner's
10.1 packet). The model was not wrong; it was told the wrong day.

**The listener's zone comes from their device, never their location.** A
browser knows its IANA zone (`Intl.DateTimeFormat().resolvedOptions()`) with
no permission prompt, and so does an iPhone; the app sends it on every
request as `X-FAM-TZ`, and Settings can pin a different one. Nothing here
reads a place.

**Set per request, read where the time is said.** The session middleware
sets it from the header (`set_for_request`); the prompt's "It is currently",
EI's `now`, the packet's "yesterday" and a live fact's "observed at" read it
(`now`, `now_line`). Work that belongs to nobody - prefetch, the Trending and
DailyFAM editions - runs on `server()`, the edition's zone, so a listener's
clock never leaks into an episode written for everybody.

**Not in the cache key, on purpose.** The zone is part of *when* an episode
was written, the way the moment itself is, and the moment is not in the key
either: what bounds a stale "tonight" is the volatile window (`ttl_for`, two
hours), and splitting every key by zone would also stop Explore and a shared
link finding the episode somebody else made.
"""
from __future__ import annotations

import contextlib
import contextvars
from datetime import datetime, timezone

#: The IANA name for this request's listener, or "" for the edition's zone.
_ZONE: contextvars.ContextVar[str] = contextvars.ContextVar("fam_listener_zone",
                                                            default="")

#: The request header a client names its zone in.
HEADER = "X-FAM-TZ"


def clean(value) -> str:
    """An IANA zone name the server can load, or ''."""
    text = str(value or "").strip()[:64]
    if not text:
        return ""
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(text)
    except Exception:  # noqa: BLE001 - an unreadable zone is simply not used
        return ""
    return text


def set_for_request(value) -> None:
    """Name this request's zone (the header's value). Unreadable means none."""
    _ZONE.set(clean(value))


def current() -> str:
    """The zone in force, by name, or '' when it is the edition's."""
    return _ZONE.get()


@contextlib.contextmanager
def using(value):
    """Run a block on this zone ('' for the edition's), then put it back."""
    token = _ZONE.set(clean(value))
    try:
        yield
    finally:
        _ZONE.reset(token)


def server():
    """Run a block on nobody's clock: the edition's zone. For prefetch and the
    editions, which a request may start but which are written for everybody."""
    return using("")


def zone():
    """The zone in force as a tzinfo: the listener's, else the edition's."""
    name = _ZONE.get()
    if name:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 - cleaned on the way in; belt and braces
            pass
    import daily_edition

    return daily_edition.zone()


def now() -> datetime:
    """This moment, on the clock in force."""
    return datetime.now(timezone.utc).astimezone(zone())


def say(when: datetime, fmt: str = "%A %d %B %Y at %H:%M %Z") -> str:
    """A moment as the episode would put it, on the clock in force."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(zone()).strftime(fmt).replace(" 0", " ")


def now_line() -> str:
    """'Wednesday 1 October 2026 at 21:00 PDT' - what the writer is told."""
    return say(datetime.now(timezone.utc))
