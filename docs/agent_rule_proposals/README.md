# Proposed rule-book changes, waiting for the owner

A session never edits a signed rule book: a changed book holds its agent
and fails the build until the owner signs it (`docs/agent_rules/README.md`).
So a change the owner has agreed to waits here, as the whole proposed
file, until the owner signs it. Nothing reads this folder at run time.

## Waiting now

**Only a signature, no book change (PROBLEMS.md §468, §469).** Two lines of
signing crossed: this branch's version 4 (`consuela.md` always 12 and
`sonic.md` always 8, signed 2026-11-03, §464) and Main's versions 4 and 5
(`mclovin.md` v2 and v3, §441, §443). Every book is already exactly as the
owner signed it; only the manifest listing them all is new:
`docs/agent_rules/MANIFEST.pending.json`, version 6. Until it is signed,
`consuela.md` and `sonic.md` do not match the signed manifest, so Consuela
and Sonic are held and the rule-book tests fail.

To sign it: steps 2-3 below with two files only - download
`MANIFEST.pending.json`, rename it `MANIFEST.json`, sign, and upload
`MANIFEST.json` and `MANIFEST.json.sig`.

Before: `mclovin.md` v3 (§443, version 5); v2 (§441, version 4);
`consuela.md` with Perplexity (§412, version 3); `nando.md` rule 12 (§392).

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
