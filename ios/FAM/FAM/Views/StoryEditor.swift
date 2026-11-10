import AVFoundation
import Speech
import SwiftUI

// VIBE! as a story (§213) and a story with no episode (§297), ported from
// the web's editor (`openVibeComposer`, `openNoteComposer`, the `vcs*`
// functions) and its one renderer (`storyCanvasHTML` / `noteCanvasHTML`).
// Everything the listener does is a layout - where the episode's picture
// sits and how big, its frame, the words' face, size and place, stickers,
// @tags, a note's background - sent with the vibe and drawn from it by the
// same `StoryCanvas` wherever a story is drawn (`vibe-caption`): a clamped
// layout, never an image. The editor and stories stay dark in both themes,
// with dark's gold (`StoryPalette`), as on the web.

// MARK: - Colours and faces (fixed: a story looks the same in both themes)

enum StoryPalette {
    static let paper = Color(hex: 0xF4EFE4)
    static let dark = Color(hex: 0x1A1620)
    static let gold = Color(hex: 0xE0B563)
    static let ink = Color(hex: 0x18151F)
    /// `.vcs`'s background.
    static let editorBack = Color(hex: 0x0E0C12)
    /// The round tool buttons and the caption box: rgba(14,12,18,.55).
    static let glass = Color(hex: 0x0E0C12).opacity(0.55)
    /// The font pills: rgba(14,12,18,.6).
    static let glass60 = Color(hex: 0x0E0C12).opacity(0.6)
    /// `.vcs-panel`: rgba(20,17,26,.97).
    static let panel = Color(hex: 0x14111A).opacity(0.97)
    /// `.vcs-aud`.
    static let pill = Color(hex: 0x26212D)
    static let pictureBack = Color(hex: 0x2A2233)
    static let postBack = Color(hex: 0x1A1620)
    /// The listening Speak button (`--coral`, dark's).
    static let coral = Color(hex: 0xE2694F)
    /// A story's canvas behind an episode (`.story-canvas`).
    static let canvas = LinearGradient(colors: [Color(hex: 0x3A2F44), Color(hex: 0x1C1722)],
                                       startPoint: .top, endPoint: .bottom)

    /// `NOTE_BACKGROUNDS` (`social.NOTE_BACKGROUNDS`), in their order.
    static let backgrounds = ["ink", "copper", "sunset", "ocean", "forest", "berry", "dusk", "cream", "night", "candy"]

    /// A note's background by name, the gradients at 160°; an unknown name
    /// is the first (`noteBackground`).
    @ViewBuilder static func background(_ name: String) -> some View {
        let stops = Self.stops(name)
        if stops.count == 1 {
            stops[0].color
        } else {
            LinearGradient(stops: stops, startPoint: UnitPoint(x: 0.33, y: 0.03), endPoint: UnitPoint(x: 0.67, y: 0.97))
        }
    }

    static func stops(_ name: String) -> [Gradient.Stop] {
        func s(_ hex: UInt32, _ at: CGFloat) -> Gradient.Stop { Gradient.Stop(color: Color(hex: hex), location: at) }
        switch name {
        case "copper": return [s(0xC08A2E, 0)]
        case "sunset": return [s(0xFEDA75, 0), s(0xFA7E1E, 0.35), s(0xD62976, 0.75), s(0x962FBF, 1)]
        case "ocean": return [s(0x5EE7DF, 0), s(0x3A7BD5, 0.55), s(0x283C86, 1)]
        case "forest": return [s(0xA8E063, 0), s(0x3E8E5E, 0.55), s(0x134E3A, 1)]
        case "berry": return [s(0xF78CA0, 0), s(0xC43A79, 0.5), s(0x5B1A5E, 1)]
        case "dusk": return [s(0x3A2F44, 0), s(0x6B4C7A, 0.5), s(0xE0B563, 1)]
        case "cream": return [s(0xF4EFE4, 0)]
        case "night": return [s(0x000000, 0)]
        case "candy": return [s(0xFBC2EB, 0), s(0xA6C1EE, 1)]
        default: return [s(0x18151F, 0)]
        }
    }
}

/// The story's typefaces at a fixed size: a layout is measured in 360ths
/// of its canvas, so its words do not grow with the phone's text size.
enum StoryFonts {
    /// The caption's faces (`.sc-cap.f-*`, `STORY_FONTS`).
    static func face(_ name: String, size: CGFloat) -> Font {
        switch name {
        case "strong": return .custom("BricolageGrotesque-Bold", fixedSize: size)
        case "type": return .custom("GeistMono-Regular", fixedSize: size)
        case "serif": return .custom("Georgia-Italic", fixedSize: size)
        case "script": return .custom("SnellRoundhand", fixedSize: size)
        default: return .custom("Geist-SemiBold", fixedSize: size)
        }
    }

    static func heading(_ size: CGFloat) -> Font { .custom("BricolageGrotesque-Medium", fixedSize: size) }
    static func mono(_ size: CGFloat, bold: Bool = false) -> Font {
        .custom(bold ? "GeistMono-Bold" : "GeistMono-Regular", fixedSize: size)
    }
    static func tag(_ size: CGFloat) -> Font { .custom("Geist-Bold", fixedSize: size) }
}

/// A choice and its words (`STORY_FONTS`, `STORY_FRAMES`).
struct StoryChoice: Hashable {
    let key: String
    let label: String
}

/// The five faces, the four frames and the stickers, as the web lists them.
enum StoryChoices {
    static let fonts: [StoryChoice] = [StoryChoice(key: "classic", label: "Classic"),
                                       StoryChoice(key: "strong", label: "Strong"),
                                       StoryChoice(key: "type", label: "Typewriter"),
                                       StoryChoice(key: "serif", label: "Serif"),
                                       StoryChoice(key: "script", label: "Script")]
    static let frames: [StoryChoice] = [StoryChoice(key: "card", label: "Card"),
                                        StoryChoice(key: "round", label: "Rounded"),
                                        StoryChoice(key: "circle", label: "Circle"),
                                        StoryChoice(key: "full", label: "Full")]
    /// `social.STORY_STICKERS`: anything else is dropped by the server.
    static let stickers: [String] = [
        "\u{1F525}", "\u{1F92F}", "\u{1F602}", "\u{1F64C}", "\u{1F440}",
        "\u{1F4AF}", "\u{2764}\u{FE0F}", "\u{1F3A7}", "\u{1F9E0}", "\u{1F44F}",
        "\u{1F62E}", "\u{2728}", "\u{1F3C6}", "\u{1F4C8}", "\u{1F30D}", "\u{1F914}",
        "MUST LISTEN", "HOT TAKE", "TIL", "SO GOOD", "NEW", "LISTEN TO THIS",
    ]
    static let maxStickers = 12
    static let maxTags = 10
    static let maxCaption = 150
    /// `NOTE_MAX` (`social.MAX_NOTE`).
    static let maxNote = 280

    /// A word sticker (any capital letter) is a copper label, else an emoji.
    static func isWord(_ k: String) -> Bool { k.contains(where: { $0.isUppercase }) }
}

// MARK: - The layout

struct StorySpot: Hashable {
    var k: String
    var x: Double
    var y: Double
    var s: Double
    var r: Double
}

struct StoryMention: Hashable {
    var handle: String
    var name: String
    var x: Double
    var y: Double
}

/// `storyStyleOf`: the defaults, then whatever the story carries.
struct StoryLayout: Hashable {
    var font = "classic"
    var size: Double = 24
    var cx: Double = 0.5
    var cy: Double = 0.8
    var frame = "card"
    var zoom: Double = 1
    var tx: Double = 0
    var ty: Double = 0
    var thumb = ""
    var bg = ""
    var stickers: [StorySpot] = []

    init() {}

    init(_ style: StoryStyle?) {
        guard let st = style else { return }
        if let v = st.font { font = v }
        if let v = st.size { size = v }
        if let v = st.cx { cx = v }
        if let v = st.cy { cy = v }
        if let v = st.frame { frame = v }
        if let v = st.zoom { zoom = v }
        if let v = st.tx { tx = v }
        if let v = st.ty { ty = v }
        if let v = st.thumb { thumb = v }
        if let v = st.bg { bg = v }
        stickers = st.stickers.map { StorySpot(k: $0.k, x: $0.x, y: $0.y, s: $0.s, r: $0.r) }
    }

    /// What `/api/vibe` and `/api/story/note` take as `style`.
    func payload(note: Bool) -> [String: Any] {
        var out: [String: Any] = [
            "font": font, "size": Int(size.rounded()), "cx": cx, "cy": cy, "frame": frame,
            "zoom": zoom, "tx": tx, "ty": ty, "thumb": thumb,
        ]
        var list: [[String: Any]] = []
        for k in stickers {
            list.append(["k": k.k, "x": k.x, "y": k.y, "s": k.s, "r": k.r])
        }
        out["stickers"] = list
        if note { out["bg"] = bg }
        return out
    }
}

/// One story as the canvas draws it: a vibe (an episode's card) or a note.
struct StoryDraft: Hashable {
    var note: Bool
    var title: String
    var minutes: Double
    var caption: String
    var text: String
    var layout: StoryLayout
    var tags: [StoryMention]

    init(note: Bool, title: String, minutes: Double, layout: StoryLayout) {
        self.note = note
        self.title = title
        self.minutes = minutes
        self.caption = ""
        self.text = ""
        self.layout = layout
        self.tags = []
    }

    init(story: Story) {
        note = story.kind == "note"
        let t = story.title ?? ""
        title = t.isEmpty ? (story.query ?? "") : t
        minutes = story.minutes ?? FAMAPI.browseMinutes
        caption = story.caption ?? ""
        text = story.text ?? ""
        layout = StoryLayout(story.style)
        tags = (story.tags ?? []).map { StoryMention(handle: $0.handle, name: $0.name, x: $0.x, y: $0.y) }
    }

    /// `storyHasLayout(v) || isNote(v)`: drawn from its layout, rather
    /// than the title-and-caption page a vibe from before the editor keeps.
    static func isComposed(_ story: Story) -> Bool {
        if story.kind == "note" { return true }
        guard let style = story.style else { return false }
        return !style.isEmpty
    }
}

// MARK: - The canvas (`storyCanvasHTML`)

/// One story, drawn from its layout: every position a fraction of the
/// canvas, every size in 360ths of its width, so it reads the same on any
/// phone. In the editor (`editor` set) each piece can be dragged, pinched,
/// selected and taken off; in the viewer only a tag takes a tap (its
/// profile) - the episode's card plays through the viewer's own tap, so a
/// hold on it still pauses the story (`StoryPostFrameKey`).
struct StoryCanvas: View {
    @EnvironmentObject private var model: AppModel
    let draft: StoryDraft
    var editor: StoryEditorModel? = nil
    var selected: String = ""
    var openTag: ((StoryMention) -> Void)? = nil

    static let space = "storyCanvas"

    var body: some View {
        GeometryReader { geo in
            let w = max(geo.size.width, 1)
            let h = max(geo.size.height, 1)
            ZStack(alignment: .topLeading) {
                backdrop
                if !draft.note {
                    post(w: w, h: h)
                }
                caption(w: w, h: h)
                stickers(w: w, h: h)
                tags(w: w, h: h)
            }
            .frame(width: w, height: h)
            .coordinateSpace(name: Self.space)
            .modifier(StoryPinch(editor: editor))
        }
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .accessibilityElement(children: editor == nil ? .ignore : .contain)
        .accessibilityLabel(accessibleWords)
    }

    private var accessibleWords: String {
        if draft.note { return draft.text.isEmpty ? "A story" : draft.text }
        return draft.caption.isEmpty ? draft.title : draft.title + ". " + draft.caption
    }

    @ViewBuilder private var backdrop: some View {
        if let ed = editor {
            back.contentShape(Rectangle()).onTapGesture { ed.touchedCanvas() }
        } else {
            back.allowsHitTesting(false)
        }
    }

    @ViewBuilder private var back: some View {
        if draft.note {
            StoryPalette.background(draft.layout.bg)
        } else {
            StoryPalette.canvas
        }
    }

    // The episode's card (`.sc-post`), framed.

    @ViewBuilder private func post(w: CGFloat, h: CGFloat) -> some View {
        let L = draft.layout
        let u = w / 360
        let x = w * CGFloat(0.5 + L.tx)
        let y = h * CGFloat(0.42 + L.ty)
        if editor != nil {
            postBody(w: w, h: h, u: u)
                .modifier(StoryGrab(id: "pic", editor: editor, selected: false, w: w, h: h))
                .scaleEffect(CGFloat(L.zoom))
                .position(x: x, y: y)
        } else {
            postBody(w: w, h: h, u: u)
                .background(GeometryReader { g in
                    Color.clear.preference(key: StoryPostFrameKey.self,
                                           value: Self.scaled(g.frame(in: .global), by: CGFloat(L.zoom)))
                })
                .scaleEffect(CGFloat(L.zoom))
                .position(x: x, y: y)
                .allowsHitTesting(false)
        }
    }

    private static func scaled(_ r: CGRect, by z: CGFloat) -> CGRect {
        let w = r.width * z
        let h = r.height * z
        return CGRect(x: r.midX - w / 2, y: r.midY - h / 2, width: w, height: h)
    }

    @ViewBuilder private func postBody(w: CGFloat, h: CGFloat, u: CGFloat) -> some View {
        switch draft.layout.frame {
        case "full":
            ZStack(alignment: .bottom) {
                picture.frame(width: w, height: h)
                postText(u: u, centred: false)
                    .padding(EdgeInsets(top: 60, leading: 16, bottom: 18, trailing: 16))
                    .background(LinearGradient(colors: [Color.clear, Color(hex: 0x0A090F).opacity(0.85)],
                                               startPoint: .top, endPoint: .bottom))
            }
            .frame(width: w, height: h)
            .background(StoryPalette.postBack)
            .clipped()
        case "circle":
            VStack(spacing: 0) {
                picture
                    .aspectRatio(1, contentMode: .fit)
                    .clipShape(Circle())
                    .shadow(color: .black.opacity(0.5), radius: 16, x: 0, y: 14)
                postText(u: u, centred: true)
                    .padding(EdgeInsets(top: 9, leading: 11, bottom: 11, trailing: 11))
            }
            .frame(width: w * 0.62)
        default:
            let round = draft.layout.frame == "round"
            VStack(alignment: .leading, spacing: 0) {
                picture.aspectRatio(round ? 1 : 4.0 / 3.0, contentMode: .fit)
                postText(u: u, centred: false)
                    .padding(EdgeInsets(top: 9, leading: 11, bottom: 11, trailing: 11))
            }
            .frame(width: w * 0.72)
            .background(StoryPalette.postBack)
            .clipShape(RoundedRectangle(cornerRadius: round ? 30 : 14))
            .shadow(color: .black.opacity(0.5), radius: 16, x: 0, y: 14)
        }
    }

    /// `.sc-pic`: the episode's picture over its grey, filled and cropped.
    private var picture: some View {
        StoryPalette.pictureBack
            .overlay {
                if let url = model.api.picture(draft.layout.thumb) {
                    AsyncImage(url: url) { image in
                        image.resizable().scaledToFill()
                    } placeholder: {
                        Color.clear
                    }
                }
            }
            .clipped()
    }

    /// The title (Bricolage 17) over "FAM · 2 MIN" (Geist Mono 9.5, gold).
    private func postText(u: CGFloat, centred: Bool) -> some View {
        let meta = "FAM \u{00B7} " + FAMAPI.minutes(draft.minutes) + " MIN"
        return VStack(alignment: centred ? .center : .leading, spacing: 5) {
            Text(draft.title)
                .font(StoryFonts.heading(17 * u))
                .foregroundStyle(StoryPalette.paper)
                .multilineTextAlignment(centred ? .center : .leading)
                .fixedSize(horizontal: false, vertical: true)
            Text(meta)
                .font(StoryFonts.mono(9.5 * u))
                .tracking(9.5 * u * 0.08)
                .foregroundStyle(StoryPalette.gold)
        }
        .frame(maxWidth: .infinity, alignment: centred ? .center : .leading)
    }

    // The caption, or a note's words (`.sc-cap`).

    @ViewBuilder private func caption(w: CGFloat, h: CGFloat) -> some View {
        let L = draft.layout
        let words = draft.note ? draft.text : draft.caption
        let shown = draft.note ? (!words.isEmpty || editor != nil) : !words.isEmpty
        if shown {
            let u = w / 360
            let half = min(L.cx, 1 - L.cx)
            let room = max(10, min(86, (200 * half).rounded() - 4)) / 100
            let light = draft.note && (L.bg == "cream" || L.bg == "candy")
            StoryWordsWidth(maxWidth: w * CGFloat(room)) {
                Text(words.isEmpty ? "Tap Text, or Speak" : words)
                    .font(StoryFonts.face(L.font, size: CGFloat(L.size) * u))
                    .foregroundStyle(light ? StoryPalette.dark : StoryPalette.paper)
                    .multilineTextAlignment(.center)
                    .shadow(color: light ? .clear : .black.opacity(0.55), radius: 4, x: 0, y: 1)
                    .opacity(words.isEmpty ? 0.55 : 1)
            }
            .modifier(StoryGrab(id: "cap", editor: editor, selected: false, w: w, h: h))
            .position(x: w * CGFloat(L.cx), y: h * CGFloat(L.cy))
            .allowsHitTesting(editor != nil)
        }
    }

    // Stickers (`.sc-stk`).

    private func stickers(w: CGFloat, h: CGFloat) -> some View {
        let list = draft.layout.stickers
        return ForEach(Array(list.enumerated()), id: \.offset) { i, k in
            let id = "stk" + String(i)
            stickerFace(k.k, u: w / 360)
                .modifier(StoryGrab(id: id, editor: editor, selected: selected == id, w: w, h: h))
                .scaleEffect(CGFloat(k.s))
                .rotationEffect(.degrees(k.r))
                .position(x: w * CGFloat(k.x), y: h * CGFloat(k.y))
                .allowsHitTesting(editor != nil)
        }
    }

    @ViewBuilder private func stickerFace(_ k: String, u: CGFloat) -> some View {
        if StoryChoices.isWord(k) {
            let em = 13 * u
            Text(k)
                .font(StoryFonts.mono(em, bold: true))
                .tracking(em * 0.06)
                .foregroundStyle(StoryPalette.ink)
                .padding(.horizontal, em * 0.8)
                .padding(.vertical, em * 0.45)
                .background(StoryPalette.gold, in: RoundedRectangle(cornerRadius: em * 0.6))
                .fixedSize()
        } else {
            Text(k).font(.system(size: 46 * u)).fixedSize()
        }
    }

    // @tags (`.sc-tag`): a button to the profile in the viewer.

    private func tags(w: CGFloat, h: CGFloat) -> some View {
        ForEach(Array(draft.tags.enumerated()), id: \.offset) { i, t in
            let id = "tag" + String(i)
            tagView(t, id: id, w: w, h: h)
                .position(x: w * CGFloat(t.x), y: h * CGFloat(t.y))
        }
    }

    @ViewBuilder private func tagView(_ t: StoryMention, id: String, w: CGFloat, h: CGFloat) -> some View {
        let em = 14 * w / 360
        let label = Text("@" + t.handle)
            .font(StoryFonts.tag(em))
            .foregroundStyle(StoryPalette.dark)
            .padding(.horizontal, em * 0.7)
            .padding(.vertical, em * 0.35)
            .background(StoryPalette.paper.opacity(0.94), in: RoundedRectangle(cornerRadius: em * 0.5))
            .fixedSize()
        if editor != nil {
            label.modifier(StoryGrab(id: id, editor: editor, selected: selected == id, w: w, h: h))
        } else if let openTag, !t.handle.isEmpty {
            Button { openTag(t) } label: { label }
                .buttonStyle(.press)
                .accessibilityLabel("@" + t.handle + "'s profile")
        } else {
            label.allowsHitTesting(false)
        }
    }
}

/// Where the episode's card sits on screen in the viewer, so a tap there
/// plays (`playStoryTap`) while a hold still pauses.
struct StoryPostFrameKey: PreferenceKey {
    static let defaultValue: CGRect = .zero
    static func reduce(value: inout CGRect, nextValue: () -> CGRect) {
        let next = nextValue()
        if next != .zero { value = next }
    }
}

/// A piece the editor can drag (in the canvas's own coordinates, so a
/// turned sticker moves the way the finger does), select, and - when
/// selected - take off with its ×.
struct StoryGrab: ViewModifier {
    let id: String
    let editor: StoryEditorModel?
    let selected: Bool
    let w: CGFloat
    let h: CGFloat

    @ViewBuilder func body(content: Content) -> some View {
        if let ed = editor {
            content
                .contentShape(Rectangle())
                .gesture(
                    DragGesture(minimumDistance: 0, coordinateSpace: .named(StoryCanvas.space))
                        .onChanged { v in
                            ed.dragged(id, dx: Double(v.translation.width / w), dy: Double(v.translation.height / h))
                        }
                        .onEnded { _ in ed.dragEnded() }
                )
                .overlay {
                    if selected {
                        Rectangle()
                            .inset(by: -5)
                            .stroke(StoryPalette.paper.opacity(0.8), style: StrokeStyle(lineWidth: 2, dash: [5, 4]))
                            .allowsHitTesting(false)
                    }
                }
                .overlay(alignment: .topTrailing) {
                    if selected {
                        Text("\u{00D7}")
                            .font(.custom("Geist-Bold", fixedSize: 13))
                            .foregroundStyle(StoryPalette.dark)
                            .frame(width: 22, height: 22)
                            .background(StoryPalette.paper, in: Circle())
                            .contentShape(Circle())
                            .offset(x: 14, y: -14)
                            .onTapGesture { ed.remove(id) }
                            .accessibilityLabel("Remove")
                            .accessibilityAddTraits(.isButton)
                    }
                }
        } else {
            content
        }
    }
}

/// A pinch anywhere on the canvas resizes the piece being held (the web's
/// pointers are captured by the canvas, so the second finger need not be
/// on the piece).
struct StoryPinch: ViewModifier {
    let editor: StoryEditorModel?

    @ViewBuilder func body(content: Content) -> some View {
        if let ed = editor {
            content.simultaneousGesture(
                MagnifyGesture()
                    .onChanged { v in ed.pinched(Double(v.magnification)) }
                    .onEnded { _ in ed.pinchEnded() }
            )
        } else {
            content
        }
    }
}

/// As wide as its words up to `maxWidth` (`width:max-content`, §250),
/// breaking only between words.
struct StoryWordsWidth: Layout {
    var maxWidth: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        guard let first = subviews.first else { return .zero }
        let ideal = first.sizeThatFits(.unspecified)
        let width = min(ideal.width, maxWidth)
        return first.sizeThatFits(ProposedViewSize(width: width, height: nil))
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        guard let first = subviews.first else { return }
        first.place(at: CGPoint(x: bounds.midX, y: bounds.midY), anchor: .center,
                    proposal: ProposedViewSize(width: bounds.width, height: nil))
    }
}

/// A row that wraps, each line centred (`.vcs-fonts`: flex-wrap, centred).
struct StoryWrapRow: Layout {
    var spacing: CGFloat = 6

    private struct Line {
        var items: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let lines = arrange(proposal.width ?? .infinity, subviews)
        var height: CGFloat = 0
        var width: CGFloat = 0
        for (n, line) in lines.enumerated() {
            height += line.height + (n > 0 ? spacing : 0)
            width = max(width, line.width)
        }
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for line in arrange(bounds.width, subviews) {
            var x = bounds.minX + (bounds.width - line.width) / 2
            for i in line.items {
                let size = subviews[i].sizeThatFits(.unspecified)
                subviews[i].place(at: CGPoint(x: x, y: y + (line.height - size.height) / 2),
                                  proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += line.height + spacing
        }
    }

    private func arrange(_ maxWidth: CGFloat, _ subviews: Subviews) -> [Line] {
        var lines: [Line] = []
        var line = Line()
        for i in subviews.indices {
            let size = subviews[i].sizeThatFits(.unspecified)
            let grown = line.items.isEmpty ? size.width : line.width + spacing + size.width
            if !line.items.isEmpty && grown > maxWidth {
                lines.append(line)
                line = Line(items: [i], width: size.width, height: size.height)
            } else {
                line.items.append(i)
                line.width = grown
                line.height = max(line.height, size.height)
            }
        }
        if !line.items.isEmpty { lines.append(line) }
        return lines
    }
}

// MARK: - What the editor was opened on

struct StoryEditorSubject: Identifiable {
    let id = UUID()
    let note: Bool
    let key: EpisodeKey
    let title: String
    /// The episode's picture as a path on the server (`/api/thumb/...`), or
    /// "" to ask `/api/episode/card` for it.
    let thumb: String
    let thread: String

    /// A story with no episode (`openNoteComposer`).
    static func note() -> StoryEditorSubject {
        StoryEditorSubject(note: true, key: EpisodeKey(query: "", minutes: FAMAPI.browseMinutes), title: "",
                           thumb: "", thread: "")
    }

    static func episode(_ key: EpisodeKey, title: String, thumb: String, thread: String) -> StoryEditorSubject {
        StoryEditorSubject(note: false, key: key, title: title.isEmpty ? key.query : title, thumb: thumb,
                           thread: thread)
    }

    /// A picture URL the app already holds, as the server path a layout
    /// may carry (`social.clean_thumb` takes only `/api/thumb/...`).
    static func thumbPath(_ url: URL?) -> String {
        guard let url, let parts = URLComponents(url: url, resolvingAgainstBaseURL: true) else { return "" }
        let path = parts.percentEncodedPath
        guard path.hasPrefix("/api/thumb/") else { return "" }
        if let q = parts.percentEncodedQuery, !q.isEmpty { return path + "?" + q }
        return path
    }
}

// MARK: - The editor's state (`vcs`)

@MainActor
final class StoryEditorModel: ObservableObject {
    @Published var draft: StoryDraft
    /// The selected sticker or tag ("stk2", "tag0"), whose × takes it off.
    @Published var sel = ""
    /// One tool at a time: "text", "stickers", "mention", "picture", "bg".
    @Published private(set) var tool = ""
    /// Asks the view to put the cursor in the box (Text was tapped).
    @Published private(set) var focusAsk = 0
    @Published private(set) var people: [FollowPerson]?
    @Published private(set) var peopleFailed = false
    @Published var term = ""
    @Published private(set) var sending = false
    @Published private(set) var listening = false

    let subject: StoryEditorSubject
    private weak var app: AppModel?
    private let dictation = StoryDictation()

    // The piece under the finger and where it started.
    private var piece = ""
    private var origin: (x: Double, y: Double, s: Double) = (0, 0, 1)
    private var pinchBase: Double = 1
    private var moved = false
    private var dragging = false
    private var pinching = false

    init(subject: StoryEditorSubject) {
        self.subject = subject
        if subject.note {
            var layout = StoryLayout()
            layout.font = "strong"
            layout.size = 30
            layout.cy = 0.45
            layout.bg = "sunset"
            draft = StoryDraft(note: true, title: "", minutes: FAMAPI.browseMinutes, layout: layout)
        } else {
            var layout = StoryLayout()
            layout.thumb = subject.thumb
            draft = StoryDraft(note: false, title: subject.title, minutes: subject.key.minutes, layout: layout)
        }
    }

    var note: Bool { subject.note }

    /// The app, once the view is up: the picture the player has not got
    /// yet is asked for as the player asks (`/api/episode/card`).
    func attach(_ model: AppModel) {
        guard app == nil else { return }
        app = model
        dictation.keepsPlayback = { [weak model] in model?.player.isLoaded ?? false }
        dictation.onHeard = { [weak self] words in self?.heard(words) }
        dictation.onEnd = { [weak self] in self?.listening = false }
        dictation.onError = { [weak self] sentence in
            self?.listening = false
            self?.app?.nowPlaying.say(sentence)
        }
        if !note && draft.layout.thumb.isEmpty {
            let key = subject.key
            let title = subject.title
            Task {
                guard let card = try? await model.api.card(key, title: title, episode: "") else { return }
                if let thumb = card.thumb, !thumb.isEmpty, self.draft.layout.thumb.isEmpty {
                    self.draft.layout.thumb = thumb
                }
            }
        }
    }

    private func say(_ text: String) { app?.nowPlaying.say(text) }

    // MARK: Words

    var words: String { note ? draft.text : draft.caption }

    func setWords(_ value: String) {
        let limit = note ? StoryChoices.maxNote : StoryChoices.maxCaption
        let cut = value.count > limit ? String(value.prefix(limit)) : value
        if note { draft.text = cut } else { draft.caption = cut }
    }

    func setSize(_ value: Double) {
        draft.layout.size = min(48, max(14, value.rounded()))
    }

    func setFont(_ font: String) { draft.layout.font = font }

    func setBg(_ name: String) { draft.layout.bg = name }

    // MARK: Tools (`vcsTool`)

    /// A second tap on the open tool closes it; `keep` is the box taking
    /// focus, which opens Text without toggling it shut.
    func setTool(_ wanted: String, keep: Bool = false) {
        var next = wanted
        if !keep && tool == wanted { next = "" }
        tool = next
        if next == "text" && !keep { focusAsk += 1 }
        if next == "mention" { loadPeople() }
    }

    var panelOpen: Bool { tool == "stickers" || tool == "mention" || tool == "picture" || tool == "bg" }

    func addSticker(_ index: Int) {
        guard StoryChoices.stickers.indices.contains(index) else { return }
        if draft.layout.stickers.count >= StoryChoices.maxStickers {
            say("That\u{2019}s the most stickers a story holds")
            return
        }
        // A little scatter, so a second sticker is not exactly on the first.
        let spot = StorySpot(k: StoryChoices.stickers[index], x: 0.3 + Double.random(in: 0..<0.4),
                             y: 0.2 + Double.random(in: 0..<0.25), s: 1,
                             r: Double(Int((Double.random(in: 0..<1) * 24 - 12).rounded())))
        draft.layout.stickers.append(spot)
        sel = "stk" + String(draft.layout.stickers.count - 1)
        setTool("")
    }

    /// @: friends only - a tag sends the episode as a message, and messages
    /// go only between people who follow each other (§520).
    func loadPeople() {
        guard people == nil, let api = app?.api else { return }
        peopleFailed = false
        Task {
            do {
                let page = try await api.friends()
                var seen = Set<String>()
                var out: [FollowPerson] = []
                for p in page.friends {
                    let handle = p.handle ?? ""
                    if handle.isEmpty || seen.contains(handle) { continue }
                    seen.insert(handle)
                    out.append(p)
                }
                self.people = out
            } catch {
                self.peopleFailed = true
            }
        }
    }

    var shownPeople: [FollowPerson] {
        let q = term.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "@ "))
        let all = people ?? []
        if q.isEmpty { return all }
        return all.filter { p in
            let hay = ((p.name ?? "") + " " + (p.handle ?? "")).lowercased()
            return hay.contains(q)
        }
    }

    func isTagged(_ handle: String) -> Bool { draft.tags.contains(where: { $0.handle == handle }) }

    func toggleTag(_ p: FollowPerson) {
        let handle = p.handle ?? ""
        guard !handle.isEmpty else { return }
        if let at = draft.tags.firstIndex(where: { $0.handle == handle }) {
            draft.tags.remove(at: at)
            sel = ""
            return
        }
        if draft.tags.count >= StoryChoices.maxTags {
            say("That\u{2019}s the most people a story can tag")
            return
        }
        let row = Double(draft.tags.count % 5)
        draft.tags.append(StoryMention(handle: handle, name: p.name ?? "", x: 0.5, y: 0.14 + 0.07 * row))
    }

    func setFrame(_ frame: String) {
        draft.layout.frame = frame
        if frame == "full" {
            draft.layout.tx = 0
            draft.layout.ty = 0.08
        }
    }

    /// Smaller / Bigger / Reset (`vcsZoom`), for anybody without a pinch.
    func zoom(_ factor: Double) {
        if factor == 0 {
            draft.layout.zoom = 1
            draft.layout.tx = 0
            draft.layout.ty = draft.layout.frame == "full" ? 0.08 : 0
        } else {
            draft.layout.zoom = min(2.5, max(0.4, draft.layout.zoom * factor))
        }
    }

    func remove(_ id: String) {
        if id.hasPrefix("stk"), let i = Int(id.dropFirst(3)), draft.layout.stickers.indices.contains(i) {
            draft.layout.stickers.remove(at: i)
        } else if id.hasPrefix("tag"), let i = Int(id.dropFirst(3)), draft.tags.indices.contains(i) {
            draft.tags.remove(at: i)
        }
        sel = ""
    }

    // MARK: Drag and pinch (the canvas's pointer handlers)

    private func value(_ id: String) -> (x: Double, y: Double, s: Double)? {
        let L = draft.layout
        if id == "pic" { return (L.tx, L.ty, L.zoom) }
        if id == "cap" { return (L.cx, L.cy, L.size) }
        if id.hasPrefix("stk"), let i = Int(id.dropFirst(3)), L.stickers.indices.contains(i) {
            let k = L.stickers[i]
            return (k.x, k.y, k.s)
        }
        if id.hasPrefix("tag"), let i = Int(id.dropFirst(3)), draft.tags.indices.contains(i) {
            let t = draft.tags[i]
            return (t.x, t.y, 1)
        }
        return nil
    }

    private func clamp(_ v: Double, _ lo: Double, _ hi: Double) -> Double { min(hi, max(lo, v)) }

    private func set(_ id: String, _ x: Double, _ y: Double, _ s: Double) {
        if id == "pic" {
            draft.layout.tx = clamp(x, -0.5, 0.5)
            draft.layout.ty = clamp(y, -0.5, 0.5)
            draft.layout.zoom = clamp(s, 0.4, 2.5)
        } else if id == "cap" {
            draft.layout.cx = clamp(x, 0.05, 0.95)
            draft.layout.cy = clamp(y, 0.05, 0.95)
            draft.layout.size = clamp(s, 14, 48).rounded()
        } else if id.hasPrefix("stk"), let i = Int(id.dropFirst(3)), draft.layout.stickers.indices.contains(i) {
            draft.layout.stickers[i].x = clamp(x, 0, 1)
            draft.layout.stickers[i].y = clamp(y, 0, 1)
            draft.layout.stickers[i].s = clamp(s, 0.4, 3)
        } else if id.hasPrefix("tag"), let i = Int(id.dropFirst(3)), draft.tags.indices.contains(i) {
            draft.tags[i].x = clamp(x, 0, 1)
            draft.tags[i].y = clamp(y, 0, 1)
        }
    }

    /// Touching the canvas closes any tool but Text.
    func touchedCanvas() {
        if !tool.isEmpty && tool != "text" { setTool("") }
    }

    func dragged(_ id: String, dx: Double, dy: Double) {
        if !dragging {
            dragging = true
            if piece.isEmpty {
                touchedCanvas()
                piece = id
                origin = value(id) ?? (0, 0, 1)
                moved = false
            }
        }
        guard piece == id, let now = value(id) else { return }
        if abs(dx) + abs(dy) > 0.01 { moved = true }
        set(id, origin.x + dx, origin.y + dy, now.s)
    }

    func dragEnded() {
        dragging = false
        if !pinching { finish() }
    }

    func pinched(_ magnification: Double) {
        guard !piece.isEmpty, let now = value(piece) else { return }
        if !pinching {
            pinching = true
            pinchBase = now.s
        }
        moved = true
        set(piece, now.x, now.y, pinchBase * magnification)
    }

    func pinchEnded() {
        pinching = false
        if !dragging { finish() }
    }

    /// A tap, not a drag: a sticker or tag is selected (its × takes it
    /// off), the words open Text, the picture clears the selection.
    private func finish() {
        let id = piece
        piece = ""
        guard !id.isEmpty, !moved else { return }
        if id.hasPrefix("stk") || id.hasPrefix("tag") {
            sel = sel == id ? "" : id
        } else {
            sel = ""
            if id == "cap" { setTool("text") }
        }
    }

    // MARK: Speak (`vcsDictate`)

    static var canDictate: Bool { VoiceSearchModel.available }

    private var before = ""

    func toggleDictation() {
        if listening {
            stopDictation()
            return
        }
        before = draft.text
        listening = true
        dictation.start()
    }

    func stopDictation() {
        guard listening else { return }
        listening = false
        dictation.stop()
    }

    /// What is heard is added to what is there (`voiceJoin`), cut at 280.
    private func heard(_ words: String) {
        let a = before.trimmingCharacters(in: .whitespaces)
        let b = words.trimmingCharacters(in: .whitespaces)
        var joined = b
        if b.isEmpty {
            joined = a
        } else if !a.isEmpty && !b.lowercased().hasPrefix(a.lowercased()) {
            joined = a + " " + b
        }
        setWords(joined)
    }

    // MARK: Posting (`postVibeStory` / `postNoteStory`)

    /// "Your story" and the arrow both post to everybody who follows (no
    /// Close Friends, §250). `done` closes the editor; `openMine` is the
    /// snack's View (your story, read again).
    func post(done: @escaping () -> Void, openMine: (() -> Void)?) {
        guard let model = app, !sending else { return }
        stopDictation()
        let api = model.api
        let layout = draft.layout
        if note {
            let text = draft.text.trimmingCharacters(in: .whitespacesAndNewlines)
            if text.isEmpty && layout.stickers.isEmpty {
                say("Write something, or add a sticker")
                return
            }
            sending = true
            let style = layout.payload(note: true)
            Task {
                do {
                    try await api.postNote(text: text, style: style)
                    done()
                    model.nowPlaying.say("Added to your story", action: openMine == nil ? nil : "View",
                                         perform: openMine)
                    await model.loadCircle()
                    await model.you.loadProfile()
                } catch {
                    self.sending = false
                    model.nowPlaying.say(SocialModel.sentence(error, else: "Could not add that to your story"))
                }
            }
            return
        }
        let key = subject.key
        let caption = draft.caption.trimmingCharacters(in: .whitespacesAndNewlines)
        var tags: [[String: Any]] = []
        for t in draft.tags {
            tags.append(["handle": t.handle, "x": t.x, "y": t.y])
        }
        let style = layout.payload(note: false)
        sending = true
        Task {
            do {
                try await api.vibeStory(key, title: self.subject.title, thread: self.subject.thread,
                                        caption: caption, style: style, tags: tags)
                done()
                model.nowPlaying.vibedFromEditor(key)
                model.nowPlaying.say("Vibed to your story", action: openMine == nil ? nil : "View",
                                     perform: openMine)
                await model.loadCircle()
                await model.you.loadProfile()
            } catch {
                self.sending = false
                model.nowPlaying.say(SocialModel.sentence(error, else: "Could not vibe that"))
            }
        }
    }
}

// MARK: - The editor (`#vibeComposer`)

/// Instagram's story editor, at the owner's direction (§213): the canvas,
/// X back (posts nothing), Text / Stickers / Mention / Picture down the
/// right - Text / Stickers / Background / Speak for a note - the caption
/// line, then Your story and the arrow. No music, no colour, no "more".
struct StoryEditor: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @StateObject private var ed: StoryEditorModel
    @FocusState private var boxFocused: Bool
    @FocusState private var searchFocused: Bool
    @State private var stage: CGSize = .zero
    private let openMine: (() -> Void)?

    init(subject: StoryEditorSubject, openMine: (() -> Void)? = nil) {
        _ed = StateObject(wrappedValue: StoryEditorModel(subject: subject))
        self.openMine = openMine
    }

    private var typing: Bool { boxFocused || searchFocused }

    var body: some View {
        ZStack {
            StoryPalette.editorBack.ignoresSafeArea()
            VStack(spacing: 10) {
                stageView
                foot
            }
            .padding(.horizontal, 10)
            .padding(.top, 8)
            .padding(.bottom, 14)
            .ignoresSafeArea(.keyboard, edges: .bottom)
            // What the keyboard pushes up: the faces, the box, the panel.
            VStack(spacing: 0) {
                Spacer(minLength: 0)
                bottomControls
            }
            .padding(.horizontal, 10)
            .padding(.bottom, typing ? 8 : 14 + 42 + 10)
        }
        .foregroundStyle(StoryPalette.paper)
        .tint(StoryPalette.gold)
        .overlay(alignment: .bottom) {
            ToastView()
                .environmentObject(model.nowPlaying)
                .padding(.bottom, 64)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(ed.note ? "Add to your story" : "VIBE! to your story")
        .onAppear { ed.attach(model) }
        .onDisappear { ed.stopDictation() }
        .onChange(of: ed.focusAsk) { _, _ in boxFocused = true }
        .onChange(of: boxFocused) { _, on in
            if on { ed.setTool("text", keep: true) }
        }
        .onChange(of: ed.tool) { _, now in
            if now != "text" && now != "" { boxFocused = false }
            if now != "mention" { searchFocused = false }
        }
    }

    private func close() {
        ed.stopDictation()
        dismiss()
    }

    // MARK: The stage

    private var stageView: some View {
        ZStack(alignment: .topLeading) {
            StoryCanvas(draft: ed.draft, editor: ed, selected: ed.sel)
            Button { close() } label: {
                WebIcon(path: Icons.close, size: 16, line: 2.4)
                    .foregroundStyle(StoryPalette.paper)
                    .frame(width: 36, height: 36)
                    .background(StoryPalette.glass, in: Circle())
            }
            .buttonStyle(.press)
            .padding(10)
            .accessibilityLabel(ed.note ? "Close" : "Back to the episode")
            tools
                .frame(maxWidth: .infinity, alignment: .trailing)
                .padding(.top, 10)
                .padding(.trailing, 8)
            if ed.tool == "text" {
                sizeSlider
                    .frame(maxHeight: .infinity, alignment: .center)
                    .padding(.leading, 6)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 18))
        .background(GeometryReader { g in
            Color.clear
                .onAppear { stage = g.size }
                .onChange(of: g.size) { _, size in stage = size }
        })
    }

    private var tools: some View {
        VStack(alignment: .trailing, spacing: 9) {
            toolButton("text", "Text") {
                Text("Aa").font(.custom("Geist-Bold", fixedSize: 14))
            }
            toolButton("stickers", "Stickers") {
                WebIcon(path: StoryIcons.sticker, size: 17, line: 1.9)
            }
            if ed.note {
                toolButton("bg", "Background") {
                    StoryPalette.background(ed.draft.layout.bg)
                        .frame(width: 18, height: 18)
                        .clipShape(Circle())
                        .overlay(Circle().stroke(StoryPalette.paper, lineWidth: 2))
                }
                if StoryEditorModel.canDictate {
                    speakButton
                }
            } else {
                toolButton("mention", "Mention") {
                    Text("@").font(.custom("Geist-Bold", fixedSize: 14))
                }
                toolButton("picture", "Picture") {
                    WebIcon(path: StoryIcons.sliders, size: 17, line: 1.9)
                }
            }
        }
    }

    private func toolButton<Icon: View>(_ id: String, _ label: String,
                                        @ViewBuilder icon: () -> Icon) -> some View {
        let on = ed.tool == id
        return Button { ed.setTool(id) } label: {
            toolFace(label, on: on, listening: false, icon: icon())
        }
        .buttonStyle(.press)
        .accessibilityLabel(label)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    private var speakButton: some View {
        Button { ed.toggleDictation() } label: {
            toolFace("Speak", on: false, listening: ed.listening,
                     icon: WebIcon(path: StoryIcons.mic, size: 17, line: 1.9))
        }
        .buttonStyle(.press)
        .accessibilityLabel(ed.listening ? "Stop speaking" : "Speak")
    }

    private func toolFace<Icon: View>(_ label: String, on: Bool, listening: Bool, icon: Icon) -> some View {
        let fill: Color = listening ? StoryPalette.coral : (on ? StoryPalette.paper : StoryPalette.glass)
        let tint: Color = listening ? Color.white : (on ? StoryPalette.dark : StoryPalette.paper)
        return HStack(spacing: 8) {
            Text(label)
                .font(.custom("Geist-SemiBold", fixedSize: 11))
                .foregroundStyle(StoryPalette.paper)
                .shadow(color: .black.opacity(0.6), radius: 3, x: 0, y: 1)
            icon
                .foregroundStyle(tint)
                .frame(width: 36, height: 36)
                .background(fill, in: Circle())
        }
        .contentShape(Rectangle())
    }

    /// The vertical size slider (14-48), 46% of the stage, gold.
    private var sizeSlider: some View {
        let length = max(120, stage.height * 0.46)
        let size = Binding<Double>(get: { ed.draft.layout.size }, set: { ed.setSize($0) })
        return Slider(value: size, in: 14...48, step: 1)
            .tint(StoryPalette.gold)
            .frame(width: length)
            .rotationEffect(.degrees(-90))
            .frame(width: 30, height: length)
            .accessibilityLabel("Text size")
    }

    // MARK: Fonts, the box, the panels

    private var bottomControls: some View {
        ZStack(alignment: .bottom) {
            VStack(spacing: 6) {
                if ed.tool == "text" { fontRow }
                box
            }
            .padding(10)
            if ed.panelOpen {
                panel
            }
        }
    }

    private var fontRow: some View {
        StoryWrapRow(spacing: 6) {
            ForEach(StoryChoices.fonts, id: \.self) { item in
                let on = ed.draft.layout.font == item.key
                Button { ed.setFont(item.key) } label: {
                    Text(item.label)
                        .font(StoryFonts.face(item.key, size: 12))
                        .foregroundStyle(on ? StoryPalette.dark : StoryPalette.paper)
                        .padding(.horizontal, 11)
                        .padding(.vertical, 6)
                        .background(on ? StoryPalette.paper : StoryPalette.glass60, in: Capsule())
                        .overlay(Capsule().stroke(StoryPalette.paper.opacity(0.4), lineWidth: 1))
                }
                .buttonStyle(.press)
                .accessibilityAddTraits(on ? .isSelected : [])
            }
        }
        .padding(.horizontal, 8)
    }

    private var box: some View {
        let words = Binding<String>(get: { ed.words }, set: { ed.setWords($0) })
        let hint = ed.note ? "Say what you think\u{2026}" : "Add a caption\u{2026}"
        return TextField("", text: words, prompt: Text(hint).foregroundColor(StoryPalette.paper.opacity(0.7)))
            .font(.custom("Geist-Medium", fixedSize: 13))
            .foregroundStyle(StoryPalette.paper)
            .focused($boxFocused)
            .submitLabel(.done)
            .onSubmit { boxFocused = false }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(StoryPalette.glass, in: RoundedRectangle(cornerRadius: 12))
            .accessibilityLabel("Caption")
    }

    private var panelTitle: String {
        switch ed.tool {
        case "stickers": return "STICKERS"
        case "mention": return "MENTION"
        case "picture": return "PICTURE"
        default: return "BACKGROUND"
        }
    }

    private var panel: some View {
        let most = max(160, stage.height * 0.62)
        return ViewThatFits(in: .vertical) {
            panelBody
            ScrollView { panelBody }
        }
        .frame(maxHeight: most)
        .background(StoryPalette.panel, in: RoundedRectangle(cornerRadius: 18))
    }

    private var panelBody: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(panelTitle)
                    .font(StoryFonts.mono(10))
                    .tracking(1.2)
                    .foregroundStyle(StoryPalette.paper.opacity(0.7))
                Spacer()
                Button("Done") { ed.setTool("") }
                    .font(.custom("Geist-Bold", fixedSize: 12))
                    .foregroundStyle(StoryPalette.gold)
                    .buttonStyle(.press)
            }
            .padding(.bottom, 10)
            switch ed.tool {
            case "stickers": stickerPanel
            case "mention": mentionPanel
            case "picture": picturePanel
            default: backgroundPanel
            }
        }
        .padding(EdgeInsets(top: 12, leading: 14, bottom: 16, trailing: 14))
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func hint(_ words: String) -> some View {
        Text(words)
            .font(.custom("Geist-Regular", fixedSize: 11))
            .foregroundStyle(StoryPalette.paper.opacity(0.6))
            .padding(.top, 10)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var stickerPanel: some View {
        let emoji = Array(StoryChoices.stickers.enumerated()).filter { !StoryChoices.isWord($0.element) }
        let words = Array(StoryChoices.stickers.enumerated()).filter { StoryChoices.isWord($0.element) }
        let six = Array(repeating: GridItem(.flexible(), spacing: 8), count: 6)
        let two = Array(repeating: GridItem(.flexible(), spacing: 8), count: 2)
        return VStack(alignment: .leading, spacing: 8) {
            LazyVGrid(columns: six, spacing: 8) {
                ForEach(emoji, id: \.offset) { item in
                    Button { ed.addSticker(item.offset) } label: {
                        Text(item.element)
                            .font(.system(size: 26))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 4)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.press)
                }
            }
            LazyVGrid(columns: two, spacing: 8) {
                ForEach(words, id: \.offset) { item in
                    Button { ed.addSticker(item.offset) } label: {
                        Text(item.element)
                            .font(StoryFonts.mono(11, bold: true))
                            .tracking(0.66)
                            .foregroundStyle(StoryPalette.ink)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 9)
                            .background(StoryPalette.gold, in: RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.press)
                }
            }
            hint("Drag a sticker to move it, pinch to resize it, tap it to take it off.")
        }
    }

    @ViewBuilder private var mentionPanel: some View {
        if model.listener?.authenticated != true {
            hint("Tagging somebody needs an account.")
        } else if ed.people == nil {
            hint(ed.peopleFailed ? "Could not load your people." : "Loading\u{2026}")
        } else {
            VStack(alignment: .leading, spacing: 0) {
                TextField("", text: $ed.term,
                          prompt: Text("Search people you follow").foregroundColor(FAMColor.textFaint))
                    .font(.custom("Geist-Regular", fixedSize: 13))
                    .foregroundStyle(FAMColor.text)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($searchFocused)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 11)
                    .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 10))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(FAMColor.borderStrong, lineWidth: 1))
                    .padding(.bottom, 10)
                let rows = ed.shownPeople
                if rows.isEmpty {
                    hint((ed.people ?? []).isEmpty ? "Follow somebody to tag them." : "Nobody you follow matches that.")
                } else {
                    ForEach(rows) { p in personRow(p) }
                }
            }
        }
    }

    private func personRow(_ p: FollowPerson) -> some View {
        let handle = p.handle ?? ""
        let name = p.name ?? ""
        let on = ed.isTagged(handle)
        return Button { ed.toggleTag(p) } label: {
            HStack(spacing: 10) {
                Face(name: name, handle: handle, avatar: p.avatar ?? "", size: 32, initialsSize: 12)
                VStack(alignment: .leading, spacing: 1) {
                    Text(name.isEmpty ? "@" + handle : name)
                        .font(.custom("Geist-Regular", fixedSize: 13))
                        .foregroundStyle(StoryPalette.paper)
                        .lineLimit(1)
                    Text("@" + handle)
                        .font(.custom("Geist-Regular", fixedSize: 11))
                        .foregroundStyle(StoryPalette.paper.opacity(0.6))
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
                ZStack {
                    Circle().fill(on ? StoryPalette.gold : Color.clear)
                    Circle().stroke(on ? StoryPalette.gold : StoryPalette.paper.opacity(0.5), lineWidth: 1.5)
                    if on {
                        WebIcon(path: Icons.check, size: 13, line: 2.4).foregroundStyle(StoryPalette.ink)
                    }
                }
                .frame(width: 22, height: 22)
            }
            .padding(.vertical, 7)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(name.isEmpty ? "@" + handle : name)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    private func optionButton(_ label: String, on: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.custom("Geist-SemiBold", fixedSize: 12))
                .foregroundStyle(on ? StoryPalette.dark : StoryPalette.paper)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 9)
                .background(on ? StoryPalette.paper : Color.clear, in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(StoryPalette.paper.opacity(0.35), lineWidth: 1))
                .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    private var picturePanel: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                ForEach(StoryChoices.frames, id: \.self) { item in
                    optionButton(item.label, on: ed.draft.layout.frame == item.key) { ed.setFrame(item.key) }
                }
            }
            .padding(.bottom, 12)
            HStack(spacing: 8) {
                optionButton("Smaller", on: false) { ed.zoom(1 / 1.15) }
                optionButton("Bigger", on: false) { ed.zoom(1.15) }
                optionButton("Reset", on: false) { ed.zoom(0) }
            }
            hint("Pinch the picture to make it bigger or smaller, and drag it to move it.")
        }
    }

    private var backgroundPanel: some View {
        let five = Array(repeating: GridItem(.flexible(), spacing: 12), count: 5)
        return VStack(alignment: .leading, spacing: 0) {
            LazyVGrid(columns: five, spacing: 12) {
                ForEach(StoryPalette.backgrounds, id: \.self) { name in
                    let on = ed.draft.layout.bg == name
                    Button { ed.setBg(name) } label: {
                        StoryPalette.background(name)
                            .aspectRatio(1, contentMode: .fit)
                            .clipShape(Circle())
                            .overlay(Circle().stroke(on ? StoryPalette.paper : StoryPalette.paper.opacity(0.25),
                                                     lineWidth: 2))
                            .overlay(Circle().inset(by: -2).stroke(on ? StoryPalette.paper.opacity(0.35) : Color.clear,
                                                                   lineWidth: 2))
                    }
                    .buttonStyle(.press)
                    .accessibilityLabel(name)
                    .accessibilityAddTraits(on ? .isSelected : [])
                }
            }
            hint("A colour or a gradient behind your words.")
        }
    }

    // MARK: Your story and the arrow

    private var foot: some View {
        let me = model.you.profile
        let circle = model.circle?.me
        let name = me?.name ?? circle?.name ?? ""
        let handle = me?.handle ?? circle?.handle ?? ""
        let avatar = me?.avatar ?? circle?.avatar ?? ""
        return HStack(spacing: 8) {
            Button { send() } label: {
                HStack(spacing: 8) {
                    Face(name: name, handle: handle, avatar: avatar, size: 26, initialsSize: 10)
                    Text("Your story")
                        .font(.custom("Geist-SemiBold", fixedSize: 12.5))
                        .foregroundStyle(StoryPalette.paper)
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 8)
                .frame(maxWidth: .infinity, minHeight: 42)
                .background(StoryPalette.pill, in: Capsule())
                .contentShape(Capsule())
            }
            .buttonStyle(.press)
            .accessibilityLabel("Your story")
            Button { send() } label: {
                WebIcon(path: StoryIcons.arrow, size: 18, line: 2.4)
                    .foregroundStyle(StoryPalette.ink)
                    .frame(width: 42, height: 42)
                    .background(StoryPalette.gold, in: Circle())
            }
            .buttonStyle(.press)
            .accessibilityLabel("Post to your story")
        }
        .disabled(ed.sending)
    }

    private func send() {
        boxFocused = false
        searchFocused = false
        ed.post(done: { dismiss() }, openMine: openMine)
    }
}

// MARK: - Speak: the phone's dictation into the box

/// Apple's recogniser into the note's words, the way searchFAM's mic hears
/// a question (`vcsDictate`); recognised on the device where it can be.
@MainActor
final class StoryDictation {
    var onHeard: (String) -> Void = { _ in }
    var onEnd: () -> Void = {}
    var onError: (String) -> Void = { _ in }
    var keepsPlayback: () -> Bool = { false }

    static let blocked = "The microphone is blocked for FAM"
    static let cannotStart = "Could not start the microphone"

    private var engine: AVAudioEngine?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var generation = 0
    private var holdsSession = false

    func start() {
        generation += 1
        let mine = generation
        Task { await begin(mine) }
    }

    func stop() {
        generation += 1
        teardown()
    }

    private func begin(_ mine: Int) async {
        let micOK = await Self.microphoneAllowed()
        guard mine == generation else { return }
        guard micOK else { fail(Self.blocked); return }
        let speech = await Self.speechAllowed()
        guard mine == generation else { return }
        guard speech == .authorized else { fail(Self.blocked); return }
        guard let recognizer = VoiceSearchModel.makeRecognizer(), recognizer.isAvailable else {
            fail(Self.cannotStart)
            return
        }
        let session = AVAudioSession.sharedInstance()
        holdsSession = true
        do {
            try session.setCategory(.record, mode: .measurement, options: [])
            try session.setActive(true, options: .notifyOthersOnDeactivation)
        } catch {
            restoreSession()
            fail(Self.cannotStart)
            return
        }
        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.channelCount > 0, format.sampleRate > 0 else {
            restoreSession()
            fail(Self.cannotStart)
            return
        }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.taskHint = .dictation
        if recognizer.supportsOnDeviceRecognition { request.requiresOnDeviceRecognition = true }
        input.installTap(onBus: 0, bufferSize: 1024, format: format, block: Self.tap(request))
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            restoreSession()
            fail(Self.cannotStart)
            return
        }
        self.engine = engine
        self.request = request
        task = recognizer.recognitionTask(with: request, resultHandler: Self.handler(mine, self))
    }

    nonisolated private static func tap(_ request: SFSpeechAudioBufferRecognitionRequest) -> AVAudioNodeTapBlock {
        return { buffer, _ in request.append(buffer) }
    }

    nonisolated private static func handler(_ mine: Int, _ owner: StoryDictation)
        -> (SFSpeechRecognitionResult?, Error?) -> Void {
        return { result, error in
            let text = result?.bestTranscription.formattedString ?? ""
            let finished = (result?.isFinal ?? false) || error != nil
            Task { @MainActor in
                owner.received(mine, text: text, finished: finished)
            }
        }
    }

    private func received(_ mine: Int, text: String, finished: Bool) {
        guard mine == generation else { return }
        if !text.isEmpty { onHeard(text) }
        if finished {
            stop()
            onEnd()
        }
    }

    private func fail(_ sentence: String) {
        generation += 1
        teardown()
        onError(sentence)
    }

    private func teardown() {
        if let engine {
            engine.stop()
            engine.inputNode.removeTap(onBus: 0)
        }
        engine = nil
        request?.endAudio()
        task?.cancel()
        request = nil
        task = nil
        restoreSession()
    }

    private func restoreSession() {
        guard holdsSession else { return }
        holdsSession = false
        let session = AVAudioSession.sharedInstance()
        if keepsPlayback() {
            try? session.setCategory(.playback, mode: .spokenAudio)
            try? session.setActive(true)
        } else {
            try? session.setActive(false, options: .notifyOthersOnDeactivation)
            try? session.setCategory(.playback, mode: .spokenAudio)
        }
    }

    nonisolated private static func microphoneAllowed() async -> Bool {
        switch AVAudioApplication.shared.recordPermission {
        case .granted: return true
        case .denied: return false
        default:
            return await withCheckedContinuation { (c: CheckedContinuation<Bool, Never>) in
                AVAudioApplication.requestRecordPermission { granted in c.resume(returning: granted) }
            }
        }
    }

    nonisolated private static func speechAllowed() async -> SFSpeechRecognizerAuthorizationStatus {
        let now = SFSpeechRecognizer.authorizationStatus()
        if now != .notDetermined { return now }
        return await withCheckedContinuation { (c: CheckedContinuation<SFSpeechRecognizerAuthorizationStatus, Never>) in
            SFSpeechRecognizer.requestAuthorization { status in c.resume(returning: status) }
        }
    }
}

// MARK: - The editor's icons, path for path

enum StoryIcons {
    /// Stickers: the folded-corner sticker with a smile.
    static let sticker: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 15.5, y: 3)); p.addLine(to: CGPoint(x: 7, y: 3))
        p.addQuadCurve(to: CGPoint(x: 3, y: 7), control: CGPoint(x: 3, y: 3))
        p.addLine(to: CGPoint(x: 3, y: 17))
        p.addQuadCurve(to: CGPoint(x: 7, y: 21), control: CGPoint(x: 3, y: 21))
        p.addLine(to: CGPoint(x: 13.5, y: 21)); p.addLine(to: CGPoint(x: 21, y: 13.5))
        p.addLine(to: CGPoint(x: 21, y: 7))
        p.addQuadCurve(to: CGPoint(x: 17, y: 3), control: CGPoint(x: 21, y: 3))
        p.closeSubpath()
        p.move(to: CGPoint(x: 13.5, y: 21)); p.addLine(to: CGPoint(x: 13.5, y: 17.5))
        p.addQuadCurve(to: CGPoint(x: 17.5, y: 13.5), control: CGPoint(x: 13.5, y: 13.5))
        p.addLine(to: CGPoint(x: 21, y: 13.5))
        p.move(to: CGPoint(x: 8.5, y: 13.5))
        p.addQuadCurve(to: CGPoint(x: 12, y: 15), control: CGPoint(x: 9.7, y: 15))
        p.addEllipse(in: CGRect(x: 8.4, y: 8.4, width: 1.2, height: 1.2))
        p.addEllipse(in: CGRect(x: 14.4, y: 8.4, width: 1.2, height: 1.2))
        return p
    }()

    /// Picture: the two sliders (`M4 7h10M18 7h2M4 17h4M12 17h8`, two knobs).
    static let sliders: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 4, y: 7)); p.addLine(to: CGPoint(x: 14, y: 7))
        p.move(to: CGPoint(x: 18, y: 7)); p.addLine(to: CGPoint(x: 20, y: 7))
        p.move(to: CGPoint(x: 4, y: 17)); p.addLine(to: CGPoint(x: 8, y: 17))
        p.move(to: CGPoint(x: 12, y: 17)); p.addLine(to: CGPoint(x: 20, y: 17))
        p.addEllipse(in: CGRect(x: 14, y: 5, width: 4, height: 4))
        p.addEllipse(in: CGRect(x: 8, y: 15, width: 4, height: 4))
        return p
    }()

    /// Speak: the mic (`rect 9,3 6x11 rx3`, `M5 11a7 7 0 0 0 14 0M12 18v3`).
    static let mic: Path = {
        var p = Path()
        p.addRoundedRect(in: CGRect(x: 9, y: 3, width: 6, height: 11), cornerSize: CGSize(width: 3, height: 3))
        p.move(to: CGPoint(x: 5, y: 11))
        p.addArc(center: CGPoint(x: 12, y: 11), radius: 7, startAngle: .degrees(180),
                 endAngle: .degrees(0), clockwise: true)
        p.move(to: CGPoint(x: 12, y: 18)); p.addLine(to: CGPoint(x: 12, y: 21))
        return p
    }()

    /// Post: the arrow (`M5 12h14M13 6l6 6-6 6`).
    static let arrow: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 5, y: 12)); p.addLine(to: CGPoint(x: 19, y: 12))
        p.move(to: CGPoint(x: 13, y: 6)); p.addLine(to: CGPoint(x: 19, y: 12)); p.addLine(to: CGPoint(x: 13, y: 18))
        return p
    }()
}

// MARK: - Posting

extension FAMAPI {
    /// VIBE! the episode to your story with its layout and @tags
    /// (`POST /api/vibe` - `/api/echo` is the same handler). Each newly
    /// tagged friend is sent the episode by the server.
    func vibeStory(_ ep: EpisodeKey, title: String, thread: String, caption: String,
                   style: [String: Any], tags: [[String: Any]]) async throws {
        let body: [String: Any] = [
            "query": ep.query, "title": title, "minutes": ep.minutes, "thread": thread,
            "caption": caption, "style": style, "tags": tags,
        ]
        _ = try await send("POST", "api/v1/vibe", body: body, as: Ignored.self)
    }

    /// A story with no episode (`POST /api/story/note`): your words on a
    /// background, and stickers. An account's only.
    func postNote(text: String, style: [String: Any]) async throws {
        let body: [String: Any] = ["text": text, "style": style]
        _ = try await send("POST", "api/v1/story/note", body: body, as: Ignored.self)
    }
}

// MARK: - Your own story, opened from a snack's View

extension StoryReel {
    /// Your story as the faces row has it (`viewMyNewStory`), or nil.
    @MainActor static func mine(_ model: AppModel) -> StoryReel? {
        guard let me = model.circle?.me, let stories = me.stories, !stories.isEmpty else { return nil }
        return StoryReel(people: [StoryPerson(id: "me", name: "Your story", handle: me.handle ?? "",
                                              avatar: me.avatar ?? "", stories: stories, mine: true)],
                         start: 0)
    }
}

/// The story viewer with this phone's seen marks, for a screen that has
/// no faces row of its own (the player's View).
struct StoryViewerCover: View {
    @EnvironmentObject private var model: AppModel
    let reel: StoryReel
    @AppStorage("storiesSeen") private var seenJSON = "{}"

    var body: some View {
        StoryViewer(reel: reel, markSeen: markSeen, seenAt: { seen[$0] ?? 0 })
            .environmentObject(model)
    }

    private var seen: [String: Double] {
        (try? JSONDecoder().decode([String: Double].self, from: Data(seenJSON.utf8))) ?? [:]
    }

    private func markSeen(_ id: String, _ at: Double) {
        var map = seen
        map[id] = max(map[id] ?? 0, at)
        if let data = try? JSONEncoder().encode(map) { seenJSON = String(decoding: data, as: UTF8.self) }
    }
}
