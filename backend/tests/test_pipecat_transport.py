"""The MediaLink <-> Pipecat transport adapter, in a real Pipecat pipeline with a fake link. No network.

Several of these pin what was MEASURED in the Phase A spike and would otherwise be assumptions: which frames overtake
which, what Pipecat's own speaking state does, and what its default output settings write to a phone line."""

import asyncio
import time

import pytest

pytest.importorskip("pipecat")

from pipecat.frames.frames import (  # noqa: E402
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    ProposedUserStoppedSpeakingFrame,
    SystemFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor  # noqa: E402

from app.media import AudioFrame  # noqa: E402
from app.pipecat_runtime.frames import ReplyEndedFrame  # noqa: E402
from app.pipecat_runtime.playout import Playout  # noqa: E402
from app.pipecat_runtime.transport import (  # noqa: E402
    CHANNELS,
    CHUNK_MS,
    SAMPLE_RATE,
    MediaLinkInputTransport,
    MediaLinkOutputTransport,
)
from tests.pipecat_helpers import Probe, TimedLink, run_pipeline  # noqa: E402
from tests.test_telephony_pipeline import until  # noqa: E402


def speech(ms: int) -> bytes:
    """Non-silent 16-bit PCM at 8 kHz: `ms` milliseconds of it."""
    return b"\x01\x02" * (SAMPLE_RATE * ms // 1000)


class Collect(FrameProcessor):
    """Keeps the frames of the types it is asked for."""

    def __init__(self, *types, **kwargs):
        super().__init__(**kwargs)
        self.types, self.frames = types, []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, self.types):
            self.frames.append(frame)

        await self.push_frame(frame, direction)


def output_rig(latency=0.0, on_failure=None):
    link, playout, failures = TimedLink(latency=latency), Playout(), []
    link.on_playout_reached(playout.reached)
    out = MediaLinkOutputTransport(link, playout, on_failure or failures.append)
    return link, playout, failures, out


async def feed_speech(worker, ms: int, frame_ms: int = 200, stop: bool = True):
    """What the SpeakerProcessor sends for one sentence: its audio, then the end of the run of speech."""
    for _ in range(ms // frame_ms):
        await worker.queue_frame(TTSAudioRawFrame(speech(frame_ms), SAMPLE_RATE, 1))

    if stop:
        await worker.queue_frame(TTSStoppedFrame())


# --- the audio is the same audio ------------------------------------------------------------------------------------


async def test_a_link_frame_becomes_a_pipecat_frame_and_back_unchanged():
    link, got = TimedLink(), Collect(InputAudioRawFrame)
    ended = asyncio.Event()

    async def on_end():
        ended.set()

    async def driver(worker):
        sent = [AudioFrame(speech(20), 8000), AudioFrame(bytes(320), 8000)]
        for frame in sent:
            link.feed(frame)
        await until(lambda: len(got.frames) == 2)

        for frame, seen in zip(sent, got.frames):
            assert (seen.audio, seen.sample_rate, seen.num_channels) == (frame.pcm, 8000, 1), "no resampling, no copy of meaning"
            assert seen.num_frames == frame.samples == 160

        link.end()
        await asyncio.wait_for(ended.wait(), 3)
        await worker.stop_when_done()

    await run_pipeline([MediaLinkInputTransport(link, on_end), got], driver)


async def test_the_pipeline_and_the_transports_are_configured_for_8khz_mono_not_pipecats_defaults():
    from pipecat.pipeline.worker import PipelineParams

    assert (PipelineParams().audio_in_sample_rate, PipelineParams().audio_out_sample_rate) == (16000, 24000), "Pipecat's defaults"
    assert (SAMPLE_RATE, CHANNELS) == (8000, 1)

    link, playout, _, out = output_rig()
    inbound = MediaLinkInputTransport(link, lambda: asyncio.sleep(0))
    seen = {}

    async def driver(worker):
        seen["out"] = (out.sample_rate, out.audio_chunk_size, out._params.audio_out_channels)
        seen["in"] = (inbound.sample_rate, inbound._params.audio_in_channels)
        await worker.stop_when_done()

    await run_pipeline([inbound, out], driver)
    assert seen["in"] == (8000, 1)
    assert seen["out"] == (8000, 320, 1), "8 kHz mono, 20 ms chunks of 16-bit samples"


async def test_the_input_transport_reports_when_the_callees_audio_ends_and_only_once():
    link, ended = TimedLink(), []

    async def on_end():
        ended.append(1)

    async def driver(worker):
        link.feed(AudioFrame(speech(20), 8000))
        link.end()
        await until(lambda: ended)
        await asyncio.sleep(0.1)
        assert ended == [1]
        await worker.stop_when_done()

    await run_pipeline([MediaLinkInputTransport(link, on_end)], driver)


async def test_a_frame_in_a_format_the_runtime_is_not_configured_for_ends_the_input():
    link, got, ended = TimedLink(), Collect(InputAudioRawFrame), asyncio.Event()

    async def on_end():
        ended.set()

    async def driver(worker):
        link.feed(AudioFrame(speech(20), 8000))
        link.feed(AudioFrame(b"\x01\x02" * 320, 16000))  # not 8 kHz
        link.feed(AudioFrame(speech(20), 8000))
        await asyncio.wait_for(ended.wait(), 3)
        await until(lambda: len(got.frames) >= 1)
        await asyncio.sleep(0.15)
        assert len(got.frames) == 1, "nothing after the frame it cannot serve is passed on"
        await worker.stop_when_done()

    await run_pipeline([MediaLinkInputTransport(link, on_end), got], driver)


# --- what leaves is paced, exact, and never padded with anything we did not ask for -----------------------------------


async def test_output_goes_out_at_8khz_in_20ms_chunks_and_is_the_audio_it_was_given():
    link, playout, failures, out = output_rig()

    async def driver(worker):
        await feed_speech(worker, 400)
        await until(lambda: sum(len(f.pcm) for f in link.sent()) >= 6400)
        await worker.stop_when_done()

    await run_pipeline([out], driver)

    assert {len(f.pcm) for f in link.sent()} == {CHUNK_MS * 16}, "every write is one 20 ms chunk (320 bytes)"
    assert {(f.sample_rate, f.channels) for f in link.sent()} == {(8000, 1)}
    assert b"".join(f.pcm for f in link.sent()) == speech(400), "byte for byte: no resampling, no gain, no padding"
    assert failures == []


async def test_output_is_paced_to_real_time_not_written_as_fast_as_it_arrives():
    """One second of speech arrives at once, as it does from a fast synthesiser. Written at once, the runtime would
    call the reply finished a second before the callee could have heard it."""
    link, playout, failures, out = output_rig()

    async def driver(worker):
        await feed_speech(worker, 1000)
        await until(lambda: sum(len(f.pcm) for f in link.sent()) >= 16000, timeout=4)
        await worker.stop_when_done()

    await run_pipeline([out], driver)
    writes = link.times_of("audio")

    assert writes[0] < 0.15, "playback starts at once"
    assert 0.85 <= writes[-1] - writes[0] <= 1.1, "and the last chunk is written about a second later, not at once"
    gaps = sorted(b - a for a, b in zip(writes, writes[1:]))
    assert 0.015 <= gaps[len(gaps) // 2] <= 0.03, "one chunk per 20 ms"


async def test_output_adds_no_silence_of_its_own_while_idle_or_at_the_end_of_the_call():
    """Pipecat's defaults insert silence when idle and two seconds of it after an EndFrame (measured: 2000 ms of
    speech became 4000 ms on the line, and the reply was called finished at once)."""
    link, playout, failures, out = output_rig()

    async def driver(worker):
        await feed_speech(worker, 400)
        await until(lambda: len(link.sent()) >= 20)
        await asyncio.sleep(0.5)  # idle: an unconfigured transport would fill this with silence
        await worker.queue_frame(EndFrame())

    await run_pipeline([out], driver)

    assert b"".join(f.pcm for f in link.sent()) == speech(400), "exactly the speech: no idle silence, no end-of-call silence"


async def test_a_reply_that_is_not_a_whole_number_of_chunks_is_padded_by_less_than_one_chunk():
    """Known and accepted: Pipecat completes the last chunk with zeros (measured: 1950 ms became 1960 ms)."""
    link, playout, failures, out = output_rig()

    async def driver(worker):
        await feed_speech(worker, 50, frame_ms=50)  # 2.5 chunks
        await worker.queue_frame(ReplyEndedFrame(flushed := asyncio.Event()))
        await asyncio.wait_for(flushed.wait(), 3)
        await worker.stop_when_done()

    await run_pipeline([out], driver)
    total = b"".join(f.pcm for f in link.sent())

    assert total.startswith(speech(50)) and len(total) - len(speech(50)) < CHUNK_MS * 16
    assert not any(total[len(speech(50)):]), "the padding is silence"


async def test_an_interruption_drops_what_is_queued_then_clears_what_the_link_holds_and_nothing_is_written_after():
    link, playout, failures, out = output_rig()

    async def driver(worker):
        await feed_speech(worker, 2000)
        await until(lambda: len(link.sent()) >= 15)  # playing: speaking, and mid-reply
        assert playout.speaking
        playout.interrupt()  # what the SessionProcessor does before it sends the frame
        await worker.queue_frame(InterruptionFrame())
        await until(lambda: link.times_of("clear"))
        await asyncio.sleep(0.4)
        await worker.stop_when_done()

    await run_pipeline([out], driver)
    (cleared_at,) = link.times_of("clear")

    assert [t for t in link.times_of("audio") if t > cleared_at] == [], "no chunk after the clear (a race the first design had)"
    assert sum(len(f.pcm) for f in link.sent()) < 16000, "most of the two seconds was dropped inside the pipeline, never sent"
    assert link.unheard_cleared > 0 and not playout.speaking


async def test_an_interruption_when_nothing_is_playing_clears_nothing():
    link, playout, failures, out = output_rig()

    async def driver(worker):
        await worker.queue_frame(InterruptionFrame())
        await asyncio.sleep(0.2)
        await worker.stop_when_done()

    await run_pipeline([out], driver)
    assert link.times_of("clear") == [] and not playout.speaking


async def test_the_end_of_a_reply_asks_the_link_to_report_once_its_audio_has_been_heard():
    link, playout, failures, out = output_rig()

    async def driver(worker):
        await feed_speech(worker, 400)
        await worker.queue_frame(ReplyEndedFrame(flushed := asyncio.Event()))
        await asyncio.wait_for(flushed.wait(), 3)
        assert not playout.played.is_set(), "there is audio the callee has not yet heard"
        await asyncio.wait_for(playout.played.wait(), 3)
        await worker.stop_when_done()

    await run_pipeline([out], driver)
    (checkpoint_at,) = link.times_of("checkpoint")

    assert checkpoint_at >= max(link.times_of("audio")), "the checkpoint follows the reply's last chunk, never precedes it"
    assert not playout.speaking


async def test_a_reply_with_no_audio_asks_for_no_checkpoint_and_is_not_waited_for():
    link, playout, failures, out = output_rig()

    async def driver(worker):
        await worker.queue_frame(ReplyEndedFrame(flushed := asyncio.Event()))
        await asyncio.wait_for(flushed.wait(), 3)
        assert playout.played.is_set()
        await worker.stop_when_done()

    await run_pipeline([out], driver)
    assert link.times_of("checkpoint") == []


async def test_a_failing_link_is_reported_once_not_once_per_chunk_and_the_pipeline_carries_on():
    class BrokenLink(TimedLink):
        async def send_audio(self, frame):
            raise ConnectionError("gone")

    link, playout, failures = BrokenLink(), Playout(), []
    out = MediaLinkOutputTransport(link, playout, failures.append)

    async def driver(worker):
        await feed_speech(worker, 400)
        await worker.queue_frame(ReplyEndedFrame(flushed := asyncio.Event()))
        await asyncio.wait_for(flushed.wait(), 3)
        await worker.stop_when_done()

    await run_pipeline([out], driver)
    assert [type(f) for f in failures] == [ConnectionError]


# --- Pipecat's own notion of "speaking" is not the callee's, and system frames overtake data frames --------------------


async def test_pipecats_bot_stopped_speaking_arrives_before_the_callee_has_heard_the_reply():
    """Why 'is the agent still speaking' comes from the link's checkpoint, not from Pipecat's frames: Pipecat calls the
    bot finished when it has written the last chunk, ahead of playback by the send lead plus the link's latency."""
    link, playout, failures, out = output_rig(latency=0.15)
    probe, stopped = Probe(), {}

    class Watch(Probe):
        async def process_frame(self, frame, direction):
            if isinstance(frame, BotStoppedSpeakingFrame) and "at" not in stopped:
                stopped["at"], stopped["speaking"] = time.monotonic(), playout.speaking
                stopped["playback_ends"] = link.play_end + link.latency

            await super().process_frame(frame, direction)

    async def driver(worker):
        await feed_speech(worker, 600)
        await worker.queue_frame(ReplyEndedFrame(flushed := asyncio.Event()))
        await asyncio.wait_for(flushed.wait(), 3)
        await until(lambda: "at" in stopped, timeout=3)
        await asyncio.wait_for(playout.played.wait(), 3)
        await worker.stop_when_done()

    await run_pipeline([Watch(), out], driver)

    assert stopped["speaking"] is True, "the runtime still counts the agent as speaking"
    assert stopped["playback_ends"] - stopped["at"] > 0.1, "Pipecat's frame ran well ahead of what the callee heard"


async def test_a_system_frame_pushed_after_data_frames_overtakes_them_so_the_end_of_an_utterance_is_pipecats_control_frame():
    """Phase A, spike A: with a downstream processor that is busy, Pipecat's UserStoppedSpeakingFrame (a SystemFrame)
    arrived BEFORE the transcript frames pushed ahead of it. The end-of-utterance signal must not be one: Pipecat's own
    ProposedUserStoppedSpeakingFrame is a control frame, and stays in order."""

    class Burst(FrameProcessor):
        def __init__(self, end_frame, **kwargs):
            super().__init__(**kwargs)
            self.end_frame = end_frame

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)

            if isinstance(frame, EndFrame):
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, InputAudioRawFrame):
                for out in (
                    InterimTranscriptionFrame("hel", "", ""),
                    TranscriptionFrame("hello", "", ""),
                    TranscriptionFrame("there", "", ""),
                    self.end_frame(),
                    TranscriptionFrame("next", "", ""),
                ):
                    await self.push_frame(out)
            else:
                await self.push_frame(frame, direction)

    async def order_for(end_frame):
        probe = Probe(slow=0.03)

        async def driver(worker):
            await worker.queue_frame(InputAudioRawFrame(speech(20), 8000, 1))
            await asyncio.sleep(0.4)
            await worker.stop_when_done()

        await run_pipeline([Burst(end_frame), probe], driver)
        return [n for n in probe.names() if n not in ("StartFrame", "EndFrame", "InputAudioRawFrame")]

    assert issubclass(UserStoppedSpeakingFrame, SystemFrame) and not issubclass(ProposedUserStoppedSpeakingFrame, SystemFrame)

    stock = await order_for(UserStoppedSpeakingFrame)
    ours = await order_for(ProposedUserStoppedSpeakingFrame)

    assert stock.index("UserStoppedSpeakingFrame") < stock.index("TranscriptionFrame"), "the stock frame jumps the queue"
    assert ours == ["InterimTranscriptionFrame", "TranscriptionFrame", "TranscriptionFrame", "ProposedUserStoppedSpeakingFrame", "TranscriptionFrame"]
