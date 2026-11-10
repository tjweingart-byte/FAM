#if canImport(SwiftUI)
import SwiftUI

/// Report something (1.2, `moderation.py`). Put it behind a "Report" row in
/// the ⋯ of a chat, a message, a profile, a story vibe and the player - the
/// places the web has one (exploreFAM and its comments left the app in
/// §499; `.comment` stays for the server's old clients):
///
///     .sheet(isPresented: $reporting) {
///         ReportSheet(api: api, subject: .message(id: message.id)) { receipt in
///             if receipt?.hidden == true { messages.removeAll { $0.id == message.id } }
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
            } catch SafetyError.accountRequired(let detail) where !detail.isEmpty {
                failure = detail
            } catch {
                failure = "Couldn't send that report"
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
    ///
    /// A refusal is never swallowed: `onError` gets the server's sentence
    /// (`detail`), else "Couldn't block them" (`blockPerson`).
    func blockConfirmation(isPresented: Binding<Bool>, name: String, handle: String,
                           api: SafetyAPI,
                           onBlocked: @escaping () -> Void,
                           onAccountRequired: @escaping () -> Void = {},
                           onError: @escaping (String) -> Void = { _ in }) -> some View {
        confirmationDialog("Block \(name.isEmpty ? "@" + handle : name)?",
                           isPresented: isPresented, titleVisibility: .visible) {
            Button("Block", role: .destructive) {
                Task { @MainActor in
                    do {
                        try await api.block(handle: handle)
                        onBlocked()
                    } catch SafetyError.accountRequired {
                        onAccountRequired()
                    } catch SafetyError.http(_, let detail) where !detail.isEmpty {
                        onError(detail)
                    } catch {
                        onError("Couldn't block them")
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Neither of you will see the other\u{2019}s comments, messages, vibes or profile, and they can\u{2019}t message you. They won\u{2019}t be told.")
        }
    }
}

/// Settings > Privacy and safety > Blocked people.
public struct BlockedPeopleView: View {
    let api: SafetyAPI
    @State private var people: [BlockedPerson] = []
    @State private var loaded = false
    /// "Unblocked @handle" / "Couldn't unblock them" (`openBlocked`).
    @State private var status = ""

    public init(api: SafetyAPI) { self.api = api }

    public var body: some View {
        List {
            if !status.isEmpty {
                Text(status)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
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
            do {
                try await api.unblock(handle: person.handle)
                status = "Unblocked @" + person.handle
            } catch {
                status = "Couldn't unblock them"
            }
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
    @State private var blockedCount: Int? = nil

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
                NavigationLink {
                    BlockedPeopleView(api: api)
                } label: {
                    HStack {
                        Text("Blocked people")
                        Spacer()
                        // The count, or "None" (the web's Settings row).
                        if let blockedCount {
                            Text(blockedCount == 0 ? "None" : String(blockedCount))
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            Link("Terms and conditions", destination: base.appendingPathComponent("terms"))
            Link("Privacy policy", destination: base.appendingPathComponent("privacy"))
            Link("Help and support", destination: base.appendingPathComponent("support"))
        } header: {
            Text("Privacy and safety")
        } footer: {
            Text("FAM writes episodes for what you ask with Anthropic's Claude, and searches for sources with Parallel and Perplexity. Turn this off and you can still listen to everything FAM writes ahead.")
        }
        .task {
            if consent.current == nil { await consent.load() }
            if signedIn { blockedCount = (try? await api.blocked())?.count }
        }
    }
}
#endif
