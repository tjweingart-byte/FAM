# 2. GitHub: getting a change to production, through staging

Everything here is done in the GitHub website. You do not need anything
installed. Repository: `https://github.com/tjweingart-byte/fam`

(New words such as *branch*, *commit*, *pull request*, *merge* and *checks*
are explained in [README → Words you will meet](README.md#words-you-will-meet).)

## Two words that matter on every pull request

A pull request copies changes **from** one branch **into** another. GitHub
shows this as a line at the top of the page:

```
base: staging  ←  compare: claude/some-change
```

- **base** = the branch being changed, where the work goes **to**.
- **compare** = where the work comes **from**.

> **The most important warning in this handbook:** when you start a new
> pull request, **GitHub sets base to `Main` by itself.** `Main` is
> production. If you forget to change it, Claude's untested change goes
> straight to listeners. **Read that line every time before you click
> Create.**

## The route every change takes

```
  claude/some-change  ──PR #1──►  staging  ──PR #2──►  Main  ──PR #3──►  staging
                                     │                   │
                              fam-staging updates   production updates
                              (you test here)       (listeners get it)
```

| Step | What | Base ← compare |
|---|---|---|
| 1 | Merge the feature branch into `staging` | `staging` ← `claude/…` |
| 2 | Watch `fam-staging` update | |
| 3 | Test on staging | |
| 4 | Open a pull request from `staging` into `Main` | `Main` ← `staging` |
| 5 | Merge it; watch production update; check it | |
| 6 | Bring `staging` level with `Main` again | `staging` ← `Main` |

Several feature branches can go through Step 1 before you do Steps 4-6
once for all of them. That is a "batch".

> **TODO(owner):** recent pull requests (#229-#231) went straight from
> `claude/...` into `Main`. This handbook uses the route above while the
> owner is away. Confirm.

### Always merge the same way

On every pull request, the green merge button has a small **▾** arrow beside
it with three choices. **Always choose "Create a merge commit".** Never
"Squash and merge", never "Rebase and merge". (The other two make `staging`
and `Main` look different even when they hold the same changes, and Step 6
then never comes out clean.)

---

## Before you start: three quick checks

**Check A. Is `Main` healthy?**
1. Open the repository → **Actions** tab (along the top).
2. On the left, click **CI**. Click the **Branch** filter and choose `Main`.
3. Look at the top run in the list.

> **Check:** it has a **green tick**.
>
> **If not:**
> - **Yellow dot** = still running. Wait, then look again.
> - **Grey circle** = cancelled (a newer run replaced it). Look at the next
>   one down.
> - **Red cross** = production's code is failing its tests. Do not merge
>   anything else. Go to [10 → CI is red on Main](10_RUNBOOKS.md#ci-is-red-on-main).

**Check B. Does `staging` already have everything `Main` has?**
Open `https://github.com/tjweingart-byte/fam/compare/staging...Main`

> **Check:** it says `There isn't anything to compare` (or
> `staging and Main are identical`).
>
> **If not:** it lists commits that are on `Main` but not on `staging`. Do
> [Step 6](#step-6-bring-staging-level-with-main) first, then come back.

**Check C. Is anything already waiting on `staging`?**
Open `https://github.com/tjweingart-byte/fam/compare/Main...staging`

> **Check:** `There isn't anything to compare`, or only commits named
> `Merge pull request … Main back into staging` (those are left over from
> last time and harmless).
>
> **If not:** other changes are already on staging and have not gone to
> production yet. Find out what they are (click them) before adding more. If
> they are from you or the owner and were tested, they go in this batch.

---

## Step 1. Merge a feature branch into `staging`

You need the branch name. A Claude session gives it at the end of its reply,
for example `claude/backend-operations-docs-jzbx0p`.

> **If not:** if the reply has no branch name, ask the session `Which branch
> did you push to?`

1. Open the repository. Click the **Pull requests** tab.
2. Click the green **New pull request** button.
3. **Change base first.** Click the **base: Main** button and choose
   **`staging`**.
4. Click **compare** and choose the feature branch (type part of its name
   to find it).

   > **Check:** the line reads `base: staging ← compare: claude/…`, and
   > below it `Able to merge` in green, then a list of commits and changed
   > files.
   >
   > **If not:**
   > - Base still says `Main`: change it to `staging` now.
   > - The branch is not in the list: Claude has not pushed it yet. Ask the
   >   session to push.
   > - `Can't automatically merge` (red): the branch clashes with `staging`.
   >   You can still create the pull request; then see
   >   [Conflicts](#this-branch-has-conflicts).
   > - `There isn't anything to compare`: this branch is already in
   >   `staging`. Nothing to do.

5. Click **Create pull request**.
6. **Title:** one sentence about what it does. **Description:** paste
   Claude's summary. Click **Create pull request** again.

   > **Check:** a page for the new pull request opens, with a number (for
   > example `#232`). Near the bottom, a box with a yellow dot says
   > `Some checks haven't completed yet`.

7. **Look at the change** while the checks run. Click the **Files
   changed** tab. You are not reviewing code. You are checking three things:
   - It only touches what was asked for. (A documentation change that also
     edits `app.py` is a question for Claude.)
   - It does **not** change any of the owner's files: anything in
     `docs/agent_rules/`, `docs/journalism_playbook.md`, `agents/rulebooks.py`,
     `tools/rulebook_manifest.py`, `tests/test_agent_rulebooks.py`,
     `.github/CODEOWNERS`, `.gitattributes`, `CLAUDE.md` or `docs/claude/`
     ([01 → Owner-only decisions](01_ACCESS.md#owner-only-decisions)).
     **One exception:** a rule-book *proposal* (see 09) adds a file in
     `docs/agent_rule_proposals/` and `docs/agent_rules/MANIFEST.pending.json`.
     That is expected.
   - Claude's reply said `all checks passed`. That is Claude's own test run on
     its own machine. GitHub's checks are a second, separate run.

   > **If any of those fail:** stop. Do not merge. Leave a comment on the
   > pull request (the box at the bottom of the **Conversation** tab) saying
   > what you saw, and ask Claude or the owner.

8. **Wait for the checks**, 35-60 minutes. Refresh the page now and then.

   > **Check:** the box at the bottom turns green: `All checks have passed`.
   >
   > **If not:** `Some checks were not successful` (red): do not merge. See
   > [Checks are red on a pull request](#checks-are-red-on-a-pull-request).

9. Click the **▾** beside the green button, choose **Create a merge
   commit**, then click **Merge pull request** → **Confirm merge**.

   > **Check:** a purple **Merged** badge appears at the top, and the
   > message `… merged commit abc1234 into staging`. Write down that
   > 7-character code (here `abc1234`).
   >
   > **If not:** if the message says **`into Main`**, the change has gone
   > to production untested. Go to
   > [Undoing a change → already in production](#the-change-is-already-in-production)
   > now, and tell the owner.

Repeat Step 1 for every feature branch in this batch.

---

## Step 2. Watch staging update

1. Open `https://dashboard.render.com` → click **fam-staging** → in the left
   menu click **Events**.

   > **Check:** within a few minutes a line `Deploy started for …` appears.
   > Within 15 minutes, `Deploy live for …`.
   >
   > **If not:** `Deploy failed` means staging did not update. Click it,
   > copy the last 50 lines of text, and give them to a Claude session
   > ([03](03_CLAUDE.md)). Stop here until it is fixed.

2. Open `<staging address>/api/health` in Firefox. In **Filter JSON** type
   `short`. Look at the one under `build`
   ([how](README.md#reading-the-health-page)).

   > **Check:** it is the same 7-character code you wrote down in Step 1.9.
   >
   > **If not:** the deploy has not finished. Wait 5 minutes and refresh.

3. Filter for `zero_spend`, then `network_guard`.

   > **Check:** both are `true`. This proves staging cannot spend money.
   >
   > **If not:** wait 5 minutes and refresh once (just after starting they
   > can briefly read `false`). Still `false`: tell the owner. Do not use
   > staging until it is `true`.

---

## Step 3. Test on staging

Open `<staging address>` on your phone or computer and sign in with your
staging account.

**Staging cannot make real episodes.** Every episode there is the same
sample script, read in a beeping placeholder tone. That is normal. You are
testing that the app *works*, not how episodes sound.

Go through this list and tick each row.

| # | Do this | You should see |
|---|---|---|
| 1 | Open the app | The sign-in screen, or your home screen |
| 2 | Sign out, then sign in again | You are back in |
| 3 | Search for anything | A loading screen that ticks off five short steps one after another, then the sample plays as a tone, with captions moving |
| 4 | Drag the playback bar, then press −15 and +15 | The position moves each time |
| 5 | Open myFAM and tap a tile | It plays |
| 6 | Tap **Go Deeper** | A suggestion and a box to type in |
| 7 | Open DailyFAM and create a mix | The new mix appears |
| 8 | Send a message to your second staging account | It appears in the chat on both accounts |
| 9 | **Whatever this batch changed.** Read each pull request's description and try it. | It does what the description says |

> **If something fails:** do not go on to Step 4. Write down the row and
> what you saw (a screenshot helps). Ask a Claude session to fix it on the
> same feature branch, then repeat Step 1 for the fix. Staging stays broken
> until then. That is fine, because listeners never see staging. If the fix
> will take long, take the change back out of staging instead
> ([The change is only on staging](#the-change-is-only-on-staging)).

The owner's full list is in `STAGING.md` → "Staging checklist".

---

## Step 4. Open a pull request from `staging` into `Main`

1. **Pull requests** tab → **New pull request**.
2. **base:** leave it as `Main`. **compare:** choose `staging`.

   > **Check:** the line reads `base: Main ← compare: staging`, and
   > `Able to merge` in green. The commits listed are this batch's, plus
   > possibly a few named `Merge pull request … Main back into staging` (left
   > over from last time; expected).
   >
   > **If not:** if you see commits you do not recognise, someone else
   > merged into `staging`. Click them to see what they are before going on.

3. Click **Create pull request**. **Title:** `Staging to Main: <short list
   of what is in it>`. **Description:** list the pull request numbers in
   the batch (`#232, #233`) and write `Tested on staging: yes`.
4. Click **Create pull request**.
5. Wait for the checks (35-60 minutes).

   > **Check:** `All checks have passed` (green).

6. **Does this batch change the database?** Look at each pull request's
   description for "new column", "new table", "migration" or "schema". If you
   are not sure, ask a Claude session: `Does PR #<number> change the shape of
   any database table? Answer yes or no, and do not change anything.`

   > **If yes:** take a database backup first
   > ([04 → Take a database backup](04_RENDER.md#take-a-database-backup)).
   > **If the backup steps there still say TODO(owner), do not merge this
   > batch.** Wait for the owner.

---

## Step 5. Merge into `Main`, and check production

1. On the `staging` → `Main` pull request: **▾** → **Create a merge
   commit** → **Merge pull request** → **Confirm merge**.

   > **Check:** the purple **Merged** badge, and `… merged commit abc1234
   > into Main`. Write down that code.

2. Render → **fam** → **Events**.

   > **Check:** `Deploy started`, then `Deploy live` within 15 minutes.
   > Production stays up the whole time.
   >
   > **If not:** `Deploy failed` means the old version is still serving,
   > so listeners are fine. Go to [04 → Task 2](04_RENDER.md#a-deploy-failed).
   > Nothing in Events after 10 minutes: see the Auto-Deploy check in
   > [04 Task 3, step 6](04_RENDER.md#roll-back-to-the-last-good-version).

3. Open `<production address>/api/health` in Firefox and filter for each of
   these ([how](README.md#reading-the-health-page)):

   | Filter for | Look under | It should say |
   |---|---|---|
   | `short` | `build` | the code from step 1 |
   | `mode` | (top level) | `live` |
   | `state` | `credentials` (not `credentials → secrets`) | `ok` |
   | `reachable` | `database` | `true` |
   | `interim` | `tts` | `false` |

   > **If any is wrong:** if the app is broken, first
   > [roll back](04_RENDER.md#roll-back-to-the-last-good-version), then look
   > up the symptom in [10](10_RUNBOOKS.md).

4. In the production app, **play one new episode** (search for something
   you have not searched before) and **one replay** (an episode from your
   listening history).

   > **Check:** both play in the real voice. The first one after a quiet
   > spell can take up to 90 seconds to start.
   >
   > **If not:** [10 → beeping tone](10_RUNBOOKS.md#episodes-are-a-beeping-tone-or-stuck-on-generating-the-audio),
   > or [10 → replays will not play](10_RUNBOOKS.md#replays-will-not-play-but-new-episodes-do).

5. RunPod rebuilds the voice after every merge into `Main`. If the voice
   seems slow for the next 30 minutes, that is why
   ([05 → Task 4](05_RUNPOD.md#after-a-merge-to-main)).

---

## Step 6. Bring `staging` level with `Main`

Merging in Step 5 adds one small "merge" commit to `Main` that `staging`
does not have. Copy it back, so the two branches match again.

1. Open `https://github.com/tjweingart-byte/fam/compare/staging...Main`

   > **Check:** it lists one or a few commits: the merge from Step 5, and any
   > urgent fix made directly to `Main`.

2. Click **Create pull request**.

   > **Check:** the line reads `base: staging ← compare: Main`. (Yes, the
   > opposite of Step 4.)
   >
   > **If not:** if base says `Main`, change it to `staging`.

3. Title: `Main back into staging`. Click **Create pull request**.
4. Wait for the checks. Then **▾** → **Create a merge commit** →
   **Merge pull request** → **Confirm merge**.
5. Open the link from step 1 again.

   > **Check:** `There isn't anything to compare`. `fam-staging` redeploys,
   > which is expected.

**The batch is done.**

---

## An urgent fix that cannot wait for staging

**Only when production is broken, after a rollback
([04 Task 3](04_RENDER.md#roll-back-to-the-last-good-version)) did not fix
it, and after you have told the owner.**

1. Do Step 1, but in point 3 choose base **`Main`** instead of `staging`.
2. Wait for green checks. Merge. Check production (Step 5, points 2-4).
3. Then do **Step 6** straight away, so `staging` gets the fix too.

   > **Check:** the Step 6 link shows nothing to compare.

---

## Undoing a change (revert)

### The change is already in production

1. Find the **`staging` → `Main` pull request** that brought it in: **Pull
   requests** tab → **Closed** → type `Staging to Main` in the search box →
   open the right one.
2. Scroll to the bottom. Click **Revert**.

   > **Check:** GitHub opens a new pull request titled `Revert "…"`, with
   > `base: Main`.
   >
   > **If not:** if GitHub says it cannot revert automatically, ask a Claude
   > session: `Revert PR #<number> on a new branch based on Main, run
   > ./dev.sh check, and push.` Then open a pull request from its branch into
   > `Main`.

   **This undoes the whole batch**, not just one change. That is usually
   what you want in an emergency.

3. Click **Create pull request**. Wait for green checks. **Create a merge
   commit** → **Merge** → **Confirm merge**.
4. Check production as in [Step 5](#step-5-merge-into-main-and-check-production), points 2-4.
5. Do **[Step 6](#step-6-bring-staging-level-with-main)**, so `staging`
   loses the change too.

If production is broken *right now*, first
[roll back on Render](04_RENDER.md#roll-back-to-the-last-good-version),
which takes minutes (the revert's checks take 35-60 minutes). Then do the revert.
**A rollback alone is not enough**: the next merge into `Main` would bring
the bad change back.

**Bringing a reverted change back later is a trap.** Merging the same
feature branch again brings back nothing, because GitHub thinks it is
already there. Ask a Claude session: `PR #<revert number> reverted a batch.
Make a branch that brings back <the good parts> plus this fix: <describe>.`

### The change is only on staging

1. Open the feature → `staging` pull request that brought it in. Click
   **Revert** at the bottom.

   > **Check:** the new pull request's line reads `base: staging`.

2. Create it, wait for green checks, merge (**Create a merge commit**).

---

## When something goes wrong in a pull request

### "This branch has conflicts"

Two changes edited the same lines. **Do not use GitHub's conflict editor.**

1. Start a Claude session ([03](03_CLAUDE.md)) and paste the "Fix a merge
   conflict" prompt from [03 → Prompts to copy](03_CLAUDE.md#prompts-to-copy).
2. When it says it has pushed, refresh the pull request.

> **Check:** `This branch has no conflicts with the base branch`, and the
> checks start again.
>
> **If not:** if Claude pushed to a **different** branch name, open a new
> pull request from that branch (Step 1), and close the old one with a
> comment `Replaced by #<new number>`.

`PROBLEMS.md` and `PROBLEMS_INDEX.md` conflict often. That is normal.

### "This branch is out-of-date with the base branch"

Click **Update branch**. If it offers a choice, choose **Update with merge
commit**.

> **Check:** the checks start again. Wait for green.

### Checks are red on a pull request

1. In the checks box, click **Details** beside the red cross.
2. First, top right of that page: **Re-run jobs** → **Re-run failed jobs**,
   once. A one-off failure sometimes passes the second time.

   > **Check:** if it turns green after 35-60 minutes, you are done.

3. Still red: click the step with the red cross to open it, and scroll to the
   red text. Copy from the first line containing `FAILED` or `Error` to the
   end. If that is very long, copy just the last 100 lines.
4. Paste it into a Claude session with the "Fix a failing check" prompt from
   [03 → Prompts to copy](03_CLAUDE.md#prompts-to-copy).

   > **Check:** after Claude pushes, the checks run again and turn green.

**Never** merge a red pull request. **Never** accept a "fix" that deletes or
skips a test.

---

## Settings only the owner changes

**TODO(owner):** confirm, in GitHub → **Settings** → **Branches** (or
**Rules**), that both `Main` and `staging`:
- require a pull request before merging,
- require the **CI** check to pass,
- require review from Code Owners (this is what makes rule-book changes need
  the owner, see [09](09_AGENT_RULE_SIGNOFF.md)).

Also decide whether every pull request needs an **approving review**. If it
does, the operator is stuck all week: GitHub never lets you approve a pull
request you opened yourself. Either leave required approvals at 0 (only the
rule-book files need the owner, through Code Owners), or give a second
person the right to approve.

The other workflows in the **Actions** tab need nothing from you. **Voice
worker** checks voice changes automatically. **Load test** runs only when
someone starts it by hand.
