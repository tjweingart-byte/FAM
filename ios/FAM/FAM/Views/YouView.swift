import FAMSafety
import SwiftUI

/// The account, privacy and safety, signing out, and deleting the account -
/// which has to be inside the app, not behind a support address (5.1.1(v)).
struct YouView: View {
    @EnvironmentObject private var model: AppModel

    @State private var confirmDelete = false
    @State private var problem = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Account") {
                    if let who = model.listener {
                        if let name = who.displayName, !name.isEmpty {
                            LabeledContent("Name", value: name)
                        }
                        LabeledContent("Email", value: who.email)
                    }
                }

                PrivacyAndSafetySection(consent: model.consent, api: model.safety,
                                        base: model.api.base, signedIn: true)

                Section {
                    Button("Sign out") { Task { await model.signOut() } }
                    Button("Delete my account", role: .destructive) { confirmDelete = true }
                    if !problem.isEmpty {
                        Text(problem).font(.footnote).foregroundStyle(.red)
                    }
                } footer: {
                    Text("Deleting your account removes it and everything FAM keeps about you: your history, mixes, messages and profile.")
                }
            }
            .navigationTitle("You")
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
}
