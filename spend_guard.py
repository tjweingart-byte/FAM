"""Zero spend: a deployment that cannot cost money, whatever it is configured with.

Staging exists to test merges before they reach the app people use (STAGING.md,
PROBLEMS.md §172), and the owner's condition for it is that **no part of the
pipeline spends anything** - no model tokens, no paid search, and not the
keyless sources either (GDELT, Polymarket), because those cost money at scale
too. An episode on staging is still made end to end - the same sentence
pipeline, pacing, streaming, cache and player - with the canned demo script as
the writer and the placeholder tone as the voice.

Two layers, because either one alone is decorative:

1. **Every paid credential is removed and every paid switch is forced off**,
   before `config.Settings` is built and before `FAM_SECRETS` is asked for
   anything (a secrets manager is not consulted at all). This is what makes
   the code *choose* its free path - `DEMO_MODE`, research that says it has no
   backend, an empty Trending that says why - rather than failing into it.
   The list is by hand, so it is the weaker layer; `tests/test_spend_guard.py`
   derives the credential-shaped variables from the source and fails when one
   is neither scrubbed here nor declared free in `NOT_SPEND`.

2. **Nothing leaves the machine.** `socket.socket.connect` refuses every
   address that is not loopback or a local socket, so a provider added next
   month, a keyless API nobody listed, or a key that arrives by a route this
   file never thought of still cannot make a call. This layer is not a list,
   which is why it is the guarantee (CLAUDE.md: a guard whose subject is
   enumerated by hand is decorative). A refused connection raises
   `ConnectionRefusedError` - an `OSError`, which every client here already
   treats as "that source is down" and falls back from - and is counted, so
   `/api/health` names each address something tried to reach.

On when it applies: `FAM_ENV=staging` turns it on **and it cannot be turned off
there** - a staging service with `ZERO_SPEND=0` in its dashboard is still zero
spend. `ZERO_SPEND=1` turns it on anywhere else (a laptop, a load test).
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading
from typing import Optional

log = logging.getLogger("fam.spend_guard")

#: The one environment that is always zero spend.
STAGING = "staging"

#: Credentials that buy something. Removed from the environment, with their
#: plural pool form (`credentials.pool` reads `NAME` and `NAMES`).
PAID_CREDENTIALS = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "EXA_API_KEY",
    "GNEWS_KEY",
    # Open-Meteo's paid plan (§194), for weather and place names.
    "OPEN_METEO_API_KEY",
    "API_SPORTS_KEY", "SPORTSDATAIO_KEY",
    "FINNHUB_KEY", "ALPHA_VANTAGE_KEY",
    "AP_ELECTIONS_KEY", "DDHQ_KEY",
    "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "RUNPOD_API_KEY", "RUNPOD_ENDPOINT_ID", "RUNPOD_POD", "RUNPOD_POD_ID",
    "REMOTE_VOICE_URL", "REMOTE_VOICE_TOKEN",
    # Not paid, but outbound to a third party that emails real people: a
    # staging signup must never register anybody with the vendor.
    "VIRAL_LOOPS_API_TOKEN",
    # Not paid per call, but outbound to a third party (error_tracking.py,
    # §202): staging's failures are read in its own logs.
    "SENTRY_DSN",
)

#: Credential-shaped names that buy nothing, so the derived test does not ask
#: for them to be scrubbed. Each says why.
NOT_SPEND = {
    # A switch, not a key: it is forced to "0" below instead.
    "CACHE_SEMANTIC_KEY": "a switch, forced off in FORCED",
    "CANONICAL_KEY_MODEL": "a model name, unused once CACHE_SEMANTIC_KEY is off",
    # Inbound secrets: they let something reach *this* server, never the reverse.
    "FAM_ADMIN_TOKEN": "inbound: who may open /admin on this server",
    "VOICE_REGISTRY_TOKEN": "inbound: who may register a voice worker here",
    # Push services charge nothing, and staging sends none (`push.status`).
    "VAPID_PRIVATE_KEY": "free: signs Web Push; push services do not bill",
    "VAPID_PUBLIC_KEY": "free: the public half of the Web Push key",
}

#: Switches that would reach a paid or metered service, and the value zero
#: spend holds them at. Forced over whatever the environment says.
FORCED = {
    # Keyless, and metered at scale all the same (the owner's ruling, §172).
    "GDELT": "0",
    "GDELT_CROSS_CHECK": "0",
    "STORIES_POLYMARKET": "0",
    "LIVE_SPORTS_PROVIDER": "",
    "LIVE_MARKETS_PROVIDER": "",
    "LIVE_ELECTIONS_PROVIDER": "",
    "TRENDING_SOURCE": "",
    # Local news feeds and weather (§194): keyless, and outbound all the same.
    "LOCAL_NEWS": "0",
    "WEATHER": "0",
    "OPEN_METEO_KEYLESS": "0",
    # Paid per picture.
    "THUMBNAILS": "0",
    # A model call in front of every request.
    "CACHE_SEMANTIC_KEY": "0",
    # Nothing to warm with: the writer is the canned script.
    "PREFETCH": "0",
    # The in-process engine: with no GPU and no model it is the placeholder
    # tone, and it never rents one.
    "VOICE_BACKEND": "chatterbox",
}

_STATE: dict = {"enabled": False, "reason": "", "scrubbed": [], "forced": {},
                "installed": False}
_BLOCKED: dict[str, int] = {}
_LOCK = threading.Lock()
_ORIGINAL_CONNECT = socket.socket.connect
_ORIGINAL_CONNECT_EX = socket.socket.connect_ex


def environment() -> str:
    """`FAM_ENV`, lower-cased: `staging`, `production`, or "" when unset."""
    return (os.environ.get("FAM_ENV") or "").strip().lower()


def wanted() -> tuple[bool, str]:
    """Whether this process must be zero spend, and the reason in words."""
    if environment() == STAGING:
        return True, "FAM_ENV=staging (always zero spend; ZERO_SPEND cannot turn it off)"
    if (os.environ.get("ZERO_SPEND") or "").strip() not in ("", "0", "false", "False"):
        return True, "ZERO_SPEND=1"
    return False, ""


def enabled() -> bool:
    return bool(_STATE["enabled"])


def scrub() -> list[str]:
    """Remove every paid credential and force every paid switch. Returns the
    names that were actually present, for the health report."""
    removed = []
    for name in PAID_CREDENTIALS:
        for variant in (name, name + "S"):
            if os.environ.pop(variant, None):
                removed.append(variant)
    forced = {}
    for name, value in FORCED.items():
        before = os.environ.get(name)
        os.environ[name] = value
        if before is not None and before != value:
            forced[name] = before
    _STATE["scrubbed"] = sorted(removed)
    _STATE["forced"] = forced
    return removed


def _is_local(address) -> bool:
    """Loopback or a local socket. Everything else is the outside world."""
    if isinstance(address, (str, bytes)):  # AF_UNIX path
        return True
    try:
        host = address[0]
    except (TypeError, IndexError):
        return False
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if host in ("localhost", ""):
        return True
    try:
        ip = ipaddress.ip_address(str(host).split("%", 1)[0])
    except ValueError:
        return False
    # `::ffff:127.0.0.1` is loopback reached over an IPv6 socket, and
    # `ipaddress` does not call it loopback by itself.
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or ip.is_unspecified or bool(mapped and mapped.is_loopback)


def _describe(address) -> str:
    try:
        return f"{address[0]}:{address[1]}"
    except (TypeError, IndexError):
        return str(address)


def _refuse(address) -> ConnectionRefusedError:
    where = _describe(address)
    with _LOCK:
        first = where not in _BLOCKED
        _BLOCKED[where] = _BLOCKED.get(where, 0) + 1
    if first:
        # Loud once per address, then counted: a log line per retry would bury
        # the one line that says what tried to leave.
        log.warning("zero spend: refused an outbound connection to %s", where)
    return ConnectionRefusedError(
        f"zero spend: this deployment makes no outbound connections ({where} refused)")


def _guarded_connect(self, address):
    if not _is_local(address):
        raise _refuse(address)
    return _ORIGINAL_CONNECT(self, address)


def _guarded_connect_ex(self, address):
    if not _is_local(address):
        _refuse(address)
        return 111  # ECONNREFUSED, which is what connect_ex reports
    return _ORIGINAL_CONNECT_EX(self, address)


def install_network_guard() -> None:
    """Refuse every non-local connection from this process. Idempotent."""
    if _STATE["installed"]:
        return
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    _STATE["installed"] = True


def uninstall_network_guard() -> None:
    """Tests only: put the socket back."""
    socket.socket.connect = _ORIGINAL_CONNECT
    socket.socket.connect_ex = _ORIGINAL_CONNECT_EX
    _STATE["installed"] = False


def apply() -> bool:
    """Called by config.py before anything reads a credential. True when on."""
    on, reason = wanted()
    _STATE["enabled"], _STATE["reason"] = on, reason
    if not on:
        return False
    scrub()
    install_network_guard()
    log.warning("zero spend is on (%s): no paid credential is read and nothing "
                "leaves this machine", reason)
    return True


def check_loop(loop) -> bool:
    """Whether the socket guard can see this event loop's connections.

    It patches Python's socket, so it sees the stdlib asyncio loop and every
    thread. **uvloop connects inside libuv and goes round it** - and uvicorn
    picks uvloop by itself when it is installed, before this module is
    imported. A zero-spend deployment therefore runs `UVICORN_LOOP=asyncio`
    (render.yaml sets it on the staging service); this is the check that the
    setting took, called by the app at startup, and `/api/health` says
    `network_guard: false` with the reason when it did not. The credentials are
    removed either way, so no token can be spent; what an uncovered loop loses
    is the backstop for a keyless source nobody listed.
    """
    kind = f"{type(loop).__module__}.{type(loop).__name__}"
    covered = type(loop).__module__.startswith("asyncio")
    _STATE["loop"] = kind
    _STATE["loop_covered"] = covered
    if enabled() and not covered:
        log.error("zero spend: the event loop is %s, which the network guard "
                  "cannot see. Start uvicorn with UVICORN_LOOP=asyncio.", kind)
    return covered


def reset(clear_blocked: bool = True) -> None:
    """Tests only."""
    uninstall_network_guard()
    _STATE.update(enabled=False, reason="", scrubbed=[], forced={})
    _STATE.pop("loop", None)
    _STATE.pop("loop_covered", None)
    if clear_blocked:
        _BLOCKED.clear()


def report() -> dict:
    """For `/api/health` and the web client's banner."""
    with _LOCK:
        blocked = dict(_BLOCKED)
    return {
        "name": environment() or "unset",
        "zero_spend": enabled(),
        "reason": _STATE["reason"],
        # In force only when the socket is patched *and* the event loop is one
        # whose connections go through it (`check_loop`). Unknown until the
        # app has started, so a script importing config reports the patch.
        "network_guard": bool(_STATE["installed"]
                              and _STATE.get("loop_covered", True)),
        "event_loop": _STATE.get("loop", "not started"),
        # Names only, never values: which credentials this deployment was
        # given and did not use - a key reaching staging is worth knowing about.
        "credentials_removed": list(_STATE["scrubbed"]),
        "switches_overridden": sorted(_STATE["forced"]),
        "blocked_connections": blocked,
    }

