from dataclasses import dataclass
import os


def _origins(value: str) -> tuple[str, ...]:
    return tuple(origin.strip() for origin in value.split(",") if origin.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    environment: str
    log_level: str
    allowed_origins: tuple[str, ...]
    asr_provider: str


def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "Real-Time Voice Agent"),
        environment=os.getenv("APP_ENV", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        allowed_origins=_origins(os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:8000")),
        asr_provider=os.getenv("ASR_PROVIDER", "none"),
    )
