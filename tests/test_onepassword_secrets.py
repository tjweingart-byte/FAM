"""`tools/onepassword_secrets.py`: FAM's keys from 1Password, the company vault.

The SDK is stubbed - there is no network or 1Password account here - so these
pin everything around the sign-in: which fields become variables (labelled
like a variable, a text or password field, never a one-time password), that
two fields with one label are refused, that a failure is one sentence with no
value in it, and that the output goes through `credentials` like any other
provider's.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import credentials  # noqa: E402

# Loaded by path: putting tools/ on sys.path would let its scripts shadow
# the app's modules of the same name (tools/trending_bank.py did).
_spec = importlib.util.spec_from_file_location(
    "onepassword_secrets", os.path.join(ROOT, "tools", "onepassword_secrets.py"))
ops = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ops)


def _field(title, value, kind="Concealed"):
    return NS(title=title, value=value, field_type=NS(value=kind))


class FakeClient:
    """The three calls the tool makes, over a dict of vaults -> items."""

    def __init__(self, vaults, fail=None):
        self._vaults, self._fail = vaults, fail
        self.vaults = NS(list=self._list_vaults)
        self.items = NS(list=self._list_items, get=self._get_item)

    async def _list_vaults(self):
        if self._fail:
            raise RuntimeError(self._fail)
        return [NS(id=f"v{i}", title=t) for i, t in enumerate(self._vaults)]

    def _items(self, vault_id):
        return self._vaults[list(self._vaults)[int(vault_id[1:])]]

    async def _list_items(self, vault_id):
        return [NS(id=str(i)) for i, _ in enumerate(self._items(vault_id))]

    async def _get_item(self, vault_id, item_id):
        return self._items(vault_id)[int(item_id)]


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "ops_test")
    monkeypatch.delenv("OP_VAULT", raising=False)


def _run(client):
    return asyncio.run(ops.fetch(client))


def test_labelled_fields_become_variables_and_nothing_else(token):
    vault = {"FAM Production": [
        NS(fields=[_field("ANTHROPIC_API_KEY", "sk-ant-1"),
                   _field("GNEWS_KEY", "g-2", kind="Text")]),
        # A vendor's login item: its own fields are lower case, its key is
        # labelled, and its two-step seed is never read.
        NS(fields=[_field("username", "ops@familiarize.net", kind="Text"),
                   _field("password", "hunter2"),
                   _field("TIINGO_API_KEY", "t-3"),
                   _field("TIINGO_TOTP", "JBSWY3DPEHPK3PXP", kind="Totp")]),
    ]}
    assert _run(FakeClient(vault)) == {
        "ANTHROPIC_API_KEY": "sk-ant-1", "GNEWS_KEY": "g-2", "TIINGO_API_KEY": "t-3"}


def test_only_the_named_vault_is_read(token, monkeypatch):
    vaults = {"People": [NS(fields=[_field("ANTHROPIC_API_KEY", "personal")])],
              "FAM Production": [NS(fields=[_field("ANTHROPIC_API_KEY", "server")])],
              "Elsewhere": [NS(fields=[_field("GNEWS_KEY", "x")])]}
    assert _run(FakeClient(vaults)) == {"ANTHROPIC_API_KEY": "server"}
    monkeypatch.setenv("OP_VAULT", "Elsewhere")
    assert _run(FakeClient(vaults)) == {"GNEWS_KEY": "x"}


def test_two_fields_with_one_label_are_refused(token):
    vault = {"FAM Production": [NS(fields=[_field("GNEWS_KEY", "a")]),
                                NS(fields=[_field("GNEWS_KEY", "b")])]}
    with pytest.raises(ops.Refused, match="GNEWS_KEY"):
        _run(FakeClient(vault))


def test_a_missing_vault_or_token_is_one_sentence(token, monkeypatch):
    with pytest.raises(ops.Refused, match="cannot see a vault"):
        _run(FakeClient({"Other": []}))
    monkeypatch.delenv("OP_SERVICE_ACCOUNT_TOKEN")
    with pytest.raises(ops.Refused, match="OP_SERVICE_ACCOUNT_TOKEN"):
        _run(FakeClient({}))


def test_a_provider_failure_carries_no_value(token):
    with pytest.raises(ops.Refused) as caught:
        _run(FakeClient({}, fail="invalid service account token"))
    assert "invalid service account token" in str(caught.value)
    assert "ops_test" not in str(caught.value)


def test_main_prints_json_and_fails_on_stderr(token, monkeypatch, capsys):
    vault = {"FAM Production": [NS(fields=[_field("GNEWS_KEY", 'a"b\nc')])]}

    async def fake_client(_token):
        return FakeClient(vault)

    monkeypatch.setattr(ops, "_client", fake_client)
    assert ops.main() == 0
    out = capsys.readouterr().out
    # JSON keeps a quote and a newline inside a value intact, which dotenv
    # lines would not; and credentials reads it as it reads any provider.
    assert credentials.parse_payload(out) == {"GNEWS_KEY": 'a"b\nc'}
    monkeypatch.delenv("OP_SERVICE_ACCOUNT_TOKEN")
    assert ops.main() == 1
    err = capsys.readouterr()
    assert err.out == "" and err.err.startswith("onepassword_secrets: ")


def test_as_a_fam_secrets_command_with_no_token_it_fails_loudly():
    """Run as FAM_SECRETS runs it: a separate process, exit status and
    stderr. No token means exit 1 and a sentence - never an empty success
    that would leave the app quietly keyless."""
    env = {k: v for k, v in os.environ.items() if k != "OP_SERVICE_ACCOUNT_TOKEN"}
    done = subprocess.run([sys.executable, "tools/onepassword_secrets.py"],
                          cwd=ROOT, env=env, capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 1 and done.stdout == ""
    assert "OP_SERVICE_ACCOUNT_TOKEN is not set" in done.stderr


def test_the_image_installs_the_sdk_and_the_recipe_is_documented():
    dockerfile = open(os.path.join(ROOT, "Dockerfile")).read()
    assert "-r requirements-1password.txt" in dockerfile
    pinned = open(os.path.join(ROOT, "requirements-1password.txt")).read()
    assert "onepassword-sdk==" in pinned
    for doc in ("CREDENTIALS.md", ".env.example"):
        assert "cmd:python tools/onepassword_secrets.py" in open(
            os.path.join(ROOT, doc)).read(), doc
