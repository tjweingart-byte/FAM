import UIKit

/// A tap anywhere that is not a text field puts the keyboard away (the
/// owner's 11.17 notes, PROBLEMS.md §648: "If the keyboard is popped up and
/// I click somewhere random on the screen, I want the keyboard to
/// disappear"). The web app does the same (`static/index.html`).
///
/// One tap recogniser on each window, which never cancels or delays the
/// touch, so whatever was tapped still gets it. A tap on a field keeps the
/// keyboard, and so does a tap in the strip just above the keyboard, where
/// a composer's Send button sits: sending a message never closes it. And a
/// tap whose own action moves the caret to another field (a "next" button,
/// a chip that opens search) keeps it: the keyboard goes only if no field
/// began editing in the moment after the tap.
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
    /// When a field last began editing, so a tap that moved focus is left
    /// alone (`tapped`).
    private var lastBegan: Date = .distantPast

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
            centre.addObserver(self, selector: #selector(fieldBegan(_:)),
                               name: UITextField.textDidBeginEditingNotification, object: nil)
            centre.addObserver(self, selector: #selector(fieldBegan(_:)),
                               name: UITextView.textDidBeginEditingNotification, object: nil)
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

    @objc private func fieldBegan(_ note: Notification) {
        lastBegan = Date()
    }

    @objc private func tapped(_ tap: UITapGestureRecognizer) {
        guard keyboardTop != nil, tap.state == .ended else { return }
        let at = Date()
        let view = tap.view
        // A beat later, after the tapped control's own action has run: if
        // it put the caret in a field, that was the point of the tap. The
        // window is held weakly by the capture list (a `weak var` never
        // changed drew Xcode's "consider let" warning; `weak let` needs
        // Swift 6.2, newer than CI's Xcode 16.4).
        Task { @MainActor [weak self, weak view] in
            try? await Task.sleep(nanoseconds: 120_000_000)
            guard let self, self.lastBegan < at else { return }
            view?.endEditing(true)
        }
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
        // The keyboard's frame is in screen coordinates; so is this point,
        // which matters when the window is not the whole screen (iPad).
        if let window = touch.window {
            let point = window.convert(touch.location(in: window),
                                       to: window.screen.coordinateSpace)
            if point.y >= top - Self.composerStrip { return false }
        }
        return true
    }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                           shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool {
        true
    }
}
