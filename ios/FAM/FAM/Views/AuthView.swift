import SwiftUI

/// Sign in, or make an account. Making one needs the terms ticked (§228):
/// an unticked box, the button off until it is ticked, and the links beside it.
struct AuthView: View {
    @EnvironmentObject private var model: AppModel

    @State private var making = false
    @State private var email = ""
    @State private var password = ""
    @State private var agreed = false
    @State private var working = false
    @State private var problem = ""

    private var ready: Bool {
        email.contains("@") && password.count >= 8 && (!making || agreed) && !working
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("FAM turns what you care about into short audio stories, so you can be part of the conversation.")
                        .foregroundStyle(.secondary)
                }
                Section {
                    TextField("Email", text: $email)
                        .keyboardType(.emailAddress)
                        .textContentType(.username)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("Password (8 or more characters)", text: $password)
                        .textContentType(making ? .newPassword : .password)
                }
                if making {
                    Section {
                        Toggle(isOn: $agreed) {
                            Text("I agree to the [Terms](https://familiarize.net/terms) and the [Privacy Policy](https://familiarize.net/privacy), and I'm 13 or older.")
                                .font(.footnote)
                        }
                    }
                }
                Section {
                    Button(making ? "Make my account" : "Sign in") {
                        Task { await go() }
                    }
                    .disabled(!ready)
                    if !problem.isEmpty {
                        Text(problem).font(.footnote).foregroundStyle(.red)
                    }
                }
                Section {
                    Button(making ? "I already have an account" : "Make an account") {
                        making.toggle()
                        problem = ""
                    }
                }
            }
            .navigationTitle(making ? "Make an account" : "Sign in")
        }
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

/// An account waiting for its place. It can still hear FAM: today's samples
/// play as replays, which is the one thing the waitlist lets through.
struct WaitingView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer
    @State private var samples: [Tile] = []

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("You're on the waitlist. Your place is saved, and we'll let you in as soon as we can.")
                }
                if !samples.isEmpty {
                    Section("Hear what FAM sounds like") {
                        ForEach(samples) { tile in
                            Button {
                                model.playSample(tile)
                            } label: {
                                Label(tile.title, systemImage: "play.circle")
                            }
                        }
                    }
                }
                if player.isLoaded {
                    Section("Now playing") {
                        Text(player.title)
                        Button(player.playing ? "Pause" : "Play") { player.toggle() }
                    }
                }
                Section {
                    Button("Check again") { Task { await model.open() } }
                    Button("Sign out", role: .destructive) { Task { await model.signOut() } }
                }
            }
            .navigationTitle("FAM")
            .task { samples = (try? await model.api.samples()) ?? [] }
        }
    }
}
