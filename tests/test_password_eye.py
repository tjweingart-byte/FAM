"""Every password field a listener types into has the eye beside it (§283):
tap to see what was typed, tap again to hide it."""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGES = ["static/index.html", "static/waitlist.html"]

_PASSWORD = re.compile(r'<input\b[^>]*\btype="password"[^>]*>', re.S)
_ID = re.compile(r'\bid="([^"]+)"')


@pytest.mark.parametrize("page", PAGES)
def test_every_password_field_has_an_eye(page):
    html = (ROOT / page).read_text()
    fields = _PASSWORD.findall(html)
    assert fields, page + " has no password field to check"
    for field in fields:
        field_id = _ID.search(field).group(1)
        after = html[html.index(field) + len(field):][:200]
        # The eye is the next element, inside the same .pw-row.
        assert re.match(r'\s*<button class="pw-eye"', after), field_id
        assert ("togglePasswordShown('" + field_id + "'" in after
                or 'data-pw="' + field_id + '"' in after), field_id
        before = html[:html.index(field)][-120:]
        assert 'class="pw-row"' in before, field_id


@pytest.mark.parametrize("page", PAGES)
def test_the_eye_starts_closed_and_says_what_it_does(page):
    html = (ROOT / page).read_text()
    for button in re.findall(r'<button class="pw-eye"[^>]*>', html):
        assert 'aria-pressed="false"' in button
        assert 'aria-label="Show password"' in button
