"""The address of the voice is discovered, checked and held here - not typed.

`remote_voice.py` owns the conversation with a worker: sentences in, PCM out.
This owns the question that comes before it - *which* worker, and is it
actually able to speak right now. They were one thing, and being one thing is
what turned every change on RunPod into a day of work (PROBLEMS.md §112):

    pod migrates -> its address changes -> Render still points at the old one
    -> 404 mid-episode -> a human reads logs, edits a dashboard, redeploys

Every arrow in that chain is a human keeping two systems in agreement by hand.
This removes the humans from all but the first.

## The ladder, which is the one definition of the order

`ladder()` lists the ways FAM can find a voice worker, in the order they are
tried, and **only the ones that can actually serve** - a discovery source with
no credentials is not a rung. Same shape and same reasoning as
`research.ladder()`: the runtime, `/api/health`, the startup warning and
`tools/voice_doctor.py` all read this rather than keeping a second copy, so
there is no way for the documented order and the real one to disagree.

    1. pinned          REMOTE_VOICE_URL, when somebody set one. Configuration
                       still wins: an operator pointing at a specific machine
                       is making a decision, not offering a hint.
    2. registered      a worker that told us where it is, inside the TTL.
                       This is the rung that survives a pod being replaced.
    3. runpod pod      pods on the account, matched by name or id, resolved to
                       their proxy URL through RunPod's own API. Works for a
                       pod that predates the heartbeat or cannot reach us.
    4. serverless      RUNPOD_ENDPOINT_ID. Always reachable, cold when idle.

## Failing over is not falling back

CLAUDE.md is categorical that a hosted voice which fails must fail rather than
become a different one, and that stands. Nothing here substitutes a voice: every
rung is the same `Dockerfile.voice` image, running the same `ChatterboxEngine`,
cloning the same reference recording, and a candidate whose `/health` reports a
sample rate this app has not already written into the stream header is
**refused**, not used. What moves is the address. What never moves is what comes
out of it - and when nothing on the ladder can speak, the episode fails with the
reason attached, exactly as it did before.

Every switch is recorded and reported. The rule §109 settled for retrieval is
the rule here: never fall back silently.

## Verify, do not inspect

A rung is not used because it is configured. It is used because a real call to
it came back correct, recently (`VOICE_VERIFY_TTL`), and that answer is held so
the synth path pays nothing. Verification is deliberately the *cheap* real
call - a worker's `/health`, or RunPod's own endpoint health - because the
expensive one wakes a serverless worker and bills for it, and a health check
that costs money is a health check somebody turns off.

`supervise_forever()` runs that check on a timer, so a pod that died at 3am is
known at 3am and the switch has already happened by the time somebody asks for
an episode. That is this file's version of the standing instruction on latency:
the work is done before the listener is waiting.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from config import settings

log = logging.getLogger(__name__)

#: What the app and the worker have agreed the JSON contract looks like. Bumped
#: only when the wire shape changes in a way an older worker cannot serve.
#: Reported by the worker, read here, and **never used to refuse a worker that
#: is otherwise speaking**: a version check that can take the voice away is a
#: layer subtracting availability, which CLAUDE.md settles against. It is
#: reported so a mismatch is visible before it is audible.
CONTRACT_VERSION = 1

#: The rungs, in order. Names are stable: they appear in `/api/health`, in the
#: doctor's output and in the log line that announces a switch.
RUNGS = ("pinned", "registered", "runpod-pod", "serverless")

#: What every request from this app to a worker calls itself, on the probe path
#: and on the synth path alike.
#:
#: One constant because the two paths disagreed and the disagreement was
#: invisible: `remote_voice` sent `FAM/remote-voice` and this module sent
#: whatever httpx defaults to, so a worker reachable by one could be refused to
#: the other by anything in between that reads a User-Agent - and RunPod's pod
#: proxy is Cloudflare, which does (PROBLEMS.md §117). A probe that succeeds
#: where the synth fails, or the reverse, is the most expensive shape of bug
#: this seam has, because it makes the health page lie.
USER_AGENT = "FAM/voice (+https://github.com/tjweingart-byte/FAM)"

#: Hosts whose 403 means something specific enough to say out loud. RunPod
#: fronts a pod's HTTP port with Cloudflare, so a request that a browser makes
#: happily can be refused at the edge when it comes from a datacentre - which
#: is a fact about the *path*, not about the worker, and reads from the app's
#: side exactly like a worker that has stopped working.
PROXIED_HOSTS = ("proxy.runpod.net",)


class VoiceControlError(RuntimeError):
    """No worker on the ladder can speak, and this says what was tried."""


@dataclass(frozen=True)
class Endpoint:
    """One place that might be able to speak, and why it is a candidate.

    `why` is in words, for the same reason a prefetch candidate carries one
    (§83): the only way to judge a discovery order is to see which kind of
    guess is answering, and "registered 12s ago by pod a1b2c3" is the sentence
    that ends an investigation that would otherwise start with a dashboard.
    """

    transport: str          # "http" | "runpod"
    url: str                # origin for http; the endpoint base for runpod
    rung: str
    why: str
    token: str = ""
    sample_rate: int = 0    # what it claims, 0 when it has not been asked

    def key(self) -> str:
        return f"{self.transport}:{self.url}"

    def as_dict(self) -> dict:
        # The token is never reported. The address is not a secret; the bearer
        # that opens it is.
        return {"transport": self.transport, "url": self.url, "rung": self.rung,
                "why": self.why, "sample_rate": self.sample_rate or None}


@dataclass(frozen=True)
class Verdict:
    """What a real call to a candidate proved, or failed to."""

    ok: bool
    detail: str
    contract: int = 0
    sample_rate: int = 0
    image: str = ""
    commit: str = ""
    latency: float = 0.0

    def as_dict(self) -> dict:
        out: dict = {"ok": self.ok, "detail": self.detail}
        for name, value in (("contract", self.contract),
                            ("sample_rate", self.sample_rate),
                            ("image", self.image), ("commit", self.commit)):
            if value:
                out[name] = value
        if self.latency:
            out["latency_seconds"] = round(self.latency, 3)
        return out


@dataclass
class _State:
    """What is in force, how it was established, and what has gone wrong.

    Module-level rather than per-engine-instance because `build_engine()`
    constructs a new engine per call - the same reason `remote_voice` keeps its
    pool on the class.
    """

    held: Optional[Endpoint] = None
    verdict: Optional[Verdict] = None
    verified_at: float = 0.0
    resolving: Optional[asyncio.Lock] = None
    # url -> (when it failed, why). A demoted endpoint is skipped for
    # `VOICE_RETRY_SECONDS` unless it is the only rung left, so a single bad
    # chunk does not pin the app to a worse worker for the rest of the day.
    failures: dict = field(default_factory=dict)
    # What was held before the current answer, kept past a demotion so that a
    # switch can still say what it moved away from. Without it, a failure
    # followed by a resolution records a move from nowhere, which is the one
    # thing a switch log exists to answer.
    previous: Optional[Endpoint] = None
    switches: list = field(default_factory=list)
    # What was true about the pods last time RunPod was asked, when it was not
    # a candidate. Usually the whole diagnosis - and sharper since the nightly
    # schedule was removed (§118): nothing stops this pod on purpose any more,
    # so "EXITED" is now unambiguously a fault rather than a clock.
    pod_notes: list = field(default_factory=list)
    last_error: str = ""


_state = _State()

#: How many switches are kept. Enough to show a flapping endpoint on a health
#: page, few enough that nothing has to be expired.
MAX_SWITCHES = 8


# -- the ladder ------------------------------------------------------------

@dataclass(frozen=True)
class Rung:
    """A way of finding a worker, and whether this deployment has it."""

    name: str
    configured: bool
    detail: str

    def as_dict(self) -> dict:
        return {"rung": self.name, "configured": self.configured,
                "detail": self.detail}


def ladder() -> list[Rung]:
    """Every rung, in order, with what it is set to. No network, no I/O.

    Lists rungs that are *configured*, and says so per rung rather than
    returning only the usable ones, because "there is no ladder" and "every
    rung is switched off" are different sentences and a health page has to be
    able to tell them apart.
    """
    pinned = (settings.remote_voice_url or "").strip()
    endpoint = (settings.runpod_endpoint_id or "").strip()
    pod = _pod_selector()
    key = _runpod_key()
    return [
        Rung("pinned", bool(pinned),
             f"REMOTE_VOICE_URL={pinned}" if pinned else "REMOTE_VOICE_URL not set"),
        Rung("registered", registration_enabled() and discovery_enabled(),
             _registration_detail()),
        Rung("runpod-pod", bool(pod and key and discovery_enabled()),
             f"RUNPOD_POD={pod}" if pod else "RUNPOD_POD / RUNPOD_POD_ID not set"),
        Rung("serverless", bool(endpoint and key),
             f"RUNPOD_ENDPOINT_ID={endpoint}" if endpoint
             else "RUNPOD_ENDPOINT_ID not set"),
    ]


def registration_enabled() -> bool:
    """Whether a worker may tell this app where it is.

    Gated on the shared secret and on nothing else. Unset means refused, not
    open: an endpoint that takes "the voice is at this URL" from anybody is an
    endpoint that hands every script FAM writes to a machine of their
    choosing, and it would look exactly like the feature working.
    """
    return bool((settings.voice_registry_token or "").strip())


def _registration_detail() -> str:
    if not discovery_enabled():
        return "VOICE_DISCOVERY=off"
    if not registration_enabled():
        return ("VOICE_REGISTRY_TOKEN not set, so no worker may register "
                "itself")
    return f"workers that registered inside {settings.voice_registry_ttl:.0f}s"


def discovery_enabled() -> bool:
    """Whether anything but the pinned URL may be used.

    `VOICE_DISCOVERY=off` is the escape hatch back to exactly the behaviour
    this file replaced: one configured address, no registry, no RunPod lookup.
    It exists because a deployment debugging something needs to be able to
    stop the app being clever, and because a knob is only dangerous when it is
    the thing that decides what a listener hears - this one decides which of
    several identical workers answers.
    """
    return str(settings.voice_discovery or "auto").strip().lower() != "off"


def allow_plain_http() -> bool:
    """Whether a worker reached over plain HTTP may be used at all.

    Read in two places - here, when RunPod names a pod's TCP address, and in
    `voice_registry`, when a worker registers one. Both ask the same question
    because it is the same decision, and a deployment that answered it twice
    could have a registered plain address it would refuse to discover.
    """
    return bool(getattr(settings, "voice_allow_plain_http", False))


def _runpod_key() -> str:
    try:
        import credentials

        found = credentials.active("RUNPOD_API_KEY")
        if found:
            return found
    except Exception:  # pragma: no cover - the chain is optional
        pass
    return (settings.runpod_api_key or "").strip()


def _pod_selector() -> str:
    return (settings.runpod_pod or "").strip()


# -- resolving -------------------------------------------------------------

async def candidates() -> list[Endpoint]:
    """Every address worth trying, in ladder order.

    Deduplicated by (transport, url): a pod that both registers itself and is
    found through RunPod's API is one worker, and trying it twice would make a
    dead pod look like two failures.
    """
    found: list[Endpoint] = []
    seen: set[str] = set()

    def add(endpoint: Optional[Endpoint]) -> None:
        if endpoint and endpoint.url and endpoint.key() not in seen:
            seen.add(endpoint.key())
            found.append(endpoint)

    add(_pinned())
    if discovery_enabled():
        for registered in _registered():
            add(registered)
        for pod in await _runpod_pods():
            add(pod)
    add(_serverless())
    return found


def _pinned() -> Optional[Endpoint]:
    url = (settings.remote_voice_url or "").strip().rstrip("/")
    if not url:
        return None
    if url.endswith("/synth"):
        url = url[: -len("/synth")]
    return Endpoint(transport="http", url=url, rung="pinned",
                    why="REMOTE_VOICE_URL, set by hand",
                    token=_http_token())


def _http_token() -> str:
    try:
        import credentials

        found = credentials.active("REMOTE_VOICE_TOKEN")
        if found:
            return found
    except Exception:  # pragma: no cover - the chain is optional
        pass
    return (settings.remote_voice_token or "").strip()


def _registered() -> list[Endpoint]:
    """Workers that said where they are, inside the TTL. Never raises.

    A registry that cannot be read must cost a rung, never the episode - which
    is why this is a list comprehension inside a try rather than a dependency
    of the request path.

    Not even opened when registration is switched off. A store that is created
    on every machine that imports the app, to hold rows nothing can write, is
    a file somebody has to explain.
    """
    if not registration_enabled():
        return []
    try:
        import voice_registry

        live = voice_registry.registry().live(ttl=settings.voice_registry_ttl)
    except Exception as exc:  # pragma: no cover - a store that will not open
        log.warning("voice registry unreadable (%s); skipping that rung",
                    type(exc).__name__)
        return []
    out = []
    for row in live:
        if not row.ready:
            continue
        out.append(Endpoint(
            transport="http", url=row.url, rung="registered",
            why=(f"registered {row.age:.0f}s ago"
                 + (f" by {row.commit[:7]}" if row.commit else "")),
            token=_http_token(), sample_rate=row.sample_rate))
    return out


def _serverless() -> Optional[Endpoint]:
    endpoint = (settings.runpod_endpoint_id or "").strip()
    key = _runpod_key()
    if not endpoint or not key:
        return None
    base = (settings.runpod_base_url or "").rstrip("/")
    return Endpoint(transport="runpod", url=f"{base}/{endpoint}",
                    rung="serverless",
                    why=f"RUNPOD_ENDPOINT_ID={endpoint}", token=key)


async def _runpod_pods() -> list[Endpoint]:
    """Ask RunPod where the pod is now. Never raises; a failure costs a rung.

    This is the rung that finds a *replaced* pod without the pod having to
    reach us - and the one whose shape belongs to somebody else, so every step
    of reading the answer is defensive. A provider that changes its schema must
    cost a rung and a log line, not the voice.

    REST first and GraphQL second, which is a fact about RunPod rather than a
    FAM policy: `rest.runpod.io/v1` is the current API and the one this
    project's key has been used against. The older GraphQL endpoint answers
    the same question and is tried when REST does not.
    """
    selector = _pod_selector()
    key = _runpod_key()
    if not selector or not key:
        return []
    _state.pod_notes = []
    for describe in (_pods_over_rest, _pods_over_graphql):
        body = await describe(key)
        if body is None:
            continue
        found = _pods_from(body, selector)
        if found:
            return found
    return []


async def _pods_over_rest(key: str):
    """`GET /v1/pods`, the API the schedule workflow already uses."""
    return await _ask_runpod("GET", f"{settings.runpod_rest_url.rstrip('/')}/pods",
                             key)


async def _pods_over_graphql(key: str):
    return await _ask_runpod("POST", settings.runpod_graphql_url, key,
                             json={"query": _POD_QUERY})


async def _ask_runpod(method: str, url: str, key: str, json: dict | None = None):
    """One call to RunPod, or `None` with a line in the log saying why not."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=settings.voice_probe_timeout) as client:
            response = await client.request(
                method, url, json=json,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"})
        if response.status_code >= 400:
            log.warning("runpod pod lookup: %s %s returned HTTP %s: %s",
                        method, url, response.status_code, response.text[:200])
            return None
        return response.json()
    except Exception as exc:
        log.warning("runpod pod lookup: %s %s failed (%s: %s)", method, url,
                    type(exc).__name__, exc)
        return None


_POD_QUERY = """
query Pods {
  myself {
    pods {
      id
      name
      desiredStatus
      runtime { ports { privatePort publicPort type isIpPublic } }
    }
  }
}
"""


def _pod_list(body: Any) -> list:
    """The pods out of whichever shape RunPod answered in.

    Three are accepted - a bare list (REST), `{"pods": [...]}` and GraphQL's
    `data.myself.pods` - because the alternative is a rung that disappears the
    day a vendor reshapes a response, with nothing in the log but silence.
    """
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        return []
    if isinstance(body.get("pods"), list):
        return body["pods"]
    if isinstance(body.get("data"), dict):
        myself = body["data"].get("myself") or {}
        if isinstance(myself, dict) and isinstance(myself.get("pods"), list):
            return myself["pods"]
    return []


def _pods_from(body: Any, selector: str) -> list[Endpoint]:
    """Turn RunPod's answer into candidates. Pure, so it can be tested offline.

    Matching is on **name or id**, because a pod that is destroyed and recreated
    keeps the name its template gave it and loses the id - and the name is
    therefore the thing an operator can rely on across a migration.

    A pod that is found and *not running* is recorded rather than dropped
    silently - and that note says more than it used to. It was written when a
    schedule stopped this pod every night, so "EXITED" was ambiguous between a
    clock and a fault. Nothing stops it on purpose any more (§118), so a pod
    that is found and not running is **always** something to act on: RunPod
    evicted it, the account ran out, or somebody stopped it by hand. An empty
    rung that said nothing would hide all three.
    """
    wanted = {part.strip().lower() for part in selector.split(",") if part.strip()}
    out: list[Endpoint] = []
    for pod in _pod_list(body):
        if not isinstance(pod, dict):
            continue
        pod_id = str(pod.get("id") or "")
        name = str(pod.get("name") or "")
        if pod_id.lower() not in wanted and name.lower() not in wanted:
            continue
        status = str(pod.get("desiredStatus") or pod.get("status") or "").upper()
        if status and status != "RUNNING":
            _note(f"pod {name or pod_id} is {status}, not RUNNING")
            continue
        # The direct address first, when the pod has one and this deployment
        # permits it. It is the same worker either way; what differs is
        # whether Cloudflare is in the path, and the proxy's edge is what
        # refuses a server while serving a browser (PROBLEMS.md §117).
        direct = _direct_endpoint(pod, name or pod_id)
        if direct is not None:
            out.append(direct)
        port = _http_port(pod)
        if not port:
            if direct is None:
                _note(f"pod {name or pod_id} exposes no http port; the image "
                      "serves ${PORT:-8001}")
            continue
        out.append(Endpoint(
            transport="http",
            url=f"https://{pod_id}-{port}.proxy.runpod.net",
            rung="runpod-pod",
            why=f"pod {name or pod_id} is running with http port {port}",
            token=_http_token()))
    if not out and not _state.pod_notes:
        _note(f"RunPod lists no pod called {selector}")
    return out


def _direct_endpoint(pod: dict, label: str) -> Optional[Endpoint]:
    """The pod's own IP and mapped port, when RunPod has published one.

    This is the rung with no edge in it. RunPod maps an exposed TCP port to a
    public IP and a port it chooses, and reports the pair - so the address is
    discoverable exactly as the proxy URL is, and it changes on every pod for
    exactly the same reason, which is why it is *found* rather than written
    into an environment (§112's whole argument, applied to the address that
    actually works from a server).

    Three shapes are read, for the reason `_pod_list` reads three: REST's
    `portMappings` and `publicIp`, REST's `runtime.ports`, and GraphQL's
    `runtime.ports`. A provider reshaping a response must cost a rung and a
    log line, never the voice.

    Returns `None` rather than an address when this deployment has not allowed
    plain HTTP - said once, in the notes, because an operator looking at a
    403 from the proxy needs to know the other path exists and is switched
    off, which is the opposite of useful to discover by reading source.
    """
    wanted = int(settings.voice_worker_port or 0)
    ip, port = _tcp_mapping(pod, wanted)
    if not ip or not port:
        return None
    if not allow_plain_http():
        _note(f"pod {label} publishes {ip}:{port}, which needs no proxy, but "
              "VOICE_ALLOW_PLAIN_HTTP is not set so it is not offered")
        return None
    return Endpoint(
        transport="http", url=f"http://{ip}:{port}", rung="runpod-pod",
        why=f"pod {label} publishes port {wanted} directly at {ip}:{port}",
        token=_http_token())


def _tcp_mapping(pod: dict, wanted: int) -> tuple[str, int]:
    """`(public ip, public port)` for the worker's port, or `("", 0)`."""
    ip = str(pod.get("publicIp") or pod.get("public_ip") or "").strip()
    mappings = pod.get("portMappings") or pod.get("port_mappings")
    if ip and isinstance(mappings, dict):
        for private, public in mappings.items():
            try:
                if int(private) == wanted and int(public) > 0:
                    return ip, int(public)
            except (TypeError, ValueError):
                continue
    runtime = pod.get("runtime")
    if not isinstance(runtime, dict):
        return "", 0
    for entry in runtime.get("ports") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("type") or "").lower() != "tcp":
            continue
        if entry.get("isIpPublic") is False:
            continue
        try:
            private = int(entry.get("privatePort") or 0)
            public = int(entry.get("publicPort") or 0)
        except (TypeError, ValueError):
            continue
        host = str(entry.get("ip") or ip or "").strip()
        if private == wanted and public and host:
            return host, public
    return "", 0


def _note(line: str) -> None:
    """Something true about the pods that is not a candidate.

    Kept because it is usually the diagnosis: an empty rung says nothing, and
    "EXITED" says everything.
    """
    if line in _state.pod_notes:
        # REST and GraphQL are asked the same question, so both answer it the
        # same way. One fact said twice reads as two findings.
        return
    log.info("runpod: %s", line)
    _state.pod_notes.append(line)
    del _state.pod_notes[:-MAX_SWITCHES]


def _http_port(pod: dict) -> int:
    """The private port RunPod's proxy fronts, preferring the configured one.

    The proxy URL is built from the port *inside* the container, which is the
    detail §78 was paid for: a pod exposing 8002 with uvicorn on 8001 answers
    404 from the proxy, and the app cannot tell that from a missing route.

    Two different nothings, and they get different answers. A pod that reports
    **no runtime at all** is one RunPod has not finished starting, so the
    configured port is used and the candidate is verified like any other - the
    cost of being wrong is one probe. A pod whose runtime lists ports and none
    of them is http genuinely has nothing to talk to, and is skipped: that is
    a fact, and inventing a port for it would be a guess that looks like one.
    """
    runtime = pod.get("runtime")
    preferred = int(settings.voice_worker_port or 0)
    if not isinstance(runtime, dict):
        return preferred
    http_ports = []
    for entry in runtime.get("ports") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("type") or "http").lower() != "http":
            continue
        private = entry.get("privatePort") or entry.get("publicPort")
        try:
            http_ports.append(int(private))
        except (TypeError, ValueError):
            continue
    if preferred and preferred in http_ports:
        return preferred
    return http_ports[0] if http_ports else 0


# -- verifying -------------------------------------------------------------

async def verify(endpoint: Endpoint) -> Verdict:
    """A real, cheap call, and what it proved. Never raises.

    Cheap on purpose. The expensive check - make it speak - is what
    `verify_voice.py` and `tools/voice_doctor.py` do, and it wakes a serverless
    worker and bills for the boot. A supervisor that did that every minute
    would be a bill for keeping a health page green.
    """
    started = time.monotonic()
    try:
        import httpx

        timeout = httpx.Timeout(settings.voice_probe_timeout,
                                connect=min(5.0, settings.voice_probe_timeout))
        async with httpx.AsyncClient(
                timeout=timeout,
                headers={"User-Agent": USER_AGENT}) as client:
            if endpoint.transport == "runpod":
                verdict = await _verify_runpod(client, endpoint)
            else:
                verdict = await _verify_http(client, endpoint)
    except Exception as exc:
        return Verdict(False, f"{type(exc).__name__}: {exc}",
                       latency=time.monotonic() - started)
    if not verdict.ok:
        return verdict
    wanted = int(settings.remote_voice_sample_rate)
    if verdict.sample_rate and verdict.sample_rate != wanted:
        # Refused rather than used. The stream header is written from the
        # configured rate before any audio is asked for, so a worker at another
        # rate plays at the wrong pitch - a failure you hear instead of one you
        # are told about.
        return Verdict(False,
                       f"the worker emits {verdict.sample_rate} Hz but this app "
                       f"streams {wanted} Hz; set REMOTE_VOICE_SAMPLE_RATE="
                       f"{verdict.sample_rate} or point at a worker that matches",
                       contract=verdict.contract,
                       sample_rate=verdict.sample_rate,
                       latency=verdict.latency)
    if verdict.contract and verdict.contract != CONTRACT_VERSION:
        # Reported, never refused: an older worker that still serves /synth is
        # a working voice, and taking it away over a version number would be
        # this layer subtracting availability.
        log.warning("voice worker at %s speaks contract %s; this app is %s",
                    endpoint.url, verdict.contract, CONTRACT_VERSION)
    return Verdict(True, verdict.detail or "ready", contract=verdict.contract,
                   sample_rate=verdict.sample_rate, image=verdict.image,
                   commit=verdict.commit,
                   latency=verdict.latency or (time.monotonic() - started))


async def _verify_http(client, endpoint: Endpoint) -> Verdict:
    """The worker's own `/health`, which is the authority on what it can do."""
    started = time.monotonic()
    headers = {"Authorization": f"Bearer {endpoint.token}"} if endpoint.token else {}
    response = await client.get(f"{endpoint.url}/health", headers=headers)
    latency = time.monotonic() - started
    if response.status_code == 404:
        return Verdict(False,
                       f"nothing at {endpoint.url} answers /health. Two things "
                       "cause that, both on the pod: a proxy URL naming a port "
                       "the worker is not listening on (the image serves "
                       "${PORT:-8001}), and an image old enough to default to "
                       "the serverless handler, which opens no port at all - "
                       "set VOICE_WORKER_MODE=http or rebuild from "
                       "Dockerfile.voice, which now chooses for itself "
                       "(REMOTE_VOICE.md, PROBLEMS.md §78 and §112).",
                       latency=latency)
    if response.status_code in (401, 403):
        return Verdict(False, _refused(endpoint, response), latency=latency)
    if response.status_code >= 400:
        return Verdict(False, f"/health returned HTTP {response.status_code}: "
                              f"{response.text[:200]}", latency=latency)
    try:
        body = response.json()
    except Exception:
        return Verdict(False, "/health did not return JSON; whatever is at "
                              f"{endpoint.url} is not a FAM voice worker",
                       latency=latency)
    if not isinstance(body, dict):
        return Verdict(False, "/health returned "
                              f"{type(body).__name__}, not an object",
                       latency=latency)
    ready = bool(body.get("ready"))
    detail = str(body.get("detail") or "")
    return Verdict(ready, detail or ("ready" if ready else "the worker says it "
                                     "cannot speak and gave no reason"),
                   contract=_int(body.get("contract")),
                   sample_rate=_int(body.get("sample_rate")),
                   image=str(body.get("image") or ""),
                   commit=str(body.get("commit") or ""),
                   latency=latency)


def _proxied(url: str) -> bool:
    """Whether this address goes through somebody else's edge to reach a worker."""
    host = urlparse(url).hostname or ""
    return any(host.endswith(suffix) for suffix in PROXIED_HOSTS)


def _refused(endpoint: Endpoint, response) -> str:
    """A 401 or a 403 from a worker's address, and which of two things it is.

    They are not the same problem and they have never read differently, which
    is most of what PROBLEMS.md §117 cost. A **401** is the worker: it has a
    `REMOTE_VOICE_TOKEN` and this app sent the wrong one or none. A **403** on
    a proxied address is almost never the worker at all - RunPod fronts a pod's
    HTTP port with Cloudflare, and Cloudflare refuses server-to-server requests
    from datacentre ranges that it serves to a browser without complaint. So
    the same URL is healthy in a tab and forbidden from Render, and every
    instinct says the voice broke.

    Said in full because the alternative is what happened: `/health returned
    HTTP 403` plus two hundred characters of Cloudflare markup, in front of a
    worker that was answering perfectly on its own port.
    """
    code = response.status_code
    body = (response.text or "").strip().replace("\n", " ")[:160]
    if code == 401:
        return (f"the worker at {endpoint.url} refused this app's token "
                "(HTTP 401). REMOTE_VOICE_TOKEN here and on the pod must be "
                f"the same string. It answered: {body or 'nothing'}")
    if not _proxied(endpoint.url):
        return (f"{endpoint.url} answered HTTP 403. Something between this app "
                f"and the worker refused the request: {body or 'no body'}")
    return (f"HTTP 403 from {endpoint.url}. This is the proxy edge in front of "
            "the pod, not the worker: RunPod fronts a pod's HTTP port with "
            "Cloudflare, which serves a browser and refuses a server. The "
            "worker itself is very likely fine - the same URL in a tab will "
            "say so. Reach it on a path that has no edge in it: expose the "
            "worker's port as a TCP port on the pod, and either let the worker "
            "register that address itself (FAM_APP_URL + VOICE_REGISTRY_TOKEN "
            "on the pod, VOICE_REGISTRY_TOKEN here) or set RUNPOD_POD and "
            "RUNPOD_API_KEY so this app can ask RunPod for it. Both need "
            "VOICE_ALLOW_PLAIN_HTTP=1, because a raw TCP port has no "
            "certificate - see REMOTE_VOICE.md. The edge said: "
            f"{body or 'nothing'}")


async def _verify_runpod(client, endpoint: Endpoint) -> Verdict:
    """RunPod's own endpoint health: does this endpoint exist and have workers.

    It deliberately does not wake one. `ready` here means "RunPod will accept a
    job for this endpoint", which is the most that can be known for free about
    something whose whole design is to be asleep.
    """
    started = time.monotonic()
    response = await client.get(
        f"{endpoint.url}/health",
        headers={"Authorization": f"Bearer {endpoint.token}"})
    latency = time.monotonic() - started
    if response.status_code == 401:
        return Verdict(False, "RUNPOD_API_KEY was rejected by RunPod",
                       latency=latency)
    if response.status_code == 404:
        return Verdict(False, f"RunPod has no endpoint {endpoint.url.rsplit('/', 1)[-1]}"
                              " - check RUNPOD_ENDPOINT_ID", latency=latency)
    if response.status_code >= 400:
        return Verdict(False, f"RunPod answered HTTP {response.status_code}: "
                              f"{response.text[:200]}", latency=latency)
    try:
        body = response.json()
    except Exception:
        return Verdict(False, "RunPod's health endpoint did not return JSON",
                       latency=latency)
    workers = (body or {}).get("workers") or {}
    ready = _int(workers.get("ready"))
    idle = _int(workers.get("idle"))
    running = _int(workers.get("running"))
    initializing = _int(workers.get("initializing"))
    unhealthy = _int(workers.get("unhealthy"))
    if unhealthy and not (ready or idle or running or initializing):
        return Verdict(False, f"every worker on this endpoint is unhealthy "
                              f"({unhealthy}); check the endpoint's logs on "
                              "RunPod", latency=latency)
    return Verdict(True, f"endpoint accepting jobs ({ready or idle or running or 0} "
                         "workers up, the rest cold)", latency=latency)


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# -- what is in force ------------------------------------------------------

def held() -> Optional[Endpoint]:
    """What is in force right now, without checking anything. For reports."""
    return _state.held


def recently_failed(key: str) -> bool:
    """Whether a real call to this address failed inside the retry window.

    Read by `remote_voice` before it short-circuits to the configured address.
    Without it, a deployment whose `REMOTE_VOICE_URL` has died pays one failed
    request per chunk - fifteen an episode - because each chunk starts again
    from the setting rather than from what was learned two seconds ago.
    """
    failed = _state.failures.get(key)
    if not failed:
        return False
    return (time.time() - failed[0]) < float(settings.voice_retry_seconds)


def fresh() -> bool:
    """Whether what is held was verified recently enough to be used as-is.

    Read by `remote_voice` on the synth path, which is why it costs nothing:
    the whole design is that a healthy deployment never probes in front of a
    listener.
    """
    return _fresh()


def _fresh() -> bool:
    if not _state.held or not _state.verdict or not _state.verdict.ok:
        return False
    return (time.time() - _state.verified_at) < float(settings.voice_verify_ttl)


async def current(force: bool = False) -> Endpoint:
    """The endpoint to speak to, verified. Raises `VoiceControlError` if none is.

    Called on the synth path, so the fast path does nothing: a held endpoint
    that was verified inside `VOICE_VERIFY_TTL` is returned without a request.
    The supervisor keeps it fresh, which is why a healthy deployment never pays
    a probe in front of a listener.
    """
    if not force and _fresh():
        return _state.held  # type: ignore[return-value]

    lock = _state.resolving
    if lock is None:
        lock = _state.resolving = asyncio.Lock()
    async with lock:
        # Another request may have resolved while this one waited for the lock.
        if not force and _fresh():
            return _state.held  # type: ignore[return-value]
        return await _resolve()


async def _resolve() -> Endpoint:
    found = await candidates()
    if not found:
        rungs = ", ".join(r.name for r in ladder() if r.configured) or "none"
        _state.last_error = (
            "no voice worker could be found. Configured rungs: "
            f"{rungs}. Set REMOTE_VOICE_URL, or RUNPOD_ENDPOINT_ID with "
            "RUNPOD_API_KEY, or let a worker register itself "
            "(see REMOTE_VOICE.md).")
        raise VoiceControlError(_state.last_error)

    now = time.time()
    tried: list[str] = []
    # Recently-failed candidates go last rather than being dropped: if they are
    # all there is, a stale failure must not be the reason nobody can speak.
    def recency(endpoint: Endpoint) -> int:
        failed = _state.failures.get(endpoint.key())
        if not failed:
            return 0
        return 1 if (now - failed[0]) < float(settings.voice_retry_seconds) else 0

    for endpoint in sorted(found, key=recency):
        verdict = await verify(endpoint)
        if verdict.ok:
            _promote(endpoint, verdict)
            return endpoint
        tried.append(f"{endpoint.rung} ({endpoint.url}): {verdict.detail}")
        _state.failures[endpoint.key()] = (time.time(), verdict.detail)

    _state.held = None
    _state.verdict = None
    _state.last_error = ("no voice worker can speak. Tried, in order: "
                         + "; ".join(tried))
    raise VoiceControlError(_state.last_error)


def _promote(endpoint: Endpoint, verdict: Verdict) -> None:
    """Hold this endpoint, and say so if it is a change.

    Announced at INFO when it is the same address and WARNING when it is a
    different one: a voice that moved is the single most useful line in the log
    when somebody asks why an episode sounded fine at nine and failed at ten.
    """
    previous = _state.held or _state.previous
    _state.held = endpoint
    _state.previous = None
    _state.verdict = verdict
    _state.verified_at = time.time()
    _state.last_error = ""
    _state.failures.pop(endpoint.key(), None)
    if previous and previous.key() == endpoint.key():
        return
    _state.switches.append({
        "at": _state.verified_at,
        "from": previous.as_dict() if previous else None,
        "to": endpoint.as_dict(),
        "detail": verdict.detail,
    })
    del _state.switches[:-MAX_SWITCHES]
    if previous:
        log.warning("voice moved: %s (%s) -> %s (%s) - %s",
                    previous.url, previous.rung, endpoint.url, endpoint.rung,
                    verdict.detail)
    else:
        log.info("voice found at %s (%s): %s", endpoint.url, endpoint.rung,
                 verdict.detail)


def demote(endpoint: Endpoint | str, detail: str) -> None:
    """A real call to this endpoint failed; do not hold it any longer.

    Called from the synth path, which is the only place that learns things a
    health check cannot - a worker whose `/health` is green and whose `/synth`
    is 404 is exactly the failure this whole file exists for, and it is
    invisible until somebody asks for audio.
    """
    key = endpoint.key() if isinstance(endpoint, Endpoint) else f"http:{endpoint}"
    _state.failures[key] = (time.time(), detail)
    if _state.held and _state.held.key() == key:
        log.warning("voice at %s failed (%s); re-resolving on the next request",
                    _state.held.url, detail)
        _state.previous = _state.held
        _state.held = None
        _state.verdict = None
        _state.verified_at = 0.0
        _state.last_error = detail


def reset() -> None:
    """Forget everything held. For tests, and for a credential rotation."""
    global _state
    _state = _State()


# -- the supervisor --------------------------------------------------------

async def supervise_forever() -> None:
    """Keep the held endpoint fresh, so a listener never discovers a dead pod.

    One cheap probe every `VOICE_SUPERVISE_SECONDS`. It exists for the case the
    request path cannot cover: nobody has asked for an episode for an hour, the
    pod was replaced twenty minutes ago, and the first person to ask would
    otherwise pay the resolution *and* possibly a failure in front of their
    first word.
    """
    interval = max(15.0, float(settings.voice_supervise_seconds))
    while True:
        try:
            await current(force=True)
        except VoiceControlError as exc:
            log.warning("voice supervisor: %s", exc)
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            raise
        except Exception:  # pragma: no cover - must never end the loop
            log.exception("voice supervisor hit an unexpected error")
        await asyncio.sleep(interval)


# -- reporting -------------------------------------------------------------

def report() -> dict:
    """What `/api/health` says about how the voice is being found.

    Deliberately separate from `remote_voice.report()`, which says whether the
    voice *spoke*. These are the two questions §52 is about and they fail
    differently: a green ladder with a failed synth is a worker problem, and a
    red ladder is an infrastructure one.
    """
    return {
        "discovery": "auto" if discovery_enabled() else "off",
        "ladder": [rung.as_dict() for rung in ladder()],
        "contract": CONTRACT_VERSION,
        # Reported because it decides whether a pod's *direct* address is a
        # rung at all, and because a deployment with it off and a proxy that
        # is refusing looks identical from outside to one that simply cannot
        # find a worker (§117). Named for the state, not the variable.
        "plain_http": "allowed" if allow_plain_http() else "refused",
        "held": _state.held.as_dict() if _state.held else None,
        "verified": _state.verdict.as_dict() if _state.verdict else None,
        "verified_age_seconds": (round(time.time() - _state.verified_at, 1)
                                 if _state.verified_at else None),
        "switches": list(_state.switches),
        "pods": list(_state.pod_notes),
        "error": _state.last_error or None,
    }


def startup_warning() -> str:
    """One line for the boot log when the ladder cannot serve, or "".

    Read from `ladder()` rather than from a second list of settings, so a rung
    added here cannot be missing from the warning that says it is not set.
    """
    if settings.voice_backend != "remote":
        return ""
    usable = [rung for rung in ladder() if rung.configured]
    if usable:
        return ""
    return ("VOICE_BACKEND=remote, but no rung of the voice ladder is "
            "configured: " + "; ".join(f"{r.name}: {r.detail}" for r in ladder())
            + ". Nothing will be able to speak. See REMOTE_VOICE.md.")
