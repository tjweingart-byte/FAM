"""Viral Loops: a supporting service, never the source of truth (WAITLIST.md).

FAM's database owns identity, profiles, friendships and place in line. Viral
Loops is told about each participant so its referral tracking, fraud and
duplicate checks and emails work - and that is all it is asked to do.

**Every call goes through the outbox** (`waitlist.Waitlist.enqueue`). A signup
never waits on this module and never fails because of it: the row is written,
the sign-up answers, and `drain` delivers the call - straight away in the
background, and again on a timer until it lands. Unconfigured (no
`VIRAL_LOOPS_API_TOKEN` or campaign id - every test, every staging deploy,
which refuses outbound connections by design) nothing is sent and nothing is
dropped: the calls wait in the outbox for the day the keys arrive.

The request shapes follow the v3 API reference (developers.viral-loops.com):
`POST /campaign/participant` to register, `POST /campaign/participant/flag` to
take somebody off the leaderboard once they are let in, the secret token in an
`apiToken` header. They could not be checked against the live docs from the
session that wrote this (the docs hosts were unreachable), so each shape lives
in one function here and nowhere else.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("fam.viral_loops")

BASE_URL = "https://app.viral-loops.com/api/v3"
TIMEOUT_SECONDS = 10.0


class ViralLoopsError(RuntimeError):
    pass


class ViralLoops:
    def __init__(self, api_token: str = "", campaign_id: str = "",
                 base_url: str = BASE_URL, transport=None) -> None:
        self.api_token = (api_token or "").strip()
        self.campaign_id = (campaign_id or "").strip()
        self.base_url = base_url.rstrip("/")
        #: For tests: an httpx transport that answers without a network.
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.api_token and self.campaign_id)

    async def _post(self, path: str, body: dict) -> dict:
        import httpx

        headers = {"apiToken": self.api_token,
                   "Content-Type": "application/json"}
        body = {"campaignId": self.campaign_id, **body}
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS,
                                     transport=self.transport) as client:
            res = await client.post(self.base_url + path, json=body,
                                    headers=headers)
        if res.status_code >= 400:
            raise ViralLoopsError(f"{path} answered {res.status_code}: "
                                  f"{res.text[:300]}")
        try:
            return res.json() or {}
        except ValueError:
            return {}

    async def register(self, email: str, name: str = "",
                       referrer_code: str = "") -> dict:
        """Register a participant. Returns {"referral_code", "participant_id"}."""
        body: dict = {"user": {"firstname": name or "", "email": email}}
        if referrer_code:
            body["referrer"] = {"referralCode": referrer_code}
        data = await self._post("/campaign/participant", body)
        return {"referral_code": str(data.get("referralCode") or ""),
                "participant_id": str(data.get("id") or data.get("participantId")
                                      or "")}

    async def flag(self, email: str, reason: str = "Granted early access") -> None:
        """Take somebody off the waiting list without deleting them."""
        await self._post("/campaign/participant/flag",
                         {"email": email, "reason": reason})


async def drain(waitlist, client: Optional[ViralLoops], limit: int = 50) -> dict:
    """Deliver whatever is due in the outbox. Never raises.

    Returns {"sent", "failed", "skipped"} so a caller can log what happened.
    """
    result = {"sent": 0, "failed": 0, "skipped": 0}
    if client is None or not client.configured:
        return result
    for item in waitlist.due(limit=limit):
        view = waitlist.vendor_view(item["user_id"])
        if view is None or not view["email"]:
            # Deleted since, or an account with no address (phone-only). There
            # is nothing to tell the vendor, so the item is finished.
            waitlist.mark_done(item["id"])
            result["skipped"] += 1
            continue
        try:
            if item["action"] == "register":
                got = await client.register(view["email"], view["name"],
                                            view["referrer_vl_code"])
                waitlist.set_vendor_ids(item["user_id"], got["referral_code"],
                                        got["participant_id"])
            elif item["action"] == "flag":
                await client.flag(view["email"])
            waitlist.mark_done(item["id"])
            result["sent"] += 1
        except Exception as exc:  # noqa: BLE001 - a vendor must never break us
            log.warning("viral loops %s for %s failed: %s", item["action"],
                        item["user_id"], exc)
            waitlist.mark_failed(item["id"], str(exc))
            result["failed"] += 1
    return result
