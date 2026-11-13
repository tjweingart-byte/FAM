"""§575: Message only between friends, on the web as in the app (§520).

The server refuses a one-to-one send to anybody who is not a mutual follow,
so the web drew a Message button that could only fail. The owner (11.12):
"Change the web to match the app."
"""
from pathlib import Path

WEB = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text()
SWIFT = Path(__file__).resolve().parent.parent / "ios" / "FAM" / "FAM"


def test_profile_message_button_is_for_friends_only():
    start = WEB.index("function renderFriendProfile(")
    body = WEB[start:start + 6000]
    gate = body.index("var mutual = (friendsData.friends || []).some(")
    button = body.index("Message</button>")
    assert gate < button
    assert "(mutual ? '<button class=\"yf-btn gold\" onclick=\"messagePerson(" in body
    ios = (SWIFT / "Views" / "PersonView.swift").read_text()
    assert "if social.isFriend(userID) {" in ios


def test_a_face_without_a_handle_opens_a_chat_only_with_a_friend():
    start = WEB.index("function openCircleFace(")
    body = WEB[start:start + 900]
    assert 'toast("You can message people who follow you back.");' in body
    assert body.index("if(notAFriend(person.user_id)){") < body.index("openThreadWith(person)")


def test_not_a_friend_only_once_the_friends_list_has_loaded():
    """Audit 4: `friendsData` starts as an empty list, so "is not on it" was
    true before the list had loaded and a face was refused that the comment
    said the server should decide. `notAFriend` asks only once it has."""
    start = WEB.index("function notAFriend(")
    body = WEB[start:start + 300]
    assert "return friendsLoaded && !(friendsData.friends || []).some(" in body
    assert WEB.count("friendsLoaded = true;") == WEB.count("friendsData = d;") == 3
    assert "friendsLoaded = false;" in WEB
    social = (SWIFT / "Social.swift").read_text()
    assert "func mayNotMessage(_ userID: String) -> Bool {\n        graph != nil && !isFriend(userID)" in social
    stories = (SWIFT / "Views" / "Stories.swift").read_text()
    assert stories.count("model.social.mayNotMessage(") == 2
    assert "isFriend(" not in stories


def test_a_typed_handle_is_held_to_the_same_rule_on_the_web():
    """Audit 4 (§575 extended): New message's typed handle to somebody who
    is not a friend is refused with the server's sentence, the card left
    open - the web follows the app's `go`."""
    start = WEB.index("function confirmNewChat(")
    body = WEB[start:start + 1600]
    refuse = body.index("if(notAFriend(found.user_id)){")
    said = body.index('toast("You can message people who follow you back.");', refuse)
    close = body.index("closeNewChatModal();", refuse)
    assert refuse < said < close
    assert body[said:close].count("return;") == 1
    ios = (SWIFT / "Views" / "MessagesView.swift").read_text()
    assert "if !chat.group && social.mayNotMessage(chat.with) {" in ios


def test_a_storys_handle_less_face_closes_the_story_before_saying_so():
    stories = (SWIFT / "Views" / "Stories.swift").read_text()
    start = stories.index("private func openChat() {")
    body = stories[start:start + 500]
    assert body.index("close()") < body.index('model.nowPlaying.say("You can message people who follow you back.")')


def test_new_message_says_friends_as_the_app_does():
    assert "Pick one or more friends</div>" in WEB
    assert "Pick one or more people you follow" not in WEB
    ios = (SWIFT / "Views" / "MessagesView.swift").read_text()
    assert 'Text("Pick one or more friends")' in ios
