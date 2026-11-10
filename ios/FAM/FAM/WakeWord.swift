import AVFoundation
import Foundation
import Speech
import UIKit

/// "Hey FAM" (§158), the web's `WAKE` ported to Apple's recogniser: saying
/// "hey FAM" or "what's up FAM" opens voice search, so the spoken path does
/// not start with finding a button. A second recogniser listens for the
/// phrase and nothing else; when it hears it, it gets off the microphone and
/// voice search takes over, with anything said after the phrase already in
/// the words. The web's four rules, kept:
///
/// * **Asked for, never assumed.** Off until turned on in Settings; turning
///   it on is the tap that asks for the microphone and speech recognition.
/// * **It hears the phrase and does nothing else.** Nothing it hears is kept,
///   sent to FAM or searched; it opens voice search, and what happens next is
///   voice search's own ("Search now" and its count, §159). On a phone that
///   can, the recognising happens on the phone.
/// * **It stands aside**: not while an episode plays (it would be listening
///   to FAM, and an open microphone reroutes the audio), not while one is
///   being made, not while voice search has the microphone, and not in the
///   background - iOS gives no app the microphone in the background for
///   this, so it listens only while FAM is open and in front. That is the
///   closest iOS comes to the web's "whenever FAM is open".
/// * **A refusal turns it off and says so**, rather than retrying a blocked
///   microphone forever. Anything else backs off and tries again.
@MainActor
final class WakeListener: ObservableObject {
    nonisolated static let key = "fam.wakePhrase"
    /// `WAKE_AFTER_CLOSE_MS`: voice search closed, then an episode may start.
    static let afterClose: TimeInterval = 1.5
    /// The web's `setInterval(syncWake, 2000)` safety net.
    static let poll: TimeInterval = 2

    @Published private(set) var isOn: Bool = UserDefaults.standard.bool(forKey: WakeListener.key)

    /// Whether it may listen now, apart from the app being in front: no
    /// voice search, nothing playing, nothing being made (`wakeShouldRun`).
    var mayListen: () -> Bool = { false }
    /// Heard: whatever followed the phrase in the same breath.
    var heard: (String) -> Void = { _ in }
    /// The app's toast.
    var say: (String) -> Void = { _ in }
    /// An episode is loaded on the player: the session goes back to playback.
    var keepsPlayback: () -> Bool = { false }

    private var engine: AVAudioEngine?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var generation = 0
    private var starting = false
    private var fails = 0
    private var notBefore = Date.distantPast
    private var timer: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?
    private var holdsSession = false
    private var inFront = true
    private var observers: [NSObjectProtocol] = []

    /// `WAKE_PHRASE`: recognisers write the name several ways, and "fam" on
    /// its own is the only one that matters; "family" is not it. Apple's
    /// apostrophe is curly, so both are taken.
    private static let phrase = try? NSRegularExpression(
        pattern: "\\b(?:hey|hi|hay|what['\u{2019}]?s up|whats up|what up|wassup|sup)[\\s,.!]+(?:fam|pham|fahm)\\b[\\s,.!?]*(.*)$",
        options: [.caseInsensitive])

    /// `wakeHeard`: the words after the phrase, or nil when it was not said.
    static func match(_ text: String) -> String? {
        guard let re = phrase else { return nil }
        let ns = text as NSString
        guard let m = re.firstMatch(in: text, range: NSRange(location: 0, length: ns.length)) else { return nil }
        let rest = m.range(at: 1)
        guard rest.location != NSNotFound else { return "" }
        return ns.substring(with: rest).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    init() {
        inFront = UIApplication.shared.applicationState == .active
        let centre = NotificationCenter.default
        let resign = centre.addObserver(forName: UIApplication.willResignActiveNotification,
                                        object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.inFront = false
                self?.stop()
            }
        }
        let active = centre.addObserver(forName: UIApplication.didBecomeActiveNotification,
                                        object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.inFront = true
                self?.schedule(after: 0)
            }
        }
        observers = [resign, active]
        if isOn { startPolling() }
    }

    // MARK: - The Settings row (`setWakePhrase`)

    /// On asks for the microphone and speech recognition first; a refusal
    /// leaves it off and says why.
    func set(_ on: Bool) {
        guard on else {
            turnOff(saying: "\u{201C}Hey FAM\u{201D} is off")
            return
        }
        Task {
            if let refusal = await Self.permissionProblem() {
                turnOff(saying: refusal + ", so \u{201C}Hey FAM\u{201D} is off")
                return
            }
            isOn = true
            UserDefaults.standard.set(true, forKey: Self.key)
            fails = 0
            notBefore = .distantPast
            say("Say \u{201C}Hey FAM\u{201D} to search by voice")
            startPolling()
            sync()
        }
    }

    /// Signed out: the opt-in is the listener's, not the phone's (it lives
    /// in `fam.prefs` on the web, which `forgetLocalPrefs` removes). Off,
    /// silently - there is nobody to tell.
    func forget() {
        isOn = false
        UserDefaults.standard.set(false, forKey: Self.key)
        pollTask?.cancel()
        pollTask = nil
        stop()
    }

    private func turnOff(saying sentence: String) {
        isOn = false
        UserDefaults.standard.set(false, forKey: Self.key)
        pollTask?.cancel()
        pollTask = nil
        stop()
        say(sentence)
    }

    // MARK: - When it listens (`syncWake`)

    private var shouldRun: Bool {
        isOn && inFront && VoiceSearchModel.available && mayListen()
    }

    /// `scheduleWake`: look again after a moment.
    func schedule(after seconds: TimeInterval) {
        timer?.cancel()
        timer = Task { [weak self] in
            if seconds > 0 { try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000)) }
            guard !Task.isCancelled else { return }
            self?.sync()
        }
    }

    /// Voice search closed: not back on the microphone until an episode the
    /// search starts has had the moment to begin.
    func holdAfterVoiceSearch() {
        stop()
        notBefore = Date().addingTimeInterval(Self.afterClose)
        schedule(after: Self.afterClose)
    }

    func sync() {
        let should = shouldRun
        let running = engine != nil || starting
        if should && !running {
            let wait = notBefore.timeIntervalSinceNow
            if wait > 0 {
                schedule(after: wait)
                return
            }
            start()
        } else if !should && running {
            stop()
        }
    }

    private func startPolling() {
        guard pollTask == nil else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(Self.poll * 1_000_000_000))
                guard let self, !Task.isCancelled else { return }
                if self.isOn || self.engine != nil { self.sync() }
            }
        }
    }

    // MARK: - Listening

    private func start() {
        generation += 1
        let mine = generation
        starting = true
        Task { await begin(mine) }
    }

    private func begin(_ mine: Int) async {
        let refusal = await Self.permissionProblem()
        // A later start or a stop owns `starting` now.
        guard mine == generation else { return }
        if let refusal {
            starting = false
            turnOff(saying: refusal + ", so \u{201C}Hey FAM\u{201D} is off")
            return
        }
        // Asked again: the answer took a moment, and things change.
        guard shouldRun else {
            starting = false
            return
        }
        guard let recognizer = VoiceSearchModel.makeRecognizer(), recognizer.isAvailable else {
            starting = false
            backOff()
            return
        }
        let session = AVAudioSession.sharedInstance()
        holdsSession = true
        do {
            // Alongside whatever else is playing on the phone, so opening
            // FAM never silences somebody's music to listen for its name -
            // nor moves it: Bluetooth stays the high-quality output (A2DP)
            // rather than dropping to the headset's call mode (HFP), so the
            // phone's own microphone does the listening.
            try session.setCategory(.playAndRecord, mode: .default,
                                    options: [.mixWithOthers, .defaultToSpeaker, .allowBluetoothA2DP])
            try session.setActive(true)
        } catch {
            restoreSession()
            starting = false
            backOff()
            return
        }
        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.channelCount > 0, format.sampleRate > 0 else {
            restoreSession()
            starting = false
            turnOff(saying: VoiceSearchModel.noMic + ", so \u{201C}Hey FAM\u{201D} is off")
            return
        }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        if recognizer.supportsOnDeviceRecognition { request.requiresOnDeviceRecognition = true }
        input.installTap(onBus: 0, bufferSize: 1024, format: format, block: Self.tap(request))
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            restoreSession()
            starting = false
            backOff()
            return
        }
        self.engine = engine
        self.request = request
        starting = false
        task = recognizer.recognitionTask(with: request, resultHandler: Self.handler(mine, self))
    }

    /// The audio tap runs on the audio thread: it only hands the buffer on.
    nonisolated private static func tap(_ request: SFSpeechAudioBufferRecognitionRequest) -> AVAudioNodeTapBlock {
        return { buffer, _ in request.append(buffer) }
    }

    /// The recogniser calls back off the main thread: plain values hop to
    /// the main actor.
    nonisolated private static func handler(_ mine: Int, _ model: WakeListener)
        -> (SFSpeechRecognitionResult?, Error?) -> Void {
        return { result, error in
            let text = result?.bestTranscription.formattedString ?? ""
            let isFinal = result?.isFinal ?? false
            let code = error.map { ($0 as NSError).code } ?? 0
            let failed = error != nil
            Task { @MainActor in
                model.received(mine, text: text, isFinal: isFinal, failed: failed, code: code)
            }
        }
    }

    private func received(_ mine: Int, text: String, isFinal: Bool, failed: Bool, code: Int) {
        guard mine == generation else { return }
        if !text.isEmpty {
            fails = 0
            if let rest = Self.match(text) {
                stop()
                heard(rest)
                return
            }
        }
        if isFinal || failed {
            // A quiet room and a cancel are not faults (`no-speech`,
            // `aborted`); anything else counts towards the back-off.
            let quiet = [0, 203, 216, 301, 1101, 1110].contains(code)
            if failed && !quiet { fails += 1 }
            ended()
        }
    }

    /// `rec.onend`: recognisers end on their own every minute or so; start
    /// again, backing off when it keeps failing rather than spinning on it.
    private func ended() {
        teardown(restore: true)
        let wait = fails > 0 ? min(30, pow(2, Double(fails))) : 0.25
        notBefore = Date().addingTimeInterval(wait)
        schedule(after: wait)
    }

    private func backOff() {
        fails += 1
        let wait = min(30, pow(2, Double(fails)))
        notBefore = Date().addingTimeInterval(wait)
        schedule(after: wait)
    }

    /// `stopWake`: off the microphone now. `restore` false when the player
    /// is about to set the session itself.
    func stop(restore: Bool = true) {
        timer?.cancel()
        generation += 1
        starting = false
        teardown(restore: restore)
    }

    private func teardown(restore: Bool) {
        generation += 1
        if let engine {
            engine.stop()
            engine.inputNode.removeTap(onBus: 0)
        }
        engine = nil
        request?.endAudio()
        task?.cancel()
        request = nil
        task = nil
        if restore {
            restoreSession()
        } else {
            holdsSession = false
        }
    }

    /// Back to the player's session, as voice search does
    /// (`VoiceSearchModel.restoreSession`).
    private func restoreSession() {
        guard holdsSession else { return }
        holdsSession = false
        let session = AVAudioSession.sharedInstance()
        if keepsPlayback() {
            try? session.setCategory(.playback, mode: .spokenAudio)
            try? session.setActive(true)
        } else {
            try? session.setActive(false, options: .notifyOthersOnDeactivation)
            try? session.setCategory(.playback, mode: .spokenAudio)
        }
    }

    // MARK: - Permissions

    /// nil when both are allowed (asking where not yet asked), else the
    /// sentence voice search would say.
    nonisolated private static func permissionProblem() async -> String? {
        let mic = await microphoneAllowed()
        if !mic { return await MainActor.run { VoiceSearchModel.micOff } }
        let speech = await speechAllowed()
        if speech != .authorized { return await MainActor.run { VoiceSearchModel.speechOff } }
        return nil
    }

    nonisolated private static func microphoneAllowed() async -> Bool {
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
}
