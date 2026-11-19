"""§657: Apple in-app purchase for the iPhone app.

The owner, 11.18: "add apple in-app purchase for the iphone app too". The
same three plans, sold through StoreKit 2; the server trusts a transaction
only if Apple signed it (a chain to the pinned root) and it was bought with
this account's `appAccountToken`. These tests sign with a root of their
own, pinned through `APPLE_ROOT_SHA256`, so nothing reaches Apple.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import accounts as accounts_mod  # noqa: E402
import apple_iap  # noqa: E402
import billing  # noqa: E402


def _cert(name, key, issuer_name, issuer_key, ca, oid=None, days=365):
    now = dt.datetime.now(dt.timezone.utc)
    builder = (x509.CertificateBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
               .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name)]))
               .public_key(key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(now - dt.timedelta(days=1))
               .not_valid_after(now + dt.timedelta(days=days))
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if oid:
        builder = builder.add_extension(
            x509.UnrecognizedExtension(x509.ObjectIdentifier(oid), b"\x05\x00"),
            critical=False)
    return builder.sign(issuer_key, hashes.SHA256())


class FakeApple:
    """A root, an intermediate and a leaf shaped like Apple's."""

    def __init__(self, leaf_oid=apple_iap.LEAF_OID):
        self.root_key = ec.generate_private_key(ec.SECP256R1())
        self.mid_key = ec.generate_private_key(ec.SECP256R1())
        self.leaf_key = ec.generate_private_key(ec.SECP256R1())
        self.root = _cert("Root", self.root_key, "Root", self.root_key, True)
        self.mid = _cert("Mid", self.mid_key, "Root", self.root_key, True,
                         apple_iap.INTERMEDIATE_OID)
        self.leaf = _cert("Leaf", self.leaf_key, "Mid", self.mid_key, False, leaf_oid)

    def der(self, cert):
        return cert.public_bytes(serialization.Encoding.DER)

    @property
    def fingerprint(self):
        return hashlib.sha256(self.der(self.root)).hexdigest()

    def sign(self, payload: dict, key=None) -> str:
        def b64(raw: bytes) -> str:
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        header = {"alg": "ES256",
                  "x5c": [base64.b64encode(self.der(c)).decode()
                          for c in (self.leaf, self.mid, self.root)]}
        signing = b64(json.dumps(header).encode()) + "." + b64(json.dumps(payload).encode())
        der = (key or self.leaf_key).sign(signing.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return signing + "." + b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


@pytest.fixture
def apple(monkeypatch):
    fake = FakeApple()
    monkeypatch.setenv("APPLE_ROOT_SHA256", fake.fingerprint)
    return fake


def _tx(user="u1", plan="single", expires=None, **extra):
    tx = {"bundleId": apple_iap.bundle_id(),
          "productId": apple_iap.product_id(plan),
          "originalTransactionId": "1000",
          "appAccountToken": apple_iap.account_token(user),
          "expiresDate": int(((expires or time.time() + 30 * 86400)) * 1000),
          "environment": "Sandbox"}
    tx.update(extra)
    return tx


# --- the signature ------------------------------------------------------------

def test_apples_signature_is_read(apple):
    assert apple_iap.verify(apple.sign({"a": 1})) == {"a": 1}


def test_a_chain_to_another_root_is_refused(apple, monkeypatch):
    monkeypatch.setenv("APPLE_ROOT_SHA256", "00" * 32)
    with pytest.raises(apple_iap.AppleError):
        apple_iap.verify(apple.sign({"a": 1}))


def test_the_production_root_is_pinned_by_default(monkeypatch):
    monkeypatch.delenv("APPLE_ROOT_SHA256", raising=False)
    assert apple_iap.root_sha256() == apple_iap.DEFAULT_ROOT_SHA256


def test_a_record_signed_by_another_key_is_refused(apple):
    with pytest.raises(apple_iap.AppleError):
        apple_iap.verify(apple.sign({"a": 1}, key=ec.generate_private_key(ec.SECP256R1())))


def test_a_tampered_record_is_refused(apple):
    head, body, sig = apple.sign({"plan": "single"}).split(".")
    forged = base64.urlsafe_b64encode(b'{"plan":"fam"}').rstrip(b"=").decode()
    with pytest.raises(apple_iap.AppleError):
        apple_iap.verify(".".join((head, forged, sig)))


def test_a_leaf_without_apples_marker_is_refused(monkeypatch):
    fake = FakeApple(leaf_oid=None)
    monkeypatch.setenv("APPLE_ROOT_SHA256", fake.fingerprint)
    with pytest.raises(apple_iap.AppleError):
        apple_iap.verify(fake.sign({"a": 1}))


# --- what a transaction says ----------------------------------------------------

def test_a_transaction_is_a_plan_with_an_end():
    sub = apple_iap.subscription_from(_tx(plan="fam"))
    assert sub["plan"] == "fam" and sub["status"] == "active"
    assert sub["original"] == "1000"


def test_refunded_lapsed_and_grace():
    assert apple_iap.subscription_from(_tx(revocationDate=1))["status"] == "canceled"
    assert apple_iap.subscription_from(_tx(expires=time.time() - 60))["status"] == "expired"
    graced = apple_iap.subscription_from(
        _tx(expires=time.time() + 60), {"gracePeriodExpiresDate": (time.time() + 86400) * 1000},
        grace=True)
    assert graced["status"] == "past_due" and graced["period_end"] > time.time() + 3600


def test_another_apps_or_product_buys_nothing():
    with pytest.raises(apple_iap.AppleError):
        apple_iap.subscription_from(_tx(bundleId="com.other.app"))
    with pytest.raises(apple_iap.AppleError):
        apple_iap.subscription_from(_tx(productId="com.other.thing"))


def test_an_account_always_buys_with_the_same_token():
    assert apple_iap.account_token("u1") == apple_iap.account_token("u1")
    assert apple_iap.account_token("u1") != apple_iap.account_token("u2")


# --- the server -----------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path, apple):
    import app as appmod
    store = accounts_mod.AccountStore(str(tmp_path / "accounts.db"))
    monkeypatch.setattr(appmod, "ACCOUNTS", store)
    monkeypatch.setattr(appmod, "BILLING", billing.BillingStore(str(tmp_path / "billing.db")))
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        assert c.post("/api/auth/signup", json={"email": "buyer@fam.test",
                                                "password": "correct horse battery"}
                      ).status_code == 200
        user = c.get("/api/auth/me").json()["user_id"]
        yield c, appmod, user


def test_a_purchase_sets_the_plan(client, apple):
    c, appmod, user = client
    setup = c.get("/api/billing/apple").json()
    assert setup["token"] == apple_iap.account_token(user)
    assert setup["products"]["single"] == apple_iap.product_id("single")
    r = c.post("/api/billing/apple", json={"transaction": apple.sign(_tx(user))})
    assert r.status_code == 200, r.text
    assert r.json()["tier"] == "plus"
    assert appmod.BILLING.subscription(user)["source"] == "apple"
    plans = c.get("/api/plans").json()
    assert plans["subscription"]["store"] == "apple"


def test_a_purchase_by_another_account_buys_nothing(client, apple):
    c, appmod, user = client
    r = c.post("/api/billing/apple", json={"transaction": apple.sign(_tx("someone-else"))})
    assert r.status_code == 403
    assert not appmod.BILLING.paid(user)


def test_an_unsigned_purchase_buys_nothing(client, apple, monkeypatch):
    c, appmod, user = client
    monkeypatch.setenv("APPLE_ROOT_SHA256", "11" * 32)
    r = c.post("/api/billing/apple", json={"transaction": apple.sign(_tx(user))})
    assert r.status_code == 400
    assert not appmod.BILLING.paid(user)


def test_a_notification_keeps_the_plan_current(client, apple):
    c, appmod, user = client
    c.get("/api/billing/apple")
    c.post("/api/billing/apple", json={"transaction": apple.sign(_tx(user))})
    refund = apple.sign({"notificationType": "REFUND", "data": {
        "signedTransactionInfo": apple.sign(_tx(user, revocationDate=int(time.time() * 1000)))}})
    r = c.post("/api/billing/apple/notifications", json={"signedPayload": refund})
    assert r.status_code == 200
    assert not appmod.BILLING.paid(user)


def test_a_forged_notification_is_refused(client, apple):
    c, _appmod, _user = client
    r = c.post("/api/billing/apple/notifications", json={"signedPayload": "a.b.c"})
    assert r.status_code == 400


def test_a_mix_episode_from_an_older_client_is_not_a_search(client, monkeypatch):
    """`surface=dailyfam` without `mix` (an iOS build before §656) playing
    one of the listener's own mixes spends an episode, not the week's one
    search - but naming the surface alone makes nothing free (§656 review)."""
    import mixes as mixes_mod
    import quotas
    c, appmod, user = client
    monkeypatch.setattr(quotas, "settings_enforcing", lambda: True)
    monkeypatch.setattr(appmod.ACCOUNTS, "_free_month_starts", lambda now: 0.0)
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    mix = appmod.MIXES.create(user, "Mine", [{"query": f"topic number {n}"} for n in range(3)])
    for item in mix.items:
        q = mixes_mod.prompt_for(item)
        r = c.get("/api/audio", params={"q": q, "minutes": 1, "fmt": "pcm",
                                         "surface": "dailyfam"})
        assert r.status_code == 200, (q, r.status_code, r.headers.get("X-FAM-Refused-By"))
    # A made-up question called a DailyFAM tile is a search: the one ask...
    assert c.get("/api/audio", params={"q": "anything at all", "minutes": 1, "fmt": "pcm",
                                        "surface": "dailyfam", "topic_id": "made-up"}
                 ).status_code == 200
    # ...and then the limit.
    refused = c.get("/api/audio", params={"q": "something else entirely", "minutes": 1,
                                           "fmt": "pcm", "surface": "dailyfam"})
    assert refused.status_code == 429 and refused.headers.get("X-FAM-Refused-By") == "quota"


def test_a_grace_period_holds_whatever_the_notification():
    renewal = {"gracePeriodExpiresDate": (time.time() + 86400) * 1000}
    sub = apple_iap.subscription_from(_tx(expires=time.time() - 60), renewal)
    assert sub["status"] == "past_due"


def test_a_restore_during_grace_does_not_end_it(tmp_path):
    store = billing.BillingStore(str(tmp_path / "b.db"))
    renewal = {"gracePeriodExpiresDate": (time.time() + 86400) * 1000}
    store.record_apple("u1", apple_iap.subscription_from(_tx(expires=time.time() - 60), renewal))
    assert store.paid("u1")
    store.record_apple("u1", apple_iap.subscription_from(_tx(expires=time.time() - 60)))
    assert store.paid("u1"), "the transaction alone cannot see the grace period"


def test_one_store_ending_never_cancels_the_others_live_plan(tmp_path):
    store = billing.BillingStore(str(tmp_path / "b.db"))
    store.record_apple("u1", apple_iap.subscription_from(_tx()))
    store.apply({"id": "late", "type": "customer.subscription.deleted", "created": int(time.time()),
                 "data": {"object": {"id": "sub_old", "customer": "cus_old", "status": "canceled",
                                     "metadata": {"user_id": "u1", "plan": "single"}}}})
    assert store.paid("u1") and store.subscription("u1")["source"] == "apple"


def test_a_dailyfam_tile_is_free_before_its_episode_is_written(client, monkeypatch):
    """A tile the server serves is a browse episode whether or not it is
    written yet; the same id with other words is a search (§656 review)."""
    import quotas
    import topics as topics_mod
    c, appmod, _user = client
    monkeypatch.setattr(quotas, "settings_enforcing", lambda: True)
    monkeypatch.setattr(appmod.ACCOUNTS, "_free_month_starts", lambda now: 0.0)
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    tiles = list(topics_mod.BANK_BY_ID.values())[:3]
    for tile in tiles:
        r = c.get("/api/audio", params={"q": tile.query, "minutes": 1, "fmt": "pcm",
                                         "surface": "myfam", "topic_id": tile.id})
        assert r.status_code == 200, (tile.id, r.status_code, r.headers.get("X-FAM-Refused-By"))
    assert c.get("/api/audio", params={"q": "not that tile's question", "minutes": 1,
                                        "fmt": "pcm", "surface": "myfam",
                                        "topic_id": tiles[0].id}).status_code == 200
    refused = c.get("/api/audio", params={"q": "nor this one", "minutes": 1, "fmt": "pcm",
                                           "surface": "myfam", "topic_id": tiles[0].id})
    assert refused.status_code == 429


def test_a_fam_plan_member_is_never_sent_the_payers_id(client, apple):
    c, appmod, user = client
    appmod.BILLING.apply({"id": "c1", "type": "checkout.session.completed",
                          "data": {"object": {"client_reference_id": "payer-id-123",
                                              "metadata": {"user_id": "payer-id-123", "plan": "fam"},
                                              "payment_status": "paid"}}})
    appmod.BILLING.fam_join(user, appmod.BILLING.fam_invite("payer-id-123"))
    body = c.get("/api/plans").text
    assert "payer-id-123" not in body
    assert c.get("/api/plans").json()["subscription"]["fam_member_of"]


def test_apples_test_notification_is_answered(client, apple):
    """App Store Connect's "Request a test notification" carries no
    transaction: signed, so 200, and nothing changes."""
    c, _appmod, _user = client
    note = apple.sign({"notificationType": "TEST", "data": {"bundleId": apple_iap.bundle_id()}})
    assert c.post("/api/billing/apple/notifications", json={"signedPayload": note}).status_code == 200
    assert c.post("/api/billing/apple/notifications", json=["not", "an", "object"]).status_code == 400
