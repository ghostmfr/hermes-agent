import AVFoundation
import Foundation

@MainActor
final class AudioPlayer: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published private(set) var isPlaying = false

    private var player: AVAudioPlayer?

    func play(data: Data) throws {
        let player = try AVAudioPlayer(data: data)
        player.delegate = self
        player.prepareToPlay()
        player.play()
        self.player = player
        isPlaying = true
    }

    func play(localFileURL: URL) throws {
        let player = try AVAudioPlayer(contentsOf: localFileURL)
        player.delegate = self
        player.prepareToPlay()
        player.play()
        self.player = player
        isPlaying = true
    }

    func play(remoteURL: URL) async throws {
        let (downloadedURL, _) = try await URLSession.shared.download(from: remoteURL)
        try play(localFileURL: downloadedURL)
    }

    func stop() {
        player?.stop()
        player = nil
        isPlaying = false
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            self.player = nil
            self.isPlaying = false
        }
    }
}
