"""§504: signing the rule books on a borrowed computer with Python alone -
the same signature OpenSSL makes, accepted by the lock, from the trusted key
only."""
from __future__ import annotations

import base64
import importlib.util
import pathlib
import shutil

import pytest

from agents import rulebooks

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sign_rulebooks", ROOT / "tools" / "sign_rulebooks.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")


def _pem(key) -> str:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode("ascii")


def test_the_trusted_key_is_owner_pub():
    text = (ROOT / "docs" / "agent_rules" / "owner.pub").read_text()
    body = "".join(line for line in text.splitlines() if "-----" not in line)
    assert body == tool.OWNER_PUBLIC_KEY


def test_its_signature_is_accepted_by_the_lock(tmp_path, monkeypatch):
    key = ed25519.Ed25519PrivateKey.generate()
    trusted = tool.public_of(key)
    books = tmp_path / "books"
    shutil.copytree(ROOT / "docs" / "agent_rules", books)
    # The lock reads only the committed owner.pub, never a folder's own
    # (§353); this test hands it the stand-in key the way it reads that one.
    monkeypatch.setattr(rulebooks, "_public_key",
                        lambda: (key.public_key(), "owner.pub", ""))
    manifest = rulebooks.manifest_for(rulebooks.book_paths(books), 99)
    (books / "MANIFEST.json").write_bytes(manifest)
    (books / "MANIFEST.json.sig").write_text(
        tool.sign(manifest, tool.load_key(_pem(key)), trusted), encoding="ascii")
    monkeypatch.setattr(rulebooks, "rules_dir", lambda: books)
    rulebooks.reset()
    try:
        state = rulebooks.load()
        assert state.signed, state.reason
    finally:
        rulebooks.reset()


def test_the_key_may_be_the_pem_block_or_its_line_alone():
    key = ed25519.Ed25519PrivateKey.generate()
    pem = _pem(key)
    line = [x for x in pem.splitlines() if "-----" not in x][0]
    assert tool.public_of(tool.load_key(pem)) == tool.public_of(key)
    assert tool.public_of(tool.load_key("  " + line + "\n")) == tool.public_of(key)


def test_a_key_fam_does_not_trust_signs_nothing():
    other = ed25519.Ed25519PrivateKey.generate()
    with pytest.raises(ValueError, match="not the key FAM trusts"):
        tool.sign(b"{}", other)
    with pytest.raises(ValueError):
        tool.load_key("hello, this is not a key")


def test_a_book_that_does_not_match_the_manifest_is_caught(tmp_path):
    manifest = (ROOT / "docs" / "agent_rules" / "MANIFEST.json").read_bytes()
    shutil.copy(ROOT / "docs" / "agent_rules" / "nando.md", tmp_path / "nando.md")
    assert tool.check_books(tmp_path, manifest) == [("nando.md", True)]
    (tmp_path / "nando.md").write_text("changed")
    assert tool.check_books(tmp_path, manifest) == [("nando.md", False)]


def test_the_signature_is_one_base64_line():
    key = ed25519.Ed25519PrivateKey.generate()
    line = tool.sign(b"manifest", key, tool.public_of(key))
    assert "\n" not in line and len(base64.b64decode(line)) == 64
