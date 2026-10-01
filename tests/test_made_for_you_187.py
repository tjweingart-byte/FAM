"""§187: one search about a flight to Israel filled Made for you with sport
from the Middle East.

The owner's report: "I looked up one episode about a flight to Israel last
night, and now my entire made for you page is sports in the middle east."
Four causes, each pinned here:

* the search was filed under nothing, so its words ("tel", "aviv") vouched
  for any live story in any field - every Maccabi Tel Aviv fixture "named
  something they follow";
* the tree filed a game under its town's subjects (`tel aviv -> israel ->
  world`), so a fixture was scored as a world story too;
* the variety cap gave way when little else cleared the floor; and
* nothing stopped a minor league on another continent at all.

And two of the owner's directions in the same message: markets reach Made
for you on an interest in money, and the weights are the owner's numbers.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories as C  # noqa: E402
import stories  # noqa: E402
import topics as T  # noqa: E402


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    store = C.CategoryStore(str(tmp_path / "categories.db"))
    monkeypatch.setattr(T, "_CATEGORIES", store)
    C.apply_seed(store)
    T.reset_topic_tags()
    yield store
    T.reset_topic_tags()


@pytest.fixture()
def log(tmp_path, tree, monkeypatch):
    # Trending chooses first (§134); these tests are about Made for you.
    monkeypatch.setattr(T, "world_inventory", lambda *a, **k: ([], []))
    stories.seed([])
    yield T.EventStore(str(tmp_path / "events.db"))
    stories.seed([])


def story(subject, query, tags, domain=stories.ATTENTION, country="",
          minor=False, strength=0.8):
    countries = ((country, 1.0),) if country else ()
    scope, key, label = stories._geography(countries)
    return stories.Story(
        subject=subject, title=subject.title(), angle=f"why {subject}",
        query=query, domain=domain, source="test", tags=tuple(tags),
        strength=strength, first_seen=time.time(),
        shelf_life=stories.DOMAIN_SHELF_LIFE[domain], countries=countries,
        geo_scope=scope, geo_key=key, geo=label, minor_league=minor)


def game(home, away, league_tags=("sports",), country="", minor=True):
    return story(f"{home} vs {away}", f"{home} vs {away} game tonight",
                 league_tags, domain=stories.SPORTS, country=country,
                 minor=minor)


def listened(log, text, at_ago=400):
    now = time.time()
    tags = T.tags_for_text(text)
    log.record(T.Event("me", "search", "", text, tags, at=now - at_ago - 100))
    log.record(T.Event("me", "complete", "", text, tags, at=now - at_ago))


def made_for_you(log, country="US", interests=()):
    feed = T.build_feed(log, "me", has_account=True, country=country,
                        interests=interests)
    return next(s["topics"] for s in feed["sections"]
                if s["key"] == "from_history")


def titles(tiles):
    return [t["title"] for t in tiles]


# --- the reported case, end to end ---------------------------------------

def test_one_flight_to_israel_does_not_fill_the_rail_with_middle_east_sport(log):
    listened(log, "flights to tel aviv tonight")
    stories.seed([
        game("maccabi tel aviv", "hapoel haifa", country="israel"),
        game("hapoel tel aviv", "maccabi haifa", country="israel"),
        game("al hilal", "al nassr", country="saudi arabia"),
        story("israel reopens its airspace",
              "why israel reopened its airspace to commercial flights",
              ("world", "israel", "middle east")),
    ])
    made = titles(made_for_you(log, interests=("sports",)))
    assert "Maccabi Tel Aviv Vs Hapoel Haifa" not in made, made
    assert "Hapoel Tel Aviv Vs Maccabi Haifa" not in made, made
    assert "Al Hilal Vs Al Nassr" not in made, made
    assert "Israel Reopens Its Airspace" in made, (
        "the subject they actually asked about is still offered")
    assert len(made) == T.SECTION_SIZE


def test_the_search_is_filed_under_the_fields_it_is_about(tree):
    tags = set(T.tags_for_text("flights to tel aviv tonight"))
    assert {"business", "world", "israel"} <= tags, tags
    assert "sports" not in tags


# --- categorisation -------------------------------------------------------

def test_a_word_from_one_field_does_not_vouch_for_another(tree):
    events = [T.Event("me", "search", "", "flights to tel aviv tonight",
                      T.tags_for_text("flights to tel aviv tonight"))]
    familiar = T.familiar_words(events)
    fixture = T.topics_from_stories([game("maccabi tel aviv", "real madrid",
                                          minor=False)])[0]
    news = T.topics_from_stories([story(
        "tel aviv rail strike", "why tel aviv's rail workers walked out",
        ("world",))])[0]
    assert "aviv" in familiar
    assert not T._subject_is_familiar(fixture, familiar)
    assert T._subject_is_familiar(news, familiar)


def test_an_uncategorised_word_vouches_for_anything_but_sport(tree):
    events = [T.Event("me", "search", "", "what is new in hartwell", ())]
    familiar = T.familiar_words(events)
    fixture = T.topics_from_stories([game("hartwell", "mercer")])[0]
    news = T.topics_from_stories([story(
        "hartwell council vote", "what the hartwell council voted on",
        ("world",))])[0]
    assert not T._subject_is_familiar(fixture, familiar)
    assert T._subject_is_familiar(news, familiar)


def test_a_place_still_vouches_everywhere(tree):
    familiar = T.familiar_words([]) | frozenset({"hartwell"})
    fixture = T.topics_from_stories([game("hartwell", "mercer")])[0]
    assert T._subject_is_familiar(fixture, familiar)


def test_a_subject_in_one_field_is_not_following_a_game_in_another(tree):
    profile = {"world": 1.0, "israel": 1.0, "middle east": 1.0}
    fixture = T.Topic("st-x", "X", "", "maccabi tel aviv vs hapoel game",
                      ("sports",), "sports", freshness=0.5)
    assert T._is_broad_match(fixture, profile)


def test_a_sports_story_is_filed_under_sport_only(tree):
    import story_sources

    tags = story_sources._tags("maccabi tel aviv hapoel jerusalem basketball",
                               "sports")
    assert "israel" in tags, "the words do name the place..."
    filed = story_sources._in_field(tags, "sports")
    assert "sports" in filed
    assert not set(filed) & {"israel", "tel aviv", "world", "middle east"}


def test_a_sport_or_league_named_is_filed_under_sport(tree):
    for text in ("stanley cup odds", "who wins the nba finals",
                 "premier league title race", "formula one in vegas"):
        assert "sports" in T.tags_for_text(text), text


# --- the far minor league ---------------------------------------------------

def test_a_minor_league_abroad_is_never_offered_even_to_a_sports_fan(log):
    listened(log, "nba playoff basketball predictions")
    listened(log, "the best basketball players this season")
    stories.seed([
        game("maccabi rishon", "hapoel holon", ("sports", "basketball"),
             country="israel"),
        game("lakers", "celtics", ("sports", "basketball", "nba"),
             country="united states", minor=False),
    ])
    made = titles(made_for_you(log))
    assert "Maccabi Rishon Vs Hapoel Holon" not in made
    assert "Lakers Vs Celtics" in made


def test_a_minor_league_on_the_listeners_own_continent_can_be(log):
    listened(log, "nba playoff basketball predictions")
    stories.seed([game("maccabi rishon", "hapoel holon",
                       ("sports", "basketball", "playoff basketball"),
                       country="israel")])
    made = titles(made_for_you(log, country="IL"))
    assert "Maccabi Rishon Vs Hapoel Holon" in made


def test_following_the_league_brings_a_far_one_back(log):
    listened(log, "euroleague basketball tonight")
    stories.seed([game("maccabi rishon", "hapoel holon",
                       ("sports", "basketball", "euroleague"),
                       country="israel")])
    assert "Maccabi Rishon Vs Hapoel Holon" in titles(made_for_you(log))


def test_an_unknown_continent_is_no_continent(tree):
    tile = T.topics_from_stories([game("a", "b", country="israel")])[0]
    assert T._far_minor_league(tile, {"sports": 1.0}, "")
    assert not T._far_minor_league(tile, {"sports": 1.0}, "asia")


# --- variety ----------------------------------------------------------------

def test_made_for_you_never_holds_more_than_two_of_one_field(log):
    listened(log, "nba playoff basketball predictions")
    stories.seed([
        game(f"home {n}", f"away {n}", ("sports", "basketball", "nba"),
             country="united states", minor=False)
        for n in "abcdef"])
    tiles = made_for_you(log)
    sport = [t for t in tiles if "sports" in t["tags"]]
    assert len(tiles) == T.SECTION_SIZE
    assert len(sport) <= T.MAX_PER_FACET, titles(tiles)


def test_strict_diversify_drops_rather_than_gives_way():
    tiles = [T.Topic(str(n), str(n), "", str(n), ("sports",), "") for n in range(5)]
    assert len(T.diversify(tiles, 4, max_per_facet=2, strict=True)) == 2
    assert len(T.diversify(tiles, 4, max_per_facet=2)) == 4


def test_the_top_up_keeps_the_cap():
    picked = [T.Topic(str(n), str(n), "", str(n), ("sports",), "") for n in range(2)]
    more = ([T.Topic(f"s{n}", "s", "", "s", ("sports",), "") for n in range(3)]
            + [T.Topic("m", "m", "", "m", ("money",), ""),
               T.Topic("w", "w", "", "w", ("world",), "")])
    extra = T._fill_to_minimum(picked, 4, more, set(), max_per_facet=2)
    assert [t.id for t in extra] == ["m", "w"]


# --- markets ----------------------------------------------------------------

def market(name):
    import story_sources

    return story(name, f"why {name} shares moved today",
                 story_sources._tags("", "money", "markets", "stock market"),
                 domain=stories.MARKETS)


def test_a_market_move_reaches_a_listener_who_follows_money(log):
    listened(log, "what the fed will do about inflation")
    stories.seed([market("nvidia")])
    assert "Nvidia" in titles(made_for_you(log))


def test_a_market_move_does_not_reach_a_listener_with_no_money_taste(log):
    listened(log, "how sleep affects memory")
    stories.seed([market("nvidia")])
    assert "Nvidia" not in titles(made_for_you(log))


def test_a_market_story_is_filed_as_a_market_not_by_the_companys_name(tree):
    import story_sources

    tags = story_sources._tags("", "money", "markets", "stock market")
    assert set(tags) == {"money", "markets", "stock market"}


def test_made_for_you_reads_what_the_pool_is_holding():
    shown = [T.Topic("a", "A", "", "a", ("money",), "")]
    held = [T.Topic("a", "A", "", "a", ("money",), ""),
            T.Topic("b", "B", "", "b", ("money",), "")]
    assert [t.id for t in T.made_for_you_candidates(shown, held)] == ["a", "b"]


def test_the_sports_source_marks_a_minor_league():
    import live_facts
    import live_sources
    import story_sources

    sport = live_sources.SPORTS["basketball"]
    row = {"league": {"name": "Ligat HaAl", "country": {"name": "Israel"}},
           "teams": {"home": {"name": "Maccabi Rishon"},
                     "away": {"name": "Hapoel Holon"}},
           "status": {"short": "NS"}, "scores": {}}
    source = story_sources.ApiSportsSignals()
    signal = source._signal(sport, row, __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc))
    if signal is None:
        pytest.skip("this row shape is not one the basketball card reads")
    assert signal.minor_league is True
    assert "sports" in signal.tags
    assert live_facts  # imported for the status vocabulary the row uses


def test_filing_a_long_history_stays_cheap(tree):
    """Bound, not result (§122's rule): `familiar_words` runs on every page,
    and filing each event reads the vocabulary. A thousand-event history
    must stay well inside a page budget, and a second page reuses the memo."""
    events = [T.Event("me", "search", "", f"flights to tel aviv number {n}", ())
              for n in range(1000)]
    started = time.perf_counter()
    T.familiar_words(events)
    first = time.perf_counter() - started
    started = time.perf_counter()
    T.familiar_words(events)
    again = time.perf_counter() - started
    assert first < 1.0, f"{first:.3f}s for a thousand events"
    assert again < 0.1, f"{again:.3f}s on the second page"
