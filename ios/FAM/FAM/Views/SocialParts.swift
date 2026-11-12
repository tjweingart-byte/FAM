import FAMSafety
import SwiftUI
import UIKit

// The pieces Messages, a conversation, Friends and a profile share, each
// measured from the web (`static/index.html`; the shots and Appendix A of
// the social spec): the dashed notice card (`.pf-empty`), the round 36 pt
// buttons (`.yf-round`), the action sheet (`#sheetOverlay`), the in-app
// banner (`#notifBanner`), the follower popup (`#followerOverlay`), and the
// web's clock words (`yfWhen`) - "16:17", as the web prints them.

// MARK: - Icons the social screens draw (the web's SVG paths)

extension Icons {
    /// Messages (`M4 4h16a2 2 0 012 2v9a2 2 0 01-2 2H9l-4 4v-4H4a2 2 0 01-2-2V6a2 2 0 012-2z`):
    /// the speech bubble, also its own `.tone` fill.
    static let messages: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 4, y: 4)); p.addLine(to: CGPoint(x: 20, y: 4))
        p.addQuadCurve(to: CGPoint(x: 22, y: 6), control: CGPoint(x: 22, y: 4))
        p.addLine(to: CGPoint(x: 22, y: 15))
        p.addQuadCurve(to: CGPoint(x: 20, y: 17), control: CGPoint(x: 22, y: 17))
        p.addLine(to: CGPoint(x: 9, y: 17)); p.addLine(to: CGPoint(x: 5, y: 21))
        p.addLine(to: CGPoint(x: 5, y: 17)); p.addLine(to: CGPoint(x: 4, y: 17))
        p.addQuadCurve(to: CGPoint(x: 2, y: 15), control: CGPoint(x: 2, y: 17))
        p.addLine(to: CGPoint(x: 2, y: 6))
        p.addQuadCurve(to: CGPoint(x: 4, y: 4), control: CGPoint(x: 2, y: 4))
        p.closeSubpath()
        return p
    }()

    /// The profile's Message (`M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z`).
    static let chat: Path = {
        var p = Path()
        let c = CGPoint(x: 13, y: 12)
        p.move(to: CGPoint(x: 21, y: 12))
        p.addArc(center: c, radius: 8, startAngle: .degrees(0), endAngle: .degrees(116.9), clockwise: false)
        p.addLine(to: CGPoint(x: 4, y: 20))
        p.addLine(to: CGPoint(x: 5, y: 15.4))
        p.addArc(center: c, radius: 8, startAngle: .degrees(157), endAngle: .degrees(360), clockwise: false)
        p.closeSubpath()
        return p
    }()

    /// The single back chevron (`M15 5l-7 7 7 7`).
    static let backOne: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 15, y: 5)); p.addLine(to: CGPoint(x: 8, y: 12)); p.addLine(to: CGPoint(x: 15, y: 19))
        return p
    }()

    /// The composer's send (`M5 12h14M13 6l6 6-6 6`).
    static let arrow: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 5, y: 12)); p.addLine(to: CGPoint(x: 19, y: 12))
        p.move(to: CGPoint(x: 13, y: 6)); p.addLine(to: CGPoint(x: 19, y: 12)); p.addLine(to: CGPoint(x: 13, y: 18))
        return p
    }()

    /// Block (`MENU_ICONS.block`: `circle r9`, `M5.6 5.6l12.8 12.8`).
    static let blockMark: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 3, y: 3, width: 18, height: 18))
        p.move(to: CGPoint(x: 5.6, y: 5.6)); p.addLine(to: CGPoint(x: 18.4, y: 18.4))
        return p
    }()

    /// Search messages / friends (`circle cx11 cy11 r7`, `M21 21l-4.3-4.3`).
    static let magnifier: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 4, y: 4, width: 14, height: 14))
        p.move(to: CGPoint(x: 21, y: 21)); p.addLine(to: CGPoint(x: 16.7, y: 16.7))
        return p
    }()
}

// MARK: - Words for times (`yfWhen`, the day separators)

enum SocialTime {
    static func date(_ at: Double) -> Date { Date(timeIntervalSince1970: at) }

    /// The web's clock (`yfWhen`): the hour without a leading zero and the
    /// minutes, on 24 hours ("9:02", "16:17").
    static func clock(_ at: Double) -> String {
        let parts = Calendar.current.dateComponents([.hour, .minute], from: date(at))
        return String(format: "%d:%02d", parts.hour ?? 0, parts.minute ?? 0)
    }

    /// A list row's time: today the clock, within 6.5 days the weekday
    /// ("Sun"), else "Nov 1".
    static func when(_ at: Double) -> String {
        let d = date(at)
        if Calendar.current.isDateInToday(d) { return clock(at) }
        if Date().timeIntervalSince(d) < 6.5 * 86_400 {
            return d.formatted(.dateTime.weekday(.abbreviated))
        }
        return d.formatted(.dateTime.month(.abbreviated).day())
    }

    /// A conversation's day separator: "TODAY 16:17", else "SUN, NOV 8".
    static func day(_ at: Double) -> String {
        let d = date(at)
        if Calendar.current.isDateInToday(d) { return "TODAY " + clock(at) }
        return d.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day()).uppercased()
    }

    static func sameDay(_ a: Double, _ b: Double) -> Bool {
        Calendar.current.isDate(date(a), inSameDayAs: date(b))
    }
}

/// `firstName`: the first word of the name, else of the handle.
func socialFirstName(_ name: String, handle: String = "") -> String {
    let source = name.trimmingCharacters(in: .whitespaces).isEmpty ? handle : name
    let first = source.split(separator: " ").first.map(String.init) ?? ""
    return first.isEmpty ? "Someone" : first
}

// MARK: - The dashed notice (`.pf-empty`)

/// Padding 15, a 1 pt dashed strong border, radius 12, Geist 11 / 16.5 muted.
struct SocialNote<Extra: View>: View {
    let text: String
    @ViewBuilder var extra: () -> Extra

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(text)
                .font(FAMFont.body(11))
                .foregroundStyle(FAMColor.textMuted)
                .lineSpacing(5.5)
                .fixedSize(horizontal: false, vertical: true)
            extra()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(15)
        .overlay(RoundedRectangle(cornerRadius: 12)
            .stroke(FAMColor.borderStrong, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
        .padding(.top, 12)
    }
}

extension SocialNote where Extra == EmptyView {
    init(text: String) {
        self.text = text
        self.extra = { EmptyView() }
    }
}

// MARK: - Round buttons (`.yf-round`)

/// A 36 pt circle: outlined, or gold (`.yf-round.gold`).
struct RoundIcon<Mark: View>: View {
    var size: CGFloat = 36
    var gold = false
    @ViewBuilder var glyph: () -> Mark

    var body: some View {
        glyph()
            .foregroundStyle(gold ? FAMColor.ink : FAMColor.text)
            .frame(width: size, height: size)
            .background(gold ? FAMColor.copper : Color.clear, in: Circle())
            .overlay(Circle().stroke(gold ? FAMColor.copper : FAMColor.borderStrong))
            .contentShape(Circle())
    }
}

/// The web's back button on a social screen (`.yf-back`, 36 pt, chevron 20).
struct SocialBackButton: View {
    /// What VoiceOver reads: "Back", or the web's own label where it has
    /// one ("Back to messages" on a conversation).
    var label: String = "Back"
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            WebIcon(path: Icons.backOne, size: 20, line: 2.2)
                .foregroundStyle(FAMColor.text)
                .frame(width: 36, height: 36)
                .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .padding(.leading, -8)
        .accessibilityLabel(label)
    }
}

// MARK: - A row of chips that wraps (`#threadChips`)

struct FlowRow: Layout {
    var spacing: CGFloat = 7

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 10_000
        var x: CGFloat = 0
        var y: CGFloat = 0
        var line: CGFloat = 0
        var widest: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0 && x + size.width > width {
                y += line + spacing
                x = 0
                line = 0
            }
            x += size.width + spacing
            line = max(line, size.height)
            widest = max(widest, x - spacing)
        }
        return CGSize(width: min(widest, width), height: y + line)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var line: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > bounds.minX && x + size.width > bounds.maxX {
                y += line + spacing
                x = bounds.minX
                line = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            line = max(line, size.height)
        }
    }
}

/// An interest chip (`.yf-chip`); "N in common" is copper.
struct InterestChip: View {
    let label: String
    var common = false
    var height: CGFloat = 23
    var size: CGFloat = 10

    var body: some View {
        Text(label)
            .font(FAMFont.body(size))
            .foregroundStyle(common ? FAMColor.copper : FAMColor.text)
            .lineLimit(1)
            .padding(.horizontal, height > 24 ? 11 : 9)
            .frame(height: height)
            .overlay(Capsule().stroke(common ? FAMColor.copper : FAMColor.borderStrong))
    }
}

// MARK: - The action sheet (`showActionSheet`, `#sheetOverlay`)

struct SheetAction: Identifiable {
    let id = UUID()
    let label: String
    var danger = false
    let run: () -> Void
}

struct SheetSpec: Identifiable {
    let id = UUID()
    var header: String = ""
    let actions: [SheetAction]

    /// The card's height: 10 on top, the header (a long sentence - a
    /// block's or a leave's - over several lines), 46 a row, Cancel and 22.
    var height: CGFloat {
        let lines = max(1, (header.count + 39) / 40)
        let head: CGFloat = header.isEmpty ? 0 : 20 + 13.5 * CGFloat(lines)
        return 10 + head + CGFloat(actions.count) * 46 + 8 + 41 + 22 + 12
    }
}

/// Rows 46 pt, Geist 13.5 semibold, danger rows coral; an optional mono
/// header; a separate white Cancel. A row closes the sheet, then runs.
struct FAMActionSheet: View {
    let spec: SheetSpec
    @Environment(\.dismiss) private var dismiss
    /// The card grows with the phone's text size (its rows' 46 pt are
    /// floors, `large-text`); past 640 pt the rows scroll.
    @ScaledMetric(relativeTo: .body) private var grow: CGFloat = 1

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !spec.header.isEmpty { SheetGroupLabel(text: spec.header) }
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(spec.actions) { action in
                        row(action)
                    }
                }
            }
            .scrollBounceBehavior(.basedOnSize)
            SheetCancelButton { dismiss() }
        }
        .padding(.top, 10)
        .padding(.bottom, 12)
        .background(FAMColor.surface)
        .frame(height: min(spec.height * grow, 640))
    }

    private func row(_ action: SheetAction) -> some View {
        Button {
            dismiss()
            // Whatever the row opens waits for the sheet to finish closing.
            let run = action.run
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 450_000_000)
                run()
            }
        } label: {
            Text(action.label)
                .font(FAMFont.body(13.5, .semibold))
                .foregroundStyle(action.danger ? FAMColor.coral : FAMColor.text)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, minHeight: 46, alignment: .leading)
                .padding(.horizontal, 22)
                .contentShape(Rectangle())
        }
        .buttonStyle(.press)
    }
}

// MARK: - The centred modal card (`.modal-card`)

/// 312 wide, padding 20, radius 18, surface, a strong border, the web's
/// shadow, over a dim that closes it.
struct ModalCard<Content: View>: View {
    let close: () -> Void
    @ViewBuilder var content: () -> Content

    var body: some View {
        ZStack {
            Color.clear
                .contentShape(Rectangle())
                .ignoresSafeArea()
                .onTapGesture { close() }
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 0) {
                content()
            }
            .padding(20)
            .frame(width: 312)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18).stroke(FAMColor.borderStrong))
            .shadow(color: .black.opacity(0.35), radius: 20, y: 20)
        }
        // A centred card over the dim the overlay draws (`famSheet`).
        .modalLayout()
    }
}

/// The modal's buttons (`.modal-btn`): 34 tall, radius 20, Geist 12 bold.
struct ModalButton: View {
    let label: String
    var primary = false
    var wide = false
    var enabled = true
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(label)
                .font(FAMFont.body(12, .bold))
                .foregroundStyle(primary ? FAMColor.ink : FAMColor.textMuted)
                .padding(.horizontal, 16)
                .frame(maxWidth: wide ? .infinity : nil)
                .frame(minHeight: 34)
                .background(primary ? FAMColor.copper : Color.clear, in: Capsule())
        }
        // Disabled reads at half strength - `.press` draws that dim itself.
        .buttonStyle(.press)
        .disabled(!enabled)
    }
}

/// A modal's white field (`.modal-input`): 41 tall, radius 10, Geist 13.
struct ModalField: View {
    let placeholder: String
    @Binding var text: String
    var plain = false

    var body: some View {
        TextField("", text: $text, prompt: Text(placeholder).foregroundColor(FAMColor.textFaint))
            .font(FAMFont.body(13))
            .foregroundStyle(FAMColor.text)
            .textInputAutocapitalization(plain ? .never : .sentences)
            .autocorrectionDisabled(plain)
            .padding(.horizontal, 12)
            .frame(minHeight: 41)
            .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(FAMColor.borderStrong))
    }
}

// MARK: - The drop-down banner (`#notifBanner`, shots 35-37)

/// Inset 12 from the sides and 10 from the top, radius 16, surface, a
/// strong border and the web's shadow; a 34 pt face, the title Geist 12.5
/// bold, the text Geist 11 muted, a 12 pt X. A tap opens; a swipe up of
/// 28 pt or the X dismisses; a finger on it holds it.
struct SocialBannerLayer: View {
    @EnvironmentObject private var social: SocialModel
    @State private var drag: CGFloat = 0

    var body: some View {
        if let b = social.banner {
            card(b)
                .padding(.horizontal, 12)
                .padding(.top, 10)
                .offset(y: min(0, drag))
                .transition(.move(edge: .top).combined(with: .opacity))
                .gesture(swipe)
                .zIndex(10)
        }
    }

    private func card(_ b: SocialBanner) -> some View {
        HStack(spacing: 11) {
            Button { social.openBanner(b) } label: {
                HStack(spacing: 11) {
                    Face(name: b.name, handle: b.handle, avatar: b.avatar, size: 34, initialsSize: 11)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(b.title)
                            .font(FAMFont.body(12.5, .bold))
                            .foregroundStyle(FAMColor.text)
                            .lineLimit(1)
                        Text(b.text)
                            .font(FAMFont.body(11))
                            .foregroundStyle(FAMColor.textMuted)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.press)
            .accessibilityHint(b.chat == nil ? "Opens Friends" : "Opens the conversation")
            Button { social.dismissBanner() } label: {
                WebIcon(path: Icons.close, size: 12, line: 2.6)
                    .foregroundStyle(FAMColor.textMuted)
                    .frame(width: 24, height: 24)
            }
            .buttonStyle(.press)
            .accessibilityLabel("Dismiss")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 11)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(FAMColor.borderStrong))
        // `0 22px 44px -18px rgba(0,0,0,.75)`: SwiftUI has no spread, so
        // the -18 is taken out of the strength rather than the size.
        .shadow(color: .black.opacity(0.5), radius: 22, y: 22)
    }

    private var swipe: some Gesture {
        DragGesture(minimumDistance: 4)
            .onChanged { value in
                if drag == 0 { social.holdBanner(true) }
                drag = value.translation.height
            }
            .onEnded { value in
                let up = value.translation.height
                drag = 0
                if up <= -28 { social.dismissBanner() } else { social.holdBanner(false) }
            }
    }
}

// MARK: - "<Name> started following you" (`#followerOverlay`, shots 33-34)

struct FollowerPopup: View {
    @EnvironmentObject private var social: SocialModel
    let person: FollowPerson

    /// `showFollowerPopup`: the name, else "@handle", else "@someone".
    private var name: String {
        let n = person.name ?? ""
        if !n.isEmpty { return n }
        let h = person.handle ?? ""
        return "@" + (h.isEmpty ? "someone" : h)
    }

    var body: some View {
        ZStack {
            Color(hex: 0x0A090F).opacity(0.62)
                .ignoresSafeArea()
                .onTapGesture { social.notNow() }
                .accessibilityHidden(true)
            VStack(spacing: 0) {
                Face(name: person.name ?? "", handle: person.handle ?? "", avatar: person.avatar ?? "",
                     size: 64, initialsSize: 22)
                    .padding(.top, 6)
                    .padding(.bottom, 12)
                (Text(name).font(FAMFont.heading(16, .semibold)).foregroundColor(FAMColor.text)
                 + Text(" started following you").font(FAMFont.heading(16, .medium)).foregroundColor(FAMColor.textMuted))
                    .multilineTextAlignment(.center)
                    .lineSpacing(5.6)
                if let h = person.handle, !h.isEmpty {
                    Text("@" + h)
                        .font(FAMFont.mono(9))
                        .tracking(0.72)
                        .foregroundStyle(FAMColor.textFaint)
                        .padding(.top, 5)
                }
                if person.followsBack == true {
                    // `#nfAlready`: Geist Mono 8.5, uppercase, sage (shot 34).
                    Text("You already follow them \u{2014} you are friends.")
                        .monoLabel(8.5, color: FAMColor.sage, tracking: 0.425)
                        .multilineTextAlignment(.center)
                        .padding(.top, 18)
                        .padding(.bottom, 10)
                } else {
                    ModalButton(label: "Follow back", primary: true, wide: true,
                                enabled: !social.followingBack) { social.followBack(person) }
                        .padding(.top, 14)
                        .padding(.bottom, 8)
                }
                ModalButton(label: "Not now", wide: true) { social.notNow() }
            }
            .padding(20)
            .frame(width: 312)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18).stroke(FAMColor.borderStrong))
            .overlay(alignment: .topTrailing) {
                Button { social.notNow() } label: {
                    WebIcon(path: Icons.close, size: 14, line: 2.2)
                        .foregroundStyle(FAMColor.textMuted)
                        .frame(width: 28, height: 28)
                }
                .buttonStyle(.press)
                .padding(13)
                .accessibilityLabel("Ignore this")
            }
            .shadow(color: .black.opacity(0.35), radius: 20, y: 20)
            .liftsIn()
        }
        .transition(.famDim(reduceMotion: UIAccessibility.isReduceMotionEnabled))
    }
}

// MARK: - A notice with one bold word that opens something (shot 04)

/// `.pf-empty` whose sentence carries a bold, tappable word inline - the
/// empty inbox's "…find them under **Friends**." The whole card is the
/// button, as the word is the only thing on it to tap.
struct SocialLinkNote: View {
    let before: String
    let link: String
    let after: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            (Text(before)
             + Text(link).font(FAMFont.body(11, .bold)).foregroundColor(FAMColor.textMuted)
             + Text(after))
                .font(FAMFont.body(11))
                .foregroundStyle(FAMColor.textMuted)
                .lineSpacing(5.5)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(15)
                .overlay(RoundedRectangle(cornerRadius: 12)
                    .stroke(FAMColor.borderStrong, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
                .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .padding(.top, 12)
        .accessibilityHint("Opens \(link)")
    }
}

// MARK: - Report, as the web's action sheet (`reportContent`, shots 18-19)

/// The server's reasons under "WHY ARE YOU REPORTING THIS?", one 46 pt row
/// each, and Cancel - the shared action sheet's look, not a system list.
/// A tap sends; the sheet closes and the server's receipt ("Thanks for
/// telling us…") is the toast, as on the web. `done` gets the receipt so
/// the caller can take a hidden thing off the screen.
struct FAMReportSheet: View {
    let api: SafetyAPI
    let subject: ReportSubject
    let say: (String) -> Void
    let done: (ReportReceipt?) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var reasons: [ReportReason] = []
    @State private var sending = false
    @ScaledMetric(relativeTo: .body) private var grow: CGFloat = 1

    /// `GET /api/report` once a session, as the web caches it (only ever
    /// touched from the sheet's own main-actor task).
    private static var cached: [ReportReason] = []

    private var height: CGFloat {
        let rows = max(reasons.count, 1)
        return 10 + 33 + CGFloat(rows) * 46 + 8 + 41 + 22 + 12
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SheetGroupLabel(text: "Why are you reporting this?")
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    if reasons.isEmpty {
                        Text("Loading\u{2026}")
                            .font(FAMFont.body(13.5, .semibold))
                            .foregroundStyle(FAMColor.textMuted)
                            .frame(maxWidth: .infinity, minHeight: 46, alignment: .leading)
                            .padding(.horizontal, 22)
                    }
                    ForEach(reasons) { reason in
                        row(reason)
                    }
                }
            }
            .scrollBounceBehavior(.basedOnSize)
            SheetCancelButton {
                done(nil)
                dismiss()
            }
        }
        .padding(.top, 10)
        .padding(.bottom, 12)
        .background(FAMColor.surface)
        .frame(height: min(height * grow, 640))
        .task { await loadReasons() }
    }

    private func row(_ reason: ReportReason) -> some View {
        Button { send(reason) } label: {
            Text(reason.label)
                .font(FAMFont.body(13.5, .semibold))
                .foregroundStyle(FAMColor.text)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, minHeight: 46, alignment: .leading)
                .padding(.horizontal, 22)
                .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .disabled(sending)
    }

    private func loadReasons() async {
        guard reasons.isEmpty else { return }
        if !FAMReportSheet.cached.isEmpty {
            reasons = FAMReportSheet.cached
            return
        }
        do {
            let options = try await api.reportOptions()
            FAMReportSheet.cached = options.reasons
            reasons = options.reasons
        } catch {
            say("Couldn\u{2019}t reach FAM. Try again.")
            done(nil)
            dismiss()
        }
    }

    private func send(_ reason: ReportReason) {
        guard !sending else { return }
        sending = true
        let api = self.api
        let subject = self.subject
        Task { @MainActor in
            do {
                let receipt = try await api.report(subject, reason: reason.id)
                dismiss()
                say(receipt.message)
                done(receipt)
            } catch SafetyError.http(_, let detail) where !detail.isEmpty {
                sending = false
                dismiss()
                say(detail)
                done(nil)
            } catch SafetyError.accountRequired(let detail) where !detail.isEmpty {
                sending = false
                dismiss()
                say(detail)
                done(nil)
            } catch {
                sending = false
                dismiss()
                say("Couldn\u{2019}t send that report")
                done(nil)
            }
        }
    }
}

// MARK: - The screens, wherever they are shown

/// One social route as its screen.
struct SocialScreen: View {
    @EnvironmentObject private var model: AppModel
    let route: SocialRoute

    var body: some View {
        switch route {
        case .thread(let open):
            ChatScreen(api: model.api, open: open)
        case .person(let open):
            PersonView(open: open)
        case .friends:
            FriendsView()
        case .topic(let open):
            TopicView(open: open)
        case .vibePosts(let open):
            VibePostsView(open: open)
        }
    }
}

/// Whether a social screen is inside a cover (always on screen while the
/// cover is up) rather than on the Messages tab's stack (on screen only
/// while that tab is).
private struct InSocialCoverKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    var inSocialCover: Bool {
        get { self[InSocialCoverKey.self] }
        set { self[InSocialCoverKey.self] = newValue }
    }
}

/// A social screen opened from another tab: its own stack, the banners
/// and the toast over it, no tab bar (the web's pushed screens have none).
struct SocialCoverView: View {
    @EnvironmentObject private var social: SocialModel
    let cover: SocialCover

    var body: some View {
        NavigationStack(path: $social.coverPath) {
            SocialScreen(route: cover.root)
                .navigationDestination(for: SocialRoute.self) { route in
                    SocialScreen(route: route)
                }
        }
        .environment(\.inSocialCover, true)
        .overlay(alignment: .top) { SocialBannerLayer() }
        .overlay(alignment: .bottom) { ToastView().padding(.bottom, 80) }
    }
}

// MARK: - Block, as the web's action sheet (`blockPerson`)

/// "Block <name>? Neither of you will see…" as the sheet's group line and
/// one coral "Block" row, as the web asks (`blockPerson`), not a system
/// dialog. The block itself: the server's sentence on a refusal, else
/// "Couldn’t block them"; on success the caller's `blocked` (which says
/// "Blocked" and re-reads what it shows).
@MainActor
enum FAMBlock {
    static let consequence = "Neither of you will see the other\u{2019}s comments, messages, vibes or profile, and they can\u{2019}t message you. They won\u{2019}t be told."

    static func sheet(name: String, handle: String, model: AppModel,
                      blocked: @escaping () -> Void) -> SheetSpec {
        let who = name.isEmpty ? "@" + handle : name
        let action = SheetAction(label: "Block", danger: true) {
            run(handle: handle, model: model, blocked: blocked)
        }
        return SheetSpec(header: "Block " + who + "? " + consequence, actions: [action])
    }

    static func run(handle: String, model: AppModel, blocked: @escaping () -> Void) {
        guard !handle.isEmpty else { return }
        let safety = model.safety
        Task { @MainActor in
            do {
                try await safety.block(handle: handle)
                blocked()
            } catch SafetyError.http(_, let detail) where !detail.isEmpty {
                model.nowPlaying.say(detail)
            } catch SafetyError.accountRequired(let detail) where !detail.isEmpty {
                model.nowPlaying.say(detail)
            } catch {
                model.nowPlaying.say("Couldn\u{2019}t block them")
            }
        }
    }
}

// MARK: - Follow's tick (`Motion.follow`)

/// The tick on "✓ Following": drawn in over 220 ms after 80 ms
/// (`M4 12.5l5 5L20 7`, stroke 3, 11 pt); whole at once under Reduce Motion.
struct FollowTick: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var drawn: CGFloat = 0

    private static let path: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 4, y: 12.5))
        p.addLine(to: CGPoint(x: 9, y: 17.5))
        p.addLine(to: CGPoint(x: 20, y: 7))
        return p
    }()

    var body: some View {
        Glyph(path: FollowTick.path, box: CGRect(x: 0, y: 0, width: 24, height: 24))
            .trim(from: 0, to: drawn)
            .stroke(style: StrokeStyle(lineWidth: 3 * 11 / 24, lineCap: .round, lineJoin: .round))
            .frame(width: 11, height: 11)
            .accessibilityHidden(true)
            .onAppear {
                if reduceMotion {
                    drawn = 1
                } else {
                    withAnimation(.easeOut(duration: 0.22).delay(0.08)) { drawn = 1 }
                }
            }
    }
}
