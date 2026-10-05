"""Errors reach a person before a listener reports them (§202).

Every failure path in FAM logs, and a log on Render is read by nobody until
somebody already knows something is wrong. The edition writers, prefetch, the
story sweeps and the voice supervisor all run in the background and all fail
with `log.exception` - correct, and until now silent from outside. This sends
those, and every unhandled request error, to Sentry.

Three rules, each the shape of one this project already keeps:

* **Off unless asked, and it says which.** `SENTRY_DSN` unset, the package
  missing, or zero spend on - each is reported by `report()` on
  `/api/health` with its reason, rather than looking identical from outside
  to a working one (failures-visible).
* **Never on staging.** The DSN is in `spend_guard.PAID_CREDENTIALS`, so
  staging removes it before this reads it, and the network guard would refuse
  the connection anyway. `init()` also checks `spend_guard.enabled()` itself:
  a third party is a third party.
* **Nothing about a listener leaves.** `send_default_pii` is off (no IP, no
  cookies, no user), and `scrub` removes cookies, every request header but
  the client version, the listener and session ids, and blanks credentials
  out of every message, exception and breadcrumb with `log_redaction` - a
  provider key in an httpx URL is exactly what §144 found in Render's logs.

`log.exception` and `log.error` become events through Sentry's logging
integration, which is what covers the background loops without editing each
of them; `log.warning` and below are breadcrumbs only.
"""
from __future__ import annotations

import logging
import os

import log_redaction
import spend_guard

log = logging.getLogger("fam.error_tracking")

#: Request headers kept on an event. Everything else - cookies, the bearer
#: token, forwarding addresses - is dropped.
KEPT_HEADERS = {"x-fam-client", "user-agent", "content-type"}

#: Keys dropped wherever they appear in an event's extra data or tags.
DROPPED_KEYS = {"listener", "listener_id", "user", "user_id", "session",
                "session_id", "email", "phone", "cookie", "cookies",
                "authorization", "token", "password"}

_STATE: dict = {"enabled": False, "reason": "not started"}


def _redact_value(value):
    if isinstance(value, str):
        return log_redaction.redact(value)
    if isinstance(value, dict):
        return {k: ("[removed]" if str(k).lower() in DROPPED_KEYS
                    else _redact_value(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(v) for v in value]
    return value


def scrub(event: dict, _hint=None) -> dict:
    """`before_send`: what may leave this server about a failure."""
    event.pop("user", None)
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("cookies", None)
        request.pop("data", None)
        request.pop("env", None)
        headers = request.get("headers") or {}
        if isinstance(headers, dict):
            request["headers"] = {k: v for k, v in headers.items()
                                  if k.lower() in KEPT_HEADERS}
        else:
            request.pop("headers", None)
        for field in ("url", "query_string"):
            if isinstance(request.get(field), str):
                request[field] = log_redaction.redact(request[field])
    for exc in (event.get("exception") or {}).get("values") or []:
        if isinstance(exc.get("value"), str):
            exc["value"] = log_redaction.redact(exc["value"])
        # Local variables can hold anything a request carried.
        for frame in (exc.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        for field in ("message", "formatted"):
            if isinstance(logentry.get(field), str):
                logentry[field] = log_redaction.redact(logentry[field])
        if logentry.get("params"):
            logentry["params"] = _redact_value(logentry["params"])
    if isinstance(event.get("message"), str):
        event["message"] = log_redaction.redact(event["message"])
    for key in ("extra", "tags", "contexts"):
        if isinstance(event.get(key), dict):
            event[key] = _redact_value(event[key])
    crumbs = event.get("breadcrumbs")
    values = crumbs.get("values") if isinstance(crumbs, dict) else crumbs
    for crumb in values or []:
        scrub_breadcrumb(crumb)
    return event


def scrub_breadcrumb(crumb: dict, _hint=None) -> dict:
    """`before_breadcrumb`: an httpx request line carries a provider's key."""
    if isinstance(crumb.get("message"), str):
        crumb["message"] = log_redaction.redact(crumb["message"])
    if isinstance(crumb.get("data"), dict):
        crumb["data"] = _redact_value(crumb["data"])
    return crumb


def _release() -> str:
    return (os.environ.get("RENDER_GIT_COMMIT")
            or os.environ.get("FAM_COMMIT") or "").strip()


def init() -> dict:
    """Start error tracking if this deployment asked for it. Never raises."""
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if spend_guard.enabled():
        _STATE.update(enabled=False,
                      reason="zero spend: nothing leaves this server")
    elif not dsn:
        _STATE.update(enabled=False, reason="SENTRY_DSN is not set")
    else:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.logging import LoggingIntegration
        except ImportError:
            _STATE.update(enabled=False,
                          reason="sentry-sdk is not installed (requirements.txt)")
        else:
            try:
                sentry_sdk.init(
                    dsn=dsn,
                    environment=spend_guard.environment() or "development",
                    release=_release() or None,
                    send_default_pii=False,
                    include_local_variables=False,
                    # Errors only: tracing is a separate bill and a separate
                    # decision.
                    traces_sample_rate=0.0,
                    before_send=scrub,
                    before_breadcrumb=scrub_breadcrumb,
                    integrations=[LoggingIntegration(
                        level=logging.INFO, event_level=logging.ERROR)],
                )
                _STATE.update(enabled=True, reason="")
            except Exception as exc:  # noqa: BLE001 - never worth a failed boot
                _STATE.update(enabled=False, reason=f"could not start: {exc}")
    if _STATE["enabled"]:
        log.info("error tracking: on (Sentry, %s)",
                 spend_guard.environment() or "development")
    else:
        log.info("error tracking: off - %s", _STATE["reason"])
    return report()


def report() -> dict:
    """For `/api/health`: on or off, and why."""
    return {"enabled": bool(_STATE["enabled"]), "provider": "sentry",
            "reason": _STATE["reason"]}
