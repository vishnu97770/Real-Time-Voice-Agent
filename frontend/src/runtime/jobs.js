// Builds the payload for POST /api/call-jobs, including the optional domain links. The server
// (app/outbound.py's CallJobRequest) requires organization_id whenever agent_id, contact_id or
// workflow_id is present, and every linked record must belong to that one organization. Each
// record fetched from /api/agents, /api/contacts and /api/workflows carries its own
// organization_id, so the links always agree here; the function still refuses a mixed set rather
// than letting the server return a confusing 422. A job with no links is a plain call, exactly as
// before the domain links existed.

export function buildCallJobPayload({
  profile_id,
  channel = "web",
  callee,
  reason,
  customer_ref = null,
  agent = null,
  contact = null,
  workflow = null,
}) {
  const payload = {
    profile_id,
    channel,
    callee,
    reason,
  };

  if (customer_ref) payload.customer_ref = customer_ref;

  const linked = [agent, contact, workflow].filter(Boolean);

  if (linked.length) {
    const organizations = new Set(linked.map((record) => record.organization_id));
    if (organizations.size !== 1) {
      throw new Error("The agent, contact and workflow must belong to the same organization");
    }

    payload.organization_id = [...organizations][0];
    if (agent) payload.agent_id = agent.id;
    if (contact) payload.contact_id = contact.id;
    if (workflow) payload.workflow_id = workflow.id;
  }

  return payload;
}

// Which of its domain links a job (from /api/call-jobs) carries, as names when resolvable.
export function jobLinks(job, { agents = [], contacts = [], workflows = [] } = {}) {
  const find = (list, id) => list.find((record) => record.id === id);

  const links = [];

  if (job.workflow_id != null) {
    const workflow = find(workflows, job.workflow_id);
    links.push({ kind: "workflow", id: job.workflow_id, name: workflow?.name ?? null });
  }
  if (job.contact_id != null) {
    const contact = find(contacts, job.contact_id);
    links.push({ kind: "contact", id: job.contact_id, name: contact?.name ?? null });
  }
  if (job.agent_id != null) {
    const agent = find(agents, job.agent_id);
    links.push({ kind: "agent", id: job.agent_id, name: agent?.name ?? null });
  }

  return links;
}