"""§502: training Nando - the answer key grades what teaches, through the
Message Batches API, and grades every solid source that files an episode."""
from __future__ import annotations

import asyncio
import dataclasses
import types

import pytest

import categories as C
import category_audit
import config
import metering
import stories
import topics
from agents import categorizer, models, netenyahu, rulebooks, roster, training
from tests.test_agents_model_layer import ledger, rows  # noqa: F401 - fixture

JUDGE_TEXT = '{"node": "nfl", "confidence": "high", "reason": "about the league"}'


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    yield t


def _row(origin, key, at, **kw):
    return dict({"origin": origin, "key": key, "at": at, "query": f"q {key}",
                 "nando": "nfl", "nando_request": "nfl", "nando_request_how": "picked",
                 "nando_how": "confirmed", "writer": "nfl", "writer_facet": "sports"}, **kw)


# ------------------------------------------------------------------ 1. what is graded
def test_every_write_path_takes_its_turn():
    rows_ = [_row("tap", f"t{i}", 100 + i) for i in range(10)] + \
            [_row("dailyfam", f"d{i}", 50 + i) for i in range(2)]
    order = [r["origin"] for r in training.choose(rows_)]
    # An edition's burst never crowds out searches, nor the reverse.
    assert order[:4] == ["dailyfam", "tap", "dailyfam", "tap"]
    assert len(order) == 12


def test_within_a_path_what_teaches_comes_first(tree):
    rows_ = [
        _row("tap", "agreed", 900),
        _row("tap", "source", 800, nando_request_how="tile"),
        _row("tap", "model", 700, nando_request_how="model"),
        _row("tap", "disagree", 600, writer="film"),
        _row("tap", "refiled", 500, nando_how="refiled"),
        _row("tap", "nothing", 400, nando="", nando_request=""),
    ]
    assert [r["key"] for r in training.choose(rows_)] == \
        ["nothing", "refiled", "disagree", "model", "source", "agreed"]


def test_within_a_tier_the_facets_take_turns(tree):
    rows_ = [_row("tap", "nfl1", 900), _row("tap", "nfl2", 800),
             _row("tap", "film", 700, nando="film", nando_request="film", writer="film")]
    assert [r["key"] for r in training.choose(rows_)] == ["nfl1", "film", "nfl2"]


def test_the_nightly_run_grades_in_that_order(tree, monkeypatch):
    monkeypatch.setattr(training, "allowed", lambda: (True, ""))
    monkeypatch.setattr(config, "settings",
                        dataclasses.replace(config.settings, nando_judge_batch=False))
    for i in range(6):
        category_audit.note(f"edition {i}", "", "dailyfam", key=f"e{i}")
    category_audit.note("a search", "", "tap", key="s0")
    asked = []

    async def ask(request):
        asked.append(dict(request.material)["request"])
        return models.AgentResult(status="ok", agent="categorizer", task="judge",
                                  value=training.JudgeLabel(node="nfl", confidence="high",
                                                            reason="r"),
                                  model="claude-opus-5-5")
    monkeypatch.setattr(models, "ask", ask)
    out = asyncio.run(training.judge_batch(lambda k: ["s"], limit=2))
    # Newest first would have taken two editions; the search takes its turn.
    assert "a search" in asked and out["by_origin"] == {"dailyfam": 1, "tap": 1}


# ------------------------------------------------------------------ 2. the batch
class FakeBatches:
    def __init__(self, status="ended", results=None, fail_create=False):
        self.created, self.status, self.results_ = [], status, results or {}
        self.fail_create = fail_create

    async def create(self, requests):
        if self.fail_create:
            raise RuntimeError("down")
        self.created.append(requests)
        return types.SimpleNamespace(id="msgbatch_1", processing_status="in_progress")

    async def retrieve(self, batch_id):
        return types.SimpleNamespace(id=batch_id, processing_status=self.status)

    async def results(self, batch_id):
        async def gen():
            for cid, result in self.results_.items():
                yield types.SimpleNamespace(custom_id=cid, result=result)
        return gen()


class FakeClient:
    def __init__(self, batches):
        self.messages = types.SimpleNamespace(batches=batches)
        self.retries = []

    def with_options(self, **kw):
        self.retries.append(kw.get("max_retries"))
        return self


def _succeeded(text=JUDGE_TEXT, model="claude-opus-5-5"):
    message = types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
        model=model, usage=types.SimpleNamespace(
            input_tokens=7000, output_tokens=100, cache_read_input_tokens=0,
            cache_creation_input_tokens=0))
    return types.SimpleNamespace(type="succeeded", message=message)


@pytest.fixture
def judge_on(monkeypatch, ledger):  # noqa: F811 - the fixture imported above
    changed = dataclasses.replace(models.settings, agents=True, categorizer=True,
                                  categorizer_judge_model="anthropic:claude-opus-5-5")
    monkeypatch.setattr(models, "settings", changed)
    monkeypatch.setattr(models.rulebooks, "check", lambda book: rulebooks.Lock(
        ok=True, version="7", sha="ab" * 32, text="RULES FOR " + book))
    monkeypatch.setattr(models.spend_guard, "enabled", lambda: False)
    return ledger


def _use_client(monkeypatch, client):
    from agents.adapters import anthropic_adapter

    monkeypatch.setattr(anthropic_adapter.ADAPTER, "client", lambda: client)


def test_a_batched_request_is_the_live_request(judge_on, monkeypatch, tree):
    batches = FakeBatches()
    _use_client(monkeypatch, FakeClient(batches))
    request = training._judge_request("how did the Eagles do", ["They won."], "nfl | sports")
    batch_id, refused, attempted = asyncio.run(models.submit_batch({"n0": request}))
    assert batch_id == "msgbatch_1" and refused == {} and attempted
    params = batches.created[0][0]["params"]
    from agents.adapters import anthropic_adapter

    ready = models._ready(request, 0.0)
    spec, adapter, system, schema, _kw = ready
    live = anthropic_adapter.ADAPTER.params(
        model=spec.model, system=system, user=models.build_user_turn(request.material),
        schema=schema, max_tokens=request.max_tokens, effort=request.effort)
    # Identical, the standard cache included: the ledger prices that one
    # correctly (a one-hour write is billed at 2x, and nothing records that).
    assert params == live


def test_a_refused_request_is_never_sent(judge_on, monkeypatch, tree):
    monkeypatch.setattr(models, "settings",
                        dataclasses.replace(models.settings, categorizer=False))
    batches = FakeBatches()
    _use_client(monkeypatch, FakeClient(batches))
    batch_id, refused, attempted = asyncio.run(models.submit_batch(
        {"n0": training._judge_request("q", ["s"], "")}))
    assert batch_id == "" and refused["n0"].status == models.OFF and not batches.created
    assert attempted is False


def test_staging_sends_no_batch(judge_on, monkeypatch, tree):
    monkeypatch.setattr(models.spend_guard, "enabled", lambda: True)
    batches = FakeBatches()
    _use_client(monkeypatch, FakeClient(batches))
    batch_id, refused, attempted = asyncio.run(models.submit_batch(
        {"n0": training._judge_request("q", ["s"], "")}))
    assert batch_id == "" and refused["n0"].status == models.STAGING and not batches.created
    assert attempted is False


def test_collected_answers_are_priced_at_half_and_recorded(judge_on, monkeypatch, tree):
    batches = FakeBatches(results={"n0": _succeeded()})
    _use_client(monkeypatch, FakeClient(batches))
    request = training._judge_request("q", [], "")
    out = asyncio.run(models.collect_batch("msgbatch_1", {"n0": request}))
    assert out["n0"].ok and out["n0"].value.node == "nfl"
    full = 7000 * 4.00 / 1e6 + 100 * 20.00 / 1e6
    assert out["n0"].cost_usd == pytest.approx(full * (1 - metering.BATCH_DISCOUNT))
    models.flush()
    assert [r["status"] for r in rows(judge_on)] == ["ok"]


def test_a_batch_still_running_is_left_for_later(judge_on, monkeypatch, tree):
    _use_client(monkeypatch, FakeClient(FakeBatches(status="in_progress")))
    assert asyncio.run(models.collect_batch(
        "msgbatch_1", {"n0": training._judge_request("q", [], "")})) is None


def test_the_night_sends_one_batch_and_the_collector_keeps_its_grades(judge_on, monkeypatch,
                                                                      tree):
    monkeypatch.setattr(training, "allowed", lambda: (True, ""))
    for key in ("a", "b"):
        category_audit.note(f"question {key}", "", "tap", key=key)
    batches = FakeBatches()
    _use_client(monkeypatch, FakeClient(batches))
    out = asyncio.run(training.judge_batch(lambda k: ["s"], limit=10))
    assert out["batched"] == 2 and len(batches.created) == 1
    assert training.store().labelled(["a", "b"]) == set()      # nothing until collected
    # Every episode is asked once, answered or not (nando.md rule 12).
    assert training.store().asked(["a", "b"]) == {"a", "b"}
    batches.results_ = {"n0": _succeeded(), "n1": _succeeded()}
    got = asyncio.run(training.collect_batches(lambda k: ["s"]))
    assert got["labelled"] == 2 and training.store().labelled(["a", "b"]) == {"a", "b"}
    # Its cost and cache share are measured, never assumed (§502).
    assert got["cost_usd"] > 0 and got["cache_hit_share"] == 0.0
    # Collected once: a second pass finds nothing waiting.
    assert asyncio.run(training.collect_batches(lambda k: ["s"]))["batches"] == 0


def test_what_the_batch_did_not_answer_is_never_asked_again(judge_on, monkeypatch, tree):
    # nando.md rule 12: each finished episode is read once; §391: never asked
    # again, answered or not. A batch gets the single attempt a live grade does.
    monkeypatch.setattr(training, "allowed", lambda: (True, ""))
    category_audit.note("question a", "", "tap", key="a")
    batches = FakeBatches()
    _use_client(monkeypatch, FakeClient(batches))
    asyncio.run(training.judge_batch(lambda k: ["s"], limit=10))
    batches.results_ = {"n0": types.SimpleNamespace(type="expired", error=None)}
    live = []

    async def judge(*a, **k):
        live.append(a)
    monkeypatch.setattr(training, "judge", judge)
    got = asyncio.run(training.collect_batches(lambda k: ["s"]))
    assert live == [] and got["failed"] == 1 and got["labelled"] == 0
    assert asyncio.run(training.judge_batch(lambda k: ["s"], limit=10))["looked"] == 0


def test_a_failed_create_is_never_followed_by_a_live_read(judge_on, monkeypatch, tree):
    # The batch may exist although the create failed: reading the same
    # episodes live as well would read each twice (rule 12, the review).
    monkeypatch.setattr(training, "allowed", lambda: (True, ""))
    category_audit.note("question a", "", "tap", key="a")
    _use_client(monkeypatch, FakeClient(FakeBatches(fail_create=True)))
    live = []

    async def judge(*a, **k):
        live.append(a)
    monkeypatch.setattr(training, "judge", judge)
    out = asyncio.run(training.judge_batch(lambda k: ["s"], limit=10))
    assert live == [] and out["failed"] == 1 and "batch_id" not in out


def test_a_vendor_that_cannot_batch_is_graded_live(judge_on, monkeypatch, tree):
    monkeypatch.setattr(training, "allowed", lambda: (True, ""))
    category_audit.note("question a", "", "tap", key="a")

    async def submit(requests, **k):
        return "", {}, False                    # nothing left the machine
    monkeypatch.setattr(models, "submit_batch", submit)
    live = []

    async def judge(key, query, sentences, listing):
        live.append(key)
        return {"node": "nfl", "confidence": "high", "reason": "r", "judge_model": "m"}
    monkeypatch.setattr(training, "judge", judge)
    out = asyncio.run(training.judge_batch(lambda k: ["s"], limit=10))
    assert live == ["a"] and out["labelled"] == 1


def test_a_batch_is_read_with_the_model_it_was_sent_to(judge_on, monkeypatch, tree):
    # A deploy that moves the key's model while a batch waits must not fail
    # every answer as the wrong model.
    monkeypatch.setattr(training, "allowed", lambda: (True, ""))
    category_audit.note("question a", "", "tap", key="a")
    batches = FakeBatches()
    _use_client(monkeypatch, FakeClient(batches))
    asyncio.run(training.judge_batch(lambda k: ["s"], limit=10))
    assert training.store().pending_batches()[0][3] == "anthropic:claude-opus-5-5"
    monkeypatch.setattr(models, "settings", dataclasses.replace(
        models.settings, categorizer_judge_model="anthropic:claude-sonnet-5-5"))
    batches.results_ = {"n0": _succeeded(model="claude-opus-5-5")}
    got = asyncio.run(training.collect_batches(lambda k: ["s"]))
    assert got["labelled"] == 1


def test_a_batch_whose_grade_cannot_be_kept_is_never_read_twice(judge_on, monkeypatch, tree):
    monkeypatch.setattr(training, "allowed", lambda: (True, ""))
    category_audit.note("question a", "", "tap", key="a")
    batches = FakeBatches()
    _use_client(monkeypatch, FakeClient(batches))
    asyncio.run(training.judge_batch(lambda k: ["s"], limit=10))
    batches.results_ = {"n0": _succeeded()}

    def broken(*a, **k):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(training, "_keep", broken)
    first = asyncio.run(training.collect_batches(lambda k: ["s"]))
    assert first["failed"] == 1
    # Finished before its grades were kept: its spend is never recorded twice.
    assert asyncio.run(training.collect_batches(lambda k: ["s"]))["batches"] == 0


def test_a_batch_collected_after_the_owners_review_never_replaces_it(judge_on, monkeypatch,
                                                                     tree):
    monkeypatch.setattr(training, "allowed", lambda: (True, ""))
    category_audit.note("question a", "", "tap", key="a")
    batches = FakeBatches()
    _use_client(monkeypatch, FakeClient(batches))
    asyncio.run(training.judge_batch(lambda k: ["s"], limit=10))
    training.owner_label("a", "film")                    # reviewed meanwhile
    batches.results_ = {"n0": _succeeded()}               # the key says nfl
    asyncio.run(training.collect_batches(lambda k: ["s"]))
    label = [l for l in training.store().labels() if l["key"] == "a"][0]
    assert (label["node"], label["judge_model"]) == ("film", training.OWNER)


def test_a_wipe_forgets_sent_batches():
    training.store().put_batch("msgbatch_x", {"n0": {"key": "a", "query": "q"}})
    training.store().forget()
    assert training.store().pending_batches() == []


def test_the_collector_is_scheduled_and_on_the_roster():
    assert ("categorizer", "collect", "categorizer",
            "nando_judge_collect_seconds") in netenyahu.FREQUENT
    assert "collect" in roster.get("categorizer").tasks


def test_the_settings_agree_with_env_example():
    text = open(".env.example", encoding="utf-8").read()
    assert "NANDO_JUDGE_BATCH=1" in text and config.settings.nando_judge_batch is True
    assert "NANDO_JUDGE_COLLECT_SECONDS=300" in text
    assert config.settings.nando_judge_collect_seconds == 300.0


# ------------------------------------------------------------------ 3. the solid sources
def test_settle_keeps_the_filing_it_started_from(tree):
    import script_generator as sg

    notes = sg.ScriptNotes()
    notes.category_placement = categorizer.Placement(node="nfl", path=(), how="tile")
    notes.category_prior_seen = categorizer.Prior("nfl", "tile")
    notes.category = "nfl"
    categorizer.settle(notes, "how did the league do")
    assert notes.category_settled["prior"] == "nfl"
    assert notes.category_settled["prior_source"] == "tile"
    row = category_audit.note("how did the league do", "nfl", "tap",
                              nando=notes.category_settled, key="k1")
    stored = [r for r in topics.category_tree().audit_rows() if r["key"] == "k1"][0]
    assert stored["nando_prior_source"] == "tile" and row["nando_prior"] == "nfl"


def test_who_filed_it(tree):
    assert training.filed_by(_row("tap", "k", 1, nando_request_how="tile")) == "tile"
    # Narrowed by the words on a bank topic's branch: still the bank's filing.
    narrowed = _row("tap", "k", 1, nando_request_how="phrase", nando="nfl",
                    nando_prior="american football", nando_prior_source="bank")
    assert "american football" in categorizer._branch("nfl")
    assert training.filed_by(narrowed) == "bank"
    # A node off the source's branch is not the source's filing.
    off = dict(narrowed, nando="film", nando_request="film")
    assert training.filed_by(off) == "phrase"
    assert training.filed_by(_row("tap", "k", 1)) == "picked"
    assert training.filed_by(_row("tap", "k", 1, nando="", nando_request="")) == "nothing"


def test_the_report_grades_each_source_and_the_composer(tree):
    for key, how, composer in (("a", "tile", "film"), ("b", "picked", ""),
                               ("c", "bank", "nfl")):
        category_audit.note(f"question {key}", "", "tap", composer=composer,
                            nando={"request": "nfl", "request_how": how, "node": "nfl",
                                   "how": "confirmed"}, key=key)
        training.store().put_label(key, f"question {key}", "nfl", "high", "r", "m")
    report = training.report()
    assert report["by_source"]["tile"]["exact"] == 1
    assert report["by_source"]["bank"]["exact"] == 1
    assert report["by_source"]["picked"]["graded"] == 1
    assert report["composer"]["graded"] == 2 and report["composer"]["wrong"] == 1
    assert report["by_origin"]["tap"]["graded"] == 3


def _composer_rule(monkeypatch, signed: bool):
    text = "12. ... teach the story composer whose ..." if signed else "12. Let your answer key"
    monkeypatch.setattr(rulebooks, "check", lambda _b: rulebooks.Lock(ok=True, text=text))


def test_until_the_owner_signs_the_composer_is_taught_nothing(tree, monkeypatch):
    _composer_rule(monkeypatch, signed=False)
    category_audit.note("Lakers trade rumours", "", "trending", composer="film", key="z")
    training.store().put_label("z", "Lakers trade rumours", "nba", "high", "r", "m")
    assert training.composer_allowed()[0] is False
    assert training.composer_corrections() == []
    # The signed book really does lack it today, so this is what runs.
    signed = open("docs/agent_rules/nando.md", encoding="utf-8").read()
    if training.COMPOSER_RULE not in signed:
        monkeypatch.undo()
        assert training.composer_corrections() == []


def test_the_composers_sure_mistakes_become_its_corrections(tree, monkeypatch):
    _composer_rule(monkeypatch, signed=True)
    category_audit.note("Lakers trade rumours", "", "trending", composer="film", key="z")
    training.store().put_label("z", "Lakers trade rumours", "nba", "high", "r", "m")
    category_audit.note("unsure one", "", "trending", composer="film", key="y")
    training.store().put_label("y", "unsure one", "nba", "medium", "r", "m")
    got = training.composer_corrections()
    assert got == [{"question": "Lakers trade rumours", "said": "film", "node": "nba"}]


def test_the_composer_is_shown_its_corrections():
    signal = types.SimpleNamespace(subject="s", observation="o", anchors=(),
                                   domain=stories.ATTENTION, outcome_pending=False)
    plain = stories.build_composer_prompt([signal], "Tuesday")
    shown = stories.build_composer_prompt(
        [signal], "Tuesday", [{"question": "Lakers trade rumours", "said": "film",
                               "node": "nba"}])
    assert "turned out to be wrong" not in plain
    assert '"Lakers trade rumours": not film, but nba' in shown


def test_no_corrections_while_nando_is_off(monkeypatch):
    monkeypatch.setattr(stories, "settings",
                        dataclasses.replace(stories.settings, agents=False))
    assert asyncio.run(stories.composer_corrections()) == []


RULE_12_SIGNED = """12. Let your answer key - a stronger model chosen by the owner - read each
    finished episode once, in the background, to grade your filing. Its
    grades only teach you and measure you; they never change an episode's
    category without the owner's review (rule 10).
"""
RULE_12_PROPOSED = """12. Let your answer key - a stronger model chosen by the owner - read each
    finished episode once, in the background, to grade your filing, and,
    once only and at the owner's request, let a second answer key read at
    most 100 of those episodes again so the owner can compare the two. Its
    grades only teach you and measure you, and teach the story composer
    whose categories you inherit; they never change an episode's category
    without the owner's review (rule 10).
"""


def test_the_proposal_is_the_signed_book_with_only_rule_12_amended():
    import pathlib

    proposed = pathlib.Path("docs/agent_rule_proposals/nando.md")
    signed = pathlib.Path("docs/agent_rules/nando.md").read_text(encoding="utf-8")
    if training.COMPOSER_RULE in signed:
        # Signed: the proposal and the pending manifest go (README, §392).
        assert not proposed.exists()
        return
    assert proposed.read_text(encoding="utf-8") == signed.replace(RULE_12_SIGNED,
                                                                  RULE_12_PROPOSED)
    assert training.COMPOSER_RULE in RULE_12_PROPOSED
    assert training.CALIBRATION_RULE in RULE_12_PROPOSED   # §503
