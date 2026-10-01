"""The pre-launch waitlist (WAITLIST.md).

**One database, one account.** Joining the waitlist creates a real FAM account
- the same row in `accounts.db` the app signs in with - with `status =
'waitlisted'`. Granting access flips that one column to 'active'. Nothing is
copied anywhere at launch, because a copy step is where data gets lost.

This module owns what the waitlist adds to that row: who invited whom, each
person's own invite code, their place in line, the access cutoff an admin sets,
and the outbox that carries every call to Viral Loops. It shares the accounts
store's connection rather than opening a file of its own: the columns live on
the accounts table (see `accounts.py`), and a second handle on the same file
would be two writers for no reason.

**Place in line is counted here, never asked of the vendor.** One source,
used everywhere: the status page, the admin table and "grant the top N" all
read `ordered()`. Counting it locally means the page still works while Viral
Loops is unreachable - on staging, where every outbound connection is refused
by design, and on production during an outage - and the admin table can never
disagree with what a listener was told.

The order is the leaderboard a referral waitlist promises: more people brought
in is further up, and among equals whoever joined first is ahead. Invites are
counted from `referred_by`, i.e. from accounts that actually exist.
"""
from __future__ import annotations

import logging
import re
import secrets
import time
from typing import Optional

log = logging.getLogger("fam.waitlist")

WAITLISTED = "waitlisted"
ACTIVE = "active"
STATUSES = (WAITLISTED, ACTIVE)

#: The URL parameter a referral link carries. Viral Loops' own referral links
#: use `referralCode`, so a link generated from their dashboard lands the same
#: way one of ours does.
REFERRAL_PARAM = "referralCode"

#: Codes are ours: short, unambiguous, URL-safe. No 0/O or 1/l/I.
_CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CODE_LENGTH = 8
_CODE_RE = re.compile(r"^[a-z0-9]{4,32}$")

#: How long a failed Viral Loops call waits before its next attempt, by
#: attempt number. Capped at the last value: a vendor outage is retried every
#: hour for as long as it lasts rather than given up on - a signup is never
#: dropped because the vendor was down (WAITLIST.md §3b).
RETRY_DELAYS = (60, 300, 900, 3600)


class WaitlistError(ValueError):
    """A request the waitlist refuses, with a sentence a person can read."""


def clean_code(code: str) -> str:
    code = str(code or "").strip().lower()
    return code if _CODE_RE.match(code) else ""


def parse_unlocks(raw: str) -> list[int]:
    """`WAITLIST_UNLOCKS` ("1,3,5") as a sorted list of distinct invite counts."""
    out = set()
    for part in str(raw or "").split(","):
        part = part.strip()
        if part.isdigit() and int(part) > 0:
            out.add(int(part))
    return sorted(out)


def next_unlock(invites: int, milestones: list[int]) -> dict:
    """Where this person stands against the unlock tiers.

    `remaining` is None once every tier is reached - the page then says so
    rather than counting down to nothing.
    """
    ahead = [m for m in milestones if m > invites]
    reached = [m for m in milestones if m <= invites]
    return {"milestones": milestones, "reached": reached,
            "next_at": ahead[0] if ahead else None,
            "remaining": (ahead[0] - invites) if ahead else None}


class Waitlist:
    """The waitlist half of the accounts store. See the module docstring."""

    def __init__(self, accounts) -> None:
        self.accounts = accounts
        with self._conn() as conn:
            # Admin-set numbers, one row each. Only `access_cutoff` today.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS waitlist_settings (
                       name  TEXT PRIMARY KEY,
                       value TEXT NOT NULL
                   )"""
            )
            # Every call to Viral Loops goes through here, so a vendor outage
            # is a queue that drains later rather than a lost signup.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS waitlist_outbox (
                       id         INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id    TEXT NOT NULL,
                       action     TEXT NOT NULL,
                       created    REAL NOT NULL,
                       attempts   INTEGER NOT NULL DEFAULT 0,
                       next_at    REAL NOT NULL DEFAULT 0,
                       last_error TEXT NOT NULL DEFAULT '',
                       done_at    REAL NOT NULL DEFAULT 0
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS waitlist_outbox_due"
                         " ON waitlist_outbox(done_at, next_at)")

    def _conn(self):
        return self.accounts._conn()

    # --- joining ----------------------------------------------------------

    def _mint_code(self) -> str:
        for _ in range(20):
            code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if not self.user_for_code(code):
                return code
        raise WaitlistError("Could not make an invite code. Try again.")

    def ensure_code(self, user_id: str) -> str:
        """This account's invite code, minted the first time it is asked for.

        Active accounts get one too: somebody already in the app can bring
        people onto the list, and their invitees still become their friends.
        """
        row = self._conn().execute(
            "SELECT referral_code FROM accounts WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise WaitlistError("There is no account to make an invite code for.")
        if row[0]:
            return row[0]
        code = self._mint_code()
        self._conn().execute(
            "UPDATE accounts SET referral_code = ? WHERE user_id = ?"
            " AND referral_code = ''", (code, user_id))
        return self._conn().execute(
            "SELECT referral_code FROM accounts WHERE user_id = ?", (user_id,)
        ).fetchone()[0]

    def user_for_code(self, code: str) -> str:
        code = clean_code(code)
        if not code:
            return ""
        row = self._conn().execute(
            "SELECT user_id FROM accounts WHERE referral_code = ?", (code,)
        ).fetchone()
        return row[0] if row else ""

    def join(self, user_id: str, referral_code: str = "",
             at: float = 0.0, referrals_per_hour: int = 0) -> dict:
        """Record the waitlist half of a new account: its code and its inviter.

        The account itself was created by the ordinary sign-up (so it is the
        one the app later opens with). `referred_by` is set here once and
        never again: a second call finds it set and leaves it alone. A code
        that does not exist, or that is the joiner's own, is ignored rather
        than refused - a stale link must never stop somebody joining.

        **A code credits at most `referrals_per_hour` new invites an hour**
        (0 = no cap). Invites decide who is let in first, and nothing about a
        signup is verified - no email is sent - so a script with one code and
        a list of made-up addresses could otherwise buy the front of the line.
        The cap is keyed on the code because the code is the one thing such a
        script cannot vary; an address can be forged per request (see
        `app._limit_key`). Past the cap the person still joins, just without
        moving their inviter up, and `capped` says so.

        Returns {"referrer": id or "", "code": their own code, "capped": bool}.
        """
        now = at or time.time()
        account = self.accounts.account(user_id)
        if not account:
            raise WaitlistError("There is no account to put on the waitlist.")
        code = self.ensure_code(user_id)
        referrer = self.user_for_code(referral_code)
        if referrer == user_id:
            referrer = ""
        capped = False
        if referrer and referrals_per_hour > 0:
            recent = self._conn().execute(
                "SELECT COUNT(*) FROM accounts WHERE referred_by = ?"
                " AND created >= ?", (referrer, now - 3600)).fetchone()[0]
            if recent >= referrals_per_hour:
                capped, referrer = True, ""
        if referrer:
            self._conn().execute(
                "UPDATE accounts SET referred_by = ? WHERE user_id = ?"
                " AND referred_by = ''", (referrer, user_id))
        self._conn().execute(
            "UPDATE accounts SET waitlist_joined_at = ? WHERE user_id = ?"
            " AND waitlist_joined_at = 0 AND status = ?",
            (now, user_id, WAITLISTED))
        row = self._conn().execute(
            "SELECT referred_by FROM accounts WHERE user_id = ?", (user_id,)
        ).fetchone()
        return {"referrer": row[0] if row else "", "code": code,
                "capped": capped}

    # --- reading ----------------------------------------------------------

    def status_of(self, user_id: str) -> str:
        """'active', 'waitlisted', or "" when there is no account."""
        if not user_id:
            return ""
        row = self._conn().execute(
            "SELECT status FROM accounts WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row[0] if row else ""

    def waitlisted_among(self, user_ids) -> set[str]:
        """Which of these ids are waitlisted. One query for a whole list."""
        ids = [u for u in dict.fromkeys(user_ids) if u]
        found: set[str] = set()
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" * len(chunk))
            rows = self._conn().execute(
                f"SELECT user_id FROM accounts WHERE status = ?"
                f" AND user_id IN ({marks})", (WAITLISTED, *chunk)).fetchall()
            found.update(r[0] for r in rows)
        return found

    def invites_of(self, user_id: str) -> list[str]:
        """Everyone who joined with this person's code, oldest first."""
        rows = self._conn().execute(
            "SELECT user_id FROM accounts WHERE referred_by = ?"
            " ORDER BY created", (user_id,)).fetchall()
        return [r[0] for r in rows]

    _ORDERED = (
        "SELECT a.user_id, a.email, a.phone, a.display_name, a.created,"
        "       a.waitlist_joined_at, a.referral_code, a.referred_by,"
        "       COALESCE(i.n, 0) AS invites"
        " FROM accounts a"
        " LEFT JOIN (SELECT referred_by, COUNT(*) AS n FROM accounts"
        "            WHERE referred_by != '' GROUP BY referred_by) i"
        "   ON i.referred_by = a.user_id"
        " WHERE a.status = ?"
        " ORDER BY invites DESC,"
        "          CASE WHEN a.waitlist_joined_at > 0 THEN a.waitlist_joined_at"
        "               ELSE a.created END ASC,"
        "          a.user_id ASC"
    )

    def ordered(self) -> list[dict]:
        """Every waitlisted account, in line. Place is the 1-based index."""
        rows = self._conn().execute(self._ORDERED, (WAITLISTED,)).fetchall()
        return [{"place": n, "user_id": r[0], "email": r[1], "phone": r[2],
                 "display_name": r[3], "created": r[4],
                 "joined": r[5] or r[4], "referral_code": r[6],
                 "referred_by": r[7], "invites": int(r[8])}
                for n, r in enumerate(rows, start=1)]

    def place_of(self, user_id: str) -> Optional[int]:
        """Their place in line, or None when they are not waitlisted.

        Counted rather than read off `ordered()`: the status page asks on
        every load, and sorting the whole line to find one person is the
        cost that grows with the line. Same order as `ordered()` - more
        invites first, then earlier join, then id - which a test pins.
        """
        me = self._conn().execute(
            "SELECT a.status, CASE WHEN a.waitlist_joined_at > 0"
            "  THEN a.waitlist_joined_at ELSE a.created END,"
            " (SELECT COUNT(*) FROM accounts r WHERE r.referred_by = a.user_id)"
            " FROM accounts a WHERE a.user_id = ?", (user_id,)).fetchone()
        if not me or me[0] != WAITLISTED:
            return None
        _status, joined, invites = me
        ahead = self._conn().execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT a.user_id,"
            "    CASE WHEN a.waitlist_joined_at > 0 THEN a.waitlist_joined_at"
            "         ELSE a.created END AS joined,"
            "    (SELECT COUNT(*) FROM accounts r WHERE r.referred_by = a.user_id)"
            "      AS invites"
            "  FROM accounts a WHERE a.status = ?)"
            " WHERE invites > ? OR (invites = ? AND (joined < ?"
            "   OR (joined = ? AND user_id < ?)))",
            (WAITLISTED, invites, invites, joined, joined, user_id)).fetchone()[0]
        return int(ahead) + 1

    def waitlisted_count(self) -> int:
        return int(self._conn().execute(
            "SELECT COUNT(*) FROM accounts WHERE status = ?", (WAITLISTED,)
        ).fetchone()[0])

    def counts(self, now: float = 0.0) -> dict:
        now = now or time.time()
        day = now - 86400
        rows = dict(self._conn().execute(
            "SELECT status, COUNT(*) FROM accounts GROUP BY status").fetchall())
        today = self._conn().execute(
            "SELECT COUNT(*) FROM accounts WHERE waitlist_joined_at >= ?",
            (day,)).fetchone()[0]
        return {"waitlisted": int(rows.get(WAITLISTED, 0)),
                "active": int(rows.get(ACTIVE, 0)),
                "signups_today": int(today)}

    # --- the cutoff -------------------------------------------------------

    def cutoff(self) -> int:
        """How many places at the front of the line are "the next batch".

        Set by an admin. 0 until they set it, which means nobody is told they
        are in the next batch before anybody has decided how big it is.
        """
        row = self._conn().execute(
            "SELECT value FROM waitlist_settings WHERE name = 'access_cutoff'"
        ).fetchone()
        try:
            return max(0, int(row[0])) if row else 0
        except ValueError:
            return 0

    def set_cutoff(self, value: int) -> int:
        value = int(value)
        if value < 0:
            raise WaitlistError("The cutoff is a number of places, 0 or more.")
        self._conn().execute(
            "INSERT INTO waitlist_settings (name, value) VALUES ('access_cutoff', ?)"
            " ON CONFLICT (name) DO UPDATE SET value = excluded.value",
            (str(value),))
        return value

    # --- granting ---------------------------------------------------------

    def grant(self, user_ids, at: float = 0.0) -> list[str]:
        """Let these people in. Returns the ids that actually changed.

        Only 'waitlisted' rows move, so granting twice is harmless and an id
        with no account is ignored. Each one granted queues a `flag` for Viral
        Loops, which takes them off its leaderboard without deleting them.
        """
        now = at or time.time()
        changed = []
        for user_id in dict.fromkeys(user_ids):
            cur = self._conn().execute(
                "UPDATE accounts SET status = ?, access_granted_at = ?"
                " WHERE user_id = ? AND status = ?",
                (ACTIVE, now, user_id, WAITLISTED))
            if cur.rowcount:
                changed.append(user_id)
                self.enqueue(user_id, "flag", at=now)
        return changed

    def grant_top(self, n: int, at: float = 0.0) -> list[str]:
        if int(n) <= 0:
            raise WaitlistError("Grant at least one place.")
        return self.grant([r["user_id"] for r in self.ordered()[:int(n)]], at=at)

    # --- the Viral Loops outbox ------------------------------------------

    def enqueue(self, user_id: str, action: str, at: float = 0.0) -> int:
        now = at or time.time()
        cur = self._conn().execute(
            "INSERT INTO waitlist_outbox (user_id, action, created, next_at)"
            " VALUES (?, ?, ?, ?)", (user_id, action, now, now))
        return int(cur.lastrowid)

    def due(self, now: float = 0.0, limit: int = 50) -> list[dict]:
        now = now or time.time()
        rows = self._conn().execute(
            "SELECT id, user_id, action, attempts FROM waitlist_outbox"
            " WHERE done_at = 0 AND next_at <= ? ORDER BY id LIMIT ?",
            (now, int(limit))).fetchall()
        return [{"id": r[0], "user_id": r[1], "action": r[2], "attempts": r[3]}
                for r in rows]

    def has_action(self, user_id: str, action: str) -> bool:
        """Whether this call was ever queued for this account, done or not."""
        return bool(self._conn().execute(
            "SELECT 1 FROM waitlist_outbox WHERE user_id = ? AND action = ?"
            " LIMIT 1", (user_id, action)).fetchone())

    def mark_done(self, item_id: int, at: float = 0.0) -> None:
        self._conn().execute(
            "UPDATE waitlist_outbox SET done_at = ?, last_error = ''"
            " WHERE id = ?", (at or time.time(), item_id))

    def mark_failed(self, item_id: int, error: str, at: float = 0.0) -> None:
        now = at or time.time()
        row = self._conn().execute(
            "SELECT attempts FROM waitlist_outbox WHERE id = ?", (item_id,)
        ).fetchone()
        attempts = (row[0] if row else 0) + 1
        delay = RETRY_DELAYS[min(attempts, len(RETRY_DELAYS)) - 1]
        self._conn().execute(
            "UPDATE waitlist_outbox SET attempts = ?, next_at = ?, last_error = ?"
            " WHERE id = ?", (attempts, now + delay, str(error)[:500], item_id))

    def mark_given_up(self, item_id: int, error: str, at: float = 0.0) -> None:
        """A call the vendor refused outright (a 4xx other than 429): retrying
        cannot change the answer, so it is finished - with the error kept, so
        it stays visible on the admin page rather than vanishing."""
        self._conn().execute(
            "UPDATE waitlist_outbox SET done_at = ?, attempts = attempts + 1,"
            " last_error = ? WHERE id = ?",
            (at or time.time(), "refused: " + str(error)[:490], item_id))

    def outbox_summary(self) -> dict:
        row = self._conn().execute(
            "SELECT COUNT(*), COALESCE(MAX(attempts), 0) FROM waitlist_outbox"
            " WHERE done_at = 0").fetchone()
        last = self._conn().execute(
            "SELECT last_error FROM waitlist_outbox WHERE done_at = 0"
            " AND last_error != '' ORDER BY id DESC LIMIT 1").fetchone()
        refused = self._conn().execute(
            "SELECT COUNT(*) FROM waitlist_outbox WHERE done_at > 0"
            " AND last_error LIKE 'refused: %'").fetchone()[0]
        last_refused = self._conn().execute(
            "SELECT last_error FROM waitlist_outbox WHERE done_at > 0"
            " AND last_error LIKE 'refused: %' ORDER BY id DESC LIMIT 1").fetchone()
        return {"pending": int(row[0]), "max_attempts": int(row[1]),
                "last_error": last[0] if last else "",
                "refused": int(refused),
                "last_refused": last_refused[0] if last_refused else ""}

    def set_vendor_ids(self, user_id: str, referral_code: str = "",
                       participant_id: str = "") -> None:
        self._conn().execute(
            "UPDATE accounts SET"
            " vl_referral_code = CASE WHEN ? != '' THEN ? ELSE vl_referral_code END,"
            " vl_participant_id = CASE WHEN ? != '' THEN ? ELSE vl_participant_id END"
            " WHERE user_id = ?",
            (referral_code, referral_code, participant_id, participant_id, user_id))

    def vendor_view(self, user_id: str) -> Optional[dict]:
        """What a Viral Loops call needs about one account."""
        row = self._conn().execute(
            "SELECT a.email, a.display_name, a.vl_referral_code,"
            "       COALESCE(r.vl_referral_code, '')"
            " FROM accounts a LEFT JOIN accounts r ON r.user_id = a.referred_by"
            " WHERE a.user_id = ?", (user_id,)).fetchone()
        if not row:
            return None
        return {"email": row[0], "name": row[1], "vl_referral_code": row[2],
                "referrer_vl_code": row[3]}

    def forget(self, user_id: str) -> int:
        """Account deletion: its queued vendor calls, and it as an inviter.

        The account row itself is `Accounts.delete_account`'s. People it
        invited keep their accounts; they simply no longer name an inviter.
        """
        removed = self._conn().execute(
            "DELETE FROM waitlist_outbox WHERE user_id = ?", (user_id,)).rowcount
        self._conn().execute(
            "UPDATE accounts SET referred_by = '' WHERE referred_by = ?", (user_id,))
        return int(removed or 0)
