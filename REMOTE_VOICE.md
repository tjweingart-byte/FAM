# The voice on a rented card, and the app where it is cheap

Chatterbox needs a GPU. The rest of FAM — Claude, the cache, eight SQLite
stores, the interface — needs a web server. Running both on the same machine
means paying GPU prices for the 99% of the time nothing is being synthesised,
which `DEPLOY.md` names as the constraint to plan around:

> **Chatterbox runs in-process**, so every replica needs a GPU, and a GPU left
> running is the expensive kind.

This splits them. The app runs on Render; the voice runs on RunPod; between
them is one JSON contract. **The audio is unchanged** — same weights, same
`reference_3.wav`, same six generation settings — because the worker imports
the real `ChatterboxEngine` rather than reimplementing it. What changes is
where the card is and what it costs.

    Render (CPU, always on)              RunPod (GPU)
    ┌───────────────────────────┐        ┌──────────────────────────┐
    │ app.py                    │        │ voice_worker/            │
    │  Claude, cache, DBs,      │ ─────▶ │  synth.py                │
    │  player, /api/audio       │  HTTP  │   └ ChatterboxEngine     │
    │ remote_voice.py           │ ◀───── │  network volume:         │
    │  RemoteChatterboxEngine   │  PCM   │   weights + reference_3  │
    └───────────────────────────┘        └──────────────────────────┘

## Two ways to rent the same card

One worker image serves both. Moving between them is **two environment
variables on the app** — never a rebuild, never a code change.

| | `REMOTE_VOICE_TRANSPORT=runpod` | `REMOTE_VOICE_TRANSPORT=http` |
|---|---|---|
| What it is | RunPod Serverless | An always-on RunPod pod |
| Billing | per second of execution | per hour, speaking or not |
| Cold start | yes, when scaled to zero | never |
| Cheaper below | ~4 hours of audio a day | above that |
| Also set | `RUNPOD_ENDPOINT_ID`, `RUNPOD_API_KEY` | `REMOTE_VOICE_URL`, `REMOTE_VOICE_TOKEN` |

And a third position that needs no worker at all: **`VOICE_BACKEND=chatterbox`**,
which is the default and is the in-process card `Dockerfile.gpu` deploys.
Nothing in this document removes that path — it is the destination, and this is
the detour taken while the volume does not justify a full-time GPU.

## Once, ever

### 1. Build and push the worker image

    docker build -f Dockerfile.voice -t <registry>/fam-voice:1 .
    docker push <registry>/fam-voice:1

### 2. Create a RunPod network volume, and put the voice on it

In the RunPod console: **Storage → Network Volume**, 20 GB is plenty, in the
region you will run workers in. Then attach it to any cheap pod once and copy
two files into it:

    scp reference_3.wav         root@<pod>:/state/voices/
    scp reference_3.rights.json root@<pod>:/state/voices/

The rights record must clear three fields, each `"yes"`:

    {"consent": "yes", "commercial_use": "yes", "synthetic_voice_cleared": "yes"}

The engine refuses to speak without both files, which is why a worker that
"has the image" still cannot talk. A cloned voice is somebody's voice, and that
stays true on rented hardware — which is also why neither file is in the image.

The volume mounts at `/state`, so the same tree does double duty: `/state/hf`
takes the Chatterbox weights on first use. **That download is most of a cold
start**, and paying it once on a volume rather than once per worker is what
makes serverless viable at all.

### 3. Create the endpoint

**Serverless → New Endpoint**, pointing at `<registry>/fam-voice:1`.

| Setting | Value | Why |
|---|---|---|
| GPU | 24 GB class (A5000, 4090, L4) | what Chatterbox was measured on |
| Network volume | the one from step 2 | weights and the voice |
| Active workers | 0 | the whole point; nothing idle is billed |
| Max workers | 1–2 | one card serves one generation at a time |
| Idle timeout | 60s | long enough that a second episode is warm |
| FlashBoot | on | it is what makes a warm start ~instant |
| Env | nothing | the image sees `RUNPOD_ENDPOINT_ID` and runs the handler |

Copy the **endpoint id**.

*For an always-on pod instead:* deploy the same image as a Pod, set
`REMOTE_VOICE_TOKEN=<a long random string>` (the mode is derived),
expose port 8001, and use the proxy URL RunPod gives you as
`REMOTE_VOICE_URL`.

**Two things about that pod are the whole of what goes wrong** (PROBLEMS.md
§78), and neither is visible from the app's side:

* **The mode used to be a variable, and forgetting it is what broke this.**
  The image defaulted to `serverless`, which runs `handler.py` and opens *no
  port at all* - so the proxy URL answered 404 on every path, including
  `/health`, and the app's log read exactly like a worker with a missing
  route. `voice_worker/start.py` now derives it: `RUNPOD_ENDPOINT_ID` means a
  Serverless worker, `RUNPOD_POD_ID` alone means a pod and a port, and
  `VOICE_WORKER_MODE` overrides both. **An image built before that change
  still needs `VOICE_WORKER_MODE=http` on a pod**, and the first line of the
  pod's log now says which half is running and why.
* **The port in the proxy URL must be the port the worker listens on.** The
  container serves `${PORT:-8001}`, so a pod exposing 8002 needs `PORT=8002`
  in its environment as well. `https://<pod>-8002.proxy.runpod.net` with
  uvicorn on 8001 is a 404 from RunPod's proxy, not from the worker.

## The address is found, not typed

Everything above is the *once, ever* setup. What made this a day of work each
time was not the setup - it was that every later change on RunPod needed a
human to put two systems back into agreement:

    pod migrates -> its address changes -> Render still points at the old one
    -> 404 mid-episode -> read logs, edit a dashboard, redeploy

`voice_control.py` removes the humans from all but the first arrow. It walks a
**ladder** - one definition, in `voice_control.ladder()`, which the runtime,
`/api/health`, the startup log and `tools/voice_doctor.py` all read rather than
keeping copies of:

| | Rung | What it is | Survives |
|---|---|---|---|
| 1 | `pinned` | `REMOTE_VOICE_URL` | nothing - it is a fact written down twice |
| 2 | `registered` | a worker that said where it is | a pod being replaced |
| 3 | `runpod-pod` | pods on the account, matched by **name**, resolved through RunPod's API | a pod being recreated, and a worker that cannot reach us |
| 4 | `serverless` | `RUNPOD_ENDPOINT_ID` | everything; it is cold, not absent |

A rung is used because a real call to it came back correct - a worker's
`/health`, or RunPod's own endpoint health - and that answer is held for
`VOICE_VERIFY_TTL`, so **the synth path pays nothing** on a healthy deployment.
`supervise_forever()` re-checks on a timer, so a pod that died at 3am is known
at 3am and the switch has already happened by the time somebody asks for an
episode.

**Failing over is not falling back.** Every rung is the same `Dockerfile.voice`
image, the same weights and the same `reference_3.wav`; a candidate whose
`/health` reports a sample rate this app has not already written into the
stream header is *refused* rather than used. What moves is the address. When no
rung can speak the episode fails with the reason attached, exactly as before -
and every switch is recorded, on `/api/health` and in the log, because §109's
rule holds here too: never fall back silently.

### The rung that makes a replaced pod free: the worker registers itself

Only the pod can know where it is. RunPod puts its id in `RUNPOD_POD_ID` inside
the container and fronts each exposed port at
`https://<pod id>-<port>.proxy.runpod.net`, so the address is derivable there
and guessable nowhere else. Three variables on the pod, and it introduces
itself on boot and every minute after:

    FAM_APP_URL=https://<your Render service>
    VOICE_REGISTRY_TOKEN=<the same long random string the app has>
    PUBLIC_WORKER_URL=<only off RunPod>

and one on the app:

    VOICE_REGISTRY_TOKEN=<the same string>

**Unset means registration is refused, not open.** An endpoint that accepts
"the voice is at this URL" from anybody redirects every script FAM writes to a
machine of their choosing, and it would look exactly like the feature working.
A registration is also only ever a *claim*: it makes a candidate, and the
candidate is verified with a real call before a listener is sent to it.

A registration expires (`VOICE_REGISTRY_TTL`, five missed heartbeats), which is
what makes a pod that was destroyed stop being offered without anything having
to notice that it died.

### Or, with nothing on the pod at all

    RUNPOD_POD=fam-voice          # the pod's NAME, not its id
    RUNPOD_API_KEY=...            # already set for the serverless transport

FAM asks RunPod where that pod is and builds the proxy URL itself. Matched on
name because a pod that is destroyed and recreated from the same template keeps
its name and loses its id - and a pod that is *stopped* is reported as such
rather than skipped in silence, which matters here because
`.github/workflows/runpod-schedule.yml` stops this project's pod every night:
"the voice cannot be found" and "the voice is asleep until 08:00" are different
problems and now read differently.

### One command when something is wrong

    python tools/voice_doctor.py            # the whole chain, on one screen
    python tools/voice_doctor.py --speak    # and make it produce real audio
    python tools/voice_doctor.py --json     # for a script or a CI step

It asks every question in the chain, in order - what this app is configured to
do, how it will look for a worker, what that search finds, whether each
candidate is really there, which build is answering, and whether it can
actually speak - and prints the fix beside each failure. Exit 0 speaking, 1
found but unable, 2 nothing found, 3 not configured for a remote voice.

The line it exists to print is this one:

    ! REMOTE_VOICE_URL names https://old-8001.proxy.runpod.net, but the worker
      that is announcing itself is at https://new-8001.proxy.runpod.net

### And the image builds itself

`.github/workflows/voice-worker.yml` checks that the two halves still agree
about the contract on every push that touches the worker, and builds and
pushes `Dockerfile.voice` to GHCR tagged with the commit. The build is opt-in
(`BUILD_VOICE_IMAGE=true`, or one click in the Actions tab) because a CUDA
image is ~10 GB.

It deliberately does **not** deploy. A workflow that can replace the running
voice on a push is a workflow that can take the voice down on a typo.

## When it answers 404

    POST https://<pod>-8002.proxy.runpod.net/synth -> 404 Not Found
    remote voice synth returned HTTP 404

A 404 means nothing was home at that address, and it cannot say which half of
the address was wrong. One command answers it, from anywhere that can reach the
pod:

    REMOTE_VOICE_TOKEN=... python tools/probe_remote_voice.py \
        --url https://<pod>-<port>.proxy.runpod.net

It asks `/health`, prints **every route the running image serves** from
`/openapi.json` - which is the authority on the route name, not this repo,
since the image may be a different version of it - and then makes it speak.
Exit 0 only when real audio came back. The three outcomes:

| What it prints | What is wrong | Fix |
|---|---|---|
| `POST /synth 200: ... bytes` | nothing; the voice works | - |
| routes listed, but no `/synth` | the image serves a different route | rebuild from `Dockerfile.voice`; the app retries at the route the worker names, at the cost of one extra request per chunk |
| `Nothing at ... is a FAM voice worker` | wrong port, or an old image running the serverless handler | set `PORT` to the exposed port; rebuild from `Dockerfile.voice`, or set `VOICE_WORKER_MODE=http` on the pod |

The pod's own log now names both facts at boot, so the same question can be
answered from RunPod's console without a probe:

    voice_worker serving on port 8001: GET /health, POST /synth, ...

### 4. Point Render at it

`render.yaml` already declares these; set the two `sync: false` values in the
Render dashboard (**Environment**):

    VOICE_BACKEND=remote
    REMOTE_VOICE_TRANSPORT=runpod
    RUNPOD_ENDPOINT_ID=<from step 3>
    RUNPOD_API_KEY=<a RunPod API key>
    REMOTE_VOICE_SAMPLE_RATE=24000

And, so that this is the last time an address is typed anywhere:

    RUNPOD_POD=<the pod's name>      # or VOICE_REGISTRY_TOKEN, or both
    VOICE_REGISTRY_TOKEN=<a long random string, also set on the pod>

`RUNPOD_API_KEY` goes through the same credential chain as everything else, so
`FAM_SECRETS` works instead and is better: rotating the key stops being an edit
to a dashboard. See `CREDENTIALS.md`.

Render redeploys on push, so there is nothing else to do.

## Before you trust it

    python tools/demo_preflight.py     # names which of the four are missing
    python verify_voice.py             # actually synthesises

`verify_voice.py` is the one that matters, because it performs the real action
rather than confirming a variable is set — the distinction PROBLEMS.md §52 is
about. From the Render service, `GET /api/health` reports the same thing:

    "tts": {
      "backend": "remote",
      "selected": "remote",
      "interim": false,
      "remote": {
        "configured": true,
        "transport": "runpod",
        "endpoint": "https://api.runpod.ai/v2/<id>",
        "reachable": {"state": "ok", "latency_seconds": 2.1}
      }
    }

**`configured` and `reachable` are different questions and are reported
separately.** `configured: true, reachable: unknown` means nothing has ever
actually spoken; `reachable: failed` carries the reason. A health check that
only read configuration would answer the cheaper question and say OK.

## The cold start, and what is done about it

A serverless worker at zero pays container boot plus a ~10s model load before
its first word. That is exactly the wait the one-sentence spec refuses.

It is answered the way CLAUDE.md says to answer latency — **by starting
earlier, not by filling the gap**. When a request arrives, `app.py` fires
`remote_voice.wake()`: a throwaway job that boots a worker and loads the model,
sent *before Claude has written a word*. The script takes several seconds to
write, and the worker boots during them.

The wake is a hint, not a mechanism. It never raises, never blocks, and never
delays a request; a miss costs only the cold start it was trying to hide. It
also will not stampede — a worker that is already booting does not boot faster
for being asked twice.

If measurement says the wake is not covering it, in order of preference:

1. **Raise the idle timeout.** The cheapest fix by far: it only bills while a
   worker is up, and a listener who plays two episodes gets the second warm.
2. **One active worker during peak hours.** This is a pod wearing a different
   hat — it is billed continuously — so price it as one.
3. **Switch to `REMOTE_VOICE_TRANSPORT=http`.** At that point you are paying
   for an always-on card and should compare against `Dockerfile.gpu` directly.

## What this does *not* do

**No fallback, ever.** A remote voice that fails raises with the reason
attached — it never becomes a local engine, a different voice, or silence. That
is PROBLEMS.md §61's second guard, re-added by hand as it said to. When nothing
can speak the honest states are still exactly two: Chatterbox speaks, or a
placeholder tone plays and everything says so.

**It is never the default.** `VOICE_BACKEND` defaults to `chatterbox` and
nothing auto-detects. Merely having a RunPod key in the environment does not
make a rented GPU what every listener gets — §61's first guard, which is how
WellSaid silently became the default voice on every machine without Piper.

**Nothing here starts, stops, resizes or pays for a pod.** `RUNPOD_PRODUCTION.md`
said that and it stays true - `.github/workflows/runpod-schedule.yml` is the
one thing that starts and stops one, on a clock somebody set. What *is*
automatic now is the **address**: FAM finds the worker wherever RunPod put it,
verifies it before using it, and switches when it stops answering. Which
machine exists, and what it costs, is still a decision somebody makes.

**Bandwidth is unchanged and still the thing that bites at scale**: 2.65 MB/min
per listener at 22050 Hz, more at Chatterbox's 24000. Opus over the stream is
the fix and is compatible with the no-audio-files rule — compression is fine,
writing a *file* is not.

## Going back to a single GPU box

Unset `VOICE_BACKEND` (or set it to `chatterbox`) and deploy `Dockerfile.gpu`
as `DEPLOY.md` describes. That is the whole procedure. Nothing about the
in-process path was removed, deprecated or altered to make room for this:
`Dockerfile.gpu`, `RUNPOD_PRODUCTION.md`, `tools/pack_for_pod.py`,
`tools/pod_production_test.sh`, `requirements-chatterbox.txt` and
`ChatterboxEngine` itself are all untouched, and
`tests/test_remote_voice.py::test_the_remote_voice_is_not_reachable_without_being_asked_for`
fails if the default ever drifts away from them.

This is a deliberate exception to the project's usual habit of deleting a thing
rather than switching it off. That rule exists for things that should not come
back — the cold open, Piper, WellSaid. A card of your own is where this is
going, so here the knob **is** the point.
