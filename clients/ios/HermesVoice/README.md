# Hermes Voice iOS MVP

Minimal SwiftUI iOS client skeleton for the phase-3-simple Hermes voice flow: tap one large button to dial Jeeves, start a backend voice session, record a single turn, hang up, and display transcript/reply messages.

This is intentionally **not** a real VoIP/CallKit implementation yet. It does not receive background calls, integrate PushKit, maintain a persistent audio stream, or present the native iOS call UI.

## Requirements

- Xcode 15+
- iOS 17+
- A Hermes backend exposing the provisional voice-session contract below

## Project

Open:

```bash
open clients/ios/HermesVoice/HermesVoice.xcodeproj
```

Build from the repo root or this directory:

```bash
xcodebuild \
  -project clients/ios/HermesVoice/HermesVoice.xcodeproj \
  -target HermesVoice \
  -sdk iphonesimulator \
  CODE_SIGNING_ALLOWED=NO \
  build
```

## Server URL configuration

The app reads `HERMES_SERVER_URL` from `Info.plist`, backed by the Xcode build setting of the same name. The default is:

```text
http://localhost:8000
```

For device testing, override `HERMES_SERVER_URL` in Xcode build settings or via an `.xcconfig` that is not committed if it contains user-specific values.

## Microphone permission

`HermesVoice/Info.plist` includes:

```xml
<key>NSMicrophoneUsageDescription</key>
<string>Hermes Voice records your speech so Jeeves can respond during a voice session.</string>
```

The app requests microphone access when the user starts a call.

## Provisional backend contract

The iOS skeleton uses simple endpoints until the backend voice slice stabilizes:

- `POST /v1/voice/sessions`
  - Request JSON: `{ "client": "ios-swiftui-mvp" }`
  - Response JSON: `{ "id": "session-id" }`
- `POST /v1/voice/sessions/{id}/turns`
  - Request: multipart form-data with an `audio` field containing an `.m4a` recording
  - Response JSON: `{ "transcript": "...", "reply": "...", "audio_url": "https://..." }`
  - All response fields are optional so early backend implementations can return partial data.
- `DELETE /v1/voice/sessions/{id}`
  - Ends the session.

## Current UX

- Idle state: button says **Call Jeeves**.
- Calling state: session is created and recording starts.
- Hang up: recording stops, one audio turn is posted, optional transcript/reply/audio response is rendered, then the session is deleted.

## Files

- `HermesVoiceApp.swift` — app entry point
- `ContentView.swift` — SwiftUI UI and simple call view model
- `VoiceSessionAPI.swift` — provisional voice session API client
- `AudioRecorder.swift` — AVFoundation `.m4a` recorder scaffold
- `AudioPlayer.swift` — AVFoundation reply playback scaffold
- `Models.swift` — small UI/API models
