"""Sign FAM's rule books on any computer: Python 3 and one package (§504).

The same signature `openssl pkeyutl -sign -rawin` makes - Ed25519 over the
manifest's exact bytes, base64 on one line - for a computer that has no
OpenSSL 3 (a Mac's own `openssl` is LibreSSL, which cannot, §470). One
signature covers every agent: the manifest lists every rule book's
fingerprint.

Put this file, `MANIFEST.pending.json` (or `MANIFEST.json`) and any changed
rule book in one folder, then from that folder:

    python3 sign_rulebooks.py

It never asks you to paste the key. It waits while you copy the private key
from your password manager, reads it from the clipboard, empties the
clipboard at once, and keeps the key in memory only - nothing is written to
disk but MANIFEST.json and MANIFEST.json.sig. It refuses a key that is not
the one FAM trusts (`docs/agent_rules/owner.pub`), and checks its own
signature before writing it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import platform
import subprocess
import sys

#: The public half FAM's lock trusts: `docs/agent_rules/owner.pub`'s key.
#: A test holds the two equal.
OWNER_PUBLIC_KEY = "MCowBQYDK2VwAyEA51PHkLmO8LcVAshpgKEKiRk0yK6ekeu8mA65DcmkKDU="


def _run(args: list, data: bytes = b"") -> bytes:
    return subprocess.run(args, input=data, capture_output=True, check=True).stdout


def read_clipboard() -> str:
    system = platform.system()
    if system == "Darwin":
        return _run(["pbpaste"]).decode("utf-8", "replace")
    if system == "Windows":
        return _run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"]
                    ).decode("utf-8", "replace")
    return _run(["xclip", "-selection", "clipboard", "-o"]).decode("utf-8", "replace")


def clear_clipboard() -> None:
    try:
        system = platform.system()
        if system == "Darwin":
            _run(["pbcopy"], b"")
        elif system == "Windows":
            _run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ' '"])
        else:
            _run(["xclip", "-selection", "clipboard"], b"")
    except Exception:  # noqa: BLE001
        print("  Could not empty the clipboard: copy something else now.")


def load_key(text: str):
    """The Ed25519 private key in `text`: a PEM block, or its one base64 line
    alone. Raises ValueError for anything else."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    text = (text or "").strip()
    if "BEGIN" not in text:
        body = "".join(text.split())
        text = "-----BEGIN PRIVATE KEY-----\n" + body + "\n-----END PRIVATE KEY-----"
    try:
        key = load_pem_private_key(text.encode("ascii", "ignore"), password=None)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("the clipboard does not hold a private key") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("that is not an Ed25519 key")
    return key


def public_of(key) -> str:
    from cryptography.hazmat.primitives import serialization

    der = key.public_key().public_bytes(serialization.Encoding.DER,
                                        serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode("ascii")


def sign(manifest: bytes, key, trusted: str = OWNER_PUBLIC_KEY) -> str:
    """The signature line for `manifest`, checked before it is returned.
    Raises ValueError for a key FAM does not trust."""
    if public_of(key) != trusted:
        raise ValueError("this is not the key FAM trusts (docs/agent_rules/owner.pub)")
    signature = key.sign(manifest)
    key.public_key().verify(signature, manifest)
    return base64.b64encode(signature).decode("ascii")


def check_books(folder: pathlib.Path, manifest: bytes) -> list:
    """Every rule book in `folder` against the manifest's fingerprint:
    `(name, ok)`. Books not downloaded are not checked - they are already
    on GitHub, unchanged."""
    books = json.loads(manifest.decode("utf-8")).get("books") or {}
    out = []
    for name, digest in sorted(books.items()):
        path = folder / name
        if path.exists():
            out.append((name, hashlib.sha256(path.read_bytes()).hexdigest() == digest))
    return out


def main() -> int:
    folder = pathlib.Path.cwd()
    source = folder / "MANIFEST.pending.json"
    if not source.exists():
        source = folder / "MANIFEST.json"
    if not source.exists():
        print("No MANIFEST.pending.json in this folder. Download it here first.")
        return 1
    manifest = source.read_bytes()
    try:
        version = json.loads(manifest.decode("utf-8"))["version"]
    except Exception:  # noqa: BLE001
        print(f"{source.name} is not a manifest. Download it again with 'Download raw file'.")
        return 1
    print(f"Manifest version {version}: {source.name}")
    bad = False
    for name, ok in check_books(folder, manifest):
        print(f"  {name}: {'matches' if ok else 'DOES NOT MATCH - download it again'}")
        bad = bad or not ok
    if bad:
        return 1
    try:
        import cryptography  # noqa: F401
    except ImportError:
        print("The cryptography package is missing:  python3 -m pip install --user cryptography")
        return 1
    input("\nCopy your private key from your password manager now (all of it),\n"
          "then come back here and press Return. Do not paste it anywhere. ")
    try:
        text = read_clipboard()
    finally:
        clear_clipboard()
    try:
        line = sign(manifest, load_key(text))
    except ValueError as exc:
        print(f"\nNot signed: {exc}.")
        return 1
    finally:
        text = ""
    (folder / "MANIFEST.json").write_bytes(manifest)
    (folder / "MANIFEST.json.sig").write_text(line, encoding="ascii")
    print("\nSigned. The clipboard is empty. Upload these to docs/agent_rules/ on GitHub:")
    print("  MANIFEST.json, MANIFEST.json.sig" + "".join(
        f", {name}" for name, _ok in check_books(folder, manifest)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
