# Sources — what an episode was built on

Every researched episode now shows which outlets it drew on, their grade and
their dates. FAM already collected all of this and discarded it at the last
step; this is mostly plumbing for data that existed.

## The rule that makes it safe

**The display channel is not the prompt channel.**

CLAUDE.md is emphatic that the evidence packet carries source *grades* and
never hostnames — "a domain in the packet is a domain the voice can read out",
and the model needs to know it is reading a wire service in order to weigh it,
not a way to say "reuters dot com" aloud.

Nothing in the provenance path reaches a prompt. `research.domains()` has
always existed "for a person to judge"; this gives that output somewhere to go.

**The obvious next step is the one to refuse.** "We show sources now, so let
the model cite them" would put hostnames back in front of the voice, which is
exactly the drift CLAUDE.md warns about. A test reads `build_prompt` and
asserts it mentions neither provenance nor hostnames.

## The shape

    Attribution(label, kind, tier, at, title, private, url)
    Provenance(items, retrievers)

`kind` is `article`, `live` or `attachment` — they are different claims and a
listener weighs them differently. `retrievers` is which *indexes* were
consulted, carried separately from which *outlets* published, because "two
indexes agreed" is a different claim from "two newspapers agreed" and
collapsing them would overstate the corroboration.

`url` is where to read it, and only an article ever has one — a live provider
is a feed rather than a page, and an attachment is a file on the listener's own
device. It is the same fact `label` already carries, made tappable: `label`
stays a hostname because that is what gets *shown*. It changes nothing about
the rule above — nothing here reaches `build_prompt`, and the test that reads
it is unchanged.

## In the interface: a corner cluster, then a popup

In the **corner of the player** are the first three publishers' marks,
overlapped, with a count. Tapping opens the list: the headline, the outlet,
its grade and date, and a way to open it.

Three, at the owner's direction, and it is the right number for a cluster this
size — more than that at 20px is a row of dots nobody can tell apart, and the
whole list is one tap away. Overlapped rather than spaced, because that reads
as "these several" where a spaced row reads as a list somebody has to count.

In the corner rather than as a full-width strip under the title, because it is
something a listener glances at and not a line of the page.

**It was invisible for as long as it has existed, and the reason was one
missing call.** `fetchEpisodeSources` ran in `onEnd` — when the episode
finished — and when Go Deeper opened, and nowhere else. So the one moment the
panel is actually for, somebody listening and wondering where this came from,
was the one moment nothing asked for it. It is now fetched on the same clock
as `/api/next`: a few seconds after the first word, and once more later,
because provenance lands with the finished script and a 10-minute script takes
longer to write than a 2-minute one.

Two smaller things went with that. An answer that comes back empty **does not
clear a strip that is already showing** — the first try often lands before the
script has finished, and a panel that disappeared halfway through an episode
would be worse than one that arrived late. And switching **captions** off used
to call `clearSources()` twice, one of the calls mis-indented under the line
above it, so turning off one control wiped a different control on a different
row about a different thing.

**The marks are drawn, not fetched.** An episode's source list is a list of
what somebody just listened to, so pinging five publishers to decorate it
would tell each of them that — for every episode, whether or not anyone ever
looked. The mark is two letters of the hostname on a colour hashed from it, so
one publisher is the same colour every time and nothing leaves the device.

The publisher's own icon is loaded **inside the popup only**, which opens when
the listener has asked to see the sources, and with `referrerpolicy="no-referrer"`
so the request says nothing about which episode it was for. A failure leaves
the drawn mark it was laid over, so a row is never a broken image.

## What is private

**An attachment title is the listener's own document and is never written to
the shared cache.** The script cache is shared and feeds Explore, so a cached
title would show one listener the name of another listener's file.
`Provenance.shareable` drops anything private before storage; an attached
episode is already uncacheable, which makes this belt and braces.

Attachment titles *are* shown to the listener who attached them, on the live
response.

## Why it is cached beside the script

A cache hit replays sentences and has no `notes` to rebuild provenance from.
Without storage, a shared or Explore episode would show an empty panel while a
freshly generated one showed a full list — the same inconsistency the `thread`
column already solved, fixed the same way: a `sources` column added by additive
migration, and a `sources(key)` accessor on both cache backends.

## Who is credited

Only what actually contributed.

* Articles that made it **into the packet** — not everything retrieval
  returned, because the writer never saw the rest.
* A live provider **only on `facts`**. One that failed, timed out, found no
  matching entity or returned something too stale to use did not contribute,
  and listing it would claim corroboration that did not happen.
* A delayed feed says so in the panel, not just in the prompt.

One outlet quoted three times is one line.

## The API

    GET /api/sources?q=...&minutes=...&context=...

    {"items": [...], "retrievers": ["exa", "gdelt"], "count": 3, "known": true}

`known` distinguishes **"we recorded no sources"** from **"there were none"**.
An episode with no stored provenance may simply predate this, or have been
answered from knowledge — rendering that as "no sources" would be the §89
mistake again.

Fetched after playback starts, alongside `/api/next`, because provenance is
only known once the script has been written — which is after the first word is
already playing.

## The interface

A cluster in the player's corner, hidden until there is something in it — so
an episode answered from knowledge has an uncluttered corner rather than an
empty label. A panel that fails to load never disturbs playback.

## Live captions read the same cache

    GET /api/transcript?q=...&minutes=...&context=...

    {"sentences": ["...", "..."], "known": true}

The captions tab did nothing for as long as it existed: it showed
`t.caption`, a line of prototype copy ending in an em dash, so turning
captions on produced a sentence about captions.

It reads `pipeline.script_for`, which is `sources_for` and `thread_for`'s
sibling and makes the same read against the same key. **The rule that makes it
affordable is that it never generates.** Captions that could trigger a write
would be a second full Claude call for every episode somebody chose to read
along with — the expensive half of an episode, paid twice for one listen. So
a miss is an empty list.

Three states, and they are different:

* **sentences** — the script is in the cache, which it usually is within a few
  seconds of the first word, because a script is written far faster than it is
  spoken;
* **still catching up** — asked, not there yet, asked again;
* **never** — an attachment episode is deliberately uncacheable, so there is
  nothing to read back. That is the privacy rule working, and the panel says
  so rather than waiting forever.

**Which sentence is highlighted is estimated, not measured,** and that is
worth naming rather than hiding: the audio is one PCM stream with no sentence
marks in it, so there is nothing to measure against. Speech time is close to
proportional to character count at the rate the pace controller is holding, so
the highlight lands within a sentence or so. The denominator is the *planned*
length rather than what has buffered — `FamAudio.duration()` grows as the
stream arrives, so dividing by it would pin the highlight near the end for the
whole episode.

The panel shows a window of four lines around the current one rather than the
whole script. A transcript that scrolls itself is a reading surface, and a
player is not one.

The copy button on that panel used to toast "Transcript copied ✓" and copy
nothing at all — the control-with-nothing-behind-it failure in its worst form,
because it said the thing had happened. It copies the sentences now, and says
so honestly when there are none.
