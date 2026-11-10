import AVFoundation
import MediaPlayer
import UIKit

/// Plays one episode from `GET /api/v1/audio?fmt=pcm` while it arrives, and
/// keeps playing with the phone locked. Grown from the lock-screen spike
/// (`ios/FAMSpike`, PROBLEMS.md §537), which passed ten minutes locked, calls,
/// AirPods and route changes on a real phone; that behaviour is kept as it
/// was. Added for the app: seeking (the draggable bar, clamped to what has
/// arrived) and ±15 s (`transport`), the episode's picture, and refusals
/// handed back to the app (the AI question, the waitlist) instead of a
/// generic failure.
///
/// What the server sends with `fmt=pcm`: bare 16-bit little-endian mono
/// samples at the rate in `X-Sample-Rate`, no header, until the episode
/// ends. `static/fam-audio.js` is the reference player; this does what its
/// PCM path does: keep every sample that arrives, start once half a second
/// is in hand, never play past what has arrived.
///
/// **Every sample is kept, and the player node only ever holds a copy.** iOS
/// throws away whatever is queued on an `AVAudioPlayerNode` when the audio
/// route changes or a call takes the session (the first phone test, PROBLEMS.md
/// §537: a call mid-episode read as the episode ending, and the last 17
/// seconds were skipped). So the position is the truth, and after any pause,
/// interruption or route change the node is refilled from that position.
/// A buffer's completion only counts if it belongs to the current filling
/// (`epoch`); one flushed by a stop is ignored.
///
/// Everything here runs on the main queue - the URLSession delegate queue is
/// `.main`, and every notification is observed on `.main` - so there is no
/// locking. At 48 KB a second the main thread does not notice.
final class StreamPlayer: NSObject, ObservableObject, URLSessionDataDelegate {
    /// Which player this is, shown on screen and in the log, so a test is
    /// never read against an older build than the one being tested.
    static let version = "player 4 (app)"

    enum Phase: String {
        case idle, connecting, buffering, playing, paused, finished, failed
    }

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var title = ""
    /// The episode's picture, when it has one.
    @Published private(set) var picture: URL?
    /// Why the last episode could not play, in the server's words.
    @Published private(set) var problem = ""
    /// Seconds of this episode heard so far.
    @Published private(set) var position: Double = 0
    /// Seconds of this episode that have arrived.
    @Published private(set) var arrived: Double = 0
    /// The length asked for, in seconds: the total shown while the episode
    /// is still being written (`scrubTotal` on the web).
    @Published private(set) var expected: Double = 0
    /// Every sample has arrived: the real length is known.
    @Published private(set) var complete = false
    /// The episode's id (`X-FAM-Episode`, §173): history and progress name it.
    @Published private(set) var episodeID = ""
    /// 0.5x to 2x; 1x by default and bypassed there (`speed-pitch`).
    @Published private(set) var rate: Double = 1
    @Published private(set) var keepsPitch = true

    /// The total the bar and the clock show: the requested length while the
    /// episode is written, its real length once all of it has arrived.
    var total: Double { complete ? arrived : max(expected, arrived) }
    /// Seconds heard this session, every episode together: the number the
    /// ten-minute locked test is read from.
    @Published private(set) var listened: Double = 0
    @Published private(set) var lines: [String] = []

    /// Called when an episode has played to its end.
    var onFinished: (() -> Void)?
    /// Called when the server refused the episode. Return true if the app
    /// dealt with it (asked the AI question and will retry, showed the
    /// waitlist); the player then goes quiet rather than showing a failure.
    var onRefused: ((FAMError) -> Bool)?
    /// The loading screen's gate (`startGate` on the web): with `holdStart`,
    /// the first audio is ready but silent until `release()`, so the five
    /// steps are checked off before the first word. Told whether this was a
    /// replay (`X-FAM-Cache: hit`), which has no steps to finish.
    var onReady: ((Bool) -> Void)?
    /// Called when an episode fails, so the loading screen can step aside.
    var onFailed: (() -> Void)?
    /// Called once every sample of a streamed episode has arrived: the app
    /// keeps it on the phone for offline listening if it may (`OfflineShelf`).
    var onArrived: (() -> Void)?
    /// The server said this audio may be kept on the device
    /// (`X-FAM-Keepable: 1`: a production voice, never the placeholder tone,
    /// never an attachment's).
    private(set) var keepable = false
    /// Playing samples kept on this phone, not a stream (`playStored`).
    private(set) var fromDevice = false
    /// Which play this is: bumped by every play, device play and stop, so a
    /// watcher from an earlier episode knows the player has moved on.
    var playToken: Int { generation }
    /// The last failure was the connection itself - the stream broke and
    /// every reconnect failed - not the server saying no. A playlist stops
    /// on it rather than walking on (`mixItemFailed`'s `err.network`).
    private(set) var lastFailureWasNetwork = false
    /// The body of the last refusal (`AudioRefusal`): the readings of a name
    /// (300), the allowance's verdict (429). Nil until something refuses.
    private(set) var refusal: AudioRefusal?
    /// The server's own sentence when the last failure was a refusal it
    /// explained; "" otherwise, so the app says "Could not generate that
    /// briefing" (`onGenerationFailed`).
    private(set) var failureWords = ""

    private let engine = AVAudioEngine()
    private let node = AVAudioPlayerNode()
    /// Speed. Keeps the voice's pitch unless told not to (`speed-pitch`):
    /// the web's WSOLA, here Apple's time-pitch unit.
    private let timePitch = AVAudioUnitTimePitch()
    private var format: AVAudioFormat?
    private lazy var session = URLSession(configuration: .default, delegate: self,
                                          delegateQueue: .main)

    private var task: URLSessionDataTask?
    private var request: URLRequest?
    /// Bumped on every new episode, so stream callbacks from the last one are ignored.
    private var generation = 0

    private var sampleRate: Double = 22050
    /// Every sample of this episode that has arrived.
    private var samples: [Float] = []
    /// Bytes of samples taken in from this episode's stream.
    private var bytesTaken = 0
    /// Bytes to drop at the front of a reconnected stream (what was already taken).
    private var skipBytes = 0
    /// A byte of a sample that straddled two chunks.
    private var carry = Data()

    /// Bumped every time the node is refilled; older buffers' completions are ignored.
    private var epoch = 0
    /// The frame the node's current filling starts at.
    private var base = 0
    /// The next frame to hand the node.
    private var next = 0
    /// Buffers of the current filling not yet played.
    private var outstanding = 0
    /// The last position read while the node was running: where to pick up
    /// when the node's own clock is gone (after a stop or a route change).
    private var lastFrame = 0

    private var streamDone = false
    private var started = false
    private var retries = 0
    private var refusedResponse: URLResponse?
    private var holdStart = false
    private var gated = false
    private var cacheHit = false
    private var refusalBody = Data()
    private var askedAt = Date()
    private var listenedBefore: Double = 0
    private var resumeAfterInterruption = false
    private var ticker: Timer?
    private var observers: [NSObjectProtocol] = []

    override init() {
        super.init()
        engine.attach(node)
        engine.attach(timePitch)
        let saved = UserDefaults.standard.double(forKey: "speed")
        rate = saved > 0 ? saved : 1
        keepsPitch = UserDefaults.standard.object(forKey: "pitch_lock") as? Bool ?? true
        applyRate()
        configure(rate: sampleRate)
        observe()
        remoteCommands()
        log("started: \(Self.version)")
    }

    // MARK: - Control

    func play(title: String, picture: URL? = nil, request: URLRequest, minutes: Double = 0,
              holdStart: Bool = false) {
        stopStream()
        expected = minutes * 60
        complete = false
        episodeID = ""
        self.holdStart = holdStart
        gated = false
        cacheHit = false
        keepable = false
        fromDevice = false
        generation += 1
        self.title = title
        self.picture = picture
        problem = ""
        self.request = request
        samples = []; bytesTaken = 0; skipBytes = 0; carry = Data()
        epoch += 1; base = 0; next = 0; outstanding = 0; lastFrame = 0
        streamDone = false; started = false; retries = 0
        refusedResponse = nil; refusalBody = Data()
        refusal = nil; failureWords = ""
        position = 0; arrived = 0
        listenedBefore = listened
        guard activateSession() else { return }
        askedAt = Date()
        phase = .connecting
        log("asked for \"\(title)\"")
        connect()
        startTicker()
        nowPlaying()
    }

    /// An episode kept on this phone, played with no connection (the web's
    /// `FamAudio.playStored`): every sample is in hand before the first, so
    /// it is a replay to the loading screen (`onReady(true)`), complete from
    /// the start, and nothing is kept again.
    func playStored(title: String, picture: URL? = nil, rate: Double, stored: [Float],
                    holdStart: Bool = false) {
        stopStream()
        guard !stored.isEmpty, rate > 0 else {
            fail("that episode isn\u{2019}t on this phone")
            return
        }
        complete = false
        episodeID = ""
        self.holdStart = holdStart
        gated = false
        cacheHit = true
        keepable = false
        fromDevice = true
        generation += 1
        self.title = title
        self.picture = picture
        problem = ""
        request = nil
        samples = []; bytesTaken = 0; skipBytes = 0; carry = Data()
        epoch += 1; base = 0; next = 0; outstanding = 0; lastFrame = 0
        streamDone = false; started = false; retries = 0
        refusedResponse = nil; refusalBody = Data()
        refusal = nil; failureWords = ""
        position = 0; arrived = 0
        listenedBefore = listened
        guard activateSession() else { return }
        askedAt = Date()
        phase = .connecting
        log("playing \"\(title)\" from this phone")
        if rate != sampleRate { configure(rate: rate) }
        samples = stored
        bytesTaken = stored.count * 2
        arrived = Double(stored.count) / sampleRate
        expected = arrived
        streamDone = true
        complete = true
        startTicker()
        feed()
        nowPlaying()
    }

    /// The whole episode as it arrived, for the offline shelf: only once
    /// every sample is here, only when the server marked it keepable, never
    /// for a device replay. A copy the player never changes under it.
    func wholeEpisode() -> (rate: Double, samples: [Float])? {
        guard streamDone, keepable, !fromDevice, !samples.isEmpty else { return nil }
        return (rate: sampleRate, samples: samples)
    }

    func pause() {
        guard phase == .playing || phase == .buffering else { return }
        hold()
        phase = .paused
        log("paused at \(fmt(position))")
        nowPlaying()
    }

    func resume() {
        guard phase == .paused else { return }
        guard activateSession(), startEngine() else { return }
        refill(from: lastFrame, play: true)
        phase = outstanding > 0 ? .playing : .buffering
        if ticker == nil { startTicker() }
        log("resumed at \(fmt(position))")
        nowPlaying()
        finishIfDone()
    }

    func toggle() {
        switch phase {
        case .paused: resume()
        case .finished:
            // Play again from the top.
            seek(to: 0)
            resume()
        default: pause()
        }
    }

    /// The draggable bar: anywhere in what has arrived, never past it.
    func seek(to seconds: Double) {
        guard started else { return }
        let limit = Int(seekLimit * sampleRate)
        let frame = min(max(0, Int(seconds * sampleRate)), max(limit, 0), samples.count)
        let playing = phase == .playing || phase == .buffering
        if phase == .finished { phase = .paused }
        lastFrame = frame
        position = Double(frame) / sampleRate
        if playing {
            refill(from: frame, play: true)
        }
        nowPlaying()
    }

    /// ±15 s. Returns false when a forward skip ran into the end of what
    /// has been written (the web's "Caught up with the writing").
    @discardableResult
    func skip(by seconds: Double) -> Bool {
        let now = playing ? Double(heardFrame()) / sampleRate : position
        let target = min(now + seconds, seekLimit)
        seek(to: target)
        return !(seconds > 0 && target - now < seconds - 1 && !complete)
    }

    /// The furthest a seek may go: all of it once complete, else 2 s short
    /// of what has arrived (`TAIL_MARGIN`), so playback never runs dry.
    var seekLimit: Double { complete ? arrived : max(0, arrived - 2.0) }

    /// Speed (0.5x-2x), saved on the phone like the web's `fam.prefs.speed`.
    func setRate(_ value: Double) {
        rate = value
        UserDefaults.standard.set(value, forKey: "speed")
        applyRate()
        nowPlaying()
    }

    func setKeepsPitch(_ on: Bool) {
        keepsPitch = on
        UserDefaults.standard.set(on, forKey: "pitch_lock")
        applyRate()
    }

    private func applyRate() {
        timePitch.rate = Float(rate)
        // Pitch kept: no shift. Not kept: shift with the speed, as tape does.
        timePitch.pitch = keepsPitch ? 0 : Float(1200 * log2(rate))
        timePitch.bypass = rate == 1 && keepsPitch
    }

    /// Whether anything is loaded at all (the mini player shows).
    var isLoaded: Bool { phase != .idle }

    var playing: Bool { phase == .playing || phase == .buffering }

    func stop() {
        stopStream()
        generation += 1
        epoch += 1
        phase = .idle
        nowPlaying()
    }

    func clearLog() { lines = [] }

    // MARK: - The stream

    private func connect() {
        guard let request else { return }
        let task = session.dataTask(with: request)
        self.task = task
        task.resume()
    }

    private func stopStream() {
        task?.cancel()
        task = nil
        node.stop()
        ticker?.invalidate()
        ticker = nil
        if started { listened = listenedBefore + position }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard dataTask == task else { completionHandler(.cancel); return }
        let http = response as? HTTPURLResponse
        if let http, !(200..<300).contains(http.statusCode) {
            refusedResponse = http
            completionHandler(.allow)
            return
        }
        if http?.value(forHTTPHeaderField: "X-FAM-Audio-Format") == "opus" {
            // Asked for pcm; the spike does not decode Opus (that is stage 3).
            completionHandler(.cancel)
            fail("the server sent Opus to a request for PCM")
            return
        }
        let rate = Double(http?.value(forHTTPHeaderField: "X-Sample-Rate") ?? "") ?? 22050
        if bytesTaken == 0 && rate != sampleRate { configure(rate: rate) }
        let ms = Int(Date().timeIntervalSince(askedAt) * 1000)
        let cache = http?.value(forHTTPHeaderField: "X-FAM-Cache") ?? "-"
        if retries == 0 {
            cacheHit = cache == "hit"
            episodeID = http?.value(forHTTPHeaderField: "X-FAM-Episode") ?? ""
            keepable = http?.value(forHTTPHeaderField: "X-FAM-Keepable") == "1"
        }
        log(retries == 0
            ? "first byte after \(ms) ms (\(Int(rate)) Hz, cache \(cache))"
            : "reconnected; skipping the \(bytesTaken) bytes already here")
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard dataTask == task else { return }
        if refusedResponse != nil {
            refusalBody.append(data)
            return
        }
        take(data)
    }

    func urlSession(_ session: URLSession, task finished: URLSessionTask,
                    didCompleteWithError error: Error?) {
        guard finished == task else { return }
        task = nil
        if let response = refusedResponse {
            refusal = try? FAMAPI.decoder.decode(AudioRefusal.self, from: refusalBody)
            do {
                try FAMAPI.check(response, refusalBody)
                fail("the server refused the episode")
            } catch let error as FAMError {
                if case let .refused(_, message) = error { failureWords = message }
                // The handler may start the next episode at once (a 409
                // asked again by its question, a playlist's resume): that
                // new stream must not be torn down here.
                let asked = generation
                if onRefused?(error) == true {
                    if asked == generation {
                        stopStream()
                        epoch += 1
                        phase = .idle
                        nowPlaying()
                    }
                } else {
                    fail(error.localizedDescription)
                }
            } catch {
                fail(error.localizedDescription)
            }
            return
        }
        if let error = error as NSError? {
            if error.code == NSURLErrorCancelled { return }
            if retries < 3 {
                // Wi-Fi to cellular, a tunnel: ask again and drop what we
                // already have. A replay is the same bytes every time; if a
                // reconnect ever sounds wrong, that assumption is what broke.
                retries += 1
                let wait = pow(2.0, Double(retries))
                log("stream broke at \(fmt(arrived)) arrived (\(error.localizedDescription)); reconnecting in \(Int(wait))s, try \(retries) of 3")
                skipBytes = bytesTaken
                carry = Data()
                let gen = generation
                DispatchQueue.main.asyncAfter(deadline: .now() + wait) { [weak self] in
                    guard let self, gen == self.generation, self.task == nil else { return }
                    self.connect()
                }
                return
            }
            fail("the stream broke and three reconnects failed: \(error.localizedDescription)",
                 network: error.domain == NSURLErrorDomain)
            return
        }
        if bytesTaken == 0 {
            fail("the server sent no audio")
            return
        }
        streamDone = true
        complete = true
        feed()
        log("all \(fmt(arrived)) arrived")
        nowPlaying()
        // Told before the ending is checked: an episode that finishes on
        // this very sample has still arrived whole.
        let asked = generation
        onArrived?()
        guard asked == generation else { return }
        finishIfDone()
    }

    /// Bytes in, Float samples kept, the node fed.
    private func take(_ data: Data) {
        var bytes = carry
        bytes.append(data)
        if skipBytes > 0 {
            let drop = min(skipBytes, bytes.count)
            bytes = Data(bytes.dropFirst(drop))
            skipBytes -= drop
        }
        let usable = bytes.count - bytes.count % 2
        bytes.withUnsafeBytes { raw in
            samples.reserveCapacity(samples.count + usable / 2)
            for i in stride(from: 0, to: usable, by: 2) {
                let value = Int16(littleEndian: raw.loadUnaligned(fromByteOffset: i, as: Int16.self))
                samples.append(Float(value) / 32768)
            }
        }
        carry = Data(bytes.dropFirst(usable))
        bytesTaken += usable
        arrived = Double(samples.count) / sampleRate
        if phase != .paused { feed() }
    }

    // MARK: - Feeding the node

    /// Hand the node every whole quarter-second that has arrived (and the
    /// tail, once the stream is done), then start if half a second is in hand.
    private func feed() {
        guard let format else { return }
        let chunk = max(1, Int(sampleRate / 4))
        while samples.count - next >= chunk || (streamDone && next < samples.count) {
            let n = min(chunk, samples.count - next)
            guard let buffer = AVAudioPCMBuffer(pcmFormat: format,
                                                frameCapacity: AVAudioFrameCount(n)) else { return }
            buffer.frameLength = AVAudioFrameCount(n)
            samples.withUnsafeBufferPointer { src in
                buffer.floatChannelData![0].update(from: src.baseAddress! + next, count: n)
            }
            next += n
            outstanding += 1
            let filling = epoch
            node.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                DispatchQueue.main.async { self?.played(filling) }
            }
        }
        if phase == .buffering && outstanding > 0 {
            phase = .playing
            log("audio caught up")
        }
        let enough = Double(next - base) >= sampleRate * 0.5
        if !started && (enough || (streamDone && next > base)) { begin() }
    }

    /// Empty the node and fill it again from `frame`. The only way the node
    /// is ever emptied, so a stop can never be mistaken for an ending.
    private func refill(from frame: Int, play: Bool) {
        epoch += 1
        node.stop()
        outstanding = 0
        base = min(max(0, frame), samples.count)
        next = base
        lastFrame = base
        feed()
        if play && started { node.play() }
    }

    /// Stop the node where it is and remember where that was.
    private func hold() {
        lastFrame = heardFrame()
        position = Double(lastFrame) / sampleRate
        node.pause()
    }

    private func begin() {
        guard startEngine() else { return }
        started = true
        if holdStart {
            // Ready, and held: the loading screen says when.
            gated = true
            log("first audio ready after \(Int(Date().timeIntervalSince(askedAt) * 1000)) ms; held for the loading screen")
            onReady?(cacheHit)
            return
        }
        node.play()
        phase = .playing
        log("first audio after \(Int(Date().timeIntervalSince(askedAt) * 1000)) ms")
        nowPlaying()
    }

    /// The loading screen finished: start the audio that was held.
    func release() {
        guard gated else { return }
        gated = false
        holdStart = false
        guard activateSession(), startEngine() else { return }
        node.play()
        phase = outstanding > 0 ? .playing : .buffering
        log("released after \(Int(Date().timeIntervalSince(askedAt) * 1000)) ms")
        nowPlaying()
        finishIfDone()
    }

    private func played(_ filling: Int) {
        guard filling == epoch else { return }
        outstanding -= 1
        guard outstanding == 0, phase == .playing || phase == .buffering else { return }
        if reachedEnd() {
            finishIfDone()
        } else if !streamDone && next == samples.count {
            phase = .buffering
            log("ran out of audio at \(fmt(position)); waiting for the stream")
        } else {
            // The queue emptied with audio still to play: iOS flushed it (a
            // call or a route change, whose notification has not arrived
            // yet). Never an ending. Give the notification a moment to
            // pause us; if nothing does, refill from where the listener was.
            log("the player was emptied at \(fmt(Double(lastFrame) / sampleRate)), not at the end")
            let filling = epoch
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
                guard let self, filling == self.epoch, self.outstanding == 0,
                      self.phase == .playing || self.phase == .buffering else { return }
                guard self.startEngine() else { return }
                self.log("refilling from \(self.fmt(Double(self.lastFrame) / self.sampleRate))")
                self.refill(from: self.lastFrame, play: true)
            }
        }
    }

    /// Whether the node's own clock has played to the last sample. A queue
    /// that iOS emptied reads false here: the engine is stopped, or the clock
    /// stands short of the end.
    private func reachedEnd() -> Bool {
        guard streamDone, next == samples.count, engine.isRunning, node.isPlaying else { return false }
        return heardFrame() >= samples.count - Int(sampleRate / 4)
    }

    private func finishIfDone() {
        guard streamDone, started, outstanding == 0, next == samples.count,
              phase == .playing || phase == .buffering else { return }
        // Only an ending if the clock got there; resume() calls this for an
        // episode that was paused within its last buffer.
        guard reachedEnd() || lastFrame >= samples.count - Int(sampleRate / 4) else { return }
        lastFrame = samples.count
        position = Double(samples.count) / sampleRate
        listened = listenedBefore + position
        ticker?.invalidate()
        ticker = nil
        phase = .finished
        log("finished: \(fmt(position)) heard; \(fmt(listened)) this session")
        nowPlaying()
        onFinished?()
    }

    private func fail(_ why: String, network: Bool = false) {
        lastFailureWasNetwork = network
        stopStream()
        epoch += 1
        phase = .failed
        problem = why
        gated = false
        log("FAILED: \(why)")
        nowPlaying()
        onFailed?()
    }

    // MARK: - Engine and session

    private func configure(rate: Double) {
        sampleRate = rate
        format = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 1)
        engine.disconnectNodeOutput(node)
        engine.disconnectNodeOutput(timePitch)
        engine.connect(node, to: timePitch, format: format)
        engine.connect(timePitch, to: engine.mainMixerNode, format: format)
    }

    private func startEngine() -> Bool {
        guard !engine.isRunning else { return true }
        do {
            try engine.start()
            return true
        } catch {
            fail("the audio engine would not start: \(error.localizedDescription)")
            return false
        }
    }

    @discardableResult
    private func activateSession() -> Bool {
        let audio = AVAudioSession.sharedInstance()
        do {
            try audio.setCategory(.playback, mode: .spokenAudio)
            try audio.setActive(true)
            return true
        } catch {
            fail("the audio session would not start: \(error.localizedDescription)")
            return false
        }
    }

    private func observe() {
        let center = NotificationCenter.default
        func on(_ name: Notification.Name, _ object: Any? = nil,
                _ body: @escaping (Notification) -> Void) {
            observers.append(center.addObserver(forName: name, object: object,
                                                queue: .main, using: body))
        }
        on(AVAudioSession.interruptionNotification) { [weak self] note in
            self?.interrupted(note)
        }
        on(AVAudioSession.routeChangeNotification) { [weak self] note in
            self?.routeChanged(note)
        }
        on(.AVAudioEngineConfigurationChange, engine) { [weak self] _ in
            self?.engineChanged()
        }
        on(AVAudioSession.mediaServicesWereResetNotification) { [weak self] _ in
            self?.fail("iOS reset its media services; play the episode again")
        }
        on(UIApplication.didEnterBackgroundNotification) { [weak self] _ in self?.log("app went to the background") }
        on(UIApplication.didBecomeActiveNotification) { [weak self] _ in self?.log("app came back") }
        on(UIApplication.protectedDataWillBecomeUnavailableNotification) { [weak self] _ in self?.log("phone locked") }
        on(UIApplication.protectedDataDidBecomeAvailableNotification) { [weak self] _ in self?.log("phone unlocked") }
    }

    /// A new output (AirPods in, a car, a call taking the audio) stops the
    /// engine and empties the node. Start it again and refill from where the
    /// listener was - never from what happened to be queued.
    private func engineChanged() {
        let wasPlaying = phase == .playing || phase == .buffering
        log("audio output changed at \(fmt(Double(lastFrame) / sampleRate)); restarting the engine")
        guard wasPlaying else { return }  // resume() refills when it is time
        guard startEngine() else { return }
        refill(from: lastFrame, play: true)
    }

    private func interrupted(_ note: Notification) {
        guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
        switch type {
        case .began:
            if phase == .playing || phase == .buffering {
                hold()
                phase = .paused
                resumeAfterInterruption = true
                nowPlaying()
            }
            log("interrupted at \(fmt(position)) (a call, an alarm, Siri)")
        case .ended:
            let options = AVAudioSession.InterruptionOptions(
                rawValue: note.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0)
            let resumes = options.contains(.shouldResume)
            log("interruption over; iOS says \(resumes ? "resume" : "stay paused")")
            if resumeAfterInterruption && resumes { resume() }
            resumeAfterInterruption = false
        @unknown default:
            break
        }
    }

    private func routeChanged(_ note: Notification) {
        guard let raw = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
              let reason = AVAudioSession.RouteChangeReason(rawValue: raw) else { return }
        let output = AVAudioSession.sharedInstance().currentRoute.outputs.first?.portName ?? "nothing"
        switch reason {
        case .oldDeviceUnavailable:
            // Headphones out: every audio app pauses here, so must we.
            log("headphones disconnected (now on \(output))")
            pause()
        case .newDeviceAvailable:
            log("new output: \(output)")
        default:
            break
        }
    }

    // MARK: - Lock screen

    private func remoteCommands() {
        let commands = MPRemoteCommandCenter.shared()
        commands.playCommand.addTarget { [weak self] _ in self?.resume(); return .success }
        commands.pauseCommand.addTarget { [weak self] _ in self?.pause(); return .success }
        commands.togglePlayPauseCommand.addTarget { [weak self] _ in self?.toggle(); return .success }
        commands.skipForwardCommand.preferredIntervals = [15]
        commands.skipForwardCommand.addTarget { [weak self] _ in self?.skip(by: 15); return .success }
        commands.skipBackwardCommand.preferredIntervals = [15]
        commands.skipBackwardCommand.addTarget { [weak self] _ in self?.skip(by: -15); return .success }
        commands.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let event = event as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
            self?.seek(to: event.positionTime)
            return .success
        }
    }

    private func nowPlaying() {
        guard phase != .idle else {
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
            return
        }
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: title,
            MPMediaItemPropertyArtist: "FAM",
            MPNowPlayingInfoPropertyElapsedPlaybackTime: position,
            MPNowPlayingInfoPropertyPlaybackRate: phase == .playing ? rate : 0.0,
        ]
        info[MPMediaItemPropertyPlaybackDuration] = total
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    // MARK: - Clock and log

    private func startTicker() {
        ticker?.invalidate()
        var beats = 0
        ticker = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            guard let self else { return }
            if self.playing {
                self.lastFrame = self.heardFrame()
            }
            self.position = Double(self.lastFrame) / self.sampleRate
            self.listened = self.listenedBefore + self.position
            beats += 1
            if beats % 10 == 0 { self.nowPlaying() }
        }
    }

    /// Where the listener is, in frames: where this filling began plus how
    /// far the node has played into it - never past what it was given (its
    /// clock runs on through silence when the stream falls behind). When
    /// the node has no clock (stopped, or the engine just changed), the last
    /// reading.
    private func heardFrame() -> Int {
        guard started, engine.isRunning, let now = node.lastRenderTime,
              let time = node.playerTime(forNodeTime: now) else { return lastFrame }
        let played = Int(Double(time.sampleTime) * sampleRate / time.sampleRate)
        return base + max(0, min(played, next - base))
    }

    private func log(_ text: String) {
        let stamp = Self.clock.string(from: Date())
        let where_ = UIApplication.shared.applicationState == .active ? "fg" : "bg"
        lines.append("\(stamp) [\(where_)] \(text)")
        if lines.count > 400 { lines.removeFirst(lines.count - 400) }
    }

    private static let clock: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    private func fmt(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}
