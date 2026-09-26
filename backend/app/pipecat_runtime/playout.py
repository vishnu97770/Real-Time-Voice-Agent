"""What the callee is hearing, as the media link reports it. Shared by the output transport (which writes audio and
clears it) and the SessionProcessor (which decides when the callee has talked over it).

This is the legacy runtime's `_speaking` / `_sent_audio` / `_played` bookkeeping, unchanged in meaning: the agent is
"speaking" from the moment audio leaves until the link says the latest checkpoint was heard, which is later than
when Pipecat believes its own output finished (measured: by the send lead plus the link's latency).
"""

import asyncio

from app.media import MediaLink


class Playout:
    def __init__(self) -> None:
        self.speaking = False  # audio is out there, and has not finished playing
        self.played = asyncio.Event()  # set when nothing is left to play
        self.played.set()
        self._sent_audio = False
        self._clear_pending = False

    def audio_sent(self) -> None:
        self.speaking = True
        self._sent_audio = True

    def reached(self) -> None:
        """The link reports that the latest checkpoint has been heard."""
        self.speaking = False
        self.played.set()

    def interrupt(self) -> bool:
        """The callee talked over the agent. Returns whether the agent was speaking. The decision is taken here,
        at once, so a second interim result a moment later does not interrupt again; the output does the clearing."""
        was_speaking = self.speaking
        self.speaking = False
        self._clear_pending = self._clear_pending or was_speaking
        return was_speaking

    async def apply_interrupt(self, link: MediaLink) -> None:
        """Called by the output once it has stopped writing: throw away what the callee has not yet heard."""
        if self._clear_pending:
            self._clear_pending = False
            self.speaking = False
            await link.clear_playout()

    async def end_of_reply(self, link: MediaLink) -> None:
        """Everything of a reply has been written. If any audio went out, ask to be told when it has been heard."""
        if not self._sent_audio:
            self.played.set()  # there is nothing to wait for
            return

        self._sent_audio = False
        self.played.clear()
        await link.checkpoint_playout()
