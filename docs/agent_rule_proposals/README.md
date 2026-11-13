# Proposed rule-book changes, waiting for the owner

A session never edits a signed rule book: a changed book holds its agent
and fails the build until the owner signs it (`docs/agent_rules/README.md`).
So a change the owner has agreed to waits here, as the whole proposed
file, until the owner signs it. Nothing reads this folder at run time.

## Waiting now

Nothing. Manifest version 9 - `egan.md`, a new agent, Egan (§579-§582) -
was signed on 2026-11-13 on the owner's MacBook Air with
`tools/sign_rulebooks.py` and uploaded with the book; the session verified
it, registered him and deleted the proposal (§582).

Before that: manifest version 8 - `journalism_playbook.md` v2, Part 3's story
shapes, on top of Main's version 7 - was signed on 2026-11-10 on the
owner's MacBook Air (OpenSSL 4.1 in a Miniforge `famsign` environment) and
uploaded to this folder; the session verified it and moved the files into
place (PROBLEMS.md §534). Before that: version 7, `nando.md` rule 12
amended (§502, §503), signed on a borrowed computer with
`tools/sign_rulebooks.py` (§505); version 6, the merged books (§470);
`mclovin.md` v3 (§443, version 5); v2 (§441, version 4); `consuela.md`
with Perplexity (§412, version 3); `nando.md` rule 12 (§392).

## Signing on a computer without OpenSSL 3 (§504)

`tools/sign_rulebooks.py` makes the same signature with Python alone (a
test holds it byte for byte equal to OpenSSL's). Put it in one folder with
`MANIFEST.pending.json` and the changed book, install the one package
(`python3 -m pip install --user cryptography`), and run
`python3 sign_rulebooks.py` there. It waits while the key is copied from
the password manager, reads it from the clipboard, empties the clipboard,
refuses any key but the one in `owner.pub`, and writes `MANIFEST.json` and
`MANIFEST.json.sig`. Nothing is pasted and the key is never saved.

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
