import AVFoundation
import Foundation

@MainActor
final class AudioRecorder: NSObject, ObservableObject, AVAudioRecorderDelegate {
    @Published private(set) var isRecording = false

    private var recorder: AVAudioRecorder?

    func start() async throws {
        let granted = await AVAudioApplication.requestRecordPermission()
        guard granted else { throw RecorderError.microphonePermissionDenied }

        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .spokenAudio, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try session.setActive(true)

        let url = Self.nextRecordingURL()
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 16_000,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.medium.rawValue
        ]
        let recorder = try AVAudioRecorder(url: url, settings: settings)
        recorder.delegate = self
        recorder.record()
        self.recorder = recorder
        isRecording = true
    }

    func stop() -> URL? {
        defer {
            recorder = nil
            isRecording = false
        }
        let url = recorder?.url
        recorder?.stop()
        return url
    }

    private static func nextRecordingURL() -> URL {
        FileManager.default.temporaryDirectory
            .appending(path: "hermes-turn-\(UUID().uuidString)")
            .appendingPathExtension("m4a")
    }
}

enum RecorderError: LocalizedError {
    case microphonePermissionDenied

    var errorDescription: String? {
        switch self {
        case .microphonePermissionDenied:
            return "Microphone permission is required to call Jeeves."
        }
    }
}
