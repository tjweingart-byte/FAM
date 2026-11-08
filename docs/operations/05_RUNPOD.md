# 5. RunPod: the voice

FAM's voice needs a graphics card (GPU). Render has none, so the voice runs
on **RunPod**, which rents GPUs by the second. When a listener plays an
episode, FAM sends each sentence to RunPod and gets audio back.

| | |
|---|---|
| Where | `https://www.runpod.io/console/serverless` → the endpoint called **FAM** (id `g65qlmwz4sc02u`) |
| How it updates | RunPod rebuilds it from `Main` **by itself** after every merge into `Main` |
| When it costs money | Only while a worker is running: speaking, or idle for up to 3 minutes after |
| Alerts | FAM emails `SUPPORT_EMAIL` within minutes if the voice stops, daily while it is down, and when it is back |

The owner's standing instruction: **nothing about the voice should need
doing by hand.** Day to day, you only look at it when something is wrong.

---

## Task 1. Check the voice is working

1. Open `<production address>/api/health`. Find (Ctrl+F) `"interim"`.

   > **Check:** `"interim": false`. (`true` means listeners hear a
   > placeholder beeping tone instead of the voice.)

2. Find `"reachable"` in the `"tts"` section.

   > **Check:** `"state": "ok"`. (Anything else is followed by a reason in
   > plain words. Copy it for the next task.)

3. Play a new episode in the production app.

   > **Check:** it speaks in the normal voice. The very first episode after
   > a quiet spell can take 60-90 seconds to start (the GPU is waking up).
   > That is normal.

**The full test**, from the Render Shell ([04_RENDER.md → Task 5](04_RENDER.md#using-the-shell)):

    python tools/voice_doctor.py

> **Check:** it ends by saying the voice is speaking. If anything is broken
> it prints the fix beside it, in plain words.

---

## Task 2. Episodes are stuck on "Generating the audio", or very slow to start

Open RunPod → **Serverless** → **FAM**, and go down this list. Stop at the
first thing that is wrong.

1. **Is a new version being built?** Click **Builds**.

   > **Check:** the newest build is **Completed** and older than 15 minutes.
   >
   > **If not:** a build that is in progress, or just finished, means the
   > first worker must download the new version, which takes several
   > minutes. **Wait 15 minutes and test again.** This is normal after any
   > merge into `Main`.

2. **Are jobs waiting a long time?** Click **Requests**. Open the most recent few.

   > **Check:** **Delay Time** is under about 90 seconds (a GPU waking up).
   >
   > **If not:** several minutes means no GPU could start. Go to step 3.

3. **Are GPUs available?** Click **Workers**.

   > **Check:** workers show as running or idle, not **Throttled**.
   >
   > **If not:** RunPod has no free GPUs of our type right now. Click
   > **Manage** → **Edit Endpoint** → **GPU Configuration** and make sure
   > all three 24 GB types are ticked: **RTX 4090, RTX A5000, RTX 3090**.
   > Save. If they were already ticked, it is a RunPod shortage. Wait, and
   > tell the owner if it lasts over an hour.

4. **Are the settings right?** **Manage** → **Edit Endpoint**. Compare:

   | Setting | Should be |
   |---|---|
   | Active workers | `0` |
   | Max workers | `4` |
   | Idle timeout | `180` seconds |
   | FlashBoot | On |
   | Execution timeout | `600` seconds |

   > **If one is different:** change it back and **Save**. (Idle timeout at 5,
   > RunPod's default, is the usual culprit: the GPU goes to sleep seconds
   > after waking.) Tell the owner what you found.

5. **Has the account run out of money?** Click **Billing** (left menu).

   > **Check:** the balance is positive.
   >
   > **If not:** tell the owner straight away. **TODO(owner):** set up
   > auto-pay or a low-balance alert, and say here who tops it up.

6. Test again ([Task 1](#task-1-check-the-voice-is-working)).

---

## Task 3. The voice is completely down

You got the "voice down" email, or `"interim": true`.

1. Do **[Task 2](#task-2-episodes-are-stuck-on-generating-the-audio-or-very-slow-to-start)**,
   steps 1-5.
2. **Did the latest build fail?** RunPod → FAM → **Builds**.

   > **If the newest build says Failed:** RunPod keeps running the previous
   > build, so this alone does not stop the voice. But if the voice *is*
   > down and the last build is the newest Completed one, roll back: click
   > the **previous** good build → **Rollback** (or **Deploy this build**).
   > Then find the PR that changed the voice and revert it
   > ([02_GITHUB.md](02_GITHUB.md#undoing-a-change-revert)), or RunPod will
   > rebuild the broken version on the next merge.

3. **Is RunPod itself down?** Check `https://status.runpod.io`.
4. **Are the settings on Render still there?** Render → **fam** →
   **Environment**.

   > **Check:** `RUNPOD_ENDPOINT_ID` and `RUNPOD_API_KEY` both exist.
   > `VOICE_BACKEND` is `remote`. `REMOTE_VOICE_TRANSPORT` is `runpod`.
   > There is **no** `REMOTE_VOICE_URL` and **no** `RUNPOD_POD`.

5. Test RunPod directly. RunPod → FAM → **Requests** → **Run** (or the
   request box). Paste exactly:

       {"input": {"warm": true}}

   > **Check:** after up to 90 seconds, the answer contains `"ready": true`.
   >
   > **If it does:** RunPod is fine, and the problem is between FAM and
   > RunPod. Run `python tools/voice_doctor.py` in the Render Shell and
   > follow what it says.
   >
   > **If it does not:** the problem is inside RunPod. Copy the error from
   > the **Logs** tab and send it to the owner.

6. Down for more than an hour and none of this explains it: **call the owner.**

While the voice is down, FAM does **not** switch to another voice. Episodes
fail with a sentence saying so. That is deliberate. Do not try to set up a
different voice.

---

<a id="after-a-merge-to-main"></a>
## Task 4. After a merge to `Main` that changed the voice

If a batch included files in `voice_worker/`, `Dockerfile.voice`, `tts.py`
or `remote_voice.py`:

1. RunPod → FAM → **Builds**.

   > **Check:** a new build appears within a few minutes, and shows
   > **Completed** 10-20 minutes later.
   >
   > **If it shows Failed:** the old voice keeps working, so listeners are
   > fine. Copy the build log into a Claude session to fix.

2. Do [Task 1](#task-1-check-the-voice-is-working).

---

## Never

- **Touch the files on the network volume** (`reference_3.wav` and
  `reference_3.rights.json`). They are a real person's voice, used under a
  signed consent.
- **Raise Active workers above 0.** Each one costs about $343 a month. It is
  the owner's decision.
- **Start a "Pod".** The old always-on setup is history.

## Cost

RunPod → **Billing** shows daily spend. Compare it weekly. A big jump goes
to the owner.
