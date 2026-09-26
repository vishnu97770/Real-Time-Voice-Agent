import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { ApiError } from "./api.js";
import {
  checkWorkflowEligibility,
  createWorkflow,
  deleteWorkflow,
  fetchWorkflow,
  fetchWorkflows,
  triggerWorkflow,
  updateWorkflow,
} from "./workflows.js";

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
});

const respond = (status, body = {}) => {
  globalThis.fetch = async (url, options) => {
    respond.last = { url, options };
    // A 204 (delete_workflow's real response) must have a null body - a real server sends none.
    return new Response(status === 204 ? null : JSON.stringify(body), { status });
  };
};

const WORKFLOW = {
  id: 4,
  organization_id: 1,
  agent_id: 2,
  name: "Appointment Reminder",
  trigger_type: "date_offset",
  trigger_config: { reference_field: "appointment_date", offset_days: -1 },
  conditions: [],
  action_config: { profile_id: "bank", reason: "your appointment tomorrow" },
  retry_policy: {},
  status: "active",
};

test("fetchWorkflows reads the list endpoint", async () => {
  respond(200, [WORKFLOW]);
  const workflows = await fetchWorkflows();

  assert.equal(respond.last.url, "/api/workflows");
  assert.equal(respond.last.options.method, undefined); // a plain GET
  assert.deepEqual(workflows, [WORKFLOW]);
});

test("fetchWorkflow reads one, by a URL-safe id", async () => {
  respond(200, WORKFLOW);
  await fetchWorkflow(4);

  assert.equal(respond.last.url, "/api/workflows/4");
});

test("createWorkflow POSTs the payload as JSON", async () => {
  respond(201, WORKFLOW);
  const created = await createWorkflow({ name: "Appointment Reminder" });

  assert.equal(respond.last.url, "/api/workflows");
  assert.equal(respond.last.options.method, "POST");
  assert.equal(respond.last.options.headers["Content-Type"], "application/json");
  assert.equal(respond.last.options.body, JSON.stringify({ name: "Appointment Reminder" }));
  assert.deepEqual(created, WORKFLOW);
});

test("updateWorkflow PUTs to the workflow's own path", async () => {
  respond(200, { ...WORKFLOW, name: "Reminder v2" });
  const updated = await updateWorkflow(4, { name: "Reminder v2" });

  assert.equal(respond.last.url, "/api/workflows/4");
  assert.equal(respond.last.options.method, "PUT");
  assert.equal(respond.last.options.body, JSON.stringify({ name: "Reminder v2" }));
  assert.deepEqual(updated, { ...WORKFLOW, name: "Reminder v2" });
});

test("deleteWorkflow DELETEs and resolves on success", async () => {
  respond(204);
  await deleteWorkflow(4);

  assert.equal(respond.last.url, "/api/workflows/4");
  assert.equal(respond.last.options.method, "DELETE");
});

test("a failed create, update or delete throws - none of them is reported as a silent success", async () => {
  respond(409, { detail: "This workflow has call jobs and cannot be deleted" });

  await assert.rejects(createWorkflow({ name: "x" }), (error) => error instanceof ApiError && error.status === 409);
  await assert.rejects(updateWorkflow(4, { name: "x" }), (error) => error instanceof ApiError && error.status === 409);
  await assert.rejects(deleteWorkflow(4), (error) => error instanceof ApiError && error.status === 409);
});

test("a 404 for an unknown or another organization's workflow is a real error, not an empty result", async () => {
  respond(404, { detail: "Unknown workflow" });

  await assert.rejects(fetchWorkflow(999), (error) => error instanceof ApiError && error.status === 404);
});

// Step 18E: the eligibility and on-demand trigger reads, both URL-safe and answer-shaped.

const EVALUATION = {
  workflow_id: 4,
  contact_id: 9,
  due: true,
  eligible: true,
  reason: "workflow_due",
  detail: "",
  due_at: "2026-09-25T10:00:00+05:30",
  execution_key: "wf-4#contact-9#2026-09-25",
};

const OUTCOME = {
  workflow_id: 4,
  contact_id: 9,
  due: true,
  eligible: true,
  call_job_created: true,
  reason: "call_job_created",
  detail: "",
  job_id: 31,
  execution_key: "wf-4#contact-9#2026-09-25",
};

test("checkWorkflowEligibility POSTs to the per-contact eligibility path (matches app/main.py's route)", async () => {
  respond(200, EVALUATION);
  const evaluation = await checkWorkflowEligibility(4, 9);

  assert.equal(respond.last.url, "/api/workflows/4/contacts/9/eligibility");
  assert.equal(respond.last.options.method, "POST");
  assert.deepEqual(evaluation, EVALUATION);
});

test("triggerWorkflow POSTs to the per-contact trigger path", async () => {
  respond(200, OUTCOME);
  const outcome = await triggerWorkflow(4, 9);

  assert.equal(respond.last.url, "/api/workflows/4/contacts/9/trigger");
  assert.equal(respond.last.options.method, "POST");
  assert.deepEqual(outcome, OUTCOME);
});

test("a refusal is a normal result, not an error - the caller reads the reason", async () => {
  respond(200, { ...OUTCOME, call_job_created: false, reason: "opted_out", job_id: null });

  const outcome = await triggerWorkflow(4, 9);
  assert.equal(outcome.call_job_created, false);
  assert.equal(outcome.reason, "opted_out");
});

test("eligibility and trigger reject on real failures (404/401) like any other read", async () => {
  respond(404, { detail: "Unknown workflow" });
  await assert.rejects(checkWorkflowEligibility(999, 9), (error) => error instanceof ApiError && error.status === 404);

  respond(401, { detail: "You must sign in" });
  await assert.rejects(triggerWorkflow(4, 9), (error) => error instanceof ApiError && error.status === 401);
});