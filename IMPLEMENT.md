# Implementation Guide

This file is the implementation map for the Real-Time Voice and Credit Underwriting Workspace. It follows the supplied PRD and is updated after each verified vertical slice.

## Current Status

- Completed: Phase 0 discovery, Phase 1 foundation, Phase 2 real-time transport, Phase 3 microphone transport
- Active next slice: Phase 4 streaming ASR
- Planned: Phases 5 through 28
- Verified tests: 5 passing

The first three phases provide a working API, a browser shell, a WebSocket session, lifecycle messages, and browser microphone chunks. They do not yet provide transcription, an LLM, TTS, underwriting data, or telephony.

## Delivery Rules

Each phase follows this loop:

1. Inspect the repository and existing changes.
2. Research only the current technology needed for the slice.
3. Record the architecture decision.
4. Install only required dependencies.
5. Implement a small end-to-end slice.
6. Test, debug, and verify it.
7. Document limitations and the next phase.

Never claim an external integration works without credentials and a real runtime check. Dependency failures must never produce fabricated financial information.

## Architecture Boundaries

- Client: microphone, playback, connection state, transcripts, workspace UI
- Real-time layer: transport, audio buffers, VAD, turn detection, cancellation, session lifecycle
- Agent layer: profiles, prompts, memory, model routing, tools, authorization
- Underwriting layer: applications, documents, extraction, calculations, risk, review, decisions, audit
- Integrations: ASR, LLM, TTS, telephony, KYC, credit data, notifications

Business state changes require explicit authorized actions. The voice agent is decision support, never autonomous credit authority.

## Phase Plan

### Phase 0: Repository and Environment Discovery

Status: Complete.

Inspect the repository, guidance files, dependencies, environment, tests, Docker configuration, and current implementation. Deliverable: a clear implementation map.

### Phase 1: Project Foundation

Status: Complete.

FastAPI/Uvicorn backend, environment configuration, JSON logging, CORS, health endpoints, browser shell, project guidance, and initial tests.

Verification: `python3 -m pytest`, Python compilation, HTTP smoke test.

### Phase 2: Real-Time Communication Foundation

Status: Complete.

WebSocket endpoint at `/ws/voice`, session IDs, lifecycle states, JSON control messages, binary frame receipt, invalid-message handling, disconnect cleanup, and browser connect/end controls.

Verification: WebSocket tests in `tests/test_health.py`.

### Phase 3: Microphone and Audio Pipeline

Status: Complete.

Browser microphone permission, `MediaRecorder` chunks, binary WebSocket frames, microphone controls, and explicit transport-only status messaging.

Limitation: browser-selected compressed media is not yet the provider-specific PCM format required by streaming ASR.

### Phase 4: Streaming ASR

Status: Next.

Select one streaming ASR adapter after comparing latency, browser/server media requirements, language support, privacy, and credentials. Define an adapter interface, session association, partial transcripts, final transcripts, timeout handling, and provider failure states.

Definition of done: a real microphone utterance produces partial and final transcript events without inventing text when ASR fails.

### Phase 5: LLM and Agent Orchestration

Implement a provider-neutral conversational agent interface, system prompt, session context, user message handling, streaming response events, cancellation, timeouts, and safe error messages.

Definition of done: transcript input produces a streamed text response with no underwriting claims.

### Phase 6: Streaming TTS

Implement a TTS adapter interface, text chunking, audio chunk streaming, playback events, cancellation, and provider error handling.

Definition of done: a real agent response is spoken in the browser when credentials are configured.

### Phase 7: First Complete Voice Agent

Connect microphone, transport, ASR, agent, TTS, and browser playback into one minimal conversation. Add session-level tracing and a runtime smoke test.

Definition of done: a user can speak and hear a basic conversational response.

### Phase 8: VAD and Turn Detection

Add voice activity detection, speech start/end events, turn state, silence thresholds, and semantic turn signals where justified. Measure false starts and missed turns.

Definition of done: speech turns are detected reliably in supported audio conditions.

### Phase 9: Full Duplex and Barge-In

Add simultaneous input/output, response cancellation, audio flushing, interruption priority, race protection, and interruption metrics.

Definition of done: a user can interrupt speech and receive a new response within the target barge-in budget when providers permit it.

### Phase 10: Underwriting Data Foundation

Add application creation, application IDs, document metadata, upload state, processing state, audit events, and the application state machine: DRAFT, DOCUMENTS_UPLOADED, PROCESSING, ANALYSIS_READY, UNDER_REVIEW, DECIDED, and PROCESSING_FAILED.

Definition of done: application state transitions are validated and auditable.

### Phase 11: Document Extraction

Add document storage boundaries, OCR or extraction adapter, structured values, provenance, page/table references, confidence, and untrusted-document handling.

Definition of done: extracted facts always retain their source and confidence.

### Phase 12: Financial Analysis

Implement deterministic financial calculations, ratios, risk indicators, and separation between extracted facts, calculated values, and interpretations.

Definition of done: calculations are unit tested and traceable to input facts.

### Phase 13: ML Risk Prediction

Add feature engineering, model inference interface, risk score, probability of default, contributing factors, model versioning, and unavailable-model handling.

Definition of done: predictions are reproducible for a model version and never replaced with guessed values.

### Phase 14: Evidence-Grounded Agent

Create reusable Agent Profile, tool registry, authorization checks, evidence retrieval, underwriting context, and model routing. Implement authorized read-only tools: application, documents, extracted values, ratios, risk prediction, recommendation, evidence source, and decision history.

Definition of done: the agent receives underwriting context only through authorized tools.

### Phase 15: LLM Underwriting Explanation

Implement grounded explanations for financial position, risks, ratios, model prediction, recommendation rationale, and evidence. Require citations to structured evidence and clearly label missing data.

Definition of done: substantive financial answers are evidence-backed and reject unsupported conclusions.

### Phase 16: Workspace Voice with Underwriting Context

Connect ASR transcripts to the underwriting agent, tools, evidence, response generation, and TTS. Preserve application and user context throughout the session.

Definition of done: voice questions receive grounded underwriting answers.

### Phase 17: Human Review and Decision

Implement review records, accept/reject/challenge/override actions, rationale, reviewer identity, timestamps, context, and explicit separation between AI recommendation and human decision.

Definition of done: no AI response can directly finalize a credit decision.

### Phase 18: Audit and Traceability

Trace speech, ASR, agent, tool calls, evidence, response, TTS, correlation IDs, application IDs, session IDs, model versions, failures, and consequential events.

Definition of done: a review can reconstruct a consequential interaction end to end.

### Phase 19: Outbound Telephony

Define a telephony adapter, call initiation, call IDs, status, media streaming, termination, retry, and failure handling without coupling the core agent to one provider.

Definition of done: an authorized test call has a complete lifecycle and audit record.

### Phase 20: Outbound Agent

Add context loading, identity verification, consent, application/user isolation, ASR, agent tools, TTS, outcomes, and call audit.

Definition of done: outbound conversations cannot access unverified or unrelated application data.

### Phase 21: Scheduling and Event Triggers

Implement event-driven and scheduled triggers, approved call windows, reminders, analysis completion events, review follow-ups, retries, and safe failure paths.

Definition of done: every call trigger is authorized, observable, and idempotent.

### Phase 22: Reusable Agent Profiles

Move identity, prompts, tone, tools, data sources, policies, voice behavior, triggers, permissions, and escalation rules into profiles consumed by the same runtime.

Definition of done: a new domain profile does not require changes to the real-time engine.

### Phase 23: Security and Governance

Implement authentication, authorization, tenant/user/application isolation, consent, secrets management, tool permissions, action confirmation, prompt-injection defenses, and safe failure.

Definition of done: authorization is enforced on the backend and untrusted document text cannot override system instructions.

### Phase 24: Observability

Add structured traces and metrics for audio ingestion, VAD, ASR partial/final, LLM first token, tools, TTS first byte, playback, end-to-end latency, barge-in, cancellation, WebSockets, and failures.

Definition of done: latency and failure budgets can be measured from logs and metrics.

### Phase 25: Performance

Measure before optimizing. Profile streaming, buffering, model latency, network latency, TTS first byte, tool latency, and event-loop blocking against the under-800 ms perceived response target and under-one-second barge-in target.

Definition of done: benchmark results identify the remaining bottleneck and regression tests protect improvements.

### Phase 26: Testing, Chaos, and Adversarial Checks

Add unit, API, integration, end-to-end voice, load, stress, packet-loss, jitter, disconnect, provider timeout, duplicate-event, ordering, multi-speaker, ambiguous-command, prompt-injection, and dependency-outage tests.

Definition of done: failure paths are tested and do not fabricate facts or bypass authorization.

### Phase 27: Docker and Deployment

Dockerize only services with clear operational boundaries. Start with the API/real-time service and add document processing, inference, storage, or telephony separation only when justified.

Definition of done: documented local and deployment startup paths are reproducible.

### Phase 28: Production Readiness

Verify the complete underwriting workflow, evidence grounding, ML traceability, workspace voice, barge-in, outbound calling, isolation, human review, audit, profiles, security, observability, performance, stress testing, and deployment.

Definition of done: a production-readiness checklist has evidence for every PRD requirement and clearly lists remaining risks.

## Current Commands

    python3 -m pytest
    python3 -m compileall -q app
    python3 -m uvicorn app.main:app --reload

## Current Documentation

- `docs/decisions/`: architecture decisions
- `docs/development/`: verified phase notes
- `AGENTS.md`: repository map and safety rules
