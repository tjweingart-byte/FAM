# 6. Cloudflare: R2, where audio and pictures are kept

FAM keeps two kinds of file in a Cloudflare **R2** bucket called
**`fam-audio`**: the audio of every episode for a week (longer if someone
saved, shared or vibed it), and every picture (profile and group photos, mix
covers, category pictures). Full setup and reasoning: `AUDIO_STORE.md`.

The database keeps only each file's *name*. The bytes are only in R2.

## The layout

| Prefix | What | How long |
|---|---|---|
| `recent/` | Every episode's audio (Opus, 24 kbps), Standard storage | 7 days. A bucket lifecycle rule deletes at 8 days as a backstop. |
| `kept/` | Audio someone saved, shared or vibed, after its first week (Infrequent Access) | Until nobody holds it. FAM checks daily and deletes. |
| `images/` | Every picture | Until no row names it. FAM deletes it. No lifecycle rule touches this prefix. |

The lifecycle rules are in `deploy/r2-lifecycle.json`; the browser access
rule (CORS) is `deploy/r2-cors.json`.

## On Render

`fam` (production only, never staging) has `AUDIO_STORE=r2`,
`AUDIO_BUCKET=fam-audio`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`.

## Is it working?

    curl -s https://<prod-host>/api/health | python3 -m json.tool | grep -A 30 '"audio"'

- `store: r2`, `codec: opus`, `check.ok: true`
- `sweep.at` within the last hour or so (the hourly sweep ran)
- `links.issued` growing, `links.fallbacks` near 0. **If fallbacks match
  issued, the CORS policy is missing an address** (below).

## How replays work, and why it matters

A first play streams through Render as it is voiced. A replay is a link,
signed for five minutes, straight to the file in R2. The phone reads it
directly. R2 charges nothing to send it; Render charges $0.15/GB. If links
stop working, the app streams through Render instead: replays keep
playing, but bandwidth costs rise.

## Common tasks

### Add an address the app is opened at (CORS)

Needed when the app gets a new domain. Without it, replays from that
address fall back to streaming.

Cloudflare dashboard → **R2** → `fam-audio` → **Settings** → **CORS
Policy** → edit. Add the address to `AllowedOrigins`, exactly as the address
bar shows it (`https://familiarize.net`, with no trailing slash). Update
`deploy/r2-cors.json` in a PR to match.

### Rotate the R2 key

1. Cloudflare → R2 → **Manage R2 API tokens** → **Create API token**:
   permission *Object Read & Write*, **scoped to `fam-audio` only**.
2. Render → `fam` → Environment: replace `R2_ACCESS_KEY_ID` and
   `R2_SECRET_ACCESS_KEY` → Save (redeploys).
3. Check `/api/health` → `audio.check.ok: true`.
4. Delete the old token in Cloudflare.

To test a key from your own computer before giving it to Render:

    AUDIO_STORE=r2 AUDIO_BUCKET=fam-audio R2_ACCOUNT_ID=<id> \
    R2_ACCESS_KEY_ID=<key> R2_SECRET_ACCESS_KEY=<secret> \
    python tools/verify_audio_store.py

Every line must say `ok`.

### Check the lifecycle rules are still there

    npx wrangler r2 bucket lifecycle list fam-audio

(Needs `npx wrangler login` once.) To restore them:
`npx wrangler r2 bucket lifecycle set fam-audio --file deploy/r2-lifecycle.json`.

## Emergency switches

| Problem | Switch on Render `fam` | Effect |
|---|---|---|
| Replay links fail for everyone | `AUDIO_LINKS=0` | Replays stream through Render (works; costs bandwidth) |
| R2 itself is failing | `AUDIO_STORE=` (empty) | New audio kept in the database again; episodes whose only copy is in R2 are voiced again when next played |

Both are temporary. Tell the owner, and undo them when R2 is back.

## Do not

- Delete objects or the bucket by hand. FAM tracks every object; deleting
  one makes an episode silently re-voice, and deleting `images/` loses
  every profile photo.
- Make the bucket public. Links are signed on purpose.
- Give the R2 token more than `fam-audio`.

## The domain

**TODO(owner):** is `familiarize.net` registered or served through
Cloudflare (DNS, proxy)? If so, say what records exist (the app's address,
`/.well-known/apple-app-site-association` for the iOS app, email records
for `SUPPORT_EMAIL`), and that the operator should not change them without
the owner.

## Cost

Storage is cents a month at today's volume. Reads from Standard are free;
reads from `kept/` (Infrequent Access) cost $0.01/GB. Cloudflare dashboard
→ R2 → Overview shows storage and operations. `AUDIO_STORE.md` → "What it
costs".
