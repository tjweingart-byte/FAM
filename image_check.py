"""Checking a photo before other people see it (App Store 1.2, PROBLEMS.md §224).

A profile picture and a public mix's cover are the two images FAM shows to
strangers. Both are small (a 96 KB avatar, a 360px cover), uploaded rarely,
and looked at by one model call each before they are kept, on the cheapest
current model (`IMAGE_CHECK_MODEL`, Claude Haiku 5.5): about $0.0002 a photo.
That is why the owner chose an automated check over relying on Report +
Remove alone, and why it does not run on the writer's model (§225).

What is refused is narrow on purpose, the image counterpart of `slurs-only`:
nudity and sexual content, graphic violence or gore, hate symbols or slurs in
the picture, and anything sexualising a minor. Ordinary photos of people,
swimwear at a beach, art, memes, text and logos are all fine. A photo that
merely swears is not refused - swearing is marked, never removed.

**A check that cannot run never costs the listener their photo** (the rule
`ei-before-search` states for every quality layer): no key (staging spends
nothing and has none), a timeout or an error lets the photo through, says so
in the log, and leaves it to Report + Remove. `Verdict.checked` records which.
A refusal by the model to look at the image is the one failure that refuses:
it is the model saying the picture is the problem.

The listener is told before they pick a photo that it is checked
automatically (the photo editor's note, and the privacy policy), which is the
disclosure 5.1.2(i) asks for when an image goes to a third-party AI.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

#: What a verdict can name. `ok` is the only one that keeps the photo.
CATEGORIES = ("ok", "nudity", "sexual", "violence", "hate", "minor")

#: What the listener reads when a photo is refused. One sentence for every
#: category: telling somebody which rule a picture broke helps nobody find a
#: way round it.
REFUSED = ("That photo can't be used on FAM. Pick another one - photos are "
           "checked automatically for nudity, violence and hate symbols.")

SYSTEM = """You check one image before a social audio app shows it to other people as a profile picture or a playlist cover.

Refuse only these:
- nudity: exposed genitals, female nipples, or buttocks
- sexual: sexual activity or sexually suggestive poses
- violence: graphic violence, gore, serious injury or dead bodies
- hate: hate symbols (for example a swastika used approvingly) or slurs written in the image
- minor: anything that sexualises a child

Everything else is fine and must be allowed: ordinary photos of people, faces, groups, pets, places, food, sport, swimwear at a beach or pool, art, cartoons, memes, screenshots, text, logos, and swearing.

Answer with allowed true and category "ok", or allowed false and the category that applies."""

SCHEMA = {
    "type": "object",
    "properties": {
        "allowed": {"type": "boolean"},
        "category": {"type": "string", "enum": list(CATEGORIES)},
    },
    "required": ["allowed", "category"],
    "additionalProperties": False,
}

#: Image types the model reads, as the app's cropper and pickers produce them.
MEDIA_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")


@dataclass
class Verdict:
    allowed: bool
    category: str = "ok"
    #: False when no check ran (off, no key, error, timeout): the photo was
    #: let through on Report + Remove alone.
    checked: bool = False
    note: str = ""


def _unchecked(note: str) -> Verdict:
    log.warning("image_check: photo let through unchecked (%s)", note)
    return Verdict(allowed=True, category="ok", checked=False, note=note)


def split_data_url(data_url: str) -> Optional[tuple[str, str]]:
    """(media type, base64) for a `data:image/...;base64,` URL, or None."""
    head, sep, data = str(data_url or "").partition(",")
    if not sep or not head.startswith("data:") or ";base64" not in head:
        return None
    media = head[5:].split(";", 1)[0].strip().lower()
    if media == "image/jpg":
        media = "image/jpeg"
    if media not in MEDIA_TYPES:
        return None
    try:
        base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None
    return media, data


async def check(data_url: str, usage=None) -> Verdict:
    """One look at one photo. Never raises.

    `usage` is a `metering.Usage` the caller records against the listener
    (`metering`: cost per listener at spend time).
    """
    from config import settings
    if not settings.image_check:
        return Verdict(allowed=True, checked=False, note="off")
    parts = split_data_url(data_url)
    if parts is None:
        # The stores refuse what is not an image; nothing here to look at.
        return Verdict(allowed=True, checked=False, note="not an image")
    import credentials
    key = credentials.active("ANTHROPIC_API_KEY")
    if not key:
        return _unchecked("no ANTHROPIC_API_KEY")
    media, data = parts
    try:
        from anthropic_client import build_async_client
        client = build_async_client(key)
        response = await asyncio.wait_for(
            client.messages.create(
                model=settings.image_check_model,
                max_tokens=settings.image_check_max_tokens,
                system=SYSTEM,
                output_config={"effort": "low",
                               "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": media, "data": data}},
                    {"type": "text", "text": "Check this image."},
                ]}],
            ),
            timeout=float(settings.image_check_timeout_seconds),
        )
    except asyncio.TimeoutError:
        return _unchecked("timed out")
    except Exception as exc:  # noqa: BLE001 - availability outranks the check
        return _unchecked("error: %s" % exc)

    if usage is not None:
        try:
            usage.add_model_call(settings.image_check_model, response.usage)
        except Exception:  # noqa: BLE001 - metering never fails an upload
            log.exception("image_check: could not meter the call")
    return verdict_from(response)


def verdict_from(response) -> Verdict:
    """The model's answer as a `Verdict`. Split out so it is testable without
    a network."""
    if getattr(response, "stop_reason", "") == "refusal":
        # The model would not look at it: the picture is the problem.
        return Verdict(allowed=False, category="other", checked=True, note="refused")
    try:
        text = next(b.text for b in response.content if b.type == "text")
        answer = json.loads(text) or {}
    except (StopIteration, AttributeError, ValueError, TypeError) as exc:
        return _unchecked("unreadable answer: %s" % exc)
    category = str(answer.get("category") or "ok")
    if category not in CATEGORIES:
        category = "ok"
    allowed = bool(answer.get("allowed")) and category == "ok"
    if not allowed:
        log.info("image_check: refused a photo (%s)", category)
    return Verdict(allowed=allowed, category=category if not allowed else "ok",
                   checked=True)


def report() -> dict:
    """For `/api/health`: whether photos are being checked, and by what."""
    from config import settings
    import credentials
    return {"enabled": bool(settings.image_check),
            "model": settings.image_check_model,
            "can_check": bool(settings.image_check
                              and credentials.active("ANTHROPIC_API_KEY"))}
