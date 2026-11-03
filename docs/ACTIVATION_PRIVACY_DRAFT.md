# Draft: Privacy Policy and consent wording for the activation step

**Status: a draft for the owner. Nothing here is live.** `pages/privacy.html`,
`consent.py` and the app are unchanged. Nothing asks for or collects a date of
birth, location or gender until the owner approves this wording
(`docs/AGENTS_BUILD_PROMPT.md`, Phase 2, the activation step; decided items 2
and 11; PROBLEMS.md §395). Delete this file once the approved wording is in
`pages/privacy.html` and the app.

Not legal advice. The Terms already name the points a lawyer should confirm
before launch; this draft adds to that list (the end of this file).

## Your decisions - answer these seven

Each line is one decision. The recommendation is first; reply with the
number and your choice (for example "1 A, 2 A, 3 A, ..."). Nothing below is
built or collected until you have answered and approved the wording.

**1. A date of birth under 13** - at activation, or one already on an
account.
- **A. Delete the account (recommended).** After a "Is {date} right?"
  check, so a typing slip costs nothing. *Why:* the current policy already
  promises to delete a child's account, and US children's privacy law
  (COPPA) expects deletion once FAM knows.
- B. Lock the account until someone reviews it.
- C. Hold it for you to review by hand.

**2. Counting someone in the totals** (the age-group, gender and region
counts, groups of 100 or more).
- **A. Opt-in: a switch on the activation screen, off until they turn it on
  (recommended).** *Why:* the safer reading for EU and UK law and for App
  Store review. *Cost:* fewer people counted, so fewer groups reach 100.
- B. On by default, with the notice on the screen and a switch in Settings
  to turn it off. More data; more legal and review risk.

**3. Where the location asked at activation is kept** - and a fix either
way, because today the app disagrees with itself: the "Where you are"
screen says the place "is never shown on your profile", but since §219 it is
shown on your profile and to friends, and the Privacy Policy says so.
- **A. Use the profile's existing place, shown on the profile as today, and
  correct the "Where you are" screen's sentence (recommended).** *Why:* one
  place, not two; what the policy already says; the screen's sentence is
  the error.
- B. A separate private location used only for the totals; the profile's
  place stays as it is (and the screen's sentence is still corrected).
- C. Make the profile's place private (reverses §219: profiles and
  `/api/person` stop showing it).

**4. "Self-describe" gender.**
- **A. Keep only the choice "self-describe", with no text box
  (recommended).** *Why:* nothing would use the words - totals count only
  "self-describe" - and data FAM does not use is data it should not
  collect (App Store 5.1.1, GDPR minimisation).
- B. Offer a text box; keep the words on the account, never shown, never
  counted.

**5. What the totals may be used for.**
- **A. Only to count what groups of listeners hear (recommended).** *Why:*
  it is all that is planned. If they should one day shape what FAM offers
  (say, Trending), that is a new policy change and everyone is asked again
  then.
- B. Also to shape what FAM offers - the policy must say so now.

**6. Asking everyone to agree to the Terms again.**
- **A. No: keep `TERMS_VERSION` at 2; the activation screen shows its own
  notice and asks for its own yes (recommended).** *Why:* nothing new goes
  to a third party, and a second prompt for the same thing adds friction.
- B. Yes: raise `TERMS_VERSION` to 3 so every signed-in listener ticks the
  Terms again.

**7. The wording itself** (sections 1-3 below: the Privacy Policy changes,
the consent notice, the activation screen's words).
- **A. Approve as drafted, with your answers to 1-6 filled in
  (recommended).**
- B. Approve with changes - mark them on this page and send them back.

After your answers I update `pages/privacy.html`, `consent.py` and the
app, build the activation step (an API first, then the screen) and then the
totals - each its own piece, checked as usual.

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

*[Owner: one choice affects this section - see decision 2. If counting is
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

*[Owner: see decision 3 - the location asked at activation is the same
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
the app. *[Owner: decision 1 decides the next sentence - recommended:]* If a
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
  add a second prompt for the same thing. *[Decision 6: if everyone should
  re-agree instead, that is one line, `TERMS_VERSION = 3`.]*
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
  `Non-binary`, `Self-describe`, `Prefer not to say`; with decision 4's
  option B, choosing Self-describe opens a short text box (`In your words`);
  with option A (recommended) it is a choice and nothing more. Help text `Never shown to anyone.
  Optional.`
- **Counting** (decision 2): either the notice alone (counted by default) or
  a switch, `Count me in FAM's totals`, with the notice under it.
- **Button:** `Continue` (enabled once a date of birth is entered); the two
  optional fields can be left empty.
- **Under 13** (decision 1, recommended wording): before saving, `Is
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
follows decision 1).

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

They are at the top of this draft, under "Your decisions - answer these
seven", each with the options and a recommendation.

## 6. For the lawyer's list (beside the Terms' existing notes)

- Whether gender, joined with listening, needs more than this notice in any
  US state FAM launches in, or in the EU or UK (where consent is the safer
  basis for the totals).
- Whether totals with a 100-person floor count as de-identified data under
  the laws that apply.
- COPPA: what FAM must do when a date of birth shows a listener is under 13.
