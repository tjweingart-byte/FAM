"""Two-step sign-in: a second thing to prove besides the password (§301).

A password proves one thing - that somebody knows it - and it is the one thing
a breach somewhere else, a phishing page or a shoulder can hand to a stranger.
This is the second proof, and it is deliberately the kind that needs no
delivery:

* **An authenticator app** (RFC 6238 TOTP: Apple Passwords, Google
  Authenticator, 1Password, Authy). The account and the app share a secret
  once, at set-up; after that the app shows a 6-digit code that changes every
  30 seconds and the server computes the same one. Nothing is sent, so it
  works for an email account, a phone-only account and an Apple account
  alike, costs nothing per sign-in, and cannot be intercepted on the way.
  SMS was the alternative and was not chosen: it needs a paid vendor, a
  number can be moved to another SIM by social engineering, and a phone
  number on FAM is an identifier that has never been verified (ACCOUNTS.md).
* **Recovery codes**, ten of them, shown once at set-up, each good once. They
  are what a person with a lost phone signs in with, so turning this on never
  turns a lost phone into a lost account.

Only the pure parts live here - the arithmetic and the formats. Who has it on,
the secrets and the spent codes are `accounts.AccountStore`'s, beside the
password hashes, because that is the one store that holds credentials.

Standard library only (`hmac`, `hashlib`, `base64`, `secrets`), as scrypt is:
TOTP is forty lines, and a dependency for forty lines is a supply chain for
nothing.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote, urlencode

#: RFC 6238's defaults, and what every authenticator app assumes when an
#: otpauth link does not say otherwise. Said in the link anyway.
DIGITS = 6
PERIOD = 30
ALGORITHM = "SHA1"

#: Codes accepted either side of now, in periods: one each way allows a phone
#: clock 30 seconds out and a code typed as it rolls over, and no more.
WINDOW = 1

#: 160 bits, RFC 4226's recommended secret length.
SECRET_BYTES = 20

#: What an authenticator app shows above the code.
ISSUER = "FAM"

RECOVERY_CODES = 10
#: No 0/O, 1/I/L: a recovery code is typed off paper, often years later.
_RECOVERY_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_RECOVERY_HALF = 5


def new_secret() -> str:
    """A fresh shared secret, base32 without padding (what the apps accept)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    clean = "".join(secret.split()).upper()
    return base64.b32decode(clean + "=" * (-len(clean) % 8))


def code_at(secret: str, step: int) -> str:
    """The code for one 30-second step (RFC 4226 HOTP over the step count)."""
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 10 ** DIGITS:0{DIGITS}d}"


def step_for(at: float) -> int:
    return int(at // PERIOD)


def matching_step(secret: str, code: str, at: float = 0.0) -> Optional[int]:
    """The step `code` belongs to within the window around `at`, or None.

    Returns the step rather than a yes so the caller can refuse a code it has
    already accepted: a code seen over a shoulder is good for the rest of its
    30 seconds, and only remembering the last step used closes that.
    """
    digits = clean_code(code)
    if len(digits) != DIGITS or not secret:
        return None
    now = step_for(at or time.time())
    found = None
    # Every candidate is compared, in constant time, so how long a refusal
    # takes says nothing about how near the guess was.
    for step in range(now - WINDOW, now + WINDOW + 1):
        if hmac.compare_digest(code_at(secret, step), digits):
            found = step
    return found


def clean_code(code: str) -> str:
    """The digits of a typed code - spaces and dashes are how apps show them."""
    return "".join(ch for ch in str(code or "") if ch.isdigit())


def otpauth_uri(secret: str, label: str) -> str:
    """The link an authenticator app reads (or a QR code of it).

    `label` is what the app lists the entry as - the account's email or
    phone - so somebody with two FAM accounts can tell them apart.
    """
    name = quote(f"{ISSUER}:{label or 'account'}", safe="@:+")
    query = urlencode({"secret": secret, "issuer": ISSUER, "algorithm": ALGORITHM,
                       "digits": DIGITS, "period": PERIOD})
    return f"otpauth://totp/{name}?{query}"


def grouped(secret: str) -> str:
    """The secret in fours, for typing into an app by hand."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def new_recovery_codes(count: int = RECOVERY_CODES) -> list[str]:
    """`count` codes like `7KQ2M-PX9RD`: 50 bits each, from `secrets`."""
    def half() -> str:
        return "".join(secrets.choice(_RECOVERY_ALPHABET)
                       for _ in range(_RECOVERY_HALF))
    return [f"{half()}-{half()}" for _ in range(count)]


def clean_recovery(code: str) -> str:
    """A typed recovery code in its stored form: upper case, no separators."""
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def looks_like_recovery(code: str) -> bool:
    """Whether a typed code is a recovery code rather than an app's digits."""
    clean = clean_recovery(code)
    return len(clean) == 2 * _RECOVERY_HALF and not clean.isdigit()
