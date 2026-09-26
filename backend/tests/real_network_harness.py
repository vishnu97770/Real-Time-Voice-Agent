"""Step 17: a safe, opt-in verification harness for the native STT + VAD path against a REAL Deepgram connection.

Nothing here ships in `app/`: like `tests/pipecat_native_stt_fake.py` (which stands in for the real Deepgram
WebSocket), this is test-only tooling - the difference is that here, the real thing is used on purpose. It exists
to answer one question honestly: does `app/pipecat_runtime/native_stt.py`'s real construction (`build_native_stt`,
unchanged from Step 15) actually work against Deepgram's real service, and does Step 16's real Silero VAD actually
observe real speech-shaped audio in a real running pipeline - without ever depending on either for the normal test
suite, and without ever risking a secret in a captured diagnostic.

Nothing here decides anything about a call: no Session, no Playout, no InterruptionFrame, no process_turn. It only
watches a pipeline run and records bounded, safe facts about what happened.
"""

import os
from dataclasses import dataclass, field

from pipecat.frames.frames import ErrorFrame, Frame, StartFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.pipecat_runtime.frames import (
    UninterruptibleInterimTranscriptionFrame,
    UninterruptibleProposedUserStoppedSpeakingFrame,
    UninterruptibleTranscriptionFrame,
)

RUN_REAL_DEEPGRAM_TESTS = os.environ.get("RUN_REAL_DEEPGRAM_TESTS") == "1"


def deepgram_credential() -> str | None:
    """The real key, read directly from the process environment - never through `Settings`/`.env`, so this harness's
    own behavior does not depend on how the application loads its configuration. `None` if not set. No caller in
    this module ever prints, logs, or otherwise surfaces the value this returns; every diagnostic below records only
    whether a credential was present, never what it was."""
    value = os.environ.get("DEEPGRAM_API_KEY")
    return value if value else None


def credential_audit() -> dict:
    """A safe summary of the environment for the report: never the key itself, only whether it is set and how long
    it is (a length is not a secret, and is occasionally useful to notice a copy-paste truncation)."""
    key = deepgram_credential()
    return {
        "run_real_deepgram_tests": RUN_REAL_DEEPGRAM_TESTS,
        "deepgram_api_key_present": key is not None,
        "deepgram_api_key_length": len(key) if key else 0,
    }


def scrub(text: str, *secrets: str | None) -> str:
    """Replace any of `secrets` (an API key, most often) that appears verbatim in `text` with a fixed placeholder.
    Applied to every exception message and log-shaped string before it is kept in a `NetworkDiagnostics` record.
    A no-op for any secret that is `None` or empty, so it is always safe to call with `deepgram_credential()`
    directly, credential present or not."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")

    return text


@dataclass
class NetworkDiagnostics:
    """Safe, bounded, real-network diagnostics: connection lifecycle and event COUNTS only - never a transcript,
    never audio, never a phone number or identity, never a secret. Every string field is expected to have already
    passed through `scrub()`. This is Phase 3's "safe diagnostic metadata", made concrete."""

    connection_attempted: bool = False
    connection_established: bool = False  # a Results/interim frame was actually seen, not just "no error yet"
    connection_closed: bool = False
    frame_counts: dict[str, int] = field(default_factory=dict)
    speech_final_seen: bool = False
    utterance_end_seen: bool = False
    transcript_nonempty_seen: bool = False
    vad_start_count: int = 0
    vad_stop_count: int = 0
    vad_to_deepgram_deltas: list[float] = field(default_factory=list)
    error_category: str | None = None
    error_message: str | None = None  # always pre-scrubbed by whoever calls record_error

    def record_frame(self, kind: str) -> None:
        self.frame_counts[kind] = self.frame_counts.get(kind, 0) + 1

    def record_error(self, exc: BaseException, *secrets: str | None) -> None:
        self.error_category = type(exc).__name__
        self.error_message = scrub(str(exc), *secrets)[:500]  # bounded: a diagnostic, not a full traceback dump


class DiagnosticsTap(FrameProcessor):
    """Sits after the native STT adapter (or after VadCorrelationTap, order does not matter to this processor):
    inspects frame TYPES (and, for a transcript frame, only whether `.text` is non-empty - never the text) to fill
    in a `NetworkDiagnostics` record. Forwards every frame unchanged, decides nothing, reads no secret."""

    def __init__(self, diagnostics: NetworkDiagnostics, *secrets: str | None, **kwargs) -> None:
        """`secrets`: anything (an API key) that must never survive into a captured error message, even if some
        future error path forgets to redact it upstream - a defense-in-depth scrub, applied here unconditionally."""
        super().__init__(**kwargs)
        self._diagnostics = diagnostics
        self._secrets = secrets

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        self._diagnostics.record_frame(type(frame).__name__)

        if isinstance(frame, (UninterruptibleInterimTranscriptionFrame, UninterruptibleTranscriptionFrame)):
            self._diagnostics.connection_established = True

            if frame.text:
                self._diagnostics.transcript_nonempty_seen = True
        elif isinstance(frame, UninterruptibleProposedUserStoppedSpeakingFrame):
            self._diagnostics.utterance_end_seen = True
        elif isinstance(frame, ErrorFrame):
            self._diagnostics.error_category = type(frame).__name__
            self._diagnostics.error_message = scrub(str(frame.error)[:500], *self._secrets)
        elif isinstance(frame, StartFrame):
            self._diagnostics.connection_attempted = True

        await self.push_frame(frame, direction)
