# Phase 4: Streaming ASR Boundary

## Built

- Typed ASR partial, final, and error events
- Provider-neutral StreamingASR protocol
- Explicit unavailable-provider adapter
- ASR capability advertised during session initialization
- Environment configuration through ASR_PROVIDER
- Unit coverage proving unavailable ASR never produces transcript text

## Safety

The default provider is none. Audio frames remain transport receipts, and an unavailable ASR dependency produces an explicit error event rather than guessed transcription.

## Remaining Work

A real provider adapter still requires a provider decision, credentials, supported audio encoding, partial/final event mapping, timeouts, and a live microphone verification.
