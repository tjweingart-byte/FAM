import SwiftUI

/// DailyFAM (`screen-myfam` in code, `names-swapped`), ported from the web's
/// screen top to bottom: the centred wordmark; the friends' stories row;
/// Pick up where you left off; then the five rails in the server's order,
/// each headed as the web heads it, its cards carrying the web's save
/// bookmark, ⋯ menu, ready badge, friends pill, live line and hook, and
/// ending in VIEW MORE.
struct DailyView: View {
    @EnvironmentObject private var model: AppModel
    /// Bumped by every tap on the DailyFAM tab while it is showing.
    @Binding var scrollToTop: Int
    /// Whether the page is at its top: a tap there refetches, a tap
    /// further down only scrolls back up (`openMyFamTab`).
    @State private var atTop = true

    var body: some View {
        NavigationStack(path: $model.dailyPath) {
            VStack(spacing: 0) {
                Wordmark(lead: "daily", size: 28)
                    .frame(maxWidth: .infinity, minHeight: 48)
                    .padding(.top, 4)
                // No connection: what still plays (`#offlineBar`).
                if model.isOffline { OfflineBar() }
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(spacing: 0) {
                            // Outside the lazy stack, so it is never unloaded
                            // and always says where the top is.
                            Color.clear
                                .frame(height: 0)
                                .id("top")
                                .background(GeometryReader { geo in
                                    Color.clear.preference(key: DailyTopKey.self,
                                                           value: geo.frame(in: .named("dailyScroll")).minY)
                                })
                            LazyVStack(alignment: .leading, spacing: 0) {
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
                    }
                    .coordinateSpace(name: "dailyScroll")
                    .onPreferenceChange(DailyTopKey.self) { y in
                        let top = y >= -2
                        if top != atTop { atTop = top }
                    }
                    .refreshable { await model.loadRails() }
                    .onChange(of: scrollToTop) { _, _ in
                        tabTapped(proxy)
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

    /// The DailyFAM tab tapped while showing: from View more back to the
    /// rails, reloaded; scrolled down, back to the top and nothing else; at
    /// the top, the page asked for afresh (`openMyFamTab`).
    private func tabTapped(_ proxy: ScrollViewProxy) {
        if !model.dailyPath.isEmpty {
            model.dailyPath = []
            Task { await model.loadRails() }
            return
        }
        if !atTop {
            withAnimation { proxy.scrollTo("top", anchor: .top) }
            return
        }
        Task { await model.loadRails() }
    }
}

/// Where the top of DailyFAM's page is, in its scroll view.
private struct DailyTopKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
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

/// `.offline-bar`: margin 0 16 10, padding 9 12, radius 12, the surface
/// with its border, Geist 12 muted - the web's words.
struct OfflineBar: View {
    var body: some View {
        Text("You\u{2019}re offline. Episodes you\u{2019}ve finished on this device still play.")
            .font(FAMFont.body(12))
            .foregroundStyle(FAMColor.textMuted)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 9)
            .padding(.horizontal, 12)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(FAMColor.border))
            .padding(.horizontal, 16)
            .padding(.bottom, 10)
    }
}

// MARK: - A rail

struct RailRow: View {
    @EnvironmentObject private var model: AppModel
    let rail: Rail
    /// Scrolled to its end (or never needed scrolling): the chevron gives
    /// way to VIEW MORE (`watchRailEnds`).
    @State private var atEnd = false
    @State private var viewport: CGFloat = 0
    @State private var endX: CGFloat = 0

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
                        // New tiles arrive one after another (§292); tiles
                        // already drawn on this phone do not arrive again.
                        ForEach(Array(rail.topics.enumerated()), id: \.element.id) { i, tile in
                            TileCard(tile: tile, railKey: rail.key, faces: model.friendFaces, width: 150)
                                .frame(maxHeight: .infinity, alignment: .top)
                                .arriving(tile.id, index: i, book: model.arrivals)
                        }
                        viewMore
                    }
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.trailing, 14)
                    .padding(.vertical, 2)
                }
                .coordinateSpace(name: "rail-" + rail.key)
                .background(GeometryReader { geo in
                    Color.clear
                        .onAppear {
                            viewport = geo.size.width
                            markEnd(width: geo.size.width, x: endX)
                        }
                        .onChange(of: geo.size.width) { _, width in
                            viewport = width
                            markEnd(width: width, x: endX)
                        }
                })
                .onPreferenceChange(RailEndKey.self) { maxX in
                    endX = maxX
                    markEnd(width: viewport, x: maxX)
                }
                .padding(.trailing, -18)
            }
            // The friends rail always ends in the way to find some (10.1 #2):
            // the Friends screen.
            if rail.key == "followers" {
                Button { model.social.open(.friends) } label: {
                    HStack(spacing: 5) {
                        WebIcon(path: Icons.plus, size: 12)
                        Text("Find new friends").font(FAMFont.body(12, .bold))
                    }
                    .foregroundStyle(FAMColor.ink)
                    .padding(.leading, 10)
                    .padding(.trailing, 13)
                    .frame(minHeight: 30)
                    .background(FAMColor.copper, in: Capsule())
                }
                .buttonStyle(.press)
                .padding(.top, 10)
                .accessibilityHint("Opens Friends")
            }
        }
    }

    /// Within 8 pt of the end, counting the rail's 14 pt of trailing room,
    /// as the web measures it - so a rail that never needed scrolling says
    /// VIEW MORE at once.
    private func markEnd(width: CGFloat, x: CGFloat) {
        let end = width > 0 && x > 0 && x + 14 <= width + 8
        if end != atEnd { atEnd = end }
    }

    /// `.feed-more`: a faint chevron while there is more to scroll, VIEW
    /// MORE once there is not; either way it opens the whole section.
    private var viewMore: some View {
        NavigationLink(value: rail.key) {
            ZStack {
                if atEnd {
                    Text("View\nmore")
                        .monoLabel(8.5, color: FAMColor.copper, tracking: 0.51)
                        .multilineTextAlignment(.center)
                        .lineSpacing(1.5)
                } else {
                    WebIcon(path: Icons.chevron, size: 14, line: 2.2)
                        .foregroundStyle(FAMColor.textMuted)
                        .opacity(0.5)
                }
            }
            .frame(width: 52)
            .frame(maxHeight: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .background(GeometryReader { geo in
            Color.clear.preference(key: RailEndKey.self,
                                   value: geo.frame(in: .named("rail-" + rail.key)).maxX)
        })
        .accessibilityLabel("View more of \(model.heading(for: rail))")
    }
}

/// Where a rail's end sits in its scroll view.
private struct RailEndKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

// MARK: - A card

/// `.seed-card`: picture (or, with none, the web's drawing) with its
/// subject chip, the save bookmark and ⋯ top right, "ready" top left, the
/// friends pill bottom right; then the whole title (stepped down when
/// long), the live line and the hook. `faces` is the face book of the page
/// the card came from - the rails' or this View more's (`friend_faces`).
struct TileCard: View {
    @EnvironmentObject private var model: AppModel
    let tile: Tile
    let railKey: String
    var faces: [FriendFace] = []
    var width: CGFloat? = 150

    @State private var menu = false
    @State private var friends = false

    private static let pillInk = Color(red: 42 / 255, green: 37 / 255, blue: 48 / 255)

    var body: some View {
        // Not a Button: the save and ⋯ buttons inside it must get their own taps.
        // Offline, a card whose episode is not on this phone is faded
        // (`not-offline`): the picture to .35 and greyed, the words to .5.
        let faded = model.isOffline && !model.isKept(tile)
        VStack(alignment: .leading, spacing: 0) {
            thumb
                .opacity(faded ? 0.35 : 1)
                .grayscale(faded ? 0.7 : 0)
            VStack(alignment: .leading, spacing: 0) {
                Text(tile.title)
                    .font(FAMFont.heading(titleSize, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .lineSpacing(titleSpacing)
                    .fixedSize(horizontal: false, vertical: true)
                if !tile.liveLine.isEmpty { liveLine.padding(.top, 5) }
                hook.padding(.top, 6)
            }
            .padding(.horizontal, 11)
            .padding(.top, 10)
            .padding(.bottom, 12)
            .opacity(faded ? 0.5 : 1)
            Spacer(minLength: 0)
        }
        .frame(width: width, alignment: .leading)
        .frame(maxWidth: width == nil ? .infinity : nil, alignment: .leading)
        .background(FAMColor.surface)
        .clipShape(RoundedRectangle(cornerRadius: 13))
        .overlay(RoundedRectangle(cornerRadius: 13).stroke(FAMColor.border))
        .contentShape(Rectangle())
        .onTapGesture { model.play(tile, railKey: railKey) }
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { model.play(tile, railKey: railKey) }
        .accessibilityHint("Plays the episode")
        .sheet(isPresented: $menu) { TileMenu(tile: tile) }
        .fullScreenCover(isPresented: $friends) {
            FriendsListened(count: tile.friendsListened, faces: shownFaces(limit: nil))
        }
    }

    private var titleSize: CGFloat {
        tile.title.count > 72 ? 10.5 : tile.title.count > 48 ? 11.5 : 12.5
    }

    /// Line heights 1.28, 1.25, 1.24 over the face's own (about 1.2).
    private var titleSpacing: CGFloat {
        tile.title.count > 72 ? titleSize * 0.04 : tile.title.count > 48 ? titleSize * 0.05 : titleSize * 0.08
    }

    /// The tile's friends, resolved against this page's face book.
    private func shownFaces(limit: Int?) -> [FriendFace] {
        var out: [FriendFace] = []
        for i in tile.friendsFaces where faces.indices.contains(i) {
            out.append(faces[i])
            if let limit, out.count >= limit { break }
        }
        return out
    }

    private var thumb: some View {
        let url = tile.thumb.isEmpty ? nil : model.api.picture(tile.thumb)
        return ZStack {
            if let url {
                Picture(url: url)
            } else {
                SeedThumb(icon: tile.icon)
            }
            // The subject chip bottom left and the friends who listened
            // bottom right, on one line and centred on each other (the
            // owner's call for iOS: the web lifts the faces above the chip).
            if !tile.tagText.isEmpty || tile.friendsListened > 0 {
                HStack(alignment: .center, spacing: 6) {
                    if !tile.tagText.isEmpty {
                        chip(onPicture: url != nil)
                    }
                    Spacer(minLength: 0)
                    if tile.friendsListened > 0 {
                        friendsPill.fixedSize()
                    }
                }
                .padding(.horizontal, url != nil ? 8 : 11)
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
            // Save and ⋯, top right: the web's 13 pt outline bookmark (an
            // outline on sage once saved) and its filled dots. Not drawn
            // offline (`.is-offline .seed-acts`): neither can reach FAM.
            if !model.isOffline {
            HStack(spacing: 5) {
                let saved = model.isSaved(tile)
                actButton(on: saved, label: saved ? "Take it off Save for Later" : "Save \(tile.title) for later") {
                    WebIcon(path: Icons.bookmark, size: 13, line: 2.2)
                } action: {
                    model.toggleSave(tile)
                }
                actButton(on: false, label: "More for \(tile.title)") {
                    WebFill(path: Icons.dots(2.2), size: 13)
                } action: {
                    menu = true
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
            .padding(6)
            }
        }
        // The whole picture, at its own 4:3 - never the web's 96 pt strip,
        // which cropped the top and bottom off every one (the owner, on iOS).
        .aspectRatio(4 / 3, contentMode: .fit)
        .clipped()
    }

    /// `.seed-tag`: plain words on the drawing; on a picture, a cream chip.
    @ViewBuilder private func chip(onPicture: Bool) -> some View {
        if onPicture {
            Text(tile.tagText)
                .monoLabel(8, color: Color(hex: 0x2A2530), tracking: 0.8)
                .lineLimit(2)
                .padding(.horizontal, 6)
                .padding(.top, 3)
                .padding(.bottom, 2)
                .background(Color(red: 237 / 255, green: 230 / 255, blue: 214 / 255).opacity(0.92),
                            in: RoundedRectangle(cornerRadius: 4))
        } else {
            Text(tile.tagText)
                .monoLabel(8, color: Color(hex: 0x5A5347), tracking: 0.8)
                .lineLimit(2)
        }
    }

    private func actButton<Icon: View>(on: Bool, label: String, @ViewBuilder icon: () -> Icon,
                                       action: @escaping () -> Void) -> some View {
        Button(action: action) {
            icon()
                .foregroundStyle(on ? FAMColor.ink : Color(hex: 0xF4EFE4))
                .frame(width: 24, height: 24)
                .background(on ? FAMColor.sage : Color(red: 26 / 255, green: 22 / 255, blue: 34 / 255).opacity(0.62),
                            in: Circle())
        }
        .buttonStyle(.press)
        .accessibilityLabel(label)
    }

    /// Up to three overlapped faces and a copper "+N" (the rest); a tap
    /// lists who listened and never plays the card. With no faces in the
    /// answer, the words alone, which do nothing (`seedFriendsHtml`).
    @ViewBuilder private var friendsPill: some View {
        let shown = shownFaces(limit: 3)
        let n = tile.friendsListened
        if shown.isEmpty {
            Text("\(n) friend\(n == 1 ? "" : "s") listened")
                .font(FAMFont.body(9, .semibold))
                .foregroundStyle(FAMColor.ink)
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(FAMColor.copper, in: RoundedRectangle(cornerRadius: 4))
        } else {
            let more = n - shown.count
            Button { friends = true } label: {
                HStack(spacing: 5) {
                    HStack(spacing: -6) {
                        ForEach(Array(shown.enumerated()), id: \.offset) { i, face in
                            Face(name: face.name, handle: face.handle ?? "", avatar: face.avatar ?? "",
                                 size: 22, initialsSize: 9)
                                // A ring in the pill's own colour, so each overlapped face reads whole.
                                .overlay(Circle().stroke(Self.pillInk.opacity(0.88), lineWidth: 1.5))
                                .zIndex(Double(3 - i))
                        }
                    }
                    if more > 0 {
                        Text("+\(more)")
                            .font(FAMFont.mono(10, bold: true))
                            .foregroundStyle(FAMColor.copper)
                    }
                }
                .padding(.leading, 3)
                .padding(.trailing, more > 0 ? 7 : 3)
                .padding(.vertical, 3)
                .frame(minHeight: 30)
                .background(Self.pillInk.opacity(0.88), in: Capsule())
                .overlay(Capsule().stroke(Color(red: 237 / 255, green: 230 / 255, blue: 214 / 255).opacity(0.18)))
            }
            .buttonStyle(.press)
            .accessibilityLabel(n == 1 ? "\(shown[0].name) listened" : "\(n) friends listened")
        }
    }

    /// A game's line, with how old it is; copper and dotted only while live
    /// and fresh (`seedLiveHtml`: in progress, and no time or under 20 min).
    private var liveLine: some View {
        let age = Date().timeIntervalSince1970 - tile.liveAsOf
        let fresh = tile.liveAsOf == 0 || age < 20 * 60
        let live = tile.liveStatus == "in_progress" && fresh
        var text = tile.liveLine
        if tile.liveAsOf > 0 {
            let mins = Int((age / 60).rounded())
            if mins >= 20 {
                text += mins < 90 ? " \u{00B7} \(mins) min ago"
                                  : " \u{00B7} \(Int((Double(mins) / 60).rounded())) h ago"
            }
        }
        return HStack(spacing: 5) {
            if live { Circle().fill(FAMColor.copper).frame(width: 6, height: 6) }
            Text(text)
                .font(FAMFont.mono(8.5, bold: live))
                .tracking(0.17)
                .foregroundStyle(live ? FAMColor.copper : FAMColor.textMuted)
                .lineLimit(1)
                .truncationMode(.tail)
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
            case "might_like": text = Text("Next to").bold().foregroundColor(FAMColor.copper) + Text(" your taste, not more of it")
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

/// A person's face: their picture, else initials on their colour - the
/// web's `personAvHTML`, coloured by `personHue(handle || name)` and
/// lettered by `deriveInitials(name || handle)`, as `PersonFace` is.
struct Face: View {
    let name: String
    let handle: String
    let avatar: String
    var size: CGFloat = 22
    /// The initials' size where the web gives one (else 40% of the face).
    var initialsSize: CGFloat? = nil

    @EnvironmentObject private var model: AppModel

    var body: some View {
        let key = handle.isEmpty ? name : handle
        let source = name.isEmpty ? (handle.isEmpty ? "?" : handle) : name
        let letters = PersonFace.initials(source)
        ZStack {
            Circle().fill(PersonFace.hue(key))
            Text(letters.isEmpty ? "?" : letters)
                .font(FAMFont.body(initialsSize ?? size * 0.4, .bold))
                .foregroundStyle(FAMColor.ink)
                .lineLimit(1)
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
    @State private var sharing: ShareSubject?
    @State private var queue = false
    @State private var addToMix = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                // The picture only when the tile has one (`openTileMenu`).
                if let url = model.api.picture(tile.thumb) {
                    Picture(url: url)
                        .frame(width: 44, height: 44)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                Text(tile.title)
                    .font(FAMFont.body(14, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(2)
            }
            .padding(.horizontal, 22)
            .padding(.vertical, 16)
            Divider()
            // "Share this FAM" for this tile's episode (`openShareModal`
            // from `openTileMenu`): to a friend or a group inside FAM, or
            // anywhere else.
            row("Share", Icons.menuShare) {
                sharing = ShareSubject(key: model.episode(for: tile).key, title: tile.title)
            }
            row("Add to myFAM Playlist", Icons.circlePlus) {
                if tile.query.isEmpty {
                    dismiss()
                    model.nowPlaying.say("Play something first")
                } else {
                    addToMix = true
                }
            }
            row("Add to Queue", Icons.addQueue) { dismiss(); model.addToQueue(tile) }
            row("Go to Queue", Icons.queue) { queue = true }
            row("Not interested", Icons.frown) { dismiss(); model.notInterested(tile) }
            Button("Cancel") { dismiss() }
                .font(FAMFont.body(14, .semibold))
                .foregroundStyle(FAMColor.textMuted)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 16)
        }
        .background(FAMColor.surface)
        // Room to grow at the larger text sizes (`large-text`).
        .presentationDetents([.medium, .large])
        .presentationBackground(FAMColor.surface)
        .sheet(item: $sharing, onDismiss: { dismiss() }) { subject in
            ShareSheet(subject: subject)
                .environmentObject(model)
                .environmentObject(model.nowPlaying)
        }
        .sheet(isPresented: $queue) {
            QueueSheet()
                .environmentObject(model)
                .environmentObject(model.nowPlaying)
        }
        .sheet(isPresented: $addToMix, onDismiss: { dismiss() }) {
            AddToMixSheet(query: tile.query, title: tile.title)
                .environmentObject(model)
                .environmentObject(model.mixes)
                .environmentObject(model.nowPlaying)
        }
    }

    /// A row with the web's `MENU_ICONS` glyph, 18 pt at stroke 1.8.
    private func row(_ label: String, _ icon: Path, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 14) {
                WebIcon(path: icon, size: 18, line: 1.8)
                    .foregroundStyle(FAMColor.textMuted)
                Text(label).font(FAMFont.body(13.5, .semibold)).foregroundStyle(FAMColor.text)
                Spacer()
            }
            .padding(.horizontal, 22)
            .padding(.vertical, 14)
            .contentShape(Rectangle())
        }
        .buttonStyle(.press)
    }
}

/// "N friends listened" (`#friendsListenedOverlay`, §394): a centred
/// card, not a sheet - the title, a bordered round close, and a row a
/// friend with their face, name and @handle; a row with a handle opens
/// that friend's profile once the card has closed.
struct FriendsListened: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let count: Int
    let faces: [FriendFace]

    var body: some View {
        ZStack {
            Color.clear
                .contentShape(Rectangle())
                .ignoresSafeArea()
                .onTapGesture { dismiss() }
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .center) {
                    Text(count == 1 ? "1 friend listened" : "\(count) friends listened")
                        .font(FAMFont.heading(16, .medium))
                        .foregroundStyle(FAMColor.text)
                    Spacer()
                    Button { dismiss() } label: {
                        WebIcon(path: Icons.close, size: 14, line: 2.4)
                            .foregroundStyle(FAMColor.text)
                            .frame(width: 30, height: 30)
                            .overlay(Circle().stroke(FAMColor.borderStrong))
                    }
                    .buttonStyle(.press)
                    .accessibilityLabel("Close")
                }
                .padding(.bottom, 10)
                ForEach(Array(faces.enumerated()), id: \.offset) { i, face in
                    Button { openProfile(face) } label: { listenedRow(i, face) }
                        .buttonStyle(.press)
                        .disabled((face.handle ?? "").isEmpty)
                }
            }
            .padding(20)
            .frame(width: 312)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18).stroke(FAMColor.borderStrong))
            .shadow(color: .black.opacity(0.35), radius: 20, y: 20)
        }
        .presentationBackground(Color(hex: 0x0A090F).opacity(0.62))
    }

    private func openProfile(_ face: FriendFace) {
        let handle = face.handle ?? ""
        guard !handle.isEmpty else { return }
        dismiss()
        model.social.openAfterClose(.person(PersonOpen(handle: handle, name: face.name, avatar: face.avatar ?? "")))
    }

    private func listenedRow(_ i: Int, _ face: FriendFace) -> some View {
        HStack(spacing: 11) {
            Face(name: face.name, handle: face.handle ?? "", avatar: face.avatar ?? "",
                 size: 30, initialsSize: 12)
            VStack(alignment: .leading, spacing: 4) {
                Text(face.name.isEmpty ? "@" + (face.handle ?? "") : face.name)
                    .font(FAMFont.body(12, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(1)
                if let h = face.handle, !h.isEmpty {
                    Text("@" + h).monoLabel(8, color: FAMColor.textFaint, tracking: 0.48)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 10)
        .overlay(alignment: .bottom) {
            if i < faces.count - 1 {
                Rectangle().fill(FAMColor.border).frame(height: 1)
            }
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
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
        // A tap on the card, not a Button around it: the X inside is its own
        // button and must never also play the card.
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
            .contentShape(Rectangle())
            .onTapGesture(perform: play)
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isButton)
            .accessibilityAction(.default, play)
            .overlay(alignment: .topTrailing) {
                Button { model.dismissPickUp(query: dismissKey) } label: {
                    WebIcon(path: Icons.close, size: 9, line: 3)
                        .foregroundStyle(FAMColor.textMuted)
                        .frame(width: 20, height: 20)
                        .contentShape(Circle())
                }
                .buttonStyle(.press)
                .padding(5)
                .accessibilityLabel("Don't offer this again")
            }
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
    /// Counts the pages dealt, so each one scrolls back to the top.
    @State private var dealt = 0

    /// Made for you cycles through what is written (`SECTION_CYCLES`).
    private var cycles: Bool { key == "from_history" }

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
                    // Wraps rather than clips at a larger text size
                    // (`large-text`, §499): never cut to two lines by Refresh.
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
                if canRefresh {
                    Button {
                        guard !loading else { return }
                        Task { await load() }
                    } label: {
                        HStack(spacing: 6) {
                            WebIcon(path: Icons.refresh, size: 13, line: 2.4)
                                .rotationEffect(.degrees(loading ? 360 : 0))
                                .animation(loading ? .linear(duration: 0.6).repeatForever(autoreverses: false) : .default,
                                           value: loading)
                            Text("Refresh").font(FAMFont.body(12.5, .semibold))
                                .fixedSize()
                        }
                        .foregroundStyle(FAMColor.text)
                        .padding(.horizontal, 13)
                        .frame(minHeight: 32)
                        .overlay(Capsule().stroke(FAMColor.borderStrong))
                    }
                    .accessibilityLabel("Show different episodes")
                }
            }
            .padding(.horizontal, 18)
            .padding(.top, 16)
            .padding(.bottom, 10)
            ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 11) {
                    Color.clear.frame(height: 0).id("sectionTop")
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
            // Every page opens at its top (`loadSectionPage`).
            .onChange(of: dealt) { _, _ in
                proxy.scrollTo("sectionTop", anchor: .top)
            }
            }
        }
        .background(FAMColor.background)
        .toolbar(.hidden, for: .navigationBar)
        .task { if page == nil { await load() } }
    }

    private var title: String {
        if key == "from_history" { return model.madeForTitle(multiline: false) }
        let known = AppModel.sectionTitle(key)
        return known.isEmpty ? "More" : known
    }

    /// Offered when a refresh can show something new; a cycling rail until
    /// it is caught up (`renderSection`).
    private var canRefresh: Bool {
        guard let page else { return false }
        if cycles { return page.caughtUp != true }
        return (page.total ?? 0) > page.topics.count
    }

    private var reason: String {
        if failed { return "Could not load this section. Everything else still works, and this fills in when the server is back." }
        guard let page else { return "Loading…" }
        if cycles && page.caughtUp == true { return "" }
        if page.topics.isEmpty && page.caughtUp != true { return page.emptyReason ?? "Nothing in this section yet." }
        let ready = page.ready ?? 0
        let line = ready == 0 ? "" : ready == 1 ? "1 of these plays instantly." : "\(ready) of these play instantly."
        return [wrappedNote, line].filter { !$0.isEmpty }.joined(separator: " ")
    }

    /// Two across; "Not interested" takes a card off here as well as off
    /// the rails. Faces resolve against this page's own `friend_faces`.
    private func grid(_ tiles: [Tile]) -> some View {
        let shown = tiles.filter { !model.notInterestedIDs.contains($0.id) }
        let faces = page?.friendFaces ?? []
        return LazyVGrid(columns: [GridItem(.flexible(), spacing: 11), GridItem(.flexible())], spacing: 11) {
            ForEach(shown) { tile in
                TileCard(tile: tile, railKey: key, faces: faces, width: nil)
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
                guard !loading else { return }
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
        // Made for you's first page starts with the rail's own tiles, so
        // it is never "caught up" before they are shown (`loadSectionPage`).
        let head = (cycles && seen.isEmpty)
            ? (model.rails.first(where: { $0.key == key })?.topics ?? []) : []
        do {
            var next = try await model.api.section(key, seen: seen + head.map(\.id),
                                                   pageSize: max(1, Self.pageSize - head.count))
            failed = false
            if !head.isEmpty {
                let topics = head + next.topics
                next = SectionPage(key: next.key, topics: topics, emptyReason: next.emptyReason,
                                   ready: topics.filter(\.cached).count, total: next.total,
                                   more: next.more, wrapped: next.wrapped, caughtUp: false,
                                   friendFaces: next.friendFaces, groups: next.groups)
            }
            let before = seen.count
            if next.wrapped == true { seen = [] }
            for tile in next.topics where !seen.contains(tile.id) { seen.append(tile.id) }
            if next.wrapped == true {
                wrappedNote = "That was all \(next.total ?? next.topics.count) for this rail, so these are the first again."
            } else if next.more == false && !cycles && before > 0 {
                // Never on Made for you, which ends "all caught up" instead.
                wrappedNote = "The last of this rail. Refresh starts again from the top."
            } else {
                wrappedNote = ""
            }
            page = next
            dealt += 1
        } catch {
            failed = true
        }
    }
}
