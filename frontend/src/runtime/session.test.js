// Headless tests for the runtime. Run with: npm test
import assert from "node:assert/strict";
import { test } from "node:test";

import { PROFILES, getProfile } from "../profiles/index.js";
import {
  classifyConfirmation,
  detectSensitive,
  redactSensitive,
  sanitizeAgentText,
} from "./guardrails.js";
import { closeCall, createSession, openCall, processTurn } from "./session.js";

const start = (id) => {
  const session = createSession(getProfile(id));

  openCall(session);

  return session;
};

test("every profile discloses AI and promises never to ask for secrets", () => {
  for (const profile of PROFILES) {
    assert.match(profile.greeting, /AI assistant/, profile.id);
    assert.match(profile.greeting, /never ask for your password, PIN/, profile.id);
  }
});

test("every profile answers each suggested prompt from its data, not the fallback", async () => {
  for (const profile of PROFILES) {
    for (const prompt of profile.prompts) {
      const session = start(profile.id);
      const result = await processTurn(session, prompt);

      assert.notEqual(result.kind, "fallback", `${profile.id}: "${prompt}"`);
      assert.ok(result.replyText.length > 10, `${profile.id}: "${prompt}"`);
    }
  }
});

test("every guarded action is defined and needs confirmation before it changes data", async () => {
  for (const profile of PROFILES) {
    for (const intent of profile.intents.filter((entry) => entry.propose)) {
      assert.ok(intent.id);
    }
    assert.ok(Object.keys(profile.actions).length >= 1, profile.id);
  }

  const session = start("bank");
  const proposal = await processTurn(session, "Freeze my credit card");

  assert.equal(proposal.kind, "confirm");
  assert.ok(proposal.pending);
  assert.equal(session.data.cards[1].status, "active", "nothing changes before consent");

  const done = await processTurn(session, "yes please");

  assert.equal(done.kind, "action");
  assert.equal(session.data.cards[1].status, "frozen");
  assert.equal(done.pending, null);
});

test("declining a guarded action changes nothing", async () => {
  const session = start("telecom");

  await processTurn(session, "Upgrade my plan");
  const result = await processTurn(session, "no, cancel");

  assert.equal(result.kind, "declined");
  assert.equal(session.data.plan, "Postpaid 599");
});

test("a vague reply never counts as consent", async () => {
  const session = start("bank");

  await processTurn(session, "Freeze my credit card");
  const result = await processTurn(session, "hmm let me think");

  assert.equal(result.kind, "reprompt");
  assert.equal(session.data.cards[1].status, "active");
  assert.ok(session.pending, "still waiting for yes or no");
});

test("moving on to another request lapses the pending action unconfirmed", async () => {
  const session = start("bank");

  await processTurn(session, "Freeze my credit card");
  const result = await processTurn(session, "What's my balance?");

  assert.equal(result.kind, "answer");
  assert.equal(session.pending, null);
  assert.equal(session.data.cards[1].status, "active");
  assert.ok(session.audit.some((event) => event.type === "action_lapsed"));
});

test("the agent asks which card when it is ambiguous", async () => {
  const session = start("bank");
  const result = await processTurn(session, "freeze my card");

  assert.equal(result.kind, "chat");
  assert.equal(session.pending, null);
});

test("secrets are refused, redacted and never reach the brain or the audit log", async () => {
  const session = start("bank");
  const result = await processTurn(session, "my pin is 4821 and card 4111 1111 1111 1111");

  assert.equal(result.blocked, true);
  assert.doesNotMatch(result.userText, /4821|4111/);
  assert.doesNotMatch(JSON.stringify(session.audit), /4821|4111/);
  assert.ok(session.audit.some((event) => event.type === "guardrail_blocked"));
});

test("guardrail detection has no false positives on normal requests", () => {
  for (const text of [
    "What's my balance?",
    "Freeze my credit card ending 3390",
    "I forgot my password",
    "How do I reset my PIN?",
    "Call me on 9876543210",
    "Show me application APP-1024",
  ]) {
    assert.equal(detectSensitive(text), false, text);
    assert.equal(redactSensitive(text), text, text);
  }

  assert.equal(detectSensitive("the OTP is 482913"), true);
  assert.equal(detectSensitive("my password is hunter2"), true);
  assert.equal(detectSensitive("4111-1111-1111-1111"), true);
});

test("agent output that solicits a secret is replaced before it can be spoken", () => {
  assert.equal(sanitizeAgentText("Please tell me your OTP to continue.").blocked, true);
  assert.equal(sanitizeAgentText("Could you read out your card number?").blocked, true);
  assert.equal(sanitizeAgentText("Your balance is 84,250 rupees.").blocked, false);
});

test("confirmation classifier", () => {
  assert.equal(classifyConfirmation("Yes, go ahead"), "yes");
  assert.equal(classifyConfirmation("no thanks"), "no");
  assert.equal(classifyConfirmation("what about my balance"), "other");
  assert.equal(classifyConfirmation("yesterday I called"), "other");
});

test("the agent never states unknown facts: unknown application is reported, not invented", async () => {
  const session = start("underwriting");
  const result = await processTurn(session, "What is the risk on APP-9999?");

  assert.match(result.replyText, /couldn't find/);
  assert.equal(result.toolCalls[0].result.found, false);
});

test("call result records outcome, actions and a full audit trail", async () => {
  const session = start("underwriting");

  await processTurn(session, "Summarize this applicant");
  await processTurn(session, "Call the applicant");
  await processTurn(session, "yes");

  const summary = closeCall(session, 42, []);

  assert.equal(summary.outcome, "action_completed");
  assert.equal(summary.profileId, "underwriting");
  assert.ok(summary.evidence.includes("Call job: JOB-3001"));
  assert.match(summary.summary, /JOB-3001/);

  const types = summary.audit.map((event) => event.type);

  for (const expected of [
    "call_started",
    "ai_disclosed",
    "user_turn",
    "tool_call",
    "action_requested",
    "action_confirmed",
    "action_executed",
    "call_ended",
  ]) {
    assert.ok(types.includes(expected), expected);
  }
});

test("each call starts from clean data", async () => {
  const first = start("telecom");

  await processTurn(first, "Upgrade my plan");
  await processTurn(first, "yes");
  assert.equal(first.data.plan, "Postpaid 799");

  const second = start("telecom");

  assert.equal(second.data.plan, "Postpaid 599");
});
