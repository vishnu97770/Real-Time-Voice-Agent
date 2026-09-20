"""Phone calls: Twilio carries them, Deepgram hears and speaks."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings, telephony_configured
from app.telephony.deepgram import DeepgramSTT, DeepgramTTS
from app.telephony.twilio import TwilioClient


@dataclass
class Telephony:
    twilio: TwilioClient
    open_listener: Callable[[], Awaitable[Any]]  # a fresh speech-to-text stream for each call
    speaker: Any
    public_api_url: str
    auth_token: str  # signs the stream tokens and validates Twilio's webhooks
    barge_in_min_words: int = 2
    inbound_enabled: bool = False
    inbound_profile: str = "bank"

    def ws_url(self) -> str:
        return self.public_api_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/api/telephony/stream"

    def status_url(self, job_id: str) -> str:
        return f"{self.public_api_url}/api/telephony/status?job={quote(job_id)}"

    def voice_url(self) -> str:
        return f"{self.public_api_url}/api/telephony/voice"


def build_telephony(settings: Settings, http: httpx.AsyncClient) -> Telephony | None:
    """None unless Twilio and Deepgram are both fully configured."""
    if not telephony_configured(settings):
        return None

    stt = DeepgramSTT(
        settings.deepgram_api_key,
        settings.deepgram_stt_model,
        settings.deepgram_endpointing_ms,
        settings.deepgram_utterance_end_ms,
    )

    return Telephony(
        twilio=TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_from_number, http),
        open_listener=stt.open,
        speaker=DeepgramTTS(settings.deepgram_api_key, http, settings.deepgram_tts_model),
        public_api_url=settings.public_api_url.rstrip("/"),
        auth_token=settings.twilio_auth_token,
        barge_in_min_words=settings.barge_in_min_words,
        inbound_enabled=settings.twilio_inbound_enabled,
        inbound_profile=settings.twilio_inbound_profile,
    )
