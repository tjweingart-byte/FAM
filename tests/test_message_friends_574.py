"""§574: Message only between friends, on the web as in the app (§520).

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
    assert body.index("friendsData.friends.some(") < body.index("openThreadWith(person)")


def test_new_message_says_friends_as_the_app_does():
    assert "Pick one or more friends</div>" in WEB
    assert "Pick one or more people you follow" not in WEB
    ios = (SWIFT / "Views" / "MessagesView.swift").read_text()
    assert 'Text("Pick one or more friends")' in ios
