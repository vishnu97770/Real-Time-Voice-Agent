"""The phone-call pipeline, driven by fake audio: no Twilio, no Deepgram, no network."""

import asyncio
import time

import pytest

from app.brains.base import Propose, TextDelta
from app.config import Settings
from app.db import Repository
from app.profiles import get_profile
from app.service import Service
from app.session import OutboundState, create_session, open_call
from app.store import SessionStore
from app.telephony.deepgram import DeepgramError, SpeechStarted, Transcript, UtteranceEnd
from app.telephony.pipeline import PhoneCall
from tests.helpers import ScriptedBrain


class FakeLine:
    """Records what the caller would hear. Echoes marks back, like Twilio does once audio has played."""

    def __init__(self):
        self.log = []
        self.call = None
        self.echo = True

    async def send_audio(self, mulaw):
        self.log.append(("audio", mulaw))

    async def send_clear(self):
        self.log.append(("clear",))

    async def send_mark(self, name):
        self.log.append(("mark", name))

        if self.echo and self.call:
            asyncio.get_running_loop().call_later(0.01, self.call.mark_played, name)

    async def hang_up(self):
        self.log.append(("hangup",))

    def said(self) -> str:
        """The sentences spoken, recovered from the fake audio."""
        return " ".join(chunk[2:].decode() for kind, *rest in self.log if kind == "audio" for chunk in rest if chunk.startswith(b"S:"))

    def count(self, kind):
        return sum(1 for entry in self.log if entry[0] == kind)


class FakeSpeaker:
    def __init__(self, delay=0.0):
        self.delay = delay
        self.texts = []

    async def synthesize(self, text):
        self.texts.append(text)

        if "EXPLODE" in text:
            raise DeepgramError("text-to-speech failed with status 402")

        yield b"S:" + text.encode()

        for _ in range(3):
            await asyncio.sleep(self.delay)
            yield b"\xff" * 160


class FakeListener:
    def __init__(self):
        self.audio = []
        self.closed = False
        self._events: asyncio.Queue = asyncio.Queue()

    def push(self, event):
        self._events.put_nowait(event)

    def drop(self):
        self._events.put_nowait(None)

    async def send_audio(self, mulaw):
        self.audio.append(mulaw)

    async def events(self):
        while (event := await self._events.get()) is not None:
            yield event

    async def aclose(self):
        self.closed = True


async def until(condition, timeout=3.0):
    end = time.time() + timeout

    while not condition():
        if time.time() > end:
            raise AssertionError("timed out waiting for the condition")
        await asyncio.sleep(0.01)


async def brain_script(ctx):
    if "many" in ctx.text:
        yield TextDelta("First sentence here. Second sentence here. Third sentence here. ")
    elif "freeze" in ctx.text:
        yield Propose("freeze_card", {"card": "credit"})
    else:
        yield TextDelta(f"You said: {ctx.text}.")


async def greeted(rig):
    """The greeting has been sent AND has finished playing (the line echoed its mark)."""
    await until(lambda: rig.line.count("mark") >= 1 and not rig.call._speaking)


class Rig:
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

        self.line, self.listener, self.speaker = FakeLine(), FakeListener(), FakeSpeaker(speaker_delay)
        self.call = PhoneCall(session=self.session, service=self.service, line=self.line, listener=self.listener, speaker=self.speaker, barge_in_min_words=min_words)
        self.line.call = self.call

    async def start(self):
        await self.call.start(open_call(self.session))
        return self

    def hear(self, text, final=True, speech_final=True):
        self.listener.push(Transcript(text, final, speech_final))

    async def cleanup(self):
        await self.call.close("test_over")


@pytest.fixture
async def rig():
    r = await Rig().start()
    yield r
    await r.cleanup()


# --- talking and listening ---------------------------------------------------------------------


async def test_the_agent_speaks_first_and_the_greeting_is_the_ai_disclosure(rig):
    await until(lambda: rig.line.count("mark") == 1)

    assert rig.line.said().startswith("Hello, this is an AI assistant.")
    assert rig.line.log[-1][0] == "mark", "asks to be told when the greeting has finished playing"


async def test_what_the_caller_says_goes_through_the_runtime_and_comes_back_as_audio(rig):
    await greeted(rig)
    rig.hear("what is my balance")
    await until(lambda: "You said: what is my balance." in rig.line.said())

    assert [e.speaker for e in rig.session.transcript][:3] == ["Agent", "You", "Agent"]
    assert any(e["type"] == "user_turn" for e in rig.session.audit)


async def test_a_sentence_can_arrive_in_several_pieces_and_is_one_turn(rig):
    await greeted(rig)
    rig.hear("what is", final=True, speech_final=False)
    rig.hear("my balance", final=True, speech_final=True)
    await until(lambda: "You said: what is my balance." in rig.line.said())

    assert len(rig.brain.calls) == 1


async def test_an_utterance_end_event_also_closes_the_sentence(rig):
    await greeted(rig)
    rig.hear("hello there", final=True, speech_final=False)
    rig.listener.push(UtteranceEnd())
    await until(lambda: "You said: hello there." in rig.line.said())


async def test_interim_guesses_and_silence_alone_do_not_start_turns(rig):
    await greeted(rig)
    rig.hear("hel", final=False, speech_final=False)
    rig.listener.push(SpeechStarted())
    rig.hear("", final=True, speech_final=True)
    await asyncio.sleep(0.15)

    assert rig.brain.calls == []


async def test_the_callers_audio_is_passed_on_to_speech_recognition(rig):
    await rig.call.audio_in(b"\x01\x02\x03")

    assert rig.listener.audio == [b"\x01\x02\x03"]


# --- barge-in -------------------------------------------------------------------------------------------


async def test_talking_over_the_agent_cuts_it_off_at_once_and_the_interruption_is_recorded():
    rig = await Rig(speaker_delay=0.05).start()
    await greeted(rig)
    rig.hear("tell me many things")
    await until(lambda: "First sentence here." in rig.line.said())  # the agent is mid-reply

    rig.hear("wait stop please", final=False, speech_final=False)  # the caller talks over it
    await until(lambda: rig.line.count("clear") == 1)

    audio_then = rig.line.count("audio")
    await asyncio.sleep(0.3)
    assert rig.line.count("audio") == audio_then, "the agent really stopped"
    assert "Third sentence here." not in rig.line.said(), "it never got to the end"

    reply = [e for e in rig.session.transcript if e.speaker == "Agent"][-1]
    assert reply.interrupted
    assert any(e["type"] == "playback_interrupted" for e in rig.session.audit)

    # ...and what the caller said is then heard as their next turn.
    rig.hear("wait stop please", final=True, speech_final=True)
    await until(lambda: "You said: wait stop please." in rig.line.said())
    await rig.cleanup()


async def test_a_single_word_is_not_enough_to_cut_the_agent_off():
    rig = await Rig(speaker_delay=0.05).start()
    await greeted(rig)
    rig.hear("tell me many things")
    await until(lambda: "First sentence here." in rig.line.said())

    rig.hear("uh", final=False, speech_final=False)
    await asyncio.sleep(0.1)

    assert rig.line.count("clear") == 0
    await until(lambda: "Third sentence here." in rig.line.said())
    await rig.cleanup()


async def test_a_new_utterance_replaces_a_reply_that_is_still_being_thought_up():
    async def slow(ctx):
        if "first" in ctx.text:
            await asyncio.sleep(2)
        yield TextDelta(f"Answer to {ctx.text}.")

    rig = await Rig(script=slow).start()
    await greeted(rig)
    rig.hear("first question")
    await until(lambda: len(rig.brain.calls) == 1)
    rig.hear("second question")
    await until(lambda: "Answer to second question." in rig.line.said())

    assert "Answer to first question." not in rig.line.said(), "the stale reply is never spoken"
    assert [e["text"] for e in rig.session.audit if e["type"] == "user_turn"][:2] == ["first question", "second question"]
    await rig.cleanup()


# --- safety over the phone --------------------------------------------------------------------------------


async def test_a_pin_read_out_loud_as_words_is_still_caught_and_never_kept(rig):
    await greeted(rig)
    rig.hear("my pin is one two three four")
    await until(lambda: "never take passwords" in rig.line.said().lower().replace("can never take", "never take"))

    assert rig.brain.calls == []
    blob = repr(rig.session.transcript) + repr(rig.session.audit) + repr(rig.session.history)
    assert "1234" not in blob and "one two three four" not in blob


async def test_a_guarded_action_still_needs_a_spoken_yes_over_the_phone(rig):
    await greeted(rig)
    rig.hear("freeze my credit card")
    await until(lambda: "yes to confirm" in rig.line.said())
    assert rig.session.data["cards"][1]["status"] == "active"

    rig.hear("yes please")
    await until(lambda: rig.session.data["cards"][1]["status"] == "frozen")


# --- ending calls -------------------------------------------------------------------------------------------


async def test_a_wrong_number_gets_a_goodbye_and_then_the_agent_hangs_up():
    rig = await Rig(outbound=True).start()
    await greeted(rig)
    rig.hear("no, wrong number")
    await until(lambda: rig.line.count("hangup") == 1)

    assert "won't take any more of your time" in rig.line.said()
    assert rig.line.log.index(("hangup",)) > max(i for i, e in enumerate(rig.line.log) if e[0] == "audio"), "hung up after the goodbye, not over it"
    assert rig.session.ended and rig.session.end_reason == "wrong_party"
    assert rig.repo.get_job("JOB-P1")["status"] == "wrong_party"
    assert rig.listener.closed


async def test_the_callee_hanging_up_records_the_call_without_touching_the_line(rig):
    await greeted(rig)
    await rig.call.close("hangup", hang_up=False)

    assert rig.session.ended and rig.session.end_reason == "hangup"
    assert rig.line.count("hangup") == 0, "they are already gone"
    assert rig.repo.get_result(rig.session.id)["channel"] == "phone"
    await rig.call.close("hangup", hang_up=False)  # idempotent


async def test_when_the_service_ends_a_call_the_phone_line_is_hung_up(rig):
    """The sweeper (idle, time limit) and a finished job end sessions through the service."""
    await greeted(rig)
    await rig.service.finalize(rig.session)

    assert rig.line.count("hangup") == 1 and rig.listener.closed
    assert rig.session.ended


async def test_an_agent_that_can_no_longer_hear_does_not_stay_on_the_line(rig):
    await greeted(rig)
    rig.listener.drop()  # speech recognition went away

    await until(lambda: rig.session.ended)
    assert rig.session.end_reason == "listener_lost" and rig.line.count("hangup") == 1


async def test_a_speech_synthesis_failure_is_logged_and_the_call_carries_on(rig):
    await greeted(rig)
    rig.hear("EXPLODE now")
    await until(lambda: any(e["type"] == "speech_error" for e in rig.session.audit))
    rig.hear("are you there")
    await until(lambda: "You said: are you there." in rig.line.said())

    assert not rig.session.ended
    assert "402" in repr([e for e in rig.session.audit if e["type"] == "speech_error"])
