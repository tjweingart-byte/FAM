"""The voice outage of 10.23 (§318), pinned from both ends.

The worker was healthy for days and the app could not find it: heartbeats
stopped landing, the registration lapsed, RunPod's v2 answer carried no ports
so the only address left was a proxy URL that does not exist, and the only
alarm was a listener's apology. Each test here is one of those links.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import pipeline_errors  # noqa: E402
import voice_control  # noqa: E402
import voice_registry  # noqa: E402
from voice_worker import register  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean():
    voice_control.reset()
    yield
    voice_control.reset()


def configure(monkeypatch, **overrides):
    defaults = {
        "voice_backend": "remote", "remote_voice_transport": "http",
        "remote_voice_url": "", "remote_voice_token": "",
        "remote_voice_sample_rate": 24000, "runpod_endpoint_id": "",
        "runpod_api_key": "", "runpod_pod": "", "voice_discovery": "auto",
        "voice_registry_token": "", "voice_registry_ttl": 300.0,
        "voice_verify_ttl": 120.0, "voice_retry_seconds": 60.0,
        "voice_probe_timeout": 2.0, "voice_worker_port": 8002,
        "voice_allow_plain_http": True,
    }
    defaults.update(overrides)
    patched = dataclasses.replace(config.settings, **defaults)
    for module in (config, voice_control):
        monkeypatch.setattr(module, "settings", patched)
    return patched


# --- the worker: a redirect is not a registration ------------------------------

def _answering(monkeypatch, status, headers=None):
    import httpx

    class Response:
        status_code = status
        text = ""

        def __init__(self):
            self.headers = headers or {}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, *_a, **_k):
            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: Client())


@pytest.fixture
def worker_env(monkeypatch):
    for name in ("PUBLIC_WORKER_URL", "RUNPOD_PUBLIC_IP", "RUNPOD_TCP_PORT_8002"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RUNPOD_POD_ID", "abc123")
    monkeypatch.setenv("FAM_APP_URL", "https://old.example")
    monkeypatch.setenv("VOICE_REGISTRY_TOKEN", "shhh")


def test_a_redirected_registration_did_not_land_and_says_where_to(
        monkeypatch, worker_env, caplog):
    _answering(monkeypatch, 308, {"location": "https://new.example/api/voice/register"})
    with caplog.at_level(logging.WARNING, logger="voice_worker.register"):
        assert run(register.announce(True, "ready")) is False
    assert "https://new.example/api/voice/register" in caplog.text
    assert "FAM_APP_URL" in caplog.text


def test_an_accepted_registration_still_lands(monkeypatch, worker_env):
    _answering(monkeypatch, 200)
    assert run(register.announce(True, "ready")) is True


# --- the app: an answer with no ports is not the last word ----------------------

def _pod(**extra):
    pod = {"id": "ilx5", "name": "unable_coral_lobster", "status": "RUNNING"}
    pod.update(extra)
    return pod


def _asks(monkeypatch, answers):
    asked = []

    async def fake(method, url, key, json=None, params=None):
        asked.append(url)
        return answers(url)

    monkeypatch.setattr(voice_control, "_ask_runpod", fake)
    return asked


def test_a_pod_named_without_ports_sends_the_question_to_the_next_api(monkeypatch):
    """10.23: v2 named the pod and nothing else, so the ladder built
    https://<pod>-8002.proxy.runpod.net - a port the pod never exposed over
    http - while v1 would have said 213.173.105.20:30248."""
    configure(monkeypatch, runpod_pod="ilx5", runpod_api_key="k",
              runpod_api_url="https://api.runpod.io/v2",
              runpod_rest_url="https://rest.runpod.io/v1")

    def answers(url):
        if "/v2/" in url:
            return {"pods": [_pod()], "pagination": {"hasNextPage": False}}
        return [_pod(publicIp="213.173.105.20", portMappings={"22": 30247, "8002": 30248})]

    asked = _asks(monkeypatch, answers)
    found = run(voice_control._runpod_pods())
    assert asked[:2] == ["https://api.runpod.io/v2/pods", "https://rest.runpod.io/v1/pods"]
    assert found[0].url == "http://213.173.105.20:30248"
    assert any("no ports" in note for note in voice_control.report()["pods"])


def test_the_guess_is_kept_when_no_api_knows_more(monkeypatch):
    configure(monkeypatch, runpod_pod="ilx5", runpod_api_key="k")
    _asks(monkeypatch, lambda url: {"pods": [_pod()]})
    found = run(voice_control._runpod_pods())
    assert [c.url for c in found] == ["https://ilx5-8002.proxy.runpod.net"]


def test_ports_declined_by_this_deployment_are_a_complete_answer(monkeypatch):
    """Plain HTTP off is a decision, not a missing answer: no second API."""
    configure(monkeypatch, runpod_pod="ilx5", runpod_api_key="k",
              voice_allow_plain_http=False)
    full = _pod(publicIp="213.173.105.20", portMappings={"8002": 30248},
                runtime={"ports": [{"privatePort": 8002, "publicPort": 30248,
                                    "type": "http"}]})
    asked = _asks(monkeypatch, lambda url: {"pods": [full]})
    run(voice_control._runpod_pods())
    assert len(asked) == 1


# --- /admin hears about it -------------------------------------------------------

@pytest.fixture
def recorded(monkeypatch):
    rows = []
    monkeypatch.setattr(pipeline_errors, "record",
                        lambda stage, kind, detail="", query="", severity="error":
                        rows.append((stage, kind, severity)))
    return rows


@pytest.fixture
def registry(monkeypatch, tmp_path):
    store = voice_registry.VoiceRegistry(str(tmp_path / "registry.db"))
    monkeypatch.setattr(voice_registry, "_REGISTRY", store)
    return store


def test_a_voice_nobody_can_reach_is_on_admin_once_an_hour(monkeypatch, recorded):
    configure(monkeypatch)

    async def nobody(force=False):
        raise voice_control.VoiceControlError("no voice worker can speak. Tried ...")

    monkeypatch.setattr(voice_control, "current", nobody)
    run(voice_control.supervise_once())
    run(voice_control.supervise_once())
    assert recorded == [("voice", "no worker can speak", "error")]


def test_a_voice_that_came_back_is_reported_again_the_next_time_it_goes(
        monkeypatch, recorded):
    configure(monkeypatch)
    up = {"now": False}

    async def maybe(force=False):
        if not up["now"]:
            raise voice_control.VoiceControlError("down")

    monkeypatch.setattr(voice_control, "current", maybe)
    run(voice_control.supervise_once())
    up["now"] = True
    run(voice_control.supervise_once())
    up["now"] = False
    run(voice_control.supervise_once())
    assert [kind for _, kind, _ in recorded] == ["no worker can speak"] * 2


def test_a_worker_that_stopped_registering_is_named_even_while_the_voice_works(
        monkeypatch, recorded, registry):
    configure(monkeypatch, voice_registry_token="shhh")
    registry.register({"url": "http://213.173.105.20:30248"}, at=1_000_000.0)

    async def fine(force=False):
        return None

    monkeypatch.setattr(voice_control, "current", fine)
    monkeypatch.setattr(voice_control.time, "time", lambda: 1_000_000.0 + 3 * 86400)
    run(voice_control.supervise_once())
    assert recorded == [("voice", "registration lapsed", "notice")]
    assert "30248" in voice_control.registration_lapse()


def test_a_live_registration_is_not_a_lapse(monkeypatch, registry):
    configure(monkeypatch, voice_registry_token="shhh")
    registry.register({"url": "http://213.173.105.20:30248"}, at=1_000_000.0)
    assert voice_control.registration_lapse(at=1_000_000.0 + 60) == ""


def test_history_is_not_a_lapse(monkeypatch, registry):
    configure(monkeypatch, voice_registry_token="shhh")
    registry.register({"url": "http://213.173.105.20:30248"}, at=1_000_000.0)
    assert voice_control.registration_lapse(at=1_000_000.0 + 30 * 86400) == ""


def test_no_registration_configured_is_not_a_lapse(monkeypatch):
    configure(monkeypatch, voice_registry_token="")
    assert voice_control.registration_lapse() == ""


def test_both_faults_are_explained_in_words():
    assert pipeline_errors.explain(
        "voice", "no worker can speak")["title"] == "No voice worker can speak"
    assert pipeline_errors.explain(
        "voice", "registration lapsed")["title"] == "The voice worker stopped registering"
    assert "voice" in pipeline_errors.STAGES
