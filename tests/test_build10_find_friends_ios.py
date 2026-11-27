"""Build 10: Find your friends on the iPhone (PROBLEMS.md §675).

§665-§672 built finding friends on the server and the web; the iPhone had
none of it, and the native app is where contacts pay (iPhone Safari has no
Contact Picker). These read the Swift's own source (nothing here compiles
it; `ios.yml` does) and the server's routes the app now calls.
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
IOS = ROOT / "ios" / "FAM"
SRC = IOS / "FAM"


def swift(rel: str) -> str:
    return (SRC / rel).read_text()


def test_the_build_is_ten_or_later_and_the_same_everywhere_it_is_written():
    # Build 11 (§683): App Store Connect already held a 10, uploaded from
    # the round-three catch-up (§681-§682). The project and the registry must name the same build.
    project = (IOS / "project.yml").read_text()
    build = re.search(r'CURRENT_PROJECT_VERSION: "(\d+)"', project)
    assert build and int(build.group(1)) >= 10
    registry = json.loads((ROOT / "releases" / "registry.json").read_text())
    ios = [r for r in registry["releases"] if r["platform"] == "ios" and r["version"] == "1.0.0"]
    assert ios and ios[0]["build"] == build.group(1)


def test_contacts_are_asked_for_only_with_a_reason_shown():
    project = (IOS / "project.yml").read_text()
    assert "NSContactsUsageDescription:" in project
    find = swift("FindFriends.swift")
    assert "requestAccess(for: .contacts)" in find
    # Read only from the button, never on appearing.
    assert find.count("ContactsReader.read()") == 1
    assert "func findFromContacts(" in find


def test_the_api_calls_the_routes_the_server_has():
    api = swift("API.swift")
    for path in ("api/v1/friends/contacts", "api/v1/friends/suggested", "api/v1/friends/follow-all",
                 "api/v1/friends/joined", "api/v1/friends/joined/seen"):
        assert f'"{path}"' in api, path
    contract = json.loads((ROOT / "releases" / "contracts" / "ios-1.0.0.json").read_text())
    for path in ("/api/friends/contacts", "/api/friends/suggested", "/api/friends/follow-all",
                 "/api/friends/joined", "/api/friends/joined/seen"):
        assert path in contract["routes"], path


def test_a_follow_says_where_it_was_tapped():
    from social import FOLLOW_VIA, follow_via
    api = swift("API.swift")
    assert 'if !via.isEmpty { body["via"] = via }' in api
    sources = "".join((SRC / p).read_text() for p in (
        "FindFriends.swift", "Social.swift", "AppLinks.swift",
        "Views/PersonView.swift", "Views/FriendsView.swift"))
    used = set(re.findall(r'via: "([a-z_]+)"', sources))
    used |= set(re.findall(r'via: [^\n]*\? "([a-z_]+)" : ', sources))
    # Both sides of `found == nil ? "friends_page" : "search"`.
    used |= set(re.findall(r'via: [^\n]*\? "[a-z_]+" : "([a-z_]+)"', sources))
    used |= {"contacts", "contacts_all"}          # built from a prefix
    assert used, "no via found"
    for via in used:
        assert follow_via(via) == via, via
        assert follow_via("signup_" + via.removeprefix("signup_")) != "", via
    # `after_follow` went with the row under Follow, and `signup_` with the
    # sign-up step (the testers' note): suggestions are followed from the
    # Friends page's list alone.
    assert {"suggested", "profile", "profile_link", "their_friends",
            "joined", "friends_page", "search"} <= used
    assert "after_follow" not in used and not any(v.startswith("signup_") for v in used)
    assert used <= FOLLOW_VIA


def test_sign_up_has_three_steps_and_find_your_friends_is_not_one():
    # It was the third of four (§665); the testers' note took it out of the
    # first run. Who you are goes straight on to the interests.
    assert "static let count = 3" in swift("Views/YouParts.swift")
    edit = swift("Views/ProfileEdit.swift")
    assert "StepMarks(step: 2)" in edit and "StepMarks(step: 3)" in edit
    assert "StepMarks(step: 4)" not in edit
    assert "StepMarks(" not in swift("FindFriends.swift")
    assert "findFriends" not in swift("YourFAM.swift")
    assert "FindFriendsView" not in swift("FAMApp.swift") + swift("FindFriends.swift")
    assert "afterIdentity :" not in swift("YourFAM.swift")
    assert edit.count("model.entry = .interests") == 2


def test_suggestions_are_a_list_on_the_friends_page_and_nowhere_else():
    find = swift("FindFriends.swift")
    assert "struct SuggestedList: View" in find
    # No sideways row of cards anywhere in the app.
    everything = "".join(p.read_text() for p in SRC.rglob("*.swift"))
    for gone in ("SuggestCards", "DiscoverPeopleSection", "loadAround(", "find.around"):
        assert gone not in everything, gone
    assert everything.count("SuggestedList(") == 1
    friends = swift("Views/FriendsView.swift")
    assert 'SuggestedList(people: find.discover, via: "suggested")' in friends
    # Instagram's order: the search, the tabs and the listener's own people,
    # then who to add; over a search, the results alone.
    assert friends.index("searchPill\n") < friends.index("FriendsTabs(tab:") < friends.index("findSection\n")
    body = friends[friends.index("@ViewBuilder private var findSection"):friends.index("// MARK: The graph")]
    assert (body.index("SuggestedList(") < body.index("ContactsBlock()")
            < body.index("ProfileLinkRow(") < body.index("Invite new users"))
    assert "if found == nil {\n                        findSection" in friends


def test_where_you_are_is_one_box_with_the_servers_options():
    api = swift("API.swift")
    assert '"api/v1/places/suggest"' in api
    edit = swift("Views/ProfileEdit.swift")
    assert edit.count("PlaceField(place: $place)") == 2      # the step and the editor
    for gone in ('Text("State or region")', 'Text("Country")', ".addressState", ".countryName"):
        assert gone not in edit, gone
    # A place nobody offered is still saved: the typed line cut at commas.
    assert 'typed.split(separator: ",")' in edit
    assert 'if typed.isEmpty { return ["city": "", "region": "", "country": ""] }' in edit
    assert "words.count >= 2, !place.settled" in edit
    assert "250_000_000" in edit


def test_profiles_carry_mutuals_and_a_qr():
    api = swift("API.swift")
    assert "let mutuals: PersonMutuals?" in api
    person = swift("Views/PersonView.swift")
    assert "MutualsLine(mutuals: m)" in person
    # The row of cards under Follow went (the testers' note).
    assert "Suggested for you" not in person
    assert "QRSubject(handle: handle, name:" in person
    find = swift("FindFriends.swift")
    # The web's words for one, two and more names.
    assert 'Text("Followed by ")' in find
    assert '" and "' in find and '", and "' in find


def test_profile_link_opens_in_the_app_and_admits_only_a_handle():
    links = swift("AppLinks.swift")
    assert "case person(String)" in links
    assert "static func linkHandle(" in links
    assert "(2...24).contains(h.count)" in links
    assert 'social.openPerson(handle: handle, via: "profile_link")' in links


def test_just_joined_uses_the_follower_card_and_is_forgotten_when_shown():
    social = swift("Social.swift")
    assert "func checkJoined() async" in social
    assert "await api.joinedSeen(next.userId)" in social
    assert 'via: joined ? "joined" : ""' in social
    parts = swift("Views/SocialParts.swift")
    assert '" just joined FAM"' in parts
