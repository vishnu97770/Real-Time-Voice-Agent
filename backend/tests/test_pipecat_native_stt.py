"""Step 15: Pipecat's own Deepgram STT, as a second recognizer behind `VOICE_PIPECAT_STT=compat|native`.

The compatibility recognizer (`RecognizerProcessor`, wrapping the existing Deepgram Listener) is the reference
behavior and stays the default. `native` (`app/pipecat_runtime/native_stt.py`) wraps Pipecat's own
`DeepgramSTTService`, configured to match it: same model, endpointing, utterance-end, smart formatting and interim
results, `speech_final` and `UtteranceEnd` both read the same way. `SessionProcessor` receives the exact same three
kinds of frame from either one.

No real Deepgram network access: `tests/pipecat_native_stt_fake.py` replaces only the WebSocket connection, so
`DeepgramSTTService`'s and `_ParityDeepgramSTTService`'s own code (settings, message parsing, `_on_message`) runs for
real. `deepgram-sdk` and `pipecat-ai` are both required for this file; skipped, not failed, if either is absent."""

import asyncio
import dataclasses
import logging
import time
import types
from functools import partial

import pytest

pytest.importorskip("pipecat")
pytest.importorskip("deepgram")

from pipecat.frames.frames import InputAudioRawFrame, InterruptionFrame  # noqa: E402
from pipecat.services.deepgram.stt import DeepgramSTTService  # noqa: E402

import app.pipecat_runtime.native_stt as native_stt_module  # noqa: E402
import app.pipecat_runtime.runtime as runtime_module  # noqa: E402
from app.brains.base import TextDelta  # noqa: E402
from app.config import Settings  # noqa: E402
from app.pipecat_runtime.frames import (  # noqa: E402
    UninterruptibleInterimTranscriptionFrame,
    UninterruptibleProposedUserStoppedSpeakingFrame,
    UninterruptibleTranscriptionFrame,
)
from app.pipecat_runtime.native_stt import NativeSttAdapter, _EndOfUtterance, _ParityDeepgramSTTService, build_native_stt
from app.pipecat_runtime.runtime import PipecatVoiceRuntime
from app.pipecat_runtime.session_processor import SessionProcessor
from app.pipecat_runtime.playout import Playout
from app.profiles import get_profile
from app.session import create_session, open_call
from app.telephony.legacy_runtime import LegacyVoiceRuntime
from app.voice_runtime_factory import create_voice_runtime
from tests.helpers import ScriptedBrain
from tests.pipecat_helpers import Probe, run_pipeline
from tests.test_pipecat_transport import Collect
from tests.pipecat_native_stt_fake import FakeConnect, result, utterance_end, wire_fake_deepgram
from tests.test_pipecat_runtime import PARITY_CONVERSATIONS, audit_types, conversation
from tests.test_voice_runtime import RuntimeRig
from tests.test_voice_runtime_selection import call, telephony

DEEPGRAM_KWARGS = dict(api_key="fake-api-key", model="nova-3", endpointing_ms=400, utterance_end_ms=1000)  # build_native_stt(...)
RUNTIME_STT_KWARGS = dict(
    deepgram_api_key="fake-api-key", deepgram_stt_model="nova-3", deepgram_endpointing_ms=400, deepgram_utterance_end_ms=1000,
)  # PipecatVoiceRuntime(...)


@pytest.fixture(autouse=True)
async def environment(monkeypatch):
    """Every rig this file starts is shut down even if the test fails; a native-mode rig also gets its Deepgram
    connection faked automatically, unless the test wires its own."""
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


def native_rig(**kwargs) -> RuntimeRig:
    rig = RuntimeRig(**kwargs)
    rig.runtime = PipecatVoiceRuntime(service=rig.service, listener=rig.listener, speaker=rig.speaker, stt="native", **RUNTIME_STT_KWARGS)
    return rig


async def native_hear(rig, text: str, *, final: bool = True, speech_final: bool = True) -> None:
    """The native-mode equivalent of `rig.hear(...)`: feeds a Results message to the fake connection `environment`
    already wired up for this rig, exactly as the real Deepgram socket would deliver one."""
    from tests.test_telephony_pipeline import until

    await until(lambda: rig.native_fake.connection is not None)
    rig.native_fake.connection.feed(result(text, is_final=final, speech_final=speech_final))


# === 1-2: configuration =====================================================================================================


def test_the_stt_mode_defaults_to_compat():
    assert Settings(_env_file=None).voice_pipecat_stt == "compat"
    assert telephony().voice_pipecat_stt == "compat"
    assert PipecatVoiceRuntime(service=None, listener=None, speaker=None)._stt_mode == "compat"


@pytest.mark.parametrize("value", ["Native", "NATIVE", "on", "legacy", "", "1"])
def test_an_invalid_stt_mode_is_rejected_at_startup(monkeypatch, value):
    from pydantic import ValidationError

    monkeypatch.setenv("VOICE_PIPECAT_STT", value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_the_runtime_itself_refuses_an_stt_mode_it_does_not_know():
    with pytest.raises(ValueError, match="unknown STT mode"):
        PipecatVoiceRuntime(service=None, listener=None, speaker=None, stt="realtime")


# === 3-4: selection ==========================================================================================================


async def test_pipecat_plus_compat_selects_the_existing_recognizerprocessor():
    """The DEFAULT Pipecat runtime (no stt= given) still builds RecognizerProcessor, not native STT: proved both by
    the runtime's own mode and by running a turn through the compat Listener's own event-injection path (`hear`),
    which only a running RecognizerProcessor consumes."""
    pipecat_rig = RuntimeRig()
    pipecat_rig.runtime = PipecatVoiceRuntime(service=pipecat_rig.service, listener=pipecat_rig.listener, speaker=pipecat_rig.speaker)

    assert pipecat_rig.runtime._stt_mode == "compat" and pipecat_rig.runtime._native_stt is None

    await pipecat_rig.start()
    await pipecat_rig.greeted()
    pipecat_rig.hear("what is my balance")
    from tests.test_telephony_pipeline import until

    await until(lambda: "You said: what is my balance." in pipecat_rig.said())
    assert not pipecat_rig.listener.closed, "compat mode never closes the Listener early (only native mode does)"
    await pipecat_rig.cleanup()


async def test_pipecat_plus_native_selects_the_native_adapter_not_the_compat_recognizer():
    rig = native_rig()
    await rig.start()
    await rig.greeted()

    assert rig.runtime._stt_mode == "native"
    assert not rig.listener.audio, "the compat Listener never receives audio in native mode"
    await rig.cleanup()


def test_legacy_runtime_ignores_native_stt_configuration():
    tel = telephony("legacy")
    tel = dataclasses.replace(tel, voice_pipecat_stt="native", deepgram_api_key="fake")
    runtime = create_voice_runtime(call(7), telephony=tel, service=object(), listener=object())

    assert type(runtime) is LegacyVoiceRuntime


# === 5: import discipline ====================================================================================================


def test_native_stt_is_imported_only_when_selected(monkeypatch):
    import sys

    for name in list(sys.modules):
        if name.startswith(("app.pipecat_runtime.native_stt", "deepgram")):
            monkeypatch.delitem(sys.modules, name, raising=False)

    PipecatVoiceRuntime(service=None, listener=None, speaker=None)  # compat: default

    assert not [n for n in sys.modules if n.startswith("app.pipecat_runtime.native_stt")]

    PipecatVoiceRuntime(service=None, listener=None, speaker=None, stt="native", **RUNTIME_STT_KWARGS)

    assert "app.pipecat_runtime.native_stt" in sys.modules


# === 6-7: configuration reaches the real service =============================================================================


def test_native_deepgram_receives_the_configured_key_model_language_sample_rate_and_format_settings():
    service, _ = build_native_stt(api_key="the-real-key", model="nova-3-general", endpointing_ms=250, utterance_end_ms=900)

    assert service._client._client_wrapper.api_key == "the-real-key"
    assert service._settings.model == "nova-3-general"
    assert str(service._settings.language) == "en"
    assert service._encoding == "linear16" and service._channels == 1
    assert service._settings.endpointing == 250
    assert service._settings.utterance_end_ms == 900
    assert service._settings.smart_format is True and service._settings.punctuate is True and service._settings.interim_results is True
    assert service._settings.extra == {"vad_events": True}, "Deepgram's own requirement for UtteranceEnd messages"


async def test_the_connect_call_itself_carries_the_configured_settings_once_the_pipeline_is_running():
    service, adapter = build_native_stt(**DEEPGRAM_KWARGS)
    fake = wire_fake_deepgram(service)
    probe = Probe()

    async def driver(worker):
        await worker.queue_frame(InputAudioRawFrame(bytes(320), 8000, 1))
        await asyncio.sleep(0.1)
        await worker.stop_when_done()

    await run_pipeline([service, adapter, probe], driver)
    (kwargs,) = fake.calls

    assert kwargs["model"] == "nova-3" and kwargs["language"] == "en" and kwargs["encoding"] == "linear16"
    assert kwargs["sample_rate"] == "8000" and kwargs["channels"] == "1"
    assert kwargs["endpointing"] == "400" and kwargs["utterance_end_ms"] == "1000"
    assert kwargs["smart_format"] == "true" and kwargs["punctuate"] == "true" and kwargs["interim_results"] == "true"
    assert kwargs["vad_events"] == "true"
    assert fake.connection.sent_media == [bytes(320)], "the runtime's own PCM, unencoded"


# === 8-11: frame mapping and ordering ========================================================================================


async def run_native(script, sample_rate=8000):
    service, adapter = build_native_stt(**DEEPGRAM_KWARGS)
    wire_fake_deepgram(service, script=script)
    kept = Collect(
        UninterruptibleInterimTranscriptionFrame, UninterruptibleTranscriptionFrame, UninterruptibleProposedUserStoppedSpeakingFrame
    )

    async def driver(worker):
        await worker.queue_frame(InputAudioRawFrame(bytes(320), sample_rate, 1))
        await asyncio.sleep(0.25)
        await worker.stop_when_done()

    await run_pipeline([service, adapter, kept], driver)
    return [type(f).__name__ for f in kept.frames], kept


async def test_a_native_interim_transcript_becomes_the_existing_uninterruptible_interim_frame():
    order, kept = await run_native([result("hel", is_final=False)])

    assert order == ["UninterruptibleInterimTranscriptionFrame"]
    assert kept.frames[0].text == "hel"


async def test_a_native_final_transcript_becomes_the_existing_uninterruptible_final_frame():
    order, kept = await run_native([result("hello", is_final=True, speech_final=False)])

    assert order == ["UninterruptibleTranscriptionFrame"]
    assert kept.frames[0].text == "hello"


async def test_a_native_end_of_utterance_event_becomes_the_existing_uninterruptible_proposed_stopped_frame():
    order, _ = await run_native([utterance_end()])

    assert order == ["UninterruptibleProposedUserStoppedSpeakingFrame"]


async def test_speech_final_on_a_result_also_produces_the_end_of_utterance_frame_right_after_the_text():
    order, _ = await run_native([result("hi there", is_final=True, speech_final=True)])

    assert order == ["UninterruptibleTranscriptionFrame", "UninterruptibleProposedUserStoppedSpeakingFrame"]


async def test_an_empty_speech_final_result_still_produces_the_end_of_utterance_frame():
    """The edge case a naive design misses: stock DeepgramSTTService pushes nothing for an empty-text final message,
    but speech_final is still true on it, and the compatibility recognizer's own Transcript always carries that flag
    regardless of text. _ParityDeepgramSTTService reads message.speech_final directly, not through stock's frame."""
    order, _ = await run_native([result("", is_final=True, speech_final=True)])

    assert order == ["UninterruptibleProposedUserStoppedSpeakingFrame"]


async def test_transcript_ordering_is_deterministic_across_a_realistic_utterance():
    order, _ = await run_native(
        [
            result("hel", is_final=False),
            result("hello", is_final=False),
            result("hello there", is_final=True, speech_final=False),
            result("", is_final=True, speech_final=True),
            utterance_end(),
        ]
    )

    assert order == [
        "UninterruptibleInterimTranscriptionFrame",
        "UninterruptibleInterimTranscriptionFrame",
        "UninterruptibleTranscriptionFrame",
        "UninterruptibleProposedUserStoppedSpeakingFrame",  # from speech_final on the empty final
        "UninterruptibleProposedUserStoppedSpeakingFrame",  # from the separate UtteranceEnd message
    ]


async def test_a_second_utterance_after_the_first_produces_its_own_frames_in_order():
    order, _ = await run_native(
        [
            result("first question", is_final=True, speech_final=True),
            result("second question", is_final=True, speech_final=True),
        ]
    )

    assert order == [
        "UninterruptibleTranscriptionFrame",
        "UninterruptibleProposedUserStoppedSpeakingFrame",
        "UninterruptibleTranscriptionFrame",
        "UninterruptibleProposedUserStoppedSpeakingFrame",
    ]


# === 12-13: an interruption cannot silently drop a native transcript or end signal ===========================================


async def frames_delivered_across_an_interruption(frame_kind: str) -> list[str]:
    """Step 14's own proof, reused here: a slow SessionProcessor, an interruption mid-turn, and whether the frame
    queued behind the one being handled survives. The frames here come from the real native pipeline, not hand-built
    ones, so this also exercises `_ParityDeepgramSTTService` and `NativeSttAdapter`, not just the protection itself."""

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
    service, adapter = build_native_stt(**DEEPGRAM_KWARGS)

    if frame_kind == "transcript":
        # Both pieces, THEN the end signal: without it nothing ever reaches _jobs, and there is no turn to interrupt.
        events = [result("first words", is_final=True, speech_final=False), result("more words", is_final=True, speech_final=True)]
    else:
        events = [result("first words", is_final=True, speech_final=False), utterance_end()]

    wire_fake_deepgram(service, script=events)

    async def driver(worker):
        await asyncio.sleep(0.05)  # the greeting is under way and blocked: nothing can speak in this pipeline
        await worker.queue_frame(InputAudioRawFrame(bytes(320), 8000, 1))
        await asyncio.sleep(0.1)
        await worker.queue_frame(InterruptionFrame())  # arrives while the first frame is still being handled
        await asyncio.sleep(0.8)
        await worker.stop_when_done()

    await run_pipeline([service, adapter, processor], driver)
    return [c.text for c in brain.calls]


async def test_an_interruption_cannot_silently_drop_a_native_transcript_frame():
    heard = await frames_delivered_across_an_interruption("transcript")
    assert heard == ["first words more words"], heard


async def test_an_interruption_cannot_silently_drop_a_native_end_of_utterance_frame():
    heard = await frames_delivered_across_an_interruption("end")
    assert heard == ["first words"], heard


# === 14-17: no policy, no side effects in the native layer ===================================================================


def test_native_stt_module_never_calls_process_turn_or_touches_playback_or_the_session():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(native_stt_module.__file__).read_text())
    named = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}

    assert not named & {"process_turn", "Session", "mark_playback_interrupted"}
    assert not named & {"clear_playout", "checkpoint_playout", "apply_interrupt", "interrupt", "_supersede"}
    assert not named & {"InterruptionFrame"}, "the native layer never decides to interrupt anything"


async def test_native_stt_does_not_directly_call_process_turn():
    calls = []
    import app.pipecat_runtime.session_processor as processor_module

    real = processor_module.process_turn
    with_patch = lambda *a, **k: calls.append(a) or real(*a, **k)  # noqa: E731

    import unittest.mock

    with unittest.mock.patch.object(processor_module, "process_turn", with_patch):
        rig = native_rig()
        await rig.start()
        await rig.greeted()
        await rig.cleanup()

    assert calls == []


async def test_native_stt_does_not_trigger_an_interruption_on_its_own(monkeypatch):
    interrupted = []
    real = Playout.interrupt
    monkeypatch.setattr(Playout, "interrupt", lambda self: interrupted.append(1) or real(self))

    rig = native_rig(speaker_delay=0.05)
    await rig.start()
    await rig.greeted()
    await native_hear(rig, "tell me many things")
    from tests.test_telephony_pipeline import until

    await until(lambda: "Third sentence here." in rig.said())  # the WHOLE reply played: nothing cut it off
    await asyncio.sleep(0.2)

    # Playout.interrupt() itself also runs on every ordinary turn transition (SessionProcessor's own bookkeeping,
    # unrelated to native STT); what a spurious interruption would actually DO is clear audio already on the link.
    assert rig.count("clear") == 0, "nothing was ever cleared: native STT caused no real interruption"
    await rig.cleanup()


async def test_native_stt_does_not_call_clear_playout(monkeypatch):
    cleared = []
    rig = native_rig()
    real_clear = rig.link.clear_playout
    rig.link.clear_playout = lambda: (cleared.append(1), real_clear())[1]
    await rig.start()
    await rig.greeted()
    await native_hear(rig, "what is my balance")
    from tests.test_telephony_pipeline import until

    await until(lambda: "You said: what is my balance." in rig.said())

    assert cleared == []
    await rig.cleanup()


async def test_native_stt_does_not_modify_session_state_beyond_what_a_turn_normally_does():
    rig = native_rig()
    await rig.start()
    await rig.greeted()
    before = (rig.session.pending, rig.session.blocked_count, rig.session.should_end)
    await native_hear(rig, "what is my balance")
    from tests.test_telephony_pipeline import until

    await until(lambda: "You said: what is my balance." in rig.said())

    assert rig.session.pending is None and rig.session.blocked_count == before[1] and rig.session.should_end is False
    await rig.cleanup()


# === 18-19: SessionProcessor and digit normalisation are unchanged ===========================================================


async def test_spoken_digits_reach_process_turn_normalised_exactly_as_they_do_on_compat():
    said_words = "my reference number is one two three four five"
    rig = native_rig()
    await rig.start()
    await rig.greeted()
    await native_hear(rig, said_words)
    from tests.test_telephony_pipeline import until

    await until(lambda: rig.brain.calls)

    assert rig.brain.calls[0].text == "my reference number is 12345"
    await rig.cleanup()


async def native_conversation(steps, script=None, outbound=False, hang_up=True):
    """`conversation()` (test_pipecat_runtime.py), with the native-mode `native_hear` in place of `rig.hear`: that
    helper is shared with compat-only tests and is not the place to special-case native STT."""
    from tests.test_pipecat_runtime import brain_script as default_script

    rig = native_rig(script=script or default_script, outbound=outbound)
    await rig.start()
    await rig.greeted()

    for said, expect in steps:
        before = rig.count("checkpoint")
        await native_hear(rig, said)
        from tests.test_telephony_pipeline import until

        await until(lambda e=expect: e in rig.said(), 4)
        await until(lambda b=before: rig.count("checkpoint") > b, 4)
        await asyncio.sleep(0.08)

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
        "job": rig.repo.get_job("JOB-P1")["status"] if outbound else None,
        "result": rig.repo.get_result(session.id) is not None,
    }


async def test_sessionprocessor_behavior_is_unchanged_the_native_conversations_match_compat():
    for name in ("a question and an answer", "a guarded action is confirmed", "a wrong number"):
        spec = PARITY_CONVERSATIONS[name]
        compat = await conversation(PipecatVoiceRuntime, **spec)
        native = await native_conversation(**spec)

        assert native == compat, name


# === 20-21: VAD off / observe do not change native STT's business behavior ===================================================


async def test_native_stt_works_with_vad_off():
    rig = native_rig()
    await rig.start()
    await rig.greeted()
    await native_hear(rig, "what is my balance")
    from tests.test_telephony_pipeline import until

    await until(lambda: "You said: what is my balance." in rig.said())
    await rig.cleanup()


async def test_native_stt_works_with_vad_observing_and_business_behavior_is_unchanged():
    rig = RuntimeRig()
    rig.runtime = PipecatVoiceRuntime(service=rig.service, listener=rig.listener, speaker=rig.speaker, stt="native", vad="observe", **RUNTIME_STT_KWARGS)
    await rig.start()
    await rig.greeted()
    await native_hear(rig, "what is my balance")
    from tests.test_telephony_pipeline import until

    await until(lambda: "You said: what is my balance." in rig.said())
    await asyncio.sleep(0.1)

    assert rig.runtime._vad_observer is not None  # it really was built and attached
    await rig.cleanup()


async def test_vad_off_and_vad_observe_give_the_same_business_result_with_native_stt():
    async def run(vad):
        rig = RuntimeRig(outbound=True)
        rig.runtime = PipecatVoiceRuntime(service=rig.service, listener=rig.listener, speaker=rig.speaker, stt="native", vad=vad, **RUNTIME_STT_KWARGS)
        await rig.start()
        await rig.greeted()
        await native_hear(rig, "yes speaking")
        from tests.test_telephony_pipeline import until

        await until(lambda: rig.brain.calls)
        await rig.callee_hangs_up()
        return (rig.session.outbound.identity, [c.text for c in rig.brain.calls])

    off, observe = await run("off"), await run("observe")
    assert off == observe


# === 22-23: failure containment and cleanup ===================================================================================


async def test_native_stt_construction_failure_falls_back_to_the_compatibility_recognizer_before_audio(monkeypatch, caplog):
    def broken(**kwargs):
        raise RuntimeError("deepgram-sdk is not installed")

    monkeypatch.setattr(native_stt_module, "build_native_stt", broken)
    caplog.set_level(logging.INFO)
    rig = RuntimeRig()
    rig.runtime = PipecatVoiceRuntime(service=rig.service, listener=rig.listener, speaker=rig.speaker, stt="native", **RUNTIME_STT_KWARGS)

    assert rig.runtime._stt_mode == "compat", "fallback decided in __init__, before the pipeline ever runs"
    await rig.start()
    await rig.greeted()
    rig.hear("what is my balance")
    from tests.test_telephony_pipeline import until

    await until(lambda: "You said: what is my balance." in rig.said())
    assert "native STT unavailable" in caplog.text and "using the compatibility recognizer" in caplog.text
    await rig.cleanup()


async def test_a_permanent_native_stt_failure_after_audio_ends_the_call_the_same_way_listener_lost_always_has():
    """Reproduces exactly what DeepgramSTTService's own connection-handler does when it gives up (a 4xx rejection, or
    repeated failures): report the error with force_treat_as_permanent=True, which VERIFIED (Step 15 audit, reading
    pipecat-ai 1.11.0's own push_error_frame) marks the processor unusable before the ErrorFrame is even pushed. This
    tests OUR reaction to that documented mechanism, not Pipecat's own retry/backoff timing, which is Pipecat's to
    get right and not ours to re-prove."""
    rig = native_rig()
    await rig.start()
    await rig.greeted()

    from tests.test_telephony_pipeline import until

    await rig.runtime._native_stt.push_error("simulated permanent Deepgram failure", force_treat_as_permanent=True)
    await until(lambda: rig.session.ended, 5)

    assert rig.session.end_reason == "listener_lost" and "listener_lost" in audit_types(rig)
    assert rig.repo.get_result(rig.session.id) is not None


async def test_native_stt_shutdown_releases_its_connection_task():
    rig = native_rig()
    await rig.start()
    await rig.greeted()
    service = rig.runtime._native_stt
    await rig.callee_hangs_up()
    await asyncio.sleep(0.1)

    assert service._connection is None and (service._connection_task is None or service._connection_task.done())


async def test_a_cancelled_native_call_leaves_no_connection_task_behind():
    before = set(asyncio.all_tasks())
    rig = native_rig()
    await rig.start()
    await rig.greeted()
    rig.task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await rig.task

    await asyncio.sleep(0.2)
    leftover = [t.get_name() for t in asyncio.all_tasks() - before if not t.done() and t is not asyncio.current_task()]
    assert leftover == [], leftover


# === Deepgram API keys are never logged (architecture item 14, the behavioral half) ==========================================


async def test_the_deepgram_api_key_never_reaches_the_logs_across_a_whole_call(caplog):
    distinctive_key = "sk-test-distinctive-3f9c8a1b7e2d4f6a"
    caplog.set_level(logging.DEBUG)
    rig = RuntimeRig()
    rig.runtime = PipecatVoiceRuntime(
        service=rig.service, listener=rig.listener, speaker=rig.speaker, stt="native",
        deepgram_api_key=distinctive_key, deepgram_stt_model="nova-3", deepgram_endpointing_ms=400, deepgram_utterance_end_ms=1000,
    )
    wire_fake_deepgram(rig.runtime._native_stt)
    await rig.start()
    await rig.greeted()
    await native_hear(rig, "what is my balance")
    from tests.test_telephony_pipeline import until

    await until(lambda: "You said: what is my balance." in rig.said())
    await rig.cleanup()

    assert distinctive_key not in caplog.text


# === 24: reversible through configuration =====================================================================================


async def test_native_stt_selection_is_reversible_through_configuration_alone():
    tel = telephony("pipecat")
    native = dataclasses.replace(tel, voice_pipecat_stt="native", deepgram_api_key="fake")
    compat_again = dataclasses.replace(native, voice_pipecat_stt="compat")

    runtime_native = create_voice_runtime(call(7), telephony=native, service=object(), listener=object())
    runtime_compat = create_voice_runtime(call(7), telephony=compat_again, service=object(), listener=object())

    assert runtime_native._stt_mode == "native" and runtime_compat._stt_mode == "compat"


# === the compat listener the route opens is closed at once when native mode is in effect =====================================


async def test_the_now_unneeded_compat_listener_is_closed_once_native_stt_takes_over():
    rig = native_rig()
    await rig.start()
    await asyncio.sleep(0.05)

    assert rig.listener.closed is True
    await rig.greeted()
    await rig.cleanup()
