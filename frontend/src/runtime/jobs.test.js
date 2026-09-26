import assert from "node:assert/strict";
import { test } from "node:test";

import { buildCallJobPayload, jobLinks } from "./jobs.js";

const AGENT = { id: 2, organization_id: 1, name: "Collections Assistant" };
const CONTACT = { id: 9, organization_id: 1, name: "Priya Sharma" };
const WORKFLOW = { id: 4, organization_id: 1, name: "Today Reminder" };

const BASE = {
  profile_id: "bank",
  channel: "web",
  callee: { name: "Priya", phone: "+919876543210" },
  reason: "your appointment",
};

test("a plain job is exactly what it always was - no links, no organization", () => {
  assert.deepEqual(buildCallJobPayload(BASE), BASE);
});

test("a customer reference is passed through when one is chosen", () => {
  const payload = buildCallJobPayload({ ...BASE, customer_ref: "al-4f" });

  assert.equal(payload.customer_ref, "al-4f");
  assert.equal("organization_id" in payload, false);
});

test("a single link carries its own organization", () => {
  const payload = buildCallJobPayload({ ...BASE, contact: CONTACT });

  assert.equal(payload.organization_id, 1);
  assert.equal(payload.contact_id, 9);
  assert.equal("agent_id" in payload && "workflow_id" in payload, false);
});

test("all three links share one organization_id", () => {
  const payload = buildCallJobPayload({ ...BASE, agent: AGENT, contact: CONTACT, workflow: WORKFLOW });

  assert.equal(payload.organization_id, 1);
  assert.equal(payload.agent_id, 2);
  assert.equal(payload.contact_id, 9);
  assert.equal(payload.workflow_id, 4);
});

test("records from different organizations are refused here, not mis-sent for a 422", () => {
  assert.throws(
    () => buildCallJobPayload({ ...BASE, agent: AGENT, contact: { ...CONTACT, organization_id: 2 } }),
    /same organization/,
  );
});

test("jobLinks names each link a job carries, in the order the engine sets them", () => {
  const job = { job_id: "wf-1", workflow_id: 4, contact_id: 9, agent_id: 2 };

  assert.deepEqual(jobLinks(job, { agents: [AGENT], contacts: [CONTACT], workflows: [WORKFLOW] }), [
    { kind: "workflow", id: 4, name: "Today Reminder" },
    { kind: "contact", id: 9, name: "Priya Sharma" },
    { kind: "agent", id: 2, name: "Collections Assistant" },
  ]);
});

test("jobLinks leaves an unresolvable id as a bare number, and a linkless job as empty", () => {
  assert.deepEqual(jobLinks({ job_id: "x", workflow_id: 7 }, { workflows: [WORKFLOW] }), [
    { kind: "workflow", id: 7, name: null },
  ]);
  assert.deepEqual(jobLinks({ job_id: "x" }), []);
});