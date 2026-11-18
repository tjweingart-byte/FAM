"""Paying for FAM: Stripe on the web, and nothing in the App Store (§652).

The owner's call (11.18): **every payment happens on a website**, so Apple's
commission is never paid - the iOS app links out to the same checkout a
browser opens. In the US that is allowed since the Epic v. Apple injunction;
outside it the iOS app must not point anybody at a purchase (APP_STORE.md
3.1.1), which `checkout_allowed_in_app` leaves to the client to ask.

What this module is, and what it is not:

* **The plans that are sold** - Student, Single and the FAM plan - all buy
  the same tier, `plus`, which has no ceiling (`entitlements`). They differ
  in price and in who may buy them, never in what they reach.
* **Prices live in Stripe**, never here (ACCOUNTS.md: one place for one
  fact). `prices()` reads them from Stripe's Price objects and caches them;
  a deploy without Stripe shows the plans with no number rather than a
  number that might be wrong.
* **A subscription is a row written by Stripe's webhook**, never by the
  client. Checkout returning to the app proves nothing - the browser came
  back - so the plan changes only when Stripe says so, signed.
* **The FAM plan is four seats**, the payer and three people they invite.
  Stripe knows nothing about seats; this table does.
* **Half a mix** (the owner: a free listener "should be stopped half way
  through a myFAM mix"): a free listener hears `mix_allowance(n)` of a mix's
  n episodes a day, counted as distinct episodes so tapping one twice is one.

No Stripe SDK: three form-encoded POSTs, one GET and an HMAC are the whole of
the API this needs, and `httpx` is already here.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from typing import Optional

import db as store_db
from paths import data_path

log = logging.getLogger("fam.billing")

#: What is for sale, cheapest first. The key is what the client sends.
PLANS: tuple[str, ...] = ("student", "single", "fam")

PLAN_LABELS: dict[str, str] = {
    "student": "FAM Student",
    "single": "FAM Single",
    "fam": "FAM plan",
}

PLAN_BLURBS: dict[str, str] = {
    "student": "Everything, unlimited, for college students.",
    "single": "Everything, unlimited, for one listener.",
    "fam": "Everything, unlimited, for you and three people you invite.",
}

#: The Stripe Price each plan is sold at. Ids, not amounts: the amount is
#: Stripe's to say.
PRICE_ENV: dict[str, str] = {
    "student": "STRIPE_PRICE_STUDENT",
    "single": "STRIPE_PRICE_SINGLE",
    "fam": "STRIPE_PRICE_FAM",
}

#: The tier every paid plan buys.
PAID_TIER = "plus"

#: The FAM plan's seats, the payer included (the owner, 11.18: "Up to 4").
FAM_SEATS = 4

#: Stripe statuses that still mean "paid". `past_due` keeps the plan while
#: Stripe retries the card - taking it away on the first failed renewal would
#: punish a listener for their bank.
LIVE_STATUSES: tuple[str, ...] = ("active", "trialing", "past_due")

#: How old a webhook's signed timestamp may be (Stripe's own default).
SIGNATURE_TOLERANCE = 300

STRIPE_API = "https://api.stripe.com/v1"

#: How long Stripe's prices are trusted before they are read again.
PRICE_TTL = 3600.0

#: Every environment variable this module reads, for `tests/conftest.py`.
BILLING_ENVIRONMENT: tuple[str, ...] = (
    "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
    "STRIPE_PRICE_STUDENT", "STRIPE_PRICE_SINGLE", "STRIPE_PRICE_FAM",
)

INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class BillingError(ValueError):
    """Something a listener can be told, phrased so it can be shown."""


def mix_allowance(episodes: int) -> int:
    """How many of a mix's episodes a free listener hears in a day: half,
    rounded down, and never none - a one-topic mix plays its one."""
    return max(1, int(episodes) // 2)


def _secret_key() -> str:
    try:
        import credentials
        key = credentials.active("STRIPE_SECRET_KEY")
        if key:
            return key
    except Exception:  # noqa: BLE001 - the environment is the fallback
        pass
    return os.environ.get("STRIPE_SECRET_KEY", "").strip()


def _webhook_secret() -> str:
    try:
        import credentials
        key = credentials.active("STRIPE_WEBHOOK_SECRET")
        if key:
            return key
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()


def price_id(plan: str) -> str:
    return os.environ.get(PRICE_ENV.get(plan, ""), "").strip()


def configured() -> bool:
    """Whether anything can be bought here: a key and at least one price.
    Staging never is (`spend_guard` removes the key)."""
    return bool(_secret_key()) and any(price_id(p) for p in PLANS)


def for_sale() -> list[str]:
    """The plans with a price behind them, cheapest first."""
    if not _secret_key():
        return []
    return [p for p in PLANS if price_id(p)]


# --- the signed webhook ------------------------------------------------------

def verify_signature(payload: bytes, header: str, secret: str,
                     now: float = 0.0,
                     tolerance: int = SIGNATURE_TOLERANCE) -> dict:
    """The event, if Stripe signed it; `BillingError` if not.

    Stripe's scheme: `Stripe-Signature: t=<unix>,v1=<hex>[,v1=...]`, where
    each v1 is HMAC-SHA256 of `"<t>.<raw body>"` under the endpoint's secret.
    The timestamp is checked too, so a captured event cannot be replayed a
    day later.
    """
    if not secret:
        raise BillingError("No webhook secret is set, so no event is trusted.")
    stamp = ""
    signatures: list[str] = []
    for part in (header or "").split(","):
        name, _, value = part.strip().partition("=")
        if name == "t":
            stamp = value
        elif name == "v1":
            signatures.append(value)
    if not stamp or not signatures:
        raise BillingError("The event is not signed.")
    try:
        signed_at = int(stamp)
    except ValueError as exc:
        raise BillingError("The event's signature is malformed.") from exc
    now = now or time.time()
    if abs(now - signed_at) > tolerance:
        raise BillingError("The event's signature is too old.")
    expected = hmac.new(secret.encode(), stamp.encode() + b"." + payload,
                        hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in signatures):
        raise BillingError("The event's signature does not match.")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BillingError("The event is not JSON.") from exc


def _period_end(sub: dict) -> float:
    """When a subscription's paid period ends. Newer Stripe API versions moved
    `current_period_end` onto the subscription's items; both are read."""
    end = sub.get("current_period_end")
    if not end:
        items = ((sub.get("items") or {}).get("data") or [])
        ends = [i.get("current_period_end") or 0 for i in items]
        end = max(ends) if ends else 0
    try:
        return float(end or 0)
    except (TypeError, ValueError):
        return 0.0


def _plan_of(sub: dict) -> str:
    """Which plan a subscription is: its metadata (set at checkout), else the
    price it is on."""
    plan = ((sub.get("metadata") or {}).get("plan") or "").strip()
    if plan in PLANS:
        return plan
    items = ((sub.get("items") or {}).get("data") or [])
    for item in items:
        price = (item.get("price") or {}).get("id", "")
        for name in PLANS:
            if price and price == price_id(name):
                return name
    return ""


class BillingStore:
    """Subscriptions, FAM-plan seats, webhook events seen, and the half-a-mix
    counter. Its own database: Stripe's webhook writes here, and it has no
    business holding the accounts store's write lock."""

    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("BILLING_DB", "billing.db", path)
        self._local = threading.local()
        conn = self._conn()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS subscriptions (
                user_id TEXT PRIMARY KEY,
                plan TEXT NOT NULL,
                status TEXT NOT NULL,
                customer TEXT NOT NULL DEFAULT '',
                subscription TEXT NOT NULL DEFAULT '',
                period_end REAL NOT NULL DEFAULT 0,
                updated REAL NOT NULL DEFAULT 0
            )""")
        conn.execute("CREATE INDEX IF NOT EXISTS subscriptions_customer"
                     " ON subscriptions (customer)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS stripe_events (
                event_id TEXT PRIMARY KEY,
                seen REAL NOT NULL
            )""")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS fam_members (
                member_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                joined REAL NOT NULL
            )""")
        conn.execute("CREATE INDEX IF NOT EXISTS fam_members_owner"
                     " ON fam_members (owner_id)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS fam_invites (
                code TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                created REAL NOT NULL
            )""")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS mix_listens (
                user_id TEXT NOT NULL,
                mix_id TEXT NOT NULL,
                day TEXT NOT NULL,
                episode TEXT NOT NULL,
                PRIMARY KEY (user_id, mix_id, day, episode)
            )""")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = store_db.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    # --- subscriptions --------------------------------------------------

    def record(self, user_id: str, plan: str, status: str, customer: str = "",
               subscription: str = "", period_end: float = 0.0,
               at: float = 0.0) -> None:
        if not user_id:
            return
        self._conn().execute(
            "INSERT INTO subscriptions (user_id, plan, status, customer,"
            " subscription, period_end, updated) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (user_id) DO UPDATE SET plan = excluded.plan,"
            " status = excluded.status,"
            " customer = CASE WHEN excluded.customer != '' THEN excluded.customer"
            "            ELSE subscriptions.customer END,"
            " subscription = CASE WHEN excluded.subscription != ''"
            "            THEN excluded.subscription ELSE subscriptions.subscription END,"
            " period_end = excluded.period_end, updated = excluded.updated",
            (user_id, plan or "", status or "", customer or "",
             subscription or "", float(period_end or 0), at or time.time()))

    def subscription(self, user_id: str) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT plan, status, customer, subscription, period_end"
            " FROM subscriptions WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return None
        return {"plan": row[0], "status": row[1], "customer": row[2],
                "subscription": row[3], "period_end": float(row[4] or 0)}

    def user_for_customer(self, customer: str) -> str:
        if not customer:
            return ""
        row = self._conn().execute(
            "SELECT user_id FROM subscriptions WHERE customer = ?",
            (customer,)).fetchone()
        return row[0] if row else ""

    def user_for_subscription(self, subscription: str) -> str:
        if not subscription:
            return ""
        row = self._conn().execute(
            "SELECT user_id FROM subscriptions WHERE subscription = ?",
            (subscription,)).fetchone()
        return row[0] if row else ""

    def _live(self, sub: Optional[dict], now: float) -> bool:
        if not sub or sub["status"] not in LIVE_STATUSES:
            return False
        # No period end recorded yet (the checkout event arrives first):
        # the status alone says paid.
        return not sub["period_end"] or sub["period_end"] > now

    def own_plan(self, user_id: str, now: float = 0.0) -> str:
        """The plan this account pays for itself, or ""."""
        sub = self.subscription(user_id) if user_id else None
        return sub["plan"] if self._live(sub, now or time.time()) else ""

    def paid(self, user_id: str, now: float = 0.0) -> bool:
        """Whether this account is on a paid plan: its own, or a seat on
        somebody's live FAM plan."""
        if not user_id:
            return False
        now = now or time.time()
        if self.own_plan(user_id, now):
            return True
        owner = self.fam_owner(user_id)
        return bool(owner) and self.own_plan(owner, now) == "fam"

    # --- webhook events -------------------------------------------------

    def first_sighting(self, event_id: str, at: float = 0.0) -> bool:
        """True the first time an event id arrives. Stripe delivers at least
        once; a repeat must change nothing."""
        if not event_id:
            return True
        cur = self._conn().execute(
            "INSERT INTO stripe_events (event_id, seen) VALUES (?, ?)"
            " ON CONFLICT (event_id) DO NOTHING", (event_id, at or time.time()))
        return bool(cur.rowcount)

    def apply(self, event: dict, at: float = 0.0) -> str:
        """Apply one verified Stripe event. Returns what it did, for the log.

        `checkout.session.completed` ties a customer and subscription to the
        account that started checkout (`client_reference_id`, which the server
        set). The `customer.subscription.*` events carry the status and the
        period, and are what keep the plan current from then on.
        """
        now = at or time.time()
        kind = str(event.get("type") or "")
        obj = ((event.get("data") or {}).get("object") or {})
        if not self.first_sighting(str(event.get("id") or ""), now):
            return "repeat"
        if kind == "checkout.session.completed":
            user = str(obj.get("client_reference_id") or
                       (obj.get("metadata") or {}).get("user_id") or "")
            plan = str((obj.get("metadata") or {}).get("plan") or "")
            if not user or plan not in PLANS:
                return "ignored: checkout without an account or plan"
            existing = self.subscription(user) or {}
            self.record(user, plan,
                        # Paid at checkout; the subscription event that follows
                        # carries the real status and period.
                        "active" if obj.get("payment_status") in ("paid", "no_payment_required")
                        else existing.get("status", "incomplete"),
                        customer=str(obj.get("customer") or ""),
                        subscription=str(obj.get("subscription") or ""),
                        period_end=existing.get("period_end", 0.0), at=now)
            return f"checkout: {user} on {plan}"
        if kind.startswith("customer.subscription."):
            sub_id = str(obj.get("id") or "")
            user = (str((obj.get("metadata") or {}).get("user_id") or "")
                    or self.user_for_subscription(sub_id)
                    or self.user_for_customer(str(obj.get("customer") or "")))
            if not user:
                return "ignored: subscription for nobody we know"
            plan = _plan_of(obj) or (self.subscription(user) or {}).get("plan", "")
            status = "canceled" if kind.endswith(".deleted") \
                else str(obj.get("status") or "")
            self.record(user, plan, status, customer=str(obj.get("customer") or ""),
                        subscription=sub_id, period_end=_period_end(obj), at=now)
            return f"subscription: {user} {plan} {status}"
        return f"ignored: {kind}"

    # --- the FAM plan's seats -------------------------------------------

    def fam_owner(self, member_id: str) -> str:
        row = self._conn().execute(
            "SELECT owner_id FROM fam_members WHERE member_id = ?",
            (member_id,)).fetchone()
        return row[0] if row else ""

    def fam_members(self, owner_id: str) -> list[str]:
        rows = self._conn().execute(
            "SELECT member_id FROM fam_members WHERE owner_id = ? ORDER BY joined",
            (owner_id,)).fetchall()
        return [r[0] for r in rows]

    def fam_invite(self, owner_id: str, at: float = 0.0) -> str:
        """A code the payer hands to somebody. Only the payer of a live FAM
        plan with a seat to spare may make one; the newest code replaces the
        last, so a code that leaked is dead the moment another is made."""
        if self.own_plan(owner_id, at) != "fam":
            raise BillingError("Only the person paying for a FAM plan can invite people to it.")
        if 1 + len(self.fam_members(owner_id)) >= FAM_SEATS:
            raise BillingError(f"Your FAM plan already has all {FAM_SEATS} people on it.")
        code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))
        conn = self._conn()
        conn.execute("DELETE FROM fam_invites WHERE owner_id = ?", (owner_id,))
        conn.execute("INSERT INTO fam_invites (code, owner_id, created)"
                     " VALUES (?, ?, ?)", (code, owner_id, at or time.time()))
        return code

    def fam_join(self, member_id: str, code: str, at: float = 0.0) -> str:
        """Take a seat on the plan this code belongs to. Returns the owner."""
        code = (code or "").strip().upper().replace("-", "").replace(" ", "")
        row = self._conn().execute(
            "SELECT owner_id FROM fam_invites WHERE code = ?", (code,)).fetchone()
        if not row:
            raise BillingError("That FAM plan code isn't right. Ask for a new one.")
        owner = row[0]
        if owner == member_id:
            raise BillingError("That's your own FAM plan.")
        if self.own_plan(owner, at) != "fam":
            raise BillingError("That FAM plan isn't active any more.")
        if self.fam_owner(member_id) == owner:
            return owner
        if 1 + len(self.fam_members(owner)) >= FAM_SEATS:
            raise BillingError("That FAM plan is full.")
        conn = self._conn()
        conn.execute("DELETE FROM fam_members WHERE member_id = ?", (member_id,))
        conn.execute("INSERT INTO fam_members (member_id, owner_id, joined)"
                     " VALUES (?, ?, ?)", (member_id, owner, at or time.time()))
        conn.execute("DELETE FROM fam_invites WHERE code = ?", (code,))
        return owner

    def fam_remove(self, owner_id: str, member_id: str) -> bool:
        cur = self._conn().execute(
            "DELETE FROM fam_members WHERE owner_id = ? AND member_id = ?",
            (owner_id, member_id))
        return bool(cur.rowcount)

    def fam_leave(self, member_id: str) -> bool:
        cur = self._conn().execute(
            "DELETE FROM fam_members WHERE member_id = ?", (member_id,))
        return bool(cur.rowcount)

    # --- half a mix -----------------------------------------------------

    def mix_heard(self, user_id: str, mix_id: str, day: str) -> set[str]:
        rows = self._conn().execute(
            "SELECT episode FROM mix_listens WHERE user_id = ? AND mix_id = ?"
            " AND day = ?", (user_id, mix_id, day)).fetchall()
        return {r[0] for r in rows}

    def take_mix_episode(self, user_id: str, mix_id: str, day: str,
                         episode: str, allowance: int) -> bool:
        """Count one episode of this mix today, if the allowance has room.
        An episode already counted is free; anything past the allowance is
        refused and not counted."""
        heard = self.mix_heard(user_id, mix_id, day)
        if episode in heard:
            return True
        if len(heard) >= allowance:
            return False
        self._conn().execute(
            "INSERT INTO mix_listens (user_id, mix_id, day, episode)"
            " VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (user_id, mix_id, day, episode))
        return True

    def give_back_mix_episode(self, user_id: str, mix_id: str, day: str,
                              episode: str) -> None:
        self._conn().execute(
            "DELETE FROM mix_listens WHERE user_id = ? AND mix_id = ? AND day = ?"
            " AND episode = ?", (user_id, mix_id, day, episode))

    def forget(self, user_id: str) -> int:
        """An account deleted: its seats and counters go. A subscription row
        stays until Stripe says it ended - deleting an account does not stop
        a card being charged, cancelling does (`/api/billing/portal`)."""
        conn = self._conn()
        n = conn.execute("DELETE FROM mix_listens WHERE user_id = ?",
                         (user_id,)).rowcount or 0
        n += conn.execute("DELETE FROM fam_members WHERE member_id = ? OR owner_id = ?",
                          (user_id, user_id)).rowcount or 0
        n += conn.execute("DELETE FROM fam_invites WHERE owner_id = ?",
                          (user_id,)).rowcount or 0
        return n


# --- Stripe's API -----------------------------------------------------------

def _stripe(method: str, path: str, data: Optional[dict] = None,
            timeout: float = 10.0) -> dict:
    key = _secret_key()
    if not key:
        raise BillingError("Subscriptions aren't available here yet.")
    import httpx
    try:
        resp = httpx.request(method, STRIPE_API + path, data=data,
                             auth=(key, ""), timeout=timeout)
    except httpx.HTTPError as exc:
        log.warning("stripe %s %s failed: %s", method, path, exc)
        raise BillingError("Checkout couldn't be reached. Try again in a minute.") from exc
    if resp.status_code >= 400:
        try:
            reason = resp.json().get("error", {}).get("message", "")
        except ValueError:
            reason = resp.text[:200]
        log.warning("stripe %s %s refused (%s): %s", method, path,
                    resp.status_code, reason)
        raise BillingError("Checkout refused that. Try again, or contact us if it keeps happening.")
    return resp.json()


def create_checkout(user_id: str, plan: str, success_url: str,
                    cancel_url: str, email: str = "",
                    customer: str = "") -> str:
    """A Stripe Checkout page for this plan, for this account. The account
    id travels in `client_reference_id` and in the subscription's metadata,
    both set here on the server, so every event names whose it is."""
    if plan not in PLANS:
        raise BillingError("That plan isn't one FAM sells.")
    price = price_id(plan)
    if not price:
        raise BillingError(f"{PLAN_LABELS[plan]} isn't for sale yet.")
    data = {
        "mode": "subscription",
        "line_items[0][price]": price,
        "line_items[0][quantity]": "1",
        "client_reference_id": user_id,
        "metadata[user_id]": user_id,
        "metadata[plan]": plan,
        "subscription_data[metadata][user_id]": user_id,
        "subscription_data[metadata][plan]": plan,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "allow_promotion_codes": "true",
    }
    if customer:
        data["customer"] = customer
    elif email:
        data["customer_email"] = email
    session = _stripe("POST", "/checkout/sessions", data)
    url = session.get("url") or ""
    if not url:
        raise BillingError("Checkout couldn't be started. Try again in a minute.")
    return url


def create_portal(customer: str, return_url: str) -> str:
    """Stripe's own page for changing card, plan or cancelling."""
    if not customer:
        raise BillingError("There's no subscription on this account to manage.")
    session = _stripe("POST", "/billing_portal/sessions",
                      {"customer": customer, "return_url": return_url})
    return session.get("url") or ""


_PRICES: dict = {"at": 0.0, "value": {}}
_PRICES_LOCK = threading.Lock()


def prices(now: float = 0.0) -> dict[str, dict]:
    """Each plan's price as Stripe has it: {plan: {amount, currency,
    interval, display}}. Cached an hour; a plan whose price could not be read
    is absent, and the screen shows the plan without a number."""
    now = now or time.time()
    with _PRICES_LOCK:
        if now - _PRICES["at"] < PRICE_TTL and _PRICES["value"]:
            return dict(_PRICES["value"])
    found: dict[str, dict] = {}
    for plan in for_sale():
        try:
            price = _stripe("GET", "/prices/" + price_id(plan), timeout=5.0)
        except BillingError:
            continue
        amount = price.get("unit_amount")
        currency = str(price.get("currency") or "usd").lower()
        interval = ((price.get("recurring") or {}).get("interval") or "month")
        if not isinstance(amount, int):
            continue
        symbol = "$" if currency == "usd" else currency.upper() + " "
        found[plan] = {"amount": amount, "currency": currency,
                       "interval": interval,
                       "display": f"{symbol}{amount / 100:.2f}/{'mo' if interval == 'month' else interval}"}
    with _PRICES_LOCK:
        _PRICES["at"] = now
        _PRICES["value"] = found
    return dict(found)


def forget_prices() -> None:
    with _PRICES_LOCK:
        _PRICES["at"] = 0.0
        _PRICES["value"] = {}
