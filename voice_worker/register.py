"""The worker tells the app where it is, and keeps telling it.

The address of a rented GPU is not a constant. RunPod replaces a pod and it
comes back at a new proxy URL; the app is still holding the old one, and the
only symptom is a 404 in the middle of somebody's episode (PROBLEMS.md §78,
§112). Keeping the two in agreement by editing a dashboard is the chain this
exists to break.

So the pod introduces itself:

    POST https://<the app>/api/voice/register
    Authorization: Bearer $VOICE_REGISTRY_TOKEN
    {"url": "https://<pod id>-8001.proxy.runpod.net", "mode": "http",
     "port": 8001, "contract": 1, "sample_rate": 24000, "ready": true,
     "image": "...", "commit": "..."}

on boot and every `VOICE_REGISTER_INTERVAL` seconds after that, and once more
on the way down to say it is going. A replacement pod is in service within one
heartbeat, and nobody edits anything.

## Why the worker computes its own address

Only it can. RunPod puts the pod's id in `RUNPOD_POD_ID` inside the container
and fronts each exposed port at `https://<pod id>-<port>.proxy.runpod.net`, so
the address is derivable *there* and guessable nowhere else. `PUBLIC_WORKER_URL`
overrides it for anything that is not RunPod - a box in a colo, a tunnel, a
different provider - which is also what keeps this module from being RunPod's.

## Three things it must not do

**It must not be required.** A worker with no `FAM_APP_URL` registers nothing,
logs one line saying so, and serves exactly as it always did. Registration is
one rung of the app's ladder, not a dependency of speech.

**It must not be able to break the worker.** Every failure is a debug line and
a retry on the next beat. A voice worker that would not start because an
unrelated web service was down would be a worse outage than the one this
prevents.

**It must not claim to be ready before it is.** `ready` comes from the same
preflight the app's `/health` reports, so a container that came up on a node
with no GPU registers as present and unable to speak, and the app's control
plane passes over it rather than sending it an episode.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys

log = logging.getLogger("voice_worker.register")

#: How often to say "still here". The app's `VOICE_REGISTRY_TTL` is five
#: minutes by default, so this is five beats of headroom: one dropped POST is
#: not an outage.
DEFAULT_INTERVAL = 60.0


def public_url() -> str:
    """This worker's address as the internet sees it, or "" if unknowable.

    Three sources, in this order, and the middle one is the change §117 was
    paid for:

    1. **`PUBLIC_WORKER_URL`**, because a deployment that is not RunPod knows
       something this cannot derive.
    2. **RunPod's direct TCP mapping**, when the worker's port is exposed as a
       TCP port. `RUNPOD_PUBLIC_IP` and `RUNPOD_TCP_PORT_<port>` are injected
       by the platform, so this is derived exactly as the proxy URL is - and
       it is preferred because nothing sits between it and the app. The proxy
       is Cloudflare, and Cloudflare serves a browser and refuses a server,
       which is why a pod that is demonstrably healthy in a tab can be
       unreachable from Render with nothing wrong on either machine.
    3. **RunPod's proxy**, which is still the right answer when no TCP port is
       exposed, and is what every existing pod falls back to.

    The app refuses a plain-HTTP address unless `VOICE_ALLOW_PLAIN_HTTP` is
    set there, so announcing one costs nothing when it is not wanted: the
    registration is refused with a sentence, rather than a worker silently
    becoming unreachable.
    """
    explicit = (os.environ.get("PUBLIC_WORKER_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    direct = direct_url()
    if direct:
        return direct
    pod_id = (os.environ.get("RUNPOD_POD_ID") or "").strip()
    if not pod_id:
        return ""
    return f"https://{pod_id}-{port()}.proxy.runpod.net"


def direct_url() -> str:
    """`http://<public ip>:<mapped port>`, or "" when RunPod has not published one.

    Both halves have to be present. A public IP with no mapping for *this*
    port is a pod exposing something else over TCP, and guessing a port there
    would point the app at whatever else is listening.
    """
    ip = (os.environ.get("RUNPOD_PUBLIC_IP") or "").strip()
    if not ip:
        return ""
    mapped = (os.environ.get(f"RUNPOD_TCP_PORT_{port()}") or "").strip()
    try:
        if int(mapped) <= 0:
            return ""
    except ValueError:
        return ""
    return f"http://{ip}:{mapped}"


def port() -> int:
    """The port this worker is actually listening on.

    `PORT` was the whole answer and it is the wrong one whenever the container
    runs more than the worker. This project's pod runs the app on `PORT` and
    the voice beside it on another port, so the worker announced the *app's*
    address and the app then health-checked a web service looking for a voice
    (PROBLEMS.md §117). That is §78 one layer up: a port that is written down
    in one place and used in another.

    So it is read from what the process was actually told, in the order that
    can be trusted: an explicit `VOICE_WORKER_PORT`, then the `--port` uvicorn
    was given on the command line, then `PORT`, then the image's default.
    """
    explicit = (os.environ.get("VOICE_WORKER_PORT") or "").strip()
    if explicit:
        try:
            return int(explicit)
        except ValueError:
            log.warning("VOICE_WORKER_PORT=%r is not a number; ignoring it",
                        explicit)
    argv_port = _port_from_argv(sys.argv)
    if argv_port:
        return argv_port
    try:
        return int(os.environ.get("PORT") or 8001)
    except ValueError:
        return 8001


def _port_from_argv(argv: list) -> int:
    """The `--port` this process was started with, if it was started with one.

    Pure, so it is tested. Both spellings, because `uvicorn --port 8002` and
    `uvicorn --port=8002` are the same command to everyone except a parser.
    """
    for index, item in enumerate(argv or []):
        arg = str(item)
        value = ""
        if arg == "--port" and index + 1 < len(argv):
            value = str(argv[index + 1])
        elif arg.startswith("--port="):
            value = arg.split("=", 1)[1]
        if value:
            try:
                found = int(value)
            except ValueError:
                continue
            if found > 0:
                return found
    return 0


def app_url() -> str:
    return (os.environ.get("FAM_APP_URL") or "").strip().rstrip("/")


def token() -> str:
    return (os.environ.get("VOICE_REGISTRY_TOKEN") or "").strip()


def identity() -> dict:
    """What this worker is, for the registration and for its own `/health`.

    `image` and `commit` are §77's rule across the split: "the fix is pushed"
    and "the fix is running on the card" are the same sentence from the app's
    side, and telling them apart by hand is most of a lost afternoon. Both are
    build arguments, so an image built without them says `unknown` rather than
    guessing.
    """
    from voice_control import CONTRACT_VERSION

    return {
        "contract": CONTRACT_VERSION,
        "mode": (os.environ.get("VOICE_WORKER_MODE") or "serverless").strip(),
        "port": port(),
        "image": (os.environ.get("VOICE_IMAGE") or "").strip() or "unknown",
        "commit": (os.environ.get("FAM_COMMIT")
                   or os.environ.get("RENDER_GIT_COMMIT") or "").strip() or "unknown",
        "host": socket.gethostname(),
        "engine": "chatterbox",
    }


def payload(ready: bool, detail: str, sample_rate: int = 0) -> dict:
    body = dict(identity())
    body.update({"url": public_url(), "ready": bool(ready),
                 "detail": detail[:300], "sample_rate": int(sample_rate or 0)})
    return body


def why_not() -> str:
    """Why this worker will not register, or "" if it will.

    Said once at boot rather than left to be noticed: a worker that is running
    perfectly and is invisible to the app is the exact shape of failure this
    project keeps paying for, and "FAM_APP_URL is not set" is the whole
    diagnosis.
    """
    if not app_url():
        return "FAM_APP_URL is not set"
    if not token():
        return "VOICE_REGISTRY_TOKEN is not set"
    if not public_url():
        return ("neither PUBLIC_WORKER_URL nor RUNPOD_POD_ID is set, so this "
                "worker cannot say where it is")
    return ""


async def announce(ready: bool, detail: str, sample_rate: int = 0,
                   timeout: float = 10.0) -> bool:
    """One registration. Returns whether it landed; never raises."""
    problem = why_not()
    if problem:
        return False
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{app_url()}/api/voice/register",
                json=payload(ready, detail, sample_rate),
                headers={"Authorization": f"Bearer {token()}",
                         "Content-Type": "application/json"})
        if response.status_code >= 400:
            log.warning("registration refused by %s: HTTP %s %s", app_url(),
                        response.status_code, response.text[:200])
            return False
        return True
    except Exception as exc:
        log.debug("registration to %s failed (%s: %s); retrying on the next beat",
                  app_url(), type(exc).__name__, exc)
        return False


async def heartbeat_forever(state, interval: float = 0.0) -> None:
    """Register now, and keep saying so. Never raises, never exits on failure.

    `state` is a callable returning `(ready, detail, sample_rate)` - the
    worker's own preflight, read on every beat rather than captured at boot, so
    a card that fell over is reported as not ready instead of being remembered
    as healthy.
    """
    problem = why_not()
    if problem:
        log.info("not registering with an app: %s. Set FAM_APP_URL, "
                 "VOICE_REGISTRY_TOKEN and (off RunPod) PUBLIC_WORKER_URL to "
                 "have this worker announce itself.", problem)
        return
    wait = interval or _interval()
    log.info("registering with %s as %s every %.0fs", app_url(), public_url(), wait)
    first = True
    while True:
        try:
            ready, detail, rate = state()
        except Exception as exc:  # pragma: no cover - a preflight that threw
            ready, detail, rate = False, f"{type(exc).__name__}: {exc}", 0
        landed = await announce(ready, detail, rate)
        if first:
            log.log(logging.INFO if landed else logging.WARNING,
                    "first registration %s", "accepted" if landed else "failed")
            first = False
        await asyncio.sleep(wait)


async def withdraw(timeout: float = 5.0) -> None:
    """Say this worker is going, so the app stops offering it immediately.

    A courtesy rather than a mechanism - the TTL is what makes a dead pod stop
    being a candidate, because a pod that is killed gets no chance to say
    anything. This only closes the window between a graceful stop and the TTL.
    """
    if why_not():
        return
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            await client.post(
                f"{app_url()}/api/voice/register",
                json={"url": public_url(), "leaving": True},
                headers={"Authorization": f"Bearer {token()}",
                         "Content-Type": "application/json"})
    except Exception as exc:
        log.debug("could not withdraw: %s", exc)


def _interval() -> float:
    try:
        return max(10.0, float(os.environ.get("VOICE_REGISTER_INTERVAL")
                               or DEFAULT_INTERVAL))
    except ValueError:
        return DEFAULT_INTERVAL
