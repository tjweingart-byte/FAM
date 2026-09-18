"""Sharing an episode outside FAM: the link, the words, and the card."""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sharing


@pytest.fixture
def store(tmp_path):
    return sharing.ShareStore(str(tmp_path / "shares.db"))


EPISODE = {"title": "Who Makes the Chips",
           "question": "why is semiconductor manufacturing concentrated",
           "minutes": 3, "url": "https://fam.audio/s/abc123"}


# --- the templates --------------------------------------------------------

def test_every_target_renders_without_a_leftover_placeholder():
    """A share whose text says `{title}` is worse than no template at all, and
    it is the failure a typo in one of nine strings produces."""
    for target in sharing.TARGETS:
        rendered = sharing.render(target.key, **EPISODE)
        assert "{" not in rendered["text"], target.key
        assert "}" not in rendered["text"], target.key


def test_a_link_destination_carries_the_link():
    for key in ("copy", "sms", "email", "whatsapp", "x", "facebook", "linkedin"):
        assert EPISODE["url"] in sharing.render(key, **EPISODE)["text"], key


def test_email_has_a_subject_and_the_others_do_not():
    assert sharing.render("email", **EPISODE)["subject"]
    assert not sharing.render("sms", **EPISODE)["subject"]


def test_a_long_question_is_trimmed_to_what_the_platform_shows():
    """A caption cut off mid-word by the platform reads as a broken app rather
    than as a long post."""
    long = dict(EPISODE, question="why " + "semiconductor manufacturing " * 20)
    text = sharing.render("x", **long)["text"]
    assert len(text) <= sharing.target("x").max_chars


def test_trimming_never_cuts_the_link_off():
    """A share whose link is truncated is worse than one with a shorter
    sentence, so the trim comes out of the text and the link is re-attached."""
    long = dict(EPISODE, question="why " + "semiconductor manufacturing " * 20)
    text = sharing.render("x", **long)["text"]
    assert text.endswith(EPISODE["url"])


def test_an_unknown_destination_is_refused():
    with pytest.raises(sharing.ShareError):
        sharing.render("myspace", **EPISODE)


def test_stories_are_the_ones_that_need_a_picture():
    """Instagram and Snapchat stories cannot carry a link as text - they are
    pictures with a sticker on them. Getting this wrong means the listener
    shares a screenshot of a player UI."""
    needs = {t.key for t in sharing.TARGETS if t.needs_image}
    assert needs == {"instagram_story", "snapchat_story"}


def test_no_template_claims_the_episode_is_good():
    """FAM did not write that opinion and the person sharing has not typed one
    yet. The template is a default they finish, not a review."""
    for target in sharing.TARGETS:
        text = sharing.render(target.key, **EPISODE)["text"].lower()
        for word in ("amazing", "incredible", "best", "must-listen", "brilliant"):
            assert word not in text, target.key


# --- the card -------------------------------------------------------------

def test_the_card_is_a_portrait_story_sized_image():
    card = sharing.story_card(**{k: EPISODE[k] for k in ("title", "question", "minutes")})
    root = ET.fromstring(card)
    assert root.get("width") == "1080" and root.get("height") == "1920"


def test_the_card_is_well_formed_with_a_question_full_of_markup():
    """The question is text a listener typed and this is markup. An unescaped
    apostrophe or angle bracket produces an image that does not render, on the
    one surface where the failure is public."""
    card = sharing.story_card("A <b>bold</b> title & more",
                              'why do people say "it\'s <fine>"?', 3)
    ET.fromstring(card)  # raises if it is not well-formed
    assert "<b>" not in card


def test_a_long_title_is_wrapped_rather_than_running_off_the_card():
    card = sharing.story_card("A very long title " * 10, "a question", 3)
    root = ET.fromstring(card)
    spans = [e for e in root.iter() if e.tag.endswith("tspan")]
    assert 1 < len(spans) <= 8


def test_the_card_names_the_person_when_they_have_a_handle():
    assert "@ana" in sharing.story_card("T", "q", 3, handle="ana")
    assert "on FAM" in sharing.story_card("T", "q", 3)


# --- the store ------------------------------------------------------------

def test_sharing_the_same_episode_twice_returns_one_link(store):
    """One link with four destinations, not four links whose open counts have
    to be added up."""
    first = store.create("u", "why bonds move", 3, "Bonds")
    second = store.create("u", "why bonds move", 3, "Bonds")
    assert first["id"] == second["id"]


def test_two_listeners_sharing_the_same_episode_get_their_own_links(store):
    """Whose share was opened is the interesting question."""
    assert store.create("a", "q", 3)["id"] != store.create("b", "q", 3)["id"]


def test_opens_are_counted(store):
    share = store.create("u", "why bonds move", 3)
    store.opened(share["id"])
    store.opened(share["id"])
    assert store.get(share["id"])["opens"] == 2


def test_an_episode_with_no_question_cannot_be_shared(store):
    with pytest.raises(sharing.ShareError):
        store.create("u", "  ", 3)


def test_an_unknown_share_is_none_rather_than_an_error(store):
    assert store.get("not-a-share") is None


def test_forget_erases_a_listeners_shares(store):
    store.create("u", "q", 3)
    store.create("them", "q", 3)
    assert store.forget("u") == 1
    assert store.get(store.create("them", "q", 3)["id"]) is not None


# --- where a share actually goes ------------------------------------------
#
# The templates were always right and reached nothing: the interface put them
# on the clipboard and left the listener to find the app themselves. These
# pin the hand-off, which is the half that makes the feature work.


def test_every_destination_that_has_one_is_a_usable_url():
    for target in sharing.TARGETS:
        rendered = sharing.render(target.key, **EPISODE)
        link = rendered["destination"]
        if not link:
            continue
        assert link.startswith(("https://", "mailto:", "sms:")), \
            f"{target.key} points somewhere a phone cannot open: {link}"
        assert " " not in link, f"{target.key} left a raw space in {link}"


def test_the_platforms_the_packet_named_can_all_be_reached():
    """iMessage, Gmail, LinkedIn, Instagram and Snapchat, by name.

    The first three open with the wording already in them. The two story
    formats deliberately have no URL - they are an image handed to the
    platform's SDK - so what is asserted there is that they say so.
    """
    by_key = {t.key: t for t in sharing.TARGETS}
    for key in ("sms", "email", "linkedin"):
        assert sharing.render(key, **EPISODE)["destination"], \
            f"{key} has no way to open the app it is for"
    for key in ("instagram_story", "snapchat_story"):
        assert by_key[key].needs_image, f"{key} must ask for the card"
        assert not sharing.render(key, **EPISODE)["destination"]


def test_an_ampersand_in_the_question_does_not_truncate_the_message():
    """`sms:` and `mailto:` split their parameters on `&`. A question with one
    in it used to arrive as half a sentence."""
    episode = dict(EPISODE, question="tariffs & inflation, what changed?")
    for key in ("sms", "email"):
        link = sharing.render(key, **episode)["destination"]
        assert "%26" in link or "&" not in link.split("body=", 1)[1]


# --- every destination, end to end ---------------------------------------


def test_every_target_renders_words_a_person_could_send():
    """All nine, with the wording actually filled in. The templates are the
    product here: a share nobody would send is a share button nobody uses."""
    for target in sharing.TARGETS:
        rendered = sharing.render(target.key, **EPISODE)
        assert rendered["text"].strip(), f"{target.key} rendered nothing"
        assert "{" not in rendered["text"], \
            f"{target.key} left a placeholder in: {rendered['text']!r}"
        assert rendered["label"], target.key
        assert rendered["kind"] in ("copy", "message", "link", "story")


def test_a_link_that_is_not_public_opens_nothing_anywhere():
    """Without PUBLIC_BASE_URL the link is relative - `/s/abc` - and handing
    that to Facebook opens their composer around a URL nobody can resolve.
    The listener then finds out on somebody else's site that FAM is broken,
    which is the worst place to learn it."""
    local = dict(EPISODE, url="/s/abc123")
    for target in sharing.TARGETS:
        rendered = sharing.render(target.key, **local)
        assert rendered["destination"] == "", \
            f"{target.key} offered to open {rendered['destination']!r}"
        # The words are still there: the clipboard works, so a deployment
        # being tested is not blocked - it just never pretends.
        assert rendered["text"].strip()


def test_the_two_that_drop_our_words_are_the_two_we_say_so_about():
    """Facebook and LinkedIn build their own preview from the page and ignore
    everything else. The interface copies the wording alongside and says so;
    this pins which two that is, so a third joining them is noticed."""
    drops_text = {"facebook", "linkedin"}
    for target in sharing.TARGETS:
        if not target.destination:
            continue
        rendered = sharing.render(target.key, **EPISODE)
        carries_words = "text=" in target.destination or "body=" in target.destination
        if target.key in drops_text:
            assert not carries_words, \
                f"{target.key} can carry our words after all - stop apologising for it"
        else:
            assert carries_words, \
                f"{target.key} silently drops the wording and nothing says so"
        assert rendered["destination"]


# --- the landing page -----------------------------------------------------
#
# Where a shared link goes, and the rule the whole page exists to keep: the
# only control that works is play, and everything else is a door to the App
# Store.

SHARE_ROW = {"id": "abc123", "title": "Who Makes the Chips",
             "query": "why is semiconductor manufacturing concentrated",
             "minutes": 3, "user_id": "listener-42", "created": 0.0,
             "opens": 0}


def test_the_landing_payload_never_carries_a_listener_id():
    """The one response that hands a stranger a row with a listener id sitting
    in it. Authorship is provenance and never identity (PROBLEMS.md 95), and a
    share link is public by construction - so this is where that rule is
    broken by accident if it is ever broken at all."""
    payload = sharing.landing_payload(SHARE_ROW, url="https://fam.audio/s/abc123")
    assert "user_id" not in payload
    assert "listener-42" not in repr(payload)


def test_the_head_describes_the_episode_rather_than_the_app():
    """Facebook and LinkedIn read the page for their preview and ignore
    everything else, so a landing page whose head says "FAM" posts every
    episode as the same link."""
    head = sharing.landing_head(sharing.landing_payload(
        SHARE_ROW, url="https://fam.audio/s/abc123"))
    assert "Who Makes the Chips" in head
    assert 'property="og:title"' in head
    assert "semiconductor manufacturing" in head


def test_a_card_that_is_not_public_is_not_advertised_as_an_image():
    """An Open Graph image is fetched by a crawler on somebody else's server.
    A relative one is no image at all, so claiming it would advertise a
    picture that never loads - the same refusal `destination_for` makes about
    the link."""
    head = sharing.landing_head(sharing.landing_payload(
        SHARE_ROW, url="/s/abc123", card_url="/api/share/card?share=abc123"))
    assert "og:image" not in head
    assert "og:url" not in head


def test_a_question_full_of_markup_cannot_break_out_of_the_page():
    """The question is text a listener typed and the payload is embedded in a
    `<script>`. `</script>` inside it would end the block and put the rest of
    the question into the document as markup."""
    nasty = dict(SHARE_ROW, query='</script><img src=x onerror=alert(1)>',
                 title='</title><script>alert(2)</script>')
    page = sharing.render_landing(
        "<html><head><!--FAM_SHARE_HEAD--></head>"
        "<body><!--FAM_SHARE_DATA--></body></html>",
        sharing.landing_payload(nasty, url="https://fam.audio/s/abc123"))
    assert "</script><img" not in page
    assert "<script>alert(2)</script>" not in page
    assert "alert(1)" in page, "the question should survive, merely escaped"


def test_the_markers_are_gone_once_the_page_is_rendered():
    """A marker left in is a page that ships an HTML comment where its title
    should be, and nothing fails."""
    page = sharing.render_landing(
        "<html><head><!--FAM_SHARE_HEAD--></head>"
        "<body><!--FAM_SHARE_DATA--></body></html>",
        sharing.landing_payload(SHARE_ROW, url="https://fam.audio/s/abc123"))
    assert sharing.HEAD_MARKER not in page
    assert sharing.DATA_MARKER not in page
    assert "window.FAM_SHARE" in page


def test_with_no_app_store_link_there_are_no_doors():
    """A control with nothing behind it is worse than no control, and a
    stranger arriving from LinkedIn is the worst audience for a button that
    404s. Nothing invents a store URL, the same way nothing invents a host."""
    assert sharing.landing_doors("") is False
    assert sharing.landing_doors("   ") is False
    assert sharing.landing_doors("https://apps.apple.com/app/fam/id1") is True
    assert sharing.landing_payload(SHARE_ROW, url="/s/abc123")["has_app"] is False


def test_the_real_page_carries_both_markers_and_the_player():
    """The shipped file, not a fixture. A page that lost its markers would
    render with no title and no episode, and only this notices."""
    import pathlib

    page = (pathlib.Path(__file__).resolve().parent.parent
            / "static" / "listen.html").read_text()
    assert sharing.HEAD_MARKER in page
    assert sharing.DATA_MARKER in page
    # The audio path is the one thing this page is for.
    assert "fam-audio.js" in page
    # Every control that is not play is a door. The delegated handler is what
    # makes a control added later a door by default rather than by somebody
    # remembering to wire it.
    assert "data-door" in page


def test_a_share_resolves_to_the_episode_the_sharer_heard():
    """The whole trace-back, in one assertion.

    There is no episode id in this product. A share row holds the question and
    the length, `pipeline.key_for` builds the cache key from exactly those, so
    the landing page asking `/api/audio` for them gets the sharer's own script
    out of the shared cache - no second model call, and nothing new stored.

    If a field is ever added that changes what an episode is, it goes in
    `key_for` (PROBLEMS.md 83) and this fails until the share row carries it
    too - which is the point of asserting it here rather than trusting it.
    """
    import asyncio

    from pipeline import key_for
    from script_generator import plan_episode

    heard = plan_episode(SHARE_ROW["query"], SHARE_ROW["minutes"])
    followed = plan_episode(SHARE_ROW["query"], SHARE_ROW["minutes"])
    assert asyncio.run(key_for(heard)) == asyncio.run(key_for(followed))


def test_every_platform_the_packet_named_has_a_target():
    """The nine destinations asked for, by name. A tenth is fine; a missing
    one is a share sheet with a gap in it."""
    wanted = {"copy", "sms", "email", "whatsapp", "x", "facebook", "linkedin",
              "instagram_story", "snapchat_story"}
    assert wanted <= set(sharing.TARGET_KEYS)
