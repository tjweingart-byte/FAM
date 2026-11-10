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

/// The myFAM tab (`screen-playfam`): the wordmark, Create new mix, the
/// category chips (§500), the public-mix search, and your mixes. Drawn to
/// the web's measurements (PARITY.md).
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
                if mixes.state == .ready {
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
                }
                ScrollViewReader { proxy in
                ScrollView {
                  VStack(alignment: .leading, spacing: 0) {
                    Color.clear.frame(height: 0).id("top")
                    CategoryChipsRow(state: $mixes.categoryState, subs: mixes.categorySubs)
                        .padding(.top, 4)
                    if mixes.categoryState.finding {
                        CategoryFind(state: $mixes.categoryState)
                    } else if let node = mixes.categoryState.node {
                        CategoryPanel(node: node, subs: $mixes.categorySubs,
                                      root: mixes.categoryState.root ?? node)
                            .id(node)
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
                }
                }
            }
            .background(FAMColor.background)
            .toolbar(.hidden, for: .navigationBar)
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
                ForEach(mixes.mixes) { mix in
                    Button { path.append(.mix(mix.id)) } label: { MixCard(mix: mix, trailing: .chevron) }
                        .buttonStyle(.press)
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

    var body: some View {
        HStack(spacing: 13) {
            MixArt(cover: mix.cover)
            VStack(alignment: .leading, spacing: 4) {
                Text(mix.name)
                    .font(FAMFont.heading(14, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .multilineTextAlignment(.leading)
                meta
                    .font(FAMFont.mono(8.5))
                    .tracking(0.425)
                    .textCase(.uppercase)
                Text(mix.items.isEmpty ? "Nothing in it yet" : mix.items.map(\.title).joined(separator: " · "))
                    .font(FAMFont.body(10))
                    .foregroundStyle(FAMColor.textMuted)
                    .lineLimit(1)
            }
            Spacer(minLength: 4)
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

/// (+) on somebody else's public mix: add a private copy to your myFAM, or
/// take it out again (`mixAddButtonHTML`, `addMixFrom`).
struct AddMixButton: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var mixes: MixesModel
    let mix: Mix
    var wide = false
    @State private var added: Bool?
    @State private var busy = false
    @State private var confirmRemove = false

    /// Once your list has loaded it is the only truth - a copy of this mix
    /// is in it or not; before that, what the server said with the search.
    private var isAdded: Bool {
        if mixes.state == .ready { return mixes.mixes.contains { $0.sourceId == mix.id } }
        return added ?? mix.added
    }

    var body: some View {
        Button {
            if isAdded { confirmRemove = true } else { Task { await add() } }
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
            } else {
                // `.mix-add`: a 34 pt ring.
                glyph
                    .foregroundStyle(isAdded ? FAMColor.ink : FAMColor.copper)
                    .frame(width: 34, height: 34)
                    .background(isAdded ? FAMColor.copper : Color.clear, in: Circle())
                    .overlay(Circle().stroke(FAMColor.copper, lineWidth: 1.5))
            }
        }
        .buttonStyle(.press)
        .disabled(busy)
        // The web's 0.4 while busy (`.mix-add:disabled`): `.press` already
        // halves a disabled button, so 0.8 of that.
        .opacity(busy ? 0.8 : 1)
        .accessibilityLabel(isAdded ? "Remove \(mix.name) from your myFAM" : "Add \(mix.name) to your myFAM")
        .confirmationDialog("Remove \(mix.name) from your myFAM?", isPresented: $confirmRemove,
                            titleVisibility: .visible) {
            Button("Yes, remove it", role: .destructive) { Task { await remove() } }
            Button("Cancel", role: .cancel) {}
        }
    }

    private var glyph: some View {
        WebIcon(path: isAdded ? Icons.check : Icons.plus, size: 14, line: isAdded ? 2.8 : 2.6)
    }

    private func add() async {
        busy = true
        defer { busy = false }
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
        busy = true
        defer { busy = false }
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
    let back: () -> Void

    var body: some View {
        ZStack {
            Color(red: 10 / 255, green: 9 / 255, blue: 15 / 255).opacity(0.62).ignoresSafeArea()
            VStack(spacing: 0) {
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
            .padding(.horizontal, 27)
            .accessibilityElement(children: .contain)
            .accessibilityAddTraits(.isModal)
        }
    }
}
