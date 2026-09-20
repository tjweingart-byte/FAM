"""The pod that logged a loaded model, a port, and 404 on everything.

Production, on `n86q9611hepgw7`:

    voice_worker model resident, emitting 24000 Hz
    Uvicorn running on http://0.0.0.0:8002
    POST https://n86q9611hepgw7-8002.proxy.runpod.net/synth -> 404
    GET  https://n86q9611hepgw7-8002.proxy.runpod.net/openapi.json -> 404

The second 404 is the one that reads: FastAPI serves `/openapi.json` for
nothing, so whatever answered was not `voice_worker/server.py`. Either the
container was never running it - `Dockerfile.voice` chose the entrypoint with a
shell string comparison, and everything except the exact token `http` ran the
*serverless* handler, which logs the same "model resident" line from the same
logger and opens no port - or the request never reached it, because the port in
the proxy URL is not the port the container serves.

So these are about the two questions a container must answer about itself, and
about being able to tell a 404 from the worker apart from a 404 in front of it.
PROBLEMS.md §112.
"""
from __future__ import annotations

import base64
import os
import pathlib
import socket
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import remote_voice  # noqa: E402
from voice_worker import deployment  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PCM = b"\x01\x02" * 480


# --- which entrypoint this container is ------------------------------------

@pytest.mark.parametrize("env, mode", [
    # The four spellings that used to open no port on a pod.
    ({}, deployment.MODE_HTTP),
    ({"VOICE_WORKER_MODE": "HTTP"}, deployment.MODE_HTTP),
    ({"VOICE_WORKER_MODE": "http "}, deployment.MODE_HTTP),
    ({"VOICE_WORKER_MODE": '"http"'}, deployment.MODE_HTTP),
    # And the ones that must still select the queue worker.
    ({"VOICE_WORKER_MODE": "serverless"}, deployment.MODE_SERVERLESS),
    ({"VOICE_WORKER_MODE": "auto",
      "RUNPOD_WEBHOOK_GET_JOB": "https://api.runpod.ai/..."},
     deployment.MODE_SERVERLESS),
])
def test_the_mode_is_resolved_rather_than_string_matched(env, mode):
    assert deployment.resolve_mode(env)[0] == mode


def test_nothing_serverless_in_the_environment_means_a_port_is_opened():
    """The asymmetry is the whole point: a pod guessed wrong is a dead
    endpoint, a serverless worker guessed wrong is a port nobody dials."""
    mode, why = deployment.resolve_mode({"VOICE_WORKER_MODE": "htp"})
    assert mode == deployment.MODE_HTTP
    assert "not a mode" in why and "rather than none" in why


def test_a_serverless_worker_is_named_by_its_own_platform():
    """Auto-detection reads what RunPod set, never a default that can only be
    wrong one way."""
    for marker in deployment.SERVERLESS_MARKERS:
        assert deployment.resolve_mode({marker: "set"})[0] == \
            deployment.MODE_SERVERLESS
    assert deployment.looks_serverless({}) == ""


def test_the_reason_is_carried_so_the_pod_log_says_who_decided():
    assert "VOICE_WORKER_MODE" in deployment.resolve_mode(
        {"VOICE_WORKER_MODE": "serverless"})[1]
    assert "RUNPOD_ENDPOINT_ID" in deployment.resolve_mode(
        {"RUNPOD_ENDPOINT_ID": "abc"})[1]


# --- which ports it answers on ---------------------------------------------

def test_both_ports_this_deployment_has_used_are_served_by_default():
    """8001 is what `.env.example` documents; 8002 is what production proxied.
    Which one is in the URL cannot be seen from inside the container."""
    assert deployment.resolve_ports({}) == [8001, 8002]


def test_the_configured_port_comes_first_and_is_never_dropped():
    assert deployment.resolve_ports({"PORT": "8002"})[0] == 8002
    assert deployment.resolve_ports({"PORT": "9000"})[0] == 9000


def test_a_declared_list_replaces_the_default_and_collapses_duplicates():
    assert deployment.resolve_ports(
        {"PORT": "8002", "VOICE_WORKER_PORTS": "8002, 8001"}) == [8002, 8001]
    assert deployment.resolve_ports({"VOICE_WORKER_PORTS": "not-a-port"}) == \
        list(deployment.DEFAULT_PORTS), "an unusable list is not no ports"


# --- the image runs the entrypoint, not a shell comparison ------------------

def test_the_image_starts_the_entrypoint():
    docker = (ROOT / "Dockerfile.voice").read_text()
    assert "voice_worker.entrypoint" in docker
    assert 'if [ "$VOICE_WORKER_MODE" = http ]' not in docker, (
        "the mode is resolved in Python; a shell can only compare strings, "
        "and every near-miss opened no port at all")


def test_the_image_exposes_every_port_the_worker_binds():
    docker = (ROOT / "Dockerfile.voice").read_text()
    for port in deployment.DEFAULT_PORTS:
        assert str(port) in docker


# --- the contract is on the route the app asks for --------------------------

@pytest.fixture
def worker_app(monkeypatch):
    """The real app, with the card stubbed out."""
    from voice_worker import server as worker_server
    from voice_worker import synth as synth_module

    class StubEngine:
        sample_rate = 24000

        async def synth(self, text, wpm=0.0, voice=None):
            return PCM

    monkeypatch.setattr(synth_module, "ChatterboxEngine", StubEngine)
    monkeypatch.setattr(synth_module, "preflight", lambda: (True, "stub"))
    monkeypatch.setattr(worker_server, "preflight", lambda: (True, "stub"))
    monkeypatch.setattr(worker_server, "TOKEN", "")
    return worker_server


def test_the_two_halves_name_the_same_route(worker_app):
    """Deployed separately, so this is checked from both ends rather than
    trusted - §83's argument about the cache key, applied to an address."""
    assert worker_app.SYNTH_ROUTE == remote_voice.SYNTH_ROUTE
    assert f"POST {remote_voice.SYNTH_ROUTE}" in worker_app.route_table()


def test_the_schema_the_app_falls_back_to_reading_names_synth(worker_app):
    from fastapi.testclient import TestClient

    with TestClient(worker_app.app) as client:
        schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert remote_voice.SYNTH_ROUTE in schema.json()["paths"]


def test_a_404_from_the_worker_says_it_came_from_the_worker(worker_app):
    """The discriminator production did not have. RunPod's proxy answers a
    port it is not forwarding with its own `404 page not found`; this answers
    with its own name, and the app logs the body."""
    from fastapi.testclient import TestClient

    with TestClient(worker_app.app) as client:
        missing = client.post("/speak", json={})
    assert missing.status_code == 404
    body = missing.json()
    assert body["worker"] == worker_app.IDENTITY
    assert body["synth"] == remote_voice.SYNTH_ROUTE
    assert f"POST {remote_voice.SYNTH_ROUTE}" in body["routes"]


# --- verify, do not inspect: a real socket, on two ports at once ------------

def _free_ports(count: int) -> list[int]:
    socks = [socket.socket() for _ in range(count)]
    try:
        ports = []
        for sock in socks:
            sock.bind(("127.0.0.1", 0))
            ports.append(sock.getsockname()[1])
        return ports
    finally:
        for sock in socks:
            sock.close()


def test_every_bound_port_serves_the_contract(worker_app, monkeypatch):
    """§52 applied to an address: reading the Dockerfile answers a cheaper
    question than asking the process. This asks the process, on both ports,
    over TCP, and decodes the reply through the app's own checks."""
    import httpx

    from voice_worker import entrypoint

    ports = _free_ports(2)
    monkeypatch.setattr(entrypoint, "HOST", "127.0.0.1")
    monkeypatch.setenv("VOICE_WORKER_PORTS", ",".join(str(p) for p in ports))
    monkeypatch.delenv("PORT", raising=False)

    thread = threading.Thread(target=entrypoint.serve_http, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{ports[0]}/health",
                             timeout=1.0).status_code == 200:
                    break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("the worker never answered /health")

        for port in ports:
            base = f"http://127.0.0.1:{port}"
            assert httpx.get(f"{base}/openapi.json", timeout=5.0).status_code == 200
            reply = httpx.post(f"{base}{remote_voice.SYNTH_ROUTE}",
                               json={"text": "One line.", "sample_rate": 24000,
                                     "format": remote_voice.WIRE_FORMAT},
                               timeout=20.0)
            assert reply.status_code == 200, reply.text
            assert base64.b64decode(reply.json()["audio"]) == PCM
            assert reply.json()["sample_rate"] == 24000, "24 kHz, unchanged"
    finally:
        entrypoint.stop_all()
        thread.join(timeout=10)


def test_a_port_already_taken_does_not_cost_the_other_one(worker_app, monkeypatch):
    """On a rented pod, something else on 8002 must not silence the voice."""
    import httpx

    from voice_worker import entrypoint

    ports = _free_ports(2)
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", ports[0]))
    blocker.listen(1)

    monkeypatch.setattr(entrypoint, "HOST", "127.0.0.1")
    monkeypatch.setenv("VOICE_WORKER_PORTS", ",".join(str(p) for p in ports))
    monkeypatch.delenv("PORT", raising=False)

    thread = threading.Thread(target=entrypoint.serve_http, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{ports[1]}/health",
                             timeout=1.0).status_code == 200:
                    break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("one unavailable port stopped the whole worker")
    finally:
        entrypoint.stop_all()
        thread.join(timeout=10)
        blocker.close()


def test_a_container_that_can_open_nothing_says_so(monkeypatch):
    from voice_worker import entrypoint

    monkeypatch.setattr(entrypoint, "_bind", lambda port: (None, "taken"))
    monkeypatch.setenv("VOICE_WORKER_PORTS", "8001")
    assert entrypoint.serve_http() == 1, "no port is a failure, not a quiet boot"
