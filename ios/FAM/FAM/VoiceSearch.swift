import AVFoundation
import Foundation
import Speech
import SwiftUI

/// Voice search (§151, §159, §165), the web's `VOICE` ported to Apple's
/// recogniser: what is said becomes the words of a search, and the server
/// receives exactly the question it would have had it been typed. On the
/// phone where it can, the words are recognised on the device.
///
/// * Speech ending starts a five-second "Search now" that fills, never a
///   silent search: nothing new for 1.6 s ends listening, and the X beside
///   the button stops the count, leaving a full button that waits.
/// * The rings move only because something was heard: each new result sets
///   the level and it decays, so a silent room shows a still mark.
/// * Tapping the words stops the mic and the count and makes them editable
///   in place; Return sends the edited words down the normal search path.
/// * Apple's recogniser stops itself after about a minute: that is the end
///   of speech, like the web's `onend`.
///
/// "Hey FAM" (`WakeListener`, §158) opens this with whatever followed the
/// phrase already in the words; it listens only while FAM is in front,
/// since iOS gives no app the microphone in the background for it.
@MainActor
final class VoiceSearchModel: ObservableObject {
    /// `VOICE_SILENCE_MS`.
    static let silence: TimeInterval = 1.6
    /// `VOICE_AUTO_SEND_MS`.
    static let autoSend: TimeInterval = 5
    /// Nothing at all heard for this long: listening ends ("Didn't catch
    /// anything"), as the browser's own `no-speech` does.
    static let nothingHeard: TimeInterval = 8

    /// The web's `VOICE_ERRORS`, worded for a phone where the web's named a page.
    static let micOff = "FAM can't hear you - microphone access is off for FAM in Settings"
    static let speechOff = "Speech recognition is turned off on this device"
    static let noMic = "No microphone was found"
    static let network = "Speech recognition couldn't reach its service. Check your connection"
    static let noSpeech = "Didn't catch anything. Tap the mark to try again"
    static let language = "Speech recognition doesn't support this language here"
    static let cannotStart = "Speech recognition couldn't start on this phone"

    @Published private(set) var isOpen = false
    @Published private(set) var listening = false
    /// Words said before this round of listening, or the edited words.
    @Published private(set) var before = ""
    @Published private(set) var heard = ""
    @Published private(set) var interim = ""
    @Published private(set) var error = ""
    @Published private(set) var editing = false
    @Published var editText = ""
    /// The "Search now" count is running.
    @Published private(set) var counting = false
    /// How full the button is, 0 to 1 (animated over the five seconds).
    @Published var fill: Double = 0
    /// When the recogniser last handed back words: the rings' level is 1
    /// then, decaying ×0.93 a frame (`voiceLevelLoop`).
    @Published private(set) var heardAt = Date.distantPast

    /// The words to search, once sent.
    var onSend: (String) -> Void = { _ in }
    /// Closed without searching.
    var onCancel: () -> Void = {}
    /// Closed either way: "Hey FAM" waits a moment before it listens again
    /// (`WAKE_AFTER_CLOSE_MS`).
    var onClosed: () -> Void = {}
    /// An episode is loaded on the player (paused for the mic, or done):
    /// the session goes straight back to playback and stays FAM's, so the
    /// lock screen keeps its controls and the episode resumes as before.
    var keepsPlayback: () -> Bool = { false }

    private var recognizer: SFSpeechRecognizer?
    private var engine: AVAudioEngine?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var generation = 0
    private var stopping = false
    /// The audio session is set for recording and must go back to playback.
    private var holdsSession = false
    private var quietTask: Task<Void, Never>?
    private var nothingTask: Task<Void, Never>?
    private var finishTask: Task<Void, Never>?
    private var countTask: Task<Void, Never>?
    private var countToken = 0

    /// A phone with no recogniser for its language (or English) is offered
    /// no mic at all (`paintVoiceMic`).
    static let available: Bool = makeRecognizer() != nil

    nonisolated static func makeRecognizer() -> SFSpeechRecognizer? {
        if let mine = SFSpeechRecognizer(locale: Locale.current) { return mine }
        return SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    }

    // MARK: - What is shown

    /// `voiceText`: what has been said, without the words still being heard.
    var said: String { Self.collapse(before + " " + heard) }

    static func collapse(_ s: String) -> String {
        s.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }.joined(separator: " ")
    }

    /// `paintVoiceState`'s line under the mark.
    var status: String {
        if !error.isEmpty { return error }
        if editing {
            return Self.collapse(editText).isEmpty ? "Type your question, or tap the mark to speak"
                : "Fix the words, then press Search now"
        }
        if listening { return "Listening\u{2026}" }
        if counting { return "Searching when the button fills. Tap the words to fix them, X to wait" }
        if !said.isEmpty { return "Tap the words to fix them, or the mark to add more" }
        return "Tap the mark to start again"
    }

    var markLabel: String {
        listening ? "Stop listening" : (said.isEmpty ? "Listen again" : "Add more")
    }

    /// Search now is hidden (keeping its height) while listening or with
    /// nothing said.
    var goHidden: Bool {
        if editing { return Self.collapse(editText).isEmpty }
        return listening || said.isEmpty
    }

    /// The rings' level at a moment: 1 when words arrived, ×0.93 a frame.
    func level(at date: Date) -> Double {
        let frames = max(0, date.timeIntervalSince(heardAt)) * 60
        return pow(0.93, frames)
    }

    // MARK: - Opening and closing

    /// `openVoiceSearch`: open and listen at once. `seed` is what followed
    /// "hey FAM" in the same breath: on screen already, listening adds to it.
    func present(seed: String = "") {
        before = ""
        heard = Self.collapse(seed)
        interim = ""
        error = ""
        editing = false
        editText = ""
        heardAt = .distantPast
        stopCount(asButton: false)
        isOpen = true
        startListening()
    }

    /// The X at the top, or a send.
    func close(sending: Bool = false) {
        abortRecognition()
        stopCount(asButton: false)
        isOpen = false
        editing = false
        before = ""
        heard = ""
        interim = ""
        error = ""
        editText = ""
        if !sending { onCancel() }
        onClosed()
    }

    /// The app left the foreground: a count stops and leaves the button to
    /// press; listening stops and keeps what was heard.
    func suspend() {
        guard isOpen else { return }
        if listening || engine != nil {
            keepInterim()
            abortRecognition()
            if !said.isEmpty { stopCount(asButton: true) }
            return
        }
        if counting { stopCount(asButton: true) }
    }

    /// The mark: stop listening (which may start the count), or listen
    /// again, adding to what is there.
    func toggle() {
        if listening { stopListening() } else { startListening() }
    }

    // MARK: - Listening

    func startListening() {
        if editing { finishEditing() }
        abortRecognition()
        stopCount(asButton: false)
        before = said
        heard = ""
        interim = ""
        error = ""
        stopping = false
        generation += 1
        let mine = generation
        listening = true
        Task { await begin(mine) }
    }

    private func begin(_ mine: Int) async {
        let micOK = await Self.microphoneAllowed()
        guard mine == generation else { return }
        guard micOK else { failToStart(Self.micOff); return }
        let speech = await Self.speechAllowed()
        guard mine == generation else { return }
        guard speech == .authorized else { failToStart(Self.speechOff); return }
        guard let recognizer = Self.makeRecognizer() else { failToStart(Self.language); return }
        guard recognizer.isAvailable else { failToStart(Self.network); return }

        let session = AVAudioSession.sharedInstance()
        holdsSession = true
        do {
            try session.setCategory(.record, mode: .measurement, options: [])
            try session.setActive(true, options: .notifyOthersOnDeactivation)
        } catch {
            restoreSession()
            failToStart(Self.cannotStart)
            return
        }
        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.channelCount > 0, format.sampleRate > 0 else {
            restoreSession()
            failToStart(Self.noMic)
            return
        }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.taskHint = .search
        if recognizer.supportsOnDeviceRecognition { request.requiresOnDeviceRecognition = true }
        input.installTap(onBus: 0, bufferSize: 1024, format: format, block: Self.tap(request))
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            restoreSession()
            failToStart(Self.cannotStart)
            return
        }
        self.recognizer = recognizer
        self.engine = engine
        self.request = request
        task = recognizer.recognitionTask(with: request, resultHandler: Self.handler(mine, self))
        listening = true
        armNothingHeard(mine)
    }

    private func failToStart(_ sentence: String) {
        listening = false
        error = sentence
        generation += 1
    }

    /// The audio tap runs on the audio thread: it only hands the buffer on.
    nonisolated private static func tap(_ request: SFSpeechAudioBufferRecognitionRequest) -> AVAudioNodeTapBlock {
        return { buffer, _ in request.append(buffer) }
    }

    /// The recogniser calls back off the main thread: the answer is read
    /// there and handed to the main actor as plain values.
    nonisolated private static func handler(_ mine: Int, _ model: VoiceSearchModel)
        -> (SFSpeechRecognitionResult?, Error?) -> Void {
        return { result, error in
            let text = result?.bestTranscription.formattedString ?? ""
            let isFinal = result?.isFinal ?? false
            var code = 0
            var domain = ""
            if let error {
                let ns = error as NSError
                code = ns.code
                domain = ns.domain
            }
            let failed = error != nil
            Task { @MainActor in
                model.received(mine, text: text, isFinal: isFinal, failed: failed, code: code, domain: domain)
            }
        }
    }

    nonisolated private static func microphoneAllowed() async -> Bool {
        // iOS 17's way of asking (AVAudioApplication); the AVAudioSession
        // calls it replaces are deprecated.
        switch AVAudioApplication.shared.recordPermission {
        case .granted: return true
        case .denied: return false
        default:
            return await withCheckedContinuation { (c: CheckedContinuation<Bool, Never>) in
                AVAudioApplication.requestRecordPermission { granted in c.resume(returning: granted) }
            }
        }
    }

    nonisolated private static func speechAllowed() async -> SFSpeechRecognizerAuthorizationStatus {
        let now = SFSpeechRecognizer.authorizationStatus()
        if now != .notDetermined { return now }
        return await withCheckedContinuation { (c: CheckedContinuation<SFSpeechRecognizerAuthorizationStatus, Never>) in
            SFSpeechRecognizer.requestAuthorization { status in c.resume(returning: status) }
        }
    }

    private func received(_ mine: Int, text: String, isFinal: Bool, failed: Bool,
                          code: Int, domain: String) {
        guard mine == generation else { return }
        if !text.isEmpty {
            split(text)
            heardAt = Date()
            nothingTask?.cancel()
            // Stopped when nothing new has been heard for a moment.
            armQuiet(mine)
        }
        if isFinal {
            ended(mine)
            return
        }
        if failed {
            // With words on the screen any end is the end of speech - the
            // recogniser's own minute included - and "didn't catch
            // anything" is not said over them.
            let hasWords = !said.isEmpty || !interim.isEmpty
            let sentence = Self.sentence(code: code, domain: domain)
            let ours = stopping && sentence != Self.noSpeech
            if !hasWords && !sentence.isEmpty && !ours { error = sentence }
            ended(mine)
        }
    }

    /// Apple's codes as the web's sentences; "" for a cancel this app made.
    static func sentence(code: Int, domain: String) -> String {
        if domain == NSURLErrorDomain { return network }
        switch code {
        case 1110, 203: return noSpeech
        case 216, 301, 1101: return ""
        case 1700: return speechOff
        default: return "Speech recognition stopped (\(code))"
        }
    }

    /// The recogniser's text is everything said this round: the last three
    /// words are still being heard (muted), the rest is said.
    private func split(_ text: String) {
        let words = Self.collapse(text).components(separatedBy: " ").filter { !$0.isEmpty }
        let tail = min(3, words.count)
        heard = words.dropLast(tail).joined(separator: " ")
        interim = words.suffix(tail).joined(separator: " ")
    }

    private func keepInterim() {
        if !interim.isEmpty {
            heard = Self.collapse(heard + " " + interim)
            interim = ""
        }
    }

    private func armQuiet(_ mine: Int) {
        quietTask?.cancel()
        quietTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(Self.silence * 1_000_000_000))
            guard !Task.isCancelled, let self, mine == self.generation, self.listening else { return }
            self.stopListening()
        }
    }

    private func armNothingHeard(_ mine: Int) {
        nothingTask?.cancel()
        nothingTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(Self.nothingHeard * 1_000_000_000))
            guard !Task.isCancelled, let self, mine == self.generation, self.listening else { return }
            if self.said.isEmpty && self.interim.isEmpty { self.error = Self.noSpeech }
            self.stopListening()
        }
    }

    /// `rec.stop()`: no more audio; the recogniser's last word, then the end.
    func stopListening() {
        guard listening else { return }
        stopping = true
        quietTask?.cancel()
        nothingTask?.cancel()
        if let engine {
            engine.stop()
            engine.inputNode.removeTap(onBus: 0)
        }
        engine = nil
        request?.endAudio()
        let mine = generation
        finishTask?.cancel()
        // The final answer usually lands at once; if not, end without it.
        finishTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 1_200_000_000)
            guard !Task.isCancelled, let self, mine == self.generation else { return }
            self.ended(mine)
        }
    }

    /// `rec.onend`: what was still interim is what they said; with words,
    /// no error and nobody editing, the count starts.
    private func ended(_ mine: Int) {
        guard mine == generation else { return }
        generation += 1
        teardown()
        keepInterim()
        listening = false
        stopping = false
        if !said.isEmpty && error.isEmpty && !editing { startCount() }
    }

    /// `rec.abort()`: stop now, start nothing.
    private func abortRecognition() {
        generation += 1
        teardown()
        listening = false
        stopping = false
    }

    private func teardown() {
        quietTask?.cancel()
        nothingTask?.cancel()
        finishTask?.cancel()
        if let engine {
            engine.stop()
            engine.inputNode.removeTap(onBus: 0)
        }
        engine = nil
        request?.endAudio()
        task?.cancel()
        request = nil
        task = nil
        recognizer = nil
        restoreSession()
    }

    /// Back to the player's own session (`Player.activateSession`), so an
    /// episode played next is heard as before.
    private func restoreSession() {
        guard holdsSession else { return }
        holdsSession = false
        let session = AVAudioSession.sharedInstance()
        if keepsPlayback() {
            try? session.setCategory(.playback, mode: .spokenAudio)
            try? session.setActive(true)
        } else {
            // Nothing of FAM's to hear: what the listener had playing
            // elsewhere carries on.
            try? session.setActive(false, options: .notifyOthersOnDeactivation)
            try? session.setCategory(.playback, mode: .spokenAudio)
        }
    }

    // MARK: - Search now

    /// `startVoiceCountdown`: the fill and the timer from the same number.
    private func startCount() {
        countToken += 1
        let mine = countToken
        counting = true
        var still = Transaction()
        still.disablesAnimations = true
        withTransaction(still) { fill = 0 }
        Task { [weak self] in
            // A beat, so the reset and the sweep are not one change.
            try? await Task.sleep(nanoseconds: 32_000_000)
            guard let self, mine == self.countToken, self.counting else { return }
            withAnimation(.linear(duration: Self.autoSend)) { self.fill = 1 }
        }
        countTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64((Self.autoSend + 0.032) * 1_000_000_000))
            guard !Task.isCancelled, let self, mine == self.countToken, self.counting else { return }
            self.counting = false
            self.send()
        }
    }

    /// `stopVoiceCountdown(asButton)`: a full, still button that waits, or
    /// an empty one.
    func stopCount(asButton: Bool) {
        countToken += 1
        countTask?.cancel()
        countTask = nil
        counting = false
        var still = Transaction()
        still.disablesAnimations = true
        withTransaction(still) { fill = asButton ? 1 : 0 }
    }

    /// The X beside Search now: "not by itself", not "not at all".
    func cancelCount() {
        stopCount(asButton: true)
    }

    // MARK: - Editing the words in place (§165)

    /// The words were tapped: the mic stops (what was still being heard is
    /// kept), the count stops, and the words are the box.
    func beginEditing() {
        if listening || engine != nil || request != nil {
            keepInterim()
            abortRecognition()
        }
        stopCount(asButton: true)
        error = ""
        editText = said
        editing = true
    }

    /// The box lost focus: the edited words are what was said, and the mark
    /// adds to them.
    func finishEditing() {
        guard editing else { return }
        before = Self.collapse(editText)
        heard = ""
        interim = ""
        editing = false
    }

    /// Search now, Return in the words, or the count running out: what is
    /// on the screen goes down the normal search path.
    func send() {
        if editing { finishEditing() }
        let words = said
        stopCount(asButton: false)
        guard !words.isEmpty else { return }
        close(sending: true)
        onSend(words)
    }
}

// MARK: - The voice search screen (`#voiceSearch`, shots 21-28)

/// Full screen over the app, on the page's own white (never charcoal): the
/// round X, the words, the orb with its rings, the line under it, and
/// Search now with its X.
struct VoiceSearchView: View {
    @ObservedObject var voice: VoiceSearchModel
    @FocusState private var focused: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var entered = false

    var body: some View {
        GeometryReader { geo in
            VStack(spacing: 0) {
                HStack {
                    Spacer()
                    closeButton
                }
                .frame(height: 30)
                .padding(.top, 6)
                words
                    .frame(maxWidth: .infinity)
                    .frame(minHeight: 96, maxHeight: max(96, geo.size.height * 0.34), alignment: .top)
                    .padding(.top, 10)
                    .padding(.horizontal, 4)
                Spacer(minLength: 0)
                stage
                Spacer(minLength: 0)
                Text(voice.status)
                    .font(FAMFont.mono(10.5))
                    .tracking(0.63)
                    .foregroundStyle(FAMColor.textMuted)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity, minHeight: 30)
                    .padding(.horizontal, 10)
                goRow
                    .padding(.top, 4)
            }
            .padding(.top, 14)
            .padding(.horizontal, 20)
            .padding(.bottom, 22)
        }
        .background(FAMColor.background.ignoresSafeArea())
        .offset(y: entered ? 0 : 14)
        .opacity(entered ? 1 : 0)
        .onAppear {
            if reduceMotion {
                entered = true
            } else {
                withAnimation(.easeOut(duration: 0.22)) { entered = true }
            }
        }
        .onChange(of: voice.editing) { _, now in
            if now { focused = true }
        }
        .onChange(of: focused) { _, now in
            if !now && voice.editing { voice.finishEditing() }
        }
    }

    private var closeButton: some View {
        Button { voice.close() } label: {
            WebIcon(path: Icons.close, size: 13, line: 2.4)
                .foregroundStyle(FAMColor.text)
                .frame(width: 30, height: 30)
                .overlay(Circle().stroke(FAMColor.borderStrong))
                .contentShape(Circle())
        }
        .buttonStyle(.press)
        .accessibilityLabel("Close voice search, back to searchFAM")
    }

    // MARK: The words

    @ViewBuilder private var words: some View {
        if voice.editing {
            editor
        } else {
            ScrollView {
                shownWords
                    .multilineTextAlignment(.center)
                    .lineSpacing(7)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
                    .padding(.horizontal, 8)
                    .contentShape(Rectangle())
                    .onTapGesture { voice.beginEditing() }
            }
            .scrollBounceBehavior(.basedOnSize)
            .defaultScrollAnchor(.bottom)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Your search - tap to correct it")
            .accessibilityValue(voice.said)
            .accessibilityAddTraits(.isButton)
            .accessibilityAction { voice.beginEditing() }
        }
    }

    private var shownWords: Text {
        let said = voice.said
        let interim = voice.interim
        if said.isEmpty && interim.isEmpty {
            return Text("The words you speak appear here\u{2026}")
                .font(FAMFont.heading(17))
                .foregroundColor(FAMColor.textFaint)
        }
        let lead = Text(said + (interim.isEmpty || said.isEmpty ? "" : " "))
            .font(FAMFont.heading(21))
            .foregroundColor(FAMColor.text)
        let tail = Text(interim)
            .font(FAMFont.heading(21))
            .foregroundColor(FAMColor.textMuted)
        return lead + tail
    }

    private var editor: some View {
        TextField("", text: $voice.editText, axis: .vertical)
            .font(FAMFont.heading(21))
            .foregroundStyle(FAMColor.text)
            .multilineTextAlignment(.center)
            .lineSpacing(7)
            .lineLimit(1...6)
            .focused($focused)
            .submitLabel(.search)
            .textInputAutocapitalization(.sentences)
            .padding(.vertical, 6)
            .padding(.horizontal, 8)
            .frame(maxWidth: .infinity, minHeight: 96, alignment: .top)
            // The wash and the copper line come up over 200 ms as editing
            // starts (`.vs-words`' transition); the words stay where they are.
            .background(FadesIn(duration: 0.2) {
                RoundedRectangle(cornerRadius: 12).fill(FAMColor.text.opacity(0.06))
            })
            .overlay(alignment: .bottom) {
                FadesIn(duration: 0.2) { Rectangle().fill(FAMColor.copper).frame(height: 2) }
            }
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .accessibilityLabel("Your search - tap to correct it")
            // Return sends (a vertical field types a new line instead; a
            // paste is plain words with its spacing collapsed).
            .onChange(of: voice.editText) { _, now in
                if now.contains("\n") {
                    voice.editText = VoiceSearchModel.collapse(now)
                    voice.send()
                }
            }
    }

    // MARK: The orb

    private var stage: some View {
        ZStack {
            TimelineView(.animation(paused: !voice.listening)) { context in
                VoiceWaves(level: voice.level(at: context.date),
                           listening: voice.listening,
                           time: context.date.timeIntervalSinceReferenceDate,
                           still: reduceMotion)
            }
            .frame(width: 250, height: 250)
            .allowsHitTesting(false)
            Button { voice.toggle() } label: {
                ZStack {
                    Circle().fill(FAMColor.surface)
                    Circle().stroke(voice.listening ? FAMColor.copper : FAMColor.text, lineWidth: 2)
                    Glyph(path: FAMLetters.a, box: CGRect(x: 3.55, y: 4.55, width: 16.9, height: 16.9))
                        .stroke(FAMColor.copper, style: StrokeStyle(lineWidth: 40 * 2.9 / 16.9,
                                                                   lineCap: .round, lineJoin: .round))
                        .frame(width: 40, height: 40)
                }
                .frame(width: 104, height: 104)
                .contentShape(Circle())
            }
            .buttonStyle(.press)
            .accessibilityLabel(voice.markLabel)
        }
        .frame(width: 250, height: 250)
    }

    // MARK: Search now

    private var goRow: some View {
        let hidden = voice.goHidden
        return HStack(spacing: 10) {
            Button { voice.send() } label: {
                Text("Search now")
                    .font(FAMFont.body(14, .bold))
                    .tracking(0.14)
                    .foregroundStyle(FAMColor.ink)
                    .padding(.horizontal, 26)
                    .frame(minWidth: 176, minHeight: 48)
                    .background(SearchNowFill(fill: voice.fill))
                    .clipShape(Capsule())
            }
            .buttonStyle(.press)
            if voice.counting {
                Button { voice.cancelCount() } label: {
                    WebIcon(path: Icons.close, size: 14, line: 2.4)
                        .foregroundStyle(FAMColor.text)
                        .frame(width: 48, height: 48)
                        .overlay(Circle().stroke(FAMColor.borderStrong))
                        .contentShape(Circle())
                }
                .buttonStyle(.press)
                .accessibilityLabel("Don't search automatically")
            }
        }
        .frame(maxWidth: .infinity, minHeight: 48)
        .opacity(hidden ? 0 : 1)
        .allowsHitTesting(!hidden)
        .accessibilityHidden(hidden)
    }
}

/// The button's ground: the web's own rgba(224,181,99,.32) - the pale gold
/// in the screenshots - and the copper filling from the left.
struct SearchNowFill: View {
    let fill: Double

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Rectangle().fill(Color(hex: 0xE0B563).opacity(0.32))
                Rectangle()
                    .fill(FAMColor.copper)
                    .frame(width: geo.size.width * CGFloat(min(max(fill, 0), 1)))
            }
        }
    }
}

/// The rings (viewBox 250): three arcs a side and two lines under the
/// orb, copper at width 3, unseen while nothing is heard; while words come
/// they show at .35 + level × .65 and swell 1.1 s in and out, the outer
/// rings .18 s and .36 s behind.
struct VoiceWaves: View {
    let level: Double
    let listening: Bool
    let time: Double
    let still: Bool

    var body: some View {
        let speaking = listening && level > 0.08
        return ZStack {
            ring(68, delay: 0)
            ring(86, delay: 0.18)
            ring(104, delay: 0.36)
            low(y: 212, half: 28, delay: 0.18)
            low(y: 225, half: 17, delay: 0.36)
        }
        .frame(width: 250, height: 250)
        .opacity(speaking ? 0.35 + level * 0.65 : 0)
    }

    private func pulse(_ delay: Double) -> Double {
        guard !still else { return 0 }
        let phase = (time - delay) / 1.1
        return 0.5 - 0.5 * cos(2 * Double.pi * phase)
    }

    private func ring(_ radius: CGFloat, delay: Double) -> some View {
        let s = 1 + level * 0.09 * pulse(delay)
        return WaveArcs(radius: radius)
            .stroke(FAMColor.copper, style: StrokeStyle(lineWidth: 3, lineCap: .round))
            .scaleEffect(CGFloat(s))
    }

    private func low(y: CGFloat, half: CGFloat, delay: Double) -> some View {
        let s = 1 + level * 0.35 * pulse(delay)
        return WaveLine(y: y, half: half)
            .stroke(FAMColor.copper, style: StrokeStyle(lineWidth: 3, lineCap: .round))
            .scaleEffect(x: CGFloat(s), y: 1)
    }
}

/// A ring's two arcs, ±40° either side of the orb, about (125, 125).
struct WaveArcs: Shape {
    let radius: CGFloat

    func path(in rect: CGRect) -> Path {
        let k = rect.width / 250
        let centre = CGPoint(x: rect.minX + 125 * k, y: rect.minY + 125 * k)
        var p = Path()
        p.addArc(center: centre, radius: radius * k, startAngle: .degrees(-40),
                 endAngle: .degrees(40), clockwise: false)
        let left = CGPoint(x: centre.x + radius * k * CGFloat(cos(140 * Double.pi / 180)),
                           y: centre.y + radius * k * CGFloat(sin(140 * Double.pi / 180)))
        p.move(to: left)
        p.addArc(center: centre, radius: radius * k, startAngle: .degrees(140),
                 endAngle: .degrees(220), clockwise: false)
        return p
    }
}

/// One of the two lines under the orb (`M97 212H153`, `M108 225H142`).
struct WaveLine: Shape {
    let y: CGFloat
    let half: CGFloat

    func path(in rect: CGRect) -> Path {
        let k = rect.width / 250
        var p = Path()
        p.move(to: CGPoint(x: rect.minX + (125 - half) * k, y: rect.minY + y * k))
        p.addLine(to: CGPoint(x: rect.minX + (125 + half) * k, y: rect.minY + y * k))
        return p
    }
}
