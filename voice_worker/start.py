"""Pick the entrypoint the machine actually needs, and say which and why.

One image serves two platforms - RunPod Serverless, which hands jobs to
`handler.py`, and an always-on pod, which needs `server.py` listening on a
port. Which one to run was an environment variable with a default, and the
default was `serverless`.

**That default is the single most expensive line in this deployment's history.**
A pod started without `VOICE_WORKER_MODE=http` runs the serverless handler and
opens *no port at all*, so the proxy URL answers 404 on every path - including
`/health` - and from the app's side that is indistinguishable from a worker
with a missing route (PROBLEMS.md §78, and again in §112). It is a one-word
mistake that produces a day of diagnosis, and it produces it silently.

So the mode is now **derived from the platform**, and the variable overrides it
rather than deciding it:

    RUNPOD_ENDPOINT_ID set   -> serverless. Only a Serverless worker has one;
                                it is the endpoint it belongs to.
    RUNPOD_POD_ID set        -> http. A pod. There is nothing to take jobs
                                from, so a handler here would sit idle behind
                                a port nobody opened.
    neither                  -> http. A plain box, a laptop, a container
                                somewhere else: the only mode that can be
                                reached at all.

Checked in that order because a Serverless worker has *both* variables. An
explicit `VOICE_WORKER_MODE` still wins, for the deployment that is doing
something this cannot see - and it is announced either way, because "the
container is running the wrong half" is the thing that has to be visible in the
pod's own log rather than inferred from an app's 404 an hour later.
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("voice_worker")

MODES = ("serverless", "http")


def decide(environ: dict | None = None) -> tuple[str, str]:
    """The mode to run, and the sentence explaining it. Pure, so it is tested."""
    env = os.environ if environ is None else environ
    asked = (env.get("VOICE_WORKER_MODE") or "").strip().lower()
    if asked in MODES:
        return asked, f"VOICE_WORKER_MODE={asked} was set"
    if asked and asked != "auto":
        # Named rather than ignored: a typo that silently fell through to a
        # guess is how a pod ends up running the wrong half with nothing said.
        log.warning("VOICE_WORKER_MODE=%r is not a mode (%s); choosing from "
                    "the platform instead", asked, " or ".join(MODES))
    if (env.get("RUNPOD_ENDPOINT_ID") or "").strip():
        return "serverless", ("RUNPOD_ENDPOINT_ID is set, so this is a "
                              "Serverless worker")
    if (env.get("RUNPOD_POD_ID") or "").strip():
        return "http", ("RUNPOD_POD_ID is set and RUNPOD_ENDPOINT_ID is not, "
                        "so this is a pod and needs a port open")
    return "http", "no RunPod environment; serving a port is the only way in"


def main() -> None:
    mode, why = decide()
    port = (os.environ.get("PORT") or "8001").strip()
    log.info("voice worker mode: %s (%s)", mode, why)
    if mode == "http":
        log.info("serving on port %s; the proxy URL must name this port "
                 "(PROBLEMS.md §78)", port)
        import uvicorn

        uvicorn.run("voice_worker.server:app", host="0.0.0.0", port=int(port))
        return
    from voice_worker import handler

    handler.main()


if __name__ == "__main__":
    main()
