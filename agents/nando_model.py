"""Nando's small trained model (§385, training piece N6).

The answer key (§381) grades episodes every night; his memory (§382) and
his mistakes (§383) show those grades to the model he asks. This is the
third use of them: a classifier of his own, trained on every grade and
reviewed example, that reads a request in a fraction of a millisecond with
no call at all.

**What it is.** Multinomial logistic regression (softmax) over hashed
features of the request's words - each word, each pair of words, and each
word's character trigrams, signed-hashed into `DIMS` slots - trained with
numpy (already a dependency) nightly at 04:00 Eastern by Netenyahu
(`categorizer:train`), after the answer key's run. Classes are the nodes
with at least `MIN_PER_CLASS` training rows, plus "none".

**How it is used.**

* Always, when a model exists: its reading and how sure it is reach the
  model Nando asks, as teaching (`categorizer.teaching`), and a reading at
  `CANDIDATE_P` or more joins his list.
* **Filing on its own** only when it is *trusted* at that confidence: at
  training, a fifth of the rows is held out, and the model is trusted at
  probability `SURE` only if, on the held-out rows it was that sure about,
  at least `TRUSTED_PRECISION` were right, over at least `MIN_HELD_OUT`.
  Even then it files only a node already on Nando's shortlist, and only
  when nothing solid decided - it never overrides a tap's own filing, a
  provider, the brief's category words or a model's pick. The owner's
  answer was that a missed guess is worse than a miss (§379): this is a
  guess only when the numbers say it is almost never wrong.

**Availability.** No model yet, a model that will not load, a stale one -
each is "no reading", the answer before this file. The weights live in the
agents' store (`nando_model`, one row); a background thread loads them, at
most every `REFRESH_SECONDS`. A wipe forgets them, as everything learnt
from the log.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import threading
import time
from typing import Optional

import numpy as np

log = logging.getLogger("fam.agents")

DIMS = 2048
MIN_PER_CLASS = 3
#: The fewest training rows worth training on.
MIN_ROWS = 40
#: A reading worth putting on the list, and worth trusting to file alone.
CANDIDATE_P = 0.5
SURE = 0.9
TRUSTED_PRECISION = 0.95
MIN_HELD_OUT = 30
REFRESH_SECONDS = 300.0
#: Training: full-batch gradient descent with momentum and L2.
ITERATIONS = 200
LEARNING_RATE = 0.5
L2 = 1e-4

_WORD = re.compile(r"[a-z0-9']+")
_LOCK = threading.Lock()
_STATE: dict = {"model": None, "at": -1e18, "running": False, "generation": 0}


# ------------------------------------------------------------------ features
def _slot(feature: str) -> tuple:
    value = int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(),
                           "big")
    return value % DIMS, (1.0 if (value >> 63) & 1 else -1.0)


def features(text: str) -> np.ndarray:
    """A unit vector of signed-hashed words, word pairs and trigrams."""
    vec = np.zeros(DIMS, dtype=np.float32)
    words = _WORD.findall((text or "").lower())
    for i, word in enumerate(words):
        slot, sign = _slot("w:" + word)
        vec[slot] += sign
        if i:
            slot, sign = _slot("b:" + words[i - 1] + " " + word)
            vec[slot] += sign
        padded = "^" + word + "$"
        for j in range(max(1, len(padded) - 2)):
            slot, sign = _slot("g:" + padded[j:j + 3])
            vec[slot] += 0.2 * sign
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm else vec


# ------------------------------------------------------------------ the model
class Model:
    def __init__(self, classes: list, weights: np.ndarray, bias: np.ndarray,
                 trusted_at: Optional[float], stats: dict) -> None:
        self.classes = list(classes)
        self.weights = weights
        self.bias = bias
        self.trusted_at = trusted_at
        self.stats = dict(stats)

    def probabilities(self, text: str) -> np.ndarray:
        return _softmax(features(text)[None, :] @ self.weights + self.bias)[0]

    def read(self, text: str) -> tuple:
        """`(node, p)`: its likeliest node ("" for none) and how sure."""
        p = self.probabilities(text)
        i = int(np.argmax(p))
        return self.classes[i], float(p[i])

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        np.savez_compressed(buf, weights=self.weights, bias=self.bias,
                            classes=np.array(self.classes, dtype=object),
                            trusted_at=np.array(-1.0 if self.trusted_at is None
                                                else self.trusted_at))
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, blob: bytes, stats: dict) -> "Model":
        data = np.load(io.BytesIO(blob), allow_pickle=True)
        trusted = float(data["trusted_at"])
        return cls(list(data["classes"]), data["weights"], data["bias"],
                   None if trusted < 0 else trusted, stats)


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def _fit(x: np.ndarray, y: np.ndarray, k: int) -> tuple:
    n = x.shape[0]
    w = np.zeros((x.shape[1], k), dtype=np.float32)
    b = np.zeros(k, dtype=np.float32)
    onehot = np.eye(k, dtype=np.float32)[y]
    vw, vb = np.zeros_like(w), np.zeros_like(b)
    for _ in range(ITERATIONS):
        grad = (_softmax(x @ w + b) - onehot) / n
        gw = x.T @ grad + L2 * w
        gb = grad.sum(axis=0)
        vw = 0.9 * vw - LEARNING_RATE * gw
        vb = 0.9 * vb - LEARNING_RATE * gb
        w += vw
        b += vb
    return w, b


def _held_out(question: str) -> bool:
    """A fixed fifth of questions, by hash: the same split every night."""
    return hashlib.blake2b(question.lower().encode("utf-8"), digest_size=2).digest()[0] < 51


def train(rows: list) -> Optional[Model]:
    """A model from `[{"question", "node"}]`, or None when there is too
    little to learn from. Measures itself on a held-out fifth first, then
    trains on everything."""
    counts: dict = {}
    for r in rows:
        counts[r["node"]] = counts.get(r["node"], 0) + 1
    usable = [r for r in rows if counts[r["node"]] >= MIN_PER_CLASS]
    classes = sorted({r["node"] for r in usable})
    if len(usable) < MIN_ROWS or len(classes) < 2:
        return None
    index = {c: i for i, c in enumerate(classes)}
    x = np.stack([features(r["question"]) for r in usable])
    y = np.array([index[r["node"]] for r in usable])
    held = np.array([_held_out(r["question"]) for r in usable])
    trusted_at, stats = None, {"rows": len(usable), "classes": len(classes),
                               "held_out": int(held.sum())}
    if held.sum() and (~held).sum():
        w, b = _fit(x[~held], y[~held], len(classes))
        p = _softmax(x[held] @ w + b)
        pick, conf = p.argmax(axis=1), p.max(axis=1)
        right = pick == y[held]
        stats["held_out_accuracy"] = round(float(right.mean()), 3)
        sure = conf >= SURE
        stats["held_out_sure"] = int(sure.sum())
        if sure.sum():
            precision = float(right[sure].mean())
            stats["held_out_precision_when_sure"] = round(precision, 3)
            if sure.sum() >= MIN_HELD_OUT and precision >= TRUSTED_PRECISION:
                trusted_at = SURE
    w, b = _fit(x, y, len(classes))
    return Model(classes, w, b, trusted_at, stats)


# ------------------------------------------------------------------ the store
def _table(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS nando_model (
        id INTEGER PRIMARY KEY, blob BLOB NOT NULL, stats TEXT NOT NULL,
        trained_at REAL NOT NULL)""")


def save(model: Model) -> None:
    import json

    from agents import training

    store = training.store()
    with store._lock, store._connect() as db:
        _table(db)
        db.execute("DELETE FROM nando_model")
        db.execute("INSERT INTO nando_model (id, blob, stats, trained_at) VALUES (1, ?, ?, ?)",
                   (model.to_bytes(), json.dumps(model.stats), time.time()))


def load() -> Optional[Model]:
    import json

    from agents import training

    if not training.exists():
        return None
    store = training.store()
    with store._connect() as db:
        _table(db)
        row = db.execute("SELECT blob, stats, trained_at FROM nando_model WHERE id = 1"
                         ).fetchone()
    if row is None:
        return None
    stats = dict(json.loads(row[1]), trained_at=row[2])
    return Model.from_bytes(bytes(row[0]), stats)


def forget() -> int:
    """A wipe: the model was learnt from the grades the wipe empties."""
    from agents import training

    store = training.store()
    with store._lock, store._connect() as db:
        _table(db)
        n = db.execute("SELECT COUNT(*) FROM nando_model").fetchone()[0]
        db.execute("DELETE FROM nando_model")
    reset()
    return int(n)


def reset() -> None:
    with _LOCK:
        _STATE.update(model=None, at=-1e18, running=False,
                      generation=_STATE["generation"] + 1)


def _reload(generation: int) -> None:
    # A load that finishes after a reset (a wipe, a new model) is dropped.
    try:
        model = load()
        with _LOCK:
            if _STATE["generation"] == generation:
                _STATE["model"] = model
    except Exception:  # noqa: BLE001
        log.exception("nando's model could not be loaded")
    finally:
        with _LOCK:
            if _STATE["generation"] == generation:
                _STATE["running"] = False


def current(background: bool = True) -> Optional[Model]:
    """The model now - a copy in memory, reloaded in the background at most
    every `REFRESH_SECONDS`. Never a read on the caller's thread unless
    `background=False`."""
    with _LOCK:
        start = (time.monotonic() - _STATE["at"] >= REFRESH_SECONDS
                 and not _STATE["running"])
        if start:
            _STATE.update(running=True, at=time.monotonic())
        generation = _STATE["generation"]
    if start:
        if background:
            threading.Thread(target=_reload, args=(generation,), name="nando-model",
                             daemon=True).start()
        else:
            _reload(generation)
    with _LOCK:
        return _STATE["model"]


def read(text: str) -> tuple:
    """`(node, p, trusted)` for a request, or `("", 0.0, False)` with no
    model. `trusted` is True only when the model is trusted at `p`. A node
    the tree has lost is no reading. Never raises."""
    try:
        import topics

        model = current()
        if model is None or not (text or "").strip():
            return "", 0.0, False
        node, p = model.read(text)
        if node and node not in topics.FACETS and topics.category_tree().get(node) is None:
            return "", 0.0, False
        return node, p, model.trusted_at is not None and p >= model.trusted_at
    except Exception as exc:  # noqa: BLE001
        log.warning("nando's model could not read %r: %s", text, exc)
        return "", 0.0, False


# ------------------------------------------------------------------ nightly
def training_rows() -> list:
    """Everything worth learning from: the key's grades it was sure of and
    the reviewed examples (his memory's rows, §382)."""
    from agents import memory

    return [{"question": r["question"], "node": r["node"]} for r in memory.rows()]


def retrain() -> dict:
    """Train on everything and save it. Returns what it did. Never raises."""
    try:
        rows = training_rows()
        model = train(rows)
        if model is None:
            return {"trained": False, "rows": len(rows), "why": "too little to learn from"}
        save(model)
        reset()
        return {"trained": True, **model.stats,
                "trusted_at": model.trusted_at}
    except Exception as exc:  # noqa: BLE001
        log.exception("nando's model could not be trained")
        return {"trained": False, "error": f"{type(exc).__name__}: {exc}"}


def handler():
    """The background task `categorizer:train`."""
    import asyncio

    async def run(_task: str) -> None:
        result = await asyncio.to_thread(retrain)
        log.info("nando's model: %s", result)
    return run


def describe() -> dict:
    model = _STATE["model"]
    return dict(model.stats, trusted_at=model.trusted_at) if model else {}
