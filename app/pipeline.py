from dataclasses import dataclass
from typing import Any

from app.agent import AgentEventType, AgentRequest, ConversationalAgent
from app.asr import ASREvent, ASREventType, StreamingASR
from app.tts import StreamingTTS, TTSEventType


@dataclass(frozen=True, slots=True)
class PipelineEvent:
    kind: str
    session_id: str
    text: str | None = None
    audio: bytes | None = None
    detail: str | None = None


class VoicePipeline:
    def __init__(self, asr: StreamingASR, agent: ConversationalAgent, tts: StreamingTTS) -> None:
        self.asr = asr
        self.agent = agent
        self.tts = tts

    async def start(self, session_id: str) -> None:
        await self.asr.start(session_id)

    async def handle_audio(self, session_id: str, audio: bytes) -> tuple[PipelineEvent, ...]:
        output: list[PipelineEvent] = []
        for event in await self.asr.push_audio(session_id, audio):
            output.append(self._asr_event(event))
            if event.event_type == ASREventType.FINAL and event.text:
                output.extend(await self.handle_text(session_id, event.text))
        return tuple(output)

    async def handle_text(self, session_id: str, text: str) -> tuple[PipelineEvent, ...]:
        output: list[PipelineEvent] = []
        agent_events = await self.agent.respond(AgentRequest(session_id, text))
        complete_text: str | None = None
        for event in agent_events:
            output.append(PipelineEvent(event.event_type, event.session_id, text=event.text, detail=event.detail))
            if event.event_type == AgentEventType.COMPLETE:
                complete_text = event.text
        if complete_text:
            for event in await self.tts.synthesize(session_id, complete_text):
                output.append(PipelineEvent(event.event_type, event.session_id, audio=event.audio, detail=event.detail))
        return tuple(output)

    @staticmethod
    def _asr_event(event: ASREvent) -> PipelineEvent:
        return PipelineEvent(event.event_type, event.session_id, text=event.text, detail=event.detail)
