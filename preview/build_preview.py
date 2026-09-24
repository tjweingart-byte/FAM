"""Build a single self-contained HTML file of the FAM interface.

Why this exists: reviewing the app on a phone used to mean running the server,
being on the same wifi, and finding the laptop's LAN address - or looking at
screenshots, which cannot be tapped. This produces one file with no backend,
no build step and no install, which can be published somewhere and opened on a
phone in one tap.

What it is honest about: it is the *interface*, not the product. There is no
model behind it, so scripts are canned and audio is generated silence at the
right length. Transport controls, progress, swipes, mixes, the topic picker
and the feeds are all real code doing real work against fake data - which is
exactly the layer worth checking on a phone. Anything about writing quality or
time-to-first-audio has to be checked against the real server.

    python preview/build_preview.py            # -> preview/fam-preview.html
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
OUT = ROOT / "preview" / "fam-preview.html"
#: Same page, minus the outer document tags, for publishing as an Artifact.
#: The Artifact host supplies <!doctype>, <head> and <body> itself, so a full
#: document here would nest one inside another and render as text.
OUT_ARTIFACT = ROOT / "preview" / "fam-artifact.html"


def load_fixtures() -> dict:
    """Canned API responses, built from the real bank so the preview shows the
    same topics the app would. Imported rather than duplicated - a fixture that
    drifts from the code is worse than no fixture."""
    sys.path.insert(0, str(ROOT))
    import entitlements
    import mixes as mixes_mod
    import preferences as prefs_mod
    import stories as stories_mod
    import topics as topics_mod
    import trending as trending_mod
    import sharing

    bank = [t.as_dict() for t in topics_mod.TOPIC_BANK]
    by_id = {t["id"]: t for t in bank}

    # What a live story tile looks like, produced by the real conversion
    # rather than described here. Templated rather than composed - no model
    # runs at build time - which is exactly the tile a deployment with no API
    # key serves, so the preview shows the floor of the feature rather than
    # its best case.
    live_tiles = [
        t.as_dict() for t in topics_mod.topics_from_stories([
            stories_mod.template(stories_mod.Signal(
                subject=item.subject, observation=item.why_now,
                domain=stories_mod.ATTENTION, source="fixture feed",
                tags=topics_mod.tags_for_text(f"{item.subject} {item.query}"),
                strength=max(0.2, 1.0 - 0.1 * max(0, item.rank - 1)),
                suggested_query=item.query, suggested_angle=item.why_now))
            for item in trending_mod.FakeTrendingSource.SUBJECTS_AS_ITEMS()
        ])
    ]

    # **Trending, as the story pool builds it since §135**: real clustered
    # stories ranked by how many outlets run them, worldwide and region by
    # region, and a game carrying its score line. Invented, like every
    # fixture here, and run through the real conversion and the real ranker
    # - `rank_world` for the rail and `trending_groups` for "View more" - so
    # the preview shows the page those functions build rather than a
    # description of it. Ranked for a listener in the United States.
    import geography

    def trending_story(subject, title, angle, coverage, countries, tags,
                       domain=stories_mod.ATTENTION, live_line="",
                       live_status="", hint=""):
        scope, key, label = geography.scope_for(countries, hint)
        return stories_mod.Story(
            subject=subject, title=title, angle=angle,
            query=f"{subject}: what is happening and why it matters",
            domain=domain, source="fixture feed", tags=tuple(tags),
            strength=min(1.0, 0.25 + coverage / 60.0),
            first_seen=__import__("time").time() - 3600,
            shelf_life=stories_mod.DOMAIN_SHELF_LIFE[domain],
            countries=tuple(countries), coverage=coverage, region_hint=hint,
            geo_scope=scope, geo_key=key, geo=label,
            live_line=live_line, live_status=live_status,
            live_as_of=__import__("time").time() - 300 if live_line else 0.0)

    everywhere = (("united states", 0.35), ("united kingdom", 0.25),
                  ("india", 0.2), ("japan", 0.2))
    trending_stories = [
        trending_story("the ceasefire talks in Cairo", "The Ceasefire Talks",
                       "What each side is actually asking for", 58,
                       everywhere, ("world",)),
        trending_story("the chip export rules", "The New Chip Export Rules",
                       "Who they hit, and who they miss", 41, everywhere,
                       ("tech",)),
        trending_story("Chiefs vs Bills", "Chiefs vs Bills",
                       "The matchup inside the matchup", 22,
                       (("united states", 1.0),), ("sports",),
                       domain=stories_mod.SPORTS,
                       live_line="Live \u00b7 Chiefs 21\u201314 Bills \u00b7 Third Quarter",
                       live_status="in_progress"),
        trending_story("the Ohio school funding vote", "Ohio's School Funding Vote",
                       "Why a state budget fight went national", 14,
                       (("united states", 1.0),), ("world",)),
        trending_story("the French rail strike", "France's Rail Strike",
                       "What the unions want this time", 19,
                       (("france", 0.8), ("united kingdom", 0.2)), ("world",)),
        trending_story("the Delhi heatwave", "The Delhi Heatwave",
                       "How hot a city can get before it stops", 16,
                       (("india", 1.0),), ("science",)),
        trending_story("the Bank of Japan decision", "The Bank of Japan's Move",
                       "Why the yen cares more than anyone", 12,
                       (("japan", 0.7), ("south korea", 0.3)), ("money",)),
    ]
    trending_tiles = topics_mod.topics_from_stories(trending_stories)
    trending_rail = [t.as_dict() for t in topics_mod.rank_world(
        trending_tiles, "US")]
    trending_view = [
        {"key": g["key"], "label": g["label"], "yours": g["yours"],
         "topics": [dict(x.as_dict(), cached=False) for x in g["topics"]]}
        for g in topics_mod.trending_groups(trending_tiles, "US")]

    def section(key, title, ids):
        # A live story tile is passed through as a dict - its inventory is not
        # the bank - and a bank id is looked up. **Copied either way**: the
        # same tile can be listed on two rails and the same bank entry is in
        # `/api/topics` as well, so a caller marking one `cached` would
        # otherwise mark it everywhere it appears.
        topics = [dict(t if isinstance(t, dict) else by_id[t])
                  for t in ids if isinstance(t, dict) or t in by_id]
        return {"key": key, "title": title, "topics": topics, "empty_reason": ""}

    # Keyed by section rather than zipped against SECTIONS in order. The zip
    # was silently truncating: a fourth section arrived and the fixture kept
    # producing three, so the preview showed a page the code no longer builds
    # and the smoke test blamed the interface. A missing key now fails loudly
    # here, where the fixture is, instead of vanishing.
    myfam_picks = {
        # Two live stories in front of the bank, because Made for you is the
        # one rail that draws on both inventories and a fixture showing only
        # one of them previews half the rail.
        "from_history": (live_tiles[:2]
                         + ["chip-supply", "energy-grid", "founder-motivation",
                            "hormuz"]),
        "might_like": ["hollywood-comebacks", "food-supply", "anxiety-loop",
                       "training-load", "pricing-psychology"],
        "followers": ["stadium-money", "sleep-science", "space-race", "longevity-claims"],
        # The crowd row leads with what is already written, so a few of these
        # carry `cached` - the badge and the ordering are the whole point of
        # that rail and are invisible on a fixture that marks none.
        #
        # These are bank topics and that is still consistent with the rule
        # that this row holds plays and nothing else: the whole fixture is a
        # *used* deployment seen by a guest, so listeners have played these
        # and a guest is who the bank is offered to. A fixture of an empty
        # deployment would preview four empty rails, which is a real state
        # and a useless thing to check a layout against.
        "most_played": ["ai-agents", "fed-next-move", "housing-market",
                        "operator-ceos", "habits-research", "transfer-window"],
        # The world row. Built from the live story pool's own conversion
        # rather than the bank, because its whole point is that its inventory
        # is not FAM's - and a fixture that drew it from the bank would
        # preview a page the app cannot build. These are invented, like every
        # other fixture here, and the preview's own badge says the page is
        # running on fixtures.
        # The ones Made for you did not claim. The real feed fills the
        # personal rail first and gives this what is left, because the two
        # draw on one pool now and a page showing the same tile twice reads as
        # a bug - so a fixture that repeated them would preview a page the app
        # does not build.
        "world_trending": trending_rail,
        # What this listener was shown last week and did not take. Eight,
        # because the rail's own size is eight and a fixture that showed six
        # would preview a shorter row than the app builds. Bank topics only:
        # a live story that was offered last week has usually expired and
        # fallen out of the pool by now, and the real rail cannot resolve one
        # it no longer holds - so neither should the preview.
        "missed": ["sleep-science", "longevity-claims", "song-breaks-internet",
                   "restaurant-scene", "space-race", "morning-mindset",
                   "the-trade", "pricing-psychology"],
    }
    missing = [k for k, _ in topics_mod.SECTIONS if k not in myfam_picks]
    if missing:
        raise SystemExit(
            f"the myFAM fixture has no topics for {', '.join(missing)} - add "
            f"them to myfam_picks, or the preview shows fewer rails than the "
            f"app builds")
    myfam = {
        "personalised": True,
        "minutes": 2,
        "sections": [section(key, title, myfam_picks[key])
                     for key, title in topics_mod.SECTIONS],
    }
    # What the two cached rows look like on a seeded deployment. Invented,
    # like every fixture here, and marked on the rows that would really carry
    # it - a "ready" badge on Trending would preview a page the app does not
    # build, because that row's tiles are new by construction.
    for row in myfam["sections"]:
        if row["key"] == "most_played":
            for index, tile in enumerate(row["topics"]):
                tile["cached"] = index < 3

    # Through the real `mixes.clean_items`, so a fixture mix is exactly the
    # shape the server stores - followed subjects, one narrowed to a team and
    # beside the whole league, an older bank episode, a typed topic (§137).
    def mix(mix_id, name, entries, public=False):
        return mixes_mod.Mix(mix_id, "preview", name, mixes_mod.clean_items(entries),
                             0, 0, public).as_dict()

    mixes = {
        "mixes": [
            mix("m1", "Morning", ["f:stocks~Nvidia", "f:ai", "fed-next-move"],
                public=True),
            mix("m2", "At the gym", ["f:nfl~Eagles", "f:nfl", "f:basketball~Lakers"]),
            mix("m3", "Wind down", ["sleep-science",
                                    {"query": "what my council is doing about the high street"}]),
        ],
        "starters": [{"name": n, "topic_ids": list(i)} for n, i in mixes_mod.STARTER_MIXES],
    }

    # Other listeners' public mixes, for DailyFAM's search and their
    # profiles - the shape `/api/mixes/public` answers in, owner included.
    def public_mix(mix_id, name, entries, owner):
        body = mix(mix_id, name, entries, public=True)
        body.pop("topics", None)
        for item in body["items"]:
            if item.get("custom"):
                item["subtitle"] = "Typed in by @" + owner[1]
        return dict(body, owner={"name": owner[0], "handle": owner[1], "avatar": ""},
                    mine=False, added=False)

    public_mixes = {"mixes": [
        public_mix("pb1", "Gym", ["f:nfl~Eagles", "f:ai",
                                  {"query": "AI updates", "title": "AI updates"}],
                   ("Beth Solomon", "beth")),
        public_mix("pb2", "Morning brief", ["f:stocks~Nvidia", "f:news"],
                   ("Mike Solomon", "mike")),
        public_mix("pb3", "Wind down", ["f:music", "f:space"],
                   ("Rachel Solomon", "rachel")),
        public_mix("pb4", "Game day", ["f:nfl", "f:basketball~Lakers"],
                   ("Nadia Okoro", "nadia")),
        public_mix("pb5", "Morning", ["f:health", "f:ai"],
                   ("Beth Solomon", "beth")),
    ]}

    # One card carries a friend's vibe, which is the whole of that tag: a
    # friend both generated the episode and vibed it. The others do not, so
    # the preview shows both states rather than one.
    explore = {"episodes": [
        {"query": q, "title": q[:1].upper() + q[1:], "minutes": m,
         "plays": p, "thread": th, "age_seconds": age,
         "vibed": m == 5,
         **({"vibed_by": {"name": "Rachel Solomon", "handle": "rachels",
                          "avatar": ""}} if m == 5 else {})}
        for q, m, p, th, age in [
            ("why the strait of hormuz moves the oil price", 3, 4,
             "why the shipping lanes run through Omani water", 140),
            ("what habit research actually shows about lasting change", 2, 2,
             "why streaks break in the third week", 900),
            ("how reusable rockets changed the economics of spaceflight", 5, 7,
             "what happens when launch gets cheaper than shipping", 4000),
            ("a good mindset for when I wake up in the morning", 1, 3,
             "why a flat morning cortisol curve might mean burnout", 9000),
            ("what the Federal Reserve is likely to do about interest rates", 3, 9,
             "who actually loses when rates stay high", 30000),
            ("how a city's food supply chain actually works", 4, 1,
             "the three days of stock nobody plans for", 100000),
        ]
    ]}

    # The Topic screen's episodes, per facet, decided at build time by the
    # same tagging the server's `/api/interest` uses: finished episodes from
    # the Explore fixture first, then the bank as the evergreen tail.
    interest = {}
    for tag in topics_mod.TAG_LABELS:
        cards = []
        for ep in explore["episodes"]:
            if tag in topics_mod.tags_for_text(ep["query"]):
                cards.append({"query": ep["query"], "title": ep["title"],
                              "minutes": ep["minutes"], "source": "cache",
                              "age_seconds": ep["age_seconds"]})
        for t in topics_mod.TOPIC_BANK:
            if tag in topics_mod.topic_tags(t):
                cards.append({"query": t.query, "title": t.title, "minutes": 0,
                              "source": "bank", "age_seconds": None,
                              "angle": t.subtitle})
        interest[tag] = cards

    import time as _time
    return {
        "/api/interest": interest,
        "/api/myfam": myfam,
        # Trending's "View more", grouped by where each story is trending -
        # built by `topics.trending_groups`, as the server builds it (§135).
        "/api/myfam/section:world_trending": {
            "key": "world_trending", "title": "Trending",
            "topics": [x for g in trending_view for x in g["topics"]],
            "groups": trending_view, "ready": 0, "empty_reason": "",
            "personalised": True, "taste_source": "taste"},
        "/api/mixes": mixes,
        "/api/mixes/public": public_mixes,
        "/api/topics": {"topics": bank},
        # The preview never reads a real file: it stands in for the extraction
        # so the flow and the chips can be exercised on a phone.
        "/api/attach": {"id": "preview-attachment", "kind": "document",
                        "name": "Attached file", "chars": 4200, "url": "",
                        "preview": "A stand-in for extracted text."},
        "/api/explore": explore,
        # The thread and the episode's own title, from the same call. Both are
        # written by the model on trailing marker lines and read back out of
        # the cache, so a preview with no Claude has to stand in for both -
        # and the title is the whole point of the swap the player does a few
        # seconds in, which a fixture without one would show none of.
        "/api/next": {"thread": "why the shipping lanes run through Omani water",
                      "title": "The Two-Mile Lane That Moves the Oil",
                      "title_final": True,
                      "summary": "Why a strait two miles wide sets the price "
                                 "of a barrel, and what insurers have to do "
                                 "with it."},
        # Resume positions and the typing note are written to the server now
        # (§127). The preview keeps neither; it only has to answer.
        "/api/progress": {"ok": True, "remembered": True, "resumable": True},
        "/api/messages/typing": {"ok": True},
        # Who this episode drew on. Invented, like every fixture here - the
        # point on a phone is the corner cluster, the overlap and the popup,
        # none of which a preview with no sources would ever draw. A live
        # feed and an article read differently in the list, so there is one
        # of each.
        "/api/sources": {
            "known": True,
            "retrievers": ["exa", "gdelt"],
            "items": [
                {"label": "reuters.com", "kind": "article", "tier": "wire service",
                 "at": "2026-09-16", "title": "Shipping rates climb as tankers reroute",
                 "url": "https://www.reuters.com/"},
                {"label": "ft.com", "kind": "article", "tier": "national paper",
                 "at": "2026-09-16", "title": "Insurers widen the war-risk zone",
                 "url": "https://www.ft.com/"},
                {"label": "apnews.com", "kind": "article", "tier": "wire service",
                 "at": "2026-09-15", "title": "Two more cargoes divert south",
                 "url": "https://apnews.com/"},
                {"label": "Polymarket", "kind": "live", "tier": "live feed",
                 "at": "2026-09-17T08:40:00", "title": "", "url": ""},
            ],
        },
        # The sentences the voice is reading, for live captions. The preview's
        # audio is silence of the right length, so the highlight walks the
        # script on the same clock it would against a real voice.
        # `live` and `done` are what the panel stops polling on now - a poll
        # count could only ever say "we have asked enough times", which is not
        # the same claim as "there is nothing here". The fixture is a finished
        # episode, so it is done.
        "/api/transcript": {"known": True, "live": False, "done": True,
                            "sentences": [
            "Tanker traffic through the strait is down about a fifth this week.",
            "The reason is not the shooting, it is the paperwork.",
            "War-risk insurance is priced daily, and on Monday the underwriters "
            "widened the zone by ninety miles.",
            "That pushed a dozen ships outside the cover they had already paid for.",
            "A captain with no cover does not sail, whatever the cargo is worth.",
            "So the queue at the southern end is now four days long.",
            "Freight rates followed within hours, because the ships that are "
            "still moving can name their price.",
            "The oil price barely moved, which is the part worth sitting with.",
            "Traders have been reading this strait for fifty years and they "
            "price the insurance, not the headlines.",
        ]},
        # Two open threads; the shim seeds two part-heard episodes alongside
        # them, so Go Deeper opens as a full grid rather than one lonely card.
        # "Pick up where you left off": two part-heard episodes and two
        # follow-ups, each with the one-line summary the section draws (§127).
        "/api/godeeper": {
            "resume": [
                {"query": "why everyone is talking about AI agents", "minutes": 7,
                 "seconds": 259, "title": "The Agents Are Coming for Your Inbox",
                 "summary": "What an AI agent actually does, and why every big "
                            "lab shipped one in the same month.", "at": 0},
                {"query": "who actually makes the world's chips", "minutes": 6,
                 "seconds": 50, "title": "Who Actually Makes the World's Chips",
                 "summary": "One company in Taiwan, the machines it cannot live "
                            "without, and why that worries everyone.", "at": 0},
            ],
            "threads": [
                {"thread": "why the shipping lanes run through Omani water",
                 "title": "The Two-Mile Lane That Moves the Oil",
                 "from_title": "Why the Strait of Hormuz Moves the Oil Price",
                 "summary": "Follows on from Why the Strait of Hormuz Moves "
                            "the Oil Price.", "at": 0},
                {"thread": "how NIL money changed college football recruiting",
                 "title": "The New College Football Arms Race",
                 "from_title": "Who Really Pays for a Stadium",
                 "summary": "Follows on from Who Really Pays for a Stadium.",
                 "at": 0},
            ],
            "similar": [],
        },
        # What a real Mac reports, minus the hosted engine that was removed.
        # One voice made the picker look like it had nothing to pick, and the
        # grouping and the scrolling both only show up on a list long enough
        # to need them - which is exactly the list a preview should show.
        "/api/voices": {"voices": [
            {"id": "piper:en_GB-alba-medium", "label": "Alba (GB, medium)",
             "engine": "piper", "detail": "neural, ships with the app"},
            {"id": "piper:en_GB-northern_english_male-medium",
             "label": "Northern_English_Male (GB, medium)",
             "engine": "piper", "detail": "neural, ships with the app"},
            {"id": "piper:en_US-amy-medium", "label": "Amy (US, medium)",
             "engine": "piper", "detail": "neural, ships with the app"},
            {"id": "piper:en_US-lessac-medium", "label": "Lessac (US, medium)",
             "engine": "piper", "detail": "neural, ships with the app"},
            {"id": "say:Samantha", "label": "Samantha", "engine": "say", "detail": "en-US"},
            {"id": "say:Daniel", "label": "Daniel", "engine": "say", "detail": "en-GB"},
            {"id": "say:Karen", "label": "Karen", "engine": "say", "detail": "en-AU"},
            {"id": "say:Moira", "label": "Moira", "engine": "say", "detail": "en-IE"},
            {"id": "say:Aman", "label": "Aman", "engine": "say", "detail": "en-IN"},
            {"id": "say:Fred", "label": "Fred", "engine": "say", "detail": "en-US"},
            {"id": "say:Rishi", "label": "Rishi", "engine": "say", "detail": "en-IN"},
            {"id": "say:Tara", "label": "Tara", "engine": "say", "detail": "en-IN"},
            {"id": "say:Tessa", "label": "Tessa", "engine": "say", "detail": "en-ZA"},
        ], "default": "piper:en_GB-alba-medium"},
        # The preview shows a signed-in listener, because that is the state
        # with something to look at - the signed-out one is two buttons.
        "/api/auth/me": {
            "user_id": "preview-listener",
            "email": "ian@example.com",
            "authenticated": True,
        },
        "/api/profile": {
            "listener": "preview-listener", "played": 34, "finished": 21,
            "searched": 12, "open_threads": 2,
            "subjects": ["tech", "money", "science", "health"],
            # The interests row, decided by the server and drawn by the page.
            # Four, ranked by what they listen to - `interests_ranked` is the
            # whole list the editor offers and `interests_source` says whether
            # the four were pinned or chosen, because those look identical on
            # screen and the copy under them is only true of one.
            "interests_max": 5,
            "interests_shown": [
                {"id": "tech", "label": "Technology", "kind": "facet"},
                {"id": "money", "label": "Money & markets", "kind": "facet"},
                {"id": "science", "label": "Science", "kind": "facet"},
                {"id": "health", "label": "Health", "kind": "facet"},
            ],
            "interests_ranked": [
                {"id": "tech", "label": "Technology", "kind": "facet"},
                {"id": "money", "label": "Money & markets", "kind": "facet"},
                {"id": "science", "label": "Science", "kind": "facet"},
                {"id": "health", "label": "Health", "kind": "facet"},
                {"id": "world", "label": "World", "kind": "facet"},
                {"id": "culture", "label": "Culture", "kind": "facet"},
                {"id": "sports", "label": "Sport", "kind": "facet"},
            ],
            "interests_pinned": [],
            "interests_source": "top",
            "since": _time.time() - 63 * 86400,
            # **Unnamed to start**, which is what a first run actually is.
            # It used to be "Ian Solomon" / "iansolomon", so the preview
            # showed a first run to somebody who already had a name and a
            # handle - and offered that name as a placeholder to everybody
            # else. `POST /api/me` fills these in, the way the app does.
            "name": "", "handle": "",
            "joined": _time.time() - 63 * 86400,
            "echo_count": 7,
            # The picture and the follow graph the profile now draws. Both
            # come from the server in the real app - see social.py.
            "avatar": "",
            "follows": {"following": 3, "followers": 2, "friends": 2},
            "mixes": [
                {"id": "m1", "name": "Morning Run", "public": True,
                 "items": [{"id": "x"}] * 14, "topics": [], "topic_ids": [],
                 "custom_count": 0, "created_at": 0, "updated_at": 0},
                {"id": "m2", "name": "Market Watch", "public": True,
                 "items": [{"id": "x"}] * 9, "topics": [], "topic_ids": [],
                 "custom_count": 0, "created_at": 0, "updated_at": 0},
                {"id": "m3", "name": "Kids' Questions", "public": True,
                 "items": [{"id": "x"}] * 21, "topics": [], "topic_ids": [],
                 "custom_count": 0, "created_at": 0, "updated_at": 0},
            ],
            "echoes": [
                {"title": "The Two-Mile Lane That Moves the Oil", "minutes": 4,
                 "query": "why the strait of hormuz moves the oil price", "asked": True},
                {"title": "How a City's Food Supply Chain Works", "minutes": 4,
                 "query": "how a city's food supply chain actually works", "asked": False},
                {"title": "Who Actually Makes the World's Chips", "minutes": 6,
                 "query": "why semiconductor manufacturing is concentrated", "asked": True},
                {"title": "What We Actually Know About Sleep", "minutes": 5,
                 "query": "what sleep research actually establishes", "asked": False},
            ],
        },
        "/api/health": {"ok": True, "demo": True, "engine": "preview"},
        "/api/event": {"ok": True},
        # The plans sheet the limit screen opens. Built from the real tier
        # table rather than a copy typed here, for the same reason as the
        # vocabulary below: a preview that shows tiers the server does not have
        # is a preview of a different product. `current` is "free" because a
        # preview has no session to read one from.
        "/api/plans": {**entitlements.catalogue(), "current": "free"},
        # The intro's two pages. Built from the real vocabulary rather than a
        # list typed out here: a picker offering a facet the ranker does not
        # score is the exact drift this module refuses to introduce.
        "/api/preferences": {
            # Six of the eight, as the picker shows them. An empty fixture log
            # means this is `popular_facets`' declared order rather than a
            # measurement, which is exactly what a fresh deployment gets and
            # what `interests_source` is here to say.
            "interests_available": [
                {"id": tag, "label": topics_mod.TAG_LABELS[tag],
                 "short": topics_mod.TAG_SHORT[tag]}
                for tag in topics_mod.PICKER_DEFAULT_ORDER[:topics_mod.PICKER_SIZE]],
            "interests_all": [{"id": tag, "label": label}
                              for tag, label in topics_mod.TAG_LABELS.items()],
            "interests_source": "default",
            # Settings' wheel: this listener's own listening. The fixture has
            # no per-listener log, so it is the same declared order and says
            # so - which is exactly what `my_facets` returns for somebody the
            # app has never seen.
            "interests_yours": [
                {"id": tag, "label": topics_mod.TAG_LABELS[tag],
                 "short": topics_mod.TAG_SHORT[tag]}
                for tag in topics_mod.PICKER_DEFAULT_ORDER[:topics_mod.PICKER_SIZE]],
            "interests_yours_source": "default",
            # Imported rather than copied, like every other fixture here, so
            # the catalogue the preview shows cannot drift from the real one.
            "catalogue": [i.as_dict() for i in topics_mod.INTEREST_CATALOGUE],
            "tag_parent": dict(topics_mod.TAG_PARENT),
            "tag_labels": dict(topics_mod.TAG_LABELS),
            "languages": [dict(lang) for lang in prefs_mod.LANGUAGES],
            "language_active": prefs_mod.LANGUAGE_ACTIVE,
            "account": True, "saved": True,
            "account_required": "You need an account for this.",
            "interests": [], "hidden_interests": [], "public_interests": [],
            "language": "en", "weekly_recap": True,
            "recap_week": "", "intro_done": False,
            # Empty rather than a plausible-looking city. A fixture that
            # arrived pre-filled with somewhere would make the Where you are
            # editor look like it had already been used, which is the
            # invented-contacts mistake in a smaller place.
            "location": {"city": "", "region": "", "country": "", "label": ""},
        },
        "/api/nextup": {
            "topics": [by_id[i] for i in
                       ["energy-grid", "chip-supply", "space-race", "housing-market"]
                       if i in by_id],
            "algo": topics_mod.ALGO_VERSION,
        },
        "/api/explorenew": {
            "topics": [by_id[i] for i in
                       ["hollywood-comebacks", "food-supply", "anxiety-loop",
                        "training-load", "pricing-psychology", "space-race"]
                       if i in by_id],
            "personalised": True,
            "reason": "Next to what you already listen to, rather than more of it.",
            "algo": topics_mod.ALGO_VERSION,
        },
        # The shelf, with two episodes already on it. Not empty, because an
        # empty-state preview shows the empty state and nothing else.
        "/api/saved": {
            "folders": [{"id": "fld_commute", "name": "Commute",
                         "created": 0, "items": 1}],
            "items": [
                {"id": "sav_1", "folder_id": "fld_commute",
                 "query": "why semiconductor manufacturing is concentrated",
                 "minutes": 2, "title": "Who Actually Makes the World's Chips",
                 "source": "player", "created": 0, "last_played": 0},
                {"id": "sav_2", "folder_id": "",
                 "query": "how electricity grids handle intermittent renewable power",
                 "minutes": 5, "title": "What the Grid Does When the Wind Drops",
                 "source": "explore", "created": 0, "last_played": 0},
            ],
        },
        "/api/share/targets": {"targets": [
            {"key": t.key, "label": t.label, "kind": t.kind,
             "needs_image": t.needs_image, "max_chars": t.max_chars}
            for t in sharing.TARGETS]},
    }


#: How a mix entry becomes an item, for both preview shims (§137). The
#: server's own rules - `mixes.followed_item`, `mixes.daily_prompt` - in the
#: browser, with every phrase and limit injected from `mixes.py` rather than
#: typed here, so the one thing a shim could get wrong is the assembly.
MIX_ITEMS_JS = r"""
  var MIX_RULES = __MIX_RULES__;
  function mixCleanFocus(text) {
    return String(text).replace(/[,~|]/g, " ").replace(/\s+/g, " ").trim()
      .slice(0, MIX_RULES.max_focus).trim();
  }
  function mixDailyPrompt(item) {
    if (!(item.follow || item.custom)) return "";
    var head;
    if (item.focus && item.focus.length) {
      var shown = item.focus.join(" and ");
      head = "The latest on " + shown + " (" + item.topic_label + ") as of " + MIX_RULES.date
        + ". Cover only " + shown + ", not " + item.topic_label + " in general";
    } else {
      head = "The latest on " + item.query + " as of " + MIX_RULES.date;
    }
    for (var i = 0; i < MIX_RULES.endings.length; i++) {
      var whole = head + MIX_RULES.endings[i];
      if (whole.split(MIX_RULES.date).join(MIX_RULES.longest).length <= MIX_RULES.max_prompt) return whole;
    }
    return head + MIX_RULES.endings[MIX_RULES.endings.length - 1];
  }
  // `f:nfl` or `f:nfl~Eagles`: a followed catalogue subject. null for
  // anything else, including an id naming no subject.
  function mixFollowItem(id, catalogue) {
    id = String(id);
    if (id.indexOf("f:") !== 0) return null;
    var cut = id.indexOf("~"), base = cut === -1 ? id : id.slice(0, cut);
    var c = catalogue.filter(function (x) { return x.id === base.slice(2); })[0];
    if (!c) return null;
    var focus = [];
    (cut === -1 ? [] : id.slice(cut + 1).split("|")).forEach(function (part) {
      var f = part;
      try { f = decodeURIComponent(part); } catch (e) {}
      f = mixCleanFocus(f);
      var have = focus.map(function (x) { return x.toLowerCase(); });
      if (f && have.indexOf(f.toLowerCase()) === -1) focus.push(f);
    });
    var shown = focus.join(", ");
    var item = {
      id: base + (focus.length ? "~" + focus.map(encodeURIComponent).join("|") : ""),
      title: focus.length ? c.label + " \u00b7 " + shown : c.label,
      query: c.label, custom: false,
      subtitle: focus.length ? "Focused on " + shown + " \u00b7 new briefing every day"
                             : "New briefing every day",
      icon: c.icon, follow: true, base: base, focus: focus, topic_label: c.label
    };
    item.daily_prompt = mixDailyPrompt(item);
    return item;
  }
  // A typed topic as the store keeps it: whole, up to the server's
  // MAX_QUERY, with the commas that separate stored items taken out.
  function mixTypedQuery(query) {
    return String(query || "").replace(/,/g, " ").replace(/\s+/g, " ").trim()
      .slice(0, MIX_RULES.max_query);
  }
  function mixTypedItem(query, title) {
    query = String(query || "").replace(/\s+/g, " ").trim();
    var item = { id: "q:" + query.toLowerCase().slice(0, 40), query: query,
                 title: title || (query.charAt(0).toUpperCase() + query.slice(1)),
                 custom: true, subtitle: "Added by you", icon: "leaf" };
    item.daily_prompt = mixDailyPrompt(item);
    return item;
  }
"""


def mix_items_js() -> str:
    sys.path.insert(0, str(ROOT))
    import mixes as mixes_mod
    return MIX_ITEMS_JS.replace("__MIX_RULES__", json.dumps({
        "date": mixes_mod.DAILY_DATE, "longest": mixes_mod.LONGEST_DATE,
        "max_prompt": mixes_mod.MAX_PROMPT, "max_focus": mixes_mod.MAX_FOCUS,
        "max_query": mixes_mod.MAX_QUERY,
        "endings": list(mixes_mod.DAILY_ENDINGS)}))


SHIM = """
<script>
/* ---- Preview shim -------------------------------------------------------
   Stands in for the Python server so this file works on its own. Every fetch
   the app makes is answered from fixtures baked in at build time; /api/audio
   returns silence of the right length so the transport, the progress bar and
   the reel timing all behave exactly as they do against the real server.
   Nothing here touches the network. -------------------------------------- */
(function () {
  var FIXTURES = __FIXTURES__;
  var SAMPLE_RATE = 22050;
  var realFetch = window.fetch.bind(window);
__MIX_ITEMS__
  var mixes = JSON.parse(JSON.stringify(FIXTURES["/api/mixes"]));
  var nextMixId = 100;
  //: Other listeners' public mixes. Mutable: adding one marks it added.
  var PUBLIC_MIXES = JSON.parse(JSON.stringify(FIXTURES["/api/mixes/public"])).mixes;

  // `mixes.match_score`, in the few lines a preview needs: every word typed
  // must be in the mix's name, a topic in it, or its owner.
  function publicMixMatches(m, q) {
    var hay = [m.name, m.owner.name, m.owner.handle]
      .concat(m.items.map(function (i) {
        return [i.title, i.query, (i.focus || []).join(" "), i.topic_label || "",
                String(i.base || "").replace(/^f:/, "").replace(/-/g, " ")].join(" ");
      })).join(" ").toLowerCase().match(/[A-Za-z0-9_']+/g) || [];
    // Word prefixes, as `mixes._has_word` does: "ai" is not in "daily".
    return q.toLowerCase().split(/\s+/).map(function (w) { return w.replace(/^@/, ""); })
      .filter(Boolean).every(function (w) {
        return hay.some(function (t) { return t.indexOf(w) === 0; }); });
  }

  // A shared mix's wording per destination, from `sharing.MIX_TEMPLATES`.
  function mixShareBody(m) {
    var titles = m.items.map(function (i) { return i.title; });
    var topics = titles.length > 4
      ? titles.slice(0, 4).join(", ") + " and " + (titles.length - 4) + " more"
      : titles.join(", ");
    var link = "/m/" + m.id, made = {};
    SHARE_TEMPLATES.forEach(function (t) {
      made[t.key] = {
        target: t.key, label: t.label, kind: t.kind,
        needs_image: t.needs_image, url: link, subject: "", destination: "",
        text: t.mix_text.replace("{name}", m.name).replace("{topics}", topics)
                        .replace("{url}", link)
      };
    });
    return { url: link, public: false, card: "/api/mixes/" + m.id + "/card", targets: made };
  }

  function json(body, status, extraHeaders) {
    var headers = { "Content-Type": "application/json" };
    Object.keys(extraHeaders || {}).forEach(function (k) {
      headers[k] = extraHeaders[k];
    });
    return Promise.resolve(new Response(JSON.stringify(body), {
      status: status || 200, headers: headers
    }));
  }

  // The share wording, taken from sharing.py at build time so the preview and
  // the server cannot show different copy for the same button.
  var SHARE_TEMPLATES = __SHARE_TEMPLATES__;

  // Silence, streamed in chunks, so the player's buffering logic runs for real.
  function silence(seconds) {
    var total = Math.round(seconds * SAMPLE_RATE);
    var sent = 0;
    var stream = new ReadableStream({
      pull: function (c) {
        if (sent >= total) { c.close(); return; }
        var n = Math.min(SAMPLE_RATE, total - sent);
        sent += n;
        c.enqueue(new Uint8Array(n * 2));
        return new Promise(function (r) { setTimeout(r, 60); });
      }
    });
    return Promise.resolve(new Response(stream, {
      status: 200,
      headers: { "Content-Type": "audio/L16", "X-Sample-Rate": String(SAMPLE_RATE) }
    }));
  }

  /* The people this preview knows. Three of them, and they are *labelled* as
     a preview rather than presented as a contact list: the real app reads
     `/api/friends`, and this stands in for it so the flow can be walked on a
     phone. */
  // What the notification poll has waiting. Empty in ordinary use - a fixture
  // has no second listener typing into it - and filled by
  // `window.famPreviewNotify`, which is how the smoke test makes a message or
  // a follow *arrive* rather than asserting that a banner can be drawn by
  // hand. The difference matters: the bug being guarded against is the poll
  // never reaching the banner, and a test that calls the banner directly
  // would pass with the poll unplugged.
  var NOTIFY = { head: 0, pending: [], follows: [] };
  //: Listening history (§142), for the life of the page.
  var PREVIEW_HISTORY = [];
  var PREVIEW_AUTHED = function () {
    return !!(FIXTURES["/api/auth/me"] && FIXTURES["/api/auth/me"].authenticated);
  };

  window.famPreviewNotify = function (item) {
    if (item && item.follow) { NOTIFY.follows.push(item.follow); return; }
    NOTIFY.pending.push(item);
  };

  var PEOPLE = {
    all: [
      // A picture, so the preview shows faces where somebody has set one and
      // initials where they have not (§127). Drawn, like every fixture.
      { user_id: "u_beth", name: "Beth Solomon", handle: "beth",
        avatar: "data:image/svg+xml;utf8," + encodeURIComponent(
          '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
          + '<rect width="40" height="40" fill="#7FC4D4"/>'
          + '<circle cx="20" cy="16" r="7" fill="#F4EFE4"/>'
          + '<path d="M7 38c2-8 7-12 13-12s11 4 13 12z" fill="#F4EFE4"/></svg>') },
      { user_id: "u_mike", name: "Mike Solomon", handle: "mike" },
      { user_id: "u_rachel", name: "Rachel Solomon", handle: "rachel" },
      // Follows and is not followed back, so the asymmetry the graph is built
      // around is visible on a phone - and so the follower popup and the
      // unread badge have something real to draw.
      { user_id: "u_nadia", name: "Nadia Okoro", handle: "nadia" }
    ],
    following: ["u_beth", "u_mike", "u_rachel"],
    followers: ["u_beth", "u_rachel", "u_nadia"],
    vibes: [
      { id: 1, query: "why the strait of hormuz moves the oil price",
        title: "The Two-Mile Lane That Moves the Oil", minutes: 3,
        thread: "", at: 0, by: "You", handle: "you" },
      { id: 2, query: "what the Federal Reserve is likely to do about interest rates",
        title: "The Fed's Next Move, Explained", minutes: 3,
        thread: "", at: 0, by: "You", handle: "you" }
    ],
    threads: {
      u_beth: [
        { id: 1, thread: "u_beth", mine: true, kind: "episode", text: "",
          query: "how reusable rockets changed the economics of spaceflight",
          minutes: 5, title: "Inside the New Space Race", topic: "Science",
          at: Date.now() / 1000 - 3 * 86400 },
        { id: 2, thread: "u_beth", mine: false, kind: "text",
          text: "didn't realize they scrubbed this launch twice before it flew",
          query: "", minutes: 0, title: "", at: Date.now() / 1000 - 3 * 86400 + 600 }
      ],
      u_mike: [
        { id: 3, thread: "u_mike", mine: false, kind: "episode", text: "",
          query: "what the Federal Reserve is likely to do about interest rates", minutes: 3,
          title: "The Fed's Next Move, Explained", topic: "Money & markets",
          finished: true, at: Date.now() / 1000 - 5400 },
        { id: 4, thread: "u_mike", mine: false, kind: "text",
          text: "the part at minute 2 is exactly our pitch",
          query: "", minutes: 0, title: "", at: Date.now() / 1000 - 5300 }
      ]
    },
    byId: function (id) {
      return this.all.filter(function (p) { return p.user_id === id; })[0];
    },
    graph: function () {
      var self = this;
      var pick = function (ids) {
        return ids.map(function (id) { return self.byId(id); })
                  .filter(Boolean)
                  .map(function (p) {
                    return { user_id: p.user_id, name: p.name,
                             handle: p.handle, avatar: p.avatar || "", at: 0 };
                  });
      };
      var friends = this.following.filter(function (id) {
        return self.followers.indexOf(id) !== -1; });
      // Somebody who has followed and not been followed back, so the popup
      // and the badge both have something to show on a phone. `new_followers`
      // is cleared by POST /api/friends/seen, the way the server clears it.
      var fresh = this.seenFollowers
        ? []
        : pick(this.followers.filter(function (id) {
            return self.following.indexOf(id) === -1; })
          ).map(function (p) {
            return { user_id: p.user_id, name: p.name, handle: p.handle,
                     avatar: "", at: 0, follows_back: false };
          });
      var told = this.announced;
      return { following: pick(this.following), followers: pick(this.followers),
               friends: pick(friends), new_followers: fresh,
               // Only who has never been announced (§142), like the server.
               announce: fresh.filter(function (p) { return !told[p.user_id]; }),
               counts: { following: this.following.length,
                         followers: this.followers.length,
                         friends: friends.length } };
    },
    seenFollowers: false,
    //: Followers the popup or banner has already announced (§142).
    announced: {},
    //: The YourFAM avatar row, the shape `/api/profile`'s `circle` has:
    //: friends first, then follows, each flagged from a vibe or an unread
    //: message the fixture actually holds.
    circle: function () {
      var self = this, g = this.graph(), seen = {}, out = [];
      g.friends.concat(g.following).forEach(function (p) {
        if (seen[p.user_id]) return;
        seen[p.user_id] = true;
        out.push({ user_id: p.user_id, name: p.name, handle: p.handle,
                   avatar: p.avatar || "", friend: g.friends.some(function (f) {
                     return f.user_id === p.user_id; }),
                   vibed: p.user_id === "u_beth",
                   fresh: p.user_id === "u_beth" || !!self.unread[p.user_id] });
      });
      return out;
    },
    //: Unread per conversation, cleared when the conversation is opened.
    unread: { u_mike: 1 },
    //: What another listener has chosen to publish. Only ever these three
    //: things: public mixes, vibes, and interests they have not hidden. A
    //: play count here would be a fixture of something the server has no
    //: endpoint for.
    published: function (handle) {
      var who = this.all.filter(function (p) { return p.handle === handle; })[0];
      if (!who) return null;
      return {
        name: who.name, handle: who.handle, avatar: "", joined: 0,
        mixes: PUBLIC_MIXES.filter(function (m) { return m.owner.handle === who.handle; }),
        vibes: [
          { id: 1, query: "how reusable rockets changed the economics of spaceflight",
            title: "Inside the New Space Race", minutes: 5, thread: "", at: 0,
            by: who.name, handle: who.handle },
          { id: 2, query: "why the strait of hormuz moves the oil price",
            title: "The Two-Mile Lane That Moves the Oil", minutes: 3,
            thread: "", at: 0, by: who.name, handle: who.handle }
        ],
        vibe_count: 2,
        interests: ["tech", "world"],
        interest_labels: ["Technology", "World"],
        follows: { following: 3, followers: 4, friends: 2 }
      };
    },
    inbox: function () {
      var self = this;
      var rows = Object.keys(this.threads).map(function (id) {
        var msgs = self.threads[id];
        var who = self.byId(id) || { name: "Someone", handle: "" };
        var last = msgs[msgs.length - 1];
        return { thread: id, with: id, name: who.name, handle: who.handle,
                 avatar: who.avatar || "", last: last, unread: self.unread[id] || 0 };
      });
      var total = rows.reduce(function (n, r) { return n + r.unread; }, 0);
      return { threads: rows, unread: total };
    }
  };

  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    if (url.indexOf("/api/") === -1) return realFetch(input, init);
    var path = url.split("?")[0];
    var method = ((init && init.method) || "GET").toUpperCase();
    var qs = new URLSearchParams((url.split("?")[1] || ""));

    if (path === "/api/audio") {
      return silence(Math.max(1, Number(qs.get("minutes") || 1)) * 60);
    }
    if (path === "/api/attach") {
      if (method === "DELETE") return json({ ok: true });
      // Echo the name back so the chip reads like the real thing. No file is
      // ever parsed here - the preview has no server.
      var sent = JSON.parse((init && init.body) || "{}");
      var stub = FIXTURES["/api/attach"];
      return json({
        id: "preview-" + Math.random().toString(36).slice(2, 8),
        kind: sent.kind || "document",
        name: sent.name || sent.url || stub.name,
        chars: sent.kind === "image" ? 0 : stub.chars,
        url: sent.url || "", preview: stub.preview
      });
    }
    // Enough of an account for the entry flow to be walked end to end. There
    // are no credentials here and nothing is checked - the point is the
    // screens, and a preview that cannot get past its own sign-up form is a
    // preview of one screen.
    if (path === "/api/auth/signup" || path === "/api/auth/login") {
      var creds = JSON.parse((init && init.body) || "{}");
      var me = FIXTURES["/api/auth/me"];
      if (creds.email) me.email = creds.email;
      if (creds.phone) me.phone = creds.phone;
      me.authenticated = true;
      return json(me);
    }
    if (path === "/api/auth/logout") {
      FIXTURES["/api/auth/me"].authenticated = false;
      return json({ ok: true });
    }
    // The intro's answers, kept for as long as the page is open. The real
    // server refuses this without an account; the preview fixture is a
    // signed-in listener, so it accepts.
    if (path === "/api/preferences" && method === "POST") {
      var chosen = JSON.parse((init && init.body) || "{}");
      var stored = FIXTURES["/api/preferences"];
      ["interests", "hidden_interests", "topics", "language", "weekly_recap",
       "intro_done", "profile_interests"].forEach(function (k) {
        if (chosen[k] !== undefined && chosen[k] !== null) stored[k] = chosen[k];
      });
      // Where they say they are. Each part merged on its own, like
      // `PreferenceStore.save`, so the Where you are editor can send a
      // corrected city without resending a country that has not changed -
      // and `label` is derived rather than stored, for the same reason
      // `preferences.Location.label` is a property.
      stored.location = stored.location
        || { city: "", region: "", country: "", label: "" };
      ["city", "region", "country"].forEach(function (k) {
        if (chosen[k] !== undefined && chosen[k] !== null) {
          stored.location[k] = String(chosen[k] || "").trim().slice(0, 60);
        }
      });
      stored.location.label = [stored.location.city, stored.location.region,
                               stored.location.country]
        .filter(function (p) { return !!p; }).join(", ");
      // The pinned row, straight back onto the profile the page is about to
      // redraw. The real server recomputes the whole row from it; the fixture
      // only has to agree about what a pin does, which is that it wins and
      // says so.
      if (chosen.profile_interests !== undefined
          && chosen.profile_interests !== null) {
        var mine = FIXTURES["/api/profile"];
        var by = {};
        (mine.interests_ranked || []).forEach(function (r) { by[r.id] = r; });
        mine.interests_pinned = chosen.profile_interests.slice(0, 5);
        mine.interests_source = mine.interests_pinned.length ? "pinned" : "top";
        if (mine.interests_pinned.length) {
          mine.interests_shown = mine.interests_pinned.map(function (id) {
            return by[id] || { id: id, label: id, kind: "topic" };
          });
        } else {
          mine.interests_shown = (mine.interests_ranked || []).slice(0, 5);
        }
      }
      // `topics_chosen` is the resolved form the interface draws, and the
      // real server derives it from `topics` rather than being told it. The
      // preview does the same derivation so the two cannot disagree about the
      // shape - a fixture that returned a hand-written list would go on
      // passing after the server stopped producing that list.
      stored.topics_chosen = (stored.topics || []).map(function (id) {
        var listed = (stored.catalogue || []).filter(function (c) {
          return c.id === id;
        })[0];
        return { id: id, label: listed ? listed.label : id,
                 icon: listed ? listed.icon : "news", typed: !listed };
      });
      return json(stored);
    }
    // --- people, messages and vibes -----------------------------------
    // The prototype used to hold three invented contacts in the page itself.
    // They are server data now, so the preview has to stand in for the
    // server - the flow being looked at on a phone is "find someone, send
    // them an episode, see it in the thread", and a fixture that never
    // changed would show the first frame of it and stop.
    if (path === "/api/me" && method === "POST") {
      var who = JSON.parse((init && init.body) || "{}");
      var mine = FIXTURES["/api/profile"];
      if (who.name) mine.name = who.name;
      if (who.handle) mine.handle = who.handle;
      if (who.avatar !== undefined && who.avatar !== null) mine.avatar = who.avatar;
      return json(mine);
    }
    if (path === "/api/profile") {
      var prof = FIXTURES["/api/profile"];
      prof.circle = PEOPLE.circle();
      return json(prof);
    }
    if (path === "/api/interest") {
      var iid = qs.get("id") || "", ilabel = qs.get("label") || iid;
      var cards = [];
      if (qs.get("filter") === "friends") {
        // What the people this listener follows vibed, on the subject. The
        // fixture's friends vibed two things; matched on the facet's words.
        var onIt = {};
        (FIXTURES["/api/interest"][iid] || []).forEach(function (c) { onIt[c.query] = true; });
        cards = PEOPLE.published("beth").vibes.filter(function (v) {
          return onIt[v.query];
        }).map(function (v) {
          return { query: v.query, title: v.title, minutes: v.minutes, source: "vibe",
                   vibed_by: { name: "Beth Solomon", handle: "beth", avatar: "" } };
        });
        return json({ label: ilabel, episodes: cards, more: false,
                      reason: cards.length ? "" : "Nobody you follow has vibed anything on this yet." });
      }
      cards = (FIXTURES["/api/interest"][iid] || []).slice();
      var off = Number(qs.get("offset") || 0), lim = Number(qs.get("limit") || 6);
      return json({ label: (FIXTURES["/api/preferences"].interests_all || []).filter(function (r) {
                      return r.id === iid; }).map(function (r) { return r.label; })[0] || ilabel,
                    episodes: cards.slice(off, off + lim), more: cards.length > off + lim,
                    reason: cards.length ? "" : "Nothing on this yet. Search it, and yours is the first." });
    }

    // ---- §142: listening history, autocorrect, delete chat, one-time follows
    //
    // History is kept in this page's memory, the shape `/api/history` answers
    // with and the same rules: two weeks, newest first, one row per episode
    // per surface, and Explore refused.
    if (path === "/api/history" && method === "POST") {
      var hb = JSON.parse((init && init.body) || "{}");
      var surfaces = ["myfam", "dailyfam", "search", "other"];
      if (!PREVIEW_AUTHED()) return json({ ok: true, remembered: false });
      if (surfaces.indexOf(hb.surface) === -1) return json({ ok: true, remembered: false });
      var twin = PREVIEW_HISTORY.filter(function (h) {
        return h.query === hb.query && h.minutes === hb.minutes && h.surface === hb.surface; })[0];
      if (hb.retitle) {
        PREVIEW_HISTORY.forEach(function (h) {
          if (h.query === hb.query && h.minutes === hb.minutes && hb.title) h.title = hb.title; });
        return json({ ok: true, remembered: true });
      }
      if (twin) PREVIEW_HISTORY.splice(PREVIEW_HISTORY.indexOf(twin), 1);
      PREVIEW_HISTORY.unshift({ query: hb.query, minutes: hb.minutes, surface: hb.surface,
                                title: hb.title || (twin ? twin.title : ""),
                                context: hb.context || "", at: Date.now() / 1000 });
      return json({ ok: true, remembered: true });
    }
    if (path === "/api/history") {
      if (!PREVIEW_AUTHED()) return json({ error: "You need an account for this." }, 401);
      var hs = qs.get("surface") || "";
      var cutoff = Date.now() / 1000 - 14 * 86400;
      return json({ items: PREVIEW_HISTORY.filter(function (h) {
                      return h.at >= cutoff && (!hs || h.surface === hs); }),
                    surfaces: ["myfam", "dailyfam", "search", "other"], days: 14 });
    }
    // A handful of the corrections `autocorrect.py` makes, so the behaviour
    // can be felt on a phone. The real list is the server's; this has no
    // dictionary and says nothing about any word it does not know.
    if (path === "/api/spell") {
      var sb = JSON.parse((init && init.body) || "{}");
      var FIX = { teh: "the", recieve: "receive", definately: "definitely",
                  tomorow: "tomorrow", becuase: "because", wiht: "with",
                  taht: "that", im: "I'm", dont: "don't", thats: "that's",
                  goverment: "government", leage: "league" };
      return json({ available: true, corrections: (sb.words || []).map(function (w, i) {
        var low = String(w || "").toLowerCase();
        if (w !== low && !((sb.first || [])[i] && w.slice(1) === low.slice(1))) return null;
        var fix = FIX[low] || null;
        if (fix && w !== low) fix = fix.charAt(0).toUpperCase() + fix.slice(1);
        return fix;
      }) });
    }
    if (path === "/api/friends/announced") {
      var ab = JSON.parse((init && init.body) || "{}");
      if (ab.user_id) PEOPLE.announced[ab.user_id] = true;
      return json({ ok: true });
    }
    if (path === "/api/messages/thread" && method === "DELETE") {
      var gone = qs.get("with") || "";
      delete PEOPLE.threads[gone];
      PEOPLE.unread[gone] = 0;
      return json({ ok: true, unread: PEOPLE.inbox().unread });
    }
    if (path === "/api/friends") return json(PEOPLE.graph());
    if (path === "/api/friends/seen") {
      PEOPLE.seenFollowers = true;
      return json({ ok: true });
    }
    if (path === "/api/person") {
      var found = PEOPLE.published(
        String(qs.get("handle") || "").replace(/^@/, ""));
      return found ? json(found)
                   : json({ error: "No listener by that handle." }, 404);
    }
    if (path === "/api/people") {
      var term = (qs.get("q") || "").toLowerCase();
      return json({ people: PEOPLE.all.filter(function (p) {
        return term.length >= 2 &&
          (p.handle.indexOf(term) === 0 || p.name.toLowerCase().indexOf(term) !== -1);
      }).map(function (p) {
        return { user_id: p.user_id, name: p.name, handle: p.handle,
                 avatar: "", following: PEOPLE.following.indexOf(p.user_id) !== -1 };
      }) });
    }
    if (path === "/api/friends/follow" && method === "POST") {
      var who = JSON.parse((init && init.body) || "{}").user_id;
      if (who && PEOPLE.following.indexOf(who) === -1) PEOPLE.following.push(who);
      return json({ ok: true, counts: PEOPLE.graph().counts });
    }
    if (path === "/api/friends/follow" && method === "DELETE") {
      var drop = PEOPLE.following.indexOf(qs.get("user_id"));
      if (drop !== -1) PEOPLE.following.splice(drop, 1);
      return json({ ok: true, counts: PEOPLE.graph().counts });
    }
    if (path === "/api/messages" && method === "GET") return json(PEOPLE.inbox());
    if (path === "/api/messages/thread") {
      var withId = qs.get("with") || "";
      // `since` is what makes an open conversation live, so the preview has
      // to answer it the way the server does: only what arrived after that
      // id, and a cursor back either way. A stub that ignored it would let
      // the poll look like it was working while replacing the conversation
      // with itself every two seconds.
      var since = Number(qs.get("since") || 0);
      var all = (PEOPLE.threads[withId] || []).slice();
      var fresh = since ? all.filter(function (m) { return m.id > since; }) : all;
      // Opening a conversation reads it, the way `mark_read` does.
      if (!since) PEOPLE.unread[withId] = 0;
      var head = all.length ? all[all.length - 1].id : since;
      return json({
        with: PEOPLE.byId(withId) || { user_id: withId, name: "Someone", handle: "" },
        messages: fresh, partial: !!since, head: head
      });
    }
    // The drop-down's poll. Nothing arrives on its own in a fixture - there
    // is no second listener typing - so this reports the cursor and silence,
    // which is the honest fixture answer and the one the banner has to
    // survive. `famPreviewNotify` below is how the smoke test makes something
    // happen.
    if (path === "/api/notifications") {
      if (qs.get("bootstrap")) {
        return json({ messages: [], follows: [], head: NOTIFY.head,
                      unread: PEOPLE.inbox().unread });
      }
      var pending = NOTIFY.pending.splice(0, NOTIFY.pending.length);
      pending.forEach(function (m) { NOTIFY.head = Math.max(NOTIFY.head, m.id); });
      return json({ messages: pending,
                    follows: NOTIFY.follows.splice(0, NOTIFY.follows.length),
                    head: NOTIFY.head, unread: PEOPLE.inbox().unread });
    }
    if (path === "/api/messages" && method === "POST") {
      var sent = JSON.parse((init && init.body) || "{}");
      var to = sent.to || "";
      if (!PEOPLE.threads[to]) PEOPLE.threads[to] = [];
      var written = {
        id: PEOPLE.threads[to].length + 1, thread: to, mine: true,
        kind: sent.query ? "episode" : "text", text: sent.text || "",
        query: sent.query || "", minutes: sent.minutes || 3,
        title: sent.title || "", at: Date.now() / 1000
      };
      PEOPLE.threads[to].push(written);
      // The written row, because the sender draws their message immediately
      // and then swaps this in for it - it carries the id the poll
      // de-duplicates on, and without it the same message arrives twice.
      return json({ ok: true, message: written });
    }
    if (path === "/api/vibes") {
      return json({ vibes: PEOPLE.vibes, count: PEOPLE.vibes.length });
    }
    if (path === "/api/vibe" || path === "/api/echo") {
      var vibeBody = method === "POST"
        ? JSON.parse((init && init.body) || "{}")
        : { query: qs.get("q"), minutes: Number(qs.get("minutes") || 3) };
      if (method === "DELETE") {
        PEOPLE.vibes = PEOPLE.vibes.filter(function (v) {
          return v.query !== vibeBody.query; });
        return json({ ok: true });
      }
      PEOPLE.vibes.unshift({
        id: PEOPLE.vibes.length + 1, query: vibeBody.query,
        title: vibeBody.title || vibeBody.query, minutes: vibeBody.minutes || 3,
        thread: "", at: Date.now() / 1000, by: "You", handle: "you"
      });
      return json(PEOPLE.vibes[0]);
    }
    // "View more" on a rail. The real endpoint reorders the same bank the
    // feed does and marks what is already written; here the bank is the
    // fixture, and a few are marked ready so the ordering and the badge can
    // be looked at on a phone.
    if (path === "/api/myfam/section") {
      var wantKey = qs.get("key") || "most_played";
      var grouped = FIXTURES["/api/myfam/section:" + wantKey];
      if (grouped) {
        var copy = JSON.parse(JSON.stringify(grouped));
        copy.minutes = Number(qs.get("minutes") || 3);
        return json(copy);
      }
      var section = FIXTURES["/api/myfam"].sections.filter(function (s) {
        return s.key === wantKey; })[0];
      if (!section) return json({ error: "No such section." }, 404);
      var seen = {};
      section.topics.forEach(function (t) { seen[t.id] = true; });
      var rest = FIXTURES["/api/topics"].topics.filter(function (t) {
        return !seen[t.id]; });
      var all = section.topics.concat(rest).map(function (t, i) {
        var copy = {}; for (var k in t) copy[k] = t[k];
        copy.cached = (i % 3 === 0);
        return copy;
      });
      all.sort(function (a, b) { return (a.cached === b.cached) ? 0 : (a.cached ? -1 : 1); });
      return json({
        key: wantKey, title: section.title, topics: all,
        ready: all.filter(function (t) { return t.cached; }).length,
        minutes: Number(qs.get("minutes") || 3),
        empty_reason: "", personalised: true,
        algo: "preview"
      });
    }
    if (path === "/api/mixes" && method === "GET") return json(mixes);
    if (path === "/api/mixes" && method === "POST") {
      var body = JSON.parse((init && init.body) || "{}");
      var made = buildMix("m" + (nextMixId++), body.name || "New mix",
                          body.topic_ids || [], !!body.public, body.cover || "");
      mixes.mixes.push(made);
      return json(made);
    }
    // Other listeners' mixes: search, one opened, the (+), and sharing.
    if (path === "/api/mixes/public" && method === "GET") {
      var pq = (qs.get("q") || "").trim();
      return json({ query: pq, mixes: PUBLIC_MIXES.filter(function (m) {
        return publicMixMatches(m, pq); }) });
    }
    if (path.indexOf("/api/mixes/public/") === 0) {
      var one = PUBLIC_MIXES.filter(function (m) { return m.id === path.split("/").pop(); })[0];
      return one ? json(one) : json({ error: "That mix is private or no longer exists." }, 404);
    }
    var mixVerb = path.match(/^\/api\/mixes\/([^/]+)\/(add|share)$/);
    if (mixVerb && method === "POST") {
      if (mixVerb[2] === "add") {
        var src = PUBLIC_MIXES.filter(function (m) { return m.id === mixVerb[1]; })[0];
        if (!src) return json({ error: "That mix is private or no longer exists." }, 404);
        if (src.added) return json({ error: "That mix is already in your DailyFAM." }, 400);
        var name = src.name;
        if (mixes.mixes.some(function (m) { return m.name.toLowerCase() === name.toLowerCase(); }))
          name = src.name + " \u00b7 @" + src.owner.handle;
        var copy = buildMix("m" + (nextMixId++), name, src.items, false, src.cover || "");
        copy.source_id = src.id;
        copy.from = { name: src.owner.name, handle: src.owner.handle };
        mixes.mixes.push(copy);
        src.added = true;
        return json(copy);
      }
      var mine = mixes.mixes.filter(function (m) { return m.id === mixVerb[1]; })[0];
      if (!mine) return json({ error: "That mix no longer exists." }, 404);
      if (!mine.public) return json({ error: "Only a public mix can be shared. Make it public first." }, 409);
      return json(mixShareBody(mine));
    }
    if (path.indexOf("/api/mixes/") === 0) {
      var id = path.split("/").pop();
      var at = mixes.mixes.findIndex(function (m) { return m.id === id; });
      if (at === -1) return json({ error: "That mix no longer exists." }, 404);
      if (method === "DELETE") { mixes.mixes.splice(at, 1); return json({ ok: true }); }
      var patch = JSON.parse((init && init.body) || "{}");
      var current = mixes.mixes[at];
      mixes.mixes[at] = buildMix(id, patch.name || current.name,
        patch.topic_ids !== undefined ? patch.topic_ids : current.items,
        patch.public !== undefined ? patch.public : current.public,
        patch.cover !== undefined ? String(patch.cover || "") : current.cover);
      if (current.from) {
        mixes.mixes[at].from = current.from;
        mixes.mixes[at].source_id = current.source_id;
      }
      return json(mixes.mixes[at]);
    }
    // Save for later. Kept in memory for the life of the page: the point of
    // the preview is the flow - press save, watch the icon go green, press it
    // again - and a fixture that never changed would show the first frame of
    // it and stop.
    //
    // The `q=` form is the save control asking whether it is lit, which is
    // what draws its state when an episode starts.
    if (path.indexOf("/api/saved?") === 0 && method === "GET") {
      var askQ = new URLSearchParams(path.split("?")[1] || "");
      if (askQ.get("q")) {
        var isOn = FIXTURES["/api/saved"].items.some(function (i) {
          return i.query === askQ.get("q")
            && String(i.minutes) === String(askQ.get("minutes")); });
        return json({ saved: isOn });
      }
    }
    if (path.indexOf("/api/saved") === 0 && method === "DELETE"
        && path.indexOf("/api/saved/") !== 0) {
      var offQ = new URLSearchParams(path.split("?")[1] || "");
      var shelfOff = FIXTURES["/api/saved"];
      shelfOff.items = shelfOff.items.filter(function (i) {
        return !(i.query === offQ.get("q")
                 && String(i.minutes) === String(offQ.get("minutes"))); });
      return json({ ok: true, saved: false });
    }
    if (path === "/api/saved" && method === "POST") {
      var wanted = JSON.parse((init && init.body) || "{}");
      var shelf = FIXTURES["/api/saved"];
      var already = shelf.items.filter(function (i) {
        return i.query === wanted.query && i.minutes === wanted.minutes; })[0];
      var item = already || {
        id: "sav_" + Math.random().toString(36).slice(2, 8),
        folder_id: wanted.folder_id || "", query: wanted.query,
        minutes: wanted.minutes || 2, title: wanted.title || wanted.query,
        source: wanted.source || "", created: Date.now() / 1000,
        last_played: 0
      };
      if (!already) shelf.items.unshift(item);
      return json({ ok: true, saved: true, item: item });
    }
    if (path === "/api/saved/folders" && method === "POST") {
      var named = JSON.parse((init && init.body) || "{}");
      var folder = { id: "fld_" + Math.random().toString(36).slice(2, 8),
                     name: named.name, created: Date.now() / 1000, items: 0 };
      FIXTURES["/api/saved"].folders.push(folder);
      return json({ ok: true, folder: folder });
    }
    if (path.indexOf("/api/saved/") === 0) {
      var parts = path.split("/");
      var savedId = parts[3];
      var verb = parts[4] || "";
      var shelf2 = FIXTURES["/api/saved"];
      var found = shelf2.items.filter(function (i) { return i.id === savedId; })[0];
      if (verb === "played" || verb === "move") {
        return json({ ok: true, item: found || null });
      }
      if (method === "DELETE") {
        var where = shelf2.items.indexOf(found);
        if (where >= 0) shelf2.items.splice(where, 1);
        return json({ ok: true });
      }
    }
    // A share link and its per-destination wording. The URL is deliberately a
    // preview one and `public` is false, so the sheet shows the same "this
    // link is not public yet" line the real server shows without one set.
    if (path === "/api/share" && method === "POST") {
      var ep = JSON.parse((init && init.body) || "{}");
      var link = "/s/preview";
      var made = {};
      SHARE_TEMPLATES.forEach(function (t) {
        made[t.key] = {
          target: t.key, label: t.label, kind: t.kind,
          needs_image: t.needs_image, url: link, subject: "",
          text: t.text.replace("{title}", ep.title || "A FAM episode")
                      .replace("{question}", ep.query || "")
                      .replace("{minutes}", ep.minutes || 3)
                      .replace("{url}", link)
        };
      });
      return json({ share: { id: "preview" }, url: link, public: false,
                    card: "/api/share/card?share=preview", targets: made });
    }
    if (FIXTURES[path]) return json(FIXTURES[path]);
    return json({ error: "Not available in the preview build." }, 404);
  };

  function buildMix(id, name, entries, isPublic, cover) {
    var bank = {};
    FIXTURES["/api/topics"].topics.forEach(function (t) { bank[t.id] = t; });
    var catalogue = FIXTURES["/api/preferences"].catalogue;
    var seen = {};
    var items = entries.map(function (e) {
      if (typeof e === "string" && e.indexOf("f:") === 0) return mixFollowItem(e, catalogue);
      if (typeof e === "string") {
        var t = bank[e];
        return t ? { id: t.id, title: t.title, query: t.query, custom: false,
                     subtitle: t.subtitle, icon: t.icon } : null;
      }
      if (e && e.custom !== undefined) return e;           // already an item
      if (e && e.query) return mixTypedItem(e.query, e.title);
      return null;
    }).filter(function (i) {
      if (!i || seen[i.id.toLowerCase()]) return false;
      return (seen[i.id.toLowerCase()] = true);
    });
    return { id: id, name: name, items: items, cover: cover || "",
             topics: items.filter(function (i) { return !i.custom; }),
             topic_ids: items.filter(function (i) { return !i.custom; })
                             .map(function (i) { return i.id; }),
             custom_count: items.filter(function (i) { return i.custom; }).length,
             public: !!isPublic, created_at: 0, updated_at: 0 };
  }

  // Part-heard episodes are the server's now (§127) - see the
  // `/api/godeeper` fixture, which carries two of them.

  // Say what this is, once, without covering anything up.
  window.addEventListener("load", function () {
    var bar = document.createElement("div");
    bar.textContent = "Preview build \\u00b7 interface only \\u00b7 no scripts, no audio";
    // Top, not bottom: at the bottom it sat over the tab bar, which is the
    // one piece of chrome a preview must never obscure.
    bar.style.cssText = "position:fixed;left:0;right:0;top:0;z-index:9999;" +
      "padding:4px 8px;text-align:center;font:600 9px/1.3 monospace;" +
      "letter-spacing:.06em;color:#0d0b12;background:#d4a853;";
    document.body.appendChild(bar);
    setTimeout(function () { bar.style.transition = "opacity .6s"; bar.style.opacity = "0"; }, 6000);
    setTimeout(function () { bar.remove(); }, 7000);
  });
})();
</script>
"""


def build() -> pathlib.Path:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    audio_js = (STATIC / "fam-audio.js").read_text(encoding="utf-8")

    # Inline the player: one file, so it can be opened from anywhere.
    html, n = re.subn(r'<script src="[^"]*fam-audio\.js"[^>]*></script>',
                      "<script>\n" + audio_js + "\n</script>", html)
    if n != 1:
        raise SystemExit("could not inline fam-audio.js - has the script tag changed?")

    sys.path.insert(0, str(ROOT))
    import sharing

    fixtures = load_fixtures()
    # Where each caption sentence starts, the way the server measures it
    # (§127) - here at an even fifteen characters a second, which is close
    # enough to a voice for the one-sentence panel to be judged on a phone.
    transcript = fixtures["/api/transcript"]
    at, starts = 0.0, []
    for line in transcript["sentences"]:
        starts.append(round(at, 2))
        at += len(line) / 15.0 + 0.35
    transcript["starts"] = starts
    shim = (SHIM.replace("__FIXTURES__", json.dumps(fixtures))
               .replace("__MIX_ITEMS__", mix_items_js())
               .replace("__SHARE_TEMPLATES__", json.dumps([
                   {"key": t.key, "label": t.label, "kind": t.kind,
                    "needs_image": t.needs_image, "text": t.template,
                    "mix_text": sharing.MIX_TEMPLATES[t.key][0]}
                   for t in sharing.TARGETS])))
    # The shim has to be installed before the first line of app code runs, so
    # it goes immediately before the first inline <script> in the document.
    at = html.index("<script>")
    html = html[:at] + shim + html[at:]

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    return OUT


def build_artifact(html: str) -> pathlib.Path:
    """Strip the outer document tags so the Artifact wrapper can supply them.

    Everything else is kept verbatim, including the Google Fonts link - the
    one external host the Artifact CSP admits, and the one this app uses.
    """
    head = re.search(r"<head[^>]*>(.*?)</head>", html, re.S)
    body = re.search(r"<body[^>]*>(.*?)</body>", html, re.S)
    if not head or not body:
        raise SystemExit("could not split the document - has the shell changed?")
    head_inner = head.group(1)
    # The host already sets charset and viewport; ours would be duplicates.
    head_inner = re.sub(r"<meta[^>]*charset[^>]*>", "", head_inner)
    head_inner = re.sub(r"<meta[^>]*viewport[^>]*>", "", head_inner)
    OUT_ARTIFACT.write_text(head_inner.strip() + "\n" + body.group(1).strip(),
                            encoding="utf-8")
    return OUT_ARTIFACT


if __name__ == "__main__":
    path = build()
    art = build_artifact(path.read_text(encoding="utf-8"))
    for f in (path, art):
        print(f"{f}  ({f.stat().st_size / 1024:.0f} KB)")
