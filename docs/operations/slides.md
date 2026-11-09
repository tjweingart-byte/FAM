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
- Every episode is the same sample: the Anthropic key setting is empty
- New episodes fail with an error: the Anthropic key was refused
- Thin or stale episodes: research keys (Perplexity, Parallel)
- Replays fail, new episodes play: Cloudflare R2
- Trending empty: GNews
> 00_SYSTEM_MAP.md, "When something is wrong, which piece to look at first".

# Access
- Own login everywhere, never a shared password
- GitHub Write (not Admin); Render Developer; RunPod Dev; Cloudflare R2 Admin; Atlas project role
- Your FAM account in FAM_ADMIN_ACCOUNTS for /admin
- Alerts: SUPPORT_EMAIL, IT_ALERT_EMAIL, MODERATION_EMAIL must reach you (one shared forwarding address is simplest)
- A production FAM account, let in from the waitlist by the owner
> 01_ACCESS.md. Fill in the table during the handover.

# What waits for the owner
- Signing an agent rule book
- Changing a settled rule in CLAUDE.md
- Buying, upgrading or cancelling a plan
- Deleting data; turning the waitlist off
- Branch protection, CODEOWNERS, who has access; the voice recording
- Exception: a leaked key. Rotate it now, tell the owner after
> 01_ACCESS.md, "Owner-only decisions".

# How to read the handbook
- Every task is numbered steps
- After the important steps, a Check box: what you should see on screen
- And an "If not" box: what to do instead
- Do not go on until the Check matches
- Stuck? Stop, screenshot, note the step. Do not push on with a guess
> README.md, "How to read the instructions".

# Reading the health page
- Open <production address>/api/health in Firefox
- Type the field name in the Filter JSON box, without quote marks
- The same word appears in many places: pick the one under the right section
- The handbook writes paths: database, then reachable, means reachable inside database
- Counters restart at every deploy: compare within the same day
- It never contains keys, so it is safe to copy into Claude
> README.md, "Reading the health page", has the table of every field the handbook uses.

# The route every change takes
- 1. Feature branch (claude/...) into staging
- 2. fam-staging updates. Test it there
- 3. staging into Main
- 4. Production updates. Check it
- 5. Main back into staging, so they match again
- Several features can ride in one batch through steps 3 to 5
> 02_GITHUB.md. Before starting: CI on Main is green, and github.com/tjweingart-byte/fam/compare/staging...Main says there is nothing to compare.

# Step 1: feature branch into staging
- Pull requests, New pull request. base: staging, compare: the claude/ branch
- GitHub starts with base: Main. Change it to staging FIRST
- Check: the line reads base: staging, compare: claude/..., and "Able to merge"
- Create pull request. Read Files changed: only what was asked, no owner-only files
- Wait 35-60 minutes. Check: "All checks have passed"
- Arrow beside the button: Create a merge commit. Then Merge, Confirm. Check: "into staging"
> 02_GITHUB.md, Step 1. Red checks: never merge; paste the error into a Claude session.

# Steps 2 and 3: watch staging, then test it
- Render, fam-staging, Events. Check: "Deploy live" within 15 minutes
- Staging /api/health: commit matches, zero_spend true, network_guard true
- Staging episodes are a sample script in a beeping tone. That is normal
- Test: sign in, search, transport, myFAM, Go Deeper, a mix, a message
- Test whatever this batch changed. Anything fails: stop, fix, start again
> 02_GITHUB.md, Steps 2 and 3.

# Step 4: staging into Main
- New pull request. base: Main, compare: staging
- Check: the commits listed are this batch (plus leftover "Main back into staging" merges)
- Title "Staging to Main: ...", list the PRs, "Tested on staging: yes"
- Wait for green checks
- Database table changing? Take a backup first
> 02_GITHUB.md, Step 4.

# Steps 5 and 6: production, then level up
- Merge. Render, fam, Events. Check: "Deploy live"
- Production /api/health: commit matches, mode live, database reachable, interim false
- Play one new episode and one replay. Check: the real voice
- Then compare staging...Main, Create pull request (base staging), merge
- Check: "There isn't anything to compare"
> 02_GITHUB.md, Steps 5 and 6. Urgent fix: base Main directly, then Step 6 straight away.

# Undoing a change
- Production broken right now: Render, Events, Rollback (2-5 minutes)
- Then always revert on GitHub: the merged PR, Revert, merge the revert
- A rollback alone lasts only until the next merge
- Then Main back into staging (Step 6)
- Conflicts: ask Claude to merge staging in. Never use the web conflict editor
> 02_GITHUB.md, "Undoing a change"; 04_RENDER.md, Task 3.

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
> 03_CLAUDE.md, "Task 2. Check Claude's work before merging".

# Render
- fam: production, follows Main, auto-deploys, Postgres, no disk
- fam-staging: follows staging, no keys, no outbound network
- Both made by hand: render.yaml documents them but changes nothing
- Environment page holds every key. Saving redeploys
- Shell runs FAM's own tools against production. Never edit files there
> 04_RENDER.md

# Render: deploy and rollback
- Watch: fam, then Events. Then the health page: build, short matches the merge
- Failed deploy: the old version keeps serving. Read the log
- Bad deploy: Events, then Rollback. Then revert the PR on GitHub
- A rollback is temporary and never rolls back the database
- Before a PR that changes a table's shape: take a database backup
> 04_RENDER.md, "Rollback" and "Backups".

# RunPod: the voice
- Serverless endpoint FAM (g65qlmwz4sc02u), built from Main's Dockerfile.voice
- Scales to zero, max 4 workers, idle timeout 180 s
- The app emails SUPPORT_EMAIL when the voice goes down
- Check: health page tts, and python tools/voice_doctor.py --speak in Render Shell
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
- 3. The operator reviews it against the checklist and comments "Reviewed for signing"
- 4. The owner signs on any computer with tools/sign_rulebooks.py, and uploads the files in one commit
- 5. The session verifies the signature and cleans up. Merge. Check health
> 09_AGENT_RULE_SIGNOFF.md. Open decision for the owner: keep signing owner-only (recommended), sign remotely on request, or add a second key (a signed code change).

# Review checklist for a rule-book proposal
- Only the intended rule changed
- Does not contradict every-agent.md or another agent's book
- Does not loosen a settled constraint in CLAUDE.md
- Gives the agent no new power: no writes, no settings, no new service
- Reason written in PROBLEMS.md; the file is the whole book; no stray files
> 09_AGENT_RULE_SIGNOFF.md, Task 2, Step 4.

# Runbooks
- Site down: Events, Rollback, Logs, status pages, then call the owner
- Voice: /api/health tts, voice_doctor, RunPod Builds, Workers, Billing
- Sample everywhere: key setting empty. Episodes fail: key refused, limit, outage
- Accounts missing: change nothing that writes, and call the owner
- Key leaked: revoke and replace now, tell the owner after
> 10_RUNBOOKS.md has every runbook step by step.

# Every day, every week
- Daily: health page, reported content (answer within 24 hours), /admin errors and outside services, CI on Main, inboxes, play two episodes
- Weekly: spend on every dashboard, usage_report, waitlist outbox, stale PRs
- Keep an incident note; send it to the owner
> 10_RUNBOOKS.md, "Routine checks".

# Before the handover
- Fill in every TODO(owner) in docs/operations/
- Grant each account; add the operator to the alert inboxes and FAM_ADMIN_ACCOUNTS
- Confirm branch protection on Main and staging
- Set spend limits and alerts: Anthropic, RunPod
- Decide: staging-first flow, and how rule books get signed while away
- Let the operator's production account in from the waitlist; make a second staging test account
- Fill in vendor logins (08 Task 2) and the emergency contact (10)
- Trial week: open every page the handbook uses, ship one change, one rollback on staging
> grep -rn "TODO(owner)" docs/operations/ lists them all.
