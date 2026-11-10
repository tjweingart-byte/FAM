# 3. Claude: asking Claude Code to make or fix a change

Almost all of FAM's code is written by Claude Code, an AI that works on the
repository. You tell it what you want, it makes the change on its own
branch, and you move that branch through staging to production
([02_GITHUB.md](02_GITHUB.md)).

**What Claude Code can do:** read and change the code, run every test, push
to a branch, and explain how anything works.

**What it cannot do:** see Render, RunPod, Cloudflare or Atlas; run anything
on production; hear the voice; judge how good an episode sounds; sign an
agent rule book.

---

## Task 1. Start a session and ask for a change

1. Open `https://claude.ai/code`. Click **New session** (or **+**).
2. Choose the repository **`tjweingart-byte/fam`**.

   > **Check:** the repository name shows at the top of the session.
   >
   > **If not:** see [01 → Part B, step 2](01_ACCESS.md#part-b-the-operator-checks-each-login-works-once).

3. Type what you want, in plain words. Use one of the
   [prompts below](#prompts-to-copy) where one fits.
4. Press Enter and wait. Small changes take a few minutes. Large ones can
   take an hour, mostly running tests. You can leave and come back.

   > **Check:** you see it working: reading files, editing, running commands.
   >
   > **If it asks your permission** ("Allow this command?"):
   > - **Allow** running tests and checks (anything with `pytest`, `./dev.sh`,
   >   `python tools/check_…`), reading files, and pushing to its own
   >   `claude/…` branch.
   > - **Deny** anything that asks for a key or password, pushes to `staging`
   >   or `Main`, deletes files you did not ask about, or contacts a website
   >   other than GitHub.
   > - Not sure? Deny, and ask it `Why do you need to do that?`

5. Read its final reply.

   > **Check:** the reply should include all of these. Ask for any that is
   > missing:
   > - **what it changed**, in a few sentences;
   > - **the branch name** it pushed to (`claude/…`);
   > - the words **`all checks passed`**. `./dev.sh check` is the project's
   >   full test run, which Claude runs on its own machine before pushing.
   >   If it only says "tests pass", ask
   >   `Did ./dev.sh check end with "all checks passed"?`
   >
   > **If it says tests failed:** reply `Fix the failing tests and run
   > ./dev.sh check again.` Never take a failing branch to staging.
   >
   > **If it gives you a command to run:** do not run it on your own computer
   > ([README → No terminal needed](README.md#no-terminal-needed)). Ask
   > `Where exactly do I run this: the Render Shell, or my own computer? Is
   > there a way without a terminal?` If the answer is your own computer,
   > send it to the owner.

6. Take the branch to staging: [02 → Step 1](02_GITHUB.md#step-1-merge-a-feature-branch-into-staging).

### Rules for sessions

- **One task per session.** New topic, new session.
- **Never paste a password, key, connection string or admin token** into a
  session. If Claude asks for one, say no. It can tell you which setting to
  change, and you change it on Render yourself. (The health page never
  contains keys, so copying from it is safe. The Render **Environment** page
  does contain keys, so never copy from there.)
- **Open pull requests yourself, in GitHub** ([02](02_GITHUB.md)). Never
  press a **Create PR** button inside Claude Code: it may aim the pull
  request at `Main`.
- **Never ask it to push to `staging` or `Main`.** It works on its own
  branch; you merge.
- **When you ask it to fix an existing branch, name the branch** (the
  prompts below do), and check its reply names the same branch. If it pushed
  to a new branch instead, use that one: open a pull request from the new
  branch, and close the old one with the comment `Replaced by #<number>`.
- If it says something goes against a rule in `CLAUDE.md` (the owner's
  decisions), **believe it**. That rule is the owner's to change.

---

## Prompts to copy

Copy the whole box. Replace each part in `<angle brackets>`, brackets
included. You do not need to understand the technical words in them;
Claude does.

**Fix a failing check on a pull request**
```
Check out the existing branch <branch name> and work on it, pushing to that
same branch, not a new one.
GitHub's checks fail on PR #<number> with this output:
<paste the red text>
Fix it, run ./dev.sh check, push, and tell me the branch you pushed to.
```

**Fix a merge conflict**
```
Check out the existing branch <branch name> and work on it, pushing to that
same branch, not a new one.
It has conflicts with staging. Merge origin/staging into it, resolve the
conflicts, run ./dev.sh check, push, and tell me the branch you pushed to.
```

**Find out what is wrong in production (no changes)**
```
Production's /api/health shows this:
<paste the part of the page that looks wrong>
What is wrong, and what should I check or change on Render? Explain it for
someone who does not code. Do not change any code.
```

**Explain something (no changes)**
```
How does <thing> work? Answer from the code, name the files, explain it for
someone who does not code, and do not change anything.
```

**A small change**
```
<describe the change, and why>.
Run ./dev.sh check. Do not open a pull request. Tell me the branch name when
you have pushed.
```

**A second opinion on a pull request (no changes)**
```
Review PR #<number> for bugs, and for anything that touches the owner-only
areas listed in docs/operations/01_ACCESS.md. Explain what you find for
someone who does not code. Do not change anything.
```

**Update this handbook**
```
In docs/operations/<file name>, <what is wrong or missing, and what you
actually saw on screen>. Fix it, keep the step-by-step Check format, run
./dev.sh check, and push.
```

**A change to an agent's rule book:** use the prompt in
[09 → Step 1](09_AGENT_RULE_SIGNOFF.md#step-1-ask-for-a-proposal). Claude
writes a *proposal*. It cannot sign.

---

## Task 2. Check Claude's work before merging

Claude sounds just as confident when it is wrong. Before you merge its
branch anywhere, check:

1. **Its reply says `all checks passed`.**
2. **The pull request's checks are green**, whatever the reply says.
3. **Files changed** on the pull request matches what you asked for, and
   nothing more.
4. **No owner-only files**: the list is in
   [02 → Step 1, point 7](02_GITHUB.md#step-1-merge-a-feature-branch-into-staging)
   (a rule-book proposal's `MANIFEST.pending.json` is the one exception).
5. **For anything you can see in the app:** test it on staging
   ([02 → Step 3](02_GITHUB.md#step-3-test-on-staging)). That is the real
   test.

> **If you are unsure about any of it:** start a **new** session with the
> "second opinion" prompt above. A new session has not seen the first one's
> reasoning, so it is a genuine second look.

---

## Task 3. The Anthropic API key (Claude inside FAM)

This is separate from Claude Code. Production uses an Anthropic API key to
write every episode: the setting `ANTHROPIC_API_KEY` on Render `fam`.
Staging has none.

### Check the key works (daily, part of 10's routine)

Open `<production address>/api/health` in Firefox and filter for `state`.
Look at the one **directly under `credentials`**, not the one under
`credentials → secrets` ([how](README.md#reading-the-health-page)).

> **Check:** `ok`.
>
> - `rejected` means the start-up check failed: usually Anthropic refused the
>   key, sometimes Anthropic was down at that moment. `detail` beside it says
>   which. See
>   [10 → episodes fail](10_RUNBOOKS.md#every-episode-is-the-same-sample-or-new-episodes-fail).
> - `absent` means the setting is empty. Every episode is then the built-in
>   sample.
>
> This is checked when the server starts. A key that stops working later
> shows up as episodes failing, not here, until the next deploy.

### Check spend (weekly)

1. Open `https://console.anthropic.com` (it may forward you to
   `platform.claude.com`) → **Usage** (or **Cost**).

   > **Check:** daily spend is close to the normal figure
   > (**TODO(owner):** write the normal daily spend here). Double or more
   > goes to the owner ([10 → Costs jump](10_RUNBOOKS.md#costs-jump)).

2. **TODO(owner):** under **Limits**, set a monthly spend limit and an email
   alert, and write the amount here.

### Replace the key (only if it leaked, or the owner asks)

Do this with nobody watching your screen and no screen-sharing.

1. Console → **API Keys** → **Create Key**. If it asks for a workspace,
   choose **TODO(owner):** (the workspace name). Name the key
   `fam-production-<today's date>`. Copy it. **Paste it nowhere except step 3.**
2. Render → **fam** → **Environment** → **Edit**.

   > **Check:** the page title says `fam`, not `fam-staging`.

3. Find `ANTHROPIC_API_KEY`. Delete the **whole** old value, then paste the
   new key. Click **Save, rebuild, and deploy** (not **Save only**).

   > **Check:** **Events** shows `Deploy live` within 15 minutes.

4. Open `/api/health` and check `credentials → state` (as above).

   > **Check:** `ok`. Then search something new in the app. It should be a
   > real episode in the real voice.
   >
   > **If not:** `rejected` means the key was not accepted. Copy it again
   > from the Console, with no space at either end, and repeat step 3.
   > `absent` means the value was saved empty.

5. Back in the Console → **API Keys** → the **old** key (the one *not* named
   with today's date) → **Disable** (or **Delete**). If you cannot tell which
   is old, or the Console will not let you, ask the owner.

**Do not change which Claude model FAM uses** (`MODEL`, `WRITER_MODEL`, or
anything in `agents/models.py`). The writing was tuned for these models.
That is the owner's decision.
