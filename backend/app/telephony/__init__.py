"""Phone calls: Twilio carries them, Deepgram hears and speaks.

`Telephony` is the phone runtime's bundle: the control-plane `adapter` the application uses (Twilio's unless
another is given) and the media-plane pieces the audio route uses (Twilio's raw client and stream, Deepgram).
The provider modules are imported when they are needed, so importing `app.telephony.base` (the provider-neutral
interface) does not load any provider.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from app.config import Settings, pipecat_agent_ids, telephony_configured
from app.telephony.base import TelephonyAdapter

if TYPE_CHECKING:
    from app.telephony.twilio import TwilioClient


@dataclass
class Telephony:
    twilio: "TwilioClient | None"  # Twilio's raw client: the media route hangs up the line through it
    open_listener: Callable[[], Awaitable[Any]]  # a fresh speech-to-text stream for each call
    speaker: Any
    public_api_url: str
    auth_token: str  # signs the stream tokens and validates Twilio's webhooks
    barge_in_min_words: int = 2
    inbound_enabled: bool = False
    inbound_profile: str = "bank"
    # Which voice runtime conducts a call (see app/voice_runtime_factory.py). Legacy unless switched on.
    voice_runtime: str = "legacy"
    voice_runtime_pipecat_agents: frozenset[int] = frozenset()  # empty: every call, when voice_runtime is "pipecat"
    voice_pipecat_vad: str = "off"  # "observe" adds observation-only VAD to a Pipecat call; never affects a legacy one
    voice_pipecat_stt: str = "compat"  # "native" tries Pipecat's own Deepgram STT for a Pipecat call; legacy ignores this
    # What "native" needs to build Pipecat's own DeepgramSTTService (app/pipecat_runtime/native_stt.py). The same
    # values `open_listener`'s Deepgram stream above is already built from; carried again, plainly, because a
    # runtime that wants them can't pull them back out of that closure. Never logged.
    deepgram_api_key: str | None = None
    deepgram_stt_model: str = "nova-3"
    deepgram_endpointing_ms: int = 400
    deepgram_utterance_end_ms: int = 1000
    # The control plane the application uses to place and end calls. Twilio's, built from the fields above,
    # unless another provider's is given.
    adapter: TelephonyAdapter | None = None

    def __post_init__(self) -> None:
        if self.adapter is None:
            from app.telephony.twilio import TwilioTelephony

            self.adapter = TwilioTelephony(self.twilio, self.auth_token, self.public_api_url)

    def ws_url(self) -> str:
        from app.telephony.twilio import ws_url

        return ws_url(self.public_api_url)

    def status_url(self, job_id: str) -> str:
        from app.telephony.twilio import status_url

        return status_url(self.public_api_url, job_id)

    def voice_url(self) -> str:
        from app.telephony.twilio import voice_url

        return voice_url(self.public_api_url)


def build_telephony(settings: Settings, http: httpx.AsyncClient) -> Telephony | None:
    """None unless Twilio and Deepgram are both fully configured."""
    if not telephony_configured(settings):
        return None

    from app.telephony.deepgram import DeepgramSTT, DeepgramTTS
    from app.telephony.twilio import TwilioClient

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
        voice_runtime=settings.voice_runtime,
        voice_runtime_pipecat_agents=pipecat_agent_ids(settings.voice_runtime_pipecat_agent_ids),
        voice_pipecat_vad=settings.voice_pipecat_vad,
        voice_pipecat_stt=settings.voice_pipecat_stt,
        deepgram_api_key=settings.deepgram_api_key,
        deepgram_stt_model=settings.deepgram_stt_model,
        deepgram_endpointing_ms=settings.deepgram_endpointing_ms,
        deepgram_utterance_end_ms=settings.deepgram_utterance_end_ms,
    )
