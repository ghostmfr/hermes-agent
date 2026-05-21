import Foundation

struct VoiceSessionAPI {
    var baseURL: URL
    var urlSession: URLSession = .shared

    init(baseURL: URL = AppConfiguration.serverURL) {
        self.baseURL = baseURL
    }

    func createSession() async throws -> VoiceSession {
        let url = baseURL.appending(path: "/v1/voice/sessions")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        applyAuth(to: &request)
        request.httpBody = try JSONEncoder().encode(["client": "ios-swiftui-mvp"])

        let response: CreateVoiceSessionResponse = try await send(request)
        return VoiceSession(id: response.id)
    }

    func sendTurn(sessionID: String, audioFileURL: URL) async throws -> CreateTurnResponse {
        let url = baseURL.appending(path: "/v1/voice/sessions/\(sessionID)/turns")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        applyAuth(to: &request)

        let boundary = "Boundary-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = try multipartBody(audioFileURL: audioFileURL, boundary: boundary)

        return try await send(request)
    }

    func endSession(sessionID: String) async throws {
        let url = baseURL.appending(path: "/v1/voice/sessions/\(sessionID)")
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        applyAuth(to: &request)
        _ = try await sendRaw(request)
    }

    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let data = try await sendRaw(request)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func applyAuth(to request: inout URLRequest) {
        guard let apiKey = AppConfiguration.apiKey, !apiKey.isEmpty else { return }
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
    }

    private func sendRaw(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await urlSession.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return data
    }

    private func multipartBody(audioFileURL: URL, boundary: String) throws -> Data {
        var data = Data()
        let fileData = try Data(contentsOf: audioFileURL)
        data.append("--\(boundary)\r\n")
        data.append("Content-Disposition: form-data; name=\"audio\"; filename=\"turn.m4a\"\r\n")
        data.append("Content-Type: audio/mp4\r\n\r\n")
        data.append(fileData)
        data.append("\r\n")
        data.append("--\(boundary)\r\n")
        data.append("Content-Disposition: form-data; name=\"tts\"\r\n\r\n")
        data.append("true\r\n")
        data.append("--\(boundary)\r\n")
        data.append("Content-Disposition: form-data; name=\"include_audio_base64\"\r\n\r\n")
        data.append("true\r\n")
        data.append("--\(boundary)--\r\n")
        return data
    }
}

private extension Data {
    mutating func append(_ string: String) {
        append(Data(string.utf8))
    }
}

enum AppConfiguration {
    static var serverURL: URL {
        if let value = Bundle.main.object(forInfoDictionaryKey: "HERMES_SERVER_URL") as? String,
           let url = URL(string: value),
           !value.isEmpty {
            return url
        }
        return URL(string: "http://localhost:8000")!
    }

    static var apiKey: String? {
        guard let value = Bundle.main.object(forInfoDictionaryKey: "HERMES_API_KEY") as? String else {
            return nil
        }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty || trimmed == "$(HERMES_API_KEY)" {
            return nil
        }
        return trimmed
    }
}
