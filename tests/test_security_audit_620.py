"""The security audit of 2026-11-16 (PROBLEMS.md §620): what it found in
sign-in and sessions, each pinned as the behaviour that closes it.

Every test here failed against the code as the audit found it - that failure
was the reproduction - and passes against the fix.

1. **Guessing a password was not paced at all.** `_rate_limit` keys on the
   session, and a request with no cookie is minted a fresh session, so a
   script that simply never sends a cookie got a new, full bucket every time:
   unlimited tries at any account's password, the admin dashboard's included.
   Wrong tries are now counted against the *address being tried*, whoever
   sends them.
2. **Both cookies went out without `Secure` on the HTTPS site.** Behind
   Render's router the connection the app sees is plain HTTP, so
   `request.url.scheme` never said https.
3. **The reset email's link was whatever host the request named.** A stranger
   could have FAM email somebody a real code beside a link to the stranger's
   own page.
4. **An admin address with no account could be claimed by anybody**, because
   an address is never verified. The server now says so out loud.
5. **Changing an account's email told nobody.** It asks for no password, and
   the next reset code goes to the new address; the old one is now told.
6. **No response said how a browser may treat it**: nothing against framing,
   type sniffing or a return over plain http.
7. **A note could be deleted by a request with no session.** The store reads
   an empty listener id as a moderator.
8. **Attaching a link made the server fetch any address it was given** - its
   own loopback, a private network, its host's metadata address - and hand
   back the page's title and first 160 characters. Only public addresses now,
   each redirect checked, the checked address the one connected to.
9. **The admin's question box could read a reset code's hash**, a let-in
   link's code and a kept Apple token: its list of hidden columns is kept by
   hand and predates all three.
10. **A visitor with no account could have photos checked by the paid model
    without end**, because the pace on it follows a session a guest never
    keeps.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts as A  # noqa: E402
import app as appmod  # noqa: E402
import mail  # noqa: E402

GOOD = "a-long-enough-password"
EMAIL = "ian@example.com"


@pytest.fixture
def outbox(monkeypatch):
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mail, "status", lambda: {"available": True, "reason": ""})
    monkeypatch.setattr(mail, "send_later",
                        lambda to, subject, body: sent.append((to, subject, body)))
    return sent


@pytest.fixture
def server(monkeypatch, tmp_path, outbox):
    """The real limiter, an accounts store of its own, and nothing remembered
    from another test."""
    monkeypatch.setattr(appmod, "ACCOUNTS", A.AccountStore(str(tmp_path / "a.db")))
    appmod._read_hits.clear()
    appmod._gen_tokens.clear()
    appmod._auth_failures.clear()
    appmod.ACCOUNTS.sign_up("u1", EMAIL, GOOD)
    return appmod.app


def _stranger(server) -> TestClient:
    """A client that has never been here: no cookie, so a fresh session."""
    return TestClient(server)


def _login(client, password, email=EMAIL):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# --- 1. guessing a password ---------------------------------------------------


def test_wrong_passwords_are_counted_against_the_address_whoever_sends_them(server):
    """Twenty guesses, each from a client with no cookie. Before: twenty 401s,
    and the twenty-first could have been the right one."""
    answers = [_login(_stranger(server), f"wrong-guess-{n}").status_code
               for n in range(20)]
    assert answers[:appmod.AUTH_FREE_FAILURES] == [401] * appmod.AUTH_FREE_FAILURES
    assert 429 in answers, "nothing ever told a guesser to stop"
    assert set(answers[appmod.AUTH_FREE_FAILURES:]) == {429}


def test_the_right_password_does_not_get_through_a_lock(server):
    """Or the lock would slow nothing: the guess that is right would pass."""
    for n in range(appmod.AUTH_FREE_FAILURES):
        assert _login(_stranger(server), f"wrong-guess-{n}").status_code == 401
    refused = _login(_stranger(server), GOOD)
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    assert "fam_session" not in refused.headers.get("set-cookie", "") or \
        refused.json().get("authenticated") is not True


def test_a_lock_reads_the_same_for_an_address_with_no_account(server):
    """The refusal must not say which addresses are registered."""
    def lock(email):
        for n in range(appmod.AUTH_FREE_FAILURES):
            _login(_stranger(server), f"wrong-guess-{n}", email)
        return _login(_stranger(server), "one-more-guess", email)

    known, unknown = lock(EMAIL), lock("nobody@example.com")
    assert (known.status_code, unknown.status_code) == (429, 429)
    assert known.json()["error"] == unknown.json()["error"]


def test_a_few_slips_do_not_lock_anybody_out(server):
    client = _stranger(server)
    for n in range(appmod.AUTH_FREE_FAILURES - 1):
        assert _login(client, f"typo-{n}").status_code == 401
        appmod._gen_tokens.clear()   # the session's own pace is not the subject
    assert _login(client, GOOD).status_code == 200
    # And the count starts again after getting it right.
    assert appmod._auth_failures == {}


def test_a_lock_ends(server, monkeypatch):
    for n in range(appmod.AUTH_FREE_FAILURES):
        _login(_stranger(server), f"wrong-guess-{n}")
    assert _login(_stranger(server), GOOD).status_code == 429
    real = appmod.time.monotonic
    monkeypatch.setattr(appmod, "_auth_clock",
                        lambda: real() + appmod.AUTH_LOCK_SECONDS + 1)
    assert _login(_stranger(server), GOOD).status_code == 200


def test_each_wrong_try_after_the_lock_waits_longer(server, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(appmod, "_auth_clock", lambda: clock["now"])
    for n in range(appmod.AUTH_FREE_FAILURES):
        _login(_stranger(server), f"wrong-guess-{n}")
    waits = []
    for n in range(8):
        waits.append(appmod._auth_failures[appmod._auth_key("email", EMAIL)][2]
                     - clock["now"])
        clock["now"] += waits[-1] + 1
        assert _login(_stranger(server), f"another-{n}").status_code == 401
    assert waits[0] == appmod.AUTH_LOCK_SECONDS
    assert waits == sorted(waits) and waits[1] > waits[0]
    assert max(waits) <= appmod.AUTH_LOCK_MAX_SECONDS


def test_the_account_itself_is_not_locked_out_of_its_own_devices(server):
    """A lock is something a stranger can cause, so it must not be a way to
    keep the owner out: a browser already signed in to the account (a reset
    code signs one in) passes with the right password."""
    owner = _stranger(server)
    assert _login(owner, GOOD).status_code == 200
    for n in range(appmod.AUTH_FREE_FAILURES + 2):
        _login(_stranger(server), f"wrong-guess-{n}")
    assert _login(_stranger(server), GOOD).status_code == 429
    assert _login(owner, GOOD).status_code == 200


def test_the_admin_sign_in_is_counted_the_same_way(server, monkeypatch):
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", EMAIL)
    answers = [_stranger(server).post("/api/admin/login",
                                      json={"email": EMAIL, "password": f"guess-{n}"}).status_code
               for n in range(appmod.AUTH_FREE_FAILURES + 3)]
    assert answers[:appmod.AUTH_FREE_FAILURES] == [401] * appmod.AUTH_FREE_FAILURES
    assert set(answers[appmod.AUTH_FREE_FAILURES:]) == {429}
    # One count for both doors: the app's sign-in is locked too.
    assert _login(_stranger(server), GOOD).status_code == 429


def test_a_phone_sign_in_is_counted_too(server):
    appmod.ACCOUNTS.sign_up_phone("u2", "+15551234567", GOOD)

    def phone(password):
        return _stranger(server).post("/api/auth/login", json={
            "phone": "+1 (555) 123-4567", "password": password})

    for n in range(appmod.AUTH_FREE_FAILURES):
        assert phone(f"wrong-guess-{n}").status_code == 401
    assert phone(GOOD).status_code == 429


# --- 2. the cookies -----------------------------------------------------------


def test_the_session_cookie_is_secure_behind_the_router(server):
    """Render's router speaks plain HTTP to the app and says the listener's
    side was https in `X-Forwarded-Proto`."""
    answer = _stranger(server).get("/api/auth/me",
                                   headers={"X-Forwarded-Proto": "https"})
    cookie = answer.headers["set-cookie"]
    assert "fam_session=" in cookie
    assert "secure" in cookie.lower()
    assert "httponly" in cookie.lower()


def test_the_admin_cookie_is_secure_behind_the_router(server, monkeypatch):
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", EMAIL)
    answer = _stranger(server).post("/api/admin/login",
                                    json={"email": EMAIL, "password": GOOD},
                                    headers={"X-Forwarded-Proto": "https"})
    assert answer.status_code == 200
    admin = [c for c in answer.headers.get_list("set-cookie") if "fam_admin=" in c]
    assert admin and "secure" in admin[0].lower()


def test_a_plain_http_address_still_gets_a_cookie_it_can_send_back(server):
    """The LAN address a phone tests on is http, and `Secure` there would
    drop the cookie."""
    cookie = _stranger(server).get("/api/auth/me").headers["set-cookie"]
    assert "secure" not in cookie.lower()


# --- 3. the reset email -------------------------------------------------------


def test_the_reset_email_never_links_where_the_request_says(server, outbox, monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    answer = _stranger(server).post(
        "/api/auth/reset/start", json={"email": EMAIL},
        headers={"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"})
    assert answer.status_code == 200
    body = outbox[-1][2]
    assert "evil.example" not in body
    assert f"{appmod.settings.app_home_url}/reset" in body


def test_the_reset_email_ignores_a_forged_host_header_too(server, outbox, monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    _stranger(server).post("/api/auth/reset/start", json={"email": EMAIL},
                           headers={"Host": "evil.example"})
    assert "evil.example" not in outbox[-1][2]


# --- 4. an admin address nobody holds ----------------------------------------


def test_an_admin_address_with_no_account_is_reported(server, monkeypatch):
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", f"{EMAIL}, boss@example.com, +15550001111")
    assert appmod._unclaimed_admin_entries() == 2


def test_no_admin_entries_unclaimed_when_every_one_has_an_account(server, monkeypatch):
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", f"{EMAIL}, u1")
    assert appmod._unclaimed_admin_entries() == 0


def test_the_admin_page_says_when_an_admin_address_is_unclaimed(server, monkeypatch):
    """Where an admin will see it: the dashboard's own sign-in answer."""
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", f"{EMAIL}, boss@example.com")
    answer = _stranger(server).post("/api/admin/login",
                                    json={"email": EMAIL, "password": GOOD})
    assert answer.status_code == 200
    assert answer.json()["unclaimed_admin_entries"] == 1
    assert "boss@example.com" not in answer.text


# --- 5. the address an account leaves ----------------------------------------


def test_the_old_address_is_told_when_the_email_changes(server, outbox):
    client = _stranger(server)
    assert _login(client, GOOD).status_code == 200
    appmod._gen_tokens.clear()
    changed = client.post("/api/account", json={"email": "new@example.com"})
    assert changed.status_code == 200 and changed.json()["email"] == "new@example.com"
    told = [m for m in outbox if m[0] == EMAIL]
    assert len(told) == 1
    assert "changed" in told[0][1]
    assert "new@example.com" in told[0][2]


def test_nothing_is_sent_when_the_email_did_not_change(server, outbox):
    client = _stranger(server)
    assert _login(client, GOOD).status_code == 200
    appmod._gen_tokens.clear()
    assert client.post("/api/account", json={"display_name": "Ian"}).status_code == 200
    assert client.post("/api/account", json={"email": EMAIL}).status_code == 200
    assert outbox == []


# --- 6. what every response says ----------------------------------------------


@pytest.mark.parametrize("path", ["/api/auth/me", "/", "/waitlist", "/terms",
                                  "/s/no-such-share", "/api/health", "/no/such/page"])
def test_every_response_refuses_framing_and_sniffing(server, path):
    answer = _stranger(server).get(path, follow_redirects=False)
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert answer.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in answer.headers["content-security-policy"]
    assert answer.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_https_is_remembered_only_when_the_request_was_https(server):
    plain = _stranger(server).get("/api/auth/me")
    assert "strict-transport-security" not in plain.headers
    routed = _stranger(server).get("/api/auth/me",
                                   headers={"X-Forwarded-Proto": "https"})
    assert routed.headers["strict-transport-security"] == appmod.HSTS


def test_a_refusal_carries_the_headers_too(server, monkeypatch):
    """The gate and the pace answer before any handler runs."""
    for n in range(appmod.AUTH_FREE_FAILURES + 1):
        refused = _login(_stranger(server), f"wrong-guess-{n}")
    assert refused.status_code == 429
    assert refused.headers["x-frame-options"] == "SAMEORIGIN"


# --- 7. a note and a request with no session ----------------------------------


def test_a_request_with_no_session_cannot_delete_somebodys_note(server, monkeypatch, tmp_path):
    import social as social_mod

    store = social_mod.SocialStore(str(tmp_path / "social.db"))
    monkeypatch.setattr(appmod, "SOCIAL", store)
    note = store.add_note("u1", "my own words")
    note_id = note.get("id") or note.get("note", {}).get("id")
    assert note_id, note

    # The accounts store cannot write, so no session is minted and the
    # request has no listener - which `remove_note` reads as a moderator.
    def broken(*args, **kwargs):
        raise RuntimeError("the accounts store is not answering")

    monkeypatch.setattr(appmod.ACCOUNTS, "new_session", broken)
    answer = _stranger(server).delete(f"/api/story/note/{note_id}")
    assert answer.status_code == 503
    assert store.note_row(int(note_id)), "a stranger with no session deleted a note"


# --- 8. a link is only ever a public page -------------------------------------

import asyncio  # noqa: E402
import http.server  # noqa: E402
import ssl  # noqa: E402
import threading  # noqa: E402

import attachments  # noqa: E402


class _Inside(http.server.BaseHTTPRequestHandler):
    """Something only this machine should be able to read."""

    hits: list = []

    def do_GET(self):  # noqa: N802 - the base class's name
        type(self).hits.append((self.path, self.headers.get("Host", "")))
        if self.path == "/hop":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
            return
        if self.path == "/big":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            try:
                for _ in range(400):
                    self.wfile.write(b"<p>" + b"x" * 4000 + b"</p>")
            except OSError:
                pass
            return
        body = b"<title>internal admin panel</title><p>DB_PASSWORD=hunter2</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def inside():
    _Inside.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Inside)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def _attach(server, url):
    appmod._gen_tokens.clear()
    return _stranger(server).post("/api/attach", json={"kind": "link", "url": url})


def test_a_link_to_this_machine_is_refused_and_never_fetched(server, inside):
    """The reproduction: this answered 200 with the page's title and
    "DB_PASSWORD=hunter2" in `preview`."""
    answer = _attach(server, f"http://127.0.0.1:{inside}/secret")
    assert answer.status_code == 400
    assert "hunter2" not in answer.text and "internal" not in answer.text
    assert _Inside.hits == [], "the server fetched an address on its own machine"


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.5", "192.168.1.10", "172.16.0.1", "169.254.169.254",
    "100.64.0.1", "0.0.0.0", "::1", "::ffff:127.0.0.1", "fd00::1", "fe80::1",
    "224.0.0.1"])
def test_no_private_address_is_public(address):
    with pytest.raises(attachments.AttachmentError):
        attachments._public_addresses(address, 80)


def test_a_public_address_is(monkeypatch):
    assert attachments._public_addresses("93.184.216.34", 443) == ["93.184.216.34"]


def _resolves(monkeypatch, table):
    real = attachments.socket.getaddrinfo

    def lookup(host, port, *args, **kwargs):
        if host in table:
            return [(attachments.socket.AF_INET, attachments.socket.SOCK_STREAM, 6, "",
                     (ip, port)) for ip in table[host]]
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(attachments.socket, "getaddrinfo", lookup)


def test_a_name_that_resolves_inside_is_refused(server, inside, monkeypatch):
    _resolves(monkeypatch, {"intranet.example": ["10.1.2.3"]})
    assert _attach(server, "http://intranet.example/").status_code == 400


def test_one_private_answer_among_public_ones_is_refused(monkeypatch):
    """How a name is made to look safe: a public address first."""
    _resolves(monkeypatch, {"mixed.example": ["93.184.216.34", "127.0.0.1"]})
    with pytest.raises(attachments.AttachmentError):
        attachments._public_addresses("mixed.example", 80)


def _as_if_public(monkeypatch, name):
    """`name` is treated as a public site that happens to live on the test
    server; every other name is checked for real."""
    real = attachments._public_addresses
    monkeypatch.setattr(attachments, "_public_addresses",
                        lambda host, port: ["127.0.0.1"] if host == name else real(host, port))


def test_a_public_page_is_still_read_by_its_checked_address(inside, monkeypatch):
    _as_if_public(monkeypatch, "pages.example")
    title, text = attachments.fetch_link(f"http://pages.example:{inside}/page")
    assert title == "internal admin panel" and "hunter2" in text
    # Connected to the address that was checked, named in Host.
    assert _Inside.hits == [("/page", f"pages.example:{inside}")]


def test_a_redirect_to_a_private_address_is_refused(inside, monkeypatch):
    _as_if_public(monkeypatch, "pages.example")
    with pytest.raises(attachments.AttachmentError) as refused:
        attachments.fetch_link(f"http://pages.example:{inside}/hop")
    assert str(refused.value) == attachments.NOT_PUBLIC
    assert [path for path, _ in _Inside.hits] == ["/hop"]


def test_a_very_large_page_is_read_only_to_the_cap(inside, monkeypatch):
    _as_if_public(monkeypatch, "pages.example")
    monkeypatch.setattr(attachments, "MAX_BYTES", 20_000)
    import httpx

    with httpx.Client(follow_redirects=False, timeout=5, trust_env=False) as client:
        body = attachments._read_public(client, f"http://pages.example:{inside}/big")
    assert 0 < len(body) <= 20_000


def test_a_link_with_a_password_in_it_is_refused():
    with pytest.raises(attachments.AttachmentError):
        attachments.fetch_link("http://user:pass@93.184.216.34/")


def test_reading_an_attachment_is_off_the_event_loop(server, monkeypatch):
    """A link is eight seconds of waiting and a PDF seconds of parsing; on
    the loop that is every listener's audio stopped."""
    seen = {}

    def build(kind, name="", data_b64="", url=""):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return attachments.Attachment(id="a1", kind="document", name="n", text="words")

    monkeypatch.setattr(appmod.attachments_mod, "build", build)
    answer = _stranger(server).post("/api/attach", json={
        "kind": "document", "name": "n.txt", "data": "d29yZHM="})
    assert answer.status_code == 200
    assert seen == {"on_loop": False}


def _self_signed(name, tmp_path):
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_file, key_file = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    return str(cert_file), str(key_file)


def test_https_is_still_verified_against_the_name_not_the_address(tmp_path, monkeypatch):
    """Connecting to the checked address must not weaken TLS: the certificate
    is checked against the site that was asked for, and a site presenting
    somebody else's is refused."""
    import httpx

    cert_file, key_file = _self_signed("pinned.test", tmp_path)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Inside)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    monkeypatch.setattr(attachments, "_public_addresses", lambda host, port: ["127.0.0.1"])
    try:
        with httpx.Client(follow_redirects=False, timeout=5, trust_env=False,
                          verify=ssl.create_default_context(cafile=cert_file)) as client:
            page = attachments._read_public(client, f"https://pinned.test:{port}/page")
            assert "hunter2" in page
            with pytest.raises(httpx.ConnectError):
                attachments._read_public(client, f"https://other.test:{port}/page")
    finally:
        server.shutdown()


# --- 9. what the admin's question box can read --------------------------------


@pytest.mark.sqlite_file
def test_a_question_cannot_read_a_reset_code_a_let_in_link_or_a_kept_token(tmp_path, monkeypatch):
    """`SECRET_COLUMNS` is a list kept by hand, and three secrets were added
    to the accounts store after it was written. Reproduced before the fix:
    the reset code's hash came back, and a six-digit code is found from its
    hash in under a second - which is somebody's password, reset."""
    import hashlib

    import admin_tracker as AT
    import mixes as M
    import waitlist as W

    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("MIXES_DB", str(tmp_path / "mixes.db"))
    store = A.AccountStore(str(tmp_path / "accounts.db"))
    store.sign_up("u1", EMAIL, GOOD)
    _user, _address, code = store.start_reset(EMAIL)
    store.sign_in_with("apple", "apple-subject-1", current_user_id="u1")
    store.keep_provider_token("apple", "apple-subject-1", "refresh-token-xyz", "client")
    W.Waitlist(store).ensure_claim_code("u1")
    mixes = M.MixStore(str(tmp_path / "mixes.db"))
    mix = mixes.create("u1", "Gym")
    mixes.invite_token("u1", mix.id)

    def read(sql):
        return [row[0] for row in AT.run_query(sql)["rows"]]

    hashes = read("SELECT code_hash FROM accounts.password_resets")
    cracked = [n for n in range(10 ** 6) if hashes and hashes[0] and hashlib.sha256(
        f"fam-reset:u1:{n:06d}".encode()).hexdigest() == hashes[0]][:1]
    assert cracked == [], f"the question box gave up reset code {code}"
    assert read("SELECT code_hash FROM accounts.password_resets") == [None]
    assert read("SELECT claim_code FROM accounts.accounts") == [None]
    assert read("SELECT token FROM accounts.identities WHERE provider = 'apple'") == [None]
    assert read("SELECT join_token FROM mixes.mixes") == [None]
    # A credential-shaped column is hidden without being listed: the list is
    # kept by hand, which is how these three were missed.
    import sqlite3

    extra = sqlite3.connect(str(tmp_path / "accounts.db"))
    extra.execute("CREATE TABLE later (id TEXT, api_token TEXT, signing_secret TEXT, note TEXT)")
    extra.execute("INSERT INTO later VALUES ('1', 'tok', 'sec', 'plain')")
    extra.commit()
    extra.close()
    assert AT.run_query("SELECT api_token, signing_secret, note FROM accounts.later"
                        )["rows"] == [[None, None, "plain"]]
    # And what it is for still reads.
    assert read("SELECT email FROM accounts.accounts") == [EMAIL]


# --- 10. a paid photo check needs an account ----------------------------------

_PIXEL = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
          "2mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def test_a_guest_cannot_spend_a_photo_check(server, monkeypatch, tmp_path):
    import image_check
    import social as social_mod

    monkeypatch.setattr(appmod, "SOCIAL", social_mod.SocialStore(str(tmp_path / "s.db")))
    calls = []

    async def check(data_url, usage=None):
        calls.append(1)
        return image_check.Verdict(True, "")

    monkeypatch.setattr(image_check, "will_call", lambda: True)
    monkeypatch.setattr(image_check, "check", check)
    # Forty strangers, none keeping a cookie: before, forty paid checks.
    answers = {_stranger(server).post("/api/me", json={
        "name": "Guest", "handle": f"guest{n}", "avatar": _PIXEL}).status_code
        for n in range(40)}
    assert answers == {401}
    assert calls == []

    # An account's picture is still checked, and kept.
    owner = _stranger(server)
    assert _login(owner, GOOD).status_code == 200
    appmod._gen_tokens.clear()
    saved = owner.post("/api/me", json={"name": "Ian", "handle": "ian", "avatar": _PIXEL})
    assert saved.status_code == 200, saved.text
    assert calls == [1]


# --- what the independent review of these fixes found -------------------------
#
# A second reviewer, on a different model and given the code without the
# author's conclusions, found two ways round the fixes above and four
# regressions. Each is pinned here.


def test_naming_made_up_addresses_cannot_reset_somebodys_count(server, monkeypatch):
    """The table was emptied of everything not waiting when it filled, so a
    guesser named twenty thousand addresses and had five fresh tries."""
    monkeypatch.setattr(appmod, "AUTH_TRACKED_MAX", 50)
    clock = {"now": 1000.0}
    monkeypatch.setattr(appmod, "_auth_clock", lambda: clock["now"])
    for n in range(appmod.AUTH_FREE_FAILURES + 1):
        _login(_stranger(server), f"wrong-guess-{n}")
    key = appmod._auth_key("email", EMAIL)
    clock["now"] = appmod._auth_failures[key][2] + 1      # its wait has just ended
    for n in range(120):                                  # the flood
        appmod._gen_tokens.clear()
        _login(_stranger(server), "x-wrong-guess", f"made-up-{n}@example.com")
    assert appmod._auth_failures.get(key, [0])[0] >= appmod.AUTH_FREE_FAILURES, \
        "a flood of made-up addresses forgot the count of the one being guessed at"
    assert len(appmod._auth_failures) <= 50


def test_a_table_full_of_locks_refuses_a_new_address_rather_than_forgetting(server, monkeypatch):
    monkeypatch.setattr(appmod, "AUTH_TRACKED_MAX", 3)
    now = appmod._auth_clock()
    for n in range(3):
        appmod._auth_failures[f"email:held-{n}@example.com"] = [9, now, now + 600]
    answer = _login(_stranger(server), "anything-at-all", "new@example.com")
    assert answer.status_code == 429
    assert len(appmod._auth_failures) == 3


def test_the_join_form_does_not_say_which_addresses_are_admins(server, monkeypatch):
    """Only an admin's address reached the count on this form, so only an
    admin's address ever answered 429."""
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", EMAIL)
    appmod.ACCOUNTS.sign_up("u9", "plain@example.com", GOOD)

    def joins(email):
        out = []
        for _ in range(appmod.AUTH_FREE_FAILURES + 2):
            appmod._gen_tokens.clear()
            out.append(_stranger(server).post("/api/waitlist/join", json={
                "email": email, "password": "a-wrong-password", "accept_terms": True}).status_code)
        return out

    assert joins(EMAIL) == joins("plain@example.com")


@pytest.mark.sqlite_file
def test_counts_of_model_tokens_still_read_in_the_question_box(tmp_path, monkeypatch):
    """`token` anywhere in a name hid `input_tokens` in the cost ledger."""
    import sqlite3

    import admin_tracker as AT

    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    A.AccountStore(str(tmp_path / "accounts.db"))
    extra = sqlite3.connect(str(tmp_path / "accounts.db"))
    extra.execute("CREATE TABLE ledger (input_tokens INTEGER, output_tokens INTEGER,"
                  " cache_read_tokens INTEGER, session_token TEXT, token TEXT, token_hash TEXT)")
    extra.execute("INSERT INTO ledger VALUES (10, 20, 30, 's', 't', 'h')")
    extra.commit()
    extra.close()
    assert AT.run_query("SELECT input_tokens, output_tokens, cache_read_tokens,"
                        " session_token, token, token_hash FROM accounts.ledger"
                        )["rows"] == [[10, 20, 30, None, None, None]]
    assert not AT._SECRET_NAME.search("done_hash") or \
        ("agents_tasks", "tasks", "done_hash") in AT._NOT_SECRET


@pytest.mark.parametrize("address", [
    "64:ff9b::a9fe:a9fe",    # NAT64 of 169.254.169.254
    "64:ff9b::7f00:1",       # NAT64 of 127.0.0.1
    "64:ff9b:1::a00:5",      # local-use NAT64 of 10.0.0.5
    "::7f00:1",              # IPv4-compatible 127.0.0.1
    "2002:a9fe:a9fe::1",     # 6to4 of 169.254.169.254
    "fec0::1"])              # site-local
def test_a_private_address_inside_an_ipv6_one_is_not_public(address):
    with pytest.raises(attachments.AttachmentError):
        attachments._public_addresses(address, 80)


def test_a_real_ipv6_address_is_public():
    assert attachments._public_addresses("2606:4700:4700::1111", 443) == ["2606:4700:4700::1111"]


def test_an_ipv6_link_names_itself_in_brackets(inside, monkeypatch):
    monkeypatch.setattr(attachments, "_public_addresses", lambda host, port: ["127.0.0.1"])
    attachments.fetch_link(f"http://[2606:4700:4700::1111]:{inside}/page")
    assert _Inside.hits == [("/page", f"[2606:4700:4700::1111]:{inside}")]


def test_a_name_opens_by_its_second_address_when_the_first_does_not_answer(inside, monkeypatch):
    """As when the name itself was connected to: a host that cannot reach
    the IPv6 address listed first must still open the page."""
    # Nothing listens on 127.0.0.9; the test server is on 127.0.0.1 only.
    monkeypatch.setattr(attachments, "_public_addresses",
                        lambda host, port: ["127.0.0.9", "127.0.0.1"])
    title, _text = attachments.fetch_link(f"http://pages.example:{inside}/page")
    assert title == "internal admin panel"


def test_a_compressed_page_is_unpacked_only_to_the_cap(monkeypatch):
    """200 KB of gzip can be 67 MB of page; it used to arrive in one piece."""
    import gzip

    import httpx

    packed = gzip.compress(b"<p>" + b"a" * 30_000_000 + b"</p>")
    assert len(packed) < 100_000
    seen = {}

    class Bomb(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen["accept"] = self.headers.get("Accept-Encoding")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(packed)))
            self.end_headers()
            self.wfile.write(packed)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Bomb)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(attachments, "_public_addresses", lambda host, port: ["127.0.0.1"])
    monkeypatch.setattr(attachments, "MAX_BYTES", 50_000)
    grown = []
    real = attachments.zlib.decompressobj

    def watched(*args):
        inner = real(*args)

        class Watch:
            def decompress(self, data, max_length=0):
                out = inner.decompress(data, max_length)
                grown.append(len(out))
                return out

        return Watch()

    monkeypatch.setattr(attachments.zlib, "decompressobj", watched)
    try:
        with httpx.Client(follow_redirects=False, timeout=5, trust_env=False) as client:
            body = attachments._read_public(
                client, f"http://pages.example:{server.server_address[1]}/")
    finally:
        server.shutdown()
    assert seen["accept"] == "identity"
    assert 0 < len(body) <= 50_000
    assert max(grown) <= 50_000, "one piece of the page was unpacked past the cap"
