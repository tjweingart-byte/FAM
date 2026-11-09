import SwiftUI

/// Ask anything, and choose how long the answer is. The length goes back to
/// 1.5 minutes every time the app comes back (`search-bar`).
struct SearchView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase

    @State private var words = ""
    @State private var minutes = 1.5
    @FocusState private var typing: Bool

    private static let lengths: [Double] = [1, 1.5, 2, 3, 4, 5]

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Ask FAM anything", text: $words, axis: .vertical)
                        .lineLimit(1...4)
                        .focused($typing)
                        .submitLabel(.search)
                        .onSubmit { go() }
                    Picker("Length", selection: $minutes) {
                        ForEach(Self.lengths, id: \.self) { value in
                            Text(label(value)).tag(value)
                        }
                    }
                    Button("Search") { go() }
                        .disabled(words.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                Section {
                    Text("FAM researches your question from current sources and tells you the story. A new episode takes a moment to write; it starts as soon as the first words are ready.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Search")
            .onChange(of: scenePhase) { _, phase in
                if phase == .active { minutes = 1.5 }
            }
        }
    }

    private func label(_ value: Double) -> String {
        value == 1 ? "1 minute" : "\(FAMAPI.minutes(value)) minutes"
    }

    private func go() {
        typing = false
        let asked = words
        Task { await model.search(asked, minutes: minutes) }
    }
}
