"""Step 10: the legacy voice runtime, PhoneCall behind the provider-neutral VoiceRuntime / MediaLink boundary.

    MediaLink (fake)  <->  LegacyVoiceRuntime  <->  PhoneCall (unchanged)  <->  Session.process_turn

The scenarios are the ones tests/test_telephony_pipeline.py pins for PhoneCall (barge-in, the guardrails, the
identity gate, every way a call ends), run again through the runtime with the audio arriving and leaving as
AudioFrames. They show two things: nothing PhoneCall did has changed, and the conversation policy is still
process_turn's, whatever carries the audio. No Twilio, no Deepgram, no network."""

import asyncio
import re
import time

import pytest

from app.brains.base import Propose, TextDelta, ToolCall
from app.config import Settings
from app.db import Repository
from app.guardrails import AGENT_SOLICITATION_REPLACEMENT
from app.media import AudioFrame
from app.models import Contact
from app.profiles import get_profile
from app.service import Service
from app.session import OutboundState, _run_tool, create_session, open_call
from app.store import SessionStore
from app.telephony import mulaw
from app.telephony.deepgram import SpeechStarted, Transcript, UtteranceEnd
from app.telephony.legacy_runtime import LegacyVoiceRuntime
from app.voice_runtime import VoiceRuntime
from tests.helpers import ScriptedBrain
from tests.test_dispatcher import NOW, clock, world  # noqa: F401  (fixtures)
from tests.test_media import FakeMediaLink
from tests.test_scheduler import scheduler_for, tick
from tests.test_telephony_adapter import FakeTelephony, fake_world, results  # noqa: F401  (fixture)
from tests.test_telephony_pipeline import FakeListener, FakeSpeaker, brain_script, until


class RuntimeRig:
    runtime_class = LegacyVoiceRuntime  # tests/test_pipecat_runtime.py runs these same scenarios with another runtime

    def __init__(self, speaker_delay=0.0, script=brain_script, min_words=2, outbound=False):
        self.repo = Repository("sqlite://")
        self.brain = ScriptedBrain(script)
        self.service = Service(
            repo=self.repo, store=SessionStore(10, 3600), brain=self.brain, settings=Settings(database_url="sqlite://", api_key="k")
        )
        state = OutboundState("JOB-P1", "Priya Sharma", "your card", "Northbridge Bank", 300) if outbound else None
        self.session = create_session(get_profile("bank"), self.brain, 5.0, state)
        self.session.channel = "phone"

        if outbound:
            now = time.time()
            self.repo.create_job(dict(
                id="JOB-P1", reference=None, profile_id="bank", customer_ref=None, channel="phone", callee_name="Priya Sharma",
                callee_phone="+919876543210", reason="your card", callback_url=None, status="in_progress", answer_token="t",
                created_at=now, expires_at=now + 60, max_duration_seconds=300, callback_status="none", callback_attempts=0,
            ))

        self.link, self.listener, self.speaker = FakeMediaLink(), FakeListener(), FakeSpeaker(speaker_delay)
        self.runtime = self.runtime_class(service=self.service, listener=self.listener, speaker=self.speaker, barge_in_min_words=min_words)
        self.task: asyncio.Task | None = None

    async def start(self):
        self.task = asyncio.create_task(self.runtime.run(self.session, self.link, open_call(self.session)))
        return self

    def said(self) -> str:
        """What the callee would have heard, recovered from the AudioFrames (the fake speaker's audio is text)."""
        # The fake speaker's audio is "S:<text>" followed by filler; joined into one stream it reads the same
        # whatever chunk sizes a runtime cut it into.
        stream = b"".join(mulaw.encode(frame.pcm) for frame in self.link.sent())
        return " ".join(text.decode() for text in re.findall(rb"S:([^\xff]*)", stream))

    def count(self, kind: str) -> int:
        return sum(1 for entry in self.link.log if entry[0] == kind)

    def hear(self, text, final=True, speech_final=True):
        self.listener.push(Transcript(text, final, speech_final))

    async def greeted(self):
        """The greeting was sent and the link reported it played."""
        await until(lambda: self.count("checkpoint") >= 1)
        await asyncio.sleep(0.06)

    async def callee_hangs_up(self):
        self.link.end()
        await asyncio.wait_for(self.task, 3)

    async def cleanup(self):
        if self.task and not self.task.done():
            self.link.end()
            await asyncio.wait_for(self.task, 3)


@pytest.fixture
async def rig():
    r = await RuntimeRig().start()
    yield r
    await r.cleanup()


def test_the_legacy_runtime_is_a_voice_runtime():
    assert callable(LegacyVoiceRuntime.run) and "run" in dir(VoiceRuntime)


# --- audio in and out is neutral frames --------------------------------------------------------------------


async def test_the_greeting_leaves_as_neutral_frames_followed_by_a_playout_checkpoint(rig):
    await until(lambda: rig.count("checkpoint") == 1)

    assert rig.said().startswith("Hello, this is an AI assistant.")
    assert rig.link.log[-1][0] == "checkpoint", "asks to be told when the greeting has finished playing"
    assert rig.link.sent() and all(type(f) is AudioFrame and (f.sample_rate, f.channels) == (8000, 1) for f in rig.link.sent())


async def test_the_callees_frames_reach_speech_recognition_as_the_bytes_it_expects(rig):
    rig.link.feed(AudioFrame(mulaw.decode(b"\x01\x02\x03"), 8000))
    await until(lambda: rig.listener.audio == [b"\x01\x02\x03"])


async def test_a_frame_the_legacy_recognizer_cannot_take_ends_the_call_cleanly():
    rig = await RuntimeRig().start()
    await rig.greeted()
    rig.link.feed(AudioFrame(b"\x00\x00" * 160, 16000))

    with pytest.raises(ValueError, match="8 kHz mono"):
        await asyncio.wait_for(rig.task, 3)

    assert rig.session.ended and rig.session.end_reason == "hangup" and rig.repo.get_result(rig.session.id) is not None


# --- the conversation is still process_turn's --------------------------------------------------------------------


async def test_what_the_caller_says_goes_through_process_turn_and_comes_back_as_frames(rig):
    await rig.greeted()
    rig.hear("what is my balance")
    await until(lambda: "You said: what is my balance." in rig.said())

    assert [e.speaker for e in rig.session.transcript][:3] == ["Agent", "You", "Agent"]
    assert any(e["type"] == "user_turn" for e in rig.session.audit)


async def test_a_sentence_can_arrive_in_several_pieces_and_is_one_turn(rig):
    await rig.greeted()
    rig.hear("what is", final=True, speech_final=False)
    rig.hear("my balance", final=True, speech_final=True)
    await until(lambda: "You said: what is my balance." in rig.said())

    assert len(rig.brain.calls) == 1


async def test_an_utterance_end_event_also_closes_the_sentence(rig):
    await rig.greeted()
    rig.hear("hello there", final=True, speech_final=False)
    rig.listener.push(UtteranceEnd())
    await until(lambda: "You said: hello there." in rig.said())


async def test_interim_guesses_and_silence_alone_do_not_start_turns(rig):
    await rig.greeted()
    rig.hear("hel", final=False, speech_final=False)
    rig.listener.push(SpeechStarted())
    rig.hear("", final=True, speech_final=True)
    await asyncio.sleep(0.15)

    assert rig.brain.calls == []


async def test_guardrails_still_catch_a_pin_read_out_as_words_and_it_is_never_kept(rig):
    await rig.greeted()
    rig.hear("my pin is one two three four")
    await until(lambda: "never take passwords" in rig.said().lower().replace("can never take", "never take"))

    assert rig.brain.calls == []
    blob = repr(rig.session.transcript) + repr(rig.session.audit) + repr(rig.session.history)
    assert "1234" not in blob and "one two three four" not in blob


async def test_the_confirmation_gate_still_needs_a_spoken_yes(rig):
    await rig.greeted()
    rig.hear("freeze my credit card")
    await until(lambda: "yes to confirm" in rig.said())
    assert rig.session.data["cards"][1]["status"] == "active"

    rig.hear("yes please")
    await until(lambda: rig.session.data["cards"][1]["status"] == "frozen")


async def test_agent_output_is_still_vetted_before_it_is_spoken():
    async def asks_for_a_pin(ctx):
        yield TextDelta("Please tell me your PIN so I can check.")

    rig = await RuntimeRig(script=asks_for_a_pin).start()
    await rig.greeted()
    rig.hear("what is my balance")
    await until(lambda: AGENT_SOLICITATION_REPLACEMENT in rig.said())

    assert "tell me your PIN" not in rig.said()
    assert rig.session.blocked_count == 1 and any(e["type"] == "guardrail_blocked" for e in rig.session.audit)
    await rig.cleanup()


async def test_the_identity_gate_still_holds_until_the_callee_confirms_who_they_are():
    async def script(ctx):
        result = await ctx.run_tool("get_flagged_activity", {})
        yield ToolCall("get_flagged_activity", {}, result)
        yield TextDelta("About your card.")

    rig = await RuntimeRig(outbound=True, script=script).start()
    await rig.greeted()

    rig.hear("what is this about")  # not a confirmation
    await until(lambda: "calling from Northbridge Bank for Priya Sharma" in rig.said())  # asked again, who is there?
    assert rig.brain.calls == [], "nothing reaches the brain before identity is confirmed"
    assert await _run_tool(rig.session, "get_flagged_activity", {}) == {"error": "The person has not confirmed who they are."}

    rig.hear("yes speaking")
    await until(lambda: len(rig.brain.calls) == 1)
    assert rig.session.outbound.identity == "confirmed"
    await rig.cleanup()


# --- barge-in and interruption, unchanged ------------------------------------------------------------------------


async def test_talking_over_the_agent_cuts_it_off_at_once_clears_the_playout_and_is_recorded():
    rig = await RuntimeRig(speaker_delay=0.05).start()
    await rig.greeted()
    rig.hear("tell me many things")
    await until(lambda: "First sentence here." in rig.said())  # the agent is mid-reply

    rig.hear("wait stop please", final=False, speech_final=False)  # the caller talks over it
    await until(lambda: rig.count("clear") == 1)

    audio_then = rig.count("audio")
    await asyncio.sleep(0.3)
    assert rig.count("audio") == audio_then, "the agent really stopped"
    assert "Third sentence here." not in rig.said()

    reply = [e for e in rig.session.transcript if e.speaker == "Agent"][-1]
    assert reply.interrupted and any(e["type"] == "playback_interrupted" for e in rig.session.audit)

    rig.hear("wait stop please", final=True, speech_final=True)  # ...and is then heard as the next turn
    await until(lambda: "You said: wait stop please." in rig.said())
    await rig.cleanup()


async def test_a_single_word_is_not_enough_to_cut_the_agent_off():
    rig = await RuntimeRig(speaker_delay=0.05).start()
    await rig.greeted()
    rig.hear("tell me many things")
    await until(lambda: "First sentence here." in rig.said())

    rig.hear("uh", final=False, speech_final=False)
    await asyncio.sleep(0.1)

    assert rig.count("clear") == 0
    await until(lambda: "Third sentence here." in rig.said())
    await rig.cleanup()


async def test_a_new_utterance_replaces_a_reply_that_is_still_being_thought_up():
    async def slow(ctx):
        if "first" in ctx.text:
            await asyncio.sleep(2)

        yield TextDelta(f"Answer to {ctx.text}.")

    rig = await RuntimeRig(script=slow).start()
    await rig.greeted()
    rig.hear("first question")
    await until(lambda: len(rig.brain.calls) == 1)
    rig.hear("second question")
    await until(lambda: "Answer to second question." in rig.said())

    assert "Answer to first question." not in rig.said()
    await rig.cleanup()


# --- every way a call ends, unchanged -----------------------------------------------------------------------------


async def test_a_wrong_number_gets_a_goodbye_and_then_the_runtime_hangs_up_the_link():
    rig = await RuntimeRig(outbound=True).start()
    await rig.greeted()
    rig.hear("no, wrong number")
    await until(lambda: rig.link.closed)

    assert "won't take any more of your time" in rig.said()
    assert rig.link.log.index(("close",)) > max(i for i, e in enumerate(rig.link.log) if e[0] == "audio"), "after the goodbye, not over it"
    await asyncio.wait_for(rig.task, 3)
    await until(lambda: rig.repo.get_job("JOB-P1")["status"] == "wrong_party")  # recorded by the detached close, which run() does not wait for
    assert rig.session.ended and rig.session.end_reason == "wrong_party"
    assert rig.listener.closed


async def test_the_callee_hanging_up_records_the_call_without_touching_the_link(rig):
    await rig.greeted()
    await rig.callee_hangs_up()

    assert rig.session.ended and rig.session.end_reason == "hangup"
    assert not rig.link.closed, "they are already gone"
    assert rig.repo.get_result(rig.session.id)["channel"] == "phone"


async def test_a_handler_cancelled_mid_call_still_records_the_call():
    rig = await RuntimeRig().start()
    await rig.greeted()
    rig.task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await rig.task

    await until(lambda: rig.session.ended)  # finished by the detached close
    await until(lambda: rig.repo.get_result(rig.session.id) is not None)
    assert rig.session.end_reason == "hangup"


async def test_when_the_service_ends_a_call_the_link_is_hung_up(rig):
    """The sweeper (idle, time limit) and a finished job end sessions through the service."""
    await rig.greeted()
    await rig.service.finalize(rig.session)

    assert rig.link.closed and rig.listener.closed and rig.session.ended


async def test_an_agent_that_can_no_longer_hear_does_not_stay_on_the_line(rig):
    await rig.greeted()
    rig.listener.drop()  # speech recognition went away

    await until(lambda: rig.session.ended)
    assert rig.session.end_reason == "listener_lost" and rig.link.closed


# --- the automated lifecycle, end to end, over a media link and a voice runtime ---------------------------------


async def test_an_automated_call_runs_from_workflow_to_result_over_a_media_link_and_a_voice_runtime(fake_world):
    """Workflow -> Scheduler -> Dispatcher -> TelephonyAdapter (fake provider) -> MediaLink (fake) -> VoiceRuntime
    -> Session -> result. No Twilio, no Deepgram, no Pipecat."""
    world = fake_world
    ids = world.seed()
    world.change(Contact, ids.contact, metadata_={"appointment_date": "2026-09-25", "doctor": "Dr. Sharma"})

    async def script(ctx):
        if "confirmed who they are" in ctx.text:
            result = await ctx.run_tool("get_call_context", {})
            yield ToolCall("get_call_context", {}, result)
            details = result["contact"]["details"]
            yield TextDelta(f"Your appointment with {details['doctor']} is on {details['appointment_date']}.")
        else:
            yield TextDelta("Thank you. Goodbye.")

    world.service.brain = ScriptedBrain(script)
    await tick(scheduler_for(world), world)  # the scheduler creates the job and the dispatcher places the call

    (job,) = world.repo.list_jobs(10)
    assert (job["status"], job["twilio_call_sid"]) == ("ringing", "FAKE-1") and world.fake.placed[0].job_id == job["id"]

    session, greeting = await world.service.answer_job_phone(job["id"], "FAKE-1")  # the callee picks up
    link, listener = FakeMediaLink(), FakeListener()
    runtime = LegacyVoiceRuntime(service=world.service, listener=listener, speaker=FakeSpeaker())
    task = asyncio.create_task(runtime.run(session, link, greeting))

    def said() -> str:
        chunks = [mulaw.encode(frame.pcm) for _, frame in (e for e in link.log if e[0] == "audio")]
        return " ".join(c[2:].decode() for c in chunks if c.startswith(b"S:"))

    await until(lambda: any(e[0] == "checkpoint" for e in link.log))
    assert "Am I speaking with Priya Sharma?" in said() and "calling from Acme" in said()
    await asyncio.sleep(0.06)
    listener.push(Transcript("yes speaking", True, True))
    await until(lambda: "2026-09-25" in said())
    assert "Dr. Sharma" in said(), "the contact's own record reached the conversation"

    link.end()  # the callee hangs up
    await asyncio.wait_for(task, 3)

    final = world.repo.get_job(job["id"])
    assert (final["status"], final["end_reason"]) == ("completed", "hangup")
    (result,) = results(world)
    assert (result.job_id, result.call_id, result.organization_id, result.outcome) == (job["id"], final["call_id"], ids.organization, "completed")
    assert world.twilio.calls == [], "Twilio was never involved"


async def test_the_runtime_lifecycle_logs_carry_the_call_and_provider_ids_and_no_secret(fake_world, caplog):
    import logging

    caplog.set_level(logging.INFO)
    world = fake_world
    world.seed()
    world.service.brain = ScriptedBrain()
    await tick(scheduler_for(world), world)
    (job,) = world.repo.list_jobs(10)
    token = world.repo.get_job(job["id"])["answer_token"]

    session, greeting = await world.service.answer_job_phone(job["id"], "FAKE-1")
    link, listener = FakeMediaLink(), FakeListener()
    task = asyncio.create_task(LegacyVoiceRuntime(service=world.service, listener=listener, speaker=FakeSpeaker()).run(session, link, greeting))
    await until(lambda: any(e[0] == "checkpoint" for e in link.log))
    listener.push(Transcript("yes speaking", True, True))
    await until(lambda: len([e for e in link.log if e[0] == "checkpoint"]) >= 2)
    link.end()
    await asyncio.wait_for(task, 3)

    text = caplog.text
    assert f"call={session.id} provider=fake provider_call=FAKE-1" in text, "answered"
    assert f"status=completed end_reason=hangup call={session.id} provider_call=FAKE-1" in text, "finished"
    assert [s for s in (token, "9876543210", "919876543210", "Priya", "yes speaking", "Am I speaking") if s in text] == []
