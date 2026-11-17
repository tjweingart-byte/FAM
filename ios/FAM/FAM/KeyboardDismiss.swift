import UIKit

/// A tap anywhere that is not a text field puts the keyboard away (the
/// owner's 11.17 notes, PROBLEMS.md §648: "If the keyboard is popped up and
/// I click somewhere random on the screen, I want the keyboard to
/// disappear"). The web app does the same (`static/index.html`).
///
/// One tap recogniser on each window, which never cancels or delays the
/// touch, so whatever was tapped still gets it. A tap on a field keeps the
/// keyboard, and so does a tap in the strip just above the keyboard, where
/// a composer's Send button sits: sending a message never closes it.
/// Scrolling already closes it (`.scrollDismissesKeyboard`).
@MainActor
final class KeyboardDismisser: NSObject, UIGestureRecognizerDelegate {
    static let shared = KeyboardDismisser()

    private static let name = "fam.keyboard-dismiss"
    /// The strip above the keyboard's top edge whose taps are left alone.
    private static let composerStrip: CGFloat = 72

    /// The keyboard's top edge in screen points, or nil while it is down.
    private var keyboardTop: CGFloat?
    private var observing = false

    /// Called on every appearance of the root view: attaches to any window
    /// that does not have the recogniser yet. Cheap and idempotent.
    func install() {
        if !observing {
            observing = true
            let centre = NotificationCenter.default
            centre.addObserver(self, selector: #selector(keyboardMoved(_:)),
                               name: UIResponder.keyboardWillChangeFrameNotification, object: nil)
            centre.addObserver(self, selector: #selector(keyboardHidden(_:)),
                               name: UIResponder.keyboardWillHideNotification, object: nil)
        }
        for scene in UIApplication.shared.connectedScenes {
            guard let windows = (scene as? UIWindowScene)?.windows else { continue }
            for window in windows { attach(to: window) }
        }
    }

    private func attach(to window: UIWindow) {
        if window.gestureRecognizers?.contains(where: { $0.name == Self.name }) == true { return }
        let tap = UITapGestureRecognizer(target: self, action: #selector(tapped(_:)))
        tap.name = Self.name
        tap.cancelsTouchesInView = false
        tap.delaysTouchesBegan = false
        tap.delaysTouchesEnded = false
        tap.delegate = self
        window.addGestureRecognizer(tap)
    }

    @objc private func keyboardMoved(_ note: Notification) {
        guard let end = note.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? CGRect else { return }
        keyboardTop = end.minY
    }

    @objc private func keyboardHidden(_ note: Notification) {
        keyboardTop = nil
    }

    @objc private func tapped(_ tap: UITapGestureRecognizer) {
        guard keyboardTop != nil, tap.state == .ended else { return }
        tap.view?.endEditing(true)
    }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                           shouldReceive touch: UITouch) -> Bool {
        guard let top = keyboardTop else { return false }
        // A tap on (or inside) a field keeps the keyboard: it is moving the
        // caret or choosing another field.
        var view = touch.view
        while let v = view {
            if v is UITextField || v is UITextView || v is UISearchBar { return false }
            view = v.superview
        }
        // The composer's row, just above the keyboard.
        if let window = touch.window {
            let y = touch.location(in: window).y
            if y >= top - Self.composerStrip { return false }
        }
        return true
    }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                           shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool {
        true
    }
}
