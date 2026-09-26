import logging

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

# Nothing listens on port 1, so connecting is refused at once (no server needed).
UNREACHABLE_POSTGRES = "postgresql+psycopg://voice:hunter2-not-a-real-password@127.0.0.1:1/voice_agent"


def make_settings(url: str = "sqlite://") -> Settings:
    return Settings(_env_file=None, database_url=url)


def test_health_returns_ok():
    # Entering the client runs the app's start-up, so this also proves the app starts.
    with TestClient(create_app(None, make_settings())) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_answers_when_postgres_is_unreachable(caplog):
    with caplog.at_level(logging.WARNING, logger="voice_agent"):
        with TestClient(create_app(None, make_settings(UNREACHABLE_POSTGRES))) as client:
            response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "Could not resume pending callbacks" in caplog.text
    assert "hunter2" not in caplog.text
