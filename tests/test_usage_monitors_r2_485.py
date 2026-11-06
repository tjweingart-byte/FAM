"""Keith's usage monitors, piece 2c: Cloudflare R2 (§485).

The whole account's operations this month by billing class and its storage,
from Cloudflare's GraphQL analytics, against R2's free monthly allowance,
with what the month would cost past it. Pinned: the query asked, the
classes as Cloudflare's price list sorts them, the levels, the estimate, a
refusal said and never a zero, nothing asked without the token, and the
token never in a reading or held by staging.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import json

import httpx
import pytest

import spend_guard
import usage_monitors as UM

NOW = datetime.datetime(2026, 11, 10, 15, 0, tzinfo=datetime.timezone.utc)
TOKEN = "cf-SECRET-TOKEN-VALUE"
ACCOUNT = "acct123"


def use(monkeypatch, **changes):
    monkeypatch.setattr(UM, "settings", dataclasses.replace(UM.settings, **changes))


@pytest.fixture
def r2(monkeypatch):
    use(monkeypatch, r2_account_id=ACCOUNT)
    monkeypatch.setattr(UM, "_secret",
                        lambda name: TOKEN if name == "CLOUDFLARE_ANALYTICS_TOKEN" else "")


def cloudflare(ops: dict, storage_bytes: float = 2.5e9, status=200, body=None,
               accounts=True):
    seen = []

    def answer(request):
        seen.append(request)
        if body is not None:
            return httpx.Response(status, json=body)
        account = {"operations": [{"sum": {"requests": n},
                                   "dimensions": {"actionType": kind}}
                                  for kind, n in ops.items()],
                   "storage": [{"max": {"payloadSize": storage_bytes,
                                        "metadataSize": 0, "objectCount": 1234},
                                "dimensions": {"datetime": "2026-11-10T14:00:00Z"}}]}
        return httpx.Response(status, json={"data": {"viewer": {
            "accounts": [account] if accounts else []}}, "errors": None})

    return httpx.AsyncClient(transport=httpx.MockTransport(answer)), seen


def read(client):
    return asyncio.run(UM.read_r2(NOW, client))


def by_name(got):
    return {f["name"]: f for f in got["figures"]}


def test_operations_are_sorted_into_cloudflare_s_classes(r2):
    client, seen = cloudflare({"PutObject": 3000, "CopyObject": 500,
                               "GetObject": 90000, "HeadObject": 10000,
                               "DeleteObject": 700, "SomethingNew": 5})
    got = read(client)
    assert got["ok"] and got["level"] == "ok"
    f = by_name(got)
    assert f["class A operations this month"]["value"] == 3500
    assert f["class A operations this month"]["limit"] == 1_000_000
    assert f["class B operations this month"]["value"] == 100000
    assert f["class B operations this month"]["limit"] == 10_000_000
    assert f["storage"]["value"] == 2.5 and f["storage"]["limit"] == 10.0
    assert f["estimated charge this month"]["value"] == 0
    assert got["detail"]["free_operations"] == 700
    assert got["detail"]["unclassified_kinds"] == ["SomethingNew"]
    assert "Infrequent Access" in got["detail"]["approximate"]
    request = seen[0]
    assert str(request.url) == "https://api.cloudflare.com/client/v4/graphql"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    sent = json.loads(request.content)
    assert sent["variables"] == {"account": ACCOUNT, "start": "2026-11-01T00:00:00Z",
                                 "end": "2026-11-10T15:00:00Z"}
    assert "r2OperationsAdaptiveGroups" in sent["query"]
    assert "r2StorageAdaptiveGroups" in sent["query"]


def test_near_the_free_allowance_warns_and_past_it_costs(r2, monkeypatch):
    # 850k Class A by the 10th: 85% of the allowance, on pace to pass it.
    got = read(cloudflare({"PutObject": 850_000})[0])
    assert by_name(got)["class A operations this month"]["level"] == "urgent"
    # Past the allowance on everything: the estimate at the published prices.
    use(monkeypatch, it_budgets="r2=5")
    got = read(cloudflare({"PutObject": 1_500_000, "GetObject": 12_000_000},
                          storage_bytes=30e9)[0])
    charge = by_name(got)["estimated charge this month"]
    assert charge["value"] == pytest.approx(0.5e6 * 4.5e-6 + 2e6 * 0.36e-6 + 20 * 0.015)
    assert charge["limit"] == 5.0 and charge["limit_source"] == "IT_BUDGETS"
    assert by_name(got)["storage"]["level"] == "urgent"


def test_a_refused_query_is_said_without_the_token(r2):
    body = {"data": None, "errors": [{"message": "not authorized for that account"}]}
    got = read(cloudflare({}, body=body)[0])
    assert not got["ok"] and got["figures"] == []
    assert "not authorized" in got["why"]
    assert TOKEN not in json.dumps(got)


def test_an_http_error_is_said(r2):
    got = read(cloudflare({}, status=403, body={"oops": TOKEN})[0])
    assert not got["ok"] and "HTTP 403" in got["why"] and TOKEN not in json.dumps(got)


def test_a_token_that_cannot_see_the_account_is_said(r2):
    got = read(cloudflare({}, accounts=False)[0])
    assert not got["ok"] and "R2_ACCOUNT_ID" in got["why"]


def test_without_the_token_nothing_is_asked(monkeypatch):
    use(monkeypatch, r2_account_id=ACCOUNT)
    monkeypatch.setattr(UM, "_secret", lambda name: "")
    client, seen = cloudflare({"PutObject": 1})
    got = read(client)
    assert not got["ok"] and "not configured" in got["why"] and seen == []


def test_staging_never_holds_the_token_and_r2_is_a_monitor():
    assert "CLOUDFLARE_ANALYTICS_TOKEN" in spend_guard.PAID_CREDENTIALS
    assert UM.MONITORS["r2"] is UM.read_r2


def test_the_charge_is_projected_from_the_operations_pace(r2, monkeypatch):
    """The review (§487): 900k Class A by the 10th costs nothing yet, but is
    on pace for 2.7M - $7.65 past the allowance - and passes a $5 budget."""
    use(monkeypatch, it_budgets="r2=5")
    charge = by_name(read(cloudflare({"PutObject": 900_000}, storage_bytes=1e9)[0]))[
        "estimated charge this month"]
    assert charge["value"] == 0
    assert charge["projected"] == pytest.approx((900_000 / 9.625 * 30 - 1e6) * 4.5e-6, abs=0.01)
    assert charge["level"] == "urgent"
