"""The telephony control plane, as the application sees it: place a call, end it, and understand what the
provider tells us about it. Nothing here names a provider.

The application (`Service`) speaks only this. A provider (today Twilio) implements `TelephonyAdapter` and owns
everything that is its own: how a call is placed, how it is authenticated, its status vocabulary. Adding a
provider means writing one more adapter; it does not mean editing the service.

This is only the control plane. Audio (the media plane) is not part of it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class TelephonyError(Exception):
    """A provider refused or could not carry out a request. Provider errors subclass this."""


class InvalidWebhook(Exception):
    """A provider event failed authentication. The caller must not act on it."""


@dataclass(frozen=True)
class PlaceCall:
    """What placing a call needs, and nothing else (no contact data, no domain data)."""

    job_id: str
    to: str  # the destination, in international (E.164) format
    ring_seconds: int  # how long to let it ring; also bounds how long the call's media token stays valid


@dataclass(frozen=True)
class ProviderCall:
    """A call as its provider knows it."""

    provider: str
    provider_call_id: str


class CallEventKind(StrEnum):
    RINGING = "ringing"
    ANSWERED = "answered"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclass(frozen=True)
class CallEvent:
    """Something the provider told us about a call, in no provider's words."""

    kind: CallEventKind
    job_id: str
    provider: str
    provider_call_id: str | None = None
    # A short word for why, kept as the job's end_reason when the event ends it (e.g. "busy", "canceled").
    reason: str | None = None


@dataclass(frozen=True)
class WebhookRequest:
    """A provider's HTTP callback, reduced to what an adapter needs to authenticate and read it."""

    body: bytes
    headers: Mapping[str, str]  # keys lower-cased
    query: Mapping[str, str]


class TelephonyAdapter(Protocol):
    name: str

    async def place_call(self, request: PlaceCall) -> ProviderCall:
        """Ring the destination. Raises TelephonyError if the provider refuses or cannot be reached."""
        ...

    async def hang_up(self, call: ProviderCall) -> None:
        """End a call. Raises TelephonyError if the provider cannot do it."""
        ...

    def parse_event(self, request: WebhookRequest) -> CallEvent | None:
        """Authenticate a provider callback and read it. Raises InvalidWebhook if it is not genuine; returns
        None for a genuine callback that carries nothing the application uses."""
        ...
