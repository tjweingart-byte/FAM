"""The evidence the writer reads, cut to what the episode can use (§270).

The writer's thinking happens in front of the first word, and part of it is
reading the packet. This hands it a shorter one: a Haiku call with thinking
off chooses which of the packet's sentences the episode could use, and the
writer (thinking on) reads only those.

**Extractive, never a summary - the guard rail the rest stand on.** Code
splits the packet's passages into numbered sentences; the model answers with
numbers and nothing else; code assembles the packet the writer reads from the
sources' own sentences, in their own order, under the headers code wrote (the
title, the date, how long ago, the kind of source). So the model's words never
reach the writer: it cannot invent a fact, change a figure, put a hostname in
reach of the voice, or turn "is expected to" into "did" ([started-not-finished]).
The worst it can do is leave a sentence out, and every rule below is about that:

* **Nothing is subtracted for good.** A source it chose nothing from keeps its
  passages whole. A call that fails, times out, is refused, runs out of room or
  answers nonsense leaves the whole packet as it was - counted, and noted on
  /admin's errors. "A layer that adds quality must not subtract availability"
  ([ei-before-search]) holds for a layer that adds speed too.
* **What the brief asked to establish stays established.** When the raw packet
  covers an item of `must_establish` (`research.packet_covers`, the same test
  the second look uses) and the cut one does not, the whole packet is used.
* **Every figure stays on a scores or markets episode.** On those, any
  sentence with a digit is kept by code whatever the model chose: a figure
  the writer cannot see is a figure it may recall, and recalled figures are
  what `ScoreGuard` and `FigureGuard` exist to catch.
* **The guards still read the whole packet.** `EpisodePlan.evidence` is left
  as it was; the cut packet is `EpisodePlan.evidence_digest`, which only the
  writer's prompt reads. A figure the writer states from a kept sentence is in
  the full packet too, so the checks lose nothing.
* **A cut that saves little is not made.** Above `KEEP_LIMIT` of the
  passages' length the writer gets the original, byte for byte.
* **Small packets are left alone** (`EVIDENCE_DIGEST_MIN_CHARS`): the call
  costs about a second, and a short packet costs the writer less than that.

**Off by default** (`EVIDENCE_DIGEST=0`) until a run with a key says it helps:
`python write.py "<q>" --compare claude-haiku-5-5:low,claude-haiku-5-5:low+digest`.
It runs while `prepare` is already waiting on the live lookup and the
forecast, so part of its second is hidden behind a wait that was there before.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import re
import time
from dataclasses import dataclass, field

from config import settings

log = logging.getLogger(__name__)

#: A block of the packet starts on one of these lines (`research.build_packet`
#: writes `SOURCE n`, `stories.as_evidence` writes the measurement). Anything
#: that is not a `SOURCE` block is passed through untouched.
_BLOCK_HEAD = re.compile(r"^(?:SOURCE \d+|WHAT FAM MEASURED)\s*$", re.M)
_SOURCE_HEAD = re.compile(r"^SOURCE \d+\s*$")
_PASSAGES = "Key evidence:"
#: Where one sentence ends and the next begins. Coarse on purpose: a split
#: inside "U.S. Senate" makes two numbered pieces, and choosing one without
#: the other is the worst it does - a fragment the writer can still read.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])[\"'”’)\]]*\s+(?=[\"'“‘(\[]?[A-Z0-9])")
#: A piece shorter than this is joined to the next rather than numbered alone.
_MIN_PIECE = 25
_DIGIT = re.compile(r"\d")

#: Above this share of the passages' characters the cut is not worth making.
KEEP_LIMIT = 0.85
#: Outcomes, in words, for `/api/health` and the episode's record.
OUTCOMES = ("cut", "no_gain", "lost_coverage", "nothing_chosen", "timeout",
            "failed", "refused", "out_of_room", "unreadable")

SYSTEM = """You choose which sentences of a search result an audio news \
episode can use. You never write the episode and you never rewrite a \
sentence: you answer with sentence numbers only.

Keep every sentence about the question's subject: what happened, when, \
who, every figure, score, price, date and result, whether something is \
finished, under way or only expected, what people said, and the context a \
listener needs to understand those. When you are unsure, keep it.

Leave out only sentences that have nothing to do with the question: \
navigation, adverts, subscription prompts, captions, related-story lists, \
and a sentence that repeats one already kept word for word."""

DIGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["keep"],
    "additionalProperties": False,
}
FORMAT = {"type": "json_schema", "schema": DIGEST_SCHEMA}


@dataclass
class _Source:
    head: str               # the lines code wrote: SOURCE n, title, date, type
    passages: list          # one list of pieces per passage


@dataclass
class _Parsed:
    blocks: list = field(default_factory=list)   # _Source or verbatim str
    pieces: list = field(default_factory=list)   # (block, passage, text)

    @property
    def sources(self) -> list:
        return [b for b in self.blocks if isinstance(b, _Source)]


@dataclass
class Digest:
    """What the cut did: the packet the writer reads, and how it got there."""

    outcome: str
    text: str = ""          # "" whenever the writer reads the packet as it was
    raw_chars: int = 0
    kept_chars: int = 0
    seconds: float = 0.0
    detail: str = ""

    def as_dict(self) -> dict:
        return {"outcome": self.outcome, "raw_chars": self.raw_chars,
                "kept_chars": self.kept_chars, "seconds": round(self.seconds, 3),
                "detail": self.detail}


def split_sentences(text: str) -> list:
    """The pieces a passage is numbered in, each at least `_MIN_PIECE` long
    unless it is the passage's last."""
    raw = [p.strip() for p in _SENTENCE_BREAK.split(" ".join(text.split()))]
    pieces: list = []
    carry = ""
    for part in raw:
        if not part:
            continue
        carry = f"{carry} {part}".strip() if carry else part
        if len(carry) >= _MIN_PIECE:
            pieces.append(carry)
            carry = ""
    if carry:
        if pieces:
            pieces[-1] = f"{pieces[-1]} {carry}"
        else:
            pieces.append(carry)
    return pieces


def parse(packet: str) -> _Parsed:
    """The packet as blocks, and its passages as numbered pieces."""
    out = _Parsed()
    starts = [m.start() for m in _BLOCK_HEAD.finditer(packet)]
    if not starts:
        out.blocks.append(packet)
        return out
    if starts[0] > 0:
        out.blocks.append(packet[:starts[0]])
    for i, start in enumerate(starts):
        chunk = packet[start:starts[i + 1] if i + 1 < len(starts) else len(packet)]
        first, _, _rest = chunk.partition("\n")
        head, marker, body = chunk.partition(_PASSAGES)
        if not _SOURCE_HEAD.match(first) or not marker:
            out.blocks.append(chunk)
            continue
        source = _Source(head=head.rstrip("\n"), passages=[])
        for line in body.split("\n"):
            if not line.strip():
                continue
            pieces = split_sentences(line)
            index = len(source.passages)
            source.passages.append(pieces)
            for text in pieces:
                out.pieces.append((source, index, text))
        out.blocks.append(source)
    return out


def _passage_chars(parsed: _Parsed) -> int:
    return sum(len(t) + 1 for _, _, t in parsed.pieces)


def assemble(parsed: _Parsed, keep: set) -> str:
    """The packet with only the kept pieces, in their own order, under their
    own headers. A source with nothing kept keeps everything."""
    kept_by_source: dict = collections.defaultdict(set)
    for number, (source, passage, text) in enumerate(parsed.pieces, 1):
        if number in keep:
            kept_by_source[id(source)].add((passage, text))
    out: list = []
    for block in parsed.blocks:
        if not isinstance(block, _Source):
            out.append(block.rstrip("\n"))
            continue
        chosen = kept_by_source.get(id(block))
        lines = [block.head, _PASSAGES]
        for index, pieces in enumerate(block.passages):
            kept = [t for t in pieces if chosen is None or (index, t) in chosen]
            if kept:
                lines.append(" ".join(kept))
        out.append("\n".join(lines))
    return "\n\n".join(b for b in out if b.strip()) + "\n"


def prompt(parsed: _Parsed, query: str, must_establish) -> str:
    lines = [f"The question: {query}"]
    wanted = [str(m).strip() for m in (must_establish or []) if str(m).strip()]
    if wanted:
        lines.append("The episode must establish: " + "; ".join(wanted))
    lines += ["", "The sentences, by source:"]
    current = None
    for number, (source, _passage, text) in enumerate(parsed.pieces, 1):
        if source is not current:
            current = source
            lines += ["", source.head]
        lines.append(f"[{number}] {text}")
    lines += ["", 'Answer {"keep": [numbers]} - every sentence the episode '
              "could use."]
    return "\n".join(lines)


def _figures_matter(plan) -> bool:
    """A scores or markets episode, by any of the three ways one is known."""
    brief = getattr(plan, "brief", None)
    live = getattr(plan, "live", None)
    named = {str(getattr(brief, "live_domain", "") or ""),
             str(getattr(brief, "live_route", "") or ""),
             str(getattr(live, "domain", "") or "")}
    return bool(named & {"sports", "markets"})


# -- counted since boot, by who was waiting, as the brief is (§268) ---------
SECONDS_KEPT = 200
_BY_PATH: dict = {"search": collections.Counter(), "ahead": collections.Counter()}
_SECONDS: dict = {"search": collections.deque(maxlen=SECONDS_KEPT),
                  "ahead": collections.deque(maxlen=SECONDS_KEPT)}
_KEPT_SHARE: collections.deque = collections.deque(maxlen=SECONDS_KEPT)


def _note(result: Digest, path: str, query: str) -> Digest:
    _BY_PATH.setdefault(path, collections.Counter())[result.outcome] += 1
    _SECONDS.setdefault(path, collections.deque(maxlen=SECONDS_KEPT)).append(
        round(result.seconds, 3))
    import pipeline_errors as _errors

    _errors.step("evidence cut", path, result.outcome, result.seconds)
    if result.outcome == "cut" and result.raw_chars:
        _KEPT_SHARE.append(round(result.kept_chars / result.raw_chars, 3))
    if result.outcome in ("timeout", "failed", "refused", "out_of_room",
                          "unreadable"):
        import pipeline_errors

        # A notice, not an error: the episode is written from the whole
        # packet, exactly as it was before this layer existed.
        pipeline_errors.record("research", f"evidence cut {result.outcome}",
                               result.detail, query, pipeline_errors.NOTICE)
    return result


def wanted(plan) -> bool:
    """Whether this plan's packet is one the cut is tried on."""
    evidence = str(getattr(plan, "evidence", "") or "")
    return (bool(settings.evidence_digest) and bool(evidence.strip())
            and len(evidence) >= int(settings.evidence_digest_min_chars))


async def digest(plan, notes=None, ahead: bool = False, client=None) -> Digest:
    """Cut `plan.evidence` to what the episode can use. Never raises.

    Returns a `Digest` whose `text` is the packet for the writer, or "" when
    the writer should read `plan.evidence` as it is - which every failure,
    and every cut that would lose something, comes to.
    """
    started = time.monotonic()
    path = "ahead" if ahead else "search"
    query = str(getattr(plan, "query", "") or "")
    packet = str(getattr(plan, "evidence", "") or "")
    parsed = parse(packet)
    raw_chars = _passage_chars(parsed)

    def done(outcome: str, text: str = "", kept: int = 0, detail: str = "") -> Digest:
        return _note(Digest(outcome, text, raw_chars, kept or raw_chars,
                            time.monotonic() - started, detail), path, query)

    if len(parsed.pieces) < 2:
        return done("no_gain", detail="nothing to choose between")

    timeout = float(settings.evidence_digest_ahead_timeout_seconds if ahead
                    else settings.evidence_digest_timeout_seconds)
    brief = getattr(plan, "brief", None)
    must = list(getattr(brief, "must_establish", None) or [])
    model = settings.evidence_digest_model
    try:
        if client is None:
            import credentials
            from anthropic_client import build_async_client

            client = build_async_client(credentials.active("ANTHROPIC_API_KEY"))
        response = await asyncio.wait_for(client.messages.create(
            model=model,
            max_tokens=int(settings.evidence_digest_max_tokens),
            system=SYSTEM,
            # Thinking off: choosing sentences is reading, not reasoning, and
            # thinking would put back the wait this exists to remove. Haiku
            # 5.5 accepts it at `low`.
            thinking={"type": "disabled"},
            output_config={"effort": "low", "format": FORMAT},
            messages=[{"role": "user", "content": prompt(parsed, query, must)}],
        ), timeout=timeout)
    except asyncio.TimeoutError:
        return done("timeout", detail=f"no answer within {timeout:g}s")
    except Exception as exc:  # noqa: BLE001 - the whole packet is the fallback
        return done("failed", detail=f"{type(exc).__name__}: {exc}")

    if notes is not None:
        notes.usage.add_model_call(model, getattr(response, "usage", None))
    stop = getattr(response, "stop_reason", "")
    if stop == "refusal":
        return done("refused", detail="the call was declined")
    if stop == "max_tokens":
        return done("out_of_room", detail="ran out of room at "
                    f"EVIDENCE_DIGEST_MAX_TOKENS={settings.evidence_digest_max_tokens}")
    try:
        text = next(b.text for b in response.content if b.type == "text")
        numbers = json.loads(text)["keep"]
        keep = {int(n) for n in numbers
                if isinstance(n, (int, float)) and 1 <= int(n) <= len(parsed.pieces)}
    except (StopIteration, AttributeError, ValueError, TypeError, KeyError) as exc:
        return done("unreadable", detail=f"{type(exc).__name__}: {exc}")
    if not keep:
        return done("nothing_chosen", detail="no sentence was chosen")

    # Every figure stays on a scores or markets episode, whatever was chosen.
    if _figures_matter(plan):
        keep |= {n for n, (_, _, t) in enumerate(parsed.pieces, 1)
                 if _DIGIT.search(t)}

    cut = assemble(parsed, keep)
    kept_chars = _passage_chars(parse(cut))
    if kept_chars > raw_chars * KEEP_LIMIT:
        return done("no_gain", kept=kept_chars,
                    detail=f"kept {kept_chars} of {raw_chars} characters")

    # What the brief asked to establish stays established.
    if must:
        import research

        _ok, raw_missing = research.packet_covers(packet, must)
        _ok, cut_missing = research.packet_covers(cut, must)
        lost = [m for m in cut_missing if m not in raw_missing]
        if lost:
            return done("lost_coverage", kept=kept_chars,
                        detail="the cut dropped: " + "; ".join(lost))
    return done("cut", text=cut, kept=kept_chars,
                detail=f"kept {kept_chars} of {raw_chars} characters")


def report() -> dict:
    """For /api/health: whether the cut runs, and how it has gone since boot."""
    from episode_intelligence import _spread

    shares = sorted(_KEPT_SHARE)
    return {
        "enabled": bool(settings.evidence_digest),
        "model": settings.evidence_digest_model,
        "thinking": "off",
        "timeout_seconds": settings.evidence_digest_timeout_seconds,
        "ahead_timeout_seconds": settings.evidence_digest_ahead_timeout_seconds,
        "min_chars": settings.evidence_digest_min_chars,
        "since_boot_by_path": {p: dict(c) for p, c in _BY_PATH.items()},
        "seconds_by_path": {p: _spread(s) for p, s in _SECONDS.items()},
        # The share of the passages a cut kept: the input the writer was
        # spared. Whether that shortened its thinking is in the episode
        # timing's `writer thinking` row, not here.
        "kept_share_median": shares[len(shares) // 2] if shares else None,
    }
