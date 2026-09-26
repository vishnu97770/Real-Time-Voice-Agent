"""One call = one session.

The session owns everything that must hold no matter which profile or brain is
loaded: guardrails, the consent gate for guarded actions, and the audit log. It
knows nothing about HTTP or audio.

process_turn() is an async generator of events, so the reply can be spoken while
it is still being written:

    {"type": "tool_call", "name", "args", "result", "guarded"}
    {"type": "sentence",  "text"}              one speakable, already-vetted sentence
    {"type": "done",      "kind", "pending", "blocked", "reply_text"}
"""

import asyncio
import copy
import re
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.brains.base import Brain, BrainContext, OutboundBrief, Propose, TextDelta, ToolCall, Turn
from app.guardrails import (
    AGENT_SOLICITATION_REPLACEMENT,
    AI_DISCLOSURE,
    SENSITIVE_REFUSAL,
    classify_confirmation,
    classify_identity,
    detect_sensitive,
    is_filler,
    redact_sensitive,
    solicits_secret,
)
from app.profiles.base import ActionSpec, Profile, ToolSpec
from app.summary import build_summary
from app.voice_context import (
    CONTEXT_TOOL,
    VoiceSessionContext,
    domain_objective,
    domain_persona,
    session_data,
)

BRAIN_ERROR_REPLY = "I'm having trouble on my side right now. Could you say that again?"
EMPTY_REPLY = "I'm sorry, I didn't catch that. Could you say it another way?"

# The person who answered is confirmed by a deterministic check, not by the LLM.
# This is what the brain is told once they have.
OPENING_PROMPT = (
    "[The person has confirmed who they are. Begin the call now: briefly say why you are "
    "calling, using your tools to get the specifics.]"
)
WRONG_PARTY_REPLY = "I'm sorry to have disturbed you. I won't take any more of your time. Goodbye."
IDENTITY_FAILED_REPLY = (
    "I'm sorry, I can't continue without confirming who I'm speaking with. Goodbye."
)
TIME_LIMIT_REPLY = "I'm afraid I'm out of time for this call. Thank you, and goodbye."
IDENTITY_ATTEMPTS = 2

_BOUNDARY = re.compile(r"[.!?]+[\"')\]]*\s+")

Event = dict[str, Any]


@dataclass
class TranscriptEntry:
    id: int
    speaker: str
    text: str
    elapsed: int
    interrupted: bool = False
    blocked: bool = False


@dataclass
class Pending:
    tool: str
    args: dict[str, Any]


@dataclass
class OutboundState:
    """An outbound call: the agent placed it, and the callee has not yet been
    confirmed to be who we asked for."""

    job_id: str
    callee_name: str
    reason: str
    organisation: str
    max_duration_seconds: int
    identity: str = "unconfirmed"  # unconfirmed | confirmed | wrong_party | failed
    attempts: int = 0


@dataclass
class Session:
    id: str
    profile: Profile
    brain: Brain
    brain_timeout: float
    started_at: float = field(default_factory=time.time)
    # The profile's mock data is copied so guarded actions can change it
    # without touching the definition, and a new call always starts clean.
    data: dict[str, Any] = field(default_factory=dict)
    pending: Pending | None = None
    audit: list[dict[str, Any]] = field(default_factory=list)
    history: list[Turn] = field(default_factory=list)
    transcript: list[TranscriptEntry] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    executed: list[dict[str, str]] = field(default_factory=list)
    declined: list[dict[str, str]] = field(default_factory=list)
    lapsed: list[dict[str, str]] = field(default_factory=list)
    blocked_count: int = 0
    # Whose data this call is about, if it came from the customers table. A
    # confirmed action is written back through `persist`.
    customer_ref: str | None = None
    customer_name: str | None = None
    persist: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    outbound: OutboundState | None = None
    # Set on an automated call made for an organization's agent and contact (see app/voice_context.py).
    context: VoiceSessionContext | None = None
    channel: str = "web"  # "web" | "phone"
    # The organization a console call was opened for (a self-service account's own), so its result
    # is theirs. None for everything else: job calls take theirs from the job.
    organization_id: int | None = None
    # Hangs up the real phone line. Set only on phone calls; called once, by finalize().
    hangup: Callable[[], Awaitable[None]] | None = None
    allowed_tools: dict[str, ToolSpec] = field(default_factory=dict)
    allowed_actions: dict[str, ActionSpec] = field(default_factory=dict)
    # Set when the agent itself ends the call (wrong party, time limit).
    should_end: bool = False
    end_reason: str = "client"
    saved: bool = False
    ended: bool = False
    duration: int | None = None
    last_active: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def log(self, type_: str, **detail: Any) -> None:
        self.audit.append({"at": datetime.now(timezone.utc).isoformat(), "type": type_, **detail})

    def elapsed(self) -> int:
        return int(time.time() - self.started_at)

    def add_entry(self, speaker: str, text: str, **flags: bool) -> TranscriptEntry:
        entry = TranscriptEntry(len(self.transcript) + 1, speaker, text, self.elapsed(), **flags)
        self.transcript.append(entry)
        return entry

    def remember(self, values: list[str], value: str | None) -> None:
        if value and value not in values:
            values.append(value)


def make_call_id() -> str:
    # The id is also the only credential for the call's endpoints, so it carries
    # real randomness rather than just a timestamp.
    return f"CALL-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(8)}"


def create_session(
    profile: Profile,
    brain: Brain,
    brain_timeout: float = 30.0,
    outbound: OutboundState | None = None,
    data: dict[str, Any] | None = None,
    customer_ref: str | None = None,
    customer_name: str | None = None,
    context: VoiceSessionContext | None = None,
) -> Session:
    if context is not None and (outbound is None or data is not None or customer_ref is not None):
        raise ValueError("a domain context belongs to an outbound call and replaces customer data")

    session = Session(
        id=make_call_id(),
        profile=profile,
        brain=brain,
        brain_timeout=brain_timeout,
        # A domain call keeps its own data, never the profile's demo data.
        data=copy.deepcopy(session_data(context) if context is not None else profile.data if data is None else data),
        outbound=outbound,
        customer_ref=customer_ref,
        customer_name=customer_name,
        context=context,
    )

    if outbound is None:
        session.allowed_tools = dict(profile.tools)
        session.allowed_actions = dict(profile.actions)
        session.log("call_started", profile=profile.id, brain=brain.name)
    else:
        config = profile.outbound

        if config is None:
            raise ValueError(f"Profile {profile.id} does not support outbound calls")

        if context is not None:
            # The profile's tools and actions read and change its demo data, which this call does not have.
            # The one tool is a read of the call's own context; there is nothing to change.
            session.allowed_tools = {CONTEXT_TOOL.name: CONTEXT_TOOL}
            session.allowed_actions = {}
        else:
            names = config.tools if config.tools is not None else list(profile.tools)
            session.allowed_tools = {name: profile.tools[name] for name in names}
            session.allowed_actions = {name: profile.actions[name] for name in config.actions}

        session.log(
            "call_started",
            profile=profile.id,
            brain=brain.name,
            direction="outbound",
            job_id=outbound.job_id,
            **({k: v for k, v in context.ids().items() if k != "job_id"} if context is not None else {}),
        )

    return session


def opening_line(session: Session) -> str:
    """The first thing said on every call, on every profile."""
    outbound = session.outbound

    if outbound is None:
        return session.profile.personalise(session.profile.greeting, session.customer_name)

    # An outbound call opens with who is calling and asks who answered. Nothing
    # about the callee is said until they confirm.
    return (
        f"{AI_DISCLOSURE} I'm calling from {outbound.organisation}. "
        f"Am I speaking with {outbound.callee_name}?"
    )


def open_call(session: Session) -> str:
    greeting = opening_line(session)

    session.log("ai_disclosed", profile=session.profile.id)
    session.add_entry("Agent", greeting)
    session.history.append(Turn("agent", greeting))
    return greeting


class SentenceSplitter:
    """Turns a stream of text deltas into whole sentences.

    A full stop only ends a sentence when whitespace follows, so "0.28" and
    "₹84,250.75" stay in one piece.
    """

    def __init__(self) -> None:
        self.buffer = ""

    def feed(self, text: str) -> list[str]:
        self.buffer += text
        sentences = []

        while match := _BOUNDARY.search(self.buffer):
            sentences.append(self.buffer[: match.end()].strip())
            self.buffer = self.buffer[match.end() :]

        return sentences

    def flush(self) -> list[str]:
        rest, self.buffer = self.buffer.strip(), ""
        return [rest] if rest else []


def split_sentences(text: str) -> list[str]:
    splitter = SentenceSplitter()
    return [*splitter.feed(text + " "), *splitter.flush()]


class _Turn:
    """Bookkeeping for the reply currently being written."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.entry: TranscriptEntry | None = None
        self.tool_calls: list[dict[str, Any]] = []
        self.blocked = False
        self.replaced = False
        self.completed = False

    def vet(self, sentence: str) -> str | None:
        """Last line of defence on agent output, for any brain."""
        if not solicits_secret(sentence):
            return sentence

        self.session.blocked_count += 1
        self.session.log("guardrail_blocked", direction="agent_output")
        self.blocked = True

        if self.replaced:
            return None

        self.replaced = True
        return AGENT_SOLICITATION_REPLACEMENT

    def say(self, sentence: str) -> Event | None:
        vetted = self.vet(sentence)

        if vetted is None:
            return None

        if self.entry is None:
            self.entry = self.session.add_entry("Agent", vetted, blocked=self.blocked)
        else:
            self.entry.text = f"{self.entry.text} {vetted}"

        return {"type": "sentence", "text": vetted}

    def finish(self) -> None:
        """Runs whether the reply completed or the caller cut it off."""
        if self.entry is None:
            return

        if self.entry.blocked is False and self.blocked:
            self.entry.blocked = True

        self.session.history.append(Turn("agent", self.entry.text))

        if not self.completed:
            self.entry.interrupted = True
            self.session.log("turn_aborted", message=self.entry.id)

    def done(self, kind: str, blocked: bool = False) -> Event:
        self.completed = True
        pending = self.session.pending
        text = self.entry.text if self.entry else ""

        return {
            "type": "done",
            "kind": kind,
            "blocked": blocked or self.blocked,
            "ended": self.session.should_end,
            "reply_text": text,
            "pending": (
                {
                    "tool": pending.tool,
                    "args": pending.args,
                    "label": _describe_pending(self.session, pending),
                }
                if pending
                else None
            ),
        }


def _describe_pending(session: Session, pending: Pending) -> str:
    action = session.profile.actions[pending.tool]
    return action.describe(pending.args, session.data)


def _confirmation_prompt(session: Session, pending: Pending) -> str:
    return (
        f"I can {_describe_pending(session, pending)}. Shall I go ahead? "
        "Please say yes to confirm, or no to cancel."
    )


async def _run_tool(session: Session, name: str, args: dict[str, Any]) -> Any:
    spec = session.allowed_tools.get(name)

    if spec is None:
        session.log("tool_error", tool=name, reason="unknown_tool")
        return {"error": f"There is no tool called {name}."}

    if session.outbound and session.outbound.identity != "confirmed":
        session.log("tool_error", tool=name, reason="identity_not_confirmed")
        return {"error": "The person has not confirmed who they are."}

    args = dict(args or {})

    # Outbound: some arguments are fixed by the profile, whatever the model asked.
    if session.outbound:
        fixed = session.profile.outbound.fixed_args.get(name, {})

        for key, value in fixed.items():
            args[key] = value(session.data) if callable(value) else value

    try:
        result = spec.run(session.data, args)
        ref = spec.ref(session.data, args)
    except Exception as error:  # a broken tool must not end the call
        session.log("tool_error", tool=name, reason=type(error).__name__)
        return {"error": "That lookup failed."}

    session.remember(session.topics, spec.topic)
    session.remember(session.refs, ref)
    session.log("tool_call", tool=name, args=args)

    return result


async def _say(turn: _Turn, text: str) -> AsyncIterator[Event]:
    for sentence in split_sentences(text):
        event = turn.say(sentence)

        if event:
            yield event


async def _execute_pending(session: Session, turn: _Turn) -> AsyncIterator[Event]:
    pending = session.pending
    action = session.profile.actions[pending.tool]

    session.log("action_confirmed", tool=pending.tool, args=pending.args)

    snapshot = copy.deepcopy(session.data)
    output = action.execute(pending.args, session.data)

    if session.persist is not None:
        try:
            await session.persist(session.data)
        except Exception as error:
            # The change must exist in the customer's record or not at all.
            session.data.clear()
            session.data.update(snapshot)
            session.pending = None
            session.log("action_failed", tool=pending.tool, reason=type(error).__name__)

            async for event in _say(
                turn, "I'm sorry, I couldn't complete that just now, so nothing was changed."
            ):
                yield event
            yield turn.done("error")
            return

    session.pending = None
    session.executed.append({"tool": pending.tool, "label": action.label, "summary": output.summary})
    session.remember(session.topics, action.topic)
    session.remember(session.refs, output.ref)
    session.log("tool_call", tool=pending.tool, args=pending.args, guarded=True)
    session.log("action_executed", tool=pending.tool, summary=output.summary)

    yield {
        "type": "tool_call",
        "name": pending.tool,
        "args": pending.args,
        "result": output.result,
        "guarded": True,
    }
    async for event in _say(turn, output.reply):
        yield event
    yield turn.done("action")


async def _decline_pending(session: Session, turn: _Turn) -> AsyncIterator[Event]:
    pending = session.pending
    action = session.profile.actions[pending.tool]

    session.log("action_declined", tool=pending.tool, args=pending.args)
    session.declined.append({"tool": pending.tool, "label": action.label})
    session.pending = None

    async for event in _say(
        turn,
        "No problem, I've cancelled that. Nothing was changed. "
        "Is there anything else I can help with?",
    ):
        yield event
    yield turn.done("declined")


def _lapse_pending(session: Session, reason: str | None = None) -> None:
    pending = session.pending

    if pending is None:
        return

    action = session.profile.actions[pending.tool]
    detail = {"reason": reason} if reason else {}

    session.log("action_lapsed", tool=pending.tool, **detail)
    session.lapsed.append({"tool": pending.tool, "label": action.label})
    session.pending = None


async def process_turn(session: Session, raw_text: str) -> AsyncIterator[Event]:
    """Handle one caller utterance. Turns are serialized per session."""
    async with session.lock:
        session.last_active = time.time()

        if session.ended:
            return

        user_text = redact_sensitive(raw_text)
        session.add_entry("You", user_text)
        session.history.append(Turn("user", user_text))
        turn = _Turn(session)

        try:
            if _over_time(session):
                async for event in _end_call(session, turn, TIME_LIMIT_REPLY, "time_limit"):
                    yield event
            else:
                async for event in _handle(session, turn, raw_text):
                    yield event
        finally:
            turn.finish()


async def _handle(session: Session, turn: _Turn, raw_text: str) -> AsyncIterator[Event]:
    if detect_sensitive(raw_text):
        session.blocked_count += 1
        session.log("guardrail_blocked", direction="user_input")
        session.log("user_turn", text=redact_sensitive(raw_text))
        turn.blocked = True

        async for event in _say(turn, SENSITIVE_REFUSAL):
            yield event
        yield turn.done("blocked", blocked=True)
        return

    session.log("user_turn", text=raw_text)

    if session.outbound and session.outbound.identity == "unconfirmed":
        async for event in _confirm_identity(session, turn, raw_text):
            yield event
        return

    if session.pending:
        answer = classify_confirmation(raw_text)

        if answer == "yes":
            async for event in _execute_pending(session, turn):
                yield event
            return

        if answer == "no":
            async for event in _decline_pending(session, turn):
                yield event
            return

        # Neither yes nor no. Consent is never assumed. A hesitation gets the
        # question again; a real new request lets the old one lapse unconfirmed.
        if is_filler(raw_text):
            reprompt = (
                "I still need a yes or no before I can go on. "
                + _confirmation_prompt(session, session.pending)
            )
            async for event in _say(turn, reprompt):
                yield event
            yield turn.done("reprompt")
            return

        _lapse_pending(session)

    async for event in _ask_brain(session, turn, raw_text):
        yield event


def _over_time(session: Session) -> bool:
    outbound = session.outbound
    return bool(outbound and session.elapsed() >= outbound.max_duration_seconds)


async def _end_call(
    session: Session, turn: _Turn, reply: str, reason: str, kind: str | None = None
) -> AsyncIterator[Event]:
    """The agent ends the call itself: say goodbye and tell the client to hang up."""
    session.should_end = True
    session.end_reason = reason

    async for event in _say(turn, reply):
        yield event
    yield turn.done(kind or reason)


async def _confirm_identity(session: Session, turn: _Turn, raw_text: str) -> AsyncIterator[Event]:
    state = session.outbound
    answer = classify_identity(raw_text)

    if answer == "yes":
        state.identity = "confirmed"
        session.log("callee_identity_confirmed")

        async for event in _ask_brain(session, turn, OPENING_PROMPT):
            yield event
        return

    if answer == "no":
        state.identity = "wrong_party"
        session.log("wrong_party")

        async for event in _end_call(session, turn, WRONG_PARTY_REPLY, "wrong_party"):
            yield event
        return

    state.attempts += 1

    if state.attempts >= IDENTITY_ATTEMPTS:
        state.identity = "failed"
        session.log("identity_not_confirmed")

        async for event in _end_call(session, turn, IDENTITY_FAILED_REPLY, "identity_failed"):
            yield event
        return

    async for event in _say(
        turn,
        f"I'm an AI assistant calling from {state.organisation} for {state.callee_name}. "
        f"Am I speaking with {state.callee_name}?",
    ):
        yield event
    yield turn.done("reprompt")


async def _ask_brain(session: Session, turn: _Turn, raw_text: str) -> AsyncIterator[Event]:
    domain = session.context
    context = BrainContext(
        profile=session.profile,
        data=session.data,
        history=session.history[:-1],  # everything before this utterance
        text=raw_text,
        run_tool=lambda name, args: _run_tool(session, name, args),
        tools=session.allowed_tools,
        actions=session.allowed_actions,
        persona=domain_persona(domain) if domain else session.profile.personalise(session.profile.persona, session.customer_name),
        outbound=(
            OutboundBrief(
                callee_name=session.outbound.callee_name,
                reason=session.outbound.reason,
                organisation=session.outbound.organisation,
                persona=domain_persona(domain) if domain else session.profile.outbound.persona,
                objective=domain_objective(domain) if domain else session.profile.outbound.objective,
            )
            if session.outbound
            else None
        ),
        domain=domain,
    )
    splitter = SentenceSplitter()
    proposal: Propose | None = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + session.brain_timeout

    try:
        async with aclosing(session.brain.respond(context)) as stream:
            iterator = aiter(stream)

            while True:
                # The deadline covers only time spent waiting on the brain, never
                # time the consumer spends with an event.
                try:
                    item = await asyncio.wait_for(anext(iterator), max(deadline - loop.time(), 0.001))
                except StopAsyncIteration:
                    break

                if isinstance(item, ToolCall):
                    call = {
                        "type": "tool_call",
                        "name": item.name,
                        "args": item.args,
                        "result": item.result,
                        "guarded": False,
                    }
                    turn.tool_calls.append(call)
                    yield call
                elif isinstance(item, TextDelta):
                    for sentence in splitter.feed(item.text):
                        event = turn.say(sentence)
                        if event:
                            yield event
                elif isinstance(item, Propose):
                    proposal = item
                    break
    except Exception as error:  # includes the timeout; the call carries on
        session.log("brain_error", reason=type(error).__name__)
        async for event in _say(turn, BRAIN_ERROR_REPLY):
            yield event
        yield turn.done("error")
        return

    for sentence in splitter.flush():
        event = turn.say(sentence)
        if event:
            yield event

    if proposal is not None:
        async for event in _propose(session, turn, proposal):
            yield event
        return

    if turn.entry is None:
        async for event in _say(turn, EMPTY_REPLY):
            yield event
        yield turn.done("fallback")
        return

    yield turn.done("answer" if turn.tool_calls else "chat")


async def _propose(session: Session, turn: _Turn, proposal: Propose) -> AsyncIterator[Event]:
    # The brain may only propose actions the profile actually defines.
    action = session.allowed_actions.get(proposal.tool)

    if action is None:
        session.log("tool_error", tool=proposal.tool, reason="unknown_action")
        async for event in _say(turn, "I'm not able to do that on this call."):
            yield event
        yield turn.done("chat")
        return

    args = dict(proposal.args or {})
    problem = action.validate(session.data, args)

    if problem:
        async for event in _say(turn, problem):
            yield event
        yield turn.done("chat")
        return

    pending = Pending(proposal.tool, args)
    session.remember(session.topics, action.topic)
    session.log("action_requested", tool=proposal.tool, args=args)

    async for event in _say(turn, _confirmation_prompt(session, pending)):
        yield event

    # Consent is only ever asked for, and only recorded as pending, once the
    # whole question has been delivered to the caller.
    session.pending = pending
    yield turn.done("confirm")


def mark_playback_interrupted(session: Session) -> None:
    """The caller talked over the agent. Called by the client, which is the only
    side that can hear it."""
    agent_entries = [entry for entry in session.transcript if entry.speaker == "Agent"]

    if agent_entries:
        agent_entries[-1].interrupted = True

    session.log("playback_interrupted", message=agent_entries[-1].id if agent_entries else None)


def close_session(session: Session) -> dict[str, Any]:
    if not session.ended:
        _lapse_pending(session, reason="call_ended")
        session.ended = True
        session.duration = session.elapsed()
        session.log("call_ended", duration_seconds=session.duration)

    return build_summary(session, session.duration or 0)
