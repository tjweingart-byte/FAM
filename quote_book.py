"""The quote book: every market reading FAM has been given, kept (§265).

**The most recent reading is never discarded.** Until §265 a market quote was
used once, in the episode that asked for it, and only if it was under five
minutes old - so after the close every quote was withheld, the 05:00 DailyFAM
edition never had one, and the two-hourly sweep (§191) threw away every quote
under its move threshold. A listener asking about the Nasdaq at 7pm heard "I
have no reading on where it closed" from a deployment that had read it several
times that day.

So every quote that reaches FAM - the sweep's watchlist and every episode's
lookup - is written here, one row per symbol per trading session, the latest
reading of that session winning. An episode is then given the newest reading
*and* the sessions before it, each with its own date: where something closed
on Tuesday is a fact about Tuesday, true forever, and the thing a question
about "Tuesday's move" needs.

A session is the exchange's day (New York), never the listener's: the close
is at 16:00 Eastern wherever the listener is.

Not personal and not derived from the event log, so a wipe leaves it alone
(the same as the GDELT copy). Bounded by `KEEP_SESSIONS` per symbol.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import db as store_db
from paths import data_path

log = logging.getLogger(__name__)

#: The exchange's clock. A session is a New York trading day.
EXCHANGE_ZONE = ZoneInfo("America/New_York")
#: When a reading counts as the session's close: at or after 16:00 Eastern.
CLOSE_HOUR = 16
#: How many sessions are kept per symbol. A month of trading days and a bit.
KEEP_SESSIONS = 30


def session_of(stamp: float) -> str:
    """The trading day a quote's own timestamp belongs to, as YYYY-MM-DD."""
    return datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone(
        EXCHANGE_ZONE).strftime("%Y-%m-%d")


def is_close(stamp: float) -> bool:
    """Was this reading taken at or after the close of its session?"""
    when = datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone(EXCHANGE_ZONE)
    return when.hour >= CLOSE_HOUR


def _num(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


class QuoteBook:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("QUOTE_BOOK_DB", "quote_book.db", path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS quotes (
                       symbol   TEXT NOT NULL,
                       session  TEXT NOT NULL,
                       t        REAL NOT NULL,
                       c        REAL NOT NULL,
                       d        REAL,
                       dp       REAL,
                       pc       REAL,
                       o        REAL,
                       h        REAL,
                       l        REAL,
                       seen_at  REAL NOT NULL,
                       PRIMARY KEY (symbol, session)
                   )""")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = store_db.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def record(self, symbol: str, data: dict, now: Optional[float] = None) -> bool:
        """Keep one Finnhub `/quote` answer. True when it was new or newer.

        Finnhub's fields: `c` price, `d` change, `dp` percent change, `pc`
        previous close, `o`/`h`/`l` the session's open, high and low, `t` the
        quote's own time. A reading without a price or a time is not a
        reading. Never raises: losing one row must not cost an episode.
        """
        symbol = str(symbol or "").strip().upper()
        price, stamp = _num((data or {}).get("c")), _num((data or {}).get("t"))
        if not symbol or not price or not stamp:
            return False
        session = session_of(stamp)
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT t FROM quotes WHERE symbol = ? AND session = ?",
                (symbol, session)).fetchone()
            if row is not None and float(row[0]) >= stamp:
                return False
            values = (symbol, session, stamp, price, _num(data.get("d")),
                      _num(data.get("dp")), _num(data.get("pc")),
                      _num(data.get("o")), _num(data.get("h")),
                      _num(data.get("l")), float(now or time.time()))
            if row is None:
                conn.execute(
                    "INSERT INTO quotes (symbol, session, t, c, d, dp, pc, o, h, "
                    "l, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
            else:
                conn.execute(
                    "UPDATE quotes SET t = ?, c = ?, d = ?, dp = ?, pc = ?, "
                    "o = ?, h = ?, l = ?, seen_at = ? WHERE symbol = ? AND "
                    "session = ?", values[2:] + values[:2])
            self._trim(conn, symbol)
            return True
        except Exception:  # noqa: BLE001 - a lost row is not a lost episode
            log.exception("quote book: could not keep %s", symbol)
            return False

    def _trim(self, conn, symbol: str) -> None:
        rows = conn.execute(
            "SELECT session FROM quotes WHERE symbol = ? ORDER BY session DESC",
            (symbol,)).fetchall()
        for row in rows[KEEP_SESSIONS:]:
            conn.execute("DELETE FROM quotes WHERE symbol = ? AND session = ?",
                         (symbol, row[0]))

    def sessions(self, symbol: str, limit: int = 5) -> list:
        """The newest `limit` sessions for a symbol, newest first, as dicts."""
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return []
        try:
            rows = self._conn().execute(
                "SELECT session, t, c, d, dp, pc, o, h, l FROM quotes "
                "WHERE symbol = ? ORDER BY session DESC LIMIT ?",
                (symbol, int(limit))).fetchall()
        except Exception:  # noqa: BLE001
            log.exception("quote book: could not read %s", symbol)
            return []
        names = ("session", "t", "c", "d", "dp", "pc", "o", "h", "l")
        return [dict(zip(names, tuple(row))) for row in rows]

    def recent_symbols(self, days: float = 7.0, limit: int = 24,
                       now: Optional[float] = None) -> list:
        """Symbols an episode or the sweep read in the last `days`, newest first.

        What the sweep follows beyond its watchlist (§265): a listener who
        follows Oracle has Oracle's every session kept from then on, not only
        the ones somebody happened to ask about.
        """
        since = float(now or time.time()) - days * 86400
        try:
            rows = self._conn().execute(
                "SELECT symbol, MAX(seen_at) AS seen FROM quotes "
                "WHERE seen_at >= ? GROUP BY symbol ORDER BY seen DESC LIMIT ?",
                (since, int(limit))).fetchall()
        except Exception:  # noqa: BLE001
            log.exception("quote book: could not list symbols")
            return []
        return [str(row[0]) for row in rows]

    def count(self) -> int:
        try:
            return int(self._conn().execute(
                "SELECT COUNT(*) FROM quotes").fetchone()[0])
        except Exception:  # noqa: BLE001
            return 0


_BOOK: Optional[QuoteBook] = None
_LOCK = threading.Lock()


def book() -> QuoteBook:
    global _BOOK
    with _LOCK:
        if _BOOK is None:
            _BOOK = QuoteBook()
        return _BOOK


def reset() -> None:
    """Forget the open handle (tests that point `QUOTE_BOOK_DB` elsewhere)."""
    global _BOOK
    with _LOCK:
        _BOOK = None


def record(symbol: str, data: dict) -> bool:
    try:
        return book().record(symbol, data)
    except Exception:  # noqa: BLE001 - the store failing to open costs nothing
        log.exception("quote book: unavailable")
        return False


def sessions(symbol: str, limit: int = 5) -> list:
    try:
        return book().sessions(symbol, limit)
    except Exception:  # noqa: BLE001
        log.exception("quote book: unavailable")
        return []


def recent_symbols(days: float = 7.0, limit: int = 24) -> list:
    try:
        return book().recent_symbols(days, limit)
    except Exception:  # noqa: BLE001
        log.exception("quote book: unavailable")
        return []
