// The signed-in operator's organization and its agent(s) - Step 18B's backend, unchanged in
// shape from what app/schemas/agent.py and app/main.py actually return.

import { getJson, post } from "./api.js";

// The organizations the signed-in operator belongs to (today: at most one). [] if there is no
// server, the operator has none yet, or the request fails - same "no server, no problem"
// convention as fetchCustomers().
export async function fetchOrganizations() {
  try {
    return await getJson("/api/organizations");
  } catch {
    return [];
  }
}

// The signed-in operator's organization's agents. [] for the same reasons as above: the caller
// treats "couldn't check" the same as "none yet", and a save attempt is where a real problem
// (no organization, a network failure) is reported, not silently swallowed.
export async function fetchAgents() {
  try {
    return await getJson("/api/agents");
  } catch {
    return [];
  }
}

export async function fetchAgent(agentId) {
  return getJson(`/api/agents/${encodeURIComponent(agentId)}`);
}

export async function createAgent(payload) {
  return (await post("/api/agents", payload)).json();
}

export async function updateAgent(agentId, payload) {
  return (await post(`/api/agents/${encodeURIComponent(agentId)}`, payload, { method: "PUT" })).json();
}
