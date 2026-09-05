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
        assert ready["capabilities"] == {"asr": "unavailable", "agent": "unavailable", "tts": "unavailable"}
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
