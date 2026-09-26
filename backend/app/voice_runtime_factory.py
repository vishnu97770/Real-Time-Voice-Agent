"""Which voice runtime conducts a call. The choice is made once, here, when the call starts, and never again.

    legacy  (the default)  LegacyVoiceRuntime: PhoneCall, as it has always run
    pipecat (opt-in)       PipecatVoiceRuntime: the same conversation on a Pipecat pipeline (app/pipecat_runtime)

The setting lives only in this module's inputs (`Telephony.voice_runtime` and the agent allow-list); nothing in
the conversation, the workflows or the providers reads it. Pipecat is imported here lazily and only when it has been
asked for, so a deployment that never asks for it never loads it.

If it has been asked for but cannot be had (not installed, or not the pinned version), the call is not failed: it is
conducted by the legacy runtime and the fallback is logged. That can only happen here, before any audio flows. A call
already running on Pipecat is never handed to another runtime.
"""

import importlib.metadata
import logging
from typing import TYPE_CHECKING, Any

from app.telephony.legacy_runtime import LegacyVoiceRuntime
from app.voice_runtime import VoiceRuntime

if TYPE_CHECKING:
    from app.session import Session
    from app.telephony import Telephony

log = logging.getLogger(__name__)


def wants_pipecat(session: "Session", telephony: "Telephony") -> bool:
    """Has Pipecat been asked for, for this call? Only the operator's settings and the call's agent decide."""
    if telephony.voice_runtime != "pipecat":
        return False

    allowed = telephony.voice_runtime_pipecat_agents

    if not allowed:
        return True  # switched on for every call

    # A call with no agent of its own (an inbound demo call, a profile job) is not on the list.
    return session.context is not None and session.context.agent.id in allowed


def _pipecat_runtime(
    *, service: Any, listener: Any, speaker: Any, barge_in_min_words: int, vad: str = "off", stt: str = "compat", **native_stt: Any
) -> VoiceRuntime:
    from app.pipecat_runtime import PIPECAT_VERSION

    try:
        installed = importlib.metadata.version("pipecat-ai")
    except importlib.metadata.PackageNotFoundError:
        raise RuntimeError("pipecat-ai is not installed") from None

    if installed != PIPECAT_VERSION:
        raise RuntimeError(f"pipecat-ai {installed} is installed but only {PIPECAT_VERSION} is supported")

    from app.pipecat_runtime.runtime import PipecatVoiceRuntime

    # Constructing it is where anything that can fail before audio (a VAD model that will not load, native STT that
    # cannot be built) fails or falls back, which is what lets the caller fall back to the legacy runtime, and lets
    # native STT fall back to the compatibility recognizer without going that far.
    return PipecatVoiceRuntime(
        service=service, listener=listener, speaker=speaker, barge_in_min_words=barge_in_min_words, vad=vad, stt=stt, **native_stt
    )


def create_voice_runtime(session: "Session", *, telephony: "Telephony", service: Any, listener: Any) -> VoiceRuntime:
    parts = {"service": service, "listener": listener, "speaker": telephony.speaker, "barge_in_min_words": telephony.barge_in_min_words}

    if wants_pipecat(session, telephony):
        try:
            runtime = _pipecat_runtime(
                **parts,
                vad=telephony.voice_pipecat_vad,
                stt=telephony.voice_pipecat_stt,
                deepgram_api_key=telephony.deepgram_api_key,
                deepgram_stt_model=telephony.deepgram_stt_model,
                deepgram_endpointing_ms=telephony.deepgram_endpointing_ms,
                deepgram_utterance_end_ms=telephony.deepgram_utterance_end_ms,
            )
        except Exception as error:
            log.warning("voice runtime call=%s pipecat unavailable (%s: %s), using legacy", session.id, type(error).__name__, error)
        else:
            log.info("voice runtime=pipecat call=%s", session.id)
            return runtime

    log.info("voice runtime=legacy call=%s", session.id)
    return LegacyVoiceRuntime(**parts)
