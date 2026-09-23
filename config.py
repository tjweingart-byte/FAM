"""Central configuration.

Every value can be overridden with an environment variable so the app can be
tuned without touching code (12-factor style).
"""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field

import credentials
import voice_store
from paths import data_path


def shared_env_path() -> pathlib.Path:
    """The per-machine settings file, alongside the shared voice store.

    `~/.fam/env` is deliberately outside any project folder. A key kept in a
    project `.env` is lost every time the app is unpacked somewhere new, and
    the workaround for that is pasting the key again - into a terminal, into a
    chat, into whatever is to hand. One file per machine, set once.
    """
    override = os.environ.get("FAM_ENV_FILE")
    if override:
        return pathlib.Path(override).expanduser()
    return pathlib.Path.home() / ".fam" / "env" if pathlib.Path.home() else pathlib.Path(".fam-env")


def key_source() -> str:
    """Where the key in force came from. A key that works is not much comfort
    when you cannot tell which file the app actually read.

    A secrets provider is asked about first because it is the one source that
    is not a file you can go and look at, so it is the one worth naming.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "nowhere - no key is set"
    if "ANTHROPIC_API_KEY" in credentials.SOURCES:
        return credentials.SOURCES["ANTHROPIC_API_KEY"]
    project = pathlib.Path(__file__).resolve().parent / ".env"
    for path, label in ((project, "the project .env"), (shared_env_path(), str(shared_env_path()))):
        try:
            if any(line.strip().lstrip("export ").startswith("ANTHROPIC_API_KEY=")
                   for line in path.read_text().splitlines()):
                return label
        except (OSError, UnicodeDecodeError):
            continue
    return "the environment"


def _dotenv_values() -> dict[str, str]:
    """Read the .env files without applying them.

    Parsed separately from being applied for one reason: `FAM_SECRETS` may
    itself be set in a .env, and the secrets provider has to run *before* the
    files are applied so that a real environment variable still outranks it.
    Reading first and applying after is what lets both be true.

    The shell scripts source .env before starting the server, so for a long
    time nothing in Python needed to. Then `python app.py` - which app.py
    itself offers, in its __main__ block - started the server without it, the
    key was invisible, and the app fell back to the canned demo script while
    .env sat there with a perfectly good key in it. Reading it here means the
    key is found however the app is started.
    """
    # Tests must not change result because of what is in a developer's .env -
    # a key there would flip the app out of demo mode mid-suite. conftest.py
    # sets this before anything imports config.
    if os.environ.get("FAM_IGNORE_DOTENV"):
        return {}
    lines: list[str] = []
    # ~/.fam/env first, project .env second, so the project can override the
    # machine-wide setting. The shared file exists for the same reason
    # ~/.fam/voices does: every new copy of the app is a fresh folder with no
    # .env in it, and re-pasting a key into each one is how keys get pasted
    # into the wrong places.
    for path in (shared_env_path(), pathlib.Path(__file__).resolve().parent / ".env"):
        try:
            lines += path.read_text().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
    if not lines:
        return {}
    # Last occurrence wins, which is what `source .env` does. A loader that took
    # the first would disagree with the shell scripts about the same file - and
    # a .env that has been appended to twice (an old key, then the corrected
    # one) would authenticate with the wrong one, silently.
    found: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        # Tolerate `export FOO=bar` and quoted values, which is what people
        # actually write in a .env.
        if name.startswith("export "):
            name = name[len("export "):].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name:
            found[name] = value
    return found


def _load_dotenv() -> None:
    """Resolve every credential, in the order that needs no human.

        1. the process environment    a platform dashboard, a CI secret, -e
        2. FAM_SECRETS               fetched now, so rotation needs no redeploy
        3. the project .env          a project pinning its own key
        4. ~/.fam/env                the per-machine store (PROBLEMS.md 53)

    A real environment variable always wins: nothing below it overwrites one,
    so `MODEL=... python app.py` still overrides every file and every provider.
    """
    values = _dotenv_values()
    # The provider can be named in a .env - that is how a laptop configures it
    # once - so lift that one variable before asking the provider anything.
    if credentials.PROVIDER_VAR in values and not os.environ.get(credentials.PROVIDER_VAR):
        os.environ[credentials.PROVIDER_VAR] = values[credentials.PROVIDER_VAR]
    # `FAM_IGNORE_DOTENV` silences the provider as well as the files. The name
    # says dotenv, but what conftest.py sets it for is "no ambient credentials
    # in this process", and a suite that shelled out to a developer's secrets
    # manager would be neither hermetic nor fast.
    #
    # A provider that is configured and cannot be read is loud and not fatal.
    # Not fatal, because the .env files below may still hold a usable key and
    # an app that refuses to start has answered a question nobody asked. Loud,
    # because the alternative is falling through to the canned script with no
    # reason given - the silent success this project has lost the most time to.
    if not os.environ.get("FAM_IGNORE_DOTENV"):
        try:
            credentials.load()
        except credentials.SecretsUnavailable:
            pass  # already logged, and reported by health and preflight
    for name, value in values.items():
        if name not in os.environ:
            os.environ[name] = value
    # Everything is now resolved, so the pool has its final contents. Publish
    # its head: `ANTHROPIC_API_KEYS` is a form only `credentials` reads, and a
    # deployment that set only that would otherwise have keys and send none.
    credentials.prime()


_load_dotenv()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


#: How a researched episode gets its facts.
#:
#: `claude` gives the model Anthropic's server-side `web_search` tool, so it
#: searches while it writes: one call, one credential, and the searching
#: happens inside the model's turn.
#:
#: `exa` retrieves first and hands Claude an evidence packet to read. Two
#: calls and a second credential, but the retrieval is a bounded, timed step
#: this codebase can measure - `research.py` keeps the call and the packet
#: byte-for-byte as the manual benchmark measured them, so the numbers already
#: taken by hand stay comparable.
#:
#: `exa` is the default. That is a real cost: it needs a second credential, and
#: a deployment without EXA_API_KEY cannot research at all - a researched
#: episode will fail rather than quietly search another way. The app says so at
#: startup and on every /api/health, because a missing credential discovered on
#: a listener's first researched question is the shape of failure this project
#: has paid for most.
#:
#: `claude` remains one variable away and needs nothing installed, so a
#: deployment without an Exa key has a working configuration to move to rather
#: than a broken one to endure.
#:
#: **`gdelt` is the third, and it is the keyless one** (§109). It was already
#: in the codebase as an additive cross-check beside Exa; it is a retriever in
#: its own right now, because the ladder that runs when a search comes back
#: empty needs a rung that costs nothing to try. Weaker than Exa - an article
#: index with no highlights - and available to a deployment that has no
#: retrieval credential at all.
#:
#: A value outside this tuple is refused - at import by
#: `Settings.__post_init__`, and again at retrieval time by `research.retrieve`
#: - rather than falling back to one silently. What the *ladder* does when a
#: rung comes back empty is a different thing, and it is recorded on the
#: packet every time (`fell_back_from`).
RESEARCH_BACKENDS = ("claude", "exa", "gdelt")

#: What a deployment gets when it says nothing. Named rather than repeated as a
#: literal, for the same reason as DEFAULT_PIPELINE.
DEFAULT_RESEARCH_BACKEND = "exa"

#: How long an episode is when nobody has said. Two minutes, not three.
#:
#: Named here because it was a literal `3` in about twenty places - every
#: endpoint's `Query` default, the client's two length controls, prefetch,
#: and half the tools - which is the shape a number takes when nobody can
#: change it without missing one. `DEPTH_BANDS` says what each band of minutes
#: is *for*; this says which one somebody who has expressed no preference
#: lands in.
DEFAULT_MINUTES = 2

#: How much of an episode prefetch pays for in advance. See `prefetch_level`.
#: Named here rather than repeated as literals so "the levels" is one fact in
#: one place: `Settings`, `prefetch.LEVELS` and the tests all read it from here.
PREFETCH_LEVELS = ("brief", "script")

#: **There is no from-knowledge cover any more.** `ANSWER_FIRST` used to start
#: a second, tool-less model call that began speaking immediately while
#: research was still reading, and hand over mid-episode. It was deleted in
#: PROBLEMS.md §108, not switched off: the cover half wrote the first ten
#: seconds of every researched episode with no brief, no evidence and no idea
#: what the episode was about, which is exactly the "confusing opening" the
#: listener reported - and a knob left behind gets turned back on, which this
#: one was, by a line in `Dockerfile.gpu`.
#:
#: Nothing is spoken now until the writer holds the whole picture.


#: Which generation pipeline a request runs through.
#:
#: `phase6` is production and is `DEFAULT_PIPELINE` below: a character-bounded
#: script buffer between the model reader and the voice, so synthesis falling
#: behind can never stop the reader, and speech-sized chunks after the first.
#: The first chunk keeps its own latency path - the first complete speakable
#: thought, no word floor.
#:
#: `legacy` is the older path: `pipeline._start` pumps every sentence into a
#: bounded queue the synthesiser drains, one sentence per synthesis call, so a
#: slow voice stops the model reading. It is kept, and kept tested, for the
#: baseline half of a comparison run and for the equivalence suite. Nothing
#: selects it automatically.
#:
#: Both stay listed because rolling back must remain one environment variable
#: and a restart. A value outside this tuple is refused - at import by
#: `Settings.__post_init__`, and again at request time by `pipeline._phase6` -
#: rather than falling back to either.
STREAMING_PIPELINES = ("legacy", "phase6")

#: Which machine fills the one production voice slot. See `voice_backend`.
VOICE_BACKENDS = ("chatterbox", "remote")

#: How the app reaches a remote voice. Both are the same worker image.
VOICE_TRANSPORTS = ("runpod", "http")

#: Whether FAM may find a worker for itself (`voice_control.ladder()`) or must
#: use only the address it was given. `off` is the pre-§112 behaviour.
VOICE_DISCOVERY = ("auto", "off")

#: What a deployment gets when it says nothing. Named rather than repeated as a
#: literal, so "the default" is one fact in one place: `Settings`, the health
#: report and the tests all read it from here.
DEFAULT_PIPELINE = "phase6"


@dataclass(frozen=True)
class Settings:
    # --- Claude -----------------------------------------------------------
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )
    # Speed IS the product here: the listener must hear an answer within about
    # a second. Opus with web search took 20-30s to produce its first sentence,
    # which no amount of clever buffering can disguise. Sonnet 5 answers from
    # what it knows almost immediately. MODEL=claude-opus-5 for depth over speed.
    model: str = field(default_factory=lambda: os.environ.get("MODEL", "claude-sonnet-5"))
    max_output_tokens: int = _env_int("MAX_OUTPUT_TOKENS", 16000)
    # HTTP/2 to api.anthropic.com is broken by some proxies and TLS-inspecting
    # middleboxes, which surfaces only as "Connection error". HTTP/1.1 is the
    # default here and is pinned explicitly in anthropic_client.py. Set
    # ANTHROPIC_HTTP2=1 (and `pip install h2`) to opt back in.
    anthropic_http2: bool = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_HTTP2", "0") not in ("0", "false", "False", "")
    )
    # low | medium | high | xhigh | max. How much hidden reasoning the writing
    # call does before its first word.
    #
    # **`low`, at the owner's explicit direction (PROBLEMS.md §129)**, which
    # reverses §108's `high`. §108 raised it because openings were confused -
    # a model that "started talking before it had worked out what it was going
    # to say". But §108 made that change in the same commit that deleted the
    # from-knowledge cover half and the search tool on the writing call, and
    # the cover half is what wrote those openings; nothing ever separated what
    # effort bought from what the deletions bought. What `high` costs is hidden
    # thinking in front of the first word, none of it audible - by reading the
    # path the largest single wait on search, not yet measured (§128's
    # `episode timing` block is what measures it).
    #
    # What stays from §108 is everything about *order*: the writer still holds
    # the brief and the evidence before its first token, still carries no
    # tools, and the prompt still asks it to decide the whole piece before it
    # opens. Only the thinking budget for doing so is smaller.
    #
    # Unheard at `low` since the cover was removed - there is no key where this
    # was changed - so the openings are the first thing to listen to. `EFFORT`
    # in the environment overrides this, and `/api/health` says which is in
    # force (`writer_effort_source`), because a dashboard value left at `high`
    # would silently undo this change on every push.
    effort: str = field(default_factory=lambda: os.environ.get("EFFORT", "low"))
    # Padding a short script back to length reintroduces the filler the opener
    # was removed for. Off by default: a briefing that ends when it runs out of
    # substance is better than one stretched to fill the slider.
    allow_topups: bool = field(
        default_factory=lambda: os.environ.get("ALLOW_TOPUPS", "0") not in ("0", "false", "False")
    )
    # always | auto | never. **The default is now `always`** - every episode is
    # researched before it is written.
    #
    # This reverses `auto`, and the reversal is about arithmetic rather than
    # taste. `auto` was written when "search" meant Anthropic's server-side
    # `web_search` tool, which front-loads 10-25 seconds; at that price a
    # keyword guess about which questions "read as time-sensitive" was worth
    # making, because the ones guessed wrong only lost freshness while the ones
    # guessed right saved half a minute. With `RESEARCH_BACKEND=exa` -
    # DEFAULT_RESEARCH_BACKEND above - retrieval is about half a second, and at
    # that price the guess costs more than it saves: every question it gets
    # wrong is answered from memory that may be years stale, and nothing
    # observable is bought for the ones it gets right.
    #
    # The specific failure that ended it: "49ers game last night" was logged as
    # `SEARCH no - nothing in it reads as time-sensitive`. A keyword list can
    # always be widened one more word, and the next question it misses is
    # already written.
    #
    # `auto` and `never` are kept, and are what `write.py`, `compare_search.py`
    # and a deployment without an Exa key use. They are not production.
    # A request can still say search=1 or search=0 explicitly and win.
    search_mode: str = field(
        default_factory=lambda: (
            "always" if os.environ.get("ENABLE_WEB_SEARCH", "") in ("1", "true", "True")
            else os.environ.get("SEARCH_MODE", "always").lower()
        )
    )
    #: Kept so existing callers and the health report still have a boolean to
    #: read; "does this specific episode search" is now a per-question answer.
    enable_web_search: bool = field(
        default_factory=lambda: os.environ.get("ENABLE_WEB_SEARCH", "0") not in ("0", "false", "False", "")
    )
    max_web_searches: int = _env_int("MAX_WEB_SEARCHES", 3)  # a ceiling, not a target
    # How much the searching call may write back. It reports evidence rather
    # than an episode - a few sources with their passages - so this is small
    # on purpose: it is a ceiling on a research note, not on a script, and a
    # large one would let a model that misread the job write the episode here
    # instead. See `research.retrieve_with_claude`.
    research_max_tokens: int = _env_int("RESEARCH_MAX_TOKENS", 4000)
    # claude | exa - see RESEARCH_BACKENDS above. Only consulted when an
    # episode is actually being researched; an unresearched one costs nothing
    # either way.
    research_backend: str = field(
        default_factory=lambda: os.environ.get(
            "RESEARCH_BACKEND", DEFAULT_RESEARCH_BACKEND).strip().lower())
    # The three numbers the manual Exa benchmark hard-coded, which are exactly
    # the knobs worth sweeping. Their defaults reproduce that run: 8 results
    # fetched, the top 3 in the packet, 2 highlights each. Raising
    # `exa_packet_sources` buys more evidence and costs prompt tokens on every
    # researched episode; nobody has measured where that stops paying.
    exa_num_results: int = _env_int("EXA_NUM_RESULTS", 8)
    exa_packet_sources: int = _env_int("EXA_PACKET_SOURCES", 3)
    exa_highlights_per_source: int = _env_int("EXA_HIGHLIGHTS_PER_SOURCE", 2)
    # Whether the evidence packet carries a publication date and a publisher
    # beside each source.
    #
    # **On, and this is the cheapest quality fix in the codebase.** The packet
    # used to be title plus highlights, so an episode was asked whether
    # something happened last night or two days ago from evidence with no dates
    # in it. Exa was already returning both fields and `build_packet` was
    # discarding them. Set to 0 only to reproduce the hand-measured 2026-09-05
    # benchmark, whose numbers were taken on the undated shape.
    exa_dated_packet: bool = field(
        default_factory=lambda: os.environ.get("EXA_DATED_PACKET", "1")
        not in ("0", "false", "False", ""))
    # Whether a packet missing what the brief asked for buys one more search.
    #
    # One, never more, and never a model call to rephrase - the second search
    # drops the recency window and searches the resolved subject. A retry costs
    # about half a second and a fifth of a cent, and only on episodes that were
    # going to be thin; an unbounded loop would put an unbounded wait in front
    # of the first word.
    research_retry: bool = field(
        default_factory=lambda: os.environ.get("RESEARCH_RETRY", "1")
        not in ("0", "false", "False", ""))

    # --- Episode intelligence --------------------------------------------
    # The layer between the typed question and the search - see
    # `episode_intelligence.py` for what it is and why it sits there.
    #
    # **On.** It costs one model call in front of the first word on the search
    # path, which breaks CLAUDE.md's one-sentence spec, and that was a
    # deliberate decision: the writing is the product, and a fast episode about
    # the wrong thing is worth less than a slower one about the right thing.
    # The browse surfaces pay none of it - there the brief is built before the
    # tap. Set to 0 to get the old behaviour exactly: the raw query goes to
    # Exa, no structure, no why-now, no temporal cautions.
    episode_intelligence: bool = field(
        default_factory=lambda: os.environ.get("EPISODE_INTELLIGENCE", "1")
        not in ("0", "false", "False", ""))
    # Understanding a request is a small, well-specified extraction, not the
    # writing. It runs on the same model as the script by default so a
    # deployment has one model to reason about, and at low effort because the
    # time here is time the listener waits.
    ei_model: str = field(
        default_factory=lambda: os.environ.get(
            "EI_MODEL", os.environ.get("MODEL", "claude-sonnet-5")))
    ei_effort: str = field(
        default_factory=lambda: os.environ.get("EI_EFFORT", "low"))
    ei_max_tokens: int = _env_int("EI_MAX_TOKENS", 1200)
    # Past this, the brief is not worth the wait and the raw query is searched
    # instead. A ceiling rather than a target: EI must degrade to the old
    # behaviour rather than become a new way for an episode to hang.
    ei_timeout_seconds: float = _env_float("EI_TIMEOUT_SECONDS", 8.0)
    # The window applied to a question about a moment when the brief names no
    # other. Two weeks is wide enough to catch a story that broke over a
    # weekend and narrow enough to keep an old well-ranked explainer out of the
    # evidence for "what happened last night".
    ei_default_recency_days: int = _env_int("EI_DEFAULT_RECENCY_DAYS", 14)

    # --- live facts ------------------------------------------------------
    # The route around the article index, for the questions an index is
    # structurally too slow for. See `live_facts.py` and LIVE_FACTS.md.
    #
    # On by default because the *registry* is what matters: with no provider
    # configured it costs one dictionary lookup and its whole effect is that
    # the writer is told there is no live feed - which is the honest state and
    # the one that stops a score being invented. `LIVE_FACTS=0` silences even
    # that, and is for offline `write.py` runs rather than for production.
    live_facts: bool = field(
        default_factory=lambda: os.environ.get("LIVE_FACTS", "1")
        not in ("0", "false", "False", ""))
    # Which provider serves each domain. Empty means none, and none is
    # reported rather than hidden. `fake` is a deterministic stand-in for
    # tests and demos and must be selected explicitly - never a fallback,
    # which is §51 and §61 applied to facts: a stand-in that can be
    # reached by accident is one that reaches a listener.
    live_sports_provider: str = field(
        default_factory=lambda: os.environ.get("LIVE_SPORTS_PROVIDER", "").strip())
    live_markets_provider: str = field(
        default_factory=lambda: os.environ.get("LIVE_MARKETS_PROVIDER", "").strip())
    live_elections_provider: str = field(
        default_factory=lambda: os.environ.get("LIVE_ELECTIONS_PROVIDER", "").strip())
    # Per-provider and whole-lookup ceilings. Small on purpose: this sits in
    # front of the first word, and the one-sentence spec was amended once for
    # EI and not again. A provider that cannot answer in a second and a half
    # is a provider the listener should not be waiting for.
    live_timeout_seconds: float = _env_float("LIVE_TIMEOUT_SECONDS", 1.5)
    live_total_timeout_seconds: float = _env_float("LIVE_TOTAL_TIMEOUT_SECONDS", 2.5)
    # How long one entity's facts may be reused, by status. In-progress is
    # short enough to only collapse a burst of simultaneous listeners; final
    # does not move, so it is generous.
    live_cache_in_progress_seconds: float = _env_float(
        "LIVE_CACHE_IN_PROGRESS_SECONDS", 10.0)
    live_cache_scheduled_seconds: float = _env_float(
        "LIVE_CACHE_SCHEDULED_SECONDS", 300.0)
    live_cache_final_seconds: float = _env_float(
        "LIVE_CACHE_FINAL_SECONDS", 900.0)
    # What the fake scoreboard pretends is happening: scheduled, in_progress
    # or final. Here rather than read from the environment inside
    # `live_sources`, because every knob this app has belongs in one place -
    # a setting read somewhere else is a setting the hermetic test cannot see
    # and a developer's shell can leak into a suite.
    live_fake_sports_status: str = field(
        default_factory=lambda: os.environ.get(
            "LIVE_FAKE_SPORTS_STATUS", "in_progress").strip())

    # --- world trending --------------------------------------------------
    # The myFAM row that says what the *world* is paying attention to, as
    # opposed to "What FAM can't stop listening to", which is this app's own play
    # counts. A different subsystem from live facts on purpose - see
    # `trending.py`: one changes what is offered, the other what is said.
    #
    # On by default for the same reason `live_facts` is: with no source
    # configured its whole effect is that the row is honestly empty and says
    # why, which is the state worth shipping.
    trending: bool = field(
        default_factory=lambda: os.environ.get("TRENDING", "1")
        not in ("0", "false", "False", ""))
    # Empty means none, and none is shipped. `fake` is a deterministic
    # stand-in for tests and demos and must be asked for by name.
    trending_source: str = field(
        default_factory=lambda: os.environ.get("TRENDING_SOURCE", "").strip())
    # How long one refresh serves. This is the row's whole economics: one
    # upstream call per window, shared by every listener. Fifteen minutes
    # matches how fast a global news index actually moves.
    trending_ttl_seconds: float = _env_float("TRENDING_TTL_SECONDS", 900.0)
    # Generous next to the live-facts ceiling, because this never sits in
    # front of the first word - it refreshes in the background and myFAM
    # renders from the cache.
    trending_timeout_seconds: float = _env_float("TRENDING_TIMEOUT_SECONDS", 8.0)
    trending_max_items: int = _env_int("TRENDING_MAX_ITEMS", 6)

    # --- myFAM's story pool ----------------------------------------------
    # What "Made for you" and "Trending" are built from: live data turned
    # into candidate episodes - a title and an angle, never a script. See
    # `stories.py` for the cost design and MYFAM.md for the whole of it.
    #
    # On by default, and on the same reasoning as `trending` and `live_facts`:
    # the *registry* is what matters. With no provider configured its whole
    # effect is that myFAM offers its evergreen bank and `/api/health` says
    # which sources are missing and why - which is the state worth shipping,
    # because the alternative is a gap nobody can see.
    stories: bool = field(
        default_factory=lambda: os.environ.get("STORIES", "1")
        not in ("0", "false", "False", ""))
    # Empty means every source this build knows, each of which reports itself
    # as not configured when its credential is absent. A comma-separated list
    # narrows it - see `story_sources.BUILDERS` for the names.
    stories_sources: str = field(
        default_factory=lambda: os.environ.get("STORIES_SOURCES", "").strip())
    # One sweep and one composition serve every listener for this long. This
    # is the whole economics of the browse page: fifteen minutes is how fast a
    # global news index actually moves, and the per-source floors in
    # `story_sources` keep the providers with daily quotas off this clock.
    stories_ttl_seconds: float = _env_float("STORIES_TTL_SECONDS", 900.0)
    # Generous, because this never sits in front of the first word: the pool
    # refreshes in the background and myFAM renders from whatever it holds.
    stories_timeout_seconds: float = _env_float("STORIES_TIMEOUT_SECONDS", 12.0)

    # Whether a model writes the titles and angles. Off gives the templated
    # tiles instead - which is exactly what a deployment with no API key gets,
    # and is a real product rather than a placeholder. Kept as a switch so the
    # two can be compared on one machine.
    stories_compose: bool = field(
        default_factory=lambda: os.environ.get("STORIES_COMPOSE", "1")
        not in ("0", "false", "False", ""))
    # One call per refresh window for every listener, so this is the cheapest
    # model call in the product and still the one that decides what the whole
    # browse page says. Same default as the rest of the app: one model to
    # reason about per deployment.
    stories_model: str = field(
        default_factory=lambda: os.environ.get(
            "STORIES_MODEL", os.environ.get("MODEL", "claude-sonnet-5")))
    stories_effort: str = field(
        default_factory=lambda: os.environ.get("STORIES_EFFORT", "low"))
    stories_max_tokens: int = _env_int("STORIES_MAX_TOKENS", 3000)
    # A ceiling rather than a target. Nobody is waiting on this - a slow
    # composition costs one window of freshness, never a listener's wait.
    stories_compose_timeout_seconds: float = _env_float(
        "STORIES_COMPOSE_TIMEOUT_SECONDS", 25.0)

    # --- the category tree ------------------------------------------------
    #
    # Whether the vocabulary grows itself at all. On: the sweep that refreshes
    # the story pool also reads the event log for subjects people have
    # actually been searching for and mints nodes for the ones enough
    # different listeners used. Off leaves `topics.py`'s hand-written eight
    # facets and twenty-nine subtags exactly as they were, which is what every
    # deployment ran on before this existed.
    categories: bool = field(
        default_factory=lambda: os.environ.get("CATEGORIES", "1")
        not in ("0", "false", "False", ""))
    # Whether a model places a new subject in the tree. Off - or with no key -
    # a subject still gets a parent, from the facet its own sightings were
    # tagged with and from containment against the nodes already there; what
    # it does not get is the *levels nobody typed*. "American football" and
    # "NFL" exist only because a model named them, and no amount of reading
    # what listeners wrote invents a level none of them wrote.
    #
    # The same shape as `stories_compose`, and for the same reason: a
    # deployment with no key gets a real, shallower vocabulary rather than a
    # broken one, and `degraded` on every node says which it is looking at.
    categories_place: bool = field(
        default_factory=lambda: os.environ.get("CATEGORIES_PLACE", "1")
        not in ("0", "false", "False", ""))
    # One call per sweep for the whole deployment, batching every new subject
    # at once. Same default model as everything else here.
    categories_model: str = field(
        default_factory=lambda: os.environ.get(
            "CATEGORIES_MODEL", os.environ.get("MODEL", "claude-sonnet-5")))
    categories_effort: str = field(
        default_factory=lambda: os.environ.get("CATEGORIES_EFFORT", "low"))
    categories_max_tokens: int = _env_int("CATEGORIES_MAX_TOKENS", 4000)
    # A ceiling, not a target. Nobody waits on this: the tree that exists is
    # used and the new subjects are placed on the next sweep.
    categories_place_timeout_seconds: float = _env_float(
        "CATEGORIES_PLACE_TIMEOUT_SECONDS", 30.0)
    # How far back the promotion sweep reads. Long enough that a subject
    # somebody was interested in last month still counts toward the listener
    # threshold, short enough that the vocabulary tracks what people are
    # asking about now rather than what they asked a year ago.
    categories_window_days: int = _env_int("CATEGORIES_WINDOW_DAYS", 45)

    # How far a price has to move before it is worth an episode. A market
    # where nothing moved more than a percent has no story in it, and offering
    # one anyway is how a browse page fills with tiles nobody wants.
    stories_market_move_percent: float = _env_float(
        "STORIES_MARKET_MOVE_PERCENT", 3.0)
    # Which API-Sports products to sweep for today's card. Empty follows
    # API_SPORTS_SPORT, which is the one a deployment says it mostly serves.
    stories_sports: str = field(
        default_factory=lambda: os.environ.get("STORIES_SPORTS", "").strip())
    # Polymarket is keyless, so without a switch of its own it would be the
    # one source that turned itself on - and on a fresh deployment it would
    # then be the *only* live source, which would make the browse page a
    # betting slip. It is also the source a deployment is most likely to want
    # to decline outright. So it ships off and is named, like every other live
    # provider in this app: nothing here is absent and quiet.
    stories_polymarket: bool = field(
        default_factory=lambda: os.environ.get("STORIES_POLYMARKET", "0")
        not in ("0", "false", "False", ""))
    # The symbols Finnhub is asked about each sweep. Empty is the named list
    # in `story_sources.WATCHLIST`; the free tier has no movers endpoint, so a
    # fixed watchlist is the honest cheap version of "what moved today".
    finnhub_watchlist: str = field(
        default_factory=lambda: os.environ.get("FINNHUB_WATCHLIST", "").strip())

    # --- GDELT -----------------------------------------------------------
    # A second retrieval index beside Exa, and the source behind the Trending
    # row. Keyless - GDELT DOC 2.0 needs no credential - so the only switch
    # that matters is this one.
    #
    # Ships OFF. Not because it costs anything, but because nothing in this
    # build has ever made a real request to it: the container's egress proxy
    # blocks it, so every shape in `gdelt.py` is written from the docs and
    # tested against recorded payloads. Turn it on somewhere with network,
    # run `python tools/gdelt_probe.py`, and leave it on once that passes.
    gdelt: bool = field(
        default_factory=lambda: os.environ.get("GDELT", "0")
        not in ("0", "false", "False", ""))
    gdelt_timeout_seconds: float = _env_float("GDELT_TIMEOUT_SECONDS", 6.0)
    gdelt_max_records: int = _env_int("GDELT_MAX_RECORDS", 20)
    # Whether a researched episode asks GDELT as well as Exa. Separate from
    # `gdelt` so the Trending row can run without adding a second call to
    # every episode - they are different clocks and different budgets.
    gdelt_cross_check: bool = field(
        default_factory=lambda: os.environ.get("GDELT_CROSS_CHECK", "0")
        not in ("0", "false", "False", ""))

    # --- live provider credentials ---------------------------------------
    # One per vendor. Empty means that vendor is not configured, which
    # `/api/health` reports as its own state - never as "there is no live
    # information in the world". Keys are never written into source; these
    # come through the same chain as every other credential (process env >
    # FAM_SECRETS > .env > ~/.fam/env). See CREDENTIALS.md.
    api_sports_key: str = field(
        default_factory=lambda: os.environ.get("API_SPORTS_KEY", "").strip())
    # API-Sports is one API per sport - different host, different response
    # shape, different status codes - so a deployment says which one it mostly
    # serves. An explicit sport word in the question still overrides it. See
    # `live_sources.sport_for` for why team-name routing is not attempted.
    api_sports_sport: str = field(
        default_factory=lambda: os.environ.get(
            "API_SPORTS_SPORT", "american-football").strip())
    sportsdataio_key: str = field(
        default_factory=lambda: os.environ.get("SPORTSDATAIO_KEY", "").strip())
    finnhub_key: str = field(
        default_factory=lambda: os.environ.get("FINNHUB_KEY", "").strip())
    alpha_vantage_key: str = field(
        default_factory=lambda: os.environ.get("ALPHA_VANTAGE_KEY", "").strip())
    ap_elections_key: str = field(
        default_factory=lambda: os.environ.get("AP_ELECTIONS_KEY", "").strip())
    ddhq_key: str = field(
        default_factory=lambda: os.environ.get("DDHQ_KEY", "").strip())
    # Polymarket's public read API needs no credential.
    polymarket_base: str = field(
        default_factory=lambda: os.environ.get(
            "POLYMARKET_BASE", "https://gamma-api.polymarket.com").strip())

    # --- Prefetch ---------------------------------------------------------
    # Writing the episode before anybody asks for it - see `prefetch.py`.
    #
    # **On, at the owner's direction** (PROBLEMS.md §105). It shipped off on
    # the tier system's reasoning - the mechanism ready, the policy decided
    # with numbers - and the policy is now decided for the one surface that
    # can answer for itself: myFAM knows what somebody might tap before they
    # tap it, so the seconds episode intelligence costs there are seconds
    # nobody has to pay. What is on is the **cheap half** (see
    # `prefetch_level`): a brief, never a whole episode, so a wrong guess
    # costs one small model call rather than a script nobody hears.
    #
    # The ceilings below still bound it, `/api/health` still reports which
    # state a deploy is in, and `PREFETCH=0` restores the old behaviour
    # exactly: every tap pays for its own brief.
    prefetch: bool = field(
        default_factory=lambda: os.environ.get("PREFETCH", "1")
        not in ("0", "false", "False", ""))
    # brief | script - how much is paid in advance.
    #
    # `brief` runs contextual relevance only and keeps the result: one small
    # model call, and it removes the seconds episode intelligence costs. The
    # tap still pays retrieval and writing. `script` writes the whole episode,
    # so the tap pays nothing at all and a wrong guess costs a full episode.
    # Starting at `brief` buys most of the felt improvement for a fraction of
    # the waste, which is the right place to start with no hit rate in hand.
    prefetch_level: str = field(
        default_factory=lambda: os.environ.get("PREFETCH_LEVEL", "brief").strip().lower())
    # How many candidates one cycle may warm. Sources are interleaved, so this
    # is shared across them rather than being per source.
    prefetch_per_cycle: int = _env_int("PREFETCH_PER_CYCLE", 6)
    # A hard daily ceiling, in two currencies because they fail differently:
    # the count stops a runaway loop, the dollars stop a *correct* loop being
    # expensive - a 10-minute researched episode costs several times a
    # 1-minute one, so counting episodes alone does not bound the bill.
    prefetch_daily_episodes: int = _env_int("PREFETCH_DAILY_EPISODES", 50)
    # Briefs are counted apart from episodes and have their own ceiling,
    # because they cost a fraction of one. Counting a brief as an episode made
    # 50 briefs the whole day's warming - six per cycle, one cycle per browse -
    # so a deployment on the shipped level would stop before lunch with five
    # cents of the two dollars below spent, and nothing would say why.
    prefetch_daily_briefs: int = _env_int("PREFETCH_DAILY_BRIEFS", 400)
    prefetch_daily_dollars: float = _env_float("PREFETCH_DAILY_DOLLARS", 2.0)
    # How long the server must have gone without generating for a real
    # listener before it will spend on a guess. A speculative episode that
    # delays a real one has inverted the entire point of prefetching.
    prefetch_quiet_seconds: float = _env_float("PREFETCH_QUIET_SECONDS", 20.0)
    # The shortest gap between two cycles warmed for the same listener.
    #
    # myFAM schedules a cycle when it is drawn, and a browse page is drawn
    # often - opening the tab, coming back from a player, a pull to refresh.
    # Without a clock on it, one listener flicking between tabs would spend
    # the whole daily ceiling on the same six tiles. Five minutes is longer
    # than a browse and shorter than an episode, so a listener who comes back
    # to a genuinely different page gets a genuinely new cycle.
    prefetch_cycle_seconds: float = _env_float("PREFETCH_CYCLE_SECONDS", 300.0)
    # How long a warmed brief stays usable. A brief is a claim about *now* - a
    # why-now hypothesis and a recency window built this morning are wrong by
    # this evening - and a stale brief is worse than none, because it would
    # make the episode confidently about the wrong day.
    prefetch_brief_ttl_seconds: float = _env_float(
        "PREFETCH_BRIEF_TTL_SECONDS", 3600.0)
    # legacy | phase6 - see STREAMING_PIPELINES above.
    #
    # **phase6 is production.** It defaulted to `legacy` until the Phase 6 path
    # had been measured through the real interface; that is now done. Validated
    # on an RTX 4090 running this server with Chatterbox and the real prompt:
    # synthesis began before the model finished writing, playback stayed
    # continuous with no gap reaching the listener, the duration ceiling held,
    # and the first chunk was one complete thought with no word floor.
    #
    # `legacy` is kept, and kept tested, for exactly two things: the baseline
    # half of a comparison run (`tools/pod_production_test.sh` measures both on
    # one card), and the equivalence suite that proves the user-visible
    # contract survives the change of execution strategy. Nothing selects it
    # automatically - reaching it takes setting this variable by hand.
    #
    # An unrecognised value is refused at import rather than falling back: a
    # typo that quietly picks a pipeline is exactly the silent-success failure
    # this project has paid for more than once. `pipeline._phase6` refuses one
    # again at request time, so neither gate stands alone.
    streaming_pipeline: str = field(
        default_factory=lambda: os.environ.get(
            "STREAMING_PIPELINE", DEFAULT_PIPELINE).strip().lower()
    )

    cache_enabled: bool = field(
        default_factory=lambda: os.environ.get("CACHE_ENABLED", "1") not in ("0", "false", "False")
    )
    cache_backend: str = field(default_factory=lambda: os.environ.get("CACHE_BACKEND", "sqlite"))
    # Absolute, and derived from the project root when unset - a bare
    # filename would follow the working directory and quietly open a
    # different, empty cache. See paths.data_path.
    cache_path: str = field(
        default_factory=lambda: data_path("CACHE_PATH", "scripts.db")
    )
    # Default lifetime for a cached script.
    cache_ttl_seconds: int = _env_int("CACHE_TTL_SECONDS", 86400)
    # Lifetime for queries that read as time-sensitive ("latest", "today").
    cache_ttl_volatile: int = _env_int("CACHE_TTL_VOLATILE", 900)
    # How long an evergreen episode may be kept alive by being played (§134).
    # A play of an entry written at the ordinary ceiling pushes its expiry a
    # full `cache_ttl_seconds` forward, up to this age from when it was
    # written. The voice is a rented GPU, so an episode that keeps being
    # played is exactly the one whose stored audio is worth keeping - and a
    # fixed day from the first write threw it away on the busiest ones.
    # Anything shorter-lived (volatile words, a result-dependent question, a
    # one-day evidence window, a scheduled fixture) never slides, so nothing
    # here can make a claim about *now* outlive the window it was true in.
    # 0 turns sliding off and restores the fixed lifetime exactly.
    cache_max_age_seconds: int = _env_int("CACHE_MAX_AGE_SECONDS", 30 * 86400)
    # Use a small model to canonicalise queries before looking them up. Raises
    # the hit rate across differently-worded requests, at the cost of one fast
    # call (~400ms) in front of every request. See cache.canonical_key.
    cache_semantic_key: bool = field(
        default_factory=lambda: os.environ.get("CACHE_SEMANTIC_KEY", "0") not in ("0", "false", "False")
    )
    canonical_key_model: str = field(
        default_factory=lambda: os.environ.get("CANONICAL_KEY_MODEL", "claude-haiku-4-5")
    )

    # Match a question against *near* neighbours in the cache, not only the
    # identical one. The vector is computed when a script is written, so a
    # lookup costs a local scan (microseconds) rather than the model call
    # CACHE_SEMANTIC_KEY pays on every request. See embeddings.py.
    #
    # **On by default since §107**, which reverses the note this comment used
    # to carry. That note said to turn it on "once tools/bench_vector_cache.py
    # has been run on traffic that looks like yours", and the bench has now
    # been run:
    #
    #     without it   9 of 41 re-phrasings found an existing episode
    #     with it     23 of 41, with no false match at any threshold
    #     cost        8.93 ms scanning 400 vectors, on the miss path only
    #
    # Fourteen episodes not written, at roughly a cent each, for nine
    # milliseconds spent only when the exact key already missed. Nothing here
    # calls a model, so this is the one place in FAM where sharing more costs
    # nothing to try.
    #
    # The risk it accepts, unchanged and worth restating: a false near match
    # plays a confident answer to a question nobody asked, which breaks the
    # first duty of an episode. What makes it acceptable is *what refuses the
    # wrong pairs*. In the bench every must-not-collapse pair is refused by a
    # guard - identical numbers, lexical overlap, agreement about needing
    # today's facts - rather than by the cosine sitting just above it. The
    # cosine is the weak half and is measurably carrying nothing: the control
    # line, guards alone with the vector ignored, finds the same 23. That is
    # what a lexical embedding is worth, and it is the number to re-read on
    # the day a real sentence model is installed in ~/.fam/embed.
    #
    # That day was §131, and the number did not move: with all-MiniLM-L6-v2
    # the shipped point still finds 23/41, because the overlap guard decides.
    # Without the guard it finds 37 and serves "how old is the eiffel tower"
    # for "how tall" at 0.879 - so these defaults stand, and the model's
    # recall waits on a cross-encoder to check it.
    #
    # `CACHE_VECTOR=0` restores the old behaviour exactly.
    cache_vector: bool = field(
        default_factory=lambda: os.environ.get("CACHE_VECTOR", "1") not in ("0", "false", "False")
    )
    # Keep each episode's finished audio beside its script, so a cached episode
    # is replayed from the database and never synthesised again (§132, at the
    # owner's direction). The voice runs on a rented GPU, and replaying cached
    # episodes through it was the largest line on the bill. `0` restores
    # re-synthesis on every play exactly.
    audio_cache: bool = field(
        default_factory=lambda: os.environ.get("AUDIO_CACHE", "1") not in ("0", "false", "False")
    )
    # The byte ceiling for kept audio, least recently played out first. Sized
    # for render.yaml's 1 GB disk, which every other database shares: at about
    # 2 MB a compressed minute this is roughly 250 minutes of episodes. An
    # evicted episode keeps its script and costs one re-synthesis. 0 = none.
    audio_cache_max_mb: int = _env_int("AUDIO_CACHE_MAX_MB", 512)

    # Cosine a near match must clear, and the share of words it must literally
    # share. Both measured, not chosen: tools/bench_vector_cache.py sweeps them
    # against 41 re-phrasings that should collapse and 20 pairs that must not.
    # This is the highest-recall setting at which *every* must-not-collapse
    # pair is refused by a guard rather than by the threshold - so there is no
    # near miss waiting for a query slightly unlike the ones measured.
    cache_vector_threshold: float = _env_float("CACHE_VECTOR_THRESHOLD", 0.68)
    cache_vector_overlap: float = _env_float("CACHE_VECTOR_OVERLAP", 0.6)
    # Rows a near-match scan will look at, newest first.
    cache_vector_scan: int = _env_int("CACHE_VECTOR_SCAN", 400)

    # --- Duration / pacing ------------------------------------------------
    min_minutes: int = 1
    max_minutes: int = 10
    # Words per minute a natural narrator hits. Used to size the script.
    target_wpm: float = _env_float("TARGET_WPM", 150.0)
    # How far the pacing controller may push the voice to hit the clock.
    min_wpm: float = _env_float("MIN_WPM", 115.0)
    max_wpm: float = _env_float("MAX_WPM", 185.0)
    # Accept anything inside this fraction of the requested length.
    duration_tolerance: float = _env_float("DURATION_TOLERANCE", 0.03)

    # --- Audio ------------------------------------------------------------
    sample_rate: int = _env_int("SAMPLE_RATE", 22050)
    channels: int = 1
    sample_width: int = 2  # 16-bit signed little-endian PCM

    # --- TTS --------------------------------------------------------------
    # auto | espeak | say | debug
    # A **development** override, not a production setting. Production does
    # not choose an engine: there is one production slot
    # (`tts.PRODUCTION_ENGINES`), filled by Chatterbox. This names a
    # development engine for deterministic local tests, and `auto` - the
    # default, and the only value a deployment should ever have - means "the
    # production engine, or a placeholder tone if this machine cannot run it".
    # It cannot name a production engine into existence: nothing here is one.
    tts_engine: str = field(default_factory=lambda: os.environ.get("TTS_ENGINE", "auto"))
    # --- Which machine speaks --------------------------------------------
    # chatterbox | remote. Fills the one production slot.
    #
    # `chatterbox` is Chatterbox in this process, on this machine's card. It is
    # the default, and the default is load-bearing: a hosted or rented voice
    # must never be reachable because nothing else happened to be installed.
    # That is the guard PROBLEMS.md §61 removed with WellSaid - `default_voice()`
    # returns the first offered voice, so merely registering a second engine
    # made it what every listener got - and this is it, re-added deliberately.
    # Nothing here auto-detects: a deployment that wants the remote voice says
    # so, and one that says nothing gets the in-process engine it always had.
    #
    # `remote` is the same Chatterbox on a card somewhere else, reached over
    # HTTP by `remote_voice.py`. Same weights, same reference recording, same
    # generation settings - so this changes where the GPU is and what it costs,
    # not what a listener hears.
    voice_backend: str = field(
        default_factory=lambda: os.environ.get("VOICE_BACKEND", "chatterbox"))
    # --- The remote voice, when VOICE_BACKEND=remote ----------------------
    # runpod | http. The transport, and the only thing that differs between
    # RunPod Serverless (sleeps when idle, pays per second) and an always-on
    # pod (never cold, pays per hour). One worker image serves both, so moving
    # between them is this line plus the endpoint - never a code change.
    remote_voice_transport: str = field(
        default_factory=lambda: os.environ.get("REMOTE_VOICE_TRANSPORT", "runpod"))
    # transport=runpod: the endpoint id from the RunPod console, and the key
    # that authorises a job on it. The key goes through the credential chain
    # (process env, FAM_SECRETS, .env, ~/.fam/env) like every other secret.
    runpod_endpoint_id: str = field(
        default_factory=lambda: os.environ.get("RUNPOD_ENDPOINT_ID", ""))
    runpod_api_key: str = field(
        default_factory=lambda: os.environ.get("RUNPOD_API_KEY", ""))
    runpod_base_url: str = field(
        default_factory=lambda: os.environ.get(
            "RUNPOD_BASE_URL", "https://api.runpod.ai/v2"))
    # transport=http: the pod's base URL, and the shared secret it checks. An
    # exposed port with no token is somebody else's free TTS service billed to
    # your pod, so the worker warns at every boot when it is unset.
    remote_voice_url: str = field(
        default_factory=lambda: os.environ.get("REMOTE_VOICE_URL", ""))
    remote_voice_token: str = field(
        default_factory=lambda: os.environ.get("REMOTE_VOICE_TOKEN", ""))
    # The rate the worker's model emits. 24000 is Chatterbox's, and it must be
    # right *before* the first request: `app.py` writes the stream header from
    # `engine.sample_rate` before any audio has been asked for. The engine
    # refuses a reply that disagrees rather than playing it at the wrong pitch.
    remote_voice_sample_rate: int = _env_int("REMOTE_VOICE_SAMPLE_RATE", 24000)
    # Long enough to cover a cold serverless worker: container boot plus a ~10s
    # model load, on top of the synthesis itself. This is a ceiling on the
    # worst case, not a target - a warm worker answers a chunk in a couple of
    # seconds, and `remote_voice.wake()` exists so the worst case is rare.
    remote_voice_timeout: float = _env_float("REMOTE_VOICE_TIMEOUT", 180.0)
    remote_voice_connect_timeout: float = _env_float(
        "REMOTE_VOICE_CONNECT_TIMEOUT", 10.0)
    # How many chunks may be in flight at once. In-process Chatterbox is
    # pinned at one because a single card cannot run concurrent generations
    # safely; that is a property of the card, not of the interface, so a
    # remote backend that can fan out across workers sets its own ceiling.
    remote_voice_concurrency: int = _env_int("REMOTE_VOICE_CONCURRENCY", 4)
    # Which reference the worker clones, when it offers more than one. Blank
    # means the worker's own default, which is reference_3.
    remote_voice_id: str = field(
        default_factory=lambda: os.environ.get("REMOTE_VOICE_ID", ""))
    # Don't send a second wake within this many seconds: a serverless worker
    # that is already booting does not boot faster for being asked twice, and
    # each ask is a queued job.
    remote_voice_wake_interval: float = _env_float(
        "REMOTE_VOICE_WAKE_INTERVAL", 60.0)
    # --- Finding the worker, rather than being told where it is ----------
    # `voice_control.py` is the whole of this. The settings below decide how
    # hard FAM works to keep its own address for the voice correct while
    # RunPod moves pods around underneath it. PROBLEMS.md §112.
    #
    # auto | off. `off` is exactly the behaviour before the ladder existed:
    # the configured transport and address, nothing discovered, no failover.
    # An escape hatch for debugging, not a product decision - every rung of
    # the ladder is the same worker image speaking the same voice.
    voice_discovery: str = field(
        default_factory=lambda: os.environ.get("VOICE_DISCOVERY", "auto"))
    # The pod to look for through RunPod's API, by **name or id**, comma
    # separated if there is more than one. A name survives a pod being
    # destroyed and recreated from the same template; an id does not, which is
    # why the name is the thing to set.
    runpod_pod: str = field(
        default_factory=lambda: (os.environ.get("RUNPOD_POD")
                                 or os.environ.get("RUNPOD_POD_ID", "")))
    runpod_rest_url: str = field(
        default_factory=lambda: os.environ.get(
            "RUNPOD_REST_URL", "https://rest.runpod.io/v1"))
    runpod_graphql_url: str = field(
        default_factory=lambda: os.environ.get(
            "RUNPOD_GRAPHQL_URL", "https://api.runpod.io/graphql"))
    # The port inside the worker container. RunPod's proxy URL is built from
    # it, and a mismatch here is the 404 that reads like a missing route
    # (PROBLEMS.md §78).
    voice_worker_port: int = _env_int("VOICE_WORKER_PORT", 8001)
    # Whether a worker may be reached over plain HTTP on the open internet.
    #
    # Off, because the bearer token and every sentence of the script ride on
    # that connection in clear. It exists because the alternative turned out
    # to be worse: RunPod's proxy is the only TLS address a pod has, and that
    # proxy is Cloudflare, which refuses server-to-server requests it serves
    # to a browser - so on a pod the choice is a raw TCP port or no voice at
    # all (PROBLEMS.md §117). Switching it on is a decision about *this*
    # deployment's threat model, which is why it is a variable somebody sets
    # rather than a default somebody discovers.
    #
    # It widens nothing on its own: a plain address still has to be verified
    # like any other, and the token still has to match.
    voice_allow_plain_http: bool = field(
        default_factory=lambda: os.environ.get(
            "VOICE_ALLOW_PLAIN_HTTP", "0") not in ("0", "false", "False")
    )
    # A registration that has not been renewed inside this many seconds stops
    # being offered. Five minutes is five missed heartbeats.
    voice_registry_ttl: float = _env_float("VOICE_REGISTRY_TTL", 300.0)
    # The shared secret a worker presents to say where it is. **Unset means
    # registration is refused**, because an open registration endpoint lets
    # anybody redirect every script FAM writes to a machine of their own.
    voice_registry_token: str = field(
        default_factory=lambda: os.environ.get("VOICE_REGISTRY_TOKEN", ""))
    # How long a verified endpoint is trusted without being checked again.
    # The synth path pays nothing inside this window.
    voice_verify_ttl: float = _env_float("VOICE_VERIFY_TTL", 120.0)
    # How often the supervisor re-checks, so a pod that died is known before
    # a listener finds out. 0 switches the background loop off; the request
    # path still resolves on demand.
    voice_supervise_seconds: float = _env_float("VOICE_SUPERVISE_SECONDS", 60.0)
    # A ceiling on a control-plane probe. These run in front of a listener on
    # the first request after a change, so they are short by design: a health
    # check that hangs is worse than one that fails.
    voice_probe_timeout: float = _env_float("VOICE_PROBE_TIMEOUT", 8.0)
    # How long a failed endpoint is sorted to the back of the ladder. Never
    # dropped - if it is all there is, a stale failure must not be the reason
    # nobody can speak.
    voice_retry_seconds: float = _env_float("VOICE_RETRY_SECONDS", 60.0)
    # --- Chatterbox: the production voice --------------------------------
    # Where the model runs. `auto` picks cuda, then mps, and refuses cpu -
    # Chatterbox on a CPU is slower than speech, so an episode would starve.
    chatterbox_device: str = field(
        default_factory=lambda: os.environ.get("CHATTERBOX_DEVICE", "auto"))
    # The recording Chatterbox clones. Per-machine state, never in the repo:
    # it is somebody's voice. Defaults to reference_3.wav in the shared voice
    # folder, and a rights record must sit beside it clearing consent,
    # commercial use and synthetic voice, or the engine reports unavailable.
    chatterbox_reference: str = field(
        default_factory=lambda: os.environ.get("CHATTERBOX_REFERENCE", ""))
    # Per-machine voice state lives in one shared per-user folder
    # (~/.fam/voices by default), NOT inside the project, so a new version of
    # the app finds it already there instead of fetching it again. This is
    # where Chatterbox's reference recording lives. Override with
    # FAM_VOICES_DIR. See voice_store.py.
    voices_dir: str = field(default_factory=lambda: str(voice_store.voices_dir()))
    espeak_binary: str = field(
        default_factory=lambda: os.environ.get("ESPEAK_BIN", "espeak-ng")
    )
    espeak_voice: str = field(default_factory=lambda: os.environ.get("ESPEAK_VOICE", "en-us"))
    # macOS `say`: present on every Mac, so nothing needs installing there.
    say_binary: str = field(default_factory=lambda: os.environ.get("SAY_BIN", "say"))
    say_voice: str = field(default_factory=lambda: os.environ.get("SAY_VOICE", ""))

    # --- Server -----------------------------------------------------------
    host: str = field(default_factory=lambda: os.environ.get("HOST", "0.0.0.0"))
    port: int = _env_int("PORT", 8000)
    # Simple abuse guard: seconds between generations from one client.
    rate_limit_seconds: float = _env_float("RATE_LIMIT_SECONDS", 3.0)
    # How many of those seconds may be spent at once. A hard gate refused the
    # second of two taps a listener genuinely makes - switching voice, or
    # tapping the episode again while it was still loading - so the pace is a
    # small bucket instead. The sustained rate is unchanged; only a burst is
    # forgiven. 1 restores the old one-at-a-time gate.
    rate_limit_burst: int = _env_int("RATE_LIMIT_BURST", 3)
    # The cheap endpoints - JSON reads and cache lookups - need a ceiling, not
    # a pace. Opening a tab fires several at once, so anything that throttles
    # a burst throttles correct use. 0 switches it off.
    read_limit_per_window: int = _env_int("READ_LIMIT_PER_WINDOW", 60)
    # --- Public API -------------------------------------------------------
    # Tier quotas. **Off by default: the tier system is built, and not yet
    # switched on.** The whole mechanism stays - tiers, limits, counters,
    # reservations, refunds, the refusal and the screen it raises - and
    # `ENFORCE_QUOTAS=1` turns it on in one place on the day the product
    # decides to. What is deliberately not happening yet is *refusing a
    # listener*, because nothing sells them a way past a refusal: there is no
    # payment (ACCOUNTS.md), so an enforced free tier is a wall with no door.
    #
    # This was on by default, and the reasoning was sound as far as it went:
    # the thing it protects is a GPU and a metered API key reachable by anyone
    # who has the URL, and a ceiling that has to be switched on is a ceiling
    # that is off on the machine nobody checked. Two things answer that while
    # it is off. `_rate_limit` still paces every generation per listener, so
    # the server cannot be spun faster than it could before; and `metering.py`
    # still records every episode and what it cost, so spend is *visible*
    # even where it is not *capped*. Visible-and-uncapped is a deliberate
    # position for a beta with no checkout, not an oversight - and it is the
    # one thing to revisit before this is open to strangers at scale.
    enforce_quotas: bool = field(
        default_factory=lambda: os.environ.get("ENFORCE_QUOTAS", "0")
        not in ("0", "false", "False", "")
    )
    # Browser origins allowed to call this server, comma separated. Empty means
    # same-origin only, which is what a localhost run and the bundled interface
    # want. A native app is not a browser and sends no Origin, so it needs
    # nothing here - this exists for a web client served from somewhere else.
    #
    # Deliberately not defaulted to "*": with credentialed requests the browser
    # refuses that combination anyway, so a wildcard here would be a setting
    # that looks permissive, is not, and hides the real fix.
    api_origins: str = field(default_factory=lambda: os.environ.get("API_ORIGINS", ""))
    # Audiences accepted from a Google or Apple identity token, comma
    # separated: the iOS bundle id, and any web client id. Empty means that
    # provider is switched off, and `/api/health` says so rather than the
    # sign-in button failing at the point somebody presses it.
    google_client_ids: str = field(
        default_factory=lambda: os.environ.get("GOOGLE_CLIENT_IDS", ""))
    apple_client_ids: str = field(
        default_factory=lambda: os.environ.get("APPLE_CLIENT_IDS", ""))
    # Where this server is reachable from the internet, for share links.
    # Unset, a share still works and its link comes back relative - what must
    # never happen is a link that names `localhost` being posted to LinkedIn,
    # so nothing here invents a host. `sharing.py` says so, and the interface
    # shows the share as not-yet-public rather than pretending.
    public_base_url: str = field(
        default_factory=lambda: os.environ.get("PUBLIC_BASE_URL", "").rstrip("/"))
    # Where a share recipient is sent when they press anything that is not
    # play. Unset on every deployment until the app actually ships, and
    # nothing here invents a URL: an App Store link that 404s is a worse
    # first impression than no button, so the landing page draws those
    # controls only when this is set (see `sharing.landing_payload`). The
    # same rule `public_base_url` above keeps, for the same reason.
    app_store_url: str = field(
        default_factory=lambda: os.environ.get("APP_STORE_URL", "").strip())
    # How much *audio* must exist before the response starts. A quantity, not
    # a delay: at TARGET_WPM this is 3.75 words, so any ordinary opening
    # sentence satisfies it on the first chunk and it costs nothing. It exists
    # because models stream in bursts, and because it is the last point at
    # which a failed generation can still become an HTTP error rather than a
    # silent empty episode. See app.PREROLL_SECONDS.
    preroll_seconds: float = _env_float("PREROLL_SECONDS", 1.5)

    def __post_init__(self) -> None:
        """Refuse a configuration that names a pipeline that does not exist.

        Deliberately at construction, so it also catches
        `dataclasses.replace(settings, ...)`, which several call sites use,
        and not only the environment. The app failing to start is the
        correct outcome: a misconfigured deployment that serves the wrong
        generation path is worse than one that refuses to serve.
        """
        if self.preroll_seconds <= 0:
            # Zero is not "no preroll", it is a broken contract: on `fmt=wav`
            # the 44-byte header alone satisfies a zero gate, the
            # empty-episode guard then fires, and a perfectly good episode
            # comes back as a 502.
            raise ValueError(
                f"PREROLL_SECONDS={self.preroll_seconds} must be greater than "
                "zero. At zero a streamed WAV's header alone satisfies the "
                "gate and every episode is refused as empty."
            )
        if self.streaming_pipeline not in STREAMING_PIPELINES:
            raise ValueError(
                f"STREAMING_PIPELINE={self.streaming_pipeline!r} is not a "
                f"pipeline. Use one of: {', '.join(STREAMING_PIPELINES)}."
            )
        if self.voice_backend not in VOICE_BACKENDS:
            # Refused at construction, like the pipeline above and for the same
            # reason: a typo here ("runpod" where "remote" was meant) would
            # otherwise fall through to no production engine at all, and the
            # deployment would serve a placeholder tone that sounds like a
            # broken GPU rather than a misspelled variable.
            raise ValueError(
                f"VOICE_BACKEND={self.voice_backend!r} is not a voice backend. "
                f"Use one of: {', '.join(VOICE_BACKENDS)}.")
        if self.voice_backend == "remote":
            if self.remote_voice_transport not in VOICE_TRANSPORTS:
                raise ValueError(
                    f"REMOTE_VOICE_TRANSPORT={self.remote_voice_transport!r} is "
                    f"not a transport. Use one of: {', '.join(VOICE_TRANSPORTS)}.")
            if self.remote_voice_sample_rate <= 0:
                raise ValueError(
                    f"REMOTE_VOICE_SAMPLE_RATE={self.remote_voice_sample_rate} "
                    "must be positive: it is the rate the stream header claims "
                    "before the first byte of audio exists.")
            if self.remote_voice_concurrency < 1:
                raise ValueError(
                    f"REMOTE_VOICE_CONCURRENCY={self.remote_voice_concurrency} "
                    "must be at least 1; zero would deadlock every episode.")
            if str(self.voice_discovery).strip().lower() not in VOICE_DISCOVERY:
                # Refused rather than treated as `off`: a typo that quietly
                # switched discovery off would look exactly like the old
                # behaviour, which is the failure the ladder exists to end.
                raise ValueError(
                    f"VOICE_DISCOVERY={self.voice_discovery!r} is not a "
                    f"setting. Use one of: {', '.join(VOICE_DISCOVERY)}.")
        if self.research_backend not in RESEARCH_BACKENDS:
            raise ValueError(
                f"RESEARCH_BACKEND={self.research_backend!r} is not a research "
                f"backend. Use one of: {', '.join(RESEARCH_BACKENDS)}."
            )
        if self.prefetch_level not in PREFETCH_LEVELS:
            raise ValueError(
                f"PREFETCH_LEVEL={self.prefetch_level!r} is not a warm level. "
                f"Use one of: {', '.join(PREFETCH_LEVELS)}. Refusing rather "
                "than picking one - an unrecognised level that quietly meant "
                "`script` would spend a full episode per guess."
            )
        for name in ("exa_num_results", "exa_packet_sources",
                     "exa_highlights_per_source"):
            if getattr(self, name) < 1:
                raise ValueError(
                    f"{name.upper()}={getattr(self, name)} must be at least 1. "
                    "Zero would send Claude an empty evidence packet and call "
                    "it research.")
        if self.exa_packet_sources > self.exa_num_results:
            raise ValueError(
                f"EXA_PACKET_SOURCES={self.exa_packet_sources} exceeds "
                f"EXA_NUM_RESULTS={self.exa_num_results}: the packet cannot "
                "hold more sources than were fetched.")

    @property
    def bytes_per_second(self) -> int:
        return self.sample_rate * self.channels * self.sample_width


settings = Settings()


def describe_key(key: str = "") -> str:
    """A safe fingerprint of the key in force, for error messages.

    "invalid x-api-key" looks the same whichever wrong key produced it, and the
    first question is always whether the one being sent is the one you think.
    Never prints enough to be a secret: a prefix, a length and the last four.
    """
    # The key in force outranks the one `settings` captured at import: after a
    # rotation or a failover they are different strings, and the whole point of
    # a fingerprint is to say which one was actually sent.
    key = key or credentials.active("ANTHROPIC_API_KEY") or settings.anthropic_api_key
    if not key:
        return "no key configured"
    shape = "looks like an API key" if key.startswith("sk-ant-") else (
        "DOES NOT start with sk-ant- - is this an API key?")
    return f"{key[:8]}...{key[-4:]} ({len(key)} chars, {shape})"
