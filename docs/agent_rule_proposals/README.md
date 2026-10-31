# Proposed rule-book changes, waiting for the owner

A session never edits a signed rule book: a changed book holds its agent
and fails the build until the owner signs it (`docs/agent_rules/README.md`).
So a change the owner has agreed to waits here, as the whole proposed
file, until the owner signs it. Nothing reads this folder at run time.

## Waiting now

**`nando.md` - always 12, the answer key** (agreed by the owner
2026-10-31, PROBLEMS.md §369). Adds one rule and changes nothing else:

> 12. Let your answer key - a stronger model chosen by the owner - read each
>     finished episode once, in the background, to grade your filing. Its
>     grades only teach you and measure you; they never change an episode's
>     category without the owner's review (rule 10).

Until it is signed, the answer key grades nothing (`agents/training.py`,
`allowed`).

## Signing it (on the owner's machine, with the owner's key)

    cp docs/agent_rule_proposals/nando.md docs/agent_rules/nando.md
    python tools/rulebook_manifest.py --write
    mv docs/agent_rules/MANIFEST.pending.json docs/agent_rules/MANIFEST.json
    openssl pkeyutl -sign -rawin -inkey ~/fam-rules-signing.pem \
      -in docs/agent_rules/MANIFEST.json | base64 | tr -d '\n' \
      > docs/agent_rules/MANIFEST.json.sig
    python tools/rulebook_manifest.py --verify
    git rm docs/agent_rule_proposals/nando.md

Commit `nando.md`, `MANIFEST.json`, `MANIFEST.json.sig` and the removal in
one commit. Update the held copy outside Claude to match.
