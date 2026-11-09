# Proposed rule-book changes, waiting for the owner

A session never edits a signed rule book: a changed book holds its agent
and fails the build until the owner signs it (`docs/agent_rules/README.md`).
So a change the owner has agreed to waits here, as the whole proposed
file, until the owner signs it. Nothing reads this folder at run time.

## Waiting now

**`journalism_playbook.md` v2** - Part 3 gains "Story shapes", the ten
shapes and `general` as drafted in `docs/story_shapes.md` (the owner,
2026-11-09: "Keep the list as drafted and sign the wording"; PROBLEMS.md
§505). `docs/agent_rules/MANIFEST.pending.json` is version 7: only the
playbook's fingerprint changes. **The playbook is not in
`docs/agent_rules/`**: it is `docs/journalism_playbook.md`, so in step 3 it
is uploaded to `docs/`, and `MANIFEST.json` and its `.sig` to
`docs/agent_rules/` - two uploads, or one commit made from a clone.

Before it: manifest version 6 - the merged books, `consuela.md` always 12
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
