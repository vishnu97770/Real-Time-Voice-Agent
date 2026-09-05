# Phase 7: Voice Pipeline

## Built

- VoicePipeline connecting ASR, agent, and TTS adapters
- Final transcript to agent to TTS event flow
- Provider-neutral pipeline events
- Binary audio output support for future TTS chunks
- Injected adapter test covering the complete event sequence

## Limitation

The default adapters remain unavailable because provider credentials are not configured. The pipeline is wired and tested with injected adapters, but the real microphone-to-spoken-response path requires provider integrations.
