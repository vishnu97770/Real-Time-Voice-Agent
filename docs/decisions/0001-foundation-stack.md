# Foundation Stack

## Decision
Use FastAPI and Uvicorn for the initial Python API foundation, with a static browser client for the first frontend connection check.

## Why
FastAPI provides an async HTTP foundation, typed route contracts, and middleware support for correlation IDs and CORS. Uvicorn provides the ASGI server. This keeps the first slice small and leaves real-time transport and provider SDKs behind an explicit later decision.

## Scope
This phase includes configuration, structured logs, a health endpoint, correlation IDs, CORS, and a browser connection check. It does not claim microphone, ASR, LLM, TTS, telephony, or underwriting behavior.

## Reference
The middleware and CORS approach follows the official FastAPI documentation:
https://fastapi.tiangolo.com/tutorial/middleware/
https://fastapi.tiangolo.com/tutorial/cors/
