"""The finance workbook: every service FAM pays for, and what it spent each day.

    GET /api/admin/financials.xlsx         built on request, so always current
    python tools/financials.py             the same workbook from local stores
    python tools/financials.py --remote https://<host>   pull it from a server

One Excel file, six sheets, written fresh from the deployment's own records
every time it is asked for - so "updates daily" is a property of where the
numbers come from, not of a job that has to remember to run:

* **Dashboard** - today, month to date, trailing 30 days, monthly run-rate,
  revenue (none yet) and net burn, and where the money goes.
* **Cost Structure** - every outside service, one row each: what it does for
  FAM, the plan in force, its price, its monthly and annual cost, and what
  buying the next plan would cost. Usage-billed rows read the ledger.
* **Daily Ledger** - one row per UTC day: episodes written, Claude, Exa, GPU
  voice and tile-picture spend from the metering stores, plus each day's share
  of the subscriptions.
* **12-Month Plan** - the run-rate carried forward, the licences a paid launch
  needs, and a revenue line to fill in.
* **Provider Calls** - requests per day to each data service, beside its limit.
* **Notes** - what each colour means and where every number comes from.

Every number is one of three things, and the Notes sheet says which:
*recorded* (the ledgers `metering.py`, `thumbnails.py` and `provider_usage.py`
write at spend time), *list price* (the published price, with the date it was
looked up; re-check before buying) or *assumption* (a blue input to change).

Reads only. Never opens a store that does not exist (it would create one),
never makes a network call, never calls a model - so it is safe on staging and
cheap enough to build on every request.
"""
from __future__ import annotations

import datetime as _dt
import io
import logging
import os
import sqlite3
import time
from contextlib import closing
from typing import Optional

from paths import data_path

log = logging.getLogger("financials")

DAY = 86400

#: The ledger goes back this far at most; it starts at the first recorded day
#: or `LEDGER_MIN_DAYS` ago, whichever is earlier.
LEDGER_MAX_DAYS = 365
LEDGER_MIN_DAYS = 30

#: EUR to USD, for GNews (priced in euros). An assumption, editable on the
#: sheet; looked up 2026-10-07.
EUR_TO_USD = 1.08

# --- the catalogue ----------------------------------------------------------
#
# One row per thing FAM pays for or depends on. `unit` decides how the list
# price becomes a monthly cost on the sheet: "/month", "/year", "EUR/month",
# or "usage" (read from the Daily Ledger column named in `ledger`). Prices are
# from docs/FINANCIAL.md and the code it cites; a row whose plan the deployment
# can tell us (GNews, Finnhub, Open-Meteo, API-Sports, the voice transport,
# tile pictures, Viral Loops) is decided by the settings in force, not copied.

HOSTING = "Hosting & infrastructure"
VOICE = "Voice (GPU)"
AI = "AI models"
DATA = "Research & data"
GROWTH = "Growth"
DEV = "Developer & accounts"
CONFIRM = "Not visible in the code"

CATEGORIES = (HOSTING, VOICE, AI, DATA, GROWTH, DEV, CONFIRM)

# Ledger columns that hold recorded, usage-billed spend.
LEDGER_USAGE = {
    "claude": "Claude API ($)",
    "exa": "Exa search ($)",
    "gpu": "GPU voice ($)",
    "images": "Tile pictures ($)",
}


def _row(category, vendor, item, purpose, plan, status, price, unit,
         next_step="", source="", ledger=""):
    return {"category": category, "vendor": vendor, "item": item,
            "purpose": purpose, "plan": plan, "status": status,
            "price": price, "unit": unit, "next": next_step,
            "source": source, "ledger": ledger}


def _setting(name: str, default=None):
    try:
        from config import settings
        return getattr(settings, name, default)
    except Exception:  # noqa: BLE001 - a report never fails on a setting
        return default


def _has_key(name: str) -> bool:
    try:
        import credentials
        return bool(credentials.active(name))
    except Exception:  # noqa: BLE001
        return bool((os.environ.get(name) or "").strip())


def _gpu() -> tuple[float, bool]:
    """(USD per GPU hour, whether the card is an always-on pod)."""
    try:
        import metering
        rate = float(metering.GPU_USD_PER_HOUR)
    except Exception:  # noqa: BLE001
        rate = 0.69
    pod = str(_setting("remote_voice_transport", "runpod") or "").strip() == "http"
    return rate, pod


def _sport_tiers() -> tuple[dict, dict]:
    """(sport -> tier, tier -> {daily, usd, label}) from live_sources."""
    try:
        import live_sources
        return live_sources.configured_tiers(), live_sources.TIERS
    except Exception:  # noqa: BLE001
        return {}, {"free": {"daily": 100, "usd": 0, "label": "Free"}}


def catalogue() -> list[dict]:
    """Every service, as the deployment in force has it configured."""
    gpu_rate, pod = _gpu()
    model = _setting("model", "claude-sonnet-5") or "claude-sonnet-5"
    rows = [
        _row(HOSTING, "Render", "fam - production web service",
             "Runs the app and API listeners talk to", "Starter (512 MB, 0.5 CPU)",
             "Paying", 7, "/month",
             "Standard $25/mo (2 GB) at ~20-40 simultaneous streams",
             "render.yaml; render.com/pricing (2026-09-25)"),
        _row(HOSTING, "Render", "fam-staging - staging web service",
             "Where every merge is tried first, zero spend",
             "Starter", "Paying", 7, "/month", "",
             "render.yaml; render.com/pricing (2026-09-25)"),
        _row(HOSTING, "Render", "Persistent disk - production (1 GB)",
             "Holds the 18 SQLite stores and kept audio", "1 GB at $0.25/GB",
             "Paying", 0.25, "/month",
             "Grow with AUDIO_CACHE_MAX_MB; $0.25 per extra GB",
             "render.yaml; render.com/pricing (2026-09-25)"),
        _row(HOSTING, "Render", "Persistent disk - staging (1 GB)",
             "Staging's own stores", "1 GB at $0.25/GB", "Paying", 0.25, "/month",
             "", "render.yaml; render.com/pricing (2026-09-25)"),
        _row(HOSTING, "Render", "Outbound bandwidth",
             "Streaming audio to listeners (2.65 MB per minute of PCM)",
             "Included allowance, then $0.15/GB", "Enter from invoice", 0,
             "/month", "Opus transport cuts this ~13x before 10k MAU",
             "Not metered by FAM - copy the line from Render's invoice"),
    ]
    if pod:
        rows.append(_row(
            VOICE, "RunPod", "Always-on GPU pod (Chatterbox)",
            "Speaks every new episode", f"Pod at ${gpu_rate:.2f}/h, 24/7",
            "Paying", round(gpu_rate * 730, 2), "/month",
            "Serverless flex bills only while synthesising",
            "REMOTE_VOICE_TRANSPORT=http; metering.GPU_USD_PER_HOUR"))
    else:
        rows.append(_row(
            VOICE, "RunPod", "Serverless GPU (Chatterbox)",
            "Speaks every new episode; replays cost nothing",
            f"Serverless flex, ${gpu_rate:.2f}/h while working",
            "Usage-billed", None, "usage",
            "Active worker ~$343/mo removes cold starts (at launch)",
            "Recorded per episode in metering.db (gpu_usd)", ledger="gpu"))
    rows.append(_row(
        VOICE, "RunPod", "Network volume (model weights, 20 GB)",
        "Keeps Chatterbox's weights next to the worker", "20 GB at ~$0.07/GB",
        "Estimate - confirm", 1.40, "/month", "",
        "REMOTE_VOICE.md; runpod.io/pricing (verify)"))

    thumbnails_on = bool(_setting("thumbnails", False))
    rows += [
        _row(AI, "Anthropic", f"Claude API ({model})",
             "Writes every brief and script, composes story tiles",
             "Pay as you go: $2 in / $10 out per M tokens (Sonnet 5)",
             "Usage-billed", None, "usage",
             "Raise the usage tier in the console before launch",
             "Recorded per episode in metering.db (claude_usd)", ledger="claude"),
        _row(AI, "Google AI Studio", "Gemini image model (tile pictures)",
             "Paints one picture per category branch",
             "~$0.067 per image, <=60/day",
             "Usage-billed" if thumbnails_on else "Off (THUMBNAILS=0)",
             None, "usage", "",
             "Recorded per picture in thumbnails.db (spend)", ledger="images"),
        _row(DATA, "Exa", "Search API",
             "Researches every new episode", "Pay as you go, ~$0.015/search",
             "Usage-billed", None, "usage",
             "$10 free credit a month (~1,400 searches)",
             "Recorded per episode in metering.db (exa_usd)", ledger="exa"),
    ]

    gnews_plan = str(_setting("gnews_plan", "free") or "free")
    gnews_paid = gnews_plan not in ("", "free")
    rows.append(_row(
        DATA, "GNews", "News API (Trending edition)",
        "Picks the ten Trending stories twice a day",
        f"{gnews_plan.title()} plan" if gnews_paid else "Free (100 req/day, development use only)",
        "Paying" if gnews_paid else "Free tier - licence needed before launch",
        49.99 if gnews_paid else 0, "EUR/month" if gnews_paid else "/month",
        "" if gnews_paid else "Essential EUR 49.99/mo before charging anyone",
        "GNEWS_PLAN; gnews.io/pricing (2026-09-25)"))

    tiers, prices = _sport_tiers()
    paid = {s: t for s, t in tiers.items() if t != "free"}
    for sport, tier in sorted(paid.items()):
        info = prices.get(tier, {})
        rows.append(_row(
            DATA, "API-Sports", f"{sport} ({info.get('label', tier)})",
            "Live scores and story-card score lines",
            f"{info.get('label', tier)}: {info.get('daily', 0):,} req/day",
            "Paying", info.get("usd", 0), "/month", "",
            "API_SPORTS_TIERS; live_sources.TIERS (2026-09-30)"))
    free_sports = sorted(s for s, t in tiers.items() if t == "free")
    if free_sports or not tiers:
        rows.append(_row(
            DATA, "API-Sports",
            f"{len(free_sports) or 10} sports on the free plan",
            "Live scores and story-card score lines",
            "Free: 100 req/day per sport", "Free tier", 0, "/month",
            "Pro $19/mo per sport (7,500 req/day)",
            "API_SPORTS_TIER; live_sources.TIERS (2026-09-30)"))

    finnhub_plan = str(_setting("finnhub_plan", "free") or "free")
    finnhub_paid = finnhub_plan not in ("", "free")
    rows.append(_row(
        DATA, "Finnhub", "Market data",
        "Market facts and market-move story cards",
        f"{finnhub_plan.title()} plan" if finnhub_paid else "Free (personal, non-commercial)",
        "Paying - confirm quote" if finnhub_paid else "Free tier - licence needed before launch",
        11.99 if finnhub_paid else 0, "/month",
        "" if finnhub_paid else "Commercial plan (quote-based, ~$11.99/mo quoted)",
        "FINNHUB_PLAN; PROVIDER_ROLLOUT.md"))

    meteo_paid = _has_key("OPEN_METEO_API_KEY")
    rows.append(_row(
        DATA, "Open-Meteo", "Weather outside the US, place lookup",
        "Forecasts where NWS does not reach",
        "API Standard (1M calls/month)" if meteo_paid else "Not bought",
        "Paying" if meteo_paid else "Free tier - licence needed before launch",
        29 if meteo_paid else 0, "/month",
        "" if meteo_paid else "API Standard $29/mo",
        "OPEN_METEO_API_KEY; open-meteo.com/en/pricing"))

    for vendor, item, purpose in (
            ("Polymarket", "Prediction markets", "Forecast beside outcome questions"),
            ("GDELT", "News export files", "Research fallback and story discovery"),
            ("National Weather Service", "US weather", "Forecasts and warnings"),
            ("Local outlets (RSS)", "Town and county feeds", "Local news")):
        rows.append(_row(DATA, vendor, item, purpose, "Free, keyless",
                         "Free", 0, "/month", "No paid plan exists or is needed",
                         "docs/FINANCIAL.md 3.2"))

    vl = _has_key("VIRAL_LOOPS_API_TOKEN") and bool(
        (os.environ.get("VIRAL_LOOPS_CAMPAIGN_ID") or "").strip())
    rows.append(_row(
        GROWTH, "Viral Loops", "Waitlist referrals",
        "Referral links, fraud checks and emails while WAITLIST=1",
        "Start-up (to 1,000 participants), billed monthly",
        "Paying" if vl else "Not connected", 49 if vl else 0, "/month",
        "$35/mo if billed annually; Plus $99 past 1,000 people; cancel at launch",
        "VIRAL_LOOPS_*; viral-loops.com/pricing (2026-10-05)"))

    rows += [
        _row(DEV, "Apple", "Apple Developer Program",
             "The iOS app and Sign in with Apple", "$99 a year",
             "Confirm", 99, "/year", "", "developer.apple.com (list)"),
        _row(DEV, "GitHub", "Repository and Actions CI",
             "Code, pull requests and the CI that tests every change",
             "Free plan assumed", "Confirm", 0, "/month", "",
             "Enter your plan if you pay for one"),
        _row(DEV, "Google", "Sign in with Google (OAuth)",
             "One of the sign-up options", "Free", "Free", 0, "/month", "",
             "oauth.py"),
        _row(DEV, "Web Push (VAPID)", "Push notifications",
             "Mix-ready pushes", "Free, no vendor", "Free", 0, "/month", "",
             "push.py"),
        _row(CONFIRM, "-", "Domain name",
             "The address people type", "Enter yours", "Enter amount", 0,
             "/year", "", "Not in the repository"),
        _row(CONFIRM, "Anthropic", "Claude plan for the team (Claude Code)",
             "Building FAM", "Enter yours", "Enter amount", 0, "/month", "",
             "Not in the repository"),
        _row(CONFIRM, "-", "Email / workspace",
             "Company email and documents", "Enter yours", "Enter amount", 0,
             "/month", "", "Not in the repository"),
    ]
    return rows


#: The licences a paid launch needs that are not yet bought, and one optional
#: upgrade. The 12-Month Plan counts the included ones from the launch month.
#: API-Sports and Render are not here: the plan sizes those from listeners.
def launch_purchases(rows: list[dict]) -> list[dict]:
    out = []
    bought = {(r["vendor"], r["status"]) for r in rows}
    if ("GNews", "Paying") not in bought:
        out.append({"item": "GNews Essential (commercial licence)",
                    "price": 49.99, "unit": "EUR/month", "include": 1})
    if not any(r["vendor"] == "Finnhub" and r["status"].startswith("Paying") for r in rows):
        out.append({"item": "Finnhub commercial plan (quoted)",
                    "price": 11.99, "unit": "/month", "include": 1})
    if ("Open-Meteo", "Paying") not in bought:
        out.append({"item": "Open-Meteo API Standard",
                    "price": 29, "unit": "/month", "include": 1})
    out.append({"item": "RunPod active worker (no cold starts)",
                "price": 343, "unit": "/month", "include": 0})
    return out


# --- the ledgers -------------------------------------------------------------


def _read(var: str, name: str, sql: str, args: tuple = ()) -> list[tuple]:
    """Rows from a store, or none when it does not exist. Never creates one."""
    path = data_path(var, name)
    if not os.path.exists(path):
        return []
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                                     timeout=5)) as db:
            return list(db.execute(sql, args))
    except sqlite3.Error as exc:
        log.warning("financials: could not read %s: %s", name, exc)
        return []


def _utc_day(at: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(at))


def daily_ledger(now: Optional[float] = None) -> dict:
    """day -> recorded spend and counts, for every UTC day with a record."""
    now = time.time() if now is None else now
    since = now - LEDGER_MAX_DAYS * DAY
    out: dict = {}

    def day(d):
        return out.setdefault(d, {"episodes": 0, "hits": 0, "claude": 0.0,
                                  "exa": 0.0, "gpu": 0.0, "images": 0.0,
                                  "image_count": 0, "live_calls": 0})

    for at, hit, claude, exa, gpu, live in _read(
            "METERING_DB", "metering.db",
            "SELECT at, cache_hit, claude_usd, exa_usd, gpu_usd, live_calls"
            " FROM usage WHERE at >= ?", (since,)):
        d = day(_utc_day(at))
        if hit:
            d["hits"] += 1
        else:
            d["episodes"] += 1
        d["claude"] += float(claude or 0)
        d["exa"] += float(exa or 0)
        d["gpu"] += float(gpu or 0)
        d["live_calls"] += int(live or 0)
    for at, images, cost in _read(
            "THUMBNAILS_DB", "thumbnails.db",
            "SELECT at, images, cost_usd FROM spend WHERE at >= ?", (since,)):
        d = day(_utc_day(at))
        d["images"] += float(cost or 0)
        d["image_count"] += int(images or 0)
    return out


def provider_calls(now: Optional[float] = None, days: int = 30) -> dict:
    """provider -> {day: requests}, for the last `days` UTC days."""
    now = time.time() if now is None else now
    try:
        import provider_usage
        if provider_usage.exists():
            provider_usage.flush()
    except Exception:  # noqa: BLE001
        pass
    out: dict = {}
    for day, provider, requests in _read(
            "PROVIDER_USAGE_DB", "provider_usage.db",
            "SELECT day, provider, requests FROM calls WHERE day >= ?",
            (_utc_day(now - (days - 1) * DAY),)):
        if "/" in provider:
            continue
        out.setdefault(provider, {})[day] = int(requests or 0)
    return out


# --- the workbook ------------------------------------------------------------


def build(now: Optional[float] = None) -> bytes:
    """The whole workbook, as .xlsx bytes."""
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, PieChart, Reference, Series
    from openpyxl.chart.label import DataLabelList
    from openpyxl.comments import Comment
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter as col

    now = time.time() if now is None else now
    today = _dt.datetime.fromtimestamp(now, _dt.timezone.utc).date()

    FONT = "Arial"
    INK, MUTED = "1F2430", "6B7280"
    BRAND, BRAND_SOFT, BAND = "3B2F8F", "ECEAF8", "F7F7FA"
    BLUE, GREEN = "0000FF", "008000"
    MONEY = '$#,##0.00;($#,##0.00);"-"'
    MONEY0 = '$#,##0;($#,##0);"-"'
    COUNT = '#,##0;(#,##0);"-"'
    PCT = '0.0%;(0.0%);"-"'
    DATE = "ddd d mmm yyyy"

    def f(size=10, bold=False, color=INK, italic=False):
        return Font(name=FONT, size=size, bold=bold, color=color, italic=italic)

    fill = lambda c: PatternFill("solid", start_color=c, end_color=c)  # noqa: E731
    thin = Side(style="thin", color="E3E3EA")
    under = Border(bottom=thin)
    top_rule = Border(top=Side(style="medium", color=BRAND))

    def title(ws, text, sub):
        ws.sheet_view.showGridLines = False
        ws["A1"] = text
        ws["A1"].font = f(18, True, BRAND)
        ws["A2"] = sub
        ws["A2"].font = f(10, color=MUTED, italic=True)
        ws.row_dimensions[1].height = 28

    def header(ws, row, labels, start=1):
        for i, label in enumerate(labels):
            c = ws.cell(row=row, column=start + i, value=label)
            c.font = f(10, True, "FFFFFF")
            c.fill = fill(BRAND)
            c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[row].height = 30

    def widths(ws, values):
        for i, w in enumerate(values, start=1):
            ws.column_dimensions[col(i)].width = w

    wb = Workbook()
    dash = wb.active
    dash.title = "Dashboard"
    cs = wb.create_sheet("Cost Structure")
    led = wb.create_sheet("Daily Ledger")
    plan = wb.create_sheet("12-Month Plan")
    calls = wb.create_sheet("Provider Calls")
    notes = wb.create_sheet("Notes")
    for ws, color in ((dash, BRAND), (cs, "5B4FC4"), (led, "2E7D6B"),
                      (plan, "B5651D"), (calls, "6B7280"), (notes, "9CA3AF")):
        ws.sheet_properties.tabColor = color

    AS_OF = "Dashboard!$C$3"

    # ---------------------------------------------------------------- ledger
    recorded = daily_ledger(now)
    first = min(recorded) if recorded else None
    start = today - _dt.timedelta(days=LEDGER_MIN_DAYS - 1)
    if first:
        start = min(start, _dt.date.fromisoformat(first))
    gpu_rate, pod = _gpu()
    days = [start + _dt.timedelta(days=i) for i in range((today - start).days + 1)]

    title(led, "Daily Ledger",
          "One row per UTC day. Spend is recorded by FAM at the moment it happens "
          "(metering.db, thumbnails.db); subscriptions are spread evenly over the year.")
    led_cols = ["Date (UTC)", "Episodes written", "Replays from cache",
                LEDGER_USAGE["claude"], LEDGER_USAGE["exa"], LEDGER_USAGE["gpu"],
                LEDGER_USAGE["images"], "Subscriptions & hosting ($)",
                "Total ($)", "Running total ($)"]
    header(led, 4, led_cols)
    L0 = 5
    LN = L0 + len(days) - 1
    for i, d in enumerate(days):
        r = L0 + i
        rec = recorded.get(d.isoformat(), {})
        led.cell(row=r, column=1, value=d).number_format = DATE
        for c, key, fmt in ((2, "episodes", COUNT), (3, "hits", COUNT),
                            (4, "claude", MONEY), (5, "exa", MONEY),
                            (6, "gpu", MONEY), (7, "images", MONEY)):
            cell = led.cell(row=r, column=c, value=round(rec.get(key, 0), 6))
            cell.number_format = fmt
            cell.font = f(color=BLUE)
        led.cell(row=r, column=8, value="='Cost Structure'!$J$FIXED*12/365")
        # GPU is a fixed rental on a pod: its per-episode figure is then an
        # allocation of a bill already counted, and is left out of the total.
        spend = f"D{r}+E{r}+G{r}+H{r}" if pod else f"SUM(D{r}:H{r})"
        led.cell(row=r, column=9, value=f"={spend}")
        led.cell(row=r, column=10, value=f"=I{r}" if r == L0 else f"=J{r - 1}+I{r}")
        for c in (8, 9, 10):
            led.cell(row=r, column=c).number_format = MONEY
        led.cell(row=r, column=8).font = f(color=GREEN)
        led.cell(row=r, column=9).font = f(bold=True)
        if i % 2:
            for c in range(1, 11):
                led.cell(row=r, column=c).fill = fill(BAND)
        if d == today:
            for c in range(1, 11):
                led.cell(row=r, column=c).fill = fill(BRAND_SOFT)
    tr = LN + 1
    led.cell(row=tr, column=1, value="Total").font = f(bold=True)
    for c in range(2, 10):
        L = col(c)
        cell = led.cell(row=tr, column=c, value=f"=SUM({L}{L0}:{L}{LN})")
        cell.number_format = COUNT if c < 4 else MONEY
        cell.font = f(bold=True)
        cell.border = top_rule
    led.cell(row=tr, column=1).border = top_rule
    led.cell(row=tr, column=10).border = top_rule
    led.freeze_panes = "B5"
    widths(led, [20, 12, 12, 14, 14, 14, 14, 16, 13, 15])
    if pod:
        led["F4"].comment = Comment(
            "The GPU is an always-on pod, billed as a fixed rental on Cost "
            "Structure. This column is each episode's share of it, shown for "
            "interest and left out of Total.", "FAM")

    def led_range(c):
        return f"'Daily Ledger'!${c}${L0}:${c}${LN}"

    LEDGER_COL = {"claude": "D", "exa": "E", "gpu": "F", "images": "G"}

    # --------------------------------------------------------- cost structure
    rows = catalogue()
    title(cs, "Cost Structure",
          "Every service FAM uses, one row each. Usage-billed rows read the last "
          "30 days of the Daily Ledger; the rest are list prices.")
    cs["A3"] = "EUR to USD"
    cs["A3"].font = f(bold=True)
    cs["B3"] = EUR_TO_USD
    cs["B3"].font = f(color=BLUE)
    cs["B3"].fill = fill("FFFF00")
    cs["B3"].number_format = "0.00"
    cs["C3"] = "Assumption, 2026-10-07 - used for prices in euros"
    cs["C3"].font = f(9, color=MUTED, italic=True)
    cols = ["Category", "Vendor", "Item", "What it does for FAM", "Plan in use",
            "Status", "List price", "Price unit", "Basis", "Monthly ($)",
            "Annual ($)", "Next step / upgrade", "Source"]
    header(cs, 5, cols)
    r = 6
    first_row = r
    status_fill = {"Paying": "E7F5EC", "Usage-billed": "E8F0FB", "Free": "F3F4F6",
                   "Free tier": "F3F4F6"}
    for cat in CATEGORIES:
        members = [x for x in rows if x["category"] == cat]
        if not members:
            continue
        for x in members:
            vals = [x["category"], x["vendor"], x["item"], x["purpose"],
                    x["plan"], x["status"]]
            for c, v in enumerate(vals, start=1):
                cell = cs.cell(row=r, column=c, value=v)
                cell.font = f(bold=(c == 2))
                cell.alignment = Alignment(vertical="top", wrap_text=c in (3, 4, 5, 12, 13))
            if x["unit"] == "usage":
                L = LEDGER_COL[x["ledger"]]
                cs.cell(row=r, column=7, value="see ledger").font = f(color=MUTED)
                cs.cell(row=r, column=8, value="usage")
                cs.cell(row=r, column=9, value="Recorded, last 30 days")
                cs.cell(row=r, column=10, value=(
                    f"=SUMIFS({led_range(L)},{led_range('A')},\">\"&({AS_OF}-30))"))
                cs.cell(row=r, column=10).font = f(color=GREEN)
            else:
                p = cs.cell(row=r, column=7, value=x["price"])
                p.font = f(color=BLUE)
                p.number_format = MONEY if x["unit"] != "EUR/month" else '"€"#,##0.00'
                if x["status"].startswith(("Enter", "Confirm", "Estimate")):
                    p.fill = fill("FFFF00")
                cs.cell(row=r, column=8, value=x["unit"])
                cs.cell(row=r, column=9, value="List price" if not x["status"].startswith(
                    ("Enter", "Estimate")) else ("Your figure" if x["status"].startswith(
                        "Enter") else "Estimate"))
                cs.cell(row=r, column=10, value=(
                    f'=IF(H{r}="/year",G{r}/12,IF(H{r}="EUR/month",G{r}*$B$3,G{r}))'))
            cs.cell(row=r, column=10).number_format = MONEY
            cs.cell(row=r, column=11, value=f"=J{r}*12").number_format = MONEY0
            cs.cell(row=r, column=12, value=x["next"])
            cs.cell(row=r, column=13, value=x["source"]).font = f(9, color=MUTED)
            for c in (12, 13):
                cs.cell(row=r, column=c).alignment = Alignment(vertical="top", wrap_text=True)
            for c in (7, 8, 9, 10, 11):
                cs.cell(row=r, column=c).alignment = Alignment(vertical="top")
            key = next((k for k in status_fill if x["status"].startswith(k)), "")
            if key:
                cs.cell(row=r, column=6).fill = fill(status_fill[key])
            if "licence needed" in x["status"] or x["status"].startswith(("Enter", "Confirm")):
                cs.cell(row=r, column=6).fill = fill("FDF2E1")
            for c in range(1, 14):
                cs.cell(row=r, column=c).border = under
            r += 1
    last_row = r - 1
    r += 1
    totals = [
        ("Subscriptions & hosting (fixed)", f'=SUMIFS(J{first_row}:J{last_row},H{first_row}:H{last_row},"<>usage")'),
        ("Usage-billed (trailing 30 days)", f'=SUMIFS(J{first_row}:J{last_row},H{first_row}:H{last_row},"usage")'),
        ("Total monthly run-rate", f"=J{r}+J{r + 1}"),
    ]
    FIXED_ROW = r
    for i, (label, formula) in enumerate(totals):
        cs.cell(row=r + i, column=9, value=label).font = f(bold=True)
        cell = cs.cell(row=r + i, column=10, value=formula)
        cell.number_format = MONEY
        cell.font = f(bold=True, size=11 if i == 2 else 10)
        a = cs.cell(row=r + i, column=11, value=f"=J{r + i}*12")
        a.number_format = MONEY0
        a.font = f(bold=True)
        if i == 2:
            for c in (9, 10, 11):
                cs.cell(row=r + i, column=c).border = top_rule
                cs.cell(row=r + i, column=c).fill = fill(BRAND_SOFT)
    RUNRATE_ROW = r + 2
    cs.freeze_panes = "D6"
    cs.auto_filter.ref = f"A5:M{last_row}"
    widths(cs, [20, 16, 30, 34, 28, 22, 11, 11, 20, 13, 12, 34, 34])
    # The ledger's daily share of subscriptions points here.
    for rr in range(L0, LN + 1):
        led.cell(row=rr, column=8).value = led.cell(row=rr, column=8).value.replace(
            "FIXED", str(FIXED_ROW))

    # -------------------------------------------------------------- dashboard
    title(dash, "FAM - Financials",
          "Pre-revenue. Built from FAM's own spend records each time it is "
          "downloaded; every figure links to the sheets behind it.")
    dash["B3"] = "As of (UTC)"
    dash["B3"].font = f(bold=True, color=MUTED)
    dash["C3"] = today
    dash["C3"].number_format = DATE
    dash["C3"].font = f(bold=True)
    dash["E3"] = "Generated " + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now))
    dash["E3"].font = f(9, color=MUTED, italic=True)

    MTD = (f"=SUMIFS({led_range('I')},{led_range('A')},\">=\"&DATE(YEAR({AS_OF}),"
           f"MONTH({AS_OF}),1),{led_range('A')},\"<=\"&{AS_OF})")
    kpis = [
        ("Spent today", f"=SUMIFS({led_range('I')},{led_range('A')},{AS_OF})", MONEY),
        ("Month to date", MTD, MONEY),
        ("Last 30 days", f"=SUMIFS({led_range('I')},{led_range('A')},\">\"&({AS_OF}-30))", MONEY),
        ("Monthly run-rate", f"='Cost Structure'!J{RUNRATE_ROW}", MONEY),
        ("Revenue (month)", 0, MONEY),
        ("Net burn / month", "=F7-E7", MONEY),
    ]
    for i, (label, formula, fmt) in enumerate(kpis):
        c = 2 + i
        lab = dash.cell(row=6, column=c, value=label)
        lab.font = f(9, True, MUTED)
        lab.fill = fill(BRAND_SOFT)
        lab.alignment = Alignment(horizontal="center")
        val = dash.cell(row=7, column=c, value=formula)
        val.font = f(16, True, BRAND)
        val.number_format = fmt
        val.fill = fill(BRAND_SOFT)
        val.alignment = Alignment(horizontal="center", vertical="center")
    dash.row_dimensions[7].height = 34
    # Net burn reads red when it is a loss.
    dash.conditional_formatting.add("G7", CellIsRule(
        operator="lessThan", formula=["0"], font=Font(name=FONT, size=16, bold=True, color="C0392B")))
    dash["F7"].font = f(16, True, BLUE)
    dash["F7"].fill = fill("FFFF00")
    dash["F7"].comment = Comment("No revenue yet. Type this month's revenue here.", "FAM")

    dash["B9"] = "Cash on hand"
    dash["B9"].font = f(bold=True)
    dash["C9"] = None
    dash["C9"].fill = fill("FFFF00")
    dash["C9"].font = f(color=BLUE)
    dash["C9"].number_format = MONEY0
    dash["C9"].comment = Comment("Type your bank balance here to see runway.", "FAM")
    dash["B10"] = "Runway (months)"
    dash["B10"].font = f(bold=True)
    dash["C10"] = '=IF(AND(ISNUMBER(C9),G7<0),C9/-G7,"-")'
    dash["C10"].number_format = '0.0" months"'
    dash["B11"] = "Yellow cells are yours to fill in; blue numbers are inputs."
    dash["B11"].font = f(9, color=MUTED, italic=True)

    dash["B12"] = "Where the money goes (monthly)"
    dash["B12"].font = f(12, True, INK)
    header(dash, 13, ["Category", "Monthly ($)", "Share"], start=2)
    present = [c for c in CATEGORIES if any(x["category"] == c for x in rows)]
    cr0 = 14
    for i, cat in enumerate(present):
        rr = cr0 + i
        dash.cell(row=rr, column=2, value=cat)
        m = dash.cell(row=rr, column=3, value=(
            f"=SUMIFS('Cost Structure'!$J${first_row}:$J${last_row},"
            f"'Cost Structure'!$A${first_row}:$A${last_row},B{rr})"))
        m.number_format = MONEY
        s = dash.cell(row=rr, column=4, value=f"=IF($E$7=0,0,C{rr}/$E$7)")
        s.number_format = PCT
        for c in (2, 3, 4):
            dash.cell(row=rr, column=c).border = under
    crN = cr0 + len(present) - 1
    tot = crN + 1
    dash.cell(row=tot, column=2, value="Total").font = f(bold=True)
    dash.cell(row=tot, column=3, value=f"=SUM(C{cr0}:C{crN})").number_format = MONEY
    dash.cell(row=tot, column=4, value=f"=SUM(D{cr0}:D{crN})").number_format = PCT
    for c in (2, 3, 4):
        dash.cell(row=tot, column=c).border = top_rule
        dash.cell(row=tot, column=c).font = f(bold=True)

    widths(dash, [3, 26, 16, 16, 16, 18, 18, 4])

    chart = BarChart()
    chart.type = "col"
    chart.grouping = "stacked"
    chart.overlap = 100
    chart.title = "Daily spend, last 30 days"
    chart.y_axis.title = "USD"
    chart.y_axis.numFmt = "$#,##0.00"
    chart.height, chart.width = 8, 24
    s0 = max(L0, LN - 29)
    for c in (4, 5, 6, 7, 8):
        if pod and c == 6:
            continue
        ref = Reference(led, min_col=c, min_row=s0, max_row=LN)
        ser = Series(ref, title=led.cell(row=4, column=c).value)
        chart.series.append(ser)
    chart.set_categories(Reference(led, min_col=1, min_row=s0, max_row=LN))
    chart.x_axis.number_format = "d mmm"
    dash.add_chart(chart, f"B{tot + 3}")

    pie = PieChart()
    pie.title = "Monthly run-rate by category"
    pie.add_data(Reference(dash, min_col=3, min_row=cr0, max_row=crN), titles_from_data=False)
    pie.set_categories(Reference(dash, min_col=2, min_row=cr0, max_row=crN))
    pie.dataLabels = DataLabelList()
    pie.dataLabels.showPercent = True
    pie.height, pie.width = 8, 12
    dash.add_chart(pie, "F12")

    # ------------------------------------------------------------ 12-month plan
    #
    # A forecast driven by listeners, not a percentage on today's bill: cost
    # follows new episodes (searches that miss the cache), and sports searches
    # miss more often - a game in progress is never served from the cache
    # (`ttl-from-evidence`) - and also spend API-Sports requests.
    title(plan, "12-Month Plan",
          "What FAM will cost as listeners arrive. Change the yellow cells; "
          "every month below follows. Sports is modelled on its own.")
    launch = _dt.date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    assumptions = [
        # (label, value, format, note, input?)
        ("Launch month", launch, "mmm yyyy",
         "Listeners and launch licences start counting from this month.", True),
        ("Listeners at launch (monthly active)", 500, COUNT,
         "Assumption. Replace with your launch target.", True),
        ("Listener growth per month", 0.25, PCT,
         "Assumption. Month-over-month growth after launch.", True),
        ("Listeners before launch (testers)", 25, COUNT,
         "Assumption. Who uses it until the launch month.", True),
        ("Searches per listener per month", 15, COUNT,
         "Estimate (docs/FINANCIAL.md 4.1). Search is what writes new episodes.", True),
        ("Plays per listener per month", 45, COUNT,
         "Estimate: 15 searches + 30 browse plays (FINANCIAL.md 4.1).", True),
        ("Share of searches that are sports", 0.40, PCT,
         "Assumption. Raise it if sports leads the launch.", True),
        ("Cache miss rate - general searches", 0.70, PCT,
         "Estimate: 85% at 100 listeners falling to ~55% at 10k (FINANCIAL.md 4.1).", True),
        ("Cache miss rate - sports searches", 0.90, PCT,
         "Assumption: scores move, and a game in progress is never served from cache.", True),
        ("Cost of one new episode ($)", None, MONEY,
         "Recorded: last 30 days' Claude + Exa + GPU per episode written. "
         "$0.06 (FINANCIAL.md 2.4) until there are episodes.", False),
        ("Sports offered (each its own API-Sports plan)", 2, COUNT,
         "Assumption, e.g. NFL and soccer. SCALING_TIMELINE.md: Pro for these at launch.", True),
        ("API-Sports requests per sports search", 2, "0.0",
         "Estimate: the lookup plus the occasional standings call (live_sources.py).", True),
        ("API-Sports background requests per sport per day", 100, COUNT,
         "Estimate: score-card sweeps while games are on - today's whole free allowance.", True),
        ("Background writing: base ($/month)", 120, MONEY,
         "Estimate: Trending, DailyFAM, story pool, categories (FINANCIAL.md 3.3).", True),
        ("Background writing: per listener ($/month)", 0.21, MONEY,
         "Estimate, fitted to FINANCIAL.md 4.2 ($150 at 100, $330 at 1,000).", True),
        ("Background writing: ceiling ($/month)", 700, MONEY,
         "Code: every daily cap hit every day (FINANCIAL.md 3.3).", True),
        ("Bandwidth per play ($)", 0.0008, '$0.0000',
         "Estimate: 2 min x 2.65 MB/min of PCM at Render's $0.15/GB.", True),
    ]
    A = {}
    for i, (label, value, fmt, note, editable) in enumerate(assumptions):
        rr = 4 + i
        A[label] = f"$B${rr}"
        plan.cell(row=rr, column=1, value=label).font = f(bold=True)
        cell = plan.cell(row=rr, column=2, value=value)
        cell.number_format = fmt
        if editable:
            cell.font = f(color=BLUE)
            cell.fill = fill("FFFF00")
        plan.cell(row=rr, column=3, value=note).font = f(9, color=MUTED, italic=True)
    ep = "Cost of one new episode ($)"
    win = f'{led_range("A")},">"&({AS_OF}-30)'
    plan[A[ep].replace("$", "")] = (
        f"=IF(SUMIFS({led_range('B')},{win})>0,"
        f"(SUMIFS({led_range('D')},{win})+SUMIFS({led_range('E')},{win})"
        f"+SUMIFS({led_range('F')},{win})+SUMIFS({led_range('G')},{win}))"
        f"/SUMIFS({led_range('B')},{win}),0.06)")
    plan[A[ep].replace("$", "")].font = f(color=GREEN)
    a = lambda label: A[label]  # noqa: E731

    HEAD = 4 + len(assumptions) + 1
    header(plan, HEAD, ["Month", "", ""] + [None] * 12)
    for m in range(12):
        y, mo = today.year, today.month + 1 + m
        y += (mo - 1) // 12
        mo = (mo - 1) % 12 + 1
        c = plan.cell(row=HEAD, column=4 + m, value=_dt.date(y, mo, 1))
        c.number_format = "mmm yy"
        c.font = f(10, True, "FFFFFF")
        c.fill = fill(BRAND)
    months = [col(4 + m) for m in range(12)]

    def plan_row(rr, label, formula_for, bold=False, fmt=MONEY, note="", info=False):
        plan.cell(row=rr, column=1, value=label).font = f(
            bold=bold, italic=info, color=MUTED if info else INK)
        if note:
            plan.cell(row=rr, column=2, value=note).font = f(8, color=MUTED, italic=True)
        for m, L in enumerate(months):
            cell = plan.cell(row=rr, column=4 + m, value=formula_for(m, L))
            cell.number_format = fmt
            cell.font = f(bold=bold, italic=info, color=MUTED if info else INK)
        for c in range(1, 16):
            plan.cell(row=rr, column=c).border = under

    lb = a("Launch month")
    live = lambda L: f"{L}${HEAD}>={lb}"  # noqa: E731
    cs_sum = lambda vendor: (  # noqa: E731
        f"SUMIFS('Cost Structure'!$J${first_row}:$J${last_row},"
        f"'Cost Structure'!$B${first_row}:$B${last_row},\"{vendor}\","
        f"'Cost Structure'!$H${first_row}:$H${last_row},\"<>usage\")")
    R = {}
    r0 = HEAD + 1
    names = ["revenue", "mau", "searches", "sports", "episodes", "episode_cost",
             "sports_cost", "sports_req", "api_sports", "bandwidth", "hosting",
             "background", "other", "licences", "total", "net", "cumulative",
             "per_listener", "sports_share"]
    for i, n in enumerate(names):
        R[n] = r0 + i
    rv = R

    plan_row(rv["revenue"], "Revenue", lambda m, L: 0)
    for L in months:
        plan[f"{L}{rv['revenue']}"].fill = fill("FFFF00")
        plan[f"{L}{rv['revenue']}"].font = f(color=BLUE)
    plan.cell(row=rv["revenue"], column=1).comment = Comment(
        "No revenue yet. Type expected revenue per month.", "FAM")
    plan_row(rv["mau"], "Listeners (monthly active)", lambda m, L: (
        f"=IF({live(L)},{a('Listeners at launch (monthly active)')}*(1+"
        f"{a('Listener growth per month')})^((YEAR({L}${HEAD})-YEAR({lb}))*12"
        f"+MONTH({L}${HEAD})-MONTH({lb})),{a('Listeners before launch (testers)')})"),
        fmt=COUNT, bold=True)
    plan_row(rv["searches"], "Searches", lambda m, L: (
        f"={L}{rv['mau']}*{a('Searches per listener per month')}"), fmt=COUNT)
    plan_row(rv["sports"], "  of which sports", lambda m, L: (
        f"={L}{rv['searches']}*{a('Share of searches that are sports')}"), fmt=COUNT)
    plan_row(rv["episodes"], "New episodes written (cache misses)", lambda m, L: (
        f"=({L}{rv['searches']}-{L}{rv['sports']})*{a('Cache miss rate - general searches')}"
        f"+{L}{rv['sports']}*{a('Cache miss rate - sports searches')}"), fmt=COUNT)
    plan_row(rv["episode_cost"], "Episodes: Claude, Exa, voice", lambda m, L: (
        f"={L}{rv['episodes']}*{a(ep)}"))
    plan_row(rv["sports_cost"], "  of which sports episodes", lambda m, L: (
        f"={L}{rv['sports']}*{a('Cache miss rate - sports searches')}*{a(ep)}"),
        info=True, note="part of the line above")
    plan_row(rv["sports_req"], "API-Sports requests / sport / day", lambda m, L: (
        f"={L}{rv['sports']}*{a('API-Sports requests per sports search')}/30"
        f"/MAX(1,{a('Sports offered (each its own API-Sports plan)')})"
        f"+{a('API-Sports background requests per sport per day')}"),
        fmt=COUNT, info=True, note="decides the plan below")
    plan_row(rv["api_sports"], "API-Sports plans", lambda m, L: (
        f"=IF({live(L)},{a('Sports offered (each its own API-Sports plan)')}*"
        f"IF({L}{rv['sports_req']}<=7500,19,IF({L}{rv['sports_req']}<=75000,29,39)),"
        f"{cs_sum('API-Sports')})"),
        note="Pro $19 to 7,500/day, Ultra $29, Mega $39")
    plan_row(rv["bandwidth"], "Bandwidth (Render)", lambda m, L: (
        f"={L}{rv['mau']}*{a('Plays per listener per month')}*{a('Bandwidth per play ($)')}"))
    plan_row(rv["hosting"], "Hosting (Render)", lambda m, L: (
        f"=IF({L}{rv['mau']}<1000,{cs_sum('Render')},IF({L}{rv['mau']}<10000,32.5,87.5))"),
        note="Standard at 1k, Pro at 10k (FINANCIAL.md 5.1)")
    plan_row(rv["background"], "Background writing", lambda m, L: (
        f"=MIN({a('Background writing: ceiling ($/month)')},"
        f"{a('Background writing: base ($/month)')}+"
        f"{a('Background writing: per listener ($/month)')}*{L}{rv['mau']})"))
    plan_row(rv["other"], "Other subscriptions (today's)", lambda m, L: (
        f"='Cost Structure'!$J${FIXED_ROW}-{cs_sum('Render')}-{cs_sum('API-Sports')}"))

    purchases = launch_purchases(rows)
    LP0 = rv["sports_share"] + 3
    plan.cell(row=LP0 - 1, column=1, value="Launch licences").font = f(12, True)
    header(plan, LP0, ["Licence", "Monthly ($)", "Include? (1/0)"])
    for i, item in enumerate(purchases):
        rr = LP0 + 1 + i
        plan.cell(row=rr, column=1, value=item["item"])
        price = item["price"]
        p = plan.cell(row=rr, column=2, value=(
            f"={price}*'Cost Structure'!$B$3" if item["unit"] == "EUR/month" else price))
        p.number_format = MONEY
        p.font = f(color=BLUE)
        inc = plan.cell(row=rr, column=3, value=item["include"])
        inc.font = f(color=BLUE)
        inc.fill = fill("FFFF00")
        inc.alignment = Alignment(horizontal="center")
        for c in (1, 2, 3):
            plan.cell(row=rr, column=c).border = under
    LPN = LP0 + max(1, len(purchases))
    plan_row(rv["licences"], "Launch licences", lambda m, L: (
        f"=IF({live(L)},SUMPRODUCT($B${LP0 + 1}:$B${LPN},$C${LP0 + 1}:$C${LPN}),0)"),
        note="table below")
    plan_row(rv["total"], "Total costs", lambda m, L: (
        f"={L}{rv['episode_cost']}+SUM({L}{rv['api_sports']}:{L}{rv['licences']})"),
        bold=True)
    plan_row(rv["net"], "Net (revenue - costs)", lambda m, L: (
        f"={L}{rv['revenue']}-{L}{rv['total']}"), bold=True)
    plan_row(rv["cumulative"], "Cumulative net", lambda m, L: (
        f"={L}{rv['net']}" if m == 0
        else f"={months[m - 1]}{rv['cumulative']}+{L}{rv['net']}"), bold=True)
    plan_row(rv["per_listener"], "Cost per listener", lambda m, L: (
        f"=IF({L}{rv['mau']}>0,{L}{rv['total']}/{L}{rv['mau']},0)"), info=True)
    plan_row(rv["sports_share"], "Sports share of costs", lambda m, L: (
        f"=IF({L}{rv['total']}>0,({L}{rv['sports_cost']}+{L}{rv['api_sports']})"
        f"/{L}{rv['total']},0)"), fmt=PCT, info=True)
    for c in range(1, 16):
        plan.cell(row=rv["total"], column=c).border = top_rule
        plan.cell(row=rv["total"], column=c).fill = fill(BRAND_SOFT)
    plan.freeze_panes = f"D{HEAD + 1}"
    widths(plan, [46, 14, 12] + [11] * 12)
    plan.column_dimensions["C"].width = 12

    growth = BarChart()
    growth.type = "col"
    growth.title = "Monthly costs as listeners grow"
    growth.y_axis.numFmt = "$#,##0"
    growth.height, growth.width = 8, 22
    growth.add_data(Reference(plan, min_col=4, max_col=15, min_row=rv["total"]),
                    from_rows=True, titles_from_data=False)
    growth.set_categories(Reference(plan, min_col=4, max_col=15, min_row=HEAD))
    growth.legend = None
    plan.add_chart(growth, f"E{LP0 - 1}")
    # The note-like assumption text runs long; keep the month grid readable.
    for i in range(len(assumptions)):
        plan.cell(row=4 + i, column=3).alignment = Alignment(wrap_text=False)

    # The forecast's two headline numbers, beside the cash on the Dashboard.
    for rr, label, formula in (
            (9, "Forecast in 12 months ($/month)",
             f"='12-Month Plan'!{months[-1]}{rv['total']}"),
            (10, "Forecast, next 12 months ($)",
             f"=SUM('12-Month Plan'!{months[0]}{rv['total']}:{months[-1]}{rv['total']})")):
        dash.cell(row=rr, column=5, value=label).font = f(bold=True)
        cell = dash.cell(row=rr, column=7, value=formula)
        cell.number_format = MONEY0
        cell.font = f(bold=True, color=GREEN)

    # ---------------------------------------------------------- provider calls
    title(calls, "Provider Calls",
          "Requests sent to each data service per UTC day (provider_usage.db). "
          "Free plans are capped per day; this is how close FAM comes.")
    seen = provider_calls(now, 30)
    try:
        import provider_usage
        names = list(provider_usage.PROVIDERS)
        labels = dict(provider_usage.LABELS)
        try:
            limits = {k: v[0] for k, v in provider_usage._plans().items()}
        except Exception:  # noqa: BLE001
            limits = {}
    except Exception:  # noqa: BLE001
        names, labels, limits = sorted(seen), {}, {}
    header(calls, 4, ["Date (UTC)"] + [labels.get(n, n) for n in names])
    for i in range(30):
        d = today - _dt.timedelta(days=29 - i)
        rr = 5 + i
        calls.cell(row=rr, column=1, value=d).number_format = DATE
        for j, n in enumerate(names):
            cell = calls.cell(row=rr, column=2 + j,
                              value=seen.get(n, {}).get(d.isoformat(), 0))
            cell.number_format = COUNT
            cell.font = f(color=BLUE)
        if i % 2:
            for c in range(1, 2 + len(names)):
                calls.cell(row=rr, column=c).fill = fill(BAND)
    calls.cell(row=35, column=1, value="Daily average").font = f(bold=True)
    calls.cell(row=36, column=1, value="Limit").font = f(bold=True)
    for j, n in enumerate(names):
        L = col(2 + j)
        a = calls.cell(row=35, column=2 + j, value=f"=AVERAGE({L}5:{L}34)")
        a.number_format = "#,##0.0"
        a.font = f(bold=True)
        a.border = top_rule
        lim = calls.cell(row=36, column=2 + j, value=limits.get(n, ""))
        lim.font = f(8, color=MUTED)
        lim.alignment = Alignment(wrap_text=True, vertical="top")
    calls.row_dimensions[36].height = 90
    calls.freeze_panes = "B5"
    widths(calls, [20] + [15] * len(names))

    # ------------------------------------------------------------------ notes
    title(notes, "Notes", "How to read this workbook.")
    lines = [
        ("How it updates",
         "Production builds this file on request at /api/admin/financials.xlsx "
         "(the admin page has a Download button). Each copy is current to the "
         "minute it was downloaded; download again tomorrow for tomorrow's numbers. "
         "Inputs you type (yellow cells) live in your copy only."),
        ("Recorded", "Claude, Exa and GPU spend per episode (metering.db), tile "
         "pictures (thumbnails.db) and provider requests (provider_usage.db), written "
         "by FAM at the moment of spend. Days are UTC."),
        ("List price", "Published prices with the date they were looked up. "
         "Re-check before buying anything. Render bandwidth is not metered by FAM: "
         "enter it from the invoice."),
        ("Assumption", "Blue numbers you can change: the EUR rate, usage growth, "
         "which launch purchases to include, revenue."),
        ("GPU", "Serverless voice is billed by the second, so the ledger's GPU "
         "column is the bill. On an always-on pod the rental is a fixed line and "
         "the ledger's GPU column is left out of the total."),
        ("Not in the ledger", "Background Claude calls that are not an episode "
         "(the story-tile composer, category placement, prefetched briefs) are "
         "billed by Anthropic but not recorded per call; compare the Claude column "
         "with the Anthropic console once a month."),
        ("Colours", "Blue text: an input or recorded figure. Black: a formula. "
         "Green: a link to another sheet. Yellow fill: yours to fill in or confirm."),
        ("12-Month Plan", "Costs follow new episodes: searches that miss the "
         "cache. Sports searches are modelled apart because they miss it more "
         "(a game in progress is never served from cache) and spend API-Sports "
         "requests, which decide each sport's plan. The cost of one episode is "
         "the recorded average once there are episodes, so the forecast sharpens "
         "as real traffic arrives. Listener numbers are yours to set."),
        ("Adding a service", "The list lives in financials.py (catalogue). Add a "
         "row there and every copy from then on carries it."),
        ("Detail", "docs/FINANCIAL.md explains every cost and when to upgrade; "
         "docs/SCALING_TIMELINE.md says when each plan is worth buying."),
    ]
    for i, (k, v) in enumerate(lines):
        rr = 4 + i
        notes.cell(row=rr, column=1, value=k).font = f(bold=True)
        c = notes.cell(row=rr, column=2, value=v)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        notes.cell(row=rr, column=1).alignment = Alignment(vertical="top")
        notes.row_dimensions[rr].height = 48
    widths(notes, [20, 110])

    for ws in wb.worksheets:
        ws.sheet_view.zoomScale = 110
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
    wb.active = 0
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def filename(now: Optional[float] = None) -> str:
    return "FAM_Financials_" + _utc_day(time.time() if now is None else now) + ".xlsx"
