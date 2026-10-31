"""Nando's memory of graded answers (§378, training piece N4).

The answer key (§377) grades written episodes every night. Each grade is a
question with the node it was truly about - exactly what Nando needs when a
new request looks like an old one: "Detroit lions at the zoo" was graded
`animals` last week, so the next "lions at the Detroit zoo" is shown that
answer. Worked examples (§376) are chosen by shared words; memory is chosen
by **meaning**, with the same local sentence model `category_near` and the
ranker use (`taste_vectors`), and it grows on its own as the key grades.

What is remembered: the answer key's grades it was sure of (`high`,
`medium`) and the reviewed examples (`category_examples.json`); a grade of
"none" is remembered too, so the model learns that answer exists. A node the
tree has since lost is dropped when recalled (N7 re-files grades when the
tree moves).

**A layer that adds quality must not subtract availability.** No model
installed, a model that throws, vectors still being built - each is an
empty memory, exactly the answer before this file. The matrix is built on a
background thread, at most every `REFRESH_SECONDS`; nothing waits for it.
`recall(..., inline=0)` embeds nothing on the caller's thread: a query not
yet embedded is queued and remembered next time. Only work that is never in
front of a listener (Nando's own call, beside the brief; anything ahead of
the tap) passes `inline=1`.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import numpy as np

log = logging.getLogger("fam.agents")

#: How many remembered questions a request is shown at most.
PER_REQUEST = 5
#: How alike a remembered question must be to be shown (cosine, same
#: local model as `category_near.NEAR_COSINE`, which measured 0.45 as the
#: line between right and wrong nodes; questions to questions sit higher).
MEMORY_COSINE = 0.55
#: How often the matrix is rebuilt from the store, in the background.
REFRESH_SECONDS = 300.0
#: The newest grades remembered at most.
MAX_ROWS = 5000
#: The key's confidences worth remembering.
REMEMBERED = ("high", "medium")

_LOCK = threading.Lock()
#: (questions, nodes, sources, unit-vector matrix) or None before the first build.
_STATE: dict = {"built": None, "at": -1e18, "running": False}


def reset() -> None:
    with _LOCK:
        _STATE.update(built=None, at=-1e18, running=False)


def _encoder():
    try:
        import taste_vectors

        return taste_vectors._encoder()
    except Exception:  # noqa: BLE001 - availability first
        return None


def rows() -> list:
    """What is remembered now, newest grade first: `[{"question", "node",
    "source"}]`. Reads the store - background only."""
    import category_examples

    from agents import training

    out, seen = [], set()
    if training.exists():
        for label in training.store().labels(limit=MAX_ROWS):
            q = " ".join((label["query"] or "").split())
            if label["confidence"] in REMEMBERED and q and q.lower() not in seen:
                seen.add(q.lower())
                out.append({"question": q, "node": label["node"], "source": "graded"})
    for ex in category_examples.load():
        q = " ".join(ex["question"].split())
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append({"question": q, "node": ex.get("node", ""), "source": "example"})
    return out[:MAX_ROWS]


def build() -> None:
    """Embed what is remembered and swap the matrix in. Never raises."""
    try:
        encoder = _encoder()
        remembered = rows() if encoder is not None else []
        if remembered:
            matrix = np.asarray(encoder([r["question"] for r in remembered]),
                                dtype=np.float32)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            matrix = matrix / np.where(norms == 0, 1.0, norms)
        else:
            matrix = np.zeros((0, 1), dtype=np.float32)
        with _LOCK:
            _STATE["built"] = (remembered, matrix)
    except Exception:  # noqa: BLE001
        log.exception("nando's memory could not be built")
    finally:
        with _LOCK:
            _STATE["running"] = False


def _built() -> Optional[tuple]:
    """The matrix now, starting a rebuild in the background when stale."""
    import taste_vectors

    with _LOCK:
        stale = time.monotonic() - _STATE["at"] >= REFRESH_SECONDS
        start = stale and not _STATE["running"]
        if start:
            _STATE.update(running=True, at=time.monotonic())
    if start:
        if getattr(taste_vectors, "_ENCODER_OVERRIDE", None) is not None:
            build()                    # a test encoder: settle now
        else:
            threading.Thread(target=build, name="nando-memory", daemon=True).start()
    with _LOCK:
        return _STATE["built"]


def warm() -> None:
    """Start the first build (boot, with `AGENTS=1`)."""
    _built()


def recall(query: str, k: int = PER_REQUEST, inline: int = 0) -> list:
    """The remembered questions most like `query`, most alike first:
    `[{"question", "node", "source", "score"}]`, each at `MEMORY_COSINE` or
    more, on a node the tree still has (or "none"). Never raises."""
    query = " ".join((query or "").split())
    if not query:
        return []
    try:
        import taste_vectors
        import topics

        built = _built()
        if not built or not built[0]:
            return []
        remembered, matrix = built
        found = taste_vectors.vectors([query], inline=inline).get(query)
        if not found:
            return []
        vec = np.asarray(found, dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if norm == 0.0 or vec.shape[0] != matrix.shape[1]:
            return []
        scores = matrix @ (vec / norm)
        tree = topics.category_tree()
        out = []
        for i in np.argsort(-scores):
            score = float(scores[i])
            if score < MEMORY_COSINE or len(out) >= k:
                break
            row = remembered[int(i)]
            node = row["node"]
            if node and node not in topics.FACETS and tree.get(node) is None:
                continue               # the tree has moved on (N7)
            out.append(dict(row, score=round(score, 3)))
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("nando's memory could not recall %r: %s", query, exc)
        return []


def describe() -> dict:
    """For the report: how much is remembered and whether it reads meaning."""
    built = _STATE["built"]
    return {"semantic": _encoder() is not None,
            "remembered": len(built[0]) if built else None}
