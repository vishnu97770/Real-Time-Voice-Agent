"""The Pipecat version contract: exactly 1.11.0, and only the Pipecat symbols the runtime actually uses.

Pipecat deprecates and removes API between minor versions (PipelineTask and PipelineRunner in 1.3, StartFrame fields in
1.8), so the runtime is pinned and refuses to start on any other version. These tests are what a future upgrade has to
get past: each says what the runtime relies on, in a form that fails loudly if Pipecat changes it."""

import ast
import importlib
import importlib.metadata
import inspect
from pathlib import Path

import pytest

BACKEND = Path(__file__).parents[1]
PACKAGE = BACKEND / "app/pipecat_runtime"
PINNED = "1.11.0"

# Every Pipecat name the runtime imports. Adding a use of Pipecat means adding it here, on purpose.
USED = {
    "pipecat.audio.vad.silero": {"SileroVADAnalyzer"},
    "pipecat.audio.vad.vad_analyzer": {"VADAnalyzer"},
    "pipecat.audio.vad.vad_controller": {"VADController"},
    "pipecat.frames.frames": {
        "DataFrame", "Frame", "InputAudioRawFrame", "InterimTranscriptionFrame", "InterruptionFrame", "OutputAudioRawFrame",
        "ProposedUserStoppedSpeakingFrame", "StartFrame", "TranscriptionFrame", "TTSAudioRawFrame", "TTSSpeakFrame",
        "TTSStoppedFrame", "UninterruptibleFrame",
    },
    "pipecat.pipeline.pipeline": {"Pipeline"},
    "pipecat.pipeline.worker": {"PipelineParams", "PipelineWorker"},
    "pipecat.processors.frame_processor": {"FrameDirection", "FrameProcessor", "FrameProcessorSetup"},
    "pipecat.services.deepgram.stt": {"DeepgramSTTService"},
    "pipecat.transcriptions.language": {"Language"},
    "pipecat.transports.base_input": {"BaseInputTransport"},
    "pipecat.transports.base_output": {"BaseOutputTransport"},
    "pipecat.transports.base_transport": {"TransportParams"},
    "pipecat.utils.time": {"time_now_iso8601"},
    "pipecat.workers.runner": {"WorkerRunner"},
}


def pipecat_imports() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}

    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "pipecat":
                found.setdefault(node.module, set()).update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                assert not [a for a in node.names if a.name.split(".")[0] == "pipecat"], f"use 'from pipecat... import' ({path.name})"

    return found


PINNED_DEEPGRAM_SDK = "7.10.0"  # what pipecat-ai's own "deepgram" extra resolves to for 1.11.0; native STT only


def test_the_requirements_file_pins_pipecat_and_native_stts_deepgram_sdk_and_the_main_requirements_name_neither():
    lines = [l.split("#")[0].strip() for l in (BACKEND / "requirements-pipecat.txt").read_text().splitlines()]

    assert sorted(l for l in lines if l) == sorted([f"pipecat-ai=={PINNED}", f"deepgram-sdk=={PINNED_DEEPGRAM_SDK}"])
    assert "pipecat" not in (BACKEND / "requirements.txt").read_text().lower(), "optional: the main requirements stay as they were"
    assert "deepgram-sdk" not in (BACKEND / "requirements.txt").read_text().lower()


def test_the_installed_version_the_declared_version_and_the_pin_are_the_same():
    from app.pipecat_runtime import PIPECAT_VERSION

    assert PIPECAT_VERSION == PINNED == importlib.metadata.version("pipecat-ai")


def test_the_runtime_imports_exactly_the_pipecat_symbols_declared_here():
    assert pipecat_imports() == USED


@pytest.mark.parametrize("module, names", sorted(USED.items()))
def test_every_symbol_the_runtime_uses_exists_in_the_pinned_version(module, names):
    loaded = importlib.import_module(module)

    assert [name for name in names if not hasattr(loaded, name)] == []


def test_pipecats_defaults_are_what_the_runtime_overrides():
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.workers.runner import WorkerRunner

    params = PipelineParams()
    assert (params.audio_in_sample_rate, params.audio_out_sample_rate) == (16000, 24000)
    assert inspect.signature(WorkerRunner.__init__).parameters["handle_sigint"].default is True
    signature = inspect.signature(PipelineWorker.__init__).parameters
    assert signature["enable_rtvi"].default is True
    assert {"idle_timeout_secs", "cancel_on_idle_timeout"} <= set(signature)


def test_the_frame_semantics_the_runtime_is_built_on():
    from pipecat.frames.frames import (
        AudioRawFrame,
        DataFrame,
        InterruptionFrame,
        SystemFrame,
        TTSSpeakFrame,
        UserStoppedSpeakingFrame,
    )

    assert AudioRawFrame(b"\x00" * 640, 8000, 1).num_frames == 320, "16-bit samples: two bytes each, per channel"
    assert TTSSpeakFrame("x").append_to_context is True, "so the runtime always passes False"
    assert issubclass(UserStoppedSpeakingFrame, SystemFrame), "and system frames overtake data frames (see test_pipecat_transport)"
    assert issubclass(InterruptionFrame, SystemFrame) and not issubclass(InterruptionFrame, DataFrame)


def test_the_hooks_the_transports_and_processors_override_are_still_there():
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.transports.base_output import BaseOutputTransport
    from pipecat.transports.base_transport import TransportParams

    assert {"write_audio_frame", "write_transport_frame", "set_transport_ready", "start"} <= set(dir(BaseOutputTransport))
    assert {"push_audio_frame", "set_transport_ready"} <= set(dir(importlib.import_module("pipecat.transports.base_input").BaseInputTransport))
    assert {"create_task", "cancel_task", "cleanup", "push_frame", "process_frame"} <= set(dir(FrameProcessor))
    assert {"audio_out_auto_silence", "audio_out_end_silence_secs", "audio_out_10ms_chunks"} <= set(TransportParams.model_fields)

    worker = PipelineWorker(Pipeline([]), enable_rtvi=False, idle_timeout_secs=None)
    assert {"on_pipeline_started", "on_pipeline_finished", "on_pipeline_error"} <= set(worker._event_handlers)
    assert {"cancel", "queue_frame"} <= set(dir(PipelineWorker))


def test_the_frame_semantics_the_end_of_utterance_and_the_transcript_protection_rely_on():
    from pipecat.frames.frames import (
        ControlFrame,
        InterimTranscriptionFrame,
        ProposedUserStoppedSpeakingFrame,
        SystemFrame,
        TranscriptionFrame,
        UninterruptibleFrame,
    )

    assert issubclass(ProposedUserStoppedSpeakingFrame, ControlFrame) and not issubclass(ProposedUserStoppedSpeakingFrame, SystemFrame), (
        "a control frame: it stays in order with the transcript, which the stock user-stopped frame (a system frame) does not"
    )
    assert not issubclass(TranscriptionFrame, UninterruptibleFrame) and not issubclass(InterimTranscriptionFrame, UninterruptibleFrame), (
        "the stock transcript frames are interruptible, which is why the runtime adds the marker"
    )
    assert not issubclass(ProposedUserStoppedSpeakingFrame, UninterruptibleFrame)


def test_the_vad_api_the_observer_relies_on():
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.audio.vad.vad_controller import VADController

    assert inspect.signature(SileroVADAnalyzer.__init__).parameters["sample_rate"].default is None, "and the runtime passes 8000"
    assert (VADParams().confidence, VADParams().start_secs, VADParams().stop_secs, VADParams().min_volume) == (0.7, 0.2, 0.2, 0.6), (
        "the defaults the observer uses and the docs describe"
    )
    assert {"setup", "start", "process_frame", "cleanup", "event_handler"} <= set(dir(VADController))
    assert {"_audio_idle_task"} <= set(vars(VADController(SileroVADAnalyzer.__new__(SileroVADAnalyzer))).keys())
