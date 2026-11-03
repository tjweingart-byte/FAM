#!/usr/bin/env python3
"""The activation step, in a real browser (PROBLEMS.md §414).

The API is unit-tested (`tests/test_activation_411.py`); what only a browser
shows is whether the screen appears when it is owed, holds through
sign-up's own steps, takes the answers, asks before an under-13 date deletes
an account, and whether Settings' rows and switch work. A zero-spend server
is started with every store in a temporary directory (found from the
source's `data_path` calls, as `smoke_opus.py` does), and the app is driven:

* a new account sees nothing on top of sign-up's steps, then the step;
* Continue waits for a date of birth; the five gender choices are drawn;
  the answers are kept, the place split, the listener counted;
* an under-13 date is asked "Is ... right?", and confirmed deletes the
  account and signs it out;
* Settings shows the date and the gender, and its switch turns counting off;
* nothing on the page throws.

    python tools/smoke_activation.py
"""
from __future__ import annotations

import datetime
import os
import pathlib
import sys
import tempfile
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from smoke_opus import free_port, launch_browser, store_variables  # noqa: E402

PASSWORD = "correct horse battery"
SIGN_UP = """async (email) => {
    const r = await fetch('/api/auth/signup', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email, password: '%s', accept_terms: true})});
    return r.status; }""" % PASSWORD


def overlay_shown(page) -> bool:
    return "active" in (page.locator("#activationOverlay").get_attribute("class") or "")


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(("  ok    " if ok else "  FAIL  ") + name
              + (f"  [{detail}]" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    today = datetime.date.today()
    try:
        young = today.replace(year=today.year - 10).isoformat()
    except ValueError:                      # 29 February
        young = today.replace(year=today.year - 10, day=28).isoformat()

    with tempfile.TemporaryDirectory() as data:
        env = dict(os.environ, FAM_ENV="staging", READ_LIMIT_PER_WINDOW="0",
                   WAITLIST="0")
        for var, name in store_variables().items():
            env[var] = os.path.join(data, name)
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        import subprocess

        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
             "--port", str(port)], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(120):
                try:
                    urllib.request.urlopen(base + "/api/auth/me", timeout=2)
                    break
                except Exception:
                    time.sleep(0.5)
            else:
                print("  FAIL  the server did not start", file=sys.stderr)
                return 1

            with sync_playwright() as pw:
                browser = launch_browser(pw)
                errors: list[str] = []

                # --- a new account: held through sign-up, then the step ---
                page = browser.new_page(viewport={"width": 390, "height": 844})
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base + "/")
                page.wait_for_timeout(1200)
                check("a sign-up is accepted", page.evaluate(SIGN_UP, "adult@fam.test") == 200)
                page.evaluate("identityMode = 'first-run'; showScreen('identity');")
                page.evaluate("refreshAuth().then(checkActivation)")
                page.wait_for_timeout(1200)
                check("nothing opens on top of sign-up's own steps", not overlay_shown(page))
                page.evaluate("showScreen('home')")
                page.wait_for_timeout(1200)
                check("the step opens once sign-up's steps are done", overlay_shown(page))
                check("it cannot be closed the first time", page.locator("#actClose").is_hidden())
                check("Continue waits for a date of birth", page.locator("#actGo").is_disabled())
                chips = page.locator(".act-chip").all_text_contents()
                check("the five gender choices are drawn",
                      chips == ["Man", "Woman", "Non-binary", "Self-describe",
                                "Prefer not to say"], str(chips))
                check("the counting notice is the server's",
                      "groups of at least 100 people" in
                      (page.locator("#actNotice").text_content() or ""))
                page.fill("#actBirth", "1990-05-04")
                page.dispatch_event("#actBirth", "input")
                page.fill("#actPlace", "Exton, PA")
                page.click(".act-chip:has-text('Woman')")
                page.click("#actGo")
                page.wait_for_timeout(1200)
                check("Continue closes the step", not overlay_shown(page))
                account = page.evaluate("fetch('/api/account').then(r => r.json())")
                check("the answers are kept and the listener counted",
                      (account.get("birth_date"), account.get("gender"),
                       account.get("counted")) == ("1990-05-04", "woman", True), str(account))
                place = page.evaluate("fetch('/api/activation').then(r => r.json())"
                                      ".then(d => d.location)")
                check("the one place box is split", (place.get("city"), place.get("region"))
                      == ("Exton", "Pennsylvania"), str(place))

                # --- Settings: the rows and the switch ---
                page.evaluate("openSettings()")
                page.wait_for_timeout(1500)
                rows = page.locator("#settingsBody .set-row").all_text_contents()
                check("Settings shows the date and the gender",
                      "Date of birth1990-05-04" in rows and "GenderWoman" in rows, str(rows))
                page.locator("#settingsBody .set-row", has_text="Count me").click()
                page.wait_for_timeout(1200)
                counted = page.evaluate("fetch('/api/consent').then(r => r.json())"
                                        ".then(d => d.demographics.counted)")
                check("Settings' switch turns counting off", counted is False)
                page.locator("#settingsBody .set-row", has_text="Gender").click()
                page.wait_for_timeout(1200)
                check("from Settings the step is an editor that closes",
                      overlay_shown(page) and page.locator("#actClose").is_visible())
                page.close()

                # --- under 13: asked first, then deleted ---
                page = browser.new_page(viewport={"width": 390, "height": 844})
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base + "/")
                page.wait_for_timeout(1200)
                page.evaluate(SIGN_UP, "young@fam.test")
                page.evaluate("refreshAuth().then(checkActivation)")
                page.wait_for_timeout(1500)
                page.fill("#actBirth", young)
                page.dispatch_event("#actBirth", "input")
                page.click("#actGo")
                page.wait_for_timeout(1200)
                question = page.locator("#actConfirmQ").text_content() or ""
                check("an under-13 date is asked about first",
                      page.locator("#actConfirm").is_visible()
                      and question.startswith("Is ") and question.endswith(" right?"),
                      question)
                page.click("#actConfirm button:has-text('Yes')")
                page.wait_for_timeout(1500)
                check("confirmed, the account is deleted and said so",
                      "13 and older" in (page.locator("#actGoneMsg").text_content() or ""))
                me = page.evaluate("fetch('/api/auth/me').then(r => r.json())")
                check("and the session is signed out", me.get("authenticated") is False)
                page.close()
                browser.close()
                check("nothing on the page threw", not errors, "; ".join(errors[:3]))
        finally:
            server.terminate()
            server.wait(timeout=10)

    if failures:
        print(f"{len(failures)} activation check(s) failed")
        return 1
    print("all activation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
