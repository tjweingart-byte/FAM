import PhotosUI
import SwiftUI
import UIKit

/// searchFAM, Google's shape (`search-bar`, spec 1.2): the bookshelf top
/// left, the FAM mark a quarter of the way down, the rounded bar with the
/// mic, attach and the copper go inside its right, the chips of what is
/// attached, the Length / Voice / Private bubbles (which never print the
/// choice), the private note, and the five trending searches.
///
/// Private search turns this screen and its tab bar charcoal; the player,
/// the sheets and voice search stay light, as on the web. Length goes back
/// to 1.5 whenever the app returns. "Hey FAM" is not on iOS 1.0, so there
/// is no control for it (`no-dead-controls`).
struct SearchView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @Binding var tab: Tab
    @ObservedObject var attach: AttachModel
    @ObservedObject var voice: VoiceSearchModel
    /// Watched only to know when the loading screen covers this one.
    @ObservedObject var loading: LoadingModel

    @State private var words = ""
    @State private var minutes = SearchView.defaultMinutes
    @State private var trending: [TrendingSearch] = []
    @State private var trendFetchedAt = Date.distantPast
    @FocusState private var typing: Bool
    @State private var library = false
    @State private var sheet: SearchSheet?
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

    /// `DEFAULT_LENGTH_MINUTES`.
    static let defaultMinutes = 1.5
    static let lengths: [Double] = [1, 1.5, 2, 3, 4, 5]
    /// `TREND_REFETCH_MS` and `TREND_SHOWN`.
    static let trendRefetch: TimeInterval = 60
    static let trendShown = 5

    static let privateNote = "Private search is on until you turn it off. Your searches and Go Deeper episodes won\u{2019}t appear in the episode library, on your friends\u{2019} DailyFAM, in your listening history, or teach your feed."

    private var palette: SearchPalette { SearchPalette.of(model.privateSearch) }
    private var blank: Bool { words.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    private var goShown: Bool { !blank || !attach.isEmpty }
    private var trendingShown: Bool { blank && attach.isEmpty && !trending.isEmpty }

    /// The system's chrome (the status bar, the keyboard) goes dark with the
    /// charcoal (`color-scheme: dark`) only while this screen is what shows.
    private var chromeDark: Bool {
        model.privateSearch && tab == .search && !library && !linkOpen
            && !model.showPlayer && !voice.isOpen && !loading.visible
    }

    var body: some View {
        let pal = palette
        return GeometryReader { geo in
            ScrollView {
                content(pal, height: geo.size.height, width: geo.size.width)
            }
            .scrollDismissesKeyboard(.interactively)
            .overlay(alignment: .topLeading) { bookshelf(pal) }
        }
        .background(pal.background.ignoresSafeArea())
        // The charcoal comes and goes with `transition: background .25s`.
        .animation(.easeInOut(duration: 0.25), value: model.privateSearch)
        .fullScreenCover(isPresented: $library) {
            CatalogView()
                .environmentObject(model)
        }
        .sheet(item: $sheet) { kind in
            YouSheet(items: items(for: kind)).environmentObject(model)
        }
        .fullScreenCover(isPresented: $linkOpen) {
            LinkModal { url in attach.addLink(url) }
        }
        .background(pickers)
        .task {
            wireVoice()
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
        .onChange(of: chromeDark) { _, dark in model.darkChrome = dark }
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
            guard let prefill else { return }
            model.searchPrefill = nil
            if !prefill.isEmpty { setWords(prefill) }
            tab = .search
        }
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

    private func content(_ pal: SearchPalette, height: CGFloat, width: CGFloat) -> some View {
        VStack(spacing: 0) {
            // The web's spacer (`flex: 0 0 25%`): the mark and the bar sit a
            // quarter of the way down the screen.
            Color.clear.frame(height: max(0, (height - 20) * 0.25))
            FAMLetters(capHeight: 21.3, color: pal.copper)
                .frame(height: 33)
                .frame(maxWidth: .infinity)
                .padding(.bottom, 22)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("FAM")
            bar(pal, width: width - 36)
            if !attach.isEmpty {
                ChipFlow(spacing: 6) {
                    ForEach(attach.items) { slot in
                        AttachChip(slot: slot, palette: pal) { attach.remove(slot) }
                    }
                }
                .padding(.top, 9)
                .padding(.horizontal, 2)
            }
            bubbles(pal)
                .padding(.top, 12)
            if model.privateSearch {
                privateNote(pal)
                    .padding(.top, 12)
                    .padding(.horizontal, 4)
                    .transition(.opacity)
            }
            if trendingShown {
                trendingList(pal)
                    .padding(.top, 16)
            }
        }
        .padding(.horizontal, 18)
        .padding(.top, 2)
        .padding(.bottom, 18)
    }

    private func bookshelf(_ pal: SearchPalette) -> some View {
        Button { library = true } label: {
            // The web's own bookshelf (index.html:3927), 19 pt at 1.8.
            WebIcon(path: Icons.bookshelf, size: 19, line: 1.8)
                .foregroundStyle(pal.text)
                .frame(width: 34, height: 34)
                .overlay(Circle().stroke(pal.borderStrong))
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("The episode library")
        .padding(.top, 8)
        .padding(.leading, 38)
    }

    // MARK: - The bar (`.fam-input-box`)

    /// One row - the words, then mic, attach and go - until the words wrap
    /// in that shape; then the words take the bar's width and the three
    /// buttons drop to a row of their own, on the right (`.tall`). One
    /// layout either way, so the field keeps its focus as it changes.
    private func bar(_ pal: SearchPalette, width: CGFloat) -> some View {
        let tall = isTall(barWidth: width)
        let layout = tall
            ? AnyLayout(VStackLayout(alignment: .trailing, spacing: 0))
            : AnyLayout(HStackLayout(alignment: .bottom, spacing: 6))
        let radius: CGFloat = tall ? 22 : 26
        return layout {
            field(pal)
                .padding(.trailing, tall ? 12 : 0)
                .frame(maxWidth: .infinity, alignment: .leading)
            buttons(pal)
        }
        .padding(.leading, 18)
        .padding(.trailing, 6)
        .padding(.top, tall ? 8 : 6)
        .padding(.bottom, 6)
        .frame(minHeight: 52)
        .background(pal.surface, in: RoundedRectangle(cornerRadius: radius))
        .overlay(RoundedRectangle(cornerRadius: radius).stroke(pal.border))
    }

    private func field(_ pal: SearchPalette) -> some View {
        TextField("", text: $words,
                  prompt: Text("FAMiliarize me about\u{2026}").foregroundColor(pal.textMuted.opacity(0.8)),
                  axis: .vertical)
            .font(FAMFont.body(15))
            .foregroundStyle(pal.text)
            .tint(pal.copper)
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

    private func buttons(_ pal: SearchPalette) -> some View {
        HStack(spacing: 2) {
            if VoiceSearchModel.available {
                barIcon(SearchIcons.mic, pal, label: "Search by voice") { openVoice() }
            }
            if !attach.full {
                barIcon(SearchIcons.attach, pal, label: "Add a document, photo or link") { openAttach() }
            }
            if goShown {
                Button { go(words) } label: {
                    GoArrow(size: 13)
                        .foregroundStyle(FAMColor.ink)
                        .frame(width: 36, height: 36)
                        .background(pal.copper, in: Circle())
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .padding(2)
                .accessibilityLabel("Search")
            }
        }
    }

    private func barIcon(_ path: Path, _ pal: SearchPalette, label: String,
                         action: @escaping () -> Void) -> some View {
        Button(action: action) {
            WebIcon(path: path, size: 18, line: 2)
                .foregroundStyle(pal.textMuted)
                .frame(width: 40, height: 40)
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }

    /// Whether the words wrap in the one-row shape (`paintSearchBar`):
    /// measured in the width they would have beside the buttons.
    private func isTall(barWidth: CGFloat) -> Bool {
        guard !words.isEmpty else { return false }
        var shown = 0
        if VoiceSearchModel.available { shown += 1 }
        if !attach.full { shown += 1 }
        if goShown { shown += 1 }
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

    private func bubbles(_ pal: SearchPalette) -> some View {
        HStack(spacing: 10) {
            bubble("Length", icon: SearchIcons.length, iconSize: 14, pal: pal, on: false,
                   label: "Episode length") { sheet = .length }
            bubble("Voice", icon: SearchIcons.voice, iconSize: 14, pal: pal, on: false,
                   label: "Voice for your searches") { openVoiceMenu() }
            bubble("Private", icon: SearchIcons.incognito, iconSize: 15, pal: pal, on: model.privateSearch,
                   label: "Private search") { model.setPrivateSearch(!model.privateSearch) }
        }
    }

    /// `.search-bubble`: 44 tall, radius 22, surface, the border, Geist 14
    /// semibold with a muted icon; the label never shows the choice. Private
    /// when on is the pale bubble with charcoal words.
    private func bubble(_ title: String, icon: Path, iconSize: CGFloat, pal: SearchPalette, on: Bool,
                        label: String, action: @escaping () -> Void) -> some View {
        let onColor = Color(hex: 0xF1EEF4)
        let ink = Color(hex: 0x161519)
        return Button(action: action) {
            HStack(spacing: 8) {
                WebIcon(path: icon, size: iconSize, line: 2)
                    .foregroundStyle(on ? ink : pal.textMuted)
                Text(title)
                    .font(FAMFont.body(14, .semibold))
                    .foregroundStyle(on ? ink : pal.text)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }
            .padding(.horizontal, 6)
            .frame(maxWidth: .infinity, minHeight: 44)
            .background(on ? onColor : pal.surface, in: Capsule())
            .overlay(Capsule().stroke(on ? onColor : pal.border))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    private func privateNote(_ pal: SearchPalette) -> some View {
        HStack(alignment: .top, spacing: 8) {
            WebIcon(path: SearchIcons.lock, size: 14, line: 2)
                .foregroundStyle(pal.textMuted)
                .padding(.top, 2)
            Text(Self.privateNote)
                .font(FAMFont.body(12.5))
                .foregroundStyle(pal.textMuted)
                .lineSpacing(5)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }

    // MARK: - Trending searches (`.trend-searches`)

    /// One bubble a search, most searched first; a tap is a search that
    /// lands on an episode already written.
    private func trendingList(_ pal: SearchPalette) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Trending searches")
                .monoLabel(9, color: pal.textMuted, tracking: 1.26)
                .padding(.horizontal, 4)
                .padding(.bottom, 1)
            ForEach(trending.prefix(Self.trendShown), id: \.self) { search in
                Button {
                    typing = false
                    model.playTrending(search)
                } label: {
                    HStack(spacing: 8) {
                        WebIcon(path: Icons.trend, size: 13)
                            .foregroundStyle(pal.textMuted)
                        Text(search.label)
                            .font(FAMFont.body(12, .medium))
                            .foregroundStyle(pal.text)
                            .lineLimit(1)
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 6)
                    .frame(minHeight: 32)
                    .background(pal.surface, in: RoundedRectangle(cornerRadius: 16))
                    .overlay(RoundedRectangle(cornerRadius: 16).stroke(pal.border))
                    .contentShape(RoundedRectangle(cornerRadius: 16))
                }
                .buttonStyle(.plain)
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
                }
            }
        case .voice:
            var list = [YouSheetItem(label: "Voice for your searches", group: true)]
            let selected = model.voices?.selected ?? ""
            for v in model.voices?.voices ?? [] {
                let id = v.id
                list.append(YouSheetItem(label: v.label, checked: id == selected) {
                    model.chooseVoice(id, fromSettings: false)
                })
            }
            return list
        case .attach:
            return [
                YouSheetItem(label: "Photo") { photoPicking = true },
                YouSheetItem(label: "File") { filePicking = true },
                YouSheetItem(label: "Link") { linkOpen = true },
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
    /// listener's words), then Photo / File / Link.
    private func openAttach() {
        typing = false
        Task {
            guard await model.consent.ensure() else { return }
            // After the consent card has gone (a second presentation waits).
            try? await Task.sleep(nanoseconds: 350_000_000)
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
        guard !typed.isEmpty || !attach.isEmpty else { return }
        typing = false
        let length = minutes
        sending = true
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
            // Not if, during the wait, they kept typing or went elsewhere.
            guard words.trimmingCharacters(in: .whitespacesAndNewlines) == typed, tab == .search else { return }
            if await model.sendSearch(query: query, title: title, minutes: length) {
                if words.trimmingCharacters(in: .whitespacesAndNewlines) == typed { setWords("") }
            }
        }
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
        if now.contains("\n") {
            let flat = now.replacingOccurrences(of: "\n", with: "")
            setWords(flat)
            go(flat)
            return
        }
        let oldNS = old as NSString
        let nowNS = now as NSString
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
    case length, voice, attach
    var id: String { rawValue }
}

/// A word the box corrected as it was typed (`acLast`).
struct WordFix: Equatable {
    let start: Int
    let from: String
    let to: String
}
