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


def _episodes(since: float) -> Optional[int]:
    try:
        import episode_quality

        if not episode_quality.exists():
            return 0
        return len(episode_quality.store().episodes_since(since))
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


def report(now: Optional[float] = None) -> dict:
    """Both figures, the plan, and the nightly jobs. Never raises."""
    now = time.time() if now is None else now
    s = _settings()
    days = max(1.0, float(getattr(s, "it_cost_days", 7) or 7))
    # From the start of the first UTC day: the spend table's own grain.
    since = now - days * DAY
    since -= since % DAY
    span = (now - since) / DAY
    episodes = _episodes(since)
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
