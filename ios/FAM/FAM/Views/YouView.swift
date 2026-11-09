import FAMSafety
import SwiftUI

/// YourFAM: the wordmark, who you are, then Settings' rows (`.set-group`,
/// `.set-row`) - privacy and safety, sign out, and deleting the account,
/// which has to be inside the app, not behind a support address (5.1.1(v)).
struct YouView: View {
    @EnvironmentObject private var model: AppModel

    @State private var confirmDelete = false
    @State private var problem = ""

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

    private func initials(_ who: Listener) -> String {
        let source = (who.displayName?.isEmpty == false ? who.displayName! : who.email)
        return String(source.prefix(1)).uppercased()
    }
}
