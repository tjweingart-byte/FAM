# 6. Cloudflare: where audio files and pictures are kept

FAM keeps files in a Cloudflare storage service called **R2**, in a
"bucket" (a folder) named **`fam-audio`**:

| Folder in the bucket | What | Kept for |
|---|---|---|
| `recent/` | Every episode's audio | 7 days |
| `kept/` | Audio someone saved, shared or vibed | As long as someone holds it |
| `images/` | Every picture: profile photos, mix covers, category pictures | As long as FAM uses it |

FAM adds and deletes these files itself, every hour. **You never need to
touch the files.**

Why it matters: when a listener **replays** an episode, their phone fetches
the audio straight from Cloudflare (free). If that fails, FAM streams it
through Render instead (it still plays, but costs bandwidth).

---

## Task 1. Check it is working

1. Open `<production address>/api/health`. Find (Ctrl+F) `"audio"`.

   > **Check:** in that section:
   > - `"store": "r2"`
   > - `"ok": true` (under `"check"`)
   > - `"fallbacks"` (under `"links"`) is a small number compared with
   >   `"issued"`.
   >
   > **If `ok` is false:** go to [Task 3](#task-3-replace-the-cloudflare-key).
   > **If `fallbacks` is about the same as `issued`:** go to
   > [Task 2](#task-2-the-app-has-a-new-address-or-replays-fall-back-for-everyone).

2. In the app, replay something from your history.

   > **Check:** it plays straight away.

---

## Task 2. The app has a new address, or replays fall back for everyone

Cloudflare only lets the listed web addresses fetch files (this list is
called "CORS"). A new address must be added.

1. Open `https://dash.cloudflare.com` → **R2** (left menu) → click
   **fam-audio** → **Settings**.
2. Scroll to **CORS Policy** → **Edit**.
3. In `AllowedOrigins`, check every address the app is opened at is listed,
   exactly as the browser's address bar shows it, starting `https://`
   and with **no** `/` at the end. For example:

       "AllowedOrigins": ["https://familiarize.net", "https://www.familiarize.net", "https://fam.onrender.com"]

4. Add any missing one. Click **Save**.

   > **Check:** the policy saves without an error. After an hour of use,
   > `/api/health` → `"fallbacks"` stops rising with `"issued"`.

5. Ask a Claude session to make the same change to `deploy/r2-cors.json`,
   so the repository matches.

---

## Task 3. Replace the Cloudflare key

Only if the key leaked, or `/api/health` shows `"ok": false` with a reason
about access or permission.

1. Cloudflare → **R2** → **Manage R2 API Tokens** (or **API Tokens**) →
   **Create API Token**.
2. Permissions: **Object Read & Write**. Under bucket scope choose **Apply to
   specific buckets only** → **fam-audio**. Click **Create**.

   > **Check:** you are shown an **Access Key ID** and a **Secret Access
   > Key**. They are only shown once. Keep this page open.

3. Render → **fam** → **Environment** → **Edit**. Replace
   `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` with the two new values.
   **Save, rebuild, and deploy**.

   > **Check:** Events shows `Deploy live`.

4. Open `/api/health` → `"audio"`.

   > **Check:** `"ok": true`.

5. Back in Cloudflare → API tokens → the **old** token → **Delete**.

---

## Emergency switches

Use only if replays or pictures are broken for everyone, and tell the
owner. Set on Render **fam** → **Environment** ([how](04_RENDER.md#task-4-change-a-setting-environment-variable)).

| Problem | Set | Result |
|---|---|---|
| Replays fail for everyone | `AUDIO_LINKS` = `0` | Replays stream through Render. They work, but cost bandwidth. |

When fixed, delete the setting again (or set it back to `1`).

---

## Never

- Delete files or the bucket by hand. Deleting `images/` deletes everyone's
  profile photo.
- Make the bucket public.
- Give a key access to more than `fam-audio`.

## The domain

**TODO(owner):** is `familiarize.net` managed in Cloudflare (DNS)? If so,
list here what its records do (the app's address, the iOS app's link file,
email), and note that the operator should not change them.
