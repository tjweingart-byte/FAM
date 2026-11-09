# 7. MongoDB Atlas: the history archive

After every episode, FAM files the news articles it used in an archive on
**MongoDB Atlas**. When someone searches, FAM looks in the archive for
earlier reporting on the subject and gives it to the writer, clearly dated.

**If the archive breaks, episodes still work.** FAM waits for it at most
half a second, then carries on without it. So an archive problem is never
an emergency. It just makes episodes slightly less informed.

| | |
|---|---|
| Where | `https://cloud.mongodb.com` → project **FAM** → the cluster (the database server) |
| Two logins ("database users") | One that can **read and write**, stored on Render as `MONGODB_URI`. One that can **only read**, used by the retriever agent Sonic, stored as `MONGODB_READ_URI`. |
| Staging | Never uses Atlas |

**TODO(owner):** confirm production has `ARCHIVE_BACKEND` = `atlas` on
Render, and write the cluster's name and tier here.

---

## Task 1. Check it is working

1. Open `<production address>/api/health` in Firefox. In **Filter JSON**
   type `archive`. Choose the section named `archive` that contains
   `enabled` ([how](README.md#reading-the-health-page)).

   > **Check:**
   > - `enabled` is `true` and `backend` is `atlas`;
   > - `errors` is `0`, or low (these count since the last deploy);
   > - `intake_articles` goes up during the day as episodes are made.
   >
   > **If `enabled` is `false`:** the `reason` beside it says why, in one
   > sentence. The usual one is that Atlas refused the connection: go to
   > [Task 2](#task-2-atlas-refuses-the-connection).

---

## Task 2. Atlas refuses the connection

Atlas only accepts connections from a list of approved internet addresses
(IP addresses). If Render's address changes, it must be added.

1. Render → **fam** → **Settings** (or **Connect**) → find **Outbound IP
   Addresses**. Copy each line exactly as shown, including any `/32` at the
   end.
2. Atlas → **Security** (left menu) → **Network Access**.
3. Compare the lists. For each Render address that is missing: **Add IP
   Address** → paste it → **Confirm**.

   > **Check:** the new address shows **Active** within a minute or two.
   >
   > **If not:** you cannot see Network Access, or the button is grey: your
   > Atlas role does not allow it. Tell the owner.

4. Wait two minutes. Then health page → `archive`.

   > **Check:** `enabled` is `true`, and `errors` has stopped rising.
   >
   > **If not:** the `reason` now says something else. Paste it into a
   > Claude session with the "Find out what is wrong in production" prompt
   > ([03](03_CLAUDE.md#prompts-to-copy)), or send it to the owner.

**Never add `0.0.0.0/0`** ("allow access from anywhere") without the owner.

---

## Task 3. Change a database password

Only if a password leaked, or the owner asks. Do this with nobody watching
your screen.

1. Atlas → **Security** → **Database Access**. Find the user (the
   read/write one, or the read-only one) → **Edit** → **Edit Password** →
   **Autogenerate Secure Password** → **Copy**. Do not click **Update
   User** yet.
2. In another tab: Render → **fam** → **Environment** → **Edit**. Click the
   eye icon on `MONGODB_URI` (for the read/write user) or `MONGODB_READ_URI`
   (for the read-only one). The value looks like
   `mongodb+srv://username:OLDPASSWORD@cluster.xxxxx.mongodb.net/?…`
3. Replace **only the old password**: the text between the `:` after the
   username and the `@`. Paste the new password there. Leave everything else
   exactly as it was.
4. Back in Atlas: click **Update User**.
5. In Render, click **Save, rebuild, and deploy**.

   Between steps 4 and the deploy finishing, the archive is off. That is
   expected: episodes still work.

6. After `Deploy live`, health page → `archive`.

   > **Check:** `enabled` is `true`. For the read-only user, also filter
   > for `connection` and look under `archive → retriever`: it says
   > `atlas read-only user`.
   >
   > **If not:** the password may have been pasted with a character missing.
   > Repeat steps 1-5.

**Never put the same connection text in both `MONGODB_URI` and
`MONGODB_READ_URI`.**

---

## Emergency switches

You may use these without asking. Tell the owner afterwards. Set them on
Render **fam** → **Environment** ([how](04_RENDER.md#task-4-change-a-setting-environment-variable)).

| Set | Result |
|---|---|
| `ARCHIVE_RECALL` = `0` | Articles are still filed, but no earlier reporting is added to episodes |
| `ARCHIVE` = `0` | The archive is off completely |

When the problem is fixed, delete the setting again.

---

## Scaling

The cluster is on the small **Flex** tier. At about 1,000 monthly listeners,
or if Atlas emails that the cluster is near its limits, it should move to
**M10** (about $57 a month). Forward the email to the owner: it is their
decision. **Do not click any "Upgrade" button** in Atlas or its emails.

## Never

- Open **Browse Collections** (it shows the data, with delete buttons). The
  agents' memory is in there, and it cannot be rebuilt.
- Give the read-only user write rights.
