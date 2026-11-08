# 7. MongoDB Atlas: the history archive

The archive is FAM's memory of past reporting. After every episode, the
articles it read go into the archive. Beside every search, the archive is
searched by meaning, and anything relevant from before the search's recency
window is handed to the writer as dated **earlier reporting**. Consuela (the
housekeeper agent) digests articles into facts; Sonic (the retriever agent)
reads them. Full setup: `ARCHIVE.md`.

**The archive is never on an episode's critical path.** If Atlas is slow or
down, the search waits at most half a second, and the episode is written
exactly as it would be without it. So an Atlas problem is never an outage.
It is a quality problem.

| | |
|---|---|
| Where | cloud.mongodb.com → project **FAM** → cluster (e.g. `fam-archive`), **Flex** tier, AWS Oregon (us-west-2) |
| Database | `fam` (`ARCHIVE_DB_NAME`) |
| Collections | `intake` (raw articles), `episode_sources`, `facts` (digested, with a vector index `facts_vector`), `live_series` (scores and prices over time) |
| Users | A **read/write** user (in `MONGODB_URI`) for the app and Consuela; a **read-only** user (in `MONGODB_READ_URI`) for Sonic. Never the same string. |
| On Render `fam` | `ARCHIVE_BACKEND=atlas`, `MONGODB_URI`, `MONGODB_READ_URI` |
| Staging | Never reaches Atlas. Uses its own local file. |

**TODO(owner):** confirm production has `ARCHIVE_BACKEND=atlas` set (until
it is, the archive is a local file), and write the cluster's name and tier
here.

## Is it working?

    curl -s https://<prod-host>/api/health | python3 -m json.tool | grep -A 30 '"archive"'

- `enabled: true`, `backend: atlas`
- `intake_articles` and `episodes_linked` climbing day to day
- `errors` at 0, `last_error` empty
- `retriever.connection: atlas read-only user`

If `enabled` is false, `reason` says why in one sentence. Usually a missing
`MONGODB_URI`, or Atlas refusing Render's address (below).

## Common tasks

### Atlas refuses connections ("not authorized", timeouts)

Atlas only accepts connections from listed addresses.

1. Render → `fam` → Settings → **Outbound IP addresses**: copy them.
2. Atlas → **Security → Network Access**: each of those must be listed.
   Add any that are missing.
3. `/api/health` → `archive` should recover within a minute (the archive
   backs off for a minute after a failure).

Render can change outbound addresses when a service moves. If the archive
suddenly fails everywhere with no deploy, check this first.

### Rotate a database password

1. Atlas → **Security → Database Access** → the user → Edit → generate a
   new password.
2. Build the new connection string (Database → Connect → Drivers → Python),
   with the new password.
3. Render → `fam` → Environment → `MONGODB_URI` (or `MONGODB_READ_URI` for
   Sonic's user) → Save.
4. Check `/api/health` → `archive`.

### Check or rebuild the indexes

From your own computer, with the read/write connection string in your
environment (never typed into a shared document):

    pip install -r requirements-archive.txt
    MONGODB_URI='mongodb+srv://...' python tools/archive_setup.py

It connects, creates any missing index, writes a probe fact, finds it by
vector search, deletes it, and ends **Everything answered**. Your
computer's address must be in Network Access for this.

### Scaling

Move from Flex to **M10** at about 1,000 monthly listeners, or when Atlas
warns the cluster is near its limits. It is a button in Atlas and FAM needs
no change. It costs more (about $57/month against $8-30), so it is an owner
decision. The trigger is in `docs/SCALING_TIMELINE.md`.

## Emergency switches

| Switch on Render `fam` | Effect |
|---|---|
| `ARCHIVE_RECALL=0` | Keep filing articles, but stop adding earlier reporting to episodes |
| `ARCHIVE=0` | The archive off entirely |

## Do not

- Delete collections or documents by hand. The agents' memory is in there,
  and there is no rebuild from scratch.
- Give the read-only user write rights, or reuse the read/write string for
  `MONGODB_READ_URI`.
- Open Network Access to `0.0.0.0/0` (anywhere) unless the owner agrees.
