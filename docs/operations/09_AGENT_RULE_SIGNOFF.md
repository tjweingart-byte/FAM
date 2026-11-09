# 9. Signing off on agent documents

## What this is about

FAM has eight AI agents. Each has a **rule book**, a plain-English file
listing what it must always and must never do. They are in
`docs/agent_rules/`, along with `every-agent.md` (rules for all of them).
Shakespeare also has a playbook, `docs/journalism_playbook.md`, which is
signed the same way.

| Name | Role (what the health page and `/admin` call it) | Rule book |
|---|---|---|
| Netenyahu | `overlord` | `netenyahu.md` |
| C3P0 | `contextualizer` | `c3p0.md` |
| Shakespeare | `editor` | `shakespeare.md` (+ `docs/journalism_playbook.md`) |
| Mclovin | `supervisor` | `mclovin.md` |
| Consuela | `housekeeper` | `consuela.md` |
| Sonic | `retriever` | `sonic.md` |
| Keith Wong | `it` | `keith.md` |
| Nando | `categorizer` | `nando.md` |

The owner decided that a rule book changes **only when the owner
deliberately signs the change**. That is enforced by the code:

- A file called `MANIFEST.json` lists a fingerprint of every rule book.
- The owner **signs** that file with a private key only the owner has. The
  signature is the file `MANIFEST.json.sig`.
- **If a rule book does not match the signed fingerprint, its agent stops
  ("is held").** FAM carries on without it. Listeners are not affected; the
  agent just does not run.
- GitHub's checks fail if a rule book does not match, so an unsigned change
  cannot be merged.

**So: you can review a change, but only the owner can make it take effect.**
That is on purpose.

### Who does what

| Step | Who |
|---|---|
| 1. Ask for a change | You or the owner |
| 2. Write the proposal | A Claude session |
| 3. Open the pull request (into `staging`) | You |
| 4. Review the proposal | **You**, then the owner |
| 5. **Sign it** | **The owner only** |
| 6. Finish the branch | A Claude session |
| 7. Approve and merge | The owner approves (GitHub requires it for these files); you merge and take it to production |
| 8. Check production | You |

> **TODO(owner):** decide how signing works while you are away. Options:
> (1) proposals wait for your weekend: recommended, since a rule-book
> change is never urgent; (2) you sign from your Mac when asked (about five
> minutes, Step 5); (3) a second signing key for the operator, which needs a
> code change that you would have to sign yourself.

---

## Task 1. Check the agents are all running (daily)

1. Open `<production address>/api/health` in Firefox. In **Filter JSON**
   type `lock`. Choose the section `agents → lock`. (Ignore
   `blocked_connections`, which also contains "lock".)
   ([How to read the health page](README.md#reading-the-health-page).)
2. Read the three values **inside `agents → lock` only**:

   > **Check:**
   > - `signed` is `true`
   > - `version` is a number. **Write it down**: you will need it in Step 8.
   >   It was `6` on 2026-11-06 and goes up by one with each signing.
   > - `held` is `{}` (empty): no agent is stopped.
   >
   > **If `held` lists a role** (for example `categorizer`): that agent is
   > stopped. The table above gives its name. See
   > [An agent is held and nobody changed anything](#an-agent-is-held-and-nobody-changed-anything).
   >
   > (There is a different `held` directly under `agents`, about queued jobs.
   > Ignore it here.)

---

## Task 2. Change a rule book, start to finish

### Step 1. Ask for a proposal

Start a Claude session ([03](03_CLAUDE.md)) and paste this, replacing the
parts in angle brackets:

```
I want to change <agent name>'s rule book: <what should change, and why,
with an example of what went wrong>.
Read docs/agent_rules/README.md, docs/agent_rule_proposals/README.md and
<agent name>'s section of docs/FAM_AGENTS.md first.
Write it as a proposal for the owner to sign. Do NOT edit the signed rule
book. Tell me the branch name when you have pushed.
```

> **Check:** Claude's reply says it wrote all of these, and that `./dev.sh
> check` ended with `all checks passed`:
> - `docs/agent_rule_proposals/<book>.md`: the **whole** proposed rule book
> - `docs/agent_rules/MANIFEST.pending.json`: the new fingerprints
> - a line under **Waiting now** in `docs/agent_rule_proposals/README.md`
> - a new section in `PROBLEMS.md` explaining why
>
> **If it edited the signed book in `docs/agent_rules/` directly:** reply
> `Undo the edit to the signed book and write it as a proposal instead.`

### Step 2. Write down the details

Write these down, because the session may be closed by the time the owner
signs:
- the **branch name**;
- the **rule book's file name** (for example `mclovin.md`, or
  `journalism_playbook.md` for Shakespeare's playbook);
- the **version** from Task 1.

### Step 3. Open a pull request into `staging`

Do [02 → Step 1](02_GITHUB.md#step-1-merge-a-feature-branch-into-staging),
points 1-6 only. **Remember to change base to `staging`.** **Do not merge
it yet.**

> **Check:** the pull request's **Files changed** shows the proposal, the
> pending manifest, the proposals README and `PROBLEMS.md`. Nothing else in
> `docs/agent_rules/` except `MANIFEST.pending.json`.

### Step 4. Review it

Ask a **new** Claude session to show you the differences:

```
On branch <branch name>, show me, line by line, the differences between
docs/agent_rule_proposals/<book>.md and docs/agent_rules/<book>.md (for the
playbook: docs/journalism_playbook.md). Explain each change for someone who
does not code. Do not change anything.
```

Then answer every question below. **Approve only if every answer is yes.**

- [ ] **Only the rule we asked for changed.** No other wording moved.
- [ ] **It does not contradict `every-agent.md`** or another agent's rule book.
- [ ] **It does not weaken one of the owner's settled rules** in `CLAUDE.md`
      (for example: never make up a current fact; nothing is spoken before
      its research arrives; slurs are the only words removed).
- [ ] **It gives the agent no new power**: it cannot now write files,
      change settings, change rules, see private listener data, or contact a
      new outside service.
- [ ] **The reason is written down** in the new `PROBLEMS.md` section.
- [ ] **The proposal is the whole rule book**, not just the changed part.

When all six are ticked:
1. On the pull request, at the bottom of the **Conversation** tab, write
   `Reviewed for signing: all six checks ticked`, then click **Comment**.
   (GitHub does not let you *approve* a pull request you opened yourself.
   Your comment is the sign-off.)
2. Tell the owner: `Rule-book proposal for <agent> is ready to sign: branch
   <branch name>, PR #<number>.`

> **If any box is not ticked:** write a comment saying which one and why.
> Ask Claude to fix the proposal on the same branch (name the branch, as in
> the 03 prompts). Then repeat this step.

### Step 5. The owner signs (on the owner's Mac)

*This step is for the owner. It is written out so the operator knows what
to expect.*

1. On GitHub, switch to the pull request's branch (the branch button at the
   top left of the file list). Open the proposal
   `docs/agent_rule_proposals/<book>.md` → click **Download raw file** (the
   download icon, top right of the file). Do the same for
   `docs/agent_rules/MANIFEST.pending.json`.
2. In **Downloads**, rename `MANIFEST.pending.json` to `MANIFEST.json`.
3. Save the private key from the password manager as plain text to
   `~/fam-rules-signing.pem` (TextEdit → **Format** → **Make Plain Text**).
   Never paste it into Terminal, a chat or a Claude session.
4. In Terminal:

       conda activate famsign
       cd ~/Downloads
       openssl pkeyutl -sign -rawin -inkey ~/fam-rules-signing.pem -in MANIFEST.json | base64 | tr -d '\n' > MANIFEST.json.sig
       rm -P ~/fam-rules-signing.pem

   > **Check:** `cat MANIFEST.json.sig` prints one line of 88 characters
   > ending in `==`, and no error was printed.

5. On GitHub, on the pull request's branch:
   - open the folder `docs/agent_rules/` → **Add file** → **Upload
     files** → drag in `MANIFEST.json` and `MANIFEST.json.sig`, **and** the
     proposal `<book>.md` (keeping its name) **if it is an agent's rule book**;
   - **for Shakespeare's playbook only**, upload `journalism_playbook.md` to
     the folder `docs/` instead, in the same commit if possible.
   - **Commit changes**.

   > **Check:** the commit lists exactly those files, nothing else.

**Always upload the files. Never edit a rule book in GitHub's in-browser
editor.** The signature covers every character, and the editor can change
an invisible one, which makes the book no longer match.

### Step 6. Finish the branch

In a Claude session (a new one is fine), paste:

```
Check out the existing branch <branch name> and work on it, pushing to that
same branch, not a new one.
The owner has uploaded the signed <book>.md, MANIFEST.json and
MANIFEST.json.sig. Verify the signature, delete the proposal and
MANIFEST.pending.json, update the proposals README, run ./dev.sh check,
push, and tell me the branch you pushed to.
```

> **Check:** the reply shows `python tools/rulebook_manifest.py --verify`
> printing every agent `may run`, then `manifest version <N>, signed with
> owner.pub`, where N is one higher than the version you wrote down. And
> `all checks passed`.
> It names the same branch.

### Step 7. Merge, and take it to production

1. On the pull request, wait for green checks.
2. The owner approves it (GitHub requires the owner's review for these
   files).

   > **Check:** the pull request shows `Approved` by the owner, and the merge
   > button is green.

3. Merge it (**Create a merge commit**), then take `staging` to `Main` as
   usual: [02 → Steps 2-6](02_GITHUB.md#step-2-watch-staging-update).

### Step 8. Check production

After `Deploy live` on production, do [Task 1](#task-1-check-the-agents-are-all-running-daily).

> **Check:** `signed` is `true`, `version` is **one higher** than the number
> you wrote down in Step 2, and `held` is `{}`.
>
> **If an agent is held:** `held` names the role and the file that does not
> match. Do not try to fix the file. Tell the owner.

The owner then updates the master copy they keep outside Claude.

---

## An agent is held and nobody changed anything

A rule book or the manifest was changed without a signature, most likely by
a merge.

1. Health page → `agents → lock → held` names the role and the file.
2. On GitHub, make sure the branch button (top left of the file list) says
   `Main`. Open the file it named → **History** (top right). The newest
   entry links to the pull request that changed it.
3. Revert that pull request ([02 → Undoing a change](02_GITHUB.md#undoing-a-change-revert)).
   If it was a `Staging to Main` batch, the revert undoes the whole batch.
   Ask the owner first if the batch held anything else urgent.

   > **Check:** once the revert is on production, `held` is `{}` again.

4. Tell the owner.

**This is never an emergency.** FAM works without the agent. Never "fix" it
by changing a rule book or the manifest without a signature.

---

## Which agent documents need a signature?

| Document | Signed? | Who approves |
|---|---|---|
| `docs/agent_rules/*.md`, `docs/journalism_playbook.md` | **Yes** | The owner signs (this document) |
| `docs/FAM_AGENTS.md` (explanations and reasoning) | No | Normal review. But if it changes what a rule *means*, the rule book must change too, and that is signed. |
| Training plans and prompts (`docs/AGENTS_TRAINING_PLAN.md`, `docs/TRAINING_NEXT_BRANCH.md`, …) | No | Normal review |
| Agent code (`agents/*.py`) | No, except `agents/rulebooks.py`, which needs the owner's review | Normal review; checks must pass |
| The lock's own files: `tools/rulebook_manifest.py`, `tests/test_agent_rulebooks.py`, `.gitattributes`, `.github/CODEOWNERS` | No | **The owner's review** (GitHub requires it) |
| Agent on/off switches on Render ([04 → the switch table](04_RENDER.md#task-4-change-a-setting-environment-variable)) | No | The owner's decision |
| Which AI model each agent uses | No | The owner's decision |
