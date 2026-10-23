"""Print FAM's secrets from 1Password, for `FAM_SECRETS`.

    FAM_SECRETS='cmd:python tools/onepassword_secrets.py'

1Password is the company vault (SECURITY_CHECKLIST.md). A **service account**
holds a token that can read only the vaults it is given; this signs in with
that token through 1Password's own SDK (`onepassword-sdk`,
requirements-1password.txt) and prints what it finds as one JSON object -
`{"ANTHROPIC_API_KEY": "..."}` - which `credentials.parse_payload` already
reads. FAM itself never knows which vendor answered, so changing vendor is
changing the one `FAM_SECRETS` line (CREDENTIALS.md).

Set on the host, beside `FAM_SECRETS`:

    OP_SERVICE_ACCOUNT_TOKEN   the service account's token (a secret)
    OP_VAULT                   the vault to read; "FAM Production" if unset

**What becomes a variable:** in that vault, every field whose label is an
upper-case variable name - `ANTHROPIC_API_KEY`, `GNEWS_KEY` - with the value
in that field. So one item can hold all of them as labelled fields, or each
vendor's item can carry its own key in a field labelled the way `.env.example`
spells it. Nothing else is read: a login's `username` and `password`, notes,
and **never a one-time-password field**, whatever it is labelled - a two-step
seed has no business in a server's environment. Two fields with one label are
refused rather than one picked by accident of order.

Why the SDK and not the `op` command (CREDENTIALS.md's older recipe, one
`op read` per key): it installs with pip like everything else in the image,
it reads the whole vault in one sign-in, and adding a key is adding a field
in 1Password rather than a line on the host.

What it never does: print a value anywhere but stdout, or put a value, the
token or the provider's words into an error. A failure is one sentence on
stderr and exit status 1; `credentials` keeps that line in the server log and
reports only that the fetch failed (its comment on `/api/health` says why).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys

DEFAULT_VAULT = "FAM Production"

#: A label that names a variable. Upper case only, so a login's own
#: `username` / `password` / `notes` fields are never mistaken for one.
_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

#: The field kinds a key can live in. A one-time-password field is never
#: read, even when labelled like a variable.
_READ_TYPES = {"Text", "Concealed"}


class Refused(Exception):
    """A sentence for stderr: what is wrong and what to set."""


async def _client(token: str):
    try:
        from onepassword.client import Client
    except ImportError:
        raise Refused("onepassword-sdk is not installed "
                      "(pip install -r requirements-1password.txt)") from None
    return await Client.authenticate(auth=token, integration_name="FAM",
                                     integration_version="1")


def _kind(field) -> str:
    kind = getattr(field, "field_type", "")
    return str(getattr(kind, "value", kind))


async def fetch(client=None) -> dict[str, str]:
    """Every labelled key in the vault, as {name: value}."""
    token = os.environ.get("OP_SERVICE_ACCOUNT_TOKEN", "").strip()
    wanted = os.environ.get("OP_VAULT", "").strip() or DEFAULT_VAULT
    if not token:
        raise Refused("OP_SERVICE_ACCOUNT_TOKEN is not set (the service "
                      "account's token, from 1Password's Developer settings)")
    try:
        client = client or await _client(token)
        vaults = await client.vaults.list()
        vault = next((v for v in vaults if wanted in (v.id, v.title)), None)
        if vault is None:
            raise Refused(f"the service account cannot see a vault named "
                          f"{wanted!r}; give it read access, or set OP_VAULT")
        overviews = await client.items.list(vault.id)
        items = [await client.items.get(vault.id, o.id) for o in overviews]
    except Refused:
        raise
    except Exception as exc:  # the SDK's errors name what failed, never a value
        raise Refused(f"1Password refused the request: {str(exc)[:200]}") from None
    found: dict[str, str] = {}
    for item in items:
        for field in item.fields:
            name = (field.title or "").strip()
            if not _NAME.match(name) or _kind(field) not in _READ_TYPES:
                continue
            if name in found:
                raise Refused(f"two fields in {wanted!r} are both labelled "
                              f"{name}; rename or remove one")
            found[name] = field.value or ""
    return found


def main() -> int:
    try:
        found = asyncio.run(fetch())
    except Refused as exc:
        print(f"onepassword_secrets: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
