"""Which voice runtime conducts a call: chosen once, when the call starts, from the operator's setting and the call's agent.

The default is the legacy runtime, and Pipecat is only ever reached by asking for it. Asking for it and not having it
(not installed, wrong version) costs the call nothing: it is conducted by the legacy runtime and the fallback is logged.
A call already running is never handed to another runtime."""

import base64
import json
import logging
import re
import types

import httpx
import pytest

from app.config import Settings
from app.telephony import Telephony, build_telephony
from app.telephony.legacy_runtime import LegacyVoiceRuntime
from app.voice_runtime_factory import create_voice_runtime, wants_pipecat
from tests.test_telephony_pipeline import FakeSpeaker
from tests.test_telephony_routes import World, play_finished, speak
from tests.test_telephony_routes import verify_signature  # noqa: F401  (used through the delivered callback)


def telephony(voice_runtime="legacy", agents=frozenset()) -> Telephony:
    return Telephony(
        twilio=None, open_listener=None, speaker=FakeSpeaker(), public_api_url="https://x", auth_token="t", adapter=object(),
        voice_runtime=voice_runtime, voice_runtime_pipecat_agents=frozenset(agents),
    )


def call(agent_id: int | None = 7) -> types.SimpleNamespace:
    context = None if agent_id is None else types.SimpleNamespace(agent=types.SimpleNamespace(id=agent_id))
    return types.SimpleNamespace(id="CALL-20260921-abc", context=context)


def runtime_for(session, tel) -> object:
    return create_voice_runtime(session, telephony=tel, service=object(), listener=object())


def is_pipecat(runtime) -> bool:
    return type(runtime).__name__ == "PipecatVoiceRuntime"


# --- the default, and the two ways of asking ------------------------------------------------------------------------------


def test_the_legacy_runtime_is_the_default_for_every_call():
    assert Telephony(twilio=None, open_listener=None, speaker=None, public_api_url="x", auth_token="t", adapter=object()).voice_runtime == "legacy"

    for session in (call(7), call(None)):
        runtime = runtime_for(session, telephony())
        assert type(runtime) is LegacyVoiceRuntime


def test_the_legacy_runtime_stays_even_when_an_agent_is_on_the_list_if_pipecat_was_not_asked_for():
    assert type(runtime_for(call(7), telephony("legacy", {7}))) is LegacyVoiceRuntime


def test_pipecat_is_chosen_for_every_call_when_it_is_asked_for_and_no_agents_are_listed():
    for session in (call(7), call(99), call(None)):
        assert is_pipecat(runtime_for(session, telephony("pipecat")))


def test_with_an_agent_list_only_those_agents_calls_use_pipecat():
    tel = telephony("pipecat", {7, 9})

    assert is_pipecat(runtime_for(call(7), tel)) and is_pipecat(runtime_for(call(9), tel))
    assert type(runtime_for(call(8), tel)) is LegacyVoiceRuntime, "another agent"
    assert type(runtime_for(call(None), tel)) is LegacyVoiceRuntime, "a call with no agent of its own is not on the list"


def test_wants_pipecat_reads_only_the_setting_and_the_calls_agent():
    assert wants_pipecat(call(7), telephony("pipecat", {7})) and not wants_pipecat(call(7), telephony("pipecat", {8}))
    assert not wants_pipecat(call(7), telephony("legacy", {7}))


def test_the_runtimes_are_built_from_the_same_parts_so_either_can_conduct_the_call():
    speaker, service, listener = FakeSpeaker(), object(), object()
    tel = telephony("pipecat")
    tel.speaker, tel.barge_in_min_words = speaker, 3

    runtime = create_voice_runtime(call(7), telephony=tel, service=service, listener=listener)

    assert (runtime._service, runtime._listener, runtime._speaker, runtime._barge_in_min_words) == (service, listener, speaker, 3)


# --- asked for, but not available: the call goes on with the legacy runtime ----------------------------------------------


def test_if_pipecat_is_not_installed_the_call_is_conducted_by_the_legacy_runtime_and_the_fallback_is_logged(monkeypatch, caplog):
    import importlib.metadata

    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    caplog.set_level(logging.INFO)
    runtime = runtime_for(call(7), telephony("pipecat"))

    assert type(runtime) is LegacyVoiceRuntime
    assert "voice runtime call=CALL-20260921-abc pipecat unavailable" in caplog.text and "using legacy" in caplog.text
    assert "voice runtime=legacy call=CALL-20260921-abc" in caplog.text


def test_a_pipecat_version_other_than_the_pinned_one_is_not_used(monkeypatch, caplog):
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda name: "1.12.0")
    caplog.set_level(logging.INFO)

    assert type(runtime_for(call(7), telephony("pipecat"))) is LegacyVoiceRuntime
    assert "1.12.0 is installed but only 1.11.0 is supported" in caplog.text


def test_if_the_runtime_cannot_even_be_imported_the_call_still_gets_the_legacy_runtime(monkeypatch, caplog):
    import app.voice_runtime_factory as factory

    def broken(**parts):
        raise ImportError("numpy exploded")

    monkeypatch.setattr(factory, "_pipecat_runtime", broken)
    caplog.set_level(logging.INFO)

    assert type(runtime_for(call(7), telephony("pipecat"))) is LegacyVoiceRuntime
    assert "ImportError" in caplog.text


def test_the_choice_is_logged_with_the_call_id_and_nothing_private(caplog):
    caplog.set_level(logging.INFO)
    runtime_for(call(7), telephony("pipecat", {7}))

    assert "voice runtime=pipecat call=CALL-20260921-abc" in caplog.text
    assert [w for w in ("+91", "Priya", "token") if w in caplog.text] == []


# --- chosen once ------------------------------------------------------------------------------------------------------------------


def test_a_runtime_that_has_been_chosen_does_not_change_when_the_setting_does():
    tel = telephony("pipecat")
    chosen = runtime_for(call(7), tel)
    tel.voice_runtime = "legacy"  # the operator flips the switch while that call is still running

    assert is_pipecat(chosen) and type(runtime_for(call(7), tel)) is LegacyVoiceRuntime, "only the NEXT call is affected"


def test_the_settings_reach_the_telephony_bundle_when_it_is_built():
    settings = Settings(
        _env_file=None, database_url="sqlite://", twilio_account_sid="AC1", twilio_auth_token="tok", twilio_from_number="+1",
        public_api_url="https://agent.example", deepgram_api_key="dg", voice_runtime="pipecat", voice_runtime_pipecat_agent_ids="4, 5",
    )
    built = build_telephony(settings, httpx.AsyncClient())

    assert (built.voice_runtime, built.voice_runtime_pipecat_agents) == ("pipecat", frozenset({4, 5}))
    default = build_telephony(settings.model_copy(update={"voice_runtime": "legacy", "voice_runtime_pipecat_agent_ids": ""}), httpx.AsyncClient())
    assert (default.voice_runtime, default.voice_runtime_pipecat_agents) == ("legacy", frozenset())


# --- the whole call, over the real Twilio route, on each runtime ------------------------------------------------------------------


def hear_stream(ws, limit=400):
    """What the caller would hear up to the next mark, from the audio as it arrives in messages of any size."""
    audio, cleared = b"", False

    for _ in range(limit):
        message = json.loads(ws.receive_text())

        if message["event"] == "media":
            audio += base64.b64decode(message["media"]["payload"])
        elif message["event"] == "clear":
            cleared = True
        elif message["event"] == "mark":
            return " ".join(t.decode() for t in re.findall(rb"S:([^\xff]*)", audio)) + (" <CLEAR>" if cleared else ""), message["mark"]["name"]

    raise AssertionError("no mark arrived")


@pytest.mark.parametrize("runtime", ["legacy", "pipecat"])
def test_a_full_phone_call_from_ring_to_signed_result_on_either_runtime(runtime, caplog):
    caplog.set_level(logging.INFO)

    with World(voice_runtime=runtime) as w:
        job = w.place().json()
        ws = w.connect(w.params())

        opener, mark = hear_stream(ws)
        assert opener.startswith("Hello, this is an AI assistant.") and "Am I speaking with Priya Sharma?" in opener
        assert "TechMart" not in opener, "nothing about the callee before they confirm who they are"
        play_finished(ws, mark)

        speak(ws, "yes speaking")
        reply, mark = hear_stream(ws)
        assert "TechMart" in reply and "Was that you?" in reply
        play_finished(ws, mark)

        speak(ws, "that was not me")
        prompt, mark = hear_stream(ws)
        assert "freeze your credit card" in prompt and "yes to confirm" in prompt
        play_finished(ws, mark)

        speak(ws, "yes please")
        done, mark = hear_stream(ws)
        assert "is now frozen" in done

        ws.send_text(json.dumps({"event": "stop", "streamSid": "MZ1"}))  # the callee hangs up
        ws.__exit__(None, None, None)

        w.wait_until(lambda: w.job(job["job_id"])["callback"]["status"] == "delivered")
        final = w.job(job["job_id"])

        assert final["status"] == "completed" and final["end_reason"] == "hangup"
        assert final["result"]["outcome"] == "action_completed" and final["result"]["outbound"]["identity"] == "confirmed"
        assert w.twilio.hangups == [], "the callee hung up: there is no line left to hang up"
        assert f"voice runtime={runtime} call=" in caplog.text


@pytest.mark.parametrize("runtime", ["legacy", "pipecat"])
def test_a_wrong_number_over_the_route_is_hung_up_by_our_adapter_once_the_goodbye_has_been_heard(runtime):
    with World(voice_runtime=runtime) as w:
        job = w.place().json()
        ws = w.connect(w.params())
        _, mark = hear_stream(ws)
        play_finished(ws, mark)

        speak(ws, "no, wrong number")
        goodbye, mark = hear_stream(ws)
        assert "won't take any more of your time" in goodbye and "TechMart" not in goodbye

        assert w.twilio.hangups == [], "not while the goodbye is still playing"
        play_finished(ws, mark)  # ...it has now been heard
        w.wait_until(lambda: w.twilio.hangups == ["CA_fake_1"])  # by Twilio's REST client through OUR adapter: exactly once
        ws.__exit__(None, None, None)

        w.wait_until(lambda: w.job(job["job_id"])["status"] == "wrong_party")
        assert w.job(job["job_id"])["result"]["outcome"] == "wrong_party"
        assert w.twilio.hangups == ["CA_fake_1"], "and never a second time, from anywhere else"


@pytest.mark.parametrize("runtime", ["legacy", "pipecat"])
def test_talking_over_the_agent_over_the_phone_stops_the_audio_on_either_runtime(runtime):
    with World(voice_runtime=runtime) as w:
        w.place()
        ws = w.connect(w.params())
        _, mark = hear_stream(ws)
        play_finished(ws, mark)
        speak(ws, "yes speaking")
        assert json.loads(ws.receive_text())["event"] == "media", "the reply begins to play"

        ws.send_text(json.dumps({"event": "media", "streamSid": "MZ1", "media": {"payload": base64.b64encode(b"INTERIM:hold on a second").decode()}}))
        events = []

        for _ in range(200):
            events.append(json.loads(ws.receive_text())["event"])

            if "clear" in events:
                break

        assert "clear" in events, "Twilio is told to throw away audio that has not played yet"
        ws.__exit__(None, None, None)
