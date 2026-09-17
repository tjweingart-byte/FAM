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
            page.evaluate("submitAuthForm()")
            page.wait_for_selector("#screen-intro.active .intro-chip",
                                   timeout=10000, state="attached")
            chips = page.eval_on_selector_all(".intro-chip", "e => e.length")
            assert chips >= 6, f"only {chips} interests offered"
            # The cap is a disabled chip, not a message after the fact.
            for i in range(7):
                page.evaluate(f"var c=document.querySelectorAll('.intro-chip')[{i}];"
                              " if(c) c.click();")
            chosen = page.eval_on_selector_all(".intro-chip.on", "e => e.length")
            assert chosen == 6, f"the six-interest cap let {chosen} through"
            assert page.query_selector(".intro-chip.full"), \
                "the seventh chip was still selectable"
            page.evaluate("introNext()")
            page.wait_for_selector("#introPageLanguage .intro-lang",
                                   timeout=10000, state="attached")
            page.evaluate("finishIntro()")
            page.wait_for_timeout(900)
            assert page.eval_on_selector(".screen.active", "e => e.id") == "screen-myfam", \
                "finishing the intro did not land in the app"

        def the_weekly_recap_pops_on_a_new_week():
            """Fixture says this week's recap is still owed, so it fires on the
            first open after the intro - and has to be dismissable."""
            page.wait_for_selector("#recapOverlay.active", timeout=10000)
            # A tile when there is a week to recap, a sentence saying so when
            # there is not. Both are correct; an empty card is not.
            assert page.text_content("#recapBody").strip(), "the recap card was blank"
            page.evaluate("closeRecap()")
            page.wait_for_timeout(400)
            assert not page.query_selector("#recapOverlay.active"), \
                "the recap could not be dismissed"

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
            # Every rail now opens its own full-length view from the card at
            # the end of it. Without a visible way through nobody finds those
            # screens, which is how Explore New became unreachable the first
            # time it came off this page.
            assert page.eval_on_selector_all(".feed-more", "e => e.length") >= 1, \
                "no rail offers a way through to its full surface"

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

        def your_fam_offers_the_recap_and_explore_new():
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(500)
            page.click("#screen-myfam .myfam-msg-btn")
            page.wait_for_timeout(600)
            tiles = page.eval_on_selector_all(".yf-tile-name", "e => e.map(x => x.textContent)")
            assert tiles == ["Weekly Recap", "Save for Later"], f"saw {tiles}"
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

        def save_for_later_lists_the_shelf_and_reaches_downloads():
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)
            page.evaluate("openSavedAll()")
            page.wait_for_selector("#screen-saved.active .sv-row",
                                   timeout=10000, state="attached")
            rows = page.eval_on_selector_all(".sv-row", "e => e.length")
            assert rows >= 2, f"the shelf showed {rows} episodes"
            # Both states of an episode on one list: saved, and saved AND held
            # on the device. Two lists would put the same episode in two places
            # and make removing it from one of them ambiguous.
            assert page.eval_on_selector_all(".sv-dl", "e => e.length") >= 1, \
                "nothing on the shelf was marked as being on this device"
            bar = page.text_content("#svDownloadBar")
            assert "OF" in bar and "FREE" in bar, \
                f"the shelf did not say how much offline room was left: {bar!r}"
            # Downloads is inside this shelf rather than beside it, because a
            # download is a *state* of a saved episode. The folder chips that
            # used to be here are gone: a shelf of a dozen things does not
            # need filing, and the one folder in it was a fixture.
            assert not page.query_selector(".sv-chip"), \
                "the folder chips came back"
            switch = page.text_content("#svSwitch")
            assert "Downloads" in switch, f"no way through to downloads: {switch!r}"
            page.click("#svSwitch")
            page.wait_for_timeout(500)
            assert page.text_content("#screen-saved .back-row h2").strip() == "Downloads", \
                "the Downloads view did not open"
            assert "All saved" in page.text_content("#svSwitch"), \
                "no way back to the whole shelf"
            page.click("#svSwitch")
            page.wait_for_timeout(400)
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

        def saving_from_the_player_asks_about_downloading():
            """The distinction the whole feature rests on. Save for later is a
            pointer and needs the network; a download is the audio on this
            device. One button that silently did both would make the limit
            arrive as a surprise."""
            page.evaluate("openMyFamTab()")
            page.wait_for_timeout(400)
            page.evaluate("showScreen('player')")
            page.evaluate("nowBarState = {query: 'why bonds move',"
                          " title: 'Bonds', minutes: 3}")
            page.evaluate("saveForLater()")
            page.wait_for_selector("#downloadOverlay.active", timeout=8000)
            # The save has already happened, so the popup says so: the only
            # question left is the download, and the old "Download?" left it
            # ambiguous whether anything had been kept at all.
            title = page.text_content("#dlTitle")
            assert "Saved" in title, f"the popup did not say the save landed: {title!r}"
            size = page.text_content("#dlSize")
            assert "MB" in size, f"the popup did not say the size: {size!r}"
            assert "no signal" in page.text_content("#dlSub").lower(), \
                "the popup did not say what downloading buys"
            # Both buttons are commitments, so there has to be a way out of
            # the question that is not the backdrop.
            assert page.query_selector("#downloadOverlay .dl-x"), \
                "the popup cannot be dismissed without choosing"
            page.click("#downloadOverlay .dl-x")
            page.wait_for_timeout(400)
            assert not page.query_selector("#downloadOverlay.active"), \
                "the X did not close the popup"
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
            assert chips == 8, f"the pickable chips are not the eight facets: {chips}"
            assert page.query_selector(".intro-more"), "no way into the catalogue"
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
            # first run lost its language page on the way out.
            page.evaluate("closeTopicCatalog()")
            page.wait_for_timeout(400)
            active = page.eval_on_selector(".screen.active", "e => e.id")
            assert active == "screen-intro", (
                f"closing the catalogue left the first run at {active}")
            assert not page.eval_on_selector("#introPageInterests", "e => e.hidden"), (
                "closing the catalogue skipped past the interests step")
            # And the run still reaches the language page from here.
            page.evaluate("introNext()")
            page.wait_for_timeout(300)
            assert not page.eval_on_selector("#introPageLanguage", "e => e.hidden"), (
                "the language step is unreachable after the catalogue")
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

        def mix_visibility():
            # Public/private has to be reachable, not buried in a menu.
            page.evaluate("openPlayFAM()")
            page.wait_for_selector(".mix-card", timeout=10000, state="attached")
            page.wait_for_timeout(400)
            page.evaluate("document.querySelectorAll('.mix-card')[0].click()")
            page.wait_for_timeout(500)
            switch = page.query_selector(".mix-switch")
            assert switch, "no public/private switch inside a mix"
            before = "on" in (switch.get_attribute("class") or "")
            page.click(".mix-vis")
            page.wait_for_timeout(900)
            after = "on" in (page.query_selector(".mix-switch").get_attribute("class") or "")
            assert after != before, "the visibility switch did not move"

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
        check("The weekly recap pops and closes", the_weekly_recap_pops_on_a_new_week)
        check("myFAM renders a rail per signal", myfam)
        check("Go Deeper titles are not cut off", go_deeper_titles_fit)
        check("Go Deeper fills for a new listener", go_deeper_fills_for_a_new_listener)
        check("A file can be attached to a search", attachments)
        check("Searching shows the loading screen", loading_screen_on_a_search)
        check("One tap sends one request", one_tap_is_one_request)
        check("A limit leads to the plans", limit_screen_offers_an_upgrade)
        check("One loading screen serves every surface", loading_screen_covers_every_surface)
        check("Save for Later lists the shelf and reaches Downloads",
              save_for_later_lists_the_shelf_and_reaches_downloads)
        check("The shelf comes back to where it was opened from",
              the_shelf_comes_back_to_where_it_was_opened_from)
        check("The photo editor crops what it shows",
              the_photo_editor_crops_what_it_shows)
        check("Saving asks about downloading",
              saving_from_the_player_asks_about_downloading)
        check("An episode can be shared outside FAM",
              an_episode_can_be_shared_outside_fam)
        check("Your FAM offers the recap and Save for Later",
              your_fam_offers_the_recap_and_explore_new)
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
        check("Mix visibility can be toggled", mix_visibility)
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
