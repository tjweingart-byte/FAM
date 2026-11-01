"""Worked examples for Nando: questions with the node each is truly about
(§380, training piece N2).

The owner wants Nando - the model - to decide (§379), and asked for
training that keeps going as the tree grows. The first lever is the one
FAM's writer already uses (`examples/`, [examples-lever]): show the model a
handful of hard cases with their right answers, chosen for the request in
front of it, so it learns "Detroit lions at the zoo" is not the NFL team
from an example rather than from a rule.

Two sources, read together:

* **`category_examples.json`**, beside the category seed and reviewed like
  it (the owner's decision, §374): the questions three reviews used to
  break Nando, each labelled with its node - or "" for none of the tree
  ("kings and queens of England"). Kept out of `tools/nando_eval.py`'s
  measured sets, so the examples cannot flatter the measurement.
* **Learned examples** from Nando's training store (the answer key and his
  corrected mistakes, pieces N3 and N5), added through `add_source` - a
  function returning rows, read in the background, never on a request.

`pick(query, candidates)` chooses up to `PER_REQUEST` examples: those whose
node is a candidate or on a candidate's branch first, then those sharing
the most content words with the question, then a "none of the tree"
example or two that share words with it, so the model sees that answer is
allowed. Memory only: the file is read once, learned rows are refreshed by
a background thread.
"""
from __future__ import annotations

import json
import logging
import pathlib
import re
import threading
import time
from typing import Callable

log = logging.getLogger("fam.categories")

PATH = pathlib.Path(__file__).resolve().parent / "category_examples.json"
PER_REQUEST = 12
#: How many "none of the tree" examples a request is shown at most.
NONE_PER_REQUEST = 2
#: How often learned examples are re-read, in the background.
REFRESH_SECONDS = 300.0

_STOP = frozenset("a an the of in on at to for and or is are was were be what how why "
                  "who did does do with from by as it its this that their his her new "
                  "my me i you we they".split())

_LOCK = threading.Lock()
_STATE: dict = {"file": None, "learned": [], "at": 0.0, "running": False}
_SOURCES: list = []


def _words(text: str) -> frozenset:
    return frozenset(w for w in re.findall(r"[a-z0-9']+", (text or "").lower())
                     if w not in _STOP and len(w) > 2)


def load(path: pathlib.Path | None = None) -> list:
    """The reviewed examples, as `[{"question", "node"}]`. Never raises."""
    try:
        data = json.loads((path or PATH).read_text(encoding="utf-8"))
        return [e for e in data.get("examples", ()) if isinstance(e, dict)
                and isinstance(e.get("question"), str) and isinstance(e.get("node", ""), str)]
    except FileNotFoundError:
        return []
    except Exception as exc:  # noqa: BLE001
        log.warning("category examples unreadable (%s); using none", exc)
        return []


def add_source(source: Callable[[], list]) -> None:
    """Register a function returning learned examples (`[{"question",
    "node"}]`), read in the background (N3, N5)."""
    if source not in _SOURCES:
        _SOURCES.append(source)


def reset() -> None:
    with _LOCK:
        _STATE.update(file=None, learned=[], at=0.0, running=False)


def _refresh() -> None:
    rows: list = []
    for source in list(_SOURCES):
        try:
            rows += [r for r in (source() or ()) if r.get("question")]
        except Exception:  # noqa: BLE001 - a source never costs a request
            log.exception("a learned-examples source failed")
    with _LOCK:
        _STATE.update(learned=rows, at=time.monotonic(), running=False)


def examples() -> list:
    """Every example now: the file's (read once) and the learned ones (a
    background copy, refreshed at most every `REFRESH_SECONDS`)."""
    with _LOCK:
        if _STATE["file"] is None:
            _STATE["file"] = load()
        stale = time.monotonic() - _STATE["at"] >= REFRESH_SECONDS
        if _SOURCES and stale and not _STATE["running"]:
            _STATE["running"] = True
            _STATE["at"] = time.monotonic()
            threading.Thread(target=_refresh, name="category-examples", daemon=True).start()
        return list(_STATE["file"]) + list(_STATE["learned"])


def pick(query: str, candidates=(), per_request: int = PER_REQUEST) -> list:
    """The examples most worth showing for this request. Never raises."""
    try:
        from agents import categorizer

        cands = set(candidates or ())
        branch = set()
        for node in cands:
            branch.update(categorizer.path_for(node))
        asked = _words(query)
        scored = []
        for i, ex in enumerate(examples()):
            if " ".join(ex["question"].lower().split()) == " ".join((query or "").lower().split()):
                continue                 # never the request's own answer
            node = ex.get("node", "")
            overlap = len(asked & _words(ex["question"]))
            on_list = 2 if node in cands else (1 if node and node in branch else 0)
            if not on_list and not overlap:
                continue
            # His own corrected mistakes first among equals (§383): they
            # are exactly where he goes wrong.
            mistake = 1 if ex.get("source") == "mistake" else 0
            scored.append((-(on_list * 2 + overlap + mistake), i, ex))
        scored.sort(key=lambda t: (t[0], t[1]))
        out, nones = [], 0
        for _s, _i, ex in scored:
            if not ex.get("node"):
                if nones >= NONE_PER_REQUEST:
                    continue
                nones += 1
            out.append(ex)
            if len(out) >= per_request:
                break
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("could not pick category examples: %s", exc)
        return []


def block(query: str, candidates=()) -> str:
    """The examples as the model reads them, or "" for none."""
    chosen = pick(query, candidates)
    if not chosen:
        return ""
    lines = ["Worked examples (question -> node; \"none\" means no node of the tree "
             "fits):"]
    for ex in chosen:
        lines.append(f"{ex['question']} -> {ex.get('node') or 'none'}")
    return "\n".join(lines)
