"""An opt-in voice runtime built on Pipecat (pinned: see PIPECAT_VERSION and requirements-pipecat.txt).

    MediaLink -> input transport -> recognizer -> SessionProcessor -> speaker -> output transport -> MediaLink
                                                        |
                                                 Session.process_turn   (all conversation policy)

Pipecat carries frames between these pieces and nothing else. It does not decide what may be said, who may hear
what, when a tool runs, or when the call is over: `Session.process_turn` does, exactly as for the legacy runtime.
Recognition and synthesis are the existing Deepgram classes; turn-taking is reproduced here rather than taken from
Pipecat's own turn detection. Nothing here reads a database or knows a workflow, job or provider.

With VOICE_PIPECAT_VAD=observe a Silero VAD also watches the audio and records when speech started and stopped. It
is observation only: it does not decide turns or interruptions, and the recognizer stays the authority for both.

Importing this package does not import Pipecat; its modules do. Only `runtime.py` is meant to be imported from
outside, and only by `app.voice_runtime_factory`, lazily.
"""

import os

# Pipecat logs through loguru, at DEBUG by default, and its frame descriptions include the text they carry. Ask for
# WARNING before loguru is first imported; runtime.py then routes what remains through `logging`, with text removed.
os.environ.setdefault("LOGURU_LEVEL", "WARNING")

PIPECAT_VERSION = "1.11.0"  # the one version this package was built and tested against
