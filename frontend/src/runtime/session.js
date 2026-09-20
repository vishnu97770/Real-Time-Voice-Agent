// One call = one session. The session owns everything that must hold no matter
// which profile or brain is loaded: guardrails, the consent gate for guarded
// actions, and the audit log. It contains no UI and no audio code.

import {
  classifyConfirmation,
  detectSensitive,
  redactSensitive,
  sanitizeAgentText,
  SENSITIVE_REFUSAL,
} from "./guardrails.js";
import { localBrain } from "./brain.js";
import { buildSummary } from "./summary.js";

function pad(value) {
  return String(value).padStart(2, "0");
}

function makeCallId(date) {
  const day = `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}`;
  const time = `${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;

  return `CALL-${day}-${time}`;
}

export function log(session, type, detail = {}) {
  session.audit.push({ at: new Date().toISOString(), type, ...detail });
}

export function createSession(profile, brain = localBrain) {
  const startedAt = Date.now();

  const session = {
    id: makeCallId(new Date(startedAt)),
    profile,
    brain,
    startedAt,
    // The profile's mock data is copied so guarded actions can change it
    // without touching the definition, and a new call always starts clean.
    data: structuredClone(profile.data),
    pending: null,
    audit: [],
    topics: [],
    refs: [],
    executed: [],
    declined: [],
    blockedCount: 0,
    lapsed: [],
  };

  log(session, "call_started", { profile: profile.id, brain: brain.name });

  return session;
}

// The first thing said on every call, on every profile.
export function openCall(session) {
  log(session, "ai_disclosed", { profile: session.profile.id });

  return session.profile.greeting;
}

function remember(list, value) {
  if (value && !list.includes(value)) list.push(value);
}

function describePending(session, pending) {
  return session.profile.actions[pending.tool].describe(pending.args, session.data);
}

function confirmationPrompt(session, pending) {
  return `I can ${describePending(session, pending)}. Shall I go ahead? Please say yes to confirm, or no to cancel.`;
}

function finish(session, fields) {
  const { text, blocked } = sanitizeAgentText(fields.replyText);

  if (blocked) {
    session.blockedCount += 1;
    log(session, "guardrail_blocked", { direction: "agent_output" });
  }

  return {
    userText: fields.userText,
    replyText: text,
    toolCalls: fields.toolCalls ?? [],
    pending: session.pending
      ? { ...session.pending, label: describePending(session, session.pending) }
      : null,
    blocked: fields.blocked || blocked,
    kind: fields.kind,
  };
}

function execute(session) {
  const { tool, args } = session.pending;
  const action = session.profile.actions[tool];

  log(session, "action_confirmed", { tool, args });

  const output = action.execute(args, session.data);

  session.pending = null;
  session.executed.push({ tool, label: action.label, summary: output.summary });
  remember(session.refs, output.ref);

  log(session, "tool_call", { tool, args, guarded: true });
  log(session, "action_executed", { tool, summary: output.summary });

  return {
    replyText: output.reply,
    toolCalls: [{ name: tool, args, result: output.result, guarded: true }],
    kind: "action",
  };
}

function decline(session) {
  const { tool, args } = session.pending;

  log(session, "action_declined", { tool, args });
  session.declined.push({ tool, label: session.profile.actions[tool].label });
  session.pending = null;

  return {
    replyText: "No problem, I've cancelled that. Nothing was changed. Is there anything else I can help with?",
    kind: "declined",
  };
}

export async function processTurn(session, rawText) {
  const userText = redactSensitive(rawText);

  if (detectSensitive(rawText)) {
    session.blockedCount += 1;
    log(session, "guardrail_blocked", { direction: "user_input" });
    log(session, "user_turn", { text: userText });

    return finish(session, {
      userText,
      replyText: SENSITIVE_REFUSAL,
      blocked: true,
      kind: "blocked",
    });
  }

  log(session, "user_turn", { text: userText });

  let decision = null;

  if (session.pending) {
    const answer = classifyConfirmation(rawText);

    if (answer === "yes") return finish(session, { userText, ...execute(session) });
    if (answer === "no") return finish(session, { userText, ...decline(session) });

    // Neither yes nor no. Consent is never assumed: if the caller has moved on
    // to a real request, the old one lapses unconfirmed; otherwise ask again.
    decision = await session.brain.respond({
      profile: session.profile,
      data: session.data,
      text: rawText,
    });

    if (decision.kind === "fallback") {
      return finish(session, {
        userText,
        replyText: `I still need a yes or no before I can go on. ${confirmationPrompt(session, session.pending)}`,
        kind: "reprompt",
      });
    }

    log(session, "action_lapsed", { tool: session.pending.tool });
    session.lapsed.push({
      tool: session.pending.tool,
      label: session.profile.actions[session.pending.tool].label,
    });
    session.pending = null;
  }

  decision ??= await session.brain.respond({
    profile: session.profile,
    data: session.data,
    text: rawText,
  });

  if (decision.kind === "answer") {
    remember(session.topics, decision.topic);
    remember(session.refs, decision.ref);

    for (const call of decision.toolCalls) {
      log(session, "tool_call", { tool: call.name, args: call.args });
    }

    return finish(session, {
      userText,
      replyText: decision.reply,
      toolCalls: decision.toolCalls,
      kind: "answer",
    });
  }

  if (decision.kind === "propose") {
    const action = session.profile.actions[decision.tool];

    // The brain may only propose actions that the profile actually defines.
    if (!action) {
      return finish(session, {
        userText,
        replyText: "I'm not able to do that on this call.",
        kind: "chat",
      });
    }

    remember(session.topics, decision.topic);
    session.pending = { tool: decision.tool, args: decision.args };
    log(session, "action_requested", { tool: decision.tool, args: decision.args });

    return finish(session, {
      userText,
      replyText: confirmationPrompt(session, session.pending),
      kind: "confirm",
    });
  }

  return finish(session, { userText, replyText: decision.reply, kind: decision.kind });
}

export function closeCall(session, durationSeconds, transcript) {
  if (session.pending) {
    log(session, "action_lapsed", { tool: session.pending.tool, reason: "call_ended" });
    session.lapsed.push({
      tool: session.pending.tool,
      label: session.profile.actions[session.pending.tool].label,
    });
    session.pending = null;
  }

  log(session, "call_ended", { duration_seconds: durationSeconds });

  return buildSummary(session, durationSeconds, transcript);
}
