"""Chatterbox on somebody else's GPU, reached over HTTP.

The app and the voice stop being the same process. `app.py` runs on a CPU host
(Render), `ChatterboxTTS` runs on a card, and this is the seam between them:
sentences in, 16-bit PCM out, exactly as `ChatterboxEngine` does it in-process.

## Why this is one engine and not two

RunPod sells the same GPU two ways - a serverless endpoint that sleeps when
idle, and a pod that stays up - and the right answer changes with volume. So
the *transport* is configuration and the *engine* is not:

    VOICE_BACKEND=remote  REMOTE_VOICE_TRANSPORT=runpod  RUNPOD_ENDPOINT_ID=...
    VOICE_BACKEND=remote  REMOTE_VOICE_TRANSPORT=http    REMOTE_VOICE_URL=https://...

Both speak the identical JSON contract below, so one worker image serves both
and moving between them is two environment variables. Nothing in `pipeline.py`,
`script_generator.py`, the cache or the player knows which one is answering.

    request   {"text": str, "voice": str|None, "sample_rate": int,
               "format": "pcm_s16le"}
    response  {"audio": "<base64 pcm_s16le>", "sample_rate": int,
               "samples": int, "engine": "chatterbox"}

The transports differ only in the envelope: RunPod wraps the request in
`{"input": ...}` and the reply in `{"output": ...}`, and may answer a slow call
with a job id to poll. `voice_worker/` implements both entrypoints over one
`synthesise()`.

## Three things this has to get right

* **The browser is still given raw PCM.** "No MP3, no audio files" is a rule
  about what reaches the listener and what is written to disk, not about what
  two servers say to each other. Base64 over JSON is a wire encoding; it is
  decoded here, in memory, and never becomes a file. Nothing is transcoded.

* **The sample rate is known before the first call.** `app.py` writes the
  stream header from `engine.sample_rate` before any audio has been requested,
  so the engine cannot wait to be told. It is configured, it is *asked for* in
  every request, and the reply is checked against it - a worker that answered
  at a different rate would play at the wrong pitch, which is a failure you
  hear rather than one you are told about.

* **It never quietly becomes something else.** No fallback to a local engine,
  no substituted voice, no silent retry that ends in a tone. Every failure
  raises with the reason attached, so a broken endpoint arrives as an error the
  interface can show. This is the guard PROBLEMS.md §61 removed with WellSaid
  and said to re-add by hand for the next hosted engine; this is by hand.

## Where the worker is, which is no longer this file's business

This used to build the address out of `settings` on every call, which made the
environment variable the single source of truth about a machine that RunPod can
move without telling anybody. `voice_control.py` now answers *which* worker;
this answers *what to say to it*. The split is the point (PROBLEMS.md §112):

* A configured endpoint is still used directly and still costs no probe. The
  happy path is unchanged, down to the number of requests.
* When the control plane is holding a verified endpoint - because the
  supervisor found the configured one dead, or because a pod registered itself
  - that is what is spoken to.
* A chunk that fails **demotes** the endpoint, so the next one re-resolves
  instead of failing the same way fifteen more times in the same episode.

Failing over is not falling back: every rung is the same worker image, the same
weights and the same reference recording, and a worker whose sample rate
disagrees with the header already written is refused rather than used. What
moves is the address; what comes out of it does not.

## The cold start, and why it is answered by starting earlier

A serverless worker that has scaled to zero pays container boot plus a ~10s
model load on the first request. That is exactly the wait the one-sentence spec
refuses - and exactly the wait CLAUDE.md says to answer by *starting earlier*
rather than filling.

So `wake()` fires a throwaway job the moment a request arrives, before Claude
has written a word. The worker boots while the script is being written, which
is several seconds of cover that costs nothing and is not filler: it is the
same "do it before the listener is waiting" move as prefetching scripts on the
browse surfaces. It is a hint, not a guarantee - `wake()` never raises, never
blocks the request, and a miss costs only the cold start it was trying to hide.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from typing import Any

from config import settings
from tts import TTSEngine, Voice


log = logging.getLogger(__name__)

#: What the worker is asked for and what it must answer with. Not negotiable
#: per request: the app's whole audio path is 16-bit little-endian PCM.
WIRE_FORMAT = "pcm_s16le"

#: Transports this engine knows how to speak. Adding one means adding an
#: envelope, not an engine.
TRANSPORTS = ("runpod", "http")

#: The route `voice_worker/server.py` serves the contract on, and the only part
#: of the URL this side invents. `REMOTE_VOICE_URL` is the pod's *base* URL.
SYNTH_ROUTE = "/synth"


def synth_url(url: str) -> str:
    """Where the POST actually goes, for any worker address.

    A worker's URL is documented as its *base* and the route is this side's to
    add - but an operator who pastes the URL they were testing with, the one
    that already ends in `/synth`, has configured something unambiguous, and
    appending a second `/synth` to it produces a 404 indistinguishable from a
    worker that has no route at all.

    Module-level rather than a method because the address no longer comes only
    from `settings`: `voice_control` can hand this a pod that registered
    itself, and two implementations of "where does the POST go" would be two
    places for a trailing route to be handled differently.
    """
    url = (url or "").rstrip("/")
    return url if url.endswith(SYNTH_ROUTE) else url + SYNTH_ROUTE


def base_url(url: str) -> str:
    """The origin of a worker address, whichever way it was written.

    `/health` and `/openapi.json` hang off this, so an address that already
    names `/synth` must not send the probes to `.../synth/health`.
    """
    url = (url or "").rstrip("/")
    return url[:-len(SYNTH_ROUTE)] if url.endswith(SYNTH_ROUTE) else url


class RemoteVoiceError(RuntimeError):
    """The remote voice could not speak, and this says why.

    Deliberately not a subclass of anything that triggers a fallback. A hosted
    voice that fails must fail, not be replaced by a different one.
    """


@dataclass(frozen=True)
class RemoteConfig:
    """The settings this engine needs, resolved and validated together.

    Built from `settings` rather than read one at a time, so "is this
    configured" is one question with one answer instead of four scattered
    truthiness checks that can each be half-right.
    """

    transport: str
    url: str
    api_key: str
    sample_rate: int
    timeout: float
    connect_timeout: float
    concurrency: int
    voice: str

    @classmethod
    def from_settings(cls) -> "RemoteConfig":
        transport = (settings.remote_voice_transport or "").strip().lower()
        if transport == "runpod":
            endpoint = (settings.runpod_endpoint_id or "").strip()
            base = (settings.runpod_base_url or "").rstrip("/")
            url = f"{base}/{endpoint}" if endpoint else ""
            key = _credential("RUNPOD_API_KEY", settings.runpod_api_key)
        else:
            url = (settings.remote_voice_url or "").strip().rstrip("/")
            key = _credential("REMOTE_VOICE_TOKEN", settings.remote_voice_token)
        return cls(
            transport=transport,
            url=url,
            api_key=key,
            sample_rate=int(settings.remote_voice_sample_rate),
            timeout=float(settings.remote_voice_timeout),
            connect_timeout=float(settings.remote_voice_connect_timeout),
            concurrency=max(1, int(settings.remote_voice_concurrency)),
            voice=(settings.remote_voice_id or "").strip(),
        )

    def synth_url(self) -> str:
        """Where the POST would go for the configured address."""
        return synth_url(self.url)

    def base_url(self) -> str:
        """The configured origin, whichever way the URL was written."""
        return base_url(self.url)

    def problem(self) -> str:
        """Why this *configured address* cannot be used, or "" if it can.

        Configuration only - no network. `available()` is called from
        `/api/health` and must not become a request to a third party; the
        question "does this endpoint actually speak" is answered by making it
        speak, in `warm_up()`, and reported separately.

        Note what this is and is not, because it used to be read as more than
        it says. It answers "is the address in this environment complete",
        which is what lets `_endpoint()` skip the ladder on a deployment that
        names its worker. It does **not** answer "can this deployment find a
        voice" - since §112 that is `voice_control.ladder()`'s question, and
        answering it from here is what made the registered rung unreachable
        (PROBLEMS.md §119).
        """
        fatal = self.fatal()
        if fatal:
            return fatal
        if not self.url:
            missing = ("RUNPOD_ENDPOINT_ID" if self.transport == "runpod"
                       else "REMOTE_VOICE_URL")
            return f"{missing} is not set"
        if self.transport == "runpod" and not self.api_key:
            return "RUNPOD_API_KEY is not set"
        return ""

    def fatal(self) -> str:
        """Why no discovered address could rescue this, or "".

        The half of `problem()` the ladder cannot answer. A transport that is
        not a transport and a sample rate that cannot be a rate are wrong
        wherever the worker turns out to be - the rate especially, since the
        stream header is written from it before any audio exists, so a worker
        found later would be refused for disagreeing with a number that was
        wrong to begin with. A missing *address*, by contrast, is only a
        problem when nothing can find one.

        `Settings` refuses both at construction when the backend is remote, so
        in production this is defence in depth. It is asked here anyway
        because `diagnose()` must not report a voice on the strength of a
        ladder while holding a setting no worker could satisfy.
        """
        if self.transport not in TRANSPORTS:
            return (f"REMOTE_VOICE_TRANSPORT={self.transport!r} is not a "
                    f"transport. Use one of: {', '.join(TRANSPORTS)}")
        if self.sample_rate <= 0:
            return f"REMOTE_VOICE_SAMPLE_RATE={self.sample_rate} must be positive"
        return ""


def _credential(name: str, configured: str) -> str:
    """The value in force, preferring the credential chain to import-time state.

    `credentials.active()` reflects a rotation that happened after startup;
    `settings` is a snapshot taken at import. Falling back to the snapshot
    keeps this working when the chain has no opinion.
    """
    try:
        import credentials

        found = credentials.active(name)
        if found:
            return found
    except Exception:  # pragma: no cover - the chain is optional here
        pass
    return (configured or "").strip()


@dataclass
class Reachability:
    """The last time a real call was attempted, and what happened.

    "Verify, do not inspect" (PROBLEMS.md §52) applied to a remote voice: a
    configured endpoint is not a reachable one, and a health check that only
    reads configuration answers a cheaper question than the one being asked.
    `warm_up()` performs the real synthesis and records the answer here, so
    `/api/health` can report what was actually observed rather than what was
    set. `unknown` until something has genuinely been tried.
    """

    state: str = "unknown"  # unknown | ok | failed
    detail: str = ""
    at: float = 0.0
    latency: float = 0.0

    def as_dict(self) -> dict:
        out = {"state": self.state, "detail": self.detail}
        if self.at:
            out["age_seconds"] = round(time.time() - self.at, 1)
        if self.latency:
            out["latency_seconds"] = round(self.latency, 3)
        return out


class RemoteChatterboxEngine(TTSEngine):
    """Chatterbox over HTTP. Same voice, different machine.

    Everything about the *audio* is decided on the worker, which runs the
    generation settings `tts.CHATTERBOX_GENERATION` names and clones the same
    reference recording. This side owns the transport and nothing else, which
    is why switching a listener between an in-process card and a remote one
    changes latency and cost but not what they hear.
    """

    name = "remote"
    keeps_audio = True

    #: Shared across requests: `build_engine()` constructs a new instance per
    #: call, so anything that must be reused - the connection pool, the
    #: concurrency gate, what the last real call proved - lives on the class.
    _client: Any = None
    _gate: "asyncio.Semaphore | None" = None
    _gate_size: int = 0
    _reachability = Reachability()
    #: When the last wake was sent, on `time.monotonic()`'s scale. The
    #: sentinel is -inf rather than 0.0 because 0.0 is a reading that
    #: clock can actually produce: `CLOCK_MONOTONIC` counts from boot, so
    #: on a machine whose uptime is still under
    #: `REMOTE_VOICE_WAKE_INTERVAL` the guard below read `now - 0.0` as
    #: "asked recently" and returned without ever firing - suppressing the
    #: wake for exactly the first minute of a container's life, which is
    #: the cold start it exists to hide.
    _woken_at: float = float("-inf")
    #: A route the worker named itself, kept as (base url, route) so a
    #: reconfigured endpoint is not answered with the old one's answer.
    _found_route: tuple[str, str] | None = None

    # -- configuration -----------------------------------------------------

    @classmethod
    def config(cls) -> RemoteConfig:
        return RemoteConfig.from_settings()

    @classmethod
    def discovery(cls) -> list[str]:
        """The rungs that could find a worker, named. Empty if none can.

        `voice_control.ladder()` reads settings and does no I/O, which is what
        lets an availability check ask it at all: `/api/health` calls this on
        every poll, and a health check that makes a billed third-party request
        is one somebody switches off.
        """
        try:
            import voice_control

            return [rung.name for rung in voice_control.ladder()
                    if rung.configured]
        except Exception:  # pragma: no cover - the ladder is optional here
            return []

    @classmethod
    def diagnose(cls) -> tuple[bool, str]:
        """Why this engine can or cannot serve, in one sentence.

        **A configured address is one way to have a voice and stopped being
        the only one in §112.** This asked `RemoteConfig.problem()` and nothing
        else, so a deployment set up exactly as REMOTE_VOICE.md documents - one
        `VOICE_REGISTRY_TOKEN` on the app, and pods that introduce themselves -
        reported `REMOTE_VOICE_URL is not set` and `build_engine()` handed back
        the placeholder tone. The registered rung could never serve, and not
        because anything on it was broken: the engine that walks the ladder was
        ruled out before it was built, so nothing ever asked (PROBLEMS.md §119).
        A worker could register, the supervisor could verify it and
        `/api/health` could report it green, and every listener still got a
        tone.

        So the question is the ladder's rather than this file's: can anything
        here find a worker. Whether one is *answering* is a different question,
        asked by a real call and reported separately in `report()` - which is
        why being generous here costs no honesty. §52's two questions stay two.
        """
        config = cls.config()
        fatal = config.fatal()
        if fatal:
            return False, fatal
        problem = config.problem()
        if not problem:
            return True, f"{config.transport}, {config.sample_rate} Hz"
        rungs = cls.discovery()
        if rungs:
            return True, (f"{config.sample_rate} Hz, address discovered: "
                          + ", ".join(rungs))
        return False, problem

    @classmethod
    def available(cls) -> bool:
        """Configured well enough to try. Deliberately not memoised.

        `ChatterboxEngine` caches this because importing torch and probing a
        card is expensive and its answer cannot change while the process runs.
        Here the inputs are environment variables, a credential that
        `credentials.refresh()` can replace mid-run, and now a ladder whose
        rungs are read from settings - so caching would pin a stale answer past
        a rotation.
        """
        return cls.diagnose()[0]

    @classmethod
    def voices(cls) -> list[Voice]:
        if not cls.available():
            return []
        config = cls.config()
        import voice_bank

        # The default voice first, by the id it has always had, then every
        # voice in the bank (§147). The bank lives in the app's database and
        # the worker is sent a recording it lacks, so the list is the app's.
        voices = [Voice(id=cls.default_voice_id(), label=voice_bank.DEFAULT_LABEL,
                        engine=cls.name,
                        detail=f"Chatterbox via {config.transport}")]
        for v in voice_bank.catalogue():
            if not v.default:
                voices.append(Voice(id=f"remote:{v.slug}", label=v.label,
                                    engine=cls.name,
                                    detail=v.description or "Chatterbox"))
        return voices

    @classmethod
    def default_voice_id(cls) -> str:
        """`voices()`'s id without its availability check - see the base."""
        return f"remote:{cls.config().voice or 'reference_3'}"

    @property
    def sample_rate(self) -> int:
        """Configured, not discovered - the header is written before the first
        call. `synth` refuses a reply that disagrees with it."""
        return int(settings.remote_voice_sample_rate)

    # -- plumbing ----------------------------------------------------------

    @classmethod
    def _http(cls, config: RemoteConfig):
        """One connection pool for the process.

        A pool per request would pay TLS setup on every chunk - roughly fifteen
        times an episode - which is exactly the kind of cost that hides inside
        an average and shows up in the first-chunk number.
        """
        if cls._client is None:
            import httpx

            import voice_control

            cls._client = httpx.AsyncClient(
                timeout=httpx.Timeout(config.timeout,
                                      connect=config.connect_timeout),
                # The same identity the probe path sends. They used to differ,
                # and a difference here is a worker that answers a health
                # check and refuses an episode - or the reverse, which is a
                # health page that lies (PROBLEMS.md §117).
                headers={"User-Agent": voice_control.USER_AGENT},
                limits=httpx.Limits(max_connections=32),
            )
        return cls._client

    @classmethod
    async def aclose(cls) -> None:
        """Release the pool. For tests and for a clean shutdown."""
        client, cls._client = cls._client, None
        if client is not None:
            await client.aclose()

    @classmethod
    def _semaphore(cls, size: int) -> asyncio.Semaphore:
        """One generation at a time, times `size`.

        In-process Chatterbox serialises on `Semaphore(1)` because one card
        cannot run concurrent generations safely. That is a property of the
        card, not of the interface, so a remote backend that fans out across
        workers sets its own ceiling. Rebuilt when the setting changes so a
        reconfigured limit is not ignored until restart.
        """
        if cls._gate is None or cls._gate_size != size:
            cls._gate = asyncio.Semaphore(size)
            cls._gate_size = size
        return cls._gate

    @staticmethod
    def _payload(text: str, config: RemoteConfig, voice: str | None = None,
                 with_recording: bool = False) -> dict:
        """One request's body. `voice` is a bank voice (§147), or None for the
        default - which is `REMOTE_VOICE_ID` when set, exactly as before."""
        import voice_bank

        payload = {
            "text": text,
            "voice": config.voice or None,
            "sample_rate": config.sample_rate,
            "format": WIRE_FORMAT,
        }
        if voice and not voice_bank.is_default(voice):
            slug = voice_bank.slug_of(voice)
            try:
                fields = voice_bank.wire_fields(slug, with_recording)
            except Exception as exc:  # noqa: BLE001 - phrased for the listener
                raise RemoteVoiceError(
                    f"voice {slug!r} could not be read from the bank: {exc}") from exc
            # A voice that has left the bank is a stale choice, and is spoken
            # in the default voice rather than refused - as it always was.
            if fields:
                payload["voice"] = slug
                payload.update(fields)
        return payload

    @staticmethod
    def _headers(token: str) -> dict:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # -- which worker ------------------------------------------------------

    async def _endpoint(self, config: RemoteConfig):
        """The worker to speak to, and why it is that one.

        Three cases, in this order, and the middle one is what keeps the happy
        path free:

        1. **The control plane is holding a verified endpoint.** It is held
           because something checked it - the supervisor on its timer, or a
           failure that demoted whatever came before. Trust it.
        2. **The configured transport is complete.** Use it, with no probe at
           all. A deployment that names its endpoint and works must not start
           paying a round trip per episode for a mechanism it does not need.
        3. **Neither.** Resolve the ladder, which is the case where somebody
           has set nothing but a registration token and the pods introduce
           themselves.
        """
        import voice_control

        held = voice_control.held()
        if held is not None and voice_control.fresh():
            return held
        configured = voice_control.Endpoint(
            transport=config.transport, url=config.url, rung="configured",
            why="the transport named in the environment",
            token=config.api_key, sample_rate=config.sample_rate)
        if not config.problem() and not voice_control.recently_failed(
                configured.key()):
            return configured
        try:
            return await voice_control.current()
        except voice_control.VoiceControlError as exc:
            raise RemoteVoiceError(str(exc)) from exc

    # -- the two envelopes -------------------------------------------------

    async def _call_runpod(self, payload: dict, config: RemoteConfig,
                           url: str = "", token: str = "") -> dict:
        """RunPod's queue API: submit, and poll if the sync wait ran out.

        `/runsync` answers directly when the job finishes inside its window and
        otherwise hands back a job id with `IN_QUEUE` or `IN_PROGRESS`. A cold
        worker routinely exceeds that window, so treating the id as a failure
        would turn every cold start into a broken episode.
        """
        client = self._http(config)
        url = url or config.url
        token = token or config.api_key
        response = await client.post(f"{url}/runsync",
                                     json={"input": payload},
                                     headers=self._headers(token))
        body = self._decode_json(response, "runsync")
        deadline = time.monotonic() + config.timeout
        while True:
            status = str(body.get("status", "")).upper()
            if status == "COMPLETED":
                output = body.get("output")
                if not isinstance(output, dict):
                    raise RemoteVoiceError(
                        "RunPod reported COMPLETED with no output object; the "
                        "worker returned "
                        f"{type(output).__name__}. Check the endpoint's logs.")
                return output
            if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
                raise RemoteVoiceError(
                    f"RunPod job {status}: {body.get('error') or 'no reason given'}")
            job_id = body.get("id")
            if not job_id:
                raise RemoteVoiceError(
                    f"RunPod answered {status or 'nothing'} with no job id: "
                    f"{str(body)[:300]}")
            if time.monotonic() >= deadline:
                raise RemoteVoiceError(
                    f"RunPod job {job_id} was still {status} after "
                    f"{config.timeout:.0f}s. A cold worker can exceed this - "
                    "raise REMOTE_VOICE_TIMEOUT, or keep a worker warm.")
            await asyncio.sleep(0.25)
            polled = await client.get(f"{url}/status/{job_id}",
                                      headers=self._headers(token))
            body = self._decode_json(polled, f"status/{job_id}")

    async def _call_http(self, payload: dict, config: RemoteConfig,
                         url: str = "", token: str = "") -> dict:
        """A plain speech server: one POST, one answer, no envelope.

        The one thing that can go wrong here without going wrong on the card is
        the *address*. A 404 is not a worker failing to speak; it is nothing
        having been asked - and the two are indistinguishable in a log unless
        this says which. So a 404 is followed by one question to the worker
        itself (`/openapi.json`, which FastAPI serves for free) rather than by a
        guess: either it names the route it does serve, and this retries there,
        or nothing at that address is a FAM voice worker and the error says so
        with the two things that cause it.
        """
        client = self._http(config)
        base = (url or config.url).rstrip("/")
        token = token or config.api_key
        found = type(self)._found_route
        route = found[1] if found and found[0] == base else synth_url(base)
        response = await client.post(route, json=payload,
                                     headers=self._headers(token))
        if response.status_code == 404:
            named = await self._route_from_worker(config, route, base, token)
            response = await client.post(named, json=payload,
                                         headers=self._headers(token))
            # Remembered only once it has answered something other than 404:
            # a second wrong route is worse than the first.
            if response.status_code != 404:
                type(self)._found_route = (base, named)
        return self._decode_json(response, "synth")

    async def _route_from_worker(self, config: RemoteConfig, tried: str,
                                 url: str = "", token: str = "") -> str:
        """Ask the worker which route takes the contract, or say why there is none.

        Bounded on purpose: one GET, only ever after a 404, never on the path a
        working deployment takes. It reads the worker's own schema instead of
        trying candidate paths, because a POST to a guessed route on a machine
        that is not this worker is a request to somebody else's service.
        """
        client = self._http(config)
        base = base_url(url or config.url)
        token = token or config.api_key
        try:
            schema = await client.get(f"{base}/openapi.json",
                                      headers=self._headers(token))
        except Exception as exc:
            raise RemoteVoiceError(
                f"remote voice synth returned HTTP 404 at {tried}, and asking "
                f"the worker what it serves failed too: {type(exc).__name__}: "
                f"{exc}") from exc
        paths: dict = {}
        if schema.status_code < 400:
            try:
                body = schema.json()
                paths = body.get("paths") or {} if isinstance(body, dict) else {}
            except Exception:
                paths = {}
        posts = [path for path, methods in paths.items()
                 if isinstance(methods, dict) and "post" in methods]
        speaks = [path for path in posts if "synth" in path.lower()] or posts
        if len(speaks) == 1:
            log.warning("remote voice: %s has no %s; this worker serves POST %s "
                        "and that is what will be used", base, SYNTH_ROUTE,
                        speaks[0])
            route = speaks[0] if speaks[0].startswith("/") else "/" + speaks[0]
            return base + route
        if not paths:
            raise RemoteVoiceError(
                f"nothing at {base} answers as a FAM voice worker: POST {tried} "
                f"returned 404 and GET {base}/openapi.json returned "
                f"{schema.status_code}. The two things that cause this are a "
                "REMOTE_VOICE_URL naming a proxied port the worker is not "
                "listening on (Dockerfile.voice serves ${PORT:-8001}), and a "
                "pod started without VOICE_WORKER_MODE=http, which runs the "
                "serverless handler and opens no port at all.")
        raise RemoteVoiceError(
            f"the worker at {base} does not serve {SYNTH_ROUTE} and does not "
            f"name one route that could: it posts {sorted(posts) or 'nothing'}. "
            "Point REMOTE_VOICE_URL at a FAM voice worker, or rebuild the "
            "image from Dockerfile.voice.")

    @staticmethod
    def _decode_json(response, what: str) -> dict:
        """Turn any non-answer into a sentence naming the endpoint and the code.

        A hosted voice fails in ways a local one cannot - 401, 404, a proxy's
        HTML error page - and "expected object, got str" would send whoever
        reads the log looking in the wrong place entirely.
        """
        if response.status_code >= 400:
            raise RemoteVoiceError(
                f"remote voice {what} returned HTTP {response.status_code}: "
                f"{response.text[:300]}")
        try:
            body = response.json()
        except Exception as exc:
            raise RemoteVoiceError(
                f"remote voice {what} did not return JSON "
                f"({type(exc).__name__}): {response.text[:200]}") from exc
        if not isinstance(body, dict):
            raise RemoteVoiceError(
                f"remote voice {what} returned {type(body).__name__}, not an object")
        return body

    # -- synthesis ---------------------------------------------------------

    async def synth(self, text: str, wpm: float, voice: str | None = None) -> bytes:
        """`wpm` is accepted and ignored - Chatterbox has no rate control.

        Same as the in-process engine, and for the same reason: length is held
        by the budget and by trimming at a sentence boundary, not by speeding
        the voice up.
        """
        config = self.config()
        endpoint = await self._endpoint(config)
        if not endpoint.url:
            raise RemoteVoiceError(
                f"remote voice is not configured: {config.problem()}")

        payload = self._payload(text, config, voice)
        started = time.monotonic()
        async with self._semaphore(config.concurrency):
            try:
                try:
                    output = await self._speak(payload, config, endpoint)
                    # A serverless worker reports a failure as `{"error"}`
                    # inside a successful job rather than as a status code.
                    if (isinstance(output, dict) and not output.get("audio")
                            and output.get("error")):
                        raise RemoteVoiceError(
                            "the remote voice returned no audio. Worker said: "
                            f"{str(output['error'])[:300]}")
                except RemoteVoiceError as exc:
                    # A bank voice this worker has never been sent (§147).
                    # Not a fault with the worker - send the recording once,
                    # on the same address, and it keeps it.
                    import voice_bank

                    if (voice_bank.MISSING_MARKER not in str(exc)
                            or "reference" in payload):
                        raise
                    payload = self._payload(text, config, voice,
                                            with_recording=True)
                    output = await self._speak(payload, config, endpoint)
            except RemoteVoiceError as exc:
                # A real call is the only thing that learns what a health check
                # cannot - a worker whose /health is green and whose /synth is
                # a 404. Tell the control plane, then try whatever it finds
                # instead. Once: a second address that fails is a deployment
                # problem, and an episode is not the place to work through a
                # list of them.
                replacement = await self._after_failure(endpoint, str(exc))
                if replacement is None:
                    raise
                log.warning("remote voice: %s failed (%s); trying %s (%s)",
                            endpoint.url, exc, replacement.url, replacement.rung)
                output = await self._speak(payload, config, replacement)
                endpoint = replacement
            except Exception as exc:
                # Network errors arrive as a dozen different exception types.
                # All of them mean the same thing to a listener, and none of
                # them may become a different voice.
                raise RemoteVoiceError(
                    f"remote voice ({config.transport}) failed: "
                    f"{type(exc).__name__}: {exc}") from exc

        pcm = self._pcm_from(output, config)
        elapsed = time.monotonic() - started
        seconds = len(pcm) / float(config.sample_rate * settings.sample_width or 1)
        log.debug("remote voice: %d words -> %.1fs audio in %.2fs (%.1fx realtime)",
                  len(text.split()), seconds, elapsed,
                  seconds / elapsed if elapsed else 0)
        type(self)._reachability = Reachability(
            state="ok", detail=f"{endpoint.rung} via {endpoint.transport}",
            at=time.time(), latency=elapsed)
        return pcm

    async def _speak(self, payload: dict, config: RemoteConfig, endpoint) -> dict:
        """One request to one worker, in that worker's envelope."""
        if endpoint.transport == "runpod":
            return await self._call_runpod(payload, config, endpoint.url,
                                           endpoint.token)
        return await self._call_http(payload, config, endpoint.url,
                                     endpoint.token)

    async def _after_failure(self, endpoint, detail: str):
        """The next address to try, or `None` when there is not a different one.

        `None` is the common answer and the important one: a single-endpoint
        deployment must raise the worker's own error rather than a sentence
        about failover, and a retry against the address that just failed would
        double every timeout in front of a listener.
        """
        try:
            import voice_control

            voice_control.demote(endpoint, detail)
            if not voice_control.discovery_enabled():
                return None
            # Not `force`: `demote` has already dropped whatever was held, so
            # the first chunk to fail resolves and the other three in flight
            # get its answer. Forcing would make four failing chunks walk the
            # ladder four times, in front of the same listener.
            replacement = await voice_control.current()
        except Exception as exc:
            log.debug("no replacement voice endpoint: %s", exc)
            return None
        if replacement.key() == endpoint.key():
            return None
        return replacement

    @classmethod
    def _pcm_from(cls, output: dict, config: RemoteConfig) -> bytes:
        """Validate the reply hard, then decode it.

        Every check here is a failure that would otherwise be *audible* rather
        than reported: a wrong sample rate plays at the wrong pitch, an odd
        byte count shifts every sample by one and turns the episode into noise,
        and an empty payload is the silent-empty-episode failure this project
        has lost the most time to.
        """
        encoded = output.get("audio")
        if not encoded:
            raise RemoteVoiceError(
                "the remote voice returned no audio. Worker said: "
                f"{str(output.get('error') or output)[:300]}")
        fmt = str(output.get("format", WIRE_FORMAT)).lower()
        if fmt != WIRE_FORMAT:
            raise RemoteVoiceError(
                f"the remote voice returned {fmt!r}; this app plays "
                f"{WIRE_FORMAT} and does not transcode.")
        rate = int(output.get("sample_rate") or config.sample_rate)
        if rate != config.sample_rate:
            raise RemoteVoiceError(
                f"the remote voice answered at {rate} Hz but the stream header "
                f"already said {config.sample_rate} Hz. Set "
                f"REMOTE_VOICE_SAMPLE_RATE={rate} to match the worker; playing "
                "it would be the wrong pitch.")
        try:
            pcm = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise RemoteVoiceError(
                f"the remote voice's audio was not valid base64: {exc}") from exc
        if not pcm:
            raise RemoteVoiceError("the remote voice returned zero bytes of audio")
        if len(pcm) % settings.sample_width:
            raise RemoteVoiceError(
                f"the remote voice returned {len(pcm)} bytes, which is not a "
                f"whole number of {settings.sample_width}-byte samples")
        return pcm

    # -- the cold start ----------------------------------------------------

    @classmethod
    async def wake(cls) -> None:
        """Ask for a worker now, so one exists by the time there is audio to make.

        Fired when a request arrives, alongside script generation rather than
        in front of it. Never raises and never blocks: a failed wake costs the
        cold start it was trying to hide and nothing else, so it must not be
        able to cost an episode.
        """
        config = cls.config()
        if config.problem() or config.transport != "runpod":
            return  # nothing to wake: an always-on pod is already up
        now = time.monotonic()
        if now - cls._woken_at < settings.remote_voice_wake_interval:
            return  # already asked recently; another job would just queue
        cls._woken_at = now
        try:
            client = cls._http(config)
            await client.post(
                f"{config.url}/run",
                json={"input": {"text": "", "warm": True,
                                "sample_rate": config.sample_rate}},
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {config.api_key}"},
                timeout=5.0,
            )
            log.debug("remote voice: wake sent")
        except Exception as exc:
            log.debug("remote voice: wake failed (%s); the next request pays "
                      "the cold start", type(exc).__name__)

    # -- what is actually true --------------------------------------------

    @classmethod
    def reachability(cls) -> dict:
        return cls._reachability.as_dict()

    @classmethod
    def record_failure(cls, detail: str) -> None:
        cls._reachability = Reachability(state="failed", detail=detail,
                                         at=time.time())


def report() -> dict:
    """What `/api/health` says about the remote voice, if one is configured.

    `endpoint` is **where the next episode would actually go**, which is the
    held address when the control plane is holding one and the configured one
    otherwise. Reporting the setting while speaking to somewhere else would be
    the §52 mistake made inside the report that exists to prevent it - and it
    is exactly the confusion that made a moved pod take a day to find.
    """
    ok, detail = RemoteChatterboxEngine.diagnose()
    config = RemoteChatterboxEngine.config()
    endpoint, source = config.url, "configured"
    try:
        import voice_control

        held = voice_control.held()
        if held is not None:
            endpoint, source = held.url, held.rung
    except Exception:  # pragma: no cover - a report is never load-bearing
        pass
    return {
        "configured": ok,
        "detail": detail,
        "transport": config.transport,
        "sample_rate": config.sample_rate,
        # Where it points, never what authorises it.
        "endpoint": endpoint or None,
        "endpoint_from": source,
        "configured_endpoint": config.url or None,
        "reachable": RemoteChatterboxEngine.reachability(),
    }
