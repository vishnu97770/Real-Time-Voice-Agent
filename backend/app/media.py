"""The media boundary of a call: audio in, audio out, and control of what the callee is hearing.

Nothing here names a telephony provider, a codec or a transport. A `MediaLink` is one call's live audio
connection as the voice runtime sees it; whatever carries the call (a phone network, a WebRTC room, a test)
implements it and keeps its own wire format, framing and codecs to itself.

It knows nothing about who is calling or why: no organization, agent, contact, workflow or job.
"""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AudioFrame:
    """A short stretch of audio: 16-bit signed little-endian PCM, interleaved when there is more than one channel."""

    pcm: bytes
    sample_rate: int  # samples per second, per channel
    channels: int = 1

    def __post_init__(self) -> None:
        if self.sample_rate <= 0 or self.channels <= 0:
            raise ValueError("sample_rate and channels must be positive")

        if len(self.pcm) % (2 * self.channels):
            raise ValueError("pcm must hold whole 16-bit samples for every channel")

    @property
    def samples(self) -> int:
        """Samples per channel."""
        return len(self.pcm) // (2 * self.channels)

    @property
    def duration_seconds(self) -> float:
        return self.samples / self.sample_rate


class MediaLink(Protocol):
    """One call's audio connection."""

    def audio_in(self) -> AsyncIterator[AudioFrame]:
        """The callee's voice, as it arrives. The iteration ends when the connection ends (the callee hung up,
        the line dropped) or after `close`."""
        ...

    async def send_audio(self, frame: AudioFrame) -> None:
        """Play audio to the callee."""
        ...

    async def clear_playout(self) -> None:
        """Throw away audio that has been sent but not yet heard (the callee spoke over it)."""
        ...

    async def checkpoint_playout(self) -> None:
        """Ask to be told when everything sent so far has finished playing; see `on_playout_reached`."""
        ...

    def on_playout_reached(self, callback: Callable[[], None]) -> None:
        """Register what is called when the most recent checkpoint is reached: the audio sent before it has
        been heard, or was cleared. A checkpoint that a later one has replaced is not reported."""
        ...

    async def close(self) -> None:
        """Hang up: end the call and the connection. Safe to call when it has already ended."""
        ...
