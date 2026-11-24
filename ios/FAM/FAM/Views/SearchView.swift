import PhotosUI
import SwiftUI
import UIKit

/// searchFAM, Google's shape (`search-bar`, spec 1.2): the bookshelf top
/// left, the FAM mark a quarter of the way down, the rounded bar with the
/// mic, attach and the copper go inside its right, the chips of what is
/// attached, the Length / Voice bubbles (which never print the choice),
/// private search's lock, and the five trending searches.
///
/// Private search is a lock and nothing more (11.16 notes): open while it
/// is off, closed and copper while it is on. The page keeps its colours -
/// it used to go charcoal behind a falling blind (§282, §298), with a note
/// under the bubbles. Length goes back
/// to 1.5 whenever the app returns. "Hey FAM" (§158) is Settings' row; when
/// it hears the phrase it opens voice search over this screen.
struct SearchView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Binding var tab: Tab
    @ObservedObject var attach: AttachModel
    @ObservedObject var voice: VoiceSearchModel
    /// Watched only to know when the loading screen covers this one.
    @ObservedObject var loading: LoadingModel

    @State private var words = ""
    @State private var minutes = SearchView.defaultMinutes
    @State private var trending: [TrendingSearch] = []
    @State private var trendFetchedAt = Date.distantPast
    /// The trending searches already drawn, so only new ones arrive.
    @State private var trendBook = ArrivalBook()
    /// The bubbles saying "set" (round three, §643 `bubble-ack` B): the
    /// length's hand turns once, the voice's bars hop; under Reduce Motion
    /// the icon is copper for a second instead.
    @State private var lengthTurns: Double = 0
    @State private var voiceHop = false
    @State private var acked: String?
    @FocusState private var typing: Bool
    @State private var library = false

    /// The list again, after a second for the closing cover (presentations
    /// are 0.35 s apart): only on searchFAM, and only if nothing else took
    /// its place.
    private func catalogBack() {
        guard model.catalogReturn else { return }
        Task { @MainActor in
            // The player's shrink (340 ms) and its cover gone first.
            try? await Task.sleep(nanoseconds: 450_000_000)
            guard !library else { return }
            guard tab == .search else {
                // Somewhere else now: the way back is not this list's.
                if !model.loading.visible && !model.showPlayer { model.catalogReturn = false }
                return
            }
            if model.catalogShouldReturn() { library = true }
        }
    }
    @State private var sheet: SearchSheet?
    /// What Private search does, asked before its first turning on (§690).
    @State private var explainPrivate: SheetSpec?
    @State private var linkOpen = false
    @State private var photoPicking = false
    @State private var photoItem: PhotosPickerItem?
    @State private var filePicking = false
    /// `searchSending`: a send waiting on the speller.
    @State private var sending = false
    /// The last word-by-word fix, which one backspace puts back (`acLast`).
    @State private var lastFix: WordFix?
    /// A change this view made itself, so it is not read as typing.
    @State private var ownChange: String?
    @State private var pauseTask: Task<Void, Never>?
    /// The app went to the background since it was last in front.
    @State private var wasAway = false
    /// At the accessibility sizes the three bubbles stack, each the full
    /// width, so none of their words is cut (`large-text`).
    @Environment(\.dynamicTypeSize) private var typeSize

    /// `DEFAULT_LENGTH_MINUTES`.
    static let defaultMinutes = 1.5
    static let lengths: [Double] = [1, 1.5, 2, 3, 4, 5]
    /// `TREND_REFETCH_MS` and `TREND_SHOWN`.
    static let trendRefetch: TimeInterval = 60
    static let trendShown = 5

    private var blank: Bool { words.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    private var goShown: Bool { !blank || !attach.isEmpty }
    private var trendingShown: Bool { blank && attach.isEmpty && !trending.isEmpty }

    var body: some View {
        GeometryReader { geo in
            page(geo: geo)
        }
        // Search DailyFAM comes back once the episode it played is closed
        // or given up (`playMyFamSearch` keeps the list under the player).
        .onChange(of: model.showPlayer) { _, _ in catalogBack() }
        .onChange(of: loading.visible) { _, _ in catalogBack() }
        .screenCover(isPresented: $library) {
            CatalogView()
                .environmentObject(model)
                .famThemed()
        }
        .famSheet(item: $sheet) { kind in
            YouSheet(items: items(for: kind)).environmentObject(model)
                .famThemed()
        }
        .famSheet(item: $explainPrivate) { spec in FAMActionSheet(spec: spec) }
        .famSheet(isPresented: $linkOpen) {
            LinkModal { url in attach.addLink(url) }
                .famThemed()
        }
        .background(pickers)
        // Built the first time searchFAM is shown (a faster start), so this
        // is that first look: words left for it by Search DailyFAM before it
        // existed, then the trending questions and the search settings, one
        // after the other.
        .task {
            wireVoice()
            takePrefill(model.searchPrefill)
            await loadTrending(force: true)
            if model.voices == nil { await model.loadSearchSettings() }
        }
        .onChange(of: scenePhase) { _, phase in
            // Only the app really leaving counts (`visibilitychange`): a
            // system alert - the microphone's own question - makes the scene
            // inactive without leaving.
            if phase == .background {
                wasAway = true
                // Leaving mid-count leaves the button to press (§159).
                voice.suspend()
            }
            // Back to 1.5 on every return to the app.
            if phase == .active && wasAway {
                wasAway = false
                minutes = Self.defaultMinutes
            }
        }
        .onChange(of: tab) { _, now in
            if now == .search { Task { await loadTrending(force: false) } }
        }
        // The Length bubble and the player's Length menu are one choice
        // (`selectedLengthMinutes`).
        .onChange(of: minutes) { _, value in
            if model.searchLength != value { model.searchLength = value }
        }
        .onChange(of: model.searchLength) { _, value in
            if minutes != value { minutes = value }
        }
        .onAppear { if minutes != model.searchLength { minutes = model.searchLength } }
        .onChange(of: photoItem) { _, item in
            guard let item else { return }
            photoItem = nil
            Task {
                if let data = try? await item.loadTransferable(type: Data.self) {
                    attach.addPhoto(data)
                } else {
                    model.nowPlaying.say("That file could not be read")
                }
            }
        }
        // Search DailyFAM's "Search FAM for it": the words in the box,
        // ready to send (`searchFromMyFam`).
        .onChange(of: model.searchPrefill) { _, prefill in
            takePrefill(prefill)
        }
        .onChange(of: model.searchRestore) { _, asked in
            takeRestore(asked)
        }
    }

    private func takePrefill(_ prefill: String?) {
        guard let prefill else { return }
        model.searchPrefill = nil
        if !prefill.isEmpty { setWords(prefill) }
        tab = .search
    }

    /// The photo library and Files, each on a view of its own so neither
    /// presentation stands in the other's way.
    private var pickers: some View {
        ZStack {
            Color.clear
                .photosPicker(isPresented: $photoPicking, selection: $photoItem, matching: .images)
            Color.clear
                .fileImporter(isPresented: $filePicking, allowedContentTypes: AttachModel.fileTypes,
                              allowsMultipleSelection: false) { result in
                    if case let .success(urls) = result, let url = urls.first {
                        attach.addFile(url)
                    }
                }
        }
        .allowsHitTesting(false)
    }

    // MARK: - The page

    private func page(geo: GeometryProxy) -> some View {
        ScrollView {
            content(height: geo.size.height, width: geo.size.width)
        }
        .scrollDismissesKeyboard(.interactively)
        .overlay(alignment: .topLeading) { bookshelf }
        .background(FAMColor.background.ignoresSafeArea())
    }

    private func content(height: CGFloat, width: CGFloat) -> some View {
        VStack(spacing: 0) {
            // The web's spacer (`flex: 0 0 25%`): the mark and the bar sit a
            // quarter of the way down the screen.
            Color.clear.frame(height: max(0, (height - 20) * 0.25))
            FAMLetters(capHeight: 21.3, color: FAMColor.copper)
                .frame(height: 33)
                .frame(maxWidth: .infinity)
                .padding(.bottom, 22)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("FAM")
            bar(width: width - 36)
            if !attach.isEmpty {
                ChipFlow(spacing: 6) {
                    ForEach(attach.items) { slot in
                        AttachChip(slot: slot) { attach.remove(slot) }
                    }
                }
                .padding(.top, 9)
                .padding(.horizontal, 2)
            }
            bubbles
                .padding(.top, 12)
            if trendingShown {
                trendingList
                    .padding(.top, 16)
            }
        }
        .padding(.horizontal, 18)
        .padding(.top, 2)
        .padding(.bottom, 18)
    }

    private var bookshelf: some View {
        Button { library = true } label: {
            // The web's own bookshelf (index.html:3927), 19 pt at 1.8.
            WebIcon(path: Icons.bookshelf, size: 19, line: 1.8)
                .foregroundStyle(FAMColor.text)
                .frame(width: 34, height: 34)
                .overlay(Circle().stroke(FAMColor.borderStrong))
                .contentShape(Circle())
        }
        .buttonStyle(.press)
        .accessibilityLabel("The episode library")
        .padding(.top, 8)
        .padding(.leading, 38)
    }

    // MARK: - The bar (`.fam-input-box`)

    /// One row - the words, then mic, attach and go - until the words wrap
    /// in that shape; then the words take the bar's width and the three
    /// buttons drop to a row of their own, on the right (`.tall`). One
    /// layout either way, so the field keeps its focus as it changes.
    private func bar(width: CGFloat) -> some View {
        let tall = isTall(barWidth: width)
        let layout = tall
            ? AnyLayout(VStackLayout(alignment: .trailing, spacing: 0))
            : AnyLayout(HStackLayout(alignment: .bottom, spacing: 6))
        let radius: CGFloat = tall ? 22 : 26
        return layout {
            field
                .padding(.trailing, tall ? 12 : 0)
                .frame(maxWidth: .infinity, alignment: .leading)
            buttons
        }
        .padding(.leading, 18)
        .padding(.trailing, 6)
        .padding(.top, tall ? 8 : 6)
        .padding(.bottom, 6)
        .frame(minHeight: 52)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: radius))
        // The edge turns copper while the box has the caret (§643,
        // `.fam-input-box:focus-within`, 120 ms).
        .overlay(RoundedRectangle(cornerRadius: radius).stroke(typing ? FAMColor.copper : FAMColor.border))
        .animation(reduceMotion ? nil : .easeOut(duration: Motion.Dur.focus), value: typing)
        // `#searchBar`: the tour's "Ask anything".
        .tourTarget(.searchBar)
    }

    private var field: some View {
        TextField("", text: $words,
                  prompt: Text("FAMiliarize me about\u{2026}").foregroundColor(FAMColor.textMuted.opacity(0.8)),
                  axis: .vertical)
            .font(FAMFont.body(15))
            .foregroundStyle(FAMColor.text)
            .tint(FAMColor.copper)
            .lineSpacing(3)
            .lineLimit(1...5)
            .textInputAutocapitalization(.sentences)
            .focused($typing)
            .submitLabel(.search)
            .onSubmit { go(words) }
            .padding(.vertical, 9)
            .onChange(of: words) { old, now in typed(old, now) }
            .onChange(of: typing) { _, on in
                if on { Task { await loadTrending(force: false) } }
            }
    }

    /// The mic and the go arrow share one 40 pt slot left of attach and
    /// cross on the icon swap as the box fills or empties; attach never
    /// moves (§643's bar-focus-go C). With no speech recognition the slot
    /// is there only while go is.
    private var buttons: some View {
        HStack(spacing: 2) {
            if VoiceSearchModel.available || goShown {
                ZStack {
                    if VoiceSearchModel.available {
                        barIcon(SearchIcons.mic, label: "Search by voice") { openVoice() }
                            .modifier(SlotFace(shown: !goShown))
                    }
                    Button { go(words) } label: {
                        GoArrow(size: 13)
                            .foregroundStyle(FAMColor.ink)
                            .frame(width: 36, height: 36)
                            .background(FAMColor.copper, in: Circle())
                            .contentShape(Circle())
                    }
                    .buttonStyle(.press)
                    .padding(2)
                    .accessibilityLabel("Search")
                    .modifier(SlotFace(shown: goShown))
                }
                .frame(width: 40, height: 40)
                .animation(reduceMotion ? nil : Motion.easeSwap(Motion.Dur.swap), value: goShown)
            }
            // Always drawn (`#attachBtn`); past what one search can carry a
            // tap says so instead of opening the menu.
            barIcon(SearchIcons.attach, label: "Add a document, photo or link") { openAttach() }
        }
    }

    private func barIcon(_ path: Path, label: String,
                         action: @escaping () -> Void) -> some View {
        Button(action: action) {
            WebIcon(path: path, size: 18, line: 2)
                .foregroundStyle(FAMColor.textMuted)
                .frame(width: 40, height: 40)
                .contentShape(Circle())
        }
        .buttonStyle(.press)
        .accessibilityLabel(label)
    }

    /// Whether the words wrap in the one-row shape (`paintSearchBar`):
    /// measured in the width they would have beside the buttons.
    private func isTall(barWidth: CGFloat) -> Bool {
        guard !words.isEmpty else { return false }
        // The shared mic / go slot, then attach.
        var shown = 1
        if VoiceSearchModel.available || goShown { shown += 1 }
        let buttonsWidth = CGFloat(shown) * 40 + CGFloat(max(shown - 1, 0)) * 2
        let gap: CGFloat = shown > 0 ? 6 : 0
        let textWidth = max(barWidth - 18 - 6 - gap - buttonsWidth, 40)
        let base = UIFont(name: "Geist-Regular", size: 15) ?? UIFont.systemFont(ofSize: 15)
        let font = UIFontMetrics(forTextStyle: .body).scaledFont(for: base)
        let bounds = (words as NSString).boundingRect(
            with: CGSize(width: textWidth, height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: font], context: nil)
        return bounds.height > font.lineHeight * 1.5
    }

    // MARK: - The bubbles (`.fam-controls`)

    private var bubbles: some View {
        let layout = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(spacing: 8))
                                                  : AnyLayout(HStackLayout(spacing: 10))
        return layout {
            bubble("Length", label: "Episode length", action: { sheet = .length }) { lengthIcon }
            bubble("Voice", label: "Voice for your searches", action: { openVoiceMenu() }) { voiceIcon }
            privateLock
        }
    }

    /// `.search-bubble`: 44 tall, radius 22, surface, the border, Geist 14
    /// semibold with a muted icon; the label never shows the choice.
    private func bubble<Icon: View>(_ title: String, label: String, action: @escaping () -> Void,
                                    @ViewBuilder icon: () -> Icon) -> some View {
        Button(action: action) {
            HStack(spacing: 8) {
                icon()
                Text(title)
                    .font(FAMFont.body(14, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(typeSize.isAccessibilitySize ? 2 : 1)
                    .minimumScaleFactor(0.8)
            }
            .padding(.horizontal, 6)
            .frame(maxWidth: .infinity, minHeight: 44)
            .background(FAMColor.surface, in: Capsule())
            .overlay(Capsule().stroke(FAMColor.border))
            .contentShape(Capsule())
        }
        .buttonStyle(.press)
        .accessibilityLabel(label)
    }

    /// Length's stopwatch, its hand drawn apart so it can turn.
    private var lengthIcon: some View {
        ZStack {
            WebIcon(path: SearchIcons.lengthDial, size: 14, line: 2)
            WebIcon(path: SearchIcons.lengthHand, size: 14, line: 2)
                .rotationEffect(.degrees(lengthTurns * 360), anchor: UnitPoint(x: 0.5, y: 13.0 / 24.0))
        }
        .foregroundStyle(acked == "Length" ? FAMColor.copper : FAMColor.textMuted)
    }

    /// Voice's bars, each its own so they hop one after another.
    private var voiceIcon: some View {
        ZStack {
            ForEach(0..<SearchIcons.voiceBars.count, id: \.self) { i in
                WebIcon(path: SearchIcons.voiceBars[i], size: 14, line: 2)
                    .scaleEffect(x: 1, y: voiceHop ? 1.6 : 1)
                    .animation(reduceMotion ? nil : Motion.easeInOut(0.12).delay(Double(i) * 0.035),
                               value: voiceHop)
            }
        }
        .foregroundStyle(acked == "Voice" ? FAMColor.copper : FAMColor.textMuted)
    }

    /// A choice made from the bubble's own sheet: 150 ms after the sheet
    /// has gone, the icon acts (the hand sweeps once, 420 ms on
    /// `--ease-in-out`; the bars hop to 1.6 and back, 240 ms, 35 ms apart).
    private func ack(_ which: String) {
        if reduceMotion {
            acked = which
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { if acked == which { acked = nil } }
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            if which == "Length" {
                withAnimation(Motion.easeInOut(0.42)) { lengthTurns += 1 }
            } else {
                voiceHop = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) { voiceHop = false }
            }
        }
    }

    /// Private search (§282, §285) as a lock (`.private-bubble`, 11.16
    /// notes): a pill with the word "Private" (§690) beside the bubbles, an open padlock
    /// while it is off; closed, copper with an ink icon, while it is on.
    /// What it does is said once, in the toast (`AppModel.setPrivateSearch`).
    private var privateLock: some View {
        let on = model.privateSearch
        return Button { tapPrivateLock() } label: {
            // Open and shut cross on the icon spring (round three).
            // The lock carries its word (the owner, 11.24, §690: "add the
            // word Private on the lock too"): a pill its own width, beside
            // Length and Voice, which share what is left.
            HStack(spacing: 7) {
                IconSwap(key: on) {
                    WebIcon(path: on ? SearchIcons.lock : SearchIcons.lockOpen, size: 16, line: 2)
                        .foregroundStyle(on ? FAMColor.ink : FAMColor.textMuted)
                }
                Text("Private")
                    .font(FAMFont.body(14, .semibold))
                    .foregroundStyle(on ? FAMColor.ink : FAMColor.text)
                    .lineLimit(1)
                    .fixedSize()
            }
                .padding(.horizontal, 15)
                .frame(minHeight: 44)
                .background(on ? FAMColor.copper : FAMColor.surface, in: Capsule())
                .overlay(Capsule().stroke(on ? FAMColor.copper : FAMColor.border))
                .contentShape(Capsule())
        }
        .buttonStyle(.press)
        .accessibilityLabel(on ? "Private search is on" : "Private search")
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    /// `tapPrivateLock` (the owner's 11.24 packet, §690: "someone who
    /// doesn't [know] might not know"): the button is a lock and its word, and
    /// the first time it is tapped on this phone a sheet says in plain
    /// words what closing it does, turning it on only on a yes. After that
    /// the lock is one tap either way; turning it off never asks.
    private func tapPrivateLock() {
        let on = model.privateSearch
        if on || UserDefaults.standard.bool(forKey: Self.privateExplainedKey) {
            model.setPrivateSearch(!on)
            return
        }
        explainPrivate = SheetSpec(header: Self.privateExplanation, actions: [
            SheetAction(label: "Turn on Private search") {
                UserDefaults.standard.set(true, forKey: Self.privateExplainedKey)
                model.setPrivateSearch(true)
            }
        ])
    }

    static let privateExplainedKey = "fam.privateExplained"
    /// The web's sheet (`tapPrivateLock`), as one sentence run.
    static let privateExplanation = "Private search: search without leaving a trace. "
        + "While the lock is closed, anything you search or Go Deeper on isn\u{2019}t saved to your history, "
        + "is never shown to friends or other listeners, and doesn\u{2019}t change what FAM recommends to you. "
        + "It stays on until you tap the lock again."

    // MARK: - Trending searches (`.trend-searches`)

    /// One bubble a search, most searched first; a tap is a search that
    /// lands on an episode already written.
    private var trendingList: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Trending searches")
                .monoLabel(9, color: FAMColor.textMuted, tracking: 1.26)
                .padding(.horizontal, 4)
                .padding(.bottom, 1)
                // The label, then each search a little after the one
                // before (round three, §643 `lists-arrive` A).
                .modifier(ArriveOnce(key: "trend:label", delay: 0, book: trendBook))
            ForEach(Array(trending.prefix(Self.trendShown).enumerated()), id: \.element) { i, search in
                Button {
                    typing = false
                    model.playTrending(search, minutes: minutes)
                } label: {
                    HStack(spacing: 8) {
                        WebIcon(path: Icons.trend, size: 13)
                            .foregroundStyle(FAMColor.textMuted)
                        // One line, an ellipsis past the width (`white-space:
                        // nowrap; text-overflow: ellipsis`).
                        Text(search.label)
                            .font(FAMFont.body(12, .medium))
                            .foregroundStyle(FAMColor.text)
                            .lineLimit(1)
                            .truncationMode(.tail)
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 6)
                    .frame(minHeight: 32)
                    .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 16))
                    .overlay(RoundedRectangle(cornerRadius: 16).stroke(FAMColor.border))
                    .contentShape(RoundedRectangle(cornerRadius: 16))
                }
                .buttonStyle(.press)
                .modifier(ArriveOnce(key: "trend:" + search.label, delay: 0.04 * Double(i + 1),
                                     book: trendBook))
            }
        }
    }

    /// On opening the page and on focusing the box, at most once a minute;
    /// errors are silent and an empty list draws nothing.
    private func loadTrending(force: Bool) async {
        if !force && Date().timeIntervalSince(trendFetchedAt) < Self.trendRefetch { return }
        trendFetchedAt = Date()
        if let found = try? await model.api.trendingSearches() { trending = found }
    }

    // MARK: - Sheets

    private func items(for kind: SearchSheet) -> [YouSheetItem] {
        switch kind {
        case .length:
            // "1 min" … "5 min", the current one marked (`openLengthMenu`).
            return Self.lengths.map { value in
                YouSheetItem(label: FAMAPI.minutes(value) + " min", checked: value == minutes) {
                    minutes = value
                    ack("Length")
                }
            }
        case .voice:
            var list = [YouSheetItem(label: "Voice for your searches", group: true)]
            let selected = model.voices?.selected ?? ""
            for v in model.voices?.voices ?? [] {
                let id = v.id
                list.append(YouSheetItem(label: v.label, checked: id == selected) {
                    model.chooseVoice(id, fromSettings: false)
                    ack("Voice")
                })
            }
            return list
        case .attach:
            // The web's two rows (`openAttachMenu`). Its "Photo or document"
            // is one file input, which iOS Safari answers with its own
            // chooser; here that chooser is the next sheet.
            return [
                YouSheetItem(label: "Photo or document") { sheet = .attachSource },
                YouSheetItem(label: "Link") { linkOpen = true },
            ]
        case .attachSource:
            // The photo library and Files, the two places the web's
            // `accept=".pdf,.docx,.txt,.md,.csv,.json,image/*"` reaches.
            return [
                YouSheetItem(label: "Photo Library") { photoPicking = true },
                YouSheetItem(label: "Choose File") { filePicking = true },
            ]
        }
    }

    /// `openVoiceMenu('search')`: the bank, or the server's silence said.
    private func openVoiceMenu() {
        if let page = model.voices, !page.voices.isEmpty {
            sheet = .voice
            return
        }
        Task {
            await model.loadSearchSettings()
            if let page = model.voices, !page.voices.isEmpty {
                sheet = .voice
            } else {
                model.nowPlaying.say("No voices reported by the server")
            }
        }
    }

    /// `openAttachMenu`: the AI question first (an attachment is the
    /// listener's words), then "Photo or document" / "Link".
    private func openAttach() {
        typing = false
        if attach.full {
            // `attach=` holds 400 characters of ids, 23 of them: the server
            // refuses a search past that, so the next one is not taken.
            model.nowPlaying.say("One search can carry \(AttachModel.most) attachments")
            return
        }
        let asked = model.consent.current?.given != true
        Task {
            guard await model.consent.ensure() else { return }
            // After the consent card has gone, when there was one.
            if asked { try? await Task.sleep(nanoseconds: 300_000_000) }
            sheet = .attach
        }
    }

    // MARK: - Voice search

    private func openVoice() {
        typing = false
        model.openVoiceSearch()
    }

    /// The words voice search heard go into the box and down the same path
    /// as a typed search: the speller, the AI question, the attachments.
    private func wireVoice() {
        voice.onSend = { said in
            model.voiceSearchSent()
            tab = .search
            setWords(said)
            go(said)
        }
    }

    // MARK: - Sending (`runSearch`, `sendSearch`)

    private func go(_ text: String) {
        guard !sending else { return }
        let typed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        // Return in an empty box goes on too (`runSearch` -> `sendSearch("", "")`):
        // the web's demo episode (`generate("spacex")`).
        typing = false
        let length = minutes
        sending = true
        // The loader opens on the press (§643): the speller's wait runs
        // under its first step - once the AI question is answered and no
        // file is still reading, as the web's `openGenOverlayNow`.
        let seq = model.cancelSeq
        if !typed.isEmpty && model.consent.current?.given == true && !attach.pending {
            model.loading.openEarly()
        }
        Task {
            var query = ""
            var title = ""
            if !typed.isEmpty {
                // Asks about every unseen word, waiting 700 ms at most.
                let fixed = await model.speller.correct(typed)
                query = fixed.query
                title = fixed.title
            }
            sending = false
            // Not if the loader was cancelled meanwhile (`genCancelSeq`), or
            // they kept typing or went elsewhere.
            guard seq == model.cancelSeq,
                  words.trimmingCharacters(in: .whitespacesAndNewlines) == typed, tab == .search else {
                model.loading.closeEarly()
                return
            }
            if await model.sendSearch(query: query, title: title, minutes: length, typed: typed) {
                if words.trimmingCharacters(in: .whitespacesAndNewlines) == typed { setWords("") }
            }
        }
    }

    /// Cancel's question and attachments, back in the bar unless it already
    /// holds something new (`restoreAskedSearch`).
    private func takeRestore(_ asked: AskedSearch?) {
        guard let asked else { return }
        model.searchRestore = nil
        guard words.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, attach.isEmpty else { return }
        setWords(asked.text)
        attach.restore(asked.slots)
    }

    private func setWords(_ value: String) {
        lastFix = nil
        if value != words {
            ownChange = value
            words = value
        }
    }

    // MARK: - Word-by-word autocorrect (`acInstall`)

    private static let boundary = CharacterSet(charactersIn: " \t\n.,!?;:)")

    private static func isBoundary(_ text: String) -> Bool {
        guard let scalar = text.unicodeScalars.first else { return false }
        return boundary.contains(scalar)
    }

    private static let lastWord = try? NSRegularExpression(pattern: "([A-Za-z]+(?:'[A-Za-z]+)?)$")
    private static let partOfWord = try? NSRegularExpression(pattern: "[\\p{L}\\p{N}@#/._-]")

    /// Every change to the box: Return sends; one backspace straight after
    /// a fix puts the typed word back (never corrected again); a word just
    /// finished at the end is asked about and fixed in place; a pause asks
    /// about the word being typed. Typing in the middle is left alone.
    private func typed(_ old: String, _ now: String) {
        if let mine = ownChange, mine == now {
            ownChange = nil
            return
        }
        ownChange = nil
        let oldNS = old as NSString
        let nowNS = now as NSString
        // Return typed at the caret sends, as Enter without Shift does; a
        // paste that carries newlines only inserts them (the web's textarea),
        // so the listener can still edit it before sending.
        if let at = Self.insertion(oldNS, nowNS),
           nowNS.substring(with: NSRange(location: at, length: 1)) == "\n" {
            let flat = nowNS.replacingCharacters(in: NSRange(location: at, length: 1), with: "")
            setWords(flat)
            go(flat)
            return
        }
        if let fix = lastFix, revert(fix, oldNS, nowNS) { return }
        lastFix = nil
        askAfterPause(now)
        guard nowNS.length == oldNS.length + 1, now.hasPrefix(old) else { return }
        let typedChar = nowNS.substring(from: nowNS.length - 1)
        guard Self.isBoundary(typedChar) else { return }
        let before = nowNS.substring(to: nowNS.length - 1)
        let beforeNS = before as NSString
        guard let re = Self.lastWord,
              let m = re.firstMatch(in: before, range: NSRange(location: 0, length: beforeNS.length)) else { return }
        let word = beforeNS.substring(with: m.range)
        let start = m.range.location
        if model.speller.isReverted(word) { return }
        // Only a whole word: not part of a handle, hashtag, path or address.
        if start > 0 {
            let prev = beforeNS.substring(with: NSRange(location: start - 1, length: 1))
            if let part = Self.partOfWord,
               part.firstMatch(in: prev, range: NSRange(location: 0, length: (prev as NSString).length)) != nil {
                return
            }
        }
        let first = SpellWords.startsSentence(beforeNS.substring(to: start))
        Task {
            guard let fix = await model.speller.ask(word, first: first), fix != word else { return }
            let current = words as NSString
            let length = (word as NSString).length
            // Only if the word is still there, untouched, where it was.
            guard current.length > start + length,
                  current.substring(with: NSRange(location: start, length: length)) == word,
                  Self.isBoundary(current.substring(with: NSRange(location: start + length, length: 1)))
            else { return }
            let fixed = current.replacingCharacters(in: NSRange(location: start, length: length), with: fix)
            ownChange = fixed
            words = fixed
            lastFix = WordFix(start: start, from: word, to: fix)
        }
    }

    /// Where one character was typed into `old` to make `now` (UTF-16
    /// offset), or nil when the change was anything else (a paste, a
    /// deletion, the keyboard's own replacement).
    private static func insertion(_ old: NSString, _ now: NSString) -> Int? {
        guard now.length == old.length + 1 else { return nil }
        var i = 0
        while i < old.length && old.character(at: i) == now.character(at: i) { i += 1 }
        let restOld = old.substring(from: i)
        let restNow = now.substring(from: i + 1)
        return restOld == restNow ? i : nil
    }

    /// The backspace straight after a fix: the fix and the character after
    /// it were the end of the box, and that character went.
    private func revert(_ fix: WordFix, _ old: NSString, _ now: NSString) -> Bool {
        let toLength = (fix.to as NSString).length
        guard old.length == fix.start + toLength + 1, now.length == old.length - 1,
              old.hasPrefix(now as String),
              old.substring(with: NSRange(location: fix.start, length: toLength)) == fix.to else { return false }
        let kept = old.substring(from: old.length - 1)
        let put = old.substring(to: fix.start) + fix.from + kept
        model.speller.noteReverted(fix.from)
        lastFix = nil
        ownChange = put
        words = put
        return true
    }

    /// `AC_PAUSE_MS`: the word being typed is asked about once typing
    /// pauses, so the send that follows finds it known. A spell lookup and
    /// nothing else (`no-typing-prefetch`).
    private func askAfterPause(_ text: String) {
        pauseTask?.cancel()
        let speller = model.speller
        pauseTask = Task {
            try? await Task.sleep(nanoseconds: UInt64(SpellWords.pause * 1_000_000_000))
            if Task.isCancelled { return }
            guard let last = SpellWords.words(in: text).last, last.word.count > 2 else { return }
            _ = await speller.ask(last.word, first: last.first)
        }
    }
}

/// Which action sheet searchFAM has up.
enum SearchSheet: String, Identifiable {
    case length, voice, attach, attachSource
    var id: String { rawValue }
}

/// A word the box corrected as it was typed (`acLast`).
struct WordFix: Equatable {
    let start: Int
    let from: String
    let to: String
}

/// One face of the mic / go slot: the one not shown shrinks to .3 behind a
/// 3 pt blur and fades, and takes no taps (the icon swap's states).
private struct SlotFace: ViewModifier {
    let shown: Bool

    func body(content: Content) -> some View {
        content
            .scaleEffect(shown ? 1 : 0.3)
            .blur(radius: shown ? 0 : 3)
            .opacity(shown ? 1 : 0)
            .allowsHitTesting(shown)
            .accessibilityHidden(!shown)
    }
}
