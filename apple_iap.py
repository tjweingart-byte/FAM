"""Apple in-app purchase for the iPhone app (§653).

The iPhone app sells the same three plans as the web (`billing.PLANS`)
through StoreKit 2, so a listener on an iPhone pays with Apple's sheet
instead of leaving the app. Both ways in land in the same row
(`BillingStore.record`), so a plan is a plan whichever store sold it.

What the server trusts, and why:

* **A StoreKit 2 transaction is a JWS signed by Apple** - ES256, with the
  certificate chain in the header's `x5c`: Apple's leaf, Apple's
  intermediate, and Apple Root CA - G3. `verify` checks each certificate is
  signed by the next and in date, that the root is **the pinned one**
  (`APPLE_ROOT_SHA256`, by fingerprint, overridable for tests), that the
  leaf and intermediate carry Apple's marker extensions, and then the JWS
  signature with the leaf's key. Anything else is refused; nothing is
  accepted on the app's word.
* **The purchase names its account.** The app buys with an
  `appAccountToken` the server issued for that account (`account_token`),
  and the server refuses a transaction whose token is not this listener's -
  so a transaction copied from somebody else's phone buys nothing.
* **Apple's server notifications** (App Store Server Notifications v2,
  `/api/billing/apple/notifications`) carry renewals, lapses, refunds and
  grace periods, signed the same way. The transaction inside is what is
  read; the notification type only names a grace period.

Nothing here calls Apple, so staging needs no guard: it can only refuse.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import os
import uuid
from typing import Optional

#: SHA-256 of Apple Root CA - G3 (DER), from apple.com/certificateauthority.
#: Every App Store signature chains to it. Overridable so tests can sign
#: with a root of their own; production never sets it.
DEFAULT_ROOT_SHA256 = (
    "63343abfb89a6a03ebb57e9b3f5fa7be7c4f5c756f3017b3a8c488c3653e9179")

#: Apple's marker on the certificates that sign App Store data.
LEAF_OID = "1.2.840.113635.100.6.11.1"
INTERMEDIATE_OID = "1.2.840.113635.100.6.2.1"

#: The app's bundle id; a transaction for any other app buys nothing.
DEFAULT_BUNDLE_ID = "net.familiarize.fam"

#: Each plan's App Store product id. The price is App Store Connect's.
PRODUCT_ENV: dict[str, tuple[str, str]] = {
    "student": ("APPLE_PRODUCT_STUDENT", "net.familiarize.fam.student.monthly"),
    "single": ("APPLE_PRODUCT_SINGLE", "net.familiarize.fam.single.monthly"),
    "fam": ("APPLE_PRODUCT_FAM", "net.familiarize.fam.famplan.monthly"),
}

APPLE_ENVIRONMENT: tuple[str, ...] = (
    "APPLE_ROOT_SHA256", "APPLE_BUNDLE_ID",
    "APPLE_PRODUCT_STUDENT", "APPLE_PRODUCT_SINGLE", "APPLE_PRODUCT_FAM",
)

#: The namespace account tokens are made in. Fixed: changing it would
#: orphan every subscription already bought.
TOKEN_NAMESPACE = uuid.UUID("6f1d5c1e-4a0b-4f0e-9a51-6d3b0c2f7e11")


class AppleError(ValueError):
    """A refusal that can be logged and, phrased, shown."""


def root_sha256() -> str:
    return (os.environ.get("APPLE_ROOT_SHA256", "").strip().lower().replace(":", "")
            or DEFAULT_ROOT_SHA256)


def bundle_id() -> str:
    return os.environ.get("APPLE_BUNDLE_ID", "").strip() or DEFAULT_BUNDLE_ID


def product_id(plan: str) -> str:
    name, default = PRODUCT_ENV[plan]
    return os.environ.get(name, "").strip() or default


def products() -> dict[str, str]:
    """{plan: product id}, for the app to load from the App Store."""
    return {plan: product_id(plan) for plan in PRODUCT_ENV}


def plan_for_product(product: str) -> str:
    for plan in PRODUCT_ENV:
        if product and product == product_id(plan):
            return plan
    return ""


def account_token(user_id: str) -> str:
    """The `appAccountToken` this account buys with: a UUID derived from
    the account id, so the same account always gets the same one."""
    return str(uuid.uuid5(TOKEN_NAMESPACE, "fam:" + user_id))


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _has_oid(cert, oid: str) -> bool:
    from cryptography import x509
    try:
        cert.extensions.get_extension_for_oid(x509.ObjectIdentifier(oid))
        return True
    except x509.ExtensionNotFound:
        return False


def _check_chain(x5c: list, now: Optional[_dt.datetime] = None):
    """The leaf certificate, if `x5c` is Apple's chain to the pinned root."""
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ec

    if not isinstance(x5c, list) or len(x5c) != 3:
        raise AppleError("The signature's certificate chain is not Apple's shape.")
    try:
        ders = [base64.b64decode(c) for c in x5c]
        certs = [x509.load_der_x509_certificate(d) for d in ders]
    except Exception as exc:  # noqa: BLE001 - any parse failure is a refusal
        raise AppleError("The signature's certificates do not parse.") from exc
    leaf, intermediate, root = certs
    if hashlib.sha256(ders[2]).hexdigest() != root_sha256():
        raise AppleError("The chain does not end at Apple's root.")
    now = now or _dt.datetime.now(_dt.timezone.utc)
    for cert in certs:
        if not (cert.not_valid_before_utc <= now <= cert.not_valid_after_utc):
            raise AppleError("A certificate in the chain is out of date.")
    for child, parent in ((leaf, intermediate), (intermediate, root)):
        try:
            parent.public_key().verify(child.signature, child.tbs_certificate_bytes,
                                       ec.ECDSA(child.signature_hash_algorithm))
        except (InvalidSignature, TypeError, AttributeError) as exc:
            raise AppleError("The chain's signatures do not hold.") from exc
    if not _has_oid(leaf, LEAF_OID) or not _has_oid(intermediate, INTERMEDIATE_OID):
        raise AppleError("The certificates are not Apple's App Store signers.")
    return leaf


def verify(jws: str, now: Optional[_dt.datetime] = None) -> dict:
    """The payload of an App Store JWS, or `AppleError`."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    parts = (jws or "").split(".")
    if len(parts) != 3:
        raise AppleError("That is not a signed App Store record.")
    try:
        header = json.loads(_b64(parts[0]))
        payload = json.loads(_b64(parts[1]))
        signature = _b64(parts[2])
    except Exception as exc:  # noqa: BLE001
        raise AppleError("The signed record does not decode.") from exc
    if header.get("alg") != "ES256":
        raise AppleError("The record is not signed the way Apple signs.")
    leaf = _check_chain(header.get("x5c"), now)
    if len(signature) != 64:
        raise AppleError("The record's signature is malformed.")
    der = encode_dss_signature(int.from_bytes(signature[:32], "big"),
                               int.from_bytes(signature[32:], "big"))
    try:
        leaf.public_key().verify(der, (parts[0] + "." + parts[1]).encode(),
                                 ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise AppleError("The record's signature does not match.") from exc
    return payload


def _seconds(ms) -> float:
    try:
        return float(ms or 0) / 1000.0
    except (TypeError, ValueError):
        return 0.0


def subscription_from(transaction: dict, renewal: Optional[dict] = None,
                      grace: bool = False, now: float = 0.0) -> dict:
    """What a verified transaction says about the plan: {plan, status,
    original, period_end, token, environment}. A revoked (refunded)
    transaction is cancelled; a lapsed one expired; a grace period keeps
    the plan to its end, as `past_due` does on the web."""
    import time as _time
    now = now or _time.time()
    if transaction.get("bundleId") != bundle_id():
        raise AppleError("That purchase is for another app.")
    plan = plan_for_product(str(transaction.get("productId") or ""))
    if not plan:
        raise AppleError("That purchase is not a FAM plan.")
    end = _seconds(transaction.get("expiresDate"))
    # A billing grace period is Apple's renewal info saying so, whatever the
    # notification that carried it (§652 review): until it ends the
    # subscriber keeps the plan, as `past_due` keeps a Stripe one.
    grace_end = _seconds((renewal or {}).get("gracePeriodExpiresDate"))
    if grace_end > now and grace_end > end:
        grace = True
    if grace and renewal:
        end = max(end, grace_end)
    if transaction.get("revocationDate"):
        status = "canceled"
    elif end and end <= now:
        status = "expired"
    elif grace:
        status = "past_due"
    else:
        status = "active"
    return {"plan": plan, "status": status,
            "original": str(transaction.get("originalTransactionId") or ""),
            "period_end": end,
            "token": str(transaction.get("appAccountToken") or "").lower(),
            # When Apple signed it: a later-signed record is the newer one.
            "signed_at": _seconds(transaction.get("signedDate")),
            "environment": str(transaction.get("environment") or "")}
