# 9. Signing off on agent documents

## What this is about

FAM has eight AI agents: Netenyahu, C3P0, Shakespeare, Mclovin, Consuela,
Sonic, Keith Wong and Nando. Each has a **rule book**, a plain-English file
listing what it must always and must never do. They are in
`docs/agent_rules/`, along with `every-agent.md` (rules for all of them)
and Shakespeare's `docs/journalism_playbook.md`.

The owner decided that a rule book changes **only when the owner
deliberately signs the change**. That is enforced by the code:

- A file called `MANIFEST.json` lists a fingerprint of every rule book.
- The owner **signs** that file with a private key only the owner has. The
  signature is `MANIFEST.json.sig`.
- **If a rule book does not match the signed fingerprint, its agent stops
  ("is held").** FAM carries on without it. Listeners are not affected; the
  agent just does not run.
- The tests fail if a rule book does not match, so an unsigned change cannot
  be merged.

**So: you can review and approve a change, but only the owner can make it
take effect.** That is on purpose.

### Who does what

| Step | Who |
|---|---|
| 1. Ask for a change | You or the owner |
| 2. Write the proposal | A Claude session |
| 3. Review the proposal | **You**, then the owner |
| 4. **Sign it** | **The owner only** |
| 5. Finish and merge | A Claude session, then you |
| 6. Check production | You |

> **TODO(owner):** decide how signing works while you are away. Options:
> (1) proposals wait for your weekend: recommended, since a rule-book
> change is never urgent; (2) you sign from your Mac when asked (about five
> minutes, Step 4); (3) a second signing key for the operator, which needs a
> code change that you would have to sign yourself.

---

## Task 1. Check the agents are all running (daily)

1. Open `<production address>/api/health`. Find (Ctrl+F) `"lock"`.

   > **Check:**
   > - `"signed": true`
   > - `"version"` is a number (6 as of 2026-11-06, higher after each new signing)
   > - `"held": {}`, empty, meaning no agent is stopped
   >
   > **If `held` lists an agent:** see
   > [An agent is held and nobody changed anything](#an-agent-is-held-and-nobody-changed-anything).

---

## Task 2. Change a rule book, start to finish

### Step 1. Ask for a proposal

Start a Claude session ([03_CLAUDE.md](03_CLAUDE.md)) and paste, filling in
the parts in angle brackets:

```
I want to change <agent>'s rule book: <what should change, and why, with an
example of what went wrong>.
Read docs/agent_rules/README.md, docs/agent_rule_proposals/README.md and
<agent>'s section of docs/FAM_AGENTS.md first.
Write it as a proposal for the owner to sign. Do NOT edit the signed rule
book in docs/agent_rules/. Tell me the branch name when you have pushed.
```

> **Check:** Claude's reply says it wrote:
> - `docs/agent_rule_proposals/<book>.md`: the **whole** proposed rule book
> - `docs/agent_rules/MANIFEST.pending.json`: the fingerprints as they
>   will be
> - a line under **Waiting now** in `docs/agent_rule_proposals/README.md`
> - a new section in `PROBLEMS.md` explaining why
>
> and that `./dev.sh check` passed.
>
> **If it edited `docs/agent_rules/<book>.md` directly:** reply "Undo the
> edit to the signed book and write it as a proposal instead."

### Step 2. Open a pull request

On GitHub, open a PR from Claude's branch into **`staging`**
([02_GITHUB.md, Step 1](02_GITHUB.md#step-1-merge-a-feature-branch-into-staging),
points 1-5). **Do not merge it yet.**

> **Check:** the PR's **Files changed** shows the proposal, the pending
> manifest, the proposals README and `PROBLEMS.md`. Nothing else in
> `docs/agent_rules/` except `MANIFEST.pending.json`.

### Step 3. Review it

Compare the proposal with the current rule book. The easiest way: ask a
**new** Claude session:

```
Show me, line by line, the differences between
docs/agent_rule_proposals/<book>.md and docs/agent_rules/<book>.md on
branch <branch name>. Do not change anything.
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

When all are ticked:
1. On the PR, click **Files changed** → **Review changes** → choose
   **Approve** → in the comment write `Reviewed for signing` → **Submit
   review**.
2. Tell the owner: "Rule-book proposal for <agent> is ready to sign on
   branch <branch name>, PR #<number>."

> **If any box is not ticked:** choose **Request changes** instead, and
> write which one and why. Ask Claude to fix the proposal on the same branch.

### Step 4. The owner signs (on the owner's Mac)

*This step is for the owner. It is written out so the operator knows what
to expect.*

1. On GitHub, switch to the PR's branch. Open
   `docs/agent_rule_proposals/<book>.md` → click the **Download raw file**
   button (top right of the file). Do the same for
   `docs/agent_rules/MANIFEST.pending.json`.
2. In **Downloads**, rename `MANIFEST.pending.json` to `MANIFEST.json`.
3. Save the private key from the password manager as plain text to
   `~/fam-rules-signing.pem` (TextEdit → Format → Make Plain Text). Never
   paste it into Terminal, a chat or a Claude session.
4. In Terminal:

       conda activate famsign
       cd ~/Downloads
       openssl pkeyutl -sign -rawin -inkey ~/fam-rules-signing.pem -in MANIFEST.json | base64 | tr -d '\n' > MANIFEST.json.sig
       rm -P ~/fam-rules-signing.pem

   > **Check:** `cat MANIFEST.json.sig` prints one line of about 88 letters
   > and numbers ending in `==`. No error was printed.

5. On GitHub, on the PR's branch, open the folder `docs/agent_rules/` →
   **Add file** → **Upload files**. Drag in exactly three files:
   `<book>.md` (the proposal, keeping its name), `MANIFEST.json`,
   `MANIFEST.json.sig`. → **Commit changes**.

   > **Check:** the commit lists exactly those three files, nothing else.

**Always upload the files. Never edit a rule book in GitHub's in-browser
editor.** The signature covers the exact bytes, and the editor can change
an invisible character, which makes the book no longer match.

### Step 5. Finish, and merge

1. In the Claude session on that branch, paste:

   ```
   The owner has uploaded the signed <book>.md, MANIFEST.json and
   MANIFEST.json.sig. Verify the signature, delete the proposal and
   MANIFEST.pending.json, update the proposals README, run ./dev.sh check,
   and push.
   ```

   > **Check:** the reply shows `python tools/rulebook_manifest.py --verify`
   > printing `signed: True` and every book `matches`, and `all checks passed`.

2. On the PR, wait for green checks. The owner approves it (GitHub requires
   the owner's review for this folder). Then **Merge pull request**.
3. Take `staging` to `Main` as usual ([02_GITHUB.md, Steps 2-6](02_GITHUB.md#step-2-watch-staging-update)).

### Step 6. Check production

After `Deploy live` on production, open `/api/health` → `"lock"`.

> **Check:** `"signed": true`, `"version"` is **one higher** than before,
> and `"held": {}`.
>
> **If an agent is held:** `held` names the file that does not match. Do not
> try to fix the file. Tell the owner.

The owner then updates the master copy they keep outside Claude.

---

## An agent is held and nobody changed anything

A rule book or the manifest was changed without a signature, most likely
by a merge.

1. `/api/health` → `"held"` names the agent and the file.
2. On GitHub, open that file → **History** (top right). Find the most recent
   change and the PR it came from.
3. Revert that PR ([02_GITHUB.md](02_GITHUB.md#undoing-a-change-revert)).

   > **Check:** after the revert reaches production, `"held": {}` again.

4. Tell the owner.

**This is never an emergency.** FAM works without the agent. Never "fix" it
by changing a rule book or the manifest without a signature.

---

## Which agent documents need a signature?

| Document | Signed? | Who approves |
|---|---|---|
| `docs/agent_rules/*.md`, `docs/journalism_playbook.md` | **Yes** | Owner signs (this document) |
| `docs/FAM_AGENTS.md` (explanations and reasoning) | No | Normal PR review. But if it changes what a rule *means*, the rule book must change too, and that is signed. |
| Training plans and prompts (`docs/AGENTS_TRAINING_PLAN.md`, `docs/TRAINING_NEXT_BRANCH.md`…) | No | Normal PR review |
| Agent code (`agents/*.py`) | No, except `agents/rulebooks.py`, which needs the owner's review | Normal PR review; checks must pass |
| Agent on/off switches on Render (`AGENTS`, `HOUSEKEEPER`, `IT_AGENT`…) | No | The owner's say-so |
| Which AI model each agent uses | No | Owner decision |
