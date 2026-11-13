import SwiftUI
import UIKit

// MARK: - Staging says so (§172, §584)

/// `.env-ribbon`: a staging or zero-spend server's name and this client,
/// in copper at the top centre of every screen - "a page that looks exactly
/// like the app people use is the one someone tests on by mistake". Mono 10,
/// bold, upper case, 0.1 em apart, ink on copper, its lower corners rounded
/// 6, padded 2 by 10; it takes no touches.
struct EnvRibbon: View {
    let words: String

    var body: some View {
        Text(words)
            .font(FAMFont.mono(10, bold: true))
            .tracking(1)
            .textCase(.uppercase)
            .lineLimit(1)
            .fixedSize()
            .foregroundStyle(FAMColor.ink)
            .padding(.vertical, 2)
            .padding(.horizontal, 10)
            .background(FAMColor.copper,
                        in: UnevenRoundedRectangle(bottomLeadingRadius: 6, bottomTrailingRadius: 6))
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .allowsHitTesting(false)
            .accessibilityLabel(words)
    }
}

/// `.demo-badge`: "not your episode" on the player while the server has no
/// working key - a built-in sample script is standing in. Mono 11, bold,
/// upper case, 0.08 em apart, ink on copper, corners 6, padded 4 by 9.
struct DemoBadge: View {
    var body: some View {
        Text("not your episode")
            .font(FAMFont.mono(11, bold: true))
            .tracking(11 * 0.08)
            .textCase(.uppercase)
            .lineLimit(1)
            .fixedSize()
            .foregroundStyle(FAMColor.ink)
            .padding(.vertical, 4)
            .padding(.horizontal, 9)
            .background(FAMColor.copper, in: RoundedRectangle(cornerRadius: 6, style: .continuous))
            .accessibilityHint("No API key: a built-in sample script is standing in for Claude")
    }
}

/// The ribbon is on every screen, the full-screen covers included, as the
/// web's `position:fixed` one is over the whole page: so it is a window of
/// its own above the app's, which never takes a touch and is never key.
@MainActor
enum EnvRibbonWindow {
    private static var window: UIWindow?
    private static var words = ""
    /// The app window's scheme - the theme, or dark over private search or
    /// the shared screen - which the status bar under this window takes.
    private static var dark = true

    static func show(_ words: String, dark: Bool) {
        self.words = words
        self.dark = dark
        window?.isHidden = true
        window = nil
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        guard !words.isEmpty,
              let scene = scenes.first(where: { $0.activationState == .foregroundActive }) ?? scenes.first
        else { return }
        let host = RibbonHost(rootView: AnyView(EnvRibbon(words: words).famThemed()))
        host.dark = dark
        host.view.backgroundColor = .clear
        let shown = TouchlessWindow(windowScene: scene)
        shown.windowLevel = UIWindow.Level(rawValue: UIWindow.Level.alert.rawValue + 1)
        shown.backgroundColor = .clear
        shown.overrideUserInterfaceStyle = dark ? .dark : .light
        shown.isUserInteractionEnabled = false
        shown.rootViewController = host
        shown.isHidden = false
        window = shown
    }

    /// The theme, private search's charcoal or the shared screen changed the
    /// app's scheme: a window this high decides the status bar, so it is
    /// told at once rather than keeping the one it was drawn in.
    static func restyle(dark: Bool) {
        self.dark = dark
        guard let window else { return }
        window.overrideUserInterfaceStyle = dark ? .dark : .light
        if let host = window.rootViewController as? RibbonHost {
            host.dark = dark
            host.setNeedsStatusBarAppearanceUpdate()
        }
    }

    /// The scene the ribbon was put on went away (disconnected, then a new
    /// one connected): it goes on the live scene again.
    static func reattach() {
        guard !words.isEmpty else { return }
        let live = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first { $0.activationState == .foregroundActive }
        guard let live, window?.windowScene !== live || window?.isHidden != false else { return }
        show(words, dark: dark)
    }
}

/// The ribbon's controller: the status bar it decides is the app's.
private final class RibbonHost: UIHostingController<AnyView> {
    var dark = true
    override var preferredStatusBarStyle: UIStatusBarStyle { dark ? .lightContent : .darkContent }
}

/// A window every touch goes through.
private final class TouchlessWindow: UIWindow {
    override func hitTest(_ point: CGPoint, with event: UIEvent?) -> UIView? { nil }
}
