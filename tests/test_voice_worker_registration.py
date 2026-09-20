"""The worker's half: computing its own address and announcing it.

Only the pod can know where it is. RunPod puts its id in the container's
environment and fronts each exposed port at a proxy URL derived from the pair,
so the address is derivable there and guessable nowhere else - which is why
this exists at all rather than somebody keeping Render's environment in
agreement with RunPod's by hand.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice_worker import register  # noqa: E402


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("PUBLIC_WORKER_URL", "RUNPOD_POD_ID", "PORT", "FAM_APP_URL",
                 "VOICE_REGISTRY_TOKEN", "VOICE_IMAGE", "FAM_COMMIT",
                 "VOICE_WORKER_MODE", "VOICE_REGISTER_INTERVAL"):
        monkeypatch.delenv(name, raising=False)


def test_a_runpod_pod_derives_its_own_proxy_url(monkeypatch):
    monkeypatch.setenv("RUNPOD_POD_ID", "abc123")
    monkeypatch.setenv("PORT", "8001")
    assert register.public_url() == "https://abc123-8001.proxy.runpod.net"


def test_the_port_it_announces_is_the_port_it_serves(monkeypatch):
    """§78's pair. A pod serving 8002 and announcing 8001 is a 404 on every
    path, and the app cannot tell that from a missing route."""
    monkeypatch.setenv("RUNPOD_POD_ID", "abc123")
    monkeypatch.setenv("PORT", "8002")
    assert register.public_url().endswith("-8002.proxy.runpod.net")


def test_anything_that_is_not_runpod_says_where_it_is(monkeypatch):
    monkeypatch.setenv("PUBLIC_WORKER_URL", "https://voice.example/")
    monkeypatch.setenv("RUNPOD_POD_ID", "abc123")
    assert register.public_url() == "https://voice.example"


def test_a_worker_that_cannot_know_its_address_says_so(monkeypatch):
    monkeypatch.setenv("FAM_APP_URL", "https://fam.example")
    monkeypatch.setenv("VOICE_REGISTRY_TOKEN", "shhh")
    assert "cannot say where it is" in register.why_not()


def test_registration_is_skipped_rather_than_attempted_half_configured(monkeypatch):
    assert register.why_not() == "FAM_APP_URL is not set"
    monkeypatch.setenv("FAM_APP_URL", "https://fam.example")
    assert register.why_not() == "VOICE_REGISTRY_TOKEN is not set"


def test_a_worker_with_nowhere_to_report_to_does_not_try(monkeypatch):
    """Registration is a rung of the app's ladder, never a dependency of
    speech: a worker with no FAM_APP_URL serves exactly as it always did."""
    def explode(*_a, **_k):  # pragma: no cover - must not be reached
        raise AssertionError("a worker with no app tried to register")

    monkeypatch.setattr(register, "payload", explode)
    assert asyncio.run(register.announce(True, "ready")) is False


def test_what_it_announces_carries_the_build_it_is_running(monkeypatch):
    """§77 across the split: "the fix is pushed" and "the fix is on the card"
    are the same sentence from the app's side."""
    monkeypatch.setenv("RUNPOD_POD_ID", "abc123")
    monkeypatch.setenv("VOICE_IMAGE", "ghcr.io/fam/voice:7")
    monkeypatch.setenv("FAM_COMMIT", "deadbeef")
    monkeypatch.setenv("VOICE_WORKER_MODE", "http")
    body = register.payload(True, "cuda", 24000)
    assert body["image"] == "ghcr.io/fam/voice:7"
    assert body["commit"] == "deadbeef" and body["mode"] == "http"
    assert body["sample_rate"] == 24000 and body["ready"] is True
    assert body["url"].startswith("https://abc123-")


def test_an_unbuilt_image_says_unknown_rather_than_guessing(monkeypatch):
    monkeypatch.setenv("RUNPOD_POD_ID", "abc123")
    body = register.payload(True, "cuda")
    assert body["image"] == "unknown" and body["commit"] == "unknown"


def test_a_worker_that_cannot_speak_registers_as_unable(monkeypatch):
    """Announced, not hidden: the app passes over it rather than sending it an
    episode, and somebody can see that the pod is up and the card is not."""
    monkeypatch.setenv("RUNPOD_POD_ID", "abc123")
    body = register.payload(False, "no GPU on this node")
    assert body["ready"] is False and "no GPU" in body["detail"]


def test_the_contract_it_claims_is_the_one_the_app_checks(monkeypatch):
    import voice_control

    monkeypatch.setenv("RUNPOD_POD_ID", "abc123")
    assert register.identity()["contract"] == voice_control.CONTRACT_VERSION


def test_a_registration_that_fails_never_raises(monkeypatch):
    monkeypatch.setenv("RUNPOD_POD_ID", "abc123")
    monkeypatch.setenv("FAM_APP_URL", "https://fam.example")
    monkeypatch.setenv("VOICE_REGISTRY_TOKEN", "shhh")

    import httpx

    class Exploding:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, *_a, **_k):
            raise RuntimeError("the app is down")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: Exploding())
    assert asyncio.run(register.announce(True, "ready")) is False


# --- which half of the image runs -------------------------------------------
#
# The default used to be `serverless`, so a pod started without
# VOICE_WORKER_MODE=http ran the handler and opened no port at all - a 404 on
# every path, indistinguishable from a missing route. One word, a day of
# diagnosis, twice (PROBLEMS.md §78, §112).


def test_a_serverless_worker_runs_the_handler():
    from voice_worker import start

    mode, why = start.decide({"RUNPOD_ENDPOINT_ID": "ep-1",
                              "RUNPOD_POD_ID": "worker-1"})
    assert mode == "serverless" and "RUNPOD_ENDPOINT_ID" in why


def test_a_pod_opens_a_port_without_being_told_to():
    """The fix for the failure this whole section is about: a pod has no
    endpoint id, so it cannot be a Serverless worker, so it needs a port."""
    from voice_worker import start

    mode, why = start.decide({"RUNPOD_POD_ID": "abc123"})
    assert mode == "http" and "pod" in why


def test_anything_that_is_not_runpod_serves_a_port():
    from voice_worker import start

    assert start.decide({})[0] == "http"


def test_an_explicit_mode_still_wins():
    from voice_worker import start

    assert start.decide({"VOICE_WORKER_MODE": "serverless",
                         "RUNPOD_POD_ID": "abc123"})[0] == "serverless"
    assert start.decide({"VOICE_WORKER_MODE": "http",
                         "RUNPOD_ENDPOINT_ID": "ep-1"})[0] == "http"


def test_a_typo_falls_back_to_the_platform_rather_than_to_a_guess():
    from voice_worker import start

    mode, why = start.decide({"VOICE_WORKER_MODE": "htpp",
                              "RUNPOD_POD_ID": "abc123"})
    assert mode == "http" and "RUNPOD_POD_ID" in why
