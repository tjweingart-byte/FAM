"""Communities whose admins might share FAM with their members (`/admin/groups`).

## What this is

FAM's outreach is to the people who run communities: a Facebook group, a
subreddit, an X Community, a Discord server, a Slack community. An admin who
shares an episode with their group is the most natural marketing FAM can have,
so this page finds communities about the subjects FAM covers, says how a
person can reach each one's admins through the community's own public
channel, and hypothesises three episodes worth showing them. A person reaches
out; FAM never does.

It replaced the reply-drafting X agent (`x_threads.py`, §321-§368) at the
owner's direction (§579): X is now one platform among five, searched the same
way, for communities rather than threads.

## Rules that are load-bearing

**Public channels only** ([community-outreach]). Discovery is a web search
through FAM's own research backends (`research.WEB_BACKENDS`), and Reddit's
and X's own APIs where their read-only keys are set (§581,
`community_sources.py`), never a logged-in scrape: nothing here signs in to
Facebook, Reddit, X, Discord or Slack, and no key held can post or message. A card says *how* to
reach the admins - the group's admin list, Reddit's modmail, the
community's moderators page, its own rules - and a person does it from FAM's
account with the community in front of them.

**No personal contact details are collected.** A phone number is never
stored, and an email only when the community itself publishes it as its
contact on its own page (`public_contacts`): `scrub` runs over every text
kept, the team's notes included. Only the community's own page speaks for
it (`own_page`) - a member's post on it gives its address and nothing else -
and an admin's name is kept only when that page states it (`admin_hints`). Nothing guesses
an address, looks a person up elsewhere, or enriches a name.

**Episodes are hypothesised, never written.** Each card carries three
pitches - a title, an angle, why it fits this community - and a kept episode
only when one FAM already has matches the community; the card says the
pitches are not written. Nothing here generates an episode.

**Asked when somebody is looking** ([sweep-on-demand]): a search is a button,
never a timer, capped per run (`COMMUNITIES_MAX_SEARCHES`) and per day
(`COMMUNITIES_DAILY_SEARCHES`) because each search is billed.

**Staging spends nothing** ([zero-spend-staging]): the search keys are
scrubbed there, and a run says so rather than pretending it found nothing.

**A candidate carries its reason in words** ([candidate-reason]).

**Egan reads each community** (§580, the outreach agent): the fit, the
pitches and the message are his, through `agents/models.py` under his
signed rule book (`egan.md`), off by default. He never reads about a
person: the material he is given is `redacted` of names, handles, addresses
and numbers, and code sends back an answer that carries one.

**A layer that adds quality must not subtract availability**: whenever
Egan cannot answer - off, unsigned, held, failed - the pitches and the
message are composed in code, and the card says why.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, Optional
from urllib.parse import quote

import config
import db as store_db
from config import as_minutes
from paths import data_path

log = logging.getLogger("fam.communities")

#: The share links this page makes are owned by this id, never a listener's.
SHARER = "fam-outreach"

#: Statuses, in the order a lead moves through them. `set_aside` is a lead
#: the read judged a poor fit: kept so the next run neither reads nor judges
#: it again, listed for audit, never actionable.
NEW, CONTACTED, REPLIED, INVITED, JOINED, PASSED = (
    "new", "contacted", "replied", "invited", "joined", "passed")
SET_ASIDE = "set_aside"
STATUSES = (NEW, CONTACTED, REPLIED, INVITED, JOINED, PASSED)
STATUS_LABELS = {
    NEW: "To contact", CONTACTED: "Contacted", REPLIED: "Replied",
    INVITED: "Invited", JOINED: "Joined", PASSED: "Passed",
}

#: Where a lead may move from each status: the page's buttons, enforced on
#: the server too. Staying put (saving notes) is always allowed.
MOVES = {NEW: {CONTACTED, PASSED}, CONTACTED: {REPLIED, PASSED},
         REPLIED: {INVITED, PASSED}, INVITED: {JOINED, PASSED},
         JOINED: set(), PASSED: {NEW}}

#: How long a lead nobody acted on is kept; a lead a person moved is kept.
KEEP_DAYS = 30

#: A fit from 1 to 5. Below this the lead is set aside.
FIT_FLOOR = 3
#: A fit the code gives when no model read the community.
CODE_FIT = 3

#: Pitches per card.
PITCHES = 3
#: A kept episode is offered on a card only at this match or better.
EPISODE_MATCH = 0.35

#: The message's ceiling: a first direct message, read in a notification.
MESSAGE_CHARS = 700

#: Members, where the search shows them. A community smaller than this has
#: nobody to share with; there is no ceiling - a big group's admin is still
#: one person to write to.
MIN_MEMBERS = 300

#: Subjects FAM does not walk into uninvited, whatever it covers.
AVOID = (
    "grief", "bereavement", "suicide", "self harm", "self-harm", "nsfw",
    "18+", "onlyfans", "porn", "escort", "gambling tips", "betting tips",
    "crypto signals", "pump", "recovery support", "support group", "victims",
)


# --------------------------------------------------------------------------
# The platforms


@dataclass(frozen=True)
class Platform:
    key: str
    label: str
    #: What one community is called there.
    noun: str
    #: Added to a subject to search for its communities.
    words: str
    #: Domains the search is held to, or () for the whole web.
    domains: tuple
    #: How a person reaches the admins, in words, for the card.
    how: str


PLATFORMS: dict[str, Platform] = {
    "facebook": Platform(
        "facebook", "Facebook", "group", "Facebook group",
        ("facebook.com",),
        "Open the group's admin list from FAM's Facebook account and message an "
        "admin there. Join first if the group asks members to."),
    "reddit": Platform(
        "reddit", "Reddit", "subreddit", "subreddit",
        ("reddit.com",),
        "Message the moderators through the subreddit's modmail from FAM's "
        "Reddit account. Read its rules on self-promotion first."),
    "x": Platform(
        "x", "X", "community", "X Community",
        ("x.com", "twitter.com"),
        "Open the community's moderators from FAM's X account and message the "
        "admin, or reply to one of their posts if their messages are closed."),
    "discord": Platform(
        "discord", "Discord", "server", "Discord server",
        ("discord.com", "discord.gg", "disboard.org"),
        "Join from FAM's Discord account, read the rules channel, then message "
        "the owner or a moderator, or post in a partnerships channel if it has one."),
    "slack": Platform(
        "slack", "Slack", "workspace", "Slack community",
        (),
        "Use the community's own contact page or organiser's address on its "
        "website; ask to join before messaging its admins in Slack."),
}

#: Path words that are a platform's own pages, never a community.
_NOT_COMMUNITIES = {
    "facebook": {"feed", "discover", "joins", "search", "create", "category",
                 "groups", "your_groups"},
    "reddit": {"all", "popular", "subreddits", "random", "friends", "mod"},
    "slack": {"app", "api", "www", "join", "status", "slack", "my", "get",
              "help", "files", "hooks", "edgeapi", "a", "slackhq"},
}

_URLS = (
    ("facebook", re.compile(r"facebook\.com/groups/([A-Za-z0-9._-]{3,80})", re.I)),
    ("reddit", re.compile(r"reddit\.com/r/([A-Za-z0-9_]{2,21})(?![A-Za-z0-9_])", re.I)),
    ("x", re.compile(r"(?<![\w-])(?:x|twitter)\.com/i/communities/(\d{5,25})", re.I)),
    ("discord", re.compile(
        r"(?:discord\.gg|discord(?:app)?\.com/invite)/([A-Za-z0-9-]{2,40})", re.I)),
    ("discord", re.compile(r"disboard\.org/server/(\d{15,22})", re.I)),
    ("slack", re.compile(r"join\.slack\.com/t/([a-z0-9-]{2,40})", re.I)),
    ("slack", re.compile(r"(?<![\w.-])([a-z0-9-]{2,40})\.slack\.com", re.I)),
)


def community_of(text: str) -> list[tuple[str, str]]:
    """`[(platform, community id)]` named in a URL or a passage, first
    mention first, each once. A platform's own pages are not communities."""
    out: list[tuple[str, str]] = []
    for platform, pattern in _URLS:
        for m in pattern.finditer(text or ""):
            ident = m.group(1).strip(".-_")
            if not ident or ident.lower() in _NOT_COMMUNITIES.get(platform, ()):
                continue
            if platform == "discord" and "disboard" in m.group(0).lower():
                ident = "disboard:" + ident
            pair = (platform, ident.lower() if platform in ("reddit", "slack") else ident)
            if pair not in out:
                out.append(pair)
    out.sort(key=lambda p: (text or "").lower().find(p[1].split(":")[-1].lower()))
    return out


def community_url(platform: str, ident: str) -> str:
    """The community's own page."""
    if platform == "facebook":
        return f"https://www.facebook.com/groups/{ident}"
    if platform == "reddit":
        return f"https://www.reddit.com/r/{ident}"
    if platform == "x":
        return f"https://x.com/i/communities/{ident}"
    if platform == "discord":
        if ident.startswith("disboard:"):
            return f"https://disboard.org/server/{ident.split(':', 1)[1]}"
        return f"https://discord.gg/{ident}"
    if platform == "slack":
        return f"https://{ident}.slack.com"
    return ""


def contact_url(platform: str, ident: str) -> str:
    """The community's own public way to its admins, or its page when the
    platform has no address for that. Never a person's profile."""
    if platform == "facebook":
        return community_url(platform, ident) + "/members/admins"
    if platform == "reddit":
        return "https://www.reddit.com/message/compose?to=" + quote(f"/r/{ident}", safe="")
    if platform == "x":
        return community_url(platform, ident) + "/moderators"
    return community_url(platform, ident)


# --------------------------------------------------------------------------
# What a search result says about a community


_COUNT = re.compile(
    r"(\d[\d,.]*)\s*([kKmM])?\+?\s*(?:total\s+)?"
    r"(members|member|subscribers|readers|followers)\b", re.I)

#: A run of digits that may be a phone number: an optional +country code,
#: an optional (area), then groups of digits joined by spaces, dots or
#: dashes. `_is_phone` decides on the digits: 7 to 15 of them, and not a date.
#: Never inside a URL or an id (`/` or `=` before it), and never with a comma
#: (a member count).
_PHONE = re.compile(
    r"(?<![\w/=.+-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,5}\)[\s.-]?)?\d{1,11}(?:[\s.-]\d{1,5}){0,6}"
    r"(?![\w/-])")
_DATE = re.compile(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}$|^\d{1,2}[-./]\d{1,2}[-./]\d{2,4}$")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
#: Words that make an email on a community's page its contact.
_CONTACT_WORDS = re.compile(
    r"\b(contact|email|e-mail|reach|questions|enquir|inquir|partnership|"
    r"sponsor|press|media|advertis|collab|admins?|mods?|moderators?|organi[sz]er)\b", re.I)
#: One person as a page names them: a handle, or a name of one to three
#: capitalised words, perhaps with a title.
_PERSON = (r"(?:(?:u/|@)[\w.-]{2,30}"
           r"|(?:(?:Dr|Mr|Mrs|Ms|Mx|Prof)\.?\s+)?[A-Z][a-z'’-]+(?:\s+[A-Z][a-z'’-]+){0,2})")
#: A role and the people after it. "Admin: Sarah", "mods are u/a and u/b",
#: "Run by Dr. Jane Smith and Bob Jones". A role word with no separator
#: ("Admin Approval Required") names nobody.
_ADMIN = re.compile(
    r"(?:\b(?i:admin(?:istrator)?s?|moderators?|mods?|organi[sz]ers?)\s*(?i:is|are|:|-|–)\s*"
    r"|\b(?i:run|founded|created|organi[sz]ed|hosted|moderated|managed)\s+(?i:by)\s+)"
    r"(" + _PERSON + r"(?:\s*(?:,|&|\b(?i:and)\b)\s*" + _PERSON + r"){0,4})")
#: Words that make a "name" a community, not a person ("Moderators: Fantasy
#: Football Talk", "Created by Taylor Swift Fans").
_NOT_PEOPLE = frozenset("""
fans fan group groups club team community communities talk network nation society
official members member page forum league approval required rules staff crew
admins mods moderators everyone us all channel server subreddit discord slack
facebook reddit twitter official news daily weekly the our this
""".split())
#: Profile links: a person's page on a platform, never a community's.
_PROFILE = re.compile(
    r"(?:https?://)?(?:[\w-]+\.)?(?:reddit\.com/(?:u|user)/[\w-]+"
    r"|(?:x|twitter)\.com/(?!i/)[A-Za-z0-9_]{2,15}(?![\w/])"
    r"|facebook\.com/(?!groups/)(?:profile\.php\?id=\d+|[A-Za-z0-9.]{3,50}))", re.I)


def _is_phone(match: "re.Match") -> bool:
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)
    if not 7 <= len(digits) <= 15 or _DATE.match(raw.strip()):
        return False
    # Digits alone, with no separator and no +: a phone only at 10-11 digits
    # (a bare US number); shorter is a count or a year, longer an id.
    if raw.isdigit():
        return len(raw) in (10, 11)
    return True


def strip_phones(text: str) -> str:
    """`text` with every phone number removed: FAM never keeps one."""
    return _PHONE.sub(lambda m: "[number removed]" if _is_phone(m) else m.group(0), text or "")


def scrub(text: str, keep: Iterable[str] = ()) -> str:
    """`text` as FAM may keep it: no phone number, and no email address but
    the community's own published contacts (`keep`)."""
    kept = {k.lower() for k in keep}
    out = strip_phones(text)
    return _EMAIL.sub(lambda m: m.group(0) if m.group(0).strip(".").lower() in kept
                      else "[address removed]", out)


def members(text: str) -> int:
    """The largest member count a passage states, or 0."""
    best = 0
    for m in _COUNT.finditer(text or ""):
        raw = m.group(1).replace(",", "")
        try:
            n = float(raw)
        except ValueError:
            continue
        unit = (m.group(2) or "").lower()
        n *= 1000 if unit == "k" else 1_000_000 if unit == "m" else 1
        best = max(best, int(n))
    return best


def privacy(text: str) -> str:
    low = (text or "").lower()
    if "private group" in low or "private community" in low:
        return "private"
    if "public group" in low or "public community" in low:
        return "public"
    return ""


def public_contacts(text: str) -> list[str]:
    """Emails a community's own page publishes as its contact: an address
    within a few words of "contact", "email", "admins" and the like. An
    address in passing is not a contact, and is not kept."""
    found = []
    for m in _EMAIL.finditer(text or ""):
        # The sentence the address is in, up to 60 characters before it: a
        # "contact" two sentences earlier is not this address's.
        before = (text or "")[max(0, m.start() - 60):m.start()]
        before = re.split(r"[.!?]\s", before)[-1]
        near = before + m.group(0)
        address = m.group(0).strip(".").lower()
        if _CONTACT_WORDS.search(near) and address not in found:
            found.append(address)
    return found[:2]


def admin_hints(text: str, community: str = "") -> list[str]:
    """Names or handles the page itself states as its admins, organisers or
    moderators. Read from the page, never looked up. A "name" made of a
    community's words (its own name, "fans", "team") is not a person."""
    ours = _words(community)
    out = []
    for m in _ADMIN.finditer(text or ""):
        for name in re.split(r"\s*(?:,|&|\band\b)\s*", m.group(1)):
            name = name.strip()
            words = {w.lower().strip(".") for w in name.split()}
            if (not name or name in out or words & _NOT_PEOPLE
                    or (not name.startswith(("u/", "@")) and words <= ours)):
                continue
            out.append(name)
    return out[:5]


def _clean_name(title: str, platform: str) -> str:
    """A result's title as the community's name: the platform's suffixes off."""
    name = re.sub(r"\s*[|\-–:]\s*(r/\S+|Facebook|Reddit|X|Twitter|Discord|Slack|DISBOARD)"
                  r"(\s.*)?$", "", title or "", flags=re.I)
    name = re.sub(r"^(r/|Join the |Join )", "", name.strip(), flags=re.I)
    return " ".join(name.split())[:120] or ""


# --------------------------------------------------------------------------
# A lead

#: Where a lead came from.
SEARCHED, SENT = "search", "sent"


@dataclass
class Lead:
    platform: str
    ident: str
    name: str
    url: str
    contact_url: str
    subject: str
    description: str = ""
    members: int = 0
    privacy: str = ""
    seen_at: str = ""
    public_contact: list = field(default_factory=list)
    admin_hint: list = field(default_factory=list)
    episode: Optional[dict] = None
    pitches: list = field(default_factory=list)
    message: str = ""
    fit: int = CODE_FIT
    fit_why: str = ""
    drafted_by: str = "code"
    score: float = 0.0
    reason: str = ""
    #: `search` (found by a run) or `sent` (a person sent it to Egan, §581).
    source: str = SEARCHED
    #: What the person who sent it said about it.
    note: str = ""
    #: What the lead's facts were read from: "web search", "Reddit's API"...
    looked_up_with: str = ""

    @property
    def id(self) -> str:
        return f"{self.platform}:{self.ident}"


#: What may follow a community's address on its own page: nothing, its about
#: page, or its member list. Anything else - a post, a comment, a photo - is a
#: member's, and a member's words are not the community's.
_ROOT_TAIL = re.compile(r"^/?(?:about|members|info|rules)?/?$", re.I)


def own_page(url: str, platform: str, ident: str) -> bool:
    """Whether `url` is the community's own page, not a member's post on it."""
    bare = (url or "").split("#", 1)[0].split("?", 1)[0]
    key = ident.split(":", 1)[-1]
    at = bare.lower().find(key.lower())
    if at < 0:
        return False
    return bool(_ROOT_TAIL.match(bare[at + len(key):]))


def _host(url: str) -> str:
    m = re.match(r"^(?:https?://)?([^/?#]+)", url or "")
    return m.group(1) if m else ""


def leads_from(results: Iterable, platform: str, subject: str) -> list[Lead]:
    """Leads from one search's results: every community a result's URL or
    text names on `platform`, with what the page says about it. A result
    naming a community on another platform is not this search's lead."""
    out: dict[str, Lead] = {}
    for r in results:
        url = str(getattr(r, "url", "") or "")
        title = str(getattr(r, "title", "") or "")
        text = " ".join(str(h) for h in (getattr(r, "highlights", None) or []))
        found = [p for p in community_of(url) if p[0] == platform]
        if not found:
            found = [p for p in community_of(text) if p[0] == platform][:1]
        for plat, ident in found[:1]:
            key = f"{plat}:{ident}"
            if key in out:
                continue
            # Only the community's own page speaks for it: a member's post on
            # it, or another site naming it, gives its address and nothing
            # else - no description, contact or admin, and only its host as
            # where it was found.
            ours = own_page(url, plat, ident)
            body = f"{title}. {text}"
            name = scrub(_clean_name(title, plat)) if ours else ident
            contacts = public_contacts(text) if ours else []
            out[key] = Lead(
                platform=plat, ident=ident, name=name or ident,
                url=community_url(plat, ident), contact_url=contact_url(plat, ident),
                subject=subject,
                description=scrub(" ".join(text.split()), contacts)[:600] if ours else "",
                members=members(body) if ours else 0,
                privacy=privacy(body) if ours else "",
                seen_at=url if ours else _host(url),
                public_contact=contacts,
                admin_hint=[scrub(n) for n in admin_hints(text, name)] if ours else [])
    return list(out.values())


def lead_from_found(found, subject: str, looked_up_with: str) -> Lead:
    """A lead from a platform API's row (`community_sources.Found`)."""
    return Lead(platform=found.platform, ident=found.ident,
                name=scrub(found.name),
                url=community_url(found.platform, found.ident),
                contact_url=contact_url(found.platform, found.ident), subject=subject,
                description=scrub(found.description)[:600], members=found.members,
                privacy=found.privacy, seen_at=found.source_url,
                looked_up_with=looked_up_with)


def screen(lead: Lead) -> str:
    """Why a lead is not worth a card, or "" when it is."""
    low = f"{lead.name} {lead.description}".lower()
    for word in AVOID:
        if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", low):
            return f"about {word!r}, which FAM does not walk into"
    if lead.members and lead.members < MIN_MEMBERS:
        return f"{lead.members:,} members, too few to share with"
    return ""


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(w) > 2}


def on_subject(lead: Lead) -> float:
    """How much of the subject's words the community's name and page share."""
    wanted = _words(lead.subject)
    if not wanted:
        return 0.0
    have = _words(f"{lead.name} {lead.description}")
    return len(wanted & have) / len(wanted)


def score(lead: Lead) -> float:
    """0-1: on the subject, a kept episode that fits, and enough members to
    matter (a log scale, flat past 50,000)."""
    size = (min(1.0, math.log10(max(lead.members, 1)) / math.log10(50_000))
            if lead.members else 0.4)
    episode = float((lead.episode or {}).get("match") or 0.0)
    return round(0.45 * on_subject(lead) + 0.3 * episode + 0.25 * size, 4)


def reason(lead: Lead) -> str:
    """The card's reason in words ([candidate-reason])."""
    plat = PLATFORMS[lead.platform]
    parts = [(f"Sent to Egan by FAM's team: a {plat.label} {plat.noun}"
              if lead.source == SENT else f"A {plat.label} {plat.noun}")
             + f" about {lead.subject}"]
    if lead.members:
        parts.append(f"{lead.members:,} members")
    if lead.privacy:
        parts.append(lead.privacy)
    if lead.episode:
        parts.append(f"FAM already has “{lead.episode['title']}” for it")
    else:
        parts.append("no kept episode fits yet, so the pitches are ideas only")
    return "; ".join(parts) + "."


# --------------------------------------------------------------------------
# FAM's kept episodes, to offer one that fits


def catalogue(cache, limit: int = 200) -> list[dict]:
    """The kept episodes a community can be matched to, never an explicit
    one, each with its vector worked out once per run."""
    import embeddings

    try:
        rows = cache.recent(limit=limit) if cache is not None else []
    except Exception as exc:  # noqa: BLE001 - a match never raises
        log.warning("communities: could not read the cache: %s", exc)
        rows = []
    out = []
    for row in rows:
        if row.get("explicit") or not row.get("query"):
            continue
        title = row.get("title") or row["query"]
        try:
            vector = embeddings.embed(f"{title}. {row['query']}")
        except Exception:  # noqa: BLE001
            vector = None
        out.append({"key": row["key"], "query": row["query"], "title": title,
                    "minutes": as_minutes(row.get("minutes") or 1),
                    "plays": int(row.get("plays") or 0), "vector": vector})
    return out


def best_episode(episodes: list[dict], lead: Lead) -> Optional[dict]:
    """The kept episode most about this community, at `EPISODE_MATCH` or
    better, or None: meaning 0.6, shared words 0.4."""
    import embeddings

    text = f"{lead.subject}. {lead.name}. {lead.description}"
    try:
        vector = embeddings.embed(text)
    except Exception:  # noqa: BLE001
        vector = None
    have = _words(text)
    best = None
    for e in episodes:
        semantic = 0.0
        if vector is not None and e.get("vector") is not None:
            try:
                semantic = max(0.0, embeddings.cosine(vector, e["vector"]))
            except Exception:  # noqa: BLE001
                semantic = 0.0
        words = _words(e["title"])
        shared = len(words & have) / len(words) if words else 0.0
        match = round(min(1.0, 0.6 * semantic + 0.4 * shared), 4)
        if match >= EPISODE_MATCH and (best is None or match > best["match"]):
            best = {k: e[k] for k in ("key", "query", "title", "minutes", "plays")}
            best["match"] = match
    return best


def suggested_subjects(cache, label_of: Callable[[str], str] = lambda k: "",
                       limit: int = 6) -> list[str]:
    """Subjects to search when the person names none: the categories of the
    most played kept episodes (`label_of` maps a key to one), else their
    titles."""
    try:
        rows = cache.recent(limit=200) if cache is not None else []
    except Exception:  # noqa: BLE001
        rows = []
    rows = sorted((r for r in rows if not r.get("explicit")),
                  key=lambda r: -int(r.get("plays") or 0))
    out: list[str] = []
    for r in rows:
        try:
            name = label_of(r["key"]) or ""
        except Exception:  # noqa: BLE001
            name = ""
        name = (name or r.get("title") or "").strip()
        if name and name.lower() not in {s.lower() for s in out}:
            out.append(name)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# The pitches and the message


#: Egan, the outreach agent (§580, `docs/agent_rule_proposals/egan.md`
#: until the owner signs it): his role in `agents.names`, his switch and
#: model settings, and his rule book.
AGENT = "outreach"
TASK = "read a community"

#: The task's fixed instructions, after the preamble and his rule books.
INSTRUCTIONS = """One community at a time, as it was found - by a search, or sent
by FAM's team with a note - is in the <evidence> tags; an episode FAM
already has may be in the <archive> tags.

Answer with:
- fit: 1-5, and fit_why in one sentence (rule book "always" 2).
- pitches: exactly three imagined episodes, each a title under 70
  characters, an angle (the question it answers and what makes it a story)
  and why this community in particular ("always" 3).
- message: the first message to the community's admins ("always" 4).
  Greet no one by name. Never mention a figure, result, date or event the
  material does not contain. If an episode is given, you may offer to send
  it.
- unsure: true when the material does not say what the community is for.
"""


try:
    from pydantic import BaseModel, ConfigDict, Field

    class Pitch(BaseModel):
        model_config = ConfigDict(extra="forbid")
        title: str = Field(..., max_length=120)
        angle: str = Field(..., max_length=400)
        why: str = Field(..., max_length=400)

    class Reading(BaseModel):
        """Egan's answer for one community."""
        model_config = ConfigDict(extra="forbid")
        fit: int = Field(..., ge=1, le=5)
        fit_why: str = Field(..., max_length=400)
        pitches: list[Pitch]
        message: str = Field(..., max_length=1200)
        unsure: bool = False
except ImportError:  # pragma: no cover - pydantic is a dependency
    Pitch = Reading = None  # type: ignore


#: What stands in for a person in the material Egan reads ("never" 1, 2).
PERSON, ADDRESS = "[a person]", "[an address]"
_HANDLE = re.compile(r"(?<![\w])(?:u/|@)[\w.-]{2,30}")
#: A handle said in words: "her handle is janeroe99", "ig: jane.roe".
_CALLED = re.compile(
    r"\b(?i:handle|user\s*name|username|account|goes by|ig|insta(?:gram)?|tiktok|"
    r"twitter|discord(?:\s+name)?)\s*(?i:is|:|-)?\s*@?([\w.]{3,30})")
#: Two or three capitalised words in a row: how a note names a person
#: ("met Jane Roe"). In the team's note only, and only when they are not the
#: community's own words.
_NAME_RUN = re.compile(r"\b[A-Z][a-z'’-]+(?:\s+[A-Z][a-z'’-]+){1,2}\b")


def note_people(note: str, community: Iterable[str] = ()) -> list[str]:
    """Who the team's note may name: the people `admin_hints` reads in it,
    and every run of capitalised words that is not the community's own
    (its name, subject or page). Over-redacting a note costs a phrase;
    under-redacting it would put a private person in front of Egan."""
    ours = set()
    for text in community:
        ours |= _words(text)
    found = admin_hints(note)
    for m in _NAME_RUN.finditer(note or ""):
        if not {w.lower() for w in m.group(0).split()} <= ours:
            found.append(m.group(0))
    return list(dict.fromkeys(found))


def redacted(text: str, people: Iterable[str] = ()) -> str:
    """`text` with nothing in it that names or reaches a person: phone
    numbers, email addresses, profile links, @handles and u/names, and the
    names given for people. Egan never reads about a person (rule book
    "never" 1; every-agent.md "never" 9)."""
    out = strip_phones(text or "")
    out = _EMAIL.sub(ADDRESS, out)
    out = _PROFILE.sub(PERSON, out)
    out = _HANDLE.sub(PERSON, out)
    out = _CALLED.sub(lambda m: m.group(0)[:m.start(1) - m.start(0)] + PERSON, out)
    for name in sorted({p for p in people if p}, key=len, reverse=True):
        out = re.sub(re.escape(name), PERSON, out, flags=re.I)
    return out


def material(lead: Lead) -> tuple:
    """The `(tag, text)` pairs Egan reads: the community as the search saw
    it, redacted, and the kept episode if one fits. Never a listener's words,
    so no `request` tag."""
    plat = PLATFORMS[lead.platform]
    people = list(lead.admin_hint) + note_people(
        lead.note, (lead.name, lead.subject, lead.description))
    evidence = "\n".join([
        f"Platform: {plat.label} ({plat.noun})",
        f"Community: {redacted(lead.name, people)}",
        f"Searched for: {redacted(lead.subject, people)}",
        f"Members: {lead.members:,}" if lead.members else "Members: not shown",
        f"Its page says: {redacted(lead.description, people) or '(nothing shown)'}",
    ] + ([f"FAM's team noted: {redacted(lead.note, people)}"] if lead.note else []))
    out = [("evidence", evidence)]
    if lead.episode:
        out.append(("archive", f"An episode FAM has: \u201c{lead.episode['title']}\u201d "
                               f"({lead.episode['minutes']} min)"))
    return tuple(out)


def request(lead: Lead):
    """Egan's one call for `lead`, through `agents/models.py`."""
    from agents import models

    return models.AgentRequest(
        agent=AGENT, task=TASK, switch_setting="outreach_agent",
        model_setting="outreach_model", rule_book="egan.md", schema=Reading,
        instructions=INSTRUCTIONS, material=material(lead),
        timeout=float(config.settings.outreach_timeout_seconds),
        max_tokens=int(config.settings.outreach_max_tokens), effort="low",
        surface="background")


def _figures(text: str) -> set:
    return set(re.findall(r"\d[\d,.]*%?", text or ""))


def code_pitches(lead: Lead) -> list[dict]:
    """Three pitches composed in code, from the subject and the episode."""
    s = lead.subject.strip() or lead.name
    out = []
    if lead.episode:
        out.append({"title": lead.episode["title"][:70],
                    "angle": "An episode FAM already has, ready to share.",
                    "why": f"It is about {s}, which this {PLATFORMS[lead.platform].noun} is for."})
    out += [
        {"title": f"What changed for {s} this week"[:70],
         "angle": f"The week's news about {s}, told as one story: what happened and why it matters.",
         "why": "A weekly catch-up is what members who miss threads want."},
        {"title": f"The argument everyone in {s} is having"[:70],
         "angle": "Both sides of the debate the community keeps returning to, fairly.",
         "why": "It gives members something to talk about together."},
        {"title": f"How {s} got here"[:70],
         "angle": f"The backstory a new member needs to follow {s}.",
         "why": "Admins are asked the same beginner questions again and again."},
    ]
    return out[:PITCHES]


def code_message(lead: Lead, pitches: list[dict]) -> str:
    plat = PLATFORMS[lead.platform]
    example = pitches[0]["title"] if pitches else f"a story about {lead.subject}"
    return trim(
        f"Hi, I run FAM, which turns what people care about into short audio "
        f"stories. {lead.name} looks like exactly the kind of {plat.noun} it is "
        f"made for. We're giving a few admins free early access so they can "
        f"share episodes with their members, for example “{example}”. "
        f"Would you like to try it?")


def trim(text: str, limit: int = MESSAGE_CHARS) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "))
    return cut[:end + 1] if end > limit // 2 else cut.rstrip() + "..."


def _line(value, limit: int) -> str:
    """A model's words as one line, cut, with no phone number in them."""
    return strip_phones(" ".join(str(value or "").split()))[:limit]


def _clean_pitch(p: dict) -> Optional[dict]:
    title = _line(p.get("title"), 90)
    if not title:
        return None
    return {"title": title, "angle": _line(p.get("angle"), 280),
            "why": _line(p.get("why"), 280)}


def refused(lead: Lead, reading) -> str:
    """Why code sends Egan's answer back, or "" when it stands: not three
    pitches; a figure the material does not contain; a person's name,
    address or number in his words."""
    if len(reading.pitches) != PITCHES:
        return f"{len(reading.pitches)} pitches, not {PITCHES}"
    words = " ".join([reading.message, reading.fit_why]
                     + [f"{p.title} {p.angle} {p.why}" for p in reading.pitches])
    if (_EMAIL.search(words) or strip_phones(words) != words or _HANDLE.search(words)
            or _PROFILE.search(words)):
        return "his words carry a way to reach a person"
    given = _figures(" ".join(text for _tag, text in material(lead)))
    stray = _figures(words) - given
    if stray:
        return f"his words name {', '.join(sorted(stray)[:3])}, which the material does not"
    low = words.lower()
    community = _words(f"{lead.name} {lead.subject}")
    for name in list(lead.admin_hint) + note_people(
            lead.note, (lead.name, lead.subject, lead.description)):
        if name and name.lower() in low and not _words(name) <= community:
            return "his words name a person"
    return ""


async def read(lead: Lead, ask=None) -> tuple[Lead, float]:
    """The fit, the pitches and the message: Egan's (§580), one call and
    one retry when code sends his answer back; the code's whenever he cannot
    answer - off, held, unsigned, failed or refused twice - and the card says
    which. Never raises but cancellation. Returns the lead and his cost."""
    def by_code(why: str) -> tuple[Lead, float]:
        lead.pitches = code_pitches(lead)
        lead.message = code_message(lead, lead.pitches)
        lead.fit, lead.fit_why, lead.drafted_by = CODE_FIT, why, "code"
        return lead, dollars

    dollars = 0.0
    try:
        if ask is None:
            from agents import models

            ask = models.ask
        built = request(lead)
        why = ""
        for _attempt in range(2):
            result = await ask(built)
            dollars += float(result.cost_usd or 0.0)
            if not result.ok:
                return by_code(f"composed in code: Egan did not answer "
                               f"({result.status}: {result.reason})")
            reading = result.value
            why = refused(lead, reading)
            if not why:
                break
        else:
            return by_code(f"composed in code: Egan's answer was sent back twice ({why})")
        if reading.unsure:
            return by_code(f"composed in code: Egan was unsure what the community is for "
                           f"({_line(reading.fit_why, 200)})")
        lead.fit = max(1, min(5, int(reading.fit)))
        lead.fit_why = _line(reading.fit_why, 300)
        lead.pitches = [p for p in (_clean_pitch(x.model_dump()) for x in reading.pitches) if p]
        lead.message = trim(strip_phones(reading.message))
        lead.drafted_by = f"Egan ({result.model or 'agent'})"
        return lead, dollars
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - never costs the card
        log.warning("communities: Egan's read fell back to code: %s", exc)
        return by_code("composed in code: Egan's read could not run")


# --------------------------------------------------------------------------
# The store


class Store:
    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("COMMUNITIES_DB", "communities.db", path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS leads (
                       id          TEXT PRIMARY KEY,
                       platform    TEXT NOT NULL,
                       found_at    REAL NOT NULL,
                       updated_at  REAL NOT NULL DEFAULT 0,
                       status      TEXT NOT NULL DEFAULT 'new',
                       score       REAL NOT NULL DEFAULT 0,
                       fit         INTEGER NOT NULL DEFAULT 0,
                       lead        TEXT NOT NULL,
                       notes       TEXT NOT NULL DEFAULT '',
                       handle      TEXT NOT NULL DEFAULT '',
                       listened_at REAL NOT NULL DEFAULT 0,
                       share_url   TEXT NOT NULL DEFAULT '',
                       source      TEXT NOT NULL DEFAULT 'search'
                   )""")
            conn.execute("CREATE INDEX IF NOT EXISTS leads_status"
                         " ON leads(status, found_at)")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS runs (
                       id       TEXT PRIMARY KEY,
                       at       REAL NOT NULL,
                       searches INTEGER NOT NULL DEFAULT 0,
                       report   TEXT NOT NULL DEFAULT '{}'
                   )""")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = store_db.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def known(self) -> set:
        return {r[0] for r in self._conn().execute("SELECT id FROM leads")}

    def add(self, lead: Lead, at: float, status: str = NEW) -> None:
        self._conn().execute(
            "INSERT OR IGNORE INTO leads (id, platform, found_at, updated_at,"
            " status, score, fit, lead, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (lead.id, lead.platform, at, at, status, lead.score, lead.fit,
             json.dumps(asdict(lead)), lead.source))

    def put(self, lead: Lead, at: float, status: str = NEW) -> None:
        """Add `lead`, or read it again over the stored one (a person sent
        it, §581): the person's notes and the handle they found are kept."""
        self.add(lead, at, status)
        self._conn().execute(
            "UPDATE leads SET lead = ?, status = ?, score = ?, fit = ?, updated_at = ?,"
            " source = ? WHERE id = ?",
            (json.dumps(asdict(lead)), status, lead.score, lead.fit, at, lead.source, lead.id))

    def _row(self, r) -> dict:
        out = json.loads(r[7])
        out.update({"id": r[0], "platform": r[1], "found_at": r[2],
                    "updated_at": r[3], "status": r[4], "score": r[5],
                    "fit": r[6], "notes": r[8], "handle": r[9],
                    "listened_at": r[10], "share_url": r[11],
                    "how": PLATFORMS[r[1]].how if r[1] in PLATFORMS else ""})
        return out

    _COLUMNS = ("id, platform, found_at, updated_at, status, score, fit, lead,"
                " notes, handle, listened_at, share_url")

    def get(self, lead_id: str) -> Optional[dict]:
        r = self._conn().execute(
            f"SELECT {self._COLUMNS} FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return self._row(r) if r else None

    def all(self, status: str = "", platform: str = "", limit: int = 300) -> list[dict]:
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        else:
            where.append("status != ?")
            args.append(SET_ASIDE)
        if platform:
            where.append("platform = ?")
            args.append(platform)
        rows = self._conn().execute(
            f"SELECT {self._COLUMNS} FROM leads WHERE " + " AND ".join(where)
            + " ORDER BY status = 'new' DESC, score DESC, found_at DESC LIMIT ?",
            (*args, int(limit))).fetchall()
        return [self._row(r) for r in rows]

    def move(self, lead_id: str, status: str, at: float, *, notes: Optional[str] = None,
             handle: Optional[str] = None) -> None:
        sets, args = ["status = ?", "updated_at = ?"], [status, at]
        if notes is not None:
            sets.append("notes = ?")
            args.append(notes)
        if handle is not None:
            sets.append("handle = ?")
            args.append(handle)
        self._conn().execute(f"UPDATE leads SET {', '.join(sets)} WHERE id = ?",
                             (*args, lead_id))

    def listened(self, lead_id: str, at: float, share_url: str) -> None:
        self._conn().execute(
            "UPDATE leads SET listened_at = ?, share_url = ? WHERE id = ?",
            (at, share_url, lead_id))

    def counts(self) -> dict:
        return {r[0]: int(r[1]) for r in self._conn().execute(
            "SELECT status, COUNT(*) FROM leads GROUP BY status")}

    def prune(self, now: float) -> int:
        """Leads a search found and nobody touched, after `KEEP_DAYS`: never
        one a person sent to Egan, moved, or wrote a note or handle on.
        Runs after 30 days."""
        cut = now - KEEP_DAYS * 86400
        n = self._conn().execute(
            "DELETE FROM leads WHERE status IN (?, ?) AND source = ? AND notes = ''"
            " AND handle = '' AND updated_at < ?",
            (NEW, SET_ASIDE, SEARCHED, cut)).rowcount
        self._conn().execute("DELETE FROM runs WHERE at < ?", (now - 30 * 86400,))
        return int(n or 0)

    def start_run(self, at: float) -> str:
        """A run or a lookup, recorded before its first search so the day's
        count includes it while it is still going."""
        import uuid

        run_id = uuid.uuid4().hex
        self._conn().execute("INSERT INTO runs (id, at, searches, report) VALUES (?, ?, 0, '{}')",
                             (run_id, at))
        return run_id

    def count_search(self, run_id: str) -> None:
        self._conn().execute("UPDATE runs SET searches = searches + 1 WHERE id = ?", (run_id,))

    def finish_run(self, run_id: str, report: dict) -> None:
        self._conn().execute("UPDATE runs SET report = ? WHERE id = ?",
                             (json.dumps(report), run_id))

    def searches_since(self, since: float) -> int:
        r = self._conn().execute(
            "SELECT COALESCE(SUM(searches), 0) FROM runs WHERE at >= ?", (since,)).fetchone()
        return int(r[0] or 0)

    def last_run(self) -> Optional[dict]:
        r = self._conn().execute(
            "SELECT at, searches, report FROM runs WHERE report != '{}'"
            " ORDER BY at DESC LIMIT 1").fetchone()
        if not r:
            return None
        return {"at": r[0], "searches": r[1], **json.loads(r[2] or "{}")}


_STORE: list = []
_STORE_LOCK = threading.Lock()


def _exists() -> bool:
    """Whether there is a store to read, without creating one."""
    if _STORE:
        return True
    try:
        return store_db.has_tables(data_path("COMMUNITIES_DB", "communities.db"))
    except Exception:  # noqa: BLE001
        return False


def store() -> Store:
    with _STORE_LOCK:
        if not _STORE:
            _STORE.append(Store())
        return _STORE[0]


def reset(new: Optional[Store] = None) -> None:
    with _STORE_LOCK:
        _STORE.clear()
        if new is not None:
            _STORE.append(new)


# --------------------------------------------------------------------------
# A run


def backends() -> list[str]:
    """The web backends a search can use, in the research ladder's order."""
    import research

    return [r for r in research.ladder() if r in research.WEB_BACKENDS
            and research.backend_available(r)]


def available() -> tuple[bool, str]:
    """Whether a run can find anything: a web search backend, or Reddit's or
    X's API (§581) for their own platforms."""
    import community_sources as CS

    have = []
    rungs = backends()
    if rungs:
        have.append(f"the web with {rungs[0]}")
    for label, (ok, _why) in (("Reddit's API", CS.reddit_available()),
                              ("X's API", CS.x_available())):
        if ok:
            have.append(label)
    if not have:
        return False, ("no source is configured: no web search backend "
                       "(PARALLEL_API_KEY or PERPLEXITY_API_KEY), and neither Reddit's "
                       "nor X's API key; on staging they are removed on purpose")
    return True, "searching " + ", ".join(have)


def search_query(subject: str, platform: str) -> str:
    return f"{subject} {PLATFORMS[platform].words}"


def _search_blocking(query: str, platform: str) -> tuple[list, float, str]:
    """One web search held to the platform's domains, down the ladder:
    `(results, dollars, backend)`. Raises when every rung failed."""
    import provider_usage
    import research

    plat = PLATFORMS[platform]
    last: Optional[Exception] = None
    if not backends():
        raise RuntimeError("no web search backend is configured")
    for backend in backends():
        try:
            if backend == "parallel":
                results, cost = research._search_parallel(
                    query, 10, include_domains=plat.domains,
                    objective=f"Find {plat.label} {plat.noun}s about this subject "
                              f"that people can join.")
            else:
                results, cost = research._search_perplexity(
                    query, 10, include_domains=plat.domains)
            provider_usage.record(backend)
            return results, cost, backend
        except Exception as exc:  # noqa: BLE001 - the next rung
            provider_usage.record(backend, ok=False)
            last = exc
    raise RuntimeError(f"every search backend failed: {last}")


def _api_for(platform: str):
    """`(search, about, label)` for a platform with its own API configured
    (§581), or None: Reddit's and X's, read-only."""
    import community_sources as CS

    if platform == "reddit" and CS.reddit_available()[0]:
        return CS.reddit_search, CS.reddit_about, "Reddit's API"
    if platform == "x" and CS.x_available()[0]:
        return CS.x_search, CS.x_about, "X's API"
    return None


class NoSource(RuntimeError):
    """Nothing could be asked about this platform: no web search backend and
    no API of its own. Not a search, so not counted against the day."""


class DailyCeiling(Exception):
    """Today's searches are used up (`COMMUNITIES_DAILY_SEARCHES`)."""


class NotPitched(Exception):
    """A community FAM does not pitch, with why."""


class NotACommunity(Exception):
    """The link is not one this page can read, in words."""


async def _find(subject: str, platform: str, searcher) -> tuple[list, float, str]:
    """`(leads, dollars, what was asked)` for one subject on one platform:
    the platform's API when it has one configured, else - or when it fails -
    the web search. A test's `searcher` stands in for the web search.
    Raises `NoSource` when nothing could be asked."""
    api = _api_for(platform) if searcher is None else None
    if api is None and searcher is None and not backends():
        raise NoSource(f"nothing searches {PLATFORMS[platform].label} here: no web search "
                       f"backend{' and no API key' if platform in ('reddit', 'x') else ''}")
    if api is not None:
        search, _about, label = api
        try:
            found = await asyncio.to_thread(search, subject)
            return ([lead_from_found(f, subject, label) for f in found if not f.adult],
                    0.0, label)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the web search is the next rung
            log.warning("communities: %s failed, searching the web: %s", label, exc)
            if not backends():
                raise RuntimeError(f"{label} failed ({exc}) and no web search is configured")
    if searcher is None:
        results, cost, backend = await asyncio.to_thread(
            _search_blocking, search_query(subject, platform), platform)
    else:
        results, cost, backend = await searcher(search_query(subject, platform), platform)
    leads = leads_from(results, platform, subject)
    for lead in leads:
        lead.looked_up_with = f"a web search ({backend})"
    return leads, float(cost or 0.0), backend


def _day_start(now: float) -> float:
    return now - (now % 86400)


def _left_today(held: "Store", now: float) -> int:
    """Searches left today, counting those a run still in progress has made."""
    return max(0, int(config.settings.communities_daily_searches)
               - held.searches_since(_day_start(now)))


def _match_episodes(cache, leads: list) -> None:
    """Each lead's kept episode and score. CPU work (an embedding per kept
    episode), so it runs in a thread, never on the event loop."""
    episodes = catalogue(cache) if leads else []
    for lead in leads:
        lead.episode = best_episode(episodes, lead)
        lead.score = score(lead)


#: What this page's searches and calls are filed under in the day's spend.
SPEND_SOURCE = "community outreach"


async def run(cache, subjects: Iterable[str], platforms: Iterable[str], *,
              now: Optional[float] = None, searcher=None, reader=None,
              held: Optional[Store] = None) -> dict:
    """Search each subject on each platform, screen, match, read; leave the
    cards on the page. Returns the run's report, which is also stored.

    Every search and call is in the day's spend under `SPEND_SOURCE` (§481).
    Each search is counted against the day's ceiling as it is made, so a
    group sent to Egan while a run is going sees it, and a run that fails
    halfway has already counted what it spent."""
    import metering

    with metering.spend_source(SPEND_SOURCE):
        return await _run(cache, subjects, platforms, now=now, searcher=searcher,
                          reader=reader, held=held)


async def _run(cache, subjects, platforms, *, now, searcher, reader, held) -> dict:
    now = time.time() if now is None else now
    held = held or store()
    await asyncio.to_thread(held.prune, now)
    settings = config.settings
    subjects = [s.strip() for s in subjects if s and s.strip()][:int(settings.communities_subjects)]
    platforms = [p for p in platforms if p in PLATFORMS] or list(PLATFORMS)
    report: dict = {"subjects": subjects, "platforms": platforms, "searched": 0,
                    "found": 0, "kept": 0, "set_aside": 0, "screened": [],
                    "failed": [], "skipped": [], "dollars": 0.0, "stopped": ""}
    run_id = await asyncio.to_thread(held.start_run, now)
    try:
        if not subjects:
            report["stopped"] = "no subjects to search"
            return report
        left = await asyncio.to_thread(_left_today, held, now)
        budget = min(int(settings.communities_max_searches), left)
        if budget <= 0:
            report["stopped"] = "today's searches are used up (COMMUNITIES_DAILY_SEARCHES)"
            return report
        pairs = [(s, p) for s in subjects for p in platforms]
        if len(pairs) > budget:
            report["stopped"] = (f"{len(pairs) - budget} searches left out to stay "
                                 f"inside today's ceiling")
        await _search_and_read(cache, pairs[:budget], report, held, run_id, now,
                               searcher, reader or read)
        return report
    finally:
        report["dollars"] = round(report["dollars"], 4)
        report["screened"] = report["screened"][:20]
        await asyncio.to_thread(held.finish_run, run_id, report)


async def _search_and_read(cache, pairs, report, held, run_id, now, searcher,
                           reader) -> None:
    import metering

    known = await asyncio.to_thread(held.known)
    found: dict[str, Lead] = {}
    for subject, platform in pairs:
        if await asyncio.to_thread(_left_today, held, now) <= 0:
            # A group sent to Egan meanwhile took the last of the day.
            report["stopped"] = "today's searches ran out during the run"
            break
        try:
            leads, cost, _asked = await _find(subject, platform, searcher)
        except asyncio.CancelledError:
            raise
        except NoSource as exc:
            report["skipped"].append(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 - recorded, the run goes on
            report["failed"].append(f"{subject} on {PLATFORMS[platform].label}: {exc}")
            report["searched"] += 1
            await asyncio.to_thread(held.count_search, run_id)
            continue
        report["searched"] += 1
        await asyncio.to_thread(held.count_search, run_id)
        report["dollars"] += float(cost or 0.0)
        if cost:
            # The day's spend (§481), like every other search FAM pays for.
            # The platforms' APIs count themselves (`community_sources`).
            metering.Usage().add_research(1, float(cost or 0.0))
        for lead in leads:
            report["found"] += 1
            if lead.id in known or lead.id in found:
                continue
            why = screen(lead)
            if why:
                report["screened"].append(f"{lead.name or lead.ident}: {why}")
                continue
            found[lead.id] = lead
    await asyncio.to_thread(_match_episodes, cache, list(found.values()))
    shortlist = sorted(found.values(), key=lambda l: -l.score)[
        :int(config.settings.communities_shortlist)]
    for lead in shortlist:
        lead, dollars = await reader(lead)
        report["dollars"] += float(dollars or 0.0)
        lead.reason = reason(lead)
        if lead.fit < FIT_FLOOR:
            await asyncio.to_thread(held.add, lead, now, SET_ASIDE)
            report["set_aside"] += 1
            continue
        await asyncio.to_thread(held.add, lead, now)
        report["kept"] += 1


def _apis() -> dict:
    try:
        import community_sources as CS

        return CS.report()
    except Exception as exc:  # noqa: BLE001 - a report is never load-bearing
        return {"error": str(exc)}


def health() -> dict:
    """For the page. Reads the store: call it in a thread."""
    ok, why = available()
    settings = config.settings
    out = {"available": ok, "detail": why,
           "max_searches": settings.communities_max_searches,
           "daily_searches": settings.communities_daily_searches,
           "platforms": {k: {"label": p.label, "noun": p.noun} for k, p in PLATFORMS.items()},
           "apis": _apis(),
           "sendable": SENDABLE,
           "statuses": STATUS_LABELS}
    if not _exists():
        out["counts"], out["searches_today"], out["last_run"] = {}, 0, None
        return out
    try:
        held = store()
        out["counts"] = held.counts()
        out["searches_today"] = held.searches_since(_day_start(time.time()))
        out["last_run"] = held.last_run()
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


# --------------------------------------------------------------------------
# A group a person sends to Egan (§581)


#: What the page says it can read.
SENDABLE = ("a Facebook group (facebook.com/groups/...), a subreddit (reddit.com/r/...), "
            "an X Community (x.com/i/communities/...), a Discord invite (discord.gg/...) "
            "or a Slack workspace (name.slack.com or join.slack.com/t/...)")


async def _facts(lead: Lead, searcher=None) -> tuple[Optional[Lead], float, str, bool]:
    """`(what the source says about it, dollars, what was asked, whether a
    request went out)`: the platform's API when it has one, else a web
    search for its address. `None` when nothing was found. Raises
    `NotPitched` for a community its platform marks adults only."""
    api = _api_for(lead.platform) if searcher is None else None
    asked = False
    if api is not None:
        _search, about, label = api
        try:
            asked = True
            found = await asyncio.to_thread(about, lead.ident)
            if found.adult:
                raise NotPitched(f"{label} marks it adults only")
            return lead_from_found(found, lead.subject, label), 0.0, label, True
        except (asyncio.CancelledError, NotPitched):
            raise
        except Exception as exc:  # noqa: BLE001 - the web is the next rung
            log.warning("communities: %s could not read %s: %s", label, lead.id, exc)
    query = lead.url.split("://", 1)[-1]
    if searcher is None:
        if not backends():
            return None, 0.0, "nothing: no web search backend is configured", asked
        results, cost, backend = await asyncio.to_thread(_search_blocking, query, lead.platform)
    else:
        results, cost, backend = await searcher(query, lead.platform)
    for found in leads_from(results, lead.platform, lead.subject):
        if found.id == lead.id and (found.description or found.name != found.ident):
            found.looked_up_with = f"a web search ({backend})"
            return found, float(cost or 0.0), backend, True
    return None, float(cost or 0.0), f"a web search ({backend})", True


async def lookup(cache, url: str, subject: str = "", note: str = "", *,
                 now: Optional[float] = None, searcher=None, reader=None,
                 held: Optional[Store] = None) -> dict:
    """A community a person sends to Egan: read it from its platform's API or
    the web, work out the public way to its admins, have Egan judge it and
    draft the message, and keep it as a lead to contact - whatever its fit,
    because a person chose it. Returns `{"lead": ..., "already": bool}`.

    Raises `NotACommunity` for a link this page cannot read, `DailyCeiling`
    when today's searches are used up, and `NotPitched` for a community FAM
    does not pitch (`AVOID`, or adults only), with the reason."""
    import metering

    now = time.time() if now is None else now
    held = held or store()
    pairs = community_of(url or "")
    if not pairs:
        raise NotACommunity(f"That link is not one this page can read. Send {SENDABLE}.")
    platform, ident = pairs[0]
    subject = scrub(" ".join((subject or "").split()))[:80]
    note = scrub(" ".join((note or "").split()))[:600]
    existing = await asyncio.to_thread(held.get, f"{platform}:{ident}")
    if existing is not None and existing["status"] != SET_ASIDE:
        return {"lead": existing, "already": True}
    if await asyncio.to_thread(_left_today, held, now) <= 0:
        raise DailyCeiling("Today's searches are used up (COMMUNITIES_DAILY_SEARCHES).")
    lead = Lead(platform=platform, ident=ident, name=ident,
                url=community_url(platform, ident), contact_url=contact_url(platform, ident),
                subject=subject, source=SENT, note=note)
    report = {"sent": lead.id, "searched": 0, "dollars": 0.0, "kept": 0, "set_aside": 0,
              "failed": [], "skipped": [], "screened": [], "stopped": ""}
    run_id = await asyncio.to_thread(held.start_run, now)
    try:
        with metering.spend_source(SPEND_SOURCE):
            try:
                facts, cost, asked, went_out = await _facts(lead, searcher)
            except (asyncio.CancelledError, NotPitched):
                raise
            except Exception as exc:  # noqa: BLE001 - Egan reads its address alone
                facts, cost, asked, went_out = None, 0.0, f"the lookup failed ({exc})", True
                report["failed"].append(str(exc))
            if went_out:
                report["searched"] = 1
                await asyncio.to_thread(held.count_search, run_id)
            report["dollars"] += cost
            if cost:
                metering.Usage().add_research(1, cost)
            if facts is not None:
                for name in ("name", "description", "members", "privacy", "seen_at",
                             "public_contact", "admin_hint", "looked_up_with"):
                    setattr(lead, name, getattr(facts, name))
            else:
                lead.looked_up_with = f"{asked}: found nothing, so Egan reads its address alone"
            if not lead.subject:
                lead.subject = lead.name
            why = screen(lead)
            if why and "too few" not in why:
                report["screened"].append(why)
                raise NotPitched(f"FAM does not pitch this community: {why}.")
            await asyncio.to_thread(_match_episodes, cache, [lead])
            lead, dollars = await (reader or read)(lead)
            report["dollars"] += float(dollars or 0.0)
    finally:
        await asyncio.to_thread(held.finish_run, run_id, report)
    lead.reason = reason(lead)
    await asyncio.to_thread(held.put, lead, now, NEW)
    report["kept"] = 1
    return {"lead": await asyncio.to_thread(held.get, lead.id), "already": False}
