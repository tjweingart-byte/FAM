# 9. Signing off on agent documents

FAM has eight agents (Netenyahu, C3P0, Shakespeare, Mclovin, Consuela,
Sonic, Keith Wong, Nando). Each has a **rule book** in plain English in
`docs/agent_rules/`: what it must always and must never do. The owner's
standing decision (2026-10-29) is that a rule book is "untouchable and
unchangeable unless we deliberately say to have them change ... not by an
agent and not by a model".

That is enforced in code, so it is worth understanding before you touch
anything in that folder.

## How the lock works

```
docs/agent_rules/
  every-agent.md, c3p0.md, consuela.md, keith.md, mclovin.md,
  nando.md, netenyahu.md, shakespeare.md, sonic.md      the rule books
  (and docs/journalism_playbook.md, Shakespeare's playbook, also signed)
  MANIFEST.json       the SHA-256 fingerprint of every book, and a version number
  MANIFEST.json.sig   the owner's Ed25519 signature of MANIFEST.json
  owner.pub           the owner's public key (checks signatures, cannot make them)
```

- **An agent runs only while its book's fingerprint matches the signed
  manifest.** A changed or unsigned book **holds** that agent: it stops,
  and the pipeline runs as it did before the agent existed. Nothing breaks
  for listeners. The agent just stops working.
- **A test fails** (`tests/test_agent_rulebooks.py`) if any book does not
  match the signed manifest, so an unsigned change cannot pass CI.
- **Only the owner's private key can sign.** It is never in the repository,
  on the server, in `FAM_SECRETS` or in a Claude session.
- **CODEOWNERS** requires the owner's review on `docs/agent_rules/`, the
  playbook, the lock code and the manifest tool (when branch protection has
  Code Owner review on; see [02_GITHUB.md](02_GITHUB.md#branch-protection-and-codeowners)).
- The owner also keeps the governing copies **outside Claude**. If those and
  the repository ever disagree, the held copy wins and the repository is
  corrected.

**Where to see it:** `/api/health` → `agents` → `lock`: `signed: true`, the
`version`, and `held` (empty when nothing is held; otherwise which agent and
which file). `/admin` shows the same. From a checkout:

    python tools/rulebook_manifest.py            # every book: matches / differs
    python tools/rulebook_manifest.py --verify   # checks the signature

## Who does what

| Step | Who |
|---|---|
| Ask for a change | The owner, or the operator after seeing an agent misbehave |
| Write the proposal | A Claude session |
| Review the proposal | **The operator** (first read) and the owner |
| **Sign it** | **The owner only.** |
| Verify, clean up, merge | A Claude session, then whoever merges |
| Update the copy held outside Claude | The owner |

**What "sign off" means for the operator:** you review and approve the
*proposal* so that, when the owner has five minutes, signing is the only
step left. You cannot make a change take effect. That is by design.

**TODO(owner):** decide whether that is enough while you are away. The
options:

1. **Keep it as is.** Rule-book changes wait for the owner's weekend.
   Agents keep running on the current signed books. (Recommended. Rule-book
   changes are rare and never urgent: a held agent harms nothing.)
2. **Sign remotely when asked.** The steps below need only a browser and the
   owner's Mac, about five minutes.
3. **Give the operator their own signing key.** The code checks one public
   key (`owner.pub`, `AGENT_RULES_PUBLIC_KEY`), so this means either
   sharing the key (not recommended; it removes the point of the lock) or a
   code change to accept a second key, which is itself a signed,
   CODEOWNERS-reviewed change.

## The process, step by step

### 1. Ask for the change

Start a Claude session ([03_CLAUDE.md](03_CLAUDE.md)) and say what should
change and why. For example:

> Mclovin keeps flagging quoted speech as opinion. Propose a change to
> `mclovin.md` so that quoted speech attributed to a named source is not
> opinion. Read `docs/agent_rules/README.md`, `docs/agent_rule_proposals/README.md`
> and the Mclovin section of `docs/FAM_AGENTS.md` first. Write it as a
> proposal for the owner to sign. Do not edit the signed book.

### 2. The session writes a proposal

It must:

- Put the **whole proposed book** at `docs/agent_rule_proposals/<book>.md`.
  It never edits `docs/agent_rules/<book>.md` itself.
- Write `docs/agent_rules/MANIFEST.pending.json` (the manifest as it will be
  once the proposal replaces the book; only that book's fingerprint and the
  version change). `python tools/rulebook_manifest.py --write` does this.
  The lock ignores the pending file, so CI stays green.
- List it under **Waiting now** in `docs/agent_rule_proposals/README.md`.
- Add a `PROBLEMS.md` section saying what changes and why.
- Push to its branch. Open a PR from it ([02_GITHUB.md](02_GITHUB.md)).

### 3. Review it (the operator, then the owner)

Open the PR. Compare the proposal with the signed book: on GitHub, open
both files side by side, or ask a session for "the diff between
`docs/agent_rule_proposals/<book>.md` and `docs/agent_rules/<book>.md`".

Approve only if every answer is yes:

- [ ] **Only the intended rule changed.** No other wording moved.
- [ ] **It does not contradict `every-agent.md`** (the rules every agent follows) or another agent's book.
- [ ] **It does not loosen a settled constraint** in `CLAUDE.md` (for example: nothing speaks before its material; never invent a current fact; slurs are the only words removed). If it does, it is the owner's decision alone.
- [ ] **It does not give an agent a new power**: writing a file or a setting, changing a rule, reaching a listener's private data, calling a new outside service.
- [ ] **The reason is written down** in `PROBLEMS.md` and agrees with `docs/FAM_AGENTS.md`.
- [ ] **The proposed file is complete**: the whole book, not a fragment.
- [ ] **No other files sneak in** to `docs/agent_rules/` (only the pending manifest).

Then leave an approving review on the PR with "Reviewed for signing" and
tell the owner it is waiting.

### 4. The owner signs (on the owner's Mac, with the owner's key)

This is `docs/agent_rule_proposals/README.md`, "Signing it". No clone is
needed:

1. From GitHub, on the PR's branch, download
   `docs/agent_rule_proposals/<book>.md` and
   `docs/agent_rules/MANIFEST.pending.json` to Downloads. Rename the second
   to `MANIFEST.json`.
2. In Terminal:

       conda activate famsign                     # OpenSSL 3
       # save the private key from the password manager, as plain text, to ~/fam-rules-signing.pem
       cd ~/Downloads
       openssl pkeyutl -sign -rawin -inkey ~/fam-rules-signing.pem -in MANIFEST.json | base64 | tr -d '\n' > MANIFEST.json.sig
       rm -P ~/fam-rules-signing.pem

   Never paste the private key into Terminal, a chat or a session.
3. On GitHub, in `docs/agent_rules/` on the branch: **Add file → Upload
   files**: `<book>.md`, `MANIFEST.json`, `MANIFEST.json.sig`, in **one
   commit**. Nothing else; check no stray file came with them.

**Upload the files. Do not edit a rule book in GitHub's web editor.** The
signature covers the exact bytes, and the editor can change a line ending or
the final newline, which makes the book differ from what was signed.

### 5. Verify, clean up, merge

Ask the session on that branch:

> The owner has uploaded the signed `<book>.md`, `MANIFEST.json` and
> `MANIFEST.json.sig`. Verify the signature, delete the proposal and the
> pending manifest, update the proposals README, run `./dev.sh check`, push.

It runs `python tools/rulebook_manifest.py --verify`, which must say
`signed: True` and every book `matches`. CI must be green. Then the owner
(as code owner) approves and the PR is merged.

### 6. Check production

After the deploy: `/api/health` → `agents.lock` → `signed: true`, the new
`version`, `held` empty. If an agent is held, `held` names the file that
differs. Do not try to fix it by editing the book; tell the owner.

The owner then updates the copy held outside Claude.

## If an agent is held and nobody changed anything

Something changed a book's bytes, or the manifest. Most likely a merge that
touched `docs/agent_rules/` or `.gitattributes`.

1. `/api/health` → `agents.lock.held` names the file.
2. Find the PR that last touched it (GitHub → the file → History).
3. Revert that PR ([02_GITHUB.md](02_GITHUB.md#undo-a-merge-revert)).
4. Tell the owner.

A held agent is safe. FAM runs without it. So this is never an emergency
that justifies unsigned changes.

## Other agent documents

Not everything about the agents is signed:

| Document | Signed? | Who approves |
|---|---|---|
| `docs/agent_rules/*.md`, `docs/journalism_playbook.md` | **Yes** | Owner signs |
| `docs/FAM_AGENTS.md` (each agent's reasoning, guardrails, plugins) | No | Normal PR review; but if it changes what a rule *means*, the rule book must change too, and that is signed |
| `docs/AGENTS_TRAINING_PLAN.md`, `docs/AGENTS_BUILD_PROMPT.md`, prompts like `docs/TRAINING_NEXT_BRANCH.md` | No | Normal PR review |
| Agent code (`agents/*.py`) | No, except `agents/rulebooks.py` (CODEOWNERS) | Normal PR review; CI |
| Agent switches on Render (`AGENTS`, `HOUSEKEEPER`, `IT_AGENT`, the dry-run flags) | No | The owner's say-so, or a session's written instruction that the owner agreed to |
| Each agent's model (`agents/models.py`, pinned vendor and model id) | No | Owner decision |
