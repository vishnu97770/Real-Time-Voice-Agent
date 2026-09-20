// The "brain" turns one user utterance into a decision. It is the only part of
// the runtime that will be swapped for an LLM: the session (consent gate,
// guardrails, audit) sits above it and does not care how the decision was made.
//
// respond({ profile, data, text }) resolves to one of:
//   { kind: "answer",   topic, toolCalls, reply, ref }   grounded answer from tools
//   { kind: "propose",  topic, tool, args }              wants to run a guarded action
//   { kind: "chat",     reply }                          small talk, no data involved
//   { kind: "fallback", reply }                          not understood
//
// A brain never executes a guarded action itself. It can only propose one; the
// session asks the caller for confirmation before anything changes.

const COMMON_INTENTS = [
  {
    match: /\b(are you|r u)\b.*\b(human|real|robot|bot|ai|machine|person)\b/i,
    reply: () =>
      "I'm an AI assistant, not a human. I only tell you what I can look up, and I always ask before I change anything.",
  },
  {
    match: /\bwhat (?:can|do) you (?:do|help)|\bhow can you help\b|\bwhat can i ask\b/i,
    reply: (profile) =>
      `I can help you ${joinList(profile.capabilities)}. What would you like to do?`,
  },
  {
    match: /\b(thanks|thank you|bye|goodbye|that's all)\b/i,
    reply: () =>
      "You're welcome. If that's everything, you can end the call whenever you like.",
  },
  {
    match: /^(?:hi|hello|hey|good (?:morning|afternoon|evening))\b/i,
    reply: (profile) =>
      `Hello again. I can help you ${joinList(profile.capabilities)}.`,
  },
];

export function joinList(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`;
}

export const localBrain = {
  name: "local-rules",

  async respond({ profile, data, text }) {
    for (const intent of profile.intents) {
      if (!intent.match.test(text)) continue;

      if (intent.propose) {
        const proposal = intent.propose(data, text);

        if (proposal.reply) return { kind: "chat", reply: proposal.reply };

        return {
          kind: "propose",
          topic: intent.topic,
          tool: proposal.tool,
          args: proposal.args,
        };
      }

      const output = intent.run(data, text);

      return {
        kind: "answer",
        topic: intent.topic,
        toolCalls: [{ name: intent.tool, args: output.args, result: output.result }],
        reply: output.reply,
        ref: output.ref,
      };
    }

    for (const intent of COMMON_INTENTS) {
      if (intent.match.test(text)) {
        return { kind: "chat", reply: intent.reply(profile) };
      }
    }

    return {
      kind: "fallback",
      reply: `I'm not sure I caught that. I can help you ${joinList(profile.capabilities)}.`,
    };
  },
};
