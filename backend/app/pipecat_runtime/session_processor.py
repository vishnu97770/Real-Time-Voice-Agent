"""The one place Pipecat meets the conversation.

It takes what the caller said, hands it to `Session.process_turn`, and passes on what `process_turn` says. It decides
nothing about the conversation: guardrails, the identity gate, the confirmation gate, tools, output vetting, the
transcript and the audit all happen inside `process_turn`, which is not changed and is not looked inside.

What it does own is turn-taking, reproduced from `PhoneCall` (app/telephony/pipeline.py) so the callee gets the same
behavior whichever runtime carries the call, and pinned to it by the parity tests:

  * pieces of one utterance are joined when the recognizer says the caller has finished (speech_final / UtteranceEnd,
    as Pipecat's ProposedUserStoppedSpeakingFrame),
    utterances still waiting are merged into it, and spoken digits become numerals BEFORE `process_turn` sees the text
    (so a PIN or card number read out loud is caught by the guardrails, as it always was);
  * two words over the agent cut it off (a caller's finished utterance replaces whatever the agent is doing);
  * the turn runs in a task of its own, is cancelled here (not by Pipecat's interruption machinery) and its generator
    is always closed, so the session's lock is released and the aborted turn is recorded;
  * after a goodbye the line is held until the goodbye has been heard, up to a limit, then the call is ended.

It never calls a model, runs a tool, reads a record or touches a job.
"""

import asyncio
from collections.abc import Callable

from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    ProposedUserStoppedSpeakingFrame,
    StartFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.guardrails import spoken_digits_to_numerals
from app.pipecat_runtime.frames import ReplyEndedFrame, SpeakFrame, SpeechFailed
from app.pipecat_runtime.playout import Playout
from app.session import Session, mark_playback_interrupted, process_turn

GOODBYE_DRAIN_SECONDS = 20  # after the agent says goodbye, how long to wait for it to be heard before hanging up


class SessionProcessor(FrameProcessor):
    def __init__(
        self,
        *,
        session: Session,
        playout: Playout,
        greeting: str,
        on_end: Callable[[str], None],
        barge_in_min_words: int = 2,
        goodbye_drain_seconds: float | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._session = session
        self._playout = playout
        self._greeting = greeting
        self._on_end = on_end  # the agent has ended the call: the runtime closes it
        self._min_words = barge_in_min_words
        self._drain_seconds = GOODBYE_DRAIN_SECONDS if goodbye_drain_seconds is None else goodbye_drain_seconds

        self._jobs: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._heard: list[str] = []  # finished pieces of what the caller is saying now
        self._current: asyncio.Task | None = None
        self._work: asyncio.Task | None = None
        self.vad_speech_hints_received = 0  # see note_vad_speech_hint(), below

    # --- frames ---------------------------------------------------------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            await self.push_frame(frame, direction)
            self._jobs.put_nowait(("say", self._greeting))
            self._work = self.create_task(self._work_loop(), "session-work")
        elif isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            await self._on_transcript(frame)
        elif isinstance(frame, ProposedUserStoppedSpeakingFrame):
            # The recognizer's speech_final / UtteranceEnd. A control frame, so it arrives in order with the transcript
            # pieces before it; consumed here directly, never turned into a UserStoppedSpeakingFrame.
            await self._utterance_finished()
        else:
            await self.push_frame(frame, direction)

    async def cleanup(self) -> None:
        task, self._current = self._current, None

        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task})

        if self._work is not None:
            work, self._work = self._work, None
            await self.cancel_task(work)

        await super().cleanup()

    def note_vad_speech_hint(self) -> None:
        """VOICE_PIPECAT_VAD=interrupt only: the runtime wires this to VadObserver's `on_speech_hint`, called when
        VAD detects speech starting. It is deliberately, provably inert: it only counts that it was called. VAD has
        no words, so it cannot satisfy the barge-in rule below (`words >= self._min_words`) by itself, and nothing
        here starts a turn, cancels one, clears playback or calls `process_turn`. Making VAD's timing actually
        change when a barge-in fires would need correlating this against a transcript that has not arrived yet -
        a real design question (buffering; the risk of interrupting on speech that turns out to be a single word),
        deliberately left for a separately-reviewed step. This method exists so that wiring, and its safety, are
        both real and tested now, not deferred to when the risk is taken on."""
        self.vad_speech_hints_received += 1

    # --- what the caller says --------------------------------------------------------------------------------------

    async def _on_transcript(self, frame: InterimTranscriptionFrame | TranscriptionFrame) -> None:
        words = len(frame.text.split())

        # Words over the agent's voice: stop talking now, not at the end of the sentence.
        if self._playout.speaking and words >= self._min_words:
            await self._supersede()

        if isinstance(frame, TranscriptionFrame) and frame.text:
            self._heard.append(frame.text)

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

        # Speech recognition writes "four one one one" for digits sometimes; make it numerals so the secret detector
        # can see a card number or PIN read out loud.
        text = spoken_digits_to_numerals(" ".join([*earlier, text]))

        await self._supersede()
        self._jobs.put_nowait(("user", text))

    # --- turns -------------------------------------------------------------------------------------------------------

    async def _work_loop(self) -> None:
        while True:
            kind, text = await self._jobs.get()
            self._current = asyncio.create_task(self._say(text) if kind == "say" else self._turn(text))

            await asyncio.wait({self._current})

            failure = None if self._current.cancelled() else self._current.exception()

            if failure is not None:
                self._session.log("pipeline_error", reason=type(failure).__name__)

    async def _supersede(self) -> None:
        """Stop whatever the agent is doing or saying: a new utterance replaces it."""
        task = self._current

        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task})

        was_speaking = self._playout.interrupt()

        # Downstream only: a broadcast would reset this processor's own queue. It stops the speaker mid-sentence and
        # drops whatever audio is still queued; the output then clears what the link already holds.
        await self.push_frame(InterruptionFrame())

        if was_speaking:
            mark_playback_interrupted(self._session)

    async def _speak(self, text: str) -> None:
        # One sentence at a time, and the next is not asked for until this one has been synthesised: the same pacing
        # the legacy loop has, so cancelling the turn stops the model where the speech had got to.
        frame = SpeakFrame(text, append_to_context=False, done=asyncio.get_running_loop().create_future())

        await self.push_frame(frame)
        await frame.done

    async def _end_of_reply(self) -> ReplyEndedFrame:
        frame = ReplyEndedFrame(flushed=asyncio.Event())
        await self.push_frame(frame)
        return frame

    async def _say(self, text: str) -> None:
        try:
            await self._speak(text)
        except SpeechFailed as error:
            self._session.log("speech_error", reason=str(error)[:80])

        await self._end_of_reply()

    async def _turn(self, text: str) -> None:
        events = process_turn(self._session, text)
        ended = False

        try:
            async for event in events:
                if event["type"] == "sentence":
                    try:
                        await self._speak(event["text"])
                    except SpeechFailed as error:
                        self._session.log("speech_error", reason=str(error)[:80])
                        break
                elif event["type"] == "done":
                    ended = bool(event["ended"])
        finally:
            await events.aclose()

        reply = await self._end_of_reply()

        if ended:
            # The agent has said goodbye (wrong person, time limit): let it finish, then hang up.
            try:
                await asyncio.wait_for(self._heard_out(reply), self._drain_seconds)
            except asyncio.TimeoutError:
                pass

            self._on_end(self._session.end_reason)

    async def _heard_out(self, reply: ReplyEndedFrame) -> None:
        await reply.flushed.wait()  # its audio has been written and the link asked to report when it is heard
        await self._playout.played.wait()
