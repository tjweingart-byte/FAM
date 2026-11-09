"""§503: the answer key on Sonnet, ten a night and only where the filers
disagree; the owner's free review queue on /admin; and the one-time check
against Opus, run once only and only once the owner signs rule 12's
amendment."""
from __future__ import annotations

import asyncio
import dataclasses

import pytest
from fastapi.testclient import TestClient

import categories as C
import category_audit
import config
import topics
from agents import models, names, rulebooks, tasks, training

ADMIN = {"X-Admin-Token": "sekrit"}


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    yield t


def _signed(monkeypatch, *words):
    text = "12. Let your answer key ... " + " ... ".join(words)
    monkeypatch.setattr(rulebooks, "check", lambda _b: rulebooks.Lock(ok=True, text=text))


def _note(key, query, nando="nfl", writer_words="nfl", composer=None, origin="tap"):
    nando_row = {"request": nando, "request_how": "picked", "node": nando,
                 "how": "confirmed"} if nando else None
    category_audit.note(query, writer_words, origin, composer=composer or "",
                        nando=nando_row, key=key)


# ------------------------------------------------------------------ settings
def test_the_owners_settings():
    s = config.settings
    assert s.categorizer_judge_model == "anthropic:claude-sonnet-5-5"
    assert s.nando_judge_per_run == 10
    assert s.nando_judge_disagreements_only is True
    assert s.categorizer_calibration_model == "anthropic:claude-opus-5-5"
    assert s.nando_calibration_episodes == 100
    assert "categorizer_calibration_model" in names.AGENTS["categorizer"][3]
    text = open(".env.example", encoding="utf-8").read()
    for line in ("CATEGORIZER_JUDGE_MODEL=anthropic:claude-sonnet-5-5",
                 "CATEGORIZER_CALIBRATION_MODEL=anthropic:claude-opus-5-5",
                 "NANDO_JUDGE_PER_RUN=10", "NANDO_JUDGE_DISAGREEMENTS_ONLY=1",
                 "NANDO_CALIBRATION_EPISODES=100"):
        assert line in text, line


# ------------------------------------------------------------------ disagreements only
def test_three_filers_on_one_branch_agree(tree):
    row = {"nando": "nfl", "writer": "nfl", "composer": "american football",
           "nando_how": "confirmed"}
    assert training.agreed(row)
    assert not training.agreed(dict(row, writer="film"))
    assert not training.agreed(dict(row, composer="film"))
    assert not training.agreed(dict(row, nando="", nando_request=""))
    assert not training.agreed(dict(row, writer=""))          # nothing checked it
    assert not training.agreed(dict(row, nando_how="refiled"))


def test_the_key_is_spent_only_where_they_disagree(tree, monkeypatch):
    monkeypatch.setattr(training, "allowed", lambda: (True, ""))
    monkeypatch.setattr(config, "settings",
                        dataclasses.replace(config.settings, nando_judge_batch=False))
    _note("same", "how did the NFL week go")                      # all on nfl
    _note("differ", "the Eagles film night", writer_words="film")
    asked = []

    async def ask(request):
        asked.append(dict(request.material)["request"])
        return models.AgentResult(status="ok", agent="categorizer", task="judge",
                                  value=training.JudgeLabel(node="nfl", confidence="high",
                                                            reason="r"), model="m")
    monkeypatch.setattr(models, "ask", ask)
    asyncio.run(training.judge_batch(lambda k: ["s"]))
    assert asked == ["the Eagles film night"]


# ------------------------------------------------------------------ the owner's review
def test_the_queue_puts_disagreements_first_and_skips_what_is_reviewed(tree):
    _note("same", "how did the NFL week go")
    _note("differ", "the Eagles film night", writer_words="film")
    _note("gone", "a script nobody kept", writer_words="film")
    scripts = {"same": ["one", "two"], "differ": ["three"]}
    queue = training.review_queue(lambda k: scripts.get(k, []))
    assert [r["key"] for r in queue] == ["differ", "same"]
    assert queue[0]["agreed"] is False and queue[0]["opening"] == "three"
    training.owner_label("differ", "film")
    assert [r["key"] for r in training.review_queue(lambda k: scripts.get(k, []))] == ["same"]


def test_a_review_is_the_grade_ahead_of_any_model_and_is_never_graded_again(tree,
                                                                           monkeypatch):
    _note("k", "the Eagles film night", writer_words="film")
    training.store().put_label("k", "the Eagles film night", "nfl", "high", "r",
                               "claude-sonnet-5-5")
    out = training.owner_label("k", "film")
    assert out["key_node"] == "nfl" and out["key_model"] == "claude-sonnet-5-5"
    label = [l for l in training.store().labels() if l["key"] == "k"][0]
    assert (label["node"], label["confidence"], label["judge_model"]) == \
        ("film", "high", training.OWNER)
    assert "k" in training.store().asked(["k"])
    # It measures the key, and teaches Nando as one of his mistakes.
    agree = training.owner_agreement()
    assert agree["compared"] == 1 and agree["wrong"] == 1 and agree["agree_rate"] == 0.0
    assert {"question": "the Eagles film night", "node": "film",
            "source": "mistake"} in training.mistakes()


def test_none_is_a_review_and_a_node_the_tree_lacks_is_refused(tree):
    _note("k", "kings and queens of England")
    assert training.owner_label("k", "")["node"] == ""
    with pytest.raises(ValueError):
        training.owner_label("k", "not a node at all")
    with pytest.raises(ValueError):
        training.owner_label("no such key", "nfl")


def test_a_review_without_a_key_grade_is_not_counted_as_agreement(tree):
    _note("k", "how did the NFL week go")
    training.owner_label("k", "nfl")
    assert training.owner_agreement()["compared"] == 0


# ------------------------------------------------------------------ the one-time check
def test_the_check_waits_for_the_owners_signature(tree, monkeypatch):
    _signed(monkeypatch)
    ok, why = training.calibration_allowed()
    assert not ok and "amendment" in why
    out = asyncio.run(training.calibrate(lambda k: ["s"]))
    assert out["refused"] and training.store().calibration() == []


def test_the_check_runs_once_and_reuses_the_nightly_grade(tree, monkeypatch):
    _signed(monkeypatch, training.CALIBRATION_RULE)
    _note("graded", "how did the NFL week go")
    _note("fresh", "the Eagles film night", writer_words="film")
    training.store().put_label("graded", "how did the NFL week go", "nfl", "high", "r",
                               "claude-sonnet-5-5")
    calls = []

    async def judge(key, query, sentences, listing, model_setting="categorizer_judge_model"):
        calls.append((key, model_setting))
        node = "film" if model_setting == "categorizer_calibration_model" else "nfl"
        return {"node": node, "confidence": "high", "reason": "r",
                "judge_model": "opus" if node == "film" else "sonnet"}
    monkeypatch.setattr(training, "judge", judge)
    out = asyncio.run(training.calibrate(lambda k: ["s"], limit=10))
    assert out["compared"] == 2
    # The graded episode is read once more, by the second key only.
    assert ("graded", "categorizer_judge_model") not in calls
    assert ("graded", "categorizer_calibration_model") in calls
    assert ("fresh", "categorizer_judge_model") in calls
    report = training.calibration_report()
    assert report["ran"] and report["compared"] == 2 and report["wrong"] == 2
    # Once only.
    assert training.calibration_allowed()[0] is False
    calls.clear()
    assert asyncio.run(training.calibrate(lambda k: ["s"]))["refused"] and calls == []


def test_the_second_key_is_nandos_own_setting():
    request = training._judge_request("q", ["s"], "", "categorizer_calibration_model")
    assert models._own_settings(request) == ""
    assert request.task == "calibrate"


def test_a_wipe_forgets_reviews_and_the_check(tree):
    _note("k", "how did the NFL week go")
    training.owner_label("k", "nfl")
    training.store().put_calibration("k", "q", "nfl", "a", "nfl", "b")
    training.store().forget()
    assert training.store().owner_reviews() == [] and training.store().calibration() == []


# ------------------------------------------------------------------ /admin
@pytest.fixture
def client(monkeypatch):
    import app as appmod

    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "sekrit")
    monkeypatch.setattr(appmod, "_script_for_review", lambda k: ["the opening"])
    return TestClient(appmod.app)


def test_the_page_is_for_admins_only(client):
    assert client.get("/api/admin/categories").status_code in (401, 403, 404)
    assert client.get("/api/admin/categories/queue").status_code in (401, 403, 404)


def test_the_owner_reviews_from_admin(client, tree):
    _note("k", "the Eagles film night", writer_words="film")
    queue = client.get("/api/admin/categories/queue", headers=ADMIN).json()["episodes"]
    assert [r["key"] for r in queue] == ["k"]
    r = client.post("/api/admin/categories/label", headers=ADMIN,
                    json={"key": "k", "node": "film"})
    assert r.status_code == 200 and r.json()["node"] == "film"
    bad = client.post("/api/admin/categories/label", headers=ADMIN,
                      json={"key": "k", "node": "not a node"})
    assert bad.status_code == 422
    panel = client.get("/api/admin/categories", headers=ADMIN).json()
    assert panel["settings"]["judge_model"] == "anthropic:claude-sonnet-5-5"
    assert "film" in panel["nodes"] and panel["calibration"]["allowed"] is False


def test_the_check_is_refused_until_signed(client):
    r = client.post("/api/admin/categories/calibrate", headers=ADMIN, json={})
    assert r.status_code == 409


def test_the_check_is_queued_once(client, tree, monkeypatch):
    import app as appmod

    _signed(monkeypatch, training.CALIBRATION_RULE)
    monkeypatch.setattr(appmod, "settings",
                        dataclasses.replace(appmod.settings, agents=True, categorizer=True))
    first = client.post("/api/admin/categories/calibrate", headers=ADMIN, json={}).json()
    again = client.post("/api/admin/categories/calibrate", headers=ADMIN, json={}).json()
    assert first["queued"] is True and again["queued"] is False
    assert any(t.startswith("categorizer:calibrate:once") for t in tasks.store().due(
        10, kinds=["categorizer:calibrate"]))


def test_the_page_draws_and_saves_a_review(client, tree, tmp_path):
    import json
    import pathlib

    playwright = pytest.importorskip("playwright.sync_api")
    root = pathlib.Path(__file__).resolve().parent.parent
    _note("k", "the Eagles film night", writer_words="film")
    answers = {"/api/admin/categories": client.get("/api/admin/categories", headers=ADMIN).json(),
               "/api/admin/categories/queue":
                   client.get("/api/admin/categories/queue", headers=ADMIN).json(),
               "/api/admin/schema": {"stores": []}}
    html = (root / "admin_ui" / "tracker.html").read_text()
    posted, errors = [], []
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"no browser: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.on("pageerror", lambda e: errors.append(str(e)))

        def route(r):
            path = "/" + r.request.url.split("/", 3)[3].split("?")[0].split("#")[0]
            if path == "/admin":
                r.fulfill(content_type="text/html; charset=utf-8", body=html)
            elif path == "/api/admin/categories/label":
                posted.append(json.loads(r.request.post_data or "{}"))
                r.fulfill(content_type="application/json", body='{"ok": true}')
            elif path in answers:
                r.fulfill(content_type="application/json", body=json.dumps(answers[path]))
            else:
                r.fulfill(content_type="application/json", body="{}")

        page.route("**/*", route)
        page.goto("http://fam.test/admin#categories")
        page.fill("#adminEmail", "owner@example.com")
        page.fill("#adminPassword", "x")
        page.click("#loginBtn")
        page.wait_for_selector("#catQueue [data-catsave]")
        assert "Sonnet 5.5" in page.inner_text("#catKpis")
        assert "the Eagles film night" in page.inner_text("#catQueue")
        assert page.is_hidden("#catCalibrate")           # unsigned: no button
        page.fill('input[data-catkey="k"]', "film")
        page.click('[data-catsave="k"]')
        page.wait_for_selector('[data-catsave="k"]:disabled')
        page.screenshot(path=str(tmp_path / "categories.png"), full_page=True)
        browser.close()
    assert posted == [{"key": "k", "node": "film"}] and errors == []
