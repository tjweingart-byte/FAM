# Friends, sharing, and keeping an episode

Four capabilities that look like one feature and are not: **following**
somebody, **sending** them an episode, **posting** one outside FAM, and
**keeping** one — either as a pointer or as audio on the device.
`social.py`, `messages.py`, `sharing.py` and `saved.py` are the code; this is
what was decided and why.

The property underneath all of it: **none of them generate anything.** A share,
an echo, a saved item and a message are all rows pointing at a question whose
script is already in the shared cache. Sending an episode to ten people costs
ten rows, not ten episodes — their taps are what synthesise audio, against
their own allowances, from one script. That is the same design the browse
surfaces use, and it is why a social layer does not change the cost model.

## Following

Asymmetric, because the app's own copy already said so — "What your followers
are listening to" was ranking co-listener overlap and promising a social
network that did not exist (CLAUDE.md open problem #6). Now there is a graph.

A **friend** is the mutual case and is *derived, never stored*. No request, no
accept, no pending state: two rows that happen to point at each other. That
removes the state machine where this shape usually goes wrong, and it makes it
impossible for the two directions to disagree.

Sharing needs neither. You can send an episode to somebody who does not follow
you back, exactly as you can text them.

Only people who have chosen a handle are findable, and a search needs two
characters — one letter returns most of the listener table, which is a
directory dump rather than a search. Somebody who never set a handle is not
hidden by a privacy setting; they are simply not in a directory.

## Sending an episode inside FAM

An **echo** is a broadcast — you push an episode at everyone who follows you.
A **share** is directed: one person, one episode, deliberately. Both are one
row.

Thread ids are **derived** from the two listener ids, sorted and joined. So
opening a conversation needs no write, and two people opening the same one
simultaneously cannot create two threads — the classic bug in this shape, which
produces a split history nobody can merge afterwards.

A message carries the **question and the length** and never a script or audio.
Those two fields are exactly the script cache's key, so the recipient's play is
a cache hit.

What is deliberately not here: **group threads** (a share to a group is closer
to an echo, and that is a decision to make when somebody wants one), **delivery
receipts** and **typing indicators** (both are promises about somebody else's
attention, and this app has a rule against inventing state it does not have).

## Sharing outside FAM

**FAM posts nothing and holds no token for any platform.** Not a gap — the
correct shape. Every destination is reached from the phone: iOS hands the app a
share sheet, and the story formats have an SDK hand-off where the app passes an
image and a link and the *platform's* app posts it, with the person watching.
So the server's job is to produce the payload and a share is something the
listener completes. No OAuth to maintain, no tokens to leak, no scope reviews
with four companies, and nothing that can post while somebody is asleep.

Nine destinations, and `kind` is what actually differs:

| kind | destinations | what it takes |
|---|---|---|
| `copy` | Copy link | the URL |
| `message` | SMS, email, WhatsApp | text, and a subject for email |
| `link` | X, Facebook, LinkedIn | text with the URL in it |
| `story` | Instagram, Snapchat | **an image**, with a link sticker |

**Stories cannot take a link.** They are pictures. Without a card the listener
shares a screenshot of a player UI, which is not an invitation to anything — so
`sharing.story_card` renders one as SVG at 1080×1920, in FAM's own colours,
with no dependency and nothing stored. The question in it is text a listener
typed and the card is markup, so everything is escaped; the test for that is
the one that would otherwise fail in public.

The templates are **defaults the listener edits**, not copy that gets posted
unseen — which is why they are written to be finished by somebody. Each says
what it is about, that it is short, and where to hear it. None claims the
episode is good: FAM did not write that opinion and the person sharing has not
typed one yet. A test enforces it.

X's caption is trimmed to fit, on a word boundary, and **the link is never the
part that gets cut** — a share with a truncated URL is worse than one with a
shorter sentence.

Without `PUBLIC_BASE_URL` the link comes back relative, `public` is `false`,
and the share sheet says so. Nothing invents a host: a link resolving to
`localhost` posted to LinkedIn is exactly the quiet failure this project keeps
a rule about.

## Save for later

A saved item is a **pointer**: the question, the length, a title. It costs one
row, there is no limit on it worth having, and playing one needs the network
like every other episode in FAM — it is synthesised, or replayed from the
shared cache, on the tap.

Saving is idempotent on `(question, length)`, which is also the script cache's
key, so two people saving the same episode point at one script.

### It is a toggle, not a question

Press save and the icon turns green; press it again and the episode comes off
the shelf. Exactly the shape of VIBE!, and drawn the same way — every save
control carries `data-save`, and `setSaved` sweeps the attribute rather than
listing ids, which is the mistake that once left the main player without a
vibe button at all.

The control asks the server whether it is lit (`GET /api/saved?q=…&minutes=…`)
when an episode starts, and un-presses with `DELETE /api/saved?q=…&minutes=…`.
Both take the question and the length rather than a row id, because that pair
is what the player has: making it fetch an id before it could un-press a
button would put a round trip in front of the second tap that the first tap
did not pay.

### What used to be here: download

There was a second thing beside saving — **download**, the audio kept on the
device so an episode played with the network off. It was a state of a saved
item rather than a second list, the shelf had a per-tier capacity
(`max_downloads`), a full shelf answered 409 naming what to clear, and saving
raised a popup asking whether to download too.

**All of it is removed**, at the owner's direction, and removed rather than
switched off on the Piper reasoning: a route or a knob left behind is an
invitation to turn it back on. Gone are the three endpoints, the store methods,
the tier field and its three environment variables, the offline IndexedDB
layer, the second view of the shelf and the Downloads tile on the profile.

What it cost was the thing the shelf is for: pressing save raised a question
instead of saving, so the one-tap action in the player was a two-tap action
with a decision in the middle.

The `downloaded`, `bytes` and `downloaded_at` columns stay in the schema and
are read and written by nothing. Dropping a column is a migration with no
benefit, and an existing shelf is not worth rewriting to delete three numbers
nothing asks for.

The settled "no MP3, no audio files" constraint is *simpler* for this, not
weaker: the server never wrote a file for a download either, and now nothing
anywhere keeps audio.

### Folders

Deleting a folder **unfiles its episodes rather than deleting them**. Losing
somebody's saved episodes because they tidied up is the kind of surprise that
stops people using a feature. *(No interface exposes folders any more — see
"In the interface" below. The rule stands for whatever puts them back.)*

## Somebody else's profile

`GET /api/person?handle=…` returns **only what they chose to publish**, and
that is the whole specification:

* **public mixes** — private by default, so anything here is a mix its owner
  switched on;
* **vibes** — a vibe *is* the act of showing somebody an episode, so a list of
  them is a list of things they published;
* **the interests they pinned**, or failing that the ones they declared and
  have not hidden — at most five (four until §133), the same number their own
  profile draws.

There is no play count, no completion total, no subjects inferred from
behaviour and no history. What somebody has listened to is theirs. The
endpoint exists because that line needed drawing in code rather than by having
no endpoint at all — the screen was already there, describing people with
nothing behind it.

**That last bullet is where §114 had to stop.** Their own profile's interest
row is now ranked by what they actually listen to, which is the point of it —
it keeps up with them instead of replaying six words they picked on their
first day. Publishing the same ranking here would publish exactly the thing
the paragraph above promises is never here, and in the worst available form:
an inference about somebody's behaviour, drawn as a pill row that reads as a
statement they made.

So the two screens deliberately differ. A **pin** is a statement. A
**declared interest** is a statement. A history is not. Pinning is how
somebody's own page becomes their public one, which is what the editor on the
profile is for.

**The boundary is the graph.** A handle can be resolved by anybody, because
handles are how people find each other; a bare `user_id` is only accepted for
somebody already in the asking listener's following or followers, since an id
is guessable in a way a handle search is not. And the response carries no
`user_id` of its own: the follow buttons on that screen already have the id
they need from the graph, and an id the client did not need is an id that can
be sent back.

**Hidden rather than shared** is how the older half of the interest choice is
stored, and it is still honoured when nothing is pinned — somebody who turned
an interest off before the editor moved did not ask for it back. Somebody's
interests are the least private thing here and the whole premise of the social
surfaces, so the honest default is that they are on their profile — and an
empty column then means "all of them", which is what every existing row
already says. Storing the *shared* set would default to nothing shared, so
every profile in the app would show an empty pill row that reads as broken
until each listener opted in one at a time.

## New followers

`GET /api/friends` carries `new_followers`: who followed since this listener
last looked. It is a query over the follow graph's own timestamps against one
column (`people.followers_seen`) saying when that was — not a second table
with its own read state to get wrong.

`POST /api/friends/seen` is what clears it, and it is called from the **Friends
tab** and never when the popup is drawn: a badge that cleared itself the moment
something drew it would be a count nobody got to read.

`followers_seen` defaults to nought, so every follower a listener already has
reads as new the first time they open the tab after this ships. That is the
right direction — the alternative is defaulting to *now* and silently
swallowing followers they were never told about.

`follows_back` rides along on each one, so the popup knows whether to offer the
button. Offering "Follow back" to somebody who is already a friend is a control
that cannot do anything.

There is no push and will not be until the app exists, so the honest moment to
say "___ started following you" is the next time this listener's own app asks —
on open, and when the profile loads.

## Where a share actually goes

Every destination now carries a **hand-off URL** as well as its wording, and
`render` returns it as `destination`. `sms:` and `mailto:` open whichever
Messages and mail app the phone actually uses — iMessage and Gmail included —
with the text already in them; `wa.me` and the intent URLs do the same for the
platforms that have web composers.

Three things about it are deliberate:

* **Every substituted value is percent-encoded**, including into the `sms:` and
  `mailto:` bodies. Those split their parameters on `&`, so a question with one
  in it used to arrive as half a sentence — which reads as a broken app rather
  than as a punctuation problem.
* **LinkedIn and Facebook take only the URL.** Both read the page for their
  own preview and drop anything else, so the composed words go to the clipboard
  alongside with one sentence saying so, rather than into a query string that
  discards them in silence. Facebook was found doing it by running all nine
  hand-offs end to end (PROBLEMS.md §96) - it had been silently dropping the
  wording for as long as it has existed, which is exactly the shape of failure
  reading the code cannot catch and pressing the button can.
* **A destination is only produced for a link the platform can actually
  open.** `sharing.is_public_link` requires `http://` or `https://`, and
  `destination_for` returns `""` otherwise - so a deployment with no public
  base URL hands the phone nothing rather than handing it a relative path that
  opens a composer pointing at `/s/abc`. The wording and the card still come
  back; what is withheld is the one part that would be wrong.
* **The two story formats still have no URL, and that is not a gap.** A story
  is an image handed to Instagram's or Snapchat's own SDK, which takes the
  picture and the sticker link as data. `needs_image` is what tells a client
  which kind of hand-off it is looking at.

### The link, and why it did not work (§114)

`_share_url` read `PUBLIC_BASE_URL`, and **nothing anywhere prompts for it** —
not `render.yaml`, not the Dockerfile, not the first run. So no deployment had
it, so every share link was `/s/abc123`: a correct relative URL and a useless
thing to send somebody. Pasted into a message it is not a link at all; pasted
into LinkedIn it resolves against linkedin.com. The whole feature worked
apart from the one part that leaves the machine, and the app said so honestly
in a sentence nobody connected to "the link does not work".

`app._public_base` reads the request instead, which is `/api/health`'s own
rule: a request arrived, so this server has an address at least one client
outside it could reach, and that address is in the request.
`X-Forwarded-Proto` before `request.url.scheme`, because behind Render's
router the connection itself is plain HTTP and a link built from the
connection would be `http://` on an HTTPS site. First value of each forwarded
header, because they accumulate one entry per hop.

`PUBLIC_BASE_URL` still wins when set — it is the only way to name a host this
server is *not* reached at, which is what a custom domain in front of a
Render URL is. And a **loopback or wildcard host is refused outright** and
still reports `public: false`: a link to `localhost` is worse than a relative
one, because it looks like a URL, so it gets posted, and it resolves on the
recipient's own machine to whatever they happen to be running.
`/api/health` reports `link_host` as `env`, `request` or `none`.

### Instagram and Snapchat: a file, never a tab (§114)

The web build did this:

    window.open(shareTargets.card, "_blank");
    toast("Card ready - add it to your story");

Three failures in two lines. `window.open` on a mobile browser is a blocked
popup, and a blocked popup is silent — the toast said the card was ready and
nothing appeared. When it did open, what opened was an **SVG document in a
browser tab**: neither platform accepts SVG, and there is no "add to story"
anywhere on a tab, so the listener's only move is a screenshot, which is the
exact thing the card exists to stop them doing. And the wording — the whole
point of a share — was left behind in the page they came from.

The card is fetched, rasterised to PNG in the page, and handed to
`navigator.share` as a **file**, with the text alongside. That is what the
platforms' own apps accept from a share sheet.

Two details are load-bearing. **Rasterising happens in the browser**, because
`story_card` is SVG precisely so the server needs no image library, and a
browser already has one — moving it server-side would add the dependency this
module was written to avoid. And the SVG is loaded through a **`data:` URL
rather than a blob URL**, because Safari treats an SVG from a blob URL as
cross-origin and taints the canvas, so `toBlob` throws on exactly the browser
this feature is mostly used from.

Where there is no file sharing — a desktop browser, an older phone — it
downloads and copies the words. A file on disk is a card somebody can post;
an unexplained tab is not. A cancelled share sheet is not reported as a
failure, because it is somebody changing their mind.

It lives here rather than in the web app for IOS_APP.md's first rule — every
feature is an API before it is a screen. A share sheet written twice is a share
sheet that behaves differently on two clients.

And it changes nothing about the rule above it: **FAM still posts nothing and
holds no token.** A hand-off opens the platform with a human looking at it.
That is the whole difference between a share sheet and an integration.

## In the interface

* **Messages are real.** The inbox, the threads and the people row in the share
  sheet read `/api/messages` and `/api/friends`. They used to be three invented
  contacts in `index.html` with invented replies, and "sent" was a toast over a
  push into a local variable — a social surface that fabricates people is the
  same failure as a profile that fabricates numbers, and worse, because it says
  a message was sent when none was.
* **The Audio / Transcript toggle is gone.** It only ever changed a word in a
  toast, and with sending made real there is nothing behind "transcript": a
  message carries the question, and the recipient's tap makes the audio.
* **Messages**: the Explore New tile became **Save for Later**, as asked.
  Explore New then moved to **myFAM** as a rail, and has since come off the
  page again (PROBLEMS.md §96) — "Trending" took that slot, because the row
  about today was sitting under two rows about what the listener already
  likes. The ranking is still computed and still serves the Explore New
  screen; it just has no rail of its own.
* **Save for Later has no folders.** The shelf shipped with a folder chip row
  and a "New folder" button, and nobody had ever made a folder — so every
  listener saw "Commute" (a fixture name) as though it were theirs. A control
  with nothing behind it is worse than no control. `saved.py` still stores a
  folder, unused, because unfiling everybody's episodes to delete a column is
  a migration with a real cost and no benefit.
* **A friend's profile is its own screen.** It used to be drawn into
  `screen-profile` with a variable deciding whose it was, and that one fact
  produced all four symptoms of the navigation bug: back from the friend
  popped to Friends, back again showed `screen-profile` still holding the
  *friend's* DOM, back again fell through to search, and the Profile tab
  flashed them before `loadProfile` replaced it. Two pages sharing one
  container is one bug, not a routing bug with four fixes.
* **A friend's vibe is named on an Explore card.** "___ vibed with this
  episode", with their face, when a *friend* both generated the episode and
  vibed it. Two conditions because the card claims a friendship: a stranger's
  vibe is not addressed to you, and a friend who generated something without
  vibing it did not recommend it. A stranger's vibe still lifts a card in the
  order and no longer puts their name on one.
* **Every player** — search, play-all and Explore — has **share** and **save**.
  Explore included, so the surface where people find things is not the one
  where they cannot keep them.
* **Saving is one tap and shows its state on the button.** The icon turns
  green; pressing it again takes the episode off the shelf. No popup, no
  second decision.
* **The player's four icons are named.** Share, vibe, save, Captions, in words
  under the glyphs. The play-all sidebar and Explore's rail had always carried
  labels; the main player was the odd one out, and unlabelled, a bookmark, a
  two-way arrow and a speech rectangle are three guesses.

## Where a shared link lands

`/s/<id>` serves **a page with one episode on it**. It used to redirect into
the web app, which handed somebody who had been sent one episode the whole
product - a search box, myFAM, Explore and a sign-up - and lost the episode
they actually came for in the process.

The rule the page keeps: **the only control that works is play, and everything
else is a door to the App Store.** Play, pause, scrub and the two fifteen-second
buttons are the whole of what it does. The wordmark, "Ask your own question",
"Browse episodes" and the Get FAM button are all `data-door`, handled by one
delegated listener, so a control added later is a door by default rather than
by somebody remembering to wire it.

### Tracing a link back to its episode

There is no episode id in this product, and this did not add one. An episode is
identified by its cache key, and `pipeline.key_for` builds that key from the
question and the length - so a share row holding `query` and `minutes` **is** a
pointer at the episode, resolved the way every other surface resolves one.
Following the link calls `/api/audio?q=...&minutes=...`, the pipeline computes
the same key, and the sharer's own script comes back out of the shared cache.

Same words, no second model call, nothing new stored. A test asserts the two
keys are equal, so a field added to `key_for` (PROBLEMS.md §83) fails here until
the share row carries it too - which is the failure that would otherwise be
silent, because a share that missed the cache would still play, just differently
and at full price.

Ten people opening one link is therefore ten syntheses against one script,
which is the cost design the rest of the app already rests on.

### The page is rendered on the server, and that is not a preference

Facebook and LinkedIn read the shared page to build their own preview and ignore
everything else - this file says so above, and §96 caught Facebook doing it. A
crawler does not run JavaScript, so a page that fetched its own title would be
posted everywhere as whatever the fallback markup said. The title, the question
and the story card are substituted into the HTML the server sends
(`sharing.render_landing`, into two comment markers in `static/listen.html`),
which is also why the player needs no round trip to discover what it is before
it can start.

**`og:image` is only claimed when the card URL is absolute.** A crawler fetches
it from its own servers, so a relative one advertises a picture that never
loads - the same refusal `destination_for` already makes about the link itself.

### The open count is reported by the page, not by the serve

Because those same crawlers *fetch* `/s/<id>`, counting the HTML serve would
make the only number sharing produces mostly robots. The page calls
`POST /api/share/<id>/open` once it is running in front of a person, and a
crawler never gets there. The alternative - a list of crawler user agents - is
the shape §76 settled against: it can always be widened by one more entry, and
the next one it misses is already written.

### With no App Store link there are no doors

`APP_STORE_URL` is unset on every deployment until the app ships, and nothing
here invents one. When it is empty the page draws **no** non-listening control
at all - not one that 404s, and not one quietly rerouted into the web app, which
is the thing the page exists to not be. A control with nothing behind it is
worse than no control, and a stranger arriving from LinkedIn is the worst
possible audience for a dead button. `/api/health` reports `sharing.app_store_url`
and `sharing.landing_doors`, because from inside the app both states look
identical.

### What it deliberately does not do

* **No `cached_only`.** A share whose script had aged out of the cache would
  refuse to play, and a broken link is a worse outcome than an episode that
  costs a model call. The exposure is real and stated rather than hidden: a link
  posted publicly can be opened by strangers, and the first one after an expiry
  pays for a script the rest then share. Quotas apply to them as to anybody.
* **No account, and no route to one.** Listening has never needed an account and
  a share is the cheapest route FAM has to a listener who does not have it yet.
* **It generates nothing by itself.** Drawing the page costs a row read. The
  episode is written only if somebody presses play.

## What is not built

* **Group threads**, and the decision about what a share to a group means.
* **Universal links.** `/s/<id>` now serves a landing page rather than
  redirecting into the web app (below), and `/api/share/<id>` resolves the same
  share for a native client - but the `apple-app-site-association` file that
  makes iOS open the app instead of the browser is app-side work, and needs a
  bundle id that does not exist yet.
* **Notifications.** A share arrives silently until somebody opens Messages.
  Push is an App Store capability and a permission prompt, and it belongs with
  the app rather than ahead of it.
* **Offline listening of any kind.** The download feature that provided it is
  removed (above), so nothing plays without the network. If it comes back it
  is a new design, not the old one switched on.
