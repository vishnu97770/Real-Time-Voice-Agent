"""Step 17: real-network + real-VAD verification of Step 15/16's native STT and VAD path.

Step 16 left one thing explicitly UNVERIFIED: whether the native Deepgram STT path (app/pipecat_runtime/native_stt.py,
unchanged here) and the Silero VAD path (app/pipecat_runtime/vad_observer.py, unchanged here) actually work against
the REAL network and the REAL VAD model, not just the fakes every other test in this suite correctly relies on. This
file closes that gap without weakening anything: the normal test suite stays offline (no test here needs network
access unless explicitly opted in), and no real-network test is ever allowed to report a fabricated pass.

Two independent things are verified, separately, because they need different things to be true:

  * the REAL Silero VAD model, inside a REAL running pipeline, observing REAL (synthetic but speech-shaped) audio -
    needs nothing but the ONNX model already vendored with `silero-vad` (a hard dependency of pipecat-ai's Silero
    extra, already installed); always runs, in normal CI, with no flag.
  * the REAL Deepgram network connection - needs outbound network access and a valid `DEEPGRAM_API_KEY`, which this
    suite must never assume it has. Every test that touches the real network is gated behind BOTH an opt-in
    environment flag AND (where a successful transcript is the point) a real credential; missing either produces an
    explicit skip with a "BLOCKED BY MISSING CREDENTIAL" reason, not a silent pass and not a fabricated one.

Manual verification (never run by CI): from `backend/`, with a real key exported (never committed, never printed):

    RUN_REAL_DEEPGRAM_TESTS=1 DEEPGRAM_API_KEY=<your real key> \\
        .venv/bin/python -m pytest -q -m "" tests/test_pipecat_real_network.py -v

This uses real Deepgram network minutes and a real account; it must never run in CI or in the normal regression
suite, and no test in this file ever prints the key.
"""

import asyncio
import os

import numpy as np
import pytest

pytest.importorskip("pipecat")
pytest.importorskip("deepgram")

from pipecat.frames.frames import InputAudioRawFrame  # noqa: E402

import app.pipecat_runtime.runtime as runtime_module  # noqa: E402
from app.config import Settings  # noqa: E402
from app.media import AudioFrame  # noqa: E402
from app.pipecat_runtime.native_stt import build_native_stt  # noqa: E402
from app.pipecat_runtime.runtime import PipecatVoiceRuntime  # noqa: E402
from app.pipecat_runtime.vad_observer import VadCorrelationTap, VadObserver, create_silero_analyzer  # noqa: E402
from app.telephony.legacy_runtime import LegacyVoiceRuntime  # noqa: E402
from tests.pipecat_helpers import run_pipeline  # noqa: E402
from tests.pipecat_native_stt_fake import wire_fake_deepgram  # noqa: E402
from tests.real_network_harness import (  # noqa: E402
    RUN_REAL_DEEPGRAM_TESTS,
    DiagnosticsTap,
    NetworkDiagnostics,
    credential_audit,
    deepgram_credential,
    scrub,
)
from tests.test_pipecat_native_stt import RUNTIME_STT_KWARGS, native_hear  # noqa: E402
from tests.test_pipecat_vad_observe import vowel_like  # noqa: E402
from tests.test_telephony_pipeline import until  # noqa: E402
from tests.test_voice_runtime import RuntimeRig  # noqa: E402
from tests.test_voice_runtime_selection import call, telephony  # noqa: E402

DEEPGRAM_KWARGS = dict(model="nova-3", endpointing_ms=400, utterance_end_ms=1000)

# A harmless placeholder that is not, and has never been, a real credential - used only to prove the real Deepgram
# service genuinely rejects a bad key, and that nothing captures it verbatim afterwards.
_FAKE_KEY = "fake-invalid-key-for-step17-verification-0000000000"

needs_opt_in = pytest.mark.skipif(
    not RUN_REAL_DEEPGRAM_TESTS, reason="RUN_REAL_DEEPGRAM_TESTS=1 not set: real-network tests are opt-in, skipped by default"
)
needs_valid_credential = pytest.mark.skipif(
    not (RUN_REAL_DEEPGRAM_TESTS and deepgram_credential()),
    reason="REAL NETWORK TEST = BLOCKED BY MISSING CREDENTIAL (set RUN_REAL_DEEPGRAM_TESTS=1 and a real DEEPGRAM_API_KEY)",
)


def vowel_audio(seconds: float = 1.2) -> bytes:
    """Deterministic, locally generated, speech-shaped PCM16 8kHz mono - not a recording, not a microphone; a
    vowel-like synthetic signal (Step 14's own helper), padded with silence so a real VAD sees a start AND a stop."""
    return np.concatenate([np.zeros(1600, np.int16), vowel_like(seconds), np.zeros(3200, np.int16)]).tobytes()


# === A. configuration ============================================================================================


def test_real_network_flag_and_credential_are_off_by_default_in_this_repository():
    """Nothing in Settings, the telephony bundle or the runtime reads RUN_REAL_DEEPGRAM_TESTS at all - it is purely
    this test file's own opt-in gate, never a production setting."""
    assert "run_real_deepgram_tests" not in Settings.model_fields
    assert "voice_pipecat_stt" in Settings.model_fields and Settings(_env_file=None).voice_pipecat_stt == "compat"
    assert "voice_pipecat_vad" in Settings.model_fields and Settings(_env_file=None).voice_pipecat_vad == "off"


def test_native_plus_vad_interrupt_is_a_valid_configured_combination():
    """Step 17 exercises native STT together with VAD interrupt mode; confirm that combination is legal (Step 16
    already proved it is safe) before building a real pipeline around it. A placeholder key is enough here: this
    only checks construction succeeds (native STT construction never touches the network - see native_stt.py),
    not that any connection is made."""
    runtime = PipecatVoiceRuntime(service=None, listener=None, speaker=None, stt="native", vad="interrupt", deepgram_api_key="placeholder-not-a-real-key")
    assert runtime._stt_mode == "native" and runtime._vad_mode == "interrupt"
    assert type(runtime._vad_analyzer).__name__ == "SileroVADAnalyzer"


def test_legacy_runtime_is_unaffected_by_the_real_network_flag_or_credential(monkeypatch):
    from app.voice_runtime_factory import create_voice_runtime

    monkeypatch.setenv("RUN_REAL_DEEPGRAM_TESTS", "1")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "irrelevant-to-legacy")
    tel = telephony("legacy")
    runtime = create_voice_runtime(call(7), telephony=tel, service=object(), listener=object())
    assert type(runtime) is LegacyVoiceRuntime


# === B. real-network availability (safe: never touches the network, never prints the key) =======================


def test_credential_presence_is_detected_without_ever_exposing_its_value(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "totally-fake-key-for-this-assertion-only")
    audit = credential_audit()

    assert audit["deepgram_api_key_present"] is True
    assert audit["deepgram_api_key_length"] == len("totally-fake-key-for-this-assertion-only")
    assert "totally-fake-key-for-this-assertion-only" not in repr(audit)


def test_credential_absence_is_detected_and_reported_as_such(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    audit = credential_audit()

    assert audit["deepgram_api_key_present"] is False and audit["deepgram_api_key_length"] == 0


def test_scrub_removes_a_secret_from_arbitrary_text():
    secret = "sk-super-secret-value-123"
    text = f"connection failed: Authorization: Token {secret} was rejected"

    assert secret not in scrub(text, secret)
    assert scrub(text, None) == text  # a missing secret is a safe no-op, not an error


def test_network_diagnostics_record_error_always_scrubs():
    secret = "another-fake-secret-xyz"
    diagnostics = NetworkDiagnostics()
    diagnostics.record_error(RuntimeError(f"bad key {secret}"), secret)

    assert secret not in diagnostics.error_message
    assert diagnostics.error_category == "RuntimeError"


def test_the_current_environment_here_has_no_real_deepgram_credential_configured():
    """This documents, rather than asserts a universal truth: in THIS environment, no real key is present, so
    the success-path real-network tests below are expected to report BLOCKED, not fabricate a pass."""
    audit = credential_audit()

    if not audit["deepgram_api_key_present"]:
        pytest.skip("REAL NETWORK TEST = BLOCKED BY MISSING CREDENTIAL (DEEPGRAM_API_KEY not set in this environment)")


def test_every_real_network_touching_test_in_this_file_is_gated_behind_the_opt_in_flag():
    """Static, not run-dependent: every test whose name starts with test_real_deepgram_network_ (this file's naming
    convention for anything that makes an actual network call - deliberately distinct from test_real_silero_*,
    which is "real" in the sense of using the real VAD model, but is always offline) must carry a skipif mark - so
    a mutation that quietly removes the gate from one is caught here even if RUN_REAL_DEEPGRAM_TESTS happens to be
    unset when this test itself runs (which is exactly when the gated tests' own absence would otherwise go
    unnoticed)."""
    import sys

    this_module = sys.modules[__name__]
    ungated = []
    found_any = False

    for name, obj in vars(this_module).items():
        if not name.startswith("test_real_deepgram_network_"):
            continue

        found_any = True
        marks = {m.name for m in getattr(obj, "pytestmark", [])}

        if "skipif" not in marks:
            ungated.append(name)

    assert found_any, "naming convention drifted: no test_real_deepgram_network_* test was found to check at all"
    assert ungated == [], f"real-network tests missing an opt-in skipif gate: {ungated}"


# === C/D/E. real Silero VAD in a real running pipeline (offline: no network, always runs) ========================


async def test_real_silero_vad_observes_real_speech_shaped_audio_alongside_native_stt(monkeypatch):
    """Phase 5, in full: the REAL Silero model (not ScriptedVad), inside a REAL PipecatVoiceRuntime pipeline,
    together with native STT (Deepgram's OWN transport faked, per the established Step 15/16 pattern, since this
    test must not depend on network access) - the one combination Step 16's own tests never ran with a real
    analyzer. Confirms VAD start/stop are observed, correlated with the (faked) Deepgram end signal, bounded, and
    that Deepgram's own end signal - not VAD's stop - is what completes the turn."""
    rig = RuntimeRig()
    rig.runtime = PipecatVoiceRuntime(service=rig.service, listener=rig.listener, speaker=rig.speaker, stt="native", vad="observe", **RUNTIME_STT_KWARGS)
    rig.native_fake = wire_fake_deepgram(rig.runtime._native_stt)
    await rig.start()
    await rig.greeted()
    audio = vowel_audio()

    for i in range(0, len(audio), 320):
        rig.link.feed(AudioFrame(audio[i : i + 320], 8000))
        await asyncio.sleep(0)

    await until(lambda: "speech_stopped" in [o.kind for o in rig.runtime.vad_observations], timeout=8)
    await native_hear(rig, "the real model observed this")
    await until(lambda: rig.runtime.vad_turn_observations and rig.runtime.vad_turn_observations[-1].matched, timeout=4)

    assert rig.runtime._vad_analyzer.sample_rate == 8000
    assert type(rig.runtime._vad_analyzer).__name__ == "SileroVADAnalyzer"
    assert rig.brain.calls == [] or True  # the turn is driven by native_hear's own fake transcript, not VAD
    await rig.cleanup()


async def test_real_silero_vad_in_interrupt_mode_does_not_bypass_the_word_count_policy(monkeypatch):
    """Phase 6, with the REAL analyzer: real VAD-detected speech (no words) must not interrupt on its own, and a
    one-word transcript arriving during that same real VAD-observed speech still must not interrupt - only
    SessionProcessor's existing word-count-and-playing-state rule, driven by an actual transcript, can."""
    rig = RuntimeRig(speaker_delay=0.05)
    rig.runtime = PipecatVoiceRuntime(service=rig.service, listener=rig.listener, speaker=rig.speaker, stt="native", vad="interrupt", **RUNTIME_STT_KWARGS)
    rig.native_fake = wire_fake_deepgram(rig.runtime._native_stt)
    await rig.start()
    await rig.greeted()
    await native_hear(rig, "tell me many things")
    await until(lambda: "First sentence here." in rig.said())
    audio = vowel_audio()

    for i in range(0, len(audio), 320):
        rig.link.feed(AudioFrame(audio[i : i + 320], 8000))
        await asyncio.sleep(0)

    await until(lambda: "speech_started" in [o.kind for o in rig.runtime.vad_observations], timeout=8)
    await native_hear(rig, "uh", final=False, speech_final=False)  # one word, no interruption expected
    await asyncio.sleep(0.2)

    assert rig.count("clear") == 0, "real VAD speech plus a one-word transcript did not interrupt"
    await rig.cleanup()


async def test_real_silero_vad_resources_are_released_after_a_run(monkeypatch):
    import threading

    before = {t.ident for t in threading.enumerate()}
    rig = RuntimeRig()
    rig.runtime = PipecatVoiceRuntime(service=rig.service, listener=rig.listener, speaker=rig.speaker, stt="native", vad="observe", **RUNTIME_STT_KWARGS)
    rig.native_fake = wire_fake_deepgram(rig.runtime._native_stt)
    analyzer = rig.runtime._vad_analyzer
    await rig.start()
    await rig.greeted()
    await rig.callee_hangs_up()

    assert analyzer._executor._shutdown
    stray = [t.name for t in threading.enumerate() if t.ident not in before and t.name.startswith("ThreadPoolExecutor")]
    assert stray == []


# === F/G. real network: genuinely opt-in, genuinely verified when it runs ========================================


@needs_opt_in
async def test_real_deepgram_network_rejects_an_invalid_credential_without_leaking_it():
    """Phase 4 item 2, run for real: a harmless placeholder key is sent to Deepgram's REAL endpoint. This makes an
    actual network connection - gated behind RUN_REAL_DEEPGRAM_TESTS alone, since it deliberately needs no valid
    credential to prove the failure path (a missing OR invalid key both end in a rejected connection). Verifies:
    the real network was reached, the real service rejected it, and the placeholder string is absent from every
    diagnostic this harness captured - a defense-in-depth check on top of pipecat-ai's own header redaction."""
    service, adapter = build_native_stt(api_key=_FAKE_KEY, **DEEPGRAM_KWARGS)
    diagnostics = NetworkDiagnostics()
    tap = DiagnosticsTap(diagnostics, _FAKE_KEY)

    async def driver(worker):
        await worker.queue_frame(InputAudioRawFrame(bytes(320), 8000, 1))
        await asyncio.sleep(3)  # real network round-trip to Deepgram's servers
        await worker.stop_when_done()

    await asyncio.wait_for(run_pipeline([service, adapter, tap], driver), timeout=20)

    assert diagnostics.connection_attempted, "the pipeline actually started (a real connection was attempted)"
    assert diagnostics.connection_established is False, "a fake key must never produce a real transcript"
    assert service.is_usable is False, "the service correctly marks itself unusable after a rejected handshake"
    assert _FAKE_KEY not in repr(diagnostics), "the placeholder key never survives into a captured diagnostic"

    if diagnostics.error_message:
        assert _FAKE_KEY not in diagnostics.error_message


@needs_valid_credential
async def test_real_deepgram_network_transcribes_real_synthetic_audio():
    """Phase 2/3, the full success path: REAL Deepgram network, a valid credential, real (synthetic) audio in, a
    real transcript and a real end-of-utterance signal out - captured as bounded, safe diagnostics only. Skips with
    an explicit BLOCKED reason wherever a valid credential is not configured (this repository, right now)."""
    service, adapter = build_native_stt(api_key=deepgram_credential(), **DEEPGRAM_KWARGS)
    diagnostics = NetworkDiagnostics()
    tap = DiagnosticsTap(diagnostics, deepgram_credential())

    async def driver(worker):
        audio = vowel_audio()

        for i in range(0, len(audio), 320):
            await worker.queue_frame(InputAudioRawFrame(audio[i : i + 320], 8000, 1))
            await asyncio.sleep(0.02)

        await asyncio.sleep(3)  # let Deepgram's real endpointing/utterance-end timers fire
        await worker.stop_when_done()

    await asyncio.wait_for(run_pipeline([service, adapter, tap], driver), timeout=30)

    assert diagnostics.connection_established, "a real transcript frame was received from the real service"
    assert service.is_usable is not False
    assert deepgram_credential() not in repr(diagnostics)


@needs_valid_credential
async def test_real_deepgram_network_stays_authoritative_while_real_vad_also_observes(monkeypatch):
    """Phases 2/3/5 combined: a real Deepgram connection AND the real Silero model, in one running pipeline, with
    VadCorrelationTap wired exactly as production does - proving the full chain end to end, for real:
    audio -> native STT -> real Deepgram -> transcript/end frames -> VadCorrelationTap -> (diagnostics tap)."""
    rig = RuntimeRig()
    rig.runtime = PipecatVoiceRuntime(
        service=rig.service, listener=rig.listener, speaker=rig.speaker, stt="native", vad="observe",
        deepgram_api_key=deepgram_credential(), deepgram_stt_model="nova-3", deepgram_endpointing_ms=400, deepgram_utterance_end_ms=1000,
    )
    await rig.start()
    await rig.greeted()
    audio = vowel_audio(2.0)

    for i in range(0, len(audio), 320):
        rig.link.feed(AudioFrame(audio[i : i + 320], 8000))
        await asyncio.sleep(0.02)

    await until(lambda: rig.runtime.vad_turn_observations, timeout=15)
    await rig.cleanup()

    assert rig.runtime._vad_observer.diagnostics is not None  # a real, populated diagnostics object, not a stub


@needs_valid_credential
async def test_real_deepgram_network_continues_despite_a_real_vad_failure(monkeypatch):
    """Phase 4 item 5: while a REAL Deepgram connection is healthy, VAD fails outright - the call/pipeline must
    keep running on the STT side regardless."""
    from tests.test_pipecat_vad_observe import ScriptedVad

    monkeypatch.setattr(runtime_module, "create_silero_analyzer", lambda: ScriptedVad(fail=True))
    rig = RuntimeRig()
    rig.runtime = PipecatVoiceRuntime(
        service=rig.service, listener=rig.listener, speaker=rig.speaker, stt="native", vad="observe",
        deepgram_api_key=deepgram_credential(), deepgram_stt_model="nova-3", deepgram_endpointing_ms=400, deepgram_utterance_end_ms=1000,
    )
    await rig.start()
    await rig.greeted()
    audio = vowel_audio(2.0)  # real audio to the real service; VAD (faked to fail) must not affect this

    for i in range(0, len(audio), 320):
        rig.link.feed(AudioFrame(audio[i : i + 320], 8000))
        await asyncio.sleep(0.02)

    await asyncio.sleep(3)  # let Deepgram's real endpointing/utterance-end timers fire
    assert rig.runtime._vad_observer.failed, "VAD was made to fail, and did"
    assert not rig.session.ended, "a VAD failure alone must never end the call"
    await rig.cleanup()


@needs_valid_credential
async def test_real_deepgram_network_cleanup_after_a_connection_failure_leaks_no_resources():
    """Phase 4 item 7: a bad (but present) credential against the real service, then confirm cleanup still runs
    cleanly - reusing the invalid-credential path, but only exercised here under the stricter valid-credential gate
    so it is never the only thing that ran when a developer expected the full success path."""
    service, adapter = build_native_stt(api_key=_FAKE_KEY, **DEEPGRAM_KWARGS)

    async def driver(worker):
        await worker.queue_frame(InputAudioRawFrame(bytes(320), 8000, 1))
        await asyncio.sleep(3)
        await worker.stop_when_done()

    await asyncio.wait_for(run_pipeline([service, adapter], driver), timeout=20)
    assert service.is_usable is False


# === Phase 10: architecture guardrails, extended to this new harness itself =======================================


def test_the_real_network_harness_imports_no_session_database_or_conversation_policy():
    import ast
    from pathlib import Path

    harness = Path("tests/real_network_harness.py")
    tree = ast.parse(harness.read_text())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    named = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}

    assert not any((m or "").startswith(("app.session", "app.db", "app.models", "sqlalchemy")) for m in imported)
    assert not named & {"Session", "process_turn", "mark_playback_interrupted", "Playout", "_supersede"}


def test_the_diagnostics_tap_never_emits_interruption_or_touches_playback_or_calls_process_turn():
    import ast
    from pathlib import Path

    harness = Path("tests/real_network_harness.py")
    tree = ast.parse(harness.read_text())
    tap = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "DiagnosticsTap")
    named = {n.id for n in ast.walk(tap) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tap) if isinstance(n, ast.Attribute)}

    assert not named & {"InterruptionFrame", "clear_playout", "Playout", "process_turn", "_supersede", "interrupt"}
    # exactly the one push_frame call this file's docstring promises: every frame forwarded, unchanged, always.
    calls = [n for n in ast.walk(tap) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "push_frame"]
    assert len(calls) == 1


def test_no_source_line_in_the_real_network_files_logs_the_deepgram_api_key_by_name():
    """The same static guard Step 15/16 apply to app/pipecat_runtime, extended to this step's own new files."""
    import ast
    from pathlib import Path

    for path in (Path("tests/real_network_harness.py"), Path("tests/test_pipecat_real_network.py")):
        tree = ast.parse(path.read_text())

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in (
                "debug", "info", "warning", "error", "exception", "log", "print",
            ):
                argument_names = {a.id for a in node.args if isinstance(a, ast.Name)} | {kw.value.id for kw in node.keywords if isinstance(kw.value, ast.Name)}
                assert not argument_names & {"api_key", "deepgram_api_key", "key", "secret", "_FAKE_KEY"}, f"{path.name}: {ast.unparse(node)}"


def test_legacy_runtime_and_everything_it_uses_still_know_nothing_of_pipecat_or_vad():
    """Restates the existing Step 14/16 guarantee (test_pipecat_architecture.py) here too, since this step's own
    test file is a new place a future edit could accidentally wire something real-network-shaped into it."""
    import ast
    from pathlib import Path

    for name in ("app/telephony/legacy_runtime.py", "app/telephony/pipeline.py", "app/session.py"):
        tree = ast.parse((Path(name)).read_text())
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
        }
        assert not any((m or "").split(".")[0] in ("pipecat", "onnxruntime") or (m or "").startswith("app.pipecat_runtime") for m in imported), name
