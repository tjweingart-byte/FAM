# 6. Cloudflare: where audio files and pictures are kept

FAM keeps files in a Cloudflare storage service called **R2**, in a
"bucket" (a storage folder) named **`fam-audio`**:

| Folder in the bucket | What | Kept for |
|---|---|---|
| `recent/` | Every episode's audio | 7 days |
| `kept/` | Audio someone saved, shared or vibed | As long as someone holds it |
| `images/` | Every picture: profile photos, mix covers, category pictures | As long as FAM uses it |

FAM adds and deletes these files itself, every hour. **You never need to
touch the files.**

Why it matters: when a listener **replays** an episode, their phone fetches
the audio straight from Cloudflare (free). If that fails, FAM streams it
through Render instead. It still plays, but Render charges for the
bandwidth.

---

## Task 1. Check it is working

1. Open `<production address>/api/health` in Firefox. In **Filter JSON**
   type `check`, and look at the one under `audio`
   ([how](README.md#reading-the-health-page)).

   > **Check:** `audio → check → ok` is `true`. (This is tested once, when the
   > server starts. A failure after that shows in step 2 instead.)
   >
   > **If not:** `false`, with a reason beside it in `why`. A reason about
   > access, permission or credentials: go to
   > [Task 3](#task-3-replace-the-cloudflare-key). Any other reason: copy it
   > into a Claude session with the "Find out what is wrong in production"
   > prompt ([03](03_CLAUDE.md#prompts-to-copy)).

2. Filter for `links` (under `audio`). Look at `issued` (replays sent to
   Cloudflare) and `fallbacks` (replays that fell back to Render).

   > **Check:** `fallbacks` is much smaller than `issued`.
   >
   > - Both under 10: there has not been enough use since the last deploy to
   >   judge. Replay something yourself and look again later.
   > - `fallbacks` about the same as `issued`: go to [Task 2](#task-2-the-app-has-a-new-address-or-replays-fall-back-for-everyone).

3. In the production app, replay an episode from your listening history.

   > **Check:** it starts playing straight away.

---

## Task 2. The app has a new address, or replays fall back for everyone

Cloudflare only lets listed web addresses fetch the files. This list is
called the **CORS policy**. Every address the app is opened at must be on
it.

The policy is a small block of text in a strict format, where one missing
comma breaks every replay. So let Claude write it:

1. In a Claude session: `The FAM app is now also opened at <new address>.
   Write the complete new CORS policy for the fam-audio R2 bucket, based on
   deploy/r2-cors.json, ready for me to paste into the Cloudflare dashboard.
   Also update deploy/r2-cors.json to match, and push.`
2. Copy the policy text it gives you.
3. Open `https://dash.cloudflare.com` → **R2** (left menu) → click
   **fam-audio** → **Settings** → scroll to **CORS Policy** → **Edit**.
4. Select all the text in the box, delete it, and paste the new policy.
   Click **Save**.

   > **Check:** it saves without an error.
   >
   > **If not:** an error means the text was not pasted whole. Click
   > **Cancel**: nothing has changed. Try again, or ask the owner.

5. Take Claude's branch through [02](02_GITHUB.md) as usual.

> **Check (an hour later):** on the health page, `audio → links →
> fallbacks` has stopped rising alongside `issued`.
>
> **If not:** use the emergency switch below, and tell the owner.

---

## Task 3. Replace the Cloudflare key

Only if the key leaked, or Task 1 showed a reason about access or
permission.

1. Cloudflare → **R2** → on the right, **Manage R2 API Tokens** (it may be
   labelled **Manage API tokens**) →
   **Create API Token**.
2. Name it `fam-production-<today's date>`. Permissions: **Object Read &
   Write**. Under which buckets: **Apply to specific buckets only** →
   **fam-audio**. Click **Create API Token**.

   > **Check:** you see an **Access Key ID** and a **Secret Access Key**.
   > They are shown only once. Keep this page open until step 3 is done.

3. Render → **fam** → **Environment** → **Edit**. Replace the values of
   **only these two**: `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`. Leave
   every other `R2_…` and `AUDIO_…` setting alone. **Save, rebuild, and
   deploy**.

   > **Check:** **Events** shows `Deploy live` within 15 minutes.

4. Health page → `audio → check → ok`.

   > **Check:** `true`.
   >
   > **If not:** the two values may have been swapped or cut short. Copy
   > them again from step 2 and repeat step 3.

5. Back in Cloudflare → **Manage R2 API Tokens** (or **Manage API tokens**) → the **old** token (the one
   not named with today's date) → **Delete**. Not sure which is old? Ask the
   owner.

---

## Emergency switches

You may use this without asking. Tell the owner afterwards. Set it on Render
**fam** → **Environment** ([how](04_RENDER.md#task-4-change-a-setting-environment-variable)).

| Problem | Set | Result |
|---|---|---|
| Replays fail for everyone | add `AUDIO_LINKS` with value `0` | Replays stream through Render. They work, but cost bandwidth. |

When the problem is fixed, **delete** the `AUDIO_LINKS` setting again.

---

## Never

- Delete files, or the bucket, by hand. Deleting `images/` deletes
  everyone's profile photo.
- Make the bucket public.
- Give a key access to more than `fam-audio`.

## The domain

**TODO(owner):** is `familiarize.net` managed in Cloudflare (**DNS**)? If
so, list here what its records do (the app's address, the iOS app's link
file, email), and note that the operator should not change them.
