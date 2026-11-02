# Proposed rule-book changes, waiting for the owner

A session never edits a signed rule book: a changed book holds its agent
and fails the build until the owner signs it (`docs/agent_rules/README.md`).
So a change the owner has agreed to waits here, as the whole proposed
file, until the owner signs it. Nothing reads this folder at run time.

## Waiting now

- **`consuela.md`** (PROBLEMS.md §398): never 8's list of approved
  providers gains Perplexity, which the owner approved on 2026-11-02. The
  rule itself - keep a provider's material only once the owner has
  approved it - is unchanged; only the "so far" list is brought up to date.
  Nothing else in the file changes.

`nando.md` rule 12, the answer key, was signed on 2026-11-01 (§392).

## Signing it (on the owner's machine, with the owner's key)

    cp docs/agent_rule_proposals/consuela.md docs/agent_rules/consuela.md
    python tools/rulebook_manifest.py --write
    mv docs/agent_rules/MANIFEST.pending.json docs/agent_rules/MANIFEST.json
    openssl pkeyutl -sign -rawin -inkey ~/fam-rules-signing.pem \
      -in docs/agent_rules/MANIFEST.json | base64 | tr -d '\n' \
      > docs/agent_rules/MANIFEST.json.sig
    python tools/rulebook_manifest.py --verify
    git rm docs/agent_rule_proposals/consuela.md

If the key is not kept in that file, save it there only for this and
delete it with `rm -P ~/fam-rules-signing.pem` straight after `openssl`;
never paste the key into a terminal (§392).

Commit `consuela.md`, `MANIFEST.json`, `MANIFEST.json.sig` and the removal in
one commit. Update the held copy outside Claude to match.
