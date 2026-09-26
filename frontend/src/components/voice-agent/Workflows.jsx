import { useCallback, useEffect, useState } from "react";
import StatusBadge from "./StatusBadge";
import ScrollReveal from "./ScrollReveal";
import { fetchAgents } from "../../runtime/agents.js";
import { fetchContacts } from "../../runtime/contacts.js";
import {
  checkWorkflowEligibility,
  createWorkflow,
  deleteWorkflow,
  fetchWorkflows,
  triggerWorkflow,
  updateWorkflow,
} from "../../runtime/workflows.js";

const STATUS_OPTIONS = ["draft", "active", "paused", "archived"];
const STATUS_TONE = { active: "ok", draft: "info", paused: "warn", archived: "warn" };

// The engine's only supported trigger (app/workflows/triggers.py's TRIGGERS dict has exactly one
// entry: "date_offset"). Any other trigger_type is refused by evaluate_trigger() as
// UNSUPPORTED_TRIGGER_TYPE - not just for the scheduler, but for the manual "Run now" button too
// (both call the same evaluate_workflow()), so a workflow saved with one could never actually run
// either way. The form only ever offers what can really work.
const TRIGGER_TYPES = [{ id: "date_offset", label: "Date offset (a date on the contact + N days)" }];

const PROFILES_FOR_FORM = [
  { id: "underwriting", name: "Underwriting" },
  { id: "bank", name: "Bank" },
  { id: "insurance", name: "Insurance" },
  { id: "telecom", name: "Telecom" },
  { id: "admissions", name: "Admissions" },
];

// One form, reused for "add a workflow" (initial is null) and "edit this one". The two JSON
// blobs the engine reads - trigger_config (when it fires) and action_config (what it does) - are
// edited as plain fields, assembled back into the model's own object shapes. conditions stays
// [] (the engine stops on any condition as unsupported) and retry_policy stays {}.
function WorkflowForm({ agents, initial, profiles, onSaved, onCancel }) {
  const [form, setForm] = useState(() => ({
    name: initial?.name ?? "",
    agentId: initial?.agent_id != null ? String(initial.agent_id) : agents[0]?.id != null ? String(agents[0].id) : "",
    status: initial?.status ?? "draft",
    triggerType: initial?.trigger_type ?? "date_offset",
    referenceField: initial?.trigger_config?.reference_field ?? "appointment_date",
    offsetDays: initial?.trigger_config?.offset_days != null ? String(initial.trigger_config.offset_days) : "-1",
    time: initial?.trigger_config?.time ?? "10:00",
    timezone: initial?.trigger_config?.timezone ?? "Asia/Kolkata",
    profileId: initial?.action_config?.profile_id ?? profiles[0]?.id ?? "bank",
    reason: initial?.action_config?.reason ?? "",
    channel: initial?.action_config?.channel ?? "phone",
  }));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value });

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError(null);

    if (!form.agentId) {
      setError("An agent is required. Create one under the agent configuration first.");
      setSaving(false);
      return;
    }

    const payload = {
      name: form.name.trim(),
      agent_id: Number(form.agentId),
      status: form.status,
      trigger_type: form.triggerType,
      trigger_config:
        form.triggerType === "date_offset"
          ? {
              reference_field: form.referenceField.trim(),
              offset_days: Number(form.offsetDays),
              time: form.time.trim(),
              timezone: form.timezone.trim(),
            }
          : {},
      conditions: [],
      action_config: {
        profile_id: form.profileId,
        reason: form.reason.trim(),
        channel: form.channel,
      },
      retry_policy: {},
    };

    try {
      const saved = initial ? await updateWorkflow(initial.id, payload) : await createWorkflow(payload);
      onSaved(saved);
    } catch (failure) {
      // The form (and whatever the operator typed) stays exactly as it was.
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <form className="history-form" onSubmit={submit}>
      <label htmlFor="wf-name">Name</label>
      <input id="wf-name" value={form.name} onChange={set("name")} maxLength={120} required />

      <label htmlFor="wf-agent">Agent</label>
      <select id="wf-agent" value={form.agentId} onChange={set("agentId")} required>
        <option value="" disabled>
          {agents.length === 0 ? "No agents yet - create one first" : "Choose an agent"}
        </option>
        {agents.map((agent) => (
          <option key={agent.id} value={String(agent.id)}>
            {agent.name}
          </option>
        ))}
      </select>

      <label htmlFor="wf-status">Status</label>
      <select id="wf-status" value={form.status} onChange={set("status")}>
        {STATUS_OPTIONS.map((status) => (
          <option key={status} value={status}>
            {status}
          </option>
        ))}
      </select>

      <label htmlFor="wf-trigger-type">Trigger</label>
      <select id="wf-trigger-type" value={form.triggerType} onChange={set("triggerType")}>
        {TRIGGER_TYPES.map((type) => (
          <option key={type.id} value={type.id}>
            {type.label}
          </option>
        ))}
      </select>

      {form.triggerType === "date_offset" && (
        <div className="history-form-buttons">
          <input
            aria-label="Reference field on the contact"
            placeholder="Metadata field, e.g. appointment_date"
            value={form.referenceField}
            onChange={set("referenceField")}
          />
          <input
            aria-label="Offset in days"
            type="number"
            placeholder="-1"
            value={form.offsetDays}
            onChange={set("offsetDays")}
          />
          <input aria-label="Time of day" placeholder="10:00" value={form.time} onChange={set("time")} />
          <input
            aria-label="Time zone"
            placeholder="Asia/Kolkata"
            value={form.timezone}
            onChange={set("timezone")}
          />
        </div>
      )}

      <label htmlFor="wf-profile">Profile the call uses</label>
      <select id="wf-profile" value={form.profileId} onChange={set("profileId")}>
        {PROFILES_FOR_FORM.map((profile) => (
          <option key={profile.id} value={profile.id}>
            {profile.name}
          </option>
        ))}
      </select>

      <label htmlFor="wf-reason">Why the call is made (said to them)</label>
      <input
        id="wf-reason"
        value={form.reason}
        onChange={set("reason")}
        maxLength={200}
        placeholder="your appointment tomorrow"
        required
      />

      <label htmlFor="wf-channel">How the call is placed</label>
      <select id="wf-channel" value={form.channel} onChange={set("channel")}>
        <option value="phone">Phone call (rings their number)</option>
        <option value="web">Web link (the person opens it and answers)</option>
      </select>

      {error && (
        <p className="voice-notice" role="alert">
          {error}
        </p>
      )}

      <div className="history-form-buttons">
        <button type="button" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
        <button type="submit" className="primary" disabled={saving}>
          {saving ? "Saving…" : initial ? "Save changes" : "Add workflow"}
        </button>
      </div>
    </form>
  );
}

// Step 18E: what this workflow would do for one contact right now (eligibility) and the one-
// click run that reuses WorkflowEngine.run_for_contact - the exact judgment the scheduler makes.
function EligibilityPanel({ contacts, workflows }) {
  // What the operator picked, if anything. The lists load after this panel first mounts, so the
  // effective selection falls back to the first entry each time instead of being frozen at ""
  // from the empty first render (which left both buttons permanently disabled).
  const [picked, setPicked] = useState({ workflow: "", contact: "" });
  const stillThere = (list, id) => list.some((item) => String(item.id) === id);
  const workflowId = stillThere(workflows, picked.workflow) ? picked.workflow : workflows[0] ? String(workflows[0].id) : "";
  const contactId = stillThere(contacts, picked.contact) ? picked.contact : contacts[0] ? String(contacts[0].id) : "";
  const setWorkflowId = (value) => setPicked((current) => ({ ...current, workflow: value }));
  const setContactId = (value) => setPicked((current) => ({ ...current, contact: value }));
  const [evaluation, setEvaluation] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const ready = workflowId !== "" && contactId !== "";

  const run = async (kind) => {
    setBusy(true);
    setError(null);
    setEvaluation(null);
    setOutcome(null);

    try {
      if (kind === "eligibility") {
        setEvaluation(await checkWorkflowEligibility(Number(workflowId), Number(contactId)));
      } else {
        setOutcome(await triggerWorkflow(Number(workflowId), Number(contactId)));
      }
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  if (workflows.length === 0 || contacts.length === 0) {
    return (
      <div className="state-block">
        {workflows.length === 0
          ? "Add a workflow to test eligibility and run it on demand."
          : "Add a contact to test eligibility and run a workflow on it."}
      </div>
    );
  }

  return (
    <div className="history-form">
      <label htmlFor="ep-workflow">Workflow</label>
      <select id="ep-workflow" value={workflowId} onChange={(event) => setWorkflowId(event.target.value)}>
        {workflows.map((workflow) => (
          <option key={workflow.id} value={String(workflow.id)}>
            {workflow.name}
          </option>
        ))}
      </select>

      <label htmlFor="ep-contact">Contact</label>
      <select id="ep-contact" value={contactId} onChange={(event) => setContactId(event.target.value)}>
        {contacts.map((contact) => (
          <option key={contact.id} value={String(contact.id)}>
            {contact.name}
          </option>
        ))}
      </select>

      <div className="history-form-buttons">
        <button type="button" onClick={() => run("eligibility")} disabled={busy || !ready}>
          {busy ? (
            <>
              <span className="button-spinner" aria-hidden="true" />
              Working…
            </>
          ) : (
            "Check eligibility"
          )}
        </button>
        <button type="button" className="primary" onClick={() => run("trigger")} disabled={busy || !ready}>
          {busy ? (
            <>
              <span className="button-spinner" aria-hidden="true" />
              Working…
            </>
          ) : (
            "Run now"
          )}
        </button>
      </div>

      {error && (
        <p className="voice-notice" role="alert">
          {error}
        </p>
      )}

      {evaluation && (
        <div className={`eligibility-result ${evaluation.eligible ? "" : "is-blocked"}`} role="status">
          <div className="er-head">{evaluation.eligible ? "Eligible" : "Not eligible"}</div>
          <p className="er-copy">
            {evaluation.reason}
            {evaluation.detail ? ` — ${evaluation.detail}` : ""}.
          </p>
        </div>
      )}

      {outcome && (
        <div className={`eligibility-result ${outcome.call_job_created ? "" : "is-blocked"}`} role="status">
          <div className="er-head">{outcome.call_job_created ? "Call recorded" : "No call made"}</div>
          <p className="er-copy">
            {outcome.call_job_created
              ? `Job ${outcome.job_id} created (recorded, not yet dialled).`
              : `${outcome.reason}${outcome.detail ? ` — ${outcome.detail}` : ""}.`}
          </p>
        </div>
      )}
    </div>
  );
}

export default function Workflows({ serverAvailable }) {
  const [workflows, setWorkflows] = useState([]);
  const [agents, setAgents] = useState([]);
  const [contacts, setContacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState(null); // null while adding a new workflow
  const [deletingId, setDeletingId] = useState(null);

  const load = useCallback(async () => {
    setError(null);

    try {
      const [workflowList, agentList, contactList] = await Promise.all([
        fetchWorkflows(),
        fetchAgents(),
        fetchContacts().catch(() => []), // no organization: the panel just shows its own hint
      ]);
      setWorkflows(workflowList);
      setAgents(agentList);
      setContacts(contactList);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Nothing to load: the render below shows the offline message instead of ever consulting
    // `loading`, regardless of its value.
    if (!serverAvailable) return undefined;

    // Deferred, same as CallHistory's own load effect: load() sets state synchronously at its
    // own start, which must not happen directly inside the effect body.
    const timer = setTimeout(load, 0);

    return () => clearTimeout(timer);
  }, [serverAvailable, load]);

  const openCreate = () => {
    setEditingId(null);
    setFormOpen(true);
  };

  const openEdit = (workflow) => {
    setEditingId(workflow.id);
    setFormOpen(true);
  };

  const saved = (workflow) => {
    setWorkflows((list) =>
      list.some((w) => w.id === workflow.id) ? list.map((w) => (w.id === workflow.id ? workflow : w)) : [...list, workflow]
    );
    setFormOpen(false);
  };

  const remove = async (workflowId) => {
    if (!window.confirm("Delete this workflow? This cannot be undone.")) return;

    setDeletingId(workflowId);
    setError(null);

    try {
      await deleteWorkflow(workflowId);
      setWorkflows((list) => list.filter((w) => w.id !== workflowId));
    } catch (failure) {
      // A workflow with call jobs cannot be deleted (409); the list is left exactly as it was.
      setError(failure.message);
    } finally {
      setDeletingId(null);
    }
  };

  if (!serverAvailable) {
    return (
      <section className="history-page">
        <h2>Workflows</h2>
        <p className="callee-note">
          Workflows are kept by the server. Start the backend (and sign in) to manage them.
        </p>
      </section>
    );
  }

  const editingWorkflow = editingId ? workflows.find((w) => w.id === editingId) ?? null : null;

  return (
    <section className="history-page">
      <div className="history-head">
        <div><h2>Workflows</h2><p className="page-sub">Automate when and how your AI agent acts.</p></div>
        <div className="history-form-buttons">
          <button onClick={load}>Refresh</button>
          <button className="primary" onClick={() => (formOpen ? setFormOpen(false) : openCreate())}>
            {formOpen ? "Close" : "Add workflow"}
          </button>
        </div>
      </div>

      <div className="workflow-path" aria-label="How a workflow runs">
        <span>Trigger</span>
        <i />
        <span>Agent</span>
        <i />
        <span>Conversation</span>
        <i />
        <span>Action</span>
      </div>

      {error && (
        <p className="voice-notice" role="alert">
          {error}
        </p>
      )}

      {formOpen && (
        <WorkflowForm
          key={editingId ?? "new"}
          agents={agents}
          profiles={PROFILES_FOR_FORM}
          initial={editingWorkflow}
          onSaved={saved}
          onCancel={() => setFormOpen(false)}
        />
      )}

      <h3>Configured workflows</h3>
      {loading ? (
        <div className="state-block is-loading">Loading workflows...</div>
      ) : workflows.length === 0 ? (
        <div className="state-block">No workflows yet.</div>
      ) : (
        <ScrollReveal>
          <ul className="workflow-list" aria-label="Configured workflows">
            {workflows.map((workflow) => {
              const agent = agents.find((a) => a.id === workflow.agent_id);
              const trigger =
                workflow.trigger_type === "date_offset"
                  ? `${workflow.trigger_config?.offset_days ?? "?"} days from ${workflow.trigger_config?.reference_field ?? "?"} @ ${workflow.trigger_config?.time ?? "?"}`
                  : workflow.trigger_type;
              const nodes = [
                ["Trigger", trigger],
                ["AI agent", agent ? agent.name : `agent #${workflow.agent_id}`],
                ["Conversation", `${workflow.action_config?.profile_id ?? "?"} · ${workflow.action_config?.channel ?? "?"}`],
                ["Action", workflow.action_config?.reason ?? "—"],
              ];

              return (
                <li key={workflow.id} className={`workflow-card is-${workflow.status}`}>
                  <div className="wf-head">
                    <h4>{workflow.name}</h4>
                    <StatusBadge tone={STATUS_TONE[workflow.status] ?? "info"}>{workflow.status}</StatusBadge>
                    <span className="wf-actions">
                      <button className="history-open" onClick={() => openEdit(workflow)}>
                        Edit
                      </button>
                      <button
                        className="history-open"
                        onClick={() => remove(workflow.id)}
                        disabled={deletingId === workflow.id}
                      >
                        {deletingId === workflow.id ? "Deleting…" : "Delete"}
                      </button>
                    </span>
                  </div>
                  <ol className="wf-nodes">
                    {nodes.map(([kind, detail]) => (
                      <li key={kind} className="wf-node" tabIndex={0}>
                        <span className="wf-kind">{kind}</span>
                        <span className="wf-detail">{detail}</span>
                      </li>
                    ))}
                  </ol>
                </li>
              );
            })}
          </ul>
        </ScrollReveal>
      )}

      <h3>Run a workflow on a contact</h3>
      <EligibilityPanel contacts={contacts} workflows={workflows} />
    </section>
  );
}