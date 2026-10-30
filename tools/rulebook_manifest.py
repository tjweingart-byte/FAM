"""The rule-book manifest: write it for the owner to sign, and check it.

    python tools/rulebook_manifest.py --status    # each book, its hash, signed or not
    python tools/rulebook_manifest.py --write     # a new MANIFEST.json to sign
    python tools/rulebook_manifest.py --verify    # exit 1 unless every agent may run
    python tools/rulebook_manifest.py --commands  # how the owner signs it

This tool never makes, reads or asks for a private key: it hashes the rule
books and checks a signature with the public key. Signing is the owner's,
on the owner's own machine, with OpenSSL (`--commands`), so the key never
touches the repository, a server, `FAM_SECRETS` or a session
(docs/FAM_AGENTS.md, Shared rules, Locked rule books; PROBLEMS.md §343).
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agents import names, rulebooks  # noqa: E402

COMMANDS = """\
Sign the rule books (once to start, then after every change to one):

  1. Make your key, once. Keep the private key OFF this repository - in a
     password manager or on a hardware key - and never paste it anywhere:

       openssl genpkey -algorithm ed25519 -out ~/fam-rules-signing.pem
       openssl pkey -in ~/fam-rules-signing.pem -pubout -out docs/agent_rules/owner.pub

  2. Write the manifest of fingerprints (this tool, no key involved):

       python tools/rulebook_manifest.py --write

  3. Sign it with your private key:

       openssl pkeyutl -sign -rawin -inkey ~/fam-rules-signing.pem \\
         -in docs/agent_rules/MANIFEST.json | base64 | tr -d '\\n' \\
         > docs/agent_rules/MANIFEST.json.sig

  4. Check it, then commit owner.pub (the first time), MANIFEST.json and
     MANIFEST.json.sig:

       python tools/rulebook_manifest.py --verify

OpenSSL 3 is needed for Ed25519 signing (`openssl version`; on a Mac,
`brew install openssl@3` and use its `openssl`). The private key file is the
only thing that can sign: move it to your password manager or hardware key
and delete ~/fam-rules-signing.pem once you have.
"""


def status() -> int:
    rulebooks.reset()
    state = rulebooks.load()
    print(f"folder: {rulebooks.rules_dir()}")
    print(f"signed: {state.signed}" + (f" (version {state.version}, key {state.key_source})"
                                       if state.signed else f" - {state.reason}"))
    for name in sorted(state.hashes):
        signed = state.signed_hashes.get(name)
        mark = ("matches" if signed == state.hashes[name] else
                "NOT IN MANIFEST" if signed is None else "DIFFERS") if state.signed else "unsigned"
        print(f"  {name:28} {state.hashes[name][:16]}  {mark}")
    return 0


def write() -> int:
    folder = rulebooks.rules_dir()
    path = folder / rulebooks.MANIFEST
    version = 0
    if path.exists():
        try:
            version = int(json.loads(path.read_text())["version"])
        except Exception:  # noqa: BLE001
            version = 0
    data = rulebooks.manifest_for(rulebooks.book_paths(folder), version + 1)
    path.write_bytes(data)
    print(f"wrote {path} (version {version + 1}). Sign it before it counts:")
    print("  python tools/rulebook_manifest.py --commands")
    return 0


def verify() -> int:
    rulebooks.reset()
    state = rulebooks.load()
    if not state.signed:
        print(f"not signed: {state.reason}")
        return 1
    held = 0
    for role, (name, book, _s, _m) in names.AGENTS.items():
        lock = rulebooks.check(book)
        print(f"  {role:15} {name:12} {'may run' if lock.ok else 'HELD: ' + lock.reason}")
        held += not lock.ok
    print(f"manifest version {state.version}, signed with {state.key_source}")
    return 1 if held else 0


def main(argv: list) -> int:
    if "--write" in argv:
        return write()
    if "--verify" in argv:
        return verify()
    if "--commands" in argv:
        print(COMMANDS)
        return 0
    return status()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
