"""Shakespeare, the editor in chief - the outline (Phase 3, piece 3, §437).

docs/agent_rules/shakespeare.md: once both packets are back, work alongside
the writer as a professional journalist would - choose what matters, outline
the story, and see that it opens, flows and closes well. Decided items 28,
29, 30 and 37: his model is a setting (`EDITOR_MODEL`, Sonnet 5.5 until the
owner decides on the comparison); the journalism playbook is part of his
instructions (the rule-book lock adds it); `examples/` are in his cached
prompt; he is judged automatically, never by hand-scoring.

**Ahead of the tap only, for now** (the owner, 2026-11-03): editions and the
write-ahead, where nobody waits. On searchFAM he will run in tandem with the
writer, never a step after it; how is designed once the first chunk's wait is
measured (P4).

**Extractive by construction.** The packets are numbered sentence by
sentence (`number_packets`) - `P3.2` is the plugin packet's third source,
second sentence; `A1.1` the archive's - and his answer may only name those
numbers and a beat's short purpose. Code checks every number exists, drops
any that do not, drops a beat left with nothing behind it ([depth-not-words]),
drops a "now" beat resting only on the archive (history is never how things
stand now), and builds the writer's block from the sources' own sentences
(`writer_block`). His own wording never reaches the writer as evidence
(every-agent.md, never 2): only a beat's purpose, which says what the beat is
for, never a fact.

**Never costs an episode.** Off, late, refused, malformed or empty, the writer
reads the packets exactly as it does today.
"""
from __future__ import annotations

import collections
import logging
import pathlib
import re
import threading
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agents.handoffs import Beat, Outline, SentenceRef

log = logging.getLogger("fam.agents")

ROOT = pathlib.Path(__file__).resolve().parent.parent

SINCE_BOOT: "collections.Counter[str]" = collections.Counter()
_LOCK = threading.Lock()


def _count(kind: str, n: int = 1) -> None:
    with _LOCK:
        SINCE_BOOT[kind] += n


#: At most this many sentences are numbered from one source, and this many
#: sources in all - his call's size is bounded by the packet, not by luck.
SENTENCES_PER_SOURCE = 12
MAX_SOURCES = 16
#: A beat's purpose may say what the beat is for, in a few words, never a fact.
MAX_BEATS = 8


# ------------------------------------------------------------------ the numbered packets
@dataclass
class NumberedSource:
    packet: str                 # "plugin" or "archive"
    number: int                 # 1-based within its packet
    title: str
    published: str
    sentences: list = field(default_factory=list)

    @property
    def prefix(self) -> str:
        return ("P" if self.packet == "plugin" else "A") + str(self.number)


def _split(text: str) -> list:
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        out.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“'])", line)
                   if len(s.strip()) > 2)
    return out


def number_packets(plan) -> list:
    """The two packets as numbered sources: the evidence packet's sources
    (the archive's marked as such) and the live block, each sentence by its
    position. Never raises."""
    import research

    try:
        evidence = str(getattr(plan, "evidence", "") or "")
        archive_label = "Source type: " + research.TIER_LABELS["archive"]
        out, counts = [], {"plugin": 0, "archive": 0}
        live = getattr(plan, "live", None)
        if live is not None:
            try:
                lines = _split(live.as_prompt_block())
            except Exception:  # noqa: BLE001
                lines = []
            if lines:
                counts["plugin"] += 1
                out.append(NumberedSource("plugin", counts["plugin"], "live state", "now",
                                          lines[:SENTENCES_PER_SOURCE]))
        for chunk in re.split(r"(?m)^(?=SOURCE \d+\s*$)", evidence):
            if not chunk.strip().startswith("SOURCE"):
                continue
            packet = "archive" if archive_label in chunk else "plugin"
            title = (re.search(r"^Title:\s*(.*)$", chunk, re.M) or [None, ""])[1]
            day = (re.search(r"^Published:\s*(.*)$", chunk, re.M) or [None, ""])[1]
            body = chunk.split("Key evidence:", 1)[1] if "Key evidence:" in chunk else ""
            sentences = _split(body)[:SENTENCES_PER_SOURCE]
            if not sentences:
                continue
            counts[packet] += 1
            out.append(NumberedSource(packet, counts[packet], title.strip(), day.strip(),
                                      sentences))
            if len(out) >= MAX_SOURCES:
                break
        return out
    except Exception:  # noqa: BLE001
        log.warning("Shakespeare could not number the packets", exc_info=True)
        return []


def packets_text(sources: list) -> tuple:
    """`(plugin, archive)` as he reads them: each sentence behind its number."""
    parts = {"plugin": [], "archive": []}
    for source in sources:
        lines = [f"{source.prefix} - {source.title} (published {source.published})"]
        lines += [f"[{source.prefix}.{i}] {s}" for i, s in enumerate(source.sentences, 1)]
        parts[source.packet].append("\n".join(lines))
    return "\n\n".join(parts["plugin"]), "\n\n".join(parts["archive"])


# ------------------------------------------------------------------ his answer
_REF = r"^[PA]\d{1,2}\.\d{1,2}$"


class _Beat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str
    sentences: list[str]


class EditorAnswer(BaseModel):
    """What he may say: numbers, and a beat's purpose. No field for a fact.
    Sizes are trimmed in code (`to_outline`), so a long answer is cut, never
    thrown away."""

    model_config = ConfigDict(extra="forbid")
    answers: str
    summary: list[str]
    opening: list[str]
    beats: list[_Beat]
    landing: list[str]
    thin: bool
    missing: list[str]


INSTRUCTIONS = """You are the editor of one audio episode. From the two packets below - what is happening now (`<evidence>`, numbered P), and what came before from FAM's archive (`<archive>`, numbered A) - choose what the writer needs and leave out what it does not, by sentence number only: write numbers like "P2.3", never the sentences themselves. Then outline the episode: what it answers (a few words), the beats in order, and the numbers that carry each beat. The archive is history - what was true on its date - never how things stand now. Where two sources disagree, the better grade wins, then the more recent; put only the winning sentence in the outline.

Plan it the way a good journalist tells a story: open on something concrete that says who, what and when and opens a question; let each beat follow from the last because, therefore or but; end on the most concrete thing you have, never a question, a tease or what might happen next. If the question asks for a result and no sentence reports one as finished, plan around what has not finished. Do not plan a part you have nothing for: a beat needs at least one number behind it. Plan no advice: on a medical, legal or money question, plan the facts. Fit the beats to the episode's length. A beat's purpose says what the beat does ("why the vote mattered"), never a fact. Anything the listener asked about that no sentence covers goes in `missing`, a few words each, for the writer only; set `thin` when the packets hold too little for the length.

The worked episodes in `<teaching>` show the house style the writer is held to: concrete openings, a story told because-therefore-but, a concrete last line. They are examples of writing, never evidence."""


def _examples() -> str:
    """`examples/` as he reads them, once per process: the same files the
    writer is shown, so the outline aims at the writing FAM wants."""
    global _EXAMPLES
    if _EXAMPLES is None:
        try:
            texts = []
            for path in sorted((ROOT / "examples").glob("*.txt")):
                texts.append(f"--- {path.stem} ---\n{path.read_text(encoding='utf-8').strip()}")
            _EXAMPLES = "\n\n".join(texts)
        except Exception:  # noqa: BLE001
            _EXAMPLES = ""
    return _EXAMPLES


_EXAMPLES: Optional[str] = None


# ------------------------------------------------------------------ checked in code
_NOW = re.compile(r"\b(now|today|current|currently|latest|this week|tonight|as it stands)\b",
                  re.I)


def _resolve(ref: str, index: dict) -> Optional[SentenceRef]:
    ref = (ref or "").strip().strip("[]")
    if not re.match(_REF, ref):
        return None
    prefix, sentence = ref.split(".")
    found = index.get(prefix)
    if found is None:
        return None
    source_i, source = found
    n = int(sentence)
    if not 1 <= n <= len(source.sentences):
        return None
    return SentenceRef(packet=source.packet, source=source_i, sentence=n - 1)


def to_outline(answer: EditorAnswer, sources: list) -> tuple:
    """`(Outline, report)`: every number checked, the bad ones dropped and
    counted; a beat with nothing behind it dropped; a "now" beat resting only
    on the archive dropped. Code, never a model."""
    index = {s.prefix: (i, s) for i, s in enumerate(sources)}
    report = collections.Counter()

    def refs(items):
        out = []
        for ref in items or []:
            resolved = _resolve(ref, index)
            if resolved is None:
                report["bad_number"] += 1
            elif resolved not in out:
                out.append(resolved)
        return tuple(out)

    beats = []
    for beat in answer.beats[:MAX_BEATS]:
        held = refs(beat.sentences[:8])
        if not held:
            report["empty_beat"] += 1
            continue
        if _NOW.search(beat.purpose) and all(r.packet == "archive" for r in held):
            report["now_on_archive"] += 1
            continue
        beats.append(Beat(purpose=" ".join(beat.purpose.split())[:120], refs=held))
    outline = Outline(summary=refs(answer.summary[:24]), beats=tuple(beats),
                      opening=refs(answer.opening[:3]), landing=refs(answer.landing[:3]),
                      thin=bool(answer.thin),
                      missing=tuple(" ".join(m.split())[:120] for m in answer.missing[:6]))
    return outline, dict(report)


def _text(ref: SentenceRef, sources: list) -> str:
    source = sources[ref.source]
    return f"{source.sentences[ref.sentence]} (published {source.published})" \
        if source.packet == "archive" else source.sentences[ref.sentence]


def writer_block(outline: Outline, answers: str, sources: list) -> str:
    """The editor's plan as the writer reads it, every line of evidence the
    sources' own sentence. "" when there is nothing to plan from."""
    if not outline.beats:
        return ""
    lines = ["", "An editor has read the sources above and planned this episode. "
             "Follow the plan: it says what to open on, what each part does and in "
             "what order, and what to land on. The sentences under each part are "
             "the sources' own - tell them in your own words, as the story, never "
             "one after another. A part marked history is what was true on its "
             "date, never how things stand now.", "<plan>"]
    if answers.strip():
        lines.append(f"What it answers: {' '.join(answers.split())[:160]}")
    if outline.opening:
        lines.append("Open on:")
        lines += [f"- {_text(r, sources)}" for r in outline.opening]
    for i, beat in enumerate(outline.beats, 1):
        history = all(r.packet == "archive" for r in beat.refs)
        lines.append(f"Part {i}{' (history)' if history else ''}: {beat.purpose}")
        lines += [f"- {_text(r, sources)}" for r in beat.refs]
    if outline.landing:
        lines.append("Land on:")
        lines += [f"- {_text(r, sources)}" for r in outline.landing]
    if outline.missing:
        lines.append("Not in the sources (for you only - never announce it; the "
                     "rules above say what may be said without them): "
                     + "; ".join(outline.missing))
    lines.append("</plan>")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ the call
async def outline(plan, notes=None, *, ahead: bool = True, client=None,
                  model_setting: str = "") -> str:
    """The writer's plan block for this episode, or "" - the writer then
    reads the packets as it does today. One call, bounded; never raises.

    `model_setting` lets the comparison (item 37) run him on Haiku or Sonnet
    through the same layer; empty is `EDITOR_MODEL` ahead of the tap."""
    try:
        from agents import models, netenyahu
        from config import settings

        sources = number_packets(plan)
        if not sources:
            _count("no_packets")
            return ""
        plugin, archive = packets_text(sources)
        material = [("request", str(getattr(plan, "query", "") or "")),
                    ("evidence", f"Episode length: about {getattr(plan, 'minutes', 0):g} "
                                 f"minutes, at most {getattr(plan, 'max_words', 0)} words.\n\n"
                                 + (plugin or "(nothing)")),
                    ("archive", archive or "(nothing)"),
                    ("teaching", _examples())]
        request = models.AgentRequest(
            agent="editor", task="outline", switch_setting="editor_agent",
            model_setting=model_setting or ("editor_model" if ahead else "editor_search_model"),
            rule_book="shakespeare.md", schema=EditorAnswer, instructions=INSTRUCTIONS,
            material=tuple(material), carries_listener_words=True,
            timeout=float(settings.editor_ahead_timeout_seconds if ahead
                          else settings.editor_timeout_seconds),
            max_tokens=int(settings.editor_max_tokens),
            effort=str(settings.editor_effort or "high") if ahead else "low",
            surface="ahead" if ahead else "search")
        result = await models.ask(request, notes=notes, client=client)
        netenyahu.report("editor", result.status)
        _count(f"call_{result.status}")
        if not result.ok:
            return ""
        built, report = to_outline(result.value, sources)
        for kind, n in report.items():
            _count(kind, n)
        block = writer_block(built, result.value.answers, sources)
        if notes is not None:
            notes.editor = {"beats": len(built.beats), "thin": built.thin,
                            "missing": list(built.missing), "dropped": report,
                            "model": getattr(result, "model", "")}
        _count("planned" if block else "nothing_to_plan")
        return block
    except Exception:  # noqa: BLE001 - the editor never costs an episode
        log.warning("Shakespeare's outline failed; the writer reads the packets",
                    exc_info=True)
        _count("failed")
        return ""


def report() -> dict:
    with _LOCK:
        return {"since_boot": dict(SINCE_BOOT)}


def reset() -> None:
    with _LOCK:
        SINCE_BOOT.clear()
