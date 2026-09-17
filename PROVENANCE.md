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

## In the interface: a strip, then a popup

Under the player is a strip of publisher marks and a count. Tapping it opens
the list: the headline, the outlet, its grade and date, and a way to open it.

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

A collapsed "Sources" panel under the captions on the player. Collapsed by
default: it is something a listener reaches for to check, not something to read
while listening. A panel that fails to load never disturbs playback.
