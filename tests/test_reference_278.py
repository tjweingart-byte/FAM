"""The reference look: Wikipedia and Wikidata beside the search (§278).

Every request is stubbed at `wikimedia._transport` - the one function that
sends - so nothing here reaches Wikimedia, and the counting, backoff and
caching around it are the real code.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
import time
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wikimedia  # noqa: E402

TODAY = date(2026, 10, 15)


def _time(value, precision=11):
    return {"snaktype": "value", "datavalue": {
        "type": "time", "value": {"time": value, "precision": precision}}}


def _item(qid):
    return {"snaktype": "value", "datavalue": {
        "type": "wikibase-entityid", "value": {"id": qid}}}


def _claim(snak, rank="normal", qualifiers=None):
    claim = {"mainsnak": snak, "rank": rank}
    if qualifiers:
        claim["qualifiers"] = qualifiers
    return claim


PLAYER = {
    "labels": {"en": {"value": "Jalen Hurts"}},
    "sitelinks": {"enwiki": {"title": "Jalen Hurts"}},
    "claims": {
        "P31": [_claim(_item("Q5"))],
        "P569": [_claim(_time("+1998-08-07T00:00:00Z"))],
        "P27": [_claim(_item("Q30"))],
        "P106": [_claim(_item("Q19204627"))],
        "P54": [
            _claim(_item("Q219714"), qualifiers={"P580": [_time("+2020-01-01T00:00:00Z", 9)]}),
            _claim(_item("Q2367185"), qualifiers={"P582": [_time("+2019-01-01T00:00:00Z", 9)]}),
            _claim(_item("Q999"), rank="deprecated"),
        ],
    },
}
LABELS = {"Q30": "United States", "Q19204627": "American football player",
          "Q219714": "Philadelphia Eagles", "Q2367185": "Oklahoma Sooners",
          "Q999": "Deprecated Team"}
SUMMARY = {"type": "standard", "title": "Jalen Hurts",
           "description": "American football player (born 1998)",
           "extract": "Jalen Alexander Hurts is an American professional football "
                      "quarterback. He played college football at Alabama and Oklahoma.",
           "timestamp": "2026-10-01T12:00:00Z", "wikibase_item": "Q1",
           "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Jalen_Hurts"}}}
ENTERPRISE = [{"name": "Jalen Hurts", "abstract": "Jalen Hurts is a quarterback "
               "for the Philadelphia Eagles. Enterprise wrote this.",
               "description": "American football player",
               "url": "https://en.wikipedia.org/wiki/Jalen_Hurts",
               "date_modified": "2026-10-10T08:00:00Z",
               "main_entity": {"identifier": "Q1"},
               "infoboxes": [{"name": "Infobox", "type": "infobox", "has_parts": [
                   {"name": "Section", "type": "section", "has_parts": [
                       {"name": "Height", "type": "field", "value": "6 ft 1 in"}]}]}]}]


class Fake:
    """A stand-in for every Wikimedia endpoint, recording what was asked."""

    def __init__(self):
        self.calls = []
        self.search = [{"id": "Q1", "label": "Jalen Hurts",
                        "description": "American football quarterback"}]
        self.entities = {"Q1": PLAYER}
        self.summary = SUMMARY
        self.status = {}
        self.delay = 0.0

    def __call__(self, method, url, *, params=None, body=None, headers=None,
                 timeout=2.0):
        self.calls.append((method, url, dict(params or {}), body, dict(headers or {})))
        if self.delay:
            time.sleep(self.delay)
        for marker, status in self.status.items():
            if marker in url:
                return status, {"Retry-After": "120"}, {}
        if url == wikimedia.WIKIDATA_API:
            if params["action"] == "wbsearchentities":
                return 200, {}, {"search": self.search}
            ids = params["ids"].split("|")
            if params.get("props") == "labels":
                return 200, {}, {"entities": {
                    i: {"labels": {"en": {"value": LABELS[i]}}} for i in ids if i in LABELS}}
            return 200, {}, {"entities": {i: self.entities[i] for i in ids
                                          if i in self.entities}}
        if url.startswith(wikimedia.SUMMARY_URL):
            return 200, {}, self.summary
        if url == wikimedia.ENTERPRISE_LOGIN:
            return 200, {}, {"access_token": "access-1", "refresh_token": "refresh-1"}
        if url == wikimedia.ENTERPRISE_REFRESH:
            return 200, {}, {"access_token": "access-2"}
        if url.startswith(wikimedia.ENTERPRISE_ARTICLE):
            return 200, {}, ENTERPRISE
        raise AssertionError(f"unexpected request {url}")

    def asked(self, fragment):
        return [c for c in self.calls if fragment in c[1]]


@pytest.fixture
def fake(monkeypatch):
    stub = Fake()
    monkeypatch.setattr(wikimedia, "_transport", stub)
    monkeypatch.setattr(wikimedia, "ENABLED_OVERRIDE", True)
    monkeypatch.setattr(wikimedia, "settings", dataclasses.replace(
        wikimedia.settings, wikimedia_enterprise_username="",
        wikimedia_enterprise_password="", wikimedia_monthly_requests=45000))
    wikimedia.reset_store()
    return stub


def _enterprise(monkeypatch, cap=45000):
    monkeypatch.setattr(wikimedia, "settings", dataclasses.replace(
        wikimedia.settings, wikimedia_enterprise_username="fam",
        wikimedia_enterprise_password="secret-pw", wikimedia_monthly_requests=cap))


# --------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------
def test_a_name_is_accepted_only_on_an_exact_label_or_alias():
    hits = [{"id": "Q1", "label": "Jalen Hurts", "description": "quarterback"},
            {"id": "Q2", "label": "Jalen Hurts Jr", "description": "someone else"}]
    assert wikimedia.pick(hits, "jalen hurts") == ("Q1", False)
    assert wikimedia.pick(hits, "Jalen") == ("", False), "a prefix is not a match"
    alias = [{"id": "Q7", "label": "Kylian Mbappé Lottin",
              "match": {"type": "alias", "text": "Kylian Mbappe"}}]
    assert wikimedia.pick(alias, "Kylian Mbappé!")[0] == "Q7", (
        "accents and punctuation are ignored, and the alias counts")


def test_two_exact_matches_are_no_answer_unless_the_question_tells_them_apart():
    hits = [{"id": "Q10", "label": "Michael Jordan",
             "description": "American basketball player"},
            {"id": "Q20", "label": "Michael Jordan",
             "description": "American actor and film producer"}]
    assert wikimedia.pick(hits, "Michael Jordan") == ("", True)
    assert wikimedia.pick(hits, "Michael Jordan",
                          "michael jordan basketball career") == ("Q10", True)
    assert wikimedia.pick(hits, "Michael Jordan", "michael jordan american") == ("", True), (
        "a word both descriptions share decides nothing")


# --------------------------------------------------------------------------
# Facts, computed in code
# --------------------------------------------------------------------------
def test_age_is_computed_from_the_date_and_respects_precision():
    born = {"year": 1998, "month": 8, "day": 7, "precision": 11}
    assert wikimedia.age(born, TODAY) == 28
    assert wikimedia.age(born, date(2026, 8, 6)) == 27, "the day before the birthday"
    assert wikimedia.age({"year": 1998, "month": 1, "day": 1, "precision": 9}, TODAY) is None
    lines = wikimedia.sentences("Someone", {"human": True, "born": {
        "year": 1998, "month": 1, "day": 1, "precision": 9}}, {}, TODAY)
    assert lines == ["Wikidata lists Someone as born 1998."]


def test_current_positions_are_those_with_no_end_date_and_deprecated_are_skipped():
    facts, ids = wikimedia.raw_facts(PLAYER)
    assert [t["id"] for t in facts["teams"]] == ["Q219714"]
    assert "Q999" not in ids and "Q2367185" not in ids
    lines = wikimedia.sentences("Jalen Hurts", facts, LABELS, TODAY)
    assert ("Wikidata lists Jalen Hurts as born August 7, 1998, which makes them 28 "
            "as of October 15, 2026.") in lines
    team = [line for line in lines if "member of" in line][0]
    assert "currently a member of Philadelphia Eagles (since 2020)" in team
    assert "Oklahoma" not in " ".join(lines)
    assert all(line.startswith("Wikidata lists") for line in lines)


def test_an_organisation_gets_its_founding_and_current_chief_executive():
    org = {"claims": {
        "P31": [_claim(_item("Q4830453"))],
        "P571": [_claim(_time("+1993-04-05T00:00:00Z"))],
        "P169": [_claim(_item("Q50"), qualifiers={"P580": [_time("+1993-04-05T00:00:00Z")]}),
                 _claim(_item("Q51"), qualifiers={"P582": [_time("+2000-01-01T00:00:00Z")]})],
        "P112": [_claim(_item("Q50"))],
        "P159": [_claim(_item("Q60"))]}}
    facts, _ids = wikimedia.raw_facts(org)
    lines = wikimedia.sentences("Nvidia", facts, {"Q50": "Jensen Huang",
                                                  "Q51": "Old Boss",
                                                  "Q60": "Santa Clara"}, TODAY)
    assert "Wikidata lists Nvidia as founded April 5, 1993." in lines
    assert ("Wikidata lists Nvidia's chief executive as Jensen Huang "
            "(since April 5, 1993), with no end date recorded.") in lines
    assert "Old Boss" not in " ".join(lines)


# --------------------------------------------------------------------------
# The two routes
# --------------------------------------------------------------------------
def test_the_public_summary_with_no_credentials(fake):
    ref = wikimedia.lookup("Jalen Hurts", today=TODAY)
    assert ref.via == "public" and ref.qid == "Q1"
    assert ref.title == "Jalen Hurts"
    assert ref.url == "https://en.wikipedia.org/wiki/Jalen_Hurts"
    assert ref.summary.startswith("Jalen Alexander Hurts")
    assert ref.revised.year == 2026
    assert not fake.asked("enterprise")
    for _m, _u, _p, _b, headers in fake.calls:
        assert "FAM" in headers["User-Agent"] and "@" in headers["User-Agent"], (
            "every request names who is asking, with a contact")
    labels = [c for c in fake.calls if c[2].get("props") == "labels"]
    assert len(labels) == 1, "referenced items' labels in one batched call"


def test_enterprise_is_preferred_parsed_and_counted(fake, monkeypatch):
    _enterprise(monkeypatch)
    ref = wikimedia.lookup("Jalen Hurts", today=TODAY)
    assert ref.via == "enterprise"
    assert ref.summary.endswith("Enterprise wrote this.")
    assert "6 ft 1 in" not in ref.summary + " ".join(ref.facts), "infoboxes are not used"
    article = fake.asked("structured-contents")[0]
    assert article[4]["Authorization"] == "Bearer access-1"
    assert article[3] == {"filters": [{"field": "is_part_of.identifier",
                                       "value": "enwiki"}], "limit": 1}
    login = fake.asked("login")[0]
    assert login[3] == {"username": "fam", "password": "secret-pw"}
    # Login and article, both counted - and the count survives a new store.
    wikimedia._STORE[0] = None
    assert wikimedia.store().count(wikimedia._month()) == 2
    assert not fake.asked(wikimedia.SUMMARY_URL)


def test_enterprise_answering_about_another_item_is_not_used():
    reply = [dict(ENTERPRISE[0], main_entity={"identifier": "Q2"})]
    assert wikimedia.parse_enterprise(reply, "Q1") is None
    assert wikimedia.parse_public(dict(SUMMARY, type="disambiguation"), "Q1") is None


def test_a_401_logs_in_again_once(fake, monkeypatch):
    _enterprise(monkeypatch)
    answers = iter([(401, {}, {}), (200, {}, ENTERPRISE)])
    real = fake.__call__

    def flaky(method, url, **kwargs):
        if url.startswith(wikimedia.ENTERPRISE_ARTICLE):
            fake.calls.append((method, url, {}, kwargs.get("body"), kwargs.get("headers")))
            return next(answers)
        return real(method, url, **kwargs)

    monkeypatch.setattr(wikimedia, "_transport", flaky)
    assert wikimedia.lookup("Jalen Hurts", today=TODAY).via == "enterprise"
    # A fresh token from the refresh token, and the article asked once more.
    assert len(fake.asked("login")) == 1 and len(fake.asked("token-refresh")) == 1
    articles = fake.asked("structured-contents")
    assert [a[4]["Authorization"] for a in articles] == ["Bearer access-1",
                                                         "Bearer access-2"]


def test_the_public_route_takes_over_when_the_month_is_spent(fake, monkeypatch):
    _enterprise(monkeypatch, cap=5)
    for _ in range(5):
        wikimedia.store().spend(wikimedia._month())
    ref = wikimedia.lookup("Jalen Hurts", today=TODAY)
    assert ref.via == "public"
    assert not fake.asked("enterprise"), "nothing is sent past the cap"
    assert wikimedia.report()["route"] == "public"


def test_a_cached_answer_makes_no_request_and_no_match_is_cached_too(fake):
    first = wikimedia.lookup("Jalen Hurts", today=TODAY)
    asked = len(fake.calls)
    again = wikimedia.lookup("jalen hurts", today=TODAY)
    assert len(fake.calls) == asked
    assert again.facts == first.facts
    fake.search = []
    assert wikimedia.lookup("Nobody Atall") is None
    asked = len(fake.calls)
    assert wikimedia.lookup("Nobody Atall") is None
    assert len(fake.calls) == asked


def test_the_age_is_recomputed_on_a_cached_read(fake):
    wikimedia.lookup("Jalen Hurts", today=TODAY)
    later = wikimedia.lookup("Jalen Hurts", today=date(2027, 9, 1))
    assert any("which makes them 29" in line for line in later.facts)


def test_a_429_backs_off_without_retrying(fake):
    fake.status = {"wikidata.org": 429}
    assert wikimedia.lookup("Jalen Hurts") is None
    sent = len(fake.calls)
    assert sent == 1
    fake.status = {}
    assert wikimedia.lookup("Someone Else") is None
    assert len(fake.calls) == sent, "nothing is sent while backing off"
    assert wikimedia.report()["backing_off_seconds"]["wikidata"] > 0


def test_every_request_is_counted_against_its_provider(fake):
    import provider_usage

    wikimedia.lookup("Jalen Hurts", today=TODAY)
    provider_usage.flush()
    seen = provider_usage.store().days(1)
    assert seen["wikidata"][wikimedia.time.strftime("%Y-%m-%d", time.gmtime())][0] == 3
    assert "wikipedia" in seen


def test_off_means_no_request(fake, monkeypatch):
    monkeypatch.setattr(wikimedia, "ENABLED_OVERRIDE", False)
    assert wikimedia.lookup("Jalen Hurts") is None
    assert fake.calls == []


# --------------------------------------------------------------------------
# Research
# --------------------------------------------------------------------------
def _brief(**fields):
    import episode_intelligence as ei

    base = {"query": "how is jalen hurts playing", "subject": "Jalen Hurts",
            "live_entity": "Jalen Hurts"}
    base.update(fields)
    return ei.Brief(**base)


def test_the_reference_name_comes_from_the_brief_and_never_where_it_cannot_help(fake):
    import research

    assert research.reference_name_for(_brief()) == "Jalen Hurts"
    assert research.reference_name_for(_brief(live_entity="")) == "Jalen Hurts"
    assert research.reference_name_for(_brief(
        live_entity="", subject="how the eagles offense changed this year")) == ""
    assert research.reference_name_for(_brief(
        live_entity="Kansas City Chiefs, Buffalo Bills")) == ""
    assert research.reference_name_for(None) == "", "no brief (EI off): none"
    assert research.reference_name_for(_brief(outcome_dependent=True)) == ""
    assert research.reference_name_for(_brief(live_domain="weather")) == ""
    assert research.reference_name_for(_brief(place="Exeter, NH, US")) == ""


def _result(url, title="t"):
    import research

    return research.SearchResult(url=url, title=title, published_date="2026-10-14",
                                 highlights=[f"passage {url.rsplit('/', 1)[-1]}"])


@pytest.fixture
def parallel(monkeypatch):
    import research

    state = {"results": [_result("https://reuters.com/a-1"),
                         _result("https://apnews.com/b-2")], "delay": 0.2}

    def search(query, num_results, recency_days=0, include_domains=(),
               objective="", extra_queries=(), hints=None):
        time.sleep(state["delay"])
        return list(state["results"]), 0.001

    monkeypatch.setattr(research, "_search_parallel", search)
    # The generator reads its own `settings`, whose default is Perplexity
    # since §344; the same stand-in answers for both indexes.
    monkeypatch.setattr(research, "_search_perplexity", search)
    monkeypatch.setattr(research, "settings", dataclasses.replace(
        research.settings, research_backend="parallel", gdelt_cross_check=False,
        research_retry=False, wikipedia_grace_seconds=0.3))
    return state


def test_research_appends_the_reference_as_one_extra_source(fake, parallel):
    import research

    packet = asyncio.run(research.retrieve(
        "jalen hurts", brief=_brief(), reference_name="Jalen Hurts"))
    context = packet.context
    assert context.index("SOURCE 1") < context.index("SOURCE 2") < context.index("SOURCE 3")
    tail = context[context.index("SOURCE 3"):]
    assert "Title: Jalen Hurts" in tail
    assert "Published: last revised 2026-10-01 (" in tail
    assert ("Source type: reference encyclopedia - background facts, never a "
            "current state") in tail
    assert "Wikidata lists Jalen Hurts as born August 7, 1998" in tail
    assert "wikipedia.org" not in context and "wikidata.org" not in context, (
        "never a hostname in the packet")
    assert packet.reference == "added" and packet.reference_via == "public"
    assert packet.as_dict()["reference"] == "added"
    labels = [i.label for i in packet.provenance.items]
    assert labels[:2] == ["reuters.com", "apnews.com"]
    assert "Wikipedia" in labels and "Wikidata" in labels
    wiki = [i for i in packet.provenance.items if i.label == "Wikipedia"][0]
    assert wiki.url == "https://en.wikipedia.org/wiki/Jalen_Hurts"
    assert "CC BY-SA" in wiki.tier


def test_a_late_reference_is_dropped_within_the_grace(fake, parallel):
    import research

    fake.delay = 1.5
    parallel["delay"] = 0.0

    async def timed():
        started = time.perf_counter()
        packet = await research.retrieve("jalen hurts", brief=_brief(),
                                         reference_name="Jalen Hurts")
        return packet, time.perf_counter() - started

    packet, seconds = asyncio.run(timed())
    assert seconds < 1.0, "held no longer than the grace past the main search"
    assert packet.reference == "late"
    assert "reference encyclopedia" not in packet.context
    # The look it left behind runs on in its own pool, and its last requests
    # read `wikimedia._transport` when they are sent: let it finish against
    # this fake, or they land in a later test's (CI on Postgres, §284).
    deadline = time.monotonic() + 10
    while not fake.asked(wikimedia.SUMMARY_URL) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert fake.asked(wikimedia.SUMMARY_URL), "the abandoned look never finished"


def test_no_reference_beside_an_empty_packet(fake, parallel):
    import research

    parallel["results"] = []
    packet = asyncio.run(research.retrieve("jalen hurts", brief=_brief(),
                                           reference_name="Jalen Hurts"))
    assert not packet, "a reference alone must not make an empty packet look researched"
    assert packet.reference == "no web sources"


def test_no_look_without_a_name(fake, parallel):
    import research

    packet = asyncio.run(research.retrieve("jalen hurts", brief=_brief()))
    assert fake.calls == [] and packet.reference == ""


def test_the_generator_passes_the_name_and_the_writer_is_told_what_it_is(fake, parallel):
    import script_generator as sg

    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = dataclasses.replace(sg.plan_episode("how is jalen hurts playing", 2),
                               search=True, brief=_brief())
    notes = sg.ScriptNotes()
    out = asyncio.run(generator.research(plan, notes))
    assert "reference encyclopedia" in out.evidence
    assert notes.research["reference"] == "added"
    assert notes.research["reference_via"] == "public"
    prompt = sg.build_prompt(out)
    assert "Never repeat its sentences word for word" in prompt
    plain = sg.build_prompt(dataclasses.replace(
        out, evidence="SOURCE 1\nTitle: x\nKey evidence:\ny"))
    assert "Never repeat its sentences word for word" not in plain, (
        "the rule rides only with a reference")

    fake.calls.clear()
    out = asyncio.run(generator.research(dataclasses.replace(
        plan, brief=_brief(outcome_dependent=True))))
    assert fake.calls == [] and "reference encyclopedia" not in out.evidence


# --------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------
def test_staging_forces_it_off_and_removes_the_credentials(monkeypatch):
    import spend_guard

    monkeypatch.setenv("ZERO_SPEND", "1")
    monkeypatch.setenv("WIKIPEDIA", "1")
    monkeypatch.setenv("WIKIMEDIA_ENTERPRISE_USERNAME", "fam")
    monkeypatch.setenv("WIKIMEDIA_ENTERPRISE_PASSWORD", "secret-pw")
    try:
        assert spend_guard.apply() is True
        assert os.environ["WIKIPEDIA"] == "0"
        assert "WIKIMEDIA_ENTERPRISE_USERNAME" not in os.environ
        assert "WIKIMEDIA_ENTERPRISE_PASSWORD" not in os.environ
        assert "secret-pw" not in json.dumps(spend_guard.report())
    finally:
        spend_guard.reset()
        for name in list(spend_guard.FORCED):
            os.environ.pop(name, None)


def test_the_hosts_are_counted_under_their_own_names():
    import provider_usage

    assert provider_usage.provider_for_host("www.wikidata.org") == "wikidata"
    assert provider_usage.provider_for_host("en.wikipedia.org") == "wikipedia"
    assert provider_usage.provider_for_host(
        "api.enterprise.wikimedia.com") == "wikimedia_enterprise"
    assert provider_usage.provider_for_host(
        "auth.enterprise.wikimedia.com") == "wikimedia_enterprise"


def test_health_reports_the_route_and_the_month(monkeypatch):
    from fastapi.testclient import TestClient

    import app as appmod

    with TestClient(appmod.app) as client:
        report = client.get("/api/health").json()["wikipedia"]
    assert report["route"] == "off"   # the suite keeps it off
    assert report["monthly_cap"] == 45000
    assert report["enterprise_requests_this_month"] == 0
    assert "secret" not in json.dumps(report)


def test_the_settings_and_their_copies_agree():
    import config

    assert config.settings.wikipedia is True
    assert config.settings.wikimedia_monthly_requests == 45000
    assert config.settings.wikipedia_timeout_seconds == 2.0
    assert config.settings.wikipedia_grace_seconds == 0.5
    assert config.settings.wikimedia_enterprise_username == ""
