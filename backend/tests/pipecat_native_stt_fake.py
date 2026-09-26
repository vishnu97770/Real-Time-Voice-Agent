"""A fake Deepgram WebSocket, standing in for the real one so the native STT tests run the REAL pipecat-ai and
deepgram-sdk code (settings, `_on_message`, message parsing, connect-kwarg building) with no network.

Only `AsyncDeepgramClient(...).listen.v1.connect(...)` is replaced: everything downstream of that one call, in both
libraries, runs unmodified."""

import asyncio
from dataclasses import dataclass, field

from deepgram.core.events import EventType
from deepgram.listen.v1.types import (
    ListenV1Results,
    ListenV1ResultsChannel,
    ListenV1ResultsChannelAlternativesItem,
    ListenV1ResultsMetadata,
    ListenV1ResultsMetadataModelInfo,
    ListenV1UtteranceEnd,
)


def result(text: str, *, is_final: bool, speech_final: bool = False, from_finalize: bool = False) -> ListenV1Results:
    """One `Results` message, with just enough of Deepgram's own shape to be real: the same object type, the same
    fields (`is_final`, `speech_final`), the same place the transcript text lives."""
    return ListenV1Results(
        channel_index=[0],
        duration=0.5,
        start=0.0,
        is_final=is_final,
        speech_final=speech_final,
        from_finalize=from_finalize,
        channel=ListenV1ResultsChannel(
            alternatives=[ListenV1ResultsChannelAlternativesItem(transcript=text, confidence=0.99, words=[])]
        ),
        metadata=ListenV1ResultsMetadata(
            request_id="fake-request",
            model_info=ListenV1ResultsMetadataModelInfo(name="nova-3", version="fake", arch="fake"),
            model_uuid="fake-model",
        ),
    )


def utterance_end(last_word_end: float = 1.0) -> ListenV1UtteranceEnd:
    return ListenV1UtteranceEnd(channel=[0], last_word_end=last_word_end)


@dataclass
class FakeDeepgramConnection:
    """What `AsyncDeepgramClient(...).listen.v1.connect(...)` yields as its connection: enough of the real SDK
    object's surface (`on`, `start_listening`, `send_*`) for `DeepgramSTTService` to run against unmodified.

    Messages are delivered from a queue, not a fixed list: a test can pre-load it (a scripted conversation) and can
    also `feed()` it live, any time after the connection is up, exactly as a call's own recognizer would keep
    receiving events for as long as the call runs."""

    fail: bool = False  # raise instead of ever opening, as a rejected handshake would
    sent_media: list[bytes] = field(default_factory=list)
    closed: bool = False
    connect_kwargs: dict | None = None

    def __post_init__(self) -> None:
        self._handlers: dict[EventType, list] = {}
        self._queue: asyncio.Queue = asyncio.Queue()

    def feed(self, message) -> None:
        self._queue.put_nowait(message)

    def on(self, event_type: EventType, callback) -> None:
        self._handlers.setdefault(event_type, []).append(callback)

    async def _emit(self, event_type: EventType, data) -> None:
        for callback in self._handlers.get(event_type, []):
            result = callback(data)

            if asyncio.iscoroutine(result):
                await result

    async def start_listening(self) -> None:
        if self.fail:
            raise ConnectionError("the handshake was rejected")

        await self._emit(EventType.OPEN, None)

        while True:  # a real stream stays open and keeps delivering messages until told otherwise; this one does too
            message = await self._queue.get()
            await self._emit(EventType.MESSAGE, message)

    async def send_media(self, data: bytes) -> None:
        self.sent_media.append(data)

    async def send_finalize(self, message=None) -> None:
        pass

    async def send_close_stream(self, message=None) -> None:
        self.closed = True

    async def send_keep_alive(self, message=None) -> None:
        pass


class FakeConnect:
    """Stands in for `client.listen.v1.connect`: an async context manager factory, called with the exact keyword
    arguments `DeepgramSTTService._build_connect_kwargs()` produced, so a test can inspect them."""

    def __init__(self, script: list | None = None, fail: bool = False) -> None:
        self.script = script or []
        self.fail = fail
        self.connection: FakeDeepgramConnection | None = None
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        self.connection = FakeDeepgramConnection(fail=self.fail, connect_kwargs=kwargs)

        for message in self.script:
            self.connection.feed(message)

        return self

    async def __aenter__(self) -> FakeDeepgramConnection:
        return self.connection

    async def __aexit__(self, *exc) -> bool:
        return False


def wire_fake_deepgram(service, script: list | None = None, fail: bool = False) -> FakeConnect:
    """Replace `service`'s connect call with a fake one. `service` must already be constructed (a real
    `DeepgramSTTService`/`_ParityDeepgramSTTService`); nothing about it changes except where the socket comes from."""
    fake = FakeConnect(script, fail=fail)
    service._client.listen.v1.connect = fake
    return fake
