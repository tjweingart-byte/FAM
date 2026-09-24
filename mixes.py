"""playFAM: named daily mixes of topics.

A mix is a standing subscription, not a saved recording: it holds *topic ids*,
and each day the episodes for those topics are whatever today's briefing on
them is. That distinction is the whole design. Saving audio would mean a mix
goes stale the moment it is made, and would break the no-files rule the rest
of the product is built on; saving topic ids means "at the gym" is fresh every
morning and costs nothing to keep.

**A mix follows subjects now, not episodes** (§137). Its members are
catalogue subjects (`f:nfl`), optionally narrowed to one specific
(`f:nfl~Eagles`), plus anything the listener typed. A bank id - a written,
evergreen episode - is still accepted and still plays, because mixes made
before this hold them, but the interface no longer offers one: a bank episode
is one story, and a mix is a list of things somebody wants a *new* episode on
every day. Each followed item is played as that day's edition (the prompt
carries the date, which is also what keys it in the shared cache), so two
listeners following the Eagles share one script a day and nobody hears
yesterday's.

Membership is still validated: an `f:` id must name a catalogue subject and a
bare id must name a bank topic, and an unknown one is rejected rather than
silently stored. The *focus* is free text, because no list of the world's
teams is complete - `topics.FOCUS_HINTS` is what is suggested, never what is
allowed.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import date
from typing import Optional, Sequence
from urllib.parse import quote, unquote

from paths import data_path
from topics import BANK_BY_ID, CATALOGUE_BY_ID, facets_only, tags_for_text

log = logging.getLogger(__name__)

MAX_NAME = 60
MAX_QUERY = 200
MAX_TOPICS = 20
MAX_MIXES_PER_USER = 30
#: How long a specific may be ("San Diego FC"). Short on purpose: the daily
#: prompt names it twice and has to fit under the 300 characters the endpoints
#: that echo a query back (`/api/next`, a vibe, a saved item) accept.
MAX_FOCUS = 40
#: Specifics one id may carry. The interface writes one per item; `a|b` is
#: accepted from another client, and bounded so its prompt still fits.
MAX_FOCUS_PER_ITEM = 3
#: A cover is a 360px square JPEG from the in-app cropper, 10-40 KB. The cap
#: is on the base64 text, with room for a PNG from a client that did not crop.
MAX_COVER_CHARS = 200_000
_COVER_PREFIXES = ("data:image/jpeg;base64,", "data:image/png;base64,",
                   "data:image/webp;base64,")


class MixError(ValueError):
    """Something the listener did wrong, phrased so it can be shown to them."""


@dataclass(frozen=True)
class MixItem:
    """One line in a mix: a bank topic, or something the listener typed.

    Both play the same way - a query goes to the pipeline and the shared
    script cache - but they cost differently, and the interface says so. A
    bank topic is shared by everyone who has it in a mix, so the second
    listener's copy is free. A typed one is only shared with people who happen
    to phrase the same question the same way, which for a niche question is
    nobody: it is a script a day, for one person.
    """

    id: str
    title: str
    query: str
    custom: bool
    subtitle: str = ""
    icon: str = "leaf"
    #: A followed catalogue subject rather than a written episode: played as
    #: today's edition, never a rerun. See `followed_item`.
    follow: bool = False
    #: The followed subject without its focus (`f:nfl` for `f:nfl~Eagles`),
    #: so the interface can tell "NFL - Eagles" and "NFL" apart as two
    #: briefings on one subject.
    base: str = ""
    focus: tuple[str, ...] = ()
    topic_label: str = ""

    def as_dict(self) -> dict:
        out = {
            "id": self.id, "title": self.title, "query": self.query,
            "custom": self.custom, "subtitle": self.subtitle, "icon": self.icon,
        }
        if self.follow:
            out.update(follow=True, base=self.base, focus=list(self.focus),
                       topic_label=self.topic_label)
        # What a play of this item asks for, with `{date}` for the listener's
        # own today. Served rather than assembled by each client: prefetch
        # has to warm the same words a tap sends, or every warm is wasted.
        out["daily_prompt"] = daily_prompt(self)
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "MixItem":
        return cls(d["id"], d["title"], d["query"], d["custom"],
                   d.get("subtitle", ""), d.get("icon", "leaf"),
                   bool(d.get("follow", False)), d.get("base", ""),
                   tuple(d.get("focus", ())), d.get("topic_label", ""))


#: Where the day goes in a daily prompt. Filled by whoever plays it - the
#: interface with the listener's local date, prefetch with the server's.
DAILY_DATE = "{date}"
#: The longest `date_label` there is, for measuring a prompt before the date
#: is known.
LONGEST_DATE = "Wednesday, September 30, 2026"
#: `/api/next`, a vibe and a saved item all take a query of up to 300
#: characters, and each of them is handed this prompt back.
MAX_PROMPT = 300
#: Tried longest first, and the first that fits is used: a long typed topic
#: loses the instruction about yesterday before it loses its own words.
DAILY_ENDINGS = (
    ": what happened in the last 24 hours, what changed, and why it matters. "
    "Lead with the newest development; skip background the listener heard yesterday.",
    ": what happened in the last 24 hours, what changed, and why it matters.",
    ".",
)


def daily_prompt(item: "MixItem") -> str:
    """The question a followed or typed item asks, with `{date}` unfilled.

    **Every play of a mix is that day's edition, never a rerun** - the
    product promise is that playing the folder gives the most up-to-date
    version of each subject. So the prompt carries the date, and that is also
    what keeps one day's briefing from being served the next: the cache key
    is built from the words, dates included, and the near-match cache
    refuses any pair whose numbers differ (§137 has the test). Two listeners
    following the Eagles on the same day share one script, which is the
    shared-cost design working as intended.

    A narrowed item says "cover only Eagles, not NFL in general", because
    "NFL - Eagles" and plain "NFL" can sit in one mix as two briefings and
    must not come out as the same episode twice.

    "" for a bank episode: that is a written story and plays as itself.
    """
    if not (item.follow or item.custom):
        return ""
    if item.focus:
        shown = " and ".join(item.focus)
        # The "cover only" sentence is the first thing to go when a prompt
        # carrying several long specifics would not otherwise fit.
        heads = (f"The latest on {shown} ({item.topic_label}) as of {DAILY_DATE}. "
                 f"Cover only {shown}, not {item.topic_label} in general",
                 f"The latest on {shown} ({item.topic_label}) as of {DAILY_DATE}")
    else:
        heads = (f"The latest on {item.query} as of {DAILY_DATE}",)
    for head in heads:
        for ending in DAILY_ENDINGS:
            if len((head + ending).replace(DAILY_DATE, LONGEST_DATE)) <= MAX_PROMPT:
                return head + ending
    # Unreachable with today's limits (a typed topic is at most MAX_QUERY and
    # a followed one at most MAX_FOCUS_PER_ITEM x MAX_FOCUS); a test says so.
    return heads[-1] + DAILY_ENDINGS[-1]


def date_label(day: date) -> str:
    """"Wednesday, September 23, 2026" - exactly what the interface's
    `toLocaleDateString("en-US", {weekday, month, day, year: long/numeric})`
    prints, so a date filled in here and one filled in there agree."""
    return f"{day:%A}, {day:%B} {day.day}, {day.year}"


def prompt_for(item: "MixItem", day: Optional[date] = None) -> str:
    """What playing this item on `day` sends to the pipeline."""
    template = daily_prompt(item)
    if not template:
        return item.query
    return template.replace(DAILY_DATE, date_label(day or date.today()))


def _bank_item(topic_id: str) -> MixItem:
    topic = BANK_BY_ID[topic_id]
    return MixItem(topic.id, topic.title, topic.query, False, topic.subtitle, topic.icon)


def custom_item(query: str, title: str = "") -> MixItem:
    """A topic the listener typed. Its id is derived from the query, so the
    same question added twice is one entry rather than two."""
    query = " ".join(str(query).split())[:MAX_QUERY]
    if not query:
        raise MixError("Type what you want to hear about.")
    ident = "q:" + hashlib.sha1(query.lower().encode("utf-8")).hexdigest()[:10]
    title = " ".join(str(title).split())[:MAX_NAME] or query[:1].upper() + query[1:]
    # The facet, not the first tag: `tags_for_text` returns subtags too, and
    # the icon vocabulary is drawn per facet. Taking tags[0] would ask it for
    # a picture of "ai" and quietly get the fallback leaf.
    facets = facets_only(tags_for_text(query))
    return MixItem(ident, title, query, True, "Added by you", _ICON_FOR_TAG.get(
        facets[0] if facets else "", "leaf"))


def clean_focus(text: str) -> str:
    """One specific, as it will be shown and spoken. `,` separates items in
    the stored id list and `~`/`|` delimit a focus, so all three become
    spaces rather than being escaped: nobody's team is named with them."""
    text = " ".join(str(text).replace(",", " ").replace("~", " ")
                    .replace("|", " ").split())
    return text[:MAX_FOCUS].strip()


def followed_item(entry: str) -> MixItem:
    """`f:<catalogue id>` or `f:<catalogue id>~<url-encoded focus>`.

    **One focus per item.** "NFL - Eagles" and "NFL - Chiefs" are two items,
    and plain `f:nfl` beside them is "also all of NFL": each is its own
    briefing, its own row and its own play button, which a combined "Eagles
    and Chiefs" brief is not. The parser still accepts `a|b`, because a
    client that sends it should not lose a pick, but it is folded into one
    title and nothing in this app writes it.

    The id is rebuilt from the cleaned parts rather than stored as sent, so
    "Eagles" typed twice with different spacing is one item, not two.
    """
    base, _, raw = entry.partition("~")
    subject = CATALOGUE_BY_ID.get(base[2:])
    if subject is None:
        raise MixError(f"There is no topic called {base[2:]!r} to follow.")
    focus: list[str] = []
    for part in raw.split("|") if raw else ():
        f = clean_focus(unquote(part))
        if f and f.lower() not in (x.lower() for x in focus):
            focus.append(f)
    if len(focus) > MAX_FOCUS_PER_ITEM:
        raise MixError(f"Pick up to {MAX_FOCUS_PER_ITEM} specifics per topic.")
    # Encoded exactly as the interface's `encodeURIComponent` does, which
    # leaves `!'()*` alone, so an id the client built and the one stored here
    # are the same string.
    ident = base + ("~" + "|".join(quote(f, safe="!'()*") for f in focus) if focus else "")
    shown = ", ".join(focus)
    return MixItem(
        ident,
        f"{subject.label} \u00b7 {shown}" if focus else subject.label,
        subject.label, False,
        (f"Focused on {shown} \u00b7 new briefing every day" if focus
         else "New briefing every day"),
        subject.icon, True, base, tuple(focus), subject.label,
    )


def clean_cover(cover: str) -> str:
    """A mix's cover photo, as a data URL, or "" for none.

    Kept inline on the mix row: the whole of this app's storage is SQLite on
    one disk, and a cropped 360px JPEG is smaller than a message thread.
    Refused rather than trimmed when it is not an image, because a cover that
    silently fails to draw looks like the app lost it. If covers ever move to
    object storage, this is where the URL comes back instead.
    """
    cover = str(cover or "").strip()
    if not cover:
        return ""
    if len(cover) > MAX_COVER_CHARS:
        raise MixError("That photo is too large. Try a smaller one.")
    if not cover.startswith(_COVER_PREFIXES):
        raise MixError("A cover has to be a photo.")
    try:
        base64.b64decode(cover.split(",", 1)[1], validate=True)
    except (binascii.Error, ValueError):
        raise MixError("That photo could not be read.") from None
    return cover


#: A typed topic still deserves a picture. Reuses the bank's icon vocabulary.
_ICON_FOR_TAG = {
    "sports": "sports", "business": "business", "money": "business",
    "tech": "tech", "science": "rocket", "health": "leaf",
    "culture": "music", "world": "business",
}


@dataclass
class Mix:
    id: str
    user_id: str
    name: str
    items: list[MixItem]
    created_at: float
    updated_at: float
    #: Public mixes appear on the listener's profile. Private is the default:
    #: a mix is a routine, and a routine is personal until someone decides
    #: otherwise.
    public: bool = False
    #: A data URL, or "" - see `clean_cover`.
    cover: str = ""
    #: Where this mix was added from, when it is a copy of somebody else's
    #: public mix: that mix's id, and its owner. Both "" for a mix somebody
    #: made. The owner is kept server-side only - `as_dict` never carries a
    #: listener id - and is what lets the list say whose mix it came from.
    source_id: str = ""
    source_user: str = ""

    @property
    def topic_ids(self) -> list[str]:
        """Shared members - bank topics and followed subjects - which is what
        the shared-cost design is measured on. Typed topics are not."""
        return [i.id for i in self.items if not i.custom]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "topic_ids": list(self.topic_ids),
            "items": [i.as_dict() for i in self.items],
            # Kept for anything still reading `topics`; bank entries only.
            "topics": [i.as_dict() for i in self.items if not i.custom],
            "custom_count": sum(1 for i in self.items if i.custom),
            "public": self.public,
            "cover": self.cover,
            "source_id": self.source_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def clean_name(name: str) -> str:
    name = " ".join(str(name).split())[:MAX_NAME]
    if not name:
        raise MixError("Give the mix a name.")
    return name


def clean_items(raw: Sequence) -> list[MixItem]:
    """Normalise whatever the interface sent into an ordered list of items.

    Accepts followed subjects (`f:nfl`, `f:nfl~Eagles`) and legacy bank ids
    as bare strings, and typed topics as `{"query": "...", "title": "..."}`.
    De-duplicated, order preserved.

    An unknown bank id is an error rather than something to drop quietly: a
    mix that silently loses a topic looks like the app forgot, which is the
    kind of invisible failure this project keeps paying for. A *typed* topic
    cannot be unknown - it is whatever they wrote.
    """
    items: list[MixItem] = []
    seen: set[str] = set()
    for entry in raw:
        if isinstance(entry, str) and entry.startswith("f:"):
            item = followed_item(entry)
        elif isinstance(entry, str):
            if entry not in BANK_BY_ID:
                raise MixError(f"There is no topic called {entry!r}.")
            item = _bank_item(entry)
        elif isinstance(entry, dict) and entry.get("query"):
            item = custom_item(entry["query"], entry.get("title", ""))
        elif isinstance(entry, dict) and entry.get("id") in BANK_BY_ID:
            item = _bank_item(entry["id"])
        else:
            raise MixError("A mix entry needs either a topic id or a question.")
        # Case-folded, so "Eagles" and "eagles" are one briefing, not two.
        if item.id.lower() not in seen:
            seen.add(item.id.lower())
            items.append(item)
    if len(items) > MAX_TOPICS:
        raise MixError(f"A mix holds up to {MAX_TOPICS} topics.")
    return items


#: Every column `_row_to_mix` reads, in its order.
_COLUMNS = ("id, user_id, name, topic_ids, created_at, updated_at, items, public,"
            " cover, source_id, source_user")

#: How many public mixes one search returns. Covers are inline data URLs, so
#: this is also what bounds the size of the response.
MAX_PUBLIC_RESULTS = 40
#: How many public rows a search reads before matching. Matching is done here
#: rather than in SQL because an item's titles live inside a JSON column.
PUBLIC_SCAN = 2000


def owner_label(name: str, handle: str) -> str:
    """How a mix's owner is named on somebody else's copy."""
    return ("@" + handle) if handle else (name or "another listener")


def match_score(mix: "Mix", query: str, owner_name: str = "",
                owner_handle: str = "") -> int:
    """How well a public mix answers a DailyFAM search, 0 meaning not at all.

    Three things a mix can be found by, and every word typed has to be found
    in one of them: its **name** ("gym"), a **topic in it** ("AI updates" -
    title, question or the specific it is narrowed to), or **whose it is**
    (a name or handle, "@sam" or "sam"). So "gym" and "AI updates" both find a
    mix called Gym that follows AI updates, and typing somebody's handle
    lists every public mix they have.

    The score only orders what matched: a hit on the name outranks a hit on
    the owner, which outranks a hit inside a topic, because the name is what
    somebody who types a word most likely meant.
    """
    words = [w.lstrip("@") for w in query.lower().split()]
    words = [w for w in words if w]
    if not words:
        return 1
    name = mix.name.lower()
    owner = f"{owner_name} {owner_handle}".lower()
    # A followed subject is findable by its catalogue id as well as its
    # label, so "ai" finds a mix following Artificial Intelligence (`f:ai`).
    topics = " ".join(
        f"{i.title} {i.query} {' '.join(i.focus)} {i.topic_label} "
        f"{i.base[2:].replace('-', ' ') if i.base.startswith('f:') else ''}"
        for i in mix.items
    ).lower()
    whole = " ".join(words)
    score = 0
    for w in words:
        if _has_word(name, w):
            score += 3
        elif _has_word(owner, w):
            score += 2
        elif _has_word(topics, w):
            score += 1
        else:
            return 0
    if whole in name:
        score += 4
    elif whole in topics:
        score += 2
    return score


def _has_word(text: str, word: str) -> bool:
    """Whether some word in `text` starts with `word`: "gy" finds Gym while
    it is being typed, and "ai" does not find "Taiwan" or "daily"."""
    return any(token.startswith(word) for token in re.findall(r"[\w']+", text))


class MixStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("MIXES_DB", "mixes.db", path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS mixes (
                       id         TEXT PRIMARY KEY,
                       user_id    TEXT NOT NULL,
                       name       TEXT NOT NULL,
                       topic_ids  TEXT NOT NULL DEFAULT '',
                       created_at REAL NOT NULL,
                       updated_at REAL NOT NULL
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS mixes_user ON mixes(user_id, created_at)")
            # Mixes shipped holding bank ids only. Typed topics need more than
            # an id, so the full ordered list moved to JSON; `topic_ids` stays
            # as the bank-only view an older row would have written.
            try:
                conn.execute("ALTER TABLE mixes ADD COLUMN items TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE mixes ADD COLUMN public INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            for column in ("cover", "source_id", "source_user"):
                try:
                    conn.execute(f"ALTER TABLE mixes ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
                except sqlite3.OperationalError:
                    pass
            conn.execute("CREATE INDEX IF NOT EXISTS mixes_public ON mixes(public, updated_at)")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def _row_to_mix(self, row) -> Mix:
        items: list[MixItem] = []
        raw = row[6] if len(row) > 6 else ""
        if raw:
            try:
                items = [MixItem.from_dict(d) for d in json.loads(raw)]
            except Exception:
                log.exception("unreadable mix items; falling back to bank ids")
        if not items:
            items = [_bank_item(t) for t in row[3].split(",") if t in BANK_BY_ID]
        return Mix(row[0], row[1], row[2], items, row[4], row[5],
                   bool(row[7]) if len(row) > 7 else False,
                   (row[8] or "") if len(row) > 8 else "",
                   (row[9] or "") if len(row) > 9 else "",
                   (row[10] or "") if len(row) > 10 else "")

    def public_for_user(self, user_id: str) -> list[Mix]:
        """What this listener has chosen to show on their profile."""
        return [m for m in self.list_for_user(user_id) if m.public]

    def list_for_user(self, user_id: str) -> list[Mix]:
        if not user_id:
            return []
        try:
            rows = self._conn().execute(
                "SELECT " + _COLUMNS + " FROM mixes WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            ).fetchall()
        except Exception:
            log.exception("could not list mixes")
            return []
        return [self._row_to_mix(r) for r in rows]

    def get(self, user_id: str, mix_id: str) -> Optional[Mix]:
        try:
            row = self._conn().execute(
                "SELECT " + _COLUMNS + " FROM mixes WHERE id = ? AND user_id = ?",
                (mix_id, user_id),
            ).fetchone()
        except Exception:
            log.exception("could not read mix")
            return None
        return self._row_to_mix(row) if row else None

    def create(self, user_id: str, name: str, topic_ids: Sequence = (),
               cover: str = "") -> Mix:
        if not user_id:
            raise MixError("No listener id; mixes are saved per person.")
        name = clean_name(name)
        items = clean_items(topic_ids)
        cover = clean_cover(cover)
        existing = self.list_for_user(user_id)
        if len(existing) >= MAX_MIXES_PER_USER:
            raise MixError(f"You already have {MAX_MIXES_PER_USER} mixes.")
        if any(m.name.lower() == name.lower() for m in existing):
            raise MixError(f"You already have a mix called {name}.")
        now = time.time()
        mix = Mix(uuid.uuid4().hex[:12], user_id, name, items, now, now, cover=cover)
        self._conn().execute(
            "INSERT INTO mixes (id, user_id, name, topic_ids, created_at, updated_at, items,"
            " cover) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (mix.id, user_id, name, ",".join(mix.topic_ids), now, now,
             json.dumps([i.as_dict() for i in items]), cover),
        )
        return mix

    def update(
        self,
        user_id: str,
        mix_id: str,
        name: Optional[str] = None,
        topic_ids: Optional[Sequence] = None,
        public: Optional[bool] = None,
        cover: Optional[str] = None,
    ) -> Mix:
        """`None` leaves a field as it is; `cover=""` removes the cover."""
        mix = self.get(user_id, mix_id)
        if not mix:
            raise MixError("That mix no longer exists.")
        if name is not None:
            mix.name = clean_name(name)
            clash = [
                m for m in self.list_for_user(user_id)
                if m.id != mix_id and m.name.lower() == mix.name.lower()
            ]
            if clash:
                raise MixError(f"You already have a mix called {mix.name}.")
        if topic_ids is not None:
            mix.items = clean_items(topic_ids)
        if public is not None:
            mix.public = bool(public)
        if cover is not None:
            mix.cover = clean_cover(cover)
        mix.updated_at = time.time()
        self._conn().execute(
            "UPDATE mixes SET name = ?, topic_ids = ?, updated_at = ?, items = ?,"
            " public = ?, cover = ? WHERE id = ? AND user_id = ?",
            (mix.name, ",".join(mix.topic_ids), mix.updated_at,
             json.dumps([i.as_dict() for i in mix.items]), int(mix.public),
             mix.cover, mix_id, user_id),
        )
        return mix

    def get_public(self, mix_id: str, viewer: str = "") -> Optional[Mix]:
        """Anybody's mix by id, but only if its owner made it public - or it
        is the viewer's own. A private mix is not found, which is the same
        answer as a mix that does not exist, on purpose."""
        try:
            row = self._conn().execute(
                "SELECT " + _COLUMNS + " FROM mixes WHERE id = ?", (mix_id,),
            ).fetchone()
        except Exception:
            log.exception("could not read mix")
            return None
        if not row:
            return None
        mix = self._row_to_mix(row)
        if mix.public or (viewer and mix.user_id == viewer):
            return mix
        return None

    def all_public(self, exclude_user: str = "", limit: int = PUBLIC_SCAN) -> list[Mix]:
        """Every public mix, most recently changed first, bar one listener's
        own - DailyFAM's search is for finding *other people's* mixes."""
        try:
            rows = self._conn().execute(
                "SELECT " + _COLUMNS + " FROM mixes WHERE public = 1 AND user_id != ?"
                " ORDER BY updated_at DESC LIMIT ?",
                (exclude_user or "", int(limit)),
            ).fetchall()
        except Exception:
            log.exception("could not list public mixes")
            return []
        return [self._row_to_mix(r) for r in rows]

    def added_from(self, user_id: str) -> set[str]:
        """The ids of the public mixes this listener has already added."""
        return {m.source_id for m in self.list_for_user(user_id) if m.source_id}

    def add_copy(self, user_id: str, source: Mix, owner: str = "") -> Mix:
        """Add somebody else's public mix to this listener's DailyFAM.

        **A copy, not a link.** It is theirs from now on - to play, rename,
        re-cover and edit - and it does not change when the original does or
        vanish when the original is made private or deleted. A mix is a
        routine, and a routine that could be rewritten by somebody else
        overnight is not one you can rely on. `source_id` remembers where it
        came from, which is what stops the same mix being added twice and
        lets the list say whose it was.

        The name is kept where it is free; a clash takes the owner's handle
        ("Gym · @sam") before it takes a number, because a listener with two
        mixes called Gym needs to know which is which.
        """
        if not user_id:
            raise MixError("No listener id; mixes are saved per person.")
        if source.user_id == user_id:
            raise MixError("That mix is already yours.")
        existing = self.list_for_user(user_id)
        if any(m.source_id == source.id for m in existing):
            raise MixError("That mix is already in your DailyFAM.")
        if len(existing) >= MAX_MIXES_PER_USER:
            raise MixError(f"You already have {MAX_MIXES_PER_USER} mixes.")
        taken = {m.name.lower() for m in existing}
        name = source.name
        if name.lower() in taken and owner:
            name = clean_name(f"{source.name} \u00b7 {owner}")
        n = 2
        base = name
        while name.lower() in taken:
            name = clean_name(f"{base[:MAX_NAME - 4]} {n}")
            n += 1
        # A typed topic says "Added by you" under it, which on a copy is
        # somebody else's words about somebody else.
        items = [replace(i, subtitle=f"From {owner}'s mix" if owner else "From the original mix")
                 if i.custom else i for i in source.items]
        now = time.time()
        mix = Mix(uuid.uuid4().hex[:12], user_id, name, items, now, now,
                  cover=source.cover, source_id=source.id, source_user=source.user_id)
        self._conn().execute(
            "INSERT INTO mixes (id, user_id, name, topic_ids, created_at, updated_at, items,"
            " cover, source_id, source_user) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mix.id, user_id, name, ",".join(mix.topic_ids), now, now,
             json.dumps([i.as_dict() for i in items]), mix.cover, source.id,
             source.user_id),
        )
        return mix

    def delete(self, user_id: str, mix_id: str) -> bool:
        cur = self._conn().execute(
            "DELETE FROM mixes WHERE id = ? AND user_id = ?", (mix_id, user_id)
        )
        return bool(cur.rowcount)

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
        for table in ('mixes',):
            try:
                cur = self._conn().execute(
                    f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
                removed += cur.rowcount or 0
            except Exception:
                log.exception("could not erase %s for %r", table, user_id)
        # Other listeners' copies of this listener's public mixes are theirs
        # and stay - but not with this listener's id written into them.
        try:
            self._conn().execute(
                "UPDATE mixes SET source_user = '' WHERE source_user = ?", (user_id,))
        except Exception:
            log.exception("could not clear copies' owner for %r", user_id)
        return removed


#: Offered on an empty playFAM page. Starting from a named example is easier
#: than starting from a blank field, and these are only suggestions - the
#: listener names their own. Subjects to follow, like everything else a mix
#: is offered since §137: a starter made of bank episodes would be a mix of
#: one-off stories, which is the thing a daily mix stopped being.
STARTER_MIXES = (
    ("Morning", ("f:news", "f:stocks", "f:ai")),
    ("At the gym", ("f:nfl", "f:basketball", "f:health")),
    ("Wind down", ("f:music", "f:movies-tv", "f:space")),
)
