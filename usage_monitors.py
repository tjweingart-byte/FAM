"""Keith's usage monitors: each service's own usage, against the owner's plan (§465).

keith.md "always" 6: "Watch every plugin's usage against its limits - data
providers, Claude, Gemini, MongoDB Atlas, Render, RunPod and Cloudflare - and
warn the owner before a subscription needs to change (at 80% of a limit, and
urgently at 95% or when a limit will run out before it resets), with the next
plan and its price." `provider_usage` counts what FAM sends; these read what
each service itself says it was used for - including what FAM cannot see,
like a GPU worker's seconds.

**Reads only, nothing spent.** Every monitor asks a service's own read API
with a key FAM already holds or a read-only one, and never changes a setting,
a plan or a pod (keith.md "never" 1, 2). Hourly, as Netenyahu's task
`it:usage`, only while `AGENTS=1`, `IT_AGENT=1` and Keith's rule book is
signed. On staging the keys are removed (`spend_guard`), so each monitor says
"not configured" and asks nothing.

**A reading** is the service, when, whether it answered, and its figures:
each a value with its unit and period, the limit it is measured against (the
plan's, or the owner's monthly budget in `IT_BUDGETS`), the share used, the
month's projection, and a level - `ok`, `warn` at `IT_WARN_SHARE` (0.80),
`urgent` at `IT_URGENT_SHARE` (0.95) or when the month's pace will pass the
limit before it resets. A figure with no limit has no share and no level
beyond `ok`: the spend is shown, never measured against nothing. Readings
are kept in `provider_usage.db` (`monitor_readings`, 90 days).

**Never raises.** A service that does not answer, or answers in a shape not
understood, is a reading with `ok: false` and why - never a figure of 0.

Built so far: RunPod (2a, §465), Atlas (2b, §466). Cloudflare R2 and Render
follow.
"""
from __future__ import annotations

import calendar
import datetime
import json
import logging
import time
from typing import Optional

from config import settings

log = logging.getLogger("usage_monitors")

#: How long one service may take to answer.
TIMEOUT_SECONDS = 20.0
#: The days into a month before its pace is projected: on the 1st, one busy
#: hour would read as a month's overrun.
PROJECT_AFTER_DAYS = 3.0

OK, WARN, URGENT = "ok", "warn", "urgent"
_RANK = {OK: 0, WARN: 1, URGENT: 2}

#: The last reading of each service in this process, for /api/health
#: (which never opens a store).
LAST: dict = {}


def budgets() -> dict:
    """`IT_BUDGETS` as {service: dollars a month}. A part that does not read
    as `name=number` is skipped and logged, never guessed at."""
    out: dict = {}
    for part in (getattr(settings, "it_budgets", "") or "").split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, value = part.partition("=")
        try:
            amount = float(value)
        except ValueError:
            amount = -1.0
        if not sep or not name.strip() or amount <= 0:
            log.warning("IT_BUDGETS: %r is not service=dollars; ignored", part)
            continue
        out[name.strip().lower()] = amount
    return out


def _month_fraction(now: datetime.datetime) -> tuple[float, int]:
    """(days elapsed in the UTC month, days in it)."""
    days = calendar.monthrange(now.year, now.month)[1]
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (now - start).total_seconds() / 86400.0, days


def figure(name: str, value: Optional[float], unit: str, period: str, *,
           limit: Optional[float] = None, limit_source: str = "",
           now: Optional[datetime.datetime] = None, next_plan: str = "") -> dict:
    """One measured figure, with its share, projection and level."""
    out = {"name": name, "value": value, "unit": unit, "period": period,
           "limit": limit, "limit_source": limit_source, "share": None,
           "projected": None, "level": OK, "next": next_plan}
    if value is None or not limit:
        return out
    out["share"] = round(value / limit, 4)
    if period == "month" and now is not None:
        elapsed, days = _month_fraction(now)
        if elapsed >= PROJECT_AFTER_DAYS:
            out["projected"] = round(value / elapsed * days, 2)
    warn = float(getattr(settings, "it_warn_share", 0.80) or 0.80)
    urgent = float(getattr(settings, "it_urgent_share", 0.95) or 0.95)
    if out["share"] >= urgent or (out["projected"] or 0) > limit:
        out["level"] = URGENT
    elif out["share"] >= warn:
        out["level"] = WARN
    return out


def reading(service: str, figures: list, *, ok: bool = True, why: str = "",
            at: Optional[float] = None, detail: Optional[dict] = None) -> dict:
    level = max((f["level"] for f in figures), key=_RANK.get, default=OK)
    return {"service": service, "at": time.time() if at is None else at,
            "ok": ok, "why": why, "level": level if ok else OK,
            "figures": figures, "detail": detail or {}}


# --------------------------------------------------------------------- RunPod
def _runpod_key() -> str:
    import voice_control

    return voice_control._runpod_key()


async def read_runpod(now: Optional[datetime.datetime] = None,
                      client=None) -> dict:
    """RunPod's own spend, from its billing history (`GET /v2/billing`).

    The voice runs serverless on prepaid credit that tops itself up, so the
    figure that matters is the money (the owner, 2026-11-06): today,
    yesterday, this UTC month and its projection, with the serverless part
    beside the total. Measured against `IT_BUDGETS`' `runpod` when set.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    key = _runpod_key()
    if not key:
        return reading("runpod", [], ok=False,
                       why="not configured: no RUNPOD_API_KEY", at=now.timestamp())
    url = f"{settings.runpod_api_url.rstrip('/')}/billing"
    # The whole UTC month, and yesterday when today is the 1st.
    days = max(2, now.day + 1)
    try:
        import httpx

        own = client is None
        client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            response = await client.get(
                url, params={"bucketSize": "day", "lastN": days},
                headers={"Authorization": f"Bearer {key}"})
        finally:
            if own:
                await client.aclose()
    except Exception as exc:  # noqa: BLE001 - a monitor never raises
        return reading("runpod", [], ok=False, at=now.timestamp(),
                       why=f"no answer: {type(exc).__name__}")
    if response.status_code >= 400:
        # Never the body: an error page can carry a request's headers.
        return reading("runpod", [], ok=False, at=now.timestamp(),
                       why=f"HTTP {response.status_code} from RunPod's billing")
    try:
        records = response.json()["records"]
        rows = [(str(r["startTime"])[:10], float(r.get("totalAmount") or 0.0),
                 float(r.get("serverlessGpuAmount") or 0.0)
                 + float(r.get("serverlessCpuAmount") or 0.0)
                 + float(r.get("serverlessDiskAmount") or 0.0)
                 + float(r.get("serverlessFeeAmount") or 0.0))
                for r in records]
    except (KeyError, TypeError, ValueError) as exc:
        return reading("runpod", [], ok=False, at=now.timestamp(),
                       why=f"an answer not understood: {type(exc).__name__}")
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    total = {day: amount for day, amount, _ in rows}
    month_total = round(sum(a for d, a, _ in rows if d.startswith(month)), 4)
    month_serverless = round(sum(s for d, _, s in rows if d.startswith(month)), 4)
    budget = budgets().get("runpod")
    figures = [
        figure("spend this month", month_total, "USD", "month",
               limit=budget, limit_source="IT_BUDGETS" if budget else "",
               now=now),
        figure("spend today", round(total.get(today, 0.0), 4), "USD", "day"),
        figure("spend yesterday", round(total.get(yesterday, 0.0), 4), "USD", "day"),
        figure("serverless this month", month_serverless, "USD", "month"),
    ]
    return reading("runpod", figures, at=now.timestamp(),
                   detail={"billing": "prepaid credit, topped up automatically"})


# ---------------------------------------------------------------------- Atlas
#: Flex's limits and the next tier, as MongoDB publishes them (checked
#: 2026-11-06: docs/atlas/billing/atlas-flex-costs, reference/flex-limitations).
FLEX_STORAGE_GB = 5.0
FLEX_CONNECTIONS = 500
FLEX_NEXT = "Dedicated M10, from $60/month (Flex itself is $8-$30/month by ops/sec)"
#: The Admin API version the flex-cluster resource answers in.
ATLAS_ACCEPT = "application/vnd.atlas.2024-11-13+json"


def _secret(name: str) -> str:
    import credentials

    return (credentials.active(name) or "").strip()


async def _atlas_cluster(client, now) -> tuple[Optional[dict], str]:
    """The Admin API's description of the Flex cluster, or (None, why)."""
    public, private = _secret("ATLAS_PUBLIC_KEY"), _secret("ATLAS_PRIVATE_KEY")
    project = (settings.atlas_project_id or "").strip()
    cluster = (settings.atlas_cluster or "").strip()
    if not (public and private and project and cluster):
        return None, ("not configured: ATLAS_PUBLIC_KEY, ATLAS_PRIVATE_KEY, "
                      "ATLAS_PROJECT_ID and ATLAS_CLUSTER are all needed")
    import httpx

    url = (f"{settings.atlas_api_url.rstrip('/')}/api/atlas/v2/groups/"
           f"{project}/flexClusters/{cluster}")
    try:
        own = client is None
        client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            response = await client.get(url, headers={"Accept": ATLAS_ACCEPT},
                                        auth=httpx.DigestAuth(public, private))
        finally:
            if own:
                await client.aclose()
    except Exception as exc:  # noqa: BLE001 - a monitor never raises
        return None, f"the Admin API did not answer: {type(exc).__name__}"
    if response.status_code == 404:
        return None, (f"no Flex cluster {cluster!r} in the project: renamed, or "
                      "moved to a dedicated tier this monitor does not read yet")
    if response.status_code >= 400:
        return None, f"HTTP {response.status_code} from the Admin API"
    try:
        body = response.json()
        return body if isinstance(body, dict) else None, ""
    except ValueError:
        return None, "the Admin API's answer was not JSON"


def _atlas_database() -> tuple[dict, str]:
    """Storage and connections, asked of the cluster itself: `atlasSize` (the
    Flex command for data plus indexes) and `serverStatus`. Sonic's read-only
    user first (§403); the archive's own user when he has none. Reads only."""
    uri, user = _secret("MONGODB_READ_URI"), "read-only user"
    if not uri:
        uri, user = _secret("MONGODB_URI"), "archive's user"
    if not uri:
        return {}, "no MONGODB_READ_URI or MONGODB_URI"
    try:
        from pymongo import MongoClient
    except ImportError:
        return {}, "pymongo is not installed (requirements-archive.txt)"
    ms = int(TIMEOUT_SECONDS * 1000)
    out: dict = {"user": user}
    client = None
    try:
        client = MongoClient(uri, appname="fam-keith", maxPoolSize=1,
                             serverSelectionTimeoutMS=ms, connectTimeoutMS=ms,
                             socketTimeoutMS=ms)
        admin = client["admin"]
        try:
            out["atlas_size"] = float(admin.command({"atlasSize": 1})["atlasSize"])
        except Exception as exc:  # noqa: BLE001
            out["atlas_size_why"] = f"atlasSize refused: {type(exc).__name__}"
        try:
            status = admin.command({"serverStatus": 1})
            out["connections"] = int(status["connections"]["current"])
        except Exception as exc:  # noqa: BLE001
            out["connections_why"] = f"serverStatus refused: {type(exc).__name__}"
        return out, ""
    except Exception as exc:  # noqa: BLE001 - a monitor never raises
        return {}, f"the database did not answer: {type(exc).__name__}"
    finally:
        if client is not None:
            client.close()


async def read_atlas(now: Optional[datetime.datetime] = None,
                     client=None, database=None) -> dict:
    """MongoDB Atlas: the archive's Flex cluster against Flex's limits.

    The Admin API (a Project Read Only key, Digest auth) says the cluster's
    state and its storage limit; the cluster itself says what is used. On a
    Flex cluster the Admin API offers no per-process figures, so operations a
    second are not read here - said, not guessed.
    """
    import asyncio

    now = now or datetime.datetime.now(datetime.timezone.utc)
    at = now.timestamp()
    if (settings.archive_backend or "").strip().lower() != "atlas":
        return reading("atlas", [], ok=False, at=at,
                       why="the archive is not on Atlas (ARCHIVE_BACKEND)")
    described, api_why = await _atlas_cluster(client, now)
    if database is None:
        found, db_why = await asyncio.to_thread(_atlas_database)
    else:
        found, db_why = database()
    if described is None and not found:
        return reading("atlas", [], ok=False, at=at,
                       why="; ".join(w for w in (api_why, db_why) if w))
    limit_gb = None
    if described:
        limit_gb = (described.get("providerSettings") or {}).get("diskSizeGB")
    source = "the Admin API" if limit_gb else "Flex's published limit"
    limit_gb = float(limit_gb or FLEX_STORAGE_GB)
    figures = []
    if "atlas_size" in found:
        figures.append(figure("storage used", round(found["atlas_size"] / 1e9, 4),
                              "GB", "now", limit=limit_gb, limit_source=source,
                              next_plan=FLEX_NEXT))
    if "connections" in found:
        figures.append(figure("connections", found["connections"], "connections",
                              "now", limit=FLEX_CONNECTIONS,
                              limit_source="Flex's published limit",
                              next_plan=FLEX_NEXT))
    detail = {"state": (described or {}).get("stateName"),
              "cluster": (described or {}).get("name") or settings.atlas_cluster,
              "read_with": found.get("user"),
              "not_read": [w for w in (api_why, db_why, found.get("atlas_size_why"),
                                       found.get("connections_why"),
                                       "operations a second: no Admin API figure on Flex")
                           if w]}
    return reading("atlas", figures, at=at, detail=detail)


#: Every monitor built, by service.
MONITORS = {"runpod": read_runpod, "atlas": read_atlas}


# ------------------------------------------------------------------- running
async def poll(now: Optional[datetime.datetime] = None, client=None) -> list:
    """Read every monitor once, keep each reading, and return them. Never raises."""
    import provider_usage

    out = []
    for service, read in MONITORS.items():
        try:
            got = await read(now, client)
        except Exception as exc:  # noqa: BLE001 - one monitor never stops the rest
            got = reading(service, [], ok=False, why=f"failed: {type(exc).__name__}")
        LAST[service] = got
        try:
            provider_usage.store().add_reading(service, got["level"],
                                               json.dumps(got), got["at"])
        except Exception as exc:  # noqa: BLE001
            log.warning("could not keep the %s reading: %s", service, exc)
        if not got["ok"]:
            log.info("usage monitor %s: %s", service, got["why"])
        out.append(got)
    return out


async def handler(_task: str) -> None:
    """Netenyahu's hourly task `it:usage`: read, keep, never act."""
    from agents import roster

    if not roster.status("it")["may_run"]:
        log.info("usage monitors not run: %s", roster.status("it")["why_not"])
        return
    await poll()


def register() -> None:
    """At startup, with `AGENTS=1`: Keith's hourly task."""
    from agents import netenyahu

    netenyahu.HANDLERS[("it", "usage")] = handler


def report(now: Optional[float] = None, days: int = 31) -> dict:
    """For /admin: each service's latest reading and its readings over
    `days`. Never raises, never creates the store."""
    import provider_usage

    now = time.time() if now is None else now
    latest: dict = {}
    count = 0
    try:
        if provider_usage.exists():
            for at, service, level, payload in provider_usage.store().readings_since(
                    now - days * 86400):
                count += 1
                if service not in latest:
                    latest[service] = json.loads(payload)
    except Exception as exc:  # noqa: BLE001 - a report is never load-bearing
        log.warning("could not read the usage monitors: %s", exc)
    return {"services": [latest.get(s) or {"service": s, "never_read": True}
                         for s in MONITORS],
            "readings": count, "budgets": budgets()}


def health() -> dict:
    """`/api/health`'s `usage_monitors`: the budgets in force, and this
    process's last reading of each service. Memory only."""
    return {"budgets": budgets(),
            "warn_share": float(settings.it_warn_share),
            "urgent_share": float(settings.it_urgent_share),
            "last": {s: {"at": r["at"], "ok": r["ok"], "level": r["level"],
                         "why": r["why"]} for s, r in LAST.items()}}
