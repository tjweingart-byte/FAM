"""The follow step and following in the app, on the iPhone (PROBLEMS.md §696,
§697, `rule:follow-topics`): the one thing the build 13 audit found the web
had and the iPhone did not. Seven pieces, each after the web's.

The Swift is read, not compiled: `ios.yml` compiles it on a Mac."""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as appmod  # noqa: E402

IOS = ROOT / "ios" / "FAM" / "FAM"
HTML = (ROOT / "static" / "index.html").read_text()


def _swift(name: str) -> str:
    return (IOS / name).read_text()


def _struct(source: str, name: str) -> str:
    start = source.index("struct " + name + ":")
    end = source.find("\n}\n", start)
    return source[start:end]


def _func(source: str, signature: str, length: int = 1600) -> str:
    return source[source.index(signature):][:length]


def _all_swift() -> str:
    return "\n".join(p.read_text() for p in sorted(IOS.rglob("*.swift")))


# --- the routes ----------------------------------------------------------------

ROUTES = {
    ("GET", "api/v1/follow/tree"),
    ("POST", "api/v1/follow/preview"),
    ("GET", "api/v1/follow/suggest"),
    ("POST", "api/v1/follow"),
    ("POST", "api/v1/follow/mix"),
}


def test_the_five_routes_are_called_with_the_servers_methods():
    api = _swift("API.swift")
    assert 'get("api/v1/follow/tree", [], as: FollowTree.self)' in api
    assert 'send("POST", "api/v1/follow/preview", body: body' in api
    assert 'get("api/v1/follow/suggest", [' in api
    assert 'send("POST", "api/v1/follow", body: ["topic": topic], as: FollowTopicAnswer.self)' in api
    assert 'send("POST", "api/v1/follow/mix", as: FollowMixAnswer.self)' in api
    served = {(m, r.path) for r in appmod.app.routes for m in (getattr(r, "methods", None) or ())}
    for method, path in ROUTES:
        assert (method, "/" + path.replace("api/v1/", "api/")) in served, (method, path)
    # What the server reads: the preview's two lists, the suggestion's two
    # words, the follow's one topic.
    assert '["interests": interests, "topics": topics]' in api
    suggest = _func(api, "func followSuggest(", 500)
    assert 'URLQueryItem(name: "q"' in suggest and 'URLQueryItem(name: "subject"' in suggest


def test_everything_new_decodes_optional_for_an_older_server():
    api = _swift("API.swift")
    assert "let worlds: [FollowWorld]?" in _struct(api, "FollowTree")
    assert "let subtopics: [FollowSubject]?" in _struct(api, "FollowWorld")
    assert "let more: [FollowLeaf]?" in _struct(api, "FollowSubject")
    assert "let topics: [Tile]?" in _struct(api, "FollowPreviewPage")
    assert "let topic: String?" in _struct(api, "FollowSuggestion")
    answer = _struct(api, "FollowTopicAnswer")
    assert "let topics: [String]?" in answer and "let following: Bool?" in answer
    assert "let made: Bool?" in _struct(api, "FollowMixAnswer")
    assert "let teams: [Tile]?" in _struct(api, "RailsPage")
    assert "let subject: String?" in _struct(api, "NextThread")
    assert 'why = (try? c.decodeIfPresent(String.self, forKey: .why)) ?? ""' in api


def test_the_tree_decodes_as_the_server_builds_it(monkeypatch):
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    from fastapi.testclient import TestClient

    tree = TestClient(appmod.app).get("/api/v1/follow/tree",
                                      headers={"X-FAM-Client": "ios/1.0.0+13"}).json()
    assert len(tree["worlds"]) == 8
    world = tree["worlds"][0]
    assert {"id", "label", "subtopics"} <= set(world)
    assert all({"label", "more"} <= set(sub) for sub in world["subtopics"])
    # The eight headings' colours are the web's, id for id.
    hues = dict(re.findall(r'(\w+):"(#[0-9A-F]{6})"', HTML[HTML.index("var FOLLOW_HUES"):][:260]))
    parts = _swift("Views/FollowParts.swift")
    assert {w["id"] for w in tree["worlds"]} == set(hues)
    for world_id, colour in hues.items():
        assert f'"{world_id}": 0x{colour[1:]}' in parts, world_id


# --- 1. the step ---------------------------------------------------------------

def test_the_step_is_the_webs_two_pages():
    step = _struct(_swift("Views/ProfileEdit.swift"), "InterestsView")
    assert 'page == 2 ? "Get specific" : "What topics do you want to follow?"' in step
    for words in ("Tap a subject to follow it and see what is under it.",
                  "Tap a topic to follow or unfollow it, or type your own.",
                  "Pick a few to start. Next, you make each one yours.",
                  "Search, or type any topic", "Type a team or topic",
                  "Pick a general topic first, or type any topic above.",
                  "Topics could not load. You can still type your own above.",
                  "Nothing listed under it yet. Type one to follow it.",
                  " more. Type to find one."):
        assert words in step, words
        assert words in HTML, words
    assert '"Following 1 topic" : "Following \\(n) topics"' in step
    # The A to Z list (§142) went, as on the web.
    assert 'Text("Your interests")' not in step and 'topics chosen"' not in step
    assert "prefs.catalogue" not in step
    # It is still sign-up's last step (the third, now Find your friends is
    # not one), and still what Settings opens.
    assert "StepMarks(step: 3)" in step
    assert "InterestsView(mode: .firstRun)" in _swift("FAMApp.swift")
    assert "InterestsView(mode: .settings)" in _swift("Views/SettingsView.swift")


def test_the_second_page_is_sections_chips_and_a_drawer_in_the_servers_order():
    step = _struct(_swift("Views/ProfileEdit.swift"), "InterestsView")
    assert "static let firstSubjects = 9" in step and "static let firstLeaves = 16" in step
    assert "var FOLLOW_FIRST = 9, FOLLOW_LEAVES = 16;" in HTML
    assert "worlds.filter { facets.contains($0.id) }" in step
    # Server order: the subjects are never sorted here.
    assert ".sorted" not in step
    shown = _func(step, "private func shownSubjects(", 500)
    assert "subs.prefix(Self.firstSubjects)" in shown
    assert "where sub.label == opened || follows(sub.label)" in shown
    # A subject's first tap follows it and opens its drawer; the open one's
    # tap unfollows and closes; a followed, closed one only opens.
    tap = _func(step, "private func tapSubject(", 400)
    assert "if open[world] == label {" in tap
    assert "setFollow(label, false)" in tap and "open[world] = nil" in tap
    assert "if !follows(label) { setFollow(label, true) }" in tap and "open[world] = label" in tap
    # §697 (7): an unfollowed subject's drawer shuts with it.
    assert "closeDrawer(label)" in _func(step, "private func setFollow(", 400)
    assert "closeDrawer(topic.label)" in _func(step, "private func unfollow(", 200)
    drawer = _func(step, "private func drawer(", 2600)
    assert '"Under " + sub.label' in drawer
    assert "prefix(want.isEmpty ? Self.firstLeaves : 40)" in drawer
    assert "let canAdd = !raw.isEmpty && !exact && !follows(raw)" in drawer
    assert '"Follow \\u{201C}" + raw + "\\u{201D} +"' in drawer
    # The search across the whole tree follows anything typed.
    found = _func(step, "@ViewBuilder private var found", 700)
    assert "if !exact && !follows(raw) { typedRow(raw) }" in found
    assert ".prefix(8)" in _func(step, "private func matches(", 500)
    assert ".onSubmit { addSearchTyped() }" in step


def test_the_buttons_are_what_the_mode_and_the_page_decide():
    step = _struct(_swift("Views/ProfileEdit.swift"), "InterestsView")
    label = _func(step, "private var primaryLabel", 250)
    assert 'if page == 1 && !facets.isEmpty { return "Next" }' in label
    assert 'return fromSettings ? "Save" : "Start listening"' in label
    assert "if !(page == 1 && fromSettings) {" in step
    assert 'Text(page == 2 ? "Back" : "Skip for now")' in step
    assert 'CloseCircle(label: "Back to Settings") { dismiss() }' in step
    # Saved through the preference call the iPhone already had, as the web
    # saves it: both lists, and `intro_done` on the first run alone.
    save = _func(step, "private func save()", 1700)
    assert 'var body: [String: Any] = ["interests": facets, "topics": topicIDs]' in save
    assert 'if !fromSettings { body["intro_done"] = true }' in save
    assert "await you.savePreference(body)" in save and "model.finishEntry()" in save
    skip = _func(step, "private func skip()", 400)
    assert '"interests": [String](), "topics": [String](), "intro_done": true' in skip
    assert "followMix" not in skip


# --- 2. the row as it is chosen --------------------------------------------------

def test_the_row_is_drawn_in_the_dock_on_every_change_debounced():
    step = _struct(_swift("Views/ProfileEdit.swift"), "InterestsView")
    assert ".onChange(of: chosen) { _, _ in paintPreview() }" in step
    assert ".onChange(of: facets) { _, _ in paintPreview() }" in step
    paint = _func(step, "private func paintPreview()", 800)
    assert "previewTask?.cancel()" in paint
    assert "try? await Task.sleep(nanoseconds: 120_000_000)" in paint
    assert "api.followPreview(interests: worlds, topics: topics)" in paint
    assert "if topics.isEmpty && worlds.isEmpty {" in paint
    # The web's debounce.
    assert "}, 120);" in HTML[HTML.index("function paintFollowPreview"):][:2400]
    dock = _func(step, "private var dock", 500)
    assert dock.index("FollowPreviewRow(tiles: preview)") < dock.index("EntryButton(label: primaryLabel")
    row = _struct(_swift("Views/FollowParts.swift"), "FollowPreviewRow")
    assert "Made for you \\u{00B7} preview" in row
    assert "Follow something specific and its episode shows up here." in row
    assert "Follow something specific and its episode shows up here." in HTML


# --- 3. the wording --------------------------------------------------------------

def test_settings_says_topics_you_follow_and_a_profile_still_says_interests():
    settings = _swift("Views/SettingsView.swift")
    assert 'SetRow(label: "Topics you follow", value: you.interestsSummary)' in settings
    assert 'SetRow(label: "Your interests"' not in settings
    assert 'settingsRow("Topics you follow"' in HTML
    # A profile's line is still Interests, as on the web.
    assert '<span class="yf-label">Interests</span>' in HTML
    assert "Add interests in Edit profile" in _swift("Views/YouView.swift")


# --- 4. "Follow this?" -----------------------------------------------------------

def test_follow_this_is_asked_after_a_search_that_is_not_private():
    now = _swift("NowPlaying.swift")
    assert "var askFollow: (Episode, String) -> Void" in now
    assert 'if !ep.cachedOnly { askFollow(ep, answer.subject ?? "") }' in now
    model = _swift("AppModel.swift")
    assert "nowPlaying.askFollow = { [weak self] ep, subject in self?.maybeAskFollow(ep, subject: subject) }" in model
    ask = _func(model, "func maybeAskFollow(", 1500)
    assert "listener?.authenticated == true, !privateSearch, !ep.isPrivate" in ask
    assert 'ep.surface == "search"' in ask
    # Once, then once more when the brief's subject arrives.
    assert "if key.isEmpty || tries >= 2 || (tries == 1 && subject.isEmpty) { return }" in ask
    assert "followAskTried[key] = subject.isEmpty ? 1 : 2" in ask
    assert "api.followSuggest(query: query, subject: subject)" in ask
    assert "if (state.no ?? []).contains(topic.lowercased()) { return }" in ask


def test_the_card_is_first_on_dailyfam_and_answers_as_the_web_does():
    daily = _swift("Views/DailyView.swift")
    page = daily[daily.index("FreeMonthCard()"):][:400]
    assert page.index("FreeMonthCard()") < page.index("FollowAskCard()") < page.index("FacesRow()")
    card = _struct(_swift("Views/FollowParts.swift"), "FollowAskCard")
    assert "if let ask = model.followAsk {" in card
    assert 'Text("After you searched ")' in card and 'Text("Follow " + ask.topic + "?")' in card
    assert 'FollowChip(label: "Follow", on: true) { model.answerFollowAsk(true) }' in card
    assert 'FollowChip(label: "Not now") { model.answerFollowAsk(false) }' in card
    model = _swift("AppModel.swift")
    answer = _func(model, "func answerFollowAsk(", 1900)
    assert "suffix(50)" in answer
    assert "api.followTopic(ask.topic)" in answer
    # §697 (8): a full list keeps nothing, and the card never says "Following".
    assert answer.index("guard answer.following == true else {") < answer.index('say("Following " + ask.topic)')
    full = "You follow as many topics as FAM keeps. Remove one in Settings first."
    assert f'static let followFullLine = "{full}"' in model and full in HTML
    assert "await self.loadRails()" in answer
    # Not followed: the question comes back rather than vanishing.
    failed = answer[answer.index("} catch {"):]
    assert "again.ask = ask" in failed and 'say("Could not follow that just now.")' in failed
    assert "Could not follow that just now." in HTML
    # Painted with the page, as the web paints it after the feed.
    assert "paintFollowAsk()" in _func(model, "func loadRails() async", 1900)


def test_the_card_is_kept_with_whose_it_is_and_cleared_on_sign_out():
    """§697 (3): kept on the device with the listener's id, so the next
    account to sign in never sees the last one's search."""
    model = _swift("AppModel.swift")
    assert 'static let followAskKey = "fam.followAsk"' in model
    assert 'localStorage.getItem("fam.followAsk")' in HTML
    kept = _struct(model, "FollowAskKept")
    assert "var who: String" in kept and "var data: FollowAskState" in kept
    read = _func(model, "private func followAskState()", 500)
    assert 'kept.who == (listener?.userId ?? "")' in read
    assert 'FollowAskKept(who: listener?.userId ?? "", data: state)' in model
    forget = _func(model, "fileprivate func forgetFollowAsk()", 300)
    assert "UserDefaults.standard.removeObject(forKey: Self.followAskKey)" in forget
    assert "followAsk = nil" in forget and "followAskTried = [:]" in forget
    assert "forgetFollowAsk()" in _func(model, "func forgetEverything()", 1800)
    # The id is the server's own answer, never sent back as a parameter
    # (`listener-id-server`).
    api = _swift("API.swift")
    follow = api[api.index("// MARK: - Following topics"):]
    assert "user" not in follow.lower().replace("fam.followask", "")


# --- 5. "Because you follow X" ---------------------------------------------------

def test_a_cards_reason_is_its_own_line_and_the_hook_is_untouched():
    api = _swift("API.swift")
    assert "let why: String" in _struct(api, "Tile")
    card = _struct(_swift("Views/DailyView.swift"), "TileCard")
    body = _func(card, "private func card(faded: Bool)", 1200)
    assert "if !tile.why.isEmpty { because.padding(.top, 5) }" in body
    assert body.index("because.padding") < body.index("hook.padding(.top, 6)")
    assert 'Text("Because you follow " + tile.why)' in card
    assert "Because you follow ' + escapeHtml(topic.why)" in HTML
    # The hook reads what it always read.
    hook = _func(card, "private var hook: some View", 900)
    assert "tile.why" not in hook and "Text(tile.hook)" in hook


# --- 6. "Your teams" -------------------------------------------------------------

def test_your_teams_is_above_the_rails_with_each_score_line_and_nothing_when_empty():
    model = _swift("AppModel.swift")
    assert "@Published private(set) var teams: [Tile] = []" in model
    load = _func(model, "func loadRails() async", 1900)
    assert "teams = page.teams ?? []" in load
    # Never from the remembered page: a kept score is not drawn as today's.
    assert load.count("teams = ") == 1
    assert "teams = []" in _func(model, "func forgetEverything()", 1800)
    daily = _swift("Views/DailyView.swift")
    feed = _func(daily, "@ViewBuilder private var feed", 900)
    assert feed.index("TeamStrip()") < feed.index("ForEach(Array(model.rails.enumerated())")
    strip = _struct(_swift("Views/FollowParts.swift"), "TeamStrip")
    assert "if !games.isEmpty {" in strip and 'Text("Your teams")' in strip
    assert "model.teams.filter { !$0.title.isEmpty }" in strip
    assert "if !game.liveLine.isEmpty {" in strip and "Text(game.liveLine)" in strip
    assert "model.play(game)" in strip
    assert '<div class="team-strip-h">Your teams</div>' in HTML


# --- 7. the first mix ------------------------------------------------------------

def test_the_first_mix_is_asked_once_after_the_first_runs_save():
    step = _struct(_swift("Views/ProfileEdit.swift"), "InterestsView")
    save = _func(step, "private func save()", 1700)
    first_run = save[save.index("} else {"):]
    # After the save has answered, on the first run alone, and only when a
    # topic was chosen - the web's `if(!topicIds.length) return;`.
    assert save.index("await you.savePreference(body)") < save.index("api.followMix()")
    assert "if !topicIDs.isEmpty {" in first_run and "api.followMix()" in first_run
    assert first_run.index("api.followMix()") < first_run.index("model.finishEntry()")
    settings = save[save.index("if fromSettings {"):save.index("} else {")]
    assert "followMix" not in settings
    web = HTML[HTML.index("function finishIntro()"):][:900]
    assert "if(!topicIds.length) return;" in web and '"/api/follow/mix"' in web
    # One place in the whole app asks for it.
    assert _all_swift().count("followMix()") == 2  # the call, and its definition
    assert "func followMix() async throws -> Bool" in _swift("API.swift")


# --- PARITY.md -------------------------------------------------------------------

def test_parity_says_what_is_built():
    parity = (ROOT / "ios" / "FAM" / "PARITY.md").read_text()
    row = [line for line in parity.splitlines()
           if line.startswith("| The follow step and following in the app (§696)")]
    assert len(row) == 1
    assert "**Not built** |" not in row[0] and "| Done" in row[0]
    assert "still the A to Z list" not in row[0]
