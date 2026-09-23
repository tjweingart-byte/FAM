"""The order of "Made for you", learned from what listeners actually tapped.

**Why this exists.** Every number in the ranker is set by hand -
`EVENT_WEIGHT`, `SUBTAG_WEIGHT`, `BROAD_MATCH_PENALTY`, `FATIGUE_WEIGHT`,
`ENGAGEMENT_*` - each with its reasoning written beside it, and none of them
fitted to anything. Meanwhile the impression log has been recording thirty
days of labelled examples: this tile, in front of this listener, at this
moment - taken or not (`EventStore.impression_outcomes`). That is exactly
the data a model of "will they tap it" is trained on, and nothing trained one.

**What it is**: a logistic regression over the signals the hand-tuned score
already combines, fitted offline by `python tools/learn_rank.py` and stored in
the event database beside the log it came from. Six features - the ones the
ranker computes anyway - so the model reweighs existing judgements rather
than inventing new ones:

    affinity    `_affinity`: tag profile against the tile's tags
    semantic    `taste_vectors`: meaning of their history against the tile
    fatigue     how often they have been shown it and passed (-log damp)
    engagement  the tile's global tap rate, as a log lift
    live        a story from the live pool rather than the standing bank
    broad       a live story matching only a whole facet they never named

**It re-orders what cleared the floor; it never admits anything else.** The
hand-tuned score still applies `RELEVANCE_FLOOR`, the broad-match penalty and
everything else that decides whether a tile is *eligible* for a rail whose
heading says it was chosen for this listener. The model sorts the eligible
tiles - and because the rail shows the first six of that order, it does
decide **which** eligible tiles are visible (an earlier draft said it never
changed what was on the rail; review of §128 showed that was false once more
tiles clear the floor than fit). That is the standard two-stage shape - a
cheap, explainable pass for *what may be shown*, a fitted one for *order* -
and it means a bad model can promote a weaker relevant tile, never an
irrelevant one.

**Global, never per listener**, for `ENGAGEMENT_WEIGHT`'s reason: per
listener there are a handful of offers per tile, and the per-listener part of
the question is already in the features (`affinity`, `semantic`, `fatigue`).

**It has to earn the job.** The learner holds out the most recent fifth of
the log, and writes a model only if it ranks those held-out offers better than
the hand-tuned score does (AUC, by `MIN_AUC_GAIN`) on at least
`MIN_POSITIVES` taps. A model that does not beat the thing it replaces is not
written, and `active()` refuses one that says it did not. With no model the
ranker is byte-for-byte what it was; `LEARNED_RANK=0` forces that.

**A wipe takes it** (`EventStore.clear`), because it is derived from the log
- §124's rule. Account deletion does not: it holds six coefficients and
nothing about anybody.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

FEATURES = ("affinity", "semantic", "fatigue", "engagement", "live", "broad")

#: Held-out taps a verdict needs. Below it an AUC is a coin with a label.
MIN_POSITIVES = 20
#: Offers overall, for the same reason.
MIN_ROWS = 200
#: How much better than the hand-tuned score the model must rank the held-out
#: offers. Not zero: a model that merely ties is a new thing to maintain that
#: buys nothing.
MIN_AUC_GAIN = 0.01
#: L2 strength, on standardised features. Six coefficients over hundreds of
#: rows barely needs it; it is there so a feature with no variance (semantic,
#: on a deployment with no model) gets a weight of zero rather than noise.
L2 = 1.0
#: How long a loaded model is held before the table is read again.
CACHE_SECONDS = 300.0


@dataclass
class Model:
    weights: list[float]
    bias: float
    mean: list[float]
    scale: list[float]
    meta: dict

    def score(self, row: list[float]) -> float:
        z = self.bias + sum(w * (x - m) / s for w, x, m, s
                            in zip(self.weights, row, self.mean, self.scale))
        return 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, z))))

    def to_json(self) -> str:
        return json.dumps({"features": list(FEATURES), "weights": self.weights,
                           "bias": self.bias, "mean": self.mean,
                           "scale": self.scale, **self.meta})

    @classmethod
    def from_json(cls, text: str) -> "Model":
        data = json.loads(text)
        if tuple(data.get("features", ())) != FEATURES:
            # A model trained on another feature set scores the wrong columns.
            raise ValueError("model features %r are not %r"
                             % (data.get("features"), FEATURES))
        meta = {k: v for k, v in data.items()
                if k not in ("features", "weights", "bias", "mean", "scale")}
        model = cls([float(w) for w in data["weights"]], float(data["bias"]),
                    [float(m) for m in data["mean"]],
                    [float(s) or 1.0 for s in data["scale"]], meta)
        if not (len(model.weights) == len(model.mean) == len(model.scale) == len(FEATURES)):
            raise ValueError("model has the wrong number of weights")
        return model

    def stamp(self) -> str:
        """What goes on an impression's `algo` so the log can tell this model
        from the next one."""
        return "lr" + str(int(self.meta.get("trained_at", 0)))


# --- The features, one definition for training and serving -----------------

def features(topic, profile: dict, semantic: dict, damp: dict,
             engage: dict, familiar: frozenset) -> list[float]:
    import topics

    live = 1.0 if (topic.freshness > 0 or topic.id.startswith("st-")) else 0.0
    # `freshness > 0` and not `live`: that is the condition the served
    # penalty uses, and a feature that fires where serving does not is skew.
    broad = 1.0 if (topic.freshness > 0 and topics._is_broad_match(topic, profile)
                    and not topics._subject_is_familiar(topic, familiar)
                    and not semantic.get(topic.id)) else 0.0
    return [
        topics._affinity(topic, profile),
        semantic.get(topic.id, 0.0),
        -math.log(max(1e-6, damp.get(topic.id, 1.0))),
        math.log(max(1e-6, engage.get(topic.id, 1.0))),
        live,
        broad,
    ]


def hand_score(row: list[float]) -> float:
    """The hand-tuned score, rebuilt from the same features - what the
    learner has to beat. Everything `rank_from_history` multiplies in except
    freshness and a listener's place, which the log does not record; both are
    multiplied back on top of the model's probability when it serves
    (`rerank`'s `thumbs`), so the two sides are compared without them and
    served with them."""
    import topics

    affinity, semantic, fatigue, engagement, _live, broad = row
    score = (affinity + semantic) * math.exp(-fatigue) * math.exp(engagement)
    if broad and score > 0:
        score *= topics.BROAD_MATCH_PENALTY
    return score


# --- Serving ----------------------------------------------------------------

_CACHE: dict[str, tuple[float, Optional[Model]]] = {}


def reset() -> None:
    _CACHE.clear()


def _switched_off() -> bool:
    return os.environ.get("LEARNED_RANK", "1").strip().lower() in ("0", "false", "no", "off")


def active(store, now: Optional[float] = None) -> Optional[Model]:
    """The model in force for this database, or None. Never raises."""
    if _switched_off():
        return None
    now = time.time() if now is None else now
    try:
        key = store.path
    except Exception:  # noqa: BLE001 - a broken store is no model
        return None
    cached = _CACHE.get(key)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    model = None
    try:
        text = store.learned_model()
        if text:
            candidate = Model.from_json(text)
            # Written by a learner that did not beat the hand-tuned score
            # (only possible with --force). Held, reported, never served.
            if candidate.meta.get("beat_hand"):
                model = candidate
    except Exception:  # noqa: BLE001
        log.exception("stored ranking model is unreadable; ranking by hand")
    _CACHE[key] = (now, model)
    return model


def rerank(scored: list, model: Model, rows: dict,
           thumbs: Optional[dict] = None) -> list:
    """`scored` is `[(hand_score, topic)]`; returns it in the model's order.

    `thumbs` multiplies the model's probability for the signals the log does
    not record - freshness and a listener's own place - so turning a model on
    does not quietly switch them off. The hand score breaks ties, the topic
    id breaks those.
    """
    thumbs = thumbs or {}
    return sorted(scored, key=lambda pair: (
        -model.score(rows[pair[1].id]) * thumbs.get(pair[1].id, 1.0),
        -pair[0], pair[1].id))


def describe(store) -> dict:
    if _switched_off():
        return {"active": False, "reason": "LEARNED_RANK=0"}
    try:
        text = store.learned_model()
    except Exception as exc:  # noqa: BLE001
        return {"active": False, "reason": type(exc).__name__}
    if not text:
        return {"active": False,
                "reason": "no model trained - python tools/learn_rank.py"}
    try:
        model = Model.from_json(text)
    except Exception as exc:  # noqa: BLE001
        return {"active": False, "reason": "unreadable: " + str(exc)}
    out = {"active": bool(model.meta.get("beat_hand")),
           "weights": dict(zip(FEATURES, (round(w, 3) for w in model.weights)))}
    out.update({k: model.meta[k] for k in ("trained_at", "rows", "positives",
                                           "auc_learned", "auc_hand", "beat_hand")
                if k in model.meta})
    if not out["active"]:
        out["reason"] = "stored model did not beat the hand-tuned score"
    return out


# --- Training ---------------------------------------------------------------

def auc(scores, labels) -> float:
    """Probability a random tap outranks a random pass. Ties count half."""
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=bool)
    pos, neg = labels.sum(), (~labels).sum()
    if pos == 0 or neg == 0:
        return 0.5
    order = scores.argsort(kind="mergesort")
    ranks = np.empty(len(scores), dtype=float)
    sorted_scores = scores[order]
    i = 0
    while i < len(scores):
        j = i
        while j + 1 < len(scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return float((ranks[labels].sum() - pos * (pos + 1) / 2.0) / (pos * neg))


def fit(rows, labels, l2: float = L2, meta: Optional[dict] = None) -> Model:
    """Logistic regression by Newton's method on standardised features."""
    x = np.asarray(rows, dtype=float)
    y = np.asarray(labels, dtype=float)
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale == 0] = 1.0
    z = (x - mean) / scale
    design = np.hstack([np.ones((len(z), 1)), z])
    beta = np.zeros(design.shape[1])
    penalty = np.eye(design.shape[1]) * l2
    penalty[0, 0] = 0.0  # never shrink the intercept
    for _ in range(50):
        p = 1.0 / (1.0 + np.exp(-np.clip(design @ beta, -35, 35)))
        grad = design.T @ (p - y) + penalty @ beta
        hess = (design * (p * (1 - p))[:, None]).T @ design + penalty
        step = np.linalg.solve(hess + np.eye(len(beta)) * 1e-9, grad)
        beta -= step
        if np.abs(step).max() < 1e-8:
            break
    return Model([float(b) for b in beta[1:]], float(beta[0]),
                 [float(m) for m in mean], [float(s) for s in scale], dict(meta or {}))


def train(rows: list[list[float]], labels: list[bool], holdout: float = 0.2,
          now: Optional[float] = None) -> tuple[Optional[Model], dict]:
    """Evaluate on the most recent `holdout`, then fit on everything.

    `rows` must be in time order. Returns `(model, report)`; the model is None
    when there is not enough data to judge, and carries `beat_hand` either
    way it is returned.
    """
    now = time.time() if now is None else now
    report = {"rows": len(rows), "positives": int(sum(labels))}
    if len(rows) < MIN_ROWS:
        report["verdict"] = f"too few offers ({len(rows)} < {MIN_ROWS})"
        return None, report
    cut = int(len(rows) * (1.0 - holdout))
    test_pos = int(sum(labels[cut:]))
    if test_pos < MIN_POSITIVES or sum(labels[:cut]) < MIN_POSITIVES:
        report["verdict"] = (f"too few taps to judge (held out {test_pos}, "
                             f"need {MIN_POSITIVES} on each side)")
        return None, report
    trial = fit(rows[:cut], labels[:cut])
    held_rows, held_labels = rows[cut:], labels[cut:]
    auc_learned = auc([trial.score(r) for r in held_rows], held_labels)
    auc_hand = auc([hand_score(r) for r in held_rows], held_labels)
    beat = auc_learned >= auc_hand + MIN_AUC_GAIN
    report.update(auc_learned=round(auc_learned, 4), auc_hand=round(auc_hand, 4),
                  held_out=len(held_rows), held_out_positives=test_pos,
                  beat_hand=beat)
    report["verdict"] = ("beats the hand-tuned order" if beat
                         else "does not beat the hand-tuned order")
    meta = {"trained_at": now, "rows": len(rows), "positives": int(sum(labels)),
            "auc_learned": report["auc_learned"], "auc_hand": report["auc_hand"],
            "beat_hand": beat}
    return fit(rows, labels, meta=meta), report
