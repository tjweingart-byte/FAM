"""The admin tracker reads the real stores, answers questions, and lets only
admins in.

Four claims, in the order they would hurt if they broke:

* a non-admin - a guest, an ordinary account, a wrong token - gets a 404 from
  every tracker endpoint, and an account named in `FAM_ADMIN_ACCOUNTS` gets in
  with no token;
* nothing a question can say writes, attaches, or reads a secret;
* the numbers are the stores' own - the two example questions the tracker was
  asked for come back right;
* the list of stores is derived from the code, so none can be left off.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts as ACC  # noqa: E402
import admin_tracker as AT  # noqa: E402
import mixes as M  # noqa: E402
import social as S  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "a-long-enough-password"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Eight listeners, six of them accounts, a friend graph and two mixes."""
    for env, name in (("ACCOUNTS_DB", "accounts.db"), ("SOCIAL_DB", "social.db"),
                      ("MIXES_DB", "mixes.db")):
        monkeypatch.setenv(env, str(tmp_path / name))
    acc = ACC.AccountStore(str(tmp_path / "accounts.db"))
    soc = S.SocialStore(str(tmp_path / "social.db"))
    mix = M.MixStore(str(tmp_path / "mixes.db"))
    ids = [f"u{i}" for i in range(8)]
    for i, uid in enumerate(ids):
        soc.seen(uid)
        if i < 6:
            acc.sign_up(uid, f"p{i}@fam.test", PASSWORD)
    for other in ids[1:7]:          # u0 is mutual friends with six people
        soc.follow("u0", other)
        soc.follow(other, "u0")
    soc.follow("u1", "u2")
    soc.follow("u2", "u1")
    soc.follow("u3", "u4")          # one-way: a follow, not a friendship
    public = mix.create("u0", "Gym")
    mix.update("u0", public.id, public=True)
    mix.create("u1", "Run")          # private
    guest = mix.create("u7", "Guest mix")   # u7 has no account
    mix.update("u7", guest.id, public=True)
    stores = [s for s in AT.discover_stores()
              if s.alias in ("accounts", "social", "mixes")]
    monkeypatch.setattr(AT, "discover_stores", lambda root=None: stores)
    return stores


def ask(question):
    return asyncio.run(AT.ask(question))


# --- the two questions it was built to answer ---------------------------------

def test_how_many_accounts_have_made_public_mixes(world):
    res = ask("how many of the accounts have made public mixes?")
    assert res["source"] == "recipe"
    # u0 has one; u7's public mix does not count because u7 has no account.
    assert res["rows"] == [[1]]
    assert "public" in res["answer"].lower()


def test_how_many_accounts_have_over_five_friends(world):
    res = ask("how many accounts have over 5 friends")
    assert res["rows"] == [[1]]           # u0, with six
    assert "more than 5" in res["answer"]


def test_a_friend_is_mutual_and_fewer_than_counts_nobody(world):
    # u1 and u2 have two friends; u3, u4, u5 have one (u0). u3 -> u4 is a
    # one-way follow and must not make them friends.
    assert ask("how many accounts have exactly 1 friend")["rows"] == [[3]]
    assert ask("how many accounts have fewer than 2 friends")["rows"] == [[3]]
    assert ask("accounts with at least 2 friends")["rows"] == [[3]]


def test_the_dashboard_counts_every_account(world):
    snap = AT.snapshot()
    m = snap["metrics"]
    assert m["accounts_total"] == 6
    assert m["listeners_total"] == 8
    assert m["friendships"] == 7
    assert m["accounts_public_mix"] == 1
    assert m["mixes_public"] == 2
    assert sum(d["n"] for d in snap["signups"]) == 6
    assert len(snap["recent_accounts"]["rows"]) == 6
    # A store that does not exist yet is "no data", never zero.
    assert m["messages"] is None


# --- the sandbox ---------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "DELETE FROM accounts.accounts",
    "UPDATE accounts.accounts SET plan = 'unlimited'",
    "SELECT 1; DELETE FROM accounts.accounts",
    "ATTACH DATABASE '/tmp/x.db' AS x",
    "PRAGMA accounts.table_info(accounts)",
    "SELECT * FROM pragma_table_info('accounts')",
    "SELECT zeroblob(100000000)",
    "SELECT load_extension('x')",
])
def test_nothing_but_reading(world, sql):
    with pytest.raises(AT.QueryError):
        AT.run_query(sql)
    assert AT.run_query("SELECT COUNT(*) FROM accounts.accounts")["rows"] == [[6]]


def test_secrets_read_as_null(world):
    res = AT.run_query("SELECT email, password FROM accounts.accounts LIMIT 1")
    assert res["rows"][0][0].endswith("@fam.test")
    assert res["rows"][0][1] is None
    tokens = AT.run_query("SELECT token_hash FROM accounts.sessions")
    assert all(row == [None] for row in tokens["rows"])


def test_a_runaway_query_is_stopped(world):
    with pytest.raises(AT.QueryError, match="stopped"):
        AT.run_query("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
                     "SELECT COUNT(*) FROM c", seconds=0.3)


def test_the_files_are_opened_read_only(world):
    before = {s.alias: Path(s.path).read_bytes() for s in world}
    AT.snapshot()
    AT.run_query("SELECT * FROM social.follows")
    assert {s.alias: Path(s.path).read_bytes() for s in world} == before


def test_no_recipe_and_no_key_says_so_rather_than_guessing(world, monkeypatch):
    import credentials
    monkeypatch.setattr(credentials, "active", lambda name: "")
    with pytest.raises(AT.QueryError, match="no Anthropic key"):
        ask("what is the median length of a display name")


# --- every store, derived -------------------------------------------------------

def test_every_store_the_app_opens_is_tracked():
    """The tracker's store list is read out of the code, so it cannot fall
    behind - which is what the old tracker did."""
    declared = set()
    for module in ROOT.glob("*.py"):
        for _, filename in re.findall(r'data_path\(\s*"([A-Z_]+)"\s*,\s*"([^"]+)"',
                                      module.read_text()):
            declared.add(filename)
    assert {s.filename for s in AT.discover_stores()} == declared
    assert {"accounts.db", "social.db", "mixes.db", "myfam.db"} <= declared


# --- who may look ---------------------------------------------------------------

@pytest.fixture
def client(world, tmp_path, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "ACCOUNTS", ACC.AccountStore(str(tmp_path / "app-accounts.db")))
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "")
    monkeypatch.delenv("FAM_ADMIN_ACCOUNTS", raising=False)
    return appmod, TestClient(appmod.app)


ENDPOINTS = [("get", "/api/admin/tracker", None), ("get", "/api/admin/schema", None),
             ("post", "/api/admin/ask", {"question": "how many accounts"}),
             ("post", "/api/admin/query", {"sql": "SELECT 1"})]


def _all_status(c, headers=None):
    out = []
    for verb, path, body in ENDPOINTS:
        kwargs = {"headers": headers or {}}
        if body is not None:
            kwargs["json"] = body
        out.append(getattr(c, verb)(path, **kwargs).status_code)
    return out


def test_unconfigured_there_is_no_admin_page_at_all(client):
    _, c = client
    assert c.get("/admin").status_code == 404
    assert _all_status(c) == [404] * 4


def test_a_guest_and_an_ordinary_account_get_nothing(client, monkeypatch):
    _, c = client
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", "boss@fam.test")
    assert _all_status(c) == [404] * 4                      # guest
    assert c.post("/api/auth/signup", json={"email": "nobody@fam.test",
                                            "password": PASSWORD}).status_code == 200
    assert _all_status(c) == [404] * 4                      # an account, not an admin
    # The page itself is a shell with no data in it.
    page = c.get("/admin")
    assert page.status_code == 200 and "@fam.test" not in page.text


def test_an_admin_account_gets_in_with_no_token(client, monkeypatch):
    _, c = client
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", "someone@else.test, Boss@FAM.test")
    assert c.post("/api/auth/signup", json={"email": "boss@fam.test",
                                            "password": PASSWORD}).status_code == 200
    assert _all_status(c) == [200] * 4
    snap = c.get("/api/admin/tracker").json()
    assert snap["metrics"]["accounts_total"] == 6 and snap["via"] == "account"
    answer = c.post("/api/admin/ask", json={
        "question": "how many accounts have over 5 friends"}).json()
    assert answer["rows"] == [[1]] and answer["sql"]
    bad = c.post("/api/admin/query", json={"sql": "DELETE FROM accounts.accounts"})
    assert bad.status_code == 400


def test_the_token_still_works_and_a_wrong_one_does_not(client, monkeypatch):
    appmod, c = client
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "sekrit")
    assert _all_status(c, {"X-Admin-Token": "wrong"}) == [404] * 4
    assert _all_status(c, {"X-Admin-Token": "sekrit"}) == [200] * 4


def test_a_client_cannot_claim_to_be_an_admin(client, monkeypatch):
    """The id is the session's, never a parameter or a header."""
    _, c = client
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", "u0")
    assert c.get("/api/admin/tracker?user=u0").status_code == 404
    assert c.get("/api/admin/tracker", headers={"X-User": "u0"}).status_code == 404
