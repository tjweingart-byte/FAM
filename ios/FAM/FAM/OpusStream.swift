import AVFoundation
import Foundation

// Opus on the iPhone (decision 3, §242, §252): what `static/fam-audio.js`
// reads, read here. Two shapes arrive.
//
// * **"fam-opus v1"**, the stream (`/api/audio?fmt=opus`, with
//   `X-FAM-Audio-Format: opus` and `X-FAM-Opus-Preskip`; `audio_codec.py`
//   is the other half):
//
//       repeated:  u16 big-endian length N > 0, then one Opus packet of N bytes
//       last:      u16 0, then u32 big-endian - the episode's length in
//                  samples at `X-Sample-Rate`, after the pre-skip
//
// * **an Ogg Opus file** in the bucket, for a replay asked with `link=1`
//   and answered with JSON (`X-FAM-Audio-Link: 1`): its pages are read as
//   they arrive, OpusHead's pre-skip taken, OpusTags skipped, and every
//   audio packet decoded exactly as the stream's are (`oggOpus`).
//
// Either way the player drops the pre-skip (stated in 48 kHz samples) and
// trims to the true length, so it holds the samples PCM would have carried:
// captions' `starts`, seeking, the arrived range and the offline shelf see
// what they always saw. Decoding is Apple's own Opus decoder
// (`kAudioFormatOpus`) behind `AVAudioConverter`; anything that fails here
// makes the player ask for PCM instead (`StreamPlayer`), so nothing stops
// playing.

/// Reads "fam-opus v1" off the stream as it arrives: whole packets out,
/// and the end marker's length once it lands. A stream cut short has no
/// marker, which the player treats as the end of what arrived.
struct FamOpusFramer {
    private var pending: [UInt8] = []
    /// The episode's length in samples at the stream's rate, from the end
    /// marker; nil until it arrives.
    private(set) var finalLength: Int?

    mutating func push(_ data: Data) -> [Data] {
        if finalLength != nil { return [] }
        pending.append(contentsOf: data)
        var packets: [Data] = []
        var at = 0
        while at + 2 <= pending.count {
            let size = (Int(pending[at]) << 8) | Int(pending[at + 1])
            if size == 0 {
                if at + 6 > pending.count { break }
                let b0 = Int(pending[at + 2])
                let b1 = Int(pending[at + 3])
                let b2 = Int(pending[at + 4])
                let b3 = Int(pending[at + 5])
                finalLength = (b0 << 24) | (b1 << 16) | (b2 << 8) | b3
                at += 6
                break
            }
            if at + 2 + size > pending.count { break }
            packets.append(Data(pending[(at + 2)..<(at + 2 + size)]))
            at += 2 + size
        }
        if at > 0 { pending.removeFirst(at) }
        return packets
    }
}

/// An Ogg Opus file read as it arrives (`oggOpus` on the web): pages in,
/// audio packets out, OpusHead's pre-skip kept. A packet may run across
/// pages (a segment of 255 continues it).
struct OggOpusReader {
    enum Failure: Error {
        case notOgg
        case notOpus
    }

    private var pending: [UInt8] = []
    private var packet: [UInt8] = []
    private var heads = 0
    /// The encoder's start-up delay, in 48 kHz samples (OpusHead).
    private(set) var preskip = 0
    /// OpusHead and OpusTags have both been read.
    var headerDone: Bool { heads >= 2 }

    mutating func push(_ data: Data) throws -> [Data] {
        pending.append(contentsOf: data)
        var out: [Data] = []
        var at = 0
        while pending.count - at >= 27 {
            let isPage = pending[at] == 0x4F && pending[at + 1] == 0x67
                && pending[at + 2] == 0x67 && pending[at + 3] == 0x53
            guard isPage else { throw Failure.notOgg }
            let segments = Int(pending[at + 26])
            if pending.count - at < 27 + segments { break }
            var bodyLength = 0
            for i in 0..<segments { bodyLength += Int(pending[at + 27 + i]) }
            let total = 27 + segments + bodyLength
            if pending.count - at < total { break }
            var cursor = at + 27 + segments
            for i in 0..<segments {
                let length = Int(pending[at + 27 + i])
                if length > 0 { packet.append(contentsOf: pending[cursor..<(cursor + length)]) }
                cursor += length
                if length < 255 {
                    try finished(&out)
                    packet = []
                }
            }
            at += total
        }
        if at > 0 { pending.removeFirst(at) }
        return out
    }

    private mutating func finished(_ out: inout [Data]) throws {
        if heads == 0 {
            let magic: [UInt8] = [0x4F, 0x70, 0x75, 0x73, 0x48, 0x65, 0x61, 0x64]  // "OpusHead"
            guard packet.count >= 12, Array(packet[0..<8]) == magic else { throw Failure.notOpus }
            preskip = Int(packet[10]) | (Int(packet[11]) << 8)
            heads = 1
            return
        }
        if heads == 1 {
            // OpusTags: nothing the player needs.
            heads = 2
            return
        }
        if !packet.isEmpty { out.append(Data(packet)) }
    }
}

/// One Opus packet in, mono Float samples at the stream's rate out, by
/// Apple's decoder. Asked at the stream's own rate first; if the phone
/// will only decode at 48 kHz, decoded there and decimated - the voice
/// holds nothing above half the stream's rate, so every n-th sample loses
/// nothing (`pumpOpus` does the same).
final class FamOpusDecoder {
    enum Failure: Error {
        case buffer
        case converter
    }

    /// The stream's rate: the rate of every sample handed out.
    let rate: Double
    private let converter: AVAudioConverter
    private let inFormat: AVAudioFormat
    private let outFormat: AVAudioFormat
    private let step: Int

    private init(rate: Double, converter: AVAudioConverter, inFormat: AVAudioFormat,
                 outFormat: AVAudioFormat, step: Int) {
        self.rate = rate
        self.converter = converter
        self.inFormat = inFormat
        self.outFormat = outFormat
        self.step = step
    }

    /// A decoder for a stream at `rate`, or nil if this phone has none.
    static func make(rate: Double) -> FamOpusDecoder? {
        guard rate > 0 else { return nil }
        var rates: [Double] = [rate]
        if rate != 48000 { rates.append(48000) }
        for decodeRate in rates {
            let ratio = decodeRate / rate
            let step = Int(ratio.rounded())
            guard step >= 1, abs(ratio - Double(step)) < 0.0001 else { continue }
            var desc = AudioStreamBasicDescription()
            desc.mSampleRate = decodeRate
            desc.mFormatID = kAudioFormatOpus
            desc.mFormatFlags = 0
            desc.mBytesPerPacket = 0
            // 20 ms a packet, what the server's encoder writes.
            desc.mFramesPerPacket = UInt32(decodeRate / 50)
            desc.mBytesPerFrame = 0
            desc.mChannelsPerFrame = 1
            desc.mBitsPerChannel = 0
            desc.mReserved = 0
            guard let inFormat = AVAudioFormat(streamDescription: &desc) else { continue }
            guard let outFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: decodeRate,
                                                channels: 1, interleaved: false) else { continue }
            guard let converter = AVAudioConverter(from: inFormat, to: outFormat) else { continue }
            // The pre-skip is dropped by the player, as the web's is: the
            // converter must not drop anything of its own.
            converter.primeMethod = .none
            return FamOpusDecoder(rate: rate, converter: converter, inFormat: inFormat,
                                  outFormat: outFormat, step: step)
        }
        return nil
    }

    func decode(_ packet: Data) throws -> [Float] {
        guard !packet.isEmpty else { return [] }
        let compressed = AVAudioCompressedBuffer(format: inFormat, packetCapacity: 1,
                                                 maximumPacketSize: packet.count)
        packet.withUnsafeBytes { raw in
            if let source = raw.baseAddress {
                compressed.data.copyMemory(from: source, byteCount: packet.count)
            }
        }
        if let descriptions = compressed.packetDescriptions {
            descriptions[0] = AudioStreamPacketDescription(mStartOffset: 0, mVariableFramesInPacket: 0,
                                                           mDataByteSize: UInt32(packet.count))
        }
        compressed.packetCount = 1
        compressed.byteLength = UInt32(packet.count)
        // 120 ms at 48 kHz: the longest packet Opus has.
        guard let out = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: 5760) else {
            throw Failure.buffer
        }
        var given = false
        var problem: NSError?
        let status = converter.convert(to: out, error: &problem) { _, inputStatus in
            if given {
                inputStatus.pointee = .noDataNow
                return nil
            }
            given = true
            inputStatus.pointee = .haveData
            return compressed
        }
        if status == .error {
            if let problem { throw problem }
            throw Failure.converter
        }
        let count = Int(out.frameLength)
        guard count > 0, let channel = out.floatChannelData?[0] else { return [] }
        if step == 1 {
            return Array(UnsafeBufferPointer(start: channel, count: count))
        }
        var result: [Float] = []
        result.reserveCapacity(count / step + 1)
        var i = 0
        while i < count {
            result.append(channel[i])
            i += step
        }
        return result
    }
}
