import assert from "node:assert/strict";
import test from "node:test";
import { buildCallRecord, callStatus, conversationTitle, describeAgent, flowStep } from "./callRecord.js";
import { displayName } from "./format.js";

const profile = { name: "Credit Underwriting", description: "Reviews loan files." };

const config = (industry, role, agentName = "Agent") => ({ industry, role, agentName, purpose: "Help." });

const summary = {
  callId: "CALL-20260920-104406",
  startedAt: Date.parse("2026-09-20T10:44:00Z"),
  durationSeconds: 272,
  outcome: "completed",
  summary: "Discussed production.",
  keyPoints: ["Covered: Monthly Production", "Covered: Site-wise Data", "1 guardrail block during the call"],
  actionsTaken: ["AI disclosure given at start of call"],
  nextSteps: ["Send report"],
  evidence: [],
  transcript: [],
  audit: [],
};

test("the title follows the configured role, whatever the domain", () => {
  assert.equal(conversationTitle({ role: "Patient Support Assistant" }), "Patient Support Conversation");
  assert.equal(conversationTitle({ role: "Customer Support Executive" }), "Customer Support Conversation");
  assert.equal(conversationTitle({ role: "Mining Operations Support" }), "Mining Operations Support Conversation");
  assert.equal(conversationTitle({ role: "Assistant" }), "Assistant Conversation");
});

test("the title falls back to the industry, then the profile, then a generic name", () => {
  assert.equal(conversationTitle({ industry: "Education" }), "Education Conversation");
  assert.equal(conversationTitle({ fallback: "Telecom Support" }), "Telecom Support Conversation");
  assert.equal(conversationTitle({}), "Voice Conversation");
});

test("an unconfigured agent is described by the built-in profile", () => {
  assert.deepEqual(describeAgent(null, profile), {
    configured: false,
    agentName: "Credit Underwriting",
    role: "Credit Underwriting",
    industry: "",
    purpose: "Reviews loan files.",
  });
  assert.equal(describeAgent(config("Mining", "Ops", "MineAssist"), profile).agentName, "MineAssist");
});

test("call status follows the call state and whether a summary exists", () => {
  assert.equal(callStatus("idle", null, false), "idle");
  assert.equal(callStatus("listening", null, false), "live");
  assert.equal(callStatus("processing", null, false), "live");
  assert.equal(callStatus("ended", null, true), "summarizing");
  assert.equal(callStatus("ended", null, false), "unavailable");
  assert.equal(callStatus("ended", summary, false), "completed");
});

test("the flow moves Configure -> Start -> Complete -> Review", () => {
  assert.equal(flowStep("idle", false), 0);
  assert.equal(flowStep("idle", true), 1);
  assert.equal(flowStep("live", true), 2);
  assert.equal(flowStep("summarizing", true), 2);
  assert.equal(flowStep("completed", true), 3);
});

test("the contact is the signed-in user, or Operator without a sign-in", () => {
  assert.equal(displayName({ email: "asha.rao@example.com" }), "asha.rao");
  assert.equal(displayName(null), "Operator");
  assert.equal(displayName({ email: "" }), "Operator");
});

test("a finished call becomes a complete record", () => {
  const record = buildCallRecord({
    config: config("Healthcare", "Patient Support Assistant", "CareBot"),
    profile,
    user: { email: "nurse@example.com" },
    callState: "ended",
    summary,
  });

  assert.equal(record.status, "completed");
  assert.equal(record.title, "Patient Support Conversation");
  assert.equal(record.contact, "nurse");
  assert.equal(record.agent.role, "Patient Support Assistant");
  assert.equal(record.callId, "CALL-20260920-104406");
  assert.equal(record.durationSeconds, 272);
  assert.deepEqual(record.topics, ["Monthly Production", "Site-wise Data"]);
  assert.equal(record.outcome.ok, true);
  assert.equal(record.outcome.detail, "The conversation was handled by CareBot.");
  assert.match(record.notes, /nurse asked about monthly production, site-wise data\./);
  assert.match(record.notes, /CareBot provided the requested information\./);
  assert.match(record.notes, /1 guardrail block during the call\./);
});

test("confirmed actions are counted in the notes", () => {
  const record = buildCallRecord({
    config: null,
    profile,
    user: null,
    callState: "ended",
    summary: {
      ...summary,
      actionsTaken: ["AI disclosure given at start of call", "Executed after confirmation: Book visit"],
    },
  });

  assert.match(record.notes, /Operator asked about/);
  assert.match(record.notes, /carried out 1 confirmed action\./);
});

test("a call with no questions has no topics and says so", () => {
  const record = buildCallRecord({
    config: null,
    profile,
    user: null,
    callState: "ended",
    summary: { ...summary, keyPoints: ["No questions were asked"] },
  });

  assert.deepEqual(record.topics, []);
  assert.match(record.notes, /did not ask anything/);
});

test("a call that did not complete says what happened instead of succeeding", () => {
  const record = buildCallRecord({
    config: null,
    profile,
    user: null,
    callState: "ended",
    summary: { ...summary, outcome: "wrong_person" },
  });

  assert.equal(record.outcome.ok, false);
  assert.equal(record.outcome.headline, "Call ended: wrong person");
});

test("before a call there is nothing to summarise and no invented details", () => {
  const record = buildCallRecord({ config: null, profile, user: null, callState: "idle" });

  assert.equal(record.status, "idle");
  assert.equal(record.callId, null);
  assert.equal(record.durationSeconds, null);
  assert.equal(record.outcome, null);
  assert.equal(record.notes, "");
});

test("a live call shows its running duration and start time", () => {
  const record = buildCallRecord({ config: null, profile, user: null, callState: "listening", duration: 12, startedAt: 5 });

  assert.equal(record.status, "live");
  assert.equal(record.durationSeconds, 12);
  assert.equal(record.startedAt, 5);
});

test("a call that ended without a summary carries the connection error", () => {
  const record = buildCallRecord({ config: null, profile, user: null, callState: "ended", callError: "Could not connect." });

  assert.equal(record.status, "unavailable");
  assert.equal(record.error, "Could not connect.");
});
