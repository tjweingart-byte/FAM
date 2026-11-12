import SwiftUI
import UIKit

/// Sign up and log in, after the web's (`screen-auth`): a back row to the
/// welcome screen with the heading, the sub line, Apple and Google (iOS
/// only), mono labels over rounded fields, what went wrong above the
/// button, and the line that switches between the two.
///
/// **Sign up is the web's waitlist join** (decision 1, `signupGoesToWaitlist`):
/// every web sign-up goes to `/waitlist`, whose form asks for the email,
/// then a password (§571, reversing §296's email alone), then the terms
/// box (§228), and carries the invite code a link brought (`referral_code`).
/// The account is put on the list, then the profile is offered with a way
/// past it, then the status page. Log in is
/// the app's own form (`submitAuthForm`): its sentences are said before any
/// request, and the button is never greyed out.
struct AuthView: View {
    @EnvironmentObject private var model: AppModel

    /// Sign up (the join) when true, Log in when false.
    @State private var making: Bool
    /// Back to the welcome screen (`backToWelcome`).
    let back: () -> Void

    @State private var email = ""
    @State private var password = ""
    @State private var showPassword = false
    @State private var agreed = false
    @State private var working = false
    @State private var problem = ""
    /// A plain line under the log-in form ("That email is already on FAM…").
    @State private var note = ""
    /// "Forgot password?" is drawn only when this server can send the
    /// code (`/api/auth/reset`, §244), and only on Log in.
    @State private var resetAvailable = false
    @State private var resetting = false
    @FocusState private var field: Field?

    enum Field { case email, password }

    init(making: Bool, back: @escaping () -> Void) {
        _making = State(initialValue: making)
        self.back = back
    }

    var body: some View {
        Group {
            if resetting {
                ResetPasswordView(initialEmail: email) { resetting = false }
            } else {
                form
            }
        }
        .task {
            if let status = try? await model.api.resetStatus() { resetAvailable = status.available }
        }
        .task(id: making) {
            // The address is focused as the form opens (`showAuthForm`).
            try? await Task.sleep(nanoseconds: 350_000_000)
            field = .email
        }
    }

    private var form: some View {
        VStack(spacing: 0) {
            YouBackRow(title: making ? "Sign up" : "Log in") { back() }
            if making { StepMarks(step: 1).padding(.top, -6) }
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    lead
                    ProviderButtons(making: making, agreed: agreed, problem: $problem, working: $working)
                        .padding(.top, 20)
                    orRule.padding(.top, 20)
                    if making { joinFields } else { loginFields }
                    switchLine
                }
                .padding(.horizontal, 22)
                .padding(.top, 6)
                .padding(.bottom, 30)
            }
            .scrollDismissesKeyboard(.interactively)
        }
        .background(FAMColor.background.ignoresSafeArea())
    }

    // MARK: The top of the form

    @ViewBuilder
    private var lead: some View {
        if making {
            // The waitlist page's own words (`#landing`).
            Text("The social information network")
                .monoLabel(9)
            Text("Listen to everything you want to know about.")
                .font(FAMFont.heading(20, .semibold))
                .foregroundStyle(FAMColor.text)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 8)
            if !model.pendingReferral.isEmpty {
                Text("A friend saved you a spot.")
                    .font(FAMFont.body(13, .semibold))
                    .foregroundStyle(FAMColor.copper)
                    .padding(.top, 10)
            }
            SampleCarousel()
                .padding(.top, 14)
        } else {
            Text("Welcome back.")
                .font(FAMFont.body(13))
                .foregroundStyle(FAMColor.textMuted)
        }
    }

    private var orRule: some View {
        HStack(spacing: 10) {
            Rectangle().fill(FAMColor.border).frame(height: 1)
            Text("or").monoLabel(9)
            Rectangle().fill(FAMColor.border).frame(height: 1)
        }
    }

    // MARK: Sign up: the waitlist join (email, password, terms; §571)

    private var joinFields: some View {
        VStack(alignment: .leading, spacing: 0) {
            label("Email").padding(.top, 18)
            emailField(prompt: "Enter your email")
            // A password in the same step (§571): the way back in from any
            // device, with no email needed (`#joinPassword`).
            label("Password").padding(.top, 18)
            passwordField
            if !problem.isEmpty { problemLine }
            Button {
                Task { await join() }
            } label: {
                Text("Join the waitlist")
            }
            .buttonStyle(EntryButtonStyle(primary: true))
            .disabled(working)
            .padding(.top, problem.isEmpty ? 22 : 10)
            terms.padding(.top, 16)
            Button("What is FAM?") { openAbout() }
                .font(FAMFont.body(13, .bold))
                .foregroundStyle(FAMColor.copper)
                .buttonStyle(.press)
                .frame(maxWidth: .infinity)
                .padding(.top, 18)
        }
    }

    // MARK: Log in (`submitAuthForm`)

    private var loginFields: some View {
        VStack(alignment: .leading, spacing: 0) {
            label("Email").padding(.top, 18)
            emailField(prompt: "you@example.com")
            label("Password").padding(.top, 18)
            passwordField
            // What went wrong sits above the button (`#authError`).
            if !problem.isEmpty { problemLine }
            Button {
                Task { await logIn() }
            } label: {
                Text(working ? "Logging in\u{2026}" : "Log in")
            }
            .buttonStyle(EntryButtonStyle(primary: true))
            .disabled(working)
            .padding(.top, problem.isEmpty ? 22 : 10)
            if !note.isEmpty {
                Text(note)
                    .font(FAMFont.body(12))
                    .foregroundStyle(FAMColor.textMuted)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 12)
            }
            if resetAvailable {
                Button("Forgot password?") {
                    problem = ""
                    resetting = true
                }
                .font(FAMFont.body(13, .bold))
                .foregroundStyle(FAMColor.copper)
                .buttonStyle(.press)
                .frame(maxWidth: .infinity)
                .padding(.top, 18)
            }
        }
    }

    private func emailField(prompt: String) -> some View {
        TextField("", text: $email,
                  prompt: Text(prompt).foregroundColor(FAMColor.textFaint))
            .keyboardType(.emailAddress)
            // Remembered only on Log in: signing up is a new identity, so
            // nothing about the last one is offered (`autocomplete`).
            .textContentType(making ? nil : UITextContentType.username)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .focused($field, equals: .email)
            .submitLabel(.next)
            .onSubmit { field = .password }
            .modifier(FieldBox())
    }

    private var passwordField: some View {
        HStack {
            Group {
                if showPassword {
                    TextField("", text: $password,
                              prompt: Text(passwordPrompt).foregroundColor(FAMColor.textFaint))
                } else {
                    SecureField("", text: $password,
                                prompt: Text(passwordPrompt).foregroundColor(FAMColor.textFaint))
                }
            }
            // A new password on Sign up, so iOS offers a strong one.
            .textContentType(making ? .newPassword : .password)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .focused($field, equals: .password)
            .submitLabel(.go)
            .onSubmit {
                if making { Task { await join() } } else { Task { await logIn() } }
            }
            Button { showPassword.toggle() } label: {
                Image(systemName: showPassword ? "eye.slash" : "eye")
                    .foregroundStyle(FAMColor.textMuted)
            }
            .accessibilityLabel(showPassword ? "Hide password" : "Show password")
            .accessibilityAddTraits(showPassword ? .isSelected : [])
        }
        .modifier(FieldBox())
    }

    private var passwordPrompt: String {
        making ? "Choose a password (at least 8 characters)" : "Your password"
    }

    private var problemLine: some View {
        Text(problem)
            .font(FAMFont.body(12.5))
            .foregroundStyle(FAMColor.coral)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, 12)
    }

    private var switchLine: some View {
        HStack(spacing: 6) {
            Text(making ? "Already have an account?" : "New to FAM?")
                .foregroundStyle(FAMColor.textMuted)
            Button(making ? "Log in" : "Sign up") { switchMode() }
                .fontWeight(.bold)
                .foregroundStyle(FAMColor.copper)
        }
        .font(FAMFont.body(13))
        .frame(maxWidth: .infinity)
        .padding(.top, 18)
    }

    /// Between the two without losing what was typed (`switchAuthMode`);
    /// the box is unticked and the password hidden again, as `openAuth` does.
    private func switchMode() {
        making.toggle()
        problem = ""
        note = ""
        agreed = false
        showPassword = false
    }

    /// The terms box: unticked, the listener ticks it (clickwrap, §228).
    private var terms: some View {
        Button { agreed.toggle() } label: {
            HStack(alignment: .top, spacing: 12) {
                RoundedRectangle(cornerRadius: 4)
                    .stroke(agreed ? FAMColor.copper : FAMColor.borderStrong, lineWidth: 1.5)
                    .background(agreed ? FAMColor.copper : Color.clear, in: RoundedRectangle(cornerRadius: 4))
                    .overlay {
                        if agreed {
                            Image(systemName: "checkmark")
                                .font(.system(size: 11, weight: .bold))
                                .foregroundStyle(FAMColor.ink)
                        }
                    }
                    .frame(width: 18, height: 18)
                TermsSentence()
            }
        }
        .buttonStyle(.press)
        .accessibilityAddTraits(agreed ? .isSelected : [])
    }

    private func label(_ text: String) -> some View {
        Text(text).monoLabel(9).padding(.bottom, 7)
    }

    private var address: String { email.trimmingCharacters(in: .whitespacesAndNewlines) }

    /// The web's address test (`/^[^\s@]+@[^\s@]+\.[^\s@]+$/`).
    static func looksLikeEmail(_ text: String) -> Bool {
        let parts = text.split(separator: "@", omittingEmptySubsequences: false)
        guard parts.count == 2, !parts[0].isEmpty, !text.contains(" ") else { return false }
        let host = parts[1].split(separator: ".", omittingEmptySubsequences: false)
        return host.count >= 2 && host.allSatisfy { !$0.isEmpty }
    }

    /// The join (`#joinForm`): checked here, said under the field.
    private func join() async {
        problem = ""
        guard !working else { return }
        guard Self.looksLikeEmail(address) else {
            problem = "That doesn\u{2019}t look like an email address."
            return
        }
        // The app's own rule and sentences (`submitAuthForm`, §571).
        guard !password.isEmpty else {
            problem = "Enter your password."
            return
        }
        guard password.count >= 8 else {
            problem = "Use at least 8 characters for your password."
            return
        }
        guard password.count <= 1024 else {
            problem = "That password is too long."
            return
        }
        guard agreed else {
            problem = "Tick the box to agree to the Terms and conditions."
            return
        }
        working = true
        defer { working = false }
        do {
            try await model.joinWaitlist(email: address, password: password)
            password = ""
        } catch {
            let words = error.localizedDescription
            problem = words
            // Already on FAM (joined before, on another device): the
            // sign-in, with the address and the password carried over,
            // is the way back.
            if words.contains("already registered") {
                switchMode()
                note = resetAvailable
                    ? "That email is already on FAM. Sign in, or use Forgot password? if you never set one."
                    : "That email is already on FAM. Sign in with its password, or on the device you joined from."
            }
        }
    }

    /// Log in (`submitAuthForm`): the sentences first, then the request.
    private func logIn() async {
        guard !working else { return }
        if address.isEmpty {
            problem = "Enter your email address."
            return
        }
        if !Self.looksLikeEmail(address) {
            problem = "That email address doesn\u{2019}t look right."
            return
        }
        if password.isEmpty {
            problem = "Enter your password."
            return
        }
        problem = ""
        working = true
        defer { working = false }
        do {
            try await model.signIn(email: address, password: password)
            password = ""
        } catch {
            problem = error.localizedDescription
        }
    }

    /// "What is FAM?": the waitlist page's explainer, in the browser.
    private func openAbout() {
        let base = model.api.base.absoluteString
        if let url = URL(string: base + "/waitlist#about") { UIApplication.shared.open(url) }
    }
}

/// "I agree to FAM's terms and I'm 13 or older…", its links on the server
/// this build talks to (`/terms`, `/privacy`, relative on the web).
struct TermsSentence: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Text(sentence)
            .font(FAMFont.body(12.5))
            .foregroundStyle(FAMColor.textMuted)
            .lineSpacing(3)
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var sentence: AttributedString {
        let base = model.api.base.absoluteString
        let markdown = "I agree to FAM\u{2019}s terms and I\u{2019}m 13 or older. Review the [terms and conditions](\(base)/terms) and the [privacy policy](\(base)/privacy)."
        return (try? AttributedString(markdown: markdown)) ?? AttributedString(markdown)
    }
}

/// A rounded field (`.auth input`, `.modal-input`): Geist 13, 12 across,
/// 41 tall at least (a floor, so larger text grows it, `large-text`).
struct FieldBox: ViewModifier {
    func body(content: Content) -> some View {
        content
            .font(FAMFont.body(13))
            .foregroundStyle(FAMColor.text)
            .padding(.horizontal, 12)
            .frame(minHeight: 41)
            .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(FAMColor.borderStrong))
    }
}

/// An account waiting for its place: the web's whole status page
/// (`/waitlist/me`, decision 2), in `WaitlistStatusPage`.
struct WaitingView: View {
    var body: some View {
        WaitlistStatusPage()
    }
}

/// "Reset password" (`[op-identity]`, §244, shots 91-93), on the log-in
/// screen: the email first, then the emailed 6-digit code and a new
/// password. Every answer is the server's own sentence; a bad code says so
/// under the button. Done, the phone is signed in, as a log-in is.
struct ResetPasswordView: View {
    @EnvironmentObject private var model: AppModel
    let initialEmail: String
    let back: () -> Void

    @State private var email = ""
    @State private var code = ""
    @State private var password = ""
    @State private var showPassword = false
    @State private var sent = false
    @State private var note = ResetPasswordView.firstNote
    @State private var problem = ""
    @State private var working = false
    @FocusState private var focus: Field?

    enum Field { case email, code }

    static let firstNote = "We\u{2019}ll email a code to the address on your account. Signed up with only a phone number? Reset needs an email address on the account."

    var body: some View {
        VStack(spacing: 0) {
            YouBackRow(title: "Reset password") { back() }
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Text("Enter the email on your account.")
                        .font(FAMFont.body(13))
                        .foregroundStyle(FAMColor.textMuted)
                        .padding(.bottom, 18)
                    label("Email")
                    TextField("", text: $email,
                              prompt: Text("you@example.com").foregroundColor(FAMColor.textFaint))
                        .keyboardType(.emailAddress)
                        .textContentType(.username)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .focused($focus, equals: .email)
                        .modifier(FieldBox())
                    if sent { codeFields }
                    // Under the fields, over the button, as the web's
                    // `#resetError` (shot 93).
                    if !problem.isEmpty {
                        Text(problem)
                            .font(FAMFont.body(12.5))
                            .foregroundStyle(FAMColor.coral)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 12)
                    }
                    Button {
                        Task { await go() }
                    } label: {
                        Text(buttonLabel)
                    }
                    .buttonStyle(CopperButtonStyle())
                    .disabled(working)
                    .padding(.top, problem.isEmpty ? 18 : 12)
                    Text(note)
                        .font(FAMFont.body(12))
                        .foregroundStyle(FAMColor.textFaint)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 18)
                    if sent {
                        Button("Send a new code") { Task { await send() } }
                            .font(FAMFont.body(13, .bold))
                            .foregroundStyle(FAMColor.copper)
                            .buttonStyle(.press)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 22)
                    }
                    HStack(spacing: 6) {
                        Text("Remembered it?").foregroundStyle(FAMColor.textMuted)
                        Button("Log in") { back() }
                            .fontWeight(.bold)
                            .foregroundStyle(FAMColor.copper)
                    }
                    .font(FAMFont.body(13))
                    .frame(maxWidth: .infinity)
                    .padding(.top, 24)
                }
                .padding(.horizontal, 22)
                .padding(.bottom, 30)
            }
            .scrollDismissesKeyboard(.interactively)
        }
        .background(FAMColor.background.ignoresSafeArea())
        .onAppear {
            if email.isEmpty { email = initialEmail }
            // The address is focused as the form opens (`openReset`).
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { focus = .email }
        }
    }

    /// "Email me a code", then "Set new password" - "Saving…" while the
    /// new one is saved (`submitReset`).
    private var buttonLabel: String {
        if !sent { return "Email me a code" }
        return working ? "Saving\u{2026}" : "Set new password"
    }

    private var codeFields: some View {
        VStack(alignment: .leading, spacing: 0) {
            label("6-digit code").padding(.top, 18)
            TextField("", text: $code, prompt: Text("123456").foregroundColor(FAMColor.textFaint))
                .keyboardType(.numberPad)
                .textContentType(.oneTimeCode)
                .focused($focus, equals: .code)
                // Six characters at most (`maxlength="6"`).
                .onChange(of: code) { _, typed in
                    if typed.count > 6 { code = String(typed.prefix(6)) }
                }
                .modifier(FieldBox())
            label("New password").padding(.top, 18)
            HStack {
                Group {
                    if showPassword {
                        TextField("", text: $password,
                                  prompt: Text("at least 8 characters").foregroundColor(FAMColor.textFaint))
                    } else {
                        SecureField("", text: $password,
                                    prompt: Text("at least 8 characters").foregroundColor(FAMColor.textFaint))
                    }
                }
                .textContentType(.newPassword)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                Button { showPassword.toggle() } label: {
                    Image(systemName: showPassword ? "eye.slash" : "eye")
                        .foregroundStyle(FAMColor.textMuted)
                }
                .accessibilityLabel(showPassword ? "Hide password" : "Show password")
            }
            .modifier(FieldBox())
        }
    }

    private func label(_ text: String) -> some View {
        Text(text).monoLabel(9).padding(.bottom, 7)
    }

    private var address: String { email.trimmingCharacters(in: .whitespacesAndNewlines) }

    private func go() async {
        if sent { await finish() } else { await send() }
    }

    /// Step 1, and "Send a new code": the server's `message` becomes the
    /// note, else the web's own sentence.
    private func send() async {
        problem = ""
        guard AuthView.looksLikeEmail(address) else {
            problem = "Enter the email address on your account."
            return
        }
        working = true
        defer { working = false }
        do {
            let answer = try await model.api.resetStart(email: address)
            let said = answer.message ?? ""
            note = said.isEmpty ? "If that address has a FAM account, a code is on its way." : said
            sent = true
            showPassword = false
            // The code is what is typed next (`sendResetCode`).
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { focus = .code }
        } catch {
            problem = error.localizedDescription
        }
    }

    private func finish() async {
        problem = ""
        let digits = String(code.filter { $0.isNumber })
        guard digits.count == 6 else {
            problem = "Enter the 6-digit code from the email."
            return
        }
        guard password.count >= 8 else {
            problem = "Use at least 8 characters for your password."
            return
        }
        working = true
        defer { working = false }
        do {
            try await model.finishReset(email: address, code: digits, password: password)
            model.nowPlaying.say("Password changed. You're signed in.")
        } catch {
            problem = error.localizedDescription
        }
    }
}
