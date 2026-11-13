"""Communities to reach (§575, `communities.py`, `/admin/groups`).

The rules pinned here: discovery is a web search, never a platform's own
pages signed in; a card says how to reach the admins through the
community's public channel; no phone number is ever kept; an email is kept
only when the community's own page publishes it as its contact; episodes
are hypothesised, never written; a run is capped; nothing sends anything.
The X agent it replaced is gone.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as appmod  # noqa: E402
import communities as C  # noqa: E402
import config  # noqa: E402


def result(url, title="", text=""):
    return SimpleNamespace(url=url, title=title, highlights=[text] if text else [])


def setting(monkeypatch, **values):
    """Settings are frozen: the run reads a copy with these changes."""
    import dataclasses

    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, **values))


# ------------------------------------------------------------- recognising one

@pytest.mark.parametrize("text, expected", [
    ("https://www.facebook.com/groups/eaglesfans/", ("facebook", "eaglesfans")),
    ("https://www.reddit.com/r/eagles/comments/abc", ("reddit", "eagles")),
    ("https://x.com/i/communities/1493446837214187523", ("x", "1493446837214187523")),
    ("join us at discord.gg/birdgang", ("discord", "birdgang")),
    ("https://disboard.org/server/123456789012345678", ("discord", "disboard:123456789012345678")),
    ("https://join.slack.com/t/austinfoodies/shared_invite/zt-1", ("slack", "austinfoodies")),
])
def test_each_platforms_community_is_recognised(text, expected):
    assert C.community_of(text)[0] == expected


def test_a_platforms_own_pages_are_not_communities():
    assert C.community_of("https://www.facebook.com/groups/feed/") == []
    assert C.community_of("https://www.reddit.com/r/popular") == []
    assert C.community_of("https://app.slack.com/client") == []


def test_the_contact_route_is_the_communitys_own_never_a_person():
    assert C.contact_url("facebook", "eaglesfans").endswith("/groups/eaglesfans/members/admins")
    assert C.contact_url("reddit", "eagles") == \
        "https://www.reddit.com/message/compose?to=%2Fr%2Feagles"
    assert C.contact_url("x", "149").endswith("/i/communities/149/moderators")
    for key, plat in C.PLATFORMS.items():
        assert plat.how, key


# ------------------------------------------------------------- what is kept

def test_a_phone_number_is_never_kept():
    text = "Call the admin on (215) 555-0134 or +44 20 7946 0958 for details."
    clean = C.strip_phones(text)
    assert "555" not in clean and "7946" not in clean
    lead = C.leads_from([result("https://www.facebook.com/groups/eaglesfans",
                                "Eagles Fans | Facebook", text)], "facebook", "Eagles")[0]
    assert "555" not in lead.description and "7946" not in lead.description


def test_an_email_is_kept_only_as_the_communitys_published_contact():
    page = ("Public group. 12K members. For partnerships contact birds@eaglesfans.org. "
            "Our member jane.doe@gmail.com posted the schedule.")
    assert C.public_contacts(page) == ["birds@eaglesfans.org"]


def test_a_contact_or_name_counts_only_on_the_communitys_own_page():
    own = result("https://www.facebook.com/groups/eaglesfans", "Eagles Fans | Facebook",
                 "Admin: Maria Lopez. Contact the admins at hello@eaglesfans.org. 12K members")
    other = result("https://someblog.com/best-groups", "Best Eagles groups",
                   "Try facebook.com/groups/birdgang - admin: Tom Reed, contact tom@x.org")
    leads = {l.ident: l for l in C.leads_from([own, other], "facebook", "Eagles")}
    assert leads["eaglesfans"].public_contact == ["hello@eaglesfans.org"]
    assert leads["eaglesfans"].admin_hint == ["Maria Lopez"]
    assert leads["eaglesfans"].members == 12000
    assert leads["birdgang"].public_contact == [] and leads["birdgang"].admin_hint == []


def test_a_search_on_one_platform_keeps_only_that_platforms_communities():
    found = C.leads_from([result("https://www.reddit.com/r/eagles", "r/eagles")],
                         "facebook", "Eagles")
    assert found == []


def test_screening():
    lead = C.Lead("facebook", "a", "Grief support for widows", "u", "c", "loss")
    assert "grief" in C.screen(lead)
    small = C.Lead("reddit", "b", "Tiny", "u", "c", "x", members=40)
    assert "too few" in C.screen(small)
    fine = C.Lead("reddit", "c", "Eagles", "u", "c", "Eagles", members=0)
    assert C.screen(fine) == ""


# ------------------------------------------------------------- the read

def test_with_no_model_the_pitches_are_composed_in_code(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    lead = C.Lead("reddit", "eagles", "Eagles", "u", "c", "Philadelphia Eagles")
    out, dollars = asyncio.run(C.read(lead))
    assert out.drafted_by == "code" and dollars == 0.0
    assert len(out.pitches) == C.PITCHES and out.message
    assert "Eagles" in out.message and len(out.message) <= C.MESSAGE_CHARS


class _Client:
    def __init__(self, body):
        self.body = body
        self.messages = self

    async def create(self, **kw):
        self.kw = kw
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(self.body))],
                               usage=None)


def test_a_figure_the_read_was_not_given_sends_the_message_back_to_codes():
    lead = C.Lead("facebook", "g", "Eagles Fans", "u", "c", "Eagles")
    client = _Client({"fit": 5, "fit_why": "fans", "message": "Your 40,000 members will love it.",
                      "pitches": [{"title": "Why the run defense broke", "angle": "a", "why": "w"}]})
    out, _ = asyncio.run(C.read(lead, client))
    assert out.fit == 5 and out.pitches[0]["title"] == "Why the run defense broke"
    assert "40,000" not in out.message
    # The community's words go inside the request's material, never loose.
    assert "<evidence>" in client.kw["messages"][0]["content"]


# ------------------------------------------------------------- a run

def _run(tmp_path, monkeypatch, results, fit=4, **kw):
    held = C.Store(str(tmp_path / "c.db"))
    searched = []

    async def searcher(query, platform):
        searched.append((query, platform))
        return results.get(platform, []), 0.001, "parallel"

    async def reader(lead):
        lead.fit, lead.pitches, lead.message = fit, C.code_pitches(lead), "hi"
        return lead, 0.0

    report = asyncio.run(C.run(None, kw.pop("subjects", ["Eagles"]),
                               kw.pop("platforms", ["facebook", "reddit"]),
                               searcher=searcher, reader=reader, held=held, **kw))
    return held, report, searched


def test_a_run_leaves_cards_and_records_its_searches(tmp_path, monkeypatch):
    results = {"facebook": [result("https://www.facebook.com/groups/eaglesfans",
                                   "Eagles Fans | Facebook", "Eagles fans. 5K members")],
               "reddit": [result("https://www.reddit.com/r/eagles", "r/eagles",
                                 "Philadelphia Eagles. 900K members")]}
    held, report, searched = _run(tmp_path, monkeypatch, results)
    assert report["searched"] == 2 and report["kept"] == 2
    assert {q for q, _ in searched} == {"Eagles Facebook group", "Eagles subreddit"}
    leads = held.all()
    assert {l["id"] for l in leads} == {"facebook:eaglesfans", "reddit:eagles"}
    assert all(l["status"] == C.NEW and l["how"] and l["reason"] for l in leads)
    # Found again, a lead is not a second card.
    _held, again, _ = _run(tmp_path, monkeypatch, results)
    assert again["kept"] == 0


def test_a_poor_fit_is_set_aside_for_audit_never_a_card(tmp_path, monkeypatch):
    results = {"reddit": [result("https://www.reddit.com/r/eagles", "r/eagles", "x")]}
    held, report, _ = _run(tmp_path, monkeypatch, results, fit=2, platforms=["reddit"])
    assert report["set_aside"] == 1 and held.all() == []
    assert [l["id"] for l in held.all(C.SET_ASIDE)] == ["reddit:eagles"]


def test_the_daily_ceiling_stops_a_run(tmp_path, monkeypatch):
    setting(monkeypatch, communities_daily_searches=3, communities_max_searches=20)
    held, report, searched = _run(tmp_path, monkeypatch, {}, subjects=["A", "B"])
    assert len(searched) == 3 and "left out" in report["stopped"]
    _held, report, searched = _run(tmp_path, monkeypatch, {})
    assert searched == [] and "used up" in report["stopped"]


def test_a_failed_search_is_recorded_and_the_run_goes_on(tmp_path):
    held = C.Store(str(tmp_path / "c.db"))

    async def searcher(query, platform):
        raise RuntimeError("down")

    report = asyncio.run(C.run(None, ["Eagles"], ["x"], searcher=searcher, held=held))
    assert report["failed"] and report["kept"] == 0


def test_a_search_is_held_to_the_platforms_domains(monkeypatch):
    import research

    asked = {}
    monkeypatch.setattr(C, "backends", lambda: ["perplexity"])
    monkeypatch.setattr(research, "_search_perplexity",
                        lambda q, n, include_domains=(): asked.update(q=q, d=include_domains) or ([], 0.001))
    C._search_blocking("Eagles subreddit", "reddit")
    assert asked["d"] == ("reddit.com",)


def test_no_backend_says_so():
    ok, why = C.available()
    assert ok is False and "PARALLEL_API_KEY" in why


# ------------------------------------------------------------- the page

@pytest.fixture
def admin(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_require_admin", lambda request: None)
    monkeypatch.setattr(appmod, "_admin_configured", lambda: True)
    held = C.Store(str(tmp_path / "c.db"))
    C.reset(held)
    lead = C.Lead("reddit", "eagles", "Eagles", C.community_url("reddit", "eagles"),
                  C.contact_url("reddit", "eagles"), "Eagles", message="hi",
                  pitches=C.code_pitches(C.Lead("reddit", "e", "E", "u", "c", "Eagles")))
    held.add(lead, 1.0)
    return TestClient(appmod.app), held


def test_the_page_and_its_list(admin):
    client, _held = admin
    assert client.get("/admin/groups").status_code == 200
    body = client.get("/api/admin/groups").json()
    assert [l["id"] for l in body["leads"]] == ["reddit:eagles"]
    assert set(body["health"]["platforms"]) == {"facebook", "reddit", "x", "discord", "slack"}


def test_moving_a_lead_keeps_notes_without_phone_numbers(admin):
    client, held = admin
    r = client.post("/api/admin/groups/move", json={
        "id": "reddit:eagles", "status": "contacted",
        "notes": "Mod said text him at 215-555-0134", "handle": "u/birdmod"})
    assert r.status_code == 200, r.text
    row = held.get("reddit:eagles")
    assert row["status"] == "contacted" and row["handle"] == "u/birdmod"
    assert "555" not in row["notes"]


def test_a_run_needs_a_search_backend(admin):
    client, _held = admin
    r = client.post("/api/admin/groups/run", json={"subjects": ["Eagles"], "platforms": ["reddit"]})
    assert r.status_code == 409 and "search" in r.json()["error"].lower()


def test_the_x_agent_is_gone():
    assert not (ROOT / "x_threads.py").exists()
    assert not (ROOT / "outreach_intelligence.py").exists()
    paths = {getattr(r, "path", "") for r in appmod.app.routes}
    assert not any(p.startswith("/api/admin/x") or p == "/admin/x" for p in paths)
    assert "/admin/groups" in paths


def test_nothing_here_can_send_or_sign_in():
    src = (ROOT / "communities.py").read_text()
    for word in ("graph.facebook.com", "oauth.reddit.com", "api.x.com", "discord.com/api",
                 "slack.com/api", ".post(", "smtp"):
        assert word not in src, word
