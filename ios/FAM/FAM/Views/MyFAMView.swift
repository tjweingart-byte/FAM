import PhotosUI
import UIKit
import SwiftUI

/// Where the myFAM tab can go.
enum MixRoute: Hashable {
    case new
    case mix(String)
    case add(String)
    case publicMix(String)
}

/// The myFAM tab (`screen-playfam`): the wordmark, then two tabs (§550):
/// Mixes - Create new mix, the public-mix search and your mixes - and
/// Categories - §500's chips without All over a list of the categories.
/// Drawn to the web's measurements (PARITY.md).
struct MyFAMView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var mixes: MixesModel
    @Binding var path: [MixRoute]

    @State private var searching = false
    @State private var words = ""
    @State private var results: [Mix]?
    @State private var searchFailed = false
    @State private var searchRun = 0
    @FocusState private var searchFocused: Bool

    var body: some View {
        NavigationStack(path: $path) {
            VStack(spacing: 0) {
                Wordmark(lead: "my", size: 28)
                    .frame(maxWidth: .infinity, minHeight: 48)
                    .padding(.top, 4)
                // Mixes | Categories (§550, the owner's 11.11 note, "like
                // Groups | Messages"), outside the scroller with the wordmark.
                PlayfamTabs(tab: mixes.playfamTab) { showTab($0) }
                // "Create new mix": on Mixes only - but its room is kept on
                // Categories and from the first frame, so the page never
                // jumps 42 pt as the mixes answer or the tab turns (§642).
                let onMixes = mixes.playfamTab == .mixes
                Button { path.append(.new) } label: {
                        HStack(spacing: 7) {
                            WebIcon(path: Icons.plus, size: 13, line: 2.4)
                            Text("Create new mix")
                        }
                        .font(FAMFont.body(12, .bold))
                        .foregroundStyle(FAMColor.text)
                        .padding(.horizontal, 16)
                        .frame(minHeight: 32)
                        .background(FAMColor.surface, in: Capsule())
                        .overlay(Capsule().stroke(FAMColor.borderStrong))
                    }
                    .buttonStyle(.press)
                    .padding(.bottom, 10)
                    .opacity(onMixes ? 1 : 0)
                    .allowsHitTesting(onMixes)
                    .accessibilityHidden(!onMixes)
                ScrollViewReader { proxy in
                ScrollView {
                  VStack(alignment: .leading, spacing: 0) {
                    // The scroller's own 2 pt (`.scroll{ padding-top:2px }`).
                    Color.clear.frame(height: 2).id("top")
                    if mixes.playfamTab == .categories {
                      // The list nudges 18 pt toward the tab chosen (round
                      // three, §642 `playfam-tabs` B).
                      VStack(alignment: .leading, spacing: 0) {
                        CategoryChipsRow(state: $mixes.categoryState, subs: mixes.categorySubs)
                            .padding(.top, 4)
                        if mixes.categoryState.finding {
                            CategoryFind(state: $mixes.categoryState)
                        } else if let node = mixes.categoryState.node {
                            // One panel kept across nodes: the last one stays,
                            // inert, while the next loads (§642, §646).
                            CategoryPanel(node: node, subs: $mixes.categorySubs,
                                          root: mixes.categoryState.root ?? node)
                        } else {
                            CategoryHome(state: $mixes.categoryState)
                        }
                      }
                      .transition(.tabSwap(forward: mixes.playfamTab == .categories))
                    } else {
                    VStack(alignment: .leading, spacing: 0) {
                        searchBar.padding(.top, 18)
                        if searching {
                            searchResults
                        } else {
                            Text("Your mixes, with fresh episodes every morning.")
                                .font(FAMFont.body(10.5))
                                .foregroundStyle(FAMColor.textMuted)
                                .padding(.top, 2)
                                .padding(.bottom, 16)
                            list
                        }
                    }
                    .padding(.horizontal, 18)
                    .padding(.bottom, 24)
                    .transition(.tabSwap(forward: mixes.playfamTab == .categories))
                    }
                  }
                }
                .refreshable { await mixes.load() }
                .onChange(of: mixes.categoryState) { old, new in
                    // A new category goes to the top (`pickCategory`); a sub
                    // chosen inside it does not.
                    if old.root != new.root {
                        mixes.categorySubs = []
                        proxy.scrollTo("top")
                    }
                    // Picking a category from anywhere opens its tab.
                    if !new.atRest && mixes.playfamTab != .categories {
                        mixes.playfamTab = .categories
                    }
                }
                // Either tab starts at the top (`showPlayfamTab`).
                .onChange(of: mixes.playfamTab) { _, _ in
                    proxy.scrollTo("top")
                }
                }
            }
            .background(FAMColor.background)
            .toolbar(.hidden, for: .navigationBar)
            // iOS's own swipe back, with the bar hidden (§652).
            .background(EdgeSwipeBack().frame(width: 0, height: 0))
            // Back on the screen from a mix is looking at it (`openPlayFAM`
            // from the back row, §550): the morning's dot goes.
            .onChange(of: path) { _, now in
                if now.isEmpty { mixes.clearMixDot() }
            }
            .navigationDestination(for: MixRoute.self) { route in
                switch route {
                case .new: NewMixView(path: $path)
                case .mix(let id): MixView(id: id, path: $path)
                case .add(let id): AddTopicsView(id: id)
                case .publicMix(let id): PublicMixView(id: id, path: $path)
                }
            }
        }
    }

    /// Which tab is on (`showPlayfamTab`): leaving Categories forgets the
    /// one that was open; arriving shows the list of them.
    private func showTab(_ which: PlayfamTab) {
        if which == .mixes && !mixes.categoryState.atRest {
            mixes.categoryState = CategoryState()
        }
        mixes.playfamTab = which
    }

    // MARK: - Your mixes

    @ViewBuilder
    private var list: some View {
        switch mixes.state {
        case .loading:
            EmptyView()
        case .failed:
            EmptyNote(text: "Your mixes could not load. They’re safe — try again in a moment.")
        case .ready:
            if mixes.mixes.isEmpty {
                EmptyNote(text: "No mixes yet. Make one for when you listen — the gym, the commute — and it’s fresh every morning.")
            } else {
                // Option A, two to a row (§616, `.mix-a-grid`).
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 11, alignment: .top),
                                    GridItem(.flexible(), spacing: 11, alignment: .top)],
                          alignment: .leading, spacing: 16) {
                    // The deck deals (round three, §642 `mix-cards-arrive`
                    // B): each new card rises, 40 ms after the one before.
                    ForEach(Array(mixes.mixes.enumerated()), id: \.element.id) { i, mix in
                        Button { path.append(.mix(mix.id)) } label: { MixDeckCard(mix: mix, index: i) }
                            .buttonStyle(.press)
                            .modifier(ArriveOnce(key: "mix:" + mix.id, delay: Double(min(i, 3)) * 0.04,
                                                 book: MixDeckCard.book))
                    }
                }
            }
        }
    }

    // MARK: - Public mixes

    private var searchBar: some View {
        HStack(spacing: 10) {
            HStack(spacing: 8) {
                WebIcon(path: Icons.search, size: 15, line: 2.2)
                    .foregroundStyle(FAMColor.textMuted)
                TextField("", text: $words,
                          prompt: Text("Search mixes, topics or people").foregroundColor(FAMColor.textMuted))
                    .font(FAMFont.body(13))
                    .foregroundStyle(FAMColor.text)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.search)
                    .focused($searchFocused)
                    .onSubmit { searchFocused = false }
                    .accessibilityLabel("Search public mixes")
            }
            .padding(.horizontal, 12)
            .frame(minHeight: 38)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12)
                .stroke(searchFocused ? FAMColor.copper : FAMColor.borderStrong))
            if searching {
                Button("Cancel") { closeSearch() }
                    .font(FAMFont.body(12, .bold))
                    .foregroundStyle(FAMColor.copper)
            }
        }
        .onChange(of: searchFocused) { _, on in if on && !searching { openSearch() } }
        .onChange(of: words) { _, _ in if searching { runSearch(after: 0.18) } }
    }

    private func openSearch() {
        searching = true
        runSearch(after: 0)
    }

    private func closeSearch() {
        searching = false
        words = ""
        results = nil
        searchFailed = false
        searchRun += 1
        searchFocused = false
        Task { await mixes.load() }
    }

    /// Debounced 180 ms; an answer that arrives out of order is dropped.
    private func runSearch(after seconds: Double) {
        searchRun += 1
        let mine = searchRun
        let q = words.trimmingCharacters(in: .whitespaces)
        Task {
            if seconds > 0 { try? await Task.sleep(nanoseconds: UInt64(seconds * 1e9)) }
            guard mine == searchRun else { return }
            do {
                let found = try await model.api.publicMixes(q)
                guard mine == searchRun else { return }
                results = found
                searchFailed = false
            } catch {
                guard mine == searchRun else { return }
                searchFailed = true
            }
        }
    }

    @ViewBuilder
    private var searchResults: some View {
        let q = words.trimmingCharacters(in: .whitespaces)
        if searchFailed {
            EmptyNote(text: "Search isn’t working right now. Tap Cancel to see your mixes.").padding(.top, 16)
        } else if let results {
            if results.isEmpty {
                EmptyNote(text: q.isEmpty
                          ? "No public mixes yet. Make one of yours public and others can find it here."
                          : "No public mixes match “\(q)”. Try a mix name, a topic, or somebody’s @handle.")
                    .padding(.top, 16)
            } else {
                Text(q.isEmpty ? "Public mixes" : "Public mixes matching “\(q)”")
                    .monoLabel(8.5, tracking: 0.85)
                    .padding(.top, 16)
                    .padding(.bottom, 10)
                // Not a Button: the card holds its own (+), and a tap on
                // that must not open the mix as well.
                ForEach(results) { mix in
                    MixCard(mix: mix, trailing: mix.mine ? .none : .add)
                        .tapSqueeze()
                        .onTapGesture { path.append(.publicMix(mix.id)) }
                        .accessibilityElement(children: .contain)
                        .accessibilityAddTraits(.isButton)
                }
            }
        } else {
            Text("Searching…").monoLabel(8.5).padding(.top, 16)
        }
    }
}

// MARK: - Pieces

/// A mix's picture: its cover (a data URL or an `/api/image` path), else the
/// paper folder.
struct MixArt: View {
    @EnvironmentObject private var model: AppModel
    let cover: String
    var size: CGFloat = 46
    var radius: CGFloat = 12
    /// Paper on a card and a page; surface-2 in the add-to-mix sheet.
    var tile: Color = FAMColor.paper
    var glyph: Color = FAMColor.ink
    var glyphSize: CGFloat? = nil

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: radius).fill(tile)
            if let image = MixCover.image(cover) {
                Image(uiImage: image).resizable().scaledToFill()
            } else if cover.hasPrefix("/"), let url = model.api.picture(cover) {
                AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { folder }
            } else {
                folder
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: radius))
        .accessibilityHidden(true)
    }

    private var folder: some View {
        WebIcon(path: Icons.folder, size: glyphSize ?? size * 0.43, line: size >= 80 ? 1.7 : 1.9)
            .foregroundStyle(glyph)
    }
}

/// A mix's row on the myFAM tab and in public search (`.mix-card`).
struct MixCard: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var mixes: MixesModel
    enum Trailing { case chevron, add, none }
    let mix: Mix
    let trailing: Trailing
    @Environment(\.dynamicTypeSize) private var typeSize

    /// "New today" (§550): your own list's card, until the mix is played.
    private var fresh: Bool { trailing == .chevron && mixes.isFresh(mix.id) }

    var body: some View {
        HStack(spacing: 13) {
            MixArt(cover: mix.shownCover)
            VStack(alignment: .leading, spacing: 4) {
                Text(mix.name)
                    .font(FAMFont.heading(14, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .multilineTextAlignment(.leading)
                if fresh && typeSize.isAccessibilitySize {
                    NewTodayChip().padding(.leading, -6)
                }
                // Your own mixes are a picture, a name and an arrow (§544, the
                // owner 11.10); a public mix found in search keeps whose it is.
                if trailing != .chevron {
                    meta
                        .font(FAMFont.mono(8.5))
                        .tracking(0.425)
                        .textCase(.uppercase)
                    Text(mix.items.isEmpty ? "Nothing in it yet" : mix.items.map(\.title).joined(separator: " · "))
                        .font(FAMFont.body(10))
                        .foregroundStyle(FAMColor.textMuted)
                        .lineLimit(1)
                }
            }
            // `.mix-card-body{ flex:1 }` with the card's 13 pt gap: a long
            // name wraps 13 pt short of the arrow, not 30.
            .frame(maxWidth: .infinity, alignment: .leading)
            // This morning's episodes waiting, until it is played (§550,
            // `.mix-new`): your own list's card only. At the accessibility
            // sizes it goes under the name rather than squeezing it.
            if fresh && !typeSize.isAccessibilitySize {
                NewTodayChip()
            }
            switch trailing {
            case .chevron:
                WebIcon(path: Icons.chevron, size: 15, line: 2.2)
                    .foregroundStyle(FAMColor.textMuted.opacity(0.6))
            case .add:
                AddMixButton(mix: mix)
            case .none:
                EmptyView()
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 13)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(FAMColor.border))
        .padding(.bottom, 11)
        .contentShape(Rectangle())
    }

    private var meta: Text {
        let n = MixesModel.briefings(mix.items.count) + " · daily"
        if let owner = mix.owner {
            return Text(owner.label).foregroundColor(FAMColor.copper)
                + Text(" · " + n).foregroundColor(FAMColor.textMuted)
        }
        var t = Text(n + " · ").foregroundColor(FAMColor.textMuted)
            + Text(mix.public ? "public" : "private")
                .foregroundColor(mix.public ? FAMColor.copper : FAMColor.textMuted)
        if let from = mix.from {
            t = t + Text(" · from " + from.label).foregroundColor(FAMColor.copper)
        }
        if mix.joint {
            t = t + Text(" · " + MixesModel.jointLabel(mix)).foregroundColor(FAMColor.copper)
        }
        return t
    }
}

/// "New today" on a mix's card (§550, `.mix-new` as §550 writes it):
/// Geist Mono 9 bold, 0.1 em, uppercase, copper words in a copper hairline
/// of radius 10, 6 pt after the name. (The web's page also matches an
/// older `.mix-new` rule - the 100%-wide dashed New mix button - which
/// stretches its chip across the card; that collision is not copied.)
struct NewTodayChip: View {
    var body: some View {
        Text("New today")
            .font(FAMFont.mono(9, bold: true))
            .tracking(0.9)
            .textCase(.uppercase)
            .foregroundStyle(FAMColor.copper)
            .lineLimit(1)
            .fixedSize()
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(FAMColor.copper, lineWidth: 1))
            .padding(.leading, 6)
            .accessibilityLabel("New today")
    }
}

/// (+) on somebody else's public mix: add a private copy to your myFAM, or
/// take it out again (`mixAddButtonHTML`, `addMixFrom`).
struct AddMixButton: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var mixes: MixesModel
    let mix: Mix
    var wide = false
    @State private var added: Bool?
    @State private var busy = false
    /// The async button (round three, §642 `public-mix-add` A): a spinner
    /// only past 300 ms, then held at least 500 ms; label, spinner and tick
    /// cross over 150 ms, the fill too.
    @State private var spinning = false
    @State private var spunAt = Date.distantPast
    @State private var run = 0
    /// `confirmRemoveMix`: FAM's own sheet, its question as the header.
    @State private var confirmRemove: SheetSpec?

    /// Once your list has loaded it is the only truth - a copy of this mix
    /// is in it or not; before that, what the server said with the search.
    private var isAdded: Bool {
        if mixes.state == .ready { return mixes.mixes.contains { $0.sourceId == mix.id } }
        return added ?? mix.added
    }

    var body: some View {
        Button {
            if isAdded { askRemove() } else { Task { await add() } }
        } label: {
            if wide {
                // `.pub-add-btn`: a 44 pt rounded bar.
                HStack(spacing: 8) {
                    glyph
                    Text(isAdded ? "In your myFAM" : "Add to myFAM")
                }
                .font(FAMFont.body(12, .bold))
                .foregroundStyle(isAdded ? FAMColor.ink : FAMColor.copper)
                .frame(maxWidth: .infinity, minHeight: 44)
                .background(isAdded ? FAMColor.copper : Color.clear, in: RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(FAMColor.copper, lineWidth: 1.5))
                .animation(Motion.anim(.easeInOut(duration: 0.15)), value: isAdded)
            } else {
                // `.mix-add`: a 34 pt ring.
                glyph
                    .foregroundStyle(isAdded ? FAMColor.ink : FAMColor.copper)
                    .frame(width: 34, height: 34)
                    .background(isAdded ? FAMColor.copper : Color.clear, in: Circle())
                    .overlay(Circle().stroke(FAMColor.copper, lineWidth: 1.5))
                    .animation(Motion.anim(.easeInOut(duration: 0.15)), value: isAdded)
            }
        }
        .buttonStyle(.press)
        .disabled(busy)
        // The web's 0.4 while busy (`.mix-add:disabled`): `.press` already
        // halves a disabled button, so 0.8 of that.
        .opacity(busy ? 0.8 : 1)
        .accessibilityLabel(isAdded ? "Remove \(mix.name) from your myFAM" : "Add \(mix.name) to your myFAM")
        .famSheet(item: $confirmRemove) { spec in FAMActionSheet(spec: spec) }
    }

    private func askRemove() {
        confirmRemove = SheetSpec(header: "Remove \(mix.name) from your myFAM?",
                                  actions: [SheetAction(label: "Yes, remove it", danger: true) {
                                      Task { await remove() }
                                  }])
    }

    private var glyph: some View {
        ZStack {
            if spinning {
                ProgressView()
                    .controlSize(.mini)
                    .tint(isAdded ? FAMColor.ink : FAMColor.copper)
                    .transition(Self.cross)
            } else {
                IconSwap(key: isAdded) {
                    WebIcon(path: isAdded ? Icons.check : Icons.plus, size: 14, line: isAdded ? 2.8 : 2.6)
                }
                .transition(Self.cross)
            }
        }
        .frame(width: 14, height: 14)
        .animation(Motion.anim(Motion.easeOut(0.15)), value: spinning)
    }

    private static var cross: AnyTransition {
        if Motion.reduced { return .identity }
        return AnyTransition.scale(scale: 0.9).combined(with: .opacity)
    }

    /// Busy from the tap; the spinner only if it is still busy at 300 ms.
    private func begin() {
        busy = true
        run += 1
        let mine = run
        DispatchQueue.main.asyncAfter(deadline: .now() + Motion.Dur.spinnerAfter) {
            guard busy, mine == run else { return }
            spunAt = Date()
            spinning = true
        }
    }

    /// Done: a spinner shown stays its 500 ms first.
    private func end() {
        busy = false
        guard spinning else { return }
        let wait = max(0, Motion.Dur.spinnerMin - Date().timeIntervalSince(spunAt))
        let mine = run
        DispatchQueue.main.asyncAfter(deadline: .now() + wait) {
            if mine == run && !busy { spinning = false }
        }
    }

    private func add() async {
        begin()
        defer { end() }
        do {
            let copy = try await model.api.addPublicMix(mix.id)
            mixes.keep(copy)
            added = true
            model.nowPlaying.say("Added to your myFAM as \(copy.name)", action: "Open") {
                model.openMix(copy.id)
            }
        } catch {
            model.nowPlaying.say(MixesModel.sentence(error, else: "Could not add that mix"))
        }
    }

    private func remove() async {
        begin()
        defer { end() }
        do {
            try await model.api.removePublicMix(mix.id)
            for copy in mixes.mixes where copy.sourceId == mix.id { mixes.drop(copy.id) }
            added = false
            model.nowPlaying.say("Removed from your myFAM")
        } catch {
            model.nowPlaying.say(MixesModel.sentence(error, else: "Could not remove that mix"))
        }
    }
}

/// The back row of every screen past the tab (`.back-row`).
struct MixHeader<Trailing: View>: View {
    let title: String
    var backLabel = "Back"
    let back: () -> Void
    @ViewBuilder var trailing: () -> Trailing
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        // The heading wraps rather than being cut; at the accessibility
        // sizes it takes a row of its own under Back and the controls, so
        // nothing on its row can squeeze it (`large-text`).
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 10) {
                    backButton
                    Spacer(minLength: 0)
                    trailing().frame(minWidth: 36)
                }
                heading
                    .padding(.horizontal, 8)
            }
            .padding(.horizontal, 10)
            .frame(minHeight: 48)
        } else {
            HStack(spacing: 10) {
                backButton
                heading
                trailing().frame(minWidth: 36)
            }
            .padding(.horizontal, 10)
            .frame(minHeight: 48)
        }
    }

    private var backButton: some View {
        Button(action: back) {
            WebIcon(path: Icons.back, size: 20, line: 2.6).foregroundStyle(FAMColor.text)
                .frame(width: 36, height: 36)
        }
        .buttonStyle(.press)
        .accessibilityLabel(backLabel)
    }

    private var heading: some View {
        Text(title)
            .font(FAMFont.heading(19, .medium))
            .foregroundStyle(FAMColor.text)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityAddTraits(.isHeader)
    }
}

/// Covers: a chosen photo cut square, 360 px, JPEG - the size the web's
/// editor sends (`Position your cover`).
enum MixCover {
    static func image(_ cover: String) -> UIImage? {
        guard cover.hasPrefix("data:"), let comma = cover.firstIndex(of: ",") else { return nil }
        guard let data = Data(base64Encoded: String(cover[cover.index(after: comma)...])) else { return nil }
        return UIImage(data: data)
    }

    static func dataURL(from data: Data) -> String? {
        guard let source = UIImage(data: data) else { return nil }
        let side = min(source.size.width, source.size.height)
        let crop = CGRect(x: (source.size.width - side) / 2, y: (source.size.height - side) / 2,
                          width: side, height: side)
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 360, height: 360),
                                               format: { let f = UIGraphicsImageRendererFormat(); f.scale = 1; return f }())
        let square = renderer.image { _ in
            let scale = 360 / side
            source.draw(in: CGRect(x: -crop.minX * scale, y: -crop.minY * scale,
                                   width: source.size.width * scale, height: source.size.height * scale))
        }
        guard let jpeg = square.jpegData(compressionQuality: 0.9) else { return nil }
        return "data:image/jpeg;base64," + jpeg.base64EncodedString()
    }

    static let note = "Other people can see this photo, so it’s checked automatically by our AI provider, Anthropic, for nudity, violence and hate symbols before it’s shown."
}

/// After the last episode of a mix (`mixDoneOverlay`).
struct MixDoneOverlay: View {
    let name: String
    /// The day's played episodes' pictures: they file in (round three).
    var pictures: [URL?] = []
    let back: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var landed = false
    @State private var squared = false
    @State private var checked = false

    private var cards: [URL?] { Array(pictures.prefix(6)) }

    var body: some View {
        ZStack {
            Color(red: 10 / 255, green: 9 / 255, blue: 15 / 255).opacity(0.62).ignoresSafeArea()
            VStack(spacing: 0) {
                if !cards.isEmpty {
                    stack.padding(.bottom, 16)
                }
                Text("You have finished listening to your \(name.isEmpty ? "myFAM" : name) playlist for today")
                    .font(FAMFont.heading(19, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .multilineTextAlignment(.center)
                    .lineSpacing(5)
                    .padding(.bottom, 8)
                Text("Fresh episodes on the same topics tomorrow.")
                    .font(FAMFont.body(12))
                    .foregroundStyle(FAMColor.textMuted)
                    .multilineTextAlignment(.center)
                    .lineSpacing(6)
                // `.nextup-back`: words only, no fill.
                Button(action: back) {
                    Text("Back to myFAM")
                        .monoLabel(10, tracking: 0.7)
                        .frame(maxWidth: .infinity)
                        .padding(11)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.press)
                .padding(.top, 14)
            }
            .padding(.horizontal, 20)
            .padding(.top, 24)
            .padding(.bottom, 14)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 20))
            .overlay(RoundedRectangle(cornerRadius: 20).stroke(FAMColor.borderStrong))
            .shadow(color: .black.opacity(0.45), radius: 30, y: 30)
            .liftsIn()
            .dropsOnLeave()
            .padding(.horizontal, 27)
            .accessibilityElement(children: .contain)
            .accessibilityAddTraits(.isModal)
        }
        .onAppear { file() }
    }

    /// The day's cards (§642 `mix-finished` B): each slides in from the
    /// right onto the stack, 260 ms on `--ease-out`, 40 ms apart; the stack
    /// squares up (300 ms) and a copper check draws on it (220 ms). Reduce
    /// Motion: the squared stack with its check.
    private var stack: some View {
        ZStack {
            ForEach(Array(cards.enumerated()), id: \.offset) { i, url in
                ZStack {
                    FAMColor.surface2
                    if let url {
                        AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { Color.clear }
                    }
                }
                .frame(width: 72, height: 54)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.white.opacity(0.8), lineWidth: 1.5))
                .shadow(color: .black.opacity(0.35), radius: 5, y: 3)
                .rotationEffect(.degrees(squared ? 0 : Double(i % 2 == 0 ? 1 : -1) * Double(3 + i * 2)))
                .offset(x: landed ? 0 : 60)
                .opacity(landed ? 1 : 0)
                .animation(reduceMotion ? nil : Motion.easeOut(0.26).delay(0.2 + 0.04 * Double(i)), value: landed)
            }
            Circle()
                .fill(FAMColor.copper)
                .frame(width: 26, height: 26)
                .overlay(
                    DoneTick()
                        .trim(from: 0, to: checked ? 1 : 0)
                        .stroke(FAMColor.ink, style: StrokeStyle(lineWidth: 2.4, lineCap: .round, lineJoin: .round))
                        .frame(width: 14, height: 14))
                .opacity(checked ? 1 : 0)
                .offset(x: 34, y: 24)
        }
        .frame(height: 70)
        .accessibilityHidden(true)
    }

    private func file() {
        if reduceMotion {
            landed = true
            squared = true
            checked = true
            return
        }
        DispatchQueue.main.async { landed = true }
        let lands = 0.2 + 0.04 * Double(max(cards.count - 1, 0)) + 0.26
        DispatchQueue.main.asyncAfter(deadline: .now() + lands) {
            withAnimation(Motion.easeInOut(0.3)) { squared = true }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + lands + 0.3) {
            withAnimation(Motion.easeOut(Motion.Dur.tick)) { checked = true }
        }
    }
}

/// The finished mix's check (`M5 12.5l4.5 4.5L19 7.5`).
private struct DoneTick: Shape {
    func path(in rect: CGRect) -> Path {
        let sx = rect.width / 24
        let sy = rect.height / 24
        var p = Path()
        p.move(to: CGPoint(x: rect.minX + 5 * sx, y: rect.minY + 12.5 * sy))
        p.addLine(to: CGPoint(x: rect.minX + 9.5 * sx, y: rect.minY + 17 * sy))
        p.addLine(to: CGPoint(x: rect.minX + 19 * sx, y: rect.minY + 7.5 * sy))
        return p
    }
}
