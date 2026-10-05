# FAM Waitlist

The pre-launch waitlist: a real FAM account, `status = 'waitlisted'`, and a
server-side gate that keeps the app closed to anybody who is not `active`.
The build spec the owner wrote is kept verbatim below. **Decisions** records
every place the build answers a question the spec left open or differs from
it, with the reason, so a later session does not quietly undo one. Update this
section when a decision changes, including why.

## How it works

* **Switch:** `WAITLIST=1` (production, set in the Render dashboard, not in
  `render.yaml`) closes the app. Launch is `WAITLIST=0`. Off, the app is
  exactly what it was, and every new account starts `active`.
* **Accounts:** `accounts.db` gains `status` (default `active`, so every
  existing account is backfilled active), `referred_by`, `referral_code`,
  `vl_referral_code`, `vl_participant_id`, `waitlist_joined_at`,
  `access_granted_at` - added the way every column in this repo is, by
  `ALTER TABLE` at startup (`accounts.py`). While the gate is on, every route
  that creates an account (email, phone, Google, Apple) writes `waitlisted`
  in the `INSERT` itself (`AccountStore.new_account_status`).
* **The gate** (`app._waitlist_refusal`, inside the session middleware): a
  guest or waitlisted account asking for `/`, `/index.html`, `/v/*` or
  `/m/*` is redirected to `/waitlist` or `/waitlist/me` (query string
  kept, so `/?referralCode=x` survives). Any `/api/*` call answers 403 with
  `X-FAM-Waitlist`, except `/api/auth/*`, `/api/waitlist/*`, `/api/admin/*`,
  `/api/health`, `/api/client-status`, `/api/me` and `/api/preferences`,
  any request carrying an admin credential, a shared episode's own two
  calls (decision 10), and the landing page's samples: `/api/welcome`,
  `/api/thumb/*`, and `/api/audio` for exactly one of today's three sign-up
  samples as a replay (`_welcome_sample_request`, PROBLEMS.md §190).
* **One browser, many accounts** (§190): joining from a browser that already
  holds an account's session makes a new account on a fresh listener id and
  moves the browser onto it; the earlier account still signs in.
* **Pages:** `static/waitlist.html` (landing at `/waitlist`, status at
  `/waitlist/me`) and `admin_ui/waitlist.html` (`/admin/waitlist`).
* **API:** `POST /api/waitlist/join`, `GET /api/waitlist/me`,
  `GET /api/admin/waitlist`, `POST /api/admin/waitlist/grant`
  (`{"user_ids": [...]}` or `{"top": N}`), `POST /api/admin/waitlist/cutoff`.
* **Viral Loops** (`viral_loops.py`): every call is a row in
  `waitlist_outbox`, delivered in the background and retried (1 min, 5 min,
  15 min, then hourly for as long as it takes). With no token, which is every
  test and every staging deploy, nothing is sent and the calls wait.

## Decisions

1. **Branch.** The spec names `feature/waitlist`; the work is on the branch the
   session assigned, and reaches `Main` through `staging` like everything
   else (`STAGING.md`).
2. **The app is closed until `active`** (the owner, 30/09). This reverses
   "an account gates what is kept, never what is heard" for as long as
   `WAITLIST=1` - see `docs/claude/constraints.md`, rule `waitlist-gate`.
   Guests are closed out too: a guest door would make the waitlist optional.
3. **Sign-up is email and password** (the owner: Apple and Google are not
   offered until the Apple Developer Program is in place). The landing page
   asks for the email, then a password - the app's own sign-up, so the account
   is the one the app opens with. The provider route still honours
   `referral_code` for the day it is offered.
4. **No email delivery in FAM; Viral Loops sends the email** (the owner).
   Welcome, referral and "you're in" email are configured in Viral Loops.
   Granting access flags the participant there.
5. **Place in line is counted in FAM, not asked of Viral Loops.** Ordered by
   invites (accounts that joined with your code), then by join time. One
   source for the status page, the admin table and "grant the top N"; it works
   on staging, which cannot reach the vendor, and during an outage.
6. **Invite codes are FAM's own**, minted at join, so a link handed out
   before Viral Loops answered still works. Links are
   `/waitlist?referralCode=<code>` - `referralCode` is the parameter Viral
   Loops' own links use. The vendor's code for each person is stored
   separately (`vl_referral_code`) and used only to name the referrer when
   registering an invitee with it.
7. **Friendship is two follows.** FAM has no friends table: a friend is a
   mutual follow, derived. A referral writes both follows at signup, so Your
   FAM on the waitlist and Your Friends in the app are the same rows.
8. **Admin is `FAM_ADMIN_ACCOUNTS`, not an `is_admin` column.** The app
   already had admin accounts and a server-side check (404 for anybody else);
   the spec allows admin to "live elsewhere". `/admin/waitlist` signs in the
   same way `/admin` does, every load.
9. **SQLite has no row-level security**, so the visibility rules live in the
   endpoints: search (`/api/people`) never returns a waitlisted account;
   `/api/person` and follow (by handle or id) treat one as not found unless
   the two are already in each other's graph; `/api/friends` labels them
   `waitlisted` ("Still on the waitlist"); `/api/messages` refuses to send
   from or to one.
10. **Anyone can listen to a shared episode, waitlist or not** (the owner,
    01/10; this reverses the first build, which closed share links too).
    `/s/<id>` is open, and so are the two calls its page makes: counting the
    open, and `/api/audio` - but only for exactly what was shared (that
    question at that length, `surface=share`, nothing attached;
    `ShareStore.is_shared`), so the share page is not a way into the rest of
    FAM. Its "Join FAM" door is the front door, which the gate sends to
    `/waitlist`. Mix links (`/m/<id>`) stay closed: a mix is a list to browse,
    which is the app.
11. **A complete profile is a name, a handle and at least one topic.** A
    photo is offered, not required. Topics are the app's own interests
    (`/api/preferences` `interests_all`), saved the same way the app saves
    them.
12. **"Watch the video" is not drawn** - there is no video yet, and a control
    with nothing behind it is worse than none. The hero photo is a drawn dusk
    gradient until there is a licensed image (set `--hero` on `.landing`;
    the dusk is drawn on `.hero`, the sign-up screen, and inherits it).
13. **The waitlist rules end at launch, not when each row changes.** Hidden
    from discovery and no messaging apply only while `WAITLIST=1`; setting it
    to 0 lifts them for everybody, including anyone never granted, so launch
    is one switch rather than a cleanup of rows.
14. **A waitlisted account can be deleted** (Delete account on the status
    page; `/api/account` is open to them). Deleting removes it from FAM; it
    does not yet tell Viral Loops, so the address stays with the vendor until
    it is removed there by hand.
15. **One invite code credits at most `WAITLIST_REFERRALS_PER_HOUR` (20)
    invites an hour.** Nothing about a signup is verified, so without a cap a
    script with one code and made-up addresses could buy the front of the
    line. The cap is keyed on the code because it is the one thing such a
    script cannot vary (an address can be forged per request). People past
    the cap still join; they do not move the inviter up. "Grant the top N"
    is still a person's decision - look at the table before granting.
16. **Viral Loops calls are sent once and given up on when refused.** One
    drain runs at a time, so no call is sent twice; a 4xx other than 408/429
    is a refusal retrying cannot change, finished with its error kept and
    shown on the admin page.
17. **A join at `/waitlist` is always a waitlist join** (the owner, 02/10,
    PROBLEMS.md §192). It used to follow `WAITLIST`, so with the switch unset
    a join made an active account and dropped the person on the app's
    sign-up screen. The gate now decides only whether the app is closed. The
    app's Sign Up sends everybody here; the form takes the password twice;
    the status page shows the exact place, the invite link and "Edit your
    profile" (date of birth, location, phone and interests added).
18. **Under the sign-up, the landing page says what FAM is** (the owner,
    03/10, PROBLEMS.md §198): what an episode is, the old way against FAM,
    Search / DailyFAM / myFAM each with real screens of the app, friends, the
    founders, and a last Join button that scrolls back to the form. The
    screens are `static/landing/*.jpg`, photographed from the preview build by
    `python tools/landing_shots.py` (run it after `build_preview.py` whenever a
    pictured screen changes); nothing in the section plays. The pictures
    show only tiles with a painted picture (real cards cut from the app's
    screens, in `tools/landing/tiles/`), made-up friends' names and a
    cover on the Morning mix (the script's docstring says how). The founders'
    photo is `static/founders.jpg` (no metadata); without the file the frame
    holds their initials. The page ends with the copyright line. The status
    page does not draw the section.
19. **The section tells why FAM exists** (the owner, 04/10, PROBLEMS.md §199):
    FAM is social information, not social media. The tagline under both
    wordmarks and the intro say so; then the problem (what passes you by on a
    moving rail, the time keeping up takes, all of it condensed into one
    short episode), the same group chat without FAM and with it, side by
    side, and why it was built (who decides which stories you hear, and the
    pipeline it collapses), before the product.
20. **Still open (ask the owner):** what each unlock gives (the tiers are
    `WAITLIST_UNLOCKS`, default `1,3,5`); whether completing a profile moves
    anyone up; one label ("Your FAM" / "Your Friends") or two.

## Viral Loops setup

1. Create a campaign from the pre-launch / waitlist template.
2. Set its website URL to `https://<fam-domain>/waitlist`.
3. Leave rewards to FAM for now (unlocks are counted here).
4. Turn on the emails Viral Loops should send (welcome, referral,
   access granted); FAM sends none.
5. Copy the Campaign ID into `VIRAL_LOOPS_CAMPAIGN_ID` and the API token into
   `VIRAL_LOOPS_API_TOKEN` on the `fam` service (never staging; the public
   token is not needed - nothing from Viral Loops runs in the browser).
6. No webhooks.
7. Turn on disposable-email blocking and fraud/duplicate detection.

The request shapes in `viral_loops.py` follow the v3 reference but could not
be checked against the live docs from the session that wrote them (the docs
hosts were blocked). After the keys are set, watch `/api/health`
(`waitlist.outbox_pending`) and the admin page's Viral Loops line: a
misshapen request shows there as a retried error, and nothing is lost.

---

# The build spec, as written


Living spec for the Claude Code session building the FAM pre-launch waitlist.
Branch: `feature/waitlist` (off `main`). Keep this file in the repo and update it when a decision changes, **including the reason**, so later sessions don't quietly undo decisions.

Design reference: the two waitlist mockups (landing page + "Founding FAM member" status page) and the existing YourFAM profile screen. Ian will attach them to the session.

---

## 0. First task: tell Ian what to set up in Viral Loops

Before writing any Viral Loops integration code:

1. Look through the repo: framework, auth, database, how migrations are run, how env vars and secrets are handled, and how the existing profile and friends data is modeled.
2. Read the current Viral Loops developer docs (https://developers.viral-loops.com and https://documentation.viral-loops.com) to confirm the endpoints, auth headers and referral-link format this spec assumes.
3. **Stop and send Ian a short checklist** of exactly what to create and copy from the Viral Loops dashboard. Use the list in §7 as the starting point, corrected against the docs. Include the env var names you'll read each value from.
4. Also report anything in the repo that conflicts with this spec (e.g., profiles already have a status field, or friends are stored differently than assumed). Don't guess your way around a conflict; ask.

You can build everything that doesn't depend on Viral Loops (migration, pages, admin) while waiting for the keys.

---

## 1. Core principle: one database, one account

A waitlist signup **creates a real FAM account** in the existing FAM database, with `status = 'waitlisted'`. Profile setup on the waitlist page writes to the **same profile record** the app uses.

**Why:** when someone gets access, nothing has to be transferred. Their profile, @handle and friends are already there, and granting access just changes `status` to `active`. Keeping a separate waitlist store would mean a migration step at launch, and that step is where data gets lost.

**Viral Loops is a supporting service, not the source of truth.** It handles referral links, the leaderboard/place in line, and fraud/duplicate protection. FAM's database owns identity, profiles and friendships.

---

## 2. Data model changes (one migration)

Add to the existing profiles/users table. Adapt names to the codebase's conventions.

| Field | Type | Notes |
|---|---|---|
| `status` | enum `waitlisted` \| `active` | Default `waitlisted` for new waitlist signups. **Existing users must be backfilled to `active`.** |
| `referred_by` | FK → profile id, nullable | Who invited them. Set once at signup, never changed afterward. |
| `referral_code` | string, unique | Their own invite code (from Viral Loops if it provides one, otherwise generated). |
| `vl_participant_id` | string, nullable | Viral Loops participant reference, if the API returns one. |
| `waitlist_joined_at` | timestamp | Tie-breaker for ordering. |
| `access_granted_at` | timestamp, nullable | Set when the admin grants access. |
| `is_admin` | boolean, default false | Gates `/admin`. Can live elsewhere if the app already has roles. |

`handle` must be **unique** and case-insensitive. If a unique constraint doesn't exist already, add one.

Friendships: when an invitee signs up with a valid referral code, **create a friendship between inviter and invitee right away** in the app's existing friends table/model.
**Why:** "Your FAM" on the waitlist and "Your Friends" in the app are the same people. Storing them in the real friends table means the carryover happens automatically.

Test the migration against a dev copy of the database before merging. It touches the live profiles table.

---

## 3. Public waitlist flow

### 3a. Landing page (`/waitlist`, or the root landing page if Ian prefers)
- Matches the mockup: FAM wordmark, headline, email field, "GET EARLY ACCESS", "Watch the video."
- Reads the referral code from the URL (use the param name Viral Loops' referral links actually use; confirm in the docs) and keeps it through sign-in.
- Signing up uses **the app's existing sign-in method**, so the account created here is the one they later open the app with. Don't build a separate email-only list.

### 3b. Signup handler (server-side)
1. Create or get the account with `status = 'waitlisted'`.
2. If a valid referral code is present and belongs to someone else: set `referred_by` and create the friendship. Ignore self-referrals and codes that don't exist.
3. Register the participant with Viral Loops (`POST /campaign/participant`), passing the referrer so their rank updates. Store the returned ids and codes.
4. If the Viral Loops call fails, **the signup still succeeds.** Log it and queue a retry. **Why:** a vendor outage should never lose a signup.

### 3c. Status page (`/waitlist/me`, signed in)
Matches the second mockup:
- "Ian, you're a Founding FAM Member · #4,821": place in line.
- "742 places until full access" plus a progress bar: `place − access_cutoff`, where `access_cutoff` is set in admin (§5). If the person is already inside the cutoff, show "You're in the next batch."
- "Invite someone": native share sheet with their referral link, plus copy-link.
- **Your FAM**: avatars of the friends they've brought in (their friendships), plus empty "+" slots.
- **Next unlock**: "N more people until your next unlock." Milestones come from config (e.g., 1, 3, 5 invites); ask Ian what each unlock gives.
- **Profile setup** (new; not in the mockup yet; design it to match): display name, @handle (claim it now), photo, topic chips using the same topic set as the app's profile. Writes directly to the profile record.

Get place in line from Viral Loops' rank endpoint server-side, or calculate it locally if that turns out simpler and consistent. Pick one source and use it everywhere.

**Optional, ask Ian first:** reward completing the profile (e.g., move up X spots). Only build it if Viral Loops supports a custom-event reward or it can be done cleanly on our side.

---

## 4. Visibility rules (enforce in the database/API, not just the UI)

- **Find new friends / search / public profile lookups return `active` accounts only.** Enforce this in the query or access rules (e.g., row-level security), so waitlisted profiles can't be reached by search or a direct link either.
- **Exception:** existing friendships show on both sides regardless of status. A waitlisted friend appears in the active user's Your Friends with a "Still on the waitlist" label.
- Waitlisted users can't use messaging until they're `active`.
- The waitlist page can only edit profile fields (name, handle, photo, topics). **It must never be able to write `status`, `referred_by`, place in line, `is_admin` or `access_granted_at`.** Enforce this server-side.

**Why:** Ian decided waitlisted accounts stay out of discovery until launch, and a client-side filter can be bypassed.

---

## 5. Admin (`/admin/waitlist`)

- Protected by a real server-side `is_admin` check on every request and API route. **An unlisted URL isn't protection**: this page shows every email and can grant access.
- Table: place, name, handle, email, invites (signups referred), profile completed yes/no, joined date. Sortable, searchable by email or handle.
- Actions:
  - **Grant access** to one person, or **grant the top N**. Each sets `status = 'active'`, sets `access_granted_at`, and flags the participant in Viral Loops (their flag endpoint removes them from the leaderboard without deleting them).
  - **Set `access_cutoff`**, which drives "places until full access."
- Summary numbers at the top: total waitlisted, active, signups today, % with a completed profile, top inviters.

---

## 6. Access handoff

When someone's status becomes `active`:
- They sign into the app with the same account, and their profile and friends are already there.
- Send an "You're in" email/notification (use Viral Loops or the app's email, whichever is already set up; ask Ian).
- Their friends from the waitlist appear in YourFAM → Your Friends. Terminology: the waitlist says "Your FAM" and the app says "Your Friends." Ask Ian whether to use one label for both.

---

## 7. Viral Loops setup checklist (starting point for §0; verify against current docs)

Things Ian likely needs to do in the Viral Loops dashboard:
1. Create a campaign using the **pre-launch / waitlist** template.
2. Set the campaign's website/URL to the waitlist page on FAM's domain, so referral links point there.
3. Set up **rewards/milestones** to match the "next unlock" tiers, or confirm we handle unlocks ourselves.
4. Decide whether Viral Loops sends emails (welcome, referral notifications) or FAM does. Turn off whichever isn't used, so people don't get duplicates.
5. Copy the **Campaign ID**, the **public token** (safe in the browser) and the **API token** (server-only, **never** in client code) into the env vars Claude Code specifies.
6. If using webhooks: add FAM's webhook URL and copy the signing secret, if there is one.
7. Turn on any available **fraud/duplicate protection**, such as email verification and disposable-email blocking.

Skip Viral Loops' own form widget; we use our own form and the API.

---

## 8. Done when

- [ ] Migration applied on dev; existing users are `active`; the handle is unique.
- [ ] Signup via a referral link creates the account, the friendship, and the Viral Loops participant.
- [ ] Signup still succeeds when Viral Loops is down.
- [ ] Status page shows the right place, Your FAM, unlock progress, and profile setup.
- [ ] Waitlisted accounts can't be found through search or a direct link; existing friends can still see them.
- [ ] Profile setup can't change status, place, or admin fields.
- [ ] `/admin/waitlist` rejects non-admins server-side; grant access works for one person and for the top N.
- [ ] A granted user opens the app and sees their profile and friends already there.
- [ ] Playwright screenshots of the landing, status and admin pages at mobile width, compared against the mockups.
