import SwiftUI

/// DailyFAM (`screen-myfam` in code, `names-swapped`), ported from the web's
/// screen top to bottom: the centred wordmark; the friends' stories row;
/// Pick up where you left off; then the five rails in the server's order,
/// each headed as the web heads it, its cards carrying the web's save
/// bookmark, ⋯ menu, ready badge, friends pill, live line and hook, and
/// ending in VIEW MORE.
struct DailyView: View {
    @EnvironmentObject private var model: AppModel
    @Binding var scrollToTop: Int

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Wordmark(lead: "daily", size: 28)
                    .frame(maxWidth: .infinity, minHeight: 48)
                    .padding(.top, 4)
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 0) {
                            Color.clear.frame(height: 0).id("top")
                            FacesRow()
                            PickUpBlock()
                            if model.rails.isEmpty {
                                if model.loadingRails || model.railsProblem.isEmpty {
                                    Skeleton()
                                } else {
                                    EmptyNote(text: model.railsProblem).padding(.top, 20)
                                }
                            }
                            ForEach(model.rails) { rail in
                                RailRow(rail: rail)
                            }
                        }
                        .padding(.horizontal, 18)
                        .padding(.bottom, 24)
                    }
                    .refreshable { await model.loadRails() }
                    .onChange(of: scrollToTop) { _, _ in
                        withAnimation { proxy.scrollTo("top", anchor: .top) }
                    }
                }
            }
            .background(FAMColor.background)
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(for: String.self) { key in
                SectionView(key: key)
            }
        }
        .task {
            if model.rails.isEmpty { await model.loadRails() }
        }
    }
}

/// The loading skeleton (first load only): "Loading" and three blocks.
private struct Skeleton: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text("Loading").monoLabel(9.5).padding(.top, 26)
            HStack(spacing: 11) {
                ForEach(0..<3, id: \.self) { _ in
                    RoundedRectangle(cornerRadius: 13)
                        .fill(FAMColor.surface)
                        .overlay(RoundedRectangle(cornerRadius: 13).stroke(FAMColor.border))
                        .frame(width: 150, height: 118)
                        .opacity(0.55)
                }
            }
        }
    }
}

// MARK: - A rail

struct RailRow: View {
    @EnvironmentObject private var model: AppModel
    let rail: Rail

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(model.heading(for: rail))
                .font(FAMFont.heading(20))
                .foregroundStyle(FAMColor.text)
                .lineSpacing(5)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 26)
                .padding(.bottom, 12)
                .accessibilityAddTraits(.isHeader)
            if rail.topics.isEmpty {
                // The friends rail with nobody followed says nothing.
                if !(rail.key == "followers" && rail.hasCircle == false) {
                    EmptyNote(text: rail.emptyReason ?? "Nothing here yet.")
                }
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 11) {
                        ForEach(rail.topics) { tile in
                            TileCard(tile: tile, railKey: rail.key, width: 150)
                                .frame(maxHeight: .infinity, alignment: .top)
                        }
                        NavigationLink(value: rail.key) {
                            Text("View\nmore")
                                .monoLabel(8.5, color: FAMColor.copper)
                                .multilineTextAlignment(.center)
                                .frame(width: 52)
                                .frame(maxHeight: .infinity)
                        }
                        .accessibilityLabel("View more of \(model.heading(for: rail))")
                    }
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.trailing, 14)
                    .padding(.vertical, 2)
                }
                .padding(.trailing, -18)
            }
        }
    }
}

// MARK: - A card

/// `.seed-card`: picture with its subject chip, the save bookmark and ⋯ top
/// right, "ready" top left, the friends pill bottom right; then the whole
/// title (stepped down when long), the live line and the hook.
struct TileCard: View {
    @EnvironmentObject private var model: AppModel
    let tile: Tile
    let railKey: String
    var width: CGFloat? = 150

    @State private var menu = false
    @State private var friends = false

    var body: some View {
        // Not a Button: the save and ⋯ buttons inside it must get their own taps.
        VStack(alignment: .leading, spacing: 0) {
                thumb
                VStack(alignment: .leading, spacing: 0) {
                    Text(tile.title)
                        .font(FAMFont.heading(titleSize, .semibold))
                        .foregroundStyle(FAMColor.text)
                        .lineSpacing(1)
                        .fixedSize(horizontal: false, vertical: true)
                    if !tile.liveLine.isEmpty { liveLine.padding(.top, 2) }
                    hook.padding(.top, 6)
                }
                .padding(.horizontal, 11)
                .padding(.top, 10)
                .padding(.bottom, 12)
                Spacer(minLength: 0)
            }
            .frame(width: width, alignment: .leading)
            .frame(maxWidth: width == nil ? .infinity : nil, alignment: .leading)
            .background(FAMColor.surface)
            .clipShape(RoundedRectangle(cornerRadius: 13))
            .overlay(RoundedRectangle(cornerRadius: 13).stroke(FAMColor.border))
            .contentShape(Rectangle())
            .onTapGesture { model.play(tile) }
            .accessibilityElement(children: .contain)
            .accessibilityAddTraits(.isButton)
            .accessibilityAction { model.play(tile) }
            .accessibilityHint("Plays the episode")
        .sheet(isPresented: $menu) { TileMenu(tile: tile) }
        .sheet(isPresented: $friends) { FriendsListened(tile: tile) }
    }

    private var titleSize: CGFloat {
        tile.title.count > 72 ? 10.5 : tile.title.count > 48 ? 11.5 : 12.5
    }

    private var thumb: some View {
        ZStack {
            Picture(url: model.api.picture(tile.thumb))
            // The subject chip bottom left and the friends who listened
            // bottom right, on one line and centred on each other (the
            // owner's call for iOS: the web lifts the faces above the chip).
            if !tile.thumbFacet.isEmpty || tile.friendsListened > 0 {
                HStack(alignment: .center, spacing: 6) {
                    if !tile.thumbFacet.isEmpty {
                        Text(tile.thumbFacet)
                            .monoLabel(8, color: Color(hex: 0x2A2530))
                            .lineLimit(1)
                            .minimumScaleFactor(0.7)
                            .padding(.horizontal, 6)
                            .padding(.top, 3)
                            .padding(.bottom, 2)
                            .background(Color(red: 237 / 255, green: 230 / 255, blue: 214 / 255).opacity(0.92),
                                        in: RoundedRectangle(cornerRadius: 4))
                    }
                    Spacer(minLength: 0)
                    if tile.friendsListened > 0 {
                        friendsPill.fixedSize()
                    }
                }
                .padding(.horizontal, 8)
                .padding(.bottom, 8)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
            }
            // "ready": written already, plays at once.
            if tile.cached {
                Text("ready")
                    .monoLabel(7, color: FAMColor.ink, bold: true)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(FAMColor.copper, in: Capsule())
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .padding(8)
            }
            // Save and ⋯, top right.
            HStack(spacing: 5) {
                actButton(model.isSaved(tile) ? "bookmark.fill" : "bookmark", on: model.isSaved(tile),
                          label: model.isSaved(tile) ? "Take it off Save for Later" : "Save \(tile.title) for later") {
                    model.toggleSave(tile)
                }
                actButton("ellipsis", on: false, label: "More for \(tile.title)") { menu = true }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
            .padding(6)
        }
        // The whole picture, at its own 4:3 - never the web's 96 pt strip,
        // which cropped the top and bottom off every one (the owner, on iOS).
        .aspectRatio(4 / 3, contentMode: .fit)
        .clipped()
    }

    private func actButton(_ icon: String, on: Bool, label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(on ? FAMColor.ink : Color(hex: 0xF4EFE4))
                .frame(width: 24, height: 24)
                .background(on ? FAMColor.sage : Color(red: 26 / 255, green: 22 / 255, blue: 34 / 255).opacity(0.62),
                            in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }

    private var friendsPill: some View {
        let faces = tile.friendsFaces.compactMap { i in
            model.friendFaces.indices.contains(i) ? model.friendFaces[i] : nil
        }
        return Button { friends = true } label: {
            if faces.isEmpty {
                Text("\(tile.friendsListened) friend\(tile.friendsListened == 1 ? "" : "s") listened")
                    .font(FAMFont.body(9, .semibold))
                    .foregroundStyle(FAMColor.ink)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 2)
                    .background(FAMColor.copper, in: RoundedRectangle(cornerRadius: 4))
            } else {
                HStack(spacing: 4) {
                    HStack(spacing: -6) {
                        ForEach(Array(faces.prefix(3).enumerated()), id: \.offset) { i, face in
                            Face(name: face.name, handle: face.handle ?? "", avatar: face.avatar ?? "", size: 18)
                                // A ring in the pill's own colour, so each overlapped face reads whole.
                                .overlay(Circle().stroke(Color(red: 42 / 255, green: 37 / 255, blue: 48 / 255), lineWidth: 1.5))
                                .zIndex(Double(3 - i))
                        }
                    }
                    if tile.friendsListened > 3 {
                        Text("+\(tile.friendsListened - 3)")
                            .font(FAMFont.mono(10, bold: true))
                            .foregroundStyle(Color(hex: 0xE0B563))
                    }
                }
                .padding(.leading, 2)
                .padding(.trailing, faces.count > 0 && tile.friendsListened > 3 ? 6 : 2)
                .padding(.vertical, 2)
                .background(Color(red: 42 / 255, green: 37 / 255, blue: 48 / 255).opacity(0.88), in: Capsule())
                .overlay(Capsule().stroke(Color(red: 237 / 255, green: 230 / 255, blue: 214 / 255).opacity(0.18)))
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(tile.friendsListened == 1 && faces.count == 1
                            ? "\(faces[0].name) listened" : "\(tile.friendsListened) friends listened")
    }

    /// A game's line, with how old it is; copper and dotted while live.
    private var liveLine: some View {
        let age = Date().timeIntervalSince1970 - tile.liveAsOf
        let live = tile.liveStatus == "in_progress" && age < 20 * 60
        var text = tile.liveLine
        if tile.liveAsOf > 0 && age >= 20 * 60 {
            text += age < 90 * 60 ? " · \(Int(age / 60)) min ago" : " · \(Int(age / 3600)) h ago"
        }
        return HStack(spacing: 4) {
            if live { Circle().fill(FAMColor.copper).frame(width: 6, height: 6) }
            Text(text)
                .font(FAMFont.mono(8.5, bold: live))
                .foregroundStyle(live ? FAMColor.copper : FAMColor.textMuted)
                .lineLimit(1)
        }
    }

    /// The hook: the angle, else the subtitle, else the rail's own reason
    /// with its bold part in copper (`seedHook`).
    private var hook: some View {
        let text: Text
        if !tile.hook.isEmpty {
            text = Text(tile.hook)
        } else {
            switch railKey {
            case "missed": text = Text("Went past you").bold().foregroundColor(FAMColor.copper) + Text(" this week")
            case "followers": text = Text("Your friends").bold().foregroundColor(FAMColor.copper) + Text(" played this")
            case "from_history": text = Text("Because of what you have played")
            default: text = Text("Playing across FAM now")
            }
        }
        // The whole line, never clamped to two with "…" (the owner, on iOS).
        return text
            .font(FAMFont.body(10))
            .foregroundStyle(FAMColor.textMuted)
            .lineSpacing(2)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// A person's face: their picture, else initials on a colour from their name.
struct Face: View {
    let name: String
    let handle: String
    let avatar: String
    var size: CGFloat = 22

    @EnvironmentObject private var model: AppModel
    private static let hues: [UInt32] = [0xC9A876, 0x8FAE9A, 0xC3A8E0, 0xE2694F, 0x8FB4D9, 0xD9B38F]

    var body: some View {
        let key = handle.isEmpty ? name : handle
        let hash = key.unicodeScalars.reduce(0) { ($0 &* 31 &+ Int($1.value)) & 0x7fffffff }
        let initials = name.split(separator: " ").prefix(2).compactMap { $0.first }.map(String.init).joined()
        ZStack {
            Circle().fill(Color(hex: Self.hues[hash % Self.hues.count]))
            Text(initials.isEmpty ? "?" : initials.uppercased())
                .font(FAMFont.body(size * 0.38, .bold))
                .foregroundStyle(FAMColor.ink)
                .minimumScaleFactor(0.5)
            if let url = model.api.picture(avatar) {
                // Held to the circle's size before it is clipped: a photo
                // scaled to fill is larger than its frame, and unclipped it
                // spilled out of the friends pill.
                AsyncImage(url: url) { image in image.resizable().scaledToFill() } placeholder: { Color.clear }
                    .frame(width: size, height: size)
            }
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
        .accessibilityHidden(true)
    }
}

// MARK: - A card's ⋯ menu

struct TileMenu: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let tile: Tile
    @State private var shareItems: [Any]?
    @State private var queue = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                Picture(url: model.api.picture(tile.thumb))
                    .frame(width: 44, height: 44)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                Text(tile.title)
                    .font(FAMFont.body(14, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(2)
            }
            .padding(.horizontal, 22)
            .padding(.vertical, 16)
            Divider()
            row("Share", "square.and.arrow.up") {
                Task {
                    let ep = model.episode(for: tile)
                    if let link = try? await model.api.share(ep.key, title: tile.title) {
                        shareItems = [link.targets?["copy"]?.text ?? link.url]
                    } else {
                        model.nowPlaying.say("Could not make a link")
                    }
                }
            }
            row("Add to Queue", "text.badge.plus") { dismiss(); model.addToQueue(tile) }
            row("Go to Queue", "list.bullet") { queue = true }
            row("Not interested", "hand.thumbsdown") { dismiss(); model.notInterested(tile) }
            Button("Cancel") { dismiss() }
                .font(FAMFont.body(14, .semibold))
                .foregroundStyle(FAMColor.textMuted)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 16)
        }
        .background(FAMColor.surface)
        .presentationDetents([.medium])
        .presentationBackground(FAMColor.surface)
        .sheet(isPresented: Binding(get: { shareItems != nil }, set: { if !$0 { shareItems = nil } })) {
            ActivityView(items: shareItems ?? []) { shareItems = nil; dismiss() }
        }
        .sheet(isPresented: $queue) {
            QueueSheet()
                .environmentObject(model)
                .environmentObject(model.nowPlaying)
        }
    }

    private func row(_ label: String, _ icon: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 14) {
                Image(systemName: icon)
                    .font(.system(size: 16))
                    .foregroundStyle(FAMColor.textMuted)
                    .frame(width: 22)
                Text(label).font(FAMFont.body(13.5, .semibold)).foregroundStyle(FAMColor.text)
                Spacer()
            }
            .padding(.horizontal, 22)
            .padding(.vertical, 14)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// "N friends listened": who.
struct FriendsListened: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let tile: Tile

    var body: some View {
        let faces = tile.friendsFaces.compactMap { i in
            model.friendFaces.indices.contains(i) ? model.friendFaces[i] : nil
        }
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(tile.friendsListened == 1 ? "1 friend listened" : "\(tile.friendsListened) friends listened")
                    .font(FAMFont.heading(16, .medium))
                    .foregroundStyle(FAMColor.text)
                Spacer()
                Button { dismiss() } label: { Image(systemName: "xmark").foregroundStyle(FAMColor.textMuted) }
                    .accessibilityLabel("Close")
            }
            ForEach(Array(faces.enumerated()), id: \.offset) { _, face in
                HStack(spacing: 12) {
                    Face(name: face.name, handle: face.handle ?? "", avatar: face.avatar ?? "", size: 30)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(face.name).font(FAMFont.body(13, .semibold)).foregroundStyle(FAMColor.text)
                        if let h = face.handle, !h.isEmpty {
                            Text("@" + h).font(FAMFont.mono(10)).foregroundStyle(FAMColor.textMuted)
                        }
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(22)
        .background(FAMColor.surface)
        .presentationDetents([.medium])
        .presentationBackground(FAMColor.surface)
    }
}

// MARK: - Pick up where you left off

/// `#goDeeperBlock`: up to four cards, alternating an episode left part way
/// (time left and a bar) and a thread to pick up (GO DEEPER · 1.5 MIN);
/// nothing at all when there is nothing to pick up.
struct PickUpBlock: View {
    @EnvironmentObject private var model: AppModel

    private enum Card: Hashable {
        case resume(PickUpResume)
        case thread(PickUpThread)
    }

    private var cards: [Card] {
        guard let page = model.pickUp else { return [] }
        var out: [Card] = []
        var r = page.resume.makeIterator()
        var t = page.threads.makeIterator()
        var takeResume = true
        while out.count < 4 {
            if takeResume, let next = r.next() { out.append(.resume(next)) }
            else if let next = t.next() { out.append(.thread(next)) }
            else if let next = r.next() { out.append(.resume(next)) }
            else { break }
            takeResume.toggle()
        }
        return out
    }

    var body: some View {
        let shown = cards
        if !shown.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                Text("Pick up where you left off")
                    .monoLabel(9.5, color: FAMColor.copper, bold: true)
                    .padding(.top, 18)
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible())], spacing: 10) {
                    ForEach(shown, id: \.self) { card in
                        cardView(card)
                    }
                }
            }
        }
    }

    @ViewBuilder private func cardView(_ card: Card) -> some View {
        switch card {
        case .resume(let r):
            let whole = (r.duration ?? 0) > 0 ? r.duration! : r.minutes * 60
            cardBody(title: r.title.isEmpty ? NowPlayingModel.titleFromQuestion(r.query) : r.title,
                 summary: r.summary ?? "",
                 meta: "\(clock(max(0, whole - r.seconds))) left", copperMeta: false,
                 progress: min(r.seconds / max(whole, 1), 1),
                 dismissKey: r.query) { model.playPickUp(resume: r) }
        case .thread(let t):
            cardBody(title: t.title, summary: t.summary ?? "", meta: "Go deeper · 1.5 min", copperMeta: true,
                 progress: nil, dismissKey: t.thread) { model.playPickUp(thread: t) }
        }
    }

    private func cardBody(title: String, summary: String, meta: String, copperMeta: Bool,
                      progress: Double?, dismissKey: String, play: @escaping () -> Void) -> some View {
        Button(action: play) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(FAMFont.heading(10.5, .medium))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(4)
                    .multilineTextAlignment(.leading)
                    .padding(.trailing, 14)
                if !summary.isEmpty {
                    Text(summary)
                        .font(FAMFont.body(8.5))
                        .foregroundStyle(FAMColor.textMuted)
                        .lineLimit(2)
                }
                Spacer(minLength: 0)
                Text(meta).monoLabel(7.5, color: copperMeta ? FAMColor.copper : FAMColor.textMuted)
            }
            .padding(.horizontal, 11)
            .padding(.top, 7)
            .padding(.bottom, 8)
            .frame(maxWidth: .infinity, minHeight: summary.isEmpty ? 80 : 104, alignment: .topLeading)
            .background(FAMColor.surface)
            .overlay(alignment: .bottomLeading) {
                if let progress {
                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            Rectangle().fill(FAMColor.borderStrong)
                            Rectangle().fill(FAMColor.copper).frame(width: geo.size.width * progress)
                        }
                    }
                    .frame(height: 3)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(FAMColor.border))
            .overlay(alignment: .topTrailing) {
                Button { model.dismissPickUp(query: dismissKey) } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(FAMColor.textMuted)
                        .frame(width: 20, height: 20)
                }
                .padding(4)
                .accessibilityLabel("Don't offer this again")
            }
        }
        .buttonStyle(.plain)
    }
}

// MARK: - View more

/// A rail's whole section (`openSection`): 16 at a time, two across,
/// Refresh deals the next 16; Made for you cycles through what is written
/// and says when you are caught up; Trending is grouped by continent.
struct SectionView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let key: String

    @State private var page: SectionPage?
    @State private var seen: [String] = []
    @State private var loading = false
    @State private var failed = false
    @State private var wrappedNote = ""

    private static let pageSize = 16

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                Button { dismiss() } label: {
                    WebIcon(path: Icons.back, size: 20, line: 2.6).foregroundStyle(FAMColor.text)
                }
                .accessibilityLabel("Back")
                Text(title)
                    .font(FAMFont.heading(19, .medium))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(2)
                Spacer()
                if canRefresh {
                    Button { Task { await load() } } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "arrow.clockwise")
                                .font(.system(size: 12, weight: .semibold))
                                .rotationEffect(.degrees(loading ? 360 : 0))
                                .animation(loading ? .linear(duration: 0.6).repeatForever(autoreverses: false) : .default,
                                           value: loading)
                            Text("Refresh").font(FAMFont.body(12.5, .semibold))
                        }
                        .foregroundStyle(FAMColor.text)
                        .padding(.horizontal, 13)
                        .frame(height: 32)
                        .overlay(Capsule().stroke(FAMColor.borderStrong))
                    }
                    .accessibilityLabel("Show different episodes")
                }
            }
            .padding(.horizontal, 18)
            .padding(.top, 16)
            .padding(.bottom, 10)
            ScrollView {
                VStack(alignment: .leading, spacing: 11) {
                    Text(reason)
                        .font(FAMFont.body(12.5))
                        .foregroundStyle(FAMColor.textMuted)
                        .lineSpacing(4)
                    if page?.caughtUp == true {
                        caughtUp
                    } else if let groups = page?.groups, !groups.isEmpty {
                        ForEach(groups) { group in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(group.label).font(FAMFont.heading(17)).foregroundStyle(FAMColor.text)
                                if group.yours == true {
                                    Text("Where you are").monoLabel(8.5, color: FAMColor.copper)
                                }
                            }
                            .padding(.top, 14)
                            grid(group.topics)
                        }
                    } else {
                        grid(page?.topics ?? [])
                    }
                }
                .padding(.horizontal, 18)
                .padding(.bottom, 20)
            }
        }
        .background(FAMColor.background)
        .toolbar(.hidden, for: .navigationBar)
        .task { if page == nil { await load() } }
    }

    private var title: String {
        if key == "from_history" { return model.madeForTitle(multiline: false) }
        return model.rails.first(where: { $0.key == key }).map { model.heading(for: $0) } ?? "More"
    }

    private var canRefresh: Bool {
        guard let page, page.caughtUp != true else { return false }
        return (page.total ?? 0) > page.topics.count || key == "from_history"
    }

    private var reason: String {
        if failed { return "Could not load this section. Everything else still works, and this fills in when the server is back." }
        guard let page else { return "Loading…" }
        if page.topics.isEmpty && page.caughtUp != true { return page.emptyReason ?? "Nothing in this section yet." }
        let ready = page.ready ?? 0
        let line = ready == 0 ? "" : ready == 1 ? "1 of these plays instantly." : "\(ready) of these play instantly."
        return [wrappedNote, line].filter { !$0.isEmpty }.joined(separator: " ")
    }

    private func grid(_ tiles: [Tile]) -> some View {
        LazyVGrid(columns: [GridItem(.flexible(), spacing: 11), GridItem(.flexible())], spacing: 11) {
            ForEach(tiles) { tile in
                TileCard(tile: tile, railKey: key, width: nil)
            }
        }
    }

    private var caughtUp: some View {
        VStack(spacing: 6) {
            Text("You’re all caught up").font(FAMFont.heading(22, .semibold)).foregroundStyle(FAMColor.text)
            Text("That’s every episode ready on your topics right now. New ones arrive as the day’s stories are written.")
                .font(FAMFont.body(13))
                .foregroundStyle(FAMColor.textMuted)
                .multilineTextAlignment(.center)
                .padding(.bottom, 12)
            Button("Start again from the top") {
                seen = []
                Task { await load() }
            }
            .buttonStyle(OutlineButtonStyle())
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 48)
        .padding(.horizontal, 16)
    }

    private func load() async {
        loading = true
        defer { loading = false }
        // Made for you's first page starts with the rail's own four.
        let head = (key == "from_history" && seen.isEmpty)
            ? (model.rails.first(where: { $0.key == key })?.topics ?? []) : []
        do {
            var next = try await model.api.section(key, seen: seen + head.map(\.id),
                                                   pageSize: Self.pageSize - head.count)
            failed = false
            if !head.isEmpty {
                next = SectionPage(key: next.key, topics: head + next.topics, emptyReason: next.emptyReason,
                                   ready: (next.ready ?? 0) + head.filter(\.cached).count, total: next.total,
                                   more: next.more, wrapped: next.wrapped, caughtUp: next.caughtUp,
                                   friendFaces: next.friendFaces, groups: next.groups)
            }
            if next.wrapped == true {
                wrappedNote = "That was all \(next.total ?? next.topics.count) for this rail, so these are the first again."
                seen = []
            } else if next.more == false && !seen.isEmpty {
                wrappedNote = "The last of this rail. Refresh starts again from the top."
            } else {
                wrappedNote = ""
            }
            seen += next.topics.map(\.id)
            page = next
        } catch {
            failed = true
        }
    }
}
