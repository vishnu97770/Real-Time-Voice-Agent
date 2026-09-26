"""Step 14: the end of an utterance is Pipecat's own frame, transcripts survive an interruption, and an observation-only
VAD watches the call without changing anything it does.

Three claims, each proved from the outside:

  A. The recognizer's speech_final / UtteranceEnd reach the SessionProcessor as Pipecat's `ProposedUserStoppedSpeakingFrame`,
     in order with the transcript, and there is no custom end-of-utterance frame any more.
  B. The transcript and that end signal are `UninterruptibleFrame`s: an `InterruptionFrame` can no longer make them vanish.
  C. With VOICE_PIPECAT_VAD=observe a Silero VAD records when speech started and stopped. It ends no turn, interrupts
     nothing, clears nothing and changes no state: the same conversations come out identical with it off and on, and
     identical to the legacy runtime.

No Twilio, no Deepgram, no network: fake link, recognizer and speaker, a real Pipecat pipeline and (where a test says so)
the real Silero model."""

import asyncio
import dataclasses
import gc
import logging
import threading
import time
import types
import weakref
from functools import partial

import numpy as np
import pytest

pytest.importorskip("pipecat")

from pipecat.frames.frames import (  # noqa: E402
    ControlFrame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    ProposedUserStoppedSpeakingFrame,
    SystemFrame,
    TranscriptionFrame,
    UninterruptibleFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADParams  # noqa: E402

import app.pipecat_runtime.frames as frames_module  # noqa: E402
import app.pipecat_runtime.runtime as runtime_module  # noqa: E402
import app.pipecat_runtime.session_processor as processor_module  # noqa: E402
from app.brains.base import TextDelta  # noqa: E402
from app.config import Settings  # noqa: E402
from app.media import AudioFrame  # noqa: E402
from app.pipecat_runtime.frames import (  # noqa: E402
    UninterruptibleInterimTranscriptionFrame,
    UninterruptibleProposedUserStoppedSpeakingFrame,
    UninterruptibleTranscriptionFrame,
)
from app.pipecat_runtime.playout import Playout  # noqa: E402
from app.pipecat_runtime.recognizer import RecognizerProcessor  # noqa: E402
from app.pipecat_runtime.runtime import PipecatVoiceRuntime  # noqa: E402
from app.pipecat_runtime.session_processor import SessionProcessor  # noqa: E402
from app.pipecat_runtime.vad_observer import VadObservation, VadObserver, create_silero_analyzer  # noqa: E402
from app.profiles import get_profile  # noqa: E402
from app.session import create_session, open_call  # noqa: E402
from app.telephony.deepgram import Transcript, UtteranceEnd  # noqa: E402
from app.telephony.legacy_runtime import LegacyVoiceRuntime  # noqa: E402
from app.voice_runtime_factory import create_voice_runtime  # noqa: E402
from tests import test_voice_runtime as scenarios  # noqa: E402
from tests.helpers import ScriptedBrain  # noqa: E402
from tests.pipecat_helpers import Probe, TimedLink, run_pipeline  # noqa: E402
from tests.test_pipecat_runtime import PARITY_CONVERSATIONS, LongSpeaker, audit_types, conversation  # noqa: E402
from tests.test_pipecat_transport import Collect  # noqa: E402
from tests.test_telephony_pipeline import FakeListener, until  # noqa: E402
from tests.test_voice_runtime import RuntimeRig  # noqa: E402
from tests.test_voice_runtime import (  # noqa: E402,F401  (the Step 10 scenarios, run below with VAD observing)
    rig,
    test_a_handler_cancelled_mid_call_still_records_the_call,
    test_a_new_utterance_replaces_a_reply_that_is_still_being_thought_up,
    test_a_sentence_can_arrive_in_several_pieces_and_is_one_turn,
    test_a_single_word_is_not_enough_to_cut_the_agent_off,
    test_a_wrong_number_gets_a_goodbye_and_then_the_runtime_hangs_up_the_link,
    test_agent_output_is_still_vetted_before_it_is_spoken,
    test_an_agent_that_can_no_longer_hear_does_not_stay_on_the_line,
    test_an_utterance_end_event_also_closes_the_sentence,
    test_guardrails_still_catch_a_pin_read_out_as_words_and_it_is_never_kept,
    test_interim_guesses_and_silence_alone_do_not_start_turns,
    test_the_callee_hanging_up_records_the_call_without_touching_the_link,
    test_the_callees_frames_reach_speech_recognition_as_the_bytes_it_expects,
    test_the_confirmation_gate_still_needs_a_spoken_yes,
    test_the_greeting_leaves_as_neutral_frames_followed_by_a_playout_checkpoint,
    test_the_identity_gate_still_holds_until_the_callee_confirms_who_they_are,
    test_talking_over_the_agent_cuts_it_off_at_once_clears_the_playout_and_is_recorded,
    test_what_the_caller_says_goes_through_process_turn_and_comes_back_as_frames,
    test_when_the_service_ends_a_call_the_link_is_hung_up,
)
from tests.test_voice_runtime_selection import call, telephony  # noqa: E402

# The imported Step 10 scenarios are the ones that run with the observing runtime.
OBSERVED_SCENARIOS = {
    name for name in dir() if name.startswith("test_") and getattr(globals()[name], "__module__", "") == "tests.test_voice_runtime"
}


@pytest.fixture(autouse=True)
async def environment(request, monkeypatch):
    """Every rig is always shut down (a failing test must not leave a pipeline running), and the imported Step 10
    scenarios run on the Pipecat runtime with VAD observing."""
    started = []
    real_start = scenarios.RuntimeRig.start

    async def start(self):
        started.append(weakref.ref(self))  # weak: a test that checks the model is released must not be kept alive by this list
        return await real_start(self)

    monkeypatch.setattr(scenarios.RuntimeRig, "start", start)

    if request.node.originalname in OBSERVED_SCENARIOS:
        monkeypatch.setattr(scenarios.RuntimeRig, "runtime_class", staticmethod(partial(PipecatVoiceRuntime, vad="observe")))

    yield

    for reference in started:
        each = reference()

        if each is not None and each.task is not None and not each.task.done():
            each.task.cancel()

            try:
                await asyncio.wait_for(each.task, 5)
            except BaseException:  # noqa: BLE001
                pass


# === helpers ===========================================================================================================


class ScriptedVad(VADAnalyzer):
    """A VAD that calls any non-silent audio speech, so a test decides exactly when speech starts and stops."""

    def __init__(self, delay: float = 0.0, fail: bool = False):
        super().__init__(sample_rate=8000, params=VADParams(confidence=0.5, start_secs=0.04, stop_secs=0.04, min_volume=0.0))
        self.delay, self.fail = delay, fail

    def num_frames_required(self) -> int:
        return 160  # 20 ms at 8 kHz

    def voice_confidence(self, buffer) -> float:
        if self.fail:
            raise RuntimeError("the model fell over")

        if self.delay:
            time.sleep(self.delay)

        return 1.0 if any(buffer) else 0.0


def talking(frames: int = 10) -> list[AudioFrame]:
    return [AudioFrame(b"\x10\x27" * 160, 8000) for _ in range(frames)]


def quiet(frames: int = 10) -> list[AudioFrame]:
    return [AudioFrame(bytes(320), 8000) for _ in range(frames)]


def input_frames(frames: list[AudioFrame]) -> list[InputAudioRawFrame]:
    return [InputAudioRawFrame(audio=f.pcm, sample_rate=f.sample_rate, num_channels=f.channels) for f in frames]


async def play(link, frames, gap: float = 0.004) -> None:
    for frame in frames:
        link.feed(frame)
        await asyncio.sleep(gap)


def observing_rig(monkeypatch, analyzer=ScriptedVad, **kwargs) -> RuntimeRig:
    """A rig whose runtime observes with `analyzer` (a factory), instead of loading the real model."""
    monkeypatch.setattr(runtime_module, "create_silero_analyzer", analyzer)
    rig_ = RuntimeRig(**kwargs)
    rig_.runtime = PipecatVoiceRuntime(service=rig_.service, listener=rig_.listener, speaker=rig_.speaker, vad="observe")
    return rig_


def kinds(runtime) -> list[str]:
    return [o.kind for o in runtime.vad_observations]


async def recognizer_frames(events, slow: float = 0.0):
    """Run the real RecognizerProcessor over `events` (Deepgram's own event types) and return the frames it emitted."""
    listener, probe, seen = FakeListener(), Probe(slow=slow), []

    class Keep(Collect):
        pass

    keep = Keep(InterimTranscriptionFrame, TranscriptionFrame, ProposedUserStoppedSpeakingFrame)

    async def lost():
        pass

    async def driver(worker):
        for event in events:
            listener.push(event)

        await until(lambda: len(keep.frames) >= len(seen_expected(events)))
        await asyncio.sleep(0.1)
        await worker.stop_when_done()

    await run_pipeline([RecognizerProcessor(listener, lost), keep, probe], driver)
    return keep.frames, probe


def seen_expected(events) -> list:
    out = []

    for e in events:
        if isinstance(e, Transcript):
            out += [1] if e.text else []
            out += [1] if e.speech_final else []
        elif isinstance(e, UtteranceEnd):
            out += [1]

    return out


# === A. the end of an utterance is Pipecat's own frame ================================================================


async def test_the_recognizer_emits_pipecats_end_of_utterance_frame_for_speech_final_and_for_utterance_end():
    frames, _ = await recognizer_frames(
        [
            Transcript("hello", True, False),  # a final piece, the caller is still talking
            Transcript("there", True, True),  # speech_final
            UtteranceEnd(),  # the gap after the last word
            Transcript("", True, True),  # speech_final with nothing new: still the end of an utterance
        ]
    )

    assert [type(f).__name__ for f in frames] == [
        "UninterruptibleTranscriptionFrame",
        "UninterruptibleTranscriptionFrame",
        "UninterruptibleProposedUserStoppedSpeakingFrame",
        "UninterruptibleProposedUserStoppedSpeakingFrame",
        "UninterruptibleProposedUserStoppedSpeakingFrame",
    ]
    assert all(isinstance(f, ProposedUserStoppedSpeakingFrame) for f in frames[2:]), "Pipecat's own frame, not ours"
    assert [f.text for f in frames[:2]] == ["hello", "there"]


def test_no_custom_end_of_utterance_frame_remains_and_the_stock_user_stopped_frame_is_not_the_signal():
    assert not hasattr(frames_module, "UtteranceEndedFrame")

    import ast
    from pathlib import Path

    package = Path(frames_module.__file__).parent

    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text())
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        used |= {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}

        assert "UtteranceEndedFrame" not in path.read_text(), path.name
        assert "UserStoppedSpeakingFrame" not in used, f"{path.name} uses the stock frame that can be reordered"

    assert not issubclass(UninterruptibleProposedUserStoppedSpeakingFrame, UserStoppedSpeakingFrame)


async def test_the_end_of_an_utterance_keeps_its_place_between_the_transcript_frames_even_with_a_slow_consumer():
    frames, probe = await recognizer_frames(
        [
            Transcript("a", False, False),
            Transcript("a b", True, True),
            Transcript("c", True, False),
            Transcript("c d", True, True),
        ],
        slow=0.03,
    )

    names = [n.replace("Uninterruptible", "") for n in probe.names()]  # the recognizer's frames are the stock ones plus a marker
    order = [n for n in names if n in ("InterimTranscriptionFrame", "TranscriptionFrame", "ProposedUserStoppedSpeakingFrame")]

    assert order == [
        "InterimTranscriptionFrame",  # "a"
        "TranscriptionFrame",  # "a b" (final) ...
        "ProposedUserStoppedSpeakingFrame",  # ... and its speech_final, straight after it
        "TranscriptionFrame",  # "c" (final, the caller carries on)
        "TranscriptionFrame",  # "c d" (final) ...
        "ProposedUserStoppedSpeakingFrame",  # ... and its speech_final
    ]


# === B. transcripts and the end signal survive an interruption =======================================================


async def test_interim_transcript_frames_are_uninterruptible_but_still_stock_frames():
    frames, _ = await recognizer_frames([Transcript("wait", False, False), Transcript("wait a", False, False)])

    assert frames and all(
        isinstance(f, InterimTranscriptionFrame) and isinstance(f, UninterruptibleFrame) and isinstance(f, UninterruptibleInterimTranscriptionFrame)
        for f in frames
    )


async def test_final_transcript_frames_are_uninterruptible_but_still_stock_frames():
    frames, _ = await recognizer_frames([Transcript("hello", True, False)])

    assert isinstance(frames[0], UninterruptibleTranscriptionFrame)
    assert isinstance(frames[0], TranscriptionFrame) and isinstance(frames[0], UninterruptibleFrame)


async def test_the_end_of_utterance_frame_is_uninterruptible_and_a_control_frame_so_it_stays_ordered():
    frames, _ = await recognizer_frames([Transcript("hello", True, True)])
    end = frames[-1]

    assert isinstance(end, UninterruptibleFrame) and isinstance(end, ControlFrame) and not isinstance(end, SystemFrame)


async def texts_heard_across_an_interruption(protected: bool, frames_to_queue: str) -> list[str]:
    """Queue frames behind a slow SessionProcessor, interrupt it, and return the text that reached the brain."""

    class Slow(SessionProcessor):
        async def _on_transcript(self, frame):
            await asyncio.sleep(0.2)  # busy with the first frame while the rest wait behind it, as in a real turn
            await super()._on_transcript(frame)

    async def script(ctx):
        yield TextDelta("ok.")

    brain = ScriptedBrain(script)
    session = create_session(get_profile("bank"), brain, 5.0)
    open_call(session)
    processor = Slow(session=session, playout=Playout(), greeting="hello", on_end=lambda reason: None)
    text_frame = UninterruptibleTranscriptionFrame if protected else TranscriptionFrame
    end_frame = UninterruptibleProposedUserStoppedSpeakingFrame if protected else ProposedUserStoppedSpeakingFrame

    async def driver(worker):
        await asyncio.sleep(0.05)  # the greeting is under way and blocked: nothing can speak in this pipeline
        if frames_to_queue == "text-and-end":
            await worker.queue_frame(text_frame("first words", "", ""))
            await worker.queue_frame(text_frame("more words", "", ""))
            await worker.queue_frame(end_frame())
        else:
            await worker.queue_frame(text_frame("first words", "", ""))
            await worker.queue_frame(end_frame())

        await asyncio.sleep(0.05)
        await worker.queue_frame(InterruptionFrame())  # arrives while the first frame is still being handled
        await asyncio.sleep(0.8)
        await worker.stop_when_done()

    await run_pipeline([processor], driver)
    return [c.text for c in brain.calls]


async def test_an_interruption_cannot_drop_a_queued_transcript():
    assert await texts_heard_across_an_interruption(protected=True, frames_to_queue="text-and-end") == ["first words more words"]
    lost = await texts_heard_across_an_interruption(protected=False, frames_to_queue="text-and-end")

    assert lost != ["first words more words"], "control: the unprotected stock frames really are lost, which is why the protection is needed"


async def test_an_interruption_cannot_drop_a_queued_end_of_utterance_signal():
    assert await texts_heard_across_an_interruption(protected=True, frames_to_queue="text-then-end") == ["first words"]
    assert await texts_heard_across_an_interruption(protected=False, frames_to_queue="text-then-end") == [], "control: unprotected, no turn happens"


# === what the SessionProcessor gets is unchanged ======================================================================


async def test_the_session_processor_still_receives_exactly_the_text_the_legacy_runtime_gives_process_turn():
    steps = [("what is my balance", "You said")]
    legacy = await conversation(LegacyVoiceRuntime, steps)
    pipecat = await conversation(PipecatVoiceRuntime, steps)

    assert pipecat["brain_saw"] == legacy["brain_saw"] == ["what is my balance"]


async def test_spoken_digit_normalisation_is_unchanged_with_the_new_frames_and_with_vad_observing():
    said = "my reference number is one two three four five"

    for runtime_class in (PipecatVoiceRuntime, partial(PipecatVoiceRuntime, vad="observe")):
        result = await conversation(runtime_class, steps=[(said, "You said")])
        assert result["brain_saw"] == ["my reference number is 12345"]


async def test_a_turn_is_completed_by_the_recognizers_signal_and_never_by_the_vad(monkeypatch):
    rig_ = observing_rig(monkeypatch)
    await rig_.start()
    await rig_.greeted()
    rig_.hear("what is my balance", final=True, speech_final=False)  # the recognizer has the words, but not the end
    await play(rig_.link, talking(10) + quiet(10))  # the VAD sees speech, then silence: an acoustic end
    await until(lambda: kinds(rig_.runtime) == ["speech_started", "speech_stopped"])
    await asyncio.sleep(0.2)

    assert rig_.brain.calls == [], "the VAD's silence did not end the utterance"
    rig_.hear("", final=True, speech_final=True)  # the recognizer's own end
    await until(lambda: len(rig_.brain.calls) == 1)
    assert rig_.brain.calls[0].text == "what is my balance"
    await rig_.cleanup()


# === C. the VAD observer ===============================================================================================


async def test_vad_off_creates_no_analyzer_and_puts_nothing_in_the_pipeline(monkeypatch):
    created = []
    monkeypatch.setattr(runtime_module, "create_silero_analyzer", lambda: created.append(1) or ScriptedVad())
    rig_ = RuntimeRig()
    rig_.runtime = PipecatVoiceRuntime(service=rig_.service, listener=rig_.listener, speaker=rig_.speaker)  # the default
    await rig_.start()
    await rig_.greeted()
    await rig_.cleanup()

    assert created == [] and rig_.runtime._vad_analyzer is None and rig_.runtime._vad_observer is None
    assert rig_.runtime.vad_observations == ()


async def test_vad_off_through_the_factory_never_calls_the_analyzer_constructor(monkeypatch):
    import app.pipecat_runtime.vad_observer as observer_module

    calls = []
    monkeypatch.setattr(observer_module, "SileroVADAnalyzer", lambda **kw: calls.append(kw))
    runtime = create_voice_runtime(call(7), telephony=telephony("pipecat"), service=object(), listener=object())

    assert type(runtime).__name__ == "PipecatVoiceRuntime" and calls == []


def test_vad_observe_creates_a_silero_analyzer_set_for_8khz_with_pipecats_default_thresholds():
    analyzer = create_silero_analyzer()

    try:
        assert type(analyzer).__name__ == "SileroVADAnalyzer"
        assert analyzer._init_sample_rate == 8000
        assert (analyzer.params.confidence, analyzer.params.start_secs, analyzer.params.stop_secs) == (0.7, 0.2, 0.2)
        analyzer.set_sample_rate(8000)
        assert analyzer.num_frames_required() == 256, "Silero's 8 kHz window: no resampling"
    finally:
        asyncio.run(analyzer.cleanup())


async def test_the_observing_runtime_is_built_with_a_real_silero_analyzer_by_the_factory():
    import dataclasses as dc

    tel = dc.replace(telephony("pipecat"), voice_pipecat_vad="observe")
    runtime = create_voice_runtime(call(7), telephony=tel, service=object(), listener=object())

    try:
        assert type(runtime).__name__ == "PipecatVoiceRuntime" and type(runtime._vad_analyzer).__name__ == "SileroVADAnalyzer"
    finally:
        await runtime._vad_analyzer.cleanup()


async def test_a_vad_start_event_is_observed(monkeypatch):
    rig_ = observing_rig(monkeypatch)
    await rig_.start()
    await rig_.greeted()
    await play(rig_.link, talking(10))
    await until(lambda: "speech_started" in kinds(rig_.runtime))
    (started, *_) = rig_.runtime.vad_observations

    assert started == VadObservation("speech_started", started.at) and started.duration is None and started.at > 0
    await rig_.cleanup()


async def test_a_vad_stop_event_is_observed_with_how_long_the_speech_lasted(monkeypatch):
    rig_ = observing_rig(monkeypatch)
    await rig_.start()
    await rig_.greeted()
    await play(rig_.link, talking(10) + quiet(10), gap=0.01)
    await until(lambda: kinds(rig_.runtime) == ["speech_started", "speech_stopped"])
    started, stopped = rig_.runtime.vad_observations

    assert stopped.at > started.at and stopped.duration is not None and 0 < stopped.duration <= stopped.at - started.at + 0.05
    await rig_.cleanup()


def vowel_like(seconds: float, sr: int = 8000, f0: float = 130.0) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    x = np.zeros_like(t)

    for h in range(1, int(3400 / f0)):
        f = f0 * h * (1 + 0.02 * np.sin(2 * np.pi * 5 * t))
        env = sum(np.exp(-(((f - c) / bw) ** 2)) for c, bw in ((700, 130), (1220, 150), (2600, 200)))
        x += env * np.sin(2 * np.pi * np.cumsum(f) / sr) / h**0.3

    x *= 0.5 + 0.5 * np.sin(2 * np.pi * 3.5 * t)
    return (x / np.abs(x).max() * 0.6 * 32767).astype(np.int16)


async def test_the_real_silero_model_at_8khz_observes_speech_like_audio_start_and_stop():
    """A synthetic vowel-like signal, not a recording: enough to show the real model runs at 8 kHz inside the pipeline and
    that its events are observed. How it behaves on real speech is not claimed here."""
    rig_ = RuntimeRig()
    rig_.runtime = PipecatVoiceRuntime(service=rig_.service, listener=rig_.listener, speaker=rig_.speaker, vad="observe")
    await rig_.start()
    await rig_.greeted()
    signal = np.concatenate([np.zeros(8000, np.int16), vowel_like(1.5), np.zeros(16000, np.int16)]).tobytes()

    for i in range(0, len(signal), 320):
        rig_.link.feed(AudioFrame(signal[i : i + 320], 8000))
        await asyncio.sleep(0)

    await until(lambda: "speech_stopped" in kinds(rig_.runtime), timeout=8)

    assert kinds(rig_.runtime)[0] == "speech_started" and kinds(rig_.runtime)[-1] == "speech_stopped"
    assert rig_.runtime._vad_analyzer.sample_rate == 8000
    await rig_.cleanup()


async def test_observations_hold_only_a_kind_and_times_never_text_audio_or_anyone_identity(monkeypatch):
    marker = "zebra passphrase 4417"
    rig_ = observing_rig(monkeypatch, outbound=True)
    await rig_.start()
    await rig_.greeted()
    await play(rig_.link, talking(10) + quiet(10))
    rig_.hear(marker)
    await until(lambda: kinds(rig_.runtime) == ["speech_started", "speech_stopped"])
    await rig_.cleanup()

    assert {f.name for f in dataclasses.fields(VadObservation)} == {"kind", "at", "duration"}
    assert all(isinstance(o.at, float) and o.kind in ("speech_started", "speech_stopped") for o in rig_.runtime.vad_observations)
    everything = repr(rig_.runtime.vad_observations) + repr(vars(rig_.runtime._vad_observer))
    assert [w for w in (marker, "zebra", "Priya", "Northbridge", "9876543210", "JOB-P1") if w in everything] == []


async def pipeline_with_observer(analyzer, frames, extra=(), **observer_kwargs):
    observer = VadObserver(analyzer, **observer_kwargs)
    probe, got = Probe(), Collect(InputAudioRawFrame)

    async def driver(worker):
        for frame in frames:
            await worker.queue_frame(frame)

        await asyncio.sleep(0.6)
        await worker.stop_when_done()

    await run_pipeline([observer, probe, got, *extra], driver)
    return observer, probe, got


async def test_the_observer_passes_every_frame_on_untouched_and_emits_none_of_its_own():
    frames = input_frames(talking(10) + quiet(10))
    observer, probe, got = await pipeline_with_observer(ScriptedVad(), frames)

    assert [f.audio for f in got.frames] == [f.audio for f in frames], "every audio frame arrived, in order, unchanged"
    emitted = {n for n in probe.names() if n not in ("StartFrame", "EndFrame", "InputAudioRawFrame")}
    forbidden = {
        "VADUserStartedSpeakingFrame", "VADUserStoppedSpeakingFrame", "UserSpeakingFrame", "UserStartedSpeakingFrame",
        "UserStoppedSpeakingFrame", "InterruptionFrame", "ProposedUserStoppedSpeakingFrame", "TranscriptionFrame", "SpeechControlParamsFrame",
    }
    assert not emitted & forbidden, emitted & forbidden
    assert [o.kind for o in observer.observations] == ["speech_started", "speech_stopped"], "and it still observed"


async def test_a_slow_analyzer_cannot_delay_the_audio_which_is_passed_on_before_it_is_analysed():
    """Forty frames through an analyzer that takes 50 ms each: analysed inline they would take two seconds to get through."""
    frames = input_frames(talking(40))
    arrivals = []

    class Stamp(Collect):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)

            if isinstance(frame, InputAudioRawFrame):
                arrivals.append(time.monotonic())

    observer = VadObserver(ScriptedVad(delay=0.05))
    stamp = Stamp(InputAudioRawFrame)
    started = {}

    async def driver(worker):
        started["at"] = time.monotonic()

        for frame in frames:
            await worker.queue_frame(frame)

        await until(lambda: len(arrivals) == 40, timeout=5)
        started["done"] = time.monotonic()
        await worker.stop_when_done()

    await run_pipeline([observer, stamp], driver)

    assert len(arrivals) == 40 and started["done"] - started["at"] < 0.5


async def test_an_analyzer_that_falls_behind_has_audio_skipped_for_it_but_the_call_still_gets_all_of_it():
    frames = input_frames(talking(60))
    observer, _, got = await pipeline_with_observer(ScriptedVad(delay=0.05), frames, max_backlog=5)

    assert len(got.frames) == 60, "the call's own audio path lost nothing"
    assert observer.dropped_frames > 0 and not observer.failed


async def test_an_analyzer_that_fails_stops_observing_and_the_call_does_not_notice():
    frames = input_frames(talking(10))
    observer, _, got = await pipeline_with_observer(ScriptedVad(fail=True), frames)

    assert observer.failed and observer.observations == ()
    assert len(got.frames) == 10


async def test_vad_observation_never_calls_process_turn_however_the_vad_behaves(monkeypatch):
    calls = []
    real = processor_module.process_turn
    monkeypatch.setattr(processor_module, "process_turn", lambda *a, **k: calls.append(a) or real(*a, **k))
    rig_ = observing_rig(monkeypatch)
    await rig_.start()
    await rig_.greeted()
    await play(rig_.link, talking(10) + quiet(10) + talking(10) + quiet(10))
    await until(lambda: kinds(rig_.runtime).count("speech_stopped") == 2)
    await asyncio.sleep(0.15)

    assert calls == [] and rig_.brain.calls == []
    await rig_.cleanup()


async def observed_while_the_agent_is_speaking(monkeypatch):
    """The greeting takes about a second to play; the VAD hears the callee speak in the middle of it."""
    interrupted, superseded = [], []
    real_interrupt, real_supersede = Playout.interrupt, SessionProcessor._supersede
    monkeypatch.setattr(Playout, "interrupt", lambda self: interrupted.append(1) or real_interrupt(self))

    async def spy_supersede(self):
        superseded.append(1)
        return await real_supersede(self)

    monkeypatch.setattr(SessionProcessor, "_supersede", spy_supersede)
    rig_ = observing_rig(monkeypatch)
    rig_.link, rig_.speaker = TimedLink(latency=0.05), LongSpeaker()
    rig_.runtime = PipecatVoiceRuntime(service=rig_.service, listener=rig_.listener, speaker=rig_.speaker, vad="observe")
    await rig_.start()
    await until(lambda: rig_.link.times_of("audio"), 5)
    await play(rig_.link, talking(20) + quiet(10), gap=0.01)  # speech while the agent is still speaking
    await until(lambda: kinds(rig_.runtime) == ["speech_started", "speech_stopped"])
    await until(lambda: rig_.link.times_of("checkpoint"), 6)
    await asyncio.sleep(max(0.0, rig_.link.play_end + rig_.link.latency - time.monotonic()) + 0.1)  # the greeting played out
    return rig_, interrupted, superseded


async def test_vad_observation_does_not_cause_an_interruption(monkeypatch):
    rig_, interrupted, superseded = await observed_while_the_agent_is_speaking(monkeypatch)

    assert interrupted == [] and superseded == [], "nothing decided to interrupt"
    assert "playback_interrupted" not in audit_types(rig_) and "turn_aborted" not in audit_types(rig_)
    await rig_.cleanup()


async def test_vad_observation_does_not_clear_playback(monkeypatch):
    rig_, _, _ = await observed_while_the_agent_is_speaking(monkeypatch)

    assert rig_.link.times_of("clear") == [], "the agent was never cut off"
    assert not rig_.session.transcript[0].interrupted, "and its greeting is not marked as interrupted"
    await rig_.cleanup()


def session_snapshot(session) -> dict:
    return {
        "transcript": [(e.speaker, e.text, e.interrupted, e.blocked) for e in session.transcript],
        "history": [(t.role, t.text) for t in session.history],
        "audit": [a["type"] for a in session.audit],
        "pending": session.pending,
        "blocked": session.blocked_count,
        "executed": list(session.executed),
        "declined": list(session.declined),
        "topics": list(session.topics),
        "should_end": session.should_end,
        "ended": session.ended,
        "end_reason": session.end_reason,
    }


async def test_vad_observation_changes_no_session_state_even_while_it_sees_speech_around_every_utterance(monkeypatch):
    async def run(observe: bool):
        rig_ = observing_rig(monkeypatch) if observe else RuntimeRig()

        if not observe:
            rig_.runtime = PipecatVoiceRuntime(service=rig_.service, listener=rig_.listener, speaker=rig_.speaker)

        await rig_.start()
        await rig_.greeted()

        for said, expect in (("freeze my credit card", "yes to confirm"), ("yes please", "frozen")):
            before = rig_.count("checkpoint")
            await play(rig_.link, talking(6) + quiet(6))
            rig_.hear(said)
            await until(lambda e=expect: e in rig_.said(), 4)
            await until(lambda b=before: rig_.count("checkpoint") > b, 4)
            await asyncio.sleep(0.08)

        await rig_.callee_hangs_up()
        return session_snapshot(rig_.session), rig_.runtime.vad_observations

    off, seen_off = await run(False)
    on, seen_on = await run(True)

    assert on == off, "every part of the session is identical"
    assert seen_off == () and len(seen_on) >= 4, "and the observer really did see the speech"


# === lifecycle: startup, failure, shutdown ============================================================================


async def test_a_vad_that_cannot_be_created_falls_the_call_back_to_the_legacy_runtime_before_any_audio(monkeypatch, caplog):
    import app.pipecat_runtime.vad_observer as observer_module

    def broken(**kwargs):
        raise RuntimeError("the model file is missing")

    monkeypatch.setattr(observer_module, "SileroVADAnalyzer", broken)
    caplog.set_level(logging.INFO)
    listener = FakeListener()
    tel = dataclasses.replace(telephony("pipecat"), voice_pipecat_vad="observe")
    runtime = create_voice_runtime(call(7), telephony=tel, service=object(), listener=listener)

    assert type(runtime) is LegacyVoiceRuntime
    assert "pipecat unavailable (RuntimeError: the model file is missing), using legacy" in caplog.text
    assert listener.audio == [], "no audio had been processed"


async def test_a_failing_analyzer_after_audio_has_started_does_not_move_the_call_to_another_runtime(monkeypatch):
    rig_ = observing_rig(monkeypatch, analyzer=lambda: ScriptedVad(fail=True))
    await rig_.start()
    await rig_.greeted()
    await play(rig_.link, talking(5))
    await until(lambda: rig_.runtime._vad_observer.failed)
    rig_.hear("what is my balance")
    await until(lambda: "You said: what is my balance." in rig_.said())

    assert type(rig_.runtime) is PipecatVoiceRuntime and not rig_.session.ended, "it carried on, as the same runtime"
    await rig_.cleanup()


async def test_shutdown_releases_the_analyzers_worker_thread_its_tasks_and_its_model(caplog):
    caplog.set_level(logging.WARNING, logger="pipecat")
    before_threads = {t.ident for t in threading.enumerate()}
    before_tasks = set(asyncio.all_tasks())

    async def one_call() -> weakref.ref:
        rig_ = RuntimeRig()
        rig_.runtime = PipecatVoiceRuntime(service=rig_.service, listener=rig_.listener, speaker=rig_.speaker, vad="observe")
        analyzer = rig_.runtime._vad_analyzer
        await rig_.start()
        await rig_.greeted()
        await play(rig_.link, talking(10) + quiet(10))
        await asyncio.sleep(0.3)  # the executor thread has run
        observer = rig_.runtime._vad_observer
        await rig_.callee_hangs_up()

        assert analyzer._executor._shutdown, "the analyzer's worker thread was shut down"
        assert observer._task is None and observer._controller._audio_idle_task is None, "its tasks were stopped"
        return weakref.ref(analyzer._model)

    model = await one_call()
    await asyncio.sleep(0.3)

    # Tasks first, BEFORE any garbage collection: a collected orphan would vanish from all_tasks() and hide the leak
    # (which is how a first version of this test let a leaking observer through).
    leftover = [t.get_name() for t in asyncio.all_tasks() - before_tasks if not t.done() and t is not asyncio.current_task()]
    assert leftover == [], leftover
    assert "dangling" not in caplog.text, "Pipecat itself reports no task left running"

    gc.collect()

    assert model() is None, "the ONNX model session was released"
    stray_threads = [t.name for t in threading.enumerate() if t.ident not in before_threads and t.name.startswith("ThreadPoolExecutor")]
    assert stray_threads == []


async def test_a_call_cancelled_mid_stream_also_releases_the_analyzer(monkeypatch):
    analyzers = []
    rig_ = observing_rig(monkeypatch, analyzer=lambda: analyzers.append(ScriptedVad()) or analyzers[-1])
    await rig_.start()
    await rig_.greeted()
    await play(rig_.link, talking(10))
    rig_.task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await rig_.task

    await until(lambda: rig_.session.ended)
    assert analyzers[0]._executor._shutdown


async def test_the_runtime_releases_the_analyzer_itself_when_the_pipeline_never_got_to_run_the_observers_cleanup(monkeypatch):
    """The second layer: if the pipeline cannot even start, the observer's own cleanup never runs, so the runtime frees
    the analyzer it created."""
    analyzers = []

    class CannotStart(runtime_module.WorkerRunner):
        async def run(self, *args, **kwargs):
            raise RuntimeError("the pipeline could not start")

    monkeypatch.setattr(runtime_module, "WorkerRunner", CannotStart)
    rig_ = observing_rig(monkeypatch, analyzer=lambda: analyzers.append(ScriptedVad()) or analyzers[-1])
    await rig_.start()

    with pytest.raises(RuntimeError, match="could not start"):
        await asyncio.wait_for(rig_.task, 15)  # the teardown waits, bounded, for a pipeline that never ran

    assert analyzers[0]._executor._shutdown
    assert rig_.session.ended, "and the call was still closed and recorded"


# === configuration and selection ==========================================================================================


def test_the_vad_setting_defaults_to_off_everywhere():
    assert Settings(_env_file=None).voice_pipecat_vad == "off"
    assert telephony().voice_pipecat_vad == "off"
    assert PipecatVoiceRuntime(service=None, listener=None, speaker=None)._vad_analyzer is None


def test_the_vad_setting_is_read_from_the_environment_and_reaches_the_telephony_bundle(monkeypatch):
    import httpx

    from app.telephony import build_telephony

    monkeypatch.setenv("VOICE_PIPECAT_VAD", "observe")
    settings = Settings(
        _env_file=None, twilio_account_sid="AC1", twilio_auth_token="tok", twilio_from_number="+1",
        public_api_url="https://agent.example", deepgram_api_key="dg",
    )

    assert settings.voice_pipecat_vad == "observe"
    assert build_telephony(settings, httpx.AsyncClient()).voice_pipecat_vad == "observe"


@pytest.mark.parametrize("value", ["on", "true", "OBSERVE", "Observe", "", "enforce", "1"])
def test_an_invalid_vad_setting_is_rejected_at_startup(monkeypatch, value):
    from pydantic import ValidationError

    monkeypatch.setenv("VOICE_PIPECAT_VAD", value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_the_runtime_itself_refuses_a_mode_it_does_not_know():
    with pytest.raises(ValueError, match="unknown VAD mode"):
        PipecatVoiceRuntime(service=None, listener=None, speaker=None, vad="on")


def test_the_vad_setting_does_not_change_which_runtime_is_chosen(monkeypatch):
    created = []
    monkeypatch.setattr(runtime_module, "create_silero_analyzer", lambda: created.append(1) or ScriptedVad())

    for vad in ("off", "observe"):
        legacy = dataclasses.replace(telephony("legacy", {7}), voice_pipecat_vad=vad)
        listed = dataclasses.replace(telephony("pipecat", {7}), voice_pipecat_vad=vad)

        assert type(create_voice_runtime(call(7), telephony=legacy, service=object(), listener=object())) is LegacyVoiceRuntime
        assert type(create_voice_runtime(call(8), telephony=listed, service=object(), listener=object())) is LegacyVoiceRuntime, "another agent"
        assert type(create_voice_runtime(call(7), telephony=listed, service=object(), listener=object())) is PipecatVoiceRuntime

    assert len(created) == 1, "the analyzer was created only for the one observing Pipecat call"


# === parity: legacy, Pipecat with VAD off, Pipecat with VAD observing ==============================================================


@pytest.mark.parametrize("name", list(PARITY_CONVERSATIONS))
async def test_the_conversation_is_identical_on_legacy_pipecat_vad_off_and_pipecat_vad_observing(name):
    spec = PARITY_CONVERSATIONS[name]
    legacy = await conversation(LegacyVoiceRuntime, **spec)
    off = await conversation(PipecatVoiceRuntime, **spec)
    observed = await conversation(partial(PipecatVoiceRuntime, vad="observe"), **spec)

    assert off == legacy
    assert observed == off == legacy
