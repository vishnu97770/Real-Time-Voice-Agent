// The server's call result (snake_case) as the summary card wants it.
export function mapResult(result) {
  return {
    callId: result.call_id,
    startedAt: Date.parse(result.started_at),
    durationSeconds: result.duration_seconds,
    profileId: result.profile_id,
    profileName: result.profile_name,
    type: result.type,
    outcome: result.outcome,
    summary: result.summary,
    keyPoints: result.key_points ?? [],
    actionsTaken: result.actions_taken ?? [],
    nextSteps: result.next_steps ?? [],
    evidence: result.evidence ?? [],
    transcript: result.transcript ?? [],
    audit: result.audit ?? [],
  };
}

const GOOD = ["completed", "action_completed"];
const IN_FLIGHT = ["ringing", "in_progress"];

// How an outcome or job status should look: ok | info | warn
export function toneOf(status) {
  if (GOOD.includes(status)) return "ok";
  if (IN_FLIGHT.includes(status)) return "info";
  return "warn";
}

export function label(status) {
  return status.replace(/_/g, " ");
}
