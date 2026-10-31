"""The lock on each agent's rule book (docs/FAM_AGENTS.md, Shared rules,
Locked rule books; PROBLEMS.md §348).

The owner (2026-10-29): each agent's rules are "untouchable and unchangeable
unless we deliberately say to have them change ... not by an agent and not
by a model". So a rule book runs only while it matches a manifest the owner
signed:

* `MANIFEST.json`, beside the rule books, lists every rule book with its
  SHA-256, and `MANIFEST.json.sig` is the owner's Ed25519 signature of that
  file's exact bytes, base64. The owner signs on their own machine with a key
  only they hold (`tools/rulebook_manifest.py` prints the commands); FAM
  holds only the public key - `docs/agent_rules/owner.pub`, which
  `AGENT_RULES_PUBLIC_KEY` defaults to and must match when it is set - so it
  can check a signature and never make one.
* The books are every `.md` in the rule-book folder but its README, and the
  journalism playbook, which Shakespeare's rule book makes part of his
  instructions (the owner, 2026-10-30).
* `load` runs at boot (and on first use); `check` runs before every agent
  call. An agent runs only while the signature verifies, the manifest
  covers every book in the folder, and its own book and `every-agent.md`
  (and, for the editor, the playbook) hash to what the manifest says - both
  as loaded and as they were on disk at the last background read, at most
  `DISK_REFRESH_SECONDS` old (§365: `check` itself reads no file, so an
  agent working while a listener waits adds no disk read in front of the
  first word). Anything else holds the agent and names the file. **No
  setting skips the check.**
* The texts are read once into frozen memory and nothing here writes a
  file. A changed file on disk is not reloaded: it holds the agent until the
  owner signs it and the server restarts.

Until the owner signs a first manifest every agent is held - FAM's state
when this was built.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import pathlib
import threading
import types
import time
from dataclasses import dataclass, field
from typing import Optional

from config import settings

log = logging.getLogger("fam.agents")

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "docs" / "agent_rules"
DEFAULT_PLAYBOOK = ROOT / "docs" / "journalism_playbook.md"

MANIFEST = "MANIFEST.json"
SIGNATURE = "MANIFEST.json.sig"
PUBLIC_KEY_FILE = "owner.pub"
#: What a session writes for the owner to sign: a name the lock never reads,
#: so the repository stays green while it waits (§352).
PENDING = "MANIFEST.pending.json"
#: The one key FAM trusts: the owner's public key as committed here, whatever
#: folder `AGENT_RULES_DIR` names. A folder that brings its own `owner.pub`
#: brings nothing; `AGENT_RULES_PUBLIC_KEY` may confirm this key, never
#: stand in for it (§353).
TRUSTED_KEY = DEFAULT_DIR / PUBLIC_KEY_FILE
EVERY_AGENT = "every-agent.md"
#: The playbook's name in the manifest, wherever the file lives.
PLAYBOOK = "journalism_playbook.md"
#: Rule books that are not one agent's but go with one.
EXTRA = {"shakespeare.md": (PLAYBOOK,)}
#: Files in the folder that are not rule books.
NOT_BOOKS = frozenset({"README.md"})

UNSIGNED = "no signed manifest yet: the owner has not signed the rule books"


@dataclass(frozen=True)
class Lock:
    """Whether one agent may run under its rule books, and which version."""

    ok: bool
    reason: str = ""
    #: The signed manifest's version, and the agent's own book's SHA-256.
    version: str = ""
    sha: str = ""
    #: The rule books' text, as the prompt's cached block carries it.
    text: str = ""
    #: The file that differs, when one does.
    file: str = ""


@dataclass(frozen=True)
class State:
    """What `load` found: one snapshot, never changed after it is made."""

    signed: bool
    reason: str
    version: str = ""
    key_source: str = ""
    #: name -> the bytes' SHA-256 as loaded.
    hashes: types.MappingProxyType = field(
        default_factory=lambda: types.MappingProxyType({}))
    #: name -> text, frozen.
    texts: types.MappingProxyType = field(
        default_factory=lambda: types.MappingProxyType({}))
    #: name -> the SHA-256 the signed manifest gives.
    signed_hashes: types.MappingProxyType = field(
        default_factory=lambda: types.MappingProxyType({}))
    #: Books in the folder the manifest does not cover.
    uncovered: tuple = ()
    #: name -> where it was read from.
    paths: types.MappingProxyType = field(
        default_factory=lambda: types.MappingProxyType({}))


def rules_dir() -> pathlib.Path:
    configured = str(getattr(settings, "agent_rules_dir", "") or "").strip()
    return pathlib.Path(configured).expanduser() if configured else DEFAULT_DIR


def playbook_path() -> pathlib.Path:
    configured = str(getattr(settings, "editor_playbook", "") or "").strip()
    return pathlib.Path(configured).expanduser() if configured else DEFAULT_PLAYBOOK


def book_paths(folder: Optional[pathlib.Path] = None) -> dict:
    """Every rule book by its manifest name: the folder's `.md` files but its
    README, and the playbook."""
    folder = folder or rules_dir()
    books = {p.name: p for p in sorted(folder.glob("*.md")) if p.name not in NOT_BOOKS}
    books[PLAYBOOK] = playbook_path()
    return books


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_for(paths: dict, version: int) -> bytes:
    """The manifest the owner signs: its exact bytes, so `--write` and the
    check agree on them."""
    body = {"format": 1, "version": int(version),
            "books": {name: sha256(path.read_bytes()) for name, path in sorted(paths.items())}}
    return (json.dumps(body, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _public_key():
    """`(key, source, problem)`. The committed file is the key, read from
    `TRUSTED_KEY` and never from the configurable folder; the setting, when
    set, must be the same key and is never a key on its own."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    def parse(text: str):
        text = text.strip()
        if "BEGIN PUBLIC KEY" in text:
            key = serialization.load_pem_public_key(text.encode("ascii"))
        else:
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(text))
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("not an Ed25519 key")
        return key

    def raw(key) -> bytes:
        return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    path = TRUSTED_KEY
    configured = str(getattr(settings, "agent_rules_public_key", "") or "").strip()
    file_key = None
    if path.exists():
        try:
            file_key = parse(path.read_text(encoding="ascii"))
        except Exception as exc:  # noqa: BLE001
            return None, "", f"{PUBLIC_KEY_FILE} is not an Ed25519 public key: {exc}"
    if file_key is None:
        return None, "", (f"no public key: {PUBLIC_KEY_FILE} is not committed in "
                          "docs/agent_rules (AGENT_RULES_PUBLIC_KEY cannot replace it)")
    if configured:
        try:
            set_key = parse(configured)
        except Exception as exc:  # noqa: BLE001
            return None, "", f"AGENT_RULES_PUBLIC_KEY is not an Ed25519 public key: {exc}"
        if raw(file_key) != raw(set_key):
            return None, "", (f"AGENT_RULES_PUBLIC_KEY is not the key in {PUBLIC_KEY_FILE}: "
                              "the two must be the same")
        return file_key, "AGENT_RULES_PUBLIC_KEY and " + PUBLIC_KEY_FILE, ""
    return file_key, PUBLIC_KEY_FILE, ""


def _load() -> State:
    folder = rules_dir()
    paths = book_paths(folder)
    texts, hashes = {}, {}
    for name, path in paths.items():
        try:
            data = path.read_bytes()
        except OSError:
            continue
        hashes[name] = sha256(data)
        texts[name] = data.decode("utf-8", errors="replace")
    frozen = dict(hashes=types.MappingProxyType(hashes),
                  texts=types.MappingProxyType(texts),
                  paths=types.MappingProxyType({n: str(p) for n, p in paths.items()}))

    manifest_path, sig_path = folder / MANIFEST, folder / SIGNATURE
    if not manifest_path.exists() or not sig_path.exists():
        return State(signed=False, reason=UNSIGNED, **frozen)
    key, source, problem = _public_key()
    if key is None:
        return State(signed=False, reason=problem, **frozen)
    manifest_bytes = manifest_path.read_bytes()
    try:
        from cryptography.exceptions import InvalidSignature

        signature = base64.b64decode(sig_path.read_text(encoding="ascii").strip())
        key.verify(signature, manifest_bytes)
    except InvalidSignature:
        return State(signed=False, key_source=source,
                     reason=f"{SIGNATURE} does not verify against the owner's key "
                            f"({source}): the manifest is not the one the owner signed",
                     **frozen)
    except Exception as exc:  # noqa: BLE001
        return State(signed=False, key_source=source,
                     reason=f"{SIGNATURE} could not be read: {exc}", **frozen)
    try:
        body = json.loads(manifest_bytes)
        signed = {str(k): str(v) for k, v in dict(body["books"]).items()}
        version = str(body.get("version", ""))
    except Exception as exc:  # noqa: BLE001
        return State(signed=False, key_source=source,
                     reason=f"{MANIFEST} is signed but unreadable: {exc}", **frozen)
    uncovered = tuple(sorted(n for n in paths if n not in signed))
    return State(signed=True, reason="", version=version, key_source=source,
                 signed_hashes=types.MappingProxyType(signed), uncovered=uncovered,
                 **frozen)


_STATE: list = [None]
_LOCK = threading.Lock()

#: **The on-disk check, kept off the request path** (§353's carry-over,
#: §365). Every book's hash as it is on disk now, re-read in a background
#: thread at most this often, never by `check` itself: an agent working
#: while a listener waits must not read a file in front of the first word.
#: A book changed on disk after boot is still caught - within this many
#: seconds rather than at once - and holds its agent until it is signed and
#: the server restarts, exactly as before.
DISK_REFRESH_SECONDS = 30.0
#: name -> the SHA-256 on disk at the last refresh, or None when it could
#: not be read; when that was; whether a refresh is running.
_DISK: dict = {"hashes": {}, "at": 0.0, "running": False}
_DISK_LOCK = threading.Lock()


def refresh_disk() -> dict:
    """Re-read every loaded book's bytes and keep their hashes. Off the
    request path: the background thread `check` starts, boot, and tests.
    Never raises; returns the new snapshot."""
    state = _STATE[0]
    hashes: dict = {}
    for name, path in (state.paths.items() if state is not None else ()):
        try:
            hashes[name] = sha256(pathlib.Path(path).read_bytes())
        except OSError:
            hashes[name] = None
    with _DISK_LOCK:
        _DISK["hashes"], _DISK["at"], _DISK["running"] = hashes, time.monotonic(), False
    return dict(hashes)


def _refresh_soon() -> None:
    """Start one background refresh when the snapshot is stale. Never
    waits and never raises."""
    with _DISK_LOCK:
        if _DISK["running"] or time.monotonic() - _DISK["at"] < DISK_REFRESH_SECONDS:
            return
        _DISK["running"] = True
    try:
        threading.Thread(target=refresh_disk, name="rulebooks-disk", daemon=True).start()
    except Exception:  # noqa: BLE001 - the last snapshot stands
        with _DISK_LOCK:
            _DISK["running"] = False


def load() -> State:
    """Read and verify once (boot, or first use). Never raises."""
    with _LOCK:
        if _STATE[0] is None:
            try:
                _STATE[0] = _load()
            except Exception as exc:  # noqa: BLE001 - a broken lock holds, never opens
                log.exception("rule-book lock could not load")
                _STATE[0] = State(signed=False, reason=f"the lock could not load: {exc}")
            state = _STATE[0]
            if not state.signed:
                log.warning("agents held: %s", state.reason)
            # What was just read is what is on disk now.
            with _DISK_LOCK:
                _DISK["hashes"] = dict(state.hashes)
                _DISK["at"], _DISK["running"] = time.monotonic(), False
        return _STATE[0]


def reset() -> None:
    """Tests: forget the snapshot, so the next call loads again."""
    with _LOCK:
        _STATE[0] = None
    with _DISK_LOCK:
        _DISK.update(hashes={}, at=0.0, running=False)


def books_for(book: str) -> tuple:
    return (EVERY_AGENT, book) + EXTRA.get(book, ())


def check(book: str) -> Lock:
    """The lock for an agent whose rule book is `book`. Never raises."""
    try:
        state = load()
        _refresh_soon()
        if not state.signed:
            return Lock(ok=False, reason=state.reason)
        if state.uncovered:
            return Lock(ok=False, version=state.version, file=state.uncovered[0],
                        reason=(f"{', '.join(state.uncovered)} not in the signed "
                                "manifest: every rule book must be signed"))
        parts = []
        for name in books_for(book):
            loaded = state.hashes.get(name)
            if loaded is None:
                return Lock(ok=False, version=state.version, file=name,
                            reason=f"{name} is missing")
            if state.signed_hashes.get(name) != loaded:
                return Lock(ok=False, version=state.version, file=name,
                            reason=f"{name} differs from the signed manifest")
            text = state.texts[name]
            if sha256(text.encode("utf-8")) != loaded:
                return Lock(ok=False, version=state.version, file=name,
                            reason=f"{name} changed in memory")
            # The last background read, never a read here (§365).
            on_disk = _DISK["hashes"].get(name, loaded)
            if on_disk is None:
                return Lock(ok=False, version=state.version, file=name,
                            reason=f"{name} can no longer be read")
            if on_disk != loaded:
                return Lock(ok=False, version=state.version, file=name,
                            reason=f"{name} changed on disk since boot")
            parts.append(f"<rule_book name=\"{name}\">\n{text.strip()}\n</rule_book>")
        return Lock(ok=True, version=state.version, sha=state.hashes[book],
                    text="\n\n".join(parts))
    except Exception as exc:  # noqa: BLE001
        log.exception("rule-book check failed for %r", book)
        return Lock(ok=False, reason=f"the lock check failed: {type(exc).__name__}")


def report() -> dict:
    """What `/api/health` and `/admin` say about the lock. Never raises."""
    try:
        from agents import names

        state = load()
        held = {}
        for role, (_n, book, _s, _m) in names.AGENTS.items():
            lock = check(book)
            if not lock.ok:
                held[role] = {"reason": lock.reason, "file": lock.file}
        return {"built": True, "signed": state.signed, "version": state.version,
                "key": state.key_source, "folder": str(rules_dir()),
                "books": sorted(state.hashes), "uncovered": list(state.uncovered),
                "reason": state.reason, "held": held}
    except Exception as exc:  # noqa: BLE001
        return {"built": True, "signed": False, "reason": f"report failed: {exc}",
                "held": "every agent"}
