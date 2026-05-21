import Foundation

struct VoiceMessage: Identifiable, Equatable {
    enum Speaker: String {
        case user = "You"
        case hermes = "Hermes"
        case system = "System"
    }

    let id = UUID()
    let speaker: Speaker
    let text: String
}

struct VoiceSession: Codable, Equatable {
    let id: String
}

struct CreateVoiceSessionResponse: Codable {
    let id: String
}

struct VoiceTurnAudio: Codable {
    let success: Bool?
    let base64: String?
    let mimeType: String?
    let audioURL: URL?

    enum CodingKeys: String, CodingKey {
        case success
        case base64
        case mimeType = "mime_type"
        case audioURL = "audio_url"
    }
}

struct CreateTurnResponse: Codable {
    let transcript: String?
    let reply: String?
    let audio: VoiceTurnAudio?
    let audioURL: URL?

    enum CodingKeys: String, CodingKey {
        case transcript
        case reply
        case audio
        case audioURL = "audio_url"
    }
}

enum CallStatus: Equatable {
    case idle
    case starting
    case calling(sessionID: String)
    case ending
    case failed(String)

    var text: String {
        switch self {
        case .idle:
            return "Ready"
        case .starting:
            return "Dialing Jeeves..."
        case .calling:
            return "Connected"
        case .ending:
            return "Hanging up..."
        case .failed(let message):
            return "Error: \(message)"
        }
    }

    var isCalling: Bool {
        if case .calling = self { return true }
        return false
    }
}
