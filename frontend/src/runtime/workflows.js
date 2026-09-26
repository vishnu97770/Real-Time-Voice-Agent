// The signed-in operator's organization's workflows - Step 18D's backend shape, unchanged from
// what app/schemas/workflow.py and app/main.py actually return. Same conventions as agents.js /
// contacts.js: a failed read throws (ApiError), a failed write throws, nothing here invents a
// "silent empty list" for anything but the offline fetchWorkflows read, which the Workflows
// screen treats the same as "no server".

import { getJson, post } from "./api.js";

// The signed-in operator's organization's workflows. [] if there is no server, the operator has
// none yet, or the request fails - same "no server, no problem" convention as fetchAgents().
export async function fetchWorkflows() {
  try {
    return await getJson("/api/workflows");
  } catch {
    return [];
  }
}

export async function fetchWorkflow(workflowId) {
  return getJson(`/api/workflows/${encodeURIComponent(workflowId)}`);
}

export async function createWorkflow(payload) {
  return (await post("/api/workflows", payload)).json();
}

export async function updateWorkflow(workflowId, payload) {
  return (await post(`/api/workflows/${encodeURIComponent(workflowId)}`, payload, { method: "PUT" })).json();
}

// No JSON body: DELETE returns 204, nothing to parse. A failed delete throws (ApiError), same as
// every other request() call - the caller must not assume success just because this resolved.
export async function deleteWorkflow(workflowId) {
  await post(`/api/workflows/${encodeURIComponent(workflowId)}`, undefined, { method: "DELETE" });
}

// Step 18E, both reusing the engine on the server - nothing evaluated here.
// eligibility: would this workflow call this contact right now, without creating anything? A POST
// (not a GET) even though it creates nothing: it matches the backend route exactly as
// app/main.py defines it (POST .../eligibility), the same way triggerWorkflow does below.
export async function checkWorkflowEligibility(workflowId, contactId) {
  return (
    await post(`/api/workflows/${encodeURIComponent(workflowId)}/contacts/${encodeURIComponent(contactId)}/eligibility`)
  ).json();
}

// trigger: the same run WorkflowEngine.run_for_contact makes, on demand. The outcome says whether
// a (recorded, not yet dialled) call job was created, or why not - refusals are a normal result.
export async function triggerWorkflow(workflowId, contactId) {
  return (await post(`/api/workflows/${encodeURIComponent(workflowId)}/contacts/${encodeURIComponent(contactId)}/trigger`)).json();
}