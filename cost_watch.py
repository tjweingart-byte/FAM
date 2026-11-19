"""What an episode costs, against the owner's plan (§492, decided items 21, 27).

keith.md "always" 10: "Watch what the agents cost per episode, and email the
owner when it runs well above the plan. Never switch an agent off because of
cost." The owner (2026-11-06) asked for two figures:

* **(a) the agents per episode** - every agent call made for an episode (the
  `search` and `ahead` surfaces, `metering`'s `agent_calls`), divided by the
  episodes written - against `IT_COST_PLAN_AGENTS_CENTS` (0.45: the Cost
  table, §460). The nightly jobs (the `background` surface: Mclovin's review,
  Nando's answer key, Consuela's runs) are the same whatever the volume, so
  they are shown beside it as dollars a month, never folded into a
  per-episode figure that would read huge on a quiet week.
* **(b) all of Claude per episode** - every Claude answer the day's spend
  counted (§481), agents included, divided by the same episodes - against
  `IT_COST_PLAN_CLAUDE_CENTS` (1.0: the owner's pricing estimate). The
  agents' nightly jobs are taken out of it (§495), as they are out of (a):
  at low volume they alone would read well above the plan.

Over the last `IT_COST_DAYS` (7), from the start of that UTC day (§495):
Claude's spend is kept by day, so the episodes and the agents' calls are
counted over the same whole days. "Well above" is `IT_COST_WELL_ABOVE` (1.5x)
the plan: an urgent incident, so it is emailed (§489) and in the pop-up. Below
`MIN_EPISODES` written, no figure is read (a handful of episodes is noise).
**Watched, never capped**: nothing here stops, holds or slows anything.

Episodes are those written to the shared cache (`episode_quality`'s rows: a
tap, prefetch, both editions); a private search's or an attachment's are
never recorded there, so their cost is in the total and not in the count -
the figure reads slightly high, never low.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger("cost_watch")

DAY = 86400.0
#: Episodes in the window before a per-episode figure is read.
MIN_EPISODES = 50

#: Tests hand in their own stores; production opens the deployment's.
LEDGER: list = [None]


def _settings():
    from config import settings

    return settings


def _ledger():
    if LEDGER[0] is not None:
        return LEDGER[0]
    import db as store_db
    import metering
    from paths import data_path

    if not store_db.has_tables(data_path("METERING_DB", "metering.db")):
        return None
    LEDGER[0] = metering.MeterStore()
    return LEDGER[0]


def _episode_rows(since: float) -> Optional[list]:
    try:
        import episode_quality

        if not episode_quality.exists():
            return []
        return list(episode_quality.store().episodes_since(since))
    except Exception as exc:  # noqa: BLE001
        log.warning("could not count the episodes: %s", exc)
        return None


def _agent_spend(since: float) -> Optional[dict]:
    ledger = _ledger()
    if ledger is None:
        return {"episode": 0.0, "nightly": 0.0}
    try:
        rows = ledger._conn().execute(
            "SELECT surface, COALESCE(SUM(cost_usd), 0) FROM agent_calls "
            "WHERE at >= ? AND cost_usd IS NOT NULL GROUP BY surface", (since,)).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read the agents' spend: %s", exc)
        return None
    out = {"episode": 0.0, "nightly": 0.0}
    for surface, usd in rows:
        out["nightly" if surface == "background" else "episode"] += float(usd or 0)
    return out


def _claude_spend(since: float) -> Optional[float]:
    try:
        import provider_usage

        if not provider_usage.exists():
            return 0.0
        provider_usage.flush()
        day = time.strftime("%Y-%m-%d", time.gmtime(since))
        return sum(float(r["usd"]) for r in provider_usage.store().spend_since(day)
                   if provider_usage.is_claude(r["model"]))
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read Claude's spend: %s", exc)
        return None


def _day(at: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(at))


def _nightly_by_day(since: float) -> dict:
    """The agents' nightly jobs' dollars by UTC day, to take out of Claude's."""
    ledger = _ledger()
    out: dict = {}
    if ledger is None:
        return out
    try:
        rows = ledger._conn().execute(
            "SELECT at, cost_usd FROM agent_calls WHERE at >= ? AND "
            "surface = 'background' AND cost_usd IS NOT NULL", (since,)).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read the agents' nightly spend: %s", exc)
        return out
    for at, usd in rows:
        day = _day(float(at))
        out[day] = out.get(day, 0.0) + float(usd or 0)
    return out


def by_day(now: Optional[float] = None, days: Optional[float] = None,
           rows: Optional[list] = None) -> dict:
    """Claude per episode, day by day, and what spent it (the owner,
    2026-11-18: "the per episode costs of claude have been going up").

    For each UTC day of the window: the episodes written to the cache and
    which surface wrote them (`origin`), Claude's dollars by spend source
    (`metering.spend_source`: `episodes` is a listener's tap or search,
    `prefetch`, `edition:*`, and the small calls beside them), the agents'
    nightly jobs taken out as `report` takes them out, and the cents an
    episode that leaves. A rise is then read as a source that grew or a
    count that fell, which the 7-day figure alone cannot tell apart. A day
    under `MIN_EPISODES / days` episodes still shows its cents: it is a row
    in a table, never an alert. `rows` are `report`'s episode rows, so the
    store is read once a draw. Never raises."""
    now = time.time() if now is None else now
    s = _settings()
    days = max(1.0, float(days or getattr(s, "it_cost_days", 7) or 7))
    since = now - days * DAY
    since -= since % DAY
    out_days: dict = {}
    order = []
    at = since
    while at <= now:
        day = _day(at)
        order.append(day)
        out_days[day] = {"day": day, "episodes": 0, "origins": {}, "sources": {},
                         "claude_usd": 0.0, "nightly_usd": 0.0,
                         "cents_per_episode": None}
        at += DAY
    if rows is None:
        rows = _episode_rows(since)
    for row in rows or []:
        written = row.get("at") if isinstance(row, dict) else None
        if written is None:
            continue
        d = out_days.get(_day(float(written)))
        if d is None:
            continue
        d["episodes"] += 1
        origin = str(row.get("origin") or "other")
        d["origins"][origin] = d["origins"].get(origin, 0) + 1
    totals: dict = {}
    try:
        import provider_usage

        if provider_usage.exists():
            provider_usage.flush()
            for r in provider_usage.store().spend_since(_day(since)):
                if not provider_usage.is_claude(r["model"]):
                    continue
                d = out_days.get(r["day"])
                if d is None:
                    continue
                usd = float(r["usd"] or 0)
                d["sources"][r["source"]] = d["sources"].get(r["source"], 0.0) + usd
                d["claude_usd"] += usd
                totals[r["source"]] = totals.get(r["source"], 0.0) + usd
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read Claude's spend by day: %s", exc)
    for day, usd in _nightly_by_day(since).items():
        if day in out_days:
            out_days[day]["nightly_usd"] = usd
    for d in out_days.values():
        net = max(0.0, d["claude_usd"] - d["nightly_usd"])
        if d["episodes"]:
            d["cents_per_episode"] = round(net * 100 / d["episodes"], 4)
        d["claude_usd"] = round(d["claude_usd"], 4)
        d["nightly_usd"] = round(d["nightly_usd"], 4)
        d["sources"] = {k: round(v, 4) for k, v in d["sources"].items()}
    sources = [k for k, _ in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"days": [out_days[d] for d in order], "sources": sources,
            "note": "Claude's dollars by spend source and the episodes written, "
                    "per UTC day; the nightly agent jobs are counted in their "
                    "source and taken out of the cents, as in the 7-day figure"}


def report(now: Optional[float] = None) -> dict:
    """Both figures, the plan, and the nightly jobs. Never raises."""
    now = time.time() if now is None else now
    s = _settings()
    days = max(1.0, float(getattr(s, "it_cost_days", 7) or 7))
    # From the start of the first UTC day: the spend table's own grain.
    since = now - days * DAY
    since -= since % DAY
    span = (now - since) / DAY
    rows = _episode_rows(since)
    episodes = None if rows is None else len(rows)
    agents = _agent_spend(since)
    claude = _claude_spend(since)
    if claude is not None and agents:
        claude = max(0.0, claude - agents["nightly"])
    well_above = float(getattr(s, "it_cost_well_above", 1.5) or 1.5)

    def per(usd, plan_cents) -> dict:
        ready = bool(episodes) and episodes >= MIN_EPISODES and usd is not None
        cents = round(usd * 100 / episodes, 4) if ready else None
        return {"cents_per_episode": cents, "plan_cents": plan_cents,
                "ratio": round(cents / plan_cents, 3) if cents is not None and plan_cents else None,
                "well_above": bool(cents is not None and plan_cents
                                   and cents > plan_cents * well_above),
                "usd": round(usd, 4) if usd is not None else None}

    return {
        "days": days, "span_days": round(span, 2), "episodes": episodes, "min_episodes": MIN_EPISODES,
        "well_above_at": well_above,
        "agents": per(agents["episode"] if agents else None,
                      float(getattr(s, "it_cost_plan_agents_cents", 0.45) or 0)),
        "claude": per(claude, float(getattr(s, "it_cost_plan_claude_cents", 1.0) or 0)),
        "nightly_usd_per_month": (round(agents["nightly"] / span * 30, 2)
                                  if agents else None),
        "since": since,
        "by_day": by_day(now, days, rows=rows),
        "note": "episodes written to the cache; private searches' cost is in "
                "the total, not the count, so it reads slightly high; the "
                "agents' nightly jobs are apart from both figures",
    }


def findings(now: Optional[float] = None) -> list:
    """An urgent incident for each figure well above its plan."""
    import incidents as INC

    out = []
    r = report(now)
    since_day = time.strftime("%Y-%m-%d", time.gmtime(r["since"]))
    for key, label, setting in (("agents", "The agents", "IT_COST_PLAN_AGENTS_CENTS"),
                                ("claude", "Claude", "IT_COST_PLAN_CLAUDE_CENTS")):
        f = r[key]
        if not f["well_above"]:
            continue
        out.append(INC.finding(
            f"cost:{key}", "cost", INC.URGENT,
            f"{label} cost {f['cents_per_episode']:.2f}¢ an episode since "
            f"{since_day} - {f['ratio']:.1f}x the plan's {f['plan_cents']:g}¢",
            detail={**f, "episodes": r["episodes"], "days": r["days"],
                    "since": r["since"]},
            # Whole UTC days (§495), so 7 to 8 of them: the dates say which.
            problem=f"${f['usd']:,.2f} over {r['episodes']:,} episodes written since "
                    f"{since_day} UTC ({r['span_days']:.1f} days), against "
                    f"{f['plan_cents']:g}¢ ({setting}).",
            heard="Nothing: cost is watched, never capped - no agent is held "
                  "or slowed for it (decided item 21).",
            recommended="Look at Claude's spend by source and model on /admin "
                        "(claude_spend) for what grew.",
            cost_risk="Each figure is this deployment's own count, priced at "
                      "metering.PRICES.",
            needs="Your decision: a lever from the Cost table, or a new plan figure."))
    return out
