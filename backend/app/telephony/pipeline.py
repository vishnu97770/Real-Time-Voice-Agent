"""One phone call, as audio.

    caller's voice -> speech-to-text -> [ the same runtime as every other channel ] -> text-to-speech -> caller

`process_turn` is the runtime: consent gate, guardrails, identity check, audit. This
module only turns audio into utterances and sentences into audio, and decides when
to stop talking. It knows nothing about Twilio or Deepgram beyond the small
interfaces below, so all of it is tested with fakes.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Protocol

from app.guardrails import spoken_digits_to_numerals
from app.session import Session, mark_playback_interrupted, process_turn
from app.telephony.deepgram import DeepgramError, SpeechStarted, Transcript, UtteranceEnd

# After the agent says goodbye, how long to wait for the audio to finish playing
# before hanging up anyway.
HANGUP_WAIT_SECONDS = 20


class Line(Protocol):
    """The phone line: what we can do to the audio the caller hears."""

    async def send_audio(self, mulaw: bytes) -> None: ...
    async def send_clear(self) -> None: ...  # throw away audio that has not played yet
    async def send_mark(self, name: str) -> None: ...  # ask to be told when playback reaches here
    async def hang_up(self) -> None: ...


class Listener(Protocol):
    async def send_audio(self, mulaw: bytes) -> None: ...
    def events(self) -> AsyncIterator[Any]: ...
    async def aclose(self) -> None: ...


class Speaker(Protocol):
    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...


class PhoneCall:
    def __init__(
        self,
        *,
        session: Session,
        service: Any,
        line: Line,
        listener: Listener,
        speaker: Speaker,
        barge_in_min_words: int = 2,
    ) -> None:
        self.session = session
        self.service = service
        self.line = line
        self.listener = listener
        self.speaker = speaker
        self.barge_in_min_words = barge_in_min_words

        self._jobs: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._heard: list[str] = []  # finished pieces of what the caller is saying now
        self._current: asyncio.Task | None = None
        self._tasks: list[asyncio.Task] = []
        self._speaking = False  # audio is out there, and has not finished playing
        self._sent_audio = False
        self._mark_number = 0
        self._last_mark: str | None = None
        self._played = asyncio.Event()
        self._played.set()  # nothing is playing yet
        self._closed = False
        self._closing: asyncio.Task | None = None

    # --- lifecycle ----------------------------------------------------------------------

    async def start(self, greeting: str) -> None:
        # If anything else ends the call (idle, time limit, a finished job), the
        # service hangs up the line through this.
        self.session.hangup = self._service_hangup
        self._jobs.put_nowait(("say", greeting))
        self._tasks = [asyncio.create_task(self._listen()), asyncio.create_task(self._work())]

    async def close(self, reason: str, *, hang_up: bool = True) -> None:
        """End the call and record its result. Safe to call from anywhere, any number
        of times."""
        if self._closed:
            return

        self._closed = True

        if self.session.end_reason == "client":
            self.session.end_reason = reason

        await self._teardown(hang_up)
        await self.service.finalize(self.session)

    async def _service_hangup(self) -> None:
        if self._closed:
            return

        self._closed = True
        await self._teardown(hang_up=True)

    async def _teardown(self, hang_up: bool) -> None:
        current = asyncio.current_task()
        pending = [t for t in [*self._tasks, self._current] if t and t is not current and not t.done()]

        for task in pending:
            task.cancel()

        if pending:
            await asyncio.wait(pending)

        await self.listener.aclose()

        if hang_up:
            try:
                await self.line.hang_up()
            except Exception:
                self.session.log("hangup_failed")

    def _end_later(self, reason: str) -> None:
        # Not awaited here: closing cancels the very task that asked for it.
        self._closing = asyncio.create_task(self.close(reason))

    # --- audio in ---------------------------------------------------------------------------

    async def audio_in(self, mulaw: bytes) -> None:
        if not self._closed:
            try:
                await self.listener.send_audio(mulaw)
            except Exception:
                pass  # the listener dropping is noticed in _listen

    def mark_played(self, name: str) -> None:
        """The line reports that audio up to a mark we sent has finished playing."""
        if name == self._last_mark:
            self._speaking = False
            self._played.set()

    async def _listen(self) -> None:
        async for event in self.listener.events():
            await self._on_event(event)

        if not self._closed:
            # Speech recognition dropped. An agent that cannot hear should not stay on the line.
            self.session.log("listener_lost")
            self._end_later("listener_lost")

    async def _on_event(self, event: Any) -> None:
        if isinstance(event, Transcript):
            words = len(event.text.split())

            # Words over the agent's voice: stop talking now, not at the end of the sentence.
            if self._speaking and words >= self.barge_in_min_words:
                await self._supersede()

            if event.text and event.is_final:
                self._heard.append(event.text)

            if event.speech_final:
                await self._utterance_finished()
        elif isinstance(event, UtteranceEnd):
            await self._utterance_finished()
        elif isinstance(event, SpeechStarted):
            pass  # words, not noise, are what cut the agent off

    async def _utterance_finished(self) -> None:
        text = " ".join(self._heard).strip()
        self._heard.clear()

        if not text:
            return

        # Anything still waiting is older than this, so it becomes part of it.
        earlier = []

        while not self._jobs.empty():
            kind, waiting = self._jobs.get_nowait()

            if kind == "user":
                earlier.append(waiting)

        # Speech recognition writes "four one one one" for digits sometimes; make it
        # numerals so the secret detector can see a card number or PIN read out loud.
        text = spoken_digits_to_numerals(" ".join([*earlier, text]))

        await self._supersede()
        self._jobs.put_nowait(("user", text))

    # --- turns ----------------------------------------------------------------------------------

    async def _work(self) -> None:
        while True:
            kind, text = await self._jobs.get()
            self._current = asyncio.create_task(self._say(text) if kind == "say" else self._turn(text))

            await asyncio.wait({self._current})

            failure = None if self._current.cancelled() else self._current.exception()

            if failure is not None:
                self.session.log("pipeline_error", reason=type(failure).__name__)

    async def _supersede(self) -> None:
        """Stop whatever the agent is doing or saying: a new utterance replaces it."""
        task = self._current

        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task})

        if self._speaking:
            await self.line.send_clear()
            self._speaking = False
            mark_playback_interrupted(self.session)

    async def _speak(self, text: str) -> None:
        # Each chunk goes out as it is synthesised, so the caller hears the first
        # syllable before the sentence has finished rendering.
        async for chunk in self.speaker.synthesize(text):
            self._speaking = True
            self._sent_audio = True
            await self.line.send_audio(chunk)

    async def _end_of_audio(self) -> None:
        if not self._sent_audio:
            self._played.set()  # there is nothing to wait for
            return

        self._sent_audio = False
        self._mark_number += 1
        self._last_mark = f"m{self._mark_number}"
        self._played.clear()
        await self.line.send_mark(self._last_mark)

    async def _say(self, text: str) -> None:
        try:
            await self._speak(text)
        except DeepgramError as error:
            self.session.log("speech_error", reason=str(error)[:80])

        await self._end_of_audio()

    async def _turn(self, text: str) -> None:
        events = process_turn(self.session, text)
        ended = False

        try:
            async for event in events:
                if event["type"] == "sentence":
                    try:
                        await self._speak(event["text"])
                    except DeepgramError as error:
                        self.session.log("speech_error", reason=str(error)[:80])
                        break
                elif event["type"] == "done":
                    ended = bool(event["ended"])
        finally:
            await events.aclose()

        await self._end_of_audio()

        if ended:
            # The agent has said goodbye (wrong person, time limit): let it finish, then hang up.
            try:
                await asyncio.wait_for(self._played.wait(), HANGUP_WAIT_SECONDS)
            except asyncio.TimeoutError:
                pass

            self._end_later(self.session.end_reason)
