"""The typed hand-offs between agents (§349).

Agents never call each other: Netenyahu hands each one the typed output of
the last (docs/FAM_AGENTS.md, Pipeline and how agents run) - `Brief` (built,
`episode_intelligence`) -> `ResearchPlan` -> `PluginPacket` / `ArchivePacket`
-> `Outline` -> `ScriptSentence` -> `Verdict`. Each is a Pydantic model that
forbids extra fields and carries `schema_version`; `validate` checks one,
never raises, and a hand-off that fails it is refused - that step then runs
as it does today.

Only fields the guide, the build prompt and the rule books already settle
are here. Each agent's own phase adds what it needs, with its version
bumped, so Phase 0 does not design Phases 1-3 ahead of them.

What the shapes hold by design, so a model cannot hand on what it must not:
evidence is the sources' own sentences, pointed at by number (`SentenceRef`),
never an agent's wording (every-agent.md, "never store or pass on your own
wording as evidence"); a verdict is one of a closed list of failures; an
archive request is one of a closed list of lookups.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class _Handoff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = 1


# ------------------------------------------------------------------ C3P0 + Nando
#: Sonic's closed list of lookups (docs/agent_rules/sonic.md, rule 4).
LOOKUPS = ("background", "result_between", "value_on_day", "series", "by_meaning",
           "corroboration")


class ArchiveRequest(_Handoff):
    """What Sonic should fetch: a node, a lookup from the closed list, a period."""

    node_id: str
    lookup: Literal["background", "result_between", "value_on_day", "series",
                    "by_meaning", "corroboration"]
    what: str = Field(default="", max_length=200)
    start: str = ""   # ISO dates, worked out in code from the listener's clock
    end: str = ""


#: FAM's closed list of plugins (c3p0.md, "always" 3; docs/FAM_AGENTS.md,
#: C3P0 responsibility 2): the search ladder, a live provider, a player's or
#: team's report, the forecast, a town's own news, the weather, and the
#: archive through Sonic. There are no others (§366).
PLUGINS = ("search", "live_facts", "players", "forecast", "local_news", "weather",
           "archive")


class PluginCall(_Handoff):
    """One plugin from FAM's own list, and what to ask it - never a result."""

    plugin: Literal["search", "live_facts", "players", "forecast", "local_news",
                    "weather", "archive"]
    query: str = Field(max_length=300)


class ResearchPlan(_Handoff):
    """C3P0's and Nando's plan, built with the brief before anything is searched."""

    entity: str = ""
    entity_kind: str = ""
    #: True when a name could mean two things (United, the Eagles): both
    #: readings are asked, never guessed (decided item 15).
    ambiguous: bool = False
    readings: tuple[str, ...] = ()
    plugins: tuple[PluginCall, ...] = ()
    #: Nando's node, from the tree, and its path.
    node_id: str = ""
    node_path: tuple[str, ...] = ()
    archive: Optional[ArchiveRequest] = None
    #: C3P0's title: three to eight words, no result in it (decided item 1).
    title: str = Field(default="", max_length=80)
    #: Whose opinion the request explicitly asks for, else "" (decided item 6).
    opinion_requested: str = ""
    #: A request about a private person gets no episode (decided items 16, 24).
    private_person: bool = False
    #: A medical, legal or money question, planned as facts (decided item 17).
    advice_request: bool = False


# ------------------------------------------------------------------ the two packets
class Source(_Handoff):
    """One source's own sentences, numbered by their position."""

    url: str
    published: str = ""            # ISO date; relative dates are code's
    grade: str = ""                # credibility grade, never the hostname to the writer
    provider: str = ""
    sentences: tuple[str, ...] = ()


class PluginPacket(_Handoff):
    """Packet one: what is happening now, from the plugins."""

    sources: tuple[Source, ...] = ()


class ArchiveItem(_Handoff):
    date: str
    node_id: str
    source: Source
    kind: Literal["fact", "figure", "quote", "result"]


class ArchivePacket(_Handoff):
    """Packet two: what came before, from MongoDB - history, in date order."""

    items: tuple[ArchiveItem, ...] = ()

    @model_validator(mode="after")
    def _oldest_first(self):
        dates = [item.date for item in self.items]
        if dates != sorted(dates):
            raise ValueError("an archive packet is in date order, oldest first")
        return self


# ------------------------------------------------------------------ Shakespeare
class SentenceRef(_Handoff):
    """A pointer to a source's own sentence - the only form evidence takes."""

    packet: Literal["plugin", "archive"]
    source: int = Field(ge=0)
    sentence: int = Field(ge=0)


class Beat(_Handoff):
    purpose: str = Field(max_length=120)
    refs: tuple[SentenceRef, ...] = ()


class Outline(_Handoff):
    summary: tuple[SentenceRef, ...] = ()
    beats: tuple[Beat, ...] = ()
    opening: tuple[SentenceRef, ...] = ()
    landing: tuple[SentenceRef, ...] = ()
    #: The packets hold too little for the length (a beat with nothing
    #: behind it is dropped, never filled).
    thin: bool = False
    #: What could not be found - for the writer only, never spoken.
    missing: tuple[str, ...] = ()


# ------------------------------------------------------------------ the writer and Mclovin
class ScriptSentence(_Handoff):
    index: int = Field(ge=0)
    text: str


#: Mclovin's named failures (docs/agent_rules/mclovin.md, "you must never").
FAILURES = ("number_not_in_packets", "quote_not_said", "quote_wrong_speaker",
            "unsupported_changing_fact", "contradicted", "unsupported_superlative",
            "wrong_date", "preview_as_result", "stories_joined", "website_named",
            "settled_fact_with_name", "advice")


class Verdict(_Handoff):
    sentence: int = Field(ge=0)
    #: `unchecked` is a check that ran out of time: the sentence played and
    #: the miss is recorded for Keith (decided item 32).
    result: Literal["pass", "fail", "unsure", "unchecked"]
    failure: Optional[Literal[
        "number_not_in_packets", "quote_not_said", "quote_wrong_speaker",
        "unsupported_changing_fact", "contradicted", "unsupported_superlative",
        "wrong_date", "preview_as_result", "stories_joined", "website_named",
        "settled_fact_with_name", "advice"]] = None

    @model_validator(mode="after")
    def _failure_only_on_fail(self):
        if (self.result == "fail") != (self.failure is not None):
            raise ValueError("a failure is named exactly when the result is fail")
        return self


# ------------------------------------------------------------------ checking a hand-off
HANDOFFS = {cls.__name__: cls for cls in (
    ResearchPlan, ArchiveRequest, PluginCall, PluginPacket, Source, ArchivePacket,
    ArchiveItem, Outline, Beat, SentenceRef, ScriptSentence, Verdict)}

#: The order Netenyahu passes them along an episode.
CHAIN = ("ResearchPlan", "PluginPacket", "ArchivePacket", "Outline", "ScriptSentence",
         "Verdict")


def validate(kind: str, data) -> tuple:
    """`(hand-off, "")`, or `(None, why)` for one that is not whole. Never
    raises: a broken or late hand-off means that step runs as it does today."""
    model = HANDOFFS.get(kind)
    if model is None:
        return None, f"no hand-off called {kind!r}"
    try:
        if isinstance(data, model):
            return data, ""
        return model.model_validate(data), ""
    except ValidationError as exc:
        return None, f"{kind} refused: {exc.error_count()} error(s)"
    except Exception as exc:  # noqa: BLE001
        return None, f"{kind} refused: {type(exc).__name__}"


def schemas() -> dict:
    """Every hand-off's JSON schema, for tests and `/admin`."""
    return {name: model.model_json_schema() for name, model in HANDOFFS.items()}
