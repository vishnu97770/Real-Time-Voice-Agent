# Phase 3: Microphone and Audio Transport

## Built

- Browser microphone permission flow using getUserMedia
- MediaRecorder chunking at 250 ms intervals
- Binary Blob frames sent over the voice WebSocket
- Microphone start/stop controls and permission/error states
- Explicit UI wording that frames are transport-only until ASR exists

## Verification

The server-side binary frame path is covered by the WebSocket tests. Browser microphone capture requires a secure browser context and a user-granted microphone permission.

## Known Limitations

MediaRecorder output is browser-selected compressed media, not yet the PCM format required by a selected streaming ASR provider. No audio playback, VAD, ASR, TTS, or transcription is implemented.

## Reference

The capture and binary-send approach follows MDN guidance for getUserMedia, MediaRecorder, and WebSocket.send.
