# Kept audio in Cloudflare R2

PROBLEMS.md §235. Kept audio is packed as Opus at 24 kbps and, with
`AUDIO_STORE=r2`, lives in a Cloudflare R2 bucket. `scripts.db` keeps each
object's name and a hot copy of recently played episodes. Until `AUDIO_STORE`
is set, everything stays in `scripts.db` as before, still packed as Opus.

## What is kept, and for how long

| Audio | Where | How long |
|---|---|---|
| Every episode, first week | `recent/` (Standard) | 7 days (`AUDIO_RECENT_DAYS`); the bucket deletes it at 8 as a backstop |
| Saved, shared or vibed, after a week | `kept/` (Infrequent Access) | Until nobody holds it, checked daily |
| Everything else, after a week | - | Deleted |

The script is not affected: a script lives by its own rules, and a kept
episode's script is pinned for as long as its audio is kept.

## Setting it up (once)

1. **Create the bucket**, near the Render service (Oregon by default):

       npx wrangler r2 bucket create fam-audio --location wnam

2. **Apply the lifecycle rules** (these replace any rules already on it):

       npx wrangler r2 bucket lifecycle set fam-audio --file deploy/r2-lifecycle.json
       npx wrangler r2 bucket lifecycle list fam-audio

3. **Make an API token** in the Cloudflare dashboard: R2 → Manage R2 API
   tokens → Create, permission *Object Read & Write*, scoped to `fam-audio`
   only. Keep the access key id and secret; the account id is on the R2
   overview page.

4. **Verify from your machine** before the service sees it:

       pip install -r requirements.txt
       AUDIO_STORE=r2 AUDIO_BUCKET=fam-audio R2_ACCOUNT_ID=<id> \
       R2_ACCESS_KEY_ID=<key> R2_SECRET_ACCESS_KEY=<secret> \
       python tools/verify_audio_store.py

   It writes, reads, copies to Infrequent Access, and deletes a real object,
   timing each step. Every line must say `ok`.

5. **Turn it on for production only.** In the Render dashboard for `fam`
   (never `fam-staging`), set `AUDIO_STORE=r2`, `AUDIO_BUCKET=fam-audio`,
   `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` (or the two
   keys through `FAM_SECRETS`). Redeploy.

6. **Check `/api/health` → `audio`**: `store: r2`, `codec: opus`,
   `check.ok: true`, and after an hour a `sweep` with an `at`. Existing audio
   is uploaded 200 rows per hourly sweep (`sweep.uploaded`).

## Turning it off

`AUDIO_STORE=` (empty) stops new uploads; rows that have a hot copy keep
playing, and rows whose only copy is in the bucket are voiced again on their
next play. `AUDIO_CODEC=zlib` restores §132's packing for new audio; Opus rows
already written keep playing.

## What it costs

At 0.36 MB per two-minute episode, 10,000 new episodes a day kept a week is
about 25 GB in `recent/`, roughly $0.38 a month, with uploads and reads inside
R2's free operations at that volume. Reading back out is free. Kept audio costs
$0.01 per GB a month, with a 30-day minimum per object. Prices are list prices;
check Cloudflare's R2 pricing page.
