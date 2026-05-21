import SwiftUI

@MainActor
final class CallViewModel: ObservableObject {
    @Published var status: CallStatus = .idle
    @Published var messages: [VoiceMessage] = [
        VoiceMessage(speaker: .system, text: "Tap Call Jeeves to start a Hermes voice session.")
    ]

    private let api = VoiceSessionAPI()
    private let recorder = AudioRecorder()
    private let player = AudioPlayer()
    private var activeSession: VoiceSession?

    var isBusy: Bool {
        if case .starting = status { return true }
        if case .ending = status { return true }
        return false
    }

    var buttonTitle: String { status.isCalling ? "Hang Up" : "Call Jeeves" }

    func toggleCall() {
        if status.isCalling {
            Task { await hangUp() }
        } else {
            Task { await startCall() }
        }
    }

    private func startCall() async {
        status = .starting
        do {
            let session = try await api.createSession()
            activeSession = session
            status = .calling(sessionID: session.id)
            messages.append(VoiceMessage(speaker: .system, text: "Session \(session.id) started."))
            try await recorder.start()
            messages.append(VoiceMessage(speaker: .system, text: "Recording started. Tap Hang Up to end the MVP session."))
        } catch {
            status = .failed(error.localizedDescription)
            messages.append(VoiceMessage(speaker: .system, text: error.localizedDescription))
        }
    }

    private func hangUp() async {
        status = .ending
        let recordingURL = recorder.stop()
        let session = activeSession
        activeSession = nil

        do {
            if let session, let recordingURL {
                let turn = try await api.sendTurn(sessionID: session.id, audioFileURL: recordingURL)
                if let transcript = turn.transcript, !transcript.isEmpty {
                    messages.append(VoiceMessage(speaker: .user, text: transcript))
                }
                if let reply = turn.reply, !reply.isEmpty {
                    messages.append(VoiceMessage(speaker: .hermes, text: reply))
                }
                if let audioBase64 = turn.audio?.base64,
                   let audioData = Data(base64Encoded: audioBase64) {
                    try player.play(data: audioData)
                } else if let audioURL = turn.audio?.audioURL ?? turn.audioURL {
                    try await player.play(remoteURL: audioURL)
                }
                try await api.endSession(sessionID: session.id)
            }
            status = .idle
            messages.append(VoiceMessage(speaker: .system, text: "Call ended."))
        } catch {
            status = .failed(error.localizedDescription)
            messages.append(VoiceMessage(speaker: .system, text: error.localizedDescription))
        }
    }
}

struct ContentView: View {
    @StateObject private var viewModel = CallViewModel()

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                Text(viewModel.status.text)
                    .font(.headline)
                    .foregroundStyle(viewModel.status.isCalling ? .green : .secondary)

                Button(action: viewModel.toggleCall) {
                    Text(viewModel.buttonTitle)
                        .font(.title2.weight(.semibold))
                        .frame(width: 220, height: 220)
                        .background(viewModel.status.isCalling ? Color.red : Color.accentColor)
                        .foregroundStyle(.white)
                        .clipShape(Circle())
                        .shadow(radius: 8)
                }
                .disabled(viewModel.isBusy)
                .accessibilityIdentifier("callJeevesButton")

                List(viewModel.messages) { message in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(message.speaker.rawValue)
                            .font(.caption.weight(.bold))
                            .foregroundStyle(.secondary)
                        Text(message.text)
                            .font(.body)
                    }
                    .padding(.vertical, 4)
                }
            }
            .padding()
            .navigationTitle("Hermes Voice")
        }
    }
}

#Preview {
    ContentView()
}
