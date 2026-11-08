"""A short name for a topic somebody typed into a mix (11.7 packet).

The owner's words, marked the most important thing on the mix screen: a
listener who types a long prompt - "positive story of the day of things
happening in the world" - should see it in their mix as something like
"Daily positive story", never the whole prompt. The prompt is still what is
asked every morning; only the label is shortened.

Two ways to the name, and the second is never skipped:

* **One small model call** (`TOPIC_TITLE_MODEL`, Haiku) when there is a key,
  the switch is on, and the listener's words may reach Anthropic
  (`consent.py` - the caller decides). Asked for a title of at most
  `MAX_WORDS` words, answered as JSON, checked here before it is kept.
* **A rule in code** (`heuristic`) whenever the call is not made, fails,
  times out or answers something unusable. It keeps the words that carry the
  subject, drops the ones that only frame the request ("tell me", "things
  happening", "in the world"), and says "Daily" when the prompt asks for one
  a day. Availability outranks the nicer name: a mix is never refused, and a
  topic is never left untitled, because a model was slow.

A short prompt is already a name - "Stanford football" is titled as typed,
with no call at all. Nothing here is on a listening path: it runs once, when
the topic is added.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

import content_filter

log = logging.getLogger(__name__)

#: A prompt of this many words or fewer is its own title.
SHORT_WORDS = 4
#: The longest title kept, in words and in characters. The mix screen draws
#: one line per topic; past this it is a sentence again.
MAX_WORDS = 5
MAX_CHARS = 40

#: Asked of the model. The example is the owner's own.
SYSTEM = (
    "You name topics in a listener's daily audio mix. Given what they typed, "
    "reply with a short title of at most five words that says what they will "
    "hear, the way a playlist names a track. Paraphrase; never copy the whole "
    "request. Keep any names (people, teams, companies, places) exactly. "
    "If it asks for something every day, the title may start with 'Daily'. "
    "Example: 'positive story of the day of things happening in the world' -> "
    "'Daily positive story'. Title case is not required; capitalise the first "
    "word and names only.")

SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
    "additionalProperties": False,
}

#: Phrases that frame a request rather than name its subject. Longest first,
#: so "what is happening in" goes before "happening in".
_FRAMING = sorted((
    "can you tell me about", "can you tell me", "tell me about", "tell me",
    "give me", "i want to hear about", "i want to hear", "i want",
    "i'd like to hear about", "i'd like", "let me know about", "keep me posted on",
    "keep me updated on", "update me on", "updates on", "an update on",
    "what is happening in", "what is happening with", "what's happening in",
    "what's happening with", "what is going on in", "what is going on with",
    "what's going on in", "what's going on with", "what's new in", "what's new with",
    "things happening in the world", "things happening", "happening in the world",
    "going on in the world", "around the world", "in the world", "of things",
    "the latest on", "the latest in", "latest news on", "latest news about",
    "news about", "a story about", "stories about", "a summary of", "summary of",
    "a recap of", "recap of", "please", "this week", "right now", "these days",
    "lately", "recently",
), key=len, reverse=True)

#: Phrases meaning "one a day", turned into a leading "Daily".
_DAILY = ("of the day", "every day", "each day", "every morning", "each morning",
          "daily", "today", "today's", "todays", "for the day", "a day")

#: Words that carry nothing on their own once the framing is gone.
_FILLER = {
    "a", "an", "the", "of", "about", "on", "in", "for", "to", "and", "with",
    "me", "my", "i", "some", "any", "all", "that", "this", "these", "those",
    "is", "are", "was", "be", "being", "what", "whats", "what's", "how",
    "which", "who", "it", "its", "it's", "things", "stuff", "there", "here",
    "happening", "going", "new", "latest", "please", "just", "really", "very",
    "from", "at", "by", "into", "around", "world",
}

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'&.+-]*")


def _tidy(text: str) -> str:
    return " ".join(str(text or "").split())


def _cap_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def is_short(query: str) -> bool:
    """Whether the prompt is already short enough to be its own title."""
    return len(_WORD.findall(query or "")) <= SHORT_WORDS


def heuristic(query: str) -> str:
    """A short title by rule, for when no model is asked. Never empty for a
    non-empty prompt."""
    text = _tidy(query).rstrip("?.! ")
    if not text:
        return ""
    if is_short(text):
        return _clip(_cap_first(text))
    low = " " + text.lower() + " "
    daily = any(" " + d + " " in low for d in _DAILY)
    for phrase in _DAILY + tuple(_FRAMING):
        low = low.replace(" " + phrase + " ", " ")
    # Keep the listener's own capitals: walk the original words, keeping each
    # one whose lower case survived the stripping, in order.
    survivors = low.split()
    kept: list[str] = []
    at = 0
    for word in _WORD.findall(text):
        w = word.lower()
        if at < len(survivors) and survivors[at] == w:
            at += 1
            if w not in _FILLER:
                kept.append(word)
    if not kept:
        # Everything was framing: fall back to the first few words as typed.
        kept = _WORD.findall(text)[:SHORT_WORDS]
    words = kept[:MAX_WORDS - (1 if daily else 0)]
    title = ("Daily " + " ".join(w if _is_name(w) else w.lower() for w in words)) if daily \
        else _cap_first(" ".join(words))
    return _clip(title)


def _is_name(word: str) -> bool:
    """A word the listener capitalised mid-sentence, or one with a digit or
    capitals inside it (NFL, iPhone, F1) - kept as typed."""
    return any(c.isupper() for c in word[1:]) or any(c.isdigit() for c in word) \
        or (word[:1].isupper() and len(word) > 1)


def _clip(title: str) -> str:
    title = _tidy(title)
    if len(title) <= MAX_CHARS:
        return title
    cut = title[:MAX_CHARS].rsplit(" ", 1)[0]
    return cut or title[:MAX_CHARS]


def acceptable(title: str, query: str) -> Optional[str]:
    """The model's title, cleaned, or None when it is not a short title.

    Refused: empty, longer than `MAX_WORDS` words, or the whole prompt back
    again (which is the thing the owner asked never to show)."""
    title = _tidy(title).strip(" \"'`.")
    if not title:
        return None
    if len(_WORD.findall(title)) > MAX_WORDS or len(title) > MAX_CHARS + 10:
        return None
    if title.lower() == _tidy(query).lower().rstrip("?.! "):
        return None
    title = content_filter.clean_line(title, MAX_CHARS + 10)
    return _clip(_cap_first(title)) or None


async def short_title(query: str, *, allow_model: bool = True,
                      usage=None) -> tuple[str, str]:
    """(title, how) for a typed topic. `how` is "as-typed", "model" or
    "rule" - what produced the name, for the log and the tests.

    Never raises and never returns an empty title for a non-empty prompt."""
    query = _tidy(query)
    if not query:
        return "", "as-typed"
    if is_short(query):
        return _clip(_cap_first(query)), "as-typed"
    if allow_model:
        named = await _ask_model(query, usage)
        if named:
            return named, "model"
    return heuristic(query), "rule"


async def _ask_model(query: str, usage=None) -> Optional[str]:
    from config import settings
    if not settings.topic_title:
        return None
    import credentials
    key = credentials.active("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        from anthropic_client import build_async_client
        client = build_async_client(key)
        response = await asyncio.wait_for(
            client.messages.create(
                model=settings.topic_title_model,
                max_tokens=settings.topic_title_max_tokens,
                system=SYSTEM,
                output_config={"effort": "low",
                               "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": query}],
            ),
            timeout=float(settings.topic_title_timeout_seconds),
        )
    except asyncio.TimeoutError:
        log.info("topic_title: timed out; titled by rule")
        return None
    except Exception as exc:  # noqa: BLE001 - availability outranks the name
        log.info("topic_title: %s; titled by rule", exc)
        return None
    if usage is not None:
        try:
            usage.add_model_call(settings.topic_title_model, response.usage,
                                 source="topic title")
        except Exception:  # noqa: BLE001 - metering never fails a save
            log.exception("topic_title: could not meter the call")
    else:
        import metering

        metering.count_call("topic title", settings.topic_title_model,
                            getattr(response, "usage", None))
    return title_from(response, query)


def title_from(response, query: str) -> Optional[str]:
    """The model's answer as a title, or None. Split out so it is testable
    without a network."""
    try:
        text = next(b.text for b in response.content if b.type == "text")
        answer = json.loads(text) or {}
    except (StopIteration, AttributeError, ValueError, TypeError):
        return None
    return acceptable(str(answer.get("title") or ""), query)
