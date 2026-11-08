# 7. MongoDB Atlas: the history archive

After every episode, FAM files the news articles it used in an archive on
**MongoDB Atlas**. When someone searches, FAM looks in the archive for
earlier reporting on the subject and gives it to the writer, clearly dated.

**If the archive breaks, episodes still work.** FAM waits for it at most
half a second, then carries on without it. So an archive problem is never
an emergency. It just makes episodes slightly less informed.

| | |
|---|---|
| Where | `https://cloud.mongodb.com` → project **FAM** → the cluster |
| Two logins (database users) | one that can **read and write** (setting `MONGODB_URI` on Render), and one that can **only read**, used by the retriever agent Sonic (setting `MONGODB_READ_URI`) |
| Staging | Never uses Atlas |

**TODO(owner):** confirm production has `ARCHIVE_BACKEND` = `atlas` on
Render, and write the cluster's name and tier here.

---

## Task 1. Check it is working

1. Open `<production address>/api/health`. Find (Ctrl+F) `"archive"`.

   > **Check:**
   > - `"enabled": true` and `"backend": "atlas"`
   > - `"errors": 0`
   > - `"intake_articles"` is higher than yesterday
   >
   > **If `enabled` is false:** the `"reason"` beside it says why in one
   > sentence. The usual one is that Atlas is refusing the connection:
   > go to [Task 2](#task-2-atlas-refuses-the-connection).

---

## Task 2. Atlas refuses the connection

Atlas only accepts connections from a list of approved internet addresses.
If Render's address changes, it must be added.

1. Render → **fam** → **Settings** → find **Outbound IP Addresses**
   (sometimes under **Connect** → **Outbound**). Copy each address.
2. Atlas → **Security** (left menu) → **Network Access**.
3. Compare the lists. For each Render address that is missing: **Add IP
   Address** → paste it → **Confirm**.

   > **Check:** the new address shows **Active** within a minute or two.

4. Wait two minutes, then open `/api/health` → `"archive"`.

   > **Check:** `"enabled": true`, and `"errors"` has stopped rising.
   >
   > **If not:** the reason now says something else. Paste it into a Claude
   > session ("What does this mean and what should I check?") or send it to
   > the owner.

Never add `0.0.0.0/0` ("allow access from anywhere") without the owner.

---

## Task 3. Change a database password

Only if a password leaked, or the owner asks.

1. Atlas → **Security** → **Database Access**. Find the user (the
   read/write one or the read-only one) → **Edit** → **Edit Password** →
   **Autogenerate Secure Password** → **Copy** → **Update User**.
2. Atlas → **Database** → the cluster's **Connect** → **Drivers** →
   **Python**. Copy the connection string. Replace `<password>` in it with the
   password from step 1.
3. Render → **fam** → **Environment** → **Edit**. Paste it into
   `MONGODB_URI` (for the read/write user) or `MONGODB_READ_URI` (for the
   read-only one). **Never put the same string in both.**
   **Save, rebuild, and deploy**.
4. After `Deploy live`, open `/api/health` → `"archive"`.

   > **Check:** `"enabled": true`. For the read-only user, also find
   > `"connection"` → `atlas read-only user`.

---

## Emergency switches

On Render **fam** → **Environment** ([how](04_RENDER.md#task-4-change-a-setting-environment-variable)).
Tell the owner if you use one.

| Set | Result |
|---|---|
| `ARCHIVE_RECALL` = `0` | Articles are still filed, but no earlier reporting is added to episodes |
| `ARCHIVE` = `0` | The archive is off completely |

---

## Scaling

The cluster is on the small **Flex** tier. At about 1,000 monthly listeners,
or if Atlas emails that the cluster is near its limits, it should move to
**M10** (about $57/month). Forward the email to the owner: it is their
decision.

## Never

- Delete collections or documents. The agents' memory is in there, and it
  cannot be rebuilt.
- Give the read-only user write rights.
