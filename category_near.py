"""The nearest category by meaning, when no phrase in the tree matches (§235).

`CategoryStore.match` files an episode only where a node's words appear in
its text, in order (§209). That is exact and it is settled, and it stays
first. What it cannot do is see that "Fair Haven, New Jersey's River Road
Food Scene" is about restaurants: none of the tree's phrases is in it, so the
episode fell back to a facet and wore the facet's picture - the globe, for a
town's restaurants.

This is the fallback beneath it, and only beneath it: the closest node by
sentence embedding (the same local model `taste_vectors` ranks with), taken
only above `NEAR_COSINE`. Asked only when the exact readers found nothing
deeper than a facet; when a facet is already known the answer must sit under
it.

**A layer that adds quality must not subtract availability.** No model, a
model that throws, a tree whose vectors are still being built - every one of
those is "", which is exactly the answer before this file existed. The
node vectors are built on a background thread whenever the tree changes;
nothing waits for them. One text is embedded inline per call at most
(`taste_vectors.vectors`, whose cache it shares), and every caller memoises.

**Measured, not hoped** (§235, all-MiniLM-L6-v2 against the seed): a node's
own name scored better than its whole path, and the right node scored 0.47
and up on every example tried ("River Road Food Scene" -> `food scene` 0.58,
"the town council's vote on the new library" -> `town council` 0.59) while
the best wrong one scored 0.39 ("hurricane season forecast" -> a hockey
team 0.32). `NEAR_COSINE` sits between.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

#: The cosine a node must reach to be the episode's category. See the
#: module docstring for where it was measured.
NEAR_COSINE = 0.45

_LOCK = threading.Lock()
#: (tree generation, node ids, unit-vector matrix) - or None while building.
_BUILT: Optional[tuple] = None
_BUILDING: Optional[float] = None


def version() -> float:
    """Which node vectors answer now: a memo of a miss keyed on this is
    forgotten once the vectors it was waiting for are built."""
    built = _BUILT
    return built[0] if built is not None else -1.0


def reset() -> None:
    global _BUILT, _BUILDING
    with _LOCK:
        _BUILT = None
        _BUILDING = None


def _encoder():
    try:
        import taste_vectors

        return taste_vectors._encoder()
    except Exception:  # noqa: BLE001 - availability first
        log.exception("category_near: no encoder")
        return None


def _build(tree, generation: float, encoder) -> None:
    global _BUILT, _BUILDING
    try:
        import topics

        facets = topics.FACETS
        ids = sorted(n for n in tree.nodes() if n not in facets)
        if ids:
            matrix = np.asarray(encoder(ids), dtype=np.float32)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            matrix = matrix / np.where(norms == 0, 1.0, norms)
        else:
            matrix = np.zeros((0, 1), dtype=np.float32)
        with _LOCK:
            _BUILT = (generation, ids, matrix)
    except Exception:  # noqa: BLE001
        log.exception("category_near: could not embed the tree")
    finally:
        with _LOCK:
            if _BUILDING == generation:
                _BUILDING = None


def _matrix(tree, encoder) -> Optional[tuple]:
    """The tree's node vectors, or None while they are being built."""
    global _BUILDING
    generation = getattr(tree, "_loaded_at", 0.0)
    with _LOCK:
        built = _BUILT
        if built is not None and built[0] == generation:
            return built
        start = _BUILDING != generation
        if start:
            _BUILDING = generation
    if start:
        import taste_vectors

        if getattr(taste_vectors, "_ENCODER_OVERRIDE", None) is not None:
            # A test encoder: settle now, so a test sees the answer.
            _build(tree, generation, encoder)
        else:
            threading.Thread(target=_build, args=(tree, generation, encoder),
                             name="category-near", daemon=True).start()
    with _LOCK:
        built = _BUILT
    # An older tree's vectors still answer while the new ones build: a node
    # it names that has since gone is dropped by the caller's `tree.get`.
    return built


def nearest(text: str, facet: str = "", inline: int = 1) -> str:
    """The node `text` is closest to by meaning, at `NEAR_COSINE` or more,
    or "". Under `facet` only, when one is given. Never raises."""
    return lookup(text, facet, inline) or ""


def lookup(text: str, facet: str = "", inline: int = 1) -> Optional[str]:
    """`nearest`, saying when it cannot answer yet: None while the tree's
    vectors or this text's are still being made (`inline=0` embeds nothing
    here and queues the text), so a caller that memoises does not keep a
    miss that was only a wait. "" is a real miss."""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        encoder = _encoder()
        if encoder is None:
            return ""
        import taste_vectors
        import topics

        tree = topics.category_tree()
        built = _matrix(tree, encoder)
        if built is None:
            return None
        _gen, ids, matrix = built
        if not ids:
            return ""
        found = taste_vectors.vectors([text], inline=inline).get(text)
        if not found:
            return None
        query = np.asarray(found, dtype=np.float32)
        norm = float(np.linalg.norm(query))
        if norm == 0.0:
            return ""
        scores = matrix @ (query / norm)
        for index in np.argsort(-scores):
            if float(scores[index]) < NEAR_COSINE:
                return ""
            node = ids[int(index)]
            if tree.get(node) is None:
                continue
            if facet and topics._root_facet(node) != facet:
                continue
            return node
        return ""
    except Exception:  # noqa: BLE001 - a fallback, never a failure
        log.exception("category_near: could not place %r", text[:80])
        return ""
