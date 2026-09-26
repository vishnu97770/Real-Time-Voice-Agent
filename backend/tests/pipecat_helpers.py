"""Shared pieces for the Pipecat tests: a real Pipecat pipeline around fake processors, and a link that keeps time."""

import asyncio
import time

import pytest

pytest.importorskip("pipecat")

from pipecat.frames.frames import DataFrame  # noqa: E402
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: E402
from pipecat.processors.frame_processor import FrameProcessor  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402

from tests.test_media import FakeMediaLink  # noqa: E402


class Probe(FrameProcessor):
    """Records every frame it is given, in order, with the direction it travelled, then passes it on."""

    def __init__(self, slow: float = 0.0, **kwargs):
        super().__init__(**kwargs)
        self.seen: list[tuple[str, str]] = []
        self.slow = slow

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        self.seen.append((type(frame).__name__, direction.name))

        if self.slow and isinstance(frame, DataFrame):
            await asyncio.sleep(self.slow)

        await self.push_frame(frame, direction)

    def names(self, direction: str = "DOWNSTREAM") -> list[str]:
        return [name for name, d in self.seen if d == direction]


async def run_pipeline(processors, driver, sample_rate: int = 8000):
    """A real pipeline, configured the way the runtime configures its own, run to completion by `driver(worker)`."""
    worker = PipelineWorker(
        Pipeline(processors),
        params=PipelineParams(audio_in_sample_rate=sample_rate, audio_out_sample_rate=sample_rate),
        enable_rtvi=False,
        idle_timeout_secs=None,
        cancel_on_idle_timeout=False,
    )
    started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def _started(_worker, _frame):
        started.set()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    run = asyncio.create_task(runner.run())
    await asyncio.wait_for(started.wait(), 10)

    try:
        await driver(worker)
    except BaseException:
        # A failed assertion in the driver must not leave a pipeline running: the test loop would never finish.
        await worker.cancel(reason="test failed")
        await asyncio.wait_for(run, 15)
        raise

    await asyncio.wait_for(run, 15)
    return worker


class TimedLink(FakeMediaLink):
    """A fake link that also remembers when each thing happened, and models a phone: audio plays in real time from
    the moment it is sent, a `clear` throws away what has not played, and a checkpoint is reported once everything
    sent before it has played (plus `latency`)."""

    def __init__(self, latency: float = 0.0, **kwargs):
        super().__init__(echo_playout=False, **kwargs)
        self.latency = latency
        self.t0 = time.monotonic()
        self.times: list[tuple[float, str, int]] = []
        self.play_end = 0.0
        self.unheard_cleared = 0.0

    def _stamp(self, kind: str, size: int = 0) -> None:
        self.times.append((time.monotonic() - self.t0, kind, size))

    async def send_audio(self, frame) -> None:
        await super().send_audio(frame)
        self._stamp("audio", len(frame.pcm))
        now = time.monotonic()
        self.play_end = max(now, self.play_end) + frame.duration_seconds

    async def clear_playout(self) -> None:
        await super().clear_playout()
        self._stamp("clear")
        now = time.monotonic()
        self.unheard_cleared += max(0.0, self.play_end - now)
        self.play_end = min(self.play_end, now)

    async def checkpoint_playout(self) -> None:
        await super().checkpoint_playout()
        self._stamp("checkpoint")
        delay = max(self.play_end, time.monotonic()) + self.latency - time.monotonic()

        if self._reached is not None:
            asyncio.get_running_loop().call_later(delay, self._reached)

    async def close(self) -> None:
        self._stamp("close")
        await super().close()

    def times_of(self, kind: str) -> list[float]:
        return [t for t, k, _ in self.times if k == kind]
