import { useCallback, useEffect, useState } from "react";
import StatusBadge from "./StatusBadge";
import ScrollReveal from "./ScrollReveal";
import CallSummary from "./CallSummary";
import { getJson, post } from "../../runtime/api.js";
import { fetchCustomers } from "../../runtime/customers.js";
import { fetchAgents } from "../../runtime/agents.js";
import { fetchContacts } from "../../runtime/contacts.js";
import { fetchWorkflows } from "../../runtime/workflows.js";
import { formatDateTime, formatTime } from "../../runtime/format.js";
import { buildCallJobPayload, jobLinks } from "../../runtime/jobs.js";
import { label, mapResult, toneOf } from "../../runtime/results.js";

const REFRESH_MS = 5000;

const when = (iso) => (iso ? formatDateTime(Date.parse(iso)) : "—");

function Badge({ status }) {
  return <StatusBadge tone={toneOf(status)}>{label(status)}</StatusBadge>;
}

// A business system does this over the API. This form is the same request,
// for an operator: it creates a call job and gives back the link to answer it.
function PlaceCall({ profiles, telephony, onPlaced }) {
  const [form, setForm] = useState({
    profile_id: profiles[0].id,
    channel: "web",
    customer_ref: "",
    name: "",
    phone: "",
    reason: "",
    agent_id: "",
    contact_id: "",
    workflow_id: "",
  });
  const [customers, setCustomers] = useState([]);
  const [links, setLinks] = useState({ agents: [], contacts: [], workflows: [] });
  const [placed, setPlaced] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value });

  useEffect(() => {
    let cancelled = false;

    fetchCustomers(form.profile_id).then((list) => {
      if (!cancelled) setCustomers(list);
    });

    return () => {
      cancelled = true;
    };
  }, [form.profile_id]);

  useEffect(() => {
    let cancelled = false;

    // The domain links are optional: an agent making the call, a contact being called, and the
    // workflow that created the job. All belong to the operator's own organization.
    Promise.all([
      fetchAgents().catch(() => []),
      fetchContacts().catch(() => []),
      fetchWorkflows().catch(() => []),
    ]).then(([agents, contacts, workflows]) => {
      if (!cancelled) setLinks({ agents, contacts, workflows });
    });

    return () => {
      cancelled = true;
    };
  }, []);

  const changeProfile = (event) => {
    setCustomers([]); // never offer the previous profile's customers for this one
    setForm({ ...form, profile_id: event.target.value, customer_ref: "" });
  };

  // Choosing a customer fills in who to call, which can still be edited.
  const changeCustomer = (event) => {
    const chosen = customers.find((customer) => customer.ref === event.target.value);

    setForm({ ...form, customer_ref: event.target.value, name: chosen ? chosen.display_name : form.name });
  };

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);

    const agent = links.agents.find((record) => record.id === Number(form.agent_id)) ?? null;
    const contact = links.contacts.find((record) => record.id === Number(form.contact_id)) ?? null;
    const workflow = links.workflows.find((record) => record.id === Number(form.workflow_id)) ?? null;

    try {
      const response = await post(
        "/api/call-jobs",
        buildCallJobPayload({
          profile_id: form.profile_id,
          customer_ref: form.customer_ref || null,
          channel: form.channel,
          callee: { name: form.name.trim(), phone: form.phone.trim() },
          reason: form.reason.trim(),
          agent,
          contact,
          workflow,
        }),
      );

      setPlaced(await response.json());
      onPlaced();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  if (placed && !placed.answer_url) {
    return (
      <div className="history-form">
        <p>
          <strong>
            Call {placed.job_id}: {placed.status === "failed" ? "could not be placed" : `calling ${placed.callee.name} now`}.
          </strong>{" "}
          {placed.status === "failed"
            ? "The phone network refused it. Check the number and the server's Twilio settings."
            : "The call is on the phone. Its status and result appear in the table below when it ends."}
        </p>
        <div className="history-form-buttons">
          <button type="button" onClick={() => setPlaced(null)}>
            Place another
          </button>
        </div>
      </div>
    );
  }

  if (placed) {
    return (
      <div className="history-form">
        <p>
          <strong>Call {placed.job_id} is ringing.</strong> Send this link to {placed.callee.name}; when they open it
          and answer, the agent makes the call.
        </p>
        <input readOnly value={placed.answer_url} aria-label="Answer link" onFocus={(e) => e.target.select()} />
        <div className="history-form-buttons">
          <button type="button" onClick={() => navigator.clipboard?.writeText(placed.answer_url)}>
            Copy link
          </button>
          <button type="button" onClick={() => window.open(placed.answer_url, "_blank", "noopener")}>
            Open it here
          </button>
          <button type="button" onClick={() => setPlaced(null)}>
            Place another
          </button>
        </div>
      </div>
    );
  }

  return (
    <form className="history-form" onSubmit={submit}>
      <label htmlFor="pc-profile">Profile</label>
      <select id="pc-profile" value={form.profile_id} onChange={changeProfile}>
        {profiles.map((profile) => (
          <option key={profile.id} value={profile.id}>
            {profile.name}
          </option>
        ))}
      </select>

      {telephony && (
        <>
          <label htmlFor="pc-channel">How</label>
          <select id="pc-channel" value={form.channel} onChange={set("channel")}>
            <option value="web">Web link (the person opens it and answers)</option>
            <option value="phone">Phone call (rings their number)</option>
          </select>
        </>
      )}

      {customers.length > 0 && (
        <>
          <label htmlFor="pc-customer">Customer data</label>
          <select id="pc-customer" value={form.customer_ref} onChange={changeCustomer}>
            <option value="">Demo data</option>
            {customers.map((customer) => (
              <option key={customer.ref} value={customer.ref}>
                {customer.display_name}
              </option>
            ))}
          </select>
        </>
      )}

      <label htmlFor="pc-name">Who to call</label>
      <input id="pc-name" value={form.name} onChange={set("name")} placeholder="Priya Sharma" required />

      <label htmlFor="pc-phone">
        Phone{form.channel === "phone" ? " (international format, this number will be dialled)" : ""}
      </label>
      <input id="pc-phone" value={form.phone} onChange={set("phone")} placeholder="+91 98765 43210" required />

      <label htmlFor="pc-reason">Why you are calling (said to them)</label>
      <input
        id="pc-reason"
        value={form.reason}
        onChange={set("reason")}
        placeholder="unusual activity on your credit card"
        required
      />

      <h4 className="history-subsection">Domain links (optional)</h4>
      <p className="callee-note">
        Link the call to a configured agent, one of your contacts, and the workflow that prompted
        it. The job's record then carries these, exactly as a workflow-created call would.
      </p>

      <label htmlFor="pc-agent">Agent</label>
      <select id="pc-agent" value={form.agent_id} onChange={set("agent_id")}>
        <option value="">— none —</option>
        {links.agents.map((agent) => (
          <option key={agent.id} value={String(agent.id)}>
            {agent.name}
          </option>
        ))}
      </select>

      <label htmlFor="pc-contact">Contact</label>
      <select id="pc-contact" value={form.contact_id} onChange={set("contact_id")}>
        <option value="">— none —</option>
        {links.contacts.map((contact) => (
          <option key={contact.id} value={String(contact.id)}>
            {contact.name}
          </option>
        ))}
      </select>

      <label htmlFor="pc-workflow">Workflow</label>
      <select id="pc-workflow" value={form.workflow_id} onChange={set("workflow_id")}>
        <option value="">— none —</option>
        {links.workflows.map((workflow) => (
          <option key={workflow.id} value={String(workflow.id)}>
            {workflow.name}
          </option>
        ))}
      </select>

      {error && (
        <p className="voice-notice" role="alert">
          {error}
        </p>
      )}

      <div className="history-form-buttons">
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Placing..." : "Place call"}
        </button>
      </div>
    </form>
  );
}

export default function CallHistory({ serverAvailable, telephony, profiles }) {
  const [jobs, setJobs] = useState([]);
  const [calls, setCalls] = useState([]);
  const [links, setLinks] = useState({ agents: [], contacts: [], workflows: [] });
  const [error, setError] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [detail, setDetail] = useState(null);

  const load = useCallback(async () => {
    try {
      const [jobRows, callRows, agents, contacts, workflows] = await Promise.all([
        getJson("/api/call-jobs?limit=30"),
        getJson("/api/calls?limit=30"),
        // The lists resolve a job's link ids to names; a job stays readable without them.
        fetchAgents().catch(() => []),
        fetchContacts().catch(() => []),
        fetchWorkflows().catch(() => []),
      ]);

      setJobs(jobRows);
      setCalls(callRows);
      setLinks({ agents, contacts, workflows });
      setError(null);
    } catch (failure) {
      setError(failure.message);
    }
  }, []);

  useEffect(() => {
    if (!serverAvailable) return undefined;

    const first = setTimeout(load, 0);
    const timer = setInterval(load, REFRESH_MS);

    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [load, serverAvailable]);

  const open = async (callId) => {
    try {
      setDetail(mapResult(await getJson(`/api/calls/${encodeURIComponent(callId)}/result`)));
    } catch (failure) {
      setError(failure.message);
    }
  };

  if (!serverAvailable) {
    return (
      <section className="history-page">
        <h2>Call History</h2>
        <p className="callee-note">
          Call history is kept by the server. Start the backend (and sign in) to see past calls and to place
          outbound calls.
        </p>
      </section>
    );
  }

  if (detail) {
    return (
      <section className="history-page">
        <button className="history-back" onClick={() => setDetail(null)}>
          ← Back to history
        </button>
        <CallSummary summary={detail} />
      </section>
    );
  }

  return (
    <section className="history-page">
      <div className="history-head">
        <div><h2>Call History</h2><p className="page-sub">Every conversation, who it was with, and how it ended.</p></div>
        <div className="history-form-buttons">
          <button onClick={load}>Refresh</button>
          <button className="primary" onClick={() => setShowForm(!showForm)}>
            {showForm ? "Close" : "Place a call"}
          </button>
        </div>
      </div>

      {error && (
        <p className="voice-notice" role="alert">
          {error}
        </p>
      )}

      {showForm && <PlaceCall profiles={profiles} telephony={telephony} onPlaced={load} />}

      <h3>Outbound jobs</h3>
      {jobs.length === 0 ? (
        <div className="state-block">No outbound calls yet.</div>
      ) : (
        <ScrollReveal>
          <ul className="call-list" aria-label="Outbound jobs">
            {jobs.map((job) => {
              const via = jobLinks(job, links);

              return (
                <li key={job.job_id} className="call-card">
                  <div className="cc-head">
                    <span className="cc-who">{job.callee.name}</span>
                    <span className="muted">{job.callee.phone}</span>
                    <Badge status={job.status} />
                  </div>
                  <div className="cc-reason">{job.reason}</div>
                  <div className="cc-meta">
                    <span>{when(job.created_at)}</span>
                    <span>{job.channel}</span>
                    {via.map((link) => (
                      <span className="cc-link" key={`${link.kind}-${link.id}`}>
                        {link.kind}: {link.name ?? link.id}
                      </span>
                    ))}
                    {job.callback.status !== "none" && <span>result sent: {job.callback.status}</span>}
                  </div>
                </li>
              );
            })}
          </ul>
        </ScrollReveal>
      )}

      <h3>Calls</h3>
      {calls.length === 0 ? (
        <div className="state-block">No finished calls yet.</div>
      ) : (
        <ScrollReveal>
          <ul className="call-list" aria-label="Calls">
            {calls.map((call) => (
              <li key={call.call_id} className="call-card">
                <div className="cc-head">
                  <span className="cc-who">{call.callee_name ?? "Unknown caller"}</span>
                  <Badge status={call.outcome} />
                  <button className="history-open cc-open" onClick={() => open(call.call_id)}>
                    View
                  </button>
                </div>
                <div className="cc-meta">
                  <span>{when(call.started_at)}</span>
                  <span>
                    {call.direction} · {call.channel}
                  </span>
                  <span>{call.profile_name}</span>
                  <span>{formatTime(call.duration_seconds ?? 0)}</span>
                </div>
              </li>
            ))}
          </ul>
        </ScrollReveal>
      )}
    </section>
  );
}
