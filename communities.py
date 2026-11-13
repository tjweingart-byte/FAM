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
owner's direction (§575): X is now one platform among five, searched the same
way, for communities rather than threads.

## Rules that are load-bearing

**Public channels only** ([community-outreach]). Discovery is a web search
through FAM's own research backends (`research.WEB_BACKENDS`), never a
logged-in scrape of any platform: nothing here signs in to Facebook, Reddit,
X, Discord or Slack, and no platform token is held. A card says *how* to
reach the admins - the group's admin list, Reddit's modmail, the
community's moderators page, its own rules - and a person does it from FAM's
account with the community in front of them.

**No personal contact details are collected.** A phone number is never
stored: it is removed from every piece of text kept (`strip_phones`). An
email is kept only when the community itself publishes it on the page the
search returned (`public_contacts`), with that page beside it, and an
admin's name only when that page states it (`admin_hints`). Nothing guesses
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

**A layer that adds quality must not subtract availability**: with no model
key the pitches and the message are composed in code, and the card says so.
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

#: Phone numbers in any of the usual shapes. Removed from everything kept.
_PHONE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?!\w)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
#: Words that make an email on a community's page its contact.
_CONTACT_WORDS = re.compile(
    r"\b(contact|email|e-mail|reach|questions|enquir|inquir|partnership|"
    r"sponsor|press|media|advertis|collab|admins?|mods?|moderators?|organi[sz]er)\b", re.I)
_ADMIN = re.compile(
    # The role is any case; the name after it must be capitalised.
    r"\b(?i:admin(?:istrator)?s?|moderators?|mods?|run by|founded by|created by|"
    r"organi[sz]ed by|organi[sz]ers?|hosted by)\b\s*(?i:is|are|:|-|–)?\s*"
    r"((?:u/|@)[\w.-]{2,30}|[A-Z][a-z'’-]+(?:\s+[A-Z][a-z'’-]+){1,2})")


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


def strip_phones(text: str) -> str:
    """`text` with every phone number removed: FAM never keeps one."""
    return _PHONE.sub("[number removed]", text or "")


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


def admin_hints(text: str) -> list[str]:
    """Names or handles the page itself states as its admins, organisers or
    moderators. Read from the page, never looked up."""
    out = []
    for m in _ADMIN.finditer(text or ""):
        name = m.group(1).strip()
        if name and name not in out and name.lower() not in {"the group", "the team"}:
            out.append(name)
    return out[:3]


def _clean_name(title: str, platform: str) -> str:
    """A result's title as the community's name: the platform's suffixes off."""
    name = re.sub(r"\s*[|\-–:]\s*(Facebook|Reddit|X|Twitter|Discord|Slack|DISBOARD)"
                  r"(\s.*)?$", "", title or "", flags=re.I)
    name = re.sub(r"^(r/|Join the |Join )", "", name.strip(), flags=re.I)
    return " ".join(name.split())[:120] or ""


# --------------------------------------------------------------------------
# A lead


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

    @property
    def id(self) -> str:
        return f"{self.platform}:{self.ident}"


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
        own_page = bool(found)
        if not found:
            found = [p for p in community_of(text) if p[0] == platform][:1]
        for plat, ident in found[:1]:
            key = f"{plat}:{ident}"
            if key in out:
                continue
            body = f"{title}. {text}"
            out[key] = Lead(
                platform=plat, ident=ident,
                name=strip_phones(_clean_name(title, plat)) if own_page else ident,
                url=community_url(plat, ident), contact_url=contact_url(plat, ident),
                subject=subject,
                description=strip_phones(" ".join(text.split()))[:600],
                members=members(body), privacy=privacy(body), seen_at=url,
                # A contact or a name is the community's own only on its own page.
                public_contact=public_contacts(text) if own_page else [],
                admin_hint=[strip_phones(n) for n in admin_hints(text)] if own_page else [])
    return list(out.values())


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
    parts = [f"A {plat.label} {plat.noun} about {lead.subject}"]
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


READ_SYSTEM = """You help FAM's founders reach the admins of online communities.

FAM turns what people care about into short audio stories, researched and
written for them, so they can keep up and be part of the conversation. FAM
offers community admins early access so they can share episodes with their
members.

You are given one community, as a web search saw it, and maybe one episode
FAM already has. Return JSON:

- fit (1-5): how likely this community's members would want audio stories
  about its subject, and how likely its admin would welcome them. 1-2 for a
  community that is about selling, support, personal matters, or anything
  where an outside product would be unwelcome.
- fit_why: one sentence.
- pitches: exactly three episodes FAM could make for this community. Each has
  a title (a story's title, specific, under 70 characters), an angle (one
  sentence: the question it answers and what makes it a story), and why
  (one sentence: why this community in particular). These are ideas, not
  written episodes: never claim a result, score, figure or event you were
  not given.
- message: the first direct message from a FAM founder to this community's
  admin. Under 600 characters. Name the community. Say in one sentence what
  FAM is, offer free early access to share episodes with their members, and
  give one pitch as the example. Plain words, no hashtags, no emoji, no
  superlatives, nothing you were not given about the community, no figures.
  If an episode is given, the message may offer to send it. End with a
  question they can answer yes or no to. Do not sign it.
"""

READ_SCHEMA = {
    "type": "object",
    "properties": {
        "fit": {"type": "integer"},
        "fit_why": {"type": "string"},
        "pitches": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"},
                           "angle": {"type": "string"},
                           "why": {"type": "string"}},
            "required": ["title", "angle", "why"],
            "additionalProperties": False}},
        "message": {"type": "string"},
    },
    "required": ["fit", "fit_why", "pitches", "message"],
    "additionalProperties": False,
}


def read_prompt(lead: Lead) -> str:
    plat = PLATFORMS[lead.platform]
    lines = [
        "<request>",
        f"Platform: {plat.label} ({plat.noun})",
        f"Community: {lead.name}",
        f"Searched for: {lead.subject}",
        f"Members: {lead.members:,}" if lead.members else "Members: not shown",
        "</request>",
        "<evidence>",
        lead.description or "(the search showed no description)",
        "</evidence>",
    ]
    if lead.episode:
        lines += ["<archive>",
                  f"An episode FAM has: “{lead.episode['title']}” "
                  f"({lead.episode['minutes']} min)", "</archive>"]
    return "\n".join(lines)


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


async def read(lead: Lead, client=None) -> tuple[Lead, float]:
    """The pitches, the message and the fit, from one call; the code's when
    no model can be asked, said so on the card. Never raises but
    cancellation. Returns the lead and the call's dollars."""
    import credentials
    import metering

    def by_code(why: str) -> tuple[Lead, float]:
        lead.pitches = code_pitches(lead)
        lead.message = code_message(lead, lead.pitches)
        lead.fit, lead.fit_why, lead.drafted_by = CODE_FIT, why, "code"
        return lead, 0.0

    if client is None and not credentials.active("ANTHROPIC_API_KEY"):
        return by_code("no model key, so the pitches were composed in code")
    model = config.settings.communities_model
    try:
        if client is None:
            from anthropic_client import build_async_client

            client = build_async_client(credentials.active("ANTHROPIC_API_KEY"))
        response = await client.messages.create(
            model=model, max_tokens=1200, system=READ_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": READ_SCHEMA}},
            messages=[{"role": "user", "content": read_prompt(lead)}])
        usage = metering.Usage()
        usage.add_model_call(model, getattr(response, "usage", None),
                             source="community outreach")
        dollars = metering.price_of(usage).total
        body = json.loads(next(b.text for b in response.content if b.type == "text"))
        try:
            lead.fit = max(1, min(5, int(body.get("fit"))))
        except (TypeError, ValueError):
            lead.fit = CODE_FIT
        lead.fit_why = _line(body.get("fit_why"), 300)
        pitches = [p for p in (_clean_pitch(x) for x in (body.get("pitches") or [])
                               if isinstance(x, dict)) if p][:PITCHES]
        lead.pitches = pitches or code_pitches(lead)
        message = trim(strip_phones(str(body.get("message") or "")))
        given = _figures(f"{lead.name} {lead.description} "
                         f"{(lead.episode or {}).get('title', '')} "
                         + " ".join(p["title"] for p in lead.pitches))
        stray = _figures(message) - given
        if not message or stray:
            # A figure FAM was not given is not said under its name.
            message = code_message(lead, lead.pitches)
        lead.message, lead.drafted_by = message, model
        return lead, dollars
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("communities: the read fell back to code: %s", exc)
        return by_code("the read could not run, so the pitches were composed in code")


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
                       share_url   TEXT NOT NULL DEFAULT ''
                   )""")
            conn.execute("CREATE INDEX IF NOT EXISTS leads_status"
                         " ON leads(status, found_at)")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS runs (
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
            " status, score, fit, lead) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (lead.id, lead.platform, at, at, status, lead.score, lead.fit,
             json.dumps(asdict(lead))))

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
        """Leads nobody acted on, after `KEEP_DAYS`; runs after 30 days."""
        cut = now - KEEP_DAYS * 86400
        n = self._conn().execute(
            "DELETE FROM leads WHERE status IN (?, ?) AND updated_at < ?",
            (NEW, SET_ASIDE, cut)).rowcount
        self._conn().execute("DELETE FROM runs WHERE at < ?", (now - 30 * 86400,))
        return int(n or 0)

    def add_run(self, at: float, searches: int, report: dict) -> None:
        self._conn().execute("INSERT INTO runs (at, searches, report) VALUES (?, ?, ?)",
                             (at, int(searches), json.dumps(report)))

    def searches_since(self, since: float) -> int:
        r = self._conn().execute(
            "SELECT COALESCE(SUM(searches), 0) FROM runs WHERE at >= ?", (since,)).fetchone()
        return int(r[0] or 0)

    def last_run(self) -> Optional[dict]:
        r = self._conn().execute(
            "SELECT at, searches, report FROM runs ORDER BY at DESC LIMIT 1").fetchone()
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
    rungs = backends()
    if not rungs:
        return False, ("no web search backend is configured (PARALLEL_API_KEY or "
                       "PERPLEXITY_API_KEY); on staging they are removed on purpose")
    return True, f"searching with {rungs[0]}"


def search_query(subject: str, platform: str) -> str:
    return f"{subject} {PLATFORMS[platform].words}"


def _search_blocking(query: str, platform: str) -> tuple[list, float, str]:
    """One web search held to the platform's domains, down the ladder:
    `(results, dollars, backend)`. Raises when every rung failed."""
    import provider_usage
    import research

    plat = PLATFORMS[platform]
    last: Optional[Exception] = None
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


def _day_start(now: float) -> float:
    return now - (now % 86400)


#: What this page's searches and calls are filed under in the day's spend.
SPEND_SOURCE = "community outreach"


async def run(cache, subjects: Iterable[str], platforms: Iterable[str], *,
              now: Optional[float] = None, searcher=None, reader=None,
              held: Optional[Store] = None) -> dict:
    """Search each subject on each platform, screen, match, read; leave the
    cards on the page. Returns the run's report, which is also stored.

    Every search and call is in the day's spend under `SPEND_SOURCE` (§481),
    and the run is recorded however it ends, so a run that fails halfway
    still counts the searches it spent against the day's ceiling."""
    import metering

    with metering.spend_source(SPEND_SOURCE):
        return await _run(cache, subjects, platforms, now=now, searcher=searcher,
                          reader=reader, held=held)


async def _run(cache, subjects, platforms, *, now, searcher, reader, held) -> dict:
    import metering

    now = time.time() if now is None else now
    held = held or store()
    held.prune(now)
    settings = config.settings
    subjects = [s.strip() for s in subjects if s and s.strip()][:int(settings.communities_subjects)]
    platforms = [p for p in platforms if p in PLATFORMS] or list(PLATFORMS)
    report: dict = {"subjects": subjects, "platforms": platforms, "searched": 0,
                    "found": 0, "kept": 0, "set_aside": 0, "screened": [],
                    "failed": [], "dollars": 0.0, "stopped": ""}
    if not subjects:
        report["stopped"] = "no subjects to search"
        held.add_run(now, 0, report)
        return report
    left = max(0, int(settings.communities_daily_searches) - held.searches_since(_day_start(now)))
    budget = min(int(settings.communities_max_searches), left)
    if budget <= 0:
        report["stopped"] = "today's searches are used up (COMMUNITIES_DAILY_SEARCHES)"
        held.add_run(now, 0, report)
        return report
    searcher = searcher or (lambda q, p: asyncio.to_thread(_search_blocking, q, p))
    pairs = [(s, p) for s in subjects for p in platforms]
    if len(pairs) > budget:
        report["stopped"] = (f"{len(pairs) - budget} searches left out to stay "
                             f"inside today's ceiling")
    try:
        await _search_and_read(cache, pairs[:budget], report, held, now,
                               searcher, reader or read, metering)
    finally:
        report["dollars"] = round(report["dollars"], 4)
        report["screened"] = report["screened"][:20]
        held.add_run(now, report["searched"], report)
    return report


async def _search_and_read(cache, pairs, report, held, now, searcher, reader,
                           metering) -> None:
    known = held.known()
    found: dict[str, Lead] = {}
    for subject, platform in pairs:
        try:
            results, cost, _backend = await searcher(search_query(subject, platform), platform)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - recorded, the run goes on
            report["failed"].append(f"{subject} on {PLATFORMS[platform].label}: {exc}")
            report["searched"] += 1
            continue
        report["searched"] += 1
        report["dollars"] += float(cost or 0.0)
        # The day's spend (§481), like every other search FAM pays for.
        metering.Usage().add_research(1, float(cost or 0.0))
        for lead in leads_from(results, platform, subject):
            report["found"] += 1
            if lead.id in known or lead.id in found:
                continue
            why = screen(lead)
            if why:
                report["screened"].append(f"{lead.name or lead.ident}: {why}")
                continue
            found[lead.id] = lead
    episodes = catalogue(cache) if found else []
    for lead in found.values():
        lead.episode = best_episode(episodes, lead)
        lead.score = score(lead)
    shortlist = sorted(found.values(), key=lambda l: -l.score)[
        :int(config.settings.communities_shortlist)]
    for lead in shortlist:
        lead, dollars = await reader(lead)
        report["dollars"] += float(dollars or 0.0)
        lead.reason = reason(lead)
        if lead.fit < FIT_FLOOR:
            held.add(lead, now, SET_ASIDE)
            report["set_aside"] += 1
            continue
        held.add(lead, now)
        report["kept"] += 1


def health() -> dict:
    ok, why = available()
    settings = config.settings
    out = {"available": ok, "detail": why,
           "max_searches": settings.communities_max_searches,
           "daily_searches": settings.communities_daily_searches,
           "platforms": {k: {"label": p.label, "noun": p.noun} for k, p in PLATFORMS.items()},
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
