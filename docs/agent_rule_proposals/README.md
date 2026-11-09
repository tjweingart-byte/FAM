# Proposed rule-book changes, waiting for the owner

A session never edits a signed rule book: a changed book holds its agent
and fails the build until the owner signs it (`docs/agent_rules/README.md`).
So a change the owner has agreed to waits here, as the whole proposed
file, until the owner signs it. Nothing reads this folder at run time.

## Waiting now

**`nando.md` rule 12, amended (PROBLEMS.md §502)**, with
`docs/agent_rules/MANIFEST.pending.json` (version 7; only `nando.md`'s
fingerprint changes). Rule 12 as signed lets the answer key's grades "only
teach you and measure you"; the amendment adds "and teach the story
composer whose categories you inherit", so the composer is shown its own
categories the key was sure were wrong. Until it is signed the code shows
the composer nothing (`training.composer_allowed`). Nothing else in the
book changes.

Before that: manifest version 6 - the merged books, `consuela.md` always 12
and `sonic.md` always 8 beside `mclovin.md` v3 - was signed on 2026-11-06
(PROBLEMS.md §470); `mclovin.md` v3 (§443, version 5); v2 (§441, version
4); `consuela.md` with Perplexity (§412, version 3); `nando.md` rule 12
(§392).

## Signing it (on the owner's Mac, with the owner's key)

When a proposal is waiting, the session writes
`docs/agent_rules/MANIFEST.pending.json`: the manifest as it will be once the
proposal replaces its book - only that book's fingerprint changes. No clone
is needed (§352, §392):

1. From GitHub, on the branch, download
   `docs/agent_rule_proposals/<book>.md` and
   `docs/agent_rules/MANIFEST.pending.json` to Downloads; rename the second
   `MANIFEST.json`.
2. In Terminal: `conda activate famsign` (OpenSSL 3), then save the private
   key from TextEdit (plain text) as `~/fam-rules-signing.pem` - never paste
   it into Terminal - and run:

       cd ~/Downloads
       openssl pkeyutl -sign -rawin -inkey ~/fam-rules-signing.pem -in MANIFEST.json | base64 | tr -d '\n' > MANIFEST.json.sig
       rm -P ~/fam-rules-signing.pem

3. On GitHub, in `docs/agent_rules/` on the branch, **Add file → Upload
   files**: `<book>.md`, `MANIFEST.json`, `MANIFEST.json.sig`, in one
   commit. Nothing else - check no stray file came with them (§392).

The session then verifies the signature
(`python tools/rulebook_manifest.py --verify`), deletes the proposal and the
pending manifest, and the owner updates the copy held outside Claude.
