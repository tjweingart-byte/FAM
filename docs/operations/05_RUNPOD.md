# 5. RunPod: the voice

FAM's voice needs a graphics card (GPU). Render has none, so the voice runs
on **RunPod**, which rents GPUs by the second. When a listener plays an
episode, FAM sends each sentence to RunPod and gets audio back.

| | |
|---|---|
| Where | `https://console.runpod.io/serverless` → the endpoint called **FAM** (id `g65qlmwz4sc02u`). If you see other endpoints, leave them alone. |
| How it updates | RunPod rebuilds it from `Main` **by itself** after every merge into `Main`. It takes up to 30 minutes. |
| When it costs money | Only while a "worker" (a rented GPU) is running: while speaking, and for up to 3 minutes after |
| Alerts | FAM emails `SUPPORT_EMAIL` within minutes if the voice stops, daily while it is down, and when it is back |

The owner's standing instruction: **nothing about the voice should need
doing by hand.** Day to day, you only look at it when something is wrong.

---

## Task 1. Check the voice is working

1. Open `<production address>/api/health` in Firefox. Filter for `interim`
   (under `tts`) ([how](README.md#reading-the-health-page)).

   > **Check:** `false`. (`true` means listeners hear a placeholder beeping
   > tone instead of the voice.)

2. Filter for `reachable`. Take the one under `tts → remote` (the first
   match), and look at its `state`.

   > **Check:** `ok`.
   >
   > - `unknown` just after a deploy is normal: nothing has spoken yet. Play
   >   one episode (step 3), refresh, and look again.
   > - `failed` is a real problem. The reason is beside it, in `detail`.
   >   Copy it and go to [Task 2](#task-2-episodes-are-stuck-on-generating-the-audio-or-very-slow-to-start).

3. Play a new episode in the production app.

   > **Check:** it speaks in the normal voice. After a quiet spell, the first
   > episode can take 60-90 seconds to start while a GPU wakes up. That is
   > normal.
   >
   > **If not:** nothing after 2 minutes: go to [Task 2](#task-2-episodes-are-stuck-on-generating-the-audio-or-very-slow-to-start).

**The full test**, from the Render Shell ([04 → Task 5](04_RENDER.md#using-the-shell)).
`--speak` makes it produce real audio, which wakes a GPU (a few cents) and
can take 90 seconds:

    python tools/voice_doctor.py --speak

> **Check:** steps 1-5 say `ok`, and step 6 says `ok … bytes, … s of audio
> at 24000 Hz`. If anything is broken it prints the fix beside it, in plain
> words.

---

## Task 2. Episodes are stuck on "Generating the audio", or very slow to start

**First:** was there a merge into `Main` in the last 30 minutes? Then the
voice is probably still being rebuilt. **Wait until 30 minutes after the
merge** and test again before doing anything else.

Then open RunPod → **Serverless** → **FAM** and go down this list. Stop at
the first thing that is wrong.

1. **Is a new version still being built?** Click the **Builds** tab.

   > **Check:** the newest build says **Completed**.
   >
   > **If not:** **Building** means wait (up to 30 minutes after the merge).
   > **Failed** means the previous build is still in use, so this is not the
   > cause. Go on to step 2.

2. **Are requests waiting a long time?** Click the **Requests** tab. Click
   the most recent few.

   > **Check:** **Delay Time** is under about 90 seconds (a GPU waking up).
   >
   > **If not:** several minutes means no GPU could start. Go to step 3.

3. **Are GPUs available?** Click the **Workers** tab.

   > **Check:** workers show as running or idle, not **Throttled**.
   >
   > **If not:** RunPod has no free GPUs of our type right now. Click
   > **Manage** → **Edit Endpoint** → **GPU Configuration**. Make sure all
   > three 24 GB types are ticked: **RTX 4090, RTX A5000, RTX 3090**.
   > **Only if one was unticked:** tick it and **Save**. Saving restarts the
   > voice for about a minute. If all were already ticked, it is a RunPod
   > shortage. Wait, and tell the owner if it lasts over an hour.

4. **Are the settings right?** **Manage** → **Edit Endpoint**. Compare:

   | Setting | Should be |
   |---|---|
   | Active workers | `0` |
   | Max workers | `4` |
   | Idle timeout | `180` seconds |
   | FlashBoot | On |
   | Execution timeout | `600` seconds |

   > **If one is different:** change it back and **Save** (the voice
   > restarts for about a minute). Tell the owner what you found. Idle timeout
   > at `5`, RunPod's default, is the usual culprit: the GPU goes back to
   > sleep seconds after waking.

5. **Has the account run out of money?** Click **Billing** in the left menu.

   > **Check:** the balance is positive.
   >
   > **If not:** tell the owner straight away. **TODO(owner):** set up
   > auto-pay or a low-balance alert, and say here who tops it up.
   > (If you cannot open Billing, your role does not allow it: see 01.)

6. Test again ([Task 1](#task-1-check-the-voice-is-working)).

---

## Task 3. The voice is completely down

You got the "voice down" email, or `tts → interim` is `true`.

1. Do **[Task 2](#task-2-episodes-are-stuck-on-generating-the-audio-or-very-slow-to-start)**,
   steps 1-5.
2. **Did a new build break it?** RunPod → FAM → **Builds**. There are two
   cases:
   - **The newest build says Failed.** RunPod is still using the build before
     it, so this did not break the voice. Go on to step 3.
   - **The newest build says Completed, and the voice broke right after it
     finished.** Click the build **before** it → **Rollback** (or **Deploy
     this build**).

     > **Check:** after a few minutes, the voice works again (Task 1).
     > Then find the pull request that changed the voice and revert it
     > ([02 → Undoing a change](02_GITHUB.md#undoing-a-change-revert)).
     > Otherwise RunPod rebuilds the broken version at the next merge.

3. **Is RunPod itself down?** Look at `https://status.runpod.io`.
4. **Are the settings on Render still there?** Render → **fam** →
   **Environment**.

   > **Check:** `RUNPOD_ENDPOINT_ID` and `RUNPOD_API_KEY` both exist.
   > `VOICE_BACKEND` is `remote`. `REMOTE_VOICE_TRANSPORT` is `runpod`.
   > There is **no** `REMOTE_VOICE_URL` and **no** `RUNPOD_POD`. (The file
   > `render.yaml` still lists `RUNPOD_POD`. Ignore that; it is out of date.)
   >
   > **If not:** do not change or delete anything here yourself. Tell the
   > owner exactly what you see.

5. **Test RunPod directly.** RunPod → FAM → **Requests** tab. Find the box
   for sending a test request (it may be labelled **Run**, **Test** or
   **Request**). Paste exactly this, then send it. It costs a few cents:

       {"input": {"warm": true}}

   > **Check:** within 90 seconds the answer contains `"ready": true`.
   >
   > - **It does:** RunPod is fine, and the problem is between FAM and
   >   RunPod. Run `python tools/voice_doctor.py` in the Render Shell. If it
   >   tells you to change a setting, change only that one
   >   ([04 → Task 4](04_RENDER.md#task-4-change-a-setting-environment-variable))
   >   and tell the owner.
   > - **It does not:** the problem is inside RunPod. Open the **Logs** tab,
   >   copy the last lines, and send them to the owner.

6. Still down after an hour, and none of this explains it: **call the
   owner.**

While the voice is down, FAM does **not** switch to another voice. Episodes
fail with a sentence saying so. That is deliberate. Do not try to set up a
different voice.

---

<a id="after-a-merge-to-main"></a>
## Task 4. After a merge into `Main`

RunPod rebuilds the voice after every merge into `Main`, even when the voice
did not change.

1. RunPod → FAM → **Builds**.

   > **Check:** a new build appears within a few minutes, and says
   > **Completed** within 30 minutes of the merge.
   >
   > **If not:** **Failed** means the old voice keeps working, so listeners
   > are fine. Copy the build log into a Claude session to find out why.

2. Do [Task 1](#task-1-check-the-voice-is-working).

---

## Never

- **Open or change the storage the voice uses** (RunPod → **Storage**, the
  "network volume"). It holds `reference_3.wav` and its consent record: a
  real person's voice, used under a signed consent. You never need to open
  it.
- **Raise Active workers above 0.** Each one costs about $343 a month. That
  is the owner's decision.
- **Start a "Pod".** The old always-on setup is history.

## Cost

RunPod → **Billing** shows daily spend. Look at it weekly
([10 → weekly](10_RUNBOOKS.md#routine-every-week-about-20-minutes)). A big
jump goes to the owner.
