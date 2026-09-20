from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # Server-side only. The key never goes to the browser.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.6-flash"
    # 0 = no thinking (fastest first word, right for voice); -1 = model decides.
    gemini_thinking_budget: int = 0

    # A single LLM round-trip (including streaming it) must finish within this.
    brain_timeout_seconds: float = 30.0

    # The Vite dev server proxies /api, so CORS only matters for other origins.
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Persistence. SQLite by default; any SQLAlchemy URL works (e.g. Postgres).
    database_url: str = "sqlite:///./voice_agent.db"

    # Outbound calling. The business API is closed until an API key is set.
    api_key: str | None = Field(default=None, validation_alias="VOICE_AGENT_API_KEY")
    # Signs result callbacks; defaults to the API key.
    webhook_secret: str | None = Field(default=None, validation_alias="VOICE_AGENT_WEBHOOK_SECRET")
    public_base_url: str = "http://localhost:5173"  # where callees open their answer link
    allow_private_callbacks: bool = False  # only for local development
    callback_attempts: int = 3
    callback_backoff_seconds: float = 2.0
    sweep_interval_seconds: float = 5.0
    call_idle_timeout_seconds: int = 120

    # Operator sign-in. On by default: the console and its call endpoints need a login.
    # Create the first user with: python -m app.cli create-user
    auth_required: bool = True
    auth_session_hours: int = 12
    cookie_secure: bool = False  # set true when served over https (always, in production)
    # Behind a reverse proxy, how many proxies to trust for X-Forwarded-For. 0 = none,
    # use the socket address. Wrong values let clients pick their own rate-limit identity.
    trusted_proxy_hops: int = 0

    # Requests per minute. The window is one minute.
    limit_login_per_ip: int = 10
    limit_login_per_email: int = 5
    limit_callee_per_ip: int = 30
    limit_jobs_per_principal: int = 60
    limit_calls_per_user: int = 20
    limit_turns_per_call: int = 30

    # --- Telephony: Twilio carries the call, Deepgram does speech-to-text and text-to-speech.
    # Phone calls switch on only when all of these are set (see telephony_configured).
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None  # your Twilio number, e.g. +14155550123
    # Where Twilio can reach THIS server over the internet (https), e.g. an ngrok URL.
    public_api_url: str | None = None
    deepgram_api_key: str | None = None
    deepgram_stt_model: str = "nova-3"
    deepgram_tts_model: str = "aura-2-thalia-en"
    # Silence, in ms, before Deepgram decides the caller has finished a sentence.
    deepgram_endpointing_ms: int = 400
    deepgram_utterance_end_ms: int = 1000
    # The caller must say this many words over the agent to cut it off.
    barge_in_min_words: int = 2
    # Off by default: caller ID proves nothing, and inbound calls have no identity
    # check, so an open phone line would hand demo data to anyone who rings.
    twilio_inbound_enabled: bool = False
    twilio_inbound_profile: str = "bank"

    max_sessions: int = 200
    session_ttl_seconds: int = 3600


def telephony_configured(settings: Settings) -> bool:
    return all(
        (
            settings.twilio_account_sid,
            settings.twilio_auth_token,
            settings.twilio_from_number,
            settings.public_api_url,
            settings.deepgram_api_key,
        )
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
