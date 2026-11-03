"""Phase 2, piece 4: Consuela's dedupe and its eval (§401).

Against fake models and a fake embedder (no key, no network):

* code settles every number, quote, forecast and live record itself;
* a merge needs both models to say "same"; a fallen-back call keeps both;
* a merge is a reversible mark - the repeat inactive, its link (never its
  text) on the keeper, deleted for good only 30 days later;
* the dedupe is a dry run by default, and stays one until the eval has
  passed for the models set;
* the eval catches a careless model, and refuses to pass on fallen-back calls.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from contextlib import closing
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import history_archive as ha  # noqa: E402
from agents import dedupe, dedupe_eval, housekeeper as hk, models  # noqa: E402

DAY = 86400.0
NOW = dedupe_eval.BASE + 1 * DAY


def _embed(text):
    """A bag-of-words unit vector: alike wording, alike vectors."""
    v = [0.0] * ha.DIMS
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        v[int(hashlib.md5(word.encode()).hexdigest(), 16) % ha.DIMS] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v], f"onnx:{ha.DIMS}"


def _settings(monkeypatch, **values):
    changed = dataclasses.replace(ha.settings, archive=True, archive_backend="sqlite",
                                  **values)
    for module in (ha, hk, dedupe):
        monkeypatch.setattr(module, "settings", changed)
    return changed


@pytest.fixture
def archive(monkeypatch, tmp_path):
    ha.reset()
    _settings(monkeypatch)
    monkeypatch.setattr(ha, "_embed", _embed)
    monkeypatch.setattr(ha, "consuela_runs", lambda: True)
    store = ha.SQLiteBackend(str(tmp_path / "archive.db"))
    ha.use(store)
    hk.reset(hk.ReportStore(str(tmp_path / "housekeeper.db")))
    yield store
    ha.reset()
    hk.reset()


class _Model:
    """`models.ask` that answers each pair with `judge(a_text, b_text)`."""

    def __init__(self, judge, status=models.OK):
        self.judge, self.status, self.requests = judge, status, []

    async def __call__(self, request, **_kw):
        self.requests.append(request)
        if self.status != models.OK:
            return models.AgentResult(status=self.status, agent=request.agent,
                                      task=request.task)
        (_tag, text), = request.material
        pairs = re.findall(r"A \(published [^)]*\): (.*)\nB \(published [^)]*\): (.*)", text)
        value = dedupe.Verdicts(pairs=[
            dedupe.PairVerdict(pair=i, verdict=self.judge(a, b))
            for i, (a, b) in enumerate(pairs, 1)])
        return models.AgentResult(status=models.OK, agent=request.agent,
                                  task=request.task, value=value)


#: A careful model: "same" only for the eval's known repeats.
_REPEATS = {frozenset((case[2][0], case[3][0])) for case in dedupe_eval.CASES
            if case[1] == dedupe_eval.SAME}


def _careful(a, b):
    return "same" if frozenset((a, b)) in _REPEATS else "different"


def _reckless(_a, _b):
    return "same"


def _fact(text, day=0, grade="established", url=None, kind="sentence", node="eagles"):
    return ha.Fact(url=url or f"https://x.com/{abs(hash(text))}", text=text,
                   title="T", host="x.com", grade=grade, kind=kind,
                   published=_date(day), subject="Eagles", node_id=node)


def _date(day):
    import time

    return time.strftime("%Y-%m-%d", time.gmtime(dedupe_eval.BASE + day * DAY))


def _active(store):
    with closing(sqlite3.connect(store.path)) as db:
        return dict(db.execute("SELECT text, active FROM facts").fetchall())


# --- code's half -----------------------------------------------------------------

def _pair(text_a, text_b, day_b=0, kind="sentence", status_a="", status_b=""):
    a = {"id": "a", "url": "https://a", "text": text_a, "kind": kind,
         "published_ts": dedupe_eval.BASE, "figure": {"status": status_a} if status_a else {}}
    b = {"id": "b", "url": "https://b", "text": text_b, "kind": kind,
         "published_ts": dedupe_eval.BASE + day_b * DAY,
         "figure": {"status": status_b} if status_b else {}}
    return a, b


@pytest.mark.parametrize("a,b,similarity,verdict", [
    (*_pair("Eagles 24, Giants 17.", "Eagles 24, Giants 17.", kind="live"), 1.0, "different"),
    (*_pair("The Fed held rates.", "the Fed held rates"), 0.5, "same"),
    (*_pair("Apple closed at $201.50.", "Apple closed at $201.50."), 1.0, "same"),
    # A price on two days, word for word, is still two prices.
    (*_pair("Apple closed at $201.50.", "Apple closed at $201.50.", day_b=1), 1.0,
     "different"),
    (*_pair("The Fed held rates.", "The Fed held rates.", day_b=5), 1.0, "different"),
    (*_pair("The Eagles are expected to win.", "The Eagles won."), 0.95, "different"),
    (*_pair("Revenue was $30 billion.", "Revenue was $30 billion in all.",
            status_a="forecast", status_b="outcome"), 0.95, "different"),
    (*_pair("Turnout in Ohio was 61 percent.", "Turnout in Ohio was 58 percent."), 0.95,
     "conflict"),
    (*_pair("Apple rose 2 percent.", "Microsoft rose 2 percent."), 0.95, "different"),
    (*_pair('"We will win," Smith said.', '"We will win," Smith told reporters.'), 0.95,
     "different"),
    # A figure is merged only word for word (§417), however alike.
    (*_pair("Nvidia's revenue hit $35.1 billion.", "Nvidia revenue reached $35.1 billion."),
     0.93, "different"),
    (*_pair("The Eagles beat the Giants 24-17.", "The Giants beat the Eagles 24-17."),
     0.99, "different"),
    (*_pair("Eagles 24, Giants 17.", "Eagles 17, Giants 24."), 0.99, "different"),
    (*_pair("Tesla rose 3 percent.", "Tesla fell 3 percent."), 0.99, "different"),
    (*_pair("Nvidia's revenue hit $35.1 billion.", "Nvidia revenue reached $35.1 billion."),
     0.85, "different"),
    (*_pair("The council approved the budget.", "The mayor visited a school."), 0.3,
     "different"),
    (*_pair("The council approved the budget.", "The budget was approved by the council."),
     0.9, "ask"),
])
def test_code_settles_what_it_can(a, b, similarity, verdict):
    assert dedupe.code_verdict(a, b, similarity)[0] == verdict


def test_names_count_the_first_word_but_not_an_opener():
    assert dedupe.names("Apple closed higher.") == {"Apple"}
    assert dedupe.names("The Eagles beat the Giants on Sunday.") == {"Eagles", "Giants",
                                                                     "Sunday"}
    assert dedupe.names("Nvidia's revenue rose.") == {"Nvidia"}


def test_the_better_source_is_kept_then_the_earlier():
    primary = {"id": "p", "grade": "primary", "published_ts": 5.0}
    earlier = {"id": "e", "grade": "established", "published_ts": 1.0}
    later = {"id": "l", "grade": "established", "published_ts": 2.0}
    assert dedupe.keeper_of(earlier, primary)[0] is primary
    assert dedupe.keeper_of(later, earlier)[0] is earlier


# --- the models' half ------------------------------------------------------------

def _asked_pair():
    return _pair("The council approved the budget.", "The budget was approved by the council.")


def test_a_merge_needs_both_models_to_say_same(monkeypatch):
    a, b = _asked_pair()
    answers = iter(["same", "unsure"])
    monkeypatch.setattr(models, "ask", _Model(lambda *_: next(answers)))
    (d,), fell_back = asyncio.run(dedupe.decide([(a, b, 0.9)]))
    assert (d.verdict, d.first, d.review, fell_back) == ("different", "same", "unsure", False)
    monkeypatch.setattr(models, "ask", _Model(lambda *_: "same"))
    (d,), _ = asyncio.run(dedupe.decide([(a, b, 0.9)]))
    assert (d.verdict, d.why) == ("same", "both models: same")


def test_the_second_opinion_is_only_asked_about_a_first_same(monkeypatch):
    a, b = _asked_pair()
    fake = _Model(lambda *_: "unsure")
    monkeypatch.setattr(models, "ask", fake)
    (d,), _ = asyncio.run(dedupe.decide([(a, b, 0.9)]))
    assert d.verdict == "different" and len(fake.requests) == 1
    assert fake.requests[0].model_setting == "housekeeper_model"


def test_a_fallen_back_call_keeps_both_and_says_so(monkeypatch):
    a, b = _asked_pair()
    monkeypatch.setattr(models, "ask", _Model(_careful, status=models.STAGING))
    (d,), fell_back = asyncio.run(dedupe.decide([(a, b, 0.9)]))
    assert d.verdict == "different" and fell_back


def test_no_number_and_no_live_record_ever_reaches_a_model(monkeypatch):
    fake = _Model(_reckless)
    monkeypatch.setattr(models, "ask", fake)
    pairs = [(*_pair("Apple rose 2 percent.", "Microsoft rose 2 percent."), 0.99),
             (*_pair("Eagles 24, Giants 17.", "Eagles 24, Giants 17.", kind="live"), 1.0)]
    decisions, _ = asyncio.run(dedupe.decide(pairs))
    assert [d.verdict for d in decisions] == ["different", "different"]
    assert fake.requests == []


# --- the eval ---------------------------------------------------------------------

def test_the_eval_passes_a_careful_model(archive, monkeypatch):
    monkeypatch.setattr(models, "ask", _Model(_careful))
    result = asyncio.run(dedupe_eval.run(embed=_embed))
    assert result["wrong_merges"] == 0 and not result["fell_back"] and result["passed"]
    assert dedupe.eval_passed() == (True, "")


def test_the_eval_catches_a_model_that_says_same_too_easily(archive, monkeypatch):
    monkeypatch.setattr(models, "ask", _Model(_reckless))
    result = asyncio.run(dedupe_eval.run(embed=_embed))
    wrong = [r["case"] for r in result["details"]
             if r["expected"] == "different" and r["verdict"] == "same"]
    assert wrong and not result["passed"]
    # Code still held every number, quote and live record.
    assert not {"a price on two days", "two companies, one figure", "one quote, two speakers",
                "two live finals of one game"} & set(wrong)
    # The instruction case reaches the models by design, and a reckless one obeys it.
    assert "an instruction inside the material" in wrong
    assert dedupe.eval_passed()[0] is False


def test_the_eval_never_passes_on_calls_that_fell_back(archive, monkeypatch):
    monkeypatch.setattr(models, "ask", _Model(_careful, status=models.OFF))
    result = asyncio.run(dedupe_eval.run(embed=_embed))
    assert result["wrong_merges"] == 0 and result["fell_back"] and not result["passed"]
    assert "fell back" in dedupe.eval_passed()[1]


def test_the_eval_belongs_to_the_models_it_ran_with(archive, monkeypatch):
    monkeypatch.setattr(models, "ask", _Model(_careful))
    asyncio.run(dedupe_eval.run(embed=_embed))
    _settings(monkeypatch, housekeeper_review_model="anthropic:claude-opus-5-5")
    assert dedupe.eval_passed() == (False, "no eval has run for these models")


def test_without_the_sentence_model_the_eval_does_not_run(archive):
    assert asyncio.run(dedupe_eval.run(embed=lambda t: (None, "")))["ran"] is False


# --- the nightly run ----------------------------------------------------------------

def _two_repeats(archive):
    ha.add_fact(_fact("The council approved the budget.", grade="primary"))
    ha.add_fact(_fact("The budget was approved by the council.", url="https://y.com/1"))


def _run():
    return asyncio.run(dedupe.run(now=NOW))


def test_the_dry_run_reports_the_merge_and_marks_nothing(archive, monkeypatch):
    monkeypatch.setattr(models, "ask", _Model(lambda *_: "same"))
    monkeypatch.setattr(ha.time, "time", lambda: NOW)
    _two_repeats(archive)
    summary = _run()
    assert (summary["mode"], summary["same"], summary["marked"]) == ("dry_run", 1, 0)
    assert set(_active(archive).values()) == {1}
    merge, = hk.store().recent_merges()
    assert merge["mode"] == "dry_run" and merge["keeper_text"] == "The council approved the budget."


def test_live_without_a_passed_eval_stays_a_dry_run(archive, monkeypatch):
    _settings(monkeypatch, housekeeper_dedupe_dry_run=False)
    monkeypatch.setattr(models, "ask", _Model(lambda *_: "same"))
    monkeypatch.setattr(ha.time, "time", lambda: NOW)
    _two_repeats(archive)
    summary = _run()
    assert summary["mode"] == "dry_run" and "no eval" in summary["why"]
    assert set(_active(archive).values()) == {1}


def test_live_after_a_passed_eval_marks_the_repeat_reversibly(archive, monkeypatch):
    monkeypatch.setattr(models, "ask", _Model(_careful))
    asyncio.run(dedupe_eval.run(embed=_embed))
    _settings(monkeypatch, housekeeper_dedupe_dry_run=False)
    monkeypatch.setattr(models, "ask", _Model(lambda *_: "same"))
    monkeypatch.setattr(ha.time, "time", lambda: NOW)
    _two_repeats(archive)
    summary = _run()
    assert (summary["mode"], summary["marked"]) == ("live", 1)
    assert _active(archive) == {"The council approved the budget.": 1,
                                "The budget was approved by the council.": 0}
    with closing(sqlite3.connect(archive.path)) as db:
        links, = db.execute("SELECT corroborations FROM facts WHERE active = 1").fetchone()
        repeat_id, why = db.execute("SELECT id, superseded_why FROM facts "
                                    "WHERE active = 0").fetchone()
    link, = json.loads(links)
    assert link["url"] == "https://y.com/1" and "text" not in link
    assert why == "both models: same"
    # Reversible: undone, the link comes off the keeper.
    assert archive.restore(repeat_id)
    assert set(_active(archive).values()) == {1}
    with closing(sqlite3.connect(archive.path)) as db:
        assert db.execute("SELECT corroborations FROM facts WHERE id != ?",
                          (repeat_id,)).fetchone()[0] == "[]"


def test_a_repeat_is_deleted_for_good_only_after_thirty_days(archive):
    keeper = ha.add_fact(_fact("The council approved the budget.", grade="primary"))
    repeat = ha.add_fact(_fact("The budget was approved by the council.", url="https://y.com/1"))
    assert archive.supersede(repeat, keeper, "test", NOW)
    assert archive.purge_superseded(NOW - 30 * DAY) == 0
    assert archive.purge_superseded(NOW + 1) == 1
    assert _active(archive) == {"The council approved the budget.": 1}


def test_a_conflict_is_flagged_and_both_kept(archive, monkeypatch):
    monkeypatch.setattr(models, "ask", _Model(_reckless))
    monkeypatch.setattr(ha.time, "time", lambda: NOW)
    monkeypatch.setattr(dedupe, "similarity_of", lambda a, b: 0.97)
    ha.add_fact(_fact("Turnout in the Ohio election was 61 percent."))
    ha.add_fact(_fact("Turnout in the Ohio election was 58 percent.", url="https://y.com/1"))
    summary = _run()
    assert summary["conflicts"] == 1 and summary["same"] == 0
    assert set(_active(archive).values()) == {1}
    assert hk.store().recent_merges()[0]["verdict"] == "conflict"


def test_with_consuela_off_nothing_runs(archive, monkeypatch):
    monkeypatch.setattr(ha, "consuela_runs", lambda: False)
    fake = _Model(_reckless)
    monkeypatch.setattr(models, "ask", fake)
    assert _run()["why"] == "Consuela may not run" and fake.requests == []


def test_the_dedupe_is_registered_and_scheduled_after_the_consolidation():
    from agents import netenyahu

    try:
        hk.register()
        assert netenyahu.HANDLERS[("housekeeper", "dedupe")] is dedupe.handler
    finally:
        for kind in ("consolidate", "dedupe", "refile", "second", "comb"):
            netenyahu.HANDLERS.pop(("housekeeper", kind), None)
    hours = {kind: hour for agent, kind, hour, _s in netenyahu.SCHEDULE
             if agent == "housekeeper"}
    assert (hours["consolidate"], hours["dedupe"]) == (1, 2)


def test_atlas_marks_restores_and_purges():
    class _Facts:
        def __init__(self):
            self.docs = {"k": {"_id": "k", "active": True, "corroborations": []},
                         "r": {"_id": "r", "active": True, "url": "https://r", "host": "r",
                               "grade": "established", "published": None}}
            self.calls = []

        def find_one(self, query):
            doc = self.docs.get(query["_id"])
            return doc if doc and doc["active"] == query["active"] else None

        def update_one(self, query, update):
            self.calls.append((query, update))
            doc = self.docs[query["_id"]]
            doc.update(update.get("$set", {}))

        def delete_many(self, query):
            self.calls.append(("delete", query))
            return SimpleNamespace(deleted_count=2)

    facts = _Facts()
    store = ha.AtlasBackend(database={"intake": None, "episode_sources": None,
                                      "facts": facts, "live_series": None})
    assert store.supersede("r", "k", "both models: same", NOW)
    push = facts.calls[0][1]["$push"]["corroborations"]
    assert push["fact"] == "r" and "text" not in push
    assert facts.docs["r"]["active"] is False and facts.docs["r"]["superseded_by"] == "k"
    assert store.restore("r") and facts.docs["r"]["active"] is True
    assert facts.calls[2] == ({"_id": "k"}, {"$pull": {"corroborations": {"fact": "r"}}})
    assert store.purge_superseded(NOW) == 2


def test_her_dedupe_defaults_to_a_dry_run():
    from config import settings

    assert settings.housekeeper_dedupe_dry_run is True
    assert settings.housekeeper_superseded_days == 30.0
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, ".env.example")).read()
    for line in ("HOUSEKEEPER_DEDUPE_DRY_RUN=1", "HOUSEKEEPER_SUPERSEDED_DAYS=30",
                 f"HOUSEKEEPER_DEDUPE_PAIRS={settings.housekeeper_dedupe_pairs}"):
        assert line in text
