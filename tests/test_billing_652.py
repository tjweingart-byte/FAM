"""§652: paying for FAM on the web (Stripe), and the free tier the owner set.

The owner, 11.18: payments go through a website, never the App Store; every
account's free month starts when the app is released (not the beta); after
it, one search or Go Deeper a week, DailyFAM's tiles free, others' myFAM
mixes to half way and none of your own. Student, Single and the FAM plan
(four people) are all unlimited.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import accounts as accounts_mod  # noqa: E402
import billing  # noqa: E402
import entitlements  # noqa: E402
import waitlist as waitlist_mod  # noqa: E402

SECRET = "whsec_test"


def _signed(event: dict, secret: str = SECRET, at: float = 0.0):
    body = json.dumps(event).encode()
    stamp = str(int(at or time.time()))
    sig = hmac.new(secret.encode(), stamp.encode() + b"." + body,
                   hashlib.sha256).hexdigest()
    return body, f"t={stamp},v1={sig}"


@pytest.fixture
def store(tmp_path):
    return billing.BillingStore(str(tmp_path / "billing.db"))


# --- the signature ----------------------------------------------------------

def test_a_signed_event_is_read_and_anything_else_is_refused():
    body, header = _signed({"id": "evt_1", "type": "x"})
    assert billing.verify_signature(body, header, SECRET)["id"] == "evt_1"
    with pytest.raises(billing.BillingError):
        billing.verify_signature(body, header, "whsec_other")
    with pytest.raises(billing.BillingError):
        billing.verify_signature(body + b" ", header, SECRET)
    with pytest.raises(billing.BillingError):
        billing.verify_signature(body, "", SECRET)
    with pytest.raises(billing.BillingError):
        billing.verify_signature(body, header, "")


def test_an_old_signature_cannot_be_replayed():
    body, header = _signed({"id": "evt_1"}, at=time.time() - 3600)
    with pytest.raises(billing.BillingError):
        billing.verify_signature(body, header, SECRET)


# --- subscriptions from events ---------------------------------------------

def _checkout(user="u1", plan="single", event_id="evt_c"):
    return {"id": event_id, "type": "checkout.session.completed",
            "data": {"object": {"client_reference_id": user,
                                "metadata": {"user_id": user, "plan": plan},
                                "customer": "cus_1", "subscription": "sub_1",
                                "payment_status": "paid"}}}


def _sub(kind="updated", status="active", end=None, event_id="evt_s", meta=True):
    obj = {"id": "sub_1", "customer": "cus_1", "status": status,
           "items": {"data": [{"current_period_end": end or time.time() + 30 * 86400}]}}
    if meta:
        obj["metadata"] = {"user_id": "u1", "plan": "single"}
    return {"id": event_id, "type": "customer.subscription." + kind,
            "data": {"object": obj}}


def test_checkout_then_subscription_events_keep_the_plan_current(store):
    assert not store.paid("u1")
    store.apply(_checkout())
    assert store.paid("u1") and store.own_plan("u1") == "single"
    store.apply(_sub(status="past_due", event_id="e2"))
    assert store.paid("u1"), "a failed renewal keeps the plan while Stripe retries"
    store.apply(_sub(kind="deleted", event_id="e3", meta=False))
    assert not store.paid("u1"), "found by subscription id, and ended"


def test_a_repeated_event_changes_nothing(store):
    store.apply(_checkout())
    store.apply(_sub(kind="deleted", event_id="e3"))
    assert store.apply(_checkout()) == "repeat"
    assert not store.paid("u1")


def test_a_lapsed_period_is_not_paid(store):
    store.apply(_checkout())
    store.apply(_sub(end=time.time() - 60, event_id="e2"))
    assert not store.paid("u1")


# --- the FAM plan -----------------------------------------------------------

def test_the_fam_plan_is_four_people_and_ends_with_the_payer(store):
    store.apply(_checkout(plan="fam"))
    joined = []
    for n in range(3):
        code = store.fam_invite("u1")
        store.fam_join(f"m{n}", code)
        joined.append(f"m{n}")
    assert all(store.paid(m) for m in joined)
    with pytest.raises(billing.BillingError):
        store.fam_invite("u1")
    store.fam_remove("u1", "m0")
    assert not store.paid("m0")
    store.apply(_sub(kind="deleted", event_id="end"))
    assert not store.paid("m1"), "a seat lasts as long as the payer's plan"


def test_only_a_fam_plan_invites_and_a_code_works_once(store):
    store.apply(_checkout(plan="single"))
    with pytest.raises(billing.BillingError):
        store.fam_invite("u1")
    store.apply(_checkout(user="u2", plan="fam", event_id="c2"))
    code = store.fam_invite("u2")
    store.fam_join("m1", code)
    with pytest.raises(billing.BillingError):
        store.fam_join("m2", code)


# --- half a mix ---------------------------------------------------------------

@pytest.mark.parametrize("episodes,allowed", [(1, 1), (2, 1), (5, 2), (6, 3), (10, 5)])
def test_half_a_mix(episodes, allowed):
    assert billing.mix_allowance(episodes) == allowed


def test_a_mix_counts_distinct_episodes(store):
    assert store.take_mix_episode("u", "mx", "d", "a", 2)
    assert store.take_mix_episode("u", "mx", "d", "a", 2), "a replay is free"
    assert store.take_mix_episode("u", "mx", "d", "b", 2)
    assert not store.take_mix_episode("u", "mx", "d", "c", 2)
    assert store.take_mix_episode("u", "mx", "next-day", "c", 2)


# --- the tiers ----------------------------------------------------------------

def test_free_is_one_ask_a_week_and_everything_else_uncapped():
    entitlements.reload_tiers()
    ask = entitlements.limit_for("free", "ask")
    assert (ask.window, ask.count) == ("week", 1)
    assert entitlements.limit_for("free", "episode").unlimited
    for name in ("plus", "unlimited"):
        assert entitlements.limit_for(name, "ask").unlimited
        assert entitlements.limit_for(name, "episode").unlimited


def test_a_search_and_a_go_deeper_are_asks_and_a_tile_is_not():
    assert entitlements.resource_for("episode", "search") == "ask"
    assert entitlements.resource_for("episode", "godeeper") == "ask"
    assert entitlements.resource_for("episode", "myfam") == "episode"
    assert entitlements.resource_for("explore", "search") == "explore"


def test_making_mixes_is_paid():
    assert not entitlements.allows("free", "mixes")
    assert entitlements.allows("plus", "mixes")


# --- the free month starts at launch, for everyone ---------------------------

def test_every_account_gets_a_month_from_launch_or_from_joining(tmp_path):
    acc = accounts_mod.AccountStore(str(tmp_path / "accounts.db"))
    waitlist_mod.Waitlist(acc)
    launch = time.time() - 10 * 86400
    early = launch - 100 * 86400
    late = launch + 40 * 86400

    acc._fm_window = (0.0, time.time())
    assert acc._with_free_month("free", 0, created=early, has_account=True) == "free", \
        "before launch nobody's month has started"

    acc._fm_window = (launch, time.time())
    now = time.time()
    assert acc._with_free_month("free", 0, now, created=early, has_account=True) == "plus"
    acc._fm_window = (launch, launch + 31 * 86400)  # the launch day, still cached
    assert acc._with_free_month("free", 0, launch + 31 * 86400, created=early,
                                has_account=True) == "free"
    acc._fm_window = (launch, late + 86400)
    assert acc._with_free_month("free", 0, late + 86400, created=late,
                                has_account=True) == "plus", "joined after launch"
    assert acc._with_free_month("free", 0, now, created=early) == "free", \
        "a guest has no account, so no month"


# --- the server ---------------------------------------------------------------

@pytest.fixture
def appmod(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "BILLING",
                        billing.BillingStore(str(tmp_path / "billing.db")))
    return appmod


def _request(listener):
    return SimpleNamespace(state=SimpleNamespace(listener=listener))


def test_a_paid_subscription_raises_the_tier(appmod):
    who = accounts_mod.Listener("u1", email="a@b.test", tier="free", has_account=True)
    assert appmod._tier(_request(who)) == "free"
    appmod.BILLING.apply(_checkout())
    assert appmod._tier(_request(who)) == "plus"


def test_the_webhook_takes_only_signed_events(appmod, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    with TestClient(appmod.app) as c:
        body, header = _signed(_checkout())
        assert c.post("/api/billing/webhook", content=body,
                      headers={"Stripe-Signature": "t=1,v1=bad"}).status_code == 400
        assert not appmod.BILLING.paid("u1")
        r = c.post("/api/billing/webhook", content=body,
                   headers={"Stripe-Signature": header})
        assert r.status_code == 200
    assert appmod.BILLING.paid("u1")


def test_mixes_are_refused_to_free_only_while_quotas_are_on(appmod, monkeypatch):
    who = accounts_mod.Listener("u1", tier="free", has_account=True)
    import quotas
    monkeypatch.setattr(quotas, "settings_enforcing", lambda: False)
    appmod._require_feature(_request(who), "mixes")
    monkeypatch.setattr(quotas, "settings_enforcing", lambda: True)
    with pytest.raises(HTTPException) as refused:
        appmod._require_feature(_request(who), "mixes")
    assert refused.value.status_code == 403
    assert refused.value.headers["X-FAM-Upgrade"] == "mixes"


def test_staging_can_never_sell():
    import spend_guard
    assert "STRIPE_SECRET_KEY" in spend_guard.PAID_CREDENTIALS


def test_nothing_is_for_sale_without_a_key(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.setenv("STRIPE_PRICE_SINGLE", "price_1")
    assert not billing.configured() and billing.for_sale() == []
