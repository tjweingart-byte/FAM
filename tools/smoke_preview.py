"""Drive the built preview in a real browser, with no server at all.

This is the check that catches what pytest cannot: a tab that renders nothing,
a feed that throws, a reel that will not advance, audio that never starts. All
three Explore bugs were found this way by hand; this runs it every push.

    python tools/smoke_preview.py [path-to-preview.html]
"""
from __future__ import annotations

import os
import re
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "preview" / "fam-preview.html"

sys.path.insert(0, str(ROOT))
import topics as topics_mod  # noqa: E402  - the real bank, not a fixture copy


def main() -> int:
    from playwright.sync_api import sync_playwright

    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not target.exists():
        print(f"no preview at {target} - run python preview/build_preview.py", file=sys.stderr)
        return 1

    def launch_browser(pw):
        """Playwright's own download first; any installed Chromium after.

        Environments that ship a browser at a different version than the
        Playwright package expects are common enough that failing there would
        make this check something people skip.
        """
        candidates = [os.environ.get("PLAYWRIGHT_CHROMIUM")]
        try:
            return pw.chromium.launch()
        except Exception as first:
            for pattern in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome",
                            "/opt/pw-browsers/chromium/chrome-linux/chrome"):
                candidates += sorted(str(p) for p in pathlib.Path("/").glob(pattern.lstrip("/")))
            for path in [c for c in candidates if c and pathlib.Path(c).exists()]:
                try:
                    return pw.chromium.launch(executable_path=path)
                except Exception:
                    continue
            raise first

    failures: list[str] = []
    with sync_playwright() as pw:
        browser = launch_browser(pw)
        page = browser.new_page(viewport={"width": 430, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("file://" + str(target.resolve()))
        page.wait_for_timeout(2500)
        page.evaluate("var s=document.getElementById('splash'); if(s)s.classList.add('hide');")

        def check(label: str, fn):
            try:
                fn()
                print(f"  ok    {label}")
            except Exception as exc:  # noqa: BLE001 - report, don't stop
                failures.append(f"{label}: {exc}")
                print(f"  FAIL  {label}: {exc}")
                # Leave the page clickable for whatever runs next.
                #
                # A behaviour that fails partway through can leave a modal
                # open, and an overlay swallows every click after it - so one
                # real failure was reported as six, five of which were the
                # harness tripping over its own wreckage. A report whose
                # failures have that blast radius is one nobody can read.
                #
                # **Only after a failure.** Doing it after every behaviour
                # dismissed state a passing one had deliberately left standing:
                # the weekly recap pops by itself on first run, and clearing it
                # made the very next check unable to find it. Tidying up after
                # a success is not tidying up, it is interference.
                try:
                    page.evaluate(
                        "document.querySelectorAll('.modal-overlay.active,"
                        " .sheet-overlay.active').forEach("
                        "  function(el){ el.classList.remove('active'); })")
                except Exception:  # noqa: BLE001 - the page may be gone
                    pass

        def first_run_asks_before_it_shows_the_app():
            """The entry flow runs once, and every path through it lands in
            the app. A first-run screen with no way out is the worst bug this
            file could miss, because it is the only screen everybody sees."""
            assert page.eval_on_selector(".screen.active", "e => e.id") == "screen-welcome", \
                "a first open did not start on the welcome screen"
            assert page.query_selector("#screen-welcome .entry-skip"), \
                "there was no way past the account step"
            # Signed up rather than skipped, because the gated surfaces below
            # (mixes, the recap) are the ones with something to check, and
            # skipping is asserted above as reachable.
            page.evaluate("openAuth('signup')")
            page.wait_for_timeout(400)
            page.evaluate("showAuthForm()")
            page.fill("#authEmail", "smoke@example.com")
            page.fill("#authPassword", "a-long-enough-password")
            # The number is asked for and kept on the same account as the
            # address. Typed as digits, because what the field shows is the
            # national formatting and what is sent is E.164.
            page.fill("#authPhone", "4155550142")
            assert page.input_value("#authPhone") == "(415) 555-0142", \
                "the phone field did not format what was typed into it"
            assert page.eval_on_selector("#authPhoneCC", "e => e.value") == "+1", \
                "the country code did not default to +1"
            page.evaluate("submitAuthForm()")

            # Who they are, before what they want to hear. A name, a handle
            # and a picture are about *them* and take a moment; the interests
            # are the last thing before the app, so asking for them first
            # would put a form between somebody and the episode they came for.
            page.wait_for_selector("#screen-identity.active", timeout=10000)
            assert page.query_selector("#identityPic"), \
                "no way to add a picture in the setup"
            assert page.query_selector("#identitySkip:not([hidden])"), \
                "there was no way past the identity step"
            # And the editor-only half is not in the first run: there is no
            # password to change yet and nothing chosen to share.
            assert page.eval_on_selector("#identityEditOnly", "e => e.hidden"), \
                "the editor's own rows are in the first run"
            # Wait out the screen's own autofocus before typing. `openIdentity`
            # focuses the name field on an 80ms timer, and Playwright's fill
            # types into whatever holds focus - so a fill that straddles the
            # timer puts the handle into the name box and this check fails with
            # an empty field. It failed exactly once, during a run with three
            # browsers going, which is what a race under load looks like.
            # Waiting for the focus to have landed removes it without weakening
            # anything below.
            page.wait_for_function(
                "document.activeElement"
                " && document.activeElement.id === 'identityName'",
                timeout=5000)
            page.fill("#identityName", "Smoke Tester")
            page.fill("#identityHandle", "@Smoke.Tester")
            # Stored lower-case and stripped, so it is shown that way while
            # it is being typed rather than corrected on save.
            assert page.input_value("#identityHandle") == "smoketester", \
                "the handle field did not clean what was typed into it"
            page.evaluate("saveIdentity()")

            page.wait_for_selector("#screen-intro.active .intro-chip",
                                   timeout=10000, state="attached")
            # Six discs on the wheel, as the designs draw them.
            chips = page.eval_on_selector_all(".intro-chip", "e => e.length")
            assert chips == 6, f"the wheel offered {chips} interests, not six"
            # And no paragraph between the heading and them.
            assert not page.query_selector("#introPageInterests .entry-lede"), \
                "the lede is back under Your interests"
            # Every one of them selectable, with nothing counting them and
            # nothing refusing the last one. There is no cap any more, so
            # there must be no trace of one either.
            for i in range(6):
                page.evaluate(f"var c=document.querySelectorAll('.intro-chip')[{i}];"
                              " if(c) c.click();")
            chosen = page.eval_on_selector_all(".intro-chip.on", "e => e.length")
            assert chosen == 6, f"only {chosen} of six discs took a tap"
            assert not page.query_selector("#introCount"), \
                "the interest counter is back, and there is no number to show"
            body = page.text_content("#introPageInterests") or ""
            for word in ("limit", "up to six", "at most"):
                assert word not in body.lower(), f"the page still mentions a cap: {word!r}"
            # Interests is the last step now - the language page is gone
            # (§100), so this button finishes the run rather than chaining on
            # to a question that was never wired to anything.
            assert page.eval_on_selector("#introNextBtn", "e => e.textContent.trim()") \
                == "Start listening", "the first run still has a second step"
            page.evaluate("finishIntro()")
            page.wait_for_timeout(900)
            assert page.eval_on_selector(".screen.active", "e => e.id") == "screen-myfam", \
                "finishing the intro did not land in the app"

        def no_weekly_recap_pops_up():
            """The weekly recap popup is gone at the owner's direction, and
            its shelf is myFAM's "What you missed last week" rail.

            Checked as an absence because the failure it guards is the popup
            coming back: a recap that fires on the first open of a new week is
            an interruption in front of an app somebody opened to listen to
            something, and a rail is not.

            The follower popup is deliberately *not* in this net. "___ started
            following you" is an interruption somebody else caused and the
            listener wants; a summary of their own week is neither."""
            page.wait_for_timeout(1200)
            assert not page.query_selector("#recapOverlay"), \
                "the weekly recap popup came back"
            overlays = page.eval_on_selector_all(
                ".modal-overlay.active", "e => e.map(x => x.id)")
            unexpected = [o for o in overlays if o != "followerOverlay"]
            assert unexpected == [], f"something popped up unasked: {unexpected}"
            # And clear whatever is up, so the next behaviour is clickable.
            page.evaluate("closeFollowerPopup()")
            page.wait_for_timeout(300)

        def myfam():
            """One rail per signal, and the count comes from the code.

            Hard-coding it meant the number had to be edited by hand whenever a
            section moved, which is exactly when nobody remembers to. Explore
            New is checked by name because it is the one that has been removed
            from this page once already.
            """
            page.evaluate("openMyFamTab()")
            page.wait_for_selector(".feed-rail .seed-card", timeout=10000, state="attached")
            rails = page.eval_on_selector_all(".feed-section", "e => e.length")
            wanted = len(topics_mod.SECTIONS)
            assert rails == wanted, f"expected {wanted} sections, saw {rails}"
            titles = page.eval_on_selector_all(".feed-title", "e => e.map(x => x.textContent)")
            # Trending is second, in the slot Explore New used to hold. It was
            # last, where a row nobody scrolls to is a row nobody reads, and it
            # is the one rail here with a reason to be looked at *today*.
            assert any("Trending" in t for t in titles), \
                f"Trending is not on myFAM: {titles}"
            assert not any("Explore New" in t for t in titles), \
                f"Explore New is back on myFAM: {titles}"
            assert titles[1].strip() == "Trending", \
                f"Trending is not in the second slot: {titles}"
            # The order and the wording the personalisation packet asks for,
            # read off the page rather than off `topics.SECTIONS` - the point
            # of checking it here is that the interface has its own copy of
            # these strings (`SECTION_TITLE`) and the two have drifted before.
            assert [t.strip().replace("\n", " ") for t in titles[1:]] == [
                "Trending",
                "What you missed last week",
                "What FAM can't stop listening to",
                "What your friends are listening to",
            ], f"the rails are not the ones the packet asks for: {titles}"
            # Every rail now opens its own full-length view from the card at
            # the end of it. Without a visible way through nobody finds those
            # screens, which is how Explore New became unreachable the first
            # time it came off this page.
            assert page.eval_on_selector_all(".feed-more", "e => e.length") >= 1, \
                "no rail offers a way through to its full surface"

        def live_story_tiles_show_their_angle():
            """A live tile says what it is about, and now so does a bank one.

            The angle is the whole of what the story pool buys a listener -
            "two cables in the Red Sea were reported damaged this week" rather
            than "because of what you have played" - and it is one `if` in
            `seedHook` away from never being drawn.

            The second half of that sentence used to be the bank's behaviour
            and is now nobody's: a card's second line is the *episode's* hook
            on every tile, from the story's angle or the bank's own, and
            `no_card_describes_the_feed_instead_of_the_episode` below is what
            holds the rail reasons out of it.

            **The two preview builds are honestly in different states here**,
            and the check reads which rather than assuming one. The fixture
            build ships templated story tiles - what a deployment with no API
            key serves, so this tests the floor of the feature and not its best
            case. The live build runs on a database in the browser with no
            server behind it, so there is no pool at all and there are no
            angles to draw; the assertion there is that the page says so,
            because an empty rail with no explanation is the failure this rule
            exists to catch. Neither branch is a skip.
            """
            page.evaluate("openMyFamTab()")
            page.wait_for_selector(".feed-rail .seed-card", timeout=10000,
                                   state="attached")
            drawn = page.evaluate(
                """() => Array.prototype.map.call(
                     document.querySelectorAll("#myfamFeed .seed-card"),
                     function(card){
                       var why = card.querySelector(".seed-why");
                       return (why ? why.textContent.trim() : "");
                     })""")
            assert drawn, "no card said anything about itself"
            expected = page.evaluate(
                """() => Object.keys(myFamTopics)
                          .map(function(k){ return myFamTopics[k].angle || ""; })
                          .filter(function(a){ return a; })""")
            if expected:
                missing = [a for a in expected if a not in drawn]
                assert not missing, (
                    "a live story's angle never reached its card - seedHook "
                    f"stopped reading it: {missing[:2]}")
                return
            # No pool on this build. Then the rail that is *only* the pool has
            # to be empty and has to say why - never "nothing is trending".
            empties = page.eval_on_selector_all(
                "#myfamFeed .feed-empty", "e => e.map(x => x.textContent.trim())")
            assert empties, (
                "this build has no live stories and no rail says so - either "
                "the pool reached the page without angles, or an empty rail "
                "is being drawn silently")
            assert not any("nothing is trending" in e.lower()
                           or "nothing is happening" in e.lower()
                           for e in empties), (
                f"an empty rail made a claim about the world: {empties}")

        def no_card_describes_the_feed_instead_of_the_episode():
            """The line under a tile's title is about the episode.

            It used to be about the *rail* - "Because of what you have
            played", "Playing across FAM now" - which answers "why is this
            card here" when the listener is asking "is this worth three
            minutes". Read off the rendered cards rather than off `seedHook`,
            because §111's lesson is that a control can be on screen and
            have quietly stopped doing anything.
            """
            page.evaluate("openMyFamTab()")
            page.wait_for_selector(".feed-rail .seed-card", timeout=10000,
                                   state="attached")
            lines = page.eval_on_selector_all(
                "#myfamFeed .seed-card .seed-why",
                "e => e.map(x => x.textContent.trim())")
            assert lines, "no card had a second line at all"
            assert all(lines), f"a card's second line was blank: {lines}"
            for reason in ("Because of what you have played",
                           "Playing across FAM now"):
                assert reason not in lines, (
                    f"a card still describes the feed: {reason!r}")
            # And each line is the tile's own hook, not a constant.
            hooks = page.evaluate(
                """() => Object.keys(myFamTopics).map(function(k){
                     var t = myFamTopics[k];
                     return t.angle || t.subtitle || "";
                   }).filter(function(h){ return h; })""")
            assert hooks, "no tile carried a hook to draw"
            drawn = [h for h in hooks if h in lines]
            assert drawn, f"no tile's own hook reached a card: {hooks[:2]}"

        def a_cold_start_rail_does_not_claim_to_be_personal():
            """"Made for you" is a claim, and a stranger has not earned it.

            A fresh viewer of this page has no account, no chosen interests
            and nothing played, so the first rail is ranked from what FAM's
            listeners play - real, and not theirs. The tiles stay; the
            heading stops claiming they were chosen for somebody the app has
            never met. Checked against whichever state this build is in,
            because the fixture build ships a feed and the live build starts
            empty - neither branch is a skip.
            """
            page.evaluate("openMyFamTab()")
            page.wait_for_selector(".feed-rail .seed-card", timeout=10000,
                                   state="attached")
            source = page.evaluate("() => myfamTasteSource")
            heading = page.eval_on_selector(
                "#myfamFeed .feed-section .feed-title", "e => e.textContent")
            if source == "startup":
                assert "Start here" in heading, heading
                assert "Made for you" not in heading, heading
                # The point of the exercise: it is filled, not explained away.
                cards = page.eval_on_selector_all(
                    "#myfamFeed .feed-section:first-child .seed-card"
                    " .seed-card-title", "e => e.length")
                assert cards >= 4, f"a cold start's first rail had {cards} tiles"
            else:
                assert "Made for you" in heading, heading

        def go_deeper_titles_fit():
            """A clipped title is invisible to every other check.

            The tiles are a fixed height, so the only thing that tells you a
            headline is being cut mid-word is looking at a phone - which is
            how it shipped once. Every title the bank can produce is measured
            here, plus a thread at the longest the prompt asks for.
            """
            page.evaluate("openMyFamTab()")
            page.wait_for_selector(".gd-card-title", timeout=10000, state="attached")
            titles = [t.title for t in topics_mod.TOPIC_BANK]
            titles.append(
                # `<<NEXT: six to twelve words>>` - a thread card shows this raw.
                "What happens to the grid operators when the subsidy expires next year"
            )
            # Text metrics depend on the browser build and the fonts actually
            # installed, so this check can pass on one machine and fail on
            # another with the same markup. When it fails it therefore has to
            # say *what it measured*, not only which titles lost - otherwise
            # the reader cannot tell a real clipped headline from a machine
            # rendering in a different font, and cannot reproduce either.
            measured = page.evaluate(
                """(xs) => {
                    var el = document.querySelector(".gd-card-title");
                    var original = el.textContent;
                    var bad = xs.filter(function(x){
                        el.textContent = x;
                        return el.scrollHeight > el.clientHeight + 1;
                    });
                    var worst = null;
                    bad.forEach(function(x){
                        el.textContent = x;
                        if(!worst || el.scrollHeight > worst.scrollHeight){
                            worst = {text: x, scrollHeight: el.scrollHeight,
                                     clientHeight: el.clientHeight};
                        }
                    });
                    el.textContent = original;
                    var style = getComputedStyle(el);
                    return {bad: bad, worst: worst,
                            font: style.fontFamily, size: style.fontSize,
                            lineHeight: style.lineHeight,
                            fonts: document.fonts ? document.fonts.status : "n/a"};
                }""",
                titles,
            )
            assert not measured["bad"], (
                f"Go Deeper tile cuts these titles off: {measured['bad']}\n"
                f"  worst: {measured['worst']}\n"
                f"  rendered with: {measured['font']} at {measured['size']}"
                f"/{measured['lineHeight']} (webfonts: {measured['fonts']})"
            )

        def go_deeper_waits_for_a_new_listener():
            """Nothing to pick up until the listener has done something (§127).

            It used to top itself up from the bank so a first run met four
            tiles under "Pick up where you left off" - a section about coming
            back to something, with nothing to come back to. Now a listener
            with no part-heard episodes, threads or plays sees no section at
            all, and the episode-length control lives in the fixed header
            instead of that section's heading.
            """
            page.evaluate(
                """() => {
                    var real = window.fetch;
                    window.fetch = function(u, o){
                        if(String(u).indexOf("/api/godeeper") === 0){
                            return Promise.resolve({ ok: true,
                                json: function(){ return Promise.resolve(
                                    { threads: [], resume: [], similar: [] }); } });
                        }
                        return real(u, o);
                    };
                }"""
            )
            page.evaluate("openMyFamTab(); loadMyFamFeed()")
            page.wait_for_timeout(1800)
            cards = page.evaluate("() => goDeeperCardCache")
            assert cards == [], f"a new listener was offered {len(cards)} Go Deeper tiles"
            assert not page.query_selector("#goDeeperBlock .gd-kicker"), \
                "an empty Pick up where you left off section still drew its heading"
            # The length control is in the fixed header now, and still
            # independent of search's. Changing it here must not move the
            # search player's.
            before = page.eval_on_selector("#lengthVal", "e => e.textContent")
            assert page.query_selector("#screen-myfam .myfam-header .gd-len"), \
                "myFAM's episode-length control is not in the header"
            page.evaluate("openMyFamLengthMenu()")
            page.wait_for_timeout(250)
            page.evaluate(
                """() => {
                    var rows = document.querySelectorAll('.sheet-item');
                    for (var i = 0; i < rows.length; i++) {
                        if (rows[i].textContent.indexOf('7 min') === 0) {
                            rows[i].click(); return;
                        }
                    }
                }""")
            page.wait_for_timeout(350)
            assert "7 min" in page.text_content("#screen-myfam .gd-len"), \
                "myFAM's length control did not take"
            assert page.eval_on_selector("#lengthVal", "e => e.textContent") == before, \
                "changing myFAM's length also changed the search player's"
            page.reload()
            page.wait_for_timeout(1200)

        def myfam_header_stays_put():
            """The wordmark, messages and the length control do not scroll
            with the rails (§127): they sit outside the scroller."""
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(1200)
            inside = page.evaluate(
                "() => !!document.querySelector('#screen-myfam .scroll .myfam-header')")
            assert not inside, "myFAM's header is inside the scrolling area"
            top_before = page.eval_on_selector(
                "#screen-myfam .myfam-header", "e => e.getBoundingClientRect().top")
            page.evaluate("document.querySelector('#screen-myfam .scroll').scrollTop = 600")
            page.wait_for_timeout(150)
            top_after = page.eval_on_selector(
                "#screen-myfam .myfam-header", "e => e.getBoundingClientRect().top")
            page.evaluate("document.querySelector('#screen-myfam .scroll').scrollTop = 0")
            assert abs(top_after - top_before) < 1, \
                f"the header moved from {top_before} to {top_after} on scroll"

        def the_loading_screen_can_be_cancelled():
            """A mis-tap used to hold the whole app behind the loading screen
            until audio started (§127). The X takes it down and goes back."""
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(500)
            page.evaluate(
                """() => {
                    var real = window.fetch;
                    window.__famRealFetch = real;
                    window.fetch = function(u, o){
                        if(String(u).indexOf("/api/audio") === 0){
                            return new Promise(function(){});   // never answers
                        }
                        return real(u, o);
                    };
                }""")
            page.evaluate("""() => {
                TOPICS['cancel_probe'] = { title: 'Cancel probe', source: 'myFAM',
                    caption: '', prompt: 'why do bonds move', titleOverridden: true };
                generate('cancel_probe');
            }""")
            page.wait_for_timeout(300)
            assert page.eval_on_selector("#famLoading", "e => e.classList.contains('active')"), \
                "the loading screen did not show"
            page.click("#famLoadingCancel")
            page.wait_for_timeout(300)
            assert not page.eval_on_selector(
                "#famLoading", "e => e.classList.contains('active')"), \
                "the X did not take the loading screen down"
            assert not page.eval_on_selector(
                "#screen-player", "e => e.classList.contains('active')"), \
                "cancelling left the listener on the player"
            page.evaluate("() => { window.fetch = window.__famRealFetch; }")

        def attachments():
            """A file becomes a chip, and the chip becomes an id on the request.

            Also pins the two rules the feature exists under: an attachment on
            its own is a summarise request rather than an error, and it is
            cleared once used so it cannot ride along on the next question.
            """
            page.evaluate("setTab('home')")
            page.wait_for_timeout(300)
            assert page.query_selector(".attach-btn"), "no way to attach anything"
            page.set_input_files("#attachFile", {
                "name": "q3-report.txt", "mimeType": "text/plain",
                "buffer": b"Revenue fell 12 percent.",
            })
            page.wait_for_timeout(800)
            chips = page.eval_on_selector_all(".attach-chip", "e => e.length")
            assert chips == 1, f"expected one chip, saw {chips}"
            name = page.text_content(".attach-chip .nm")
            assert "q3-report" in name, f"the chip does not name the file: {name!r}"
            assert page.evaluate("() => attachedIds()"), "the chip carries no id"

            # Nothing typed: the attachment itself is the request.
            page.evaluate("runSearch()")
            page.wait_for_timeout(600)
            asked = page.evaluate("() => TOPICS['_custom'] && TOPICS['_custom'].prompt")
            assert asked and "attached" in asked.lower(), \
                f"an attachment alone did not become a request: {asked!r}"
            carried = page.evaluate("() => TOPICS['_custom'].attach")
            assert carried, "the episode was generated without the attachment"
            assert page.eval_on_selector_all(".attach-chip", "e => e.length") == 0, \
                "the attachment stayed on screen and would ride along on the next search"
            page.reload()
            page.wait_for_timeout(1200)

        def your_fam_is_messages_and_only_messages():
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(500)
            page.click("#screen-myfam .myfam-msg-btn")
            page.wait_for_timeout(600)
            # The two tiles that sat above the threads both came off at the
            # owner's direction: the weekly recap is gone entirely, and Save
            # for Later is on the profile, where a listener's shelves live.
            assert not page.query_selector(".yf-tile"), \
                "the Your FAM tiles came back"
            assert not page.query_selector("#recapOverlay"), \
                "the weekly recap popup came back"
            # Messages is its own screen now, titled for what it is - the
            # YourFAM mark moved to the hub the tab opens on.
            title = page.query_selector("#screen-messages .yf-title")
            assert title and title.text_content().strip() == "Messages", \
                "the Messages screen is not titled Messages"
            ensure_account()
            page.evaluate("openProfile()")
            page.wait_for_selector("#screen-profile.active .yf-head .wordmark",
                                   timeout=10000)
            # "Your" in type, "FAM" as the mark - the same three glyphs
            # myFAM, DailyFAM and exploreFAM all set.
            glyphs = page.eval_on_selector_all(
                "#screen-profile .yf-head .wordmark .wm-glyph,"
                " #screen-profile .yf-head .wordmark .wm-a",
                "e => e.length")
            assert glyphs == 3, f"the FAM mark drew {glyphs} glyphs, not three"
            assert page.text_content("#screen-profile .yf-head .wordmark").strip() == "Your", \
                "the heading still spells FAM out in type"
            # And what came off the hub on purpose stays off it.
            assert not page.query_selector("#screen-profile .pf-time"), \
                "the This month card came back"
            assert not page.query_selector("#screen-profile .thread-row"), \
                "the conversation list came back onto the hub"
            # Explore New is off myFAM at the owner's direction, but the
            # ranking, the endpoint and the screen are all still here - which
            # is what makes putting the rail back a one-line change rather
            # than a rebuild. Driven directly, because nothing links to it.
            page.evaluate("openExploreNew()")
            page.wait_for_selector("#screen-explorenew.active .xn-card",
                                   timeout=10000, state="attached")
            assert page.eval_on_selector_all(".xn-card", "e => e.length") >= 4
            assert page.text_content("#xnReason").strip(), \
                "Explore New did not say why it was showing these"
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def save_for_later_lists_the_shelf():
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)
            page.evaluate("openSavedAll()")
            page.wait_for_selector("#screen-saved.active .sv-row",
                                   timeout=10000, state="attached")
            rows = page.eval_on_selector_all(".sv-row", "e => e.length")
            assert rows >= 2, f"the shelf showed {rows} episodes"
            # One list of pointers. Downloads used to be a second view of this
            # shelf with a switch between them, and the folder chips a row
            # above that; both are gone with their features.
            assert not page.query_selector("#svSwitch"), \
                "the Downloads switch came back"
            assert not page.query_selector("#svDownloadBar"), \
                "the offline-capacity bar came back"
            assert not page.query_selector(".sv-chip"), \
                "the folder chips came back"
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def the_shelf_comes_back_to_where_it_was_opened_from():
            """Opening it from the profile and pressing back landed on search,
            because `openSaved` unwound the Your FAM sheet whether or not that
            sheet was open - and a `goBack()` on the profile takes the profile
            off the stack."""
            # The Profile tab is a door without an account (§114), so this is
            # not a check that can be run as a guest - the shelf is behind it.
            ensure_account()
            page.evaluate("openProfile()")
            page.wait_for_selector("#screen-profile.active .yf-btn.cream",
                                   timeout=10000, state="attached")
            # Clicked in the page rather than by the mouse: the new-follower
            # popup can be over the hub, and what is under test is where the
            # button goes, not the popup.
            page.evaluate("document.querySelector('#screen-profile .yf-btn.cream').click()")
            page.wait_for_timeout(700)
            assert page.eval_on_selector(".screen.active", "e => e.id") == "screen-saved"
            page.evaluate("goBack()")
            page.wait_for_timeout(500)
            assert page.eval_on_selector(".screen.active", "e => e.id") == "screen-profile", \
                "back from the shelf did not return to the profile"
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def saving_is_a_toggle_on_every_player():
            """Press save, the icon goes green, press it again and it does
            not. Saving used to raise a popup asking whether to download the
            audio to the device as well, which made the one-tap action in the
            player a two-tap action with a decision in the middle.

            Checked across every save control at once, the way VIBE! is: the
            main player went without a vibe button for a while because the
            function that drew that state listed ids, and a save control on a
            fifth player would hit exactly that wall."""
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)
            page.evaluate("showScreen('player')")
            page.evaluate("nowBarState = {query: 'why bonds move',"
                          " title: 'Bonds', minutes: 2}")

            controls = page.eval_on_selector_all("[data-save]", "e => e.length")
            assert controls >= 3, f"only {controls} save controls carry data-save"

            page.evaluate("saveForLater()")
            page.wait_for_timeout(800)
            assert not page.query_selector("#downloadOverlay"), \
                "the download popup came back"
            lit = page.eval_on_selector_all(
                "[data-save]",
                "e => e.filter(x => x.classList.contains('saved-on')).length")
            assert lit == controls, \
                f"saving lit {lit} of {controls} save controls"
            word = page.eval_on_selector("#playerSave .save-cap",
                                         "e => e.textContent").strip().lower()
            assert word == "saved", f"the save control still says {word!r}"

            page.evaluate("saveForLater()")
            page.wait_for_timeout(800)
            still = page.eval_on_selector_all(
                "[data-save]",
                "e => e.filter(x => x.classList.contains('saved-on')).length")
            assert still == 0, f"{still} save controls stayed lit after unsaving"
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def live_captions_show_the_script_being_read():
            """The captions tab did nothing. It showed `t.caption` - a line of
            prototype copy ending in an em dash - so turning captions on
            produced a sentence about captions, and nothing was ever wired to
            the episode.

            The sentences come from `/api/transcript`, which reads the cache
            the script is already stored under and never generates: captions
            that could trigger a write would be a second full Claude call for
            every episode somebody chose to read along with."""
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)
            page.evaluate("showScreen('player')")
            page.evaluate("rememberEpisode('why bonds move', 2, '')")
            page.evaluate("resetCaptions()")

            before = page.text_content("#cc-text")
            assert "turned on" in before.lower(), \
                f"the panel said something other than 'off': {before!r}"

            page.evaluate("toggleCC()")
            page.wait_for_timeout(1500)
            assert page.eval_on_selector("#ccPanel", "e => e.classList.contains('on')"), \
                "the captions panel did not open"
            text = page.text_content("#cc-text")
            assert "\u2014" not in text or len(text) > 80, \
                f"the panel is still showing the prototype line: {text!r}"
            assert len(text.strip()) > 40, f"the panel stayed empty: {text!r}"
            # One sentence marked as the one being spoken. Without a highlight
            # it is a transcript, not captions.
            assert page.query_selector("#cc-text mark"), \
                "no sentence was marked as the one being read"

            page.evaluate("toggleCC()")
            page.wait_for_timeout(400)
            assert not page.eval_on_selector(
                "#ccPanel", "e => e.classList.contains('on')"), \
                "the captions panel did not close"
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def the_sources_cluster_shows_three_in_the_corner():
            """It was fetched when an episode *ended* and when Go Deeper
            opened, and nowhere else - so the one moment it is for, somebody
            listening and wondering where this came from, was the one moment
            nothing asked for it."""
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)
            page.evaluate("showScreen('player')")
            page.evaluate("rememberEpisode('why bonds move', 2, '')")
            page.evaluate("fetchEpisodeSources()")
            page.wait_for_selector("#srcPanel:not([hidden])", timeout=8000)

            marks = page.eval_on_selector_all("#srcMarks .src-mark", "e => e.length")
            assert marks == 3, f"the corner showed {marks} publisher marks, not three"
            # Overlapped, which is what makes it read as "these several"
            # rather than as a list somebody has to count.
            overlap = page.eval_on_selector(
                "#srcMarks .src-mark:nth-child(2)",
                "e => getComputedStyle(e).marginLeft")
            assert overlap.startswith("-"), \
                f"the marks are not overlapped: margin-left {overlap}"
            # In the corner of the player, not a strip across it.
            box = page.eval_on_selector("#srcPanel", "e => {"
                                        " var r = e.getBoundingClientRect();"
                                        " var p = e.closest('.mini-stage')"
                                        "   .getBoundingClientRect();"
                                        " return {w: r.width, pw: p.width,"
                                        "  right: p.right - r.right}; }")
            assert box["w"] < box["pw"] * 0.6, \
                "the sources panel is still a full-width strip"
            assert box["right"] < 4, "the sources panel is not in the corner"

            # And the whole list is one tap away, with the ones that are
            # hidden in the corner still in it.
            page.evaluate("openSources()")
            page.wait_for_selector("#sourcesOverlay.active", timeout=6000)
            rows = page.eval_on_selector_all("#srcList .src-row", "e => e.length")
            assert rows >= 4, f"the popup listed {rows} sources"
            page.evaluate("closeSources()")
            page.wait_for_timeout(300)
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def tapping_the_player_generates_nothing():
            """A tap anywhere on the stage that was not a button either jumped
            to the next episode in the album or, with no album, generated a
            random myFAM topic - an episode nobody asked for, costing a model
            call and a GPU, in place of whatever was playing."""
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)
            page.evaluate("showScreen('player')")
            page.wait_for_timeout(300)
            assert page.eval_on_selector(
                "#playerStage", "e => !e.getAttribute('onclick')"), \
                "the player stage still has a tap handler on it"
            assert page.evaluate("typeof playerTapAdvance") == "undefined", \
                "playerTapAdvance is still defined"
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def an_episode_is_titled_by_what_it_is_about():
            """The title used to be whatever the listener typed. Somebody who
            asked "what happened with the fed yesterday" got an episode called
            exactly that - their own words handed back with capital letters.

            The model writes the title on a trailing marker line, so it is not
            known until the script is finished - after the first word is
            already playing. The player opens on a provisional title derived
            from the question and swaps in the real one when it lands, the way
            the Go Deeper chip fills."""
            typed = "what happened with the fed yesterday"
            provisional = page.evaluate(
                "q => deriveTitleFromPrompt(q)", typed)
            # Not the raw question, and not Title Case on every word either -
            # capitalising "With" and "The" reads as a transcript of a search
            # box rather than as a title.
            assert provisional != typed, "the provisional title is the question"
            assert " with " in provisional, \
                f"the small words were capitalised: {provisional!r}"
            assert provisional.startswith("What"), provisional

            page.evaluate("showScreen('player')")
            page.evaluate("currentPlayingTopicKey = '_titletest'")
            page.evaluate("""() => {
              TOPICS['_titletest'] = { title: 'Provisional', prompt: 'q',
                                       source: 'x', caption: '' };
              document.getElementById('p-title').textContent = 'Provisional';
              rememberEpisode('q', 2, '');
            }""")
            page.evaluate("fetchEpisodeThread()")
            page.wait_for_timeout(1200)
            shown = page.text_content("#p-title").strip()
            assert shown != "Provisional", \
                "the real title never replaced the provisional one"
            assert len(shown) > 4, f"the title came back as {shown!r}"

            # A title somebody typed themselves, or a bank tile's own
            # hand-written one, is not up for replacement.
            page.evaluate("""() => {
              TOPICS['_titletest'].titleOverridden = true;
              TOPICS['_titletest'].title = 'Mine';
              document.getElementById('p-title').textContent = 'Mine';
              applyEpisodeTitle('Something The Model Wrote');
            }""")
            assert page.text_content("#p-title").strip() == "Mine", \
                "a title the listener set was overwritten"
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def a_friends_vibe_is_named_on_an_explore_card():
            """"<name> vibed with this episode", with their face, when a
            *friend* both generated it and vibed it.

            Driven through `renderReel` with a card of each kind rather than
            read off the feed, because the live build has one listener in its
            database and therefore honestly no friends - the tag is the same
            code on both, and this checks the code."""
            page.evaluate("showScreen('explore')")
            page.evaluate("""() => {
              reelCurrent = { query: 'why volcanoes erupt',
                title: 'What Makes A Volcano Go', minutes: 2, thread: '',
                age_seconds: 300, vibed: true,
                vibed_by: { name: 'Rachel Solomon', handle: 'rachels',
                            avatar: '' } };
              renderReel();
            }""")
            page.wait_for_timeout(300)
            assert page.eval_on_selector("#reelVibe", "e => !e.hidden"), \
                "the friend tag did not appear"
            said = page.text_content("#reelVibe").strip()
            assert "Rachel Solomon" in said and "vibed with this episode" in said, \
                f"the tag says {said!r}"
            assert page.eval_on_selector("#reelVibeAv", "e => e.innerHTML.length") > 0, \
                "no picture beside the name"

            # A stranger's vibe lifts a card and does not name anybody. Putting
            # names the listener has never heard of under a card about their
            # friends is the mistake §102 took off myFAM.
            page.evaluate("""() => {
              reelCurrent = { query: 'why bonds move', title: 'Bonds',
                minutes: 2, thread: '', age_seconds: 300, vibed: true };
              renderReel();
            }""")
            page.wait_for_timeout(300)
            assert page.eval_on_selector("#reelVibe", "e => e.hidden"), \
                "a card with no friend behind it still showed the tag"
            # Put Explore's own state back. This check reached into
            # `reelCurrent` to drive the render, and leaving a synthetic card
            # there breaks the reel for anything that runs after it.
            page.evaluate("reelCurrent = null; reelQueue = []; reelHistory = []")
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def friends_navigation_comes_back_to_your_own_profile():
            """The bug the packet describes, in four symptoms with one cause:
            a friend's profile was drawn into `screen-profile` with a variable
            deciding whose it was. So back from the friend popped to Friends,
            back again showed `screen-profile` still holding the *friend's*
            DOM, back again fell through to search, and the Profile tab
            flashed them. Two pages sharing one container is one bug, not a
            routing bug with four fixes - the friend's profile has its own
            screen now."""
            ensure_account()
            page.evaluate("openProfile()")
            page.wait_for_selector("#screen-profile.active .yf-msgcard",
                                   timeout=10000, state="attached")
            page.wait_for_timeout(600)
            mine = page.text_content("#profileBody")
            page.evaluate("openFriends()")
            page.wait_for_timeout(1200)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-friends", "Friends did not open"

            # Opened by name rather than by tapping a row: the live build has
            # one listener in its database and so honestly no friends to tap,
            # and this is a check about the navigation, which is the same code
            # on both builds.
            page.evaluate("openPersonProfile('beth')")
            page.wait_for_timeout(900)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-person", "a friend's profile is not its own screen"

            page.evaluate("goBack()")
            page.wait_for_timeout(500)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-friends", "back from a friend did not reach Friends"

            page.evaluate("goBack()")
            page.wait_for_timeout(500)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-profile", \
                "back from Friends did not reach your own profile"
            # And it is *yours*, not the last friend's left behind in the DOM.
            assert page.text_content("#profileBody") == mine, \
                "your own profile came back holding a friend's page"
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def a_friends_profile_shows_what_they_published():
            """Public mixes, vibes and interest pills - and nothing else. What
            somebody has listened to is theirs; there is no endpoint that
            hands one listener another one's history, and a number invented
            here would be a promise to keep later."""
            ensure_account()
            page.evaluate("openFriends()")
            page.wait_for_timeout(1000)
            page.evaluate("openPersonProfile('beth')")
            page.wait_for_timeout(1400)
            body = page.text_content("#personBody") or ""
            assert "Vibes" in body, "no vibes section on a friend's profile"
            # The line that has to stay true of this screen, whether or not
            # there is anything on it.
            assert "no endpoint" in body, \
                "the profile stopped saying what it deliberately does not know"

            # The rest is about what a *filled* profile shows, and needs
            # somebody in the graph to have published something. The live
            # build has one listener in its database and so honestly nobody
            # else at all - which is a fact about that deployment, and the
            # reason this is a precondition rather than an assertion.
            if not page.evaluate("(friendsData.followers || []).length"):
                page.evaluate("goBack()")
                page.evaluate("openMyFamTab()")
                page.wait_for_timeout(400)
                return
            assert page.eval_on_selector_all("#personBody .yf-vrow",
                                             "e => e.length") >= 1, \
                "their vibes are not listed"
            assert page.eval_on_selector_all("#personBody .yf-chip",
                                             "e => e.length") >= 1, \
                "no interest chips on a friend's profile"
            assert "public mixes" in body.lower(), "no public mixes on their profile"
            # Message is the primary action and opens the chat with them.
            page.evaluate("document.querySelector('#personBody .yf-btn.gold').click()")
            page.wait_for_selector("#screen-thread.active", timeout=8000)
            page.evaluate("stopThreadPolling(); goBack()")
            page.wait_for_timeout(400)
            page.evaluate("goBack()")
            page.wait_for_timeout(400)
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def a_new_follower_is_announced_and_can_be_followed_back():
            """"___ started following you", with their picture, a Follow back
            button and an X.

            Driven with a synthetic follower rather than read off the graph,
            because the live build has one listener in its database and so
            honestly nobody to be one - the popup is the same code on both
            builds, and this checks the code."""
            ensure_account()
            page.evaluate("""() => {
              followerPopupShown = {};
              showFollowerPopup({ user_id: 'u_test', name: 'Nadia Okoro',
                                  handle: 'nadia', avatar: '',
                                  follows_back: false });
            }""")
            page.wait_for_selector("#followerOverlay.active", timeout=8000)
            said = page.text_content("#nfName").strip()
            assert "Nadia Okoro" in said and "started following you" in said, \
                f"the popup says {said!r}"
            assert page.eval_on_selector("#nfAvatar", "e => e.innerHTML.length") > 0, \
                "no picture at the top of the popup"
            assert page.query_selector("#followerOverlay .modal-x"), \
                "the popup cannot be ignored"
            assert not page.eval_on_selector("#nfBack", "e => e.hidden"), \
                "no way to follow back"

            # Somebody already followed gets no button, because "Follow back"
            # there is a control that cannot do anything.
            page.evaluate("""() => {
              showFollowerPopup({ user_id: 'u_test2', name: 'Beth Solomon',
                                  handle: 'beth', avatar: '',
                                  follows_back: true });
            }""")
            page.wait_for_timeout(300)
            assert page.eval_on_selector("#nfBack", "e => e.hidden"), \
                "Follow back was offered to somebody already followed"
            page.evaluate("closeFollowerPopup()")
            page.wait_for_timeout(300)
            assert not page.query_selector("#followerOverlay.active"), \
                "the popup did not close"

            # Who is new clears on the Friends *screen* and never when a popup
            # is drawn - otherwise it is a fact nobody got to read. (The
            # count on a Friends tile went with the tile: YourFAM has no such
            # tile, and the popup and the banner are what announce a follow.)
            page.evaluate("""() => {
              newFollowers = [{ user_id: 'u_test', name: 'Nadia Okoro',
                                handle: 'nadia', avatar: '',
                                follows_back: false }];
            }""")
            page.evaluate("openFriends()")
            page.wait_for_timeout(1200)
            assert page.evaluate("newFollowers.length") == 0, \
                "opening the Friends tab did not clear the count"
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def edit_profile_is_a_screen_with_everything_on_it():
            """The Edit profile pill used to open two chained modals - a name,
            then a handle - with no way back between them, no picture, and
            placeholders reading "e.g. Ian Solomon" and "iansolomon": a real
            name and handle offered to every listener in the app.

            It is one screen now, with the picture, the name, the username
            and the account rows.

            Which interests are *shared* used to be here too and is not any
            more (§114): that choice is on the profile, beside the row it
            changes. This screen is who you are, not what you show."""
            ensure_account()
            # Somebody with a profile to edit. A listener who has never set
            # one gets the same screen with empty fields, which is correct and
            # is not what this check is about.
            page.evaluate("""() => fetch('/api/me', { method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ name: 'Smoke Tester',
                                     handle: 'smoketester' }) })""")
            page.wait_for_timeout(600)
            page.evaluate("openProfile()")
            page.wait_for_timeout(1100)
            page.evaluate("editIdentity()")
            page.wait_for_selector("#screen-identity.active", timeout=10000)

            assert page.query_selector("#identityPic"), "no picture to change"
            assert page.query_selector("#identityName"), "no name field"
            assert page.query_selector("#identityHandle"), "no username field"
            assert not page.eval_on_selector("#identityEditOnly", "e => e.hidden"), \
                "the editor's own rows are hidden in the editor"
            rows = page.text_content("#identityAccountRows") or ""
            assert "Change password" in rows, "no way to change a password"
            assert not page.query_selector("#identityShared"), \
                "the old shared-interests block came back"
            assert not page.eval_on_selector("#identityInterests", "e => e.hidden"), \
                "Edit profile has no interests section"

            # Prefilled from what is stored, so an editor opens on the current
            # state rather than on empty fields somebody has to retype.
            assert page.input_value("#identityName").strip(), \
                "the editor opened with an empty name"

            # A handle under three characters is refused under the field
            # rather than in a toast that is gone before it is read.
            page.fill("#identityHandle", "ab")
            page.evaluate("saveIdentity()")
            page.wait_for_timeout(500)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-identity", "a bad handle was saved anyway"
            assert page.eval_on_selector(
                "#identityHandleNote", "e => e.classList.contains('bad')"), \
                "the refusal was not shown under the field"

            page.evaluate("closeIdentity()")
            page.wait_for_timeout(400)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-profile", "the X on Edit profile did not return"
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def the_player_names_its_four_icons():
            """Share, vibe, save and captions. Unlabelled, a bookmark, a
            two-way arrow and a speech rectangle are three guesses - and the
            play-all sidebar and Explore's rail had always carried labels, so
            this row was the odd one out."""
            page.evaluate("showScreen('player')")
            page.wait_for_timeout(300)
            words = page.eval_on_selector_all(
                "#screen-player .pc-row2 .pt-cap",
                "e => e.map(x => x.textContent.trim().toLowerCase())")
            assert words == ["share", "vibe", "save", "captions"], \
                f"the player's icons are labelled {words}"
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def the_photo_editor_crops_what_it_shows():
            """The crop used to be a silent centre crop - a guess made on
            somebody's behalf about where their face is. What matters here is
            that the export reads the same numbers the preview is painted
            from: a preview computed one way and an export computed another is
            a crop that lies, and nobody finds out until afterwards.

            Driven with a synthetic image, because a file picker cannot be."""
            page.evaluate("openProfile()")
            page.wait_for_timeout(600)
            # A 400x200 image: wider than tall, so the crop has a real choice
            # to make and the clamp has something to clamp.
            page.evaluate(
                """() => {
                    var c = document.createElement('canvas');
                    c.width = 400; c.height = 200;
                    var x = c.getContext('2d');
                    x.fillStyle = '#123456'; x.fillRect(0, 0, 400, 200);
                    x.fillStyle = '#e0b563'; x.fillRect(0, 0, 40, 200);
                    window.__testPhoto = c.toDataURL('image/jpeg', 0.9);
                }""")
            page.evaluate("openPhotoEditor(window.__testPhoto)")
            page.wait_for_selector("#photoOverlay.active", timeout=8000)
            page.wait_for_timeout(500)
            state = page.evaluate(
                """() => ({ stage: photo.stage, base: photo.base,
                            ox: photo.ox, oy: photo.oy, w: photo.img.width })""")
            assert state["stage"] > 0, "the stage was measured before it had a size"
            # Covering, always: the image can never be dragged off the square.
            assert state["ox"] <= 0.01 and state["oy"] <= 0.01, state
            assert state["ox"] >= state["stage"] - state["w"] * state["base"] - 0.01, state
            # Dragged hard left, the clamp holds rather than letting the crop
            # run off the edge of the picture.
            page.evaluate("photo.ox = -99999; paintPhoto();")
            after = page.evaluate("() => photo.ox")
            assert after >= state["stage"] - state["w"] * state["base"] - 0.01, after
            # Zooming keeps the middle of the crop where it was.
            page.evaluate(
                """() => { document.getElementById('photoZoom').value = 220;
                           photoZoomed(); }""")
            zoomed = page.evaluate("() => photo.zoom")
            assert abs(zoomed - 2.2) < 0.01, zoomed
            page.evaluate("closePhotoEditor()")
            page.wait_for_timeout(300)
            assert not page.query_selector("#photoOverlay.active")
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def the_interest_catalogue_is_the_whole_list():
            """The first run's "View more". Seventy-odd named subjects, each
            with an icon - and the chips above it are still only the eight
            facets, which is the line CLAUDE.md draws and this keeps."""
            page.evaluate(
                "try{ localStorage.removeItem('fam.prefs'); }catch(e){}; startEntry()")
            page.wait_for_timeout(600)
            page.evaluate("skipAccount()")
            page.wait_for_selector("#screen-intro.active .intro-chip",
                                   timeout=10000, state="attached")
            chips = page.eval_on_selector_all(".intro-chip", "e => e.length")
            assert chips == topics_mod.PICKER_SIZE, (
                f"the wheel drew {chips} discs, not {topics_mod.PICKER_SIZE}")
            # Six of the eight are *shown*; the eight are still the whole
            # pickable vocabulary, and every one of them is reachable through
            # the catalogue in the middle - which is what makes narrowing the
            # wheel a screen decision rather than a vocabulary one. The discs
            # carry `TAG_SHORT`, because "Money & markets" does not fit in one.
            ids = page.eval_on_selector_all(
                ".intro-chip", "e => e.map(x => x.textContent.trim())")
            assert set(ids) <= set(topics_mod.TAG_SHORT.values()), (
                f"the wheel drew something that is not a facet: {ids}")
            # The way in is the hub of the wheel.
            assert page.query_selector(".orbit-more"), "no way into the catalogue"
            page.evaluate("openTopicCatalog()")
            page.wait_for_selector("#screen-catalog.active .cat-row",
                                   timeout=10000, state="attached")
            rows = page.eval_on_selector_all(".cat-row", "e => e.length")
            assert rows > 50, f"the catalogue offered {rows} interests"
            names = page.eval_on_selector_all(".cat-name", "e => e.map(x => x.textContent)")
            for wanted in ("Soccer", "Formula 1", "K-pop", "Personal Finance"):
                assert wanted in names, f"{wanted} is missing from the catalogue"
            # Every row draws an icon. A row with none is a row that looks
            # broken next to the ones that have them.
            icons = page.eval_on_selector_all(".cat-art svg", "e => e.length")
            assert icons == rows, f"{rows - icons} rows had no icon"
            # And the search narrows it rather than decorating it.
            page.fill("#catalogSearch", "hockey")
            page.wait_for_timeout(400)
            found = page.eval_on_selector_all(".cat-name", "e => e.map(x => x.textContent)")
            assert found == ["Ice Hockey"], found
            # Closing it must come back to the step it was opened from. It
            # used to `goBack()`, and the intro is drawn with `showScreen` and
            # never joins the stack - so the pop landed on SearchFAM and the
            # first run fell out of the intro on the way out.
            page.evaluate("closeTopicCatalog()")
            page.wait_for_timeout(400)
            active = page.eval_on_selector(".screen.active", "e => e.id")
            assert active == "screen-intro", (
                f"closing the catalogue left the first run at {active}")
            assert not page.eval_on_selector("#introPageInterests", "e => e.hidden"), (
                "closing the catalogue skipped past the interests step")
            page.evaluate("finishIntro()")
            page.wait_for_timeout(700)

        def the_catalogue_saves_what_was_chosen():
            """Adding a topic used to take effect the instant it was tapped
            and there was nothing to press afterwards, so the screen could not
            tell a choice somebody had made from one they were considering -
            and there was no way to back out of either. §107 adds a Save.

            The X still leaves without saving, which is what makes the Save
            button mean anything.
            """
            page.evaluate("openTopicCatalog()")
            page.wait_for_selector("#screen-catalog.active .cat-row",
                                   timeout=10000, state="attached")
            page.wait_for_timeout(300)
            assert page.eval_on_selector("#catalogDock", "e => e.hidden"), \
                "Save is offered before anything has been chosen"

            page.evaluate("document.querySelectorAll('.cat-row')[0].click()")
            page.wait_for_timeout(300)
            assert not page.eval_on_selector("#catalogDock", "e => e.hidden"), \
                "choosing a topic did not offer a way to save it"

            # Out by the X: nothing kept, because nothing was saved.
            page.evaluate("closeTopicCatalog()")
            page.wait_for_timeout(300)
            assert page.evaluate("chosenTopics.length") == 0, \
                "the X kept a topic that was never saved"

            # And again, this time pressing Save.
            page.evaluate("openTopicCatalog()")
            page.wait_for_selector("#screen-catalog.active .cat-row",
                                   timeout=10000, state="attached")
            page.wait_for_timeout(300)
            page.evaluate("document.querySelectorAll('.cat-row')[0].click()")
            page.wait_for_timeout(200)
            page.evaluate("saveTopicCatalog()")
            page.wait_for_timeout(500)
            assert page.evaluate("chosenTopics.length") == 1, \
                "Save kept nothing"
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                != "screen-catalog", "Save did not leave the screen"

        def the_catalogue_search_can_add_what_it_did_not_find():
            """The search used to filter the list and nothing else, so
            searching for something absent produced an empty screen and a
            sentence telling the listener to go and ask somewhere else - a
            search that can only fail, on the screen whose whole job is
            collecting what somebody is interested in (§107, item 13)."""
            page.evaluate("openTopicCatalog()")
            page.wait_for_selector("#screen-catalog.active", timeout=10000)
            page.wait_for_timeout(300)
            page.fill("#catalogSearch", "nineteenth century canals")
            page.wait_for_timeout(400)
            add = page.query_selector(".cat-add-typed")
            assert add, "a search that matched nothing offered no way forward"
            assert "nineteenth century canals" in add.inner_text()

            before = page.evaluate("chosenTopics.length")
            page.evaluate("addTypedInterest()")
            page.wait_for_timeout(300)
            assert page.evaluate("chosenTopics.length") == before + 1, \
                "adding what was typed kept nothing"
            labels = page.evaluate("chosenTopicLabels()")
            assert "nineteenth century canals" in labels, labels

            # Typing the name of something that *is* listed adds the listed
            # entry rather than a second row that reads the same and carries
            # none of the catalogue's tags.
            page.fill("#catalogSearch", "Formula 1")
            page.wait_for_timeout(300)
            assert not page.query_selector(".cat-add-typed"), \
                "offered to add a topic that is already on the list"
            page.evaluate("closeTopicCatalog()")
            page.wait_for_timeout(300)

        def the_app_opens_on_the_front_door_rather_than_flashing_myfam():
            """§107, items 1 and 9. myFAM was the screen marked `active` in the
            markup, so it was on screen from the moment the page parsed - and
            who the listener is is not known until `/api/auth/me` answers. A
            listener without an account saw myFAM flash and be replaced.

            Checked against the markup rather than by racing the boot, because
            that is where the bug was: a default screen is a guess at an
            answer that has not come back, and the fix is that there is no
            default."""
            import re as _re

            markup = target.read_text()
            active = _re.findall(r'<section class="screen([^"]*)" id="screen-([a-z]+)"',
                                 markup)
            lit = [name for cls, name in active if "active" in cls]
            assert not lit, f"a screen is active before the app knows who is here: {lit}"
            # And the decision itself: signed out lands on the front door.
            page.evaluate("AUTH = { authenticated: false }; bootToFirstScreen();")
            page.wait_for_timeout(500)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-welcome", "a signed-out listener did not land on sign-up"
            # The door through it is still there: listening needs no account,
            # and this screen is the one place that could quietly become a wall.
            assert page.query_selector(".entry-skip"), \
                "the front door has no way past it"
            page.evaluate("AUTH = { authenticated: true }; bootToFirstScreen();")
            page.wait_for_timeout(600)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-myfam", "a signed-in listener did not go straight in"

        def a_message_arrives_without_being_asked_for():
            """§107, item 3. Messages appeared only when the chat screen was
            opened, so two people talking had to leave and come back to see
            each other - and nothing at all announced one while you were
            elsewhere in the app.

            Driven through the poll rather than by calling the banner, because
            the bug being guarded against is the poll never reaching it: a
            test that drew the banner by hand would pass with the poll
            unplugged."""
            page.evaluate("startNotificationPolling()")
            page.wait_for_timeout(500)
            page.evaluate("""
                window.famPreviewNotify({
                  id: 9001, kind: "text", text: "are you hearing this",
                  from: { user_id: "u_beth", name: "Beth Solomon", handle: "beth" }
                });
                pollNotifications();
            """)
            page.wait_for_selector("#notifBanner.show", timeout=8000)
            assert "Beth" in page.text_content("#notifTitle")
            assert "are you hearing this" in page.text_content("#notifText")

            # Tapping it opens that conversation, which is the whole reason it
            # is a control rather than a toast.
            page.evaluate("openNotification()")
            page.wait_for_timeout(600)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-thread", "tapping the banner did not open the chat"
            assert page.evaluate("currentThread && currentThread.user_id") == "u_beth"

            # And a follow goes to Friends, where following back lives.
            page.evaluate("""
                window.famPreviewNotify({ follow: {
                  user_id: "u_nadia", name: "Nadia Okoro", handle: "nadia" } });
                pollNotifications();
            """)
            page.wait_for_selector("#notifBanner.show", timeout=8000)
            assert "started following you" in page.text_content("#notifTitle")
            page.evaluate("openNotification()")
            page.wait_for_timeout(600)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-friends", "tapping a follow did not reach Friends"
            page.evaluate("stopNotificationPolling(); openMyFamTab()")
            page.wait_for_timeout(400)

        def a_sent_message_appears_before_the_server_answers():
            """It used to wait for the POST and then re-fetch the whole
            conversation - two round trips before your own words appeared,
            which is where "a few seconds of latency when I send" came from."""
            page.evaluate("openMessages()")
            page.wait_for_timeout(500)
            page.evaluate("openThreadWith({user_id:'u_beth', name:'Beth Solomon', handle:'beth'})")
            page.wait_for_selector("#screen-thread.active", timeout=8000)
            page.wait_for_timeout(500)
            before = page.eval_on_selector_all(".msg-row", "e => e.length")
            page.fill("#threadInput", "typed and drawn at once")
            page.evaluate("sendThreadMessage()")
            # No wait: the point is that it is on screen in the same turn.
            rows = page.eval_on_selector_all(".msg-row", "e => e.length")
            assert rows == before + 1, \
                "a sent message waited for the server before appearing"
            assert "typed and drawn at once" in page.text_content("#thread-body")
            # And once acknowledged it stops being pending, and is not drawn
            # twice when the poll hands the same message back.
            page.wait_for_timeout(1200)
            assert not page.query_selector(".msg-row.pending"), \
                "an acknowledged message is still drawn as pending"
            page.wait_for_timeout(2500)
            after = page.eval_on_selector_all(".msg-row", "e => e.length")
            assert after == before + 1, \
                f"the poll drew the same message again: {before + 1} -> {after}"
            page.evaluate("stopThreadPolling(); openMyFamTab()")
            page.wait_for_timeout(400)

        def messages_show_faces_and_typing():
            """§127. The inbox drew initials for everybody, a friend with a
            photo included, and a conversation gave no sign the other person
            was writing back."""
            page.evaluate("openMessages()")
            page.wait_for_timeout(1500)
            # The fixture build has a friend with a picture; the live build
            # starts with an empty inbox, so only the dots can be asked of it.
            rows = page.eval_on_selector_all(".thread-row", "e => e.length")
            if rows:
                faces = page.eval_on_selector_all(".thread-row .av img", "e => e.length")
                assert faces >= 1, "the inbox drew initials for a friend with a picture"
            page.evaluate(
                """() => {
                    var real = window.fetch;
                    window.__famRealFetch = real;
                    window.fetch = function(u, o){
                        var url = String(u);
                        if(url.indexOf("/api/messages/thread") === 0
                           && url.indexOf("since=") !== -1){
                            return real(u, o).then(function(r){ return r.json(); })
                              .then(function(d){ d.typing = true; d.messages = [];
                                return { ok: true, json: function(){ return Promise.resolve(d); } }; });
                        }
                        return real(u, o);
                    };
                }""")
            page.evaluate("openThreadWith({user_id:'u_beth', name:'Beth Solomon', handle:'beth'})")
            page.wait_for_selector("#screen-thread.active", timeout=8000)
            page.wait_for_selector("#threadTypingRow", timeout=6000, state="attached")
            if rows:
                assert page.query_selector("#thread-av img"), \
                    "the conversation header drew initials for a friend with a picture"
            page.evaluate("stopThreadPolling(); window.fetch = window.__famRealFetch;"
                          " openMyFamTab()")
            page.wait_for_timeout(400)

        def an_interest_chip_opens_its_topic():
            """YourFAM's chips are doors, not labels: each opens the Topic
            screen for that interest, with the length pill every browse
            surface uses. There is no inline Edit - interests are changed in
            Edit profile - and the page generates nothing to fill itself."""
            ensure_account()
            page.evaluate("openProfile()")
            page.wait_for_selector("#screen-profile.active .yf-chips .yf-chip",
                                   timeout=10000)
            page.wait_for_timeout(600)
            assert not page.query_selector("#screen-profile .pf-tags-edit"), \
                "the inline interests Edit came back"
            # A listener with no interests yet gets one muted chip that opens
            # Edit profile instead; give this one something to tap.
            if not page.query_selector("#screen-profile .yf-chips .yf-chip:not(.muted)"):
                page.evaluate("""() => savePreferences({ interests: ['tech'] })
                    .then(function(){ loadProfile(); })""")
                page.wait_for_selector(
                    "#screen-profile .yf-chips .yf-chip:not(.muted)", timeout=10000)
            chip = "#screen-profile .yf-chips .yf-chip:not(.muted)"
            label = page.text_content(chip).strip()
            page.evaluate(f"document.querySelector('{chip}').click()")
            page.wait_for_selector("#screen-topic.active .yf-topic-h", timeout=10000)
            page.wait_for_timeout(900)
            heading = page.text_content("#screen-topic .yf-topic-h").strip()
            assert heading.lower() == label.lower(), \
                f"the topic screen ({heading!r}) is not about the chip that opened it ({label!r})"
            page.wait_for_timeout(900)
            # The badges follow the pill: it sets how long a tapped episode is
            # written, it does not filter by duration.
            pill = page.text_content("#topicLengthVal").strip()
            badges = page.eval_on_selector_all(
                "#screen-topic .yf-card-len", "e => e.map(x => x.textContent.trim())")
            assert all(b.lower() == pill.lower() for b in badges), \
                f"card lengths {badges} do not follow the pill ({pill})"
            page.evaluate("setTopicFilter('friends')")
            page.wait_for_timeout(700)
            assert page.query_selector("#screen-topic .yf-filter.on:nth-child(2)"), \
                "Friends vibed did not become the active filter"
            page.evaluate("goBack()")
            page.wait_for_timeout(400)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-profile", "back from a topic did not reach YourFAM"

        def a_phone_number_says_which_country(  ):
            """§107, item 7. Composed from the ISO code rather than drawn, so
            forty-five flags cost no assets - and the code and the letters stay
            beside it, because a platform with no flag font would otherwise
            leave a mystery box where the country was."""
            page.evaluate("fillCountryCodes('authPhoneCC')")
            page.wait_for_timeout(200)
            options = page.eval_on_selector_all(
                "#authPhoneCC option", "e => e.map(x => x.textContent)")
            assert options, "the country picker is empty"
            assert "\U0001F1FA\U0001F1F8" in options[0], \
                f"no flag on the first country: {options[0]!r}"
            assert "+1" in options[0] and "US" in options[0], \
                f"the dialling code or the letters went missing: {options[0]!r}"
            # The one that disagrees: dialled +44, written UK, ISO GB. Reading
            # the flag off the label would put the wrong flag beside it.
            uk = [o for o in options if "+44" in o][0]
            assert "\U0001F1EC\U0001F1E7" in uk, f"the UK flag is wrong: {uk!r}"
            assert "UK" in uk, f"the UK is labelled something else: {uk!r}"

        def an_episode_can_be_shared_outside_fam():
            """FAM posts nothing: the server writes the link and the wording,
            and the phone does the sending. What has to be on screen is a
            destination for each place somebody would send it."""
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)
            page.evaluate("showScreen('player')")
            page.evaluate("nowBarState = {query: 'why bonds move',"
                          " title: 'Bonds', minutes: 3}")
            page.evaluate("openShareModal()")
            page.wait_for_selector("#shareTargets .sh-target", timeout=8000)
            names = page.eval_on_selector_all(".sh-name", "e => e.map(x => x.textContent)")
            for wanted in ("Facebook", "LinkedIn", "Instagram story", "Snapchat story"):
                assert wanted in names, f"{wanted} was not offered: {names}"
            # Sharing inside FAM did not go away to make room for it: one
            # sheet, two halves, because "share this" is one intent.
            #
            # The people are the real follow graph now, so "how many" is a
            # fact about the database this preview is running on - the live
            # one has exactly one listener in it. What must never happen is
            # the half going *silent*: either it lists people or it says why
            # it cannot, and an empty space that explains nothing is the
            # failure. Which is also why this reads the section rather than
            # counting rows.
            inside = page.eval_on_selector(
                "#shareContacts", "e => e.textContent.trim()")
            people = page.eval_on_selector_all(".share-contact", "e => e.length")
            assert people > 0 or inside, \
                "the sheet's in-FAM half was empty and said nothing"
            assert not page.eval_on_selector("#shareNote", "e => e.hidden"), \
                "the preview link is not public and the sheet did not say so"
            page.evaluate("closeShareModal()")
            page.wait_for_timeout(300)
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def whats_next_offers_four_and_counts_down():
            """The popup, driven the way an ended episode drives it. The
            countdown tile is checked for existence, not waited out - five
            seconds of real time in a smoke test buys nothing."""
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)
            page.evaluate("showScreen('player')")
            page.evaluate("maybeOfferNextUp('what the fed did to interest rates', '')")
            page.wait_for_selector("#nextUpOverlay.active .nextup-tile",
                                   timeout=10000)
            tiles = page.eval_on_selector_all(".nextup-tile", "e => e.length")
            assert tiles == 4, f"expected a 2x2 grid, saw {tiles} tiles"
            assert page.query_selector(".nextup-tile.lead .nextup-timer"), \
                "the first tile has no countdown"
            assert "starts in" in page.text_content("#nextUpSub").lower()
            # The countdown tile is the most likely next listen, and says so.
            # With no album and no predicted follow-up it is the ranking's own
            # first pick, and the four tiles are all from that one ranking -
            # the popup is the feed's opinion arrived at one tap earlier,
            # never a second recommender.
            lead = page.text_content(".nextup-tile.lead .nextup-tile-sub").strip()
            assert lead and lead.lower() != "recommended", \
                f"the countdown tile does not say why it is first: {lead!r}"
            # Tapping anything else cancels the countdown rather than racing it.
            page.evaluate("closeNextUp()")
            page.wait_for_timeout(300)
            assert not page.query_selector("#nextUpOverlay.active")
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def ensure_account():
            """Sign up unless this browser already has an account.

            Needed because one check above clears localStorage on purpose, and
            on the live preview that is a real logout: the session token lives
            there. Mixes are account-gated, so anything below that touches them
            has to put an account back first. The email is unique per call -
            the store is durable and the same address twice is refused, exactly
            as the server refuses it.
            """
            if page.evaluate("() => AUTH && AUTH.authenticated"):
                return
            page.evaluate(
                """() => {
                    var who = "smoke-" + Math.random().toString(36).slice(2, 9)
                              + "@example.com";
                    return fetch("/api/auth/signup", {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({ email: who,
                                               password: "a-long-enough-password" })
                    }).then(function(){ return refreshAuth(); });
                }"""
            )
            page.wait_for_timeout(600)

        def the_account_gate_reads_as_a_choice():
            """Skipping the account step has to look like a decision, not a
            broken screen - and it has to offer the way out of itself."""
            page.evaluate("openPlayFAM()")
            # After the load settles, not with it: loadMixes writes the same
            # element asynchronously and would paint over this.
            page.wait_for_timeout(1200)
            page.evaluate("renderMixesLocked()")
            page.wait_for_selector("#screen-playfam .locked-note", timeout=10000)
            assert page.eval_on_selector_all("#screen-playfam .locked-acts .pf-btn",
                                             "e => e.length") == 2, \
                "the gate offered no way to sign up or log in"
            text = page.text_content("#screen-playfam .locked-note").lower()
            assert "start you over" in text, \
                "the gate did not say signing up keeps what they already have"

        def signed_out():
            """Present as a guest without destroying the session.

            `AUTH` is what every gate in the app reads, and it is the server's
            answer rather than anything this page decides - so faking it is
            exactly the state a guest is in, and it is reversible. Actually
            logging out of the live preview would take the session token with
            it and every check below would have to sign up again.
            """
            page.evaluate(
                "AUTH = {user_id:'', email:'', authenticated:false}")

        def signed_back_in():
            """Put the real answer back. Asked of the server, not restored
            from a variable, so a check after this one sees the truth."""
            page.evaluate("() => refreshAuth()")
            page.wait_for_timeout(400)

        def the_profile_tab_is_a_door_until_there_is_an_account():
            """Not a profile with pieces missing.

            It used to draw the whole page for a guest - name, counts,
            shelves, vibes - with a note at the bottom offering an account.
            Everything on it was true, and that was the problem: a profile is
            the one screen that is *about* having an account, so drawing a
            full one for somebody without one invites them to furnish a room
            the app is about to say is not theirs.
            """
            signed_out()
            page.evaluate("openProfile()")
            page.wait_for_selector("#screen-profile .pf-gate", timeout=10000)
            assert not page.query_selector("#screen-profile .pf-id"), \
                "a guest was shown a profile"
            assert page.eval_on_selector_all("#screen-profile .pf-gate .pf-btn",
                                             "e => e.length") == 2, \
                "the gate offered no way to sign up or log in"
            signed_back_in()

        def an_account_gate_opens_the_real_sign_up_screen():
            """Every Sign up button in the app reaches the same screen.

            They used to open two chained modals asking for an address and
            then a password - a form that cannot offer a phone number, Google
            or Apple. So a listener who reached an account from DailyFAM and
            one who reached it from the front door were shown two different
            products, and only one of them was the product.

            Asserted by pressing it, not by reading the markup: §111 is that
            a control which is on screen and does somebody else's job is
            exactly what a presence check cannot see.
            """
            signed_out()
            page.evaluate("openProfile()")
            page.wait_for_selector("#screen-profile .pf-gate", timeout=10000)
            page.click("#screen-profile .pf-gate .pf-btn.primary")
            page.wait_for_selector("#screen-auth.active", timeout=10000)
            assert page.text_content("#authTitle").strip().lower() == "sign up"
            # And it knows where it came from, so the gate is simply working
            # when they get back to it.
            page.click("#screen-auth .back-row .back")
            page.wait_for_selector("#screen-profile.active", timeout=10000)
            signed_back_in()

        def edit_profile_chooses_up_to_five_interests():
            """"Tap to swap, or add your own - up to 5." Selected chips are
            gold; at five the rest dim and a tap on one does nothing; a typed
            topic becomes a chip and is chosen if there is room; and what was
            chosen is what the hub shows after Save."""
            ensure_account()
            page.evaluate("""() => fetch('/api/me', { method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ name: 'Smoke Tester',
                                     handle: 'smoketester' }) })""")
            page.wait_for_timeout(500)
            page.evaluate("openProfile()")
            page.wait_for_selector("#screen-profile .yf-chips", timeout=10000)
            page.wait_for_timeout(500)
            shown = page.eval_on_selector_all(
                "#screen-profile .yf-chips .yf-chip:not(.muted)", "e => e.length")
            assert shown <= 5, f"the hub drew {shown} interests"
            page.evaluate("editIdentity()")
            page.wait_for_selector("#screen-identity.active #identityIntChips .yf-int",
                                   timeout=10000)
            # Clear, then choose until full.
            page.evaluate("""() => { identityIntPick = []; renderIdentityInterests(); }""")
            n = page.eval_on_selector_all("#identityIntChips .yf-int", "e => e.length")
            for i in range(min(n, 6)):
                page.eval_on_selector_all("#identityIntChips .yf-int",
                                          "(e, i) => e[i] && e[i].click()", i)
                page.wait_for_timeout(60)
            on = page.eval_on_selector_all("#identityIntChips .yf-int.on", "e => e.length")
            assert on <= 5, f"the editor let {on} interests be chosen"
            if n > 5:
                assert page.eval_on_selector_all("#identityIntChips .yf-int.dim",
                                                 "e => e.length") >= 1, \
                    "a full set did not dim the rest"
            assert page.text_content("#identityIntCount").strip() == f"{on} OF 5"
            # Room for a typed one: take one off, then add it.
            page.eval_on_selector_all("#identityIntChips .yf-int.on",
                                      "e => e[0] && e[0].click()")
            page.fill("#identityIntAdd", "Surfing")
            page.evaluate("addIdentityInterest()")
            page.wait_for_timeout(200)
            typed = page.eval_on_selector_all(
                "#identityIntChips .yf-int.on",
                "e => e.map(x => x.textContent.trim())")
            assert "Surfing" in typed, f"a typed topic was not chosen: {typed}"
            page.evaluate("saveIdentity()")
            page.wait_for_selector("#screen-profile.active .yf-chips", timeout=10000)
            page.wait_for_timeout(900)
            hub = page.eval_on_selector_all("#screen-profile .yf-chips .yf-chip",
                                            "e => e.map(x => x.textContent.trim())")
            assert "Surfing" in hub, f"the hub did not show what was chosen: {hub}"
            # Put it back: a typed topic is a real interest (it joins
            # `topics`), and a check further down asserts the catalogue starts
            # with nothing chosen.
            page.evaluate("""() => savePreferences({ topics: [], profile_interests: [] })
                .then(function(){ return loadPrefChoices(); })""")
            page.wait_for_timeout(500)
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def the_bar_can_be_dragged_to_seek():
            """Sliding the bar is a seek, and it has to be a real one.

            Driven with the mouse rather than by calling FamAudio.seek: the
            thing under test is the gesture - pointer capture, the clamp at the
            buffered edge, the class that says the bar has been picked up -
            not the seek underneath it, which the transport already had.
            """
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(500)
            page.evaluate("startBankTopic(Object.keys(myFamTopics)[0])")
            page.wait_for_selector("#screen-player.active", timeout=15000)
            # Enough audio has to have arrived for there to be anywhere to
            # seek to: the bar clamps at what has been written.
            page.wait_for_timeout(4500)
            before = page.evaluate("() => FamAudio.position()")

            box = page.eval_on_selector(
                "#screen-player .progress-bar",
                "e => { var r = e.getBoundingClientRect();"
                " return {x: r.x, y: r.y, w: r.width}; }")
            page.mouse.move(box["x"] + 4, box["y"] + 2)
            page.mouse.down()
            page.mouse.move(box["x"] + box["w"] * 0.9, box["y"] + 2, steps=8)
            assert page.query_selector("#screen-player .progress-bar.scrubbing"), \
                "the bar did not say it had been picked up"
            page.mouse.up()
            page.wait_for_timeout(400)

            after = page.evaluate("() => FamAudio.position()")
            assert after > before + 0.5, \
                f"dragging the bar did not move playback ({before:.2f} -> {after:.2f})"
            assert not page.query_selector("#screen-player .progress-bar.scrubbing"), \
                "the bar stayed picked up after the drag ended"
            # And the two gestures still coexist: the buttons were the point of
            # "on top of", not a thing this replaced.
            page.evaluate("skipAudio(-15)")
            page.wait_for_timeout(300)
            assert page.evaluate("() => FamAudio.position()") < after, \
                "the 15-second button stopped working once the bar could be dragged"
            page.evaluate("goBack()")
            page.wait_for_timeout(400)

        def explores_bar_scrubs_without_swiping():
            """The bar in Explore seeks, and does not deal the next card.

            Explore listens for swipes on an ancestor of its bar, so without
            the guard in makeScrubbable a drag along the bar is both a seek and
            a swipe - and the episode you were aiming at is gone.
            """
            page.evaluate("openExplore()")
            page.wait_for_selector("#screen-explore.active", timeout=10000)
            # Coming back to the tab keeps the listener's place but does not
            # resume - setTab stops playback - so press play the way they
            # would, then let enough audio arrive to have somewhere to seek to.
            page.evaluate("if(!FamAudio.isActive()) reelTogglePlay();")
            page.wait_for_timeout(4500)
            was = page.text_content("#reelTitle")
            before = page.evaluate("() => FamAudio.position()")
            box = page.eval_on_selector(
                "#screen-explore .reel-progress",
                "e => { var r = e.getBoundingClientRect();"
                " return {x: r.x, y: r.y, w: r.width}; }")
            page.mouse.move(box["x"] + 3, box["y"] + 1)
            page.mouse.down()
            page.mouse.move(box["x"] + box["w"] * 0.9, box["y"] + 1, steps=8)
            assert page.query_selector("#screen-explore .reel-progress.scrubbing"), \
                "the reel bar did not say it had been picked up"
            page.mouse.up()
            page.wait_for_timeout(500)
            assert page.text_content("#reelTitle") == was, \
                "dragging the bar swiped to the next episode"
            after = page.evaluate("() => FamAudio.position()")
            assert after > before + 0.5, \
                f"dragging the reel bar did not move playback ({before:.2f} -> {after:.2f})"
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def dailyfam():
            ensure_account()
            page.evaluate("openPlayFAM()")
            page.wait_for_selector(".mix-card", timeout=10000, state="attached")
            assert page.eval_on_selector_all(".mix-card", "e => e.length") >= 1

        def a_playlist_plays_through_and_then_stops():
            """§134. "Play all" used to play the first episode and stop, and
            the What's next popup then counted down into a recommendation. A
            playlist now plays every episode once, in order, with a skip on
            the player - and at the end nothing else starts: the screen says
            which playlist is done for today.

            Driven by the skip button rather than by waiting out each episode,
            because both paths go through the same `advanceMix` and a fixture
            episode's length is not what is being checked.
            """
            ensure_account()
            page.evaluate("openPlayFAM()")
            page.wait_for_selector(".mix-card", timeout=10000, state="attached")
            page.evaluate("() => openMix(mixes[0].id)")
            page.wait_for_timeout(400)
            name, count = page.evaluate(
                "() => [mixById(currentMixId).name, mixById(currentMixId).items.length]")
            assert count >= 2, f"the fixture mix has {count} episode(s); need two"
            page.evaluate("playMix()")
            page.wait_for_timeout(2500)
            assert page.evaluate("() => mixPlay && mixPlay.order.length") == count, \
                "Play all did not queue the whole playlist"
            assert page.eval_on_selector("#mixSkipBtn", "e => !e.hidden"), \
                "no skip-to-next on the DailyFAM player"
            for step in range(1, count):
                page.evaluate("skipMixItem()")
                page.wait_for_timeout(1200)
                assert page.evaluate("() => mixPlay && mixPlay.pos") == step, \
                    f"skip did not move to episode {step + 1}"
            page.evaluate("skipMixItem()")
            page.wait_for_timeout(600)
            assert page.evaluate(
                "() => document.getElementById('mixDoneOverlay')"
                ".classList.contains('active')"), "the end of the playlist was not shown"
            said = page.text_content("#mixDoneH") or ""
            assert name in said and "for today" in said, f"the end screen said {said!r}"
            assert not page.evaluate(
                "() => document.getElementById('nextUpOverlay')"
                ".classList.contains('active')"), "What's next ran after a playlist"
            assert page.evaluate("() => mixPlay === null"), "the queue kept going"
            assert page.eval_on_selector("#mixSkipBtn", "e => e.hidden"), \
                "the skip button outlived its playlist"
            page.evaluate("closeMixDone(true)")
            page.wait_for_timeout(300)

        def the_new_mix_button_waits_for_an_account():
            """A mix is one of the things an account is *for*, so the "+" is
            not offered to somebody who cannot keep one.

            It used to be in the markup unconditionally: tapping it opened a
            naming modal, then a whole topic picker, and refused only at the
            save. A control with nothing behind it is worse than no control,
            and this one took two screens to say so. The sign-up buttons the
            locked note already draws are the way in.
            """
            page.evaluate("openPlayFAM()")
            page.wait_for_timeout(1200)
            assert page.eval_on_selector("#newMixBtn", "e => !e.hidden"), \
                "the new-mix + is hidden from a listener who has an account"
            page.evaluate("renderMixesLocked()")
            page.wait_for_timeout(200)
            assert page.eval_on_selector("#newMixBtn", "e => e.hidden"), \
                "the new-mix + is offered to a listener who cannot keep a mix"
            page.evaluate("renderMixList()")
            page.wait_for_timeout(200)
            assert page.eval_on_selector("#newMixBtn", "e => !e.hidden"), \
                "the new-mix + did not come back with the mixes"

        def a_locked_mix_list_keeps_the_topic_bank():
            """A 401 from `/api/mixes` says something about the account. It
            says nothing about `/api/topics`, which is not gated and answered
            on its own promise.

            `loadMixes` used to return on the locked branch before taking the
            bank off that promise, so `topicBank` stayed empty - and the
            picker reads an empty bank as "Could not load the topic list".
            Searching it offered nothing, which is why the (+) after a search
            had nothing to add: a fact about the listener's account, reported
            as a fact about the server.
            """
            page.evaluate(
                """() => {
                    window.__realFetch = window.fetch;
                    window.fetch = function (input, init) {
                        var url = typeof input === "string"
                                ? input : ((input && input.url) || "");
                        if (url.indexOf("/api/mixes") === 0) {
                            return Promise.resolve(
                                new Response("{}", { status: 401 }));
                        }
                        return window.__realFetch(input, init);
                    };
                    topicBank = [];
                    pickerSelection = [];
                }""")
            try:
                page.evaluate("openPlayFAM()")
                page.wait_for_timeout(1000)
                assert page.query_selector("#screen-playfam .locked-note"), \
                    "the stubbed 401 did not reach the locked branch"
                assert page.evaluate("topicBank.length") > 0, \
                    "a locked mix list emptied the topic bank"
                page.evaluate("openMixPicker('smoke')")
                page.wait_for_timeout(250)
                page.fill("#pickerSearch", "a topic nobody has in the bank")
                page.wait_for_timeout(350)
                body = page.text_content("#pickerBody")
                assert "Could not load the topic list" not in body, \
                    "the picker blamed the topic list for the account gate"
                assert page.query_selector(".typed-offer"), \
                    "searching the picker offered nothing to add"
            finally:
                # An arrow function with no return value: `window.fetch = ...`
                # as an expression hands Playwright the function itself to
                # serialise, and the failure it raises names fetch, so it
                # reads exactly like the thing this check is testing.
                page.evaluate("() => { window.fetch = window.__realFetch; }")
                page.evaluate("() => { pickerSelection = []; }")
                page.evaluate("openPlayFAM()")
                page.wait_for_timeout(1000)

        def the_search_page_opens_on_the_length_it_will_generate():
            """The number on the search chip and the number its own menu ticks
            are one setting, so they have to be one answer.

            They were not: `selectedLengthMinutes` was 2 and the markup
            printed "3 min" in five places, so search opened reading three
            minutes, its own menu showed two ticked, and a question typed
            without touching either generated two.

            The playback pills are deliberately not checked here. They name
            the length of the episode that is *playing*, which is a different
            question and may honestly differ from the default.
            """
            want = page.evaluate("selectedLengthMinutes")
            page.evaluate("setTab('home')")
            page.wait_for_timeout(300)
            chip = page.text_content("#lengthVal").strip()
            assert chip.startswith("%d min" % want), \
                f"the search chip reads {chip!r}, the setting is {want} min"
            for element_id in ("gcLengthVal", "lengthModalVal"):
                shown = page.text_content("#" + element_id).strip()
                assert shown == "%d min" % want, \
                    f"#{element_id} reads {shown!r}, the setting is {want} min"
            # And the menu ticks the one the chip names, which is the pair
            # that disagreed.
            page.evaluate("openLengthMenu()")
            page.wait_for_timeout(250)
            ticked = page.evaluate(
                """() => {
                    var rows = document.querySelectorAll('.sheet-item');
                    for (var i = 0; i < rows.length; i++) {
                        if (rows[i].textContent.indexOf('\u2713') !== -1) {
                            return rows[i].textContent;
                        }
                    }
                    return "";
                }""")
            page.evaluate("closeSheet()")
            page.wait_for_timeout(200)
            assert ticked.strip().startswith("%d min" % want), \
                f"the menu ticks {ticked!r}, the chip says {chip!r}"

        def picker():
            """Typing a topic into a mix and tapping it puts it in the mix.

            This used to stop at "the offer is on screen", which is the half
            that worked: the row rendered, the tap landed, and nothing
            happened, because two top-level functions in one script shared the
            name `addTypedTopic` and the catalogue's copy won. Asserting a
            control exists is not asserting it does anything - so the click
            and its effect are checked here, on the screen, the way a listener
            meets it."""
            page.evaluate("document.querySelectorAll('.mix-card')[0].click()")
            page.wait_for_timeout(400)
            page.evaluate("editMixTopics()")
            page.wait_for_selector("#screen-mixpicker.active .mix-topic",
                                   timeout=10000, state="attached")
            typed = "a topic nobody has in the bank"
            before = page.evaluate("pickerSelection.length")
            page.fill("#pickerSearch", typed)
            page.wait_for_timeout(300)
            offer = page.query_selector(".typed-offer")
            assert offer, "typing offers no way to add it"

            offer.click()
            page.wait_for_timeout(300)
            assert page.evaluate("pickerSelection.length") == before + 1, \
                "tapping the add button on a typed topic kept nothing"
            assert page.evaluate(
                "pickerSelection.some(function(s){ return s && s.query === "
                + repr(typed).replace("'", '"') + "; })"), \
                "what was typed is not what was added"

            # And it is on the screen, under its own heading, with the search
            # box cleared - a selection the listener cannot see is the same
            # failure one step later. Lower-cased because `.mix-meta` is
            # uppercased in CSS and innerText reports what is rendered.
            body = page.inner_text("#pickerBody").lower()
            assert typed in body, "the typed topic is not shown in the picker"
            assert "also in this mix" in body, "the typed topic has no heading"
            assert page.eval_on_selector("#pickerSearch", "e => e.value") == "", \
                "adding a typed topic left the search box full"
            # Counted in briefings, which is what a narrowed subject makes
            # several of (§136).
            chosen = page.evaluate("pickerPayload().length")
            assert f"{chosen} briefing" in page.inner_text("#pickerCount"), \
                "the count did not notice the topic"

            # A subject to follow is the other half of the same screen.
            page.evaluate("document.querySelectorAll('#pickerBody .mix-topic')"
                          "[document.querySelectorAll('#pickerBody .mix-topic').length - 1].click()")
            page.wait_for_timeout(300)
            assert page.evaluate("pickerSelection.length") == before + 2, \
                "tapping a subject to follow kept nothing"

        def a_new_mix_follows_narrowed_subjects():
            """The §136 flow end to end: name it on its own screen, follow NFL,
            narrow it to the Eagles and keep the whole league too, find a team
            by searching for it, save - and land in a mix whose rows are the
            briefings chosen, with recommendations under them that are
            subjects to follow and never something already followed.

            Every step is a screen the listener meets, so every step is
            asserted on the screen rather than in a variable alone."""
            page.evaluate("openPlayFAM()")
            page.wait_for_selector(".mix-card", timeout=10000, state="attached")
            page.evaluate("openNewMix()")
            page.wait_for_selector("#screen-newmix.active", timeout=5000)
            assert page.eval_on_selector("#nmCreate", "e => e.disabled"), \
                "Create is offered before the mix has a name"
            name = "Smoke %d" % int(time.time() * 1000 % 100000)
            page.fill("#nmInput", name)
            page.evaluate("onNewMixName()")
            assert not page.eval_on_selector("#nmCreate", "e => e.disabled"), \
                "a named mix still cannot be created"
            assert page.text_content("#nmPreview").strip() == name, \
                "the folder preview does not show the name"
            before = page.evaluate("mixes.length")
            page.click("#nmCreate")
            page.wait_for_selector("#screen-mixpicker.active", timeout=5000)
            assert page.evaluate("mixes.length") == before, \
                "Create made the mix before a topic was chosen"
            # Back keeps the name: the naming screen is step one of two.
            page.evaluate("cancelMixPicker()")
            page.wait_for_selector("#screen-newmix.active", timeout=5000)
            assert page.eval_on_selector("#nmInput", "e => e.value") == name, \
                "going back from the topics lost the name"
            page.click("#nmCreate")
            page.wait_for_selector("#screen-mixpicker.active .fol-ic",
                                   timeout=10000, state="attached")
            assert page.query_selector("#pickerChips .pk-chip.on"), \
                "no interest filters over the topics"

            # Follow NFL: "anything specific?" opens on its own.
            page.evaluate("toggleMixTopic('f:nfl')")
            page.wait_for_timeout(200)
            panel = page.inner_text("#pickerBody .fol-wrap.open .focus-panel")
            assert "Anything specific in NFL?" in panel, panel[:200]
            page.click("#pickerBody .focus-sugg .focus-chip[data-v='Eagles']")
            page.wait_for_timeout(200)
            assert page.query_selector("#pickerBody .focus-all"), \
                "no way to keep all of NFL beside the Eagles"
            page.click("#pickerBody .focus-all")
            page.wait_for_timeout(200)
            assert "2 briefings" in page.inner_text("#pickerCount"), \
                page.inner_text("#pickerCount")

            # Search reaches into the specifics: people think in teams.
            page.fill("#pickerSearch", "lakers")
            page.evaluate("onPickerSearch()")
            page.wait_for_timeout(300)
            body = page.inner_text("#pickerBody")
            assert "Lakers" in body and "Basketball" in body, body[:300]
            page.click("#pickerBody .mix-topic[data-v='Lakers']")
            page.wait_for_timeout(200)
            payload = page.evaluate("pickerPayload()")
            assert payload == ["f:nfl~Eagles", "f:nfl", "f:basketball~Lakers"], payload

            page.evaluate("saveMixPicker()")
            page.wait_for_selector("#screen-mixdetail.active", timeout=8000)
            page.wait_for_timeout(400)
            rows = page.eval_on_selector_all(
                "#mixBody > .mix-topic .mix-topic-title",
                "els => els.map(e => e.textContent)")
            assert rows == ["NFL \u00b7 Eagles", "NFL", "Basketball \u00b7 Lakers"], rows
            assert "edition \u00b7" in page.inner_text("#mixBody").lower(), \
                "the edition is not dated"

            # Recommendations: five subjects, none already followed, shuffled.
            recs = page.eval_on_selector_all(
                "#mixBody .rec-list .mix-topic-title", "els => els.map(e => e.textContent)")
            assert len(recs) == 5, recs
            assert "NFL" not in recs and "Basketball" not in recs, recs
            page.click("#mixBody .rec-shuffle")
            page.wait_for_timeout(200)
            again = page.eval_on_selector_all(
                "#mixBody .rec-list .mix-topic-title", "els => els.map(e => e.textContent)")
            assert len(again) == 5 and again != recs, (recs, again)

            # A play is today's edition of the subject, narrowed to the team.
            prompt = page.evaluate("""() => {
                var real = generate, sent = null;
                generate = function(key){ sent = TOPICS[key]; };
                try { playMixItem(mixById(currentMixId).items[0], false); }
                finally { generate = real; }
                return sent && [sent.prompt, sent.title, sent.bankTopicId];
            }""")
            assert prompt, "playing a followed subject asked for nothing"
            text, title, bank = prompt
            assert "Cover only Eagles, not NFL in general" in text, text
            assert str(time.localtime().tm_year) in text and "{date}" not in text, text
            assert title.endswith("today") and bank == "", (title, bank)
            page.evaluate("openPlayFAM()")
            page.wait_for_timeout(300)

        def a_cover_is_square_and_the_avatar_is_not():
            """A mix cover goes through the avatar's own editor, square; the
            avatar must come out of it still a circle."""
            page.evaluate("openNewMix()")
            page.wait_for_selector("#screen-newmix.active", timeout=5000)
            page.evaluate(
                """() => {
                    var c = document.createElement('canvas');
                    c.width = 300; c.height = 200;
                    c.getContext('2d').fillRect(0, 0, 300, 200);
                    window.__coverPhoto = c.toDataURL('image/jpeg', 0.9);
                    openPhotoEditor(window.__coverPhoto, { square: true, px: COVER_PX,
                        title: 'Position your cover',
                        onSave: function(d){ applyCover('new', d); } });
                }""")
            page.wait_for_selector("#photoOverlay.active.cover-mode", timeout=8000)
            page.wait_for_timeout(400)
            assert page.text_content("#photoTitle").strip() == "Position your cover", \
                "the cover editor kept the avatar title"
            page.evaluate("savePhotoCrop()")
            page.wait_for_timeout(300)
            assert page.evaluate("newMixCover.indexOf('data:image/jpeg') === 0"), \
                "the crop was not kept for the new mix"
            assert page.eval_on_selector("#nmFolder", "e => e.classList.contains('has-cover')"), \
                "the folder does not show the cover"
            page.evaluate("openPhotoEditor(window.__coverPhoto)")
            page.wait_for_selector("#photoOverlay.active", timeout=8000)
            assert not page.eval_on_selector(
                "#photoOverlay", "e => e.classList.contains('cover-mode')"), \
                "the avatar editor opened square after a cover"
            assert page.evaluate("photo.onSave === null"), \
                "the avatar crop would be handed to the cover"
            page.evaluate("closePhotoEditor()")
            page.evaluate("cancelNewMix()")
            page.wait_for_timeout(300)

        def messages_sheet():
            # The sheet has to be leavable. A tab that cannot be left is the
            # bug this app already shipped once, on Explore.
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(600)
            page.click("#screen-myfam .myfam-msg-btn")
            page.wait_for_timeout(700)
            assert page.eval_on_selector(".screen.active", "e => e.id") == "screen-messages"
            page.click("#screen-messages .yf-back")
            page.wait_for_timeout(700)
            assert page.eval_on_selector(".screen.active", "e => e.id") == "screen-myfam", \
                "closing messages did not return to myFAM"

        def profile():
            page.evaluate("openProfile()")
            page.wait_for_timeout(1200)
            assert page.eval_on_selector(".screen.active", "e => e.id") == "screen-profile"
            assert page.query_selector("#screen-profile .yf-name"), "no identity block"
            assert page.eval_on_selector_all("#screen-profile .yf-shelf-tile",
                                             "e => e.length") == 2, \
                "the shelf is not public mixes and vibes"
            assert page.query_selector("#screen-profile .yf-friend.invite"), \
                "the friends row has no Invite"
            assert page.query_selector("#screen-profile .yf-msgcard"), \
                "Messages is not one entry point on the hub"
            assert page.text_content(
                "#screen-profile .tabbar .tab.active .lbl").strip() == "YourFAM", \
                "the last tab is not YourFAM"

        def location_is_collected_and_editable():
            """Where a listener says they are, on both surfaces it lives on.

            It reaches the ranker (`LOCAL_BOOST`, and the cold start's local
            question), so an editor that silently failed to save would be a
            feed quietly ranked as though nobody had a location - which looks
            from outside exactly like a location that is not worth much.

            Two things are checked rather than one: the **sign-up flow**
            carries the fields, and the **Settings row** round-trips. The
            second is the one worth the effort - it asserts the value comes
            back, not merely that a screen appeared, which is the weakness
            §111 names about a control that is on screen and inert.
            """
            # 1. The first run's identity step asks for it.
            page.evaluate("openIdentity('first-run', 'settings')")
            page.wait_for_selector("#screen-identity.active", timeout=10000)
            for field in ("identityCity", "identityRegion", "identityCountry"):
                assert page.query_selector(f"#{field}"), \
                    f"the sign-up step is missing {field}"

            # 2. Settings has a row for it, and it opens its own screen -
            #    not Edit profile, which would be the row asking one thing
            #    and the screen answering another.
            page.evaluate("openProfile()")
            page.wait_for_timeout(500)
            page.evaluate("openSettings()")
            page.wait_for_selector("#screen-settings.active", timeout=10000)
            rows = page.eval_on_selector_all(
                "#settingsBody .set-label", "els => els.map(e => e.textContent)")
            assert "Where you are" in rows, \
                f"no location row in Settings; rows were {rows}"

            page.evaluate("openLocationFromSettings()")
            page.wait_for_timeout(700)
            active = page.eval_on_selector(".screen.active", "e => e.id")
            if active != "screen-location":
                # A guest is sent to sign-up instead, which is the gate every
                # stored preference has. Nothing more to assert here.
                assert active == "screen-auth", \
                    f"the location row went to {active}"
                return

            page.fill("#locCity", "Cincinnati")
            page.fill("#locRegion", "Ohio")
            page.fill("#locCountry", "United States")
            page.evaluate("saveLocation()")
            page.wait_for_timeout(900)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-settings", "Save did not come back to Settings"

            # It came back, which is the whole point of an editor.
            page.evaluate("openLocationFromSettings()")
            page.wait_for_timeout(700)
            assert page.eval_on_selector("#locCity", "e => e.value") \
                == "Cincinnati", "the city did not survive the round trip"
            page.evaluate("closeLocation()")
            page.wait_for_timeout(400)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-settings", "the X did not return to Settings"

        def every_settings_screen_comes_back_to_settings():
            """A settings row is an editor, not the first run happening again.

            Interests and Language reuse the intro screen, and reusing the
            screen meant reusing the flow: Next chained on to the language page
            and "Start listening" ran `finishIntro`, which writes `intro: done`
            and drops the listener on myFAM. From Settings there has to be an X
            that goes back, and a Save that goes back.
            """
            page.evaluate("openProfile()")
            page.wait_for_timeout(700)
            page.evaluate("openSettings()")
            page.wait_for_selector("#screen-settings.active", timeout=10000)

            # One opener now: Language had no editor of its own worth having
            # and the page it opened is gone (§100).
            for opener in ("openInterestsFromSettings()",):
                page.evaluate(opener)
                page.wait_for_selector("#screen-intro.active", timeout=10000)
                assert not page.eval_on_selector("#introTop", "e => e.hidden"), (
                    f"{opener} gave no way out")
                assert page.query_selector("#introTop .sheet-close"), \
                    f"{opener} has no X at the top right"
                # The docked button is a save here, not a step in a setup.
                label = page.eval_on_selector(
                    "#introDockInterests[hidden] ~ .entry-dock:not([hidden])"
                    " .entry-btn, #introDockInterests:not([hidden]) .entry-btn",
                    "e => e.textContent.trim()")
                assert label == "Save", f"{opener} still says {label!r}"
                # The X comes back to Settings.
                page.evaluate("closeIntroToSettings()")
                page.wait_for_timeout(400)
                assert page.eval_on_selector(".screen.active", "e => e.id") \
                    == "screen-settings", f"the X after {opener} did not return"

            # And so does Save - the case the note is actually about, because
            # this is the one that used to end up on myFAM.
            page.evaluate("openInterestsFromSettings()")
            page.wait_for_selector("#screen-intro.active", timeout=10000)
            page.evaluate("introPrimary()")
            page.wait_for_timeout(600)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-settings", "saving interests left Settings behind"

            # Edit profile is a screen now rather than two chained modals
            # that asked for a name and then a handle with no way back
            # between them - and it closes the way every other editor does.
            page.evaluate("openIdentity('edit', 'settings')")
            page.wait_for_selector("#screen-identity.active", timeout=10000)
            assert not page.eval_on_selector("#identityTop", "e => e.hidden"), \
                "the editor gave no way out"
            assert page.query_selector("#identityTop .sheet-close"), \
                "Edit profile has no X at the top right"
            assert page.eval_on_selector("#identityNextBtn",
                                         "e => e.textContent.trim()") == "Save", \
                "the editor's docked button is not a save"
            page.evaluate("closeIdentity()")
            page.wait_for_timeout(400)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-settings", "the X on Edit profile did not return"

        def the_interests_wheel_turns_and_stays_tappable():
            """The first run's wheel: six discs orbiting "View more".

            Three things have to hold at once, and the third is the one a
            static screenshot cannot see. The discs have to *move*; their
            labels have to stay upright while they do (the ring rotates, each
            disc counter-rotates by exactly as much); and every disc has to
            stay hit-testable at its own centre the whole way round, because a
            control that moves and cannot be tapped is worse than one that
            does not move.

            `elementFromPoint` rather than `page.click`, deliberately:
            Playwright waits for an element to stop moving before it will
            click, and this one never does. That is a fact about the harness
            rather than about the interface, and asking the browser what is
            under the point answers the real question.
            """
            probe = """() => {
              var chips = Array.from(document.querySelectorAll('.intro-chip'));
              var ring = document.getElementById('orbitRing');
              var rm = new DOMMatrix(getComputedStyle(ring).transform);
              return {
                ring: Math.round(Math.atan2(rm.b, rm.a) * 180 / Math.PI),
                chips: chips.map(function(c){
                  var r = c.getBoundingClientRect();
                  var x = Math.round(r.left + r.width / 2);
                  var y = Math.round(r.top + r.height / 2);
                  var hit = document.elementFromPoint(x, y);
                  var m = new DOMMatrix(getComputedStyle(c).transform);
                  return { x: x, y: y,
                           spin: Math.round(Math.atan2(m.b, m.a) * 180 / Math.PI),
                           hit: !!hit && (hit === c || c.contains(hit)) };
                })
              };
            }"""
            page.evaluate(
                "try{ localStorage.removeItem('fam.prefs'); }catch(e){}; startEntry()")
            page.wait_for_timeout(600)
            page.evaluate("skipAccount()")
            page.wait_for_selector("#screen-intro.active .intro-chip",
                                   timeout=10000, state="attached")
            page.wait_for_timeout(300)
            before = page.evaluate(probe)
            assert len(before["chips"]) == 6, "the wheel is not six discs"
            assert all(c["hit"] for c in before["chips"]), \
                "a disc was not hit-testable at its own centre"
            # The hub is reachable too - the ring must not lie on top of it.
            hub = page.evaluate(
                """() => { var b = document.querySelector('.orbit-more')
                             .getBoundingClientRect();
                           var el = document.elementFromPoint(
                             Math.round(b.left + b.width/2),
                             Math.round(b.top + b.height/2));
                           return !!el && el.classList.contains('orbit-more'); }""")
            assert hub, "the ring is swallowing taps meant for View more"

            page.wait_for_timeout(4000)
            after = page.evaluate(probe)
            moved = [((after["chips"][i]["x"] - before["chips"][i]["x"]) ** 2
                      + (after["chips"][i]["y"] - before["chips"][i]["y"]) ** 2) ** 0.5
                     for i in range(6)]
            assert min(moved) > 8, f"the wheel is not turning: {moved}"
            # Counter-clockwise, following the arrows in the design.
            assert after["ring"] != before["ring"], "the ring did not rotate"
            # And still tappable, and still upright: each disc's own spin is
            # the exact inverse of the ring's, so the two cancel.
            assert all(c["hit"] for c in after["chips"]), \
                "a disc stopped being hit-testable once it had moved"
            for c in after["chips"]:
                assert abs(c["spin"] + after["ring"]) <= 1, (
                    f"a label is rotating with the ring: disc {c['spin']}deg "
                    f"against ring {after['ring']}deg")

            # And a tap must not tilt it. This is the bug §100 fixes: the tap
            # used to rebuild the ring, and a *new* element's animation starts
            # at zero - so a disc drawn mid-revolution counter-rotated from the
            # wrong place and sat at an angle for the rest of the turn. Four
            # seconds in is exactly when it showed.
            page.evaluate("document.querySelectorAll('.intro-chip')[0].click()")
            page.wait_for_timeout(250)
            tapped = page.evaluate(probe)
            assert len(tapped["chips"]) == 6, "the tap lost a disc"
            for c in tapped["chips"]:
                assert abs(c["spin"] + tapped["ring"]) <= 2, (
                    f"tapping tilted a label: disc {c['spin']}deg against ring "
                    f"{tapped['ring']}deg")
            assert page.eval_on_selector_all(".intro-chip.on", "e => e.length") == 1, \
                "the tap did not select anything"

            # A rebuild has to survive it too, not just a tap.
            page.evaluate("renderInterestWheel()")
            page.wait_for_timeout(250)
            rebuilt = page.evaluate(probe)
            for c in rebuilt["chips"]:
                assert abs(c["spin"] + rebuilt["ring"]) <= 2, (
                    f"a rebuilt disc came back tilted: {c['spin']}deg against "
                    f"ring {rebuilt['ring']}deg")

        def the_settings_wheel_is_the_listeners_own():
            """Two wheels, two questions - and Settings now answers with what
            this listener *chose* (§107, revising §100).

            It used to draw `interests_yours`: their most-played facets,
            topped up from their choices and then from a declared order so the
            wheel always had six discs. Three of those four sources are the
            app's answer rather than the listener's, and a screen called Your
            interests that shows a recommendation is answering a question
            nobody asked. So the filler is gone, and what fills an empty wheel
            is the hub in the middle of it - which now says "Edit/add topics"
            rather than "View more", and is the only route to that list.

            The first run is unchanged and still draws the crowd's six, which
            is the honest answer to somebody with no history.

            Both halves are checked through `renderIntro`, which is the thing
            that decides, rather than by restarting the first run - that flow
            runs once per session here and the catalogue behaviour needs it.
            """
            page.evaluate("openProfile()")
            page.wait_for_timeout(600)
            page.evaluate("openSettings()")
            page.wait_for_selector("#screen-settings.active", timeout=10000)
            rows = page.eval_on_selector_all(
                ".set-row", "e => e.map(x => x.textContent)")
            assert not any("Language" in r for r in rows), \
                f"the Language row is back in Settings: {rows}"
            # The second door to the catalogue came off with this change: the
            # hub inside the wheel is the way there now, and two rows opening
            # one screen under two different names is one of them being wrong.
            assert not any("More topics" in r for r in rows), \
                f"the More topics row is back in Settings: {rows}"
            # And the first run is not something a listener replays from here.
            assert not any("first run" in r.lower() for r in rows), \
                f"Replay the first run is back in Settings: {rows}"
            # Subscription is, though - it used to be reachable only from the
            # screen somebody sees after they have already hit a limit.
            assert any("Subscription" in r for r in rows), \
                f"there is no way to the plans from Settings: {rows}"

            # Opened the way a listener opens it, then given a known
            # selection: `openInterestsFromSettings` re-reads preferences, so
            # setting the state first would have it overwritten a moment later.
            page.evaluate("openInterestsFromSettings()")
            page.wait_for_selector("#screen-intro.active", timeout=10000)
            page.wait_for_timeout(400)
            page.evaluate("""
                introSelection = ['tech', 'sport'];
                chosenTopics = [{ id: 'my typed thing', label: 'my typed thing',
                                  typed: true }];
                introMode = 'settings';
                renderIntro();
            """)
            page.wait_for_timeout(300)
            assert not page.eval_on_selector("#introSub", "e => e.hidden"), \
                "the settings wheel does not say what it is showing"
            shown = page.eval_on_selector_all(
                ".intro-chip", "e => e.map(x => x.textContent.trim())")
            assert len(shown) == 3, f"the wheel drew {shown}, not the three chosen"
            assert "my typed thing" in shown, \
                f"a subject the listener typed is not on their own wheel: {shown}"
            crowd = page.evaluate(
                """() => (PREF_CHOICES.interests_available || [])
                       .map(function(i){ return i.short || i.label; })""")
            assert shown != crowd, "settings is still drawing the crowd's six"
            assert page.eval_on_selector("#orbitMore", "e => e.textContent.trim()") \
                == "Edit/add topics", "the hub does not offer to edit them"

            # Tapping a subject removes it. It cannot be toggled back on from
            # here - the wheel only ever shows what was chosen - so the disc
            # goes rather than changing colour.
            page.evaluate("toggleInterest('topic:my typed thing')")
            page.wait_for_timeout(250)
            after = page.eval_on_selector_all(
                ".intro-chip", "e => e.map(x => x.textContent.trim())")
            assert "my typed thing" not in after, \
                f"tapping a subject did not remove it: {after}"

            # An empty wheel is possible now, and says so rather than filling
            # itself with an answer nobody gave.
            page.evaluate("""
                introSelection = []; chosenTopics = []; renderIntro();
            """)
            page.wait_for_timeout(250)
            assert page.eval_on_selector_all(".intro-chip", "e => e.length") == 0, \
                "the wheel filled itself when the listener had chosen nothing"
            assert "Nothing chosen" in page.text_content("#introSub"), \
                "an empty wheel does not say it is empty"

            # The same screen in the other mode draws the other list, and
            # stops explaining itself.
            page.evaluate("introMode = 'first-run'; renderIntro();")
            page.wait_for_timeout(300)
            assert page.eval_on_selector("#introSub", "e => e.hidden"), \
                "the first run is explaining a wheel that needs no explaining"
            first = page.eval_on_selector_all(
                ".intro-chip", "e => e.map(x => x.textContent.trim())")
            crowd = page.evaluate(
                """() => (PREF_CHOICES.interests_available || [])
                       .map(function(i){ return i.short || i.label; })""")
            assert first == crowd, f"the first run drew {first}, not {crowd}"
            # And its one button finishes the run rather than chaining on to a
            # second page, because there is no second page.
            assert page.eval_on_selector("#introNextBtn", "e => e.textContent.trim()") \
                == "Start listening"
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)

        def mix_visibility():
            """Public/private has to be reachable, not buried in a menu - and
            it has to read the right way round.

            The switch means **private** and carries the lock. It used to mean
            public, which made the lit position the state where other people
            could see your mix, and a toggle whose on position is the less
            private one is read backwards by everybody who has ever used a
            phone."""
            page.evaluate("openPlayFAM()")
            page.wait_for_selector(".mix-card", timeout=10000, state="attached")
            page.wait_for_timeout(400)
            page.evaluate("document.querySelectorAll('.mix-card')[0].click()")
            page.wait_for_timeout(500)
            switch = page.query_selector(".mix-switch")
            assert switch, "no public/private switch inside a mix"

            def state():
                lit = "on" in (page.query_selector(".mix-switch")
                               .get_attribute("class") or "")
                word = page.text_content(".mix-vis-t").strip()
                note = page.text_content(".mix-vis-s").strip()
                shackle = page.eval_on_selector(
                    ".mix-switch span svg path", "e => e.getAttribute('d')")
                return lit, word, note, shackle

            lit, word, note, shackle = state()
            # On means private, off means public, and the word matches.
            assert (word == "Private") == lit, \
                f"the switch says {word!r} in its {'on' if lit else 'off'} position"
            if not lit:
                assert "displayed on your profile" in note.lower(), \
                    f"the public note is missing: {note!r}"
            # A closed padlock closes: its shackle path ends back at the body.
            assert shackle.rstrip().endswith("v3.1") == lit, \
                f"the lock is {'open' if lit else 'closed'} in the wrong position"

            page.click(".mix-vis")
            page.wait_for_timeout(900)
            after, word2, _note2, shackle2 = state()
            assert after != lit, "the visibility switch did not move"
            assert word2 != word, "the switch moved and the word did not"
            assert shackle2 != shackle, "the switch moved and the lock did not"

        #: Every screen a listener can control playback from. VIBE! belongs on
        #: all of them - checking two ids by name is what let the main player
        #: ship without one.
        #:
        #: `nowBar` is deliberately not here, and was: the mini bar had a VIBE
        #: button and it was taken off. It is a strip with three things
        #: competing for a thumb, and the only irreversible one of them was
        #: the one that posts to your friends.
        PLAYERS = ["screen-player", "screen-playall", "screen-explore"]

        def the_mini_bar_offers_no_vibe():
            """Removed on purpose, so an absence is a decision and not a gap."""
            assert page.query_selector("#nowBar"), "no mini bar to check"
            assert not page.eval_on_selector_all(
                "#nowBar [data-echo]", "e => e.length"), (
                "VIBE! is back on the mini bar - see PLAYERS above")

        def one_transport_for_one_episode():
            """Pause anywhere and every control agrees, including the mini bar.

            Four surfaces pause the same audio. They used to keep four
            booleans, so the mini bar could draw a pause button over a stopped
            episode - and its own button returned early the moment the episode
            finished, which is when that bar is most often the only thing on
            screen.
            """
            page.evaluate("setPlayState(false)")
            paused = page.evaluate(
                """() => ({
                     player: document.getElementById("playIcon2").innerHTML,
                     playall: document.getElementById("paPlayIcon2").innerHTML,
                     reel: document.getElementById("reelPlay").innerHTML,
                     bar: document.getElementById("nowPlay").innerHTML
                   })""")
            page.evaluate("setPlayState(true)")
            playing = page.evaluate(
                """() => ({
                     player: document.getElementById("playIcon2").innerHTML,
                     playall: document.getElementById("paPlayIcon2").innerHTML,
                     reel: document.getElementById("reelPlay").innerHTML,
                     bar: document.getElementById("nowPlay").innerHTML
                   })""")
            for key in ("player", "playall", "reel", "bar"):
                assert paused[key] != playing[key], (
                    f"{key} drew the same thing paused and playing")
            # The mini bar's button must route through the same state rather
            # than moving the audio behind everyone else's back.
            page.evaluate("toggleNowBar()")
            after = page.evaluate("""() => document.getElementById("playIcon2").innerHTML""")
            page.evaluate("setPlayState(true)")
            assert after == paused["player"], (
                "the mini bar paused without telling the player")

        def echo_button():
            page.evaluate("openExplore()")
            page.wait_for_timeout(2200)
            missing = page.evaluate(
                """(ids) => ids.filter(function(id){
                       var el = document.getElementById(id);
                       return !el || !el.querySelector("[data-echo]");
                   })""",
                PLAYERS,
            )
            assert not missing, f"no VIBE! control on: {missing}"

        def echo_state_reaches_every_player():
            """One vibe must light up all of them, not just the one tapped."""
            page.evaluate("setEchoed(true)")
            lit = page.evaluate(
                """() => Array.from(document.querySelectorAll("[data-echo]"))
                       .filter(function(el){ return el.classList.contains("echoed"); }).length"""
            )
            total = page.evaluate("""() => document.querySelectorAll("[data-echo]").length""")
            page.evaluate("setEchoed(false)")
            still = page.evaluate(
                """() => Array.from(document.querySelectorAll("[data-echo]"))
                       .filter(function(el){ return el.classList.contains("echoed"); }).length"""
            )
            assert total >= 3, f"expected an echo control on every player, found {total}"
            assert lit == total, f"only {lit} of {total} echo controls showed the echoed state"
            assert still == 0, f"{still} echo control(s) stayed lit after un-echoing"

        def loading_screen_on_a_search():
            """The listener must be able to tell the search was received.

            This is the check the old code could not have passed: the overlay
            was chosen by screen name and the home screen's id did not exist,
            so pressing search showed the search page again and nothing else.
            """
            page.evaluate("setTab('home')")
            page.wait_for_timeout(300)
            assert not page.evaluate(
                """() => document.getElementById("famLoading").classList.contains("active")"""
            ), "the loading screen was showing before anything was asked for"

            # Watched rather than sampled. The screen is up from the tap until
            # audio arrives, held for at least GEN_MIN_VISIBLE_MS - and the
            # preview's audio arrives almost at once, so it is on screen for
            # about that long and no longer. This used to look once, 400ms
            # after the tap: a 50ms margin that a loaded machine overshot, and
            # the check then reported a missing screen that had been shown and
            # correctly taken down. An observer installed before the tap sees
            # it come and go whatever the timing.
            page.evaluate("""() => {
                var el = document.getElementById("famLoading");
                var seen = window.__loadingSeen = { shown: 0, hidden: 0, held: 0, status: "" };
                if (window.__loadingWatch) window.__loadingWatch.disconnect();
                window.__loadingWatch = new MutationObserver(function () {
                    var on = el.classList.contains("active");
                    if (on && !seen.shown) {
                        seen.shown = performance.now();
                        seen.status = document.getElementById("famLoadingStatus").textContent;
                    } else if (!on && seen.shown && !seen.hidden) {
                        seen.hidden = performance.now();
                        // Against the page's own clock, not this callback's:
                        // an observer runs only once the tap's synchronous
                        // work is done, tens of ms after the screen went up,
                        // so its own stamps under-read how long it was held.
                        seen.held = Date.now() - genShownAt;
                    }
                });
                window.__loadingWatch.observe(el, { attributes: true, attributeFilter: ["class"] });
            }""")
            page.fill("#searchInput", "what happened with the fed today")
            page.evaluate("runSearch()")
            try:
                page.wait_for_function("() => window.__loadingSeen.shown > 0", timeout=5000)
            except Exception:
                raise AssertionError("pressing search showed no loading screen")
            # The preview's audio is immediate, so the screen comes down on
            # its floor; give it that long and check the floor held. A server
            # that is still writing just leaves it up, which is not a failure.
            try:
                page.wait_for_function("() => window.__loadingSeen.hidden > 0", timeout=3000)
            except Exception:
                pass
            seen = page.evaluate("() => window.__loadingSeen")
            if seen["hidden"]:
                # The floor that stops a cache hit reading as a flicker.
                held = seen["held"]
                floor = page.evaluate("GEN_MIN_VISIBLE_MS")
                assert held >= floor, (
                    f"the loading screen was up for {held:.0f}ms, under its "
                    f"{floor}ms floor - that is a flicker, not a wait")
            page.evaluate("window.__loadingWatch.disconnect()")

            status = (seen["status"] or page.text_content("#famLoadingStatus") or "")
            assert status.strip(), "the loading screen said nothing about what it was doing"
            # PROBLEMS.md 55: the wait names itself. A brand animation that
            # replaced that line would be the filler problem in a nicer font.
            #
            # 82 added the first of these: episode intelligence deliberately
            # put seconds back in front of the first word, so the line now
            # moves through understanding, retrieval and writing in the order
            # they happen rather than claiming audio has already started.
            assert ("asking" in status or "Writing" in status
                    or "sources" in status
                    or "sample script" in status or "rejected" in status), (
                f"the loading screen does not say what it is waiting for: {status!r}"
            )
            page.evaluate("clearGenOverlay()")
            page.wait_for_timeout(200)
            assert not page.evaluate(
                """() => document.getElementById("famLoading").classList.contains("active")"""
            ), "the loading screen did not go away"

        def one_tap_is_one_request():
            """One episode, one /api/audio - however many times it is tapped.

            Production answered ordinary playback with 429 (PROBLEMS.md 70).
            The server side of that is fixed and tested in pytest; this is the
            other half. A duplicate request is not free even now: it is a
            second stream the server has to prime, and before the fix each one
            also spent an episode of a free listener's daily allowance. The
            player's own topic row invites exactly this - it says "tap to
            generate new episode" - and a listener watching the honest wait
            taps it again.
            """
            page.evaluate("stopSpeech(); clearGenOverlay()")
            page.evaluate("""() => {
                window.__audioCalls = [];
                if (!window.__countingFetch) {
                    window.__countingFetch = true;
                    var inner = window.fetch;
                    window.fetch = function (input, init) {
                        var url = typeof input === "string" ? input : (input && input.url) || "";
                        if (url.indexOf("/api/audio") === 0) window.__audioCalls.push(url);
                        return inner(input, init);
                    };
                }
            }""")
            page.evaluate("setTab('home')")
            page.wait_for_timeout(200)
            page.fill("#searchInput", "what the evidence says about longevity")
            # All three taps in one go, because that is the case: the second
            # and third land while the first is still in flight. The preview
            # answers instantly, so pausing between them would be measuring
            # the shim rather than the guard.
            page.evaluate("runSearch(); generate('_custom'); generate('_custom');")
            page.wait_for_timeout(600)
            calls = page.evaluate("() => window.__audioCalls.length")
            assert calls == 1, (
                f"one tap on one episode sent {calls} requests to /api/audio; "
                "every one past the first is a stream the server primes for "
                "nothing, and used to be an episode off the listener's day"
            )
            # And the boundary, so the guard is not mistaken for "one episode,
            # ever": once audio is playing, re-tapping is a replay and goes
            # through - it costs neither a model call nor a second episode.
            page.evaluate("generate('_custom')")
            page.wait_for_timeout(400)
            again = page.evaluate("() => window.__audioCalls.length")
            assert again == 2, (
                f"re-tapping an episode that is already playing sent {again - 1} "
                "request(s); a replay costs nothing and must not be blocked")
            page.evaluate("stopSpeech(); clearGenOverlay()")
            page.wait_for_timeout(150)

        def limit_screen_offers_an_upgrade():
            """Reaching a limit says which limit, and opens a way past it.

            The tier system ships switched off, so no amount of tapping in the
            preview will produce a refusal - the screen is driven from the
            server's verdict, and this feeds it the verdict a refused request
            carries. That is the honest way to check a screen you cannot reach
            yet: the shape of the data is the contract, and `quotas.Verdict`
            is where it comes from.
            """
            page.evaluate("""() => showLimitReached({
                allowed: false, resource: "episode", tier: "free", window: "day",
                used: 5, limit: 5, unlimited: false, remaining: 0,
                resets_at: (Date.now() / 1000) + 3600,
                service: "searches",
                title: "You've reached your daily limit for searches",
                message: "That is all 5 of your searches for today. You get more at 00:00 UTC. A bigger plan lifts the limit."
            })""")
            page.wait_for_timeout(200)
            assert page.evaluate(
                """() => document.getElementById("limitOverlay").classList.contains("active")"""
            ), "reaching a limit showed nothing"

            title = page.text_content("#limitTitle") or ""
            assert "daily limit for searches" in title, (
                f"the limit screen does not name what they were doing: {title!r}")
            body = page.text_content("#limitBody") or ""
            assert "5" in body and "more at" in body, (
                f"the limit screen does not say the number or when it comes back: {body!r}")
            # The server counts windows in UTC and its sentence says so, so the
            # localised line has to say whose clock it is or the card shows one
            # fact as two times.
            when = page.text_content("#limitReset") or ""
            assert "in your time" in when, (
                f"the reset line does not say whose clock it is: {when!r}")
            # A limit with no way past it is a dead end; the whole point of the
            # screen is the next tap.
            assert page.evaluate(
                """() => !!document.querySelector('#limitOverlay .modal-btn.primary')"""
            ), "the limit screen offers no way to upgrade"

            page.evaluate("openPlansFromLimit()")
            page.wait_for_timeout(500)
            assert not page.evaluate(
                """() => document.getElementById("limitOverlay").classList.contains("active")"""
            ), "the limit screen stayed up behind the plans"
            assert page.evaluate(
                """() => document.getElementById("plansOverlay").classList.contains("active")"""
            ), "See plans opened nothing"

            rows = page.evaluate("""() => document.querySelectorAll("#plansList .plan-row").length""")
            assert rows >= 2, f"the plans sheet listed {rows} plan(s)"
            assert page.evaluate(
                """() => !!document.querySelector("#plansList .plan-row.current")"""
            ), "the plans sheet does not say which plan they are on"
            # Said out loud, not discovered by tapping: there is no checkout.
            note = page.text_content("#plansList .plan-note") or ""
            assert "not switched on yet" in note, (
                f"the plans sheet does not say upgrading is unavailable: {note!r}")

            page.evaluate("closePlans()")
            page.wait_for_timeout(150)

        def loading_screen_covers_every_surface():
            """One screen, not one per tab. Four overlays chosen by id is how
            the home screen ended up with none."""
            count = page.evaluate(
                """() => document.querySelectorAll(".fam-loading").length"""
            )
            assert count == 1, f"expected one loading screen, found {count}"
            leftovers = page.evaluate(
                """() => document.querySelectorAll(".generating").length"""
            )
            assert leftovers == 0, f"{leftovers} old per-screen overlay(s) survive"

        def explore():
            page.evaluate("openExplore()")
            page.wait_for_timeout(2500)
            first = page.text_content("#reelTitle")
            assert first and "Loading" not in first, f"reel never loaded ({first!r})"
            page.wait_for_timeout(2000)
            assert page.evaluate("FamAudio.position()") > 0, "audio never started"
            # §134: the corner is the episode's play count, never how many
            # cards this session has swiped - and the thumbs are on the card.
            corner = page.text_content("#reelCount") or ""
            assert re.fullmatch(r"[\d,]+ plays?", corner), \
                f"the corner reads {corner!r}, not a play count"
            assert page.eval_on_selector_all(
                "#reelLike, #reelDislike, #reelVibeN", "e => e.length") == 3
            page.evaluate("nextReel()")
            page.wait_for_timeout(1500)
            assert page.text_content("#reelTitle") != first, "swipe did not advance"

        print(f"smoke test: {target.name}")
        check("The first run asks, then lets you in", first_run_asks_before_it_shows_the_app)
        check("No weekly recap pops up", no_weekly_recap_pops_up)
        check("myFAM renders a rail per signal", myfam)
        check("a live story tile shows its angle", live_story_tiles_show_their_angle)
        check("a card describes the episode, not the feed",
              no_card_describes_the_feed_instead_of_the_episode)
        check("a cold start's rail does not claim to be personal",
              a_cold_start_rail_does_not_claim_to_be_personal)
        check("Go Deeper titles are not cut off", go_deeper_titles_fit)
        check("Go Deeper waits for a new listener", go_deeper_waits_for_a_new_listener)
        check("myFAM's header stays put while the rails scroll", myfam_header_stays_put)
        check("The loading screen can be cancelled", the_loading_screen_can_be_cancelled)
        check("Messages show faces and typing", messages_show_faces_and_typing)
        check("A file can be attached to a search", attachments)
        check("Searching shows the loading screen", loading_screen_on_a_search)
        check("One tap sends one request", one_tap_is_one_request)
        check("A limit leads to the plans", limit_screen_offers_an_upgrade)
        check("One loading screen serves every surface", loading_screen_covers_every_surface)
        check("Save for Later lists the shelf", save_for_later_lists_the_shelf)
        check("The shelf comes back to where it was opened from",
              the_shelf_comes_back_to_where_it_was_opened_from)
        check("The photo editor crops what it shows",
              the_photo_editor_crops_what_it_shows)
        check("Saving is a toggle on every player", saving_is_a_toggle_on_every_player)
        check("The player names its four icons", the_player_names_its_four_icons)
        check("An episode is titled by what it is about",
              an_episode_is_titled_by_what_it_is_about)
        check("A friend's vibe is named on an Explore card",
              a_friends_vibe_is_named_on_an_explore_card)
        check("Friends navigation comes back to your own profile",
              friends_navigation_comes_back_to_your_own_profile)
        check("A friend's profile shows what they published",
              a_friends_profile_shows_what_they_published)
        check("A new follower is announced and can be followed back",
              a_new_follower_is_announced_and_can_be_followed_back)
        check("Edit profile is a screen with everything on it",
              edit_profile_is_a_screen_with_everything_on_it)
        check("Live captions show the script being read",
              live_captions_show_the_script_being_read)
        check("The sources cluster shows three in the corner",
              the_sources_cluster_shows_three_in_the_corner)
        check("Tapping the player generates nothing",
              tapping_the_player_generates_nothing)
        check("An episode can be shared outside FAM",
              an_episode_can_be_shared_outside_fam)
        check("Messages is its own screen, and the hub keeps its shape",
              your_fam_is_messages_and_only_messages)
        check("What's next offers four with a countdown",
              whats_next_offers_four_and_counts_down)
        check("The bar can be dragged to seek", the_bar_can_be_dragged_to_seek)
        check("The account gate reads as a choice", the_account_gate_reads_as_a_choice)
        check("The profile tab is a door until there is an account",
              the_profile_tab_is_a_door_until_there_is_an_account)
        check("An account gate opens the real sign-up screen",
              an_account_gate_opens_the_real_sign_up_screen)
        check("Edit profile chooses up to five interests",
              edit_profile_chooses_up_to_five_interests)
        check("DailyFAM lists mixes", dailyfam)
        check("A playlist plays through, then stops", a_playlist_plays_through_and_then_stops)
        check("The new-mix + waits for an account",
              the_new_mix_button_waits_for_an_account)
        check("A locked mix list keeps the topic bank",
              a_locked_mix_list_keeps_the_topic_bank)
        check("Search opens on the length it will generate",
              the_search_page_opens_on_the_length_it_will_generate)
        check("picker offers a typed topic", picker)
        check("A new mix follows narrowed subjects", a_new_mix_follows_narrowed_subjects)
        check("A mix cover is square and the avatar is not",
              a_cover_is_square_and_the_avatar_is_not)
        check("Explore plays and advances", explore)
        check("Explore's bar scrubs without swiping", explores_bar_scrubs_without_swiping)
        check("Messages opens and closes", messages_sheet)
        check("YourFAM renders identity, friends, messages and shelf", profile)
        check("The interests wheel turns and stays tappable",
              the_interests_wheel_turns_and_stays_tappable)
        check("The settings wheel is the listener's own",
              the_settings_wheel_is_the_listeners_own)
        check("Mix visibility can be toggled", mix_visibility)
        check("Every settings screen comes back to Settings",
              every_settings_screen_comes_back_to_settings)
        check("Location is collected at sign-up and editable after",
              location_is_collected_and_editable)
        check("The interest catalogue is the whole list",
              the_interest_catalogue_is_the_whole_list)
        check("The catalogue saves what was chosen",
              the_catalogue_saves_what_was_chosen)
        check("The catalogue search adds what it did not find",
              the_catalogue_search_can_add_what_it_did_not_find)
        check("The app opens on the front door, not on myFAM",
              the_app_opens_on_the_front_door_rather_than_flashing_myfam)
        check("A message arrives without being asked for",
              a_message_arrives_without_being_asked_for)
        check("A sent message appears before the server answers",
              a_sent_message_appears_before_the_server_answers)
        check("An interest chip opens its topic",
              an_interest_chip_opens_its_topic)
        check("A phone number says which country",
              a_phone_number_says_which_country)
        check("VIBE! is on every real player", echo_button)
        check("The mini bar offers no VIBE", the_mini_bar_offers_no_vibe)
        check("One transport for one episode", one_transport_for_one_episode)
        check("VIBE! state reaches every player", echo_state_reaches_every_player)

        if errors:
            failures.append(f"page errors: {errors}")
            print(f"  FAIL  page errors: {errors}")
        browser.close()

    if failures:
        print(f"\n{len(failures)} check(s) failed", file=sys.stderr)
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
