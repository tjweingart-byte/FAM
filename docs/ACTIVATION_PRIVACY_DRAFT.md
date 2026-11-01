# Draft: Privacy Policy and consent wording for the activation step

**Status: a draft for the owner. Nothing here is live.** `pages/privacy.html`,
`consent.py` and the app are unchanged. Nothing asks for or collects a date of
birth, location or gender until the owner approves this wording
(`docs/AGENTS_BUILD_PROMPT.md`, Phase 2, the activation step; decided items 2
and 11; PROBLEMS.md §393). Delete this file once the approved wording is in
`pages/privacy.html` and the app.

Not legal advice. The Terms already name the points a lawyer should confirm
before launch; this draft adds to that list (the end of this file).

## What the wording has to cover

From the owner's decisions (items 2 and 11) and `consuela.md` (never 4):

- **When:** when a listener is let off the waitlist into the app; listeners
  already in are asked the next time they open the app.
- **Date of birth: required, 13 and over only.** An under-13 date cannot enter.
- **Location: optional**; the city is kept, and the totals use only the region.
- **Gender: optional**; man, woman, non-binary, self-describe, prefer not to say.
- **What it is for:** totals and correlations of what is searched and heard,
  by category, region, time, age band (13-17, 18-24, 25-34, 35-44, 45-54,
  55-64, 65+) and gender, kept only for groups of **100 or more**, never a
  name, an account id or a row per person, never from guests or private
  searches.
- **Account deletion removes them** (`accounts.delete_account`).

## 1. Privacy Policy - the changes, section by section

Each block shows the current text and the proposed text. Anything not
listed stays as it is. The page's date (`app.py`, the privacy policy's date)
changes when this goes live.

### The short version - one bullet added after "We keep what FAM needs to work"

> **New:** When you join, we ask your date of birth (to check you are 13 or
> older) and, if you want to give them, where you live and your gender. We
> use them only to count, in groups of at least 100 people, what kinds of
> listeners hear what - never to pick you out, and never sent to anyone.

### What we collect - the Profile row is split in two

**Current (Profile):** Display name, @handle and profile picture if you set
them; date of birth if you give it; the town, region or country you type in
(FAM never reads your device's location). *Why:* To show your profile and to
suggest stories about where you live.

**Proposed (Profile):** Display name, @handle and profile picture if you set
them; the town, region or country you type in (FAM never reads your
device's location). *Why:* To show your profile and to suggest stories about
where you live.

**Proposed new row (About you):** Your date of birth, which we ask when you
join; your gender if you choose to give it (man, woman, non-binary, your own
words, or prefer not to say). Neither is ever shown to anyone. *Why:* Your
date of birth checks you are 13 or older. Both are used, with your place's
region, only to count what groups of listeners hear - see "Totals about
groups of listeners".

### New section after "What we collect": Totals about groups of listeners

> FAM keeps totals of what is searched and heard - by kind of story, region
> and time of day - and how those totals differ by age group (13-17, 18-24,
> 25-34, 35-44, 45-54, 55-64, 65 and over), gender and region. They tell us
> what kinds of listeners care about what.
>
> - **Only groups of 100 or more.** No total is kept for a group smaller than
>   100 people, so no total describes you.
> - **Never about one person.** A total holds no name, no account, no city and
>   no date of birth - only the age group and the region.
> - **Never from a guest, and never from a Private search.** If you listen
>   without an account or search with Private on, nothing you do is counted.
> - **Never shared or sold.** The totals stay with FAM.
>
> Your date of birth, gender and place stay on your account. You can change
> or remove your gender and place at any time in Settings, and deleting
> your account deletes all three.

*[Owner: one choice affects this section - see question 2. If counting is
opt-in, add: "You are counted only if you said yes when you joined; you can
change that in Settings, Privacy and safety."]*

### Who else receives it - one sentence added under the table

> Your date of birth and gender are sent to none of these companies.

(True as designed: the totals are computed beside the accounts database, on
FAM's own servers. The place you set is already sent to Anthropic when you
tap the story about where you live - that row stays as it is.)

### What other people can see - one bullet added

> Your date of birth and gender are never shown to anyone. The place you
> set shows on your profile, as it does today.

*[Owner: see question 3 - the location asked at activation is the same
place as the profile's, which other people can see.]*

### How long we keep it - two rows added

| What | For how long |
|---|---|
| Your date of birth, gender and place | Until you change them or delete your account |
| Totals about groups of 100 or more listeners | Indefinitely; they describe groups, not you, and are reviewed at two years |

### When you delete your account - one sentence added

> Your date of birth, gender and place are erased with it. Totals already
> counted stay, because they describe groups of at least 100 people and
> nothing in them is yours.

### Children - replaced

**Current:** FAM is not meant for children under 13, and we do not
knowingly collect their information. If you believe a child has an account,
email us and we will delete it.

**Proposed:** FAM is not meant for children under 13. We ask every
listener's date of birth when they join, and someone under 13 cannot enter
the app. *[Owner: question 1 decides the next sentence - recommended:]* If a
date of birth shows a listener is under 13, we delete their account and
what it holds. If you believe a child has an account, email us and we will
delete it.

## 2. Consent - how the yes is kept

`consent.py` keeps two scopes today: `ai` (their words to Anthropic) and
`terms` (the Terms and this policy, agreed with a checkbox at sign-up and
again when `TERMS_VERSION` rises).

**Proposed:**

- **A third scope, `demographics`, version 1**, kept exactly like `ai`:
  the answer, its version and when, keyed on the account; a no is kept too,
  and Settings can change it. The aggregate job reads only accounts whose
  answer is yes to the current version.
- **`TERMS_VERSION` is not raised.** The policy change adds what FAM asks at
  activation and nothing sent to a third party, and the activation screen
  shows the notice itself, so asking everyone to tick the terms again would
  add a second prompt for the same thing. *[Owner: say if you would rather
  everyone re-agree; that is one line, `TERMS_VERSION = 3`.]*
- **The notice text** (the `demographics` scope's `NOTICE`, shown on the
  activation screen under the fields):

> We use your age group, gender and region only to count what groups of
> listeners hear, in groups of at least 100 people. You are never picked
> out, and none of it is shown to anyone or sent to anyone.
> [Privacy policy](/privacy)

## 3. The activation screen - in-app wording

An API before a screen ([api-before-screen]); this is the screen's text.

- **Title:** A few things before you start
- **Date of birth** (required): label `Date of birth`; help text
  `To check you're 13 or older. Never shown to anyone.`
- **Where you live** (optional): label `Where you live`; placeholder
  `Town or city`; help text `Shows on your profile, and helps FAM find
  stories near you. Optional.`
- **Gender** (optional): label `Gender`; choices `Man`, `Woman`,
  `Non-binary`, `Self-describe`, `Prefer not to say`; choosing Self-describe
  opens a short text box (`In your words`). Help text `Never shown to anyone.
  Optional.`
- **Counting** (question 2): either the notice alone (counted by default) or
  a switch, `Count me in FAM's totals`, with the notice under it.
- **Button:** `Continue` (enabled once a date of birth is entered); the two
  optional fields can be left empty.
- **Under 13** (question 1, recommended wording): before saving, `Is
  {date} right?` with `Yes` / `Change it`, so a slip of the finger does not
  cost an account; then `FAM is for people 13 and older, so we can't let you
  in. We've deleted your account.`
- **Already let in:** the same screen, the next time the app opens, with
  any date of birth already on the account filled in. The title becomes
  `One more thing`.

**Settings** gets a row for gender beside the place's (the place already
has its own screen, `screen-location`) and, under Privacy and safety, the
counting switch if counting is opt-in. The date of birth is
shown but cannot be cleared (it can be corrected; a correction under 13
follows question 1).

## 4. App Store

- **App Privacy labels** (App Store Connect) need updating when this ships:
  date of birth and gender under *Other Data Types* (or whichever category
  Apple's current list assigns them - check at the time), linked to the
  user, not used for tracking; the typed place may count as *Coarse
  Location* - check Apple's definition when filling it in.
- **Guideline 5.1.1** (minimum data): only the date of birth is required, for
  the 13-and-over gate; location and gender are optional, as the owner
  decided (item 11).

## 5. Questions for the owner

1. **An under-13 date of birth** - at activation, or one already on an active
   account: delete the account (recommended; it matches what the policy
   already promises and US children's privacy law, COPPA, expects once FAM
   knows), lock it, or hold it for you to review?
2. **Counting: opt-in or on by default?** Opt-in (a switch, off until
   turned on) is the safer reading for the EU and UK and for App Store
   review, but fewer groups will reach 100 people. On by default, with the
   notice on the screen and a switch in Settings to turn it off, gives more
   data. Either way guests and Private searches are never counted.
3. **Location:** use the profile's existing place, which other people can
   see (recommended - one place, and the screen says it shows on your
   profile), or a separate private location only for the totals?
   **The app already disagrees with itself here:** the "Where you are"
   screen (`static/index.html`, `screen-location`) says "It's never shown on
   your profile", but since §219 the place is printed on your profile and on
   the page a friend opens (`/api/person` `location`, the
   `[op-friend-profile]` rule), and the Privacy Policy lists it under what
   other people can see. Which is right? If it should be public, that
   screen's sentence is wrong today; if private, §219 is reversed.
4. **Self-describe:** keep the listener's own words on the account (never
   shown, never counted - totals use only "self-describe"), or keep just the
   choice "self-describe" and no words?
5. **The totals' use:** the draft says they are used "only to count what
   groups of listeners hear". If they may later shape what FAM offers (say,
   Trending), the wording must say so now.

## 6. For the lawyer's list (beside the Terms' existing notes)

- Whether gender, joined with listening, needs more than this notice in any
  US state FAM launches in, or in the EU or UK (where consent is the safer
  basis for the totals).
- Whether totals with a 100-person floor count as de-identified data under
  the laws that apply.
- COPPA: what FAM must do when a date of birth shows a listener is under 13.
