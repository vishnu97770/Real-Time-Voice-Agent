import logging

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

UNREACHABLE_POSTGRES = "postgresql+psycopg://voice:hunter2-not-a-real-password@127.0.0.1:1/voice_agent"


def test_database_outage_is_a_503_with_no_details(caplog):
    settings = Settings(_env_file=None, database_url=UNREACHABLE_POSTGRES)

    with caplog.at_level(logging.WARNING, logger="voice_agent"):
        with TestClient(create_app(None, settings)) as client:
            response = client.get("/api/calls")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
    assert "hunter2" not in response.text + caplog.text


@pytest.mark.parametrize("debug, env", [(False, "development"), (True, "production")])
def test_unexpected_error_is_a_generic_500(debug, env, caplog):
    settings = Settings(_env_file=None, database_url="sqlite://", debug=debug, app_env=env)
    app = create_app(None, settings)

    @app.get("/boom/{call_id}")
    async def boom(call_id: str) -> None:
        raise RuntimeError("secret-internal-detail")

    with caplog.at_level(logging.ERROR, logger="voice_agent"):
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/boom/call-id-that-is-a-bearer-token")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "secret-internal-detail" not in response.text
    # The traceback is in the log, keyed by route template rather than the concrete path.
    assert "secret-internal-detail" in caplog.text
    assert "GET /boom/{call_id}" in caplog.text
    assert "call-id-that-is-a-bearer-token" not in caplog.text
