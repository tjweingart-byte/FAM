"""Drive the built preview in a real browser, with no server at all.

This is the check that catches what pytest cannot: a tab that renders nothing,
a feed that throws, a reel that will not advance, audio that never starts. All
three Explore bugs were found this way by hand; this runs it every push.

    python tools/smoke_preview.py [path-to-preview.html]
"""
from __future__ import annotations

import os
import pathlib
import sys

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

        def nothing_pops_up_on_a_new_week():
            """The weekly recap popup is gone at the owner's direction, and
            its shelf is myFAM's "What you missed last week" rail.

            Checked as an absence because the failure it guards is the popup
            coming back: a recap that fires on the first open of a new week is
            an interruption in front of an app somebody opened to listen to
            something, and a rail is not."""
            page.wait_for_timeout(1200)
            assert not page.query_selector("#recapOverlay"), \
                "the weekly recap popup came back"
            overlays = page.eval_on_selector_all(
                ".modal-overlay.active", "e => e.map(x => x.id)")
            assert overlays == [], f"something popped up unasked: {overlays}"

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
            """A live tile says what it is about; a bank tile says why it is
            there.

            The angle is the whole of what the story pool buys a listener -
            "two cables in the Red Sea were reported damaged this week" rather
            than "because of what you have played" - and it is one `if` in
            `seedWhy` away from never being drawn.

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
                    "a live story's angle never reached its card - seedWhy "
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
            clipped = page.evaluate(
                """(xs) => {
                    var el = document.querySelector(".gd-card-title");
                    var original = el.textContent;
                    var bad = xs.filter(function(x){
                        el.textContent = x;
                        return el.scrollHeight > el.clientHeight + 1;
                    });
                    el.textContent = original;
                    return bad;
                }""",
                titles,
            )
            assert not clipped, f"Go Deeper tile cuts these titles off: {clipped}"

        def go_deeper_fills_for_a_new_listener():
            """Four tiles even with no history - the case nobody develops in.

            Everyone testing this has threads and half-heard episodes, so the
            empty section only ever appeared for someone opening the app for
            the first time. The tiles must be real bank topics (a query to
            generate from), not placeholder text, and must not repeat what the
            rails below are already showing.
            """
            page.evaluate(
                """() => {
                    try { localStorage.clear(); } catch (e) {}
                    var real = window.fetch;
                    window.fetch = function(u, o){
                        if(String(u).indexOf("/api/godeeper") === 0){
                            return Promise.resolve({ ok: true,
                                json: function(){ return Promise.resolve({ threads: [] }); } });
                        }
                        return real(u, o);
                    };
                }"""
            )
            page.evaluate("openMyFamTab(); loadMyFamFeed()")
            page.wait_for_timeout(1800)
            cards = page.evaluate("() => goDeeperCardCache")
            assert len(cards) == 4, f"a new listener saw {len(cards)} Go Deeper tiles, not 4"
            assert all(c["kind"] == "starter" for c in cards), \
                f"expected all starters, got {[c['kind'] for c in cards]}"
            assert all(c.get("query") and c.get("topicId") for c in cards), \
                "a starter tile with no query or topic id cannot generate or be logged"
            titles = page.eval_on_selector_all(".gd-card-title", "e => e.map(x => x.textContent)")
            rails = page.eval_on_selector_all(".seed-card-title", "e => e.map(x => x.textContent)")
            repeated = sorted(set(titles) & set(rails))
            assert not repeated, f"Go Deeper repeats what the rails show: {repeated}"
            # The heading, not the right-hand slot: that slot is the length
            # control now, and the sentence about the tiles moved into the
            # kicker. The rule it protects is unchanged - a listener on their
            # first run has not left anything off.
            label = page.text_content(".gd-kicker")
            assert "left off" not in label.lower(), \
                f"told a first-run listener they left something off: {label!r}"
            # And the control that replaced it is real and independent of
            # search's. Changing it here must not move the search player's.
            before = page.eval_on_selector("#lengthVal", "e => e.textContent")
            assert page.query_selector(".gd-len"), \
                "myFAM has no episode-length control"
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
            assert "7 min" in page.text_content(".gd-len"), \
                "myFAM's length control did not take"
            assert page.eval_on_selector("#lengthVal", "e => e.textContent") == before, \
                "changing myFAM's length also changed the search player's"
            page.reload()
            page.wait_for_timeout(1200)

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
            # "Your" in type, "FAM" as the mark - the same three glyphs
            # myFAM, DailyFAM and exploreFAM all set. It was plain text.
            head = page.query_selector("#screen-messages .myfam-header h2")
            assert head, "Your FAM has no heading"
            assert "wordmark" in (head.get_attribute("class") or ""), \
                "the Your FAM heading is not set as a wordmark"
            glyphs = page.eval_on_selector_all(
                "#screen-messages .myfam-header h2 .wm-glyph,"
                " #screen-messages .myfam-header h2 .wm-a",
                "e => e.length")
            assert glyphs == 3, f"the FAM mark drew {glyphs} glyphs, not three"
            assert head.text_content().strip() == "Your", \
                "the heading still spells FAM out in type"
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
            page.evaluate("openProfile()")
            page.wait_for_selector("#screen-profile.active .pf-hub-tile",
                                   timeout=10000, state="attached")
            page.evaluate("openSavedAll()")
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

        def picker():
            page.evaluate("document.querySelectorAll('.mix-card')[0].click()")
            page.wait_for_timeout(400)
            page.evaluate("editMixTopics()")
            page.wait_for_selector("#screen-mixpicker.active .mix-topic",
                                   timeout=10000, state="attached")
            page.fill("#pickerSearch", "a topic nobody has in the bank")
            page.wait_for_timeout(300)
            assert page.query_selector(".typed-offer"), "typing offers no way to add it"

        def messages_sheet():
            # The sheet has to be leavable. A tab that cannot be left is the
            # bug this app already shipped once, on Explore.
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(600)
            page.click("#screen-myfam .myfam-msg-btn")
            page.wait_for_timeout(700)
            assert page.eval_on_selector(".screen.active", "e => e.id") == "screen-messages"
            page.click("#screen-messages .sheet-close")
            page.wait_for_timeout(700)
            assert page.eval_on_selector(".screen.active", "e => e.id") == "screen-myfam", \
                "closing messages did not return to myFAM"

        def profile():
            page.evaluate("openProfile()")
            page.wait_for_timeout(1200)
            assert page.eval_on_selector(".screen.active", "e => e.id") == "screen-profile"
            assert page.query_selector(".pf-name"), "no identity block"
            assert page.eval_on_selector_all(".pf-echo", "e => e.length") > 0, "no echoes"
            assert page.eval_on_selector_all(".pf-art b", "e => e.length") > 0, "no public mixes"
            assert page.query_selector(".pf-headline"), "no my-FAM-is-your-FAM headline"

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

            # Every modal a settings row opens closes the same way.
            page.evaluate("editIdentity()")
            page.wait_for_timeout(400)
            assert page.query_selector("#modalOverlay.active .modal-x"), \
                "the name modal has no X"
            page.evaluate("closeModal()")
            page.wait_for_timeout(300)
            assert page.eval_on_selector(".screen.active", "e => e.id") \
                == "screen-settings", "closing the name modal left Settings"

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
            """Two wheels, two questions (§100). Settings shows what this
            listener listens to, and says so; the first run shows what
            everybody plays, and says nothing because there is nothing yet to
            say. The language page is gone from both.

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

            page.evaluate("openInterestsFromSettings()")
            page.wait_for_selector("#screen-intro.active .intro-chip",
                                   timeout=10000, state="attached")
            page.wait_for_timeout(300)
            assert not page.eval_on_selector("#introSub", "e => e.hidden"), \
                "the settings wheel does not say what it is showing"
            assert page.eval_on_selector_all(".intro-chip", "e => e.length") == 6
            shown = page.eval_on_selector_all(
                ".intro-chip", "e => e.map(x => x.textContent.trim())")
            yours = page.evaluate(
                """() => (PREF_CHOICES.interests_yours || [])
                       .map(function(i){ return i.short || i.label; })""")
            assert shown == yours, f"settings drew {shown}, not {yours}"

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

            page.fill("#searchInput", "what happened with the fed today")
            page.evaluate("runSearch()")
            page.wait_for_timeout(400)
            assert page.evaluate(
                """() => document.getElementById("famLoading").classList.contains("active")"""
            ), "pressing search showed no loading screen"

            status = page.text_content("#famLoadingStatus") or ""
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
            page.evaluate("nextReel()")
            page.wait_for_timeout(1500)
            assert page.text_content("#reelTitle") != first, "swipe did not advance"

        print(f"smoke test: {target.name}")
        check("The first run asks, then lets you in", first_run_asks_before_it_shows_the_app)
        check("Nothing pops up on a new week", nothing_pops_up_on_a_new_week)
        check("myFAM renders a rail per signal", myfam)
        check("a live story tile shows its angle", live_story_tiles_show_their_angle)
        check("Go Deeper titles are not cut off", go_deeper_titles_fit)
        check("Go Deeper fills for a new listener", go_deeper_fills_for_a_new_listener)
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
        check("Live captions show the script being read",
              live_captions_show_the_script_being_read)
        check("The sources cluster shows three in the corner",
              the_sources_cluster_shows_three_in_the_corner)
        check("Tapping the player generates nothing",
              tapping_the_player_generates_nothing)
        check("An episode can be shared outside FAM",
              an_episode_can_be_shared_outside_fam)
        check("Your FAM is messages and only messages",
              your_fam_is_messages_and_only_messages)
        check("What's next offers four with a countdown",
              whats_next_offers_four_and_counts_down)
        check("The bar can be dragged to seek", the_bar_can_be_dragged_to_seek)
        check("The account gate reads as a choice", the_account_gate_reads_as_a_choice)
        check("DailyFAM lists mixes", dailyfam)
        check("picker offers a typed topic", picker)
        check("Explore plays and advances", explore)
        check("Explore's bar scrubs without swiping", explores_bar_scrubs_without_swiping)
        check("Messages opens and closes", messages_sheet)
        check("Profile renders identity, mixes and echoes", profile)
        check("The interests wheel turns and stays tappable",
              the_interests_wheel_turns_and_stays_tappable)
        check("The settings wheel is the listener's own",
              the_settings_wheel_is_the_listeners_own)
        check("Mix visibility can be toggled", mix_visibility)
        check("Every settings screen comes back to Settings",
              every_settings_screen_comes_back_to_settings)
        check("The interest catalogue is the whole list",
              the_interest_catalogue_is_the_whole_list)
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
