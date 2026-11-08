# 5. RunPod: the voice

FAM's voice is Chatterbox, which needs a GPU. Render has none, so the voice
runs on **RunPod Serverless**: a GPU worker that starts when FAM sends it a
sentence and stops after a few idle minutes. It is billed by the second it
runs. Full history and reasoning: `REMOTE_VOICE.md`.

| | |
|---|---|
| Endpoint | **`FAM`**, id **`g65qlmwz4sc02u`** |
| Built from | This repository, branch `Main`, `Dockerfile.voice`, rebuilt by RunPod on every push |
| Network volume | `safe_peach_goat_volume` (EU-RO-1): the voice recording `reference_3.wav`, its rights record, and the model weights (`/runpod-volume/hf`) |
| GPUs | 24 GB, previous generation: RTX 4090, RTX A5000, RTX 3090 |
| Workers | 0 active (scales to zero), max 4, idle timeout **180 s**, FlashBoot on, execution timeout 600 s |
| On Render | `VOICE_BACKEND=remote`, `REMOTE_VOICE_TRANSPORT=runpod`, `RUNPOD_ENDPOINT_ID`, `RUNPOD_API_KEY` |

**Nothing about the voice is meant to be done by hand** (owner's direction,
§318). The endpoint id never changes, RunPod rebuilds it from `Main`, and
the app emails `SUPPORT_EMAIL` within minutes if the voice goes down. Your
job is to notice the email and work through the checks below.

## Is it working?

From anywhere:

    curl -s https://<prod-host>/api/health | python3 -m json.tool | grep -A 25 '"tts"'

- `interim: false` (true means the placeholder tone is playing)
- `remote.reachable.state: ok`
- `control.held.rung: serverless`

From Render → `fam` → **Shell** (the most complete answer):

    python tools/voice_doctor.py            # walks the whole chain, prints the fix beside any failure
    python tools/voice_doctor.py --speak    # also makes real audio (may pay a cold start)

Exit code 0 means speaking; 1 found but unable; 2 nothing found; 3 not
configured.

## The RunPod console, tab by tab

runpod.io → **Serverless** → **FAM**:

| Tab | Look at it for |
|---|---|
| **Overview** | Workers running/idle, queue, recent failures |
| **Builds** | A build per push to `Main`. A failed build leaves the previous one serving. Any earlier build can be rolled back to from here. |
| **Requests** | Each job: *Delay Time* is waiting for a worker (the cold start), *Execution Time* is speaking. CANCELLED means the app gave up because nobody was listening (normal). |
| **Workers** | *Throttled* or none starting while jobs queue = no GPU free in the volume's data centre. |
| **Logs** | The worker's own output |
| **Manage → Edit Endpoint** | GPU types, max workers, idle timeout, FlashBoot, execution timeout, env vars (`FAM_VOICES_DIR=/runpod-volume`, `HF_HOME=/runpod-volume/hf`) |

**Test it directly** (Requests tab → run with this input):

    {"input": {"warm": true}}

should answer `"ready": true` with `"detail": "cuda, cloning reference_3.wav"`. Then

    {"input": {"text": "Testing the FAM voice."}}

should answer with audio.

## When episodes stall at "Generating the audio"

Work down this list (it is `REMOTE_VOICE.md` → "When episodes stall"):

1. **Idle timeout** reads 180 s (Edit Endpoint). RunPod's default of 5 s
   makes almost every episode meet a cold worker.
2. **Requests:** a delay of 60-90 s is a normal cold start; several minutes
   means no worker could start (next step).
3. **Workers / GPU supply:** throttled workers mean no card of the chosen
   types is free in EU-RO-1. Tick all three 24 GB types in GPU
   Configuration.
4. **A fresh build:** right after a merge, the first worker pulls the whole
   new image (minutes). It stops once one worker has booted on the build.
5. **FlashBoot** on, **Max workers** 4.
6. **Execution timeout** 600 s.

Then `/api/health` → `tts` as above, and `/admin` → errors.

## When the voice is completely down

1. Read the alert email and `/api/health` → `tts.remote.reachable` (it
   carries the reason).
2. RunPod → Builds: is the latest build failed? **Roll back to the last
   good build** from the Builds tab. Then find the PR that broke it and
   revert it ([02_GITHUB.md](02_GITHUB.md#undo-a-merge-revert)), or RunPod
   will build the broken code again on the next push.
3. RunPod → Billing: has the account run out of credit? **TODO(owner):**
   set up auto-pay or a low-balance alert, and say here who tops it up.
4. RunPod status page (status.runpod.io) for an outage.
5. Check Render still has `RUNPOD_ENDPOINT_ID` and `RUNPOD_API_KEY` set, and
   that nobody re-added `REMOTE_VOICE_URL` or `RUNPOD_POD` (the ladder tries
   those first).

While the voice is down, FAM does **not** fall back to another voice (a
settled rule). Episodes fail with a sentence that says so. That is
intended. Do not try to switch FAM to a different voice.

## What not to touch

- **The network volume's files.** `reference_3.wav` and
  `reference_3.rights.json` are a real person's voice under a signed
  consent. Never copy them anywhere else, and never replace them.
- **`VOICE_REFERENCE_FINGERPRINT`**, if set on Render, pins that recording.
- **Active workers** above 0 costs about $343 a month per worker. It is
  the owner's call (it removes cold starts).
- **Old pods.** The always-on pod setup in `REMOTE_VOICE.md` is history and
  the fallback. Do not start a pod unless the owner asks.

## Cost

Billed per second while a worker runs (speaking or idle within the 180 s).
RunPod → Billing shows the daily spend. FAM's per-listener view of voice
cost is in `python tools/usage_report.py`. Expected figures:
`docs/FINANCIAL.md`.
