"""iOS build 12 (PROBLEMS.md §692): the audit of Main since build 11 found one
thing the iPhone lacked - Explore | Playlists (§691) - and the owner added
one more while it was built: a Search friends button top right of dailyFAM,
on the web and the iPhone.

The Swift is read, not compiled: `ios.yml` compiles it on a Mac."""

from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
IOS = ROOT / "ios" / "FAM" / "FAM"
HTML = (ROOT / "static" / "index.html").read_text()


def _swift(name: str) -> str:
    return (IOS / name).read_text()


def _struct(source: str, name: str) -> str:
    start = source.index("struct " + name + ":")
    end = source.find("\n}\n", start)
    return source[start:end]


# --- the build ---------------------------------------------------------------

def test_the_build_is_twelve_and_the_registry_agrees():
    project = (ROOT / "ios" / "FAM" / "project.yml").read_text()
    build = re.search(r'CURRENT_PROJECT_VERSION: "(\d+)"', project).group(1)
    assert int(build) >= 12
    ios = [r for r in json.loads((ROOT / "releases" / "registry.json").read_text())["releases"]
           if r["platform"] == "ios"]
    assert ios[0]["build"] == build


def test_explore_asks_a_route_the_contract_already_holds():
    """No route was added, so the contract was not recut: Explore's All is
    the category route with `id=all` and `sort=trending`."""
    api = _swift("API.swift")
    assert 'static let exploreAll = "all"' in api
    call = api[api.index("func categoryEpisodes("):][:400]
    assert '"api/v1/categories/episodes"' in call and 'value: "trending"' in call
    contract = json.loads((ROOT / "releases" / "contracts" / "ios-1.0.0.json").read_text())
    assert "/api/categories/episodes" in json.dumps(contract)


# --- Explore | Playlists (§691) on the iPhone --------------------------------

def test_the_tabs_are_explore_then_playlists_and_an_arrival_opens_on_explore():
    tabs = _struct(_swift("Views/CategoryViews.swift"), "PlayfamTabs")
    assert tabs.index('button(.categories, "Explore")') < tabs.index('button(.mixes, "Playlists")')
    assert "index: tab == .categories ? 0 : 1" in tabs
    mixes = _swift("Mixes.swift")
    assert "@Published var playfamTab: PlayfamTab = .categories" in mixes
    arrived = mixes[mixes.index("func arrived(playlists: Bool = false)"):][:400]
    assert "playfamTab = (playlists || fresh) ? .mixes : .categories" in arrived
    # A flow that is about a playlist comes back to Playlists.
    assert "model.mixes.arrived(playlists: true)" in _swift("FAMApp.swift")
    assert "mixes.arrived(playlists: true)" in _swift("Views/MixScreens.swift")
    back = _swift("Views/MixView.swift")
    back = back[back.index("private func back()"):][:200]
    assert "mixes.playfamTab = .mixes" in back
    for opener in ("func openMix(_ id: String)", "func openPublicMix(_ id: String)"):
        body = _swift("AppModel.swift")
        assert "mixes.playfamTab = .mixes" in body[body.index(opener):][:300], opener


def test_all_is_the_first_chip_and_the_panel_draws_cards():
    views = _swift("Views/CategoryViews.swift")
    chips = _struct(views, "CategoryChipsRow")
    assert chips.index('CategoryChip(label: "All", on: true)') < chips.index("ForEach(page.mine)")
    panel = _struct(views, "CategoryPanel")
    assert "node.isEmpty ? FAMAPI.exploreAll : node" in panel
    assert "ExploreCard(episode: e" in panel and "AddToMixSheet(query: e.query" in panel
    # §500's hero and §550's list of categories went, as on the web.
    assert "struct CategoryHome" not in views and "private func hero(" not in views
    # Play all and Follow only inside a category.
    assert "if !node.isEmpty { actions(page) }" in panel
    # Asked again on every arrival, so All is never yesterday's.
    assert "mixes.exploreAsk" in panel and "exploreAsk += 1" in _swift("Mixes.swift")


def test_an_explore_card_has_the_webs_two_buttons_and_no_invented_line():
    card = _struct(_swift("Views/Playlists.swift"), "ExploreCard")
    assert 'Text("Add to playlist")' in card and "FAMLetters(" in card
    assert "if let hook = episode.hook, !hook.isEmpty" in card
    api = _swift("API.swift")
    episode = _struct(api, "CategoryEpisode")
    for field in ("let node: String?", "let hook: String?", "let trending: Bool?"):
        assert field in episode, field
    # The web's card, for the words.
    assert "'Add to playlist</button>'" in HTML


def test_a_playlist_is_a_row_card_and_new_playlist_is_under_them():
    card = _struct(_swift("Views/Playlists.swift"), "PlaylistCard")
    assert "mix.topicsLine" in card and "MixesModel.briefings(mix.items.count)" in card
    assert 'accessibilityLabel("Play \\(mix.name)")' in card
    page = _swift("Views/MyFAMView.swift")
    assert "PlaylistCard(mix: mix" in page and "MixDeckCard(mix:" not in page
    assert 'Text("Create new mix")' not in page and 'Text("New playlist")' in page
    # The search bar keeps its place in the stack, so opening it keeps the caret.
    pane = page[page.index("if !searching {"):][:400]
    assert pane.index("newPlaylistRow") < pane.index("searchBar") < pane.index("if searching { searchResults }")
    line = _swift("Views/Playlists.swift")
    assert 'names.prefix(3).joined(separator: ", ") + " and \\(names.count - 3) more"' in line


def test_a_playlists_page_follows_the_player():
    page = _swift("Views/MixView.swift")
    assert "PlaylistHead(name:" in page and "PlaylistIcons.sliders" in page
    body = page[page.index("var body: some View"):page.index(".background(FAMColor.background)")]
    playing = body[body.index("playSection(mix)"):]
    # What plays first, the playlist's own details under it.
    assert playing.index("playlistRows(mix)") < playing.index("addTopicsPill") < playing.index("hero(mix)")
    rows = _swift("Views/Playlists.swift")
    order = rows[rows.index("func pageRows(live list: MixPlaylist?)"):][:1500]
    assert "n < list.pos ? .heard : (n == list.pos ? .now : .next)" in order
    assert "item.heardToday ? .heard : .none" in order
    assert "for item in items where !seen.contains(item.id)" in order
    pill = _struct(rows, "PlaylistPill")
    assert '(playing ? "Playing " : "Paused ")' in pill and '"Play " + name' in pill
    item = _struct(_swift("API.swift"), "MixItem")
    assert "let episodeHook: String" in item and "let heardToday: Bool" in item


def test_the_heart_is_saved_for_later_and_one_shelf_with_the_player():
    page = _swift("Views/MixView.swift")
    rows = page[page.index("private func playlistRows("):][:1400]
    assert "MixPlaylist.prompt(row.item).words" in rows
    assert 'source: "myFAM"' in rows and "model.isSaved(query: words" in rows
    model = _swift("AppModel.swift")
    assert "nowPlaying.savedChanged = " in model
    toggle = model[model.index("func toggleSaved(query: String"):][:1200]
    assert "nowPlaying.noteSaved(query: query, minutes: minutes, on: !on)" in toggle
    now = _swift("NowPlaying.swift")
    assert now.count("savedChanged(ep.key.query, ep.key.minutes,") == 3
    # Never a vibe (`op-social-live`: not on the mini bar).
    mini = _swift("Views/PlayerView.swift")
    mini = mini[mini.index("struct MiniPlayer"):]
    assert "now.toggleSave()" in mini and "vibe" not in mini[:mini.index("private var asked")].lower().replace("never a vibe", "")


def test_the_mini_player_says_where_the_playlist_is_and_keeps_its_x():
    mini = _swift("Views/PlayerView.swift")
    mini = mini[mini.index("struct MiniPlayer"):]
    assert "now.episode?.mixKey == list.key" in mini
    assert 'of \\(list.order.count)"' in mini
    assert mini.count("if inPlaylist {") == 2 and "model.skipMixItem()" in mini
    assert 'accessibilityLabel("Hide the player")' in mini


def test_the_tour_names_the_tabs_the_iphone_has():
    tour = _swift("Tour.swift")
    assert 'title: "Explore"' in tour and 'title: "Your playlists"' in tour
    assert 'title: "Your mixes"' not in tour and 'title: "Categories"' not in tour


# --- Search friends on dailyFAM (the owner, 11.24) ---------------------------

def test_dailyfam_has_a_search_friends_button_top_right_on_the_web():
    start = HTML.index('<section class="screen" id="screen-myfam">')
    head = HTML[start:HTML.index('<div class="scroll">', start)]
    button = head[head.index('id="dailyFriendSearchBtn"') - 40:]
    assert 'class="myfam-head-btn"' in button and 'aria-label="Search friends"' in button
    assert 'onclick="openFriendSearch()"' in button
    # After the wordmark in a header that packs to the right.
    assert head.index("</h2>") < head.index('id="dailyFriendSearchBtn"')
    css = HTML.split("  .myfam-header-fixed{", 1)[1].split("}", 1)[0]
    assert "justify-content:flex-end" in css
    fn = HTML.split("function openFriendSearch(", 1)[1].split("\n  }\n", 1)[0]
    # The page that exists, never a second search; a guest gets its door.
    assert fn.index("openFriends();") < fn.index("if(!AUTH.authenticated) return;") < fn.index(".focus(")


def test_dailyfam_has_a_search_friends_button_on_the_iphone():
    daily = _swift("Views/DailyView.swift")
    head = daily[daily.index('Wordmark(lead: "daily", size: 28)'):][:1400]
    assert ".overlay(alignment: .trailing)" in head
    assert "model.social.open(.friends)" in head and "Icons.search" in head
    assert 'accessibilityLabel("Search friends")' in head
