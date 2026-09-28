# The iOS app and the decisions ahead

> Moved verbatim from `CLAUDE.md` (2026-09-28, PROBLEMS.md §167). `CLAUDE.md`
> keeps one line per rule and loads every session; this file keeps the
> reasoning and history and is read on demand. Each rule starts at a
> `<!-- rule:ID -->` marker, and `CLAUDE.md` cites the same ID in brackets -
> `grep -n 'rule:ID' docs/claude/*.md` jumps to it.
> `tests/test_claude_md.py` fails if the two sets of IDs ever disagree.

## There is an iOS app coming, and it changes how to write everything else

<!-- rule:ios-native-client -->
`IOS_APP.md` is the whole of it. The short version, because it constrains work
that has nothing to do with the app:

**The app is a native client of this API, not a web view around
`static/index.html`.** A wrapper is rejected under guideline 4.2, and worse, iOS
suspends a `WKWebView`'s `AudioContext` when the phone locks - so the episode
would stop the moment it is most wanted.

Three consequences for ordinary changes, starting now:

<!-- rule:api-before-screen -->
- **Every feature is an API before it is a screen.** Behaviour that exists only
  in the interface is behaviour that has to be written a second time.
<!-- rule:audio-no-browser -->
- **Nothing new on the audio path may assume a browser** - not `AudioContext`
  semantics, not a cookie riding along on its own, not a relative URL.
  `static/fam-audio.js` is the port's specification: the retained `Int16`
  buffer, the clock-derived cursor and `TAIL_MARGIN` were all paid for in bugs.
<!-- rule:listener-header -->
- **A listener id still never comes from the client, and `_listener` must be
  satisfiable by a header**, not only by the cookie. A server-minted bearer
  token in the Keychain keeps that rule exactly; `?user=` never does.

<!-- rule:ios-pressures -->
The two settled constraints the app pressures, and how they resolve: **no MP3,
no audio files still holds** - Opus over a stream is decoded as it arrives and
writes nothing, so compression was always compatible and is now a prerequisite
rather than a scale question (26 MB for a ten-minute episode on cellular). And
**account deletion is missing**, which is a hard rejection for any app that
creates accounts - and an interesting decision here, because the script cache
is shared, so a deleted listener's scripts are other listeners' Explore feed.

<!-- rule:ios-server-built -->
**The server side of that is now built** - `ACCOUNTS.md` is the whole of it.
Email-or-phone sign-up, Sign in with Google and Apple, account settings,
in-app deletion, three tiers with enforced per-window limits, bearer sessions
beside the cookie, and every endpoint reachable at `/api/v1/...`. What is not
built, and is not an oversight: **payment** (nothing sets a paid plan yet, and
the enforcement path is worth trusting before money moves) and **delivery**
(no email or SMS, so no password reset and no verified address or number).

<!-- rule:ios-order -->
What has to happen, in order: hear an episode in the production voice (open
problem #1 - everything else is scaffolding around an unlistened product);
deploy the API somewhere with a GPU that is up when a phone asks; then a
throwaway Swift spike that plays one streamed episode with the phone locked,
which is the go/no-go for all of it.

## Decisions that will shape the next phase

<!-- rule:dec-deploy -->
- **Where does this deploy?** Bandwidth is 2.65 MB/min uncompressed; that is
  fine on localhost and expensive at scale.
<!-- rule:dec-accounts -->
- ~~**Is there a user account, and what does it entitle you to?**~~ *Answered
  twice: §66 for the shape, §70 for the boundary.* An identity is a session; an
  account is credentials attached to one; and what an account buys is
  **durability** - the things the server keeps for you. The constraint above
  says exactly which. Still open, and more visible than it was: **password
  reset**, which needs email delivery, and which the sign-up screen now says
  out loud rather than letting anyone find out the hard way.
<!-- rule:dec-voices -->
- **Local or hosted voices?** Changes the cost model more than the model choice
  does.
<!-- rule:dec-prefetch -->
- **How much to prefetch?** *Half answered (§105), and the half that is left is
  the expensive one.* What schedules a cycle is decided: myFAM, on the draw,
  per listener, at most one every `PREFETCH_CYCLE_SECONDS`. What is warmed is
  the **brief** - cheap enough that being wrong costs a fraction of a cent, and
  worth enough that being right removes the seconds EI costs a browse tap.
  **Still open: whether to warm scripts**, which is the question that actually
  costs money, and it is now a measurement rather than a guess - let it warm
  and read the per-source hit rate off `/api/health` or
  `tools/prefetch_report.py --live`. A source warmed often and taken rarely is
  paying for episodes nobody wanted; one taken nearly every time is worth
  warming deeper (`PREFETCH_LEVEL=script`). One thing nothing
  warms yet: no cycle is scheduled for a listener who is not looking.
  DailyFAM is no longer a gap - since §143 its episodes are written whole by
  the daily edition, not guessed at by prefetch.
<!-- rule:dec-embedding -->
- **Is a local embedding model worth installing?** *Half answered (§107): the
  near-match cache is **on** now, and the embedding is still the part earning
  nothing.* The mechanism raises the share of re-phrasings that find an
  existing episode from 22% to 56% on a measured corpus - 9 of 41 to 23 of 41
  - with no false match at any threshold and 8.93 ms of local scanning, on the
  miss path only, with no model call anywhere. Fourteen episodes not written
  at roughly a cent each, so it ships on; `CACHE_VECTOR=0` restores the old
  behaviour exactly.
  The bench's control line is what is still open: **guards alone, with the
  cosine ignored, find the same 23.** Every must-not-collapse pair is refused
  by a guard - identical numbers, lexical overlap, agreement about needing
  today's facts - rather than by the threshold sitting above it, so the vector
  is carrying none of the gain. A real sentence model in `~/.fam/embed` is the
  only thing that changes that, and it is the same trade as the voices - ship
  a model with the app, or pay a service per call.
  **Answered for the cache, and the answer is "not by itself"** *(§131).*
  all-MiniLM-L6-v2 is installed by `tools/install_embed_model.py` and by the
  Dockerfile, and the bench with it finds the same 23 at the safe point: it
  reaches 37 of 41 with the overlap guard off, and serves "how old is the
  eiffel tower" for "how tall" at 0.879 on the way. Only a cross-encoder
  separates those, so the cache's guards and defaults are unchanged.
  **The model is used by the ranker instead** (`taste_vectors.py`), where a
  near miss costs a weaker tile rather than a wrong episode: a bounded
  additive term in Made for you for how close a tile is to what the listener
  has asked for, ~2 ms a warm page, `{}` and the old ranking with no model.
  And the order of that rail can now be **fitted to taps**
  (`learned_rank.py`, `python tools/learn_rank.py`): a logistic regression over
  the ranker's own six signals, rebuilt point in time from the impression
  log, stored only if it beats the hand-tuned order on held-out offers, and
  allowed to re-order what cleared the floor but never to admit a tile under it.
