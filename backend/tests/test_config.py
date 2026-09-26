import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError

from app.config import Settings
from app.logging_setup import configure_logging


def test_database_url_is_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_settings_read_the_environment(monkeypatch):
    monkeypatch.setenv("APP_NAME", "Acme Voice")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings(_env_file=None)

    assert (settings.app_name, settings.log_level, settings.app_env, settings.debug) == (
        "Acme Voice",
        "debug",
        "development",
        False,
    )


def test_invalid_log_level_is_rejected():
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        configure_logging("loud")


def test_alembic_runs_against_the_configured_database():
    # There are no migrations yet: this proves alembic/env.py imports the app and connects.
    command.upgrade(Config("alembic.ini"), "head")


# --- the voice runtime setting -----------------------------------------------------------------------------------------


def test_the_voice_runtime_defaults_to_legacy_with_no_agent_list():
    settings = Settings(_env_file=None)

    assert (settings.voice_runtime, settings.voice_runtime_pipecat_agent_ids) == ("legacy", "")


def test_the_voice_runtime_is_read_from_the_environment(monkeypatch):
    from app.config import pipecat_agent_ids

    monkeypatch.setenv("VOICE_RUNTIME", "pipecat")
    monkeypatch.setenv("VOICE_RUNTIME_PIPECAT_AGENT_IDS", " 3, 7,,")
    settings = Settings(_env_file=None)

    assert settings.voice_runtime == "pipecat" and pipecat_agent_ids(settings.voice_runtime_pipecat_agent_ids) == {3, 7}


@pytest.mark.parametrize("name, value", [("VOICE_RUNTIME", "livekit"), ("VOICE_RUNTIME", "Pipecat "), ("VOICE_RUNTIME_PIPECAT_AGENT_IDS", "3, seven")])
def test_a_mistyped_voice_runtime_setting_fails_at_startup_instead_of_quietly_selecting_nothing(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
