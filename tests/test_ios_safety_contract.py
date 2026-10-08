"""The iOS safety screens (`ios/FAMSafety`) ask for what the server has.

The Swift was written without a compiler to hand (PROBLEMS.md §223), and a
compiler would not have caught this anyway: a path that is not a route, or a
field the server does not send, decodes as a failure on a phone in review.
So every `/api/v1/...` path in the Swift must route, and every property a
`Codable` type there decodes must be a key in the server's real answer.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as appmod  # noqa: E402

SWIFT = ROOT / "ios" / "FAMSafety" / "Sources" / "FAMSafety"


def _swift() -> str:
    return "\n".join(p.read_text() for p in sorted(SWIFT.rglob("*.swift")))


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", name).lower()


def _fields(struct: str) -> set[str]:
    """The stored properties a public `Codable` struct decodes."""
    body = re.search(r"public struct %s:[^{]*\{(.*?)\n\}" % struct, _swift(), re.S)
    assert body, f"no struct {struct} in ios/FAMSafety"
    return {_snake(m) for m in re.findall(r"public let (\w+):", body.group(1))}


def test_every_path_the_app_asks_for_routes():
    paths = set(re.findall(r'"(/api/v1/[a-z/\-]+)', _swift()))
    assert paths, "found no /api/v1 paths in the Swift"
    routes = {getattr(r, "path", "") for r in appmod.app.routes}
    missing = sorted(p for p in paths if p.replace("/api/v1/", "/api/") not in routes)
    assert not missing, missing


def _client(name: str, handle: str) -> TestClient:
    c = TestClient(appmod.app)
    c.post("/api/auth/signup", json={"email": f"{handle}@example.com", "password": "password12"})
    c.post("/api/me", json={"name": name, "handle": handle})
    return c


def test_every_field_the_app_decodes_is_sent(monkeypatch):
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    ann, ben = _client("Ann", "ann"), _client("Ben", "ben")

    ai = ann.get("/api/v1/consent").json()["ai"]
    assert _fields("AIConsent") <= set(ai), _fields("AIConsent") - set(ai)
    assert _fields("ConsentNotice") <= set(ai["notice"])

    options = ann.get("/api/v1/report").json()
    assert _fields("ReportOptions") <= set(options), _fields("ReportOptions") - set(options)
    assert _fields("ReportReason") <= set(options["reasons"][0])

    receipt = ann.post("/api/v1/report", json={"kind": "person", "target": "ben",
                                               "reason": "spam"}).json()
    assert _fields("ReportReceipt") <= set(receipt), _fields("ReportReceipt") - set(receipt)

    ann.post("/api/v1/block", json={"handle": "ben"})
    people = ann.get("/api/v1/blocks").json()["people"]
    assert people and _fields("BlockedPerson") <= set(people[0])


def test_the_reasons_the_app_sends_are_the_servers():
    """`ReportSubject.kind` names only kinds `moderation.KINDS` accepts."""
    import moderation
    kinds = set(re.findall(r'case \.\w+: return "(\w+)"', _swift()))
    assert kinds == set(moderation.KINDS), kinds ^ set(moderation.KINDS)


def test_the_consent_header_is_the_one_the_server_sends():
    assert 'consentHeader = "X-FAM-Consent"' in _swift()
    assert "X-FAM-Consent" in Path(ROOT / "app.py").read_text()
