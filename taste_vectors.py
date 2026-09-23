"""What a listener has asked for, compared with what a tile is about - by
meaning rather than by tag.

**Why this exists.** `topics._affinity` scores a tile by the tags it shares
with a listener's profile, and the tags are a vocabulary: eight facets,
twenty-nine subtags and whatever `categories.py` has grown. Everything a
vocabulary cannot say, that score cannot see. Somebody who has searched
"why did the fed hold rates" and "mortgage rates this week" has a profile that
says `money` - and so does every other money tile in the inventory, equally.
`BROAD_MATCH_PENALTY` and `familiar_words` are both workarounds for that gap,
and the second one gives up and reads raw words.

A sentence embedding closes it without a vocabulary: it puts "fed holds rates"
next to "what the housing market is doing" because they are about related
things, not because anybody wrote a keyword list saying so.

**What it is**, in one line: for each tile, the closest thing in this
listener's recent positive history, by cosine, discounted by how weak and old
that piece of history is - then turned into a bounded *additive* term in
`rank_from_history`. Additive rather than multiplicative on purpose: a
multiplier on `_affinity` can only reorder what the tags already found, and
the whole point is to find what the tags missed.

**Max, not mean.** A listener's history is several interests, and a mean of
"golf" and "AI chips" is a vector about neither. The nearest single item
answers the question actually being asked - *is this tile near anything they
have wanted* - and weighting it by that item's strength keeps one stray search
from a month ago from carrying a tile.

**Positive history only.** A skip already reaches the ranking through `taste`;
"a tile that is near something they skipped" is a much weaker claim than
"they skipped this subject", and a negative term driven by a nearest
neighbour would bury adjacent subjects for one bad episode.

Four rules hold it up, each one a rule this codebase already keeps somewhere:

* **A layer that adds quality must not subtract availability.** No model
  installed, no onnxruntime, a model that throws - every one of those is `{}`,
  which is exactly the feed that shipped before this file existed. A test
  pins that the ranking is identical.
* **Nothing on the browse path waits.** Embedding is ~10 ms a sentence, so a
  cold process could spend a second on one page. The page embeds at most
  `MAX_INLINE` new texts itself and hands the rest to a background thread;
  a tile that is not embedded yet simply has no semantic term this time.
  The bank and the startup set are warmed at boot.
* **An impression never becomes taste.** Only behaviour (`EVENT_WEIGHT > 0`)
  is read; what the feed showed is never a history item.
* **Say what is in force.** `describe()` is on `/api/health`.

`SEMANTIC_TASTE=0` turns it off; there is no other setting.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import OrderedDict
from typing import Callable, Iterable, Optional

import numpy as np

log = logging.getLogger(__name__)

#: How much the best semantic match can add to a tile's score. `_affinity`
#: for a tile squarely on a listener's strongest facet is around 1-2 and
#: `RELEVANCE_FLOOR` is 0.12, so at full strength this is enough to carry a
#: tile the tags scored at nothing over the floor - which is the point - and
#: not enough to outrank a strong tag match on its own.
SEMANTIC_WEIGHT = 0.6

#: Cosine below which a match is worth nothing. all-MiniLM-L6-v2 puts
#: unrelated short questions at roughly 0.0-0.2 and related ones at 0.45 and
#: up (measured in §128: "who won the game last night" against "the result of
#: yesterday's match" is 0.58, against "how do vaccines work" 0.05). The
#: floor sits above the unrelated band so noise adds exactly zero.
SEMANTIC_FLOOR = 0.3

#: History items read, newest first. Enough to hold several interests; few
#: enough that a month-old search cannot outvote this week.
MAX_HISTORY = 48

#: New texts one page may embed itself before handing the rest to the
#: background thread. ~10 ms each on one core.
MAX_INLINE = 6

#: In-process vector cache. The bank and the startup set are ~40 texts, a live
#: pool a few dozen more, and each listener adds a handful - so this holds a
#: busy deployment's whole working set.
MAX_CACHED = 5000

_VECTORS: "OrderedDict[str, list[float]]" = OrderedDict()
_LOCK = threading.Lock()
_PENDING: set[str] = set()
_WORKER: Optional[threading.Thread] = None

#: A test's encoder: `list[str] -> list[list[float]]`, unit vectors. When set,
#: it replaces the installed model, and background embedding is done inline so
#: a test sees one deterministic answer.
_ENCODER_OVERRIDE: Optional[Callable[[list[str]], list[list[float]]]] = None


def set_encoder(encoder: Optional[Callable[[list[str]], list[list[float]]]]) -> None:
    """Install a test encoder (or remove it with None). Clears the cache."""
    global _ENCODER_OVERRIDE
    _ENCODER_OVERRIDE = encoder
    reset()


def reset() -> None:
    with _LOCK:
        _VECTORS.clear()
        _PENDING.clear()


def _switched_off() -> bool:
    return os.environ.get("SEMANTIC_TASTE", "1").strip().lower() in ("0", "false", "no", "off")


def _encoder() -> Optional[Callable[[list[str]], list[list[float]]]]:
    if _switched_off():
        return None
    if _ENCODER_OVERRIDE is not None:
        return _ENCODER_OVERRIDE
    try:
        import embeddings

        model = embeddings.semantic_encoder()
    except Exception:  # noqa: BLE001 - availability first, see the docstring
        log.exception("semantic encoder unavailable")
        return None
    return model.encode_many if model is not None else None


def enabled() -> bool:
    return _encoder() is not None


def describe() -> dict:
    """For `/api/health`: is the ranker reading meaning, and if not, why not."""
    if _switched_off():
        return {"enabled": False, "reason": "SEMANTIC_TASTE=0"}
    if _ENCODER_OVERRIDE is not None:
        return {"enabled": True, "model": "test encoder", "cached": len(_VECTORS)}
    try:
        import embeddings

        if not embeddings.model_installed():
            return {"enabled": False,
                    "reason": "no model in " + str(embeddings.model_dir())
                              + " - python tools/install_embed_model.py"}
        model = embeddings.semantic_encoder()
        if model is None:
            return {"enabled": False,
                    "reason": embeddings.describe().get("error") or "model did not load"}
        return {"enabled": True, "model": str(embeddings.model_dir()),
                "dims": model.dims,
                "cached": len(_VECTORS), "weight": SEMANTIC_WEIGHT,
                "floor": SEMANTIC_FLOOR}
    except Exception as exc:  # noqa: BLE001
        return {"enabled": False, "reason": type(exc).__name__ + ": " + str(exc)}


def tile_text(topic) -> str:
    """What a tile is about, as one sentence. Title and query together: the
    title is how it reads and the query is what the episode will answer."""
    title = (getattr(topic, "title", "") or "").strip()
    query = (getattr(topic, "query", "") or "").strip()
    if query and query.lower() != title.lower():
        return f"{title}. {query}" if title else query
    return title or query


def _store(texts: list[str], vectors: list[list[float]]) -> None:
    with _LOCK:
        for text, vec in zip(texts, vectors):
            _VECTORS[text] = vec
            _VECTORS.move_to_end(text)
            _PENDING.discard(text)
        while len(_VECTORS) > MAX_CACHED:
            _VECTORS.popitem(last=False)


def _encode(encoder, texts: list[str]) -> None:
    try:
        _store(texts, encoder(texts))
    except Exception:  # noqa: BLE001
        log.exception("semantic embedding failed for %d texts", len(texts))
    finally:
        # The whole batch leaves the queue whatever happened - an encoder that
        # threw, or returned fewer vectors than texts, must cost those texts a
        # term, never keep the worker re-encoding them forever.
        with _LOCK:
            _PENDING.difference_update(texts)


def _drain() -> None:
    global _WORKER
    while True:
        with _LOCK:
            batch = list(_PENDING)[:16]
            if not batch:
                _WORKER = None
                return
        encoder = _encoder()
        if encoder is None:
            with _LOCK:
                _PENDING.clear()
                _WORKER = None
            return
        _encode(encoder, batch)


def _later(texts: list[str]) -> None:
    global _WORKER
    with _LOCK:
        _PENDING.update(texts)
        if _WORKER is not None:
            return
        _WORKER = threading.Thread(target=_drain, name="taste-vectors", daemon=True)
        _WORKER.start()


def vectors(texts: Iterable[str], inline: int = MAX_INLINE) -> dict[str, list[float]]:
    """Vectors for whichever of `texts` are available now.

    Up to `inline` missing ones are embedded here; the rest are queued for the
    background thread and are absent from the answer. With a test encoder
    everything is embedded here, so a test sees the settled answer.
    """
    encoder = _encoder()
    if encoder is None:
        return {}
    wanted = list(dict.fromkeys(t for t in texts if t))
    with _LOCK:
        missing = [t for t in wanted if t not in _VECTORS]
    if missing:
        if _ENCODER_OVERRIDE is not None:
            _encode(encoder, missing)
        else:
            now_part, later_part = missing[:inline], missing[inline:]
            if now_part:
                _encode(encoder, now_part)
            if later_part:
                _later(later_part)
    with _LOCK:
        found = {}
        for t in wanted:
            if t in _VECTORS:
                # Least recently *used*, not first in: without this the bank
                # vectors warmed at boot are the first evicted once 5000
                # listener texts have passed, and re-embedded six a page.
                _VECTORS.move_to_end(t)
                found[t] = _VECTORS[t]
        return found


def warm(topics: Iterable) -> None:
    """Queue tiles for embedding in the background. Called at boot for the
    bank and the startup set, so the first page after a deploy is not the one
    that pays for them. Returns at once."""
    if _encoder() is None:
        return
    texts = [tile_text(t) for t in topics]
    with _LOCK:
        texts = [t for t in texts if t and t not in _VECTORS]
    if texts:
        _later(texts)


def history(events: Iterable, now: float, weights: dict,
            decay: Callable[[float], float],
            lookup: Callable[[str], Optional[object]]) -> list[tuple[str, float]]:
    """`(text, strength)` for this listener's recent positive behaviour.

    A search is its own words; a play or a completion is the tile it played,
    in the same `tile_text` form a candidate is embedded in, so the two sides
    of every comparison live in the same space. Strength is the event's
    `EVENT_WEIGHT` times the same decay `taste` uses, summed when one text
    recurs, then scaled so the strongest item is 1.0.
    """
    strengths: dict[str, float] = {}
    order: list[str] = []
    for event in events:  # newest first, as EventStore.for_user returns them
        weight = weights.get(event.kind, 0.0)
        if weight <= 0:
            continue
        text = (event.text or "").strip()
        if not text and event.topic_id:
            topic = lookup(event.topic_id)
            text = tile_text(topic) if topic is not None else ""
        if not text:
            continue
        if text not in strengths:
            if len(order) >= MAX_HISTORY:
                continue
            order.append(text)
            strengths[text] = 0.0
        strengths[text] += weight * decay(max(0.0, now - event.at))
    peak = max(strengths.values(), default=0.0)
    if peak <= 0:
        return []
    return [(t, strengths[t] / peak) for t in order]


def scores(candidates: Iterable, past: list[tuple[str, float]],
           inline: int = MAX_INLINE) -> dict[str, float]:
    """`{topic_id: additive term}` for every candidate with a real match.

    Absent means zero. `{}` whenever there is no model, no history, or no
    vector for anything yet - which every caller already reads as "no
    evidence".
    """
    if not past:
        return {}
    candidates = list(candidates)
    tile_texts = {t.id: tile_text(t) for t in candidates}
    vecs = vectors([text for text, _s in past] + list(tile_texts.values()),
                   inline=inline)
    rows = [(vecs[text], strength) for text, strength in past if text in vecs]
    ids = [tid for tid, text in tile_texts.items() if text in vecs]
    if not rows or not ids:
        return {}
    past_matrix = np.asarray([v for v, _s in rows], dtype=np.float32)
    # A weak or old item still counts, at up to half: strength decides how
    # much a match to it is worth, never whether it can match at all.
    discount = np.asarray([0.5 + 0.5 * s for _v, s in rows], dtype=np.float32)
    tiles = np.asarray([vecs[tile_texts[i]] for i in ids], dtype=np.float32)
    best = (tiles @ past_matrix.T * discount).max(axis=1)
    terms = SEMANTIC_WEIGHT * np.clip((best - SEMANTIC_FLOOR) / (1.0 - SEMANTIC_FLOOR), 0.0, None)
    return {tid: float(term) for tid, term in zip(ids, terms) if term > 0}


def for_listener(events: list, candidates: Iterable, now: Optional[float] = None,
                 known: Optional[dict] = None, settled: bool = False) -> dict[str, float]:
    """The one call the ranker makes. Never raises.

    `known` is `topics.known_topics(now)`, for a caller that already holds it
    - the learner calls this once per offer and must not rebuild it each time.

    `settled` embeds everything it needs before answering, instead of
    handing the excess to the background thread. Offline only: a training
    row whose semantic feature depended on how far a thread had got would
    make the same log train two different models.
    """
    try:
        if _encoder() is None:
            return {}
        import topics

        now = time.time() if now is None else now
        known = topics.known_topics(now) if known is None else known
        past = history(events, now, topics.EVENT_WEIGHT, topics._decay, known.get)
        return scores(candidates, past, inline=10 ** 9 if settled else MAX_INLINE)
    except Exception:  # noqa: BLE001 - availability first
        log.exception("semantic taste failed; ranking on tags alone")
        return {}


def unit(vec: list[float]) -> list[float]:
    """Scale to length one. For a test encoder, which must return unit vectors
    exactly as the real model does."""
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec
