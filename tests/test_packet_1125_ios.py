"""The owner's 11.25 packet on the iPhone: What's next shows after an episode
and only the listener takes it away; an Explore card plays one episode, by
its id; the card says when it was made and its play is a play button; the
chip row is the main categories and never changes; a playlist's header and
empty page; the Messages composer clears and shrinks after a send.

The Swift is read, not compiled: `ios.yml` compiles it on a Mac."""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
IOS = ROOT / "ios" / "FAM" / "FAM"


def _swift(name: str) -> str:
    return (IOS / name).read_text()


def _struct(source: str, name: str) -> str:
    start = source.index("struct " + name)
    end = source.find("\n}\n", start)
    return source[start:end]


def _func(source: str, signature: str) -> str:
    start = source.index(signature)
    return source[start:source.index("\n    }\n", start)]


# --- 1. What's next ----------------------------------------------------------

def test_one_explore_card_plays_one_episode_and_queues_nothing():
    panel = _struct(_swift("Views/CategoryViews.swift"), "CategoryPanel")
    card = panel[panel.index("ExploreCard(episode: e"):][:600]
    assert "play: { model.playExplore(e) }" in card
    one = _func(_swift("AppModel.swift"), "func playExplore(_ episode: CategoryEpisode)")
    assert "replaceCategoryQueue" not in one and "queued: false" in one
    assert "cachedOnly: true" in one
    # Play all still queues the page behind the first.
    assert "Button { model.playCategory(page.episodes, from: 0) }" in panel
    every = _func(_swift("AppModel.swift"), "func playCategory(_ list: [CategoryEpisode], from index: Int)")
    assert "nowPlaying.replaceCategoryQueue(rest)" in every and "queued: true" in every
    # What a single card leaves behind it is dropped only because it is not
    # marked as part of a category's list; the listener's own queue stays.
    assert "made.fromCategory = queued" in _swift("AppModel.swift")


def test_whats_next_is_offered_with_one_tile_and_to_a_replay():
    now = _swift("NowPlaying.swift")
    offer = _func(now, "private func offerNextUp(_ mine: Int) async")
    assert "guard mine == run, !tiles.isEmpty else { return }" in offer
    assert "tiles.count >= 2" not in now
    # Nothing leaves out an Explore replay.
    assert "cachedOnly" not in offer and "fromCategory" not in offer
    # Still the playlist's and the Queue's first, and the full player's only.
    carry = _func(now, "private func carryOn(after ep: Episode, _ mine: Int)")
    assert carry.index("onEnded?(ep) == true") < carry.index("queue.first") \
        < carry.index("guard playerShowing() else { return }") < carry.index("offerNextUp(mine)")
    assert "if goDeeperOpen { return }" in _func(now, "func ended()")


def test_only_the_listener_takes_whats_next_away():
    now = _swift("NowPlaying.swift")
    start = now.index("// MARK: - What's next")
    section = now[start:now.index("// MARK: - Go Deeper", start)]
    for timer in ("Task.sleep", "asyncAfter", "Timer"):
        assert timer not in section, timer
    overlay = _struct(_swift("Views/PlayerSheets.swift"), "NextUpOverlay")
    for timer in ("Task.sleep", "asyncAfter", "Timer"):
        assert timer not in overlay, timer
    # A tile, the search box, Back - and a tap outside the card.
    assert overlay.count("now.stopNextUp()") == 3
    assert ".onTapGesture { now.stopNextUp() }" in overlay
    assert "now.playNextUp(index)" in overlay


# --- 2. Explore plays by episode id ------------------------------------------

def test_an_explore_row_decodes_the_episode_id_and_plays_by_it():
    row = _struct(_swift("API.swift"), "CategoryEpisode:")
    assert "let episode: String?" in row and "let madeAgeSeconds: Double?" in row
    made = _func(_swift("AppModel.swift"), "private func categoryEpisode(_ e: CategoryEpisode, queued: Bool)")
    # Replay only, by the id when there is one; "" falls back to the question.
    assert 'made.episodeID = e.episode ?? ""' in made and "made.cachedOnly = true" in made
    assert 'surface: ""' in made
    # The id reaches the request the way a history replay's does.
    api = _swift("API.swift")
    assert 'URLQueryItem(name: "episode", value: episode)' in api
    assert "cachedOnly: cachedOnly, episode: ep.episodeID" in _swift("AppModel.swift")


# --- 3 and 4. The card -------------------------------------------------------

def test_an_explore_card_says_the_day_it_was_made():
    card = _struct(_swift("Views/Playlists.swift"), "ExploreCard:")
    assert "guard let age = episode.madeAgeSeconds" in card
    assert "VibePostPlay.dayLabel(age)" in card
    top = card[card.index("Text(kicker)"):card.index("Text(name)")]
    # Trailing on the top line, in the kicker's own label.
    assert top.index("Spacer(minLength: 0)") < top.index("Text(made)")
    assert top.count("FAMFont.mono(9.5)") == 2 and top.count("FAMColor.textFaint") == 2
    assert "static func dayLabel(_ age: Double) -> String" in _swift("Views/VibePosts.swift")


def test_the_cards_play_is_a_round_play_button_with_no_fam_letters():
    card = _struct(_swift("Views/Playlists.swift"), "ExploreCard:")
    assert "FAMLetters(" not in card
    play = card[card.index("private var playButton: some View"):card.index("private var addButton: some View")]
    assert "PlayTriangle().fill(FAMColor.background)" in play and ".offset(x: 1)" in play
    assert ".frame(width: 36, height: 36)" in play and ".background(FAMColor.text, in: Circle())" in play
    # 36 + 4 all round = 44 to hit.
    assert play.index(".padding(4)") < play.index(".contentShape(Rectangle())") < play.index(".padding(-4)")
    assert 'accessibilityLabel("Play \\(name)")' in play
    assert "Capsule()" not in play
    # Add to playlist stays, on the trailing side.
    assert 'Text("Add to playlist")' in card
    row = card[card.index("ViewThatFits(in: .horizontal)"):][:200]
    assert row.index("playButton") < row.index("Spacer(minLength: 0)") < row.index("addButton")


# --- 5. The chips ------------------------------------------------------------

def test_the_chip_row_is_all_and_the_main_categories_and_never_changes():
    views = _swift("Views/CategoryViews.swift")
    chips = _struct(views, "CategoryChipsRow:")
    assert chips.index('CategoryChip(label: "All", on: state.atRest)') < chips.index("ForEach(page.facets) { f in")
    # One row, the same whether or not a category is picked.
    assert chips.count("CategoryChip(") == 2 and "if state" not in chips and "else" not in chips
    for gone in ('"More"', "xmark", "page.mine", "subs", "sub.", "finding", ".filter"):
        assert gone not in chips, gone
    # A followed main category keeps its star and its place.
    assert "starred: mixes.follows(f.id)" in chips
    # The lit chip again is All.
    assert "state = state.node == id ? CategoryState() : CategoryState(root: id, node: id)" in chips
    assert "struct CategoryFind" not in views and "finding" not in views
    for name in ("Views/MyFAMView.swift", "Mixes.swift"):
        assert "categorySubs" not in _swift(name) and "CategoryFind" not in _swift(name), name
    # Play all and Follow stay inside a category.
    panel = _struct(views, "CategoryPanel:")
    assert "if !node.isEmpty { actions(page) }" in panel and "followButton" in panel


# --- 6. A playlist's page ----------------------------------------------------

def test_a_playlists_header_has_no_line_under_it():
    rows = _swift("Views/Playlists.swift")
    head = _struct(rows, "PlaylistHead<")
    assert 'accessibilityLabel("myFAM Playlist")' in head
    assert "static func sub(" not in rows and "Self.sub(" not in rows
    assert "DateFormatter" not in head and "let count: Int" not in head
    assert "PlaylistHead(back: { back() })" in _swift("Views/MixView.swift")
    # The pill still says the name and the count.
    pill = _struct(rows, "PlaylistPill:")
    assert '"Play " + name' in pill and '"\\(count) stories"' in pill


def test_an_empty_playlist_offers_topics_under_its_own_details():
    page = _swift("Views/MixView.swift")
    body = page[page.index("var body: some View"):page.index(".background(FAMColor.background)")]
    empty = body[body.index("if mix.items.isEmpty {"):body.index("} else if isEditing(mix) {")]
    assert empty.index("hero(mix)") < empty.index("actions(mix)") \
        < empty.index('EmptyNote(text: "Nothing in this playlist yet.') < empty.index("addTopicsPill")
    # One with topics keeps its order: stories, Add topics, then the details.
    full = body[body.index("playSection(mix)"):]
    assert full.index("playlistRows(mix)") < full.index("addTopicsPill") < full.index("hero(mix)")


# --- 7. The Messages composer ------------------------------------------------

def test_a_sent_message_leaves_the_box_empty():
    social = _swift("Social.swift")
    send = _func(social, "func sendText()")
    assert send.index('draft = ""') < send.index("clearSent(text)")
    again = social[social.index("/// **The box really is empty after a send**"):
                   social.index("/// The composer's (+)")]
    assert again.count("self?.clearIfStill(sent)") == 2
    assert "Task.sleep(nanoseconds: 150_000_000)" in again
    # Only nothing, or exactly what was sent: what was typed since is kept.
    assert ("guard draft.isEmpty || draft.trimmingCharacters(in: .whitespacesAndNewlines) == sent "
            "else { return }") in again
    assert "11.25 packet" in again
    chat = _swift("Views/ChatView.swift")
    sender = _func(chat, "private func send()")
    assert sender.index("corrector?.reset()") < sender.index("chat.sendText()")
    composer = chat.split("private var composer: some View {", 1)[1].split("private func send()", 1)[0]
    # Return and the arrow both go through it.
    assert composer.count("send()") == 2 and "chat.sendText()" not in composer


def test_the_box_is_sized_by_a_hidden_mirror_of_the_draft():
    chat = _swift("Views/ChatView.swift")
    composer = chat.split("private var composer: some View {", 1)[1].split("private func send()", 1)[0]
    assert "11.25 packet" in composer
    mirror = composer[composer.index("Text(Self.mirror(chat.draft))"):composer.index(".overlay(alignment: .topLeading) {")]
    for part in (".font(FAMFont.body(12.5))", ".lineLimit(1...5)",
                 ".fixedSize(horizontal: false, vertical: true)", ".opacity(0)",
                 ".accessibilityHidden(true)"):
        assert part in mirror, part
    field = composer[composer.index(".overlay(alignment: .topLeading) {"):]
    assert 'TextField("", text: $chat.draft' in field
    assert ".frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)" in field
    # The paddings, the floor and the box are the mirror's, as they were the field's.
    box = field[field.index(".onAppear { if corrector == nil"):]
    assert box.index(".padding(.horizontal, 13)") < box.index(".padding(.vertical, 9)") \
        < box.index(".frame(minHeight: 36)") < box.index("RoundedRectangle(cornerRadius: 18)")
    # An empty draft is one space: one line.
    fn = _func(chat, "static func mirror(_ draft: String) -> String")
    assert 'if draft.isEmpty { return " " }' in fn


# --- PARITY.md ---------------------------------------------------------------

def test_parity_says_so():
    parity = (ROOT / "ios" / "FAM" / "PARITY.md").read_text()
    assert "**The 11.25 packet on Explore \\| Playlists**" in parity
    assert "the first starting in 15 s" not in parity
    assert "`ChatModel.clearSent`" in parity
