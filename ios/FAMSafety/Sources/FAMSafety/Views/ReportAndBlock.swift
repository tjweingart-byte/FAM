#if canImport(SwiftUI)
import SwiftUI

/// Report something (1.2, `moderation.py`). Put it behind a "Report" row in
/// the ⋯ of a comment, a chat, a profile, a story vibe, the player and the
/// Explore reel - the same places the web has one:
///
///     .sheet(isPresented: $reporting) {
///         ReportSheet(api: api, subject: .comment(id: comment.id)) { receipt in
///             if receipt?.hidden == true { comments.removeAll { $0.id == comment.id } }
///         }
///     }
///
/// The reasons and the confirmation are the server's words.
public struct ReportSheet: View {
    let api: SafetyAPI
    let subject: ReportSubject
    let done: (ReportReceipt?) -> Void

    @State private var options: ReportOptions?
    @State private var sending = false
    @State private var receipt: ReportReceipt?
    @State private var failure: String?
    @Environment(\.dismiss) private var dismiss

    public init(api: SafetyAPI, subject: ReportSubject,
                done: @escaping (ReportReceipt?) -> Void) {
        self.api = api
        self.subject = subject
        self.done = done
    }

    public var body: some View {
        NavigationStack {
            Group {
                if let receipt {
                    VStack(spacing: 16) {
                        Image(systemName: "checkmark.circle")
                            .font(.largeTitle)
                            .accessibilityHidden(true)
                        Text(receipt.message)
                            .multilineTextAlignment(.center)
                        Button("Done") { finish(receipt) }
                            .buttonStyle(.borderedProminent)
                    }
                    .padding(24)
                } else if let options {
                    List {
                        Section {
                            ForEach(options.reasons) { reason in
                                Button(reason.label) { send(reason) }
                                    .disabled(sending)
                            }
                        } header: {
                            Text("Why are you reporting this?")
                        } footer: {
                            Text("A person reviews every report within \(options.reviewHours) hours. You won't see this again.")
                        }
                    }
                } else if let failure {
                    Text(failure).padding(24)
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("Report")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { finish(nil) }
                }
            }
        }
        .task {
            do { options = try await api.reportOptions() }
            catch { failure = "Couldn't reach FAM. Try again." }
        }
    }

    private func send(_ reason: ReportReason) {
        sending = true
        Task {
            do {
                receipt = try await api.report(subject, reason: reason.id)
            } catch SafetyError.http(_, let detail) where !detail.isEmpty {
                failure = detail
            } catch {
                failure = "Couldn't send that report."
            }
            sending = false
        }
    }

    private func finish(_ receipt: ReportReceipt?) {
        done(receipt)
        dismiss()
    }
}

public extension View {
    /// "Block <name>?" with the consequence spelled out, then the call. Put
    /// "Block" in the ⋯ of a profile, a chat and a story. Blocking needs an
    /// account: `onAccountRequired` shows the sign-up gate.
    func blockConfirmation(isPresented: Binding<Bool>, name: String, handle: String,
                           api: SafetyAPI,
                           onBlocked: @escaping () -> Void,
                           onAccountRequired: @escaping () -> Void = {}) -> some View {
        confirmationDialog("Block \(name.isEmpty ? "@" + handle : name)?",
                           isPresented: isPresented, titleVisibility: .visible) {
            Button("Block", role: .destructive) {
                Task {
                    do {
                        try await api.block(handle: handle)
                        onBlocked()
                    } catch SafetyError.accountRequired {
                        onAccountRequired()
                    } catch {}
                }
            }
        } message: {
            Text("Neither of you will see the other's comments, messages, vibes or profile, and they can't message you. They won't be told.")
        }
    }
}

/// Settings > Privacy and safety > Blocked people.
public struct BlockedPeopleView: View {
    let api: SafetyAPI
    @State private var people: [BlockedPerson] = []
    @State private var loaded = false

    public init(api: SafetyAPI) { self.api = api }

    public var body: some View {
        List {
            if loaded && people.isEmpty {
                Text("You haven't blocked anyone.")
                    .foregroundStyle(.secondary)
            }
            ForEach(people) { person in
                HStack {
                    VStack(alignment: .leading) {
                        Text(person.name.isEmpty ? "@" + person.handle : person.name)
                        Text("@" + person.handle)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Unblock") { unblock(person) }
                        .buttonStyle(.bordered)
                }
            }
        }
        .navigationTitle("Blocked people")
        .task { await reload() }
    }

    private func reload() async {
        people = (try? await api.blocked()) ?? []
        loaded = true
    }

    private func unblock(_ person: BlockedPerson) {
        Task {
            try? await api.unblock(handle: person.handle)
            await reload()
        }
    }
}

/// The Settings section the web calls Privacy and safety: the AI answer,
/// blocked people, and the three pages every listener agreed to or may need.
public struct PrivacyAndSafetySection: View {
    @ObservedObject var consent: ConsentModel
    let api: SafetyAPI
    let base: URL
    let signedIn: Bool

    public init(consent: ConsentModel, api: SafetyAPI, base: URL, signedIn: Bool) {
        self.consent = consent
        self.api = api
        self.base = base
        self.signedIn = signedIn
    }

    public var body: some View {
        Section {
            Toggle("Send my questions to AI", isOn: Binding(
                get: { consent.current?.given == true },
                set: { allow in
                    Task {
                        if allow { _ = await consent.ensure() } else { await consent.answer(false) }
                    }
                }))
            if signedIn {
                NavigationLink("Blocked people") { BlockedPeopleView(api: api) }
            }
            Link("Terms and community rules", destination: base.appendingPathComponent("terms"))
            Link("Privacy policy", destination: base.appendingPathComponent("privacy"))
            Link("Help and support", destination: base.appendingPathComponent("support"))
        } header: {
            Text("Privacy and safety")
        } footer: {
            Text("FAM writes episodes for what you ask with Anthropic's Claude, and searches for sources with Exa. Turn this off and you can still listen to everything FAM writes ahead.")
        }
        .task { if consent.current == nil { await consent.load() } }
    }
}
#endif
