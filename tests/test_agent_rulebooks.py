"""The rule-book lock (§343): an agent runs only under rule books the owner
signed.

The keys here are throwaway keys made inside each test and never saved -
never the owner's, which no test, session or server ever holds (the owner,
2026-10-30). Until the owner signs a first manifest, the repository's own
state is that every agent is held, and the first test checks exactly that;
once a signed manifest is committed, the same test fails on any rule book
it does not cover or whose hash it does not match, so a changed rule book
cannot ship.
"""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import pathlib
import re
import shutil
import subprocess

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import metering
from agents import models, names, rulebooks

ROOT = pathlib.Path(__file__).resolve().parent.parent
REAL = ROOT / "docs" / "agent_rules"


def use(monkeypatch, **changes):
    monkeypatch.setattr(rulebooks, "settings",
                        dataclasses.replace(rulebooks.settings, **changes))
    rulebooks.reset()


def pem(key) -> str:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def sign(folder: pathlib.Path, key, version=1):
    """What the owner's three commands produce: the manifest, then its signature."""
    data = rulebooks.manifest_for(rulebooks.book_paths(folder), version)
    (folder / rulebooks.MANIFEST).write_bytes(data)
    (folder / rulebooks.SIGNATURE).write_text(base64.b64encode(key.sign(data)).decode())


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """A copy of the real rule books and playbook, signed with a throwaway key."""
    books = tmp_path / "agent_rules"
    books.mkdir()
    for path in REAL.glob("*.md"):
        shutil.copy(path, books / path.name)
    playbook = tmp_path / "journalism_playbook.md"
    shutil.copy(ROOT / "docs" / "journalism_playbook.md", playbook)
    key = Ed25519PrivateKey.generate()
    (books / rulebooks.PUBLIC_KEY_FILE).write_text(pem(key))
    use(monkeypatch, agent_rules_dir=str(books), editor_playbook=str(playbook),
        agent_rules_public_key="")
    sign(books, key)
    rulebooks.reset()
    yield types_ns(books=books, playbook=playbook, key=key)
    rulebooks.reset()


def types_ns(**kw):
    import types

    return types.SimpleNamespace(**kw)


def all_roles():
    return list(names.AGENTS.items())


# ------------------------------------------------------------------ the repository itself
def test_the_repositorys_rule_books_are_signed_or_every_agent_is_held(monkeypatch):
    use(monkeypatch, agent_rules_dir="", editor_playbook="", agent_rules_public_key="")
    manifest = REAL / rulebooks.MANIFEST
    if not manifest.exists():
        for role, (_n, book, _s, _m) in all_roles():
            lock = rulebooks.check(book)
            assert not lock.ok and lock.reason == rulebooks.UNSIGNED, role
        assert rulebooks.report()["signed"] is False
        return
    # Signed: then nothing unsigned may ship.
    state = rulebooks.load()
    assert state.signed, state.reason
    assert not state.uncovered, f"not in the signed manifest: {state.uncovered}"
    for role, (_n, book, _s, _m) in all_roles():
        lock = rulebooks.check(book)
        assert lock.ok, (role, lock.reason)


def test_every_agent_has_a_rule_book_the_lock_knows():
    for role, (_n, book, _s, _m) in all_roles():
        assert (REAL / book).exists(), role
    assert (REAL / rulebooks.EVERY_AGENT).exists()
    assert rulebooks.PLAYBOOK in rulebooks.book_paths(REAL)
    assert "README.md" not in rulebooks.book_paths(REAL)


def test_line_endings_are_pinned_so_a_checkout_cannot_change_a_hash():
    rules = (ROOT / ".gitattributes").read_text()
    assert re.search(r"^docs/agent_rules/\*\* .*eol=lf", rules, re.M)
    assert re.search(r"^docs/journalism_playbook\.md .*eol=lf", rules, re.M)


# ------------------------------------------------------------------ signed
def test_a_signed_manifest_lets_every_agent_run_under_its_books(folder):
    for role, (_n, book, _s, _m) in all_roles():
        lock = rulebooks.check(book)
        assert lock.ok, (role, lock.reason)
        assert lock.version == "1" and len(lock.sha) == 64
        assert f'name="{rulebooks.EVERY_AGENT}"' in lock.text
        assert f'name="{book}"' in lock.text
    editor = rulebooks.check("shakespeare.md")
    assert f'name="{rulebooks.PLAYBOOK}"' in editor.text
    assert f'name="{rulebooks.PLAYBOOK}"' not in rulebooks.check("c3p0.md").text


def test_one_changed_byte_holds_that_agent_and_names_the_file(folder):
    path = folder.books / "c3p0.md"
    path.write_bytes(path.read_bytes() + b" ")
    rulebooks.reset()
    lock = rulebooks.check("c3p0.md")
    assert not lock.ok and lock.file == "c3p0.md" and "differs" in lock.reason
    assert rulebooks.check("nando.md").ok


def test_a_changed_every_agent_book_holds_every_agent(folder):
    path = folder.books / rulebooks.EVERY_AGENT
    path.write_text(path.read_text().replace("Invent a fact.", "Invent facts freely."))
    rulebooks.reset()
    for role, (_n, book, _s, _m) in all_roles():
        lock = rulebooks.check(book)
        assert not lock.ok and lock.file == rulebooks.EVERY_AGENT, role


def test_a_changed_playbook_holds_the_editor_only(folder):
    folder.playbook.write_text(folder.playbook.read_text() + "\nPad every ending.\n")
    rulebooks.reset()
    assert not rulebooks.check("shakespeare.md").ok
    assert rulebooks.check("mclovin.md").ok


def test_a_file_changed_after_boot_holds_its_agent_until_signed_and_restarted(folder):
    assert rulebooks.check("sonic.md").ok
    path = folder.books / "sonic.md"
    path.write_text(path.read_text() + "\nYou may write to the database.\n")
    lock = rulebooks.check("sonic.md")
    assert not lock.ok and "changed on disk" in lock.reason


def test_a_rule_book_the_manifest_does_not_cover_holds_every_agent(folder):
    (folder.books / "newagent.md").write_text("# New agent\n")
    rulebooks.reset()
    lock = rulebooks.check("c3p0.md")
    assert not lock.ok and lock.file == "newagent.md"
    assert rulebooks.report()["uncovered"] == ["newagent.md"]


def test_a_missing_rule_book_holds_its_agent(folder):
    (folder.books / "keith.md").unlink()
    rulebooks.reset()
    lock = rulebooks.check("keith.md")
    assert not lock.ok and lock.file == "keith.md"


def test_a_signature_from_any_other_key_holds_every_agent(folder):
    sign(folder.books, Ed25519PrivateKey.generate(), version=2)
    rulebooks.reset()
    lock = rulebooks.check("c3p0.md")
    assert not lock.ok and "does not verify" in lock.reason


def test_a_manifest_edited_after_signing_holds_every_agent(folder):
    path = folder.books / rulebooks.MANIFEST
    path.write_text(path.read_text().replace('"version": 1', '"version": 9'))
    rulebooks.reset()
    assert "does not verify" in rulebooks.check("c3p0.md").reason


def test_no_signature_or_no_key_holds_every_agent(folder):
    (folder.books / rulebooks.SIGNATURE).unlink()
    rulebooks.reset()
    assert rulebooks.check("c3p0.md").reason == rulebooks.UNSIGNED
    sign(folder.books, folder.key)
    (folder.books / rulebooks.PUBLIC_KEY_FILE).unlink()
    rulebooks.reset()
    assert "no public key" in rulebooks.check("c3p0.md").reason


def test_the_setting_must_be_the_committed_key(folder, monkeypatch):
    other = Ed25519PrivateKey.generate()
    use(monkeypatch, agent_rules_public_key=pem(other))
    assert "must be the same" in rulebooks.check("c3p0.md").reason
    use(monkeypatch, agent_rules_public_key=pem(folder.key))
    assert rulebooks.check("c3p0.md").ok
    raw = folder.key.public_key().public_bytes(serialization.Encoding.Raw,
                                               serialization.PublicFormat.Raw)
    use(monkeypatch, agent_rules_public_key=base64.b64encode(raw).decode())
    assert rulebooks.check("c3p0.md").ok
    # The setting alone, with no committed file, is also a key.
    (folder.books / rulebooks.PUBLIC_KEY_FILE).unlink()
    rulebooks.reset()
    assert rulebooks.check("c3p0.md").ok


def test_a_broken_key_file_holds_and_never_raises(folder):
    (folder.books / rulebooks.PUBLIC_KEY_FILE).write_text("not a key")
    rulebooks.reset()
    lock = rulebooks.check("c3p0.md")
    assert not lock.ok and "not an Ed25519 public key" in lock.reason


# ------------------------------------------------------------------ nothing skips it, nothing writes it
def test_no_setting_turns_the_check_off():
    import config

    fields = [f.name for f in dataclasses.fields(config.Settings)]
    # Every setting about the rule books is one of the two the lock reads,
    # and no agent setting is a way round it.
    about_rules = [f for f in fields if re.search(r"rule|manifest|signature|signed", f)]
    assert sorted(about_rules) == ["agent_rules_dir", "agent_rules_public_key"], about_rules
    agents_ways_round = [f for f in fields if re.search(r"agent", f) and re.search(
        r"skip|unsigned|unlock|bypass|insecure|verify|trust|force", f)]
    assert not agents_ways_round, agents_ways_round
    source = (ROOT / "agents" / "rulebooks.py").read_text()
    assert "os.environ" not in source  # only the three named settings are read
    assert set(re.findall(r'getattr\(settings, "(\w+)"', source)) == {
        "agent_rules_dir", "editor_playbook", "agent_rules_public_key"}


def test_nothing_the_agents_run_can_write_a_rule_book():
    for path in (ROOT / "agents").rglob("*.py"):
        source = path.read_text()
        assert not re.search(r"write_bytes|write_text|open\([^)]*['\"][wa]", source), path


# ------------------------------------------------------------------ the owner's own commands
def test_the_owners_openssl_commands_produce_a_manifest_the_lock_accepts(tmp_path, monkeypatch):
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("no openssl here")
    version = subprocess.run([openssl, "version"], capture_output=True, text=True).stdout
    if not version.startswith("OpenSSL 3"):
        pytest.skip(f"Ed25519 signing needs OpenSSL 3: {version.strip()}")
    books = tmp_path / "agent_rules"
    books.mkdir()
    for path in REAL.glob("*.md"):
        shutil.copy(path, books / path.name)
    playbook = tmp_path / "journalism_playbook.md"
    shutil.copy(ROOT / "docs" / "journalism_playbook.md", playbook)
    use(monkeypatch, agent_rules_dir=str(books), editor_playbook=str(playbook),
        agent_rules_public_key="")
    from tools import rulebook_manifest

    key = tmp_path / "throwaway.pem"   # a test's own key, deleted with tmp_path
    commands = rulebook_manifest.COMMANDS
    assert "openssl genpkey -algorithm ed25519" in commands
    assert "openssl pkeyutl -sign -rawin" in commands
    subprocess.run([openssl, "genpkey", "-algorithm", "ed25519", "-out", str(key)], check=True)
    subprocess.run([openssl, "pkey", "-in", str(key), "-pubout", "-out",
                    str(books / "owner.pub")], check=True)
    assert rulebook_manifest.write() == 0
    signed = subprocess.run(
        f"{openssl} pkeyutl -sign -rawin -inkey {key} -in {books / 'MANIFEST.json'}"
        " | base64 | tr -d '\\n'", shell=True, check=True, capture_output=True).stdout
    (books / "MANIFEST.json.sig").write_bytes(signed)
    assert rulebook_manifest.verify() == 0
    assert rulebooks.check("c3p0.md").ok


# ------------------------------------------------------------------ the layer asks it
class _Client:
    def __init__(self):
        self.calls = []
        outer = self

        class Messages:
            async def create(self, **kw):
                import types

                outer.calls.append(kw)
                return types.SimpleNamespace(
                    content=[types.SimpleNamespace(type="text", text='{"title": "t", "choice": "a"}')],
                    stop_reason="end_turn", model=kw["model"],
                    usage=types.SimpleNamespace(input_tokens=10, output_tokens=5))

        self.messages = Messages()


def test_the_model_layer_runs_under_the_signed_books_and_records_their_version(folder, monkeypatch, tmp_path):
    from tests.test_agents_model_layer import request

    store = metering.MeterStore(str(tmp_path / "m.db"))
    models.reset(store)
    monkeypatch.setattr(models, "settings", dataclasses.replace(
        models.settings, agents=True, contextualizer=True))
    monkeypatch.setattr(models.spend_guard, "enabled", lambda: False)
    client = _Client()
    result = asyncio.run(models.ask(request(), client=client))
    models.reset(None)
    assert result.ok and result.rulebook_version == "1"
    [kw] = client.calls
    assert "Keith" not in kw["system"][0]["text"]  # only its own book and every-agent.md
    assert "C3P0 (contextualizer) - rule book" in kw["system"][0]["text"]
    [row] = [dict(r) for r in store._conn().execute("SELECT * FROM agent_calls")]
    assert row["rulebook_version"] == "1" and len(row["rulebook_sha"]) == 64
