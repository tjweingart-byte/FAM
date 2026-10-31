"""C3P0, the contextualizer: the `ResearchPlan` (§355) and his call (§360).

docs/agent_rules/c3p0.md: work out what a request is about, which plugins
to ask and what to ask them, what Sonic should fetch, and the title. Phase 1
starts where the guide's "First" line says to (docs/FAM_AGENTS.md, C3P0,
Plugin options): write the `ResearchPlan` and fill it from today's brief and
code routes with **no model**, so the plan C3P0's call will later improve is
one written decision per episode from the start, and so how often a search
came back empty while the archive held something is measured before the
archive is allowed to answer alone (responsibility 8).

`from_brief` reads the same predicates the pipeline already acts on - the
ladder always ([always-researched]), `live_route.domain_of` for a provider,
`live_facts.wants_forecast`, `history_archive.recall_wanted`, the brief's
town - so the plan describes what the episode will ask, never a second
opinion that could drift from it; when C3P0's call answered, the plan is
his (§360). It is pure: no file, store, socket or model, microseconds after
the brief. The pipeline does not route on the plan yet; it is kept on the
episode's notes and counted.

`search_half` is C3P0's call (§360): the brief's search half, sent through
the agent layer, with his and Nando's fields after it, each held in code
(`checked`).
"""
from __future__ import annotations

import collections
import logging
import threading
from typing import Optional

from agents.handoffs import PLUGINS, ArchiveRequest, PluginCall, ResearchPlan

log = logging.getLogger("fam.agents")

#: How plans have been built since boot, and what empty searches found in
#: the archive (`note_empty_search`). `/api/health`'s agents block reads it.
SINCE_BOOT: "collections.Counter[str]" = collections.Counter()
_LOCK = threading.Lock()


def _count(kind: str) -> None:
    with _LOCK:
        SINCE_BOOT[kind] += 1


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def from_brief(brief, query: str = "") -> Optional[ResearchPlan]:
    """The plan today's brief and code routes already make, as a
    `ResearchPlan`. None for no brief. Never raises."""
    if brief is None:
        return None
    try:
        import history_archive
        import live_facts
        import live_route
        from config import settings

        calls = [PluginCall(plugin="search", query=_text(
            getattr(brief, "retrieval", "") or query, 300))]
        place = _text(getattr(brief, "place", ""), 120)
        domain = live_route.domain_of(brief)
        entity = _text(live_route.entity_of(brief), 300)
        if domain == "weather":
            calls.append(PluginCall(plugin="weather", query=place or entity))
        elif domain:
            calls.append(PluginCall(plugin="live_facts", query=entity))
            if domain == "sports":
                calls.append(PluginCall(plugin="players", query=entity))
        if place and settings.local_news:
            calls.append(PluginCall(plugin="local_news", query=place))
        if live_facts.wants_forecast(brief):
            calls.append(PluginCall(plugin="forecast", query=_text(
                getattr(brief, "forecast_query", "") or entity, 300)))
        path = tuple(str(p) for p in (getattr(brief, "category_path", None) or ()))
        node = path[-1] if path else ""
        archive = None
        if not history_archive.recall_wanted(brief):
            calls.append(PluginCall(plugin="archive", query=_text(
                getattr(brief, "subject", "") or query, 300)))
            archive = ArchiveRequest(node_id=node, lookup="background",
                                     what=_text(getattr(brief, "subject", ""), 200))
        said = getattr(brief, "contextualized", None) or {}
        if said:
            # C3P0's call answered (§360): his reading and his plan, and how
            # far his plugin choices agree with the routes code took - which
            # is what decides whether the pipeline should follow them.
            mine = tuple(PluginCall(plugin=c["plugin"], query=_text(c["query"], 300))
                         for c in said.get("plugins") or ())
            code_set = {c.plugin for c in calls}
            mine_set = {c.plugin for c in mine}
            _count("plugins_agree" if mine_set == code_set else "plugins_differ")
            if said.get("archive", {}).get("lookup", "none") != "none":
                archive = ArchiveRequest(node_id=node, lookup=said["archive"]["lookup"],
                                         what=_text(said["archive"].get("what", ""), 200))
            plan = ResearchPlan(
                entity=_text(said.get("entity") or getattr(brief, "subject", ""), 200),
                entity_kind=_text(said.get("entity_kind") or domain, 60),
                ambiguous=bool(said.get("ambiguous")),
                readings=tuple(_text(r, 200) for r in said.get("readings") or ()),
                plugins=mine or tuple(calls), node_id=node, node_path=path, archive=archive,
                title=_text(said.get("title") or getattr(brief, "title", ""), 80),
                opinion_requested=_text(said.get("opinion_requested"), 120),
                private_person=bool(said.get("private_person")),
                advice_request=bool(said.get("advice_request")))
            _count("from_c3p0")
            return plan
        plan = ResearchPlan(
            entity=_text(getattr(brief, "live_entity", "") or getattr(brief, "subject", ""), 200),
            entity_kind=domain,
            plugins=tuple(calls),
            node_id=node,
            node_path=path,
            archive=archive,
            title=_text(getattr(brief, "title", ""), 80),
        )
        _count("from_brief")
        return plan
    except Exception as exc:  # noqa: BLE001 - a plan never costs an episode
        log.warning("the code research plan could not be built: %s", exc)
        _count("from_brief_failed")
        return None


def note_empty_search(look) -> None:
    """A search came back empty with the archive recall started beside it:
    count whether the archive held something - without waiting for it and
    without handing it to the writer (an empty search still gets no
    archive, §316, until `archive_alone` is built). Never raises."""
    try:
        _count("empty_search")
        if look is None:
            _count("empty_search_no_recall")
            return

        def done(fut) -> None:
            try:
                found = [] if fut.cancelled() else list(fut.result() or [])
            except Exception:  # noqa: BLE001
                found = []
            _count("empty_search_archive_had" if found else "empty_search_archive_nothing")

        look.add_done_callback(done)
    except Exception:  # noqa: BLE001
        log.warning("could not note an empty search", exc_info=True)


def report() -> dict:
    from config import settings

    with _LOCK:
        counts = dict(SINCE_BOOT)
    empty = counts.get("empty_search", 0)
    had = counts.get("empty_search_archive_had", 0)
    return {"since_boot": counts,
            # Whether his title is the episode's while he works (§360).
            "titles": bool(settings.contextualizer_titles),
            # None, not 0, before any empty search ([metering]'s rule).
            "empty_search_archive_had_rate": round(had / empty, 3) if empty else None}


def reset() -> None:
    with _LOCK:
        SINCE_BOOT.clear()


# ------------------------------------------------------------------ C3P0's call (§360)
# C3P0 rides in one of the brief's existing calls - the search half, which
# already writes the searches and the title (the owner's answers 1 and 2,
# §354) - sent through `agents.models.ask`, so it is metered, locked under
# his signed rule book and checked like any agent call. One call, at the
# moment the brief's search half was always asked: nothing new in series.
# With Nando working too, the same call picks his node from a candidate list
# code built from the tree (Nando's choice, §346: folded into the brief).
import re  # noqa: E402

from pydantic import BaseModel, ConfigDict, Field  # noqa: E402
from typing import Literal  # noqa: E402

SOURCE_KINDS = ("general", "news", "official", "filings", "academic", "local")


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pronounce(_Closed):
    name: str
    say: str


class PlannedCall(_Closed):
    plugin: Literal["search", "live_facts", "players", "forecast", "local_news", "weather",
                    "archive"]
    query: str = Field(max_length=300)


class ArchiveAsk(_Closed):
    lookup: Literal["background", "result_between", "value_on_day", "series", "by_meaning",
                    "corroboration", "none"]
    what: str = Field(max_length=200)


class SearchPlan(_Closed):
    """The brief's search half (`episode_intelligence.PARTS["search"]`,
    field for field) with C3P0's and Nando's fields after it."""

    # The search half, as `episode_intelligence.SEARCH_SCHEMA` has it.
    search_query: str
    search_fallback: str
    search_angles: list[str]
    search_objective: str
    title: str
    category: str
    forecast_query: str
    source_kind: Literal["general", "news", "official", "filings", "academic", "local"]
    pronounce: list[Pronounce]
    # C3P0.
    entity: str = Field(max_length=200)
    entity_kind: str = Field(max_length=60)
    ambiguous: bool
    readings: list[str] = Field(max_length=4)
    likeliest: str = Field(max_length=200)
    plugins: list[PlannedCall] = Field(max_length=7)
    archive: ArchiveAsk
    opinion_requested: str = Field(max_length=120)
    private_person: bool
    advice_request: bool
    # Nando: a node id from the candidate list, or "none".
    node: str = Field(max_length=120)


C3P0_INSTRUCTIONS = """You also plan the research for this episode, as \
FAM's contextualizer, and file it, as FAM's categorizer, in the same answer.

First work out exactly what the request is about. Names are often shared: \
United is an airline and a football club; the Eagles are a bird and a team. \
Decide from the words around the name and the request's context. If you \
cannot tell, set ambiguous true, give the readings (two to four, each a few \
words a listener would recognise, like "United Airlines" and "Manchester \
United") and say which is likeliest from the request's own words; never \
guess silently. entity is the thing the request is about, resolved; \
entity_kind what kind of thing it is (team, airline, company, person, \
animal, place, event...).

Choose plugins only from the list the schema allows, and only the ones the \
request needs: search always; live_facts for a score, a price or an \
election count; players for a player's or a team's report; forecast beside \
a question whose answer turns on an outcome; local_news only for a town's \
own news; weather only for a weather question; archive for earlier \
reporting. Write each one's query as what the source must find, never as \
what you think it will say. Nothing has been looked up yet. Wrong: "Eagles \
beat Giants 24-17 recap". Right: "Eagles Giants final score, Sunday 11 \
October 2026". For a scoreboard or a price, give the name as the provider \
lists it ("Philadelphia Eagles", "NVIDIA"); never an id or a ticker. Work \
out any dates from the moment given. archive is what FAM's own archive \
should look for: the lookup from the list and what to look for, or "none".

If the request is about a person who is not a public figure, set \
private_person true; FAM does not research private people. If it asks for \
medical, legal or financial advice, set advice_request true and plan the \
facts around it, never a recommendation. Only when the request explicitly \
asks for a named person's opinion or point of view, put that person's name \
in opinion_requested; otherwise leave it empty.

The title (three to eight words) names the subject and the angle - never a \
result, a score or a claim, since nothing has been looked up yet.

Then file the episode: choose the single most specific node from the \
candidate list that the episode is truly about, using its id exactly as \
listed, or "none". A node matches what the episode is about, not a word it \
contains: "hurricanes hitting the Carolina coast" is weather, not the \
hockey team. A broader true node is better than a specific wrong one."""

#: A result where a search input or a title must not have one: a score, or
#: a verb saying how something ended. Not a result (review, §361): a date
#: or a date range ("2026-10-11", "October 10-11"), a range of ages, days,
#: stars or players ("ages 18-25", "5-7 day forecast", "top 10-15"), a
#: named thing with a number in it ("9-11", "737-800", "4-3 defense",
#: "50-50", "9-5 job"); "who won", "did they win", "can they beat" are
#: questions and "have won four straight" a streak.
_MONTH = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
          r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
_SCORE = re.compile(r"(?<![\w.:\-–—])(\d{1,3})\s*[-–—]\s*(\d{1,3})(?![\w%\-–—]|[.:]\d)")
#: "Eagles 24, Giants 17" - two names, each with a number, the comma
#: between them. Without the comma it is too like "Windows 11 Office 365"
#: to be read as a score.
_NAMED_SCORE = re.compile(r"\b[A-Z][\w.'&]*(?: [A-Z][\w.'&]*)* (\d{1,3}), "
                          r"(?:[A-Z][\w.'&]*(?: [A-Z][\w.'&]*)*) (\d{1,3})\b")
_NOT_A_SCORE_BEFORE = re.compile(
    r"\b(ages?|aged|between|from|top|week|weeks|day|days|years?|grades?|sizes?|"
    r"rooms?|chapters?|pages?|steps?|levels?|hours?|boeing|airbus|covid|" + _MONTH +
    r")\s*$", re.I)
_NOT_A_SCORE_AFTER = re.compile(
    r"^\s*(-?\s*)?(day|days|week|weeks|month|months|year|years|hour|hours|minute|"
    r"minutes|percent|per cent|%|degrees|inch|inches|mph|km|stars?|players?|"
    r"defen[cs]e|formation|job|jobs|chance|split|attacks?|" + _MONTH + r")\b", re.I)
_RESULT_VERB = re.compile(
    r"\b(won(?!')|wins|beat|beats|defeated|defeats|crush|crushed|crushes|rout|routed|"
    r"routs|edged|topped|lost|loses|lose|clinched|clinches|eliminated|swept|sweeps|"
    r"fell to|victory over|cruised past)\b", re.I)
#: Words in front of a result verb that make it a question, a prospect or a
#: streak rather than a result.
_ASKING = re.compile(
    r"\b(who|did|does|do|whether|if|can|could|will|would|should|might|may|to|"
    r"chance|chances|odds|have|has|had|not)\s+(\w+\s+){0,3}$", re.I)


def _is_score(text: str, m) -> bool:
    a, b = int(m.group(1)), int(m.group(2))
    if a == b and a >= 10:
        return False                      # "50-50"
    return not (_NOT_A_SCORE_BEFORE.search(text[:m.start()])
                or _NOT_A_SCORE_AFTER.search(text[m.end():]))


def has_result(text: str) -> bool:
    text = text or ""
    if any(_is_score(text, m) for m in _SCORE.finditer(text)):
        return True
    if _NAMED_SCORE.search(text):
        return True
    for m in _RESULT_VERB.finditer(text):
        if not _ASKING.search(text[:m.start()]):
            return True
    return False


def asks_for_view(person: str, query: str) -> bool:
    """Whether `query` names `person` and asks for that person's view: the
    name (or its last word, "Krugman" for "Paul Krugman") right before a
    view verb ("Krugman think") or a possessive view noun with what it is on
    ("Krugman's take on"), or right after "the opinion of". A word inside
    another word is not the name ("Byron" is not "Ron"), and reporting verbs
    are not views: "Biden said GDP grew" asks for a fact (review, §361)."""
    full = " ".join((person or "").split())
    if not full:
        return False
    names = [full] + ([full.split()[-1]] if len(full.split()) > 1 else [])
    view_verb = (r"\s+(?:\w+\s+){0,2}(?:think|thinks|thought|believe|believes|"
                 r"argue|argues|argued|feel|feels|make of|stand on)\b")
    view_noun = (r"'s?\s+(?:view|views|opinion|opinions|take|position|stance|thoughts)"
                 r"\s+(?:on|about|of|regarding)\b")
    before = (r"\b(?:opinion|opinions|view|views|take|position|stance|thoughts)\s+of\s+")
    for name in names:
        n = r"(?<![\w'])" + re.escape(name) + r"(?![\w])"
        if (re.search(n + view_verb, query or "", re.I)
                or re.search(n + view_noun, query or "", re.I)
                or re.search(before + n, query or "", re.I)):
            return True
    return False


#: C3P0's title: three to eight words (c3p0.md, "always" 7), held in code.
TITLE_WORDS = (3, 8)


def checked(answer: "SearchPlan", query: str, candidates: tuple) -> tuple:
    """`(search_fields, extras, notes)`: the answer held to the rules in
    code (c3p0.md "never" 1-2; the guide's Guardrails). Never raises."""
    import episode_intelligence as ei

    data = answer.model_dump()
    why: list = []
    search = {k: data[k] for k in ("search_query", "search_fallback", "search_angles",
                                   "search_objective", "title", "category",
                                   "forecast_query", "source_kind", "pronounce")}
    # The title: no result, the brief's own cleaning (not the question handed
    # back, no trailing "?", at most 80 characters), three to eight words.
    raw_title = search["title"]
    # Over eighty characters is refused rather than cut mid-word.
    title = (ei.clean_title(raw_title, query)
             if not has_result(raw_title) and len(raw_title.strip()) <= 80 else "")
    title_ok = bool(title) and TITLE_WORDS[0] <= len(title.split()) <= TITLE_WORDS[1]
    if raw_title and not title_ok:
        why.append(f"title refused: {raw_title!r}")
    search["title"] = title if title_ok else ""
    for name in ("search_query", "search_fallback", "forecast_query"):
        if has_result(search[name]):
            why.append(f"{name} with a result dropped: {search[name]!r}")
            search[name] = ""
    search["search_angles"] = [a for a in search["search_angles"] if not has_result(a)]
    plugins = []
    for call in data["plugins"]:
        if has_result(call["query"]):
            why.append(f"{call['plugin']} input with a result replaced")
            call = dict(call, query=search["search_query"] or query)
        plugins.append(call)
    readings = [r.strip() for r in data["readings"] if r.strip()]
    ambiguous = bool(data["ambiguous"]) and len(readings) >= 2
    archive = dict(data["archive"])
    if has_result(archive.get("what", "")):
        why.append("archive request with a result dropped")
        archive = {"lookup": "none", "what": ""}
    opinion = data["opinion_requested"].strip()
    if opinion and not asks_for_view(opinion, query):
        why.append(f"opinion_requested {opinion!r} dropped: not asked for by name")
        opinion = ""
    node = data["node"].strip()
    if node and node != "none" and node not in candidates:
        why.append(f"node {node!r} not among the candidates")
        node = ""
    extras = {
        "entity": data["entity"].strip(), "entity_kind": data["entity_kind"].strip(),
        "ambiguous": ambiguous, "readings": readings if ambiguous else [],
        "likeliest": data["likeliest"].strip() if ambiguous else "",
        "plugins": plugins, "archive": archive,
        "opinion_requested": opinion, "private_person": bool(data["private_person"]),
        "advice_request": bool(data["advice_request"]),
        "node": "" if node == "none" else node, "title": search["title"] if title_ok else "",
        "checked": why,
    }
    return search, extras, why


def candidate_block(candidates: tuple) -> str:
    """The candidate nodes as the call reads them: id, path, and the
    vocabulary's definition and exclusions when drafted (§359)."""
    import category_vocabulary
    from agents import categorizer

    lines = []
    for node in candidates:
        path = " > ".join(categorizer.path_for(node))
        about = category_vocabulary.definition(node)
        extra = ""
        if about.get("definition"):
            extra += f" - {about['definition']}"
        if about.get("excludes"):
            extra += f" (not: {about['excludes']})"
        lines.append(f"{node} | {path}{extra}")
    return "\n".join(lines)


def instructions() -> str:
    """The cacheable block after the preamble and the rule books: the
    brief's own instructions, the search half's field guide, then C3P0's."""
    import episode_intelligence as ei

    base = ei.EI_SYSTEM + (ei.examples_block() if ei.settings.ei_examples else "")
    return (base + "\n\nWork out:\n\n" + ei.field_guide(ei.PARTS["search"])
            + "\n\n" + C3P0_INSTRUCTIONS)


def _moment(query: str, minutes: int, context: str, now: str, covered: tuple) -> str:
    import episode_intelligence as ei

    text = ei.build_ei_prompt(query, minutes, context, now, covered, ())
    # The request goes in its own tag; what remains is the moment, the
    # length and any follow-up - the brief's own wording of them. What to
    # work out is in the instructions, so the empty heading goes.
    text = text.replace(f"<request>{query}</request>", "(the request above)")
    return text.rstrip().removesuffix("Work out:").rstrip()


async def search_half(query: str, minutes: int, context: str, now: str, covered: tuple,
                      ahead: bool, notes, candidates: tuple, fallback) -> tuple:
    """The brief's search half as C3P0's call: `(kind, data, reason)` in the
    shape `episode_intelligence._ask` returns, `data` carrying the search
    fields and `_c3p0` beside them. A request that was never sent (held,
    off, misconfigured, staging...) runs the brief's own search half instead
    - no time was spent. One that was sent and failed is the search half
    failing, as it can today. Never raises."""
    from agents import models, netenyahu
    from config import settings

    try:
        material = [("request", query), ("evidence", _moment(query, minutes, context, now,
                                                             covered))]
        if candidates:
            material.append(("evidence", "Candidate nodes (id | path):\n"
                             + candidate_block(candidates)))
        request = models.AgentRequest(
            agent="contextualizer", task="contextualise", switch_setting="contextualizer",
            model_setting="contextualizer_model", rule_book="c3p0.md", schema=SearchPlan,
            instructions=instructions(), material=tuple(material),
            carries_listener_words=True,
            timeout=float(settings.ei_ahead_timeout_seconds if ahead
                          else settings.ei_timeout_seconds),
            max_tokens=int(settings.ei_max_tokens),
            effort=str(settings.ei_ahead_effort if ahead else settings.ei_effort),
            surface="ahead" if ahead else "search")
    except Exception as exc:  # noqa: BLE001 - nothing was sent: the brief's own half
        log.warning("c3p0's request could not be built (%s); the brief's own search half",
                    exc)
        _count("call_fell_back_to_brief")
        return await fallback()
    result = await models.ask(request, notes=notes)     # never raises
    try:
        netenyahu.report("contextualizer", result.status)
        _count(f"call_{result.status}")
        if not result.ok:
            # Only a request that never left (`sent` is set the moment it
            # does): one sent and then failed is never sent again (review,
            # §361 - a second request for one site).
            if not result.sent:
                _count("call_fell_back_to_brief")
                return await fallback()
            kind = "timeout" if result.status == models.TIMEOUT else "failed"
            return kind, None, f"C3P0 {result.status}: {result.reason}"
        search, extras, why = checked(result.value, query, candidates)
    except Exception as exc:  # noqa: BLE001 - sent: the search half failed, once
        log.exception("c3p0's answer could not be read")
        return "failed", None, f"C3P0's answer could not be read: {type(exc).__name__}"
    for line in why:
        log.info("c3p0 on %r: %s", query, line)
    if why:
        _count("answers_corrected")
    data = dict(search, _c3p0=extras)
    return "ok", data, ""
