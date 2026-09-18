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
        "world_trending": live_tiles[2:],
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

    def mix(mix_id, name, ids, typed=(), public=False):
        items = [dict(by_id[i], query=by_id[i]["query"], custom=False) for i in ids]
        items += [{"id": "q:" + t.lower().replace(" ", "")[:10], "title": t, "query": t,
                   "custom": True, "subtitle": "Added by you", "icon": "leaf"} for t in typed]
        return {"id": mix_id, "name": name, "items": items,
                "topics": [i for i in items if not i["custom"]],
                "topic_ids": [i["id"] for i in items if not i["custom"]],
                "custom_count": len(typed), "public": public,
                "created_at": 0, "updated_at": 0}

    mixes = {
        "mixes": [
            mix("m1", "Morning", ["fed-next-move", "ai-agents", "morning-mindset"],
                public=True),
            mix("m2", "At the gym", ["training-load", "the-trade", "habits-research"]),
            mix("m3", "Wind down", ["sleep-science", "anxiety-loop"],
                typed=["what my council is doing about the high street"]),
        ],
        "starters": [{"name": n, "topic_ids": list(i)} for n, i in mixes_mod.STARTER_MIXES],
    }

    explore = {"episodes": [
        {"query": q, "title": q[:1].upper() + q[1:], "minutes": m,
         "plays": p, "thread": th, "age_seconds": age,
         "echoed_by": "Rachel Solomon" if m == 5 else ""}
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

    import time as _time
    return {
        "/api/myfam": myfam,
        "/api/mixes": mixes,
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
                      "title": "The Two-Mile Lane That Moves the Oil"},
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
        "/api/transcript": {"known": True, "sentences": [
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
        "/api/godeeper": {"threads": [
            {"thread": "why the shipping lanes run through Omani water",
             "title": "The Two-Mile Lane That Moves the Oil",
             "from_title": "Why the Strait of Hormuz Moves the Oil Price", "at": 0},
            {"thread": "how NIL money changed college football recruiting",
             "title": "The New College Football Arms Race",
             "from_title": "Who Really Pays for a Stadium", "at": 0},
        ]},
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
            "since": _time.time() - 63 * 86400,
            "name": "Ian Solomon", "handle": "iansolomon",
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
            "languages": [dict(lang) for lang in prefs_mod.LANGUAGES],
            "language_active": prefs_mod.LANGUAGE_ACTIVE,
            "account": True, "saved": True,
            "account_required": "You need an account for this.",
            "interests": [], "language": "en", "weekly_recap": True,
            "recap_week": "", "intro_done": False,
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
  var mixes = JSON.parse(JSON.stringify(FIXTURES["/api/mixes"]));
  var nextMixId = 100;

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
  var PEOPLE = {
    all: [
      { user_id: "u_beth", name: "Beth Solomon", handle: "beth" },
      { user_id: "u_mike", name: "Mike Solomon", handle: "mike" },
      { user_id: "u_rachel", name: "Rachel Solomon", handle: "rachel" }
    ],
    following: ["u_beth", "u_mike", "u_rachel"],
    followers: ["u_beth", "u_rachel"],
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
          minutes: 5, title: "Inside the New Space Race", at: 0 },
        { id: 2, thread: "u_beth", mine: false, kind: "text",
          text: "didn't realize they scrubbed this launch twice before it flew",
          query: "", minutes: 0, title: "", at: 0 }
      ],
      u_mike: [
        { id: 3, thread: "u_mike", mine: true, kind: "episode", text: "",
          query: "who actually makes the world's chips", minutes: 3,
          title: "Who Actually Makes the World's Chips", at: 0 },
        { id: 4, thread: "u_mike", mine: false, kind: "text",
          text: "Just listened — explains a lot about why the stock moved",
          query: "", minutes: 0, title: "", at: 0 }
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
                             handle: p.handle, avatar: "", at: 0 };
                  });
      };
      var friends = this.following.filter(function (id) {
        return self.followers.indexOf(id) !== -1; });
      return { following: pick(this.following), followers: pick(this.followers),
               friends: pick(friends),
               counts: { following: this.following.length,
                         followers: this.followers.length,
                         friends: friends.length } };
    },
    inbox: function () {
      var self = this;
      var rows = Object.keys(this.threads).map(function (id) {
        var msgs = self.threads[id];
        var who = self.byId(id) || { name: "Someone", handle: "" };
        return { thread: id, with: id, name: who.name, handle: who.handle,
                 last: msgs[msgs.length - 1], unread: 0 };
      });
      return { threads: rows, unread: 0 };
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
      ["interests", "language", "weekly_recap", "intro_done"].forEach(function (k) {
        if (chosen[k] !== undefined && chosen[k] !== null) stored[k] = chosen[k];
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
    if (path === "/api/friends") return json(PEOPLE.graph());
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
      return json({
        with: PEOPLE.byId(withId) || { user_id: withId, name: "Someone", handle: "" },
        messages: (PEOPLE.threads[withId] || []).slice()
      });
    }
    if (path === "/api/messages" && method === "POST") {
      var sent = JSON.parse((init && init.body) || "{}");
      var to = sent.to || "";
      if (!PEOPLE.threads[to]) PEOPLE.threads[to] = [];
      PEOPLE.threads[to].push({
        id: PEOPLE.threads[to].length + 1, thread: to, mine: true,
        kind: sent.query ? "episode" : "text", text: sent.text || "",
        query: sent.query || "", minutes: sent.minutes || 3,
        title: sent.title || "", at: Date.now() / 1000
      });
      return json({ ok: true });
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
                          body.topic_ids || [], !!body.public);
      mixes.mixes.push(made);
      return json(made);
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
        patch.public !== undefined ? patch.public : current.public);
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

  function buildMix(id, name, entries, isPublic) {
    var bank = {};
    FIXTURES["/api/topics"].topics.forEach(function (t) { bank[t.id] = t; });
    var items = entries.map(function (e) {
      if (typeof e === "string") {
        var t = bank[e];
        return t ? { id: t.id, title: t.title, query: t.query, custom: false,
                     subtitle: t.subtitle, icon: t.icon } : null;
      }
      if (e && e.custom !== undefined) return e;           // already an item
      if (e && e.query) {
        return { id: "q:" + e.query.toLowerCase().slice(0, 24), title: e.title || e.query,
                 query: e.query, custom: true, subtitle: "Added by you", icon: "leaf" };
      }
      return null;
    }).filter(Boolean);
    return { id: id, name: name, items: items,
             topics: items.filter(function (i) { return !i.custom; }),
             topic_ids: items.filter(function (i) { return !i.custom; })
                             .map(function (i) { return i.id; }),
             custom_count: items.filter(function (i) { return i.custom; }).length,
             public: !!isPublic, created_at: 0, updated_at: 0 };
  }

  // Two part-heard episodes, so the preview shows Go Deeper as it looks once
  // someone has been using the app. Seeded once and then left alone, so
  // anything you do to it in the preview sticks.
  try {
    if (!localStorage.getItem("fam_resume")) {
      localStorage.setItem("fam_resume", JSON.stringify({
        "why everyone is talking about AI agents":
          { minutes: 7, at: 259, saved: Date.now() },
        "who actually makes the world's chips":
          { minutes: 6, at: 50, saved: Date.now() - 9000 }
      }));
    }
  } catch (e) { /* private browsing: the section is simply emptier */ }

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

    shim = (SHIM.replace("__FIXTURES__", json.dumps(load_fixtures()))
               .replace("__SHARE_TEMPLATES__", json.dumps([
                   {"key": t.key, "label": t.label, "kind": t.kind,
                    "needs_image": t.needs_image, "text": t.template}
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
