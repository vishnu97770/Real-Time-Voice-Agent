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
