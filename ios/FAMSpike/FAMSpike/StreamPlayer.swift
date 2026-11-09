import AVFoundation
import MediaPlayer
import UIKit

/// Plays one episode from `GET /api/v1/audio?fmt=pcm` while it arrives, and
/// keeps playing with the phone locked. This is the whole spike: if this
/// class works on a phone, the rest of the app is ordinary work
/// (IOS_APP.md stage 2).
///
/// What the server sends with `fmt=pcm`: bare 16-bit little-endian mono
/// samples at the rate in `X-Sample-Rate` (22050 unless it says otherwise),
/// no header, until the episode ends. `static/fam-audio.js` is the reference
/// player; this does what its PCM path does: buffer as it arrives, start
/// once half a second is in hand, never play past what has arrived.
///
/// Everything here runs on the main queue - the URLSession delegate queue is
/// `.main`, and every notification is observed on `.main` - so there is no
/// locking. At 44 KB a second the main thread does not notice.
final class StreamPlayer: NSObject, ObservableObject, URLSessionDataDelegate {
    enum Phase: String {
        case idle, connecting, buffering, playing, paused, finished, failed
    }

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var title = ""
    /// Seconds of this episode heard so far.
    @Published private(set) var position: Double = 0
    /// Seconds of this episode that have arrived.
    @Published private(set) var arrived: Double = 0
    /// Seconds heard this session, every episode together: the number the
    /// ten-minute locked test is read from.
    @Published private(set) var listened: Double = 0
    @Published private(set) var lines: [String] = []

    /// Called when an episode has played to its end.
    var onFinished: (() -> Void)?

    private let engine = AVAudioEngine()
    private let node = AVAudioPlayerNode()
    private var format: AVAudioFormat?
    private lazy var session = URLSession(configuration: .default, delegate: self,
                                          delegateQueue: .main)

    private var task: URLSessionDataTask?
    private var request: URLRequest?
    /// Bumped on every new episode, so callbacks from the last one are ignored.
    private var generation = 0

    private var sampleRate: Double = 22050
    /// Bytes of samples taken in from this episode's stream.
    private var bytesTaken = 0
    /// Bytes to drop at the front of a reconnected stream (what was already taken).
    private var skip = 0
    /// A byte of a sample that straddled two chunks.
    private var carry = Data()
    private var pending: [Float] = []
    private var scheduledFrames: AVAudioFramePosition = 0
    private var outstanding = 0
    private var streamDone = false
    private var started = false
    private var retries = 0
    private var refusedStatus: Int?
    private var refusalBody = Data()
    private var askedAt = Date()
    private var listenedBefore: Double = 0
    private var resumeAfterInterruption = false
    private var ticker: Timer?
    private var observers: [NSObjectProtocol] = []

    override init() {
        super.init()
        engine.attach(node)
        configure(rate: sampleRate)
        observe()
        remoteCommands()
    }

    // MARK: - Control

    func play(title: String, request: URLRequest) {
        stopStream()
        generation += 1
        self.title = title
        self.request = request
        bytesTaken = 0; skip = 0; carry = Data(); pending = []
        scheduledFrames = 0; outstanding = 0
        streamDone = false; started = false; retries = 0
        refusedStatus = nil; refusalBody = Data()
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

    func pause() {
        guard phase == .playing || phase == .buffering else { return }
        node.pause()
        phase = .paused
        log("paused")
        nowPlaying()
    }

    func resume() {
        guard phase == .paused else { return }
        guard activateSession() else { return }
        if !engine.isRunning {
            do { try engine.start() } catch { fail("the audio engine would not restart: \(error.localizedDescription)"); return }
        }
        node.play()
        phase = outstanding > 0 ? .playing : .buffering
        log("resumed")
        nowPlaying()
    }

    func toggle() { phase == .paused ? resume() : pause() }

    func stop() {
        stopStream()
        generation += 1
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
            refusedStatus = http.statusCode
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
        log(retries == 0
            ? "first byte after \(ms) ms (\(Int(rate)) Hz, cache \(cache))"
            : "reconnected; skipping the \(bytesTaken) bytes already here")
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard dataTask == task else { return }
        if refusedStatus != nil {
            refusalBody.append(data)
            return
        }
        take(data)
    }

    func urlSession(_ session: URLSession, task finished: URLSessionTask,
                    didCompleteWithError error: Error?) {
        guard finished == task else { return }
        task = nil
        if let status = refusedStatus {
            fail(SpikeError.refused(status: status,
                                    message: SpikeAPI.message(in: refusalBody)).localizedDescription)
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
                skip = bytesTaken
                carry = Data()
                let gen = generation
                DispatchQueue.main.asyncAfter(deadline: .now() + wait) { [weak self] in
                    guard let self, gen == self.generation, self.task == nil else { return }
                    self.connect()
                }
                return
            }
            fail("the stream broke and three reconnects failed: \(error.localizedDescription)")
            return
        }
        if bytesTaken == 0 {
            fail("the server sent no audio")
            return
        }
        streamDone = true
        flush(all: true)
        log("all \(fmt(arrived)) arrived")
        nowPlaying()
        finishIfDone()
    }

    /// Bytes in, Float samples out, scheduled in quarter-second buffers.
    private func take(_ data: Data) {
        var bytes = carry
        bytes.append(data)
        if skip > 0 {
            let drop = min(skip, bytes.count)
            bytes = Data(bytes.dropFirst(drop))
            skip -= drop
        }
        let usable = bytes.count - bytes.count % 2
        bytes.withUnsafeBytes { raw in
            pending.reserveCapacity(pending.count + usable / 2)
            for i in stride(from: 0, to: usable, by: 2) {
                let value = Int16(littleEndian: raw.loadUnaligned(fromByteOffset: i, as: Int16.self))
                pending.append(Float(value) / 32768)
            }
        }
        carry = Data(bytes.dropFirst(usable))
        bytesTaken += usable
        arrived = Double(bytesTaken / 2) / sampleRate
        flush(all: false)
    }

    private func flush(all: Bool) {
        guard let format else { return }
        let chunk = max(1, Int(sampleRate / 4))
        while pending.count >= chunk || (all && !pending.isEmpty) {
            let n = min(chunk, pending.count)
            guard let buffer = AVAudioPCMBuffer(pcmFormat: format,
                                                frameCapacity: AVAudioFrameCount(n)) else { return }
            buffer.frameLength = AVAudioFrameCount(n)
            pending.withUnsafeBufferPointer { src in
                buffer.floatChannelData![0].update(from: src.baseAddress!, count: n)
            }
            pending.removeFirst(n)
            outstanding += 1
            scheduledFrames += AVAudioFramePosition(n)
            let gen = generation
            node.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                DispatchQueue.main.async { self?.played(gen) }
            }
        }
        if phase == .buffering && outstanding > 0 {
            phase = .playing
            log("audio caught up")
        }
        let enough = Double(scheduledFrames) >= sampleRate * 0.5
        if !started && (enough || (streamDone && scheduledFrames > 0)) { begin() }
    }

    private func begin() {
        if !engine.isRunning {
            do { try engine.start() } catch { fail("the audio engine would not start: \(error.localizedDescription)"); return }
        }
        node.play()
        started = true
        phase = .playing
        log("first audio after \(Int(Date().timeIntervalSince(askedAt) * 1000)) ms")
        nowPlaying()
    }

    private func played(_ gen: Int) {
        guard gen == generation else { return }
        outstanding -= 1
        if outstanding == 0 && !streamDone && phase == .playing {
            phase = .buffering
            log("ran out of audio at \(fmt(position)); waiting for the stream")
        }
        finishIfDone()
    }

    private func finishIfDone() {
        guard streamDone, outstanding == 0, started, phase != .finished else { return }
        position = Double(scheduledFrames) / sampleRate
        listened = listenedBefore + position
        ticker?.invalidate()
        ticker = nil
        phase = .finished
        log("finished: \(fmt(position)) heard; \(fmt(listened)) this session")
        nowPlaying()
        onFinished?()
    }

    private func fail(_ why: String) {
        stopStream()
        phase = .failed
        log("FAILED: \(why)")
        nowPlaying()
    }

    // MARK: - Engine and session

    private func configure(rate: Double) {
        sampleRate = rate
        format = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 1)
        engine.disconnectNodeOutput(node)
        engine.connect(node, to: engine.mainMixerNode, format: format)
        engine.prepare()
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
            guard let self else { return }
            // A new output (AirPods in, a car) stops the engine. Start it
            // again and carry on from the same buffers.
            let wasPlaying = self.phase == .playing || self.phase == .buffering
            self.log("audio output changed; restarting the engine")
            do { try self.engine.start() } catch { self.fail("the engine would not restart: \(error.localizedDescription)"); return }
            if wasPlaying { self.node.play() }
        }
        on(AVAudioSession.mediaServicesWereResetNotification) { [weak self] _ in
            self?.fail("iOS reset its media services; play the episode again")
        }
        on(UIApplication.didEnterBackgroundNotification) { [weak self] _ in self?.log("app went to the background") }
        on(UIApplication.willEnterForegroundNotification) { [weak self] _ in self?.log("app came back") }
        on(UIApplication.protectedDataWillBecomeUnavailableNotification) { [weak self] _ in self?.log("phone locked") }
        on(UIApplication.protectedDataDidBecomeAvailableNotification) { [weak self] _ in self?.log("phone unlocked") }
    }

    private func interrupted(_ note: Notification) {
        guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
        switch type {
        case .began:
            log("interrupted (a call, an alarm, Siri)")
            if phase == .playing || phase == .buffering {
                node.pause()
                phase = .paused
                resumeAfterInterruption = true
                nowPlaying()
            }
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
            log("headphones disconnected; pausing (now on \(output))")
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
        commands.skipForwardCommand.isEnabled = false
        commands.skipBackwardCommand.isEnabled = false
        commands.changePlaybackPositionCommand.isEnabled = false
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
            MPNowPlayingInfoPropertyPlaybackRate: phase == .playing ? 1.0 : 0.0,
        ]
        if streamDone {
            info[MPMediaItemPropertyPlaybackDuration] = arrived
        }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    // MARK: - Clock and log

    private func startTicker() {
        ticker?.invalidate()
        var beats = 0
        ticker = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            guard let self else { return }
            self.position = self.heard()
            self.listened = self.listenedBefore + self.position
            beats += 1
            if beats % 10 == 0 { self.nowPlaying() }
        }
    }

    /// Where the player is, never past what was scheduled: the node's clock
    /// keeps running through silence when the stream falls behind.
    private func heard() -> Double {
        guard started, let now = node.lastRenderTime,
              let time = node.playerTime(forNodeTime: now) else { return position }
        let clock = Double(time.sampleTime) / time.sampleRate
        return max(0, min(clock, Double(scheduledFrames) / sampleRate))
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
