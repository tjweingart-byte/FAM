# FAM backend operations handbook

**Who this is for:** the person who looks after FAM's backend during the
work week, when the owner is not available. You do not need to know Git,
Render or FAM's code. You need a web browser, the logins in
[01_ACCESS.md](01_ACCESS.md), and this handbook.

**What you will be able to do:** ship a change Claude has made (through
staging, then to production), undo a bad change, check that everything is
healthy, and handle the common problems. You will also know what to leave
for the owner.

> **Draft.** Written 2026-11-08. Anything marked **TODO(owner)** is a fact
> only the owner knows. The owner fills those in before the handover.

---

## How to read the instructions

Every task is a numbered list of steps. After the important steps there is
a box like this:

> **Check:** what you should see on the screen if the step worked.
>
> **If not:** what to do instead.

**Do not go on to the next step until the Check matches.** If it does not
match and the "If not" does not fix it, stop, take a screenshot, and write
down which step you were on. A half-finished task is easier to fix than one
pushed through on a guess.

Words in **bold** are the exact words of a button or a menu item. Words in
`this font` are things you type, or exact text you will see.

---

## Read in this order

| # | Document | What it teaches you |
|---|---|---|
| 0 | [00_SYSTEM_MAP.md](00_SYSTEM_MAP.md) | The pieces of FAM and how they connect. Which piece to suspect when something breaks. |
| 1 | [01_ACCESS.md](01_ACCESS.md) | The accounts you need. What you must leave for the owner. |
| 2 | [02_GITHUB.md](02_GITHUB.md) | **How a change gets to production:** a feature branch into `staging`, tested, then `staging` into `Main`. Undoing a change. |
| 3 | [03_CLAUDE.md](03_CLAUDE.md) | Asking Claude Code to make or fix a change, and checking its work. |
| 4 | [04_RENDER.md](04_RENDER.md) | The servers: watching a deploy, rolling back, changing a setting, the Shell, the database. |
| 5 | [05_RUNPOD.md](05_RUNPOD.md) | The voice: checking it, and what to do when it stops. |
| 6 | [06_CLOUDFLARE.md](06_CLOUDFLARE.md) | Where audio and pictures are stored. |
| 7 | [07_MONGODB.md](07_MONGODB.md) | The history archive. |
| 8 | [08_OTHER_SERVICES.md](08_OTHER_SERVICES.md) | Every other outside service, and what breaks when each one fails. |
| 9 | [09_AGENT_RULE_SIGNOFF.md](09_AGENT_RULE_SIGNOFF.md) | Signing off on a change to an agent's rule book. |
| 10 | [10_RUNBOOKS.md](10_RUNBOOKS.md) | Daily and weekly checks. What to do in each kind of emergency. |

There is also a slide deck that walks through all of this:
`FAM_Backend_Operations.pptx` ([how to edit it](#editing-the-deck)).

---

## The five rules of the job

1. **Look before you change anything, and look again after.** The health
   page (`/api/health`) and the admin page (`/admin`) tell you what the
   server thinks is true.
2. **Changes reach production only through GitHub.** First into `staging`,
   tested, then into `Main`. Nobody edits code on the server.
3. **Never type a password or key into a chat, a document, a commit or a
   Claude session.** Keys live only on Render's Environment page.
4. **Staging is free and safe. Production is real listeners and real
   money.** Try things on staging. On production, change one thing at a time,
   and write down what you changed and when.
5. **Some decisions are the owner's.** The list is in
   [01_ACCESS.md](01_ACCESS.md#owner-only-decisions). When in doubt, wait for the owner.

---

## The one page you will open most: the health page

Open this in your browser: `https://<production address>/api/health`
(the address is in [00_SYSTEM_MAP.md](00_SYSTEM_MAP.md#the-addresses)).

It is a long page of text in curly brackets. You do not need to read all of
it. Use your browser's find (**Ctrl+F**, or **Cmd+F** on a Mac) to jump to
the word you need. The documents tell you which word to look for, and what
it should say.

> **Check:** the page loads and starts with `{`. Firefox shows it as a
> neat, foldable list. In Chrome, tick **Pretty-print** at the top if you
> see it.
>
> **If not:** if the page does not load at all, the server is down. Go to
> [10_RUNBOOKS.md → The site is down](10_RUNBOOKS.md#the-site-is-down).

---

## Where the deeper material is

This handbook is *how to operate FAM*. The *why* is elsewhere. You do not
need these day to day:

- `docs/ONBOARDING.md`: what FAM is, and a course for learning the code.
- `docs/BACKEND.md`: every part of the server.
- `docs/FINANCIAL.md`, `docs/SCALING_TIMELINE.md`: costs, and when to upgrade.
- `STAGING.md`, `DEPLOY.md`, `CREDENTIALS.md`, `POSTGRES.md`,
  `REMOTE_VOICE.md`, `AUDIO_STORE.md`, `ARCHIVE.md`: the history of how each
  service was set up.
- `CLAUDE.md`: the owner's settled decisions. Claude reads it every session.
- `PROBLEMS.md`: the engineering log of every problem and its fix.

If this handbook and a dashboard disagree, **the dashboard is right**. Ask
a Claude session to correct the handbook.

---

## Editing the deck

The deck is built from the plain-text file `slides.md`.

1. Edit `docs/operations/slides.md`. A line starting `# ` begins a new
   slide. Lines starting `- ` are bullet points. A line starting `> ` is a
   speaker note.
2. Rebuild it, from the repository folder on a computer with Python:

       pip install -r requirements-docs.txt
       python docs/operations/build_deck.py

   > **Check:** it prints `wrote docs/operations/FAM_Backend_Operations.pptx (N slides)`.

3. Commit both files. Or ask a Claude session: "I edited
   `docs/operations/slides.md`. Rebuild the deck and push."

You can also edit the `.pptx` directly in PowerPoint or Keynote. The next
rebuild overwrites those edits, though, so lasting changes belong in
`slides.md`.
