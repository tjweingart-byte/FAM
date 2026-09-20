"""Which entrypoint this container is, and which ports it must answer on.

One image serves two platforms (`Dockerfile.voice`), and until this file
existed the choice between them was a string comparison in `sh`:

    if [ "$VOICE_WORKER_MODE" = http ]; then uvicorn ...; else handler.py; fi

That is three failures wearing one hat, and production wore all three
(PROBLEMS.md §78, §112). `http ` with a trailing space, `HTTP`, `"http"` with
the quotes an operator pasted in from a dashboard, or the variable simply not
set - each of them runs the *serverless* handler, which opens **no port at
all**, and a pod that opens no port answers 404 on every path including
`/health`. From the app's side that is indistinguishable from a worker with a
missing route, which is the one voice failure that says nothing about the
voice.

So the two questions a container has to answer about itself are answered here,
in Python, as pure functions over an environment:

* **which mode** - and when nothing says, the answer is decided by what the
  platform itself set, not by a default that can only be wrong one way. A pod
  guessed as `serverless` is a dead endpoint; a serverless worker guessed as
  `http` is a port nobody dials. Neither guess is needed: RunPod names a
  serverless worker in its environment.
* **which ports** - plural, deliberately. The container served `${PORT:-8001}`
  while the proxy URL in production named **8002**, and a proxied port with
  nothing behind it returns RunPod's own `404 page not found`. Listening on a
  second port costs one socket in a process that is already resident on a GPU,
  and it removes an entire class of 404 that took two sessions to read.

Nothing here has side effects and nothing here imports the engine, so the
tests can ask "what would this environment do" without a card, a network or a
container.
"""
from __future__ import annotations

import re


MODE_HTTP = "http"
MODE_SERVERLESS = "serverless"
MODES = (MODE_HTTP, MODE_SERVERLESS)

#: What an operator might reasonably have written meaning "open a port". Not
#: generosity for its own sake: every one of these used to select the
#: serverless handler and open nothing, which is the failure this file exists
#: to make impossible.
_HTTP_WORDS = frozenset({
    "http", "https", "server", "pod", "uvicorn", "port", "always-on", "alwayson",
})

_SERVERLESS_WORDS = frozenset({
    "serverless", "queue", "handler", "endpoint", "worker",
})

#: Variables RunPod sets on a serverless worker and not on a pod.
#: `RUNPOD_WEBHOOK_GET_JOB` is the job queue the SDK polls - a worker without
#: it has no queue to serve - and `RUNPOD_REALTIME_PORT` means the SDK is
#: serving its own HTTP, which is not this contract.
SERVERLESS_MARKERS = (
    "RUNPOD_WEBHOOK_GET_JOB",
    "RUNPOD_ENDPOINT_ID",
    "RUNPOD_REALTIME_PORT",
)

#: Every port this deployment has ever been proxied on. `.env.example`
#: documents 8001, production ran 8002, and which of the two is in the URL is
#: not visible from inside the container - so it answers on both.
DEFAULT_PORTS = (8001, 8002)


def _word(raw: object) -> str:
    """The value as an operator meant it, not as a dashboard stored it."""
    return str(raw or "").strip().strip("'\"").strip().lower()


def looks_serverless(env) -> str:
    """The marker proving this is a serverless worker, or "" if none is set."""
    for name in SERVERLESS_MARKERS:
        if _word(env.get(name)):
            return name
    return ""


def resolve_mode(env) -> tuple[str, str]:
    """The entrypoint this container should run, and why - in one sentence.

    The `why` is not decoration. A mode chosen silently is the thing that took
    two sessions to see, so whatever decided it is logged at boot in the one
    place all of it is visible: the pod's own log.
    """
    raw = env.get("VOICE_WORKER_MODE")
    word = _word(raw)
    marker = looks_serverless(env)

    if word in _SERVERLESS_WORDS:
        return MODE_SERVERLESS, f"VOICE_WORKER_MODE={raw!r}"
    if word in _HTTP_WORDS:
        return MODE_HTTP, f"VOICE_WORKER_MODE={raw!r}"

    if word in ("", "auto"):
        if marker:
            return MODE_SERVERLESS, f"{marker} is set, so this is a serverless worker"
        return MODE_HTTP, ("nothing here names a serverless queue, so this is a "
                           "pod and a port is opened")

    # Unrecognised, which used to mean "serverless" and therefore "no port".
    # It now means whatever the platform says, and the reason says so out loud.
    if marker:
        return MODE_SERVERLESS, (f"VOICE_WORKER_MODE={raw!r} is not a mode "
                                 f"({', '.join(MODES)}); {marker} is set, so "
                                 "the serverless handler is what this is")
    return MODE_HTTP, (f"VOICE_WORKER_MODE={raw!r} is not a mode "
                       f"({', '.join(MODES)}); nothing here looks serverless, so "
                       "a port is opened rather than none")


def resolve_ports(env) -> list[int]:
    """Every port the HTTP worker should answer on, in order of authority.

    `PORT` first, because that is what the deployment set and what every
    earlier version of this image served. Then `VOICE_WORKER_PORTS`, or
    `DEFAULT_PORTS` when it is unset - so an image deployed with no port
    configuration at all still answers the two ports this product has actually
    been proxied on.
    """
    ports: list[int] = []

    def add(value: object) -> None:
        try:
            port = int(_word(value))
        except (TypeError, ValueError):
            return
        if 1 <= port <= 65535 and port not in ports:
            ports.append(port)

    add(env.get("PORT"))
    declared = env.get("VOICE_WORKER_PORTS")
    for part in re.split(r"[,;\s]+", str(declared or "")):
        add(part)
    if declared is None:
        for port in DEFAULT_PORTS:
            add(port)
    if not ports:
        for port in DEFAULT_PORTS:
            add(port)
    return ports
