"""Step 12: the Pipecat voice runtime, run through the SAME scenarios as the legacy runtime.

    MediaLink (fake) <-> PipecatVoiceRuntime <-> Session.process_turn

`tests/test_voice_runtime.py` pins what the legacy runtime does for a call: the conversation is process_turn's, the
callee can talk over the agent, every way a call can end is recorded. The scenarios imported below are those very
tests, run unchanged except that the rig builds a `PipecatVoiceRuntime`. A scenario that passes on both runtimes is
the parity claim: the business behavior does not depend on which one carries the audio. Where the two differ on
purpose the difference is a test of its own further down, and it is a difference of timing or of how the end is
reported, never of what the conversation does.

No Twilio, no Deepgram, no network: fake link, fake recognizer, fake speaker, and a real Pipecat pipeline."""

import pytest

pytest.importorskip("pipecat")

from app.pipecat_runtime.runtime import PipecatVoiceRuntime  # noqa: E402
from tests import test_voice_runtime as scenarios  # noqa: E402
from tests.test_voice_runtime import (  # noqa: E402,F401  (fixture and the scenarios that are runtime-independent)
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


@pytest.fixture(autouse=True)
async def on_pipecat(monkeypatch):
    """Every rig in this file runs on the Pipecat runtime, and is always shut down afterwards: a test that fails
    halfway must not leave a pipeline running, or the event loop never finishes and the failure looks like a hang."""
    import asyncio

    started = []
    real_start = scenarios.RuntimeRig.start

    async def start(self):
        started.append(self)
        return await real_start(self)

    monkeypatch.setattr(scenarios.RuntimeRig, "start", start)
    monkeypatch.setattr(scenarios.RuntimeRig, "runtime_class", PipecatVoiceRuntime)
    yield

    for rig in started:
        if rig.task is not None and not rig.task.done():
            rig.task.cancel()

            try:
                await asyncio.wait_for(rig.task, 5)
            except BaseException:  # noqa: BLE001  (cancelled, or ended some other way: either way it is over)
                pass


# === what is specific to this runtime ====================================================================================

import asyncio  # noqa: E402
import logging  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402

import app.pipecat_runtime.runtime as runtime_module  # noqa: E402
import app.pipecat_runtime.session_processor as processor_module  # noqa: E402
from app.brains.base import Propose, TextDelta, ToolCall  # noqa: E402
from app.guardrails import spoken_digits_to_numerals  # noqa: E402
from app.media import AudioFrame  # noqa: E402
from app.telephony import mulaw  # noqa: E402
from app.telephony.deepgram import DeepgramError, Transcript, UtteranceEnd  # noqa: E402
from app.telephony.legacy_runtime import LegacyVoiceRuntime  # noqa: E402
from tests.pipecat_helpers import TimedLink  # noqa: E402
from tests.test_media import FakeMediaLink  # noqa: E402
from tests.test_telephony_pipeline import FakeSpeaker, brain_script, until  # noqa: E402
from tests.test_voice_runtime import RuntimeRig  # noqa: E402


def audit_types(rig) -> list[str]:
    return [entry["type"] for entry in rig.session.audit]


async def test_the_scenarios_above_really_run_on_the_pipecat_runtime(rig):
    """Guards the whole file: an import that silently kept the legacy runtime would make every parity claim vacuous."""
    assert type(rig.runtime) is PipecatVoiceRuntime
    await rig.greeted()
    assert "pipecat.pipeline.worker" in sys.modules and rig.link.sent(), "and the audio really left through a pipeline"


async def test_the_pipeline_is_configured_explicitly_not_left_to_pipecats_defaults(monkeypatch):
    seen = {}

    class SpyWorker(runtime_module.PipelineWorker):
        def __init__(self, pipeline, **kwargs):
            seen["worker"] = kwargs
            super().__init__(pipeline, **kwargs)

    class SpyRunner(runtime_module.WorkerRunner):
        def __init__(self, **kwargs):
            seen["runner"] = kwargs
            super().__init__(**kwargs)

    monkeypatch.setattr(runtime_module, "PipelineWorker", SpyWorker)
    monkeypatch.setattr(runtime_module, "WorkerRunner", SpyRunner)
    rig = await RuntimeRig().start()
    await rig.greeted()
    await rig.cleanup()

    params = seen["worker"]["params"]
    assert (params.audio_in_sample_rate, params.audio_out_sample_rate) == (8000, 8000), "not 16 kHz in / 24 kHz out"
    assert seen["worker"]["enable_rtvi"] is False and seen["worker"]["idle_timeout_secs"] is None
    assert seen["runner"] == {"handle_sigint": False}, "the server, not Pipecat, owns SIGINT"


async def test_a_frame_the_recognizer_cannot_take_ends_the_call_cleanly_and_is_recorded():
    rig = await RuntimeRig().start()
    await rig.greeted()
    rig.link.feed(AudioFrame(b"\x00\x00" * 160, 16000))
    await asyncio.wait_for(rig.task, 3)  # no exception here: the legacy runtime raises ValueError, this ends quietly

    assert rig.session.ended and rig.session.end_reason == "hangup" and rig.repo.get_result(rig.session.id) is not None


# --- the same conversation, on both runtimes, comes out the same ----------------------------------------------------------


async def conversation(runtime_class, steps, script=brain_script, outbound=False, hang_up=True):
    rig = await type("Rig", (RuntimeRig,), {"runtime_class": staticmethod(runtime_class)})(script=script, outbound=outbound).start()
    await rig.greeted()

    for said, expect in steps:
        before = rig.count("checkpoint")
        rig.hear(said)
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


PARITY_CONVERSATIONS = {
    "a question and an answer": dict(steps=[("what is my balance", "You said: what is my balance.")]),
    "a reply of several sentences": dict(steps=[("tell me many things", "Third sentence here.")]),
    "a pin read out as words is refused and never kept": dict(steps=[("my pin is one two three four", "never take passwords")]),
    "a guarded action is confirmed": dict(steps=[("freeze my credit card", "yes to confirm"), ("yes please", "frozen")]),
    "a guarded action is declined": dict(steps=[("freeze my credit card", "yes to confirm"), ("no thanks", "cancelled that")]),
    "an unclear answer to a confirmation asks again": dict(steps=[("freeze my credit card", "yes to confirm"), ("hmm", "still need a yes or no")]),
    "a wrong number": dict(steps=[("no, wrong number", "won't take any more of your time")], outbound=True, hang_up=False),
    "an unconfirmed identity is asked twice and then the call ends": dict(
        steps=[("what is this about", "Am I speaking with Priya Sharma?"), ("still not saying", "can't continue without confirming")],
        outbound=True,
        hang_up=False,
    ),
    "an identity confirmed and the conversation continues": dict(steps=[("yes speaking", "You said")], outbound=True),
}


@pytest.mark.parametrize("name", list(PARITY_CONVERSATIONS))
async def test_the_conversation_and_its_record_are_the_same_on_both_runtimes(name, monkeypatch):
    """Transcript, audit trail, what was spoken, what the brain was shown, tools, identity, end state, job outcome."""
    spec = PARITY_CONVERSATIONS[name]
    legacy = await conversation(LegacyVoiceRuntime, **spec)
    pipecat = await conversation(PipecatVoiceRuntime, **spec)

    assert pipecat == legacy


async def test_spoken_digits_reach_process_turn_as_numerals_exactly_as_they_do_on_the_legacy_runtime():
    said = "my reference number is one two three four five"
    seen = {}

    for runtime_class in (LegacyVoiceRuntime, PipecatVoiceRuntime):
        snapshot = await conversation(runtime_class, steps=[(said, "You said")])
        seen[runtime_class.__name__] = snapshot["brain_saw"]

    assert seen["PipecatVoiceRuntime"] == seen["LegacyVoiceRuntime"] == [spoken_digits_to_numerals(said)] == ["my reference number is 12345"]


async def test_pieces_of_one_utterance_are_merged_into_one_turn_with_digits_normalised_across_all_of_it(rig):
    await rig.greeted()
    rig.hear("my reference is one two", final=True, speech_final=False)
    rig.hear("three four five", final=True, speech_final=True)
    await until(lambda: rig.brain.calls)

    assert [c.text for c in rig.brain.calls] == ["my reference is 12345"]


# --- what is spoken is what process_turn said -----------------------------------------------------------------------------


async def test_what_is_spoken_is_exactly_the_sentences_process_turn_produced_and_nothing_else(rig):
    await rig.greeted()
    greeting = list(rig.speaker.texts)
    rig.hear("tell me many things")
    await until(lambda: "Third sentence here." in rig.said())
    await asyncio.sleep(0.1)

    assert rig.speaker.texts == [*greeting, "First sentence here.", "Second sentence here.", "Third sentence here."]
    reply = [e for e in rig.session.transcript if e.speaker == "Agent"][-1].text
    assert reply == "First sentence here. Second sentence here. Third sentence here.", "and the transcript agrees"


async def test_a_sentence_the_vetting_replaced_is_spoken_as_the_replacement_and_the_original_is_never_synthesised():
    from app.guardrails import AGENT_SOLICITATION_REPLACEMENT

    async def asks(ctx):
        yield TextDelta("Please tell me your PIN so I can check. Then we are done.")

    rig = await RuntimeRig(script=asks).start()
    await rig.greeted()
    rig.hear("hello there")
    await until(lambda: any("Then we are done" in t for t in rig.speaker.texts))

    assert AGENT_SOLICITATION_REPLACEMENT in rig.speaker.texts
    assert not [t for t in rig.speaker.texts if "tell me your PIN" in t], "the vetted-out sentence never reached the synthesiser"
    await rig.cleanup()


async def test_a_tool_the_brain_ran_is_recorded_and_the_reply_that_followed_is_spoken():
    async def script(ctx):
        result = await ctx.run_tool("get_balance", {})
        yield ToolCall("get_balance", {}, result)
        yield TextDelta("Your balance is on the screen.")

    rig = await RuntimeRig(script=script).start()
    await rig.greeted()
    rig.hear("what is my balance")
    await until(lambda: "Your balance is on the screen." in rig.said())

    assert any(e["type"] == "tool_call" and e["tool"] == "get_balance" for e in rig.session.audit)
    assert rig.session.topics, "the session recorded what the conversation covered"
    await rig.cleanup()


# --- the lock, the generator and cancellation ------------------------------------------------------------------------------


def slow_reply(closed):
    async def script(ctx):
        try:
            for i in range(30):
                yield TextDelta(f"Sentence {i} about {ctx.text.split()[0]}. ")
                await asyncio.sleep(0.05)
        finally:
            closed.append(ctx.text)

    return script


async def test_an_interrupted_turn_closes_its_generator_releases_the_session_lock_and_the_next_turn_runs():
    closed = []
    rig = await RuntimeRig(script=slow_reply(closed), speaker_delay=0.02).start()
    await rig.greeted()
    rig.hear("first question")
    await until(lambda: "Sentence 1 about first." in rig.said())

    rig.hear("wait stop please", final=False, speech_final=False)  # two words over the agent
    await until(lambda: closed == ["first question"])

    assert not rig.session.lock.locked(), "the aborted turn let go of the session"
    rig.hear("wait stop please")
    await until(lambda: "Sentence 0 about wait." in rig.said())
    assert "turn_aborted" in audit_types(rig) and "playback_interrupted" in audit_types(rig)
    await rig.cleanup()


async def test_a_call_that_ends_mid_reply_still_closes_the_generator_and_releases_the_lock():
    closed = []
    rig = await RuntimeRig(script=slow_reply(closed), speaker_delay=0.02).start()
    await rig.greeted()
    rig.hear("first question")
    await until(lambda: "Sentence 1 about first." in rig.said())

    await rig.callee_hangs_up()

    assert closed == ["first question"] and not rig.session.lock.locked() and rig.session.ended


async def test_a_new_utterance_while_the_reply_is_still_being_thought_up_replaces_it_as_the_legacy_runtime_does():
    """Nothing has been said yet, so there is nothing to clear; and because the aborted turn had produced no sentence
    there is no entry to mark aborted, on either runtime (process_turn's own rule, not the runtime's)."""

    async def slow(ctx):
        if "first" in ctx.text:
            await asyncio.sleep(2)

        yield TextDelta(f"Answer to {ctx.text}.")

    async def run(runtime_class):
        rig = await type("Rig", (RuntimeRig,), {"runtime_class": runtime_class})(script=slow).start()
        await rig.greeted()
        rig.hear("first question")
        await until(lambda: len(rig.brain.calls) == 1)
        rig.hear("second question")
        await until(lambda: "Answer to second question." in rig.said())
        await rig.cleanup()
        return audit_types(rig), rig.count("clear"), rig.speaker.texts[-1]

    legacy, pipecat = await run(LegacyVoiceRuntime), await run(PipecatVoiceRuntime)

    assert pipecat == legacy
    assert pipecat[1] == 0 and "playback_interrupted" not in pipecat[0], "nothing was playing"


async def test_interrupting_a_reply_that_had_begun_is_recorded_as_an_aborted_turn_and_an_interrupted_playback():
    rig = await RuntimeRig(script=slow_reply([]), speaker_delay=0.02).start()
    await rig.greeted()
    rig.hear("first question")
    await until(lambda: "Sentence 1 about first." in rig.said())
    rig.hear("second question")
    await until(lambda: "Sentence 0 about second." in rig.said())

    types = audit_types(rig)
    assert types.index("turn_aborted") < types.index("playback_interrupted"), "in the order the legacy runtime records them"
    assert rig.count("clear") == 1
    await rig.cleanup()


async def test_after_an_interruption_none_of_the_stale_reply_is_synthesised_or_sent():
    rig = await RuntimeRig(script=slow_reply([]), speaker_delay=0.02).start()
    await rig.greeted()
    rig.hear("first question")
    await until(lambda: "Sentence 2 about first." in rig.said())
    rig.hear("wait stop please", final=False, speech_final=False)
    await until(lambda: rig.count("clear") == 1)
    spoken_then, audio_then = list(rig.speaker.texts), rig.count("audio")
    await asyncio.sleep(0.4)

    assert rig.speaker.texts == spoken_then and rig.count("audio") == audio_then, "the stale reply was cut off, not finished"
    await rig.cleanup()


async def test_a_second_interim_result_does_not_interrupt_or_record_the_interruption_twice():
    rig = await RuntimeRig(script=slow_reply([]), speaker_delay=0.02).start()
    await rig.greeted()
    rig.hear("first question")
    await until(lambda: "Sentence 1 about first." in rig.said())
    rig.hear("wait stop", final=False, speech_final=False)
    rig.hear("wait stop please", final=False, speech_final=False)
    rig.hear("wait stop please now", final=False, speech_final=False)
    await asyncio.sleep(0.3)

    assert audit_types(rig).count("playback_interrupted") == 1 and rig.count("clear") == 1
    await rig.cleanup()


# --- the goodbye is heard before the line is dropped -----------------------------------------------------------------------


async def test_after_a_goodbye_the_line_is_held_until_the_callee_has_heard_it():
    rig = RuntimeRig(outbound=True)
    rig.link, rig.speaker = TimedLink(latency=0.25), LongSpeaker()  # a phone: each sentence takes a second to play
    rig.runtime = PipecatVoiceRuntime(service=rig.service, listener=rig.listener, speaker=rig.speaker)
    await rig.start()
    await until(lambda: rig.link.times_of("checkpoint"), 5)
    await asyncio.sleep(max(0.0, rig.link.play_end + rig.link.latency - time.monotonic()) + 0.05)  # the greeting has been heard

    rig.hear("no, wrong number")
    await until(lambda: rig.link.closed, 8)
    (closed_at,) = rig.link.times_of("close")

    assert closed_at >= rig.link.play_end - rig.link.t0 + rig.link.latency - 0.05, "not before the goodbye had played and been reported"
    assert max(rig.link.times_of("audio")) < closed_at
    assert rig.session.end_reason == "wrong_party"


async def test_without_a_report_from_the_link_the_goodbye_wait_ends_at_its_limit_and_the_line_is_dropped(monkeypatch):
    monkeypatch.setattr(processor_module, "GOODBYE_DRAIN_SECONDS", 0.5)
    rig = RuntimeRig(outbound=True)
    rig.link = FakeMediaLink(echo_playout=False)  # the link never says anything was heard
    await rig.start()
    await until(lambda: rig.count("checkpoint") >= 1)
    started = time.monotonic()
    rig.hear("no, wrong number")
    await until(lambda: rig.link.closed, 5)

    assert 0.4 <= time.monotonic() - started <= 2.0, "it waited for its limit, and no longer"
    assert rig.session.end_reason == "wrong_party"


async def test_the_goodbye_wait_limit_is_the_legacy_one():
    from app.telephony.pipeline import HANGUP_WAIT_SECONDS

    assert processor_module.GOODBYE_DRAIN_SECONDS == HANGUP_WAIT_SECONDS == 20


# --- pacing, at the level of a whole call --------------------------------------------------------------------------------------


class LongSpeaker(FakeSpeaker):
    """A synthesiser that hands over a full second of audio at once."""

    async def synthesize(self, text):
        self.texts.append(text)
        yield b"S:" + text.encode()

        for _ in range(5):
            yield b"\xff" * 1600  # 200 ms of 8 kHz mu-law each


async def test_a_reply_is_paced_and_its_checkpoint_follows_the_last_chunk_not_the_synthesis():
    rig = RuntimeRig()
    rig.link, rig.speaker = TimedLink(latency=0.0), LongSpeaker()
    rig.runtime = PipecatVoiceRuntime(service=rig.service, listener=rig.listener, speaker=rig.speaker)
    await rig.start()
    await until(lambda: rig.link.times_of("checkpoint"), 5)
    audio, (checkpoint,) = rig.link.times_of("audio"), rig.link.times_of("checkpoint")

    assert audio[-1] - audio[0] >= 0.85, "a second of greeting takes about a second to write, not an instant"
    assert checkpoint >= audio[-1], "and the callee is only asked to report after the last chunk"
    await rig.cleanup()


async def test_a_replys_last_partial_chunk_is_not_held_back_to_be_glued_onto_the_next_reply(rig):
    """Regression: without a TTSStoppedFrame per sentence, Pipecat keeps up to 20 ms of every reply in a buffer."""
    await rig.greeted()
    rig.hear("what is my balance")
    await until(lambda: "You said: what is my balance." in rig.said())
    await until(lambda: rig.count("checkpoint") >= 2)
    synthesised = sum(len(mulaw.decode(b"S:" + t.encode())) + 3 * 320 for t in rig.speaker.texts)
    written = sum(len(f.pcm) for f in rig.link.sent())

    assert synthesised <= written < synthesised + len(rig.speaker.texts) * 320, "everything synthesised was sent, plus padding of less than a chunk each"


# --- failures are recorded as they always were ------------------------------------------------------------------------------------


async def test_when_the_recognizer_is_lost_the_call_is_ended_and_the_loss_recorded(rig):
    await rig.greeted()
    rig.listener.drop()
    await until(lambda: rig.session.ended)

    assert "listener_lost" in audit_types(rig) and rig.session.end_reason == "listener_lost"


async def test_a_speech_failure_is_recorded_the_rest_of_that_reply_is_dropped_and_the_call_carries_on():
    async def script(ctx):
        if "boom" in ctx.text:
            yield TextDelta("EXPLODE here. Second sentence here. ")
        else:
            yield TextDelta("All fine.")

    rig = await RuntimeRig(script=script).start()
    await rig.greeted()
    rig.hear("boom please")
    await until(lambda: "speech_error" in audit_types(rig))
    await asyncio.sleep(0.1)

    (error,) = [e for e in rig.session.audit if e["type"] == "speech_error"]
    assert "402" in error["reason"], "the reason is recorded (never the text)"
    assert "Second sentence here." not in rig.speaker.texts, "the reply was abandoned where the speech failed"

    rig.hear("hello again")
    await until(lambda: "All fine." in rig.said())
    await rig.cleanup()


async def test_an_unexpected_failure_in_speech_is_recorded_as_a_pipeline_error_and_the_call_carries_on():
    class Broken(FakeSpeaker):
        async def synthesize(self, text):
            self.texts.append(text)

            if "BOOM" in text:
                raise RuntimeError("secret detail that must not be recorded")

            yield b"S:" + text.encode()

    async def script(ctx):
        yield TextDelta("BOOM now." if "boom" in ctx.text else "Still here.")

    rig = RuntimeRig(script=script)
    rig.speaker = Broken()
    rig.runtime = PipecatVoiceRuntime(service=rig.service, listener=rig.listener, speaker=rig.speaker)
    await rig.start()
    await rig.greeted()
    rig.hear("boom please")
    await until(lambda: "pipeline_error" in audit_types(rig))

    (error,) = [e for e in rig.session.audit if e["type"] == "pipeline_error"]
    assert error["reason"] == "RuntimeError" and "secret" not in repr(rig.session.audit)
    rig.hear("hello again")
    await until(lambda: "Still here." in rig.said())
    await rig.cleanup()


async def test_a_link_that_fails_to_take_audio_is_recorded_once_and_the_call_can_still_be_ended():
    class DeafLink(FakeMediaLink):
        fail = False

        async def send_audio(self, frame):
            if self.fail:
                raise ConnectionError("socket closed")

            await super().send_audio(frame)

    rig = RuntimeRig()
    rig.link = DeafLink()
    await rig.start()
    await rig.greeted()
    rig.link.fail = True
    rig.hear("tell me many things")
    await until(lambda: "pipeline_error" in audit_types(rig))
    await asyncio.sleep(0.2)

    assert audit_types(rig).count("pipeline_error") == 1
    await rig.callee_hangs_up()
    assert rig.session.ended and rig.repo.get_result(rig.session.id) is not None


async def test_a_hangup_that_fails_is_recorded_and_the_result_is_still_saved():
    class StubbornLink(FakeMediaLink):
        async def close(self):
            raise ConnectionError("cannot hang up")

    rig = RuntimeRig(outbound=True)
    rig.link = StubbornLink()
    await rig.start()
    await until(lambda: rig.count("checkpoint") >= 1)
    rig.hear("no, wrong number")
    await until(lambda: rig.session.ended, 6)
    rig.link.end()  # the callee's line goes when they do
    await asyncio.wait_for(rig.task, 4)

    assert "hangup_failed" in audit_types(rig) and rig.repo.get_result(rig.session.id) is not None


async def test_a_run_that_is_cancelled_ends_the_pipeline_and_leaves_no_task_behind():
    before = {t for t in asyncio.all_tasks()}
    rig = await RuntimeRig().start()
    await rig.greeted()
    rig.task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await rig.task

    await until(lambda: rig.session.ended)
    await asyncio.sleep(0.3)
    leftover = [t.get_name() for t in asyncio.all_tasks() - before if not t.done() and t is not asyncio.current_task()]

    assert leftover == [], leftover


# --- logging: Pipecat's own output carries the words of the call, and must not reach ours ------------------------------------------


def test_pipecats_log_lines_are_forwarded_at_warning_and_above_with_the_text_of_any_frame_taken_out(caplog):
    from loguru import logger

    caplog.set_level(logging.DEBUG, logger="pipecat")
    logger.debug("TranscriptionFrame#1(user: u, text: [debug line that must not appear])")
    logger.warning("dropped TranscriptionFrame#2(user: u, text: [my card is 4111 1111 1111 1111], language: None) for a reason")
    logger.error("SomethingFailed: transcript: [read out loud] and content=[more words]")

    lines = [r.getMessage() for r in caplog.records if r.name == "pipecat"]

    assert lines == [
        "dropped TranscriptionFrame#2(user: u, text: [redacted], language: None) for a reason",
        "SomethingFailed: transcript: [redacted] and content=[redacted]",
    ]
    assert "4111" not in caplog.text and "debug line" not in caplog.text


async def test_a_pipeline_error_is_recorded_by_kind_never_by_its_message():
    from pipecat.frames.frames import ErrorFrame

    session = types.SimpleNamespace(audit=[], log=lambda type_, **detail: session.audit.append({"type": type_, **detail}))
    call = runtime_module._Call(service=None, session=session, link=None, listener=None)
    await call.pipeline_error(ErrorFrame("the caller said: my pin is 1234", exception=ValueError("1234")))
    await call.pipeline_error(ErrorFrame("no exception attached"))

    assert session.audit == [{"type": "pipeline_error", "reason": "ValueError"}, {"type": "pipeline_error", "reason": "ErrorFrame"}]


# --- gaps the mutation run found, closed ---------------------------------------------------------------------------------------------


async def test_a_speech_failure_on_the_greeting_is_recorded_and_the_call_carries_on():
    class GreetingFails(FakeSpeaker):
        async def synthesize(self, text):
            self.texts.append(text)

            if len(self.texts) == 1:
                raise DeepgramError("text-to-speech failed with status 402")

            yield b"S:" + text.encode()

    async def script(ctx):
        yield TextDelta("Still here.")

    rig = RuntimeRig(script=script)
    rig.speaker = GreetingFails()
    rig.runtime = PipecatVoiceRuntime(service=rig.service, listener=rig.listener, speaker=rig.speaker)
    await rig.start()
    await until(lambda: "speech_error" in audit_types(rig))

    (error,) = [e for e in rig.session.audit if e["type"] == "speech_error"]
    assert "402" in error["reason"]
    rig.hear("hello there")
    await until(lambda: "Still here." in rig.said())
    await rig.cleanup()


async def test_the_link_is_cleared_only_after_the_writer_has_stopped_so_nothing_is_written_once_the_clear_begins():
    """A link whose clear takes a while makes the ordering visible: if the writer were still running, it would keep
    writing during the clear. (With a fast link the same mistake is a race that almost never shows.)"""
    from pipecat.frames.frames import InterruptionFrame

    from app.pipecat_runtime.playout import Playout
    from app.pipecat_runtime.transport import MediaLinkOutputTransport
    from tests.pipecat_helpers import run_pipeline
    from tests.test_pipecat_transport import feed_speech

    class SlowClearLink(TimedLink):
        cleared_at = None

        async def clear_playout(self):
            self.cleared_at = time.monotonic()
            await asyncio.sleep(0.15)
            await super().clear_playout()

    link, playout = SlowClearLink(), Playout()
    out = MediaLinkOutputTransport(link, playout, lambda error: None)

    async def driver(worker):
        await feed_speech(worker, 2000)
        await until(lambda: len(link.sent()) >= 10)
        playout.interrupt()
        await worker.queue_frame(InterruptionFrame())
        await until(lambda: link.cleared_at is not None)
        await asyncio.sleep(0.4)
        await worker.stop_when_done()

    await run_pipeline([out], driver)

    assert [t for t in link.times_of("audio") if link.t0 + t > link.cleared_at + 0.005] == [], "the writer had already stopped"


async def test_utterances_still_waiting_are_merged_and_an_unspoken_greeting_dropped_exactly_as_phonecall_does():
    from pipecat.frames.frames import TranscriptionFrame

    from app.pipecat_runtime.playout import Playout
    from app.pipecat_runtime.session_processor import SessionProcessor
    from app.telephony.pipeline import PhoneCall
    from tests.test_telephony_pipeline import FakeLine, FakeListener

    async def nothing(*args, **kwargs):
        return None

    legacy = PhoneCall(session=types.SimpleNamespace(), service=None, line=FakeLine(), listener=FakeListener(), speaker=FakeSpeaker())
    legacy._jobs.put_nowait(("say", "hello"))
    await legacy._on_event(Transcript("my number is one two", True, True))
    await legacy._on_event(Transcript("three four five", True, True))

    ours = SessionProcessor(session=types.SimpleNamespace(), playout=Playout(), greeting="hello", on_end=lambda reason: None)
    ours.push_frame = nothing  # no pipeline here: only what is queued for the next turn is looked at
    ours._jobs.put_nowait(("say", "hello"))

    for text in ("my number is one two", "three four five"):
        await ours._on_transcript(TranscriptionFrame(text, "", ""))
        await ours._utterance_finished()

    assert list(ours._jobs._queue) == list(legacy._jobs._queue) == [("user", "my number is 12345")]
