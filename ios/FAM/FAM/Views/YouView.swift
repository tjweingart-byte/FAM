import FAMSafety
import SwiftUI

/// YourFAM: the wordmark, who you are, then Settings' rows (`.set-group`,
/// `.set-row`) - privacy and safety, sign out, and deleting the account,
/// which has to be inside the app, not behind a support address (5.1.1(v)).
struct YouView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var social: SocialModel

    @State private var confirmDelete = false
    @State private var problem = ""
    @State private var info: AccountInfo?
    @State private var linkProblem = ""
    @State private var linking = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Wordmark(lead: "your", size: 28)
                    .frame(maxWidth: .infinity, minHeight: 48)
                    .padding(.top, 4)
                Form {
                    if let who = model.listener {
                        Section {
                            HStack(spacing: 15) {
                                Text(initials(who))
                                    .font(FAMFont.heading(26))
                                    .foregroundStyle(FAMColor.ink)
                                    .frame(width: 64, height: 64)
                                    .background(FAMColor.paper, in: Circle())
                                VStack(alignment: .leading, spacing: 5) {
                                    if let name = who.displayName, !name.isEmpty {
                                        Text(name)
                                            .font(FAMFont.heading(22))
                                            .foregroundStyle(FAMColor.text)
                                    }
                                    Text(who.email)
                                        .font(FAMFont.mono(10.5))
                                        .foregroundStyle(FAMColor.textMuted)
                                }
                            }
                            .padding(.vertical, 6)
                        }
                        .listRowBackground(FAMColor.background)
                    }

                    friendsRow
                        .listRowBackground(FAMColor.surface)

                    signInMethods
                        .listRowBackground(FAMColor.surface)

                    PrivacyAndSafetySection(consent: model.consent, api: model.safety,
                                            base: model.api.base, signedIn: true)
                        .listRowBackground(FAMColor.surface)

                    Section {
                        Button("Sign out") { Task { await model.signOut() } }
                            .foregroundStyle(FAMColor.text)
                        Button("Delete my account", role: .destructive) { confirmDelete = true }
                            .foregroundStyle(FAMColor.coral)
                        if !problem.isEmpty {
                            Text(problem).font(FAMFont.body(12)).foregroundStyle(FAMColor.coral)
                        }
                    } header: {
                        Text("Account").monoLabel(9, color: FAMColor.copper, bold: true)
                    } footer: {
                        Text("Deleting your account removes it and everything FAM keeps about you: your history, mixes, messages and profile.")
                            .font(FAMFont.body(11))
                            .foregroundStyle(FAMColor.textFaint)
                    }
                    .listRowBackground(FAMColor.surface)
                }
                .font(FAMFont.body(14, .semibold))
                .scrollContentBackground(.hidden)
                .background(FAMColor.background)
            }
            .background(FAMColor.background)
            .toolbar(.hidden, for: .navigationBar)
            .task(id: model.listener?.userId) { info = try? await model.api.account() }
            // YourFAM loading is one of the two moments the web looks for a
            // follower to announce (`checkNewFollowers`).
            .task {
                await social.loadGraph()
                social.checkNewFollowers()
            }
            .confirmationDialog("Delete your account?", isPresented: $confirmDelete,
                                titleVisibility: .visible) {
                Button("Delete my account", role: .destructive) {
                    Task {
                        do { try await model.deleteAccount() } catch { problem = error.localizedDescription }
                    }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This can't be undone.")
            }
        }
    }

    /// How this account signs in, and linking Apple or Google to it, so a
    /// later "Sign in with Apple" lands here rather than making a new
    /// account (the server never joins accounts by email).
    /// Friends (the web's friends number on YourFAM): the counts, and how
    /// many followed since Friends was last opened - cleared by opening
    /// it, never by the popup (quirk 18).
    private var friendsRow: some View {
        Section {
            Button { social.open(.friends) } label: {
                HStack(spacing: 10) {
                    Text("Friends").foregroundStyle(FAMColor.text)
                    Spacer()
                    if let fresh = social.graph?.newFollowers?.count, fresh > 0 {
                        TabBadge(count: fresh)
                            .accessibilityLabel("\(fresh) new")
                    }
                    Text(friendsLine)
                        .font(FAMFont.body(12))
                        .foregroundStyle(FAMColor.textMuted)
                    WebIcon(path: Icons.chevron, size: 14)
                        .foregroundStyle(FAMColor.textFaint)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        } header: {
            Text("Friends").monoLabel(9, color: FAMColor.copper, bold: true)
        }
    }

    private var friendsLine: String {
        guard let counts = social.graph?.counts else { return "" }
        let friends = counts.friends ?? 0
        let following = counts.following ?? 0
        return "\(friends) \(friends == 1 ? "friend" : "friends") \u{00B7} \(following) following"
    }

    private var signInMethods: some View {
        Section {
            if info?.hasPassword == true {
                methodRow("Email and password", linked: true, provider: nil)
            }
            methodRow("Apple", linked: isLinked("apple"), provider: "apple")
            if ProviderButtons.googleConfigured {
                methodRow("Google", linked: isLinked("google"), provider: "google")
            }
            if !linkProblem.isEmpty {
                Text(linkProblem).font(FAMFont.body(12)).foregroundStyle(FAMColor.coral)
            }
        } header: {
            Text("Sign-in methods").monoLabel(9, color: FAMColor.copper, bold: true)
        } footer: {
            Text("Link Apple or Google here and they sign you in to this account.")
                .font(FAMFont.body(11))
                .foregroundStyle(FAMColor.textFaint)
        }
    }

    private func isLinked(_ provider: String) -> Bool {
        info?.identities.contains(where: { $0.provider == provider }) ?? false
    }

    @ViewBuilder
    private func methodRow(_ name: String, linked: Bool, provider: String?) -> some View {
        if linked {
            HStack {
                Text(name).font(FAMFont.body(14, .semibold)).foregroundStyle(FAMColor.text)
                Spacer()
                Image(systemName: "checkmark.circle.fill").foregroundStyle(FAMColor.sage)
                    .accessibilityLabel("Linked")
                // The server's rule (`unlink_identity`): another way in must remain.
                if let provider, info?.hasPassword == true
                    || (info?.identities.contains(where: { $0.provider != provider }) ?? false) {
                    Button("Unlink") {
                        Task {
                            do {
                                try await model.api.unlink(provider: provider)
                                info = try? await model.api.account()
                            } catch {
                                linkProblem = error.localizedDescription
                            }
                        }
                    }
                    .font(FAMFont.body(12, .semibold))
                    .foregroundStyle(FAMColor.coral)
                    .buttonStyle(.borderless)
                }
            }
        } else if let provider {
            VStack(alignment: .leading, spacing: 8) {
                Text("Link \(name)").font(FAMFont.body(14, .semibold)).foregroundStyle(FAMColor.text)
                ProviderButtons(making: false, agreed: true, problem: $linkProblem, working: $linking,
                                linking: true, showApple: provider == "apple", showGoogle: provider == "google")
                    .onChange(of: linking) { _, busy in
                        if !busy { Task { info = try? await model.api.account() } }
                    }
            }
            .padding(.vertical, 4)
        }
    }

    private func initials(_ who: Listener) -> String {
        let source = (who.displayName?.isEmpty == false ? who.displayName! : who.email)
        return String(source.prefix(1)).uppercased()
    }
}
