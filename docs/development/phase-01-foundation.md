# Phase 1: Project Foundation

## Built
- Async FastAPI application with / and /health endpoints
- Environment-based configuration via .env.example conventions
- JSON logs and request correlation IDs
- Restricted CORS configuration for the browser client
- Initial tests for health and correlation

## Verification
Run python3 -m pytest from the repository root.

## Known Limitations
No voice transport, microphone capture, ASR, LLM, TTS, authentication, database, or underwriting tools are implemented yet. Provider credentials are intentionally unused until their adapters are introduced.

## Next Phase
Research and implement the real-time session transport boundary, starting with session lifecycle and text/control messages before raw audio.
