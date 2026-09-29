"""Zero spend (§172): a staging deployment cannot cost money, whatever it holds.

The promise is two layers deep - paid credentials removed and paid switches
forced off, and nothing allowed to leave the machine - and each layer is tested
the way it would fail: a key that arrives anyway, a keyless source someone
switches on, a client library nobody listed.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import spend_guard  # noqa: E402

#: Documentation range (RFC 5737): never routed, so even a guard that failed
#: open could not reach anybody.
OUTSIDE = ("203.0.113.9", 443)


@pytest.fixture
def guard(monkeypatch):
    """The guard switched on for one test and fully undone after it."""
    monkeypatch.setenv("ZERO_SPEND", "1")
    try:
        yield spend_guard
    finally:
        spend_guard.reset()
        for name in list(spend_guard.FORCED):
            os.environ.pop(name, None)


def test_staging_is_always_zero_spend_and_cannot_be_talked_out_of_it(monkeypatch):
    monkeypatch.setenv("FAM_ENV", "staging")
    monkeypatch.setenv("ZERO_SPEND", "0")
    on, reason = spend_guard.wanted()
    assert on and "staging" in reason


def test_nothing_is_zero_spend_unless_asked(monkeypatch):
    monkeypatch.delenv("FAM_ENV", raising=False)
    monkeypatch.delenv("ZERO_SPEND", raising=False)
    assert spend_guard.wanted() == (False, "")
    monkeypatch.setenv("FAM_ENV", "production")
    assert spend_guard.wanted()[0] is False


def test_every_paid_credential_is_removed_and_every_paid_switch_forced(guard, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_API_KEYS", "sk-ant-a,sk-ant-b")
    monkeypatch.setenv("EXA_API_KEY", "exa")
    monkeypatch.setenv("GNEWS_KEY", "gnews")
    monkeypatch.setenv("GDELT", "1")
    monkeypatch.setenv("STORIES_POLYMARKET", "1")
    monkeypatch.setenv("LIVE_ELECTIONS_PROVIDER", "polymarket")
    monkeypatch.setenv("VOICE_BACKEND", "remote")
    assert guard.apply() is True
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEYS", "EXA_API_KEY", "GNEWS_KEY"):
        assert name not in os.environ
    assert os.environ["GDELT"] == "0"
    assert os.environ["STORIES_POLYMARKET"] == "0"
    assert os.environ["LIVE_ELECTIONS_PROVIDER"] == ""
    assert os.environ["VOICE_BACKEND"] == "chatterbox"
    report = guard.report()
    assert report["zero_spend"] is True
    # Names, never values.
    assert "ANTHROPIC_API_KEY" in report["credentials_removed"]
    assert "sk-ant" not in json.dumps(report)
    assert {"GDELT", "STORIES_POLYMARKET", "VOICE_BACKEND"} <= set(report["switches_overridden"])


def test_an_outbound_connection_is_refused_and_counted(guard):
    guard.apply()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(ConnectionRefusedError, match="zero spend"):
            sock.connect(OUTSIDE)
        assert sock.connect_ex(OUTSIDE) != 0
    finally:
        sock.close()
    assert guard.report()["blocked_connections"]["203.0.113.9:443"] == 2


def test_loopback_still_connects(guard):
    """The server has to be able to talk to itself - and a test client to it."""
    guard.apply()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect(server.getsockname())
    finally:
        client.close()
        server.close()


def test_the_asyncio_loop_is_covered(guard):
    """Every async client here (httpx, the Anthropic SDK) connects through the
    event loop, so the guard is only real if the loop's connect is covered."""
    guard.apply()

    async def reach():
        assert guard.check_loop(asyncio.get_running_loop()) is True
        await asyncio.open_connection(*OUTSIDE)

    with pytest.raises(OSError):
        asyncio.run(reach())
    assert "203.0.113.9:443" in guard.report()["blocked_connections"]


def test_httpx_is_refused_without_knowing_it_is_a_provider(guard):
    import httpx

    guard.apply()
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"http://{OUTSIDE[0]}:{OUTSIDE[1]}/", timeout=2)


def test_a_loop_the_guard_cannot_see_is_reported_not_trusted(guard):
    class Loop:  # stands in for uvloop.Loop, whose connects bypass the socket
        pass

    Loop.__module__ = "uvloop"
    guard.apply()
    assert guard.check_loop(Loop()) is False
    assert guard.report()["network_guard"] is False


def test_credentials_are_not_fetched_from_a_secrets_manager(guard, monkeypatch):
    import credentials

    monkeypatch.setenv("FAM_SECRETS", "cmd:echo ANTHROPIC_API_KEY=sk-ant-from-manager")
    guard.apply()
    assert credentials.load(force=True) == {}
    assert "ANTHROPIC_API_KEY" not in os.environ


# --- Derived: a new credential cannot slip past the list ------------------

_CREDENTIAL = re.compile(r'environ(?:\.get)?[\(\[]\s*"([A-Z0-9_]+(?:_KEY|_KEYS|_TOKEN|_SECRET))"')


def _credential_names_in_code() -> set:
    names = set()
    for path in list(ROOT.glob("*.py")) + list((ROOT / "voice_worker").glob("*.py")):
        names.update(_CREDENTIAL.findall(path.read_text(encoding="utf-8")))
    # `config.py` also reads keys through `_env`-style helpers by name.
    names.update(re.findall(r'"([A-Z0-9_]+_API_KEY)"',
                            (ROOT / "config.py").read_text(encoding="utf-8")))
    return names


def test_every_credential_the_code_reads_is_either_scrubbed_or_declared_free():
    """A guard whose subject is enumerated by hand is decorative - unless the
    enumeration is checked against the code. A key added next month for a new
    provider fails here until it is classified."""
    unclassified = (_credential_names_in_code()
                    - set(spend_guard.PAID_CREDENTIALS) - set(spend_guard.NOT_SPEND))
    assert not unclassified, (
        f"These credentials are read by the code and zero spend does not know "
        f"them: {sorted(unclassified)}. Add each to spend_guard.PAID_CREDENTIALS "
        "(it buys something) or NOT_SPEND (with the reason it does not).")


# --- The whole app, booted as staging --------------------------------------

_STAGING_BOOT = r"""
import json, os, sys
sys.path.insert(0, os.environ["FAM_ROOT"])
import tests.conftest  # the suite's hermetic environment
os.environ.update(json.loads(os.environ["STAGING_ENV"]))
from fastapi.testclient import TestClient
import app as appmod
from config import settings
out = {"demo": appmod.DEMO_MODE, "gdelt": settings.gdelt,
       "polymarket": settings.stories_polymarket,
       "elections": settings.live_elections_provider,
       "thumbnails": settings.thumbnails, "voice": settings.voice_backend,
       "key": settings.anthropic_api_key}
with TestClient(appmod.app) as client:
    health = client.get("/api/health").json()
    out["environment"] = health["environment"]
    out["mode"] = health["mode"]
    audio = client.get("/api/audio", params={"q": "how do tides work", "minutes": 1})
    out["audio_status"] = audio.status_code
    out["audio_bytes"] = len(audio.content)
print(json.dumps(out))
"""


def test_a_staging_boot_holds_no_key_calls_nothing_and_still_makes_an_episode(tmp_path):
    staging = {
        "FAM_ENV": "staging",
        "ZERO_SPEND": "0",  # cannot turn staging off
        "ANTHROPIC_API_KEY": "sk-ant-this-would-cost-money",
        "EXA_API_KEY": "exa-this-would-cost-money",
        "GDELT": "1", "STORIES_POLYMARKET": "1",
        "LIVE_ELECTIONS_PROVIDER": "polymarket", "THUMBNAILS": "1",
        "GEMINI_API_KEY": "gemini-this-would-cost-money",
        "VOICE_BACKEND": "remote", "RUNPOD_API_KEY": "runpod",
    }
    env = dict(os.environ, FAM_ROOT=str(ROOT), STAGING_ENV=json.dumps(staging),
               FAM_IGNORE_DOTENV="1")
    for name in __import__("release_contracts").data_variables():
        env[name] = str(tmp_path / f"{name.lower()}.db")
    result = subprocess.run([sys.executable, "-c", _STAGING_BOOT], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr[-3000:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["demo"] is True and out["mode"] == "demo"
    assert out["key"] == ""
    assert out["gdelt"] is False and out["polymarket"] is False
    assert out["elections"] == "" and out["thumbnails"] is False
    assert out["voice"] == "chatterbox"
    env_report = out["environment"]
    assert env_report["name"] == "staging" and env_report["zero_spend"] is True
    assert env_report["network_guard"] is True
    # An episode is still made end to end: the canned writer, the real pipeline.
    assert out["audio_status"] == 200 and out["audio_bytes"] > 44


def _service(name: str) -> str:
    """One service's block of render.yaml, as text (no YAML parser in CI)."""
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    blocks = re.split(r"\n  - type: web\n", text)
    for block in blocks[1:]:
        if re.search(rf"^    name: {re.escape(name)}$", block, re.M):
            return block
    raise AssertionError(f"render.yaml has no service {name!r}")


def test_the_staging_blueprint_is_zero_spend_and_shares_nothing_with_production():
    staging = _service("fam-staging")
    production = _service("fam")
    assert re.search(r"- key: FAM_ENV\n\s+value: staging", staging)
    assert re.search(r"- key: UVICORN_LOOP\n\s+value: asyncio", staging)
    assert re.search(r"^    branch: staging$", staging, re.M)
    assert re.search(r"^    branch: Main$", production, re.M)
    keys = set(re.findall(r"- key: ([A-Z0-9_]+)", staging))
    handed_money = keys & (set(spend_guard.PAID_CREDENTIALS) | set(spend_guard.FORCED)
                           | {"FAM_SECRETS"})
    assert not handed_money, f"fam-staging is given {sorted(handed_money)}"
    disk = re.search(r"disk:\n\s+name: (\S+)", staging).group(1)
    assert disk != re.search(r"disk:\n\s+name: (\S+)", production).group(1)


def test_loopback_in_every_spelling_is_local():
    for address in (("127.0.0.1", 80), ("::1", 80, 0, 0), ("::ffff:127.0.0.1", 80, 0, 0),
                    ("localhost", 80), "/tmp/fam.sock"):
        assert spend_guard._is_local(address), address
    for address in (("203.0.113.9", 443), ("::ffff:203.0.113.9", 443, 0, 0),
                    ("2001:db8::1", 443, 0, 0), ("api.anthropic.com", 443)):
        assert not spend_guard._is_local(address), address
