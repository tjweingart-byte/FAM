# The history archive on MongoDB Atlas — setting it up

PROBLEMS.md §306. The code is `history_archive.py`; the agent that will fill
it is described in `docs/FAM_AGENTS.md` (the Database inventory specialist).

## What it does today

- **After every episode is written and cached**, its articles go into the
  archive's `intake` collection: URL, title, outlet, grade, publish date and
  the passages the search returned. The episode's list of article links goes
  into `episode_sources`, for a sources view on the episode screen later.
  Private searches and attached documents are never archived.
- **Beside every search**, the archive's `facts` are searched by meaning
  (Atlas Vector Search). Anything relevant that was published before the
  search's recency window is added to the writer's packet as dated **earlier
  reporting**, never as a current state. The search never waits for this for
  more than half a second.
- **`facts` is empty until the inventory agent is built.** That agent will
  digest `intake` into key sentences and figures. Until then the recall finds
  nothing, and the intake keeps every article so nothing is lost.

Without Atlas, the same thing runs on a local file (`/data/archive.db`).
Production uses that file until you switch it over. Staging always uses its
own file and never reaches Atlas.

## What you do, once

### 1. Create the cluster

1. Sign in at <https://cloud.mongodb.com> and create a project (e.g. `FAM`).
2. **Create a cluster:** choose **Flex**, provider **AWS**, region
   **Oregon (us-west-2)**. That is beside Render's default region, which keeps
   each lookup to a few milliseconds of network. Name it e.g. `fam-archive`.
3. Move to **M10** (dedicated) at about 1,000 monthly listeners, or when
   Atlas reports the cluster near its limits (PROBLEMS.md §305 has the
   projections). The move is a button in Atlas; nothing in FAM changes.

### 2. Make a database user

**Security → Database Access → Add New Database User**: password
authentication, a generated password, the built-in role **Read and write to
any database** (or a custom role limited to the `fam` database). Keep the
password for step 4.

### 3. Let Render reach it

**Security → Network Access → Add IP Address.** Render's outbound addresses
for the `fam` service are under **Settings → Outbound IP addresses** in the
Render dashboard (paid services only). Add each one.

`0.0.0.0/0` (anywhere) also works, but only the password then protects the
database. Use it only if Render's plan has no fixed addresses, and with a
long generated password.

### 4. Copy the connection string

**Database → Connect → Drivers → Python.** It looks like:

    mongodb+srv://<user>:<password>@fam-archive.xxxxx.mongodb.net/?retryWrites=true&w=majority&appName=fam-archive

Put the password in. This string is the credential: it never goes into a
file in the repository.

### 5. Create the index and prove it works (from your computer)

```bash
pip install -r requirements-archive.txt
MONGODB_URI='mongodb+srv://...' python tools/archive_setup.py
```

Better: put `MONGODB_URI` in `~/.fam/env` or your secrets manager
(`FAM_SECRETS`, see CREDENTIALS.md) instead of typing it in the shell.

The tool connects and times the round trip, then creates the indexes,
including the vector index `facts_vector`; it waits up to ten minutes for
that index to build the first time. Then it writes a probe fact, finds it
again through the same vector search the app uses, and deletes it. It ends
with **Everything answered** and exits 0 only if every step worked. If your
computer's address isn't in Network Access, add it for this step.

### 6. Turn it on in Render

On the **fam** service's **Environment** page:

| Key | Value |
|---|---|
| `MONGODB_URI` | the string from step 4 |
| `ARCHIVE_BACKEND` | `atlas` |

Save, and let it redeploy. Then open `https://<your app>/api/health` and
look at `archive`. It should say `"enabled": true, "backend": "atlas"`.
After a few episodes, `intake_articles` and `episodes_linked` should be
climbing, with `errors` at 0.

If `enabled` is false, `reason` says why, in one sentence. Most often it's a
missing `MONGODB_URI`, or an address Atlas refused (step 3).

## Settings

All of them are in `.env.example`, with the defaults that `config.py` uses:
`ARCHIVE`, `ARCHIVE_BACKEND`, `ARCHIVE_DB_NAME` (`fam`),
`ARCHIVE_TIMEOUT_SECONDS`, `ARCHIVE_RECALL`, `ARCHIVE_GRACE_SECONDS`,
`ARCHIVE_RECALL_LIMIT`, `ARCHIVE_MIN_SIMILARITY` and `ARCHIVE_INTAKE_MAX`.

## What it costs

- Flex is about $8–30 a month; M10 is about $57. These are list prices
  reported mid-2026; check MongoDB's pricing page.
- FAM sends a handful of small writes per episode and one vector search per
  search, so traffic is far inside either tier's limits.
- Atlas bills for the cluster, not per request.

## If something goes wrong

- Every failure is counted on `/api/health` > `archive` (`errors`,
  `last_error`) and listed on `/admin`'s errors.
- After a failure the archive backs off for a minute.
- An unreachable Atlas costs an episode nothing: the episode is written
  exactly as it would be without the archive.
- `ARCHIVE=0` turns the archive off entirely. `ARCHIVE_RECALL=0` keeps the
  writing but stops adding history to packets.
