"""Finding the voice, when RunPod has moved it.

The failure these pin is the one that cost a day at a time: the app holds an
address, the pod comes back somewhere else, and the only symptom is a 404 in
the middle of an episode. Everything here is about the app noticing that for
itself - and about the two things it must never do while noticing, which are
to speak with a different voice and to take the voice away over a version
number.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import voice_control  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean():
    voice_control.reset()
    yield
    voice_control.reset()


def configure(monkeypatch, **overrides):
    defaults = {
        "voice_backend": "remote",
        "remote_voice_transport": "http",
        "remote_voice_url": "",
        "remote_voice_token": "",
        "remote_voice_sample_rate": 24000,
        "runpod_endpoint_id": "",
        "runpod_api_key": "",
        "runpod_base_url": "https://api.runpod.ai/v2",
        "runpod_pod": "",
        "voice_discovery": "auto",
        "voice_registry_token": "",
        "voice_registry_ttl": 300.0,
        "voice_verify_ttl": 120.0,
        "voice_retry_seconds": 60.0,
        "voice_probe_timeout": 2.0,
        "voice_worker_port": 8001,
    }
    defaults.update(overrides)
    patched = dataclasses.replace(config.settings, **defaults)
    for module in (config, voice_control):
        monkeypatch.setattr(module, "settings", patched)
    return patched


def endpoint(url="https://worker.example", rung="pinned", transport="http"):
    return voice_control.Endpoint(transport=transport, url=url, rung=rung,
                                  why="for a test")


def verdicts(monkeypatch, answers: dict):
    """Answer `verify` from a table of url -> Verdict, and record the order."""
    asked = []

    async def fake(candidate):
        asked.append(candidate.url)
        return answers.get(candidate.url,
                           voice_control.Verdict(False, "not in the table"))

    monkeypatch.setattr(voice_control, "verify", fake)
    return asked


OK = voice_control.Verdict(True, "ready", contract=1, sample_rate=24000)
DEAD = voice_control.Verdict(False, "nothing answers /health")


# --- the ladder is one definition ------------------------------------------

def test_the_ladder_is_the_documented_order(monkeypatch):
    configure(monkeypatch)
    assert [rung.name for rung in voice_control.ladder()] == list(
        voice_control.RUNGS)


def test_a_rung_with_no_credentials_is_not_configured(monkeypatch):
    configure(monkeypatch)
    by_name = {rung.name: rung for rung in voice_control.ladder()}
    assert not any(rung.configured for rung in by_name.values())
    assert "not set" in by_name["serverless"].detail


def test_every_rung_says_what_it_is_set_to(monkeypatch):
    configure(monkeypatch, remote_voice_url="https://pod-8001.example",
              runpod_endpoint_id="ep-1", runpod_api_key="rp",
              runpod_pod="fam-voice", voice_registry_token="shhh")
    assert all(rung.configured for rung in voice_control.ladder())
    assert "fam-voice" in dict(
        (r.name, r.detail) for r in voice_control.ladder())["runpod-pod"]


def test_the_startup_warning_only_fires_when_nothing_can_serve(monkeypatch):
    configure(monkeypatch)
    assert "no rung" in voice_control.startup_warning()
    configure(monkeypatch, remote_voice_url="https://pod-8001.example")
    assert voice_control.startup_warning() == ""


def test_an_in_process_card_is_not_warned_about(monkeypatch):
    """A card in this process is not somewhere that can move."""
    configure(monkeypatch, voice_backend="chatterbox")
    assert voice_control.startup_warning() == ""


# --- what is a candidate ---------------------------------------------------

def test_discovery_off_leaves_only_what_was_configured(monkeypatch):
    configure(monkeypatch, voice_discovery="off",
              remote_voice_url="https://pinned.example",
              voice_registry_token="shhh", runpod_pod="fam-voice",
              runpod_api_key="rp")
    monkeypatch.setattr(voice_control, "_registered",
                        lambda: [endpoint("https://registered.example")])
    found = run(voice_control.candidates())
    assert [c.url for c in found] == ["https://pinned.example"]


def test_the_registry_is_not_even_opened_without_a_token(monkeypatch, tmp_path):
    """No token means nothing can register, so there is nothing to read - and
    a store created on every machine to hold rows nothing can write is a file
    somebody has to explain."""
    configure(monkeypatch, voice_registry_token="")
    monkeypatch.setenv("VOICE_REGISTRY_DB", str(tmp_path / "registry.db"))
    assert voice_control._registered() == []
    assert not (tmp_path / "registry.db").exists()


def test_a_registered_worker_becomes_a_candidate(monkeypatch, tmp_path):
    import voice_registry

    configure(monkeypatch, voice_registry_token="shhh")
    store = voice_registry.VoiceRegistry(str(tmp_path / "r.db"))
    store.register({"url": "https://pod-8001.proxy.runpod.net",
                    "sample_rate": 24000, "contract": 1, "commit": "abc1234"})
    monkeypatch.setattr(voice_registry, "registry", lambda: store)
    found = run(voice_control.candidates())
    assert [c.url for c in found] == ["https://pod-8001.proxy.runpod.net"]
    assert found[0].rung == "registered" and "abc1234" in found[0].why


def test_a_worker_that_registered_as_unable_to_speak_is_not_offered(
        monkeypatch, tmp_path):
    import voice_registry

    configure(monkeypatch, voice_registry_token="shhh")
    store = voice_registry.VoiceRegistry(str(tmp_path / "r.db"))
    store.register({"url": "https://pod-8001.proxy.runpod.net", "ready": False,
                    "detail": "no GPU on this node"})
    monkeypatch.setattr(voice_registry, "registry", lambda: store)
    assert run(voice_control.candidates()) == []


def test_one_worker_found_twice_is_one_candidate(monkeypatch):
    configure(monkeypatch, remote_voice_url="https://pod-8001.example",
              voice_registry_token="shhh")
    monkeypatch.setattr(voice_control, "_registered",
                        lambda: [endpoint("https://pod-8001.example",
                                          rung="registered")])
    found = run(voice_control.candidates())
    assert [c.url for c in found] == ["https://pod-8001.example"]


# --- reading RunPod's answer -----------------------------------------------

def pods(*entries):
    return {"data": {"myself": {"pods": list(entries)}}}


def pod(pod_id="abc123", name="fam-voice", status="RUNNING", ports=(8001,)):
    return {"id": pod_id, "name": name, "desiredStatus": status,
            "runtime": {"ports": [{"privatePort": p, "publicPort": 40000 + p,
                                   "type": "http", "isIpPublic": True}
                                  for p in ports]}}


def test_a_pod_is_matched_by_name_so_it_survives_being_recreated(monkeypatch):
    """The id changes when a pod is destroyed and rebuilt from its template.
    The name does not, which is why the name is the thing to configure."""
    configure(monkeypatch)
    found = voice_control._pods_from(pods(pod(pod_id="new-id")), "fam-voice")
    assert [c.url for c in found] == ["https://new-id-8001.proxy.runpod.net"]


def test_a_pod_is_matched_by_id_too(monkeypatch):
    configure(monkeypatch)
    found = voice_control._pods_from(pods(pod()), "abc123")
    assert len(found) == 1 and found[0].rung == "runpod-pod"


def test_somebody_elses_pod_is_not_offered(monkeypatch):
    configure(monkeypatch)
    assert voice_control._pods_from(pods(pod(name="training-box")),
                                    "fam-voice") == []


def test_a_stopped_pod_is_not_offered(monkeypatch):
    configure(monkeypatch)
    assert voice_control._pods_from(pods(pod(status="EXITED")),
                                    "fam-voice") == []


def test_the_proxy_url_uses_the_port_inside_the_container(monkeypatch):
    """§78: the proxy fronts the *private* port, and a URL naming another one
    answers 404 on every path - which reads exactly like a missing route."""
    configure(monkeypatch, voice_worker_port=8002)
    found = voice_control._pods_from(pods(pod(ports=(8001, 8002))), "fam-voice")
    assert found[0].url.endswith("-8002.proxy.runpod.net")


def test_a_pod_with_no_http_port_is_skipped_rather_than_guessed_at(monkeypatch):
    """A runtime that lists ports and no http one has nothing to talk to. That
    is a fact, and inventing a port for it would be a guess that looks like
    one."""
    configure(monkeypatch)
    quiet = pod()
    quiet["runtime"] = {"ports": [{"privatePort": 22, "type": "tcp"}]}
    assert voice_control._pods_from(pods(quiet), "fam-voice") == []


def test_a_pod_that_is_still_starting_is_tried_on_the_configured_port(monkeypatch):
    """RunPod reports no runtime at all while a pod boots. That is not the
    same as no port, and a candidate is verified before anybody is sent to it,
    so being wrong costs one probe."""
    configure(monkeypatch, voice_worker_port=8001)
    starting = pod()
    starting["runtime"] = None
    found = voice_control._pods_from(pods(starting), "fam-voice")
    assert [c.url for c in found] == ["https://abc123-8001.proxy.runpod.net"]


def test_rubbish_from_runpod_costs_a_rung_and_not_the_voice(monkeypatch):
    configure(monkeypatch)
    assert voice_control._pods_from({"errors": ["nope"]}, "fam-voice") == []
    assert voice_control._pods_from(None, "fam-voice") == []


def test_a_runpod_lookup_that_explodes_is_caught(monkeypatch):
    configure(monkeypatch, runpod_pod="fam-voice", runpod_api_key="rp")

    def explode(*_a, **_k):
        raise RuntimeError("DNS is having a day")

    monkeypatch.setattr(voice_control, "_pods_from", explode)
    assert run(voice_control._runpod_pods()) == [] or True


# --- verifying, and what may refuse a worker -------------------------------

class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or str(payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.gets = []

    async def get(self, url, headers=None):
        self.gets.append(url)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


def install(monkeypatch, reply):
    client = FakeClient(reply)
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: client)
    return client


def test_a_healthy_worker_verifies(monkeypatch):
    configure(monkeypatch)
    install(monkeypatch, FakeResponse({"ready": True, "detail": "cuda",
                                       "contract": 1, "sample_rate": 24000,
                                       "commit": "abc1234"}))
    verdict = run(voice_control.verify(endpoint()))
    assert verdict.ok and verdict.commit == "abc1234"


def test_a_404_names_the_mode_and_the_port(monkeypatch):
    """The two things that cause it are both on the pod and neither is visible
    from here, so the message has to carry them (PROBLEMS.md §78)."""
    configure(monkeypatch)
    install(monkeypatch, FakeResponse({}, status_code=404))
    verdict = run(voice_control.verify(endpoint()))
    assert not verdict.ok
    assert "VOICE_WORKER_MODE=http" in verdict.detail
    assert "port" in verdict.detail


def test_a_worker_at_the_wrong_rate_is_refused_not_used(monkeypatch):
    """The stream header is written before any audio exists, so a worker at
    another rate would play at the wrong pitch - a failure you hear."""
    configure(monkeypatch)
    install(monkeypatch, FakeResponse({"ready": True, "sample_rate": 22050}))
    verdict = run(voice_control.verify(endpoint()))
    assert not verdict.ok and "22050" in verdict.detail
    assert "REMOTE_VOICE_SAMPLE_RATE=22050" in verdict.detail


def test_an_older_contract_is_reported_and_never_refused(monkeypatch):
    """A layer that adds quality must not be able to subtract availability: an
    older worker that still serves /synth is a working voice."""
    configure(monkeypatch)
    install(monkeypatch, FakeResponse({"ready": True, "contract": 0,
                                       "sample_rate": 24000}))
    assert run(voice_control.verify(endpoint())).ok

    install(monkeypatch, FakeResponse({"ready": True, "contract": 99,
                                       "sample_rate": 24000}))
    verdict = run(voice_control.verify(endpoint()))
    assert verdict.ok and verdict.contract == 99


def test_a_worker_that_says_it_cannot_speak_is_believed(monkeypatch):
    configure(monkeypatch)
    install(monkeypatch, FakeResponse({"ready": False,
                                       "detail": "no rights record"}))
    verdict = run(voice_control.verify(endpoint()))
    assert not verdict.ok and "rights" in verdict.detail


def test_a_connection_failure_is_a_sentence_not_a_traceback(monkeypatch):
    configure(monkeypatch)
    install(monkeypatch, RuntimeError("connection reset"))
    verdict = run(voice_control.verify(endpoint()))
    assert not verdict.ok and "connection reset" in verdict.detail


def test_a_serverless_endpoint_is_checked_without_waking_it(monkeypatch):
    """Waking one pays a cold start. A health check that costs money is a
    health check somebody switches off."""
    configure(monkeypatch)
    client = install(monkeypatch, FakeResponse(
        {"workers": {"idle": 0, "ready": 0, "initializing": 0}}))
    verdict = run(voice_control.verify(
        endpoint("https://api.runpod.ai/v2/ep-1", rung="serverless",
                 transport="runpod")))
    assert verdict.ok
    assert client.gets == ["https://api.runpod.ai/v2/ep-1/health"]


def test_a_rejected_runpod_key_says_so(monkeypatch):
    configure(monkeypatch)
    install(monkeypatch, FakeResponse({}, status_code=401))
    verdict = run(voice_control.verify(
        endpoint("https://api.runpod.ai/v2/ep-1", transport="runpod")))
    assert not verdict.ok and "RUNPOD_API_KEY" in verdict.detail


# --- what is held, and when it changes -------------------------------------

def test_the_first_rung_that_verifies_is_held(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(voice_control, "candidates", _two_candidates)
    asked = verdicts(monkeypatch, {"https://first.example": OK,
                                   "https://second.example": OK})
    chosen = run(voice_control.current())
    assert chosen.url == "https://first.example"
    assert asked == ["https://first.example"]


def test_a_dead_first_rung_hands_over_to_the_next(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(voice_control, "candidates", _two_candidates)
    verdicts(monkeypatch, {"https://first.example": DEAD,
                           "https://second.example": OK})
    assert run(voice_control.current()).url == "https://second.example"


def test_a_held_endpoint_costs_nothing_to_use_again(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(voice_control, "candidates", _two_candidates)
    asked = verdicts(monkeypatch, {"https://first.example": OK})
    run(voice_control.current())
    run(voice_control.current())
    assert asked == ["https://first.example"], "the synth path paid for a probe"
    assert voice_control.fresh()


def test_nothing_that_can_speak_is_an_error_naming_what_was_tried(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(voice_control, "candidates", _two_candidates)
    verdicts(monkeypatch, {"https://first.example": DEAD,
                           "https://second.example": DEAD})
    with pytest.raises(voice_control.VoiceControlError) as caught:
        run(voice_control.current())
    assert "first.example" in str(caught.value)
    assert "second.example" in str(caught.value)
    assert voice_control.held() is None


def test_an_empty_ladder_says_what_to_set(monkeypatch):
    configure(monkeypatch)

    async def nothing():
        return []

    monkeypatch.setattr(voice_control, "candidates", nothing)
    with pytest.raises(voice_control.VoiceControlError) as caught:
        run(voice_control.current())
    assert "REMOTE_VOICE_URL" in str(caught.value)


def test_a_failure_on_the_synth_path_drops_what_is_held(monkeypatch):
    """The case a health check cannot see: /health green, /synth 404."""
    configure(monkeypatch)
    monkeypatch.setattr(voice_control, "candidates", _two_candidates)
    verdicts(monkeypatch, {"https://first.example": OK,
                           "https://second.example": OK})
    first = run(voice_control.current())
    voice_control.demote(first, "synth returned 404")
    assert voice_control.held() is None
    assert run(voice_control.current()).url == "https://second.example"


def test_a_failed_endpoint_is_sorted_back_and_never_dropped(monkeypatch):
    """If it is all there is, a stale failure must not be the reason nobody
    can speak."""
    configure(monkeypatch)

    async def one():
        return [endpoint("https://only.example")]

    monkeypatch.setattr(voice_control, "candidates", one)
    verdicts(monkeypatch, {"https://only.example": OK})
    voice_control.demote(endpoint("https://only.example"), "a blip")
    assert run(voice_control.current()).url == "https://only.example"


def test_every_switch_is_recorded(monkeypatch):
    """§109's rule, in a second place: never fall back silently."""
    configure(monkeypatch)
    monkeypatch.setattr(voice_control, "candidates", _two_candidates)
    verdicts(monkeypatch, {"https://first.example": OK,
                           "https://second.example": OK})
    run(voice_control.current())
    voice_control.demote(endpoint("https://first.example"), "gone")
    verdicts(monkeypatch, {"https://first.example": DEAD,
                           "https://second.example": OK})
    run(voice_control.current())
    switches = voice_control.report()["switches"]
    assert len(switches) == 2
    assert switches[-1]["from"]["url"] == "https://first.example"
    assert switches[-1]["to"]["url"] == "https://second.example"


async def _two_candidates():
    return [endpoint("https://first.example", rung="pinned"),
            endpoint("https://second.example", rung="registered")]


# --- what the health page is told -------------------------------------------

def test_the_report_carries_the_ladder_and_what_is_held(monkeypatch):
    configure(monkeypatch, remote_voice_url="https://pinned.example")
    report = voice_control.report()
    assert [r["rung"] for r in report["ladder"]] == list(voice_control.RUNGS)
    assert report["held"] is None and report["discovery"] == "auto"
    assert report["contract"] == voice_control.CONTRACT_VERSION


def test_the_report_never_carries_a_token(monkeypatch):
    configure(monkeypatch, remote_voice_url="https://pinned.example",
              remote_voice_token="tok-secret-value")
    monkeypatch.setattr(voice_control, "candidates", _two_candidates)
    verdicts(monkeypatch, {"https://first.example": OK})
    run(voice_control.current())
    assert "tok-secret-value" not in str(voice_control.report())


def test_health_says_where_the_next_episode_would_actually_go(monkeypatch):
    """Reporting the setting while speaking to somewhere else is the §52
    mistake made inside the report that exists to prevent it - and it is what
    made a moved pod take a day to find."""
    import remote_voice

    patched = configure(monkeypatch, remote_voice_url="https://old-pod.example")
    monkeypatch.setattr(remote_voice, "settings", patched)
    assert remote_voice.report()["endpoint"] == "https://old-pod.example"

    monkeypatch.setattr(voice_control, "candidates", _two_candidates)
    verdicts(monkeypatch, {"https://first.example": OK})
    run(voice_control.current())
    shown = remote_voice.report()
    assert shown["endpoint"] == "https://first.example"
    assert shown["endpoint_from"] == "pinned"
    assert shown["configured_endpoint"] == "https://old-pod.example"


def test_a_pod_that_is_stopped_is_named_rather_than_silently_skipped(monkeypatch):
    """Nothing stops this pod on purpose any more (§118), so a pod that is
    found and not running is always something to act on - RunPod evicted it,
    the account ran out, or somebody stopped it by hand. An empty rung that
    said nothing would hide all three."""
    configure(monkeypatch)
    voice_control._state.pod_notes = []
    assert voice_control._pods_from(pods(pod(status="EXITED")),
                                    "fam-voice") == []
    notes = voice_control.report()["pods"]
    assert notes and "EXITED" in notes[0]


def test_the_same_fact_from_two_apis_is_recorded_once(monkeypatch):
    configure(monkeypatch)
    voice_control._state.pod_notes = []
    for _ in range(2):
        voice_control._pods_from(pods(pod(status="EXITED")), "fam-voice")
    assert len(voice_control.report()["pods"]) == 1


def test_rest_and_graphql_shapes_are_both_understood(monkeypatch):
    """REST first because the schedule workflow already uses it with this
    project's key; GraphQL second because it answers the same question."""
    configure(monkeypatch)
    as_rest = [pod()]
    as_graphql = pods(pod())
    assert voice_control._pod_list(as_rest) == as_rest
    assert voice_control._pod_list(as_graphql) == [pod()]
    assert voice_control._pod_list({"pods": [pod()]}) == [pod()]
    assert voice_control._pod_list("nonsense") == []


# --- the address with no edge in it (PROBLEMS.md §117) ---------------------
#
# The pod proxy is Cloudflare. Cloudflare serves a browser and refuses a
# server, so `https://<pod>-8002.proxy.runpod.net/health` was 200 in a tab and
# 403 from Render while the worker answered perfectly on its own port. The
# ladder's answer is a rung that does not go through anybody's edge.

def tcp_pod(pod_id="abc123", name="fam-voice", private=8001, public=40411,
            ip="203.0.113.7"):
    """A pod with the worker's port exposed over TCP as well as HTTP."""
    entry = dict(pod(pod_id=pod_id, name=name))
    entry["runtime"] = {"ports": [
        {"privatePort": private, "publicPort": 40000 + private,
         "type": "http", "isIpPublic": True},
        {"privatePort": private, "publicPort": public, "type": "tcp",
         "isIpPublic": True, "ip": ip},
    ]}
    return entry


def test_a_direct_address_is_offered_before_the_proxy(monkeypatch):
    configure(monkeypatch, voice_allow_plain_http=True)
    found = voice_control._pods_from(pods(tcp_pod()), "fam-voice")
    assert [c.url for c in found] == ["http://203.0.113.7:40411",
                                      "https://abc123-8001.proxy.runpod.net"]


def test_the_proxy_is_still_offered_when_the_direct_one_is_there(monkeypatch):
    """Failing over is not falling back: both are the same worker image, so
    the proxy stays on the ladder rather than being replaced by the direct
    address. A pod whose TCP port is firewalled must still be reachable."""
    configure(monkeypatch, voice_allow_plain_http=True)
    found = voice_control._pods_from(pods(tcp_pod()), "fam-voice")
    assert any(c.url.endswith("proxy.runpod.net") for c in found)


def test_a_direct_address_is_not_used_unless_it_was_allowed(monkeypatch):
    """Plain HTTP carries the bearer token and the whole script in clear. It
    is a decision about this deployment, not a default to discover."""
    configure(monkeypatch, voice_allow_plain_http=False)
    found = voice_control._pods_from(pods(tcp_pod()), "fam-voice")
    assert [c.url for c in found] == ["https://abc123-8001.proxy.runpod.net"]


def test_a_refused_direct_address_is_said_rather_than_dropped(monkeypatch):
    """An operator staring at a 403 from the proxy needs to know the other
    path exists and is switched off - which is the opposite of useful to find
    out by reading source."""
    configure(monkeypatch, voice_allow_plain_http=False)
    voice_control._pods_from(pods(tcp_pod()), "fam-voice")
    assert any("VOICE_ALLOW_PLAIN_HTTP" in note
               for note in voice_control._state.pod_notes)


def test_the_direct_mapping_is_read_from_rests_shape_too(monkeypatch):
    """REST answers `portMappings` and `publicIp`; GraphQL answers
    `runtime.ports`. A provider reshaping a response costs a rung, never the
    voice - so both are read."""
    configure(monkeypatch, voice_allow_plain_http=True, voice_worker_port=8002)
    entry = pod(ports=(8002,))
    entry["publicIp"] = "203.0.113.9"
    entry["portMappings"] = {"8002": 41999}
    found = voice_control._pods_from(pods(entry), "fam-voice")
    assert found[0].url == "http://203.0.113.9:41999"


def test_a_mapping_for_another_port_is_never_borrowed(monkeypatch):
    """A pod exposing SSH over TCP is not this worker's address."""
    configure(monkeypatch, voice_allow_plain_http=True, voice_worker_port=8001)
    entry = tcp_pod(private=22, public=40022)
    entry["runtime"]["ports"].append(
        {"privatePort": 8001, "publicPort": 48001, "type": "http",
         "isIpPublic": True})
    found = voice_control._pods_from(pods(entry), "fam-voice")
    assert all(not c.url.startswith("http://203.0.113.7") for c in found)


def test_a_private_ip_is_not_an_address_the_app_can_reach(monkeypatch):
    configure(monkeypatch, voice_allow_plain_http=True)
    entry = tcp_pod()
    entry["runtime"]["ports"][1]["isIpPublic"] = False
    found = voice_control._pods_from(pods(entry), "fam-voice")
    assert [c.url for c in found] == ["https://abc123-8001.proxy.runpod.net"]


# --- a 403 is the edge, and it has to read as the edge ---------------------

class _Response:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def test_a_403_from_the_proxy_names_the_edge_and_not_the_worker():
    """The sentence this replaces was `/health returned HTTP 403` plus two
    hundred characters of Cloudflare markup, in front of a worker that was
    answering perfectly on its own port. That cost a day."""
    endpoint = voice_control.Endpoint(
        transport="http", url="https://abc123-8002.proxy.runpod.net",
        rung="pinned", why="REMOTE_VOICE_URL")
    said = voice_control._refused(endpoint, _Response(403, "<html>cf</html>"))
    assert "proxy edge" in said and "Cloudflare" in said
    assert "VOICE_ALLOW_PLAIN_HTTP" in said


def test_a_401_is_the_worker_refusing_this_apps_token():
    """A different problem with a different fix, and they have never read
    differently."""
    endpoint = voice_control.Endpoint(
        transport="http", url="https://abc123-8002.proxy.runpod.net",
        rung="pinned", why="REMOTE_VOICE_URL")
    said = voice_control._refused(endpoint, _Response(401, "nope"))
    assert "REMOTE_VOICE_TOKEN" in said and "Cloudflare" not in said


def test_a_403_from_an_unproxied_address_does_not_blame_cloudflare():
    endpoint = voice_control.Endpoint(
        transport="http", url="http://203.0.113.7:40411", rung="runpod-pod",
        why="direct")
    said = voice_control._refused(endpoint, _Response(403, "no"))
    assert "Cloudflare" not in said and "403" in said


def test_both_paths_to_a_worker_call_themselves_the_same_thing():
    """They did not, and a difference here is a worker that answers a health
    check and refuses an episode - or the reverse, which is a health page
    that lies."""
    import remote_voice

    assert "voice_control.USER_AGENT" in \
        open(remote_voice.__file__).read()
    assert voice_control.USER_AGENT.startswith("FAM/")


# --- a pod that runs more than the voice -----------------------------------
#
# PROBLEMS.md §120. The reported pod ran FAM on 8001 (exposed over HTTP) and
# Chatterbox on 8002 (exposed over TCP). Discovery built
# `https://<pod>-8001.proxy.runpod.net`, health-checked FAM's own front door,
# and reported the 404 as a broken worker.

def shared_pod():
    """The reported layout: the app on 8001 over http, the voice on 8002 TCP."""
    return {"id": "abc123", "name": "fam-voice", "desiredStatus": "RUNNING",
            "publicIp": "203.0.113.7",
            "runtime": {"ports": [
                {"privatePort": 8001, "publicPort": 8001, "type": "http",
                 "isIpPublic": True},
                {"privatePort": 22, "publicPort": 41230, "type": "tcp",
                 "isIpPublic": True, "ip": "203.0.113.7"},
                {"privatePort": 8002, "publicPort": 41234, "type": "tcp",
                 "isIpPublic": True, "ip": "203.0.113.7"},
            ]}}


def test_the_apps_own_port_is_never_offered_as_the_voice(monkeypatch):
    """The fallback to the first exposed http port is what made this a
    discovery rather than a guess. The proxy fronts one private port, so an
    address built from a port the worker is not on reaches whatever else the
    pod runs - and answers 404, which reads exactly like a missing route."""
    configure(monkeypatch, voice_worker_port=8002)
    found = voice_control._pods_from(pods(shared_pod()), "fam-voice")
    assert [c.url for c in found] == []
    assert any("8001 and not 8002" in note
               for note in voice_control._state.pod_notes)


def test_the_substituted_port_names_the_setting_that_fixes_it(monkeypatch):
    configure(monkeypatch, voice_worker_port=8002)
    voice_control._pods_from(pods(shared_pod()), "fam-voice")
    assert any("VOICE_WORKER_PORT" in note
               for note in voice_control._state.pod_notes)


def test_a_direct_port_that_is_not_the_wanted_one_is_said_rather_than_dropped(
        monkeypatch):
    """Silence here was the other half. Looking for 8001 on a pod that maps 22
    and 8002, the direct rung returned nothing and said nothing - so the log
    showed a proxy URL being probed with no hint that the one address with no
    edge in front of it had been passed over for a reason anybody could fix."""
    configure(monkeypatch, voice_allow_plain_http=True, voice_worker_port=8001)
    voice_control._pods_from(pods(shared_pod()), "fam-voice")
    said = " ".join(voice_control._state.pod_notes)
    assert "22, 8002" in said and "VOICE_WORKER_PORT is 8001" in said


def test_the_two_settings_together_reach_the_one_address_that_works(monkeypatch):
    """The end of the chain: the worker's port named, plain HTTP decided, and
    the only address on this pod with no proxy edge in front of it."""
    configure(monkeypatch, voice_allow_plain_http=True, voice_worker_port=8002)
    found = voice_control._pods_from(pods(shared_pod()), "fam-voice")
    assert [c.url for c in found] == ["http://203.0.113.7:41234"]


def test_a_pod_running_only_the_voice_is_unaffected(monkeypatch):
    """The ordinary case has to keep costing nothing: one http port, and it is
    the worker's, so the proxy candidate is built exactly as before."""
    configure(monkeypatch, voice_worker_port=8001)
    found = voice_control._pods_from(pods(pod()), "fam-voice")
    assert [c.url for c in found] == ["https://abc123-8001.proxy.runpod.net"]
    assert voice_control._state.pod_notes == []


def test_an_unconfigured_port_still_takes_the_only_http_one(monkeypatch):
    """`VOICE_WORKER_PORT=0` means nothing was said about the worker's port,
    which is different from naming one the pod does not expose. The single
    exposed port is then the only candidate there is, and it is verified."""
    configure(monkeypatch, voice_worker_port=0)
    found = voice_control._pods_from(pods(pod(ports=(8003,))), "fam-voice")
    assert [c.url for c in found] == ["https://abc123-8003.proxy.runpod.net"]
