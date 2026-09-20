// Builds the "call result" for a finished call: outcome, transcript and audit
// trail. This is the shape a business workflow would receive back from an
// outbound call job.

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

export function buildSummary(session, durationSeconds, transcript = []) {
  const { profile } = session;
  const userTurns = session.audit.filter((event) => event.type === "user_turn").length;
  const toolCalls = session.audit.filter((event) => event.type === "tool_call").length;

  const sentences = [];

  if (session.topics.length) {
    sentences.push(`Discussed ${session.topics.join(", ").toLowerCase()} with the ${profile.counterparty}.`);
  } else {
    sentences.push(`The call ended without any ${profile.counterparty} requests being handled.`);
  }

  session.executed.forEach((item) => sentences.push(`${item.summary}.`));
  session.declined.forEach((item) => sentences.push(`The ${profile.counterparty} declined: ${item.label.toLowerCase()}.`));
  session.lapsed.forEach((item) => sentences.push(`Not confirmed, so not carried out: ${item.label.toLowerCase()}.`));

  const keyPoints = session.topics.length
    ? session.topics.map((topic) => `Covered: ${topic}`)
    : ["No questions were asked"];

  if (session.blockedCount) {
    keyPoints.push(`${plural(session.blockedCount, "sensitive-data attempt")} blocked`);
  }

  const actionsTaken = [
    "AI disclosure given at start of call",
    ...session.executed.map((item) => `Executed after confirmation: ${item.label}`),
    ...session.declined.map((item) => `Declined by caller: ${item.label}`),
    ...session.lapsed.map((item) => `Not confirmed: ${item.label}`),
  ];

  return {
    callId: session.id,
    startedAt: session.startedAt,
    durationSeconds,
    profileId: profile.id,
    profileName: profile.name,
    type: `Live workspace session (AI ↔ ${profile.counterparty})`,
    outcome: session.executed.length ? "action_completed" : "completed",
    summary: sentences.join(" "),
    keyPoints,
    actionsTaken,
    nextSteps: profile.nextSteps,
    evidence: [
      ...session.refs,
      `Call transcript (${plural(userTurns, "caller turn")})`,
      `Agent tool logs (${plural(toolCalls, "call")})`,
    ],
    transcript,
    audit: session.audit,
  };
}
