"""Step 10: the provider-neutral media boundary.

    AudioFrame / MediaLink (app/media.py)        no provider, codec or domain in them
    TwilioMediaLink (app/telephony/media.py)     Twilio's wire format, mu-law, marks and clear live here
    mu-law (app/telephony/mulaw.py)              the codec, a telephony-side concern

Covered: the frame, the codec (checked against G.711's defining values), one MediaLink contract run against a fake
link and the Twilio adapter, the adapter's exact wire behaviour, and the dependency rules."""

import ast
import asyncio
import base64
import dataclasses
import json
import re
import struct
from pathlib import Path

import pytest

from app.media import AudioFrame, MediaLink
from app.telephony import mulaw
from app.telephony.base import TelephonyError
from app.telephony.media import SAMPLE_RATE, TwilioMediaLink
from tests.test_telephony_routes import FakeTwilio

BACKEND = Path(__file__).parents[1]


# === AudioFrame ===================================================================================================


def test_an_audio_frame_is_plain_pcm_with_a_sample_rate_and_nothing_provider_specific():
    frame = AudioFrame(pcm=b"\x01\x00\x02\x00", sample_rate=8000)

    assert {f.name for f in dataclasses.fields(AudioFrame)} == {"pcm", "sample_rate", "channels"}
    assert (frame.channels, frame.samples, frame.duration_seconds) == (1, 2, 2 / 8000)
    assert AudioFrame(b"\x00\x00" * 480, 48000, channels=2).samples == 240
    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.sample_rate = 16000


@pytest.mark.parametrize("kwargs", [
    {"pcm": b"\x00", "sample_rate": 8000},  # half a sample
    {"pcm": b"\x00\x00\x00\x00", "sample_rate": 8000, "channels": 3},  # not whole samples for every channel
    {"pcm": b"", "sample_rate": 0},
    {"pcm": b"", "sample_rate": 8000, "channels": 0},
])
def test_an_audio_frame_rejects_what_is_not_whole_sixteen_bit_audio(kwargs):
    with pytest.raises(ValueError):
        AudioFrame(**kwargs)


# === the mu-law codec =============================================================================================


def pcm_of(code: int) -> int:
    return struct.unpack("<h", mulaw.decode(bytes([code])))[0]


def test_the_codec_reproduces_g711_s_defining_values():
    assert (pcm_of(0xFF), pcm_of(0x7F)) == (0, 0), "both zeros"
    assert (pcm_of(0x80), pcm_of(0x00)) == (32124, -32124), "the loudest positive and negative"
    assert (pcm_of(0xFE), pcm_of(0xEF), pcm_of(0x7E)) == (8, 132, -8)
    assert [pcm_of(c) for c in range(0xFF, 0x7F, -1)] == sorted(pcm_of(c) for c in range(0xFF, 0x7F, -1)), "louder is bigger"


def test_every_mu_law_code_survives_a_round_trip_except_the_duplicate_zero():
    """0x7F ('negative zero') decodes to the same silence as 0xFF, and encodes back as 0xFF."""
    assert [c for c in range(256) if mulaw.encode(mulaw.decode(bytes([c])))[0] != c] == [0x7F]
    assert mulaw.encode(mulaw.decode(bytes(range(256)))).count(0xFF) == 2


def test_encoding_is_accurate_to_mu_laws_own_step_size_across_the_whole_range():
    for sample in range(-32768, 32768, 7):
        error = abs(pcm_of(mulaw.encode(struct.pack("<h", sample))[0]) - sample)
        assert error <= max(8, abs(sample) // 16 + 8) or abs(sample) > 32124, (sample, error)


def test_the_codec_handles_whole_chunks_and_refuses_broken_ones():
    chunk = bytes(range(256)) * 7

    assert len(mulaw.decode(chunk)) == 2 * len(chunk) and len(mulaw.encode(mulaw.decode(chunk))) == len(chunk)
    assert mulaw.decode(b"") == b"" and mulaw.encode(b"") == b""
    with pytest.raises(ValueError):
        mulaw.encode(b"\x00\x01\x02")


# === a MediaLink with nothing behind it, and one contract for every implementation ===================================


class FakeMediaLink:
    """A MediaLink with no provider behind it: the test plays the callee and the loudspeaker."""

    def __init__(self, echo_playout: bool = True):
        self.log: list[tuple] = []
        self.echo = echo_playout
        self.closed = False
        self._inbound: asyncio.Queue = asyncio.Queue()
        self._reached = None

    async def audio_in(self):
        while (frame := await self._inbound.get()) is not None:
            yield frame

    def feed(self, frame: AudioFrame) -> None:
        self._inbound.put_nowait(frame)

    def end(self) -> None:
        self._inbound.put_nowait(None)

    async def send_audio(self, frame: AudioFrame) -> None:
        self.log.append(("audio", frame))

    async def clear_playout(self) -> None:
        self.log.append(("clear",))

    async def checkpoint_playout(self) -> None:
        self.log.append(("checkpoint",))

        if self.echo and self._reached is not None:
            asyncio.get_running_loop().call_later(0.01, self._reached)

    def on_playout_reached(self, callback) -> None:
        self._reached = callback

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.log.append(("close",))
            self.end()  # closing the call ends the callee's audio, as a real hang-up does

    def sent(self) -> list[AudioFrame]:
        return [entry[1] for entry in self.log if entry[0] == "audio"]


class FakeSocket:
    """A Twilio Media Streams WebSocket, as far as the media adapter uses it."""

    def __init__(self):
        self.sent: list[dict] = []
        self._incoming: asyncio.Queue = asyncio.Queue()

    def push(self, message) -> None:
        self._incoming.put_nowait(message if isinstance(message, str) else json.dumps(message))

    def stop(self) -> None:
        self.push({"event": "stop"})

    async def iter_text(self):
        while (raw := await self._incoming.get()) is not None:
            yield raw

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


class Harness:
    """What the contract needs from a MediaLink: the callee's audio in, and a view of what was sent out."""

    def __init__(self, link, callee_says, hangs_up, outbound, playout_reached):
        self.link, self.callee_says, self.hangs_up = link, callee_says, hangs_up
        self.outbound, self.playout_reached = outbound, playout_reached


def fake_harness() -> Harness:
    link = FakeMediaLink(echo_playout=False)
    return Harness(
        link,
        callee_says=lambda frame: link.feed(frame),
        hangs_up=link.end,
        outbound=lambda: [e[0] for e in link.log],
        playout_reached=lambda: link._reached(),
    )


def twilio_harness() -> Harness:
    socket, telephony = FakeSocket(), SimpleTelephony()
    link = TwilioMediaLink(socket, "MZ1", "CA1", telephony)
    kinds = {"media": "audio", "clear": "clear", "mark": "checkpoint"}

    def callee_says(frame: AudioFrame) -> None:
        socket.push({"event": "media", "media": {"payload": base64.b64encode(mulaw.encode(frame.pcm)).decode()}})

    def playout_reached() -> None:
        socket.push({"event": "mark", "mark": {"name": [m for m in socket.sent if m["event"] == "mark"][-1]["mark"]["name"]}})

    harness = Harness(link, callee_says, socket.stop, lambda: [kinds[m["event"]] for m in socket.sent] + (["close"] if telephony.hung_up else []), playout_reached)
    harness.socket, harness.telephony = socket, telephony
    return harness


class SimpleTelephony:
    """The part of the telephony bundle the media adapter uses: an adapter that can hang up."""

    def __init__(self, refuse: bool = False):
        self.hung_up: list = []
        self.adapter = self
        self.name = "twilio"
        self._refuse = refuse

    async def hang_up(self, call) -> None:
        if self._refuse:
            raise TelephonyError("already gone")

        self.hung_up.append(call)


def voice(samples: int = 160) -> AudioFrame:
    return AudioFrame(mulaw.decode(bytes(range(64)) * (samples // 64) + b"\xff" * (samples % 64)), SAMPLE_RATE)


@pytest.mark.parametrize("make", [fake_harness, twilio_harness], ids=["fake", "twilio"])
async def test_a_media_link_meets_the_contract(make):
    h = make()
    link: MediaLink = h.link

    # the callee's audio arrives as frames, in order, and the iteration ends when they hang up
    received, first, second = [], voice(160), voice(80)
    reader = asyncio.create_task(_collect(link.audio_in(), received))
    h.callee_says(first)
    h.callee_says(second)
    await _until(lambda: len(received) == 2)
    assert [f.pcm for f in received] == [first.pcm, second.pcm] and all(f.sample_rate == SAMPLE_RATE for f in received)

    # audio can be sent, playout can be cleared, and a checkpoint reports back once (and only for the latest)
    reached = []
    link.on_playout_reached(lambda: reached.append(1))
    await link.send_audio(voice(160))
    await link.checkpoint_playout()
    await link.clear_playout()
    assert h.outbound() == ["audio", "checkpoint", "clear"]
    h.playout_reached()
    await _until(lambda: reached == [1])

    # closing hangs up, and is safe to repeat (it must not raise the second time)
    await link.close()
    await link.close()
    assert "close" in h.outbound()

    h.hangs_up()
    await asyncio.wait_for(reader, 2)  # the iteration ended
    assert reader.done() and not reader.cancelled()


async def _collect(iterator, into):
    async for item in iterator:
        into.append(item)


async def _until(condition, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout

    while not condition():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.01)


def test_the_media_interface_is_small_and_asynchronous_where_it_waits_on_the_network():
    names = {n for n in dir(MediaLink) if not n.startswith("_")}

    assert names == {"audio_in", "send_audio", "clear_playout", "checkpoint_playout", "on_playout_reached", "close"}


# === the Twilio media adapter: exactly Twilio's wire format ===========================================================


async def test_twilio_media_messages_become_audio_frames_and_stop_ends_the_stream():
    h = twilio_harness()
    received = []
    reader = asyncio.create_task(_collect(h.link.audio_in(), received))
    ulaw = bytes(range(0, 256, 4))

    h.socket.push({"event": "connected", "protocol": "Call"})  # not audio: ignored
    h.socket.push({"event": "media", "media": {"payload": base64.b64encode(ulaw).decode()}})
    h.socket.push({"event": "something-new"})
    h.socket.stop()
    await asyncio.wait_for(reader, 2)

    (frame,) = received
    assert frame == AudioFrame(mulaw.decode(ulaw), 8000) and frame.samples == len(ulaw)


@pytest.mark.parametrize("broken", ["not json", '{"event": "media"}', '{"event": "media", "media": {"payload": "abc"}}', '{"event": "mark"}'])
async def test_a_message_that_cannot_be_read_ends_the_stream_as_it_always_did(broken):
    h = twilio_harness()
    received = []
    reader = asyncio.create_task(_collect(h.link.audio_in(), received))
    h.socket.push(broken)
    await asyncio.wait_for(reader, 2)

    assert received == []


async def test_a_payload_with_stray_characters_is_decoded_as_leniently_as_the_route_always_did():
    """b64decode drops characters outside the alphabet, so "!!!" is an empty payload, not an error."""
    h = twilio_harness()
    received = []
    reader = asyncio.create_task(_collect(h.link.audio_in(), received))
    h.socket.push({"event": "media", "media": {"payload": "!!!"}})
    h.socket.stop()
    await asyncio.wait_for(reader, 2)

    assert received == [AudioFrame(b"", 8000)]


async def test_sent_audio_goes_out_as_twilios_base64_mu_law_media_message():
    h = twilio_harness()
    frame = voice(160)
    await h.link.send_audio(frame)

    (message,) = h.socket.sent
    assert message["event"] == "media" and message["streamSid"] == "MZ1"
    assert base64.b64decode(message["media"]["payload"]) == mulaw.encode(frame.pcm)


@pytest.mark.parametrize("frame", [AudioFrame(b"\x00\x00" * 160, 16000), AudioFrame(b"\x00\x00" * 160, 8000, channels=2)])
async def test_twilio_carries_only_8khz_mono_and_says_so(frame):
    h = twilio_harness()

    with pytest.raises(ValueError, match="8 kHz mono"):
        await h.link.send_audio(frame)

    assert h.socket.sent == []


async def test_clear_and_playout_checkpoints_are_twilios_clear_and_mark_events():
    h = twilio_harness()
    await h.link.clear_playout()
    await h.link.checkpoint_playout()
    await h.link.checkpoint_playout()

    assert h.socket.sent == [
        {"event": "clear", "streamSid": "MZ1"},
        {"event": "mark", "mark": {"name": "m1"}, "streamSid": "MZ1"},
        {"event": "mark", "mark": {"name": "m2"}, "streamSid": "MZ1"},
    ]


async def test_only_the_echo_of_the_latest_mark_means_the_playout_was_reached():
    h = twilio_harness()
    reached = []
    h.link.on_playout_reached(lambda: reached.append(1))
    reader = asyncio.create_task(_collect(h.link.audio_in(), []))
    await h.link.checkpoint_playout()  # m1
    await h.link.checkpoint_playout()  # m2

    for name in ("m1", "unknown", "m2", "m2"):
        h.socket.push({"event": "mark", "mark": {"name": name}})

    h.socket.stop()
    await asyncio.wait_for(reader, 2)

    assert reached == [1, 1], "m2 reported (and a repeated echo of it), the stale m1 and the unknown one were not"


async def test_a_mark_echo_before_anyone_is_listening_is_harmless():
    h = twilio_harness()
    reader = asyncio.create_task(_collect(h.link.audio_in(), []))
    await h.link.checkpoint_playout()
    h.socket.push({"event": "mark", "mark": {"name": "m1"}})
    h.socket.stop()
    await asyncio.wait_for(reader, 2)


async def test_closing_hangs_up_through_the_telephony_adapter_and_swallows_a_call_that_is_already_gone():
    h = twilio_harness()
    await h.link.close()

    assert [(c.provider, c.provider_call_id) for c in h.telephony.hung_up] == [("twilio", "CA1")]

    gone = TwilioMediaLink(FakeSocket(), "MZ2", "CA2", SimpleTelephony(refuse=True))
    await gone.close()  # no exception


async def test_closing_reaches_twilio_itself_through_the_default_adapter():
    from app.telephony import Telephony

    twilio = FakeTwilio()
    telephony = Telephony(twilio=twilio, open_listener=None, speaker=None, public_api_url="https://x.example", auth_token="t")
    await TwilioMediaLink(FakeSocket(), "MZ3", "CA_real", telephony).close()

    assert twilio.hangups == ["CA_real"]


# === the dependency rules ==================================================================================================


def imports(path: Path) -> set[str]:
    found: set[str] = set()

    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
            found |= {f"{node.module}.{alias.name}" for alias in node.names}

    return found


PROVIDER_WORDS = re.compile(r"twilio|mulaw|mu-law|μ-law|streamsid|stream_sid|deepgram|websocket|\bmarks?\b|media stream", re.I)


@pytest.mark.parametrize("name", ["media", "voice_runtime"])
def test_the_shared_media_interfaces_contain_no_provider_or_wire_concept(name):
    text = (BACKEND / f"app/{name}.py").read_text()

    assert PROVIDER_WORDS.findall(text) == [], f"app/{name}.py names a provider or wire concept"


@pytest.mark.parametrize("name", ["media", "voice_runtime"])
def test_the_shared_media_interfaces_import_nothing_but_the_standard_library_and_each_other(name):
    allowed = ("collections", "dataclasses", "typing", "app.media", "app.session")
    stray = [m for m in imports(BACKEND / f"app/{name}.py") if m and not m.startswith(allowed)]

    assert stray == [], stray


@pytest.mark.parametrize("path", [
    "app/media.py", "app/voice_runtime.py", "app/telephony/media.py", "app/telephony/legacy_runtime.py",
    "app/telephony/pipeline.py", "app/telephony/mulaw.py",
], ids=str)
def test_the_media_runtime_imports_no_domain_workflow_scheduling_or_database_code(path):
    forbidden = ("app.workflows", "app.models", "app.db", "app.service", "app.voice_context", "app.outbound", "app.scheduler")
    stray = [m for m in imports(BACKEND / path) if m.startswith(forbidden)]

    assert stray == [], stray


def test_the_workflow_layer_imports_no_media_runtime():
    forbidden = ("app.media", "app.voice_runtime", "app.telephony.pipeline", "app.telephony.legacy_runtime", "app.telephony.media", "app.telephony.mulaw")

    for path in sorted(BACKEND.joinpath("app/workflows").glob("*.py")):
        assert [m for m in imports(path) if m.startswith(forbidden)] == [], path.name


def test_the_conversation_policy_stays_in_the_session_not_in_the_runtime():
    """PhoneCall still hands every utterance to process_turn; the new runtime code contains no policy of its own."""
    assert "process_turn" in {n.id for n in ast.walk(ast.parse((BACKEND / "app/telephony/pipeline.py").read_text())) if isinstance(n, ast.Name)}

    for path in ("app/voice_runtime.py", "app/telephony/legacy_runtime.py", "app/telephony/media.py"):
        source = ast.parse((BACKEND / path).read_text())
        used = {n.id for n in ast.walk(source) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(source) if isinstance(n, ast.Attribute)}

        assert not used & {"process_turn", "classify_identity", "classify_confirmation", "detect_sensitive", "solicits_secret", "redact_sensitive", "_run_tool", "GeminiBrain", "BrainContext"}, path
        assert not [m for m in imports(BACKEND / path) if m.startswith(("app.guardrails", "app.brains", "app.profiles"))], path
