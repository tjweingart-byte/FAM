import SwiftUI

/// Sign up and log in, after the web's (`.auth`): the heading in the display
/// face, mono labels over rounded fields, the copper button, the terms box
/// (unticked, §228) under it, and "Already have an account? Log in".
struct AuthView: View {
    @EnvironmentObject private var model: AppModel

    @State private var making = true
    @State private var email = ""
    @State private var password = ""
    @State private var showPassword = false
    @State private var agreed = false
    @State private var working = false
    @State private var problem = ""
    @FocusState private var field: Field?

    enum Field { case email, password }

    private var ready: Bool {
        email.contains("@") && password.count >= 8 && (!making || agreed) && !working
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Wordmark(size: 30)
                    .frame(maxWidth: .infinity)
                    .padding(.top, 36)
                    .padding(.bottom, 28)

                Text(making ? "Sign up" : "Log in")
                    .font(FAMFont.heading(28))
                    .foregroundStyle(FAMColor.text)
                Text(making ? "Takes ten seconds. Listening stays free either way."
                            : "Welcome back.")
                    .font(FAMFont.body(14))
                    .foregroundStyle(FAMColor.textMuted)
                    .padding(.top, 10)

                ProviderButtons(making: making, agreed: agreed, problem: $problem, working: $working)
                    .padding(.top, 24)
                HStack(spacing: 10) {
                    Rectangle().fill(FAMColor.border).frame(height: 1)
                    Text("or").monoLabel(9)
                    Rectangle().fill(FAMColor.border).frame(height: 1)
                }
                .padding(.top, 20)

                label("Email").padding(.top, 18)
                TextField("", text: $email,
                          prompt: Text("you@example.com").foregroundColor(FAMColor.textFaint))
                    .keyboardType(.emailAddress)
                    .textContentType(.username)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($field, equals: .email)
                    .submitLabel(.next)
                    .onSubmit { field = .password }
                    .modifier(FieldBox())

                label("Password").padding(.top, 18)
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
                    .textContentType(making ? .newPassword : .password)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($field, equals: .password)
                    .submitLabel(.go)
                    .onSubmit { if ready { Task { await go() } } }
                    Button { showPassword.toggle() } label: {
                        Image(systemName: showPassword ? "eye.slash" : "eye")
                            .foregroundStyle(FAMColor.textMuted)
                    }
                    .accessibilityLabel(showPassword ? "Hide password" : "Show password")
                }
                .modifier(FieldBox())

                Button {
                    Task { await go() }
                } label: {
                    if working { ProgressView().tint(FAMColor.ink) } else { Text(making ? "Create account" : "Log in") }
                }
                .buttonStyle(CopperButtonStyle())
                .disabled(!ready)
                .opacity(ready ? 1 : 0.55)
                .padding(.top, 26)

                if making { terms.padding(.top, 18) }

                if !problem.isEmpty {
                    Text(problem)
                        .font(FAMFont.body(13))
                        .foregroundStyle(FAMColor.coral)
                        .padding(.top, 14)
                }

                HStack(spacing: 6) {
                    Text(making ? "Already have an account?" : "New to FAM?")
                        .foregroundStyle(FAMColor.textMuted)
                    Button(making ? "Log in" : "Sign up") {
                        making.toggle()
                        problem = ""
                    }
                    .fontWeight(.bold)
                    .foregroundStyle(FAMColor.copper)
                }
                .font(FAMFont.body(14))
                .frame(maxWidth: .infinity)
                .padding(.top, 40)
            }
            .padding(.horizontal, 40)
            .padding(.bottom, 30)
        }
        .scrollDismissesKeyboard(.interactively)
        .background(FAMColor.background.ignoresSafeArea())
    }

    /// The terms box: unticked, the button off until it is ticked (§228).
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
                Text("I agree to FAM's terms and I'm 13 or older. Review the [terms and conditions](https://familiarize.net/terms) and the [privacy policy](https://familiarize.net/privacy).")
                    .font(FAMFont.body(13))
                    .foregroundStyle(FAMColor.textMuted)
                    .lineSpacing(3)
                    .multilineTextAlignment(.leading)
            }
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(agreed ? .isSelected : [])
    }

    private func label(_ text: String) -> some View {
        Text(text).monoLabel(9).padding(.bottom, 7)
    }

    private func go() async {
        working = true
        defer { working = false }
        problem = ""
        do {
            let who = email.trimmingCharacters(in: .whitespacesAndNewlines)
            if making {
                try await model.signUp(email: who, password: password)
            } else {
                try await model.signIn(email: who, password: password)
            }
        } catch {
            problem = error.localizedDescription
        }
    }
}

/// A rounded field (`.auth input`).
struct FieldBox: ViewModifier {
    func body(content: Content) -> some View {
        content
            .font(FAMFont.body(15))
            .foregroundStyle(FAMColor.text)
            .padding(.horizontal, 14)
            .frame(minHeight: 44)
            .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(FAMColor.borderStrong))
    }
}

/// An account waiting for its place. It can still hear FAM: today's samples
/// play as replays, which is the one thing the waitlist lets through.
struct WaitingView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer
    @State private var samples: [Tile] = []
    @State private var confirmDelete = false
    @State private var problem = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Wordmark(size: 30)
                    .frame(maxWidth: .infinity)
                    .padding(.top, 36)
                    .padding(.bottom, 28)
                Text("You're on the list")
                    .font(FAMFont.heading(28))
                    .foregroundStyle(FAMColor.text)
                Text("Your place is saved, and we'll let you in as soon as we can. Until then, hear what FAM sounds like.")
                    .font(FAMFont.body(14))
                    .foregroundStyle(FAMColor.textMuted)
                    .lineSpacing(3)
                    .padding(.top, 10)

                if !samples.isEmpty {
                    Text("Today on FAM").monoLabel(9).padding(.top, 26).padding(.bottom, 10)
                    ForEach(samples) { tile in
                        Button {
                            if player.isLoaded && player.title == tile.title { player.toggle() } else { model.playSample(tile) }
                        } label: {
                            HStack(spacing: 12) {
                                Picture(url: model.api.picture(tile.thumb))
                                    .frame(width: 64, height: 48)
                                    .clipShape(RoundedRectangle(cornerRadius: 8))
                                Text(tile.title)
                                    .font(FAMFont.heading(13, .semibold))
                                    .foregroundStyle(FAMColor.text)
                                    .multilineTextAlignment(.leading)
                                Spacer(minLength: 4)
                                Image(systemName: player.isLoaded && player.title == tile.title && player.playing
                                      ? "pause.fill" : "play.fill")
                                    .font(.system(size: 13, weight: .bold))
                                    .foregroundStyle(FAMColor.ink)
                                    .frame(width: 32, height: 32)
                                    .background(FAMColor.copper, in: Circle())
                            }
                            .padding(10)
                            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 13))
                            .overlay(RoundedRectangle(cornerRadius: 13).stroke(FAMColor.border))
                        }
                        .buttonStyle(.plain)
                        .padding(.bottom, 9)
                    }
                }

                HStack(spacing: 10) {
                    Button("Check again") { Task { await model.open() } }
                        .buttonStyle(OutlineButtonStyle())
                    Button("Sign out") { Task { await model.signOut() } }
                        .buttonStyle(OutlineButtonStyle())
                }
                .frame(maxWidth: .infinity)
                .padding(.top, 26)

                // Every account can be deleted from inside the app, a
                // waiting one included (5.1.1(v); `/api/account` is open
                // past the waitlist).
                Button("Delete this account") { confirmDelete = true }
                    .font(FAMFont.body(13, .semibold))
                    .foregroundStyle(FAMColor.coral)
                    .frame(maxWidth: .infinity)
                    .padding(.top, 18)
                if !problem.isEmpty {
                    Text(problem).font(FAMFont.body(12)).foregroundStyle(FAMColor.coral)
                        .frame(maxWidth: .infinity)
                }
            }
            .padding(.horizontal, 24)
            .padding(.bottom, 30)
        }
        .background(FAMColor.background.ignoresSafeArea())
        .confirmationDialog("Delete this account?", isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("Delete this account", role: .destructive) {
                Task {
                    do { try await model.deleteAccount() } catch { problem = error.localizedDescription }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This removes the account and its place on the waitlist, and unlinks its Apple ID. It can't be undone.")
        }
        .task { samples = (try? await model.api.samples()) ?? [] }
    }
}
