# Phases 8 and 9: VAD and Barge-In Boundaries

## Built

- Typed speech start/end/error VAD events
- Provider-neutral VoiceActivityDetector interface
- Explicit unavailable VAD adapter for compressed browser input
- Response generation cancellation tokens
- Interrupt handling that invalidates in-flight agent responses
- Capability reporting and cancellation tests

## Remaining Work

A real VAD requires a selected PCM audio format or provider. Full duplex playback requires TTS streaming, browser audio buffering, cancellation of active playback, and latency measurements.
