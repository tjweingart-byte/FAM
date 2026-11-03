"""Mclovin, the script supervisor - the source screen (Phase 3, §433).

docs/agent_rules/mclovin.md "always" 2: screen each packet the moment it
comes back, from both paths, and remove every opinion piece (op-eds,
editorials, columns, commentary, letters) and every blog post before
Shakespeare or the writer reads it; record each removal and why. The one
exception: when the request explicitly asks for a person's opinion or point
of view (C3P0's `opinion_requested`), keep *that person's* opinion pieces,
marked as opinion so the episode tells them as that person's view. Official
blogs (a company's newsroom, a government agency's announcements) are
primary sources and kept; pieces labelled "analysis" are reporting and kept
(decided items 6 and 13).

**Code only, and only what code is sure of.** Each page is read from what it
says about itself - the section in its URL path, the label in its title, a
blogging platform's host - never from a guess at its tone. A page the code
cannot settle (a `/blog/` on a host that is neither a blogging platform nor
a primary source) is **kept and counted** as unsettled (the owner,
2026-11-03): on the tap a thinner packet costs more than an unsettled page,
and ahead of the tap a small model may judge it (a later piece).

**Where it runs.** `research.screen_results` asks `screen()` on every rung,
the second look and GDELT; `research.finish_history_look` asks
`screen_facts()` on Sonic's (or the recall's) facts before they are merged.
Both stand aside unless `screening()` is in force, which the writer's
research step enters only when Netenyahu lets Mclovin work on the episode
(`SUPERVISOR=1`, under `AGENTS=1`). Off, nothing here runs and the packet is
byte for byte what it was (`tests/test_agents_off.py`).

**Never a refusal.** The screen only removes; the brief's one retry runs
after it on a thin packet as it always has, and an empty packet is a missed
search (§256) - written from what there is, the gap unannounced.
"""
from __future__ import annotations

import collections
import contextlib
import contextvars
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("fam.agents")

#: What the screen has done since boot. `/api/health`'s agents block reads it.
SINCE_BOOT: "collections.Counter[str]" = collections.Counter()
_LOCK = threading.Lock()


def _count(kind: str, n: int = 1) -> None:
    with _LOCK:
        SINCE_BOOT[kind] += n


# ------------------------------------------------------------------ what a page says it is
#: URL path sections that are a publication's opinion pages. Whole path
#: segments only: `/opinion/` is a section, `/opinion-poll-shows/` is a slug.
OPINION_SECTIONS = frozenset({
    "opinion", "opinions", "op-ed", "op-eds", "oped", "opeds", "editorial",
    "editorials", "editorial-board", "commentary", "column", "columns",
    "columnist", "columnists", "letters", "letters-to-the-editor",
    "letter-to-the-editor", "guest-essay", "guest-column", "guest-columns",
    "viewpoint", "viewpoints", "commentisfree",
})

#: The label a title gives itself. At the start ("Opinion: ...",
#: "Op-Ed | ...") or as a trailing section name ("... | Opinion").
_LABELS = (r"opinion|op-?ed|editorial|commentary|column|letters? to the editor|"
           r"letters?|guest (?:essay|column|opinion)|viewpoint|my view|our view|"
           r"the big read: opinion")
_TITLE_OPINION_HEAD = re.compile(r"^\s*(?:\(?\s*(?:" + _LABELS + r")\s*\)?)\s*[:|\-–—]\s*\S",
                                 re.I)
_TITLE_OPINION_TAIL = re.compile(r"[|\-–—]\s*(?:opinion|op-?ed|editorial|commentary)\s*$",
                                 re.I)
_TITLE_OPINION_PAREN = re.compile(r"\((?:opinion|op-?ed|editorial)\)", re.I)

#: "Analysis" is reporting (decided item 13): a path section or title label
#: that says so keeps the page even on an outlet that files analysis beside
#: its columns - unless the page is in the opinion section itself.
_TITLE_ANALYSIS = re.compile(r"^\s*(?:news )?analysis\s*[:|\-–—]|[|\-–—]\s*analysis\s*$",
                             re.I)
ANALYSIS_SECTIONS = frozenset({"analysis", "news-analysis"})

#: Hosts that publish anyone's posts. A page on one is a blog post whatever
#: its path - a reporter's newsletter there is still not the outlet's.
BLOG_PLATFORMS = (
    "substack.com", "medium.com", "blogspot.com", "blogger.com", "wordpress.com",
    "tumblr.com", "ghost.io", "typepad.com", "livejournal.com", "beehiiv.com",
    "wixsite.com", "weebly.com", "hashnode.dev", "over-blog.com", "squarespace.com",
)

#: Path sections and subdomains that say "blog". On a blogging platform or
#: a primary source they settle it; anywhere else they are unsettled.
BLOG_SECTIONS = frozenset({"blog", "blogs", "weblog", "weblogs"})

#: Path sections that are a company's or an agency's own announcements -
#: primary sources (decided item 13), never a blog to drop.
OFFICIAL_SECTIONS = frozenset({
    "newsroom", "press", "press-release", "press-releases", "pressroom",
    "news-releases", "news-release", "media-centre", "media-center",
    "announcements", "media-releases",
})


def _path_segments(url: str) -> list:
    url = (url or "").strip().lower()
    if "//" in url:
        url = url.split("//", 1)[1]
    path = url.split("/", 1)[1] if "/" in url else ""
    path = re.split(r"[?#]", path, maxsplit=1)[0]
    return [s for s in path.split("/") if s]


def _host(url: str) -> str:
    import research

    return research.host_of(type("R", (), {"url": url})())


def _on(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


@dataclass(frozen=True)
class Reading:
    """What one page says it is. `kind` is one of `reporting`, `opinion`,
    `blog`, `official`, `analysis`, `unsettled`; `why` is a few words for the
    log and `/admin`."""

    kind: str
    why: str = ""

    @property
    def dropped(self) -> bool:
        return self.kind in ("opinion", "blog")


def classify(url: str, title: str = "") -> Reading:
    """What a page says it is, from its URL and title. Never raises."""
    try:
        import research

        segments = _path_segments(url)
        host = _host(url)
        title = " ".join(str(title or "").split())
        section = set(segments[:-1]) if len(segments) > 1 else set(segments)
        # The last segment is usually the article's slug; a section named
        # alone (`/opinion`) still counts. Slugs never do.
        if any(s in OPINION_SECTIONS for s in section):
            return Reading("opinion", "opinion section in the address")
        if _TITLE_OPINION_HEAD.search(title) or _TITLE_OPINION_TAIL.search(title) \
                or _TITLE_OPINION_PAREN.search(title):
            return Reading("opinion", "labelled opinion in the title")
        if any(s in ANALYSIS_SECTIONS for s in section) or _TITLE_ANALYSIS.search(title):
            return Reading("analysis", "labelled analysis: reporting")
        if _on(host, BLOG_PLATFORMS):
            return Reading("blog", "a blogging platform")
        blog_path = any(s in BLOG_SECTIONS for s in section) or \
            host.split(".", 1)[0] in BLOG_SECTIONS
        if any(s in OFFICIAL_SECTIONS for s in section):
            return Reading("official", "a newsroom or press office")
        if blog_path:
            tier = research.credibility(type("R", (), {"url": url})())
            # A government's or a university's own blog is its announcements;
            # an outlet's blog, even a wire service's, is not "official".
            if tier == "primary" and not _on(host, research.TIER_PRIMARY):
                return Reading("official", "a government's or university's own blog")
            return Reading("unsettled", "a blog section on a "
                           + ("known outlet" if tier == "established" else "host FAM does not know"))
        return Reading("reporting")
    except Exception:  # noqa: BLE001 - a screen that fails keeps the page
        log.warning("the source screen could not read %r; kept", url, exc_info=True)
        return Reading("unsettled", "could not be read")


# ------------------------------------------------------------------ whose opinion
def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return text.lower()


def _name_tokens(name: str) -> list:
    return [t for t in re.findall(r"[a-z0-9]+", _fold(name)) if len(t) > 1]


def by(name: str, url: str, title: str = "", passages=()) -> bool:
    """Whether a page is *by* `name`, from what code can see of a byline:
    the name's slug in the address (an author page, a columnist's path, their
    own newsletter's host), the title opening or closing on the name, or a
    passage saying "By <name>". A piece merely *about* them is not theirs.
    Never raises."""
    try:
        tokens = _name_tokens(name)
        if not tokens:
            return False
        # The host and the sections, never the article's own slug: a slug
        # names what the piece is about. One-word names are too common to
        # find in an address at all.
        if len(tokens) > 1:
            segments = _path_segments(_fold(url))
            where = " ".join([_host(_fold(url))] + segments[:-1])
            for joiner in ("-", "", "_", "."):
                if joiner.join(tokens) in where:
                    return True
        spoken = r"\s+".join(map(re.escape, tokens))
        folded_title = _fold(" ".join(str(title or "").split()))
        if re.match(r"^\s*" + spoken + r"\s*[:|\-–—]", folded_title) or \
                re.search(r"(?:[|\-–—]|\bby)\s*" + spoken + r"\s*$", folded_title):
            return True
        for passage in list(passages or [])[:6]:
            if re.search(r"\bby\s+" + spoken + r"\b", _fold(passage)):
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ in force for one episode
@dataclass(frozen=True)
class Screen:
    """The screen for one episode: whose opinion was asked for, if anyone's."""

    opinion_of: str = ""


#: The screen in force for the research of the episode being written, or
#: None. A context variable, so every rung, the second look and GDELT -
#: started as tasks or threads from inside `screening()` - see the same one,
#: and an episode researched beside it sees its own.
_SCREEN: "contextvars.ContextVar[Optional[Screen]]" = contextvars.ContextVar(
    "fam_supervisor_screen", default=None)


@contextlib.contextmanager
def screening(opinion_of: str = ""):
    """Put the screen in force for the research inside the block."""
    token = _SCREEN.set(Screen(" ".join(str(opinion_of or "").split())[:120]))
    try:
        yield
    finally:
        _SCREEN.reset(token)


def active() -> Optional[Screen]:
    return _SCREEN.get()


def _keep_as_theirs(screen: Screen, reading: Reading, url: str, title: str,
                    passages) -> bool:
    return bool(screen.opinion_of) and reading.dropped and by(
        screen.opinion_of, url, title, passages)


def screen(results) -> tuple:
    """`(kept, dropped)` for one rung's results, in the order given, where
    `dropped` is `[(host, why)]`. With no screen in force, `(results, [])`
    untouched. Never raises."""
    current = _SCREEN.get()
    results = list(results or [])
    if current is None:
        return results, []
    kept, dropped = [], []
    try:
        for result in results:
            url = str(getattr(result, "url", "") or "")
            title = str(getattr(result, "title", "") or "")
            passages = getattr(result, "highlights", None) or ()
            reading = classify(url, title)
            if reading.kind == "unsettled":
                _count("screen_unsettled")
            if reading.dropped:
                if _keep_as_theirs(current, reading, url, title, passages):
                    _count("screen_opinion_kept")
                    kept.append(result)
                    continue
                _count(f"screen_dropped_{reading.kind}")
                dropped.append((_host(url), reading.why))
                continue
            kept.append(result)
        _count("screen_pages", len(results))
        return kept, dropped
    except Exception:  # noqa: BLE001 - a screen that fails keeps everything
        log.warning("the source screen failed; the packet is unscreened", exc_info=True)
        return results, []


def screen_facts(facts) -> list:
    """The same screen on the archive's facts (Sonic's packet, or the
    recall's): each fact is judged by the article it came from. Facts with no
    web address (a provider's data) are kept. Untouched with no screen in
    force. Never raises."""
    current = _SCREEN.get()
    facts = list(facts or [])
    if current is None or not facts:
        return facts
    try:
        kept, dropped = [], []
        for fact in facts:
            url = str(getattr(fact, "url", "") or "")
            if not url.startswith(("http://", "https://")):
                kept.append(fact)
                continue
            title = str(getattr(fact, "title", "") or "")
            reading = classify(url, title)
            if reading.dropped and not _keep_as_theirs(
                    current, reading, url, title, (getattr(fact, "text", ""),)):
                _count(f"screen_archive_dropped_{reading.kind}")
                dropped.append((_host(url), reading.why))
                continue
            kept.append(fact)
        if dropped:
            log.info("Mclovin: screened %d archive fact%s out: %s", len(dropped),
                     "" if len(dropped) == 1 else "s",
                     ", ".join(f"{h or '?'} ({why})" for h, why in dropped[:8]))
        return kept
    except Exception:  # noqa: BLE001
        log.warning("the archive screen failed; the facts are unscreened", exc_info=True)
        return facts


def opinion_label(result) -> str:
    """The packet line that marks a kept opinion piece as its author's view,
    or "". Only for the person whose view was asked for; "" with no screen in
    force, so the packet is unchanged when Mclovin is off."""
    current = _SCREEN.get()
    if current is None or not current.opinion_of:
        return ""
    try:
        url = str(getattr(result, "url", "") or "")
        title = str(getattr(result, "title", "") or "")
        reading = classify(url, title)
        if reading.dropped and by(current.opinion_of, url, title,
                                  getattr(result, "highlights", None) or ()):
            return (f"Opinion: {current.opinion_of}'s own view - tell it as "
                    "their view, never as fact")
        return ""
    except Exception:  # noqa: BLE001
        return ""


def report() -> dict:
    with _LOCK:
        counts = dict(SINCE_BOOT)
    pages = counts.get("screen_pages", 0)
    dropped = sum(v for k, v in counts.items() if k.startswith("screen_dropped_"))
    return {"since_boot": counts,
            # None, not 0, before any page is screened ([metering]'s rule).
            "screen_drop_rate": round(dropped / pages, 3) if pages else None}


def reset() -> None:
    with _LOCK:
        SINCE_BOOT.clear()
