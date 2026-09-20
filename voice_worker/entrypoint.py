"""The container's one entrypoint: resolve what this machine is, then be it.

    python -m voice_worker.entrypoint

`Dockerfile.voice` used to make this decision in a shell `if`, and the shell
could only compare a string. This decides it in Python, says why in the pod's
log, and - the part that matters - **cannot end up having opened no port on a
pod**. See `voice_worker/deployment.py` for the two resolutions and
PROBLEMS.md §112 for the production 404 that made this a file.

## Two ports, one process, one card

`serve_http` binds every port `resolve_ports` names and serves the same `app`
on all of them. That is not load balancing: it is the answer to a 404 whose
cause was an address rather than a voice. The proxy URL names a port, the
container serves a port, and nothing inside the container can see the URL - so
it answers on both of the ports this deployment has used, and the mismatch
stops being able to happen.

One process, so the model is loaded once and `ChatterboxEngine`'s own
`Semaphore(1)` still means one generation at a time on one card. A port that
cannot be bound is logged with its reason and skipped rather than killing a
worker that is otherwise able to speak; only a container that bound *nothing*
exits, because that is the state in which it cannot do its job.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice_worker.deployment import (  # noqa: E402
    MODE_SERVERLESS, looks_serverless, resolve_mode, resolve_ports,
)


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("voice_worker")

HOST = os.environ.get("VOICE_WORKER_HOST", "0.0.0.0")

#: The servers this process is running, one per bound port. A list rather than
#: a count so a shutdown reaches all of them - several uvicorn servers in one
#: loop each installing their own SIGTERM handler leaves the last one holding
#: it, which is a container that closes one port and waits out the grace
#: period on the rest.
SERVERS: list = []


def _bind(port: int) -> tuple[socket.socket | None, str]:
    """A listening socket for `port`, or the reason there is not one.

    Bound here rather than left to uvicorn so that one unavailable port is a
    line in the log instead of a container that exits - on a rented pod,
    something else on 8002 must not cost the voice.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, port))
        sock.listen(2048)
        sock.set_inheritable(True)
    except OSError as exc:
        sock.close()
        return None, f"{type(exc).__name__}: {exc}"
    return sock, ""


def _server(app, port: int, sock: socket.socket):
    """A uvicorn server that does not touch the process's signal handlers.

    Several servers in one loop each installing their own leaves the last one
    holding SIGTERM, so a `docker stop` would shut one port down and wait out
    the grace period on the rest. The signals are handled once, below.
    """
    import uvicorn

    class Quiet(uvicorn.Server):
        def install_signal_handlers(self) -> None:  # uvicorn < 0.29
            return None

        @contextlib.contextmanager
        def capture_signals(self):  # uvicorn >= 0.29
            yield

    config = uvicorn.Config(app, host=HOST, port=port,
                            log_level=os.environ.get("LOG_LEVEL", "info").lower())
    return Quiet(config), sock


def stop_all() -> None:
    """Ask every server in this process to finish. Idempotent."""
    for server in SERVERS:
        server.should_exit = True


async def _run(app, bound: list[tuple[int, socket.socket]]) -> None:
    servers = [_server(app, port, sock) for port, sock in bound]
    SERVERS[:] = [server for server, _ in servers]

    loop = asyncio.get_event_loop()

    def stop() -> None:
        stop_all()

    for sig in (signal.SIGINT, signal.SIGTERM):
        # Only the main thread of the main interpreter may take a signal, and
        # a worker served from a thread (a test, a debugger) is still a worker.
        # It is stopped by `stop_all()` there instead.
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(sig, stop)

    await asyncio.gather(*(server.serve(sockets=[sock])
                           for server, sock in servers))


def serve_http() -> int:
    """Open every resolved port and serve the contract on all of them."""
    from voice_worker import server as worker_server

    ports = resolve_ports(os.environ)
    bound: list[tuple[int, socket.socket]] = []
    for port in ports:
        sock, problem = _bind(port)
        if sock is None:
            log.warning("port %s is not available (%s); the other ports still "
                        "serve", port, problem)
            continue
        bound.append((port, sock))

    if not bound:
        log.error("no port could be opened (wanted %s), so nothing can reach "
                  "this worker. Set PORT or VOICE_WORKER_PORTS to a free port.",
                  ", ".join(str(p) for p in ports))
        return 1

    log.info("serving %s on %s:%s - %s", worker_server.IDENTITY, HOST,
             ", ".join(str(port) for port, _ in bound),
             ", ".join(worker_server.route_table()))
    asyncio.run(_run(worker_server.app, bound))
    return 0


def serve_serverless() -> int:
    """Hand over to the RunPod queue worker, which opens no port by design."""
    if not looks_serverless(os.environ):
        # Explicit, so it is obeyed - and loud, because this is the shape the
        # production pod was in: a healthy worker, a loaded model, and nothing
        # listening on the port the app was dialling.
        log.error("VOICE_WORKER_MODE selects the serverless handler on a "
                  "machine with no serverless queue: no port will be opened, "
                  "so every HTTP request to this container answers 404. Set "
                  "VOICE_WORKER_MODE=http on a pod, or unset it and let this "
                  "decide.")
    from voice_worker.handler import main as serverless_main

    serverless_main()
    return 0


def main() -> int:
    mode, why = resolve_mode(os.environ)
    log.info("mode %s (%s)", mode, why)
    if mode == MODE_SERVERLESS:
        return serve_serverless()
    return serve_http()


if __name__ == "__main__":
    raise SystemExit(main())
