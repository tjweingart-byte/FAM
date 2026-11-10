import SwiftUI
import UIKit

/// One screen: where the server is, what to play, how long it has played,
/// and a log of everything the phone did to the audio.
struct ContentView: View {
    @EnvironmentObject private var model: SpikeModel
    @EnvironmentObject private var player: StreamPlayer

    var body: some View {
        NavigationStack {
            Form {
                Section("Server") {
                    TextField("https://familiarize.net", text: $model.server)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button(model.loading ? "Loading…" : "Load today's samples") {
                        Task { await model.load() }
                    }
                    .disabled(model.loading)
                    Text(StreamPlayer.version).font(.footnote).foregroundStyle(.secondary)
                    if !model.message.isEmpty {
                        Text(model.message).font(.footnote).foregroundStyle(.secondary)
                    }
                }

                if !model.samples.isEmpty {
                    Section("Samples") {
                        ForEach(model.samples) { sample in
                            Button {
                                model.play(sample)
                            } label: {
                                HStack {
                                    Text(sample.title).foregroundStyle(.primary)
                                    Spacer()
                                    if model.current == sample {
                                        Image(systemName: "speaker.wave.2.fill")
                                            .accessibilityLabel("Playing")
                                    }
                                }
                            }
                        }
                        Toggle("Keep going when one ends", isOn: $model.keepGoing)
                    }
                }

                if player.phase != .idle {
                    Section("Now") {
                        Text(player.title).font(.headline)
                        LabeledContent("State", value: player.phase.rawValue)
                        LabeledContent("Heard", value: "\(clock(player.position)) of \(clock(player.arrived)) arrived")
                        LabeledContent("This session", value: clock(player.listened))
                        ProgressView(value: min(player.listened / 600, 1)) {
                            Text("Ten minutes locked")
                        }
                        HStack {
                            Button(player.phase == .paused ? "Resume" : "Pause") { player.toggle() }
                                .disabled(!([.playing, .buffering, .paused] as [StreamPlayer.Phase]).contains(player.phase))
                            Spacer()
                            Button("Stop", role: .destructive) { player.stop() }
                        }
                        .buttonStyle(.borderless)
                    }
                }

                Section {
                    if player.lines.isEmpty {
                        Text("Nothing yet.").foregroundStyle(.secondary)
                    }
                    ForEach(Array(player.lines.enumerated().reversed()), id: \.offset) { _, line in
                        Text(line).font(.caption.monospaced())
                    }
                } header: {
                    HStack {
                        Text("Log")
                        Spacer()
                        Button("Copy") { UIPasteboard.general.string = player.lines.joined(separator: "\n") }
                        Button("Clear") { player.clearLog() }
                    }
                    .textCase(nil)
                }
            }
            .navigationTitle("FAM audio spike")
        }
    }

    private func clock(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}
