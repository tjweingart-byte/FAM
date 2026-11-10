import SafariServices
import SwiftUI

/// YourFAM's own icons, path for path from `yfIcon` and the screens'
/// inline SVGs (spec §4).
enum YouIcons {
    /// The header's gear (`yfIcon("gear")`): the web's path, its arcs
    /// turned into curves by `tools/ios_icons.py`'s converter.
    static let gear: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 9, y: 9, width: 6, height: 6))
        p.move(to: CGPoint(x: 19.4, y: 15))
        p.addCurve(to: CGPoint(x: 19.73, y: 16.82), control1: CGPoint(x: 19.128, y: 15.617), control2: CGPoint(x: 19.258, y: 16.338))
        p.addLine(to: CGPoint(x: 19.79, y: 16.88))
        p.addCurve(to: CGPoint(x: 19.79, y: 19.71), control1: CGPoint(x: 20.571, y: 17.661), control2: CGPoint(x: 20.571, y: 18.929))
        p.addCurve(to: CGPoint(x: 16.96, y: 19.71), control1: CGPoint(x: 19.009, y: 20.491), control2: CGPoint(x: 17.741, y: 20.491))
        p.addLine(to: CGPoint(x: 16.9, y: 19.65))
        p.addCurve(to: CGPoint(x: 15.08, y: 19.32), control1: CGPoint(x: 16.418, y: 19.178), control2: CGPoint(x: 15.697, y: 19.048))
        p.addCurve(to: CGPoint(x: 14.08, y: 20.83), control1: CGPoint(x: 14.476, y: 19.579), control2: CGPoint(x: 14.083, y: 20.172))
        p.addLine(to: CGPoint(x: 14.08, y: 21))
        p.addCurve(to: CGPoint(x: 12.08, y: 23), control1: CGPoint(x: 14.08, y: 22.105), control2: CGPoint(x: 13.185, y: 23))
        p.addCurve(to: CGPoint(x: 10.08, y: 21), control1: CGPoint(x: 10.975, y: 23), control2: CGPoint(x: 10.08, y: 22.105))
        p.addLine(to: CGPoint(x: 10.08, y: 20.91))
        p.addCurve(to: CGPoint(x: 9, y: 19.4), control1: CGPoint(x: 10.064, y: 20.233), control2: CGPoint(x: 9.636, y: 19.634))
        p.addCurve(to: CGPoint(x: 7.18, y: 19.73), control1: CGPoint(x: 8.383, y: 19.128), control2: CGPoint(x: 7.662, y: 19.258))
        p.addLine(to: CGPoint(x: 7.12, y: 19.79))
        p.addCurve(to: CGPoint(x: 4.29, y: 19.79), control1: CGPoint(x: 6.339, y: 20.571), control2: CGPoint(x: 5.071, y: 20.571))
        p.addCurve(to: CGPoint(x: 4.29, y: 16.96), control1: CGPoint(x: 3.509, y: 19.009), control2: CGPoint(x: 3.509, y: 17.741))
        p.addLine(to: CGPoint(x: 4.35, y: 16.9))
        p.addCurve(to: CGPoint(x: 4.68, y: 15.08), control1: CGPoint(x: 4.822, y: 16.418), control2: CGPoint(x: 4.952, y: 15.697))
        p.addCurve(to: CGPoint(x: 3.17, y: 14.08), control1: CGPoint(x: 4.421, y: 14.476), control2: CGPoint(x: 3.828, y: 14.083))
        p.addLine(to: CGPoint(x: 3, y: 14.08))
        p.addCurve(to: CGPoint(x: 1, y: 12.08), control1: CGPoint(x: 1.895, y: 14.08), control2: CGPoint(x: 1, y: 13.185))
        p.addCurve(to: CGPoint(x: 3, y: 10.08), control1: CGPoint(x: 1, y: 10.975), control2: CGPoint(x: 1.895, y: 10.08))
        p.addLine(to: CGPoint(x: 3.09, y: 10.08))
        p.addCurve(to: CGPoint(x: 4.6, y: 9), control1: CGPoint(x: 3.767, y: 10.064), control2: CGPoint(x: 4.366, y: 9.636))
        p.addCurve(to: CGPoint(x: 4.27, y: 7.18), control1: CGPoint(x: 4.872, y: 8.383), control2: CGPoint(x: 4.742, y: 7.662))
        p.addLine(to: CGPoint(x: 4.21, y: 7.12))
        p.addCurve(to: CGPoint(x: 4.21, y: 4.29), control1: CGPoint(x: 3.429, y: 6.339), control2: CGPoint(x: 3.429, y: 5.071))
        p.addCurve(to: CGPoint(x: 7.04, y: 4.29), control1: CGPoint(x: 4.991, y: 3.509), control2: CGPoint(x: 6.259, y: 3.509))
        p.addLine(to: CGPoint(x: 7.1, y: 4.35))
        p.addCurve(to: CGPoint(x: 8.92, y: 4.68), control1: CGPoint(x: 7.582, y: 4.822), control2: CGPoint(x: 8.303, y: 4.952))
        p.addLine(to: CGPoint(x: 9, y: 4.68))
        p.addCurve(to: CGPoint(x: 10, y: 3.17), control1: CGPoint(x: 9.604, y: 4.421), control2: CGPoint(x: 9.997, y: 3.828))
        p.addLine(to: CGPoint(x: 10, y: 3))
        p.addCurve(to: CGPoint(x: 12, y: 1), control1: CGPoint(x: 10, y: 1.895), control2: CGPoint(x: 10.895, y: 1))
        p.addCurve(to: CGPoint(x: 14, y: 3), control1: CGPoint(x: 13.105, y: 1), control2: CGPoint(x: 14, y: 1.895))
        p.addLine(to: CGPoint(x: 14, y: 3.09))
        p.addCurve(to: CGPoint(x: 15, y: 4.6), control1: CGPoint(x: 14.003, y: 3.748), control2: CGPoint(x: 14.396, y: 4.341))
        p.addCurve(to: CGPoint(x: 16.82, y: 4.27), control1: CGPoint(x: 15.617, y: 4.872), control2: CGPoint(x: 16.338, y: 4.742))
        p.addLine(to: CGPoint(x: 16.88, y: 4.21))
        p.addCurve(to: CGPoint(x: 19.71, y: 4.21), control1: CGPoint(x: 17.661, y: 3.429), control2: CGPoint(x: 18.929, y: 3.429))
        p.addCurve(to: CGPoint(x: 19.71, y: 7.04), control1: CGPoint(x: 20.491, y: 4.991), control2: CGPoint(x: 20.491, y: 6.259))
        p.addLine(to: CGPoint(x: 19.65, y: 7.1))
        p.addCurve(to: CGPoint(x: 19.32, y: 8.92), control1: CGPoint(x: 19.178, y: 7.582), control2: CGPoint(x: 19.048, y: 8.303))
        p.addLine(to: CGPoint(x: 19.32, y: 9))
        p.addCurve(to: CGPoint(x: 20.83, y: 10), control1: CGPoint(x: 19.579, y: 9.604), control2: CGPoint(x: 20.172, y: 9.997))
        p.addLine(to: CGPoint(x: 21, y: 10))
        p.addCurve(to: CGPoint(x: 23, y: 12), control1: CGPoint(x: 22.105, y: 10), control2: CGPoint(x: 23, y: 10.895))
        p.addCurve(to: CGPoint(x: 21, y: 14), control1: CGPoint(x: 23, y: 13.105), control2: CGPoint(x: 22.105, y: 14))
        p.addLine(to: CGPoint(x: 20.91, y: 14))
        p.addCurve(to: CGPoint(x: 19.4, y: 15), control1: CGPoint(x: 20.252, y: 14.003), control2: CGPoint(x: 19.659, y: 14.396))
        p.closeSubpath()
        return p
    }()

    /// Saved for Later's bookmark (`M6 3h12v18l-6-4-6 4z`).
    static let bookmark: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 6, y: 3)); p.addLine(to: CGPoint(x: 18, y: 3))
        p.addLine(to: CGPoint(x: 18, y: 21)); p.addLine(to: CGPoint(x: 12, y: 17))
        p.addLine(to: CGPoint(x: 6, y: 21)); p.closeSubpath()
        return p
    }()

    /// The picture's camera badge (`M3 8h3.5L8 5.5h8L17.5 8H21v11H3z`, a lens r 3.4).
    static let camera: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 3, y: 8)); p.addLine(to: CGPoint(x: 6.5, y: 8))
        p.addLine(to: CGPoint(x: 8, y: 5.5)); p.addLine(to: CGPoint(x: 16, y: 5.5))
        p.addLine(to: CGPoint(x: 17.5, y: 8)); p.addLine(to: CGPoint(x: 21, y: 8))
        p.addLine(to: CGPoint(x: 21, y: 19)); p.addLine(to: CGPoint(x: 3, y: 19)); p.closeSubpath()
        p.addEllipse(in: CGRect(x: 8.6, y: 9.6, width: 6.8, height: 6.8))
        return p
    }()

    /// A Settings row's chevron (`M9 5l7 7-7 7`).
    static let rowChevron: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 9, y: 5)); p.addLine(to: CGPoint(x: 16, y: 12)); p.addLine(to: CGPoint(x: 9, y: 19))
        return p
    }()

    /// The hold sheet's "remove": a circle with a bar.
    static let remove: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 3, y: 3, width: 18, height: 18))
        p.move(to: CGPoint(x: 8, y: 12)); p.addLine(to: CGPoint(x: 16, y: 12))
        return p
    }()

    /// The chosen interest's tick (`M5 12.5l4.5 4.5L19 7.5`).
    static let check: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 5, y: 12.5)); p.addLine(to: CGPoint(x: 9.5, y: 17)); p.addLine(to: CGPoint(x: 19, y: 7.5))
        return p
    }()

    /// The password field's eye.
    static let eye: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 2, y: 12))
        p.addQuadCurve(to: CGPoint(x: 22, y: 12), control: CGPoint(x: 12, y: 2))
        p.addQuadCurve(to: CGPoint(x: 2, y: 12), control: CGPoint(x: 12, y: 22))
        p.closeSubpath()
        p.addEllipse(in: CGRect(x: 9, y: 9, width: 6, height: 6))
        return p
    }()
}

// MARK: - The back row (`.back-row`)

/// The double chevron and the screen's name: Bricolage 19 medium, padding
/// 16 18 10 (Settings, Save for Later, the history, My Vibe).
struct YouBackRow: View {
    let title: String
    let back: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Button(action: back) {
                WebIcon(path: Icons.back, size: 20, line: 2.6)
                    .foregroundStyle(FAMColor.text)
                    .opacity(0.85)
                    .frame(width: 28, height: 28, alignment: .leading)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Back")
            Text(title)
                .font(FAMFont.heading(19, .medium))
                .foregroundStyle(FAMColor.text)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 18)
        .padding(.top, 16)
        .padding(.bottom, 10)
    }
}

// MARK: - Settings' rows (`.set-group`, `.set-row`, `.set-note`)

/// Geist Mono 9 bold, copper, uppercase; 22 above (6 for the first), 8 below.
struct SetGroup: View {
    let text: String
    var first = false

    var body: some View {
        Text(text)
            .monoLabel(9, color: FAMColor.copper, bold: true, tracking: 1.08)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, first ? 6 : 22)
            .padding(.bottom, 8)
            .accessibilityAddTraits(.isHeader)
    }
}

/// A row: 45 tall at the default size (a floor, `large-text`), padding 13
/// 14, radius 12, the surface with a 1 pt border; the label Geist 13
/// semibold, the value Geist Mono 9.5 muted, the 13 pt chevron when the row
/// does something. A row with no action ("Signed in as") is not a button.
struct SetRow: View {
    let label: String
    var value: String = ""
    var danger = false
    var badge = 0
    var action: (() -> Void)?

    var body: some View {
        if let action {
            Button(action: action) { content(chevron: true) }
                .buttonStyle(.plain)
                .accessibilityElement(children: .combine)
        } else {
            content(chevron: false)
                .accessibilityElement(children: .combine)
        }
    }

    private func content(chevron: Bool) -> some View {
        HStack(spacing: 10) {
            Text(label)
                .font(FAMFont.body(13, .semibold))
                .foregroundStyle(danger ? FAMColor.coral : FAMColor.text)
                .frame(maxWidth: .infinity, alignment: .leading)
            if badge > 0 {
                TabBadge(count: badge)
            }
            if !value.isEmpty {
                Text(value)
                    .font(FAMFont.mono(9.5))
                    .tracking(0.475)
                    .foregroundStyle(FAMColor.textMuted)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(maxWidth: 160, alignment: .trailing)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if chevron {
                WebIcon(path: YouIcons.rowChevron, size: 13, line: 2.2)
                    .foregroundStyle(FAMColor.textFaint)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 13)
        .frame(minHeight: 45)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(FAMColor.border))
        .contentShape(RoundedRectangle(cornerRadius: 12))
        .padding(.bottom, 8)
    }
}

/// Geist 11 / 16.5 faint, padding 2 2 4, straight under its row.
struct SetNote: View {
    let text: String

    var body: some View {
        Text(text)
            .font(FAMFont.body(11))
            .foregroundStyle(FAMColor.textFaint)
            .lineSpacing(5.5)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 2)
            .padding(.top, -6)
            .padding(.bottom, 10)
    }
}

// MARK: - The entry screens' pieces (`.intro-top`, `.entry-dock`, `.auth-label`)

/// The round X of an editor (`.sheet-close`): 30 pt, a strong border, a 15 pt X.
struct CloseCircle: View {
    var label = "Close"
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            WebIcon(path: Icons.close, size: 15, line: 2.4)
                .foregroundStyle(FAMColor.text)
                .frame(width: 30, height: 30)
                .overlay(Circle().stroke(FAMColor.borderStrong))
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }
}

/// The docked primary (`.entry-btn.primary`): 46 tall, radius 23, copper,
/// Geist 13.5 bold ink.
struct EntryButton: View {
    let label: String
    var working = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack {
                if working {
                    ProgressView().tint(FAMColor.ink)
                } else {
                    Text(label)
                        .font(FAMFont.body(13.5, .bold))
                        .foregroundStyle(FAMColor.ink)
                }
            }
            .frame(maxWidth: .infinity, minHeight: 46)
            .background(FAMColor.copper, in: Capsule())
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .disabled(working)
    }
}

/// The dock under an entry screen: padding 12 22 14 on the page's white.
struct EntryDock<Content: View>: View {
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(spacing: 0) { content() }
            .padding(.horizontal, 22)
            .padding(.top, 12)
            .padding(.bottom, 14)
            .background(
                LinearGradient(stops: [.init(color: FAMColor.background.opacity(0), location: 0),
                                       .init(color: FAMColor.background, location: 0.38)],
                               startPoint: .top, endPoint: .bottom)
            )
    }
}

/// "YOUR NAME", "USERNAME" (`.auth-label`), with the first run's copper
/// "required" (`.auth-req`).
struct FieldLabel: View {
    let text: String
    var required = false

    var body: some View {
        HStack(spacing: 4) {
            Text(text).monoLabel(9, color: FAMColor.textFaint, tracking: 0.72)
            if required {
                Text("required")
                    .font(FAMFont.mono(9))
                    .foregroundStyle(FAMColor.copper)
            }
        }
        .padding(.bottom, 6)
    }
}

extension View {
    /// `.modal-input`: 41 tall (a floor), padding 11 12, radius 10, the
    /// strong border (coral when a required field is missing), white, Geist 13.
    func youInput(missing: Bool = false) -> some View {
        self.font(FAMFont.body(13))
            .foregroundStyle(FAMColor.text)
            .padding(.horizontal, 12)
            .frame(minHeight: 41)
            .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10)
                .stroke(missing ? FAMColor.coral : FAMColor.borderStrong))
    }
}

/// The sign-up steps (`firstRunStep`): three marks, copper up to this one.
struct StepMarks: View {
    let step: Int

    var body: some View {
        HStack(spacing: 5) {
            ForEach(1...3, id: \.self) { i in
                Capsule()
                    .fill(i <= step ? FAMColor.copper : Color(hex: 0xD6D2CB))
                    .frame(width: 18, height: 3)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 12)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Step \(step) of 3")
    }
}

// MARK: - Sheets with a head, groups and ticks

/// One row of a YourFAM sheet: a group line, an item, or a danger item,
/// with an optional icon (`menuLabel`).
struct YouSheetItem: Identifiable {
    let id = UUID()
    let label: String
    var sub: String = ""
    var group = false
    var danger = false
    var icon: Path?
    /// The current choice, marked with a check just after the label
    /// (searchFAM's Length and Voice sheets; the web writes "  ✓").
    var checked = false
    var run: () -> Void = {}
}

/// `showActionSheet` with everything YourFAM's sheets use: an optional
/// head (picture, title, sub), group lines anywhere, items with a muted
/// second line or an icon, danger rows coral, the white Cancel. A row
/// closes the sheet, then runs once it has gone.
struct YouSheet: View {
    var headTitle = ""
    var headSub = ""
    var headThumb = ""
    let items: [YouSheetItem]
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !headTitle.isEmpty { head }
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(items) { item in
                        if item.group {
                            SheetGroupLabel(text: item.label)
                        } else {
                            row(item)
                        }
                    }
                }
            }
            .scrollBounceBehavior(.basedOnSize)
            SheetCancelButton { dismiss() }
        }
        .padding(.top, 10)
        .padding(.bottom, 12)
        .background(FAMColor.surface)
        .presentationDetents([.height(min(height, 640))])
        .presentationBackground(FAMColor.surface)
        .presentationCornerRadius(20)
    }

    private var height: CGFloat {
        var h: CGFloat = 10 + 41 + 8 + 22 + 12
        if !headTitle.isEmpty { h += 66 }
        for item in items {
            if item.group {
                h += item.label.count > 40 ? 52 : 33
            } else {
                h += item.sub.isEmpty ? 46 : 58
            }
        }
        return h
    }

    /// `menuHead`: a 44 pt picture (radius 8), the title Geist 14 semibold,
    /// the sub 11.5 muted, a line under it.
    private var head: some View {
        HStack(spacing: 12) {
            Picture(url: model.api.picture(headThumb))
                .frame(width: 44, height: 44)
                .clipShape(RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 2) {
                Text(headTitle)
                    .font(FAMFont.body(14, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(2)
                if !headSub.isEmpty {
                    Text(headSub)
                        .font(FAMFont.body(11.5))
                        .foregroundStyle(FAMColor.textMuted)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 22)
        .padding(.top, 4)
        .padding(.bottom, 12)
        .overlay(alignment: .bottom) { Rectangle().fill(FAMColor.border).frame(height: 1) }
    }

    private func row(_ item: YouSheetItem) -> some View {
        Button {
            dismiss()
            let run = item.run
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 450_000_000)
                run()
            }
        } label: {
            HStack(spacing: 14) {
                if let icon = item.icon {
                    WebIcon(path: icon, size: 18)
                        .foregroundStyle(item.danger ? FAMColor.coral : FAMColor.textMuted)
                }
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 5) {
                        Text(item.label)
                            .font(FAMFont.body(13.5, .semibold))
                            .foregroundStyle(item.danger ? FAMColor.coral : FAMColor.text)
                            .lineLimit(1)
                        if item.checked {
                            WebIcon(path: Icons.check, size: 13, line: 2.6)
                                .foregroundStyle(FAMColor.text)
                        }
                    }
                    if !item.sub.isEmpty {
                        Text(item.sub)
                            .font(FAMFont.body(11.5))
                            .foregroundStyle(FAMColor.textMuted)
                            .lineLimit(1)
                    }
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, minHeight: 46, alignment: .leading)
            .padding(.horizontal, 22)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(item.checked ? .isSelected : [])
    }
}

// MARK: - A text modal (`showModal`: New folder, Rename, passwords)

/// The centred card with one field: the title Bricolage 16, the X, the
/// field, Cancel and the copper primary; Return confirms; an empty value
/// does nothing. Shown in a cover with the dim behind it.
struct TextModal: View {
    let title: String
    let placeholder: String
    let button: String
    var initial = ""
    var secure = false
    var note = ""
    let done: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        ModalCard(close: { dismiss() }) {
            HStack(alignment: .top) {
                Text(title)
                    .font(FAMFont.heading(16))
                    .foregroundStyle(FAMColor.text)
                Spacer()
                Button { dismiss() } label: {
                    WebIcon(path: Icons.close, size: 14, line: 2.4)
                        .foregroundStyle(FAMColor.textMuted)
                        .frame(width: 28, height: 28)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Close")
            }
            .padding(.bottom, 12)
            field
            if !note.isEmpty {
                Text(note)
                    .font(FAMFont.body(11))
                    .foregroundStyle(FAMColor.textFaint)
                    .padding(.top, 8)
            }
            HStack(spacing: 8) {
                Spacer()
                ModalButton(label: "Cancel") { dismiss() }
                ModalButton(label: button, primary: true) { confirm() }
            }
            .padding(.top, 16)
        }
        .onAppear {
            text = initial
            focused = true
        }
    }

    @ViewBuilder private var field: some View {
        if secure {
            SecureField("", text: $text, prompt: Text(placeholder).foregroundColor(FAMColor.textFaint))
                .textContentType(.password)
                .focused($focused)
                .submitLabel(.done)
                .onSubmit { confirm() }
                .youInput()
        } else {
            TextField("", text: $text, prompt: Text(placeholder).foregroundColor(FAMColor.textFaint))
                .focused($focused)
                .submitLabel(.done)
                .onSubmit { confirm() }
                .youInput()
        }
    }

    private func confirm() {
        let value = secure ? text : text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return }
        dismiss()
        let done = self.done
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 350_000_000)
            done(value)
        }
    }
}

/// What a text modal is for, so one cover can show whichever is asked.
struct TextAsk: Identifiable {
    let id = UUID()
    let title: String
    let placeholder: String
    let button: String
    var initial = ""
    var secure = false
    var note = ""
    let done: (String) -> Void
}

extension View {
    /// Show a `TextAsk` as the web's centred modal over a dim.
    func textModal(_ ask: Binding<TextAsk?>) -> some View {
        self.fullScreenCover(item: ask) { a in
            TextModal(title: a.title, placeholder: a.placeholder, button: a.button,
                      initial: a.initial, secure: a.secure, note: a.note, done: a.done)
        }
    }
}

// MARK: - Terms, Privacy, Help (`window.open` on the web; Safari here, Q17)

struct SafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url)
    }

    func updateUIViewController(_ vc: SFSafariViewController, context: Context) {}
}

/// A page to show in Safari's sheet.
struct LegalPage: Identifiable {
    let id = UUID()
    let url: URL
}
