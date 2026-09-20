"""Builds the call result for a finished call: outcome, transcript and audit
trail. This is the shape a business workflow receives back from a call job."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.session import Session


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def format_elapsed(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def build_summary(session: "Session", duration_seconds: int) -> dict[str, Any]:
    profile = session.profile
    user_turns = sum(1 for event in session.audit if event["type"] == "user_turn")
    tool_calls = sum(1 for event in session.audit if event["type"] == "tool_call")

    sentences = []

    if session.topics:
        sentences.append(
            f"Discussed {', '.join(session.topics).lower()} with the {profile.counterparty}."
        )
    else:
        sentences.append(
            f"The call ended without any {profile.counterparty} requests being handled."
        )

    sentences += [f"{item['summary']}." for item in session.executed]
    sentences += [
        f"The {profile.counterparty} declined: {item['label'].lower()}." for item in session.declined
    ]
    sentences += [
        f"Not confirmed, so not carried out: {item['label'].lower()}." for item in session.lapsed
    ]

    key_points = (
        [f"Covered: {topic}" for topic in session.topics]
        if session.topics
        else ["No questions were asked"]
    )

    if session.blocked_count:
        key_points.append(f"{_plural(session.blocked_count, 'guardrail block')} during the call")

    actions_taken = [
        "AI disclosure given at start of call",
        *[f"Executed after confirmation: {item['label']}" for item in session.executed],
        *[f"Declined by caller: {item['label']}" for item in session.declined],
        *[f"Not confirmed: {item['label']}" for item in session.lapsed],
    ]

    outbound = session.outbound
    outcome = "action_completed" if session.executed else "completed"

    if outbound and outbound.identity == "wrong_party":
        outcome = "wrong_party"
    elif outbound and outbound.identity != "confirmed":
        outcome = "identity_not_confirmed"

    return {
        "call_id": session.id,
        "started_at": datetime.fromtimestamp(session.started_at, timezone.utc).isoformat(),
        "duration_seconds": duration_seconds,
        "profile_id": profile.id,
        "profile_name": profile.name,
        "type": f"Live workspace session (AI ↔ {profile.counterparty})",
        "outcome": outcome,
        "end_reason": session.end_reason,
        "direction": "outbound" if outbound else "inbound",
        "channel": session.channel,
        "disclosure_given": any(event["type"] == "ai_disclosed" for event in session.audit),
        **(
            {
                "outbound": {
                    "job_id": outbound.job_id,
                    "callee_name": outbound.callee_name,
                    "reason": outbound.reason,
                    "identity": outbound.identity,
                }
            }
            if outbound
            else {}
        ),
        "summary": " ".join(sentences),
        "key_points": key_points,
        "actions_taken": actions_taken,
        "next_steps": profile.next_steps,
        "evidence": [
            *session.refs,
            f"Call transcript ({_plural(user_turns, 'caller turn')})",
            f"Agent tool logs ({_plural(tool_calls, 'call')})",
        ],
        "transcript": [
            {
                "id": entry.id,
                "speaker": entry.speaker,
                "text": entry.text,
                "time": format_elapsed(entry.elapsed),
                **({"interrupted": True} if entry.interrupted else {}),
                **({"blocked": True} if entry.blocked else {}),
            }
            for entry in session.transcript
        ],
        "audit": session.audit,
    }
