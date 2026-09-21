"""The startup topics: what a listener FAM knows nothing about is offered.

Somebody who taps "Continue as guest" and then "Skip for now" has an empty
event log, so every rail on myFAM scores them zero against every tile and the
one that matters most comes back empty. That is correct behaviour for a ranker
and the wrong product - it is the only impression of FAM that listener will
ever form for free.

`startup.py` is the answer and this file pins the five things about it that
would each decay quietly:

* **it fills the rail** - a cold start's first row is not empty;
* **it is about today** - every question is time-anchored, so the episode is
  researched fresh on the tap rather than being an evergreen explainer;
* **it claims nothing** - a tile written before anything is retrieved carries
  a question, never a result (PROBLEMS.md §88, §102);
* **it takes itself down** - one real signal and the ranker takes over for
  good, which is what makes it "the algorithm before the algorithm";
* **it hands over cleanly** - a play on a startup tile teaches the taste model
  the facet it was about, which is the whole point of going first.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import startup  # noqa: E402
import topics as T  # noqa: E402


@pytest.fixture
def store():
    return T.EventStore(":memory:")


# --------------------------------------------------------------------------
# The set itself
# --------------------------------------------------------------------------

def test_one_question_per_facet_and_every_facet_covered():
    """The size of the set is a decision, not a number somebody liked.

    A cold start knows nothing about which of the eight facets this listener
    wants, so complete coverage with no guess about which is the only
    principled shape. Fewer means some listeners silently get a worse first
    page; more is a guess dressed as an editorial decision.
    """
    facets = [t.tags[0] for t in T.STARTUP_TOPICS]
    assert sorted(facets) == sorted(T.TAG_LABELS)
    assert len(T.STARTUP_TOPICS) == len(T.TAG_LABELS) == 8


def test_every_startup_topic_carries_exactly_its_facet_and_nothing_finer():
    """One tag each, and it is a facet.

    Two reasons that agree. It is true - "the world this week" is the whole
    facet, not a corner of it. And the startup prior is over facets, so a
    subtag would contribute nothing to `_affinity` while still counting in
    its `sqrt(len(tags))` denominator: a two-tag startup topic would score
    below an identical one-tag topic for no reason any listener benefits from.
    """
    for topic in T.STARTUP_TOPICS:
        assert len(topic.tags) == 1, topic.id
        assert topic.tags[0] in T.FACETS
        assert topic.tags[0] not in T.TAG_PARENT, f"{topic.id} carries a subtag"


def test_every_query_is_time_anchored():
    """Where the freshness actually comes from.

    These are not evergreen questions with a fresh coat of paint: each asks
    about now, and `SEARCH_MODE=always` means the episode is researched on the
    tap. That is what lets a startup tile cost nothing at page load and still
    be up to date, and it is why the set needs no live provider configured.
    """
    now_words = ("this week", "right now", "recently", "past week", "recent")
    for topic in T.STARTUP_TOPICS:
        assert any(w in topic.query.lower() for w in now_words), topic.id


def test_no_startup_tile_asserts_a_fact_about_the_world():
    """§88 and §102, one layer earlier.

    Nothing has been retrieved when this page is drawn, so a tile may carry a
    question and never an answer. The specific failure this rules out is a
    title or hook that says a thing happened, or how it came out - a tile is
    read as a claim, and a wrong one is §88 with a larger audience.
    """
    banned = ("won", "lost", "beat", "final", "wins", "loses", "defeated",
              "record high", "record low", "surged", "crashed", "collapsed",
              "announced", "resigned", "died", "elected")
    for topic in T.STARTUP_TOPICS:
        text = f"{topic.title} {topic.subtitle}".lower()
        for word in banned:
            assert word not in text, f"{topic.id} asserts {word!r}"
        # No digits either: a number on a standing tile is a measurement that
        # was true once, and nothing ever comes back to check it.
        assert not any(c.isdigit() for c in text), topic.id


def test_hooks_fit_the_card_they_are_drawn_on():
    """A clamp is the one failure that hides itself.

    `.seed-why` is two clamped lines, so an over-long hook is cut mid-word and
    nothing says so. Asserted on the strings rather than measured in a
    browser, because PROBLEMS.md §115 is what happens when a layout assertion
    depends on which fonts the machine running it happens to have.
    """
    for topic in T.STARTUP_TOPICS + T.TOPIC_BANK:
        assert topic.subtitle, f"{topic.id} has no hook"
        assert len(topic.subtitle) <= startup.MAX_HOOK, \
            f"{topic.id}: {len(topic.subtitle)} > {startup.MAX_HOOK}"


def test_ids_are_prefixed_and_cannot_collide_with_the_bank():
    for topic in T.STARTUP_TOPICS:
        assert topic.id.startswith(startup.ID_PREFIX)
        assert topic.id not in T.BANK_BY_ID


def test_startup_topics_are_not_in_the_bank():
    """They are a third inventory, not twenty-eight plus eight.

    The bank serves every rail; this set serves one rail for one kind of
    listener. Letting these into `TOPIC_BANK` would put a cold-start tile in
    "What FAM can't stop listening to" and in the mix picker, which are both
    surfaces where a question written for a first impression has no business.
    """
    assert not set(T.BANK_BY_ID) & set(T.STARTUP_BY_ID)
    ranked = {t.id for t in T.rank_bank({})}
    assert not ranked & set(T.STARTUP_BY_ID)


# --------------------------------------------------------------------------
# The prior
# --------------------------------------------------------------------------

def test_the_prior_covers_every_facet_and_decays(store):
    """A flat prior is the `topic.id` sort §98 took out of the picker.

    All eight facets are present - not the six the picker shows, because
    leaving two at zero would make two of the eight startup topics
    unofferable to the listener who wanted exactly those - and each is worth
    less than the one above it so the ordering means something.
    """
    prior, source = T.startup_profile(store)
    assert set(prior) == set(T.TAG_LABELS)
    assert source == "default"
    values = list(prior.values())
    assert values == sorted(values, reverse=True)
    assert len(set(values)) == len(values), "a flat prior orders nothing"
    # And every weight stays clear of the floor, or the last facet is
    # effectively unofferable.
    assert min(values) > T.RELEVANCE_FLOOR


def test_the_prior_says_whether_it_measured_or_declared(store):
    """A declared order and a measured one look identical on screen.

    Same contract as `popular_facets`, whose answer this is: a fresh
    deployment is showing a decision somebody wrote down, and calling that
    "what people listen to" would be inventing a number.
    """
    _prior, source = T.startup_profile(store)
    assert source == "default"
    # A single-facet topic, deliberately: `sleep-science` carries `health` and
    # `science` both, so playing it could not show which facet led.
    assert T.facets_only(T.BANK_BY_ID["anxiety-loop"].tags) == ["health"]
    for _ in range(3):
        store.record(T.Event("someone", "play", "anxiety-loop", "worry",
                             T.BANK_BY_ID["anxiety-loop"].tags))
    prior, source = T.startup_profile(store)
    assert source == "played"
    # What the crowd plays now leads the prior, over the declared order.
    assert max(prior, key=prior.get) == "health"
    assert prior["health"] > prior["world"]


# --------------------------------------------------------------------------
# The feed
# --------------------------------------------------------------------------

def test_a_cold_start_gets_a_full_first_rail(store):
    """The whole point. This rail used to be empty."""
    feed = T.build_feed(store, "brand-new", interests=())
    first = feed["sections"][0]
    assert first["key"] == "from_history"
    assert len(first["topics"]) == T.SECTION_SIZE
    assert not first["empty_reason"]
    assert feed["taste_source"] == "startup"


def test_the_startup_set_leads_the_cold_start_rail(store):
    """Leading is by construction, not by score.

    What makes these right for this listener is that they are about today and
    the bank is about always, and no score can say that without borrowing
    `freshness` from the live pool - which would be a lie about that field.
    """
    feed = T.build_feed(store, "brand-new", interests=())
    ids = [t["id"] for t in feed["sections"][0]["topics"]]
    assert all(i.startswith(startup.ID_PREFIX) for i in ids), ids


def test_the_cold_start_rail_is_not_headed_made_for_you():
    """The heading is a claim, and on a cold start it is not this listener's.

    Pinned in the interface source, because the rail stays where it is and
    only its heading changes - so the thing that could silently regress is
    one branch in one function.
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    page = open(os.path.join(here, "static", "index.html")).read()
    body = page[page.index("function madeForTitle"):]
    body = body[:body.index("\n  }")]
    assert 'myfamTasteSource === "startup"' in body
    assert "Start here" in body


def test_one_real_signal_and_the_ranker_takes_over(store):
    """"The algorithm before the algorithm" - scaffolding that comes down.

    Derived from an empty `taste` rather than from a "they skipped the intro"
    flag, which is strictly better: a flag cannot say whether behaviour has
    since arrived.
    """
    store.record(T.Event("listener", "complete", "su-sports", "sport",
                         T.tags_for_id("su-sports")))
    feed = T.build_feed(store, "listener", interests=())
    assert feed["taste_source"] == "taste"
    ids = [t["id"] for t in feed["sections"][0]["topics"]]
    assert ids, "a listener with taste still gets a rail"
    assert not any(i.startswith(startup.ID_PREFIX) for i in ids), ids
    # And what it took over with is about the facet they actually played.
    assert any("sports" in T.BANK_BY_ID[i].tags for i in ids if i in T.BANK_BY_ID)


def test_choosing_interests_is_not_a_cold_start(store):
    """The set is for somebody who said nothing, and only them.

    A listener who picked interests in the intro told us something, so using
    the prior would be overriding their answer with the crowd's.
    """
    feed = T.build_feed(store, "chose", interests=("culture",))
    assert feed["taste_source"] == "taste"
    ids = [t["id"] for t in feed["sections"][0]["topics"]]
    assert not any(i.startswith(startup.ID_PREFIX) for i in ids), ids


def test_only_the_first_rail_gets_the_prior(store):
    """Every other rail would be claiming something a prior cannot support.

    "What your friends are listening to" and "What went past you this week"
    are statements about a graph and an impression log, and a cold start has
    neither. They stay honestly empty - which is the rule this whole page is
    built on, and the reason the prior is allowed on the one rail whose
    question is answerable for a stranger.
    """
    feed = T.build_feed(store, "brand-new", interests=())
    by_key = {s["key"]: s for s in feed["sections"]}
    for key in ("missed", "followers"):
        assert by_key[key]["topics"] == []
        assert by_key[key]["empty_reason"]


def test_view_more_opens_on_the_same_ranking(store):
    """One ranker, two views.

    A startup rail whose "View more" ran the ordinary ranker would open on
    exactly the empty list the rail exists to avoid - two different answers
    to one question, which is the thing `build_section` exists not to give.
    """
    section = T.build_section(store, "brand-new", "from_history")
    assert section["taste_source"] == "startup"
    ids = [t["id"] for t in section["topics"]]
    rail = [t["id"] for t in
            T.build_feed(store, "brand-new")["sections"][0]["topics"]]
    # The rail is the head of the screen, in the same order.
    assert ids[:len(rail)] == rail
    # And the screen carries the rest of the set the rail had no room for.
    assert set(T.STARTUP_BY_ID) <= set(ids)


# --------------------------------------------------------------------------
# Handover
# --------------------------------------------------------------------------

def test_a_startup_tile_is_resolvable_by_id(store):
    """A tap has to be able to say what it was about.

    These queries are written for a research backend, not for
    `tags_for_text`: "the most consequential world news story of the past
    week" contains none of the keywords `world` is matched on. So without
    this lookup the *first* episode of somebody's history - the one event
    that decides whether the ranker ever learns anything - would be logged
    with no tags at all, and the set that exists to bootstrap a taste model
    would teach it nothing.
    """
    for topic in T.STARTUP_TOPICS:
        assert T.tags_for_id(topic.id, topic.query) == topic.tags
        assert topic.id in T.known_topics()


def test_the_words_of_a_startup_query_would_not_have_found_its_facet():
    """Pins the reason the lookup above exists rather than the lookup.

    If this ever starts passing by keyword, the test above is still right and
    this note is what says it was load-bearing at the time.
    """
    missed = [t.id for t in T.STARTUP_TOPICS
              if t.tags[0] not in T.tags_for_text(t.query)]
    assert missed, "no startup query needs the id lookup any more"


def test_nothing_on_the_cold_start_path_generates_anything(store):
    """The one thing this page may never do.

    `build_feed` reads two caches and an event log and returns; the startup
    set is eight hand-written strings. So adding it cannot have put a model
    call in front of a browse tap, which is the rule the whole module rests
    on.
    """
    import inspect
    for fn in (T.build_feed, T.rank_startup, T.startup_profile):
        src = inspect.getsource(fn)
        assert "await" not in src, fn.__name__
        assert "client" not in src, fn.__name__
    assert "anthropic" not in inspect.getsource(startup)


# --------------------------------------------------------------------------
# The card's second line
# --------------------------------------------------------------------------

def _page() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(os.path.join(here, "static", "index.html")).read()


def test_a_browse_card_describes_the_episode_not_the_rail():
    """The line under a tile's title answers "is this worth three minutes".

    It used to answer "why is this card here" - "Because of what you have
    played", "Playing across FAM now" - which is a fact about the feed and no
    help at all to somebody choosing. The order is `angle`, then `subtitle`:
    a live tile's claim about today beats a standing hook, and every tile has
    a hook, which is what makes the line reliable.
    """
    page = _page()
    body = page[page.index("function seedHook"):]
    body = body[:body.index("\n  }")]
    angle = body.index("topic.angle")
    sub = body.index("topic.subtitle")
    assert angle < sub, "a claim about today must beat a standing hook"
    for rail_reason in ("Because of what you have played",
                        "Playing across FAM now"):
        assert body.index(rail_reason) > sub, \
            f"{rail_reason!r} is reachable before the episode's own hook"


def test_the_rail_and_the_section_screen_draw_one_line():
    """Two spellings of the card's second line is how one of them rots.

    The section screen showed `subtitle` while the rail showed the rail's
    reason, which is how one tile ended up with two different second lines
    depending on which surface was drawing it.
    """
    page = _page()
    assert page.count("seedHook(") == 3, "one definition, two call sites"
    assert "seedWhy" not in page, "the old name is still reachable"


def test_the_hook_line_is_clamped():
    """Cards in a rail are one height whatever their hooks are.

    Paired with `MAX_HOOK` above: the clamp keeps the layout and the length
    budget keeps the clamp from hiding a cut word.
    """
    page = _page()
    block = page[page.index(".seed-why{"):]
    block = block[:block.index("}")]
    assert "-webkit-line-clamp:2" in block


def test_the_prior_cannot_confirm_its_own_guess(store):
    """The loop this would otherwise close.

    The prior decides what every cold-start listener is offered, and their
    first play is by definition a play of what it offered them - so if those
    plays fed `popular_facets` the prior would spend the rest of the
    deployment confirming its opening guess. Same failure as an impression
    becoming taste, through a different door.

    What it does *not* cost: the play still enters that listener's own taste
    in full, which is what retires the prior for them immediately.
    """
    before, _src = T.startup_profile(store)
    for _ in range(20):
        store.record(T.Event("a-stranger", "complete", "su-culture", "",
                             T.tags_for_id("su-culture")))
    after, source = T.startup_profile(store)
    assert after == before, "startup plays voted on what the next stranger sees"
    assert source == "default", "the crowd signal is still unmeasured"
    # But the listener who played it is no longer a cold start.
    assert T.build_feed(store, "a-stranger")["taste_source"] == "taste"


def test_a_startup_tile_declined_often_enough_makes_way(store):
    """Eight tiles into a rail of six needs fatigue, or two are unreachable.

    A cold start has no taste to re-rank on, so without damping a listener who
    keeps opening myFAM and never tapping sees the same six in the same order
    forever. It can only reorder and never shorten: no floor is applied to the
    lead, `FATIGUE_FLOOR` keeps the multiplier positive, and `FATIGUE_GRACE`
    means being sent a tile twice is not yet evidence of anything.
    """
    prior, _src = T.startup_profile(store)
    fresh = [t.id for t in T.rank_startup(prior, set(), T.SECTION_SIZE)]
    assert len(fresh) == T.SECTION_SIZE
    buried, spare = fresh[0], [t.id for t in T.STARTUP_TOPICS
                               if t.id not in fresh]
    assert spare, "the set is no longer larger than the rail"

    # Shown on many separate occasions and never played.
    damped = T.rank_startup(prior, set(), T.SECTION_SIZE,
                            damp={buried: T.FATIGUE_FLOOR})
    ids = [t.id for t in damped]
    assert len(ids) == T.SECTION_SIZE, "damping shortened the rail"
    assert ids[0] != buried, "the declined tile is still leading"
    assert buried in ids or set(spare) & set(ids), \
        "nothing moved, so the bottom of the set is unreachable"


def test_fatigue_reaches_the_startup_rail_through_build_feed(store):
    """The wiring, not just the ranker.

    `build_feed` computes one fatigue table for the page; this asserts the
    cold-start rail actually receives it, which is the half that was missing
    when `rank_startup` accepted `damp` and used it only for the top-up.
    """
    import inspect
    src = inspect.getsource(T.rank_startup)
    lead = src[src.index("lead.sort"):src.index("lead = lead[:limit]")]
    assert "damp" in lead, "the lead is ranked without the fatigue table"
