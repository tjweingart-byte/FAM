# FAM Backend Operations
- Keeping FAM running during the work week
- Handbook: docs/operations/
> Draft, 2026-11-08. Every slide points to a page in docs/operations/ with the full steps. Edit this file and run `python docs/operations/build_deck.py` to rebuild.

# Why this exists
- One person built the backend and knows how to run it
- That person is away Monday to Friday
- Someone must own production in the meantime: ship, roll back, respond
- The handbook is how. This deck is the tour.
> The goal: the operator can keep production running, ship a change Claude made, undo a bad one and handle the common incidents without the owner, and knows what to leave for the owner.

# The five rules of the job
- Look before you change: /api/health and /admin, before and after
- Code reaches production only through a merged PR with green CI
- Never type a secret into a chat, a commit, a doc or a Claude session
- Staging spends nothing; production spends real money. One change at a time
- Some decisions wait for the owner
> README.md

# The system on one page
- GitHub (tjweingart-byte/fam): the code. Branch Main = production
- Render: fam (production) and fam-staging (zero spend), plus Postgres
- RunPod Serverless: the voice, rebuilt from Main on every push
- Cloudflare R2 (fam-audio): kept audio and every picture
- MongoDB Atlas: the history archive (earlier reporting)
- Anthropic: Claude writes the episodes, and Claude Code writes the code
- Around a dozen data vendors: research, news, sports, markets, weather
> 00_SYSTEM_MAP.md. Walk through one episode: account check in Postgres, Claude decides the search, Perplexity/Parallel/GDELT plus BALLDONTLIE/Tiingo/weather plus Atlas, Claude writes, RunPod voices, Render streams, R2 keeps. A replay is a link to R2.

# Symptom to suspect
- Site will not load: Render fam, then Postgres
- Placeholder tone, or stuck at "Generating the audio": RunPod
- Every episode is the same sample script: the Anthropic key
- Thin or stale episodes: research keys (Perplexity, Parallel)
- Replays fail, new episodes play: Cloudflare R2
- Trending empty: GNews
> 00_SYSTEM_MAP.md, "One episode, crossing the services".

# Access
- Own login everywhere, never a shared password
- GitHub Write (not Admin); Render Developer; RunPod Dev; Cloudflare R2 Admin; Atlas project role
- Your FAM account in FAM_ADMIN_ACCOUNTS for /admin
- Join the alert inboxes: SUPPORT_EMAIL, IT_ALERT_EMAIL, MODERATION_EMAIL
> 01_ACCESS.md. Fill in the table during the handover.

# What waits for the owner
- Signing an agent rule book
- Changing a settled rule in CLAUDE.md
- Buying, upgrading or cancelling a plan
- Deleting data; turning the waitlist off
- Branch protection, CODEOWNERS, who has access; the voice recording
- Exception: a leaked key. Rotate it now, tell the owner after
> 01_ACCESS.md, "Owner-only decisions".

# GitHub: how a change ships
- Claude pushes to its own claude/... branch
- You open a pull request into staging (or Main)
- CI runs: tests, interface checks, browser smoke runs. Green or do not merge
- Read "Files changed": only what was asked? Any owner-only file?
- Merge, then watch Render deploy and check build.commit
> 02_GITHUB.md. Decide with the owner: staging first (recommended while away), or straight to Main as recent PRs did.

# GitHub: when things go wrong
- Bad merge: the Revert button on the PR, then merge the revert
- Conflicts: ask a Claude session to merge Main in and resolve them
- Red CI on a PR: paste the failing step into a session on that branch
- Red CI on Main: urgent, hold every other merge until it is green
- Never force-push Main or staging; never skip a test to get green
> 02_GITHUB.md

# Claude Code
- claude.ai/code, repository tjweingart-byte/fam, one task per session
- Each session works on its own branch and opens no PR unless asked
- It follows the ship loop: ./dev.sh check, republish the preview, summary
- It cannot see dashboards, hear the voice or judge writing quality
- Never give it a secret. Give it /api/health output and logs instead
> 03_CLAUDE.md has prompts that work, and how to check its work before merging.

# Checking Claude's work
- It quotes "all checks passed", and CI on the PR is green
- Files changed match the request, and nothing else
- No rule book, CLAUDE.md rule, CODEOWNERS or deletion
- Look at the preview on a phone for anything visible
- Unsure? A fresh session reviews the PR without changing anything
> 03_CLAUDE.md, "Checking Claude's work before you merge".

# Render
- fam: production, follows Main, auto-deploys, Postgres, no disk
- fam-staging: follows staging, no keys, no outbound network
- Both made by hand: render.yaml documents them but changes nothing
- Environment page holds every key. Saving redeploys
- Shell runs FAM's own tools against production. Never edit files there
> 04_RENDER.md

# Render: deploy and rollback
- Watch: fam, then Events. Check /api/health build.commit afterwards
- Failed deploy: the old version keeps serving. Read the log
- Bad deploy: Events, then Rollback. Then revert the PR on GitHub
- A rollback is temporary and never rolls back the database
- Before a PR that changes a table's shape: take a database backup
> 04_RENDER.md, "Rollback" and "Backups".

# RunPod: the voice
- Serverless endpoint FAM (g65qlmwz4sc02u), built from Main's Dockerfile.voice
- Scales to zero, max 4 workers, idle timeout 180 s
- The app emails SUPPORT_EMAIL when the voice goes down
- Check: /api/health tts, and python tools/voice_doctor.py in Render Shell
- Stalls: idle timeout, Requests delay, GPU supply, a fresh build
- No fallback voice, by design. Never touch the voice recording
> 05_RUNPOD.md

# Cloudflare R2
- Bucket fam-audio: recent/ (7 days), kept/ (saved/shared), images/
- Replays are signed links straight to R2: free to serve
- Check /api/health audio: check.ok, links.fallbacks near 0
- New app address: add it to the bucket's CORS policy
- Emergency: AUDIO_LINKS=0 streams replays through Render
> 06_CLOUDFLARE.md

# MongoDB Atlas
- The history archive: past articles and facts, filed after every episode
- Never on an episode's critical path: an outage is a quality problem
- Two users: read/write (MONGODB_URI) and read-only for Sonic
- Connection refused? Render's outbound IPs must be in Network Access
- Emergency: ARCHIVE_RECALL=0 or ARCHIVE=0
> 07_MONGODB.md

# Everything else
- Research: Perplexity, Parallel, GDELT, Wikimedia
- Live: GNews (Trending), BALLDONTLIE (sports), Tiingo (markets), Polymarket, NWS, Open-Meteo
- Pictures: Gemini. Email: SMTP. Sign-in: Google, Apple. Waitlist: Viral Loops
- Losing one makes episodes worse, never absent. /admin shows each one's use
- If email breaks, alerts stop. Check it first when things are oddly quiet
> 08_OTHER_SERVICES.md

# Agent rule books: the lock
- Eight agents, each with a plain-English rule book in docs/agent_rules/
- MANIFEST.json holds every book's fingerprint, signed with the owner's key
- A changed or unsigned book holds its agent. FAM runs on without it
- CI fails on an unsigned book. CODEOWNERS requires the owner's review
- See it: /api/health, agents, lock (signed, version, held)
> 09_AGENT_RULE_SIGNOFF.md

# Signing off on an agent document
- 1. Ask a Claude session for a proposal, never an edit of the signed book
- 2. It writes agent_rule_proposals/<book>.md and MANIFEST.pending.json
- 3. The operator reviews it against the checklist and approves the PR
- 4. The owner signs on their Mac and uploads three files in one commit
- 5. The session verifies the signature and cleans up. Merge. Check health
> 09_AGENT_RULE_SIGNOFF.md. Open decision for the owner: keep signing owner-only (recommended), sign remotely on request, or add a second key (a signed code change).

# Review checklist for a rule-book proposal
- Only the intended rule changed
- Does not contradict every-agent.md or another agent's book
- Does not loosen a settled constraint in CLAUDE.md
- Gives the agent no new power: no writes, no settings, no new service
- Reason written in PROBLEMS.md; the file is the whole book; no stray files
> 09_AGENT_RULE_SIGNOFF.md, step 3.

# Runbooks
- Site down: Events, Rollback, Logs, status pages, then call the owner
- Voice: /api/health tts, voice_doctor, RunPod Builds, Workers, Billing
- Sample script everywhere: Anthropic key, spend limit, outage
- Accounts missing: change nothing that writes, and call the owner
- Key leaked: revoke and replace now, tell the owner after
> 10_RUNBOOKS.md has every runbook step by step.

# Every day, every week
- Daily: /api/health, /admin errors and outside services, CI on Main, inboxes, play two episodes
- Weekly: spend on every dashboard, usage_report --flagged, moderation, waitlist outbox, stale PRs
- Keep an incident note; send it to the owner
> 10_RUNBOOKS.md, "Routine checks".

# Before the handover
- Fill in every TODO(owner) in docs/operations/
- Grant each account; add the operator to the alert inboxes and FAM_ADMIN_ACCOUNTS
- Confirm branch protection on Main and staging
- Set spend limits and alerts: Anthropic, RunPod
- Decide: staging-first flow, and how rule books get signed while away
- One supervised week: the operator ships one change and runs one rollback on staging
> grep -rn "TODO(owner)" docs/operations/ lists them all.
