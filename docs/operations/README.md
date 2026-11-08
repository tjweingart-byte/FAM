# FAM backend operations handbook

**Who this is for:** the person responsible for FAM's backend during the
work week, when the owner is not available. It assumes you can use a
terminal and a web dashboard. It does not assume you know Git, Render or
any of FAM's code.

**What it promises:** with this handbook and the accounts listed in
[01_ACCESS.md](01_ACCESS.md), you can keep production running, ship a
change Claude has made, roll back a bad one, and handle the common
incidents without the owner. You will know what to leave for the owner too.

> **Draft.** Written 2026-11-08 from the repository as it stood at
> `e744c8a`. Anything marked **TODO(owner)** is a fact only the owner
> knows: an account, a login, a decision. Fill those in before the
> handover.

## Read in this order

| # | Document | Answers |
|---|---|---|
| 0 | [00_SYSTEM_MAP.md](00_SYSTEM_MAP.md) | What runs where, and how one episode crosses the services. Read this first. |
| 1 | [01_ACCESS.md](01_ACCESS.md) | Every account you need, at what permission level, and the alert inboxes to join. |
| 2 | [02_GITHUB.md](02_GITHUB.md) | Branches, pull requests, CI, merging and reverting, in plain steps. |
| 3 | [03_CLAUDE.md](03_CLAUDE.md) | Working with Claude Code: starting a session, what to ask for, checking its work before you merge. Also the Anthropic API account. |
| 4 | [04_RENDER.md](04_RENDER.md) | The two web services, environment variables, deploys, rollback, the Shell, logs, the Postgres database. |
| 5 | [05_RUNPOD.md](05_RUNPOD.md) | The voice: the Serverless endpoint, its builds, and what to do when FAM goes silent. |
| 6 | [06_CLOUDFLARE.md](06_CLOUDFLARE.md) | R2, where kept audio and pictures live: the bucket, tokens, CORS, lifecycle rules. |
| 7 | [07_MONGODB.md](07_MONGODB.md) | The history archive on Atlas: users, network access, health, scaling. |
| 8 | [08_OTHER_SERVICES.md](08_OTHER_SERVICES.md) | Every other outside service (research, news, sports, markets, weather, pictures, email, waitlist, Apple), with what breaks when each one fails. |
| 9 | [09_AGENT_RULE_SIGNOFF.md](09_AGENT_RULE_SIGNOFF.md) | How a change to an agent's rule book is proposed, reviewed and signed off. |
| 10 | [10_RUNBOOKS.md](10_RUNBOOKS.md) | Incidents, step by step, and the daily and weekly checks. |

The slide deck that goes with these is built from
[`slides.md`](slides.md) by [`build_deck.py`](build_deck.py) into
`FAM_Backend_Operations.pptx` (see [Editing the deck](#editing-the-deck)).

## The five rules of the job

1. **Look before you change.** `https://<prod-host>/api/health` and `/admin`
   tell you what the server actually thinks. Read them before and after
   anything you do.
2. **Production changes go through GitHub.** Code reaches production only by
   a merged pull request with green CI. Nobody edits code on the server.
3. **Never type a secret into a chat, a commit, a document or a Claude
   session.** Keys live in Render's Environment page (or the secrets manager
   named by `FAM_SECRETS`), and nowhere else.
4. **Staging spends nothing and production spends real money.** Try things on
   `fam-staging`. On `fam`, change one thing at a time and write down what
   you changed.
5. **Some things wait for the owner.** Signing an agent rule book,
   rotating the owner's signing key, deleting data, buying or cancelling a
   plan, and anything that changes what FAM *is* (the rules in `CLAUDE.md`).
   See [the owner-only list](01_ACCESS.md#owner-only-decisions).

## Where the deeper material is

This handbook is the *how to operate it*. It points to the reference set
rather than repeating it:

- `docs/ONBOARDING.md`: what FAM is and a five-day curriculum for learning the code.
- `docs/BACKEND.md`: every route, background job and outside call.
- `docs/FINANCIAL.md` and `docs/SCALING_TIMELINE.md`: what each service costs and when to upgrade it.
- `STAGING.md`, `DEPLOY.md`, `CREDENTIALS.md`, `POSTGRES.md`, `REMOTE_VOICE.md`, `AUDIO_STORE.md`, `ARCHIVE.md`, `WAITLIST.md`: the setup history and the reasoning behind each service.
- `CLAUDE.md`: the settled rules. Claude reads it every session, and you should read its headings once.
- `PROBLEMS.md`: the engineering log. `PROBLEMS_INDEX.md` lists every section.

**Where a document disagrees with the code or a dashboard, the code and the
dashboard win.** Fix the document in the same change.

## Editing the deck

    pip install -r requirements-docs.txt       # python-pptx, once
    python docs/operations/build_deck.py       # slides.md -> FAM_Backend_Operations.pptx

`slides.md` is plain text: `# ` starts a slide, `- ` lines are bullets,
two spaces before `- ` indent a bullet, and a `> ` line is a speaker note.
Edit it and rebuild. You can also open the `.pptx` in Keynote or
PowerPoint and edit it there, but the next rebuild overwrites those edits,
so put lasting changes in `slides.md`.
