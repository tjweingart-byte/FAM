"""Keith's usage monitors, piece 2b: MongoDB Atlas (§484).

The archive's Flex cluster against Flex's limits: the Admin API (a Project
Read Only key, Digest auth) for its state and storage limit, the cluster's
own `atlasSize` and `serverStatus` for what is used. Pinned: the request
asked, the figures and their levels, what could not be read said (never a
zero), nothing asked when the archive is not on Atlas or the keys are not
there, and the keys never in a reading or kept by staging.
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
PUBLIC, PRIVATE = "pubkey123", "PRIVATE-SECRET-VALUE"
PROJECT, CLUSTER = "6add48a0a599673c1877a727", "fam-archive"


def use(monkeypatch, **changes):
    monkeypatch.setattr(UM, "settings", dataclasses.replace(UM.settings, **changes))


@pytest.fixture
def atlas(monkeypatch):
    use(monkeypatch, archive_backend="atlas", atlas_project_id=PROJECT,
        atlas_cluster=CLUSTER)
    keys = {"ATLAS_PUBLIC_KEY": PUBLIC, "ATLAS_PRIVATE_KEY": PRIVATE}
    monkeypatch.setattr(UM, "_secret", lambda name: keys.get(name, ""))
    return keys


def admin_api(status=200, disk=5.0):
    seen = []

    def answer(request):
        seen.append(request)
        body = {"name": CLUSTER, "stateName": "IDLE", "clusterType": "REPLICASET",
                "providerSettings": {"diskSizeGB": disk, "regionName": "US_EAST_1"}}
        return httpx.Response(status, json=body if status == 200 else
                              {"detail": f"bad {PRIVATE}"})

    return httpx.AsyncClient(transport=httpx.MockTransport(answer)), seen


def read(client, database):
    return asyncio.run(UM.read_atlas(NOW, client, database))


def by_name(got):
    return {f["name"]: f for f in got["figures"]}


def test_the_flex_cluster_is_read_from_the_admin_api_and_the_cluster(atlas):
    client, seen = admin_api()
    got = read(client, lambda: ({"atlas_size": 19_550_000, "connections": 4,
                                 "user": "read-only user"}, ""))
    assert got["ok"] and got["level"] == "ok"
    f = by_name(got)
    assert f["storage used"]["value"] == 0.0196 and f["storage used"]["limit"] == 5.0
    assert f["storage used"]["limit_source"] == "the Admin API"
    assert "M10" in f["storage used"]["next"]
    assert f["connections"]["value"] == 4 and f["connections"]["limit"] == 500
    assert got["detail"]["state"] == "IDLE"
    assert any("operations a second" in w for w in got["detail"]["not_read"])
    request = seen[0]
    assert request.url.path == f"/api/atlas/v2/groups/{PROJECT}/flexClusters/{CLUSTER}"
    assert request.headers["Accept"] == UM.ATLAS_ACCEPT


def test_storage_near_the_limit_warns_then_is_urgent(atlas):
    warn = read(admin_api()[0], lambda: ({"atlas_size": 4.1e9}, ""))
    assert by_name(warn)["storage used"]["level"] == "warn"
    urgent = read(admin_api()[0], lambda: ({"atlas_size": 4.8e9, "connections": 480}, ""))
    assert urgent["level"] == "urgent"
    assert by_name(urgent)["connections"]["level"] == "urgent"


def test_without_the_admin_api_flex_s_published_limit_is_used_and_said(atlas):
    got = read(admin_api(status=401)[0], lambda: ({"atlas_size": 1e9}, ""))
    assert got["ok"]
    storage = by_name(got)["storage used"]
    assert storage["limit"] == 5.0 and storage["limit_source"] == "Flex's published limit"
    assert any("HTTP 401" in w for w in got["detail"]["not_read"])
    assert PRIVATE not in json.dumps(got)


def test_what_the_cluster_refuses_is_said_never_zero(atlas):
    got = read(admin_api()[0], lambda: ({"atlas_size": 1e9,
                                         "connections_why": "serverStatus refused: OperationFailure"}, ""))
    assert "connections" not in by_name(got)
    assert any("serverStatus refused" in w for w in got["detail"]["not_read"])


def test_nothing_answering_is_a_failed_reading(atlas):
    got = read(admin_api(status=500)[0], lambda: ({}, "the database did not answer: ServerSelectionTimeoutError"))
    assert not got["ok"] and got["figures"] == []
    assert "HTTP 500" in got["why"] and "did not answer" in got["why"]


def test_a_cluster_no_longer_flex_is_said(atlas):
    got = read(admin_api(status=404)[0], lambda: ({}, "no MONGODB_READ_URI or MONGODB_URI"))
    assert not got["ok"] and "dedicated tier" in got["why"]


def test_nothing_is_asked_when_the_archive_is_not_on_atlas(monkeypatch):
    use(monkeypatch, archive_backend="sqlite")
    client, seen = admin_api()
    asked = []
    got = read(client, lambda: asked.append(1) or ({}, ""))
    assert not got["ok"] and "not on Atlas" in got["why"]
    assert seen == [] and asked == []


def test_without_its_keys_the_admin_api_is_not_asked(monkeypatch):
    use(monkeypatch, archive_backend="atlas", atlas_project_id=PROJECT, atlas_cluster=CLUSTER)
    monkeypatch.setattr(UM, "_secret", lambda name: "")
    client, seen = admin_api()
    got = read(client, lambda: ({"atlas_size": 1e9}, ""))
    assert seen == [] and got["ok"]
    assert any("not configured" in w for w in got["detail"]["not_read"])


def test_staging_never_holds_the_atlas_keys():
    assert {"ATLAS_PUBLIC_KEY", "ATLAS_PRIVATE_KEY"} <= set(spend_guard.PAID_CREDENTIALS)


def test_atlas_is_one_of_keiths_monitors():
    assert UM.MONITORS["atlas"] is UM.read_atlas


class _FakeAdmin:
    def __init__(self, refuse=()):
        self.refuse, self.asked = refuse, []

    def command(self, cmd):
        name = next(iter(cmd))
        self.asked.append(name)
        if name in self.refuse:
            raise PermissionError(name)
        if name == "atlasSize":
            return {"atlasSize": 2.5e8, "ok": 1}
        return {"connections": {"current": 7, "available": 493}}


def fake_mongo(monkeypatch, refuse=()):
    import sys
    import types

    made = []

    class Client:
        def __init__(self, uri, **kw):
            self.uri, self.kw, self.closed = uri, kw, False
            self.admin = _FakeAdmin(refuse)
            made.append(self)

        def __getitem__(self, name):
            assert name == "admin"
            return self.admin

        def close(self):
            self.closed = True

    monkeypatch.setitem(sys.modules, "pymongo", types.SimpleNamespace(MongoClient=Client))
    return made


def test_the_cluster_is_asked_with_the_read_only_user_first(monkeypatch):
    made = fake_mongo(monkeypatch)
    uris = {"MONGODB_READ_URI": "mongodb+srv://reader", "MONGODB_URI": "mongodb+srv://writer"}
    monkeypatch.setattr(UM, "_secret", lambda name: uris.get(name, ""))
    found, why = UM._atlas_database()
    assert why == "" and found == {"user": "read-only user", "atlas_size": 2.5e8,
                                   "connections": 7}
    assert made[0].uri == "mongodb+srv://reader" and made[0].closed
    assert made[0].admin.asked == ["atlasSize", "serverStatus"]       # reads only


def test_without_the_read_only_user_the_archive_s_is_used_and_said(monkeypatch):
    made = fake_mongo(monkeypatch, refuse=("serverStatus",))
    monkeypatch.setattr(UM, "_secret",
                        lambda name: "mongodb+srv://writer" if name == "MONGODB_URI" else "")
    found, why = UM._atlas_database()
    assert found["user"] == "archive's user" and made[0].uri == "mongodb+srv://writer"
    assert "connections" not in found and "serverStatus refused" in found["connections_why"]
    assert made[0].closed
