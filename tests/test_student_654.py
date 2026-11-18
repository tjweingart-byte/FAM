"""§654: the Student plan is for a verified .edu address.

The owner, 11.18: "make students verify with a .edu email". A six-digit
code goes to the school address; entered, the account may buy Student for
a year. One address verifies one account at a time.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import accounts as accounts_mod  # noqa: E402
import billing  # noqa: E402


@pytest.mark.parametrize("email,ok", [
    ("me@mit.edu", True), ("Me@CS.Stanford.EDU", True),
    ("me@school.edu.au", False), ("me@gmail.com", False), ("me@edu", False),
    ("@mit.edu", False), ("a@b@mit.edu", False), ("me@.edu", False),
])
def test_only_a_dot_edu_address(email, ok):
    assert billing.is_edu(email) is ok


@pytest.fixture
def store(tmp_path):
    return billing.BillingStore(str(tmp_path / "billing.db"))


def test_the_right_code_verifies_for_a_year(store):
    code = store.start_student("u1", "me@mit.edu")
    assert not store.student_verified("u1")
    assert store.confirm_student("u1", code)["verified"]
    assert store.student_verified("u1")
    assert not store.student_verified("u1", time.time() + 366 * 86400)


def test_wrong_codes_run_out_and_old_codes_expire(store):
    code = store.start_student("u1", "me@mit.edu")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(billing.STUDENT_CODE_TRIES):
        with pytest.raises(billing.BillingError):
            store.confirm_student("u1", wrong)
    with pytest.raises(billing.BillingError):
        store.confirm_student("u1", code)
    code = store.start_student("u1", "me@mit.edu")
    with pytest.raises(billing.BillingError):
        store.confirm_student("u1", code, now=time.time() + 3600)


def test_one_address_verifies_one_account(store):
    store.confirm_student("u1", store.start_student("u1", "me@mit.edu"))
    with pytest.raises(billing.BillingError):
        store.start_student("u2", "me@mit.edu")


def test_codes_are_rationed(store):
    for _ in range(billing.STUDENT_CODES_PER_HOUR):
        store.start_student("u1", "me@mit.edu")
    with pytest.raises(billing.BillingError):
        store.start_student("u1", "me@mit.edu")


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    sent = []
    monkeypatch.setattr(appmod, "ACCOUNTS",
                        accounts_mod.AccountStore(str(tmp_path / "accounts.db")))
    monkeypatch.setattr(appmod, "BILLING", billing.BillingStore(str(tmp_path / "billing.db")))
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod.mail_mod, "status", lambda: {"available": True})
    monkeypatch.setattr(appmod.mail_mod, "send_later",
                        lambda to, subject, body: sent.append((to, body)))
    with TestClient(appmod.app) as c:
        assert c.post("/api/auth/signup", json={"email": "s@fam.test",
                                                "password": "correct horse battery"}
                      ).status_code == 200
        yield c, appmod, sent


def test_student_checkout_waits_for_the_check(client, monkeypatch):
    c, appmod, sent = client
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_STUDENT", "price_s")
    monkeypatch.setattr(billing, "create_checkout", lambda *a, **k: "https://checkout.test/s")
    refused = c.post("/api/billing/checkout", json={"plan": "student"})
    assert refused.status_code == 403 and refused.headers["X-FAM-Student"] == "verify"

    assert c.post("/api/billing/student", json={"email": "me@gmail.com"}).status_code == 400
    assert c.post("/api/billing/student", json={"email": "Me@MIT.edu"}).status_code == 200
    to, body = sent[-1]
    assert to == "me@mit.edu"
    code = "".join(ch for ch in body.split("is ", 1)[1][:7] if ch.isdigit())
    assert c.post("/api/billing/student/confirm", json={"code": code}).json()["verified"]
    assert c.get("/api/plans").json()["student"]["verified"] is True
    r = c.post("/api/billing/checkout", json={"plan": "student"})
    assert r.status_code == 200 and r.json()["url"] == "https://checkout.test/s"


def test_other_plans_need_no_check(client, monkeypatch):
    c, _appmod, _sent = client
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_SINGLE", "price_1")
    monkeypatch.setattr(billing, "create_checkout", lambda *a, **k: "https://checkout.test/1")
    assert c.post("/api/billing/checkout", json={"plan": "single"}).status_code == 200


def test_no_mail_says_so(client, monkeypatch):
    c, appmod, _sent = client
    monkeypatch.setattr(appmod.mail_mod, "status", lambda: {"available": False})
    r = c.post("/api/billing/student", json={"email": "me@mit.edu"})
    assert r.status_code == 503
