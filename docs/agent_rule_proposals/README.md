# Proposed rule-book changes, waiting for the owner

A session never edits a signed rule book: a changed book holds its agent
and fails the build until the owner signs it (`docs/agent_rules/README.md`).
So a change the owner has agreed to waits here, as the whole proposed
file, until the owner signs it. Nothing reads this folder at run time.

## Waiting now

**The answer-key rules (PROBLEMS.md §418)**: `consuela.md` gains always 12
and `sonic.md` always 8 - in the shape of `nando.md` rule 12, a stronger
model chosen by the owner grades a sample of the agent's work each night,
in the background, and its grades only teach and measure, never change the
archive or an episode. Nothing else in either book changes
(`tests/test_answer_key_proposals_418.py`). Before: `consuela.md` with
Perplexity (§412, version 3); `nando.md` rule 12 (§392).

## Signing it (on the owner's Mac, with the owner's key)

The session has written `docs/agent_rules/MANIFEST.pending.json` (version
4): the manifest as it will be once the two proposals replace the signed
books - only `consuela.md`'s and `sonic.md`'s fingerprints change. No clone
is needed (§352, §392, §412):

1. From GitHub, on the branch, download
   `docs/agent_rule_proposals/consuela.md`,
   `docs/agent_rule_proposals/sonic.md` and
   `docs/agent_rules/MANIFEST.pending.json` to Downloads; rename the last
   `MANIFEST.json`.
2. In Terminal: `conda activate famsign` (OpenSSL 3), then save the private
   key from TextEdit (plain text) as `~/fam-rules-signing.pem` - never paste
   it into Terminal - and run:

       cd ~/Downloads
       openssl pkeyutl -sign -rawin -inkey ~/fam-rules-signing.pem -in MANIFEST.json | base64 | tr -d '\n' > MANIFEST.json.sig
       rm -P ~/fam-rules-signing.pem

3. On GitHub, in `docs/agent_rules/` on the branch, **Add file → Upload
   files**: `consuela.md`, `sonic.md`, `MANIFEST.json`, `MANIFEST.json.sig`,
   in one commit. Nothing else - check no stray file came with them (§392).

The session then verifies the signature
(`python tools/rulebook_manifest.py --verify`), deletes the proposal and the
pending manifest, and the owner updates the copy held outside Claude.
