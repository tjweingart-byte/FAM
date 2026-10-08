"""Echoes, and the little identity a listener needs to make one.

An **echo** is a deliberate act: someone finishes an episode and pushes it to
the people who follow them. The episode may well have reached those people
anyway - scripts are shared, so a popular question is already sitting in the
cache Explore reads from - but an echo changes what the card *says*. It stops
being "someone asked this" and becomes "Rachel sent you this", which is a
different reason to press play.

That is the whole design: an echo costs no generation at all. It is a row
pointing at a query that already exists, so the social layer is free in exactly
the way the browse surfaces are.

Identity is the minimum an echo needs to make sense: a name to put on it and a
handle to find it by. There are still no accounts - this is keyed on the same
anonymous per-device id as everything else, and is documented as such on the
profile page rather than dressed up as a login.

`people` is nonetheless the app's **listener table**, and `seen()` is what makes
it one. Until it existed a row appeared only when someone chose a name, so the
app could not answer "who is out there" or "when were they last here" for the
overwhelming majority of its listeners - they had a taste profile and no
record. Two things need that record. Recency: a daily feed has to know a
listener is still listening, and a decayed event log tells you what they liked,
not whether they came back. And identity itself: when accounts arrive, the work
is attaching credentials to rows that already exist, rather than inventing a
user table underneath live data.

What it deliberately does not store is anything nobody has supplied - no email,
no device fingerprint, no invented counters. `first_seen` and `last_seen` are
facts the server observed; `name` and `handle` are facts the listener typed.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Optional

from paths import data_path

log = logging.getLogger(__name__)

MAX_NAME = 40
MAX_HANDLE = 24
#: How big a profile picture may be, as a `data:` URL.
#:
#: Stored inline rather than as a file, because a file means a writable media
#: directory, a URL that serves it, a cache header and a deletion path - four
#: new things to get wrong for one small square. At 256x256 JPEG a picture is
#: comfortably under this; a client that sends the original off a phone camera
#: is refused with a sentence telling it to downscale, rather than quietly
#: filling the database.
MAX_AVATAR = 96_000
_AVATAR_PREFIX = ("data:image/jpeg;base64,", "data:image/png;base64,",
                  "data:image/webp;base64,")
_HANDLE_OK = re.compile(r"^[a-z0-9_.]{2,24}$")


class SocialError(ValueError):
    """Something the listener did wrong, phrased so it can be shown to them."""


@dataclass
class Echo:
    id: int
    user_id: str
    query: str
    title: str
    minutes: int
    thread: str
    at: float

    def as_dict(self, name: str = "", handle: str = "") -> dict:
        return {
            "id": self.id, "query": self.query, "title": self.title,
            "minutes": self.minutes, "thread": self.thread, "at": self.at,
            "by": name, "handle": handle,
        }


def clean_avatar(avatar: str) -> str:
    """The picture as it will be stored, or "" for none.

    Only a base64 `data:` URL of an image type every browser and iOS can
    decode. A remote URL is refused on purpose: a profile picture fetched from
    somewhere else is a request every viewer's device makes to a third party,
    which is a tracking pixel wearing a face.
    """
    avatar = str(avatar or "").strip()
    if not avatar:
        return ""
    if not avatar.startswith(_AVATAR_PREFIX):
        raise SocialError("A profile picture has to be an image from your device.")
    if len(avatar) > MAX_AVATAR:
        raise SocialError("That picture is too big — try a smaller one.")
    return avatar


def clean_handle(handle: str) -> str:
    handle = str(handle).strip().lstrip("@").lower()[:MAX_HANDLE]
    if not _HANDLE_OK.match(handle):
        raise SocialError("A handle is 2-24 letters, numbers, dots or underscores.")
    return handle


#: How long a vibe's caption may be (10.5 packet #9): a line over a story,
#: not a post.
MAX_CAPTION = 150
#: How long a comment may be (10.5 packet #8).
MAX_COMMENT = 500


def _clean_words(text: str, limit: int) -> str:
    """Whitespace folded, cut to `limit`, and slurs removed the way an
    episode's are (`content_filter.scrub`, §171): swearing stays."""
    text = " ".join(str(text or "").split())[:limit]
    if not text:
        return ""
    try:
        import content_filter
        return content_filter.scrub(text)
    except Exception:  # noqa: BLE001 - the filter failing is not a lost post
        log.exception("could not run the slur filter over a post")
        return text


def clean_caption(caption: str) -> str:
    return _clean_words(caption, MAX_CAPTION)


# --- a vibe composed like a story (10.6 packet #2) --------------------------
#
# The vibe sheet became a story editor: the episode's picture, framed and
# pinched to size, a caption in a chosen face and size, stickers, and people
# tagged with @. All of it is *where things go* - a small JSON layout beside
# the row, never an image: the viewer draws the story from it, so a story
# costs a few hundred bytes and its words stay words. Every field is clamped
# here, because a layout is drawn on other people's screens.

#: The caption's typefaces. The three FAM loads (`typefaces`) and two the
#: phone already has, so a choice costs no font link.
STORY_FONTS = ("classic", "strong", "type", "serif", "script")
STORY_FRAMES = ("card", "round", "circle", "full")
#: The stickers on offer. A fixed set rather than any text: a sticker is
#: drawn big on somebody else's screen, and free text there is a caption.
STORY_STICKERS = (
    "\U0001F525", "\U0001F92F", "\U0001F602", "\U0001F64C", "\U0001F440",
    "\U0001F4AF", "\u2764\uFE0F", "\U0001F3A7", "\U0001F9E0", "\U0001F44F",
    "\U0001F62E", "\u2728", "\U0001F3C6", "\U0001F4C8", "\U0001F30D", "\U0001F914",
    "MUST LISTEN", "HOT TAKE", "TIL", "SO GOOD", "NEW", "LISTEN TO THIS",
)
MAX_STICKERS = 12
MAX_TAGS = 10
#: Who a vibe is for: "" everybody who follows, "close" the poster's close
#: friends only.
AUDIENCES = ("", "close")


def _num(value, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return round(max(lo, min(hi, v)), 4)


#: What `thumbnails.url_for` makes: a node's picture and its version.
_THUMB_PATH = re.compile(r"^/api/thumb/[A-Za-z0-9_.%~-]+(\?v=\d+)?$")


def clean_thumb(url: str) -> str:
    """The episode's picture, as a path on this server or "".

    Never a remote address, for the reason a profile picture is never one:
    every viewer's phone would fetch it from somebody else.
    """
    url = str(url or "").strip()[:300]
    # Only a picture route, never any other path: every viewer's browser
    # requests this with their own cookies, so `/api/audio?...` here would
    # spend their quota on whatever the poster chose.
    return url if _THUMB_PATH.match(url) else ""


def clean_style(style) -> dict:
    """A story's layout, every field clamped; {} for none."""
    if not isinstance(style, dict) or not style:
        return {}
    out: dict = {
        "font": style.get("font") if style.get("font") in STORY_FONTS else "classic",
        "size": int(_num(style.get("size"), 14, 48, 24)),
        "cx": _num(style.get("cx"), 0.05, 0.95, 0.5),
        "cy": _num(style.get("cy"), 0.05, 0.95, 0.78),
        "frame": style.get("frame") if style.get("frame") in STORY_FRAMES else "card",
        "zoom": _num(style.get("zoom"), 0.4, 2.5, 1.0),
        "tx": _num(style.get("tx"), -0.5, 0.5, 0.0),
        "ty": _num(style.get("ty"), -0.5, 0.5, 0.0),
        "thumb": clean_thumb(style.get("thumb")),
    }
    stickers = []
    raw = style.get("stickers") or []
    for item in (raw if isinstance(raw, list) else [])[:MAX_STICKERS * 4]:
        if len(stickers) >= MAX_STICKERS:
            break
        if not isinstance(item, dict) or item.get("k") not in STORY_STICKERS:
            continue
        stickers.append({"k": item["k"],
                         "x": _num(item.get("x"), 0, 1, 0.5),
                         "y": _num(item.get("y"), 0, 1, 0.5),
                         "s": _num(item.get("s"), 0.4, 3, 1),
                         "r": _num(item.get("r"), -180, 180, 0)})
    out["stickers"] = stickers
    return out


def clean_tags(tags) -> list[dict]:
    """[{user_id, x, y}] - who is tagged and where the tag sits.

    Ids here are the server's own: the endpoint resolves each handle the
    listener typed to somebody in their graph before this sees it.
    """
    out, seen = [], set()
    for item in (tags or [])[:MAX_TAGS]:
        uid = str((item or {}).get("user_id") or "")
        if not uid or uid in seen:
            continue
        seen.add(uid)
        out.append({"user_id": uid, "x": _num(item.get("x"), 0, 1, 0.5),
                    "y": _num(item.get("y"), 0, 1, 0.3)})
    return out


class SocialStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("SOCIAL_DB", "social.db", path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS people (
                       user_id TEXT PRIMARY KEY,
                       name    TEXT NOT NULL DEFAULT '',
                       handle  TEXT NOT NULL DEFAULT '',
                       joined  REAL NOT NULL
                   )"""
            )
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS people_handle"
                         " ON people(handle) WHERE handle != ''")
            # Added after the table shipped. `joined` already records the
            # first sighting, so it doubles as first_seen and is not renamed:
            # a rename would mean rewriting every reader for no new fact.
            try:
                conn.execute("ALTER TABLE people ADD COLUMN"
                             " last_seen REAL NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # already there
            conn.execute("CREATE INDEX IF NOT EXISTS people_last_seen"
                         " ON people(last_seen)")
            # The profile picture, added after the table shipped, for the same
            # reason as `last_seen`: widened rather than recreated, because a
            # rebuild would drop the follow graph to gain one column.
            try:
                conn.execute("ALTER TABLE people ADD COLUMN"
                             " avatar TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # already there
            # When this listener last looked at who follows them. A new
            # follower is one whose `follows.at` is later than this, which
            # makes "how many are new" a query over data that already existed
            # rather than a second table with its own read state to get wrong.
            #
            # Nought means never looked, so every follower a listener already
            # has reads as new the first time they open the tab after this
            # ships. That is the right direction: the alternative is defaulting
            # to *now* and silently swallowing followers they were never told
            # about.
            try:
                conn.execute("ALTER TABLE people ADD COLUMN"
                             " followers_seen REAL NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # already there
            conn.execute(
                """CREATE TABLE IF NOT EXISTS echoes (
                       id      INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id TEXT NOT NULL,
                       query   TEXT NOT NULL,
                       title   TEXT NOT NULL DEFAULT '',
                       minutes INTEGER NOT NULL DEFAULT 0,
                       thread  TEXT NOT NULL DEFAULT '',
                       at      REAL NOT NULL
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS echoes_user ON echoes(user_id, at)")
            # One echo per person per episode: echoing twice is the same
            # statement made twice, not two statements.
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS echoes_once"
                         " ON echoes(user_id, query, minutes)")
            # Counting an episode's vibes (§134) asks by episode, and the index
            # above leads with the listener, so without this every Explore
            # card was a scan of the whole table.
            conn.execute("CREATE INDEX IF NOT EXISTS echoes_episode"
                         " ON echoes(query, minutes)")
            # The follow graph, which the app has been describing for a while
            # without having (CLAUDE.md open problem #6: "What your followers
            # are listening to" ranked co-listener overlap, and the heading
            # promised a social network the app did not have).
            #
            # **Asymmetric, like the copy already says.** Following somebody is
            # a decision one person makes; a "friend" is the mutual case and is
            # derived rather than stored, so there is no request-and-accept
            # state machine to get wrong and no way for the two directions to
            # disagree. Sharing an episode does not require either: you can
            # send one to somebody who does not follow you back, exactly as you
            # can send them a text message.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS follows (
                       follower TEXT NOT NULL,
                       followee TEXT NOT NULL,
                       at       REAL NOT NULL,
                       PRIMARY KEY (follower, followee)
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS follows_followee"
                         " ON follows(followee, at)")
            # Likes and dislikes on an episode (§134, Explore's thumbs). One
            # row per person per episode, keyed like a vibe - `(query,
            # minutes)` - so the three counts on a card are about the same
            # thing. `value` is +1 or -1; taking a thumb back deletes the row
            # rather than writing a zero, so a count is always `COUNT(*)`.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS ratings (
                       user_id TEXT NOT NULL,
                       query   TEXT NOT NULL,
                       minutes INTEGER NOT NULL DEFAULT 0,
                       value   INTEGER NOT NULL,
                       at      REAL NOT NULL,
                       PRIMARY KEY (user_id, query, minutes)
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ratings_episode"
                         " ON ratings(query, minutes)")
            # A vibe's caption (10.5 packet #9): what the person vibing it
            # said about it, shown with the episode when it plays as a story.
            # "" for none, and for every vibe sent before captions existed.
            try:
                conn.execute("ALTER TABLE echoes ADD COLUMN"
                             " caption TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # already there
            # A vibe composed as a story (10.6 packet #2): its layout and the
            # people tagged in it (JSON, "" for a plain vibe), who it is for
            # ("" everybody, "close" close friends only), and whether its
            # poster took it off their story (#1) - which leaves the vibe on
            # their profile, as Instagram's "remove from story" leaves a post.
            for column in ("style TEXT NOT NULL DEFAULT ''",
                           "tags TEXT NOT NULL DEFAULT ''",
                           "audience TEXT NOT NULL DEFAULT ''",
                           "unstoried INTEGER NOT NULL DEFAULT 0"):
                try:
                    conn.execute(f"ALTER TABLE echoes ADD COLUMN {column}")
                except sqlite3.OperationalError:
                    pass  # already there
            # Close friends (10.6 packet #2): the people a "Close Friends"
            # story is for, chosen in Settings. One-way, like a follow - being
            # on somebody's list is their decision and is never shown to you.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS close_friends (
                       owner  TEXT NOT NULL,
                       member TEXT NOT NULL,
                       at     REAL NOT NULL,
                       PRIMARY KEY (owner, member)
                   )"""
            )
            # Comments on an episode (10.5 packet #8): Explore's comments
            # sheet. Keyed like a vibe and a thumb - `(query, minutes)` - so
            # everybody who hears that episode reads the same thread. A reply
            # names its parent; replies are one level deep, the way the
            # owner's picture draws them, so a reply to a reply is filed under
            # the top-level comment it hangs from.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS comments (
                       id        INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id   TEXT NOT NULL,
                       query     TEXT NOT NULL,
                       minutes   INTEGER NOT NULL DEFAULT 0,
                       parent_id INTEGER NOT NULL DEFAULT 0,
                       text      TEXT NOT NULL,
                       at        REAL NOT NULL
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS comments_episode"
                         " ON comments(query, minutes, at)")
            conn.execute("CREATE INDEX IF NOT EXISTS comments_user"
                         " ON comments(user_id)")
            # One like per person per comment; taking it back deletes the row,
            # so a comment's count is always `COUNT(*)`, like a thumb's.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS comment_likes (
                       user_id    TEXT NOT NULL,
                       comment_id INTEGER NOT NULL,
                       at         REAL NOT NULL,
                       PRIMARY KEY (user_id, comment_id)
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS comment_likes_comment"
                         " ON comment_likes(comment_id)")
            # Who has already been announced to whom (§142). The "___ started
            # following you" popup used to remember this in page memory, so
            # every fresh open of the app showed the latest follower again.
            # One row per (listener, follower), with no timestamp condition:
            # once somebody has been told, that person is never announced
            # again - not on the next open, not on another device. Separate
            # from `followers_seen`, which is the Friends badge's and is
            # cleared by opening Friends and nothing else.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS announced (
                       user_id  TEXT NOT NULL,
                       follower TEXT NOT NULL,
                       at       REAL NOT NULL,
                       PRIMARY KEY (user_id, follower)
                   )"""
            )

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    # --- who someone is ---------------------------------------------------

    def person(self, user_id: str) -> dict:
        row = None
        try:
            row = self._conn().execute(
                "SELECT name, handle, joined, last_seen, avatar FROM people"
                " WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        except Exception:
            log.exception("could not read person")
        return {
            "user_id": user_id,
            "name": row[0] if row else "",
            "handle": row[1] if row else "",
            "joined": row[2] if row else 0.0,
            "last_seen": row[3] if row else 0.0,
            "avatar": (row[4] if row else "") or "",
            # Whether the app has ever seen this id before, as opposed to
            # whether they got around to naming themselves. The profile page
            # needs to tell those apart; before `seen()` it could not.
            "known": bool(row),
        }

    def seen(self, user_id: str, at: float = 0.0) -> None:
        """Note that this listener exists and is here now.

        Called on the surfaces that already touch a database, never on the
        generation path: `/api/audio` has one job, and a write in front of the
        first word is the single cost this product refuses. A play still lands
        here, just after the stream has been handed over.

        Failure is swallowed for the same reason every other write in this
        module swallows it - knowing when someone last visited is worth less
        than the visit.
        """
        if not user_id:
            return
        now = at or time.time()
        try:
            self._conn().execute(
                "INSERT INTO people (user_id, name, handle, joined, last_seen)"
                " VALUES (?, '', '', ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET last_seen = excluded.last_seen",
                (user_id[:64], now, now),
            )
        except Exception:
            log.exception("could not record listener; continuing")

    def active_since(self, since: float, limit: int = 500) -> list[str]:
        """Listeners seen since `since`, most recent first.

        The recency signal a daily feed needs: who to build tomorrow morning
        for. Deliberately returns ids and nothing else - what to build is the
        feed's question, not this table's.
        """
        try:
            rows = self._conn().execute(
                "SELECT user_id FROM people WHERE last_seen >= ?"
                " ORDER BY last_seen DESC LIMIT ?",
                (float(since), int(limit)),
            ).fetchall()
        except Exception:
            log.exception("could not read active listeners")
            return []
        return [r[0] for r in rows]

    def set_person(self, user_id: str, name: str, handle: str,
                   avatar: Optional[str] = None) -> dict:
        """Name, handle and optionally the picture.

        `avatar=None` leaves whatever is there alone; `avatar=""` removes it.
        Two different requests, and collapsing them would mean any client that
        does not know about pictures deletes one every time somebody renames
        themselves.
        """
        if not user_id:
            raise SocialError("No listener id.")
        name = " ".join(str(name).split())[:MAX_NAME]
        if not name:
            raise SocialError("Give yourself a name.")
        handle = clean_handle(handle)
        picture = None if avatar is None else clean_avatar(avatar)
        taken = self._conn().execute(
            "SELECT user_id FROM people WHERE handle = ? AND user_id != ?",
            (handle, user_id),
        ).fetchone()
        if taken:
            raise SocialError(f"@{handle} is taken.")
        existing = self.person(user_id)
        now = time.time()
        # Only name and handle are overwritten. `joined` and `last_seen` are
        # observations the server made and naming yourself is not new evidence
        # about either, so an update must not quietly reset them.
        self._conn().execute(
            "INSERT INTO people (user_id, name, handle, joined, last_seen, avatar)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET name = excluded.name,"
            " handle = excluded.handle, avatar = excluded.avatar",
            (user_id, name, handle, existing["joined"] or now,
             existing["last_seen"] or now,
             existing["avatar"] if picture is None else picture),
        )
        return self.person(user_id)

    # --- echoes -----------------------------------------------------------

    def echo(self, user_id: str, query: str, title: str, minutes: int,
             thread: str = "", caption: str = "", style=None, tags=None,
             audience: Optional[str] = None) -> Echo:
        if not user_id:
            raise SocialError("No listener id.")
        query = " ".join(str(query).split())[:300]
        if not query:
            raise SocialError("Nothing to echo.")
        now = time.time()
        conn = self._conn()
        # An echo of something already echoed just moves it to the top: the
        # listener's intent is "send this", not "send this twice".
        import json
        # None is "not sent" - an installed client that predates the story
        # editor - and keeps what the row already says, so its re-vibe never
        # turns a Close Friends story public or wipes its layout.
        layout = clean_style(style) if style is not None else None
        tagged = clean_tags(tags) if tags is not None else None
        if audience is not None:
            audience = audience if audience in AUDIENCES else ""
        # Vibing it again is posting it again: back on the story, with the
        # new layout, whoever it is for now.
        conn.execute(
            "INSERT INTO echoes (user_id, query, title, minutes, thread, at, caption,"
            " style, tags, audience, unstoried)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)"
            " ON CONFLICT(user_id, query, minutes) DO UPDATE SET at = excluded.at,"
            " title = excluded.title, thread = excluded.thread,"
            " caption = excluded.caption, style = COALESCE(?, style),"
            " tags = COALESCE(?, tags), audience = COALESCE(?, audience),"
            " unstoried = 0",
            (user_id, query, str(title)[:200], int(minutes), str(thread)[:200], now,
             clean_caption(caption), json.dumps(layout) if layout else "",
             json.dumps(tagged) if tagged else "", audience or "",
             None if layout is None else (json.dumps(layout) if layout else ""),
             None if tagged is None else (json.dumps(tagged) if tagged else ""),
             audience),
        )
        row = conn.execute(
            "SELECT id, user_id, query, title, minutes, thread, at FROM echoes"
            " WHERE user_id = ? AND query = ? AND minutes = ?",
            (user_id, query, int(minutes)),
        ).fetchone()
        return Echo(*row)

    def tagged_in(self, user_id: str, query: str, minutes: int) -> set:
        """Who this listener's vibe of an episode already tags, by id."""
        import json
        try:
            row = self._conn().execute(
                "SELECT tags FROM echoes WHERE user_id = ? AND query = ? AND minutes = ?",
                (user_id, " ".join(str(query).split())[:300], int(minutes))).fetchone()
            return {t.get("user_id") for t in json.loads(row[0])} if row and row[0] else set()
        except Exception:
            return set()

    def unstory(self, user_id: str, echo_id: int) -> bool:
        """Take one of this listener's vibes off their story (10.6 packet #1).

        Only their own: the row is matched on both the id and the poster. The
        vibe stays on their profile; vibing it again puts it back up.
        """
        try:
            cur = self._conn().execute(
                "UPDATE echoes SET unstoried = 1 WHERE id = ? AND user_id = ?",
                (int(echo_id), user_id))
        except Exception:
            log.exception("could not take a vibe off a story")
            return False
        return bool(cur.rowcount)

    # --- close friends (10.6 packet #2) -------------------------------------

    def close_friends(self, owner: str) -> list[str]:
        try:
            rows = self._conn().execute(
                "SELECT member FROM close_friends WHERE owner = ? ORDER BY at",
                (owner,)).fetchall()
        except Exception:
            log.exception("could not read close friends")
            return []
        return [r[0] for r in rows]

    def set_close_friend(self, owner: str, member: str, on: bool = True) -> bool:
        if not owner or not member or owner == member:
            raise SocialError("Pick somebody else.")
        conn = self._conn()
        if on:
            conn.execute("INSERT OR IGNORE INTO close_friends (owner, member, at)"
                         " VALUES (?, ?, ?)", (owner, member, time.time()))
        else:
            conn.execute("DELETE FROM close_friends WHERE owner = ? AND member = ?",
                         (owner, member))
        return on

    @staticmethod
    def _visible(alias: str, viewer: str) -> tuple[str, tuple]:
        """The SQL that keeps a close-friends vibe from anybody not on the
        poster's list. The poster always sees their own."""
        return (f" AND ({alias}.audience = '' OR {alias}.user_id = ?"
                " OR EXISTS (SELECT 1 FROM close_friends cf"
                f" WHERE cf.owner = {alias}.user_id AND cf.member = ?))",
                (viewer or "", viewer or ""))

    def unecho(self, user_id: str, query: str, minutes: int) -> bool:
        cur = self._conn().execute(
            "DELETE FROM echoes WHERE user_id = ? AND query = ? AND minutes = ?",
            (user_id, " ".join(str(query).split())[:300], int(minutes)),
        )
        return bool(cur.rowcount)

    # -- likes, dislikes and the counts on an Explore card (§134) ----------

    def rate(self, user_id: str, query: str, minutes: int, value: int) -> int:
        """Set this listener's thumb on an episode: 1, -1, or 0 to take it back.

        Returns the value now stored. A second like is the same statement, not
        two, and a dislike replaces a like rather than sitting beside it.
        """
        if not user_id:
            raise SocialError("No listener id.")
        query = " ".join(str(query).split())[:300]
        if not query:
            raise SocialError("Nothing to rate.")
        value = 1 if value > 0 else (-1 if value < 0 else 0)
        conn = self._conn()
        if not value:
            conn.execute("DELETE FROM ratings WHERE user_id = ? AND query = ?"
                         " AND minutes = ?", (user_id, query, int(minutes)))
            return 0
        conn.execute(
            "INSERT INTO ratings (user_id, query, minutes, value, at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(user_id, query, minutes) DO UPDATE SET"
            " value = excluded.value, at = excluded.at",
            (user_id, query, int(minutes), value, time.time()))
        return value

    def episode_counts_many(self, pairs, user_id: str = "") -> dict:
        """`episode_counts` for a whole Explore page in four queries.

        `(query, minutes) -> counts`, every pair present. The feed asks this
        for up to sixty cards at once, and one call per card was four queries
        a card inside an async handler.
        """
        wanted = {(" ".join(str(q).split())[:300], int(m)): (q, m)
                  for q, m in pairs}
        out = {orig: {"vibes": 0, "likes": 0, "dislikes": 0, "rating": 0,
                      "vibed": False} for orig in wanted.values()}
        if not wanted:
            return out
        queries = sorted({q for q, _m in wanted})
        marks = ",".join("?" for _ in queries)
        try:
            conn = self._conn()
            for q, m, n in conn.execute(
                    f"SELECT query, minutes, COUNT(*) FROM echoes WHERE query IN ({marks})"
                    # A close-friends vibe is not counted for anybody else
                    # (10.6 #2): on a quiet episode the number would give it away.
                    " AND (audience = '' OR user_id = ?)"
                    " GROUP BY query, minutes", (*queries, user_id or "")):
                if (q, m) in wanted:
                    out[wanted[(q, m)]]["vibes"] = int(n)
            for q, m, value, n in conn.execute(
                    f"SELECT query, minutes, value, COUNT(*) FROM ratings"
                    f" WHERE query IN ({marks}) GROUP BY query, minutes, value",
                    queries):
                if (q, m) in wanted:
                    out[wanted[(q, m)]]["likes" if value > 0 else "dislikes"] = int(n)
            if user_id:
                for q, m, value in conn.execute(
                        f"SELECT query, minutes, value FROM ratings WHERE user_id = ?"
                        f" AND query IN ({marks})", (user_id, *queries)):
                    if (q, m) in wanted:
                        out[wanted[(q, m)]]["rating"] = int(value)
                for q, m in conn.execute(
                        f"SELECT query, minutes FROM echoes WHERE user_id = ?"
                        f" AND query IN ({marks})", (user_id, *queries)):
                    if (q, m) in wanted:
                        out[wanted[(q, m)]]["vibed"] = True
        except Exception:
            log.exception("could not count vibes and ratings for a page")
        return out

    def episode_counts(self, query: str, minutes: int,
                       user_id: str = "") -> dict:
        """Vibes, likes and dislikes on one episode, and this listener's own.

        Counts over *everybody*, which is the point of a number on a card, and
        never who: the rows carry listener ids and none of them leave here.
        """
        query = " ".join(str(query).split())[:300]
        out = {"vibes": 0, "likes": 0, "dislikes": 0, "rating": 0,
               "vibed": False}
        try:
            conn = self._conn()
            out["vibes"] = int(conn.execute(
                "SELECT COUNT(*) FROM echoes WHERE query = ? AND minutes = ?"
                " AND (audience = '' OR user_id = ?)",
                (query, int(minutes), user_id or "")).fetchone()[0])
            for value, n in conn.execute(
                    "SELECT value, COUNT(*) FROM ratings WHERE query = ?"
                    " AND minutes = ? GROUP BY value", (query, int(minutes))):
                out["likes" if value > 0 else "dislikes"] = int(n)
            if user_id:
                row = conn.execute(
                    "SELECT value FROM ratings WHERE user_id = ? AND query = ?"
                    " AND minutes = ?", (user_id, query, int(minutes))).fetchone()
                out["rating"] = int(row[0]) if row else 0
                out["vibed"] = self.has_echoed(user_id, query, minutes)
        except Exception:
            log.exception("could not count an episode's vibes and ratings")
        return out

    def echoes_by(self, user_id: str, limit: int = 40,
                  viewer: Optional[str] = None) -> list[Echo]:
        """Their vibes, newest first. `viewer` is somebody else looking: a
        close-friends vibe is left out unless they are on the list."""
        where, args = ("", ())
        if viewer is not None and viewer != user_id:
            where, args = self._visible("e", viewer)
        try:
            rows = self._conn().execute(
                "SELECT id, user_id, query, title, minutes, thread, at FROM echoes e"
                " WHERE user_id = ?" + where + " ORDER BY at DESC LIMIT ?",
                (user_id, *args, int(limit)),
            ).fetchall()
        except Exception:
            log.exception("could not read echoes")
            return []
        return [Echo(*r) for r in rows]

    def has_echoed(self, user_id: str, query: str, minutes: int) -> bool:
        try:
            return bool(self._conn().execute(
                "SELECT 1 FROM echoes WHERE user_id = ? AND query = ? AND minutes = ?",
                (user_id, " ".join(str(query).split())[:300], int(minutes)),
            ).fetchone())
        except Exception:
            return False

    def recent_echoes(self, limit: int = 200, exclude_user: str = "") -> dict:
        """(query, minutes) -> who echoed it, newest first.

        Used to label Explore cards. There is no follow graph yet, so every
        echo is visible to everyone; when follows exist this is where the
        filter goes, and nothing else has to change.
        """
        try:
            rows = self._conn().execute(
                "SELECT e.query, e.minutes, p.name, p.handle, e.at, e.user_id"
                " FROM echoes e LEFT JOIN people p ON p.user_id = e.user_id"
                # Anybody's label: never a close-friends vibe (10.6 #2).
                " WHERE e.audience = ''"
                " ORDER BY e.at DESC LIMIT ?", (int(limit),),
            ).fetchall()
        except Exception:
            log.exception("could not read echo labels")
            return {}
        out: dict = {}
        for query, minutes, name, handle, at, user_id in rows:
            if user_id == exclude_user:
                continue
            key = (query, minutes)
            if key not in out:
                out[key] = {"by": name or "Someone", "handle": handle or "", "at": at}
        return out

    def echoes_among(self, user_ids, limit: int = 400, viewer: str = "") -> dict:
        """(query, minutes) -> the person who vibed it, for a named set only.

        The read behind Explore's friend tag. `recent_echoes` answers "did
        *anybody* vibe this", which was the right question when there was no
        follow graph; this answers "did one of *these people* vibe this", which
        is the one a card claiming a friendship has to ask.

        Carries the avatar, because the tag shows a face beside the name and a
        second lookup per card to find it would be a query per tile.
        """
        ids = [str(u) for u in (user_ids or []) if u]
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        where, args = self._visible("e", viewer)
        try:
            rows = self._conn().execute(
                "SELECT e.query, e.minutes, p.name, p.handle, p.avatar, e.at,"
                " e.user_id, e.title FROM echoes e"
                " LEFT JOIN people p ON p.user_id = e.user_id"
                f" WHERE e.user_id IN ({marks})" + where +
                " ORDER BY e.at DESC LIMIT ?",
                (*ids, *args, int(limit)),
            ).fetchall()
        except Exception:
            log.exception("could not read echoes for a circle")
            return {}
        out: dict = {}
        for query, minutes, name, handle, avatar, at, user_id, title in rows:
            key = (query, minutes)
            if key in out:
                continue
            # The title rides along for the Topic screen's "Friends vibed",
            # which draws the episode rather than just tagging a card that
            # already has one.
            out[key] = {"user_id": user_id, "name": name or "",
                        "handle": handle or "", "avatar": avatar or "",
                        "at": at, "title": title or ""}
        return out

    def latest_echo_at(self, user_ids, viewer: str = "") -> dict:
        """user_id -> when they last vibed anything, for a named set only.

        What YourFAM's avatar row asks. `echoes_among` cannot answer it: it
        keeps one row per *episode*, so two friends vibing the same one hides
        the earlier of them, and its row cap lets one prolific account crowd
        everybody else out. One grouped read per person has neither problem.
        """
        ids = [str(u) for u in (user_ids or []) if u]
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        try:
            where, args = self._visible("e", viewer)
            rows = self._conn().execute(
                "SELECT user_id, MAX(at) FROM echoes e"
                f" WHERE user_id IN ({marks}) AND unstoried = 0" + where +
                " GROUP BY user_id", (*ids, *args),
            ).fetchall()
        except Exception:
            log.exception("could not read the circle's latest vibes")
            return {}
        return {user_id: float(at or 0.0) for user_id, at in rows}

    def stories_among(self, user_ids, since: float,
                      per_person: int = 10, viewer: str = "") -> dict:
        """user_id -> their vibes since `since`, oldest first, as stories.

        YourFAM's avatar row plays a friend's vibes the way a story app plays
        stories (the 27/09 packet): one after another, oldest first, and only
        for 24 hours. Every vibe counts here - two friends vibing the same
        episode are two stories - which is why this is not `echoes_among`.
        """
        ids = [str(u) for u in (user_ids or []) if u]
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        where, args = self._visible("e", viewer)
        try:
            rows = self._conn().execute(
                "SELECT id, user_id, query, title, minutes, thread, at, caption,"
                " style, tags, audience FROM echoes e"
                f" WHERE user_id IN ({marks}) AND at >= ? AND unstoried = 0"
                + where + " ORDER BY at ASC",
                (*ids, float(since), *args),
            ).fetchall()
        except Exception:
            log.exception("could not read the circle's stories")
            return {}
        import json
        out: dict = {}
        for (eid, user_id, query, title, minutes, thread, at, caption,
             style, tags, audience) in rows:
            try:
                layout = json.loads(style) if style else {}
            except ValueError:
                layout = {}
            try:
                tagged = json.loads(tags) if tags else []
            except ValueError:
                tagged = []
            people = []
            for tag in tagged:
                person = self.person(tag.get("user_id") or "")
                if person.get("handle"):
                    # A handle and where it sits - never the id.
                    people.append({"handle": person["handle"],
                                   "name": person.get("name") or "",
                                   "x": tag.get("x", 0.5), "y": tag.get("y", 0.3)})
            out.setdefault(user_id, []).append(
                {"id": int(eid), "query": query, "title": title or "",
                 "minutes": int(minutes or 0),
                 "thread": thread or "", "at": float(at),
                 # What they said about it, drawn with the story (10.5 #9).
                 "caption": caption or "",
                 # How they laid it out, who they tagged, and who it is for
                 # (10.6 #2).
                 "style": layout, "tags": people,
                 "close": audience == "close"})
        # The newest `per_person`, still oldest first.
        return {uid: items[-per_person:] for uid, items in out.items()}

    # --- comments on an episode (10.5 packet #8) --------------------------

    def add_comment(self, user_id: str, query: str, minutes: int, text: str,
                    parent_id: int = 0) -> dict:
        """Post a comment, or a reply to one. Returns it as `comments` draws it.

        A reply is filed under the top-level comment it answers, so a thread
        is one level deep however somebody replied. A parent that is not on
        this episode is refused rather than quietly made top-level: a reply
        that lands somewhere else reads as the app losing it.
        """
        if not user_id:
            raise SocialError("No listener id.")
        query = " ".join(str(query).split())[:300]
        if not query:
            raise SocialError("Nothing to comment on.")
        text = _clean_words(text, MAX_COMMENT)
        if not text:
            raise SocialError("Write something first.")
        conn = self._conn()
        parent = 0
        if parent_id:
            row = conn.execute(
                "SELECT id, parent_id, query, minutes FROM comments WHERE id = ?",
                (int(parent_id),)).fetchone()
            if not row or row[2] != query or int(row[3]) != int(minutes):
                raise SocialError("That comment is not here any more.")
            parent = int(row[1]) or int(row[0])
        now = time.time()
        cur = conn.execute(
            "INSERT INTO comments (user_id, query, minutes, parent_id, text, at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, query, int(minutes), parent, text, now))
        person = self.person(user_id)
        return self._comment_dict(cur.lastrowid, user_id, parent, text, now,
                                  person, 0, False, user_id)

    def _comment_dict(self, cid, user_id, parent, text, at, person, likes,
                      liked, viewer) -> dict:
        # No `user_id`: a listener id in a response is an id the client could
        # send back (`listener-id-server`). `mine` says what the id would.
        return {"id": int(cid), "parent_id": int(parent or 0), "text": text,
                "at": float(at), "name": person.get("name") or "",
                "handle": person.get("handle") or "",
                "avatar": person.get("avatar") or "",
                "likes": int(likes or 0), "liked": bool(liked),
                "mine": bool(viewer) and viewer == user_id}

    def comments(self, query: str, minutes: int, viewer: str = "",
                 limit: int = 200) -> list[dict]:
        """An episode's comments, most liked first then newest, each with its
        replies (oldest first, as a conversation reads) under `replies`."""
        query = " ".join(str(query).split())[:300]
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT c.id, c.user_id, c.parent_id, c.text, c.at,"
                " (SELECT COUNT(*) FROM comment_likes l WHERE l.comment_id = c.id)"
                " FROM comments c WHERE c.query = ? AND c.minutes = ?"
                " ORDER BY c.at DESC LIMIT ?",
                (query, int(minutes), int(limit) * 4)).fetchall()
        except Exception:
            log.exception("could not read an episode's comments")
            return []
        # The newest are kept when a thread is past the cap, then read oldest
        # first so every reply comes after the comment it hangs from.
        rows.reverse()
        ids = [r[0] for r in rows]
        liked: set = set()
        if viewer and ids:
            marks = ",".join("?" for _ in ids)
            liked = {r[0] for r in conn.execute(
                f"SELECT comment_id FROM comment_likes WHERE user_id = ?"
                f" AND comment_id IN ({marks})", (viewer, *ids))}
        people: dict = {}
        tops: list[dict] = []
        by_id: dict = {}
        for cid, uid, parent, text, at, likes in rows:
            if uid not in people:
                people[uid] = self.person(uid)
            item = self._comment_dict(cid, uid, parent, text, at, people[uid],
                                      likes, cid in liked, viewer)
            if parent:
                host = by_id.get(parent)
                if host is not None:
                    host["replies"].append(item)
                continue
            item["replies"] = []
            by_id[cid] = item
            tops.append(item)
        tops.sort(key=lambda c: (-c["likes"], -c["at"]))
        return tops[:limit]

    def comment_counts_many(self, pairs) -> dict:
        """`(query, minutes) -> number of comments`, replies included, for a
        whole Explore page in one query."""
        wanted = {(" ".join(str(q).split())[:300], int(m)): (q, m)
                  for q, m in pairs}
        out = {orig: 0 for orig in wanted.values()}
        if not wanted:
            return out
        queries = sorted({q for q, _ in wanted})
        marks = ",".join("?" for _ in queries)
        try:
            rows = self._conn().execute(
                "SELECT query, minutes, COUNT(*) FROM comments"
                f" WHERE query IN ({marks}) GROUP BY query, minutes",
                queries).fetchall()
        except Exception:
            log.exception("could not count comments")
            return out
        for query, minutes, n in rows:
            orig = wanted.get((query, int(minutes)))
            if orig is not None:
                out[orig] = int(n)
        return out

    def like_comment(self, user_id: str, comment_id: int, on: bool = True) -> dict:
        """Like a comment, or take the like back. Returns its count and state."""
        if not user_id:
            raise SocialError("No listener id.")
        conn = self._conn()
        if not conn.execute("SELECT 1 FROM comments WHERE id = ?",
                            (int(comment_id),)).fetchone():
            raise SocialError("That comment is not here any more.")
        if on:
            conn.execute("INSERT OR IGNORE INTO comment_likes (user_id, comment_id, at)"
                         " VALUES (?, ?, ?)", (user_id, int(comment_id), time.time()))
        else:
            conn.execute("DELETE FROM comment_likes WHERE user_id = ? AND comment_id = ?",
                         (user_id, int(comment_id)))
        n = conn.execute("SELECT COUNT(*) FROM comment_likes WHERE comment_id = ?",
                         (int(comment_id),)).fetchone()[0]
        return {"id": int(comment_id), "likes": int(n), "liked": bool(on)}

    def delete_comment(self, user_id: str, comment_id: int) -> bool:
        """Take back your own comment, with its replies and likes."""
        conn = self._conn()
        row = conn.execute("SELECT user_id FROM comments WHERE id = ?",
                           (int(comment_id),)).fetchone()
        if not row or row[0] != user_id:
            return False
        ids = [int(comment_id)] + [r[0] for r in conn.execute(
            "SELECT id FROM comments WHERE parent_id = ?", (int(comment_id),))]
        marks = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM comment_likes WHERE comment_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM comments WHERE id IN ({marks})", ids)
        return True

    # --- the follow graph -------------------------------------------------

    def follow(self, user_id: str, target_id: str, at: float = 0.0) -> bool:
        """Follow somebody. Idempotent, and returns whether anything changed.

        Refuses to follow yourself - not a moral position, a correctness one:
        `friends()` is the intersection of following and followers, so a
        self-follow would make everybody their own friend and put them in their
        own circle rail.
        """
        if not user_id or not target_id:
            raise SocialError("Nobody to follow.")
        if user_id == target_id:
            raise SocialError("You cannot follow yourself.")
        now = at or time.time()
        cur = self._conn().execute(
            "INSERT INTO follows (follower, followee, at) VALUES (?, ?, ?)"
            " ON CONFLICT (follower, followee) DO NOTHING",
            (user_id, target_id, now))
        return bool(cur.rowcount)

    def unfollow(self, user_id: str, target_id: str) -> bool:
        cur = self._conn().execute(
            "DELETE FROM follows WHERE follower = ? AND followee = ?",
            (user_id, target_id))
        return bool(cur.rowcount)

    def is_following(self, user_id: str, target_id: str) -> bool:
        row = self._conn().execute(
            "SELECT 1 FROM follows WHERE follower = ? AND followee = ?",
            (user_id, target_id)).fetchone()
        return bool(row)

    def following(self, user_id: str, limit: int = 500) -> list[dict]:
        """Who this listener follows, newest first, with their details."""
        return self._graph(
            "SELECT f.followee, p.name, p.handle, f.at, p.avatar FROM follows f"
            " LEFT JOIN people p ON p.user_id = f.followee"
            " WHERE f.follower = ? ORDER BY f.at DESC LIMIT ?",
            user_id, limit)

    def followers(self, user_id: str, limit: int = 500) -> list[dict]:
        return self._graph(
            "SELECT f.follower, p.name, p.handle, f.at, p.avatar FROM follows f"
            " LEFT JOIN people p ON p.user_id = f.follower"
            " WHERE f.followee = ? ORDER BY f.at DESC LIMIT ?",
            user_id, limit)

    def _graph(self, sql: str, user_id: str, limit: int) -> list[dict]:
        try:
            rows = self._conn().execute(sql, (user_id, int(limit))).fetchall()
        except Exception:
            log.exception("could not read the follow graph")
            return []
        return [{"user_id": r[0], "name": r[1] or "", "handle": r[2] or "",
                 "at": r[3], "avatar": (r[4] if len(r) > 4 else "") or ""}
                for r in rows]

    def friends(self, user_id: str, limit: int = 500) -> list[dict]:
        """Mutual follows. Derived, never stored - see the schema note.

        This is the list an app means by "friends": the people a share is most
        likely to be going to, and the ones whose echoes are worth surfacing
        above the crowd's.
        """
        mine = {p["user_id"] for p in self.following(user_id, limit)}
        return [p for p in self.followers(user_id, limit) if p["user_id"] in mine]

    def circle_of(self, user_id: str, limit: int = 500) -> list[str]:
        """Whose listening the "what your friends are listening to" rail reads.

        Friends first, then everyone else they follow. Both, rather than
        friends only, and the reason is what the rail is for: following is
        asymmetric here by design, so a listener who follows six people and is
        followed back by none has no friends and would get an empty rail on
        the day they had just gone and found six people to follow. Following
        somebody is already a statement that their taste is worth seeing.

        Friends come first in the list and that is presentation rather than
        weight: the ranker counts plays and treats everyone here equally, on
        purpose. Following somebody is already the choice, and a rule that
        made a mutual follow's listening count double would need a number
        nobody has any way to tune.

        Returns ids only. `topics.rank_friends` takes a set of ids and knows
        nothing about this module - the ranking stays a pure function of the
        event log, which is what keeps it testable without a social graph.
        """
        if not user_id:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for person in (self.friends(user_id, limit)
                       + self.following(user_id, limit)):
            uid = person.get("user_id") or ""
            if uid and uid not in seen:
                seen.add(uid)
                out.append(uid)
        return out

    def new_followers(self, user_id: str, limit: int = 20,
                      unannounced: bool = False) -> list[dict]:
        """People who followed this listener since they last looked.

        `follows_back` rides along so the popup knows whether to offer the
        button: offering "Follow back" to somebody who is already a friend is
        a control that cannot do anything, and finding that out would cost a
        query per person.

        `unannounced` narrows it to the people the popup and the banner have
        never shown this listener (§142) - the badge still counts everybody
        new, because it is cleared by opening Friends and not by a popup.
        """
        if not user_id:
            return []
        extra = ""
        if unannounced:
            extra = ("   AND NOT EXISTS(SELECT 1 FROM announced a"
                     "                  WHERE a.user_id = f.followee"
                     "                    AND a.follower = f.follower)")
        try:
            rows = self._conn().execute(
                "SELECT f.follower, p.name, p.handle, f.at, p.avatar,"
                "       EXISTS(SELECT 1 FROM follows b"
                "              WHERE b.follower = ? AND b.followee = f.follower)"
                " FROM follows f"
                " LEFT JOIN people p ON p.user_id = f.follower"
                " WHERE f.followee = ?"
                "   AND f.at > COALESCE((SELECT followers_seen FROM people"
                "                        WHERE user_id = ?), 0)"
                + extra +
                " ORDER BY f.at DESC LIMIT ?",
                (user_id, user_id, user_id, int(limit)),
            ).fetchall()
        except Exception:
            log.exception("could not read new followers")
            return []
        return [
            {"user_id": r[0], "name": r[1] or "", "handle": r[2] or "",
             "at": r[3], "avatar": r[4] or "", "follows_back": bool(r[5])}
            for r in rows
        ]

    def mark_announced(self, user_id: str, follower: str,
                       at: float = 0.0) -> None:
        """This listener has been shown that `follower` follows them.

        Written when the popup or the banner is raised, so a follow is
        announced once and never again (§142). Idempotent.
        """
        if not user_id or not follower:
            return
        try:
            self._conn().execute(
                "INSERT OR IGNORE INTO announced (user_id, follower, at)"
                " VALUES (?, ?, ?)",
                (user_id[:64], follower[:64], at or time.time()))
        except Exception:
            log.exception("could not note an announced follower; continuing")

    def mark_followers_seen(self, user_id: str, at: float = 0.0) -> None:
        """They looked. Everything up to now stops being new.

        Written on the Friends *tab*, never when the popup appears: a badge
        that cleared itself the moment a popup was drawn would be a count
        nobody ever got to read.
        """
        if not user_id:
            return
        now = at or time.time()
        try:
            self._conn().execute(
                "INSERT INTO people (user_id, name, handle, joined, last_seen,"
                " followers_seen) VALUES (?, '', '', ?, ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET"
                " followers_seen = excluded.followers_seen",
                (user_id[:64], now, now, now),
            )
        except Exception:
            log.exception("could not mark followers seen; continuing")

    def follow_counts(self, user_id: str) -> dict:
        """Numbers for a profile. Counted rather than kept in a column, because
        a denormalised counter is a number that can be wrong, and this app has
        a rule about inventing numbers on the profile page.

        **`friends` is counted here, and it was the missing one.** Two screens
        read `follows.friends` - the profile's own line beside the picture and
        the Friends tab's header - and this returned only the two directions.
        A missing key reads as `undefined`, and `undefined || 0` prints zero,
        so a listener who followed somebody who followed them back was shown
        "0 friends · 1 following · 1 follower": three numbers that contradict
        each other, from the one page with a rule against inventing any.

        Derived by the same intersection `friends()` uses rather than a second
        definition of what a friend is - SQL here and a set intersection there
        would be two answers to one question, which is the shape of bug this
        module already avoids by never storing mutuality.
        """
        try:
            following = self._conn().execute(
                "SELECT COUNT(*) FROM follows WHERE follower = ?",
                (user_id,)).fetchone()[0]
            followers = self._conn().execute(
                "SELECT COUNT(*) FROM follows WHERE followee = ?",
                (user_id,)).fetchone()[0]
            friends = self._conn().execute(
                "SELECT COUNT(*) FROM follows a JOIN follows b"
                "  ON a.followee = b.follower AND a.follower = b.followee"
                " WHERE a.follower = ?",
                (user_id,)).fetchone()[0]
        except Exception:
            log.exception("could not count follows")
            return {"following": 0, "followers": 0, "friends": 0}
        return {"following": int(following), "followers": int(followers),
                "friends": int(friends)}

    def find_people(self, term: str, exclude_user: str = "",
                    limit: int = 20) -> list[dict]:
        """Look somebody up by handle or name, to follow or share with.

        Handle-prefix first and only then name, because a handle is what
        somebody gives out and a name is not unique. Deliberately requires two
        characters: a one-letter search returns most of the listener table,
        which is a directory dump rather than a search.
        """
        term = " ".join(str(term or "").split()).strip().lstrip("@").lower()
        if len(term) < 2:
            return []
        like = term.replace("%", "").replace("_", "") + "%"
        try:
            rows = self._conn().execute(
                "SELECT user_id, name, handle, avatar FROM people"
                " WHERE handle != '' AND (handle LIKE ? OR LOWER(name) LIKE ?)"
                " ORDER BY (handle LIKE ?) DESC, handle LIMIT ?",
                (like, "%" + like, like, int(limit)),
            ).fetchall()
        except Exception:
            log.exception("could not search people")
            return []
        return [{"user_id": r[0], "name": r[1] or "", "handle": r[2] or "",
                 "avatar": r[3] or ""}
                for r in rows if r[0] != exclude_user]

    def forget(self, user_id: str) -> int:
        """Erase everything this store holds for one listener.

        Part of account deletion, which App Store guideline 5.1.1(v) requires
        of any app that creates accounts. Each store implements its own rather
        than a central deleter reaching into six databases by table name: that
        deleter silently stops covering the seventh, and the failure is
        invisible until somebody audits it.

        Returns rows removed, so the endpoint can report what it did rather
        than that it tried.
        """
        removed = 0
        # Follows are erased in both directions: a deleted listener must not
        # stay in anybody else's follower count, pointing at an id that no
        # longer resolves to a person.
        try:
            cur = self._conn().execute(
                "DELETE FROM follows WHERE follower = ? OR followee = ?",
                (user_id, user_id))
            removed += cur.rowcount or 0
            cur = self._conn().execute(
                "DELETE FROM announced WHERE user_id = ? OR follower = ?",
                (user_id, user_id))
            removed += cur.rowcount or 0
            cur = self._conn().execute(
                "DELETE FROM close_friends WHERE owner = ? OR member = ?",
                (user_id, user_id))
            removed += cur.rowcount or 0
        except Exception:
            log.exception("could not erase follows for %r", user_id)
        # Their comments go, and every reply under them and every like on
        # them, so no thread is left hanging from a comment nobody wrote.
        try:
            conn = self._conn()
            own = [r[0] for r in conn.execute(
                "SELECT id FROM comments WHERE user_id = ?", (user_id,))]
            if own:
                marks = ",".join("?" for _ in own)
                # Other people's replies under them, and the likes on those.
                own += [r[0] for r in conn.execute(
                    f"SELECT id FROM comments WHERE parent_id IN ({marks})", own)]
                marks = ",".join("?" for _ in own)
                conn.execute(
                    f"DELETE FROM comment_likes WHERE comment_id IN ({marks})", own)
                cur = conn.execute(
                    f"DELETE FROM comments WHERE id IN ({marks}) AND user_id != ?",
                    (*own, user_id))
                removed += cur.rowcount or 0
            cur = conn.execute("DELETE FROM comment_likes WHERE user_id = ?",
                               (user_id,))
            removed += cur.rowcount or 0
            cur = conn.execute("DELETE FROM comments WHERE user_id = ?", (user_id,))
            removed += cur.rowcount or 0
        except Exception:
            log.exception("could not erase comments for %r", user_id)
        # Their id out of the tags on other people's stories.
        try:
            import json
            conn = self._conn()
            for eid, tags in conn.execute(
                    "SELECT id, tags FROM echoes WHERE tags LIKE ?",
                    (f"%{user_id}%",)).fetchall():
                try:
                    kept = [t for t in json.loads(tags) if t.get("user_id") != user_id]
                except ValueError:
                    kept = []
                conn.execute("UPDATE echoes SET tags = ? WHERE id = ?",
                             (json.dumps(kept) if kept else "", eid))
        except Exception:
            log.exception("could not untag %r", user_id)
        for table in ('echoes', 'ratings', 'people'):
            try:
                cur = self._conn().execute(
                    f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
                removed += cur.rowcount or 0
            except Exception:
                log.exception("could not erase %s for %r", table, user_id)
        return removed
