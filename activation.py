"""The activation step: date of birth, location and gender, asked once (§411).

Decided items 2 and 11, and the wording the owner approved on 2026-11-03
(`docs/ACTIVATION_PRIVACY_DRAFT.md`, PROBLEMS.md §410):

* **When.** An account let into the app - every account not `waitlisted` -
  is asked once: when it is let off the waitlist, and, for those already in,
  the next time they open the app. `needed` says so; `/api/auth/me`, which
  every open asks first, carries it.
* **Date of birth: required, 13 and over only.** An under-13 date is asked
  to be confirmed ("Is {date} right?") so a typing slip costs nothing; a
  confirmed one deletes the account and everything FAM holds about it
  (decision 1) - at activation, or when the date is corrected later.
* **Location: optional** - the profile's existing place, which shows on the
  profile (decision 3). **Gender: optional** - one of five choices, no text
  box (decision 4).
* **Counted by default** (decision 2): finishing the step records a yes to
  the `demographics` consent, whose notice the screen shows; Settings' switch
  records a no.

An API before a screen ([api-before-screen]): this module and its two
endpoints are the whole step; a client draws them. **Old clients keep
working** ([old-clients]): nothing is refused to an account that has not
done the step - an old client never shows it, so its listener is simply not
counted (no yes is ever recorded) until a client that shows it is used.
"""
from __future__ import annotations

from typing import Optional

#: What a client shows, in one place so every client says the same thing.
TITLE_FIRST = "A few things before you start"
TITLE_AGAIN = "One more thing"
UNDER_AGE = ("FAM is for people 13 and older, so we can't let you in. "
             "We've deleted your account.")
HELP = {
    "birth_date": "To check you're 13 or older. Never shown to anyone.",
    "location": "Shows on your profile, and helps FAM find stories near you. Optional.",
    "gender": "Never shown to anyone. Optional.",
}
GENDER_LABELS = {"man": "Man", "woman": "Woman", "non-binary": "Non-binary",
                 "self-describe": "Self-describe",
                 "prefer-not-to-say": "Prefer not to say"}


def confirm_text(birth_date: str) -> str:
    """The question asked before an under-13 date deletes an account."""
    import datetime

    day = datetime.date.fromisoformat(birth_date[:10])
    return f"Is {day:%B} {day.day}, {day.year} right?"


def needed(account: Optional[dict], answer: Optional[dict]) -> bool:
    """Whether this account has the step still to do: an account let into
    the app with no date of birth, or never shown the counting notice."""
    if not account or account.get("status") == "waitlisted":
        return False
    return not account.get("birth_date") or answer is None


def state(account: dict, location: dict, answer: Optional[dict]) -> dict:
    """`GET /api/activation`: what the screen draws and fills itself from."""
    import accounts as accounts_mod
    import consent as consent_mod

    first = not account.get("birth_date") and answer is None
    return {
        "needed": needed(account, answer),
        "title": TITLE_FIRST if first else TITLE_AGAIN,
        "birth_date": account.get("birth_date") or "",
        "gender": account.get("gender") or "",
        "location": location,
        "genders": [{"id": g, "label": GENDER_LABELS[g]} for g in accounts_mod.GENDERS],
        "help": HELP,
        "min_age": accounts_mod.MIN_AGE,
        "counting": consent_mod.describe_demographics(answer),
    }
