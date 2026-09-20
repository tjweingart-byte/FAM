"""Always-on entrypoint: the same voice, behind a plain HTTP port.

    python -m voice_worker.entrypoint          # what the image runs
    uvicorn voice_worker.server:app --port 8001   # one port, by hand

This is the other half of the switch `remote_voice.py` describes. Serverless
sleeps and pays per second; a pod stays up and pays per hour, which becomes the
cheaper answer somewhere north of a few hours of audio a day and is always the
lower-latency one because nothing ever cold-starts. Moving between them is two
environment variables on the app, because both entrypoints wrap one
`synthesise()` and answer with the identical object.

## The port is the security boundary

A serverless endpoint is authenticated by RunPod: a job needs the account's API
key to be queued at all. A pod's exposed port is authenticated by nobody. So
`REMOTE_VOICE_TOKEN` is checked here when it is set, and its absence is logged
at every boot rather than assumed to be deliberate - an open endpoint on a
rented GPU is somebody else's free TTS service, billed to you, and it would
look exactly like your own traffic in the metering log.

## A 404 from here says it came from here

`voice_worker/entrypoint.py` is what starts this, on every port
`deployment.resolve_ports` names. The one thing that still cannot be resolved
from inside the container is whether a request *arrived* - and production spent
two sessions on a 404 that could have been the proxy, the port, the mode or a
missing route (PROBLEMS.md §78, §112). So this worker answers an unknown path
with its own name, the route it does serve, and the ports it is on. RunPod's
proxy answers a port it is not routing with `404 page not found` and no such
body, which makes the two tellable apart in a single line of the app's log -
`remote_voice._decode_json` already prints the body it got.
"""
from __future__ import annotations

import logging
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Header, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from voice_worker.deployment import resolve_ports  # noqa: E402
from voice_worker.synth import WorkerError, preflight, synthesise  # noqa: E402


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("voice_worker")

app = FastAPI(title="FAM voice worker")

#: What this container is, said in a word that nothing else on a rented host
#: would answer with. It is in the identity page and in every 404, because
#: "was that our worker or the proxy in front of it" is the question a 404
#: cannot otherwise answer.
IDENTITY = "fam-voice-worker"

#: The route the app posts the contract to. The same string as
#: `remote_voice.SYNTH_ROUTE`, asserted equal by a test rather than trusted:
#: the two halves are deployed separately and can be different versions of
#: this repo.
SYNTH_ROUTE = "/synth"


def route_table() -> list[str]:
    """Every route this image serves, as `METHOD /path`."""
    return sorted(f"{sorted(r.methods)[0]} {r.path}"
                  for r in app.routes if getattr(r, "methods", None))


def identity() -> dict:
    """Who is answering, on what, with which route. No secrets, no state."""
    return {"worker": IDENTITY, "engine": "chatterbox",
            "synth": SYNTH_ROUTE, "routes": route_table(),
            "ports": resolve_ports(os.environ)}


#: Shared secret the app sends as `Authorization: Bearer ...`. Read at import
#: from the process environment: this is a single-purpose container, and a
#: rotation is a restart.
TOKEN = (os.environ.get("REMOTE_VOICE_TOKEN") or "").strip()


def _authorised(header: str | None) -> bool:
    """Constant-time comparison, so the check cannot be timed open."""
    if not TOKEN:
        return True
    sent = (header or "")
    prefix = "Bearer "
    if sent.startswith(prefix):
        sent = sent[len(prefix):]
    return secrets.compare_digest(sent.strip(), TOKEN)


#: One process serves several ports, so uvicorn runs this app's startup once
#: per server. The load is paid once: a second `_load()` would be a second
#: generation on the one card for nothing.
_MODEL_READY = False


@app.on_event("startup")
async def _startup() -> None:
    global _MODEL_READY
    if _MODEL_READY:
        return
    ready, detail = preflight()
    if not ready:
        # Loud, and then it keeps serving /health so an operator can see why.
        # Unlike the serverless worker there is no queue to poison here, and a
        # container that exits on boot is harder to diagnose than one that
        # answers the question.
        log.error("this worker cannot speak: %s", detail)
        return
    if not TOKEN:
        log.warning("REMOTE_VOICE_TOKEN is not set: this port will synthesise "
                    "for anyone who can reach it, billed to this pod")
    log.info("chatterbox ready (%s); loading the model", detail)
    from voice_worker.synth import _load

    rate = await _load()
    _MODEL_READY = True
    log.info("model resident, emitting %s Hz", rate)


@app.on_event("startup")
async def _announce() -> None:
    """Say what this container serves, and where.

    A 404 in the app's log says only that nothing was home at an address. It
    cannot say whether this worker was the thing that answered, was listening
    on another port, or was never started in `http` mode at all - and the pod's
    own log is the one place all three are visible. So it is printed rather
    than left to be inferred from a Dockerfile.
    """
    ports = ", ".join(str(port) for port in resolve_ports(os.environ))
    log.info("serving on port %s: %s", ports, ", ".join(route_table()))


@app.get("/")
async def root() -> dict:
    """What this is, for whoever opened the proxy URL in a browser."""
    return identity()


@app.get("/health")
async def health() -> dict:
    """What this worker can actually do. Cheap enough to be a probe target."""
    ready, detail = preflight()
    return {"ready": ready, "detail": detail, "engine": "chatterbox",
            "authenticated": bool(TOKEN)}


@app.post("/synth")
async def synth(request: Request,
                authorization: str | None = Header(default=None)) -> dict:
    if not _authorised(authorization):
        raise HTTPException(status_code=401, detail="bad or missing bearer token")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400,
                            detail=f"body is not JSON: {exc}") from exc
    try:
        return await synthesise(payload)
    except WorkerError as exc:
        # 422, not 500: everything `WorkerError` covers is something the caller
        # or the deployment can fix, and the message says which.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - depends on the card
        log.exception("synthesis failed")
        raise HTTPException(status_code=500,
                            detail=f"{type(exc).__name__}: {exc}") from exc


@app.exception_handler(404)
async def _not_found(request: Request, exc) -> JSONResponse:
    """A 404 that says which 404 it is.

    The app's log prints the body of whatever it got (`_decode_json`), so this
    is the difference between "the worker is up and the route is wrong" and
    "nothing was routed to the worker at all" - which, from outside, are the
    same status code. RunPod's proxy answers a port it is not forwarding with
    its own `404 page not found`; this answers with its own name.
    """
    return JSONResponse(
        status_code=404,
        content={"error": f"{IDENTITY} does not serve {request.url.path}; the "
                          f"contract is POST {SYNTH_ROUTE}",
                 **identity()},
    )
