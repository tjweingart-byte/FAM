"""Outreach Intelligence: what `/admin/x` understands about a post before
it writes a word under it (§337).

## What this is

The owner's flip chart (10.28), "X & LinkedIn Automated Marketing Engines":
target posts with high engagement, cross-reference FAM's cached episodes,
find a match, and "creatively curate a post that is best fit for the post
it is replying to & platform that it's on" - on X, curiosity and being part
of the conversation; on LinkedIn, informative and context-giving, a way to
Go Deeper. Circled: "the engines need some kind of awareness/intelligence
(like Episode Intelligence)". The LinkedIn engine (`/admin/linkedin`, §322)
was removed at the owner's direction; X is the one engine left.

Episode Intelligence's lesson, carried over: **decide before writing, and
write only from material.** Before §337 the drafting call was handed a post
and an episode's *title*, so a reply could say nothing the title did not.
Now one call, per card, first *reads*:

- `subject` - what the post is about, in a few words;
- `intent`  - asking, arguing, teaching, sharing news, reacting;
- `wants`   - what the people reading it want next;
- which of up to `OFFERED` kept episodes answers that (`choice`, -1 none),
  each shown with what it actually says - its summary and the opening of
  its script (`material`), not just its title;
- `angle` and `detail` - the one thing FAM's reply adds, and one specific
  from the chosen episode's material that carries it;

and only then writes, in the platform's voice (`VOICE`). The read is stored
on the card and shown on the page, so a person sees why the words are what
they are ([candidate-reason]).

## Rules that are load-bearing

**Nothing is stated that FAM has not got.** A figure in a drafted reply
must appear in the post, the episode's title or the material the call was
shown (`unsupported_figures`); one that does not sends the card back to the
code's words, and the card says so ([score-guard]'s spirit, here).

**A layer that adds quality must not subtract availability.** No model, or a
model that fails: `code_read` reads the post with the code's patterns, the
code's gates (`x_threads.on_subject`) stand, and the card says it was read
in code.

**Reads only what is kept.** `material` reads the script cache and nothing
else - no network, no model.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from config import as_minutes

log = logging.getLogger("fam.outreach_intelligence")

X = "x"

#: How many kept episodes the read chooses among: the best, and two more.
OFFERED = 3
#: How much of an episode's script the read is shown, per episode.
MATERIAL_CHARS = 900

#: What a post is doing.
INTENTS = ("asking", "arguing", "teaching", "news", "reacting", "other")


# --------------------------------------------------------------------------
# The episodes: what is kept, and what each one says


def catalogue(cache) -> list[dict]:
    """The episodes a post can be matched to: the newest 200 kept, never an
    explicit one, each with its names and its vector worked out once - a
    search run matches dozens of posts against the same episodes."""
    import embeddings
    import x_threads

    rows = []
    try:
        rows = cache.recent(limit=200) if cache is not None else []
    except Exception as exc:  # noqa: BLE001 - a match never raises
        log.warning("outreach: could not read the cache: %s", exc)
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
                    "plays": int(row.get("plays") or 0),
                    "names": x_threads.names_for(title, row["query"]),
                    "vector": vector})
    return out


def rank(episodes: list[dict], text: str, *, limit: int = OFFERED,
         post_vector: Optional[list] = None) -> list[dict]:
    """`episodes` (a `catalogue`) most about what the post is about, best
    first, each with its match (0-1): `x_threads.match`'s - embeddings 0.6,
    the episode's names 0.4. A name counts only as a whole word: a post runs
    to thousands of characters, and "Fed" is inside "federal"."""
    import embeddings

    if post_vector is None:
        try:
            post_vector = embeddings.embed(text)
        except Exception:  # noqa: BLE001
            post_vector = None
    low = (text or "").lower()
    scored = []
    for e in episodes:
        semantic = 0.0
        if post_vector is not None and e.get("vector") is not None:
            try:
                semantic = max(0.0, embeddings.cosine(post_vector, e["vector"]))
            except Exception:  # noqa: BLE001
                semantic = 0.0
        names = e.get("names") or ()
        named = (sum(1 for n in names if re.search(
            r"(?<!\w)" + re.escape(n.lower()) + r"(?!\w)", low)) / len(names)) if names else 0.0
        scored.append({"key": e["key"], "query": e["query"], "title": e["title"],
                       "minutes": e["minutes"], "plays": e["plays"],
                       "names": list(names),
                       "match": round(min(1.0, 0.6 * semantic + 0.4 * named), 4),
                       "off_subject": off_subject(text, e)})
    scored.sort(key=lambda e: (-e["match"], -e["plays"]))
    return scored[:limit]


@dataclass
class _Text:
    """What `x_threads.on_subject` reads of a post: its words."""
    text: str


def off_subject(text: str, episode: dict) -> str:
    """Why the post is not about the episode's subject, or "" when it is:
    `x_threads.on_subject` (§326) - it must name what the episode names, and
    a one-word name must come with one of the episode's own subject words.
    An episode with no names to check is judged by the match alone."""
    import x_threads

    names = tuple(episode.get("names") or ())
    if not names:
        return ""
    seed = x_threads.Seed(episode.get("key", ""), episode.get("query", ""),
                          episode.get("title", ""),
                          as_minutes(episode.get("minutes") or 1), names)
    return x_threads.on_subject(_Text(text), seed)


def material(cache, key: str, chars: int = MATERIAL_CHARS) -> dict:
    """What a kept episode actually says: its one-line summary, the opening
    of its script up to `chars`, and its Go Deeper suggestion. Read from the
    script cache only; any of them may be "" (an older cache, a stand-in)."""
    out = {"summary": "", "excerpt": "", "next": ""}
    if cache is None or not key:
        return out
    try:
        import content_filter
    except Exception:  # noqa: BLE001
        content_filter = None

    def clean(text) -> str:
        text = " ".join(str(text or "").split())
        return content_filter.scrub(text) if content_filter else text

    for field_name, reader in (("summary", "summary"), ("next", "thread")):
        try:
            read = getattr(cache, reader, None)
            out[field_name] = clean(read(key))[:300] if read else ""
        except Exception:  # noqa: BLE001
            out[field_name] = ""
    try:
        # `script`, never `get`: a quote is not a replay, and `get` counts
        # a hit - the hit rates a prefetch decision waits on ([dec-prefetch]).
        script = getattr(cache, "script", None)
        sentences = script(key) if script else None
    except Exception:  # noqa: BLE001
        sentences = None
    excerpt = ""
    for sentence in sentences or []:
        sentence = clean(sentence)
        if not sentence:
            continue
        if excerpt and len(excerpt) + 1 + len(sentence) > chars:
            break
        excerpt = f"{excerpt} {sentence}".strip()
    out["excerpt"] = excerpt[:chars]
    return out


def options_block(options: list[dict], materials: Optional[dict] = None) -> str:
    """The episodes the read chooses among, numbered from 0, each with what
    it says - so the read can tell the episode on this post's question from
    one on the same subject about something else."""
    materials = materials or {}
    lines = []
    for i, option in enumerate(options):
        lines.append(f"Episode {i}: “{option.get('title', '')}” "
                     f"({as_minutes(option.get('minutes') or 1)} min) - "
                     f"the question it answers: {option.get('query', '')}")
        said = materials.get(option.get("key", "")) or {}
        if said.get("summary"):
            lines.append(f"  In one line: {said['summary']}")
        if said.get("excerpt"):
            lines.append(f"  How it opens: {said['excerpt']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# The read


READ_RULES = """Before you write anything, read the post the way a good \
editor reads a question - decide first, write second:
- subject: what the post is about, in a few words (the team, person, \
company, idea or event - not a topic word).
- intent: what the post is doing - asking, arguing, teaching, news (sharing \
something that happened), reacting, or other.
- wants: what the people reading this post want next, in one sentence.
- same_subject: is ANY of FAM's episodes about the same subject as the \
post - the same team, person, company, event or question - and not merely \
sharing a word or a name with it (the Philadelphia Eagles are not eagles; \
Apple the company is not apples; a United Airlines flight is not \
Manchester United)? Judge strictly, because an off-topic reply is spam. \
When unsure, false.
- choice: the number of the episode that answers or deepens what THIS post \
asks or argues, or -1 when none does. A story on the same subject about \
something else is -1. When unsure, -1.
- why: one sentence - what the post is about, and whether the chosen \
episode is that.
- angle: the one thing FAM's words add that the post and its readers do \
not have yet, in one sentence.
- detail: one specific, true detail from the chosen episode's material \
(its one line or its opening) that carries the angle, copied closely; "" \
when there is none or no episode was chosen.

Then write, from the read and nothing else:
- Never state a number, date, name or result unless it appears in the post \
or in the chosen episode's title, one line or opening. Never claim to know \
the poster.
- Never "check out", never "link below", no hashtags, no emoji, no \
exclamation marks, no flattery ("great question", "great post", "love \
this")."""

#: How FAM sounds on each platform - the flip chart's X column.
VOICE = {
    X: """How FAM sounds on X - curiosity, and being part of the conversation:
- FAM is one more person in this thread, there because it is curious about \
the same thing. Answer what THIS post says, in the thread's register: \
short, plain, conversational.
- Spark curiosity with the detail: the turn, the cause or the specific that \
the thread has not said yet - stated plainly, never teased ("you won't \
believe", "here's the thing").
- Stay in the conversation: you may end with one short, genuine question \
back to the poster or the thread.
- Only when an episode was chosen may you say once, plainly, that FAM \
explored this in a short episode; otherwise never mention FAM or an \
episode.""",
}

READ_PROPERTIES = {
    "subject": {"type": "string"},
    "intent": {"type": "string", "enum": list(INTENTS)},
    "wants": {"type": "string"},
    "same_subject": {"type": "boolean"},
    "choice": {"type": "integer"},
    "why": {"type": "string"},
    "angle": {"type": "string"},
    "detail": {"type": "string"},
}


def system(platform: str, rules: str, chars: int) -> str:
    """The drafting call's instructions: who FAM is, the read, the
    platform's voice, then `rules` - what this engine's card needs besides."""
    return ("You draft a reply on X for FAM, an app that turns what people care "
            "about into short audio stories. It goes under somebody else's "
            "post. A person at FAM reads it and posts it themselves.\n\n"
            f"{READ_RULES}\n\n{VOICE[platform]}\n\n{rules}\n"
            f"- At most {chars} characters.")


def schema(write: dict) -> dict:
    """The read's fields first, then this engine's `write` fields: the call
    answers in this order, so it has decided before it writes."""
    properties = {**READ_PROPERTIES, **write}
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


@dataclass
class Read:
    """What the engine understood about one post. `choice` is an index into
    the episodes it was offered, -1 for none; `by` is a model id or "code"."""

    subject: str = ""
    intent: str = "other"
    wants: str = ""
    same_subject: bool = True
    choice: int = 0
    why: str = ""
    angle: str = ""
    detail: str = ""
    by: str = "code"
    #: Why the drafted words were replaced by the code's, or "".
    replaced: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _line(value, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def parse(body: dict, offered: int, by: str) -> Read:
    """The model's answer as a `Read`. A `choice` past the episodes offered
    is none. An answer from before §337 (`episode_fits`, `fits`, no
    `choice`) reads as the first episode or none."""
    if "choice" in body:
        try:
            choice = int(body.get("choice"))
        except (TypeError, ValueError):
            choice = -1
    elif "episode_fits" in body or "fits" in body:
        fits = body.get("episode_fits", body.get("fits"))
        choice = 0 if fits is not False else -1
    else:
        choice = 0
    if not 0 <= choice < offered:
        choice = -1
    intent = _line(body.get("intent"), 20).lower()
    return Read(subject=_line(body.get("subject"), 120),
                intent=intent if intent in INTENTS else "other",
                wants=_line(body.get("wants")),
                same_subject=body.get("same_subject") is not False,
                choice=choice, why=_line(body.get("why")),
                angle=_line(body.get("angle")), detail=_line(body.get("detail")),
                by=by)


def code_read(text: str, options: list, *, intent: str = "", why: str = "") -> Read:
    """The read with no model: the code's patterns for what the post is
    doing, the best-matched episode for what it is about, and no claim of
    an angle. `options` may be dicts or `x_threads.Seed`s."""
    import x_threads

    if not intent:
        intent = ("asking" if x_threads.asks(text) else
                  "arguing" if x_threads.takes_a_side(text) else "other")
    first = options[0] if options else None
    names = (getattr(first, "names", None) if first is not None and not isinstance(first, dict)
             else (first or {}).get("names")) or ()
    return Read(subject=", ".join(names[:2]), intent=intent,
                choice=0 if options else -1, why=why, by="code")


def summary(read: Optional[dict]) -> str:
    """A read in one line for a card: "Asking about the Eagles' defence;
    adds: ..."."""
    if not read:
        return ""
    bits = []
    intent = read.get("intent") or "other"
    head = {"asking": "Asking", "arguing": "Arguing", "teaching": "Teaching",
            "news": "Sharing news", "reacting": "Reacting"}.get(intent, "About")
    if read.get("subject"):
        bits.append(f"{head} about {read['subject']}" if head != "About"
                    else f"About {read['subject']}")
    if read.get("angle"):
        bits.append(f"adds: {read['angle']}")
    return "; ".join(bits)


# --------------------------------------------------------------------------
# What the words may say


#: A figure standing alone: "31", "$4.5", "40%", "1,200". Digits joined to
#: letters or slashes are names and shorthand, not claims - "5G", "Q3",
#: "2nd", "3x", "24/7", "COVID-19", "49ers".
_FIGURE = re.compile(r"(?<![A-Za-z0-9/])(?<![A-Za-z]-)\$?\d[\d,]*(?:\.\d+)?%?"
                     r"(?![A-Za-z0-9/])")


def _figures(text: str) -> set:
    out = set()
    for raw in _FIGURE.findall(text or ""):
        digits = raw.strip("$%").replace(",", "")
        if digits:
            out.add(digits.rstrip("0").rstrip(".") if "." in digits else digits)
    return out


def unsupported_figures(words: str, sources: Iterable[str]) -> list[str]:
    """The figures in `words` that none of `sources` states - the post, the
    episode's title, the material the call was shown. A reply that names
    one is saying something FAM has not got."""
    have = set()
    for source in sources:
        have |= _figures(source)
    return sorted(_figures(words) - have)


def sources_for(post_text: str, option: Optional[dict], said: Optional[dict]) -> list[str]:
    """What a reply under this post may state figures from."""
    option, said = option or {}, said or {}
    return [post_text or "", option.get("title", ""), option.get("query", ""),
            said.get("summary", ""), said.get("excerpt", "")]
