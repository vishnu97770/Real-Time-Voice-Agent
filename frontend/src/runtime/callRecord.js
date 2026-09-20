// Turns what the console knows (the agent configuration, the call state and the
// finished call's result) into the "call record" the Home page shows: a
// summary in the centre and phone-call style details on the right. Nothing here
// is specific to a domain: every label comes from the configuration or the call.

import { displayName } from "./format.js";
import { label, toneOf } from "./results.js";

const JOB_TITLE = /\s+(assistant|agent|executive|representative|specialist|associate|advisor|bot)$/i;
const COVERED = "Covered: ";
const NO_QUESTIONS = "No questions were asked";
const EXECUTED = "Executed after confirmation";

const LIVE_STATES = ["connecting", "listening", "processing", "speaking"];

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

// "Patient Support Assistant" -> "Patient Support Conversation". Falls back to the
// industry, then the built-in profile, so an unconfigured agent still has a name.
export function conversationTitle({ role, industry, fallback } = {}) {
  const base = (role || industry || fallback || "").replace(JOB_TITLE, "").trim();

  return base ? `${base} Conversation` : "Voice Conversation";
}

// Who the agent is, from the saved configuration when there is one and from the
// built-in profile otherwise (calls still work before anything is configured).
export function describeAgent(config, profile) {
  return {
    configured: Boolean(config),
    agentName: config?.agentName || profile.name,
    role: config?.role || profile.name,
    industry: config?.industry || "",
    purpose: config?.purpose || profile.description || "",
  };
}

// idle | live | summarizing | completed | unavailable
export function callStatus(callState, summary, summaryPending) {
  if (LIVE_STATES.includes(callState)) return "live";
  if (callState !== "ended") return "idle";
  if (summary) return "completed";

  return summaryPending ? "summarizing" : "unavailable";
}

// Which step of Configure -> Start -> Complete -> Review the person is on.
export function flowStep(status, configured) {
  if (status === "idle") return configured ? 1 : 0;
  if (status === "live") return 2;

  return status === "summarizing" ? 2 : 3;
}

function topicsOf(summary) {
  return (summary?.keyPoints ?? [])
    .filter((point) => point.startsWith(COVERED))
    .map((point) => point.slice(COVERED.length));
}

// Key points that are neither a topic nor the "nothing asked" filler, such as
// how many attempts the guardrails blocked.
function flagsOf(summary) {
  return (summary?.keyPoints ?? []).filter((point) => !point.startsWith(COVERED) && point !== NO_QUESTIONS);
}

function buildNotes({ summary, topics, contact, agentName }) {
  if (!summary) return "";

  const executed = summary.actionsTaken.filter((action) => action.startsWith(EXECUTED)).length;
  const notes = [];

  if (topics.length) {
    notes.push(`${contact} asked about ${topics.join(", ").toLowerCase()}.`);
    notes.push(
      executed
        ? `${agentName} carried out ${plural(executed, "confirmed action")}.`
        : `${agentName} provided the requested information.`
    );
  } else {
    notes.push(`${contact} did not ask anything before the call ended.`);
  }

  flagsOf(summary).forEach((flag) => notes.push(`${flag}.`));

  return notes.join(" ");
}

function outcomeOf(summary, agentName) {
  if (!summary) return null;

  const ok = toneOf(summary.outcome) === "ok";

  return {
    ok,
    headline: ok ? "Call completed successfully" : `Call ended: ${label(summary.outcome)}`,
    detail: ok
      ? `The conversation was handled by ${agentName}.`
      : `${agentName} could not fully complete this call.`,
  };
}

export function buildCallRecord({
  config,
  profile,
  user,
  callState,
  summary = null,
  summaryPending = false,
  duration = 0,
  startedAt = null,
  callError = null,
}) {
  const agent = describeAgent(config, profile);
  const status = callStatus(callState, summary, summaryPending);
  const contact = displayName(user);
  const topics = topicsOf(summary);

  return {
    status,
    agent,
    contact,
    title: conversationTitle({ role: agent.role, industry: agent.industry, fallback: profile.name }),
    callId: summary?.callId ?? null,
    startedAt: summary?.startedAt ?? startedAt,
    durationSeconds: summary ? summary.durationSeconds : status === "idle" ? null : duration,
    summaryText: summary?.summary ?? "",
    topics,
    actions: summary?.actionsTaken ?? [],
    nextSteps: summary?.nextSteps ?? [],
    evidence: summary?.evidence ?? [],
    transcript: summary?.transcript ?? [],
    audit: summary?.audit ?? [],
    notes: buildNotes({ summary, topics, contact, agentName: agent.agentName }),
    outcome: outcomeOf(summary, agent.agentName),
    error: status === "unavailable" ? callError : null,
  };
}
