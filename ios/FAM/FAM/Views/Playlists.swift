import SwiftUI

// Explore | Playlists on the iPhone (§691, the owner's 11.24 mock; built
// for build 12, §692). After the web's `exploreCardHTML`, `playlistCardHTML`,
// `playlistRowHTML`, `paintMixHead` and `playlistPageOrder`, to the web's
// measurements (PARITY.md).

// MARK: - Explore's card

/// **A story on Explore** (`.xp-card`, `exploreCardHTML`): its picture small
/// beside its category, its title, its one line, then **FAM** (plays it,
/// replay only, the rest of the page queued after it) and **Add to
/// playlist** (the player's own sheet, for this story's topic). A story
/// with no stored summary draws no line (`no-dead-controls`).
struct ExploreCard: View {
    @EnvironmentObject private var model: AppModel
    let episode: CategoryEpisode
    /// "SPORT · NBA" (`exploreKicker`).
    let kicker: String
    /// The facet's colour behind a missing picture (`categoryTone`).
    let tone: Color
    let play: () -> Void
    let add: () -> Void

    private var name: String { episode.title.isEmpty ? episode.query : episode.title }

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 18)
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 9) {
                thumb
                Text(kicker)
                    .font(FAMFont.mono(9.5))
                    .tracking(1.33)
                    .textCase(.uppercase)
                    .foregroundStyle(FAMColor.textFaint)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .padding(.bottom, 9)
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                if episode.explicit == true { ExplicitBadge() }
                Text(name)
                    .font(FAMFont.heading(17, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let hook = episode.hook, !hook.isEmpty {
                Text(hook)
                    .font(FAMFont.body(13))
                    .foregroundStyle(FAMColor.textMuted)
                    .lineSpacing(3)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 7)
            }
            // FAM on the left, Add to playlist on the right; one under the
            // other when the row is too narrow for both (`flex-wrap`).
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) {
                    playButton
                    Spacer(minLength: 0)
                    addButton
                }
                VStack(alignment: .leading, spacing: 10) {
                    playButton
                    addButton
                }
            }
            .padding(.top, 13)
        }
        .padding(.horizontal, 14)
        .padding(.top, 14)
        .padding(.bottom, 15)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(FAMColor.surface, in: shape)
        .overlay(shape.stroke(FAMColor.border))
        .padding(.bottom, 12)
    }

    /// `.xp-thumb`: 34 pt, the facet's colour under the picture.
    private var thumb: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 9).fill(tone)
            if let source = episode.thumb, !source.isEmpty, let url = model.api.picture(source) {
                AsyncImage(url: url) { image in
                    image.resizable().scaledToFill()
                } placeholder: { Color.clear }
            }
        }
        .frame(width: 34, height: 34)
        .clipShape(RoundedRectangle(cornerRadius: 9))
        .accessibilityHidden(true)
    }

    /// `.xp-play`: the play triangle and the wordmark's three letters,
    /// dark on light in dark mode and light on dark in light.
    private var playButton: some View {
        Button(action: play) {
            HStack(spacing: 8) {
                PlayTriangle().fill(FAMColor.background).frame(width: 12, height: 12)
                FAMLetters(capHeight: 13, color: FAMColor.background)
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 8)
            .frame(minWidth: 98, minHeight: 40)
            .background(FAMColor.text, in: Capsule())
            .contentShape(Capsule())
        }
        .buttonStyle(.press)
        .accessibilityLabel("Play \(name)")
    }

    /// `.xp-add`.
    private var addButton: some View {
        Button(action: add) {
            HStack(spacing: 7) {
                WebIcon(path: Icons.plus, size: 13, line: 2.2)
                Text("Add to playlist")
                    .font(FAMFont.body(13, .semibold))
                    .lineLimit(1)
                    .fixedSize()
            }
            .foregroundStyle(FAMColor.text)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .frame(minHeight: 40)
            .overlay(Capsule().stroke(FAMColor.borderStrong))
            .contentShape(Capsule())
        }
        .buttonStyle(.press)
        .accessibilityLabel("Add \(name) to a playlist")
    }
}

extension MixesModel {
    /// The facet a category sits under (`categoryFacetOf`): its own id when
    /// it is a facet, else the tree's word; "" when the tree is not here.
    func facet(of node: String) -> String {
        guard !node.isEmpty, let page = categories else { return "" }
        if page.facets.contains(where: { $0.id == node }) { return node }
        return page.all.first(where: { $0.id == node })?.facet ?? ""
    }

    /// A category's name (`categoryLabelOf`), "" when unknown.
    func categoryLabel(_ id: String) -> String {
        guard !id.isEmpty, let page = categories else { return "" }
        if let facet = page.facets.first(where: { $0.id == id }) { return facet.label }
        return page.all.first(where: { $0.id == id })?.label ?? ""
    }

    /// "SPORT · NBA" (`exploreKicker`): the facet, then the category when
    /// it is a narrower one. The text is upper-cased where it is drawn.
    func exploreKicker(_ episode: CategoryEpisode) -> String {
        let top: String = categoryLabel(facet(of: episode.node ?? ""))
        let own: String = episode.nodeLabel ?? ""
        if !top.isEmpty && !own.isEmpty && top.lowercased() != own.lowercased() {
            return top + " \u{00B7} " + own
        }
        return own.isEmpty ? top : own
    }
}

// MARK: - A playlist's card

extension Mix {
    /// "Startups, AI, NBA and 4 more" (`playlistTopicsLine`): the topics
    /// in today's order.
    var topicsLine: String {
        let names: [String] = playOrder.map { items[$0].title }.filter { !$0.isEmpty }
        if names.isEmpty { return "No topics yet" }
        if names.count <= 3 { return names.joined(separator: ", ") }
        return names.prefix(3).joined(separator: ", ") + " and \(names.count - 3) more"
    }

    /// "trending" or "update" when the card's lead says so (§545), else "".
    var leadWhy: String {
        guard let why = headline?.why, why == "update" || why == "trending" else { return "" }
        return why
    }
}

/// **A playlist's card** (`.pl-card`, `playlistCardHTML`; §691, after
/// §616's stacked deck): one row - today's lead picture or the playlist's
/// own photo, its name, "7 BRIEFINGS · DAILY", its topics in today's order
/// and a play button that starts it from the top without opening it.
struct PlaylistCard: View {
    let mix: Mix
    /// "New today" (§550): until the playlist is played.
    let fresh: Bool
    let open: () -> Void
    let play: () -> Void

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 18)
        HStack(alignment: .center, spacing: 13) {
            // `.pl-art`: the playlist's colour under the folder, else its picture.
            MixArt(cover: mix.shownCover, size: 72, radius: 14,
                   tile: MixDeckHue.color(mix.id), glyph: Color(hex: 0x18151F).opacity(0.6),
                   glyphSize: 26)
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(mix.name)
                        .font(FAMFont.heading(17, .semibold))
                        .foregroundStyle(FAMColor.text)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    if fresh {
                        Text("New today")
                            .font(FAMFont.body(9.5, .bold))
                            .foregroundStyle(FAMColor.ink)
                            .padding(.horizontal, 7)
                            .padding(.vertical, 2)
                            .background(FAMColor.copper, in: RoundedRectangle(cornerRadius: 8))
                            .fixedSize()
                    }
                }
                meta
                Text(mix.topicsLine)
                    .font(FAMFont.body(12.5))
                    .foregroundStyle(FAMColor.textMuted)
                    .lineSpacing(2)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if !mix.items.isEmpty {
                // `.pl-play`: 40 pt, with 4 pt more to hit all round.
                Button(action: play) {
                    PlayTriangle().fill(FAMColor.text).frame(width: 13, height: 13)
                        .frame(width: 40, height: 40)
                        .overlay(Circle().stroke(FAMColor.borderStrong))
                        .padding(4)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.press)
                .padding(-4)
                .accessibilityLabel("Play \(mix.name)")
            }
        }
        .padding(13)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(FAMColor.surface, in: shape)
        .overlay(shape.stroke(FAMColor.border))
        .contentShape(shape)
        // Not a Button: the card holds its own play, and a tap on that must
        // not open the playlist as well.
        .onTapGesture { open() }
        .tapSqueeze()
        .accessibilityElement(children: .contain)
        .accessibilityLabel(mix.name)
        .accessibilityAddTraits(.isButton)
        .padding(.bottom, 11)
    }

    /// `.pl-meta`: "7 BRIEFINGS · DAILY", then what its lead is in the
    /// signal's own colour (§545).
    private var meta: some View {
        let why: String = mix.leadWhy
        let base: String = MixesModel.briefings(mix.items.count) + " \u{00B7} daily"
        let lead: Text = Text(why.isEmpty ? base : base + " \u{00B7} ")
            .font(FAMFont.mono(9))
            .foregroundColor(FAMColor.textFaint)
        let word: Text = Text(why.isEmpty ? "" : (why == "update" ? "Major update" : "Trending"))
            .font(FAMFont.mono(9, bold: true))
            .foregroundColor(FAMColor.signal(why == "update"))
        return (lead + word)
            .tracking(1.26)
            .textCase(.uppercase)
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
    }
}

// MARK: - A playlist's page

/// The play or pause drawn in whatever colour its parent sets
/// (`PL_PLAY_SVG`, `PL_PAUSE_SVG`).
struct PlaylistGlyph: View {
    let pause: Bool
    var size: CGFloat = 13

    var body: some View {
        ZStack {
            if pause {
                HStack(spacing: size * 0.15) {
                    RoundedRectangle(cornerRadius: size * 0.06)
                        .frame(width: size * 0.175, height: size * 0.625)
                    RoundedRectangle(cornerRadius: size * 0.06)
                        .frame(width: size * 0.175, height: size * 0.625)
                }
            } else {
                PlayTriangle()
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

/// The sliders that open a playlist's menu (`.pl-tune`), path for path.
enum PlaylistIcons {
    static let sliders: Path = {
        var p = Path()
        let lines: [(CGFloat, CGFloat, CGFloat, CGFloat)] = [
            (6, 4, 6, 9), (6, 13, 6, 20), (12, 4, 12, 13), (12, 17, 12, 20), (18, 4, 18, 7), (18, 11, 18, 20),
            (3.5, 11, 8.5, 11), (9.5, 15, 14.5, 15), (15.5, 9, 20.5, 9),
        ]
        for line in lines {
            p.move(to: CGPoint(x: line.0, y: line.1))
            p.addLine(to: CGPoint(x: line.2, y: line.3))
        }
        return p
    }()
}

/// The page's header (`.pl-head`, `paintMixHead`): "myFAM Playlist" over
/// "MORNING · NOV 23 · 7 STORIES", Back before it and the sliders after.
/// At the accessibility text sizes the heading takes a row of its own, so
/// nothing on its row can squeeze it (`large-text`).
struct PlaylistHead<Trailing: View>: View {
    let name: String
    let count: Int
    let back: () -> Void
    @ViewBuilder var trailing: () -> Trailing
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        Group {
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 10) {
                        backButton
                        Spacer(minLength: 0)
                        trailing()
                    }
                    heading.padding(.horizontal, 8)
                }
            } else {
                HStack(spacing: 10) {
                    backButton
                    heading
                    trailing()
                }
            }
        }
        .padding(.horizontal, 10)
        .padding(.bottom, 14)
        .frame(minHeight: 48)
    }

    private var backButton: some View {
        Button(action: back) {
            WebIcon(path: Icons.back, size: 20, line: 2.6).foregroundStyle(FAMColor.text)
                .frame(width: 36, height: 36)
                .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .accessibilityLabel("Back")
    }

    private var heading: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .firstTextBaseline, spacing: 7) {
                Wordmark(lead: "my", size: 25)
                Text("Playlist")
                    .font(FAMFont.heading(25, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("myFAM Playlist")
            .accessibilityAddTraits(.isHeader)
            Text(Self.sub(name: name, count: count))
                .font(FAMFont.mono(9))
                .tracking(1.44)
                .textCase(.uppercase)
                .foregroundStyle(FAMColor.textFaint)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// "Morning · Nov 23 · 7 stories", the device's date.
    static func sub(name: String, count: Int) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US")
        f.dateFormat = "MMM d"
        let stories: String = count == 1 ? "1 story" : "\(count) stories"
        return name + " \u{00B7} " + f.string(from: Date()) + " \u{00B7} " + stories
    }
}

/// The page's one button (`.mix-play.pl-pill`): "Play Morning · 7 stories",
/// or - while this playlist plays - "Playing Morning · 2 of 7", which
/// pauses it, and "Paused Morning · 2 of 7", which carries on.
struct PlaylistPill: View {
    @EnvironmentObject private var player: StreamPlayer
    let name: String
    /// Where the playlist has got to, from 1; nil when it is not playing.
    let at: Int?
    let count: Int
    let action: () -> Void

    var body: some View {
        let live: Bool = at != nil
        let playing: Bool = live && player.playing
        Button(action: action) {
            HStack(spacing: 8) {
                PlaylistGlyph(pause: playing, size: 13)
                Text(label(playing))
                    .font(FAMFont.body(14.5, .bold))
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .foregroundStyle(FAMColor.copper)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, minHeight: 50)
            .background(live ? FAMColor.text.opacity(0.04) : Color.clear, in: Capsule())
            .overlay(Capsule().stroke(FAMColor.copper, lineWidth: 1.5))
            .contentShape(Capsule())
        }
        .buttonStyle(.press)
    }

    private func label(_ playing: Bool) -> String {
        if let at {
            return (playing ? "Playing " : "Paused ") + name + " \u{00B7} \(at) of \(count)"
        }
        return "Play " + name + " \u{00B7} " + (count == 1 ? "1 story" : "\(count) stories")
    }
}

/// What a row of a playlist's page is, as it plays.
enum PlaylistRowState: Equatable {
    /// Not playing, and not heard today.
    case none
    case heard
    case now
    case next
}

/// One row of the page: the topic and its state.
struct PlaylistEntry: Identifiable, Equatable {
    let item: MixItem
    let state: PlaylistRowState
    var id: String { item.id }
}

extension Mix {
    /// **The rows of a playlist's page, in the order they play**
    /// (`playlistPageOrder`): today's order, best first (§616), each one
    /// heard today marked - or, while this playlist plays, the order it is
    /// playing, less what has since been taken out of it. Whatever the
    /// playlist holds that neither order names follows, so a topic still
    /// being added, or added while it plays, is on the page.
    func pageRows(live list: MixPlaylist?) -> [PlaylistEntry] {
        var held: [String: MixItem] = [:]
        for item in items { held[item.id] = item }
        var seen: Set<String> = []
        var out: [PlaylistEntry] = []
        if let list {
            for (n, index) in list.order.enumerated() {
                guard index >= 0, index < list.items.count else { continue }
                let was: MixItem = list.items[index]
                if held[was.id] == nil && n != list.pos { continue }
                if seen.contains(was.id) { continue }
                seen.insert(was.id)
                let state: PlaylistRowState = n < list.pos ? .heard : (n == list.pos ? .now : .next)
                out.append(PlaylistEntry(item: held[was.id] ?? was, state: state))
            }
        } else {
            for index in playOrder {
                let item: MixItem = items[index]
                if seen.contains(item.id) { continue }
                seen.insert(item.id)
                out.append(PlaylistEntry(item: item, state: item.heardToday ? .heard : .none))
            }
        }
        for item in items where !seen.contains(item.id) {
            seen.insert(item.id)
            out.append(PlaylistEntry(item: item, state: .none))
        }
        return out
    }
}

/// **One story of a playlist** (`.pl-row`, `playlistRowHTML`): its picture
/// with the play (or pause) on it, "UP NEXT · AI" over today's title and
/// its one line, and a heart that keeps it in Saved for Later - the pointer
/// the player's own save keeps (`save-pointer`), never a like and nothing
/// anybody else sees. One already heard is a single quiet line with a tick;
/// a tap plays it again.
struct PlaylistRow: View {
    @EnvironmentObject private var player: StreamPlayer
    let item: MixItem
    let state: PlaylistRowState
    /// Who added it, on a joint playlist.
    let adder: MixPerson?
    let saved: Bool
    /// The row or its picture tapped: plays from here - or, on the one
    /// playing, pauses and carries on.
    let play: () -> Void
    let save: () -> Void

    private static let cream = Color(hex: 0xF4EFE4)

    private var name: String { item.shownTitle }

    /// "UP NEXT · AI": the state, then the topic unless the title is it.
    private var kick: String {
        var parts: [String] = []
        switch state {
        case .now: parts.append("Now playing")
        case .next: parts.append("Up next")
        case .heard: parts.append("Heard")
        case .none: break
        }
        if !item.episodeTitle.isEmpty && !item.title.isEmpty { parts.append(item.title) }
        return parts.joined(separator: " \u{00B7} ")
    }

    /// The one line: today's episode's, else the topic's own while the row
    /// still shows the topic.
    private var hook: String {
        if !item.episodeHook.isEmpty { return item.episodeHook }
        return item.episodeTitle.isEmpty ? item.subtitle : ""
    }

    /// A changed topic's signal (§545): one that carries both.
    private var signal: MixSignal? { item.since == nil ? nil : item.signal }

    private var addedBy: String {
        guard let adder else { return "" }
        if adder.you == true { return "Added by you" }
        if let who = adder.name, !who.isEmpty { return "Added by " + who }
        if let handle = adder.handle, !handle.isEmpty { return "Added by @" + handle }
        return ""
    }

    var body: some View {
        if state == .heard {
            heard
        } else {
            full
        }
    }

    private var heard: some View {
        HStack(alignment: .center, spacing: 13) {
            picture(side: 40, radius: 10)
            VStack(alignment: .leading, spacing: 2) {
                kicker
                Text(name)
                    .font(FAMFont.body(13))
                    .foregroundStyle(FAMColor.textMuted)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 10)
        .overlay(alignment: .bottom) { Rectangle().fill(FAMColor.border).frame(height: 1) }
        .contentShape(Rectangle())
        .onTapGesture { play() }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
        .accessibilityHint("Plays it again")
    }

    private var full: some View {
        let now: Bool = state == .now
        let shape = RoundedRectangle(cornerRadius: 16)
        return HStack(alignment: .top, spacing: 13) {
            picture(side: 76, radius: 13)
            VStack(alignment: .leading, spacing: 0) {
                if let signal { MixSignalTag(signal: signal).padding(.bottom, 3) }
                if !kick.isEmpty { kicker }
                Text(name)
                    .font(FAMFont.heading(15.5, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, kick.isEmpty ? 0 : 4)
                if !hook.isEmpty {
                    Text(hook)
                        .font(FAMFont.body(12.5))
                        .foregroundStyle(FAMColor.textMuted)
                        .lineSpacing(3)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 5)
                }
                if let adder, !addedBy.isEmpty {
                    HStack(spacing: 6) {
                        PersonFace(person: adder, size: 18)
                        Text(addedBy)
                            .font(FAMFont.body(11))
                            .foregroundStyle(FAMColor.textFaint)
                    }
                    .padding(.top, 6)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            heart
        }
        // `.pl-row.now`: the one playing is a card of its own.
        .padding(.horizontal, now ? 10 : 18)
        .padding(.vertical, now ? 13 : 14)
        .background(now ? FAMColor.surface : Color.clear, in: shape)
        .overlay { if now { shape.stroke(FAMColor.borderStrong) } }
        // A changed topic's edge, in the signal's colour (`.has-sig`).
        .overlay(alignment: .leading) {
            if let signal, !now {
                Rectangle().fill(FAMColor.signal(signal.isUpdate)).frame(width: 3)
            }
        }
        .padding(now ? 8 : 0)
        .overlay(alignment: .bottom) {
            if !now { Rectangle().fill(FAMColor.border).frame(height: 1) }
        }
        .contentShape(Rectangle())
        // The row plays from here; the one playing is moved by its picture.
        .onTapGesture { if !now { play() } }
    }

    private var kicker: some View {
        Text(kick)
            .font(FAMFont.mono(9))
            .tracking(1.26)
            .textCase(.uppercase)
            .foregroundStyle(state == .now ? FAMColor.copper : FAMColor.textFaint)
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
    }

    /// `.pl-row-pic`: the story's picture (the subject's icon without one)
    /// under the play, the pause or - once heard - the tick.
    private func picture(side: CGFloat, radius: CGFloat) -> some View {
        let now: Bool = state == .now
        let pausing: Bool = now && player.playing
        let label: String = now ? (pausing ? "Pause" : "Play") : "Play \(name)"
        return Button(action: play) {
            ZStack {
                RoundedRectangle(cornerRadius: radius).fill(FAMColor.surface2)
                if item.picture.isEmpty {
                    SubjectIcon(name: item.icon, size: side * 0.42)
                        .foregroundStyle(FAMColor.textMuted)
                        .opacity(0.35)
                } else {
                    MixPicture(source: item.picture)
                        .frame(width: side, height: side)
                        .opacity(state == .heard ? 0.45 : 1)
                }
                if state == .heard {
                    WebIcon(path: Icons.check, size: 15, line: 3)
                        .foregroundStyle(item.picture.isEmpty ? FAMColor.textMuted : Self.cream)
                } else {
                    PlaylistGlyph(pause: pausing, size: 13)
                        .foregroundStyle(now ? FAMColor.ink : Self.cream)
                        .frame(width: 36, height: 36)
                        .background(now ? FAMColor.copper : Color(hex: 0x100E16).opacity(0.55), in: Circle())
                        .overlay(Circle().stroke(now ? FAMColor.copper : Self.cream.opacity(0.85), lineWidth: 1.5))
                }
            }
            .frame(width: side, height: side)
            .clipShape(RoundedRectangle(cornerRadius: radius))
            .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .accessibilityLabel(label)
    }

    /// `.pl-heart`: 32 pt, 6 pt more to hit all round.
    private var heart: some View {
        Button(action: save) {
            ZStack {
                if saved { WebFill(path: Icons.heart, size: 19) }
                WebIcon(path: Icons.heart, size: 19, line: 2)
            }
            .foregroundStyle(saved ? FAMColor.copper : FAMColor.textMuted)
            .frame(width: 32, height: 32)
            .padding(6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .padding(-6)
        .padding(.top, -5)
        .padding(.trailing, -6)
        .accessibilityLabel(saved ? "Take it off Saved for Later" : "Save for later")
        .accessibilityAddTraits(saved ? .isSelected : [])
    }
}
