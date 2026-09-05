from dataclasses import dataclass
from app.agent import AgentEventType, AgentRequest, ConversationalAgent
from app.asr import ASREvent, ASREventType, StreamingASR
from app.tts import StreamingTTS
from app.vad import UnavailableVAD, VoiceActivityDetector


@dataclass(frozen=True, slots=True)
class PipelineEvent:
    kind: str
    session_id: str
    text: str | None = None
    audio: bytes | None = None
    detail: str | None = None


class VoicePipeline:
    def __init__(self, asr: StreamingASR, agent: ConversationalAgent, tts: StreamingTTS, vad: VoiceActivityDetector | None = None) -> None:
        self.asr = asr
        self.agent = agent
        self.tts = tts
        self.vad = vad or UnavailableVAD()
        self._response_generations: dict[str, int] = {}

    async def start(self, session_id: str) -> None:
        await self.asr.start(session_id)

    async def handle_audio(self, session_id: str, audio: bytes) -> tuple[PipelineEvent, ...]:
        output: list[PipelineEvent] = [
            PipelineEvent(event.event_type, event.session_id, detail=event.detail)
            for event in await self.vad.process(session_id, audio)
        ]
        for event in await self.asr.push_audio(session_id, audio):
            output.append(self._asr_event(event))
            if event.event_type == ASREventType.FINAL and event.text:
                output.extend(await self.handle_text(session_id, event.text))
        return tuple(output)

    async def handle_text(self, session_id: str, text: str) -> tuple[PipelineEvent, ...]:
        output: list[PipelineEvent] = []
        generation = self._response_generations.get(session_id, 0)
        agent_events = await self.agent.respond(AgentRequest(session_id, text))
        if generation != self._response_generations.get(session_id, 0):
            return (PipelineEvent("response.cancelled", session_id, detail="response interrupted"),)
        complete_text: str | None = None
        for event in agent_events:
            output.append(PipelineEvent(event.event_type, event.session_id, text=event.text, detail=event.detail))
            if event.event_type == AgentEventType.COMPLETE:
                complete_text = event.text
        if complete_text and generation == self._response_generations.get(session_id, 0):
            for event in await self.tts.synthesize(session_id, complete_text):
                output.append(PipelineEvent(event.event_type, event.session_id, audio=event.audio, detail=event.detail))
        return tuple(output)

    @staticmethod
    def _asr_event(event: ASREvent) -> PipelineEvent:
        return PipelineEvent(event.event_type, event.session_id, text=event.text, detail=event.detail)


    def interrupt(self, session_id: str) -> None:
        self._response_generations[session_id] = self._response_generations.get(session_id, 0) + 1
