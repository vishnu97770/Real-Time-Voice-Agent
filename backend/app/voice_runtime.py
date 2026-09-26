"""The voice runtime: conducts one call's conversation over a media link.

    MediaLink  <->  VoiceRuntime  <->  Session (process_turn)

The runtime turns the callee's audio into utterances, gives each to the session, and plays what the session says.
It does not decide what may be said, who may hear what, or when a tool may run: the session's `process_turn`
owns all of that (guardrails, identity, confirmation, tool authorization, output vetting, transcript and audit,
the conversation with the brain). A runtime must not reimplement any of it, and must not wire audio straight to
a language model.

It is domain-blind: it imports no workflow, scheduling, job or database code.
"""

from typing import TYPE_CHECKING, Protocol

from app.media import MediaLink

if TYPE_CHECKING:
    from app.session import Session


class VoiceRuntime(Protocol):
    async def run(self, session: "Session", link: MediaLink, greeting: str) -> None:
        """Conduct the call: say `greeting`, then listen and reply until the media connection ends, the
        conversation ends, or this is cancelled. However it ends, the call is closed and recorded before
        this returns (or, if cancelled, still completes in the background)."""
        ...
