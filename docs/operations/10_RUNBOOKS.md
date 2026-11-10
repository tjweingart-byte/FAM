# 10. Runbooks: routine checks and emergencies

## Call the owner straight away if

- production is still down after a rollback;
- anything looks like listeners' data is lost or exposed;
- a deploy refuses to start because of the database;
- police, a court or a lawyer gets in touch;
- spending is climbing fast and you cannot find why.

**How to reach the owner in an emergency:** **TODO(owner):** phone number,
messaging app, and what is worth interrupting for.

## In any emergency

1. Write down the time, **with your time zone** (Render's logs use UTC), and
   what you saw. A screenshot is best.
2. Find the symptom below. Follow its steps in order, checking each one
   before the next.
3. Write down everything you changed, and when.
4. Send the owner a short note afterwards: what happened, what you did, and
   whether it is fixed.

**Symptoms:**
[The site is down](#the-site-is-down) ·
[A deploy failed](#a-deploy-failed) ·
[Beeping tone / stuck](#episodes-are-a-beeping-tone-or-stuck-on-generating-the-audio) ·
[Sample script / episodes fail](#every-episode-is-the-same-sample-or-new-episodes-fail) ·
[Vague episodes](#episodes-are-vague-out-of-date-or-say-they-found-nothing) ·
[Trending empty](#the-trending-row-is-empty) ·
[Accounts missing](#accounts-look-missing-or-sign-in-fails-for-everyone) ·
[Replays fail](#replays-will-not-play-but-new-episodes-do) ·
[CI red on Main](#ci-is-red-on-main) ·
[Key leaked](#a-key-has-leaked) ·
[Abuse report](#a-listener-reports-abuse) ·
[Costs jump](#costs-jump) ·
[Agent held or misbehaving](#an-agent-is-held-or-behaving-badly)

---

## Routine: every working day (about 10 minutes)

**1. Find the latest production code.** Open
`https://github.com/tjweingart-byte/fam`. Make sure the branch button (top
left of the file list) says `Main`. At the top right of the file list is a
7-character code, the latest commit. Write it down.

**2. The health page.** Open `<production address>/api/health` in Firefox.
Filter for each of these ([how](README.md#reading-the-health-page)):

| Filter for | Look under | Should say |
|---|---|---|
| `short` | `build` | the code from step 1 |
| `mode` | (top level) | `live` |
| `state` | `credentials` (not `credentials → secrets`) | `ok` |
| `reachable` | `database` | `true` |
| `ephemeral` | `storage` | `[]` (empty) |
| `interim` | `tts` | `false` |
| `check` | `audio` | `ok` is `true` |
| `archive` | `archive` | `enabled` is `true`, and `last_error` is empty |
| `lock` | `agents → lock` | `signed` is `true`, `held` is `{}` |

> **If any differs:** find that symptom in the list at the top of this page.
> **If `build → short` differs from step 1:** the latest merge has not
> deployed. Render → **fam** → **Events** says why
> ([04 Task 1](04_RENDER.md#task-1-watch-a-deploy)), and check Auto-Deploy
> ([04 Task 3 step 6](04_RENDER.md#roll-back-to-the-last-good-version)).

**3. Reports from listeners: answer within 24 hours** (Apple requires it).
`<production address>/admin` → in the menu (down the left side; in a narrow
window, behind the three-line button) → under
**Inbox**, **Reported content**.

> **Check:** the **Open** tab is empty. If not, answer each one now:
> [A listener reports abuse](#a-listener-reports-abuse).

**4. Errors and outside services.** Same menu → **Pipeline errors**, then
**Outside service requests**.

> **Check:** no error repeating many times; no service in red or at its
> limit. **If not:** [08 → Task 1](08_OTHER_SERVICES.md#task-1-find-which-service-is-failing).

**5. GitHub checks on `Main`.** Repository → **Actions** → **CI** → branch
`Main`.

> **Check:** the newest finished run has a green tick.

**6. Email.** Look for anything from FAM (sender: **TODO(owner)**), Render
or GitHub.

**7. Play two episodes** in the production app: one new search and one
replay from your history.

> **Check:** both speak in the real voice.

---

## Routine: every week (about 20 minutes)

1. **Spending.** Open the billing or usage page of Anthropic, RunPod,
   Render, Cloudflare and MongoDB Atlas. Write each week's figure in the
   spending log (**TODO(owner):** where it is, and the normal figure for
   each).

   > **Check:** each is close to last week. **Anything that has doubled goes
   > to the owner.**

2. **Heavy users.** Render → **fam** → **Shell**
   ([how](04_RENDER.md#using-the-shell)), and run:

       python tools/usage_report.py --days 7 --flagged

   > **Check:** read **Most expensive listeners**: the week's top spenders.
   > One account far above the rest is worth sending to the owner. (**Worth a
   > look**, further down, covers only the last hour. Its healthy answer is
   > `nobody crossed a threshold in the last hour`.)

3. **Waitlist.** `<production address>/admin/waitlist`.

   > **Check:** there is no line saying `… Viral Loops call(s) waiting`, or
   > the number is lower than last week.

4. **Open pull requests** on GitHub. Anything red, old, or waiting for you?
5. **Staging matches Main.** Open
   `https://github.com/tjweingart-byte/fam/compare/staging...Main`.

   > **Check:** `There isn't anything to compare`. If not, do
   > [02 → Step 6](02_GITHUB.md#step-6-bring-staging-level-with-main).

---

## The site is down

*The app will not load, and `/api/health` does not answer either.*

1. Check it is not you: try the address again, and on your phone with Wi-Fi
   off.
2. Render → **fam** → **Events**. Read the top few lines.
   - **A merge deployed shortly before it broke?** Probably that. Go to
     step 3.
   - **A setting was saved shortly before it broke?** Put that setting back
     as it was ([04 Task 4](04_RENDER.md#task-4-change-a-setting-environment-variable)).
     Then go to step 7.
   - **A deploy failed?** The old version should still be running, so that
     is not the cause. Go to step 4.
   - **Nothing recent?** Go to step 4.
3. **Roll back** ([04 Task 3](04_RENDER.md#roll-back-to-the-last-good-version),
   all six steps, including the revert and the Auto-Deploy check).

   > **Check:** within 5 minutes, `/api/health` loads and shows the older
   > code, and the app works.

4. Render → **fam** → **Logs**. Scroll to the bottom.

   > **Look for:** `Traceback`, `ERROR`, or anything about the database.
   > Copy the last 50 lines.

5. Is Render itself down? Look at `https://status.render.com`.
6. Render → click the database.

   > **Check:** its status is **Available**, and its storage is not full.

7. Still down: **call the owner** and send them the log lines.

---

## A deploy failed

Listeners are fine: the previous version keeps running. Follow
[04 → Task 2](04_RENDER.md#a-deploy-failed).

---

## Episodes are a beeping tone, or stuck on "Generating the audio"

The voice. **First:** was there a merge into `Main` in the last 30 minutes?
Then wait until 30 minutes have passed since the merge, and try again.

Then follow [05 → Task 2](05_RUNPOD.md#task-2-episodes-are-stuck-on-generating-the-audio-or-very-slow-to-start),
and [05 → Task 3](05_RUNPOD.md#task-3-the-voice-is-completely-down) if it
is completely down.

---

## Every episode is the same sample, or new episodes fail

These are two different problems. Both are about the Anthropic key.

**1. Look at the health page.** Filter for `mode`, then for `state` under
`credentials` (not under `credentials → secrets`).

| What you see | Means | Go to |
|---|---|---|
| `mode` = `demo`, `credentials → state` = `absent` | The `ANTHROPIC_API_KEY` setting is **empty**. Every episode is the built-in sample script. | step 2 |
| `mode` = `live`, `credentials → state` = `rejected` | The key is set, but the start-up check failed: usually Anthropic **refused** it, sometimes Anthropic was down at that moment (`detail` says which). New episodes fail. | step 3 |
| `mode` = `live`, `credentials → state` = `ok`, but episodes still fail | The key worked at start-up, and has since been disabled, run out of spend, or Anthropic is down | step 3 |

**2. The setting is empty.** Render → **fam** → **Environment**. Is
`ANTHROPIC_API_KEY` there?
- **It has gone:** someone deleted it. Look at **Events** for when. Tell the
  owner, then put a key back ([03 → Replace the key](03_CLAUDE.md#replace-the-key-only-if-it-leaked-or-the-owner-asks)).

**3. The key is refused.** Open `https://console.anthropic.com` (it may
forward you to `platform.claude.com`):
- **API Keys:** is the production key still **Active**?
- **Limits** and **Usage:** has the spend limit been reached?
- `https://status.anthropic.com`: is Anthropic down?

Then:
- **Anthropic is down:** wait. It recovers by itself.
- **Spend limit reached:** tell the owner. Raising it is their decision.
- **Key disabled or deleted:** replace it ([03 → Replace the key](03_CLAUDE.md#replace-the-key-only-if-it-leaked-or-the-owner-asks)).

> **Check:** `credentials → state` is `ok`, and a new search plays a real
> episode in the real voice.

---

## Episodes are vague, out of date, or say they found nothing

Research services. This lowers quality; it is not an outage. Follow
[08 → Tasks 1 and 2](08_OTHER_SERVICES.md#task-1-find-which-service-is-failing).
Look at **Perplexity** and **Parallel** first.

---

## The Trending row is empty

1. Trending is rebuilt at **05:00 and 17:00 Eastern**. A failed build is
   retried every 30 minutes, and the row keeps the last good edition for up
   to 36 hours. So an empty row is never just "waiting": something is wrong.
2. Health page → filter for `gnews`. Look under `trending_bank → gnews`.
   (Not the section called just `trending`, which is something else.)

   > **Check:** `ready` is `true`.
   >
   > **If not:** `detail` says why, in words (for example a key missing). Also
   > read `trending_bank → last_failure`. Then check the GNews account
   > ([08 → Task 2](08_OTHER_SERVICES.md#task-2-a-service-is-failing-what-to-check-at-the-vendor)).

3. After a fix, the row fills within about 30 minutes (straight after the
   redeploy if the key had been missing). There is no backup source, on
   purpose.

---

## Accounts look missing, or sign-in fails for everyone

**Serious. Do not change anything.**

1. Health page:

   > **Check:** `database → backend` is `postgres`, `database → reachable` is
   > `true`, `storage → ephemeral` is `[]`.

2. Render → **fam** → **Environment**.

   > **Check:** a setting named `DATABASE_URL` is there. (Do not open it or
   > change it.)

3. Render → **fam** → **Events**: did anyone change a setting recently?
4. Render → the database: is it **Available**? Any recent restore or change?
5. **Call the owner** with what you found, whatever it is. **Do not restore
   anything yourself**: a restore deletes everything written since the
   backup.

---

## Replays will not play, but new episodes do

Cloudflare R2. Follow [06 → Task 1](06_CLOUDFLARE.md#task-1-check-it-is-working).

If replays fail for everyone and you cannot fix it quickly, use the
[emergency switch](06_CLOUDFLARE.md#emergency-switches) and tell the owner.

---

## CI is red on Main

*The newest finished run of **CI** on `Main` has a red cross.*

1. Hold all other merges into `Main` until this is fixed.
2. Open the red run → top right, **Re-run jobs** → **Re-run failed jobs**,
   once.

   > **Check:** if it turns green after 35-60 minutes, it was a one-off.
   > Done.

3. Still red: find which merge broke it. In the list of runs (newest at the
   top), scroll down to the most recent **green** run. The red run just
   **above** it is the one that broke it. Its title names the pull request.
4. Open the red run → the job → the step with the red cross. Copy the red
   text (the last 100 lines if it is long).
5. Choose:
   - **That pull request obviously caused it:** revert it
     ([02 → Undoing a change](02_GITHUB.md#undoing-a-change-revert)).
   - **Not obvious:** start a Claude session with:

     ```
     GitHub's checks on Main have failed since <the 7-character code> with:
     <paste>
     Find the cause and fix it. The checks use Python 3.12, so reproduce it in
     3.12. Run ./dev.sh check, push, and tell me the branch name.
     ```

     Take the fix through the **normal route**
     ([02 → Steps 1-6](02_GITHUB.md#step-1-merge-a-feature-branch-into-staging)).
     Use the urgent route only if production itself is also broken.

   > **Check:** the next CI run on `Main` has a green tick.

---

## A key has leaked

*A key or password was pasted into a chat or email, committed to GitHub,
shown in a log, or seen by someone it should not be.*

**Do this straight away, without waiting for the owner**, for the keys
listed here:

1. **Make a new key and cancel the old one:**

   | Key | How |
   |---|---|
   | `ANTHROPIC_API_KEY` | [03 → Replace the key](03_CLAUDE.md#replace-the-key-only-if-it-leaked-or-the-owner-asks) |
   | `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | [06 → Task 3](06_CLOUDFLARE.md#task-3-replace-the-cloudflare-key) |
   | `MONGODB_URI` / `MONGODB_READ_URI` | [07 → Task 3](07_MONGODB.md#task-3-change-a-database-password) |
   | Research, news, sports, markets, weather, pictures | [08 → Task 3](08_OTHER_SERVICES.md#task-3-replace-a-services-key) |
   | `FAM_ADMIN_TOKEN` | Make a new random value with your password manager's generator (40 characters, letters and numbers only). Paste it as the new value on Render ([04 Task 4](04_RENDER.md#task-4-change-a-setting-environment-variable)). Production and staging have different tokens: change whichever leaked. |
   | **Anything else** (`RUNPOD_API_KEY`, `DATABASE_URL`, `VAPID_PRIVATE_KEY`, Apple keys, `SMTP_PASSWORD`, `VOICE_REGISTRY_TOKEN`, a GitHub or vendor password) | **Call the owner now.** Do not change these yourself. |

   > **Check:** the vendor shows the old key as disabled or deleted, and the
   > health page and `/admin` are healthy with the new one.

2. **If it was committed to GitHub:** replacing the key is the real fix.
   Deleting the commit is not enough, because it stays in GitHub's history.
   Then ask a Claude session to remove it from the files too. Tell it the
   **file name and the setting name, never the key itself**.
3. Tell the owner: which key, where it leaked, and when it was replaced.

---

## A listener reports abuse

1. `/admin` → menu → **Reported content**, **Open** tab. The oldest is at
   the top.
2. Read each report. For each one, choose one button (not every report
   shows all three: a report about a person or a group has no **Remove**):
   - **Remove**: takes the content down for everyone.
   - **Suspend**: removes it **and** stops that account posting anything.
   - **Dismiss**: leaves it up (it stays hidden from the person who reported
     it).

   > **Check:** the report moves to the **Answered** tab.

3. **Anything legal** (threats of harm, a court order, police, a copyright
   notice): **forward it to the owner the same day, and do not reply
   yourself.**

---

## Costs jump

1. Which service? Look at each billing page, and `/admin` → **Outside
   service requests**.
2. Render Shell: `python tools/usage_report.py --days 1 --flagged`

   > **Check:** under **Most expensive listeners**, is one or two accounts
   > far above the rest, or is everyone higher? (**Worth a look** shows only
   > the last hour's heavy users.)

3. **One or two accounts:** copy that part of the report and send it to the
   owner. The owner can move an account to the limited free plan.
4. **Everyone:** Render → **fam** → **Environment**: is `ENFORCE_QUOTAS`
   still `1`? Did a recent merge change something? Tell the owner either way.

---

## An agent is held, or behaving badly

- **Held** (`agents → lock → held` lists a role): see
  [09 → An agent is held](09_AGENT_RULE_SIGNOFF.md#an-agent-is-held-and-nobody-changed-anything).
  FAM works without it. Not an emergency.
- **Behaving badly** (wrong categories, good sentences dropped, wrong facts
  filed): write down three examples, with times. Send them to the owner. If
  it is harming episodes and the owner agrees, its switch on Render can turn
  it off (`0`). The switch names are in
  [04 → the agents table](04_RENDER.md#task-4-change-a-setting-environment-variable).
