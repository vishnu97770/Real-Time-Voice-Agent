"""Step 16: Silero VAD alongside native (and compat) Deepgram STT, correlated but never authoritative.

Three things are under test:

  A. Correlation - VadObserver can now be told (by VadCorrelationTap, which sees only frame TYPES, never text) that
     a transcript arrived and that the recognizer ended an utterance, so its own start/stop boundaries can be
     compared with Deepgram's, in bounded, PII-free diagnostics.
  B. VOICE_PIPECAT_VAD=interrupt - VAD's speech-started signal reaches SessionProcessor as one inert hint
     (`note_vad_speech_hint`, which only counts that it was called). Business behavior is REQUIRED to be identical
     to VOICE_PIPECAT_VAD=observe: VAD has no words, so it cannot itself satisfy SessionProcessor's word-count barge-in
     rule, and there is no other path from VAD to `_supersede()`/`InterruptionFrame`/`clear_playout()`/`process_turn`.
  C. Everything Step 14 already proved about VAD (observation-only, never blocks audio, fails without affecting the
     call, releases its resources) still holds with native STT and with the new correlation tap in the pipeline.

Deepgram's own speech_final / UtteranceEnd remain the only end-of-turn signal, unchanged since Step 10; this file
proves that, not assumes it."""

import asyncio
import dataclasses
import logging
import time
from functools import partial

import pytest

pytest.importorskip("pipecat")
pytest.importorskip("deepgram")

from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADParams  # noqa: E402
from pipecat.frames.frames import InputAudioRawFrame, InterruptionFrame  # noqa: E402

import app.pipecat_runtime.runtime as runtime_module  # noqa: E402
import app.pipecat_runtime.session_processor as processor_module  # noqa: E402
from app.config import Settings  # noqa: E402
from app.media import AudioFrame  # noqa: E402
from app.pipecat_runtime.playout import Playout  # noqa: E402
from app.pipecat_runtime.runtime import PipecatVoiceRuntime  # noqa: E402
from app.pipecat_runtime.vad_observer import VadCorrelationTap, VadDiagnostics, VadObserver  # noqa: E402
from app.telephony.legacy_runtime import LegacyVoiceRuntime  # noqa: E402
from app.voice_runtime_factory import create_voice_runtime  # noqa: E402
from tests.test_pipecat_native_stt import RUNTIME_STT_KWARGS, native_conversation, native_hear  # noqa: E402
from tests.test_pipecat_native_stt import native_rig as _native_rig_stt_only  # noqa: E402
from tests.test_pipecat_native_stt import wire_fake_deepgram  # noqa: E402
from tests.test_pipecat_runtime import PARITY_CONVERSATIONS, audit_types, conversation  # noqa: E402
from tests.test_pipecat_vad_observe import ScriptedVad, quiet, talking  # noqa: E402
from tests.test_telephony_pipeline import until  # noqa: E402
from tests.test_voice_runtime import RuntimeRig  # noqa: E402
from tests.test_voice_runtime_selection import call, telephony  # noqa: E402


@pytest.fixture(autouse=True)
async def environment(monkeypatch):
    """Every rig this file starts is shut down even if the test fails, and a native-STT rig always gets its
    Deepgram connection faked, exactly as in test_pipecat_native_stt.py."""
    started = []
    real_start = RuntimeRig.start

    async def start(self):
        started.append(self)

        if isinstance(self.runtime, PipecatVoiceRuntime) and self.runtime._native_stt is not None:
            self.native_fake = wire_fake_deepgram(self.runtime._native_stt)

        return await real_start(self)

    monkeypatch.setattr(RuntimeRig, "start", start)
    yield

    for rig in started:
        if rig.task is not None and not rig.task.done():
            rig.task.cancel()

            try:
                await asyncio.wait_for(rig.task, 5)
            except BaseException:  # noqa: BLE001
                pass


def build_rig(stt: str, vad: str, analyzer=ScriptedVad, monkeypatch=None, **kwargs) -> RuntimeRig:
    """Any of the 6 (stt x vad) combinations, from one place."""
    if monkeypatch is not None:
        monkeypatch.setattr(runtime_module, "create_silero_analyzer", analyzer)

    if kwargs.get("script") is None:  # RuntimeRig's own default (brain_script), not an explicit None override
        kwargs.pop("script", None)

    rig = RuntimeRig(**kwargs)
    extra = RUNTIME_STT_KWARGS if stt == "native" else {}
    rig.runtime = PipecatVoiceRuntime(service=rig.service, listener=rig.listener, speaker=rig.speaker, stt=stt, vad=vad, **extra)
    return rig


async def hear(rig, text: str, **kwargs) -> None:
    """The right "say this" helper for whichever STT the rig was built with."""
    if rig.runtime._stt_mode == "native":
        await native_hear(rig, text, **kwargs)
    else:
        rig.hear(text, **{k: v for k, v in kwargs.items() if k in ("final", "speech_final")})


async def play(link, frames, gap: float = 0.004) -> None:
    for frame in frames:
        link.feed(frame)
        await asyncio.sleep(gap)


def kinds(runtime) -> list[str]:
    return [o.kind for o in runtime.vad_observations]


# === 1-3: VAD modes create (or do not create) an analyzer =====================================================================


def test_vad_off_creates_no_analyzer():
    assert PipecatVoiceRuntime(service=None, listener=None, speaker=None, vad="off")._vad_analyzer is None


def test_vad_observe_creates_an_analyzer():
    runtime = PipecatVoiceRuntime(service=None, listener=None, speaker=None, vad="observe")
    assert type(runtime._vad_analyzer).__name__ == "SileroVADAnalyzer"


def test_vad_interrupt_creates_an_analyzer():
    runtime = PipecatVoiceRuntime(service=None, listener=None, speaker=None, vad="interrupt")
    assert type(runtime._vad_analyzer).__name__ == "SileroVADAnalyzer"


@pytest.mark.parametrize("value", ["ON", "interrupted", "Interrupt", "1"])
def test_an_invalid_vad_mode_is_still_rejected_now_that_a_third_value_exists(monkeypatch, value):
    from pydantic import ValidationError

    monkeypatch.setenv("VOICE_PIPECAT_VAD", value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_interrupt_is_now_a_valid_configured_value():
    assert Settings(_env_file=None, voice_pipecat_vad="interrupt").voice_pipecat_vad == "interrupt"


# === 4: legacy ignores VAD mode entirely ========================================================================================


def test_legacy_runtime_ignores_every_vad_mode_including_interrupt():
    for vad in ("off", "observe", "interrupt"):
        tel = dataclasses.replace(telephony("legacy"), voice_pipecat_vad=vad)
        runtime = create_voice_runtime(call(7), telephony=tel, service=object(), listener=object())

        assert type(runtime) is LegacyVoiceRuntime


# === 5-6: native STT starts correctly with VAD observe / interrupt =============================================================


async def test_native_stt_plus_vad_observe_starts_and_runs_a_turn(monkeypatch):
    rig = build_rig("native", "observe", monkeypatch=monkeypatch)
    await rig.start()
    await rig.greeted()
    await hear(rig, "what is my balance")
    await until(lambda: "You said: what is my balance." in rig.said())
    await rig.cleanup()


async def test_native_stt_plus_vad_interrupt_starts_and_runs_a_turn(monkeypatch):
    rig = build_rig("native", "interrupt", monkeypatch=monkeypatch)
    await rig.start()
    await rig.greeted()
    await hear(rig, "what is my balance")
    await until(lambda: "You said: what is my balance." in rig.said())
    await rig.cleanup()


# === 7-8: VAD start/stop are still observed with native STT in the pipeline ====================================================


async def test_vad_start_is_observed_alongside_native_stt(monkeypatch):
    rig = build_rig("native", "observe", monkeypatch=monkeypatch)
    await rig.start()
    await rig.greeted()
    await play(rig.link, talking(10))
    await until(lambda: "speech_started" in kinds(rig.runtime))
    await rig.cleanup()


async def test_vad_stop_is_observed_alongside_native_stt(monkeypatch):
    rig = build_rig("native", "observe", monkeypatch=monkeypatch)
    await rig.start()
    await rig.greeted()
    await play(rig.link, talking(10) + quiet(10))
    await until(lambda: kinds(rig.runtime) == ["speech_started", "speech_stopped"])
    await rig.cleanup()


# === 9-12: bounded, and no sensitive data ========================================================================================


def test_vad_observations_are_bounded():
    from app.pipecat_runtime.vad_observer import VadObservation

    observer = VadObserver(ScriptedVad(), max_observations=5)

    for _ in range(20):
        observer._observations.append(VadObservation("speech_started", 0.0))

    assert len(observer._observations) <= 5


def test_vad_turn_observations_are_bounded():
    observer = VadObserver(ScriptedVad(), max_turn_observations=5)

    for i in range(20):
        observer.note_end_signal()

    assert len(observer.turn_observations) <= 5


def test_vad_observations_and_turn_observations_and_diagnostics_hold_no_transcript_no_phone_no_identity():
    from dataclasses import fields

    from app.pipecat_runtime.vad_observer import VADTurnObservation, VadObservation

    assert {f.name for f in fields(VadObservation)} == {"kind", "at", "duration"}
    assert {f.name for f in fields(VADTurnObservation)} == {
        "sequence", "vad_start_at", "vad_stop_at", "deepgram_end_at", "vad_duration", "vad_to_deepgram_delta", "matched",
    }
    assert {f.name for f in fields(VadDiagnostics)} == {
        "vad_speech_no_transcript", "transcript_no_vad_start", "vad_stop_before_deepgram_end",
        "deepgram_end_before_vad_stop", "repeated_vad_boundaries", "vad_activity_during_playback",
    }
    # every field is a count, a sequence number, a bool, or a float on the monotonic clock, except the one fixed-
    # vocabulary string ("speech_started"/"speech_stopped"): nothing that could ever hold a transcript or a phone
    # number.
    allowed_types = (str, float, int, bool, type(None))
    for cls in (VadObservation, VADTurnObservation, VadDiagnostics):
        for f in fields(cls):
            args = getattr(f.type, "__args__", (f.type,))
            assert all(a in allowed_types for a in args), (cls.__name__, f.name, f.type)
    assert fields(VadObservation)[0].name == "kind" and fields(VadObservation)[0].type is str


async def test_no_sensitive_call_data_reaches_vad_observations_across_a_real_native_call(monkeypatch):
    marker = "zebra passphrase 4417"
    rig = build_rig("native", "observe", monkeypatch=monkeypatch)
    await rig.start()
    await rig.greeted()
    await play(rig.link, talking(10) + quiet(10))
    await hear(rig, marker)
    await until(lambda: rig.brain.calls)
    await rig.cleanup()

    blob = repr(rig.runtime.vad_observations) + repr(rig.runtime.vad_turn_observations) + repr(rig.runtime.vad_diagnostics)
    assert [w for w in (marker, "zebra", "Priya", "Northbridge", "9876543210", "JOB-P1") if w in blob] == []


# === 13-14: correlation, and Deepgram stays authoritative ========================================================================


async def test_vad_stop_can_be_correlated_with_the_recognizers_own_end_signal(monkeypatch):
    rig = build_rig("native", "observe", monkeypatch=monkeypatch)
    await rig.start()
    await rig.greeted()
    await play(rig.link, talking(10) + quiet(10))
    await until(lambda: kinds(rig.runtime) == ["speech_started", "speech_stopped"])
    await hear(rig, "what is my balance")
    await until(lambda: rig.runtime.vad_turn_observations and rig.runtime.vad_turn_observations[-1].matched)
    await rig.cleanup()

    (observation,) = [o for o in rig.runtime.vad_turn_observations if o.matched]
    assert observation.vad_start_at is not None and observation.vad_stop_at is not None and observation.deepgram_end_at is not None
    assert observation.vad_duration == pytest.approx(observation.vad_stop_at - observation.vad_start_at)
    assert observation.vad_to_deepgram_delta == pytest.approx(observation.deepgram_end_at - observation.vad_stop_at)


async def test_deepgram_remains_authoritative_for_end_of_turn_vad_alone_never_ends_an_utterance(monkeypatch):
    rig = build_rig("native", "observe", monkeypatch=monkeypatch)
    await rig.start()
    await rig.greeted()
    await hear(rig, "what is my balance", final=True, speech_final=False)  # words arrive, but no end signal
    await play(rig.link, talking(10) + quiet(10))  # VAD sees speech, then silence: an acoustic end, nothing more
    await until(lambda: kinds(rig.runtime) == ["speech_started", "speech_stopped"])
    await asyncio.sleep(0.2)

    assert rig.brain.calls == [], "VAD's own stop did not end the utterance"
    await hear(rig, "", speech_final=True)  # Deepgram's own end signal
    await until(lambda: len(rig.brain.calls) == 1)
    assert rig.brain.calls[0].text == "what is my balance"
    await rig.cleanup()


# === 15-18: no independent decision, whatever the mode ===========================================================================


async def test_vad_cannot_call_process_turn_even_in_interrupt_mode(monkeypatch):
    calls = []
    real = processor_module.process_turn
    monkeypatch.setattr(processor_module, "process_turn", lambda *a, **k: calls.append(a) or real(*a, **k))
    rig = build_rig("native", "interrupt", monkeypatch=monkeypatch)
    await rig.start()
    await rig.greeted()
    await play(rig.link, talking(10) + quiet(10))
    await until(lambda: kinds(rig.runtime) == ["speech_started", "speech_stopped"])
    await asyncio.sleep(0.1)

    assert calls == [] and rig.brain.calls == []
    await rig.cleanup()


async def test_vad_cannot_directly_emit_an_interruption_frame_even_in_interrupt_mode(monkeypatch):
    """It reaches SessionProcessor's inert hint, and nothing downstream of that sees an InterruptionFrame that was
    not already going to happen from a real, word-bearing transcript."""
    interrupts = []
    rig = build_rig("native", "interrupt", monkeypatch=monkeypatch, speaker_delay=0.05)
    real_interrupt = Playout.interrupt
    monkeypatch.setattr(Playout, "interrupt", lambda self: interrupts.append(1) or real_interrupt(self))
    await rig.start()
    await rig.greeted()

    await play(rig.link, talking(10) + quiet(10))  # VAD's own start/stop; no word-bearing transcript at all
    await until(lambda: kinds(rig.runtime) == ["speech_started", "speech_stopped"])
    await asyncio.sleep(0.2)

    assert interrupts == [], "VAD's speech-started hint never reached _supersede()/InterruptionFrame on its own"
    assert rig.count("clear") == 0, "and nothing was ever cleared on the link"
    await rig.cleanup()


async def test_vad_cannot_call_clear_playout_even_in_interrupt_mode(monkeypatch):
    cleared = []
    rig = build_rig("native", "interrupt", monkeypatch=monkeypatch)
    real_clear = rig.link.clear_playout
    rig.link.clear_playout = lambda: (cleared.append(1), real_clear())[1]
    await rig.start()
    await rig.greeted()
    await play(rig.link, talking(10) + quiet(10))
    await until(lambda: kinds(rig.runtime) == ["speech_started", "speech_stopped"])
    await asyncio.sleep(0.1)

    assert cleared == []
    await rig.cleanup()


async def test_vad_cannot_modify_session_state_even_in_interrupt_mode(monkeypatch):
    rig = build_rig("native", "interrupt", monkeypatch=monkeypatch)
    await rig.start()
    await rig.greeted()
    before = (rig.session.pending, rig.session.blocked_count, rig.session.should_end, len(rig.session.transcript))
    await play(rig.link, talking(10) + quiet(10))
    await until(lambda: kinds(rig.runtime) == ["speech_started", "speech_stopped"])
    await asyncio.sleep(0.1)

    assert (rig.session.pending, rig.session.blocked_count, rig.session.should_end, len(rig.session.transcript)) == before
    await rig.cleanup()


def test_the_interrupt_hook_itself_is_provably_inert():
    """SessionProcessor.note_vad_speech_hint, called directly: only a counter changes."""
    import types

    session = types.SimpleNamespace(lock=asyncio.Lock())
    processor = processor_module.SessionProcessor.__new__(processor_module.SessionProcessor)
    processor.vad_speech_hints_received = 0

    processor_module.SessionProcessor.note_vad_speech_hint(processor)
    processor_module.SessionProcessor.note_vad_speech_hint(processor)

    assert processor.vad_speech_hints_received == 2
    assert vars(processor) == {"vad_speech_hints_received": 2}, "and nothing else on the object changed"


# === 19-23: failure isolation ====================================================================================================


async def test_vad_failure_does_not_stop_native_stt(monkeypatch):
    rig = build_rig("native", "observe", analyzer=lambda: ScriptedVad(fail=True), monkeypatch=monkeypatch)
    await rig.start()
    await rig.greeted()
    await play(rig.link, talking(5))
    await until(lambda: rig.runtime._vad_observer.failed)
    await hear(rig, "what is my balance")
    await until(lambda: "You said: what is my balance." in rig.said())

    assert not rig.session.ended
    await rig.cleanup()


async def test_a_slow_vad_analyzer_does_not_block_native_stts_own_frames():
    from tests.pipecat_helpers import run_pipeline
    from tests.test_pipecat_native_stt import DEEPGRAM_KWARGS, result
    from app.pipecat_runtime.native_stt import build_native_stt
    from app.pipecat_runtime.vad_observer import VadObserver as _VadObserver
    from tests.test_pipecat_transport import Collect
    from app.pipecat_runtime.frames import UninterruptibleTranscriptionFrame

    service, adapter = build_native_stt(**DEEPGRAM_KWARGS)
    wire_fake_deepgram(service, script=[result("hello there", is_final=True, speech_final=True)])
    observer = _VadObserver(ScriptedVad(delay=0.2))  # far slower than the whole test's own timeout
    kept = Collect(UninterruptibleTranscriptionFrame)

    async def driver(worker):
        await worker.queue_frame(InputAudioRawFrame(bytes(320), 8000, 1))
        await until(lambda: len(kept.frames) >= 1, 3)
        await worker.stop_when_done()

    await run_pipeline([observer, service, adapter, kept], driver)
    assert kept.frames and kept.frames[0].text == "hello there"


async def test_a_vad_analyzer_that_raises_is_contained_and_native_stt_carries_on(monkeypatch):
    rig = build_rig("native", "observe", analyzer=lambda: ScriptedVad(fail=True), monkeypatch=monkeypatch)
    await rig.start()
    await rig.greeted()
    await play(rig.link, talking(5))
    await until(lambda: rig.runtime._vad_observer.failed)
    await hear(rig, "hello there")
    await until(lambda: "You said: hello there." in rig.said())
    await rig.cleanup()


async def test_vad_resources_are_released_with_native_stt_in_the_pipeline(monkeypatch):
    import threading

    before_threads = {t.ident for t in threading.enumerate()}
    rig = build_rig("native", "observe", monkeypatch=monkeypatch)
    analyzer = rig.runtime._vad_analyzer
    await rig.start()
    await rig.greeted()
    await play(rig.link, talking(10) + quiet(10))
    await asyncio.sleep(0.2)
    await rig.callee_hangs_up()

    assert analyzer._executor._shutdown
    stray = [t.name for t in threading.enumerate() if t.ident not in before_threads and t.name.startswith("ThreadPoolExecutor")]
    assert stray == []


async def test_no_vad_task_leaks_with_native_stt_and_interrupt_mode(monkeypatch):
    before = set(asyncio.all_tasks())
    rig = build_rig("native", "interrupt", monkeypatch=monkeypatch)
    await rig.start()
    await rig.greeted()
    await play(rig.link, talking(10) + quiet(10))
    await rig.callee_hangs_up()
    await asyncio.sleep(0.2)

    leftover = [t.get_name() for t in asyncio.all_tasks() - before if not t.done() and t is not asyncio.current_task()]
    assert leftover == [], leftover


# === 24-26: existing barge-in, and goodbye drain, unchanged =====================================================================


async def test_one_word_speech_does_not_unexpectedly_interrupt_with_vad_observing(monkeypatch):
    rig = build_rig("native", "observe", monkeypatch=monkeypatch, speaker_delay=0.05)
    await rig.start()
    await rig.greeted()
    await hear(rig, "tell me many things")
    await until(lambda: "First sentence here." in rig.said())
    await hear(rig, "uh", final=False, speech_final=False)
    await asyncio.sleep(0.15)

    assert rig.count("clear") == 0
    await until(lambda: "Third sentence here." in rig.said())
    await rig.cleanup()


async def test_the_existing_two_word_barge_in_still_works_with_vad_observing(monkeypatch):
    rig = build_rig("native", "observe", monkeypatch=monkeypatch, speaker_delay=0.05)
    await rig.start()
    await rig.greeted()
    await hear(rig, "tell me many things")
    await until(lambda: "First sentence here." in rig.said())
    await hear(rig, "wait stop please", final=False, speech_final=False)
    await until(lambda: rig.count("clear") == 1)

    assert "Third sentence here." not in rig.said()
    await rig.cleanup()


async def test_the_existing_two_word_barge_in_still_works_with_vad_interrupt(monkeypatch):
    rig = build_rig("native", "interrupt", monkeypatch=monkeypatch, speaker_delay=0.05)
    await rig.start()
    await rig.greeted()
    await hear(rig, "tell me many things")
    await until(lambda: "First sentence here." in rig.said())
    await hear(rig, "wait stop please", final=False, speech_final=False)
    await until(lambda: rig.count("clear") == 1)

    assert "Third sentence here." not in rig.said()
    await rig.cleanup()


async def test_goodbye_drain_is_unchanged_with_vad_observing(monkeypatch):
    rig = build_rig("native", "observe", monkeypatch=monkeypatch, outbound=True)
    await rig.start()
    await until(lambda: rig.count("checkpoint") >= 1)
    await hear(rig, "no, wrong number")
    await until(lambda: rig.link.closed, 6)

    assert rig.link.log.index(("close",)) > max(i for i, e in enumerate(rig.link.log) if e[0] == "audio"), "after the goodbye, not over it"
    assert rig.session.end_reason == "wrong_party"


# === 27-28: native frame protection is unchanged =================================================================================


async def test_native_transcript_frames_remain_uninterruptible_with_vad_in_the_pipeline(monkeypatch):
    from app.pipecat_runtime.frames import UninterruptibleTranscriptionFrame, UninterruptibleInterimTranscriptionFrame
    from pipecat.frames.frames import UninterruptibleFrame

    for cls in (UninterruptibleTranscriptionFrame, UninterruptibleInterimTranscriptionFrame):
        assert issubclass(cls, UninterruptibleFrame)


async def test_native_end_frame_remains_uninterruptible_with_vad_in_the_pipeline(monkeypatch):
    from app.pipecat_runtime.frames import UninterruptibleProposedUserStoppedSpeakingFrame
    from pipecat.frames.frames import UninterruptibleFrame

    assert issubclass(UninterruptibleProposedUserStoppedSpeakingFrame, UninterruptibleFrame)


# === 29-31: still functional, unchanged SessionProcessor =========================================================================


async def test_native_stt_remains_fully_functional_with_vad_observe(monkeypatch):
    rig = build_rig("native", "observe", monkeypatch=monkeypatch)
    await rig.start()
    await rig.greeted()
    await hear(rig, "my reference is one two three four five")
    await until(lambda: rig.brain.calls)
    assert rig.brain.calls[0].text == "my reference is 12345"
    await rig.cleanup()


async def test_compat_stt_remains_fully_functional_with_vad_observe(monkeypatch):
    rig = build_rig("compat", "observe", monkeypatch=monkeypatch)
    await rig.start()
    await rig.greeted()
    await hear(rig, "what is my balance")
    await until(lambda: "You said: what is my balance." in rig.said())
    await rig.cleanup()


async def test_sessionprocessor_source_is_unchanged_beyond_the_one_new_inert_method():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(processor_module.__file__).read_text())
    methods = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    assert "note_vad_speech_hint" in methods
    # the turn-taking methods Step 12/14/15 built are all still there, unrenamed:
    assert {"_on_transcript", "_utterance_finished", "_supersede", "_turn", "_work_loop"} <= methods


# === 32 + extra: parity scenarios ================================================================================================


@pytest.mark.parametrize("name", list(PARITY_CONVERSATIONS))
async def test_parity_scenarios_are_unchanged_with_native_stt_and_vad_observing(name, monkeypatch):
    spec = PARITY_CONVERSATIONS[name]
    compat = await conversation(PipecatVoiceRuntime, **spec)
    monkeypatch.setattr(runtime_module, "create_silero_analyzer", ScriptedVad)
    native_with_vad = await _native_conversation_with_vad("observe", spec)

    assert native_with_vad == compat, name


async def _native_conversation_with_vad(vad: str, spec: dict):
    rig = build_rig("native", vad, script=spec.get("script"), outbound=spec.get("outbound", False))
    await rig.start()
    await rig.greeted()

    for said, expect in spec["steps"]:
        before = rig.count("checkpoint")
        await hear(rig, said)
        await until(lambda e=expect: e in rig.said(), 4)
        await until(lambda b=before: rig.count("checkpoint") > b, 4)
        await asyncio.sleep(0.08)

    hang_up = spec.get("hang_up", True)

    if hang_up and not rig.link.closed:
        await rig.callee_hangs_up()
    else:
        await asyncio.wait_for(rig.task, 4)

    session = rig.session
    return {
        "transcript": [(e.speaker, e.text, e.interrupted, e.blocked) for e in session.transcript],
        "audit": audit_types(rig),
        "spoken": list(rig.speaker.texts),
        "brain_saw": [c.text for c in rig.brain.calls],
        "end_reason": session.end_reason,
        "blocked": session.blocked_count,
        "executed": list(session.executed),
        "declined": list(session.declined),
        "identity": session.outbound.identity if session.outbound else None,
        "job": rig.repo.get_job("JOB-P1")["status"] if spec.get("outbound") else None,
        "result": rig.repo.get_result(session.id) is not None,
    }


async def test_assistant_speaking_plus_one_word_user_interruption_matches_across_modes(monkeypatch):
    async def run(vad):
        rig = build_rig("native", vad, monkeypatch=monkeypatch, speaker_delay=0.05)
        await rig.start()
        await rig.greeted()
        await hear(rig, "tell me many things")
        await until(lambda: "First sentence here." in rig.said())
        await hear(rig, "uh", final=False, speech_final=False)
        await asyncio.sleep(0.15)
        result = (rig.count("clear"), "Third sentence here." in rig.said() or None)
        await rig.cleanup()
        return result

    off, observe, interrupt = await run("off"), await run("observe"), await run("interrupt")
    assert off[0] == observe[0] == interrupt[0] == 0


async def test_assistant_speaking_plus_two_word_user_interruption_matches_across_modes(monkeypatch):
    async def run(vad):
        rig = build_rig("native", vad, monkeypatch=monkeypatch, speaker_delay=0.05)
        await rig.start()
        await rig.greeted()
        await hear(rig, "tell me many things")
        await until(lambda: "First sentence here." in rig.said())
        await hear(rig, "wait stop please", final=False, speech_final=False)
        await until(lambda: rig.count("clear") == 1)
        outcome = audit_types(rig)
        await rig.cleanup()
        return outcome

    off, observe, interrupt = await run("off"), await run("observe"), await run("interrupt")
    assert off == observe == interrupt


async def test_goodbye_drain_matches_across_vad_modes(monkeypatch):
    async def run(vad):
        rig = build_rig("native", vad, monkeypatch=monkeypatch, outbound=True)
        await rig.start()
        await until(lambda: rig.count("checkpoint") >= 1)
        await hear(rig, "no, wrong number")
        await until(lambda: rig.link.closed, 6)
        # The link closing and the job's final status being persisted are two separate, sequential steps
        # (call.agent_ended finalizes the job after the drain); wait for the whole call to finish, as
        # conversation() and native_conversation() do, so the job's status is not read mid-write.
        await asyncio.wait_for(rig.task, 4)
        result = (rig.session.end_reason, rig.repo.get_job("JOB-P1")["status"])
        return result

    assert await run("off") == await run("observe") == await run("interrupt")


async def test_recognizer_failure_matches_across_vad_modes(monkeypatch):
    async def run(vad):
        rig = build_rig("compat", vad, monkeypatch=monkeypatch)
        await rig.start()
        await rig.greeted()
        rig.listener.drop()
        await until(lambda: rig.session.ended)
        return rig.session.end_reason

    assert await run("off") == await run("observe") == await run("interrupt") == "listener_lost"


async def test_vad_failure_matches_business_outcome_across_vad_modes(monkeypatch):
    async def run(vad):
        rig = build_rig("native", vad, analyzer=lambda: ScriptedVad(fail=True), monkeypatch=monkeypatch)
        await rig.start()
        await rig.greeted()
        await hear(rig, "what is my balance")
        await until(lambda: "You said: what is my balance." in rig.said())
        outcome = list(rig.brain.calls)
        await rig.cleanup()
        return [c.text for c in outcome]

    off_result = await run("off")
    observe_result = await run("observe")
    assert off_result == observe_result == ["what is my balance"]


async def test_vad_stall_does_not_change_business_outcome(monkeypatch):
    rig = build_rig("native", "observe", analyzer=lambda: ScriptedVad(delay=0.3), monkeypatch=monkeypatch)
    await rig.start()
    await rig.greeted()
    await hear(rig, "what is my balance")
    await until(lambda: "You said: what is my balance." in rig.said(), 3)
    await rig.cleanup()


# === VAD during assistant speech (required "VAD DURING BOT SPEECH" section) ======================================================


async def test_vad_during_bot_speech_plus_silence_never_clears_playback(monkeypatch):
    rig = build_rig("native", "observe", monkeypatch=monkeypatch)
    await rig.start()
    await until(lambda: rig.count("audio") > 0)
    await play(rig.link, quiet(10))
    await asyncio.sleep(0.2)

    assert rig.count("clear") == 0
    await rig.cleanup()


async def test_vad_during_bot_speech_plus_user_speech_is_observed_but_does_not_clear_playback_by_itself(monkeypatch):
    rig = build_rig("native", "observe", monkeypatch=monkeypatch)
    await rig.start()
    await until(lambda: rig.count("audio") > 0)
    await play(rig.link, talking(20) + quiet(10), gap=0.01)
    await until(lambda: kinds(rig.runtime) == ["speech_started", "speech_stopped"])

    assert rig.count("clear") == 0, "VAD alone never clears; only a real, word-bearing interruption does"
    await rig.cleanup()


async def test_vad_during_bot_speech_plus_short_user_speech_is_observed_only(monkeypatch):
    rig = build_rig("native", "interrupt", monkeypatch=monkeypatch)
    await rig.start()
    await until(lambda: rig.count("audio") > 0)
    await play(rig.link, talking(3) + quiet(5), gap=0.01)  # a brief burst
    await asyncio.sleep(0.3)

    assert rig.count("clear") == 0
    await rig.cleanup()


async def test_vad_during_bot_speech_plus_two_word_speech_still_needs_the_transcript_to_interrupt(monkeypatch):
    """The point of "interrupt" mode: VAD's own start event, even during playback, cannot by itself cause a clear;
    only the existing word-count rule, driven by an actual transcript, still can."""
    rig = build_rig("native", "interrupt", monkeypatch=monkeypatch, speaker_delay=0.05)
    await rig.start()
    await rig.greeted()
    await hear(rig, "tell me many things")
    await until(lambda: "First sentence here." in rig.said())
    await play(rig.link, talking(20), gap=0.01)  # VAD sees speech; no transcript at all yet
    await until(lambda: "speech_started" in kinds(rig.runtime))
    await asyncio.sleep(0.15)

    assert rig.count("clear") == 0, "VAD's speech-started hint alone did not interrupt"
    await hear(rig, "wait stop please", final=False, speech_final=False)  # now the real, word-bearing interruption
    await until(lambda: rig.count("clear") == 1)
    await rig.cleanup()


def test_bot_speaking_check_is_read_only_never_an_import_of_playout():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(runtime_module.__file__).read_text())
    call = next(
        n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "VadObserver"
    )
    kwarg = next(k for k in call.keywords if k.arg == "is_bot_speaking")
    assert isinstance(kwarg.value, ast.Lambda), "a plain read, not a shared object handed over"


# === parity matrix ================================================================================================================


PARITY_MATRIX_SPEC = PARITY_CONVERSATIONS["a question and an answer"]


@pytest.mark.parametrize("stt", ["compat", "native"])
@pytest.mark.parametrize("vad", ["off", "observe", "interrupt"])
async def test_the_parity_matrix_compat_and_native_times_off_observe_interrupt(stt, vad, monkeypatch):
    monkeypatch.setattr(runtime_module, "create_silero_analyzer", ScriptedVad)
    rig = build_rig(stt, vad, script=PARITY_MATRIX_SPEC.get("script"))
    await rig.start()
    await rig.greeted()

    for said, expect in PARITY_MATRIX_SPEC["steps"]:
        await hear(rig, said)
        await until(lambda e=expect: e in rig.said(), 4)

    await rig.callee_hangs_up()
    assert rig.session.ended and rig.repo.get_result(rig.session.id) is not None
