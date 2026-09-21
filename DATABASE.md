# The database and the algorithm

What is actually stored, what actually reads it, and what the four things we
want to emphasise would cost. Written from a full read of the code on
`claude/kind-heisenberg-6thmxl`, not from memory of it.

`CLAUDE.md` says where the product is going; `MYFAM.md` describes the browse
page the ranker feeds. This file is the layer underneath both: the data, and
the one path from a row in it to a tile on a screen.

---

## Part 1 — What "the database" is

There is no database server. There are **thirteen SQLite files**, one per
subject, each owned by exactly one module, each opened in WAL mode with a
thread-local connection. `paths.py` resolves every one of them from the
project root or from an absolute environment variable, never from the current
working directory, and the `Dockerfile` pins all thirteen to the mounted
`/data` disk so a redeploy does not erase them.

| File | Module | What it holds |
|---|---|---|
| `scripts.db` | `cache.py` | **The shared script cache.** The only store not keyed by person. |
| `myfam.db` | `topics.py` | **The event log.** Every interaction. This is the taste model's entire input. |
| `preferences.db` | `preferences.py` | One row per listener: chosen interests, chosen topics, language, what is shown on the profile. |
| `accounts.db` | `accounts.py` | Credentials, sessions, third-party identities. |
| `social.db` | `social.py` | People, the follow graph, vibes. |
| `messages.db` | `messages.py` | Direct messages and per-thread read marks. |
| `saved.db` | `saved.py` | Save-for-later pointers and folders. |
| `shares.db` | `sharing.py` | Share links and their open counts. |
| `mixes.db` | `mixes.py` | DailyFAM mixes — topic ids and typed topics, never audio. |
| `quotas.db` | `quotas.py` | Per-window counters and per-episode charges. |
| `metering.db` | `metering.py` | One row per episode: tokens, searches, GPU seconds, dollars. |
| `attachments.db` | `attachments.py` | Extracted text from documents a search carried. |
| `voice_registry.db` | `voice_registry.py` | Which GPU workers have announced themselves. |

**There are no foreign keys and no joins across files.** `user_id` is the only
thing that connects them, and the connecting happens in Python, in `app.py`,
one store at a time. That is a real constraint and it is deliberate: SQLite
with no server is what lets a fresh clone run with no infrastructure at all,
and the price is that "everything about this listener" is a fan-out across
seven files rather than a query.

### The one division that matters

Twelve of the thirteen are **per-listener**. One is not.

`scripts.db` is keyed on `pipeline.key_for(plan)` — a hash of the normalised
question, the length, the context and whether it was researched. **The
listener is not in that key and must never be.** Two people asking the same
thing share one script and one model call, which is the entire cost design of
the product: cost per listener falls as listeners are added. `scripts.author`
records who generated an entry first, but only so Explore can keep a
listener's own episodes off their own feed — it is provenance, never identity,
and `tests/` reads `key_for`'s own source to make sure the word never appears
in it.

Everything in Part 4 has to respect that line. **Personalisation is free on
the selection side and ruinous on the generation side**, and the wall between
them is the cache key.

### The event log, in full

`myfam.db` is the whole personal model. One table:

```
events(id, user_id, kind, topic_id, text, tags, at, thread, section, algo)
```

* **`kind`** is one of `search`, `play`, `complete`, `skip`, `pick`,
  `impression`. Anything else is dropped with a warning.
* **`text`** is what they typed, or the tile's query. This is the search
  history, and it is the only record in FAM of *what* somebody was interested
  in rather than which of eight headings it lived under.
* **`tags`** are the facets and subtags matched at write time, so the ranker
  never re-derives them.
* **`section`** and **`algo`** are stamped on impressions only: which rail
  showed it, under which version of the ranking.

Behavioural rows are kept indefinitely. Impressions are pruned at 30 days
because a feed load writes ~18 of them.

### Everything else about a listener

* `preferences`: `interests` (facet slugs), `topics` (catalogue ids *and*
  free text somebody typed, newline separated because a comma is a legal
  character in "rocketry, but the engines"), `language`, `hidden_interests`,
  `profile_interests`, `intro_done`.
* `accounts`: `user_id`, `email`, `phone`, `password` hash, `display_name`,
  `plan`, plus `sessions` and `identities` (Google/Apple keyed on
  `(provider, subject)`).
* `social.people`: `name`, `handle`, `avatar`, `joined`, `last_seen`,
  `followers_seen`. `social.follows` is the asymmetric graph; a friend is the
  mutual case, derived and never stored.

**A listener id is always server-minted** and arrives on an HttpOnly cookie or
as a bearer token. Anonymous listeners get a full identity and a full event
log; an account is credentials *attached to an id that already exists*, which
is why signing up late loses nothing.

---

## Part 2 — How the algorithm works

All of it is in `topics.py` (2,866 lines), and all of it is a **pure query over
the event log**. Nothing about a listener is stored pre-computed. That is a
deliberate call: a stored profile is a cache that can disagree with the log it
came from, and this one cannot.

### The chain, end to end

```
events ──tags_for_text──▶ taste(profile) ──_affinity──▶ rank_*() ──diversify──▶ section
                                   ▲                        ▲
            preferences.interests ─┘        fatigue(impressions) ─┘
```

**1. Tagging.** `tags_for_text` is a set intersection between the words of the
question and `TAG_WORDS` — 8 facets and 29 subtags, 37 keyword lists written
by hand. A matched subtag always brings its parent facet with it, so the
vocabulary is additive. No classifier, no model call: a wrong tag costs one
mediocre recommendation, and classifying every search would cost more than the
episode it is recommending.

**2. Taste.** `taste(events, now, interests)` walks the log and sums

```
EVENT_WEIGHT[kind] × 0.5^(age / 14 days)
```

per tag, then normalises to a peak of 1.0 so scores are comparable between
listeners. Weights: `complete` 2.5, `pick` 1.6, `search` 1.0, `play` 1.0,
`skip` −1.5. Declared interests enter flat at 1.0 before normalising — a
starting position that behaviour outvotes within a day.

**3. Affinity.** `_affinity(topic, profile)` is a weighted sum over the tile's
tags divided by `sqrt(len(tags))`, so breadth does not beat precision. A
subtag match counts `SUBTAG_WEIGHT = 1.75` against a facet match.

**4. Three dampers**, each answering a different failure:

* `fatigue` — shown on many separate occasions and never played. Per-topic,
  never per-tag, and it can only push down. An impression may become fatigue
  and may **never** become taste, or the feed would teach itself its own
  preferences.
* `BROAD_MATCH_PENALTY = 0.3` — a *live story* whose only claim is a whole
  facet, on a subject whose words this listener has never used
  (`familiar_words` reads the log's own `text`). This is the small-school
  college football fix, and it damps rather than excludes.
* `RELEVANCE_FLOOR = 0.12` — below this a tile is not a recommendation, it is
  the least bad thing left, and the rail is better short than padded.

**5. Freshness.** `FRESHNESS_BOOST = 1.6` multiplied by `Story.push()`, so a
hot live story beats a standing explainer of the same affinity and a nearly
expired one does not.

### The six rankings

| Ranking | Question it answers | Reads |
|---|---|---|
| `rank_from_history` | closest to what they play | taste, both inventories |
| `rank_might_like` | adjacent but not identical | taste (currently off-page, `UNSHELVED`) |
| `rank_friends` | what people they follow played | follow graph + everyone's plays |
| `rank_most_played` | what FAM plays | global play counts, identical for everyone |
| `rank_missed` | offered / played / trending this week and not taken | impressions + crowd plays + pool |
| `rank_startup` | cold start | `popular_facets`, global |

`build_feed` fills them most-constrained-first (`FILL_ORDER`), never repeats a
tile, reserves `WORLD_FLOOR = 4` live stories for Trending *before* the loop
runs, caps each rail at two per facet and then tops it back up. It reads two
caches somebody else refreshed in the background and **cannot cause a model
call** — there is no path from opening myFAM to generating anything.

### The three inventories it ranks

* **The evergreen bank** — 28 hand-written topics, shared by everybody. 27
  distinct tag signatures across 28 topics.
* **The live story pool** — up to 24, composed by one small model call per
  refresh window *for the whole deployment*, from four sources (GDELT,
  Finnhub, Polymarket, API-Sports). Off until `GDELT=1`; no deployment has it.
* **The startup set** — 8 time-anchored questions, one per facet, for a
  listener with no history at all.

### What the algorithm does *not* touch

Once a tile is tapped, the ranker is finished. `EpisodePlan` — the thing the
writing pipeline is handed — carries the query, the length, the context, the
attachments and the evidence. **It carries nothing about the listener**, and
`episode_intelligence.understand(query, minutes, context)` never sees one
either. Every episode about a given question is written identically for
everybody, because it has to be: the alternative is a private cache per
listener and the end of the cost model.

So today, **"Episode intelligence + Algorithm = relevance" is two halves that
never meet.** The algorithm decides what to put in front of somebody; EI
decides how to research what they tapped; and no fact about the listener
crosses from the first to the second. Part 4 says the one safe way to change
that.

---

## Part 3 — What is knowingly absent

* **Location.** Nothing. Not a column, not a header read, not a setting. The
  word does not appear in a per-listener context anywhere in the codebase.
* **A category tree deeper than two levels.** `TAG_PARENT` is one level of
  parenting over eight roots, hand-written, with hand-written keyword lists.
* **Any model of what somebody will click.** Affinity answers "will they
  like it". Nothing answers "will they tap it" except fatigue, which only ever
  says no.
* **Any embedding of a listener or a topic.** `embeddings.py` exists and is
  used for near-match *cache lookups* only, with a hashing backend; the ONNX
  path has never been run on any machine here.
* **Time of day, day of week, device, session length.** All derivable from
  `events.at` and none derived.
* **Collaborative filtering beyond raw play counts.** No co-occurrence matrix,
  no matrix factorisation, no neighbourhood model.

---

## Part 4 — The four things to emphasise

### 1. Profiles, interests, login, search history and location drive the algorithm

**Three of the five already do, and they do it well.** The event log holds
every search anybody has typed and it is the primary input to `taste`;
`preferences` holds chosen interests and chosen topics and they seed `taste`
before behaviour arrives; the account ties both to a durable identity across
devices.

Four things are genuinely wrong or missing, in the order they cost:

**(a) Location does not exist.** See §4 below.

**(b) A signal is being written and silently thrown away.** `app.py:2008`
records a `share` event when somebody sends an episode to a friend. `share`
is not in `EVENT_KINDS`, so `EventStore.record` logs *"ignoring unknown event
kind 'share'"* and drops the row. Sending somebody an episode is one of the
strongest taste signals in the app — arguably stronger than finishing one —
and none of it has ever been recorded. Verified by running it. The fix is two
lines (an `EVENT_WEIGHT` entry, which is a number worth choosing on purpose;
`2.0`, between a play and a completion, is the honest starting position).

**(c) "Once an account is created" would be a narrowing, not a widening.**
Today the event log is deliberately *outside* the account gate: an anonymous
listener gets a full history and a fully ranked feed, and `ACCOUNT_REQUIRED`
covers only what is *kept* — mixes, chosen interests, saved episodes. If
profiles only begin at sign-up, a guest gets an unranked feed, which reverses
the settled constraint that an account gates what is kept and never what is
heard. **Recommendation: keep collecting from the first tap and let the
account make it durable**, which is what happens now and is why signing up
late loses nothing.

**(d) Durability is measured but not yet proven on the deployment.** §114
added a boot-time announcement and `/api/health` reports `storage` as
`disk`/`image`/`unknown` measured by `st_dev`. `python tools/storage_doctor.py
--url <deployment>` answers it for real. Worth running before any of this,
because a richer profile stored on an ephemeral disk is a richer thing to
lose.

### 2. Categories with no ceiling, tiered, and maintained automatically

This is the largest of the four and the one that changes the most files.

**The ceiling, quantified.** 8 facets → 29 subtags → stop. 37 hand-written
keyword lists. 28 bank topics carrying 27 distinct tag signatures. There is no
`nfl` tag, no `college-football` tag and no `bengals` tag; all three are
`sports`, and `BROAD_MATCH_PENALTY` exists precisely because *the vocabulary
cannot express the difference* and something had to be done about it without a
vocabulary. `Sports → American Football → NFL → Cincinnati Bengals` is four
levels deep. The current tree can hold two.

**What a growing taxonomy must not break**, or it will cost more than it buys:

1. **A tag must mean the same thing to two listeners.** Per-listener
   vocabularies would kill `rank_friends`, `rank_most_played` and
   `rank_missed`, all of which compare listeners through shared topic ids and
   tags. The tree must be global.
2. **Nothing on the page-load path may call a model.** `build_feed` is a pure
   function of the log plus two background caches, and that is what makes
   myFAM instant. Minting categories happens in the background sweep, never in
   front of a listener.
3. **The pickable vocabulary stays small.** The eight facets are a *screen*,
   not the ranking vocabulary. A 400-node tree is a fine ranker and a terrible
   first-run picker.
4. **A new node must not rewrite history.** Events carry the tags matched at
   write time. A node minted today applies from today; back-filling would
   silently change what every past feed meant.

**The shape that satisfies all four.** A `categories` table in `myfam.db`:

```
categories(id, label, parent_id, depth, source, first_seen, uses, aliases)
```

fed by three things that already flow through the app and are already free:

* **searches** — `events.text` for `kind='search'`, which is the only place
  somebody names a subject in their own words;
* **story subjects** — `stories.py` already resolves a subject per live story
  and already makes one model call per refresh window for the whole
  deployment;
* **typed topics** — `preferences.topics`, where somebody has literally
  written down an interest the catalogue could not express.

A phrase is **promoted to a node** when it has been seen by enough distinct
listeners or stories to be a subject rather than a typo. Its parent is
assigned **once**, in the same background call that composes stories, so
maintaining the tree costs a handful of output tokens per window rather than a
call per listener or per search. Depth is whatever the parent's depth plus one
is, so the tree deepens on its own where the traffic is and stays shallow
where it is not.

**Matching is where the keyword sweep runs out.** Set intersection over
hand-written word lists cannot scale to hundreds of nodes — the lists would
have to be written. This is the point at which a real sentence embedder stops
being a nice-to-have: `embeddings.py` already exists, the hashing backend
already measures, and `CLAUDE.md`'s open question *"is a local embedding model
worth installing?"* currently answers "not yet, the guards are doing the work".
**A learned taxonomy is the thing that makes it pay.** Worth deciding
deliberately and in that order: the tree first, on keywords plus aliases;
embeddings when the tree is large enough that the keywords visibly miss.

**Guard rails to write down with it**, each of which has a precedent in this
codebase: growth is capped per window (`MAX_PER_SOURCE` shape); a node with no
recent use expires and its clock outlives its membership (§103, "an eviction
that forgets is a creation"); an unmatched phrase damps and never excludes
(`BROAD_MATCH_PENALTY`); and a minted node never becomes pickable without
somebody deciding it should (`TAG_LABELS` stays hand-written).

**What it buys, concretely.** A listener who has played three NFL episodes
gets `sports → american-football → nfl` weight and a college football story
matches only at `sports`, two levels up, scored accordingly — by the ranking
rather than by a special-case penalty bolted on beside it. That is the
reported complaint, fixed at the level it actually lives at.

### 3. The algorithm's two questions

Today the code answers one and a half:

* *"What will they like?"* — `_affinity` over `taste`. Answered.
* *"What will they tap?"* — nothing positive. `fatigue` is the only thing in
  the system that reads an impression, and it can only ever say **no**.

**The data to answer the second is already being written and has never been
read for it.** Every impression row carries `(user_id, topic_id, section,
algo, at)` and every play carries `(user_id, topic_id, at)`. Joining them is a
per-tile, per-rail, per-algorithm-version click-through rate — an honest
engagement dataset, thirty days deep, already in `myfam.db`. Nobody has
queried it that way. `tools/` has no report for it, which is the first thing
to build, because a number nobody can see is a number nobody can improve.

**The shape to add**, deliberately separate from affinity so the two can be
tuned and read apart:

```
score = affinity(topic, taste)          # will they like it
      × engagement(topic, listener)     # will they tap it        ← new
      × fatigue(topic)                  # have they ignored it
      × (1 + FRESHNESS_BOOST × push)    # is it about today
```

`engagement` is an empirical play rate with a prior — plays over impressions
for this topic, blended with the rate for its category and with the global
rate, so a tile with three impressions is not treated as measured. **No model
and no training run** for the first version; it is two aggregate queries over
a table that already exists.

**The danger, named so it is a decision and not a discovery.** An engagement
term optimised alone converges on whatever is most clickable for everybody,
which is a row the app *already has* and deliberately keeps separate
(`most_played`, "What FAM can't stop listening to"). If it bleeds into "Made
for you", the two rails become the same rail and the page loses a signal. So:
bound it (a multiplier in roughly `[0.5, 1.5]`, not an unbounded score), keep
it out of `rank_most_played` entirely, and bump `ALGO_VERSION` when it ships
so the impression log can tell the two regimes apart. That last part is what
makes the change measurable rather than believed.

### 4. Location

**Feasibility, honestly, three routes:**

| Route | Precision | Permission | Cost | Works for |
|---|---|---|---|---|
| IP geolocation, server-side | City, often wrong on mobile carriers and VPNs | None | A provider or a bundled database | Web and app |
| Browser Geolocation API | Precise | A prompt, per site | Free | Web only |
| iOS CoreLocation | Precise | A prompt, per app | Free | The app that is coming |

All three are feasible. Behind Render the server sees the router's address, so
IP lookup has to read `X-Forwarded-For` — the app already has the precedent
for reading forwarded headers correctly (`app._public_base` reads
`X-Forwarded-Proto` for exactly this reason).

**Recommendation: the typed field is primary, exactly as asked.** City, region
and country, collected in the first run beside the name and the handle — it
belongs in that step, which already asks who somebody is and already has a
"Continue as guest" door. Reasons, in order: it is stable (an IP changes on
every train journey and a listener's interests do not), it is correctable, it
needs no permission prompt in front of a product somebody has not heard yet,
and it is visible — a location a listener cannot see is a guess about where
they are, which is the same argument that put a preview on the avatar crop.
IP lookup is worth adding later as a *suggested default for that field*, shown
and editable, never a silent value.

**Where it is stored: `preferences`, not `accounts`.** Accounts hold
credentials; preferences hold settings, and a location is a setting. Three
additive columns (`city`, `region`, `country`), the same guarded
`ALTER TABLE` every other column in that file got, validated the way
`language` is validated against `LANGUAGES`.

That puts it behind the existing account gate, because `POST
/api/preferences` requires one — which matches the ask ("when someone creates
their account, they should put in their location") and keeps the settled
boundary intact: an account gates what is *kept*, never what is heard. A
guest still gets a fully ranked feed, just not a stored location. If a
location-aware feed for guests turns out to matter, the honest way to get it
is an IP-derived *session* default that is never written to the row — not an
exception carved into the gate.

**How it drives the algorithm — two places, one of which is a trap:**

*Safe, and where most of the value is:* **selection.** A location becomes a
node in the tree from §2 (`world → united-states → ohio → cincinnati`), so it
is scored by the same machinery as every other category rather than by a
special case. Local stories from the pool get a boost; the startup set can
lead with a local question for a listener we otherwise know nothing about,
which is the single best use of it — it is exactly the cold-start listener the
prior exists for. `story_sources.py` can ask GDELT for a region.

*The trap:* **generation.** If location becomes a hidden field on
`EpisodePlan`, it enters `key_for`, and at that moment every listener has
their own cache, the shared-cost design is gone, and **nothing fails** — the
app keeps working and quietly costs several times more. The rule is
`scripts.author`'s rule, one layer earlier.

The way through is that **location personalises the question, never the
episode**. A Cincinnati listener is offered *"what the Bengals' cap situation
means for next season"*; the episode written for that question is shared with
every other Bengals listener in the world, because the location did its work
before the question was chosen and does none after. That preserves the cache
exactly and still gets a local feed.

**And it must damp and boost, never filter.** A location filter empties a rail
for somebody in a small town — the same failure `BROAD_MATCH_PENALTY` avoids
by damping, and the same failure `RELEVANCE_FLOOR` accepts a short rail to
avoid.

---

## Part 5 — What this read turned up

1. **`share` events are written and dropped** (`app.py:2008` against
   `topics.EVENT_KINDS`). Reproduced. One of the strongest taste signals in
   the app has never reached the ranker.
2. **Episode intelligence is listener-blind by construction**, and that is
   currently load-bearing rather than an oversight. Any plan that says
   "episodes created that are relevant to their interests" has to route the
   personalisation through the *choice of question*, not through the
   generation call.
3. **The click-through data already exists**, thirty days deep, and nothing
   reads it. This is the cheapest of the four asks to start on and the only
   one that needs no new collection.
4. **The taxonomy ceiling is 37 tags and two levels**, and three separate
   mechanisms (`SUBTAG_WEIGHT`, `BROAD_MATCH_PENALTY`, `familiar_words`) exist
   to work around what the vocabulary cannot say. That is a strong argument
   that the vocabulary, not the scoring, is the binding constraint — which is
   the same conclusion §80 reached one level up.

**Suggested order**, cheapest-first and each independently useful: the `share`
signal and a CTR report (hours) → location field and its node (a day) →
engagement term (a day, once the report says what it is worth) → the growing
category tree (the real project, and the thing the other three get better
under).
