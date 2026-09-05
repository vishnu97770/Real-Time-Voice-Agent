from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_endpoint_reports_ready_service():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-correlation-id"]


def test_client_correlation_id_is_preserved():
    response = client.get("/", headers={"X-Correlation-ID": "test-correlation"})
    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == "test-correlation"



def test_voice_session_has_lifecycle_and_text_transport():
    with client.websocket_connect("/ws/voice") as websocket:
        ready = websocket.receive_json()
        assert ready["type"] == "session.ready"
        assert ready["state"] == "LISTENING"
        assert ready["session_id"]
        assert ready["capabilities"] == {"asr": "unavailable", "agent": "unavailable", "tts": "unavailable", "vad": "unavailable"}
        websocket.send_json({"type": "text", "content": "hello"})
        received = websocket.receive_json()
        state = websocket.receive_json()
        assert received["type"] == "text.received"
        assert received["content"] == "hello"
        assert received["state"] == "PROCESSING"
        assert state["state"] == "LISTENING"


def test_voice_session_accepts_binary_transport_without_claiming_processing():
    with client.websocket_connect("/ws/voice") as websocket:
        ready = websocket.receive_json()
        websocket.send_bytes(b"audio-frame")
        received = websocket.receive_json()
        assert received == {"type": "audio.received", "session_id": ready["session_id"], "bytes": 11, "state": "LISTENING"}


def test_voice_session_rejects_invalid_messages_and_can_end():
    with client.websocket_connect("/ws/voice") as websocket:
        ready = websocket.receive_json()
        websocket.send_json({"type": "unknown"})
        error = websocket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "INVALID_MESSAGE"
        websocket.send_json({"type": "end"})
        ended = websocket.receive_json()
        assert ended == {"type": "session.ended", "session_id": ready["session_id"], "state": "SESSION_END"}



def test_unavailable_asr_returns_explicit_error_event():
    import asyncio
    from app.asr import ASREventType, UnavailableASR

    events = asyncio.run(UnavailableASR().push_audio("session-1", b"audio"))
    assert len(events) == 1
    assert events[0].event_type == ASREventType.ERROR
    assert events[0].text is None
    assert events[0].detail == "ASR provider is not configured"



def test_unavailable_agent_and_tts_are_explicit():
    import asyncio
    from app.agent import AgentEventType, AgentRequest, UnavailableAgent
    from app.tts import TTSEventType, UnavailableTTS

    agent_events = asyncio.run(UnavailableAgent().respond(AgentRequest("session-1", "hello")))
    tts_events = asyncio.run(UnavailableTTS().synthesize("session-1", "hello"))
    assert agent_events[0].event_type == AgentEventType.ERROR
    assert agent_events[0].text is None
    assert tts_events[0].event_type == TTSEventType.ERROR
    assert tts_events[0].audio is None



def test_voice_pipeline_connects_final_transcript_to_agent_and_tts():
    import asyncio
    from app.agent import AgentEvent, AgentEventType
    from app.asr import ASREvent, ASREventType
    from app.pipeline import VoicePipeline
    from app.tts import TTSEvent, TTSEventType

    class FakeASR:
        provider = "test"
        available = True
        async def start(self, session_id): pass
        async def push_audio(self, session_id, audio):
            return (ASREvent(ASREventType.FINAL, session_id, text="hello"),)
        async def finish(self, session_id): return ()

    class FakeAgent:
        provider = "test"
        available = True
        async def respond(self, request):
            return (AgentEvent(AgentEventType.COMPLETE, request.session_id, text="hi there"),)

    class FakeTTS:
        provider = "test"
        available = True
        async def synthesize(self, session_id, text):
            return (TTSEvent(TTSEventType.AUDIO_CHUNK, session_id, audio=b"audio"), TTSEvent(TTSEventType.COMPLETE, session_id))

    events = asyncio.run(VoicePipeline(FakeASR(), FakeAgent(), FakeTTS()).handle_audio("session-1", b"frame"))
    assert [event.kind for event in events] == ["transcript.final", "agent.complete", "tts.audio.chunk", "tts.complete"]
    assert events[0].text == "hello"
    assert events[2].audio == b"audio"



def test_pipeline_cancels_response_after_barge_in():
    import asyncio
    from app.agent import AgentEvent, AgentEventType
    from app.asr import UnavailableASR
    from app.pipeline import VoicePipeline
    from app.tts import UnavailableTTS

    class SlowAgent:
        provider = "test"
        available = True
        async def respond(self, request):
            await asyncio.sleep(0.02)
            return (AgentEvent(AgentEventType.COMPLETE, request.session_id, text="late response"),)

    async def scenario():
        pipeline = VoicePipeline(UnavailableASR(), SlowAgent(), UnavailableTTS())
        task = asyncio.create_task(pipeline.handle_text("session-1", "hello"))
        await asyncio.sleep(0)
        pipeline.interrupt("session-1")
        return await task

    events = asyncio.run(scenario())
    assert events[0].kind == "response.cancelled"
    assert events[0].detail == "response interrupted"



def test_application_workflow_validates_state_and_records_audit():
    response = client.post("/applications", json={"applicant_name": "Acme Ltd"})
    assert response.status_code == 201
    application = response.json()
    application_id = application["application_id"]
    assert application["state"] == "DRAFT"

    document = client.post(f"/applications/{application_id}/documents", json={"filename": "annual-report.pdf", "content_type": "application/pdf", "size_bytes": 128})
    assert document.status_code == 201
    assert document.json()["state"] == "DOCUMENTS_UPLOADED"

    invalid = client.post(f"/applications/{application_id}/state", json={"state": "DECIDED"})
    assert invalid.status_code == 409

    for state in ["PROCESSING", "ANALYSIS_READY", "UNDER_REVIEW", "DECIDED"]:
        assert client.post(f"/applications/{application_id}/state", json={"state": state}).status_code == 200
    audit = client.get(f"/applications/{application_id}/audit")
    assert audit.status_code == 200
    assert any(event["event_type"] == "application.state_changed" for event in audit.json())


def test_unknown_application_returns_not_found():
    assert client.get("/applications/missing").status_code == 404
