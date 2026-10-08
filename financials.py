"""The finance workbook: every service FAM pays for, and what it spent each day.

    GET /api/admin/financials.xlsx         built on request, so always current
    python tools/financials.py             the same workbook from local stores
    python tools/financials.py --remote https://<host>   pull it from a server

One workbook, written fresh from the deployment's own records every time it
is asked for - so "updates daily" is a property of where the numbers come
from, not of a job that has to remember to run. Costs and projections only:

* **Costs** - every outside service, one row each: what it is for, the plan
  in force, its monthly and annual cost. Usage-billed rows add up the last 30
  days of Daily Spend; the rest are list prices.
* **Marketing & Materials** - a budget per line: before launch, after launch
  and one-off at launch, spread over the twelve months.
* **Projections** - twelve months of costs driven by listeners, with sports
  apart (it misses the cache more and spends API-Sports requests), marketing
  from the tab above, launch licences, revenue to fill in, and every rate in a
  labelled cell.
* **Daily Spend** - one row per UTC day of recorded spend (`metering.py`,
  `thumbnails.py`).

Plain formulas only (SUMIFS, IF, MIN, SUMPRODUCT), no charts or macros, so it
opens the same in Excel and in Google Sheets. Yellow cells are inputs.

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
# or "usage" (read from the Daily Spend column named in `ledger`). Prices are
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
        _row(HOSTING, "Render", "Production web service (fam)",
             "Runs the app and API listeners talk to", "Starter (512 MB, 0.5 CPU)",
             "Paying", 7, "/month",
             "Standard $25/mo (2 GB) at ~20-40 simultaneous streams",
             "render.yaml; render.com/pricing (2026-09-25)"),
        _row(HOSTING, "Render", "Staging web service (fam-staging)",
             "Where every merge is tried first, zero spend",
             "Starter", "Paying", 7, "/month", "",
             "render.yaml; render.com/pricing (2026-09-25)"),
        _row(HOSTING, "Render", "Production disk (1 GB)",
             "Holds the 18 SQLite stores and kept audio", "1 GB at $0.25/GB",
             "Paying", 0.25, "/month",
             "Grow with AUDIO_CACHE_MAX_MB; $0.25 per extra GB",
             "render.yaml; render.com/pricing (2026-09-25)"),
        _row(HOSTING, "Render", "Staging disk (1 GB)",
             "Staging's own stores", "1 GB at $0.25/GB", "Paying", 0.25, "/month",
             "", "render.yaml; render.com/pricing (2026-09-25)"),
        _row(HOSTING, "Render", "Outbound bandwidth",
             "Streaming audio to listeners (2.65 MB per minute of PCM)",
             "Included allowance, then $0.15/GB", "Enter from invoice", 0,
             "/month", "Opus transport cuts this ~13x before 10k MAU",
             "Not metered by FAM: copy the line from Render's invoice"),
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
        "Estimate, confirm", 1.40, "/month", "",
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
        "Paying" if gnews_paid else "Free tier, licence needed before launch",
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
        "Paying, confirm quote" if finnhub_paid else "Free tier, licence needed before launch",
        11.99 if finnhub_paid else 0, "/month",
        "" if finnhub_paid else "Commercial plan (quote-based, ~$11.99/mo quoted)",
        "FINNHUB_PLAN; PROVIDER_ROLLOUT.md"))

    meteo_paid = _has_key("OPEN_METEO_API_KEY")
    rows.append(_row(
        DATA, "Open-Meteo", "Weather outside the US, place lookup",
        "Forecasts where NWS does not reach",
        "API Standard (1M calls/month)" if meteo_paid else "Not bought",
        "Paying" if meteo_paid else "Free tier, licence needed before launch",
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
#: upgrade. Projections counts the included ones from the launch month.
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


def daily_rows(now: Optional[float] = None) -> list[list]:
    """The Daily Spend tab as rows: [date, episodes, Claude, Exa, GPU, pictures].

    Every UTC day from the first record (or `LEDGER_MIN_DAYS` ago) to today,
    days with no spend included as zeros, so a sheet that replaces its rows
    with these has no gaps. Shared by the workbook and the JSON the Google
    Sheet's Apps Script pulls each morning.
    """
    now = time.time() if now is None else now
    today = _dt.datetime.fromtimestamp(now, _dt.timezone.utc).date()
    recorded = daily_ledger(now)
    start = today - _dt.timedelta(days=LEDGER_MIN_DAYS - 1)
    if recorded:
        start = min(start, _dt.date.fromisoformat(min(recorded)))
    rows = []
    for i in range((today - start).days + 1):
        d = start + _dt.timedelta(days=i)
        rec = recorded.get(d.isoformat(), {})
        rows.append([d.isoformat(), int(rec.get("episodes", 0))] + [
            round(rec.get(k, 0.0), 6) for k in ("claude", "exa", "gpu", "images")])
    return rows


#: The marketing and materials budget: (section, line, before launch per
#: month, after launch per month, one-off in the launch month). Starting
#: placeholders - every figure is a yellow input on the sheet for the owner to
#: set; none of them is a quote or a commitment.
MARKETING = [
    ("Marketing", "Paid social ads (Instagram, TikTok, X)", 0, 1000, 0),
    ("Marketing", "Apple Search Ads (App Store)", 0, 500, 0),
    ("Marketing", "Creator and sports-influencer partnerships", 0, 750, 0),
    ("Marketing", "Content production (clips, trailers, socials)", 200, 400, 0),
    ("Marketing", "Sports-fan communities and podcast sponsorships", 0, 300, 0),
    ("Marketing", "Referral rewards (waitlist and invite prizes)", 0, 250, 0),
    ("Marketing", "PR and launch push", 0, 0, 1500),
    ("Materials", "Brand and design assets (logo, art, templates)", 0, 0, 500),
    ("Materials", "Merch and swag (shirts, hats)", 0, 0, 1000),
    ("Materials", "Printed materials (stickers, flyers, cards)", 50, 50, 150),
    ("Materials", "Events and tabling (game days, campus)", 0, 300, 0),
    ("Materials", "Photo and video equipment", 0, 0, 0),
]


def build(now: Optional[float] = None) -> bytes:
    """The whole workbook, as .xlsx bytes.

    Costs, Marketing & Materials and Projections, then Daily Spend, the
    recorded days the Costs tab adds up. Plain formulas (SUMIFS, IF,
    MIN, SUMPRODUCT), no charts or macros, so it imports into Google Sheets
    unchanged (File > Import, or an upload to Drive).
    """
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter as col

    now = time.time() if now is None else now
    today = _dt.datetime.fromtimestamp(now, _dt.timezone.utc).date()

    FONT = "Arial"
    INK, MUTED = "1F2430", "6B7280"
    BRAND, BRAND_SOFT, BAND = "3B2F8F", "ECEAF8", "F7F7FA"
    BLUE, GREEN, YELLOW = "0000FF", "008000", "FFF59D"
    MONEY = '$#,##0.00;($#,##0.00);"-"'
    MONEY0 = '$#,##0;($#,##0);"-"'
    COUNT = '#,##0;(#,##0);"-"'
    PCT = '0.0%;(0.0%);"-"'

    def f(size=10, bold=False, color=INK, italic=False):
        return Font(name=FONT, size=size, bold=bold, color=color, italic=italic)

    fill = lambda c: PatternFill("solid", start_color=c, end_color=c)  # noqa: E731
    under = Border(bottom=Side(style="thin", color="E3E3EA"))
    rule = Border(top=Side(style="medium", color=BRAND))

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
        ws.row_dimensions[row].height = 28

    def widths(ws, values):
        for i, w in enumerate(values, start=1):
            ws.column_dimensions[col(i)].width = w

    def entry(cell, fmt=MONEY):
        """A number the owner types: blue on yellow."""
        cell.font = f(color=BLUE)
        cell.fill = fill(YELLOW)
        cell.number_format = fmt

    wb = Workbook()
    costs = wb.active
    costs.title = "Costs"
    mkt = wb.create_sheet("Marketing & Materials")
    proj = wb.create_sheet("Projections")
    led = wb.create_sheet("Daily Spend")
    for ws, color in ((costs, BRAND), (mkt, "B5651D"), (proj, "2E7D6B"),
                      (led, "9CA3AF")):
        ws.sheet_properties.tabColor = color

    # --------------------------------------------------------------- ledger
    # One row per UTC day of recorded spend, which the usage-billed rows on
    # Costs add up. Written fresh on every download.
    ledger = daily_rows(now)
    _, pod = _gpu()
    header(led, 1, ["Date (UTC)", "Episodes written", LEDGER_USAGE["claude"],
                    LEDGER_USAGE["exa"], LEDGER_USAGE["gpu"], LEDGER_USAGE["images"]])
    L0, LN = 2, 1 + len(ledger)
    for i, row in enumerate(ledger):
        led.cell(row=L0 + i, column=1,
                 value=_dt.date.fromisoformat(row[0])).number_format = "yyyy-mm-dd"
        for c, value in enumerate(row[1:], start=2):
            cell = led.cell(row=L0 + i, column=c, value=value)
            cell.number_format = COUNT if c == 2 else MONEY
    widths(led, [14, 12, 14, 14, 14, 14])
    led["A1"].comment = Comment(
        "Source: FAM's spend records (metering.db, thumbnails.db), one row per "
        f"UTC day, written {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(now))}.",
        "FAM")
    led.freeze_panes = "A2"
    LEDGER_COL = {"claude": "C", "exa": "D", "gpu": "E", "images": "F"}
    rng = lambda c: f"'Daily Spend'!${c}${L0}:${c}${LN}"  # noqa: E731
    AS_OF = "Costs!$B$3"
    last30 = lambda c: f'SUMIFS({rng(c)},{rng("A")},">"&({AS_OF}-30))'  # noqa: E731

    # ---------------------------------------------------------------- costs
    title(costs, "FAM costs",
          "Every service FAM pays for. Usage-billed lines are what was actually "
          "spent in the last 30 days; the rest are list prices.")
    costs["A3"] = "As of"
    costs["A3"].font = f(bold=True, color=MUTED)
    costs["B3"] = today
    costs["B3"].number_format = "d mmm yyyy"
    costs["B3"].font = f(bold=True)
    costs["C3"] = ("Download a new copy for new numbers: /admin > Financials, "
                   "or the morning delivery.")
    costs["C3"].font = f(9, color=MUTED, italic=True)

    rows = catalogue()
    free = [r for r in rows if r["status"] == "Free"]
    kept = [r for r in rows if r not in free]
    HEAD = 9
    header(costs, HEAD, ["Category", "Service", "What it's for", "Plan",
                         "Status", "Monthly ($)", "Annual ($)", "Notes"])
    r = HEAD + 1
    R0 = r
    for cat in CATEGORIES:
        for x in (x for x in kept if x["category"] == cat):
            label = x["vendor"] if x["vendor"] not in ("-",) else x["item"]
            what = x["item"] if x["vendor"] not in ("-",) else x["purpose"]
            vals = [cat, label, what, x["plan"], x["status"]]
            for c, v in enumerate(vals, start=1):
                cell = costs.cell(row=r, column=c, value=v)
                cell.font = f(bold=(c == 2))
                cell.alignment = Alignment(vertical="top", wrap_text=c in (3, 4, 5))
            m = costs.cell(row=r, column=6)
            if x["unit"] == "usage":
                m.value = "=" + last30(LEDGER_COL[x["ledger"]])
                m.font = f(color=GREEN)
                note = "Recorded spend, last 30 days. " + x["next"]
            elif x["unit"] == "/year" and x["price"]:
                m.value = f"={x['price']}/12"
                entry(m)
                note = f"${x['price']:,.0f} a year. " + x["next"]
            elif x["unit"] == "EUR/month":
                m.value = f"={x['price']}*{EUR_TO_USD}"
                entry(m)
                note = f"EUR {x['price']:,.2f} a month at {EUR_TO_USD} USD. " + x["next"]
            else:
                m.value = x["price"]
                entry(m)
                note = x["next"] or ("Yearly price divided by 12." if x["unit"] == "/year" else "")
            m.number_format = MONEY
            costs.cell(row=r, column=7, value=f"=F{r}*12").number_format = MONEY0
            n = costs.cell(row=r, column=8, value=note.strip())
            n.font = f(9, color=MUTED)
            n.alignment = Alignment(vertical="top", wrap_text=True)
            for c in range(1, 9):
                costs.cell(row=r, column=c).border = under
                if c in (6, 7):
                    costs.cell(row=r, column=c).alignment = Alignment(vertical="top")
            r += 1
    names = ", ".join(x["vendor"] for x in free)
    costs.cell(row=r, column=1, value=DATA)
    costs.cell(row=r, column=2, value="Free services").font = f(bold=True)
    costs.cell(row=r, column=3, value=names).alignment = Alignment(wrap_text=True, vertical="top")
    costs.cell(row=r, column=4, value="Free, no paid plan")
    costs.cell(row=r, column=5, value="Free")
    costs.cell(row=r, column=6, value=0).number_format = MONEY
    costs.cell(row=r, column=7, value=f"=F{r}*12").number_format = MONEY0
    for c in range(1, 9):
        costs.cell(row=r, column=c).border = under
    RN = r
    # The three numbers that matter, above the list.
    summary = [
        ("Subscriptions & hosting", f'=SUMPRODUCT((LEFT(H{R0}:H{RN},8)<>"Recorded")*F{R0}:F{RN})'),
        ("Usage (last 30 days)", f'=SUMPRODUCT((LEFT(H{R0}:H{RN},8)="Recorded")*F{R0}:F{RN})'),
        ("Total per month", "=B6+C6"),
    ]
    for i, (label, formula) in enumerate(summary):
        lab = costs.cell(row=5, column=2 + i, value=label)
        lab.font = f(9, True, MUTED)
        lab.fill = fill(BRAND_SOFT)
        lab.alignment = Alignment(horizontal="center")
        val = costs.cell(row=6, column=2 + i, value=formula)
        val.font = f(15, True, BRAND)
        val.fill = fill(BRAND_SOFT)
        val.number_format = MONEY
        val.alignment = Alignment(horizontal="center", vertical="center")
    costs.row_dimensions[6].height = 30
    costs["E5"] = "Yellow: yours to change. Green: added up from FAM's own records."
    costs["E5"].font = f(9, color=MUTED, italic=True)
    costs.freeze_panes = f"C{HEAD + 1}"
    widths(costs, [22, 20, 34, 30, 24, 13, 12, 44])
    TOTAL_MONTHLY = "Costs!$D$6"
    cs_vendor = lambda v: (  # noqa: E731
        f'SUMIFS(Costs!$F${R0}:$F${RN},Costs!$B${R0}:$B${RN},"{v}")')
    FIXED = "Costs!$B$6"
    USAGE = "Costs!$C$6"

    # --------------------------------------------------- the twelve months
    months_dates = []
    for m in range(12):
        y, mo = today.year, today.month + 1 + m
        y += (mo - 1) // 12
        mo = (mo - 1) % 12 + 1
        months_dates.append(_dt.date(y, mo, 1))
    months = [col(4 + m) for m in range(12)]

    def month_header(ws, row, first_label, rest=("", "")):
        header(ws, row, [first_label, *rest])
        for m, d in enumerate(months_dates):
            c = ws.cell(row=row, column=4 + m, value=d)
            c.number_format = "mmm yy"
            c.font = f(10, True, "FFFFFF")
            c.fill = fill(BRAND)
            c.alignment = Alignment(horizontal="center")

    # ---------------------------------------------- marketing and materials
    title(mkt, "Marketing & Materials",
          "Budget by month. Set what each line gets per month before and after "
          "launch, plus anything spent once at launch; the months fill in.")
    mkt["A3"] = "Launch month"
    mkt["A3"].font = f(bold=True)
    mkt["B3"] = "=Projections!$B$4"
    mkt["B3"].number_format = "mmm yyyy"
    mkt["B3"].font = f(bold=True, color=GREEN)
    mkt["C3"] = "Set on Projections."
    mkt["C3"].font = f(9, color=MUTED, italic=True)
    MH = 5
    header(mkt, MH, ["Line", "Before launch / month", "After launch / month",
                     "One-off at launch"])
    for m, d in enumerate(months_dates):
        c = mkt.cell(row=MH, column=5 + m, value=d)
        c.number_format = "mmm yy"
        c.font = f(10, True, "FFFFFF")
        c.fill = fill(BRAND)
        c.alignment = Alignment(horizontal="center")
    mmonths = [col(5 + m) for m in range(12)]
    rr = MH + 1
    section_totals = {}
    for section in ("Marketing", "Materials"):
        mkt.cell(row=rr, column=1, value=section).font = f(11, True, BRAND)
        rr += 1
        s0 = rr
        for sec, line, pre, post, once in MARKETING:
            if sec != section:
                continue
            mkt.cell(row=rr, column=1, value=line)
            for c, v in ((2, pre), (3, post), (4, once)):
                entry(mkt.cell(row=rr, column=c, value=v), MONEY0)
            for m, L in enumerate(mmonths):
                cell = mkt.cell(row=rr, column=5 + m, value=(
                    f"=IF({L}${MH}<$B$3,$B{rr},$C{rr})+IF({L}${MH}=$B$3,$D{rr},0)"))
                cell.number_format = MONEY0
            for c in range(1, 17):
                mkt.cell(row=rr, column=c).border = under
            rr += 1
        sN = rr - 1
        mkt.cell(row=rr, column=1, value=f"{section} total").font = f(bold=True)
        for c in range(2, 17):
            L = col(c)
            cell = mkt.cell(row=rr, column=c, value=f"=SUM({L}{s0}:{L}{sN})")
            cell.number_format = MONEY0
            cell.font = f(bold=True)
            cell.border = rule
        mkt.cell(row=rr, column=1).border = rule
        section_totals[section] = rr
        rr += 2
    MT = rr
    mkt.cell(row=MT, column=1, value="Marketing & materials total").font = f(11, True)
    for c in range(2, 17):
        L = col(c)
        cell = mkt.cell(row=MT, column=c, value="=" + "+".join(
            f"{L}{t}" for t in section_totals.values()))
        cell.number_format = MONEY0
        cell.font = f(11, True)
        cell.fill = fill(BRAND_SOFT)
    mkt.cell(row=MT, column=1).fill = fill(BRAND_SOFT)
    mkt.cell(row=MT + 2, column=1, value=(
        "Starting placeholders, not quotes: change every yellow cell to your own "
        "plan. Add a line by inserting a row inside a section; the totals follow."
    )).font = f(9, color=MUTED, italic=True)
    mkt.freeze_panes = f"B{MH + 1}"
    widths(mkt, [44, 13, 13, 13] + [10] * 12)
    MKT_TOTAL = lambda m: f"'Marketing & Materials'!{mmonths[m]}${MT}"  # noqa: E731

    # ---------------------------------------------------------- projections
    title(proj, "Projections",
          "Twelve months of costs as listeners arrive. Change the yellow cells; "
          "every month follows. Sports is modelled on its own.")
    launch = months_dates[0]
    assumptions = [
        ("Launch month", launch, "mmm yyyy", "Licences, launch marketing and listeners start here."),
        ("Listeners at launch", 500, COUNT, "Monthly active listeners in the launch month."),
        ("Listener growth per month", 0.25, PCT, "Month-over-month growth after launch."),
        ("Listeners before launch", 25, COUNT, "Testers until the launch month."),
        ("Searches per listener per month", 15, COUNT, "Searches are what write new episodes."),
        ("Plays per listener per month", 45, COUNT, "All surfaces; drives bandwidth."),
        ("Share of searches that are sports", 0.40, PCT, "Raise it if sports leads the launch."),
        ("New episodes per search, general", 0.70, PCT, "The rest replay a kept episode for almost nothing."),
        ("New episodes per search, sports", 0.90, PCT, "Higher: a game in progress is never replayed from the cache."),
        ("Cost of one new episode", None, MONEY, "Recorded average once there are episodes; $0.06 until then."),
        ("Sports offered", 2, COUNT, "Each is its own API-Sports plan (e.g. NFL and soccer)."),
    ]
    A = {}
    for i, (label, value, fmt, note) in enumerate(assumptions):
        row = 4 + i
        A[label] = f"$B${row}"
        proj.cell(row=row, column=1, value=label).font = f(bold=True)
        cell = proj.cell(row=row, column=2, value=value)
        if value is None:
            cell.value = (f"=IF(SUMIFS({rng('B')},{rng('A')},\">\"&({AS_OF}-30))>0,"
                          f"({last30('C')}+{last30('D')}+{last30('E')}+{last30('F')})"
                          f"/SUMIFS({rng('B')},{rng('A')},\">\"&({AS_OF}-30)),0.06)")
            cell.font = f(color=GREEN)
            cell.number_format = fmt
        else:
            entry(cell, fmt)
        proj.cell(row=row, column=3, value=note).font = f(9, color=MUTED, italic=True)
    a = lambda label: A[label]  # noqa: E731
    PH = 4 + len(assumptions) + 1
    month_header(proj, PH, "Month")
    lb = a("Launch month")
    live = lambda L: f"{L}${PH}>={lb}"  # noqa: E731
    names = ["mau", "searches", "sports", "episodes", "episode_cost", "api_sports",
             "hosting", "bandwidth", "subs", "licences", "marketing", "total",
             "revenue", "net", "cumulative", "per_listener", "sports_share"]
    R = {n: PH + 1 + i for i, n in enumerate(names)}

    # Every rate the months use, in its own labelled cell under the table.
    purchases = launch_purchases(rows)
    licences = [p for p in purchases if p["include"]]
    rates = [
        ("fallback", "Cost of one new episode before there is traffic", 0.06, MONEY,
         "Estimate, docs/FINANCIAL.md 2.4"),
        ("req_per_search", "API-Sports requests per sports search", 2, "0.0",
         "Estimate: the lookup plus the occasional standings call"),
        ("req_cards", "API-Sports score-card requests per sport per day", 100, COUNT,
         "Estimate: today's free allowance, used by score cards"),
        ("pro_limit", "API-Sports Pro: requests per day", 7500, COUNT, "api-sports.io, 2026-09-30"),
        ("pro", "API-Sports Pro: per sport per month", 19, MONEY, "api-sports.io, 2026-09-30"),
        ("ultra_limit", "API-Sports Ultra: requests per day", 75000, COUNT, "api-sports.io, 2026-09-30"),
        ("ultra", "API-Sports Ultra: per sport per month", 29, MONEY, "api-sports.io, 2026-09-30"),
        ("mega", "API-Sports Mega: per sport per month", 39, MONEY, "api-sports.io, 2026-09-30"),
        ("std_at", "Render Standard from this many listeners", 1000, COUNT,
         "docs/FINANCIAL.md 5.1"),
        ("std", "Render on Standard, per month", 32.5, MONEY,
         "render.com: $25 production + $7 staging + disks"),
        ("pro_at", "Render Pro from this many listeners", 10000, COUNT, "docs/FINANCIAL.md 5.1"),
        ("rpro", "Render on Pro, per month", 87.5, MONEY,
         "render.com: $80 production + $7 staging + disks"),
        ("bw", "Bandwidth per play", 0.0008, "$0.0000",
         "Estimate: 2 min at 2.65 MB/min, $0.15/GB (Render)"),
        ("bg_base", "Background writing: base per month", 120, MONEY,
         "Estimate, docs/FINANCIAL.md 3.3 and 4.2"),
        ("bg_per", "Background writing: per listener per month", 0.21, MONEY,
         "Estimate, docs/FINANCIAL.md 4.2"),
        ("bg_cap", "Background writing: ceiling per month", 700, MONEY,
         "Code: every daily cap reached, docs/FINANCIAL.md 3.3"),
    ] + [
        (f"lic{i}", p["item"], (f"={p['price']}*{EUR_TO_USD}" if p["unit"] == "EUR/month"
                                else p["price"]), MONEY,
         "Launch licence, list price" + (f" (EUR {p['price']} at {EUR_TO_USD})"
                                         if p["unit"] == "EUR/month" else ""))
        for i, p in enumerate(licences)
    ]
    RS = R["sports_share"] + 5
    RATE = {}
    proj.cell(row=RS - 1, column=1, value="Rates used above").font = f(11, True, BRAND)
    for i, (key, label, value, fmt, source) in enumerate(rates):
        row = RS + i
        RATE[key] = f"$B${row}"
        proj.cell(row=row, column=1, value=label)
        entry(proj.cell(row=row, column=2, value=value), fmt)
        proj.cell(row=row, column=3, value="Source: " + source).font = f(9, color=MUTED, italic=True)
    t = lambda k: RATE[k]  # noqa: E731
    lic_cells = [RATE[f"lic{i}"] for i in range(len(licences))]

    def prow(n, label, formula_for, fmt=MONEY, bold=False, muted=False):
        rr = R[n]
        proj.cell(row=rr, column=1, value=label).font = f(
            bold=bold, color=MUTED if muted else INK, italic=muted)
        for m, L in enumerate(months):
            cell = proj.cell(row=rr, column=4 + m, value=formula_for(m, L))
            cell.number_format = fmt
            cell.font = f(bold=bold, color=MUTED if muted else INK, italic=muted)
        for c in range(1, 16):
            proj.cell(row=rr, column=c).border = under

    ep_cell = proj[A["Cost of one new episode"].replace("$", "")]
    ep_cell.value = ep_cell.value.replace(",0.06)", f",{t('fallback')})")
    prow("mau", "Listeners", lambda m, L: (
        f"=ROUND(IF({live(L)},{a('Listeners at launch')}*(1+{a('Listener growth per month')})"
        f"^((YEAR({L}${PH})-YEAR({lb}))*12+MONTH({L}${PH})-MONTH({lb})),"
        f"{a('Listeners before launch')}),0)"), COUNT, bold=True)
    prow("searches", "Searches", lambda m, L: (
        f"={L}{R['mau']}*{a('Searches per listener per month')}"), COUNT)
    prow("sports", "  of which sports", lambda m, L: (
        f"={L}{R['searches']}*{a('Share of searches that are sports')}"), COUNT, muted=True)
    prow("episodes", "New episodes written", lambda m, L: (
        f"=({L}{R['searches']}-{L}{R['sports']})*{a('New episodes per search, general')}"
        f"+{L}{R['sports']}*{a('New episodes per search, sports')}"), COUNT)
    prow("episode_cost", "Episodes (Claude, Exa, voice)", lambda m, L: (
        f"={L}{R['episodes']}*{a('Cost of one new episode')}"))
    # Requests per sport per day: two per sports search, plus ~100 for the
    # score cards; that picks Pro (7,500/day), Ultra (75k) or Mega.
    req = lambda L: (  # noqa: E731
        f"({L}{R['sports']}*{t('req_per_search')}/30/MAX(1,{a('Sports offered')})"
        f"+{t('req_cards')})")
    prow("api_sports", "Sports data (API-Sports)", lambda m, L: (
        f"=IF({live(L)},{a('Sports offered')}*IF({req(L)}<={t('pro_limit')},{t('pro')},"
        f"IF({req(L)}<={t('ultra_limit')},{t('ultra')},{t('mega')})),"
        f"{cs_vendor('API-Sports')})"))
    prow("hosting", "Hosting (Render)", lambda m, L: (
        f"=IF({L}{R['mau']}<{t('std_at')},{cs_vendor('Render')},IF({L}{R['mau']}<{t('pro_at')},{t('std')},{t('rpro')}))"))
    prow("bandwidth", "Bandwidth", lambda m, L: (
        f"={L}{R['mau']}*{a('Plays per listener per month')}*{t('bw')}"))
    prow("subs", "Other subscriptions and background writing", lambda m, L: (
        f"={FIXED}-{cs_vendor('Render')}-{cs_vendor('API-Sports')}"
        f"+MIN({t('bg_cap')},{t('bg_base')}+{t('bg_per')}*{L}{R['mau']})"))
    prow("licences", "Launch licences", lambda m, L: (
        f"=IF({live(L)},{'+'.join(lic_cells) or 0},0)"))
    prow("marketing", "Marketing & materials", lambda m, L: f"={MKT_TOTAL(m)}")
    for L in months:
        proj[f"{L}{R['marketing']}"].font = f(color=GREEN)
    prow("total", "Total costs", lambda m, L: (
        f"=SUM({L}{R['episode_cost']}:{L}{R['marketing']})"), bold=True)
    prow("revenue", "Revenue", lambda m, L: 0)
    for L in months:
        entry(proj[f"{L}{R['revenue']}"])
    prow("net", "Net (revenue - costs)", lambda m, L: (
        f"={L}{R['revenue']}-{L}{R['total']}"), bold=True)
    prow("cumulative", "Cumulative", lambda m, L: (
        f"={L}{R['net']}" if m == 0 else f"={months[m - 1]}{R['cumulative']}+{L}{R['net']}"),
        bold=True)
    prow("per_listener", "Cost per listener", lambda m, L: (
        f"=IF({L}{R['mau']}>0,{L}{R['total']}/{L}{R['mau']},0)"), muted=True)
    prow("sports_share", "Sports share of running costs", lambda m, L: (
        f"=IF({L}{R['total']}-{L}{R['marketing']}>0,"
        f"({L}{R['sports']}*{a('New episodes per search, sports')}*{a('Cost of one new episode')}"
        f"+{L}{R['api_sports']})/({L}{R['total']}-{L}{R['marketing']}),0)"), PCT, muted=True)
    for c in range(1, 16):
        proj.cell(row=R["total"], column=c).border = rule
        proj.cell(row=R["total"], column=c).fill = fill(BRAND_SOFT)
        proj.cell(row=R["cumulative"], column=c).fill = fill(BRAND_SOFT)
    # Year totals, beside the assumptions.
    proj["E4"] = "Next 12 months"
    proj["E4"].font = f(9, True, MUTED)
    for i, (label, n) in enumerate((("Total costs", "total"),
                                    ("Marketing & materials", "marketing"),
                                    ("Cumulative net", "cumulative"))):
        proj.cell(row=5 + i, column=5, value=label).font = f(bold=True)
        formula = (f"={months[-1]}{R[n]}" if n == "cumulative"
                   else f"=SUM({months[0]}{R[n]}:{months[-1]}{R[n]})")
        v = proj.cell(row=5 + i, column=8, value=formula)
        v.number_format = MONEY0
        v.font = f(bold=True, color=BRAND)
    proj.cell(row=R["sports_share"] + 2, column=1, value=(
        "How it works: costs follow new episodes, the searches not answered "
        "by an episode already made. The cost of one is FAM's recorded average "
        "once there is traffic, so the forecast sharpens as listeners arrive. "
        "Hosting steps up at 1,000 and 10,000 listeners; each sport's data plan "
        "follows its requests (Pro $19, Ultra $29, Mega $39)."
    )).font = f(9, color=MUTED, italic=True)
    proj.freeze_panes = f"B{PH + 1}"
    widths(proj, [40, 12, 12] + [11] * 12)
    for i in range(len(assumptions)):
        proj.cell(row=4 + i, column=3).alignment = Alignment(wrap_text=False)
    proj.cell(row=R["revenue"], column=1).comment = Comment(
        "No revenue yet. Type expected revenue per month.", "FAM")

    for ws in (costs, mkt, proj):
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
