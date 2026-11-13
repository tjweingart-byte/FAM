"""Keith's usage monitors: each service's own usage, against the owner's plan (§483).

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

Built: RunPod (2a, §483), Atlas (2b, §484), Cloudflare R2 (2c, §485),
Render (2d, §486), and Claude from FAM's own spend count (§487).
"""
from __future__ import annotations

import asyncio
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
        # No row for a day is RunPod not having billed it yet - unknown, not
        # $0 (the review).
        figure("spend today", round(total[today], 4) if today in total else None,
               "USD", "day"),
        figure("spend yesterday", round(total[yesterday], 4)
               if yesterday in total else None, "USD", "day"),
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


# ------------------------------------------------------------- Cloudflare R2
#: R2's published prices and free allowance (checked 2026-11-06,
#: developers.cloudflare.com/r2/pricing). The free allowance is monthly and
#: covers Standard storage only; Infrequent Access has none.
R2_FREE = {"class A": 1_000_000, "class B": 10_000_000, "storage_gb": 10.0}
R2_PRICE = {"class A": 4.50 / 1e6, "class B": 0.36 / 1e6, "storage_gb": 0.015}
R2_CLASS_A = frozenset((
    "ListBuckets", "PutBucket", "ListObjects", "PutObject", "CopyObject",
    "CompleteMultipartUpload", "CreateMultipartUpload",
    "LifecycleStorageTierTransition", "ListMultipartUploads", "UploadPart",
    "UploadPartCopy", "ListParts", "PutBucketEncryption", "PutBucketCors",
    "PutBucketLifecycleConfiguration"))
R2_CLASS_B = frozenset((
    "HeadBucket", "HeadObject", "GetObject", "UsageSummary",
    "GetBucketEncryption", "GetBucketLocation", "GetBucketCors",
    "GetBucketLifecycleConfiguration"))
R2_FREE_OPS = frozenset(("DeleteObject", "DeleteBucket", "AbortMultipartUpload"))
R2_NEXT = "pay as you go past the free allowance - no plan to change"

_R2_QUERY = """
query FamR2($account: string!, $start: Time!, $end: Time!) {
  viewer {
    accounts(filter: {accountTag: $account}) {
      operations: r2OperationsAdaptiveGroups(
          limit: 10000, filter: {datetime_geq: $start, datetime_leq: $end}) {
        sum { requests }
        dimensions { actionType }
      }
      storage: r2StorageAdaptiveGroups(
          limit: 1, filter: {datetime_geq: $start, datetime_leq: $end},
          orderBy: [datetime_DESC]) {
        max { payloadSize metadataSize objectCount }
        dimensions { datetime }
      }
    }
  }
}
"""


async def read_r2(now: Optional[datetime.datetime] = None, client=None) -> dict:
    """Cloudflare R2, the whole account: this UTC month's operations by
    billing class and the storage now, from Cloudflare's GraphQL analytics
    (`CLOUDFLARE_ANALYTICS_TOKEN`, Account Analytics: Read). Counted by
    Cloudflare, so the replays a listener's app fetches from a signed link
    (§252), which FAM never sees, are in it.

    Measured against the free monthly allowance, with what the month would
    cost past it at the published prices - measured against `IT_BUDGETS`'
    `r2` when set. Approximate for storage: the free allowance covers
    Standard storage only, FAM keeps `kept/` in Infrequent Access (§237),
    and these datasets do not split storage by class - said in the reading.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    at = now.timestamp()
    token = _secret("CLOUDFLARE_ANALYTICS_TOKEN")
    account = (settings.r2_account_id or "").strip()
    if not (token and account):
        return reading("r2", [], ok=False, at=at,
                       why="not configured: CLOUDFLARE_ANALYTICS_TOKEN and "
                           "R2_ACCOUNT_ID are both needed")
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    variables = {"account": account,
                 "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "end": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        import httpx

        own = client is None
        client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            response = await client.post(
                settings.cloudflare_graphql_url,
                json={"query": _R2_QUERY, "variables": variables},
                headers={"Authorization": f"Bearer {token}"})
        finally:
            if own:
                await client.aclose()
    except Exception as exc:  # noqa: BLE001 - a monitor never raises
        return reading("r2", [], ok=False, at=at,
                       why=f"no answer: {type(exc).__name__}")
    if response.status_code >= 400:
        return reading("r2", [], ok=False, at=at,
                       why=f"HTTP {response.status_code} from Cloudflare's analytics")
    try:
        body = response.json()
        if body.get("errors"):
            # The messages name fields and permissions, never the token.
            said = "; ".join(str(e.get("message", ""))[:120]
                             for e in body["errors"][:3])
            return reading("r2", [], ok=False, at=at,
                           why=f"Cloudflare refused the query: {said}")
        accounts = body["data"]["viewer"]["accounts"]
        if not accounts:
            return reading("r2", [], ok=False, at=at,
                           why="the token cannot see this account (R2_ACCOUNT_ID)")
        groups = accounts[0].get("operations") or []
        stored = accounts[0].get("storage") or []
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return reading("r2", [], ok=False, at=at,
                       why=f"an answer not understood: {type(exc).__name__}")
    counts = {"class A": 0, "class B": 0, "free": 0, "unknown": 0}
    unknown: set = set()
    for g in groups:
        kind = str((g.get("dimensions") or {}).get("actionType") or "")
        n = int((g.get("sum") or {}).get("requests") or 0)
        if kind in R2_CLASS_A:
            counts["class A"] += n
        elif kind in R2_CLASS_B:
            counts["class B"] += n
        elif kind in R2_FREE_OPS:
            counts["free"] += n
        else:
            counts["unknown"] += n
            unknown.add(kind)
    figures = [
        figure("class A operations this month", counts["class A"], "operations",
               "month", limit=R2_FREE["class A"],
               limit_source="R2's free monthly allowance", now=now, next_plan=R2_NEXT),
        figure("class B operations this month", counts["class B"], "operations",
               "month", limit=R2_FREE["class B"],
               limit_source="R2's free monthly allowance", now=now, next_plan=R2_NEXT),
    ]
    storage_gb = None
    if stored:
        top = stored[0].get("max") or {}
        storage_gb = round((float(top.get("payloadSize") or 0)
                            + float(top.get("metadataSize") or 0)) / 1e9, 4)
        figures.append(figure("storage", storage_gb, "GB", "now",
                              limit=R2_FREE["storage_gb"],
                              limit_source="R2's free storage (Standard only)",
                              next_plan=R2_NEXT))
    # What the month would cost past the free allowance, at Standard prices.
    over = (max(0, counts["class A"] - R2_FREE["class A"]) * R2_PRICE["class A"]
            + max(0, counts["class B"] - R2_FREE["class B"]) * R2_PRICE["class B"]
            + max(0.0, (storage_gb or 0.0) - R2_FREE["storage_gb"])
            * R2_PRICE["storage_gb"])
    budget = budgets().get("r2")
    charge = figure("estimated charge this month", round(over, 4), "USD",
                    "month", limit=budget,
                    limit_source="IT_BUDGETS" if budget else "", now=now)
    # Projected from the operations' pace, not the charge's (the review): a
    # charge is nothing until the allowance is passed, so its own straight
    # line under-projects a month that will pass it.
    elapsed, days = _month_fraction(now)
    if elapsed >= PROJECT_AFTER_DAYS:
        scale = days / elapsed
        projected = (max(0, counts["class A"] * scale - R2_FREE["class A"])
                     * R2_PRICE["class A"]
                     + max(0, counts["class B"] * scale - R2_FREE["class B"])
                     * R2_PRICE["class B"]
                     + max(0.0, (storage_gb or 0.0) - R2_FREE["storage_gb"])
                     * R2_PRICE["storage_gb"])
        charge["projected"] = round(projected, 2)
        if budget and projected > budget:
            charge["level"] = URGENT
    figures.append(charge)
    return reading("r2", figures, at=at, detail={
        "scope": "the whole Cloudflare account",
        "free_operations": counts["free"],
        "unclassified_operations": counts["unknown"],
        "unclassified_kinds": sorted(unknown)[:10],
        "objects": int(((stored[0].get("max") or {}).get("objectCount") or 0))
        if stored else None,
        "approximate": ("storage is all classes together; the free allowance "
                        "is Standard's only, and Infrequent Access (kept/) is "
                        "billed from the first GB at $0.01/GB-month")})


# --------------------------------------------------------------------- Render
#: The next plan up from the owner's, as the dashboard priced them
#: (2026-11-06, §483). Render's API names a plan; anything not here says
#: where to look rather than guess a price.
RENDER_NEXT = {
    # Web services ("compute plans").
    "starter": "Standard (1 CPU, 2 GB), $25/month",
    "standard": "Pro (2 CPU, 4 GB), $85/month",
    # Postgres.
    "basic_256mb": "Basic 1 GB (0.5 CPU), $19/month",
    "basic_1gb": "1 CPU, 2 GB, $40/month",
}
RENDER_NEXT_UNKNOWN = "the next plan on the service's Compute page"
RENDER_BANDWIDTH_NEXT = "$0.15 per GB past the workspace's included bandwidth"
_GB_PER = {"b": 1e-9, "bytes": 1e-9, "byte": 1e-9, "kb": 1e-6, "mb": 1e-3,
           "gb": 1.0, "tb": 1e3, "kib": 1024 / 1e9, "mib": 1024 ** 2 / 1e9,
           "gib": 1024 ** 3 / 1e9}


def _gb(value: float, unit: str) -> Optional[float]:
    """`value` in `unit`, in GB; None for a unit not understood."""
    per = _GB_PER.get(str(unit or "").strip().lower())
    return None if per is None else float(value) * per


async def _render_get(client, path: str, key: str, params=None):
    """One GET to Render's API: (body, "") or (None, why). Never the body of
    an error - it can carry the request."""
    try:
        response = await client.get(
            f"{settings.render_api_url.rstrip('/')}{path}", params=params,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001 - a monitor never raises
        return None, f"{path}: no answer ({type(exc).__name__})"
    if response.status_code >= 400:
        return None, f"{path}: HTTP {response.status_code}"
    try:
        return response.json(), ""
    except ValueError:
        return None, f"{path}: not JSON"


def _series_points(body) -> list:
    """[(value, unit)] over every series in a metrics answer, oldest first."""
    out = []
    for series in body if isinstance(body, list) else []:
        unit = (series or {}).get("unit", "")
        for point in (series or {}).get("values") or []:
            try:
                out.append((str(point.get("timestamp", "")), float(point["value"]),
                            point.get("unit", unit)))
            except (KeyError, TypeError, ValueError):
                continue
    return [(v, u) for _, v, u in sorted(out, key=lambda p: p[0])]


def _server_connections() -> Optional[dict]:
    """`db.server_connections()`: None off Postgres or when it does not answer."""
    import db

    return db.server_connections()


async def read_render(now: Optional[datetime.datetime] = None, client=None) -> dict:
    """Render: `fam`'s memory against its plan's, `fam-db`'s storage against
    its disk and its memory against its plan's, and `fam`'s outbound
    bandwidth this month against the workspace's included bandwidth.

    Read with `RENDER_API_KEY` (Render's keys are not scoped: Keith only ever
    reads with it, and staging never holds it) for `RENDER_SERVICE_ID` and the
    Postgres instance named `RENDER_POSTGRES_NAME`. A metric Render does not
    answer, or answers in a unit not understood, is listed in `not_read`.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    at = now.timestamp()
    key = _secret("RENDER_API_KEY")
    service = (settings.render_service_id or "").strip()
    if not (key and service):
        return reading("render", [], ok=False, at=at,
                       why="not configured: RENDER_API_KEY and RENDER_SERVICE_ID "
                           "are both needed")
    import httpx

    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    figures, not_read, detail = [], [], {}
    hour_ago = (now - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = {"startTime": hour_ago, "endTime": stamp}

    async def latest_gb(metric: str, resource: str, label: str) -> Optional[float]:
        body, why = await _render_get(client, f"/metrics/{metric}", key,
                                      {**recent, "resource": resource})
        if body is None:
            not_read.append(f"{label}: {why}")
            return None
        points = _series_points(body)
        if not points:
            not_read.append(f"{label}: no data points")
            return None
        value, unit = points[-1]
        gb = _gb(value, unit)
        if gb is None:
            not_read.append(f"{label}: unit {unit!r} not understood")
        return gb

    try:
        described, why = await _render_get(client, f"/services/{service}", key)
        plan = ((described or {}).get("serviceDetails") or {}).get("plan", "")
        detail["service_plan"] = plan or None
        if described is None:
            not_read.append(f"the service: {why}")
        used = await latest_gb("memory", service, "fam's memory")
        limit = await latest_gb("memory-limit", service, "fam's memory limit")
        if used is not None:
            figures.append(figure("fam memory", round(used, 4), "GB", "now",
                                  limit=limit, limit_source="its plan (Render)",
                                  next_plan=RENDER_NEXT.get(plan, RENDER_NEXT_UNKNOWN)))
        # This month's outbound bandwidth, against the workspace's allowance.
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        body, why = await _render_get(client, "/metrics/bandwidth", key, {
            "resource": service, "startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endTime": stamp})
        if body is None:
            not_read.append(f"fam's bandwidth: {why}")
        else:
            total, bad = 0.0, set()
            for value, unit in _series_points(body):
                gb = _gb(value, unit)
                if gb is None:
                    bad.add(unit)
                else:
                    total += gb
            if bad:
                not_read.append(f"fam's bandwidth: unit(s) {sorted(bad)} not understood")
            else:
                included = float(settings.render_bandwidth_included_gb or 0) or None
                figures.append(figure(
                    "fam outbound bandwidth this month", round(total, 4), "GB",
                    "month", limit=included,
                    limit_source="the workspace's included bandwidth",
                    now=now, next_plan=RENDER_BANDWIDTH_NEXT))
        # The database: found by name, its plan and disk, its use.
        name = (settings.render_postgres_name or "").strip()
        listed, why = await _render_get(client, "/postgres", key,
                                        {"name": name, "limit": 20}) if name else (None, "")
        rows = [row.get("postgres") or {} for row in
                (listed if isinstance(listed, list) else []) if isinstance(row, dict)]
        database = next((row for row in rows if row.get("name") == name), None)
        if not name:
            not_read.append("the database: RENDER_POSTGRES_NAME is empty")
        elif database is None:
            not_read.append(f"the database {name!r}: {why or 'not found'}")
        else:
            db_id, db_plan = database.get("id", ""), database.get("plan", "")
            detail.update(postgres=name, postgres_plan=db_plan or None,
                          disk_autoscaling=database.get("diskAutoscalingEnabled"))
            disk_gb = database.get("diskSizeGB")
            stored = await latest_gb("disk-usage", db_id, f"{name}'s storage")
            if stored is not None:
                figures.append(figure(
                    f"{name} storage", round(stored, 4), "GB", "now",
                    limit=float(disk_gb) if disk_gb else None,
                    limit_source="its disk (Render)",
                    next_plan="grow the disk: $0.30/GB-month, in steps of 5 GB"))
            db_memory = await latest_gb("memory", db_id, f"{name}'s memory")
            db_limit = await latest_gb("memory-limit", db_id, f"{name}'s memory limit")
            # Who holds connections on it (§574): each is a server process
            # with memory of its own, busy or idle, so the memory figure
            # carries them - an idle pile is fixed in code, not by a plan.
            held = await asyncio.to_thread(_server_connections)
            if held is not None:
                detail["connections"] = held
                figures.append(figure(
                    f"{name} connections", held["total"], "connections", "now",
                    limit=float(held["max_connections"]) or None,
                    limit_source="its max_connections (Postgres)",
                    next_plan="close idle ones (DATABASE_IDLE_SECONDS), or fewer "
                              "per server (DATABASE_MAX_CONNECTIONS)"))
            if db_memory is not None:
                memory = figure(
                    f"{name} memory", round(db_memory, 4), "GB", "now",
                    limit=db_limit, limit_source="its plan (Render)",
                    next_plan=RENDER_NEXT.get(db_plan, RENDER_NEXT_UNKNOWN))
                if held is not None:
                    memory["connections"] = held
                figures.append(memory)
    finally:
        if own:
            await client.aclose()
    detail["not_read"] = not_read
    if not figures:
        return reading("render", [], ok=False, at=at,
                       why="; ".join(not_read) or "nothing answered", detail=detail)
    return reading("render", figures, at=at, detail=detail)


# --------------------------------------------------------------------- Claude
async def read_anthropic(now: Optional[datetime.datetime] = None,
                         client=None) -> dict:
    """Claude: what FAM's own spend table says Claude cost (§481) - today,
    yesterday and this UTC month, Claude's models alone - against the
    owner's monthly figure in `IT_BUDGETS` (`anthropic=`). The console shows
    the owner no Limits page (§483), so FAM's count is the figure; nothing
    is asked of Anthropic. Calls with no price are said beside it."""
    import provider_usage

    now = now or datetime.datetime.now(datetime.timezone.utc)
    at = now.timestamp()
    spent = provider_usage.spend_report(at)
    if spent is None:
        return reading("anthropic", [], ok=False, at=at,
                       why="nothing counted yet (provider_usage's spend table)")
    claude = spent["claude"]
    budget = budgets().get("anthropic")
    figures = [
        figure("Claude spend this month", claude["month"], "USD", "month",
               limit=budget, limit_source="IT_BUDGETS" if budget else "", now=now),
        figure("Claude spend today", claude["today"], "USD", "day"),
        figure("Claude spend yesterday", claude["yesterday"], "USD", "day"),
    ]
    return reading("anthropic", figures, at=at, detail={
        "source": "FAM's own count of every Claude answer, priced at metering.PRICES",
        "unpriced_calls_this_month": claude["unpriced_month"]})


#: Every monitor built, by service.
MONITORS = {"runpod": read_runpod, "atlas": read_atlas, "r2": read_r2,
            "render": read_render, "anthropic": read_anthropic}


# ------------------------------------------------------------------- running
async def poll(now: Optional[datetime.datetime] = None, client=None,
               only: Optional[set] = None) -> list:
    """Read every monitor once (or those in `only`: the owner's "I fixed it"
    re-reads its own service, §495), keep each reading, and return them.
    Never raises."""
    import provider_usage

    out = []
    for service, read in MONITORS.items():
        if only is not None and service not in only:
            continue
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
    process's last reading of each service. Memory only. A reading's `why`
    stays on /admin (§495): it can carry a provider's own error text, and
    health is public."""
    return {"budgets": budgets(),
            "warn_share": float(settings.it_warn_share),
            "urgent_share": float(settings.it_urgent_share),
            "last": {s: {"at": r["at"], "ok": r["ok"], "level": r["level"]}
                     for s, r in LAST.items()}}
