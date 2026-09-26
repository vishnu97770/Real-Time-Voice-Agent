"""The dependency rules of the Pipecat runtime, checked from the source (AST), not from behavior.

Pipecat carries frames; `Session.process_turn` owns the conversation. These tests keep it that way: the package reads no
database, knows no workflow, dispatcher, scheduler or provider, contains no policy, and is the only code that imports
Pipecat. They also prove the default path never loads it."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_media import imports

BACKEND = Path(__file__).parents[1]
PACKAGE = BACKEND / "app/pipecat_runtime"
MODULES = sorted(p for p in PACKAGE.glob("*.py"))


def names_used(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}


def imported_modules(path: Path) -> set[str]:
    """Every module a file imports, and every name it imports from one, as dotted paths."""
    return {m for m in imports(path) if m}


def test_the_package_has_only_the_modules_that_are_needed():
    assert [p.name for p in MODULES] == [
        "__init__.py", "frames.py", "native_stt.py", "playout.py", "recognizer.py", "runtime.py", "session_processor.py",
        "speaker.py", "transport.py", "vad_observer.py",
    ]


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_the_package_reads_no_database_and_knows_no_workflow_dispatcher_scheduler_or_domain_record(path):
    forbidden = (
        "app.workflows", "app.outbound", "app.db", "app.models", "app.schemas", "app.service", "app.store", "app.main", "app.auth",
        "app.voice_context", "app.cli", "sqlalchemy", "alembic", "psycopg",
    )

    assert [m for m in imported_modules(path) if m.startswith(forbidden)] == [], path.name


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_the_package_never_calls_a_telephony_provider_or_the_telephony_adapter(path):
    forbidden_imports = ("app.telephony.twilio", "app.telephony.base", "app.telephony.routes", "app.telephony.media", "app.telephony.pipeline")

    assert [m for m in imported_modules(path) if m.startswith(forbidden_imports)] == [], path.name
    tree = ast.parse(path.read_text())
    types_named = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}  # `x.hang_up`, `x.adapter`, not a local flag

    assert not types_named & {"TelephonyAdapter", "TwilioTelephony", "ProviderCall", "PlaceCall"}, path.name
    assert not attributes & {"hang_up", "adapter", "twilio"}, path.name


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_the_package_imports_no_llm_brain_or_profile_code(path):
    forbidden = ("app.brains", "app.profiles", "google", "openai", "anthropic")

    assert [m for m in imported_modules(path) if m.startswith(forbidden)] == [], path.name


def test_session_processor_is_the_only_module_of_the_package_that_imports_app_session():
    importers = [p.name for p in MODULES if [m for m in imported_modules(p) if m == "app.session" or m.startswith("app.session.")]]

    assert importers == ["session_processor.py"]


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_pipecat_llm_tts_or_turn_detection_is_used_and_vad_and_native_stt_only_where_declared(path):
    """Pipecat's language-model services, its text-to-speech, its context aggregators, its turn detection (strategies,
    the user-turn processor, smart turn) and its serializers are none of them in the path, anywhere. Its
    voice-activity detection is imported by the observer and by nothing else. Of its speech-to-text services, the one
    Deepgram STT service is imported by native_stt.py, deliberately (Step 15) and by nothing else."""
    forbidden = (
        "pipecat.processors.aggregators", "pipecat.audio.turn", "pipecat.audio.filters", "pipecat.audio.mixers",
        "pipecat.turns", "pipecat.serializers", "pipecat.adapters", "pipecat.processors.frameworks", "pipecat.transports.websocket",
        "pipecat.transports.livekit", "pipecat.processors.audio",
    )

    assert [m for m in imported_modules(path) if m.startswith(forbidden)] == [], path.name

    services = sorted(m for m in imported_modules(path) if m.startswith("pipecat.services"))

    if path.name == "native_stt.py":
        assert services and all(m.startswith("pipecat.services.deepgram.stt") for m in services), services
    else:
        assert services == [], path.name

    vad = [m for m in imported_modules(path) if m.startswith("pipecat.audio.vad")]
    assert vad == [] or path.name == "vad_observer.py", f"{path.name} imports Pipecat's VAD"


def test_the_package_contains_no_conversation_policy_of_its_own():
    policy = {
        "classify_identity", "classify_confirmation", "detect_sensitive", "redact_sensitive", "solicits_secret", "is_filler",
        "_run_tool", "close_session", "GeminiBrain", "BrainContext", "OutboundState", "create_session", "open_call",
    }

    for path in MODULES:
        assert not names_used(path) & policy, path.name

    used = {p.name: names_used(p) for p in MODULES}
    assert [n for n, u in used.items() if "process_turn" in u] == ["session_processor.py"], "the conversation is called from one place"
    assert [n for n, u in used.items() if "spoken_digits_to_numerals" in u] == ["session_processor.py"]
    assert [n for n, u in used.items() if "mark_playback_interrupted" in u] == ["session_processor.py"]


def test_only_the_package_imports_pipecat_and_only_the_factory_reaches_it_and_lazily():
    for path in (BACKEND / "app").rglob("*.py"):
        if PACKAGE in path.parents:
            continue

        assert not [m for m in imported_modules(path) if m.split(".")[0] == "pipecat"], f"{path.relative_to(BACKEND)} imports Pipecat"

    factory = BACKEND / "app/voice_runtime_factory.py"
    top_level = ast.parse(factory.read_text()).body
    module_level = {m for node in top_level if isinstance(node, (ast.Import, ast.ImportFrom)) for m in imports_of(node)}

    assert not [m for m in module_level if m.startswith(("pipecat", "app.pipecat_runtime"))], "the factory imports the runtime lazily"
    assert "app.pipecat_runtime.runtime" in imported_modules(factory), "and does reach it, inside a function"


def imports_of(node) -> set[str]:
    if isinstance(node, ast.Import):
        return {a.name for a in node.names}

    return {node.module or ""} | {f"{node.module}.{a.name}" for a in node.names}


def test_the_generic_media_interfaces_still_name_no_pipecat_provider_or_wire_concept():
    for name in ("app/media.py", "app/voice_runtime.py"):
        assert not [m for m in imported_modules(BACKEND / name) if m.startswith(("pipecat", "app.pipecat_runtime"))], name


def test_the_workflow_layer_the_session_and_the_service_do_not_know_the_runtime_setting():
    """The flag is read in the factory and set in the settings and the telephony bundle; nowhere else."""
    allowed = {"app/config.py", "app/telephony/__init__.py", "app/voice_runtime_factory.py"}

    for path in (BACKEND / "app").rglob("*.py"):
        relative = str(path.relative_to(BACKEND))

        if relative in allowed:
            continue

        assert not names_used(path) & {"voice_runtime", "voice_runtime_pipecat_agents", "voice_runtime_pipecat_agent_ids"}, relative


def test_the_route_chooses_the_runtime_once_before_running_it_and_never_inside_a_loop():
    source = (BACKEND / "app/telephony/routes.py").read_text()
    tree = ast.parse(source)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "create_voice_runtime"]

    assert len(calls) == 1
    loops = [n for n in ast.walk(tree) if isinstance(n, (ast.While, ast.For, ast.AsyncFor))]
    assert not [loop for loop in loops if calls[0] in ast.walk(loop)], "not repeated while the call goes on"
    assert source.index("create_voice_runtime(") < source.index("runtime.run("), "chosen, then run"


def test_pipecat_is_not_imported_when_the_application_starts_and_a_legacy_call_is_set_up():
    """In a fresh interpreter: import the app, build the runtime for a call with the default setting, and see that
    neither Pipecat nor the runtime package was ever loaded."""
    script = "\n".join(
        [
            "import os, sys, types",
            "os.environ['DATABASE_URL'] = 'sqlite://'; os.environ['VOICE_AGENT_API_KEY'] = 'k'",
            "import app.main",
            "from app.telephony import Telephony",
            "from app.voice_runtime_factory import create_voice_runtime",
            "from app.telephony.legacy_runtime import LegacyVoiceRuntime",
            "telephony = Telephony(twilio=None, open_listener=None, speaker=object(), public_api_url='https://x', auth_token='t', adapter=object())",
            "session = types.SimpleNamespace(id='CALL-1', context=None)",
            "runtime = create_voice_runtime(session, telephony=telephony, service=object(), listener=object())",
            "assert type(runtime) is LegacyVoiceRuntime, type(runtime)",
            "loaded = sorted(m for m in sys.modules if m.split('.')[0] in ('pipecat', 'loguru', 'onnxruntime') or m.startswith('app.pipecat_runtime'))",
            "assert not loaded, loaded",
            "print('clean')",
        ]
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=BACKEND, capture_output=True, text=True, timeout=120)

    assert result.returncode == 0 and "clean" in result.stdout, result.stdout + result.stderr


# --- the VAD observer -------------------------------------------------------------------------------------------------------------

VAD = PACKAGE / "vad_observer.py"


def test_the_vad_observer_imports_no_database_workflow_dispatcher_scheduler_or_service_code():
    forbidden = (
        "app.db", "app.models", "app.schemas", "sqlalchemy", "alembic", "psycopg",  # database
        "app.workflows", "app.outbound",  # workflows, the dispatcher and the scheduler
        "app.service", "app.store", "app.voice_context", "app.main",
    )

    assert [m for m in imported_modules(VAD) if m.startswith(forbidden)] == []


def test_the_vad_observer_does_not_know_the_session_and_never_touches_the_conversation_or_the_playback():
    tree = ast.parse(VAD.read_text())
    named = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}

    assert not [m for m in imported_modules(VAD) if m == "app.session" or m.startswith("app.session.")]
    assert not named & {"Session", "process_turn", "mark_playback_interrupted", "_supersede", "InterruptionFrame", "Playout"}
    assert not named & {"clear_playout", "checkpoint_playout", "apply_interrupt", "interrupt", "send_audio", "close"}, "it never touches the link"
    assert not named & {"TelephonyAdapter", "adapter", "hang_up", "twilio", "TwilioTelephony", "ProviderCall"}
    assert not named & {"ProposedUserStoppedSpeakingFrame", "UserStoppedSpeakingFrame", "UserStartedSpeakingFrame", "TranscriptionFrame"}


def test_the_vad_observer_emits_nothing_of_its_own_it_only_passes_frames_on_as_it_was_given_them():
    """Two processors live in this module (Step 16 added VadCorrelationTap alongside VadObserver); each forwards
    every frame it is given, unchanged, and pushes nothing else."""
    calls = [n for n in ast.walk(ast.parse(VAD.read_text())) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") in ("push_frame", "broadcast_frame", "queue_frame")]

    assert [(c.func.attr, [getattr(a, "id", "?") for a in c.args]) for c in calls] == [
        ("push_frame", ["frame", "direction"]),
        ("push_frame", ["frame", "direction"]),
    ]


def test_the_user_turn_processor_smart_turn_and_deepgram_flux_are_never_imported_and_deepgram_stt_only_where_declared():
    """Native STT (Step 15) is `pipecat.services.deepgram.stt.DeepgramSTTService` and `deepgram.listen.v1.types`,
    imported by native_stt.py alone; Deepgram Flux (Pipecat's OWN turn-detecting STT), UserTurnProcessor and smart
    turn are never imported anywhere, including there."""
    for path in MODULES:
        tree = ast.parse(path.read_text())
        named = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        deepgram_imports = sorted(m for m in imported_modules(path) if m.split(".")[0] == "deepgram" or "services.deepgram" in m)

        if path.name == "native_stt.py":
            assert deepgram_imports and all(m.startswith(("deepgram.listen.v1.types", "pipecat.services.deepgram.stt")) for m in deepgram_imports), deepgram_imports
        else:
            assert deepgram_imports == [], path.name
            assert "DeepgramSTTService" not in named, path.name

        assert not named & {"DeepgramFluxSTTService", "UserTurnProcessor", "LocalSmartTurnAnalyzerV3", "UserTurnStrategies"}, path.name
        assert not [n for n in named if "SmartTurn" in n or "MinWordsUserTurnStart" in n], path.name
        assert "livekit" not in [m.split(".")[0] for m in imported_modules(path)] and "LiveKit" not in "".join(named), path.name


def test_native_stt_is_reachable_only_from_the_pipecat_runtime_module():
    importers = sorted(p.name for p in MODULES if "app.pipecat_runtime.native_stt" in imported_modules(p))

    assert importers == ["runtime.py"]


# --- native STT (Step 15) ----------------------------------------------------------------------------------------------------------

NATIVE_STT = PACKAGE / "native_stt.py"


def test_the_native_adapter_does_not_call_clear_playout_or_decide_an_interruption():
    tree = ast.parse(NATIVE_STT.read_text())
    named = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}

    assert not named & {"clear_playout", "checkpoint_playout", "apply_interrupt", "interrupt", "_supersede"}, "it never touches the link"
    assert not named & {"InterruptionFrame", "mark_playback_interrupted", "Playout"}, "it decides no business interruption"
    assert not named & {"process_turn", "Session", "spoken_digits_to_numerals"}, "no conversation policy either"


def test_the_default_stt_mode_stays_compat_in_the_settings_the_bundle_and_the_runtime():
    settings_source = ast.parse((BACKEND / "app/config.py").read_text())
    defaults = {
        n.target.id: ast.unparse(n.value)
        for n in ast.walk(settings_source)
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None
    }
    bundle = {
        n.target.id: ast.unparse(n.value)
        for n in ast.walk(ast.parse((BACKEND / "app/telephony/__init__.py").read_text()))
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None
    }
    runtime_init = next(
        n for n in ast.walk(ast.parse((PACKAGE / "runtime.py").read_text())) if isinstance(n, ast.FunctionDef) and n.name == "__init__" and any(a.arg == "stt" for a in n.args.kwonlyargs)
    )
    runtime_default = {a.arg: ast.unparse(d) for a, d in zip(runtime_init.args.kwonlyargs, runtime_init.args.kw_defaults) if d is not None}["stt"]

    assert defaults["voice_pipecat_stt"] == "'compat'" and bundle["voice_pipecat_stt"] == "'compat'" and runtime_default == "'compat'"


def test_no_source_line_in_the_package_logs_the_deepgram_api_key_by_name():
    """Static guard: no `log`/`logger` call anywhere in the package is even given the key as an argument. The
    behavioral guard (the key never actually reaching a log line, including one from within pipecat-ai or
    deepgram-sdk's own code) is tests/test_pipecat_native_stt.py's own job, since that needs a running call."""
    for path in MODULES:
        tree = ast.parse(path.read_text())

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in (
                "debug", "info", "warning", "error", "exception", "log",
            ):
                argument_names = {a.id for a in node.args if isinstance(a, ast.Name)} | {kw.value.id for kw in node.keywords if isinstance(kw.value, ast.Name)}
                assert not argument_names & {"api_key", "deepgram_api_key"}, f"{path.name}: {ast.unparse(node)}"


def test_the_runtime_reaches_the_vad_only_through_the_observer_module():
    imports_of_runtime = imported_modules(PACKAGE / "runtime.py")

    assert "app.pipecat_runtime.vad_observer" in imports_of_runtime
    assert not [m for m in imports_of_runtime if m.startswith("pipecat.audio")]


def test_the_legacy_runtime_and_everything_it_uses_know_nothing_of_pipecat_or_the_vad():
    for name in ("app/telephony/legacy_runtime.py", "app/telephony/pipeline.py", "app/telephony/media.py", "app/telephony/deepgram.py", "app/session.py"):
        imported = imported_modules(BACKEND / name)

        assert not [m for m in imported if m.split(".")[0] in ("pipecat", "onnxruntime") or m.startswith("app.pipecat_runtime")], name


def test_the_vad_default_stays_off_in_the_settings_the_bundle_and_the_runtime():
    settings_source = ast.parse((BACKEND / "app/config.py").read_text())
    defaults = {
        n.target.id: ast.unparse(n.value)
        for n in ast.walk(settings_source)
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None
    }
    bundle = {
        n.target.id: ast.unparse(n.value)
        for n in ast.walk(ast.parse((BACKEND / "app/telephony/__init__.py").read_text()))
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None
    }
    runtime_init = next(
        n for n in ast.walk(ast.parse((PACKAGE / "runtime.py").read_text())) if isinstance(n, ast.FunctionDef) and n.name == "__init__" and any(a.arg == "vad" for a in n.args.kwonlyargs)
    )
    runtime_default = {a.arg: ast.unparse(d) for a, d in zip(runtime_init.args.kwonlyargs, runtime_init.args.kw_defaults) if d is not None}["vad"]

    assert defaults["voice_pipecat_vad"] == "'off'" and bundle["voice_pipecat_vad"] == "'off'" and runtime_default == "'off'"


# --- correlation and the interrupt hook (Step 16) -----------------------------------------------------------------------------------

PROCESSOR = BACKEND / "app/pipecat_runtime/session_processor.py"
RUNTIME = PACKAGE / "runtime.py"


def test_vad_correlation_tap_reads_only_frame_types_never_frame_content():
    """VadCorrelationTap's whole job is `isinstance(frame, ...)`; it must never read an attribute OFF a frame (its
    text, its confidence, anything), which would mean it had started looking at content instead of type."""
    tree = ast.parse(VAD.read_text())
    tap = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "VadCorrelationTap")
    frame_attribute_reads = [
        n.attr
        for n in ast.walk(tap)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "frame"
    ]

    assert frame_attribute_reads == [], frame_attribute_reads


def test_the_note_methods_vad_calls_back_into_take_no_arguments_carrying_content():
    """`note_transcript_seen` and `note_end_signal` (VadObserver) are the only things VadCorrelationTap calls on the
    observer; neither is ever passed the frame, its text or anything else - they are bare notifications."""
    tree = ast.parse(VAD.read_text())
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") in ("note_transcript_seen", "note_end_signal")
    ]

    assert calls and all(not c.args and not c.keywords for c in calls), "both are called with nothing"


def test_note_vad_speech_hint_is_inert_by_construction_it_only_ever_increments_its_own_counter():
    """Statically pins the one thing Stage B's inert hook is allowed to do: SessionProcessor.note_vad_speech_hint's
    body is exactly one statement, and that statement only augments `self.vad_speech_hints_received`. It cannot grow
    a call to `_supersede`, `process_turn`, `_playout` or anything else without this test failing first."""
    tree = ast.parse(PROCESSOR.read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "note_vad_speech_hint")
    body = [n for n in method.body if not isinstance(n, ast.Expr) or not isinstance(n.value, ast.Constant)]  # skip the docstring

    assert len(body) == 1, ast.unparse(method)
    (statement,) = body
    assert isinstance(statement, ast.AugAssign) and isinstance(statement.op, ast.Add), ast.unparse(statement)
    assert ast.unparse(statement.target) == "self.vad_speech_hints_received", ast.unparse(statement)


def test_the_runtime_wires_the_interrupt_hook_only_to_note_vad_speech_hint_and_only_in_interrupt_mode():
    tree = ast.parse(RUNTIME.read_text())
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "VadObserver")
    hint = next(k.value for k in call.keywords if k.arg == "on_speech_hint")

    assert ast.unparse(hint) == "session_processor.note_vad_speech_hint if self._vad_mode == 'interrupt' else None"


def test_vad_observer_and_correlation_tap_never_call_process_turn_or_emit_interruption_or_touch_playout():
    """Restates, for both Step-16 processors specifically (rather than the whole module as the older tests already
    do), the exact set of forbidden calls the prompt singles out."""
    tree = ast.parse(VAD.read_text())
    named = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}

    assert not named & {"process_turn", "InterruptionFrame", "clear_playout", "Playout", "_supersede", "mark_playback_interrupted"}
