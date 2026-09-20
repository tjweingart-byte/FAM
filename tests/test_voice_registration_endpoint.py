"""The door a voice worker knocks on, and what keeps it shut.

`POST /api/voice/register` is the inbound half of the ladder: the pod says
where it is, so nobody has to edit a dashboard when RunPod moves it. It is also
the one endpoint in the app whose whole purpose is to change where FAM sends
the text it has just written, which is why most of this file is about who is
allowed to use it.
"""
from __future__ import annotations

import dataclasses
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import config  # noqa: E402
import voice_control  # noqa: E402
import voice_registry  # noqa: E402

POD = "https://abc123-8001.proxy.runpod.net"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    voice_control.reset()
    yield TestClient(appmod.app)
    voice_control.reset()


@pytest.fixture
def store(monkeypatch, tmp_path):
    opened = voice_registry.VoiceRegistry(str(tmp_path / "voice.db"))
    monkeypatch.setattr(voice_registry, "registry", lambda: opened)
    return opened


def with_token(monkeypatch, token="shared-secret"):
    patched = dataclasses.replace(config.settings, voice_registry_token=token)
    for module in (config, appmod, voice_control):
        monkeypatch.setattr(module, "settings", patched)
    return {"Authorization": f"Bearer {token}"}


def test_registration_is_absent_until_a_secret_is_set(client, store):
    """Not an open endpoint with a warning: an endpoint that takes "the voice
    is at this URL" from anybody redirects every script FAM writes."""
    answer = client.post("/api/voice/register", json={"url": POD})
    assert answer.status_code == 404
    assert store.all() == []


def test_a_wrong_token_is_refused(client, store, monkeypatch):
    with_token(monkeypatch)
    answer = client.post("/api/voice/register", json={"url": POD},
                         headers={"Authorization": "Bearer guess"})
    assert answer.status_code == 401
    assert store.all() == []


def test_a_worker_with_the_secret_is_recorded(client, store, monkeypatch):
    headers = with_token(monkeypatch)
    answer = client.post("/api/voice/register",
                         json={"url": POD, "mode": "http", "port": 8001,
                               "contract": 1, "sample_rate": 24000},
                         headers=headers)
    assert answer.status_code == 200
    assert answer.json()["registered"] is True
    assert [row.url for row in store.all()] == [POD]


def test_the_reply_names_no_other_worker_and_no_setting(client, store,
                                                        monkeypatch):
    """The caller is a GPU on somebody else's network. It needs to know it was
    heard, and nothing else about this deployment."""
    headers = with_token(monkeypatch, "shared-secret")
    store.register({"url": "https://someone-else-8001.proxy.runpod.net"})
    body = client.post("/api/voice/register", json={"url": POD},
                       headers=headers).json()
    assert "someone-else" not in str(body)
    assert "shared-secret" not in str(body)
    assert set(body) == {"ok", "registered", "url", "contract_expected",
                         "ttl_seconds"}


def test_an_address_the_registry_refuses_is_a_sentence_not_a_500(
        client, store, monkeypatch):
    headers = with_token(monkeypatch)
    answer = client.post("/api/voice/register",
                         json={"url": "http://pod.example"}, headers=headers)
    assert answer.status_code == 422 and "https" in answer.json()["error"]


def test_a_body_that_is_not_json_says_so(client, store, monkeypatch):
    headers = with_token(monkeypatch)
    answer = client.post("/api/voice/register", content=b"not json",
                         headers={**headers, "Content-Type": "application/json"})
    assert answer.status_code == 400


def test_a_worker_on_its_way_out_is_forgotten(client, store, monkeypatch):
    headers = with_token(monkeypatch)
    client.post("/api/voice/register", json={"url": POD}, headers=headers)
    answer = client.post("/api/voice/register",
                         json={"url": POD, "leaving": True}, headers=headers)
    assert answer.status_code == 200 and answer.json()["registered"] is False
    assert store.all() == []


def test_registering_is_a_claim_and_never_a_promotion(client, store,
                                                      monkeypatch):
    """Nothing is spoken to because it registered. The control plane still
    verifies it with a real call first, exactly as it does a configured
    address."""
    headers = with_token(monkeypatch)
    client.post("/api/voice/register", json={"url": POD}, headers=headers)
    assert voice_control.held() is None


def test_a_heartbeat_does_not_mint_a_listener(client, store, monkeypatch):
    """Every sixty seconds, for ever. Without this the accounts store fills up
    with listeners that are one GPU saying where it is - and its bearer token
    is a registration token rather than a session, so nothing would match."""
    headers = with_token(monkeypatch)
    minted = []
    monkeypatch.setattr(
        appmod.ACCOUNTS, "new_session",
        lambda *a, **k: (minted.append(1), ("tok", "u-1"))[1])
    client.post("/api/voice/register", json={"url": POD}, headers=headers)
    assert minted == [], "a worker's heartbeat minted a listener"
