"""Sharing an episode outside FAM: a link, the words to send with it, and a card.

Three things a share needs, and this module makes all three:

1. **A link** that opens the episode for somebody who may not have the app.
2. **The words**, written per destination, because a LinkedIn post and a text
   message to one friend are not the same message.
3. **A card**, for the destinations that cannot take a link at all.

## What this deliberately does not do: post anything

FAM holds no Facebook token, no LinkedIn token, no Snapchat token, and asks for
none. It does not post on anybody's behalf, and it cannot.

That is not a gap to be filled later - it is the correct shape. Every platform
here is reached from the phone: iOS hands the app a share sheet, and the story
formats have their own SDK hand-off where the app passes an image and a link
and the *platform's* app does the posting, with the person looking at it. So
the server's job is to produce the payload, and a share is something the
listener completes.

Which means: no OAuth to maintain, no tokens to leak, no scope reviews with
four companies, and nothing that can post while somebody is asleep.

## Stories cannot take a link, and that is why there is a card

Instagram and Snapchat stories are **images**. You cannot post a sentence with
a URL in it; you attach a sticker to a picture. So a story share needs a
picture, and if the app does not make one the listener shares a screenshot of
whatever was on screen - which is a player UI, not an invitation.

`story_card` renders one as SVG: text on FAM's own colours, at 1080x1920. SVG
rather than a rasteriser because it needs no dependency, is a few kilobytes,
and both clients can turn it into a bitmap - a browser through canvas, iOS
through its own renderer. The card is generated per episode and never stored:
it is a function of the title and the question, both of which we already have.

## The link, and what it is allowed to promise

`PUBLIC_BASE_URL` is where the app is reachable from the internet. Unset, a
share still works - the link comes back relative and the interface can still
copy it - but it names no host, and anything showing it must not pretend
otherwise. A share link posted to LinkedIn that resolves to `localhost` is the
kind of quiet failure this project keeps a rule about.

A share row is the unit, not the query string, so an episode can be shared once
and the link stays the same however many places it goes - and so opens can be
counted per share rather than per platform, which is the number that tells you
whether sharing works at all.
"""
from __future__ import annotations

import html
import logging
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from paths import data_path

log = logging.getLogger(__name__)

MAX_TITLE = 200
MAX_QUERY = 500


class ShareError(ValueError):
    """Something the listener can fix, phrased so it can be shown to them."""


@dataclass(frozen=True)
class Target:
    """One destination, and what it can carry.

    `kind` is the thing that actually differs:

    * `link` - a URL with text around it. Facebook, LinkedIn, X.
    * `message` - a private message to somebody. SMS, email, WhatsApp.
    * `story` - an image with a link attached. Instagram, Snapchat.
    * `copy` - the link on its own, for everywhere not listed.
    """

    key: str
    label: str
    kind: str
    #: What the platform will actually show before truncating. 0 means no
    #: practical limit. Enforced here rather than hoped for, because a caption
    #: cut off mid-question reads as a broken app rather than a long post.
    max_chars: int
    #: Whether a picture is required for this to be postable at all.
    needs_image: bool
    #: `{title}`, `{question}`, `{minutes}`, `{url}` are substituted.
    template: str
    #: Only used where the platform has a subject line of its own.
    subject: str = ""
    #: Where to actually send the person, with `{text}`, `{subject}` and
    #: `{url}` substituted and percent-encoded.
    #:
    #: **This is a hand-off, not a post.** Every one of these opens the
    #: platform - its app on a phone, its web composer otherwise - with the
    #: wording already filled in and a human looking at it. FAM still holds no
    #: token and still posts nothing; see the module docstring.
    #:
    #: It lives here rather than in the web app because IOS_APP.md's first
    #: rule is that every feature is an API before it is a screen: a share
    #: sheet written twice is a share sheet that behaves differently on two
    #: clients. Empty means the destination has no URL that works - the two
    #: stories, which take the card, and `copy`, which is the clipboard.
    destination: str = ""


#: The house voice for a share, per destination. These are **defaults the
#: listener edits**, not copy that gets posted unseen - which is why they are
#: written to be finished by somebody rather than to be complete.
#:
#: The shape is the same everywhere and is the argument for pressing play:
#: what it is about, that it is short, and where to hear it. What is
#: deliberately absent is any claim about the episode being good, because FAM
#: did not write that opinion and the person sharing it has not typed one yet.
TARGETS: tuple[Target, ...] = (
    Target("copy", "Copy link", "copy", 0, False,
           "{title} - a {minutes}-minute FAM episode: {url}"),
    # `sms:` with a body is what opens Messages - iMessage on an iPhone, the
    # SMS app anywhere else. The `&` after the empty recipient is not a typo:
    # iOS only parses the body parameter in that form.
    Target("sms", "iMessage", "message", 0, False,
           "Listen to this - {title}. About {minutes} minutes: {url}",
           destination="sms:&body={text}"),
    # `mailto:` rather than a Gmail web URL. It reaches whichever mail app the
    # person actually uses - Gmail included - where mail.google.com/compose
    # would send an Outlook user somewhere they cannot send from.
    Target("email", "Email", "message", 0, False,
           "I asked FAM \"{question}\" and it made a {minutes}-minute episode "
           "answering it.\n\nHave a listen: {url}",
           subject="{title}",
           destination="mailto:?subject={subject}&body={text}"),
    Target("whatsapp", "WhatsApp", "message", 0, False,
           "Listen to this - {title}. About {minutes} minutes: {url}",
           destination="https://wa.me/?text={text}"),
    # 280 including the URL, which platforms shorten to a fixed length; the
    # template is kept well under so an edited version still fits.
    Target("x", "X", "link", 240, False,
           "{question}\n\nFAM made me a {minutes}-minute episode on it. {url}",
           destination="https://x.com/intent/post?text={text}"),
    # Facebook's sharer takes the URL and builds its own preview from the
    # page; `quote` has not been honoured for years. So, like LinkedIn below,
    # the composed words go to the clipboard alongside rather than into a
    # query string that drops them without saying so.
    Target("facebook", "Facebook", "link", 0, False,
           "I asked FAM \"{question}\" - here is the {minutes}-minute answer. {url}",
           destination="https://www.facebook.com/sharer/sharer.php?u={url}"),
    # Long-form by convention, and the one place a share reads as a post rather
    # than a message, so the template leaves an obvious place to add a thought.
    # LinkedIn takes only the URL and then reads the page for its own preview,
    # so the composed text goes to the clipboard alongside rather than into
    # the query string, where it would be dropped in silence.
    Target("linkedin", "LinkedIn", "link", 2800, False,
           "\"{question}\"\n\nFAM turned that into a {minutes}-minute briefing. "
           "Worth a listen if you have been wondering the same thing.\n\n{url}",
           destination="https://www.linkedin.com/feed/?shareActive=true&shareUrl={url}"),
    # No `destination` for either, and that is not an omission. A story is an
    # image handed to the platform's own SDK - `instagram-stories://share` and
    # Snapchat's Creative Kit - which takes the picture and the sticker link
    # as data, not as a URL. A web build can only open the card; the iOS app
    # does the hand-off. `needs_image` is what tells a client which it is.
    Target("instagram_story", "Instagram story", "story", 0, True,
           "{title}"),
    Target("snapchat_story", "Snapchat story", "story", 0, True,
           "{title}"),
)

TARGET_KEYS: tuple[str, ...] = tuple(t.key for t in TARGETS)
_BY_KEY = {t.key: t for t in TARGETS}


def target(key: str) -> Optional[Target]:
    return _BY_KEY.get(key)


def render(target_key: str, *, title: str, question: str, minutes: int,
           url: str) -> dict:
    """The text to hand the platform, trimmed to what it will show.

    Trimming happens on a word boundary and adds an ellipsis, because a caption
    that stops mid-word looks like the app broke rather than like the platform
    has a limit.
    """
    chosen = target(target_key)
    if chosen is None:
        raise ShareError(f"Unknown share destination {target_key!r}.")
    values = {
        "title": (title or "A FAM episode").strip()[:MAX_TITLE],
        "question": (question or "").strip()[:MAX_QUERY],
        "minutes": max(1, int(minutes or 0)),
        "url": url or "",
    }
    return _rendered(chosen, chosen.template, chosen.subject, values)


#: The same destinations, worded for a whole DailyFAM mix rather than one
#: episode: `(template, subject)` per target key, with `{name}`, `{topics}`
#: and `{url}` substituted. A mix is a standing list, so the pitch is "a fresh
#: briefing every day on these", and the ask is to add it rather than to
#: listen once. The owner is the one sharing it, hence "my".
MIX_TEMPLATES: dict[str, tuple[str, str]] = {
    "copy": ("{name} - a daily FAM mix: {topics}. {url}", ""),
    "sms": ("My daily FAM mix, {name} - a new briefing every day on {topics}. "
            "Add it to yours: {url}", ""),
    "email": ("I made a daily mix on FAM called \"{name}\". Every day it is a "
              "fresh briefing on {topics}.\n\nAdd it to your DailyFAM: {url}",
              "{name} - a daily FAM mix"),
    "whatsapp": ("My daily FAM mix, {name} - a new briefing every day on {topics}. "
                 "Add it to yours: {url}", ""),
    "x": ("My daily FAM mix, {name}: {topics}. {url}", ""),
    "facebook": ("My daily FAM mix, {name} - a fresh briefing every day on {topics}. {url}", ""),
    "linkedin": ("\"{name}\" is my daily FAM mix - a fresh briefing every morning on "
                 "{topics}.\n\n{url}", ""),
    "instagram_story": ("{name}", ""),
    "snapchat_story": ("{name}", ""),
}


def mix_topics_line(titles: list[str], shown: int = 4) -> str:
    """"NFL · Eagles, AI updates, Stocks and 2 more" - what a mix is about,
    short enough to sit inside one sentence of a share."""
    titles = [t for t in titles if t]
    if not titles:
        return "whatever I add to it"
    head = titles[:shown]
    rest = len(titles) - len(head)
    if rest > 0:
        return ", ".join(head) + f" and {rest} more"
    if len(head) == 1:
        return head[0]
    return ", ".join(head[:-1]) + " and " + head[-1]


def render_mix(target_key: str, *, name: str, topics: list[str], url: str) -> dict:
    """One destination's wording for sharing a whole mix. Same trimming, same
    hand-off rules as an episode - only the words differ."""
    chosen = target(target_key)
    if chosen is None:
        raise ShareError(f"Unknown share destination {target_key!r}.")
    template, subject = MIX_TEMPLATES.get(chosen.key, (chosen.template, chosen.subject))
    values = {
        "name": (name or "A DailyFAM mix").strip()[:MAX_TITLE],
        "topics": mix_topics_line(list(topics))[:MAX_QUERY],
        "url": url or "",
    }
    return _rendered(chosen, template, subject, values)


def _rendered(chosen: Target, template: str, subject_template: str,
              values: dict) -> dict:
    text = template.format(**values)
    if chosen.max_chars and len(text) > chosen.max_chars:
        keep = text[:chosen.max_chars - 1]
        # Never cut the URL off: a share whose link is truncated is worse than
        # a share with a shorter sentence, so the trim is taken out of the text
        # and the link is re-attached.
        if values["url"] and values["url"] not in keep:
            room = chosen.max_chars - len(values["url"]) - 4
            keep = text.split(values["url"])[0][:max(0, room)]
            keep = keep.rsplit(" ", 1)[0] + "… " + values["url"]
        else:
            keep = keep.rsplit(" ", 1)[0] + "…"
        text = keep
    subject = subject_template.format(**values) if subject_template else ""
    return {
        "target": chosen.key,
        "label": chosen.label,
        "kind": chosen.kind,
        "needs_image": chosen.needs_image,
        "text": text,
        "subject": subject,
        "url": values["url"],
        # Where to send them. Composed here so both clients open the same
        # place with the same wording - see `Target.destination`.
        "destination": destination_for(chosen, text=text, subject=subject,
                                       url=values["url"]),
    }


def destination_for(chosen: Target, *, text: str, subject: str,
                    url: str) -> str:
    """The hand-off URL for one rendered share, or "" where there is none.

    Every substituted value is percent-encoded, including into `mailto:` and
    `sms:` bodies: a question with an `&` in it truncates the body everywhere
    it is not, and a share that arrives half-written reads as a broken app
    rather than as a punctuation problem.

    **A link that is not public produces no hand-off at all.** Without
    `PUBLIC_BASE_URL` the share link comes back relative - `/s/abc123` - and
    handing that to Facebook or LinkedIn opens their composer around a URL
    they cannot resolve. The listener then finds out on somebody else's site
    that FAM's link is broken, which is the worst possible place to learn it.
    The sheet already says the link is not public; this stops it opening a
    door onto that. The clipboard still works, so a deploy being tested is not
    blocked, it just never pretends.
    """
    if not chosen.destination:
        return ""
    if not is_public_link(url):
        return ""
    return chosen.destination.format(
        text=quote(text, safe=""),
        subject=quote(subject, safe=""),
        url=quote(url, safe=""),
    )


def is_public_link(url: str) -> bool:
    """Whether this URL means anything to somebody who is not on this network.

    Absolute and http(s). A relative path is the shape `share_url` returns
    when `PUBLIC_BASE_URL` is unset, and it is exactly what must never be
    posted anywhere.
    """
    return str(url or "").lower().startswith(("http://", "https://"))


# --- the story card -------------------------------------------------------

def _wrap(text: str, per_line: int, max_lines: int) -> list[str]:
    """Greedy word wrap. Good enough for a card and it needs no font metrics -
    which would mean a rendering dependency for a picture made of five lines
    of text."""
    words = str(text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if len(candidate) <= per_line:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(words)) > sum(len(l) + 1 for l in lines):
        lines[-1] = lines[-1].rstrip(" ,.") + "…"
    return lines


def story_card(title: str, question: str, minutes: int, handle: str = "",
               pill: str = "") -> str:
    """A 1080x1920 story image, as SVG.

    Portrait and full-bleed because that is the only shape a story is. The
    colours are FAM's own from `static/index.html`, restated here rather than
    imported: a share card that drifts from the app's palette looks like
    somebody else's product, and a stylesheet is not reachable from a server
    that renders this without a browser.

    Everything is escaped - the question is text a listener typed, and this is
    markup.
    """
    lines = _wrap(title or "A FAM episode", per_line=18, max_lines=4)
    ask = _wrap(question or "", per_line=34, max_lines=2)
    minutes = max(1, int(minutes or 0))
    esc = html.escape

    title_svg = "".join(
        f'<tspan x="90" dy="{0 if i == 0 else 104}">{esc(line)}</tspan>'
        for i, line in enumerate(lines))
    ask_svg = "".join(
        f'<tspan x="90" dy="{0 if i == 0 else 46}">{esc(line)}</tspan>'
        for i, line in enumerate(ask))
    who = esc(("@" + handle.lstrip("@")) if handle else "on FAM")
    # The rounded label under the question: an episode's length, or what a
    # mix is ("Daily mix") - anything short enough for the same pill.
    pill = (pill or f"{minutes} min")[:24]

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1920" viewBox="0 0 1080 1920">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0.6" y2="1">
      <stop offset="0%" stop-color="#2A2733"/>
      <stop offset="55%" stop-color="#1E1B27"/>
      <stop offset="100%" stop-color="#38334A"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.2" cy="0.12" r="0.7">
      <stop offset="0%" stop-color="#E0B563" stop-opacity="0.20"/>
      <stop offset="100%" stop-color="#E0B563" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="1080" height="1920" fill="url(#bg)"/>
  <rect width="1080" height="1920" fill="url(#glow)"/>

  <text x="90" y="250" fill="#E0B563" font-family="'Space Grotesk',Helvetica,Arial,sans-serif"
        font-size="34" font-weight="700" letter-spacing="6">FAM</text>

  <text x="90" y="700" fill="#F4EFE4" font-family="Georgia,'Times New Roman',serif"
        font-size="92" font-weight="600">{title_svg}</text>

  <text x="90" y="1180" fill="#ABA3C4" font-family="'Space Grotesk',Helvetica,Arial,sans-serif"
        font-size="38">{ask_svg}</text>

  <rect x="90" y="1320" width="{60 + len(pill) * 18}" height="64" rx="32"
        fill="none" stroke="#E0B563" stroke-width="2"/>
  <text x="120" y="1362" fill="#E0B563"
        font-family="'Space Grotesk',Helvetica,Arial,sans-serif" font-size="30"
        font-weight="600">{esc(pill)}</text>

  <text x="90" y="1700" fill="#8FAE9A" font-family="'Space Grotesk',Helvetica,Arial,sans-serif"
        font-size="32" font-weight="500">{who}</text>
  <text x="90" y="1760" fill="#8A83A0" font-family="'Space Grotesk',Helvetica,Arial,sans-serif"
        font-size="28">Ask anything. Hear the answer.</text>
</svg>'''


# --- the store ------------------------------------------------------------

class ShareStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("SHARES_DB", "shares.db", path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS shares (
                       id      TEXT PRIMARY KEY,
                       user_id TEXT NOT NULL,
                       query   TEXT NOT NULL,
                       minutes INTEGER NOT NULL DEFAULT 0,
                       title   TEXT NOT NULL DEFAULT '',
                       created REAL NOT NULL,
                       opens   INTEGER NOT NULL DEFAULT 0
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS shares_user"
                         " ON shares(user_id, created)")
            # One share per episode per listener, so sharing the same thing to
            # four platforms produces one link with four destinations rather
            # than four links whose open counts have to be added up.
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS shares_once"
                         " ON shares(user_id, query, minutes)")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def create(self, user_id: str, query: str, minutes: int, title: str = "",
               at: float = 0.0) -> dict:
        query = " ".join(str(query or "").split())[:MAX_QUERY]
        if not query:
            raise ShareError("There is no episode to share.")
        minutes = max(0, int(minutes or 0))
        title = " ".join(str(title or "").split())[:MAX_TITLE]
        now = at or time.time()

        existing = self._conn().execute(
            "SELECT id FROM shares WHERE user_id = ? AND query = ? AND minutes = ?",
            (user_id, query, minutes)).fetchone()
        if existing:
            return self.get(existing[0]) or {}

        share_id = secrets.token_urlsafe(9)
        self._conn().execute(
            "INSERT INTO shares (id, user_id, query, minutes, title, created)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (share_id, user_id, query, minutes, title, now))
        return self.get(share_id) or {}

    def get(self, share_id: str) -> Optional[dict]:
        try:
            row = self._conn().execute(
                "SELECT id, user_id, query, minutes, title, created, opens"
                " FROM shares WHERE id = ?", (share_id,)).fetchone()
        except Exception:
            log.exception("could not read a share")
            return None
        if not row:
            return None
        return {"id": row[0], "user_id": row[1], "query": row[2],
                "minutes": row[3], "title": row[4], "created": row[5],
                "opens": row[6]}

    def opened(self, share_id: str) -> None:
        """Somebody followed the link. The only number here, and it is the one
        that says whether sharing does anything at all."""
        try:
            self._conn().execute(
                "UPDATE shares SET opens = opens + 1 WHERE id = ?", (share_id,))
        except Exception:
            log.exception("could not count a share open")

    def forget(self, user_id: str) -> int:
        try:
            cur = self._conn().execute("DELETE FROM shares WHERE user_id = ?",
                                       (user_id,))
            return cur.rowcount or 0
        except Exception:
            log.exception("could not erase shares for %r", user_id)
            return 0


# --- the landing page -----------------------------------------------------
#
# Where a shared link actually goes, and the one place in FAM that is *not*
# the app.
#
# ## Tracing a link back to its episode, without inventing an episode id
#
# There is no episode id in this product, and adding one to carry a share
# would be a second identity for a thing that already has one. An episode is
# identified by its cache key, and `pipeline.key_for` builds that key from the
# question and the length - so a share row holding `query` and `minutes` *is*
# a pointer at the episode, resolved the same way every other surface resolves
# one. Following the link calls `/api/audio?q=<query>&minutes=<minutes>`, the
# pipeline computes the same key, and the sharer's own script comes back out
# of the shared cache. Same words, no second model call, nothing new stored.
#
# That is also why a share costs one row: it records a *question*, not audio
# and not a script. Ten people opening the link is ten syntheses against one
# cached script, which is the cost design the whole app rests on.
#
# ## Why the page is rendered here rather than fetched by the page
#
# Facebook and LinkedIn read the shared page to build their own preview and
# ignore anything in the query string (SHARING.md says so, and §96 caught
# Facebook doing it). A crawler does not run JavaScript, so a landing page
# that fetched its own title would be posted everywhere as whatever the
# fallback markup said. The title, the question and the card therefore have to
# be *in the HTML the server sends*, which means substituted here.
#
# The same substitution carries the payload the player needs, so the page does
# not spend a round trip discovering what it is before it can start - the
# one-sentence spec applies to a stranger's first second of FAM more than to
# anybody else's.

#: Substituted in `static/listen.html`. Comments rather than a template
#: language: the file has to stay a page a browser can open directly, so that
#: the preview build and `tools/check_js.py` see the same markup the server
#: sends.
HEAD_MARKER = "<!--FAM_SHARE_HEAD-->"
DATA_MARKER = "<!--FAM_SHARE_DATA-->"


def landing_payload(share: dict, *, url: str, card_url: str = "",
                    app_store: str = "") -> dict:
    """Everything the landing page is allowed to know.

    Note what is absent: `user_id`. A share link is public by construction -
    that is the entire point of it - and the listener id behind it is the one
    the whole app authenticates with. Authorship is provenance and never
    identity (PROBLEMS.md §95), and a share row is the one place that rule
    could be broken by accident, because the row has the id sitting right
    there next to the question.

    `app_store` empty is a real state and not a missing value: see
    `landing_doors`.
    """
    return {
        "id": str(share.get("id") or ""),
        "title": (share.get("title") or "A FAM episode").strip()[:MAX_TITLE],
        "question": (share.get("query") or "").strip()[:MAX_QUERY],
        "minutes": max(1, int(share.get("minutes") or 0)),
        "url": url or "",
        "card": card_url or "",
        "app_store": app_store or "",
        # Whether there is anywhere to send somebody who presses something
        # that is not play. False means the page draws no such control at all.
        "has_app": bool(app_store),
    }


def landing_doors(app_store: str) -> bool:
    """Whether the landing page may draw anything but the player.

    Every control on that page except play is a door to the App Store, so with
    no App Store link there are no doors - not doors that go nowhere, and not
    doors quietly rerouted into the web app, which is the thing the page
    exists to *not* be.

    This is the settled rule twice over: nothing invents a host
    (`PUBLIC_BASE_URL` keeps it for share links), and a control with nothing
    behind it is worse than no control - which took a fixture folder called
    "Commute" and three invented contacts out of this app already. A stranger
    following a link from LinkedIn is the worst possible audience for a button
    that 404s.
    """
    return bool(str(app_store or "").strip())


def _json_for_script(payload: dict) -> str:
    """JSON that cannot end the `<script>` element it is embedded in.

    The question is text a listener typed. `</script>` inside it would close
    the block and put the rest of the question into the document as markup;
    escaping the slash is the standard fix and survives `JSON.parse` because
    `<\\/` and `</` are the same string to it. `<!--` gets the same treatment:
    it opens a comment in the legacy HTML script grammar.
    """
    import json

    text = json.dumps(payload, ensure_ascii=False)
    return (text.replace("<", "\\u003c").replace(">", "\\u003e")
                .replace("&", "\\u0026"))


def landing_head(payload: dict) -> str:
    """The preview card Facebook, LinkedIn, X and iMessage build the link from.

    Server-rendered because none of them run JavaScript, and because this is
    the only thing standing between a well-composed share and a post that says
    whatever `<title>` happened to hold.

    `og:image` is only claimed when the card URL is absolute. A relative image
    in an Open Graph tag is not resolved by most crawlers, so a deployment
    without `PUBLIC_BASE_URL` would advertise a picture that never loads -
    which is the same failure `destination_for` already refuses to produce.
    """
    esc = html.escape
    title = esc(payload.get("title") or "A FAM episode")
    question = (payload.get("question") or "").strip()
    minutes = payload.get("minutes") or 1
    description = esc(
        f"{question} - a {minutes}-minute FAM episode." if question
        else f"A {minutes}-minute FAM episode.")
    url = payload.get("url") or ""
    card = payload.get("card") or ""

    tags = [
        f'<title>{title} - FAM</title>',
        f'<meta name="description" content="{description}">',
        '<meta property="og:site_name" content="FAM">',
        '<meta property="og:type" content="music.song">',
        f'<meta property="og:title" content="{title}">',
        f'<meta property="og:description" content="{description}">',
    ]
    if is_public_link(url):
        tags.append(f'<meta property="og:url" content="{esc(url)}">')
    if is_public_link(card):
        tags.append(f'<meta property="og:image" content="{esc(card)}">')
        tags.append('<meta property="og:image:width" content="1080">')
        tags.append('<meta property="og:image:height" content="1920">')
        tags.append('<meta name="twitter:card" content="summary_large_image">')
        tags.append(f'<meta name="twitter:image" content="{esc(card)}">')
    else:
        tags.append('<meta name="twitter:card" content="summary">')
    tags.append(f'<meta name="twitter:title" content="{title}">')
    tags.append(f'<meta name="twitter:description" content="{description}">')
    return "\n  ".join(tags)


def render_landing(template: str, payload: dict) -> str:
    """`static/listen.html` with this episode's head and payload in it.

    Substitution rather than a template engine, and markers that are HTML
    comments, so the file on disk stays openable and checkable on its own.
    """
    page = template.replace(HEAD_MARKER, landing_head(payload))
    data = (f'<script>window.FAM_SHARE = {_json_for_script(payload)};'
            f'</script>')
    return page.replace(DATA_MARKER, data)
