# Running FAM as a service

This replaces the pod ritual — `pack_for_pod.py`, scp a tarball, extract, pip
install, export keys, run the harness. That loop is right for renting a card
for an hour to *measure* something. It is wrong for a service: every step is a
chance to set it up differently from last time, and none of it is recorded.

The split that makes it repeatable:

| baked into the image | set once on the host | never anywhere |
|---|---|---|
| CUDA, torch, chatterbox, exa, the app | `FAM_SECRETS` (or the two keys) | credentials in the image |
| the validated settings (phase6, exa) | the persistent volume | the voice in the repo |

**Prefer `FAM_SECRETS` to the two keys.** It is one *non-secret* variable that
names where the credentials live, so the host template carries no secret at all,
a new pod fetches its own, and rotating a key is a change in the manager rather
than an edit to every template. `CREDENTIALS.md` has the recipes.

## The one setting the image used to pin, and why it is gone

`ANSWER_FIRST=1`.

It turned on a from-knowledge cover: a second model call with no tools that
started speaking immediately while the research was still reading, handing
over mid-episode. The image pinned it because the configuration that had been
*listened to* ran with the cover on, and `config.py` had since started
deriving the value from the research backend - so an image without the line
would have deployed something nobody had heard.

What it actually deployed was an opening written with no brief, no evidence
and no idea what the episode was going to be about, in front of a researched
episode that was fine. That is precisely what came back from listening: the
first ten seconds make no sense, the rest is almost perfect.

The mechanism is deleted in PROBLEMS.md §108 - deleted rather than defaulted
off, because this line is the proof that a leftover knob gets turned back on.
A container from this image now resolves

    phase6 · exa · one stream, written after the evidence lands

and there is no setting here to get wrong.

## Once, ever

**1. Build and push the image.**

    docker build -f Dockerfile.gpu -t <registry>/fam:<tag> .
    docker push <registry>/fam:<tag>

**2. Create a persistent volume** and mount it at `/state`. On RunPod this is a
network volume attached to the template; on any other host it is a normal
volume. One mount, three trees:

    /state/hf       Chatterbox weights
    /state/voices   reference_3.wav + reference_3.rights.json
    /state/data     the eight SQLite stores

**3. Put the voice on the volume, once.** The engine refuses to speak without
both files — a cloned voice is somebody's voice:

    scp reference_3.wav        <host>:/state/voices/
    scp reference_3.rights.json <host>:/state/voices/

The rights record must clear three fields, each `"yes"`:

    {"consent": "yes", "commercial_use": "yes", "synthetic_voice_cleared": "yes"}

**4. Set the credentials on the template**, as environment variables. Not typed
into a shell on the pod - that is the step you are trying to stop repeating.

One line is enough, and it is not a secret:

    FAM_SECRETS=cmd:aws secretsmanager get-secret-value --secret-id fam --query SecretString --output text

The pod authenticates as itself - an IAM role, a service account - and fetches
`ANTHROPIC_API_KEY` and `EXA_API_KEY` at startup. Setting the two keys directly
still works and is one step simpler; what it costs is that rotating either one
means editing the template and restarting, where the provider is picked up by a
running server the next time a key is rejected. See `CREDENTIALS.md`.

## Every deploy after that

Start the container. That is the whole procedure. Nothing is installed,
nothing is uploaded, nothing is typed.

The first pod to run pays a one-time weight download into `/state/hf`; every
pod after that starts with them already there. That download is the
`up after 38s (warm-up included)` line in the pod logs — it is infrastructure
boot, not request latency, and the volume is what stops you paying it again.

## Before you trust it, one command

    python tools/demo_preflight.py

It reports all four ways this can look like it is working when it is not —
writing, speech, research, cache — and names which is missing. A deployment
that passes it will speak; one that does not will say why in a sentence.

`python verify_voice.py` goes further and actually synthesises, which is the
difference between "Chatterbox is installed" and "this machine can speak".

## "Everyone's accounts were erased when I deployed"

One command, which asks the running deployment and prints the fix:

    python tools/storage_doctor.py --url https://<your-host>

Exit 1 means at least one store will be erased by the next push. The answer
is almost always the first bullet below.

**The server now says it at boot too.** `_announce_storage` logs an error on
startup when a store is ephemeral *and* its environment variable is set -
that pair being the whole diagnosis, because it means this deployment asked
for a mounted disk and did not get one. It is deliberately silent on a
laptop, where nothing is configured and the databases sitting beside the code
is correct: a warning every developer sees on every run is a warning nobody
reads, which is how this went unnoticed while the measurement for it already
existed.

The long way round, which is what both of those read: `/api/health` reports
`storage`, and it is **measured** — a mounted volume is a different
filesystem, so a database on the same device as the application code is inside
the container image and goes when the image is replaced:

    curl -s https://<your-host>/api/health | python -m json.tool | grep -A 20 '"storage"'

`ephemeral` names every store that will not survive the next push, and `note`
says what to do. Two things it distinguishes that reading configuration cannot:

* **A store pointed at `/data` with no disk actually attached** reports
  `image`. This is the common case on Render and the likely cause of accounts
  disappearing: `render.yaml` declares the disk, but a blueprint only applies
  to a service *created from it*. A service made by hand in the dashboard has
  no disk however many times that file says it should, and nothing anywhere
  says so. Fix it under the service's **Disks** tab: add one, mount it at
  `/data`, redeploy. The first deploy after that starts empty one last time.
* **A store nobody remembered to pin.** Four were missing until §107 —
  messages, saved, shares and quotas — so conversations, saved episodes and
  share links were discarded on every push while accounts survived. They are
  in the `Dockerfile` now, and `tests/test_data_paths.py` derives its list from
  the code so a store added later cannot be left out quietly.

## Taking the demonstration data back out

`tools/seed_demo.py` writes three invented listeners and their plays so the
browse surfaces have something to show on a fresh install. Right while
*showing* the product, wrong while **measuring** it: every seeded play is a
vote in the taste model, so "is myFAM recommending the right things" has an
unknown share of its answer coming from people who do not exist.

    python tools/wipe_demo_data.py --url https://<your-host>            # dry run
    python tools/wipe_demo_data.py --url https://<your-host> --yes      # the seed
    python tools/wipe_demo_data.py --url https://<your-host> --all --yes

`--url` needs `FAM_ADMIN_TOKEN` set on the service and passed with `--token`
(or in your own environment). Without that variable the endpoint returns 404
rather than 401 - an unconfigured deployment should not advertise that it has
a delete endpoint at all.

Nothing happens without `--yes`, in both directions: the endpoint's own
`dry_run` defaults to true, so a request body that forgot a field cannot be
the one that empties the event log.

`--all` empties the whole script cache and the whole event log, not just the
seed. That is the true blank slate and it takes real listening with it. It
costs nothing that cannot be regenerated - a script is about three cents and
audio is never stored - and the taste model starts from nothing for
everybody. Neither scope touches accounts, credentials or the metering
ledger.

## Sharing needs no setting any more

`PUBLIC_BASE_URL` used to be the only way a share link could name a host, and
nothing prompted for it - so every link was `/s/abc123`, which is a correct
relative URL and a useless thing to send somebody. The host is read off the
request now (`X-Forwarded-Proto` and `X-Forwarded-Host`, which is what
Render's router sets), so sharing works on an unconfigured deployment.

Set `PUBLIC_BASE_URL` only when the host people reach is **not** the one this
service answers on - a custom domain in front of the Render URL. Check which
of the three states a deployment is in:

    curl -s https://<your-host>/api/health | python -m json.tool | grep -A 4 '"sharing"'

`link_host` is `env` (PUBLIC_BASE_URL), `request` (derived, the normal case)
or `none` (a loopback host, where there is no honest link to give).

## Renaming the Render URL

Render derives `<something>.onrender.com` from the **service name**, which was
taken from the repository when the service was created — hence
`search-no-mp3-prompt-to-text-to-audio.onrender.com`. It is not in any file
here; `render.yaml` names the service `fam`, but that only applies to a service
created *from the blueprint*, and renaming an existing one is a dashboard
action.

**Read the warning before doing it**, because this is the part that bites: the
old address stops resolving the moment the rename takes effect. There is no
redirect. Every `/s/<id>` share link anybody has already posted — in a message,
on a timeline, in a preview card a crawler has already cached — is built from
the old host and dies with it. That is the only real cost, and it is smallest
right now.

1. Render dashboard → the service → **Settings**.
2. **Name** → change it to `fam` → **Save**.
3. The URL becomes `https://fam.onrender.com` within a minute or so. Render
   refuses the name if another account already holds it — the subdomain is
   global, not per account — in which case pick something else here rather
   than working around it elsewhere.
4. Update anything that hard-codes the old host. In this repo that is
   `APP_STORE_URL`'s neighbours and any `FAM_PUBLIC_URL`-style setting on the
   service; `grep -ri onrender.com` over the repo is the check, and it should
   come back empty.
5. Re-check `/api/health` on the new address. It reports `build`, so this also
   confirms the rename did not quietly land you on a different deploy.

If the links already in the wild matter more than the name, the alternative is
a **custom domain** — Settings → Custom Domains — which Render serves
*alongside* the `onrender.com` one rather than instead of it. That is the
option that costs nobody a broken link, and it is what a real launch wants
anyway.

## What is deliberately still manual

**The voice and its rights record.** They are per-machine state and stay out
of the image on purpose: a cloned voice in a container registry is somebody's
voice in a container registry. Once on the volume, they persist.

**Starting and paying for the machine.** `RUNPOD_PRODUCTION.md` says nothing in
this repository starts, stops, resizes or pays for a pod, and that stays true.
This makes the machine reproducible; it does not make it automatic.

## Scaling past one box

The constraint to know before you plan around it: **Chatterbox runs in-process**,
so every replica needs a GPU, and a GPU left running is the expensive kind.

**That split is now built** — `REMOTE_VOICE.md`, PROBLEMS.md §75. The app runs
on cheap CPU hosting (Render) and only the voice is on a card, reached over
HTTP by `remote_voice.py`. It is one variable on each side, and going back to
the single-box deployment this file describes is unsetting `VOICE_BACKEND`.
Nothing below changed to make room for it.

Which one is right is a volume question, and the numbers are in `REMOTE_VOICE.md`:
below roughly four hours of audio a day a rented card billed by the second is
cheaper than one billed by the hour, and above it this file wins.

The other number that bites at scale is bandwidth: **2.65 MB/min uncompressed**
per listener. Opus over the stream is the fix and is compatible with the
no-audio-files constraint — compression is fine, writing a *file* is not.
